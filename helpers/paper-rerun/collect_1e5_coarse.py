#!/usr/bin/env python3
"""Collect + verify summary for the completed coarse-grid 1e-5 MW sweeps.

The 30 coarse-grid point jobs already ran (submit_freq_1e5_coarse_rerun.py
wave, all COMPLETED).  This script only runs the per-core collection:
rebuild the sweep mapping files from the per-case manifests, restore the
80-point grid in run_params.txt, and run the collector in legacy-request
mode (the original sweep_request.manifest.json was overwritten by earlier
per-point rerun attempts and its fingerprint is unrecoverable; every
per-case manifest remains fully provenanced, and the legacy-mode choice
is recorded in the collection manifest).

Fixes over the failed embedded collect: the mapping rebuild uses a list
comprehension (a bare `for` after simple statements is a SyntaxError).
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

REBUILD_MAPPING_PY = (
    "import json, os; from pathlib import Path; "
    "r = Path(os.path.expanduser({results!r})); "
    "rows = [(float(json.loads(p.read_text())['manifest']['overrides']['perturbationOmega']), "
    "float(json.loads(p.read_text())['manifest']['stop_time']), "
    "int(json.loads(p.read_text())['manifest']['number_of_intervals'])) "
    "for p in sorted(r.glob('freq*/*_res.manifest.json'))]; "
    "rows.sort(key=lambda x: x[0]); "
    "(r / 'stop_time_by_freq.csv').write_text('frequency_rad_s,stop_time_s\\n' + "
    "''.join(f'{{w:.12g}},{{s:.12g}}\\n' for w, s, _ in rows)); "
    "(r / 'output_grid_by_freq.csv').write_text('frequency_rad_s,stop_time_s,number_of_intervals,output_step_s\\n' + "
    "''.join(f'{{w:.12g}},{{s:.12g}},{{n:d}},{{s/n:.12g}}\\n' for w, s, n in rows)); "
    "print('rebuilt mapping files from', len(rows), 'case manifests')"
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


def collect_command(core: str, commit: str) -> str:
    remote = REMOTE
    campaign = f"{remote}/00runs/{CAMPAIGN}"
    results = f"{campaign}/freq/{core}/power_0p00001"
    params = f"{results}/run_params.txt"
    logs = f"{campaign}/logs"
    py = f"PYTHONPATH={remote} python3.12"
    parts = [
        f"mkdir -p {logs}",
        *guard_block(commit),
        f"{py} -c {shlex.quote(REBUILD_MAPPING_PY.format(results=results))}"
        f" 2>&1 | tee {logs}/freq_{core}_0p00001_coarse_mapping_rebuild.log",
        f"sed -i 's/^freq_min\\b.*/freq_min\\t0.001/;"
        f" s/^freq_max\\b.*/freq_max\\t10/;"
        f" s/^num_freq\\b.*/num_freq\\t80/' {params}",
        # The stale 1-point sweep_request.manifest.json left by the earlier
        # per-point rerun attempts supersedes legacy mode when present and
        # hides the other 79 points; its original 80-point content and
        # fingerprint are unrecoverable, so remove it for the legacy-mode
        # collection (recorded in the collection manifest).
        f"rm -f {results}/sweep_request.manifest.json",
        f"{py} -m freq.collectFreqNominalParallel --results_dir {results}"
        f" --plot --n_jobs 1 --allow_legacy_request_manifest"
        f" 2>&1 | tee {logs}/freq_{core}_0p00001_coarse_collect.log",
        f"grep -a -E 'Collection status|Successfully processed|failed' "
        f"{logs}/freq_{core}_0p00001_coarse_collect.log"
        f" > {logs}/freq_{core}_0p00001_coarse_verify_summary.txt || true",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--core", action="append", choices=("1r", "9r"))
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    commit = args.commit or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd="."
    ).strip()
    config = load_user_config()
    workers = get_config_list(config, "workers") or []
    cores = args.core or ["1r", "9r"]
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
    for i, core in enumerate(cores):
        job = {
            "command": collect_command(core, commit),
            "workdir": f"{SCRATCH}/{CAMPAIGN}-freq-{core}-coarse-collect2",
            "name": f"paper-{CAMPAIGN}-freq-{core}-0p00001-coarse-collect2",
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
    print("1e-5 MW coarse collection complete on", ", ".join(cores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
