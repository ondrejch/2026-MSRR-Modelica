#!/usr/bin/env python3
"""SSH-native job agent for dispatching Modelica workloads to worker nodes.

This module runs on a login node and launches commands on worker nodes over SSH.
Job state is tracked in a shared filesystem directory so callers can inspect
status, logs, and results without keeping a persistent daemon alive.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from .omc_gw_config import (
        config_path_hint,
        get_config_int,
        get_config_list,
        get_config_str,
        load_user_config,
    )
except ImportError:  # pragma: no cover - supports direct script execution
    from omc_gw_config import (  # type: ignore[no-redef]
        config_path_hint,
        get_config_int,
        get_config_list,
        get_config_str,
        load_user_config,
    )

DEFAULT_MAX_TASKS_PER_WORKER = 32
DEFAULT_JOBS_ROOT = "~/.modelica_ssh/jobs"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELED", "SUBMIT_FAILED"}
# A RUNNING job whose heartbeat is older than this is treated as orphaned
# (worker rebooted or the launcher died before start). 0 disables reaping.
DEFAULT_STALE_HEARTBEAT_SECONDS = 1800.0
STALE_HEARTBEAT_ENV_VAR = "MODELICA_SSH_STALE_HEARTBEAT_SECONDS"
HEARTBEAT_PERIOD_SECONDS = 60

# job_id values are generated as
# f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"; the
# strict pattern is enforced before any job_id is joined into a path.
JOB_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def _validate_job_id(job_id: str) -> None:
    """Reject job_id values that do not match the generator pattern.

    Validation runs before *job_id* is joined into any path so a crafted value
    cannot traverse out of the jobs root. The rejected value is deliberately
    not included in the error message.
    """

    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError(
            "job_id must match the generator pattern YYYYMMDDTHHMMSSZ-XXXXXXXX "
            "(8 digits, 'T', 6 digits, 'Z', dash, 8 lowercase hex digits)."
        )


def _validate_worker_name(worker: str) -> None:
    """Reject worker values ssh could parse as options or shell tokens.

    The rejected value is deliberately not included in the error message.
    """

    if worker.startswith("-"):
        raise ValueError(
            "Worker names must not start with '-' (they would be parsed as an SSH option)."
        )
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in worker):
        raise ValueError("Worker names must not contain whitespace or control characters.")


def _utc_now_iso() -> str:
    """Return the current UTC time in a compact ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from *path*."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_private_text(path: Path, text: str) -> None:
    """Write *text* to *path* readable only by the owner (mode 0o600).

    Submitted command text can embed credentials or tokens; on a shared jobs
    filesystem the file must never pass through a group/world-readable state,
    so it is created with 0o600 and the mode is re-asserted afterwards.
    """

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object to *path* atomically with stable formatting.

    The payload can embed the submitted command, so the temporary file is
    owner-only (0o600) and its name carries the writer PID so two writers of
    the same file cannot interleave in one sibling temporary.
    """

    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    _write_private_text(tmp_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_path, path)


def _emit_json(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    """Write a JSON payload to *stream*."""

    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()


def _job_dir(jobs_root: Path, job_id: str) -> Path:
    """Return the job directory for *job_id* (validated before the path join)."""

    _validate_job_id(job_id)
    return jobs_root / job_id


def _optional_text(path: Path) -> str | None:
    """Read text from *path* if it exists."""

    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _optional_int(path: Path) -> int | None:
    """Read an integer value from *path* if it exists."""

    raw = _optional_text(path)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _stale_heartbeat_seconds() -> float:
    """Return the stale-heartbeat threshold; <= 0 disables orphan reaping."""

    raw = os.environ.get(STALE_HEARTBEAT_ENV_VAR)
    if raw is None or raw.strip() == "":
        return DEFAULT_STALE_HEARTBEAT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_STALE_HEARTBEAT_SECONDS


def _heartbeat_age_seconds(job_directory: Path) -> float | None:
    """Return seconds since the last sign of life for *job_directory*.

    Prefers the worker-side ``heartbeat`` file mtime. When the job never wrote
    a heartbeat (remote launch never started), falls back to the submission
    timestamp from ``meta.json``. Returns ``None`` when neither is available.
    """

    heartbeat_path = job_directory / "heartbeat"
    if heartbeat_path.exists():
        try:
            return max(0.0, time.time() - heartbeat_path.stat().st_mtime)
        except OSError:
            return None

    meta_path = job_directory / "meta.json"
    if not meta_path.exists():
        return None
    try:
        submitted = _read_json(meta_path).get("submitted_at")
    except (json.JSONDecodeError, OSError):
        return None
    if not submitted:
        return None
    try:
        submitted_ts = datetime.fromisoformat(str(submitted).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, time.time() - submitted_ts.timestamp())


def _mark_orphaned(job_directory: Path) -> None:
    """Record the orphan reaping decision once for *job_directory*."""

    marker = job_directory / "orphaned_at"
    if marker.exists():
        return
    try:
        marker.write_text(_utc_now_iso() + "\n", encoding="utf-8")
    except OSError:  # noqa: BLE001 - best-effort bookkeeping on shared FS
        pass


def _compute_state(job_directory: Path) -> tuple[str, int | None]:
    """Infer job state and exit code from the tracked files in *job_directory*."""

    if (job_directory / "canceled_at").exists():
        return "CANCELED", None

    exit_code = _optional_int(job_directory / "exit_code")
    if exit_code is not None:
        return ("COMPLETED" if exit_code == 0 else "FAILED"), exit_code

    meta_path = job_directory / "meta.json"
    if meta_path.exists():
        try:
            meta = _read_json(meta_path)
        except json.JSONDecodeError:
            meta = {}
        if meta.get("state") == "SUBMIT_FAILED":
            return "SUBMIT_FAILED", None

    stale_seconds = _stale_heartbeat_seconds()
    if stale_seconds > 0:
        age = _heartbeat_age_seconds(job_directory)
        if age is not None and age > stale_seconds:
            _mark_orphaned(job_directory)
            return "FAILED", None

    return "RUNNING", None


def _tail_lines(path: Path, line_count: int) -> list[str]:
    """Return the last *line_count* lines from *path*."""

    if line_count <= 0 or not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=line_count))


def _parse_workers(worker_csv: str | None) -> list[str]:
    """Parse a comma-separated worker list."""

    if not worker_csv:
        raise ValueError(
            "No workers configured. Pass --workers/--worker or configure "
            f"'workers' in {config_path_hint()}."
        )
    workers = [item.strip() for item in worker_csv.split(",") if item.strip()]
    if not workers:
        raise ValueError("No workers configured.")
    for worker in workers:
        _validate_worker_name(worker)
    return workers


def _job_tasks(meta: dict[str, Any]) -> int:
    """Return the number of reserved task slots for a job metadata record."""

    try:
        tasks = int(meta.get("tasks", 1))
    except (TypeError, ValueError):
        tasks = 1
    return max(tasks, 1)


def _active_tasks_by_worker(jobs_root: Path) -> dict[str, int]:
    """Return active reserved task counts keyed by worker hostname."""

    counts: dict[str, int] = {}
    if not jobs_root.exists():
        return counts

    for child in jobs_root.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = _read_json(meta_path)
        except json.JSONDecodeError:
            continue
        worker = meta.get("worker")
        if not worker:
            continue
        state, _ = _compute_state(child)
        if state not in TERMINAL_STATES:
            counts[worker] = counts.get(worker, 0) + _job_tasks(meta)
    return counts


def _active_jobs_by_worker(jobs_root: Path) -> dict[str, int]:
    """Return active job counts keyed by worker hostname."""

    counts: dict[str, int] = {}
    if not jobs_root.exists():
        return counts

    for child in jobs_root.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = _read_json(meta_path)
        except json.JSONDecodeError:
            continue
        worker = meta.get("worker")
        if not worker:
            continue
        state, _ = _compute_state(child)
        if state not in TERMINAL_STATES:
            counts[worker] = counts.get(worker, 0) + 1
    return counts


def _pick_worker(
    jobs_root: Path,
    workers: list[str],
    *,
    requested_tasks: int,
    max_tasks_per_worker: int,
) -> str | None:
    """Pick the least-loaded worker that has enough free task capacity."""

    counts = _active_tasks_by_worker(jobs_root)
    candidates = [
        worker
        for worker in workers
        if counts.get(worker, 0) + requested_tasks <= max_tasks_per_worker
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda worker: (counts.get(worker, 0), worker))


def _build_submit_command(args: argparse.Namespace) -> str:
    """Build a shell command string from CLI submit arguments."""

    if args.cmd and args.command:
        raise ValueError("Use either --cmd or positional command tokens, not both.")

    if args.cmd:
        command = args.cmd.strip()
        if not command:
            raise ValueError("--cmd cannot be empty.")
        return command

    tokens = args.command
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    if not tokens:
        raise ValueError("No command provided. Use --cmd or append command tokens after '--'.")
    return " ".join(shlex.quote(token) for token in tokens)


def _build_run_script(
    job_directory: Path,
    workdir: Path,
    command: str,
    tasks: int,
    *,
    modelica_bin_dir: str | None = None,
) -> str:
    """Create the bash wrapper executed on the worker node."""

    modelica_bin_block = ""
    if modelica_bin_dir:
        modelica_bin_block = (
            f"MODELICA_BIN_DIR={shlex.quote(modelica_bin_dir)}\n"
            'export PATH="$MODELICA_BIN_DIR:$PATH"\n'
        )

    return f"""#!/usr/bin/env bash
set -u

JOB_DIR={shlex.quote(str(job_directory))}
WORKDIR={shlex.quote(str(workdir))}
STDOUT_FILE={shlex.quote(str(job_directory / "stdout.log"))}
STDERR_FILE={shlex.quote(str(job_directory / "stderr.log"))}
EXIT_FILE={shlex.quote(str(job_directory / "exit_code"))}
START_FILE={shlex.quote(str(job_directory / "started_at"))}
END_FILE={shlex.quote(str(job_directory / "finished_at"))}
HEARTBEAT_FILE={shlex.quote(str(job_directory / "heartbeat"))}
CMD={shlex.quote(command)}
TASKS={tasks}

umask 077
date -Is > "$START_FILE"
echo "${{HOSTNAME:-unknown}}" > "$JOB_DIR/hostname"
echo "$TASKS" > "$JOB_DIR/tasks_used"

# Touch a heartbeat file periodically so the agent can detect jobs whose
# worker died (reboot, lost nohup) instead of reserving their slots forever.
(
  while :; do
    sleep {HEARTBEAT_PERIOD_SECONDS}
    touch "$HEARTBEAT_FILE" 2>/dev/null || exit
  done
) &
HEARTBEAT_PID=$!
trap 'kill "$HEARTBEAT_PID" 2>/dev/null' EXIT

# Default to one threaded math/runtime libraries so reserved task slots are not
# silently oversubscribed by nested BLAS/OpenMP threading.
export MODELICA_SSH_TASKS="$TASKS"
{modelica_bin_block}export OMP_NUM_THREADS="${{OMP_NUM_THREADS:-1}}"
export OPENBLAS_NUM_THREADS="${{OPENBLAS_NUM_THREADS:-1}}"
export MKL_NUM_THREADS="${{MKL_NUM_THREADS:-1}}"
export NUMEXPR_NUM_THREADS="${{NUMEXPR_NUM_THREADS:-1}}"
export VECLIB_MAXIMUM_THREADS="${{VECLIB_MAXIMUM_THREADS:-1}}"
export BLIS_NUM_THREADS="${{BLIS_NUM_THREADS:-1}}"

mkdir -p -- "$WORKDIR" 2>/dev/null || true
cd "$WORKDIR" || {{
  echo "Failed to cd to $WORKDIR" > "$STDERR_FILE"
  echo 200 > "$EXIT_FILE"
  date -Is > "$END_FILE"
  exit 200
}}

bash -lc "$CMD" > "$STDOUT_FILE" 2> "$STDERR_FILE"
rc=$?
echo "$rc" > "$EXIT_FILE"
date -Is > "$END_FILE"
exit "$rc"
"""


def _launch_remote_job(
    *,
    ssh_bin: str,
    ssh_options: list[str],
    worker: str,
    run_script_path: Path,
) -> str:
    """Launch *run_script_path* on *worker* over SSH and return the background PID."""

    _validate_worker_name(worker)
    remote_command = f"nohup bash {shlex.quote(str(run_script_path))} >/dev/null 2>&1 & echo $!"
    cmd = [ssh_bin, *ssh_options, worker, remote_command]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"SSH launch failed on {worker}: rc={proc.returncode}; "
            f"stdout={proc.stdout.strip()!r}; stderr={proc.stderr.strip()!r}"
        )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"SSH launch on {worker} returned no PID.")
    launcher_pid = lines[-1]
    # The PID comes from remote ssh stdout; only a plain non-negative integer
    # is accepted so untrusted output never reaches meta.json verbatim. The
    # rejected value is deliberately not included in the message.
    if not (launcher_pid.isascii() and launcher_pid.isdigit()):
        raise RuntimeError("SSH launch returned a non-numeric launcher PID.")
    return launcher_pid


def _load_job_view(jobs_root: Path, job_id: str) -> dict[str, Any]:
    """Load status and metadata for *job_id*."""

    job_directory = _job_dir(jobs_root, job_id)
    meta_path = job_directory / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Unknown job_id: {job_id}")
    try:
        meta = _read_json(meta_path)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt metadata for job {job_id}: {exc}") from exc

    state, exit_code = _compute_state(job_directory)
    stdout_path = job_directory / "stdout.log"
    stderr_path = job_directory / "stderr.log"

    view: dict[str, Any] = dict(meta)
    view["state"] = state
    view["exit_code"] = exit_code
    view["started_at"] = _optional_text(job_directory / "started_at")
    view["finished_at"] = _optional_text(job_directory / "finished_at")
    view["orphaned_at"] = _optional_text(job_directory / "orphaned_at")
    view["hostname"] = _optional_text(job_directory / "hostname")
    view["stdout_path"] = str(stdout_path)
    view["stderr_path"] = str(stderr_path)
    view["stdout_bytes"] = stdout_path.stat().st_size if stdout_path.exists() else 0
    view["stderr_bytes"] = stderr_path.stat().st_size if stderr_path.exists() else 0
    return view


def _stream_log_until_complete(job_directory: Path, log_path: Path, poll_seconds: float) -> None:
    """Follow *log_path* until the job reaches a terminal state."""

    position = 0
    while True:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                chunk = handle.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                position = handle.tell()
        state, _ = _compute_state(job_directory)
        if state in TERMINAL_STATES:
            if log_path.exists():
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    chunk = handle.read()
                    if chunk:
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
            break
        time.sleep(max(poll_seconds, 0.05))


@contextmanager
def _submission_lock(jobs_root: Path) -> Iterator[None]:
    """Serialize submission-time capacity accounting on the shared jobs root."""

    jobs_root.mkdir(parents=True, exist_ok=True)
    lock_path = jobs_root / ".submit.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reserve_job_slot(
    *,
    args: argparse.Namespace,
    jobs_root: Path,
    workers: list[str],
    command: str,
) -> tuple[str, Path, Path, dict[str, Any]]:
    """Reserve worker capacity and create the initial job record."""

    if args.tasks <= 0:
        raise ValueError("--tasks must be a positive integer.")
    if args.max_tasks_per_worker <= 0:
        raise ValueError("--max-tasks-per-worker must be a positive integer.")
    if args.tasks > args.max_tasks_per_worker:
        raise ValueError("--tasks cannot exceed --max-tasks-per-worker.")

    submit_started = time.monotonic()
    timeout_seconds = args.wait_timeout_seconds

    while True:
        with _submission_lock(jobs_root):
            if args.worker:
                if args.worker not in workers:
                    raise ValueError(f"Requested worker is not in the allowed workers list: {workers}")
                active_by_worker = _active_tasks_by_worker(jobs_root)
                chosen_worker = None
                if active_by_worker.get(args.worker, 0) + args.tasks <= args.max_tasks_per_worker:
                    chosen_worker = args.worker
            else:
                chosen_worker = _pick_worker(
                    jobs_root,
                    workers,
                    requested_tasks=args.tasks,
                    max_tasks_per_worker=args.max_tasks_per_worker,
                )

            if chosen_worker is not None:
                job_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
                job_directory = _job_dir(jobs_root, job_id)
                job_directory.mkdir(parents=False, exist_ok=False)
                run_script_path = job_directory / "run.sh"
                run_script_path.write_text(
                    _build_run_script(
                        job_directory,
                        Path(args.workdir).expanduser(),
                        command,
                        args.tasks,
                        modelica_bin_dir=args.modelica_bin_dir,
                    ),
                    encoding="utf-8",
                )
                os.chmod(run_script_path, 0o700)

                # The metadata file is written while holding the lock so the task
                # reservation becomes visible before any competing submit call
                # makes its own scheduling decision.
                meta = {
                    "job_id": job_id,
                    "name": args.name,
                    "worker": chosen_worker,
                    "workers": workers,
                    "command": command,
                    "workdir": str(Path(args.workdir).expanduser()),
                    "jobs_root": str(jobs_root),
                    "submitted_at": _utc_now_iso(),
                    "submit_host": socket.gethostname(),
                    "state": "SUBMITTED",
                    "tasks": args.tasks,
                    "max_tasks_per_worker": args.max_tasks_per_worker,
                }
                _write_json(job_directory / "meta.json", meta)
                _write_private_text(job_directory / "command.sh", command + "\n")
                return chosen_worker, job_directory, run_script_path, meta

        if not args.wait_for_slot:
            raise RuntimeError("No worker has enough free task capacity for this submission.")
        if timeout_seconds is not None and (time.monotonic() - submit_started) > timeout_seconds:
            raise TimeoutError("Timed out waiting for a worker task slot.")
        time.sleep(max(args.poll_seconds, 0.05))


def _worker_capacity_view(
    jobs_root: Path,
    workers: list[str],
    *,
    max_tasks_per_worker: int,
) -> list[dict[str, Any]]:
    """Return a structured worker-capacity summary."""

    task_counts = _active_tasks_by_worker(jobs_root)
    job_counts = _active_jobs_by_worker(jobs_root)
    view: list[dict[str, Any]] = []
    for worker in workers:
        active_tasks = task_counts.get(worker, 0)
        view.append(
            {
                "worker": worker,
                "active_jobs": job_counts.get(worker, 0),
                "active_tasks": active_tasks,
                "free_tasks": max(0, max_tasks_per_worker - active_tasks),
                "max_tasks": max_tasks_per_worker,
            }
        )
    return view


def _command_submit(args: argparse.Namespace) -> int:
    """Handle the ``submit`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    jobs_root.mkdir(parents=True, exist_ok=True)

    workers = _parse_workers(args.workers or args.worker)
    command = _build_submit_command(args)
    chosen_worker, job_directory, run_script_path, meta = _reserve_job_slot(
        args=args,
        jobs_root=jobs_root,
        workers=workers,
        command=command,
    )

    try:
        launcher_pid = _launch_remote_job(
            ssh_bin=args.ssh_bin,
            ssh_options=list(args.ssh_option or []),
            worker=chosen_worker,
            run_script_path=run_script_path,
        )
        meta["launcher_pid"] = launcher_pid
        meta["launched_at"] = _utc_now_iso()
        meta["state"] = "RUNNING"
        _write_json(job_directory / "meta.json", meta)
    except Exception as exc:  # noqa: BLE001
        meta["state"] = "SUBMIT_FAILED"
        meta["submit_error"] = str(exc)
        _write_json(job_directory / "meta.json", meta)
        _emit_json({"ok": False, "job_id": meta["job_id"], "error": str(exc)}, stream=sys.stderr)
        return 2

    _emit_json(
        {
            "ok": True,
            "job_id": meta["job_id"],
            "worker": chosen_worker,
            "launcher_pid": launcher_pid,
            "job_dir": str(job_directory),
            "tasks": meta["tasks"],
        }
    )
    return 0


def _command_status(args: argparse.Namespace) -> int:
    """Handle the ``status`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    try:
        payload = _load_job_view(jobs_root, args.job_id)
    except FileNotFoundError as exc:
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 1
    _emit_json({"ok": True, "job": payload})
    return 0


def _command_result(args: argparse.Namespace) -> int:
    """Handle the ``result`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    try:
        payload = _load_job_view(jobs_root, args.job_id)
    except FileNotFoundError as exc:
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 1

    job_directory = _job_dir(jobs_root, args.job_id)
    payload["stdout_tail"] = _tail_lines(job_directory / "stdout.log", args.tail_lines)
    payload["stderr_tail"] = _tail_lines(job_directory / "stderr.log", args.tail_lines)
    _emit_json({"ok": True, "job": payload})
    return 0


def _command_list(args: argparse.Namespace) -> int:
    """Handle the ``list`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    jobs: list[dict[str, Any]] = []
    if jobs_root.exists():
        for child in sorted(jobs_root.iterdir(), key=lambda path: path.name, reverse=True):
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.exists():
                continue
            try:
                jobs.append(_load_job_view(jobs_root, child.name))
            except Exception:  # noqa: BLE001
                continue
            if len(jobs) >= args.limit:
                break
    _emit_json({"ok": True, "jobs": jobs})
    return 0


def _command_logs(args: argparse.Namespace) -> int:
    """Handle the ``logs`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    job_directory = _job_dir(jobs_root, args.job_id)
    if not job_directory.exists():
        print(f"Unknown job_id: {args.job_id}", file=sys.stderr)
        return 1

    log_name = "stdout.log" if args.stream == "stdout" else "stderr.log"
    log_path = job_directory / log_name

    if not args.follow:
        for line in _tail_lines(log_path, args.lines):
            sys.stdout.write(line)
        sys.stdout.flush()
        return 0

    for line in _tail_lines(log_path, args.lines):
        sys.stdout.write(line)
    sys.stdout.flush()
    _stream_log_until_complete(job_directory, log_path, poll_seconds=args.poll_seconds)
    return 0


def _command_capacity(args: argparse.Namespace) -> int:
    """Handle the ``capacity`` subcommand."""

    jobs_root = Path(args.jobs_root).expanduser().resolve()
    workers = _parse_workers(args.workers)
    _emit_json(
        {
            "ok": True,
            "workers": _worker_capacity_view(
                jobs_root,
                workers,
                max_tasks_per_worker=args.max_tasks_per_worker,
            ),
        }
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        prog="modelica-ssh-agent",
        description=(
            "Run Modelica-related commands on worker nodes via SSH and track job "
            "state in a shared filesystem."
        ),
    )
    parser.add_argument(
        "--jobs-root",
        default=None,
        help=(
            "Directory for job metadata and logs. Defaults to 'jobs_root' from the "
            "user config, then ~/.modelica_ssh/jobs."
        ),
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    submit = sub.add_parser("submit", help="Submit a command to a worker node.")
    submit.add_argument("--workdir", default=".", help="Working directory for the command.")
    submit.add_argument(
        "--workers",
        default=None,
        help="CSV list of worker hostnames. Defaults to configured 'workers'.",
    )
    submit.add_argument("--worker", default=None, help="Force a specific worker.")
    submit.add_argument("--name", default="", help="Optional human-readable label.")
    submit.add_argument("--tasks", type=int, default=1, help="Reserved task slots for this job (default: 1).")
    submit.add_argument(
        "--max-tasks-per-worker",
        type=int,
        default=None,
        help=(
            "Maximum reserved task slots per worker. Defaults to configured "
            f"'max_tasks_per_worker', then {DEFAULT_MAX_TASKS_PER_WORKER}."
        ),
    )
    submit.add_argument(
        "--modelica-bin-dir",
        default=None,
        help=(
            "Optional OpenModelica bin directory prepended to PATH on workers. "
            "Defaults to configured 'modelica_bin_dir'."
        ),
    )
    submit.add_argument(
        "--wait-for-slot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for worker capacity when no slot is immediately free (default: enabled).",
    )
    submit.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Polling interval while waiting for a free worker slot.",
    )
    submit.add_argument(
        "--wait-timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout while waiting for worker capacity.",
    )
    submit.add_argument("--cmd", default=None, help="Command string run with bash -lc.")
    submit.add_argument("--ssh-bin", default="ssh", help="SSH executable (default: ssh).")
    submit.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        help="Additional option passed to SSH. Repeat for multiple values; defaults to configured 'ssh_options'.",
    )
    submit.add_argument("command", nargs=argparse.REMAINDER, help="Command tokens, optionally after '--'.")
    submit.set_defaults(handler=_command_submit)

    status = sub.add_parser("status", help="Get job status.")
    status.add_argument("job_id", help="Job ID from submit output.")
    status.set_defaults(handler=_command_status)

    result = sub.add_parser("result", help="Get status and output tail.")
    result.add_argument("job_id", help="Job ID from submit output.")
    result.add_argument("--tail-lines", type=int, default=40, help="Number of tail lines for stdout/stderr.")
    result.set_defaults(handler=_command_result)

    list_cmd = sub.add_parser("list", help="List recent jobs.")
    list_cmd.add_argument("--limit", type=int, default=20, help="Max jobs to return.")
    list_cmd.set_defaults(handler=_command_list)

    logs = sub.add_parser("logs", help="Print job logs.")
    logs.add_argument("job_id", help="Job ID from submit output.")
    logs.add_argument("--stream", choices=("stdout", "stderr"), default="stdout", help="Which log to print.")
    logs.add_argument("--lines", type=int, default=100, help="Initial tail lines.")
    logs.add_argument("--follow", action="store_true", help="Follow log stream until job completes.")
    logs.add_argument(
        "--poll-seconds",
        type=float,
        default=0.5,
        help="Polling interval while following logs.",
    )
    logs.set_defaults(handler=_command_logs)

    capacity = sub.add_parser("capacity", help="Show per-worker task capacity.")
    capacity.add_argument(
        "--workers",
        default=None,
        help="CSV list of worker hostnames. Defaults to configured 'workers'.",
    )
    capacity.add_argument(
        "--max-tasks-per-worker",
        type=int,
        default=None,
        help=(
            "Maximum reserved task slots per worker. Defaults to configured "
            f"'max_tasks_per_worker', then {DEFAULT_MAX_TASKS_PER_WORKER}."
        ),
    )
    capacity.set_defaults(handler=_command_capacity)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the agent CLI."""

    config = load_user_config()
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.jobs_root = args.jobs_root or get_config_str(config, "jobs_root") or DEFAULT_JOBS_ROOT
    if hasattr(args, "workers") and not args.workers:
        configured_workers = get_config_list(config, "workers")
        if configured_workers:
            args.workers = ",".join(configured_workers)
    if hasattr(args, "max_tasks_per_worker") and args.max_tasks_per_worker is None:
        args.max_tasks_per_worker = get_config_int(
            config,
            "max_tasks_per_worker",
            DEFAULT_MAX_TASKS_PER_WORKER,
        )
    if hasattr(args, "modelica_bin_dir") and not args.modelica_bin_dir:
        args.modelica_bin_dir = get_config_str(config, "modelica_bin_dir")
    if hasattr(args, "ssh_option") and not args.ssh_option:
        args.ssh_option = get_config_list(config, "ssh_options") or []
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
