#!/usr/bin/env python3
"""Settle-only diagnostic for the two-phase 1e-5 MW frequency rerun.

The 15 high-frequency 1e-5 MW points fail deterministically in OMC 1.27.0
(``LOG_ASSERT | out of memory`` in ``delayZeroCrossing``) at the forcing
onset (t=4e7 s), independent of worker load.  This diagnostic runs the
two most extreme points as settle-only cases: identical single-point
sweep invocation with the stop time fixed exactly at the forcing start
(4e7 s), so no forcing events are simulated.  It runs in an isolated
base dir (``power_0p00001_twophase_diag``) so the sweep's
``stop_time_by_freq.csv`` and case directories are untouched.

- If the 10 rad/s case (382M output rows) completes, the assertion is
  tied to the forcing-event machinery and the two-phase
  settle-then-restart design is viable.
- If it fails, the assertion is tied to the settle horizon itself and
  the two-phase path is dead; the campaign publishes the 10 feasible
  powers and documents the 1e-5 MW limitation.
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
SETTLE_STOP = "40000000"
SCRATCH = "~/tmp/SMD2"

# The smallest and largest of the 15 heavy grid points (verbatim from the
# campaign's stop_time_by_freq.csv).
DIAG_POINTS = {
    "1r": ("1.95496614309", "c0801"),
    "9r": ("10", "c0809"),
}


def build_command(core: str, omega: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    table = f"{campaign}/setpoints/setpoints_{core}.csv"
    results = f"{campaign}/freq/{core}/power_0p00001_twophase_diag"
    logs = f"{campaign}/logs"
    py = f"PYTHONPATH={remote} python3.12"
    run = (
        f"{py} -m freq.runFreqNominalParallel --package legacy"
        f" --core_model {core} --power {POWER}"
        f" --freq_min {omega} --freq_max {omega} --num_freq 1"
        " --sin_mag_auto --stop_time_mode fixed"
        f" --stop_time {SETTLE_STOP}"
        f" --steady_state_table {table} --base_dir {results}"
        " --n_jobs 1 --reduced_csv_for_collect --cleanup_omc_artifacts"
        " --omc_timeout_seconds 0.0"
    )
    parts = [
        f"mkdir -p {results} {logs}",
        f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
        f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}',
        f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
        f' || {{ echo "tracked remote worktree is dirty" >&2;'
        f' git -C {remote} status --short; exit 3; }}',
        f"{run} 2>&1 | tee {logs}/freq_{core}_twophase_diag_{omega}.log",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


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
    for core, (omega, worker) in DIAG_POINTS.items():
        name = f"paper-{CAMPAIGN}-freq-{core}-twophase-diag-{omega}"
        job = client.submit(
            build_command(core, omega, commit),
            workdir=f"{SCRATCH}/{CAMPAIGN}-freq-{core}-twophase-diag",
            workers=[worker],
            name=name,
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        jobs[core] = (str(job.get("job_id")), omega)
        print(f"[{name}] job={jobs[core][0]} worker={worker} omega={omega}", flush=True)
    if args.no_wait:
        return 0

    for core, (job_id, omega) in jobs.items():
        print(f"[{core}/{omega}] waiting for {job_id}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=60)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{core}/{omega}] job={job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            print(f"two-phase diagnostic FAILED: {core}/{omega}", file=sys.stderr)
            return 1
    print("two-phase diagnostic green: settle-only completes for both extreme points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
