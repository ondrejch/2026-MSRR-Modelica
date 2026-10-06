#!/usr/bin/env python3
"""Wide rerun of the 1e-5 MW frequency sweep points, one point per job.

The 15 highest-frequency points of each 1e-5 MW sweep (1.95..10 rad/s)
need a ~40e6 s settle phase at forcing-window output resolution
(numberOfIntervals ~ 3.8e8).  The original campaign job (16 points per
worker) lost those points with ``LOG_ASSERT | out of memory`` inside
``delayZeroCrossing``; live measurement during this rerun shows each
heavy omc run holds only ~0.3-0.4 GB RSS (peaking well under 2 GB), so
the original failure was not plain RAM exhaustion by the sims alone --
the workers are shared PBS hosts and the assertion is an OMC-internal
allocation failure under the then-current load.  This dispatches every
remaining point as its own job at 3 heavy points per worker (measured
safe: ~1-3 GB aggregate), in two waves:

wave 1: 27 jobs (3 per worker on all 9 workers)
wave 2:  3 jobs (workers 1-3, free after wave 1)

then one collect + verify job per core.  The sweep protocol is identical
to the campaign driver's frequency job (same frequencies, stop times,
output resolution); only the parallelism and job granularity change, and
result reuse is enabled so the 65 completed low-frequency points and any
completed point are validated and reused, never resimulated.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys

from helpers.omc_gw.modelica_ssh_client import (
    _default_agent_command,
    ModelicaSshClient,
)
from helpers.omc_gw.omc_gw_config import (
    get_config_int,
    get_config_list,
    get_config_str,
    load_user_config,
)

REMOTE = "~/git/SMD-MSRR-dev"
CAMPAIGN = "paper-rerun-review-2026-09"
POWER = "1e-05"
SCRATCH = "~/tmp/SMD2"
MAX_PER_WORKER = 3

# The 15 failed high-frequency grid values, verbatim from the campaign's
# stop_time_by_freq.csv (the values the original sweep actually used).
OMEGA_1R = [
    "1.95496614309", "2.19670709079", "2.46834046707", "2.7735626142",
    "3.11652694493", "3.50190046143", "3.9349272631", "4.42149990737",
    "4.96823959473", "5.58258626886", "6.2728998582", "7.04857403645",
    "7.92016405019", "8.89953035289", "10",
]


def tag(omega: str) -> str:
    return f"{float(omega):09.5f}"


def guard_block(commit: str) -> list[str]:
    remote = REMOTE
    return [
        f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
        f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}',
        f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
        f' || {{ echo "tracked remote worktree is dirty" >&2;'
        f' git -C {remote} status --short; exit 3; }}',
    ]


def point_command(core: str, omega: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    table = f"{campaign}/setpoints/setpoints_{core}.csv"
    results = f"{campaign}/freq/{core}/power_0p00001"
    logs = f"{campaign}/logs"
    t = tag(omega)
    py = f"PYTHONPATH={remote} python3.12"
    run = (
        f"{py} -m freq.runFreqNominalParallel --package legacy"
        f" --core_model {core} --power {POWER}"
        f" --freq_min {omega} --freq_max {omega} --num_freq 1"
        " --sin_mag_auto --stop_time_mode min_cycles_after_ss"
        " --min_cycles_after_ss 12"
        f" --steady_state_table {table} --base_dir {results}"
        " --n_jobs 1 --reduced_csv_for_collect --cleanup_omc_artifacts"
        " --omc_timeout_seconds 0.0"
    )
    parts = [f"mkdir -p {results} {logs}", *guard_block(commit), run]
    tail = f" 2>&1 | tee {logs}/freq_{core}_0p00001_wide_{t}.log"
    parts[-1] = run + tail
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def collect_command(core: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    results = f"{campaign}/freq/{core}/power_0p00001"
    logs = f"{campaign}/logs"
    py = f"PYTHONPATH={remote} python3.12"
    parts = [
        f"mkdir -p {logs}",
        *guard_block(commit),
        f"{py} -m freq.collectFreqNominalParallel --results_dir {results}"
        f" --plot --n_jobs 1 2>&1 | tee {logs}/freq_{core}_0p00001_wide_collect.log",
        f"{py} -m freq.verify_campaign {results}"
        f" > {logs}/freq_{core}_0p00001_wide_verify.json",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def submit(client, job_cfg: dict) -> dict:
    return client.submit(
        job_cfg["command"],
        workdir=job_cfg["workdir"],
        workers=[job_cfg["worker"]],
        name=job_cfg["name"],
        tasks=1,
        max_tasks_per_worker=job_cfg["max_tasks"],
        modelica_bin_dir=job_cfg["modelica_bin_dir"],
        wait_for_slot=True,
        poll_seconds=job_cfg["poll_seconds"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()
    config = load_user_config()
    workers = get_config_list(config, "workers") or []
    if len(workers) < 3:
        print(f"need at least 3 workers for the wave layout, got {workers}", file=sys.stderr)
        return 2
    client = ModelicaSshClient(
        host=get_config_str(config, "host"),
        agent_command=_default_agent_command(config),
        ssh_bin=get_config_str(config, "ssh_bin") or "ssh",
        ssh_options=tuple(get_config_list(config, "ssh_options") or ()),
        jobs_root=get_config_str(config, "jobs_root"),
    )
    max_tasks = get_config_int(config, "max_tasks_per_worker", 32) or 32
    modelica_bin_dir = get_config_str(config, "modelica_bin_dir")

    print(f"Remote commit pin: {commit}")
    print(f"Workers ({len(workers)}): {', '.join(workers)}; max heavy jobs per worker: {MAX_PER_WORKER}")

    # Point jobs: 15 frequencies x 2 cores = 30; waves of len(workers)*MAX_PER_WORKER.
    jobs = []
    for core in ("1r", "9r"):
        for omega in OMEGA_1R:
            t = tag(omega)
            jobs.append(
                {
                    "command": point_command(core, omega, commit),
                    "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-{t}-wide",
                    "name": f"paper-{CAMPAIGN}-freq-{core}-{t}-wide-rerun",
                    "core": core,
                    "omega": omega,
                }
            )
    wave_size = len(workers) * MAX_PER_WORKER
    waves = [jobs[i : i + wave_size] for i in range(0, len(jobs), wave_size)]

    all_jobs: list[dict] = []
    for wave_no, wave in enumerate(waves, 1):
        print(f"--- wave {wave_no}: {len(wave)} jobs ---")
        wave_entries: list[dict] = []
        for i, job in enumerate(wave):
            job = dict(job)
            job["worker"] = workers[i % len(workers)]
            job["max_tasks"] = max_tasks
            job["modelica_bin_dir"] = modelica_bin_dir
            job["poll_seconds"] = args.poll_seconds
            submitted = submit(client, job)
            job_id = str(submitted.get("job_id"))
            job["job_id"] = job_id
            wave_entries.append(job)
            all_jobs.append(job)
            print(
                f"[{job['name']}] job={job_id} worker={job['worker']} "
                f"omega={job['omega']}",
                flush=True,
            )
        if args.no_wait:
            return 0
        failed = []
        for job in wave_entries:
            job_id = str(job["job_id"])
            print(f"[{job['name']}] waiting for {job_id}", flush=True)
            final = client.wait(job_id, poll_seconds=args.poll_seconds)
            detail = client.result(job_id, tail_lines=60)
            state = str(final.get("state", detail.get("state", "UNKNOWN")))
            code = detail.get("exit_code", final.get("exit_code"))
            code = int(code) if code is not None else None
            print(f"[{job['name']}] job={job_id} state={state} exit_code={code}")
            if not (state == "COMPLETED" and code in (None, 0)):
                failed.append(job)
                for line in detail.get("stderr_tail", []) or []:
                    print(f"  {line}", file=sys.stderr)
        if failed:
            print(
                f"wave {wave_no} FAILED: "
                + ", ".join(f"{j['core']}/{j['omega']}" for j in failed),
                file=sys.stderr,
            )
            return 1

    if args.no_wait:
        return 0

    # Collect + verify, one job per core on two distinct (now idle) workers.
    for core, worker in zip(("1r", "9r"), workers[:2]):
        job_cfg = {
            "command": collect_command(core, commit),
            "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-wide-collect",
            "name": f"paper-{CAMPAIGN}-freq-{core}-0p00001-wide-collect",
            "worker": worker,
            "max_tasks": max_tasks,
            "modelica_bin_dir": modelica_bin_dir,
            "poll_seconds": args.poll_seconds,
        }
        submitted = submit(client, job_cfg)
        job_id = str(submitted.get("job_id"))
        print(f"[{job_cfg['name']}] job={job_id} worker={worker}", flush=True)
        if args.no_wait:
            return 0
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=80)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{job_cfg['name']}] job={job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            print(f"collect FAILED for {core}", file=sys.stderr)
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            return 1

    print("1e-5 MW wide rerun complete: all 30 points, collection, and verification green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
