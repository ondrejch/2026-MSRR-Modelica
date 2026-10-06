#!/usr/bin/env python3
"""Final campaign tail: plots (10 feasible powers) + inventory + sync.

The 1e-5 MW frequency sweep is excluded from the published Bode
comparison (owner-approved path 3): its 15 high-frequency points are
infeasible on OMC v1.27.0-cmake (deterministic internal ``LOG_ASSERT |
out of memory`` in ``delayZeroCrossing`` during the documented 4e7 s
low-power settle horizon; see the campaign's excluded_powers.json for
the full evidence trail).  The 65 low-frequency 1e-5 points completed
by the original campaign job remain in the tree as partial data.

This tail runs, on the cluster:

1. the driver's plots job verbatim with the Bode ``--powers`` list
   restricted to the 10 feasible powers (1e-4 .. 1.2 MW); the startup,
   transient, and 1.0 MW freq-time-example figures are unchanged;
2. the driver's inventory job verbatim (campaign_summary.json +
   SHA256SUMS + completed_utc.txt);

then syncs the campaign tree to ``00runs/paper-rerun-review-2026-09/``
locally with the driver's rsync command.  The two FAILED 1e-5 campaign
records stay FAILED in the gateway state file as the honest record;
nothing in the state file is rewritten.
"""

from __future__ import annotations

import argparse
import json
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
CAMPAIGN_DIR = f"{REMOTE}/00runs/{CAMPAIGN}"
SCRATCH = "~/tmp/SMD2"
LOCAL_DEST = "00runs/paper-rerun-review-2026-09"

# The 10 feasible powers, driver formatting (f"{p:.12g}").
BODE_POWERS = ["0.0001", "0.001", "0.01", "0.1", "0.2", "0.4", "0.6", "0.8", "1", "1.2"]

EXCLUDED_POWERS_NOTE = {
    "campaign": CAMPAIGN,
    "excluded_powers_mw": [1e-05],
    "decision": (
        "publish the 10 feasible powers (1e-4..1.2 MW); the 1e-5 MW "
        "frequency sweep is excluded from the published Bode comparison"
    ),
    "rationale": (
        "OMC v1.27.0-cmake raises an internal allocation assert "
        "(LOG_ASSERT | debug | out of memory in delayZeroCrossing) during "
        "the documented low-power settle horizon (factor=400, 4.0e7 s at "
        "1e-5 MW). The failure is deterministic on both the 1r and 9r "
        "models and independent of worker load: the original 16-parallel "
        "campaign job, a serial two-worker rerun, a 3-per-worker wide "
        "per-point rerun, and settle-only diagnostics (no forcing events) "
        "all fail identically at ~1-2 GB RSS on 250 GB idle workers. The "
        "1e-4 MW horizon (4.0e6 s) completes cleanly, locating the "
        "failure at the 1e-5-specific horizon."
    ),
    "partial_data": (
        "freq/<core>/power_0p00001 holds the 65 low-frequency points "
        "(0.001-1.73983 rad/s) completed by the original campaign job; "
        "the 15 high-frequency points (1.95497-10 rad/s) are absent"
    ),
    "evidence": {
        "omc_version": "v1.27.0-cmake",
        "original_campaign_jobs": {
            "1r": "20260901T021536Z-abaeda9b",
            "9r": "20260901T021544Z-19d80d45",
        },
        "serial_rerun_jobs": {
            "1r": "20260901T043114Z-cb3c38b9",
            "9r": "20260901T043115Z-ed4e6f69",
        },
        "wide_rerun_jobs": (
            "27 single-point jobs 20260901T045612Z-f22b46f8 .. "
            "20260901T045631Z-ad04b609 (all FAILED exit 1, same assert)"
        ),
        "settle_only_diagnostics": {
            "1r_1.95497": "20260901T053526Z-34b8360b",
            "9r_10.0": "20260901T053527Z-aeaf5a5b",
        },
    },
    "date_utc": "2026-09-01",
    "approved_by": "owner (path 3 of the documented escalation options)",
}


def guard_block(commit: str) -> list[str]:
    return [
        f'test "$(git -C {REMOTE} rev-parse HEAD)" = {shlex.quote(commit)}'
        f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}',
        f'test -z "$(git -C {REMOTE} status --porcelain --untracked-files=no)"'
        f' || {{ echo "tracked remote worktree is dirty" >&2;'
        f' git -C {REMOTE} status --short; exit 3; }}',
    ]


def plots_command(commit: str) -> str:
    py = f"PYTHONPATH={REMOTE} python3.12"
    campaign = CAMPAIGN_DIR
    note_json = json.dumps(EXCLUDED_POWERS_NOTE, indent=2, sort_keys=True)
    note_path = f"{campaign}/metadata/excluded_powers.json"
    write_note_py = (
        "from pathlib import Path; "
        f"note = {json.dumps(note_json)}; "
        f"p = Path({note_path!r}); p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(note); print('excluded_powers.json written')"
    )
    parts = [
        f"mkdir -p {campaign}/figures/startup {campaign}/figures/transients"
        f" {campaign}/figures/frequency {campaign}/.mplconfig {campaign}/logs"
        f" {campaign}/metadata",
        f"export MPLBACKEND=Agg MPLCONFIGDIR={campaign}/.mplconfig",
        *guard_block(commit),
        f"{py} -c {shlex.quote(write_note_py)}"
        f" 2>&1 | tee {campaign}/logs/plots_excluded_powers.log",
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
    parts.append(
        f"{py} -m freq.plotBodeCompareCoreModels --results_root {campaign}/freq"
        f" --out_dir {campaign}/figures/frequency"
        " --powers " + " ".join(BODE_POWERS)
    )
    parts.append(
        f"{py} -m freq.plotFreqTimeCompareCoreModels --results_root"
        f" {campaign}/freq_time_example --power 1.0 --freq 0.1"
        f" --out_path {campaign}/figures/frequency/MSRR_freq_nominal_time_example.png"
    )
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def inventory_command() -> str:
    py = f"PYTHONPATH={REMOTE} python3.12"
    campaign = CAMPAIGN_DIR
    inventory_python = (
        "import os; from pathlib import Path; import json; "
        f"r=Path(os.path.expanduser({campaign!r})); "
        "files=[p for p in r.rglob('*') if p.is_file()]; "
        "payload={'file_count':len(files),"
        "'csv_count':sum(p.suffix=='.csv' for p in files),"
        "'png_count':sum(p.suffix=='.png' for p in files),"
        "'json_count':sum(p.suffix=='.json' for p in files),"
        "'total_bytes':sum(p.stat().st_size for p in files)}; "
        "(r/'campaign_summary.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\\n'); "
        "print(json.dumps(payload,sort_keys=True))"
    )
    parts = [
        f"date -u +%Y-%m-%dT%H:%M:%SZ > {campaign}/metadata/completed_utc.txt",
        f"{py} -c {shlex.quote(inventory_python)} 2>&1 | tee {campaign}/logs/inventory.log",
        f"find {campaign} -type f ! -name SHA256SUMS -print0 | sort -z |"
        f" xargs -0 sha256sum > {campaign}/SHA256SUMS",
    ]
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


def sync_local(client: ModelicaSshClient) -> None:
    ssh_command = f"{client.ssh_bin} {' '.join(client.ssh_options)}"
    cmd = [
        "rsync",
        "-az",
        "--partial",
        "--info=progress2",
        "-e",
        ssh_command,
        f"{client.host}:{CAMPAIGN_DIR.rstrip('/')}/",
        f"{LOCAL_DEST}/",
    ]
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"rsync failed with exit code {proc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--plots-worker", default="c0803")
    parser.add_argument("--inventory-worker", default="c0802")
    parser.add_argument("--skip-sync", action="store_true")
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

    def run_job(name: str, command: str, worker: str, workdir: str) -> None:
        print(f"Remote commit pin: {commit}; {name} on {worker}", flush=True)
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
        job_id = str(job.get("job_id"))
        print(f"[{name}] job={job_id} worker={job.get('worker')}", flush=True)
        final = client.wait(job_id, poll_seconds=args.poll_seconds)
        detail = client.result(job_id, tail_lines=80)
        state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        code = int(code) if code is not None else None
        print(f"[{name}] job={job_id} state={state} exit_code={code}", flush=True)
        if not (state == "COMPLETED" and code in (None, 0)):
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
            raise RuntimeError(f"{name} job {job_id} failed (state={state})")

    run_job(
        f"paper-{CAMPAIGN}-final-plots",
        plots_command(commit),
        args.plots_worker,
        f"{SCRATCH}/{CAMPAIGN}-final-plots",
    )
    run_job(
        f"paper-{CAMPAIGN}-final-inventory",
        inventory_command(),
        args.inventory_worker,
        f"{SCRATCH}/{CAMPAIGN}-final-inventory",
    )
    if args.skip_sync:
        print("sync skipped (--skip-sync)")
        return 0
    print("syncing campaign tree locally (99 GB)...", flush=True)
    sync_local(client)
    print("final tail complete: plots (10 powers) + inventory + sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
