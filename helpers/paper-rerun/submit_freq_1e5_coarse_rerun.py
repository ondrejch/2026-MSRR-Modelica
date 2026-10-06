#!/usr/bin/env python3
"""Coarse-grid 1e-5 MW frequency sweeps: fresh, self-consistent completion.

Root cause (established 2026-09-01): the MSRR models' pump/region trip
delays (``delay(time, tripTime=1e12, ...)`` etc.) append one 16-byte
(time, value) entry per communication point to OpenModelica delay ring
buffers that are never trimmed (delayTime far exceeds the run length).
At 2**26 = 67,108,864 stored entries the buffer's next doubling requests
2**27 * 16 B = 2**31 bytes, which overflows the 32-bit
``bufferSize*itemSize`` computation in ``expandRingBuffer``
(ringbuffer.c:114, OMC v1.27.0-cmake); realloc returns NULL, the "out of
memory" assert prints (threadData=NULL), and the next delay access
segfaults.  The protocol's frequency-scaled output grid gives the 1e-5 MW
long-horizon points 74.8M-382M output intervals -- past the cliff; the
highest surviving point, omega=1.73983 rad/s, has 66.5M (a 1% margin).

Remedy (owner-approved): keep the entire low-power protocol (4e7 s
settle horizon, nFloor switch, forcingTimeStep cadence, tolerance,
solver) and change only the settle output grid via
``--disable_low_power_auto_output_grid --output_interval_mode fixed_rate
--output_intervals_per_second 0.00005`` (~2003 output intervals over the
full span).  The forcing window stays dense because every forcing event
(forcingTimeStep=0.05 s) is written to the CSV, and the transfer-function
fit uses only t >= perturbation_start.  Validated at omega=1.73983 rad/s
on both cores: the collector's fit agrees with the protocol-grid fit to
0.06% in gain and 0.002 deg in phase.

Why a FULL fresh sweep: the collector and verify_campaign enforce
sweep-common provenance (every case manifest must embed the same sweep
request reference).  The earlier per-point rerun attempts overwrote the
sweep-level request/mapping files and their fingerprints are
unrecoverable, so a mixed set (65 old + 15 new) can never aggregate.
Therefore the previous results dir was archived by rename to
``power_0p00001_protocol_archive`` (nothing destroyed; the protocol-grid
comparison case is additionally preserved under
``power_0p00001_proto_check``) and each core runs ONE runner invocation
over the full original 80-point spec into a fresh ``power_0p00001``,
followed by a normal-mode collect and verify_campaign.
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

FREQ_MIN = "0.001"
FREQ_MAX = "10"
NUM_FREQ = 80
N_JOBS = 12

GRID_FLAGS = (
    "--disable_low_power_auto_output_grid"
    " --output_interval_mode fixed_rate"
    " --output_intervals_per_second 0.00005"
)


def guard_block(commit: str) -> list[str]:
    remote = REMOTE
    return [
        f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
        f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}',
        f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
        f' || {{ echo "tracked remote worktree is dirty" >&2;'
        f' git -C {remote} status --short; exit 3; }}',
    ]


def sweep_command(core: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    table = f"{campaign}/setpoints/setpoints_{core}.csv"
    results = f"{campaign}/freq/{core}/power_0p00001"
    logs = f"{campaign}/logs"
    py = f"PYTHONPATH={remote} python3.12"
    run = (
        f"{py} -m freq.runFreqNominalParallel --package legacy"
        f" --core_model {core} --power {POWER}"
        f" --freq_min {FREQ_MIN} --freq_max {FREQ_MAX} --num_freq {NUM_FREQ}"
        " --sin_mag_auto --stop_time_mode min_cycles_after_ss"
        " --min_cycles_after_ss 12"
        f" --steady_state_table {table} --base_dir {results}"
        f" {GRID_FLAGS}"
        f" --n_jobs {N_JOBS} --reduced_csv_for_collect --cleanup_omc_artifacts"
        " --omc_timeout_seconds 0.0"
    )
    parts = [f"mkdir -p {results} {logs}", *guard_block(commit), run]
    parts[-1] = run + f" 2>&1 | tee {logs}/freq_{core}_0p00001_coarse_sweep.log"
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
        f" --plot --n_jobs 1 2>&1 | tee {logs}/freq_{core}_0p00001_coarse_collect.log",
        f"{py} -m freq.verify_campaign {results}"
        f" > {logs}/freq_{core}_0p00001_coarse_verify.json",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--core", action="append", choices=("1r", "9r"))
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()

    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()
    config = load_user_config()
    workers = get_config_list(config, "workers") or []
    if len(workers) < 2:
        print("need at least 2 workers", file=sys.stderr)
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
    cores = args.core or ["1r", "9r"]

    print(f"Remote commit pin: {commit}")
    for i, core in enumerate(cores):
        job = {
            "command": sweep_command(core, commit),
            "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-coarse-sweep2",
            "name": f"paper-{CAMPAIGN}-freq-{core}-0p00001-coarse-sweep2",
            "worker": workers[i % len(workers)],
        }
        submitted = client.submit(
            job["command"],
            workdir=job["workdir"],
            workers=[job["worker"]],
            name=job["name"],
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        job_id = str(submitted.get("job_id"))
        print(f"[{job['name']}] job={job_id} worker={job['worker']}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=60)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{job['name']}] state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            return 1

    if args.skip_collect:
        print("sweeps complete; collect skipped (--skip-collect)")
        return 0

    for core, worker in zip(cores, workers[: len(cores)]):
        job = {
            "command": collect_command(core, commit),
            "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-coarse-collect3",
            "name": f"paper-{CAMPAIGN}-freq-{core}-0p00001-coarse-collect3",
            "worker": worker,
        }
        submitted = client.submit(
            job["command"],
            workdir=job["workdir"],
            workers=[job["worker"]],
            name=job["name"],
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        job_id = str(submitted.get("job_id"))
        print(f"[{job['name']}] job={job_id} worker={worker}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=60)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{job['name']}] state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            print(f"collect FAILED for {core}", file=sys.stderr)
            return 1

    print("1e-5 MW coarse sweeps complete: fresh 80-point sweeps, collection, and verification green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
