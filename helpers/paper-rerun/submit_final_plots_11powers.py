#!/usr/bin/env python3
"""Regenerate the campaign frequency figures with all 11 powers and
replace metadata/excluded_powers.json with the resolution note.

The 1e-5 MW sweeps are complete again (fresh coarse-grid sweeps, 80/80
points verified publication-eligible on both cores), so the Bode set is
regenerated at 11 powers and the earlier exclusion note is superseded by
a resolution note documenting the root cause, the remedy, the measured
protocol-vs-coarse equivalence, and the archive location of the
protocol-grid attempt.
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

# All 11 powers, driver formatting (f"{p:.12g}").
BODE_POWERS = [
    "1e-05", "0.0001", "0.001", "0.01", "0.1",
    "0.2", "0.4", "0.6", "0.8", "1", "1.2",
]

RESOLUTION_NOTE = {
    "campaign": CAMPAIGN,
    "supersedes": "excluded_powers.json (2026-09-01 exclusion of the 1e-5 MW sweep)",
    "status": "RESOLVED: the complete 80-point 1e-5 MW sweeps (both cores) are "
    "publication-eligible (verify_campaign accepted 80/80, both cores)",
    "root_cause": (
        "OMC v1.27.0-cmake delay ring-buffer int32 overflow: the models' "
        "pump/region trip delays (delay(time, tripTime=1e12, ...)) append one "
        "16-byte entry per communication point to never-trimmed delay buffers; "
        "at 2^26=67,108,864 entries the next doubling requests 2^27*16 = 2^31 "
        "bytes, overflowing the 32-bit bufferSize*itemSize in expandRingBuffer "
        "(ringbuffer.c:114); realloc returns NULL, the 'out of memory' assert "
        "prints, and the next delay access segfaults (delay.c:250 / "
        "delay.c:106). Deterministic at ~67M output points; the protocol grid "
        "gave the 1e-5 MW points 74.8M-382M intervals."
    ),
    "remedy": (
        "Same protocol (4e7 s settle horizon, nFloor switch, forcingTimeStep, "
        "tolerance, solver); only the settle output grid changed via "
        "--disable_low_power_auto_output_grid --output_interval_mode "
        "fixed_rate --output_intervals_per_second 0.00005 (~2003 intervals). "
        "The forcing window stays dense (0.05 s forcing-event rows) and the "
        "fit uses only t >= perturbation_start."
    ),
    "equivalence_measurement": (
        "At omega=1.73983 rad/s (the protocol-grid marginal survivor) on both "
        "cores, the collector's fit agrees between grids to 0.06% gain and "
        "0.002 deg phase. Comparison pair preserved under "
        "freq/1r/power_0p00001_coarse_check (coarse) and "
        "freq/1r/power_0p00001_proto_check (protocol grid)."
    ),
    "archive": (
        "The previous protocol-grid results dir (65 completed + 15 crashed "
        "points, with sweep metadata overwritten by earlier per-point rerun "
        "attempts) is preserved by rename under "
        "freq/<core>/power_0p00001_protocol_archive. Nothing was destroyed."
    ),
    "date_utc": "2026-09-01",
    "approved_by": "owner (investigate-and-remedy directive)",
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
    note_path = f"{campaign}/metadata/excluded_powers.json"
    write_note_py = (
        "from pathlib import Path; "
        f"note = {json.dumps(json.dumps(RESOLUTION_NOTE, indent=2, sort_keys=True))}; "
        f"import os; p = Path(os.path.expanduser({note_path!r})); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(note); print('resolution note written')"
    )
    parts = [
        f"mkdir -p {campaign}/figures/frequency {campaign}/.mplconfig {campaign}/logs"
        f" {campaign}/metadata",
        f"export MPLBACKEND=Agg MPLCONFIGDIR={campaign}/.mplconfig",
        *guard_block(commit),
        f"{py} -c {shlex.quote(write_note_py)} 2>&1 | tee {campaign}/logs/plots_resolution_note.log",
    ]
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--worker", default="c0803")
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

    print(f"Remote commit pin: {commit}", flush=True)
    submitted = client.submit(
        plots_command(commit),
        workdir=f"{SCRATCH}/{CAMPAIGN}-final-plots-11p",
        workers=[args.worker],
        name=f"paper-{CAMPAIGN}-final-plots-11p",
        tasks=1,
        max_tasks_per_worker=max_tasks,
        modelica_bin_dir=modelica_bin_dir,
        wait_for_slot=True,
        poll_seconds=args.poll_seconds,
    )
    job_id = str(submitted.get("job_id"))
    print(f"[final-plots-11p] job={job_id} worker={args.worker}", flush=True)
    final = client.wait(job_id, poll_seconds=args.poll_seconds)
    detail = client.result(job_id, tail_lines=60)
    state = str(final.get("state", detail.get("state", "UNKNOWN")))
    code = detail.get("exit_code", final.get("exit_code"))
    code = int(code) if code is not None else None
    print(f"[final-plots-11p] state={state} exit_code={code}")
    if not (state == "COMPLETED" and code in (None, 0)):
        for line in detail.get("stderr_tail", []) or []:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("final 11-power plots complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
