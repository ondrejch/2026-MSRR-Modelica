#!/usr/bin/env python3
"""Partial plots stage: startup + transients figures only.

The campaign's plots stage (all 7 paper-facing figures) is gated behind
the 1e-5 MW frequency rerun.  The startup and transient figures depend
only on already-completed raw results, so this dispatch runs just that
subset -- the driver's plot commands verbatim -- to make the figures
available without waiting for the frequency points.  The full plots
stage still runs at campaign completion and regenerates every figure
into the same paths (the frequency subset plus these, idempotently).
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
SCRATCH = "~/tmp/SMD2"
WORKER = "c0804"


def build_command(commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    py = f"PYTHONPATH={remote} python3.12"
    parts = [
        f"mkdir -p {campaign}/figures/startup {campaign}/figures/transients"
        f" {campaign}/.mplconfig {campaign}/logs",
        f"export MPLBACKEND=Agg MPLCONFIGDIR={campaign}/.mplconfig",
        f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
        f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}',
        f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
        f' || {{ echo "tracked remote worktree is dirty" >&2;'
        f' git -C {remote} status --short; exit 3; }}',
    ]
    for core in ("1r", "9r"):
        run_dir = f"{campaign}/startup/{core}"
        parts.append(
            f"{py} -m startup.plotApproachToCriticalityPhase4 --core_model {core}"
            f" --run_dir {run_dir}"
            f" --out {campaign}/figures/startup/startup_phase1to4_{core}.png"
            f" --signed_log_out {campaign}/figures/startup/startup_phase1to4_signedlog_{core}.png"
        )
        parts.append(
            f"{py} -m startup.plotStartUpTo1MW --core_model {core}"
            f" --run_dir {run_dir}"
            f" --out {campaign}/figures/startup/startup_to1MW_{core}.png"
        )
    parts.append(
        f"{py} -m transients.plot_nonlinear_steps --package legacy"
        f" --outputs_dir {campaign}/transients --fig_dir {campaign}/figures/transients"
        " --core_models 1r 9r"
    )
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--worker", default=WORKER)
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

    name = f"paper-{CAMPAIGN}-plots-startup-transients"
    workdir = f"{SCRATCH}/{CAMPAIGN}-plots-partial"
    print(f"Remote commit pin: {commit}; worker: {args.worker}")
    job = client.submit(
        build_command(commit),
        workdir=workdir,
        workers=[args.worker],
        name=name,
        tasks=1,
        max_tasks_per_worker=max_tasks,
        modelica_bin_dir=modelica_bin_dir,
        wait_for_slot=True,
        poll_seconds=args.poll_seconds,
    )
    job_id = str(job.get("job_id"))
    print(f"[{name}] submitted job {job_id} worker={job.get('worker')}")
    if args.no_wait:
        return 0
    final = client.wait(job_id, poll_seconds=args.poll_seconds)
    detail = client.result(job_id, tail_lines=60)
    state = str(final.get("state", detail.get("state", "UNKNOWN")))
    code = detail.get("exit_code", final.get("exit_code"))
    code = int(code) if code is not None else None
    print(f"[{name}] job {job_id} state={state} exit_code={code}")
    if not (state == "COMPLETED" and code in (None, 0)):
        for line in detail.get("stderr_tail", []) or []:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("startup + transients figures written under "
          f"{CAMPAIGN}/figures/{{startup,transients}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
