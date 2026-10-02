#!/usr/bin/env python3
"""Rerun the 1e-5 MW frequency sweeps with serial in-job parallelism.

The 1e-5 MW sweeps are the campaign's low-power long-horizon cases: each
point simulates a ~40e6 s settle phase at the forcing-window output
resolution (numberOfIntervals ~ 3.8e8). The original campaign jobs ran
16 points in parallel per sweep; the 15 highest-frequency points of each
sweep failed ``final_time_reaches_requested_stop`` with ``LOG_ASSERT |
out of memory`` inside ``delayZeroCrossing``. Live measurement during the
wide rerun showed each heavy omc run holds only ~0.3-0.4 GB RSS, so the
original failure was an OMC-internal allocation failure under the then-
current shared-host load, not linear RAM exhaustion by the sims alone.

This rerun keeps the sweep protocol exactly as the campaign driver built
it (same frequencies, settle times, output resolution, collect, and
verify) and changes only two operational parameters:

- ``--n_jobs 1``: points run serially in-job, so a failure isolates to a
  single point (replaced by the wide per-point rerun while this was
  underway);
- result reuse is left enabled (the driver's ``--no-reuse`` is dropped),
  so the 65 already-completed points are validated and reused and only
  the 15 failed points are simulated.

Each core is pinned to a dedicated worker so the two serial reruns never
share one machine.
"""

from __future__ import annotations

import argparse
import shlex
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
CORE_WORKERS = {"1r": "c0805", "9r": "c0807"}


def build_command(core: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    table = f"{campaign}/setpoints/setpoints_{core}.csv"
    results = f"{campaign}/freq/{core}/power_0p00001"
    logs = f"{campaign}/logs"
    py = f"PYTHONPATH={remote} python3.12"
    run = (
        f"{py} -m freq.runFreqNominalParallel --package legacy"
        f" --core_model {core} --power {POWER}"
        " --freq_min 1e-03 --freq_max 1e+01 --num_freq 80"
        " --sin_mag_auto --stop_time_mode min_cycles_after_ss"
        " --min_cycles_after_ss 12"
        f" --steady_state_table {table} --base_dir {results}"
        " --n_jobs 1 --reduced_csv_for_collect --cleanup_omc_artifacts"
        " --omc_timeout_seconds 0.0"
    )
    collect = (
        f"{py} -m freq.collectFreqNominalParallel"
        f" --results_dir {results} --plot --n_jobs 1"
    )
    verify = f"{py} -m freq.verify_campaign {results}"
    parts = [
        f"mkdir -p {results} {logs}",
        (
            f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
            f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}'
        ),
        (
            f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
            f' || {{ echo "tracked remote worktree is dirty" >&2;'
            f' git -C {remote} status --short; exit 3; }}'
        ),
        f"{run} 2>&1 | tee {logs}/freq_{core}_0p00001_rerun.log",
        f"{collect} 2>&1 | tee {logs}/freq_{core}_0p00001_rerun_collect.log",
        f"{verify} > {logs}/freq_{core}_0p00001_rerun_verify.json",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    import subprocess

    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()
    config = load_user_config()
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
    jobs = {}
    for core, worker in CORE_WORKERS.items():
        command = build_command(core, commit)
        name = f"paper-{CAMPAIGN}-freq-{core}-0p00001-rerun"
        workdir = f"{SCRATCH}/{CAMPAIGN}-freq-{core}-0p00001-rerun"
        print(f"[freq-{core}-0p00001-rerun] worker={worker} n_jobs=1 (reuse enabled)")
        job = client.submit(
            command,
            workdir=workdir,
            workers=[worker],
            name=name,
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        jobs[core] = job
        print(
            f"[freq-{core}-0p00001-rerun] submitted job {job['job_id']} "
            f"worker={job.get('worker')} state={job.get('state')}"
        )

    if args.no_wait:
        return 0

    failures = []
    for core, job in jobs.items():
        job_id = str(job["job_id"])
        print(f"[freq-{core}-0p00001-rerun] waiting for {job_id}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=80)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[freq-{core}-0p00001-rerun] job {job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            failures.append(core)
            print(f"[freq-{core}-0p00001-rerun] FAILED", file=sys.stderr)
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
    if failures:
        print(f"rerun FAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("1e-5 MW reruns complete: 1r and 9r sweep results, collection, and verification green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
