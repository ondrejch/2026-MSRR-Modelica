#!/usr/bin/env python3
"""Watch omc_gw remote frequency jobs and collect completed cases locally."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import textwrap
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.omc_gw.modelica_ssh_client import (  # noqa: E402
    TERMINAL_STATES,
    ModelicaSshClient,
    _default_agent_command,
)
from helpers.omc_gw.omc_gw_config import (  # noqa: E402
    get_config_list,
    get_config_str,
    load_user_config,
)


REMOTE_PROGRESS_SCRIPT = textwrap.dedent(
    """
    from __future__ import annotations

    import csv
    import json
    import math
    from pathlib import Path
    import sys


    def read_run_params(path: Path) -> dict[str, str]:
        params: dict[str, str] = {}
        if not path.exists():
            return params
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\\n").split("\\t")
                if len(parts) == 2:
                    params[parts[0]] = parts[1]
        return params


    def format_dir_name(freq: float) -> str:
        return f"freq{freq:08.5f}"


    def build_expected_stop_map(base: Path) -> dict[str, float]:
        mapping_path = base / "stop_time_by_freq.csv"
        if mapping_path.exists():
            stop_map: dict[str, float] = {}
            with mapping_path.open("r", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    stop_map[format_dir_name(float(row["frequency_rad_s"]))] = float(
                        row["stop_time_s"]
                    )
            return stop_map

        params = read_run_params(base / "run_params.txt")
        freq_min = float(params.get("freq_min", "0") or 0.0)
        freq_max = float(params.get("freq_max", "0") or 0.0)
        num_freq = int(float(params.get("num_freq", "0") or 0.0))
        stop_time = float(params.get("stop_time", "0") or 0.0)
        if freq_min <= 0 or freq_max <= 0 or num_freq <= 0 or stop_time <= 0:
            return {}
        if num_freq == 1:
            return {format_dir_name(freq_min): stop_time}

        log_min = math.log10(freq_min)
        log_max = math.log10(freq_max)
        stop_map = {}
        for idx in range(num_freq):
            frac = idx / (num_freq - 1)
            freq = 10 ** (log_min + frac * (log_max - log_min))
            stop_map[format_dir_name(freq)] = stop_time
        return stop_map


    def read_last_time(path: Path) -> float | None:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 4096))
                tail = handle.read().decode("utf-8", errors="ignore").splitlines()
            if not tail:
                return None
            return float(tail[-1].split(",")[0])
        except Exception:
            return None


    base = Path(sys.argv[1])
    stop_map = build_expected_stop_map(base)
    complete_count = 0
    csv_count = 0
    missing: list[str] = []
    short: list[dict[str, float | str | None]] = []
    expected_names = sorted(stop_map) if stop_map else sorted(
        item.name for item in base.glob("freq*") if item.is_dir()
    )

    for dirname in expected_names:
        target_stop = stop_map.get(dirname)
        work_dir = base / dirname
        csv_path = next(work_dir.glob("*_res.csv"), None) if work_dir.exists() else None
        if csv_path is None:
            missing.append(dirname)
            continue
        csv_count += 1
        final_time = read_last_time(csv_path)
        if target_stop is None:
            complete_count += 1
            continue
        if final_time is not None and final_time + 1e-6 >= target_stop:
            complete_count += 1
        else:
            short.append(
                {
                    "dir": dirname,
                    "final_time": final_time,
                    "target_time": target_stop,
                }
            )

    print(
        json.dumps(
            {
                "expected_count": len(expected_names),
                "csv_count": csv_count,
                "complete_count": complete_count,
                "missing": missing[:8],
                "short": short[:8],
            }
        )
    )
    """
)


@dataclass(frozen=True)
class WatchCase:
    core: str
    power_tag: str
    base_dir: str
    job_id: str
    power: float | None = None
    worker: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.core, self.power_tag, self.base_dir)

    @property
    def label(self) -> str:
        return f"{self.core} {self.power_tag}"


def default_collect_jobs() -> int:
    return max(1, os.cpu_count() or 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Watch remote omc_gw frequency jobs, sync completed reduced CSVs, "
            "and run local collection only when every frequency reached its "
            "requested stop_time."
        )
    )
    parser.add_argument(
        "--submissions_jsonl",
        action="append",
        required=True,
        help="Submission log JSONL. Repeat for multiple batches; later entries win.",
    )
    parser.add_argument(
        "--poll_seconds",
        type=float,
        default=30.0,
        help="Polling interval while waiting for unfinished cases (default: 30).",
    )
    parser.add_argument(
        "--collect_jobs",
        type=int,
        default=default_collect_jobs(),
        help=f"Parallel jobs for local collection (default: {default_collect_jobs()}).",
    )
    parser.add_argument(
        "--log_path",
        type=str,
        default=None,
        help="Optional log file path; messages are appended there as well as stdout.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Inspect current state once and exit instead of polling.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Override omc_gw SSH host.",
    )
    return parser.parse_args()


def load_watch_cases(submission_paths: list[str]) -> dict[tuple[str, str, str], WatchCase]:
    latest_by_key: dict[tuple[str, str, str], WatchCase] = {}
    for path_str in submission_paths:
        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(f"Submission log not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            for lineno, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                core = str(payload["core"])
                power_tag = str(payload["power_tag"])
                base_dir = str(payload["base_dir"])
                case = WatchCase(
                    core=core,
                    power_tag=power_tag,
                    base_dir=base_dir,
                    job_id=str(payload["job_id"]),
                    power=float(payload["power"]) if "power" in payload else None,
                    worker=str(payload["worker"]) if "worker" in payload else None,
                )
                latest_by_key[case.key] = case
    return latest_by_key


def build_client(host_override: str | None) -> ModelicaSshClient:
    config = load_user_config()
    return ModelicaSshClient(
        host=host_override or get_config_str(config, "host"),
        agent_command=_default_agent_command(config),
        ssh_bin="ssh",
        ssh_options=tuple(get_config_list(config, "ssh_options") or ()),
        jobs_root=get_config_str(config, "jobs_root"),
    )


def append_log(log_path: Path | None, message: str) -> None:
    print(message, flush=True)
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def remote_results_dir(job: dict[str, Any], base_dir: str) -> str:
    base = PurePosixPath(base_dir)
    if base.is_absolute():
        return base.as_posix()
    workdir = job.get("workdir")
    if not workdir:
        raise ValueError(f"Job {job.get('job_id')} is missing remote workdir")
    return str(PurePosixPath(str(workdir)) / base)


def local_results_dir(base_dir: str) -> Path:
    base = Path(base_dir)
    if base.is_absolute():
        return base
    return ROOT / base


def fetch_remote_progress(client: ModelicaSshClient, remote_dir: str) -> dict[str, Any]:
    if not client.host:
        raise RuntimeError("omc_gw host is not configured")
    cmd = [client.ssh_bin, *client.ssh_options, client.host, "python3", "-", remote_dir]
    proc = subprocess.run(
        cmd,
        input=REMOTE_PROGRESS_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Remote progress query failed: "
            f"rc={proc.returncode}, stderr={proc.stderr.strip()!r}"
        )
    return json.loads(proc.stdout)


def classify_case(job: dict[str, Any], progress: dict[str, Any]) -> str:
    expected = int(progress.get("expected_count", 0) or 0)
    complete = int(progress.get("complete_count", 0) or 0)
    if expected > 0 and complete >= expected:
        return "ready"
    if job.get("state") in TERMINAL_STATES:
        return "blocked"
    return "waiting"


def extract_failure_hint(job: dict[str, Any]) -> str | None:
    for line in reversed(job.get("stdout_tail", []) or []):
        text = str(line).strip()
        if not text:
            continue
        if "simulation output did not reach requested stop_time" in text:
            return text
        if text.startswith("Results are in:"):
            continue
        return text
    return None


def build_wait_signature(job: dict[str, Any], progress: dict[str, Any]) -> tuple[Any, ...]:
    short = progress.get("short") or []
    short_dir = short[0]["dir"] if short else None
    return (
        job.get("job_id"),
        job.get("state"),
        progress.get("complete_count"),
        progress.get("csv_count"),
        progress.get("expected_count"),
        short_dir,
    )


def build_rsync_ssh_command(client: ModelicaSshClient) -> str:
    return " ".join(
        shlex.quote(part) for part in [client.ssh_bin, *client.ssh_options]
    )


def sync_reduced_results(
    client: ModelicaSshClient,
    remote_dir: str,
    local_dir: Path,
) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-az",
        "--prune-empty-dirs",
        "--include",
        "*/",
        "--include",
        "*_res.csv",
        "--include",
        "run_params.txt",
        "--include",
        "stop_time_by_freq.csv",
        "--include",
        "sin_mag_by_freq.csv",
        "--include",
        "output_grid_by_freq.csv",
        "--exclude",
        "*",
        "-e",
        build_rsync_ssh_command(client),
        f"{client.host}:{remote_dir.rstrip('/')}/",
        f"{local_dir}/",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "rsync failed: "
            f"rc={proc.returncode}, stdout={proc.stdout.strip()!r}, stderr={proc.stderr.strip()!r}"
        )


def run_local_collect(results_dir: Path, collect_jobs: int) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "freq" / "collectFreqNominalParallel.py"),
        "--results_dir",
        str(results_dir),
        "--n_jobs",
        str(collect_jobs),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "local collect failed: "
            f"rc={proc.returncode}, stdout={proc.stdout.strip()!r}, stderr={proc.stderr.strip()!r}"
        )


def case_message_prefix(case: WatchCase, job: dict[str, Any], progress: dict[str, Any]) -> str:
    return (
        f"{case.label} job={case.job_id} state={job.get('state')} "
        f"complete={progress.get('complete_count')}/{progress.get('expected_count')} "
        f"csvs={progress.get('csv_count')}"
    )


def main() -> int:
    args = parse_args()
    client = build_client(args.host)
    log_path = Path(args.log_path) if args.log_path else None

    collected_cases: set[tuple[str, str, str]] = set()
    blocked_job_ids: dict[tuple[str, str, str], str] = {}
    wait_signatures: dict[tuple[str, str, str], tuple[Any, ...]] = {}

    while True:
        cases = load_watch_cases(args.submissions_jsonl)
        if not cases:
            append_log(log_path, "no cases found in submission logs")
            return 1

        waiting_cases = 0
        blocked_cases = 0

        for key, case in sorted(cases.items(), key=lambda item: item[1].base_dir):
            if key in collected_cases:
                continue

            try:
                job = client.result(case.job_id, tail_lines=8)
                remote_dir = remote_results_dir(job, case.base_dir)
                progress = fetch_remote_progress(client, remote_dir)
            except Exception as exc:  # noqa: BLE001
                waiting_cases += 1
                signature = (case.job_id, "query_error", str(exc))
                if wait_signatures.get(key) != signature or args.once:
                    append_log(
                        log_path,
                        f"waiting {case.label} job={case.job_id} query_error={exc}",
                    )
                    wait_signatures[key] = signature
                continue

            status = classify_case(job, progress)
            prefix = case_message_prefix(case, job, progress)

            if status == "ready":
                append_log(log_path, f"raw complete {prefix}")
                try:
                    sync_reduced_results(client, remote_dir, local_results_dir(case.base_dir))
                    run_local_collect(local_results_dir(case.base_dir), args.collect_jobs)
                except Exception as exc:  # noqa: BLE001
                    blocked_cases += 1
                    append_log(log_path, f"blocked {prefix} collect_error={exc}")
                    blocked_job_ids[key] = case.job_id
                    continue
                collected_cases.add(key)
                blocked_job_ids.pop(key, None)
                wait_signatures.pop(key, None)
                append_log(log_path, f"local collect done {case.label}")
                continue

            if status == "blocked":
                blocked_cases += 1
                if blocked_job_ids.get(key) != case.job_id or args.once:
                    hint = extract_failure_hint(job)
                    short = progress.get("short") or []
                    first_short = short[0]["dir"] if short else None
                    message = f"blocked {prefix}"
                    if first_short:
                        message += f" first_incomplete={first_short}"
                    if hint:
                        message += f" reason={hint}"
                    append_log(log_path, message)
                    blocked_job_ids[key] = case.job_id
                continue

            waiting_cases += 1
            signature = build_wait_signature(job, progress)
            if wait_signatures.get(key) != signature or args.once:
                short = progress.get("short") or []
                first_short = short[0]["dir"] if short else None
                message = f"waiting {prefix}"
                if first_short:
                    message += f" first_incomplete={first_short}"
                append_log(log_path, message)
                wait_signatures[key] = signature

        unresolved_cases = len(cases) - len(collected_cases)
        if args.once:
            return 1 if blocked_cases else 0
        if unresolved_cases == 0:
            return 0
        if waiting_cases == 0:
            return 1
        time.sleep(max(args.poll_seconds, 0.05))


if __name__ == "__main__":
    raise SystemExit(main())
