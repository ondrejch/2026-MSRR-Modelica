#!/usr/bin/env python3
"""Re-attach to the wide 1e-5 MW frequency rerun after the submitter died.

The submitter (submit_freq_1e5_wide_rerun.py) submitted all 27 wave-1
point jobs and then crashed on a bug in its wait loop before submitting
wave 2.  The 27 remote jobs keep running independently of the client.
This script parses the submitted job ids from the submitter log, waits
for them, then submits and waits for the 3 wave-2 points and the two
per-core collect + verify jobs.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

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
from submit_freq_1e5_wide_rerun import (
    collect_command,
    CAMPAIGN,
    OMEGA_1R,
    point_command,
    SCRATCH,
    tag,
)

LOG_FILE = Path("00runs/tmp/freq1e5_wide_rerun.log")
JOB_RE = re.compile(r"^\[paper-paper-rerun-review-2026-09-freq-(?P<core>1r|9r)-(?P<tag>\d+\.\d+)-wide-rerun\] job=(?P<job_id>\S+) worker=(?P<worker>\S+) omega=(?P<omega>\S+)")


def parse_submitted(log: Path) -> list[dict]:
    entries = []
    for line in log.read_text().splitlines():
        m = JOB_RE.match(line)
        if m:
            entries.append(m.groupdict())
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    entries = parse_submitted(LOG_FILE)
    if len(entries) != 27:
        print(f"expected 27 submitted wave-1 jobs in {LOG_FILE}, found {len(entries)}", file=sys.stderr)
        return 2

    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()
    config = load_user_config()
    workers = get_config_list(config, "workers") or []
    client = ModelicaSshClient(
        host=get_config_str(config, "host"),
        agent_command=_default_agent_command(config),
        ssh_bin=get_config_str(config, "ssh_bin") or "ssh",
        ssh_options=tuple(get_config_list(config, "ssh_options") or ()),
        jobs_root=get_config_str(config, "jobs_root"),
    )
    max_tasks = get_config_int(config, "max_tasks_per_worker", 32) or 32
    modelica_bin_dir = get_config_str(config, "modelica_bin_dir")

    print(f"Re-attaching to {len(entries)} wave-1 jobs (commit pin {commit})")
    failed = []
    for e in entries:
        job_id = e["job_id"]
        label = f"{e['core']}-{e['tag']}"
        print(f"[{label}] waiting for {job_id}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=60)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{label}] job={job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            failed.append(label)
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
    if failed:
        print(f"wave 1 FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("wave 1 complete: all 27 points green")

    # Wave 2: the 3 remaining 9r points (indices 12-14 of the 9r block).
    wave2 = []
    for i, omega in enumerate(OMEGA_1R[12:15]):
        t = tag(omega)
        wave2.append(
            {
                "command": point_command("9r", omega, commit),
                "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-9r-{t}-wide",
                "name": f"paper-{CAMPAIGN}-freq-9r-{t}-wide-rerun",
                "worker": workers[i % len(workers)],
                "omega": omega,
            }
        )
    print(f"--- wave 2: {len(wave2)} jobs ---")
    for job in wave2:
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
        job["job_id"] = str(submitted.get("job_id"))
        print(
            f"[{job['name']}] job={job['job_id']} worker={job['worker']} "
            f"omega={job['omega']}",
            flush=True,
        )
    for job in wave2:
        job_id = str(job["job_id"])
        print(f"[{job['name']}] waiting for {job_id}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=60)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{job['name']}] job={job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            print(f"wave 2 FAILED: {job['omega']}", file=sys.stderr)
            return 1
    print("wave 2 complete: all 30 points green")

    # Collect + verify, one job per core on two distinct (now idle) workers.
    for core, worker in zip(("1r", "9r"), workers[:2]):
        job_cfg = {
            "command": collect_command(core, commit),
            "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-wide-collect",
            "name": f"paper-{CAMPAIGN}-freq-{core}-0p00001-wide-collect",
        }
        submitted = client.submit(
            job_cfg["command"],
            workdir=job_cfg["workdir"],
            workers=[worker],
            name=job_cfg["name"],
            tasks=1,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
        )
        job_id = str(submitted.get("job_id"))
        print(f"[{job_cfg['name']}] job={job_id} worker={worker}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=80)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{job_cfg['name']}] job={job_id} state={state} exit_code={code}")
        if not (state == "COMPLETED" and code in (None, 0)):
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            print(f"collect FAILED for {core}", file=sys.stderr)
            return 1

    print("1e-5 MW wide rerun complete: all 30 points, collection, and verification green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
