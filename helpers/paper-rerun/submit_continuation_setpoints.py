#!/usr/bin/env python3
"""Dispatch the documented low-power setpoint continuation campaign.

Follows the rerun guide section 4.2: low-power setpoint points that fail the
plain generator's late-window checks are regenerated with
``core.init.generateSetpointTableContinuation`` in a separate, documented
campaign (``00runs/paper-rerun-review-2026-09-continuation/``), warm-started
from the main campaign's qualified setpoint table. Publication thresholds
are unchanged; ``--accept_unconverged`` is never passed.

One job per core (the continuation chain is sequential by design); the two
cores run in parallel on the gateway worker pool.

Steady-state early termination (``--enable_steady_state``) is deliberately
NOT used: from the near-nominal default initialization the low-power points
look quiescent almost immediately, so OMC's -steadyStateTol criterion stops
the run at a non-converged point and the result fails
``final_time_reaches_requested_stop``. The protocol's long stop-time caps
(2e5/1e6/1e8 s) plus the per-power interval caps are the designed
convergence mechanism for these points.
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

REMOTE_WORKDIR = "~/git/SMD-MSRR-dev"
MAIN_CAMPAIGN = "paper-rerun-review-2026-09"
CONT_CAMPAIGN = "paper-rerun-review-2026-09-continuation"
SCRATCH = "~/tmp/SMD2"

CONTINUATION_TARGETS = {
    # core: (target powers for the continuation chain, descending internally)
    "1r": "1e-2,1e-3,1e-5",
    "9r": "1e-2,1e-5",
}


def expected_commit() -> str:
    import subprocess

    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()


def build_command(core: str, commit: str) -> str:
    remote = REMOTE_WORKDIR
    main = f"{remote}/00runs/{MAIN_CAMPAIGN}"
    cont = f"{remote}/00runs/{CONT_CAMPAIGN}"
    work = f"{cont}/setpoints/work_{core}"
    output = f"{cont}/setpoints/setpoints_{core}_continuation.csv"
    init_from = f"{main}/setpoints/setpoints_{core}.csv"
    log = f"{cont}/logs/continuation_{core}.log"
    py = f"PYTHONPATH={remote} python3.12"
    parts = [
        f"mkdir -p {work} {cont}/logs",
        (
            f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
            f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}'
        ),
        (
            f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
            f' || {{ echo "tracked remote worktree is dirty" >&2;'
            f' git -C {remote} status --short; exit 3; }}'
        ),
        (
            f"{py} -m core.init.generateSetpointTableContinuation"
            f" --core_model {core}"
            f" --powers {CONTINUATION_TARGETS[core]}"
            f" --init_from {init_from}"
            f" --qualification_profile publication"
            f" --work_dir {work}"
            f" --output {output}"
            f" 2>&1 | tee {log}"
        ),
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None, help="expected remote HEAD")
    parser.add_argument("--no-wait", action="store_true", help="submit and exit")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    commit = args.commit or expected_commit()
    config = load_user_config()
    host = get_config_str(config, "host")
    ssh_bin = get_config_str(config, "ssh_bin") or "ssh"
    client = ModelicaSshClient(
        host=host,
        agent_command=_default_agent_command(config),
        ssh_bin=ssh_bin,
        ssh_options=tuple(get_config_list(config, "ssh_options") or ()),
        jobs_root=get_config_str(config, "jobs_root"),
    )
    workers = get_config_list(config, "workers") or []
    max_tasks = get_config_int(config, "max_tasks_per_worker", 32) or 32
    modelica_bin_dir = get_config_str(config, "modelica_bin_dir")

    print(f"Remote commit pin: {commit}")
    jobs = {}
    for core in CONTINUATION_TARGETS:
        command = build_command(core, commit)
        name = f"paper-{CONT_CAMPAIGN.replace('paper-rerun-', '')}-setpoints-{core}"
        workdir = f"{SCRATCH}/{CONT_CAMPAIGN}-setpoints-{core}"
        print(f"[setpoints-{core}] powers={CONTINUATION_TARGETS[core]}")
        print(f"[setpoints-{core}] command:\n{command}")
        job = client.submit(
            command,
            workdir=workdir,
            workers=workers,
            name=name,
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        jobs[core] = job
        print(
            f"[setpoints-{core}] submitted job {job['job_id']} "
            f"worker={job.get('worker')} state={job.get('state')}"
        )

    if args.no_wait:
        return 0

    failures = []
    for core, job in jobs.items():
        job_id = str(job["job_id"])
        print(f"[setpoints-{core}] waiting for {job_id}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=80)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[setpoints-{core}] job {job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            failures.append(core)
            print(f"[setpoints-{core}] FAILED", file=sys.stderr)
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
    if failures:
        print(
            f"continuation campaign FAILED: {', '.join(failures)}", file=sys.stderr
        )
        return 1
    print("continuation campaign complete: 1r and 9r continuation tables written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
