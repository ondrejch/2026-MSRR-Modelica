#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewer-response frequency sensitivity: hA exponent at three frequencies.

Answers ANUCENE-D-26-00942 reviewer item R1.3a (TASK-20260901-01 Phase 1):
how does the FF^hAExp film-convection exponent shift the 1R nominal-power
(1.0 MW) frequency response at 0.005, 0.02, and 0.1 rad/s -- three points
bracketing the nominal-power roll-off?

Modeling approach (documented per the task card)
------------------------------------------------
Every case is one legacy ``simulate()`` run of the production frequency
vehicle ``MSRR.MSRRuhxNominalTrimNoTrips`` (``freq._common
.MODEL_NAME_BY_CORE['1r']``), driven purely through OpenModelica
``-override=`` payloads: the 1.0 MW setpoint-table row (same
``load_steady_state_overrides`` route as the production sweep), a 1 pcm
sinusoidal perturbation at the requested ``perturbationOmega``, and the new
component parameters ``core1R.fuelchannel.hAExp`` / ``heatExchanger.hAExp``
(both power-law twins varied together). The response amplitude and phase
come from the shared closed-form sine-fit machinery
(``freq._common.fit_sine_least_squares`` +
``phase_relative_to_perturbation_start_deg``), exactly as
``freq.collectFreqNominalParallel`` fits production sweeps.

Physics note on the default grid
--------------------------------
The hA power law is normalized at nominal flow: ``hA = hAnom*FF^hAExp``
with the pumps pinned at FF = 1, so at nominal prescribed flow the exponent
cancels exactly (``1^hAExp == 1``) and the gain/phase shifts of the FF = 1
rows are zero to solver noise -- that exact invariance is a reportable
reviewer answer. The default grid therefore also runs the same exponent
sweep at a reduced prescribed flow (0.66, the production ``flow_66pct``
operating point) where the exponent is observable
(``--flow_fractions`` overrides both axes).

Outputs (under ``00runs/sensitivity-review-2026-09/freq/`` by default):
``sensitivity_metrics.csv`` (gain/phase per case plus gain/phase shift vs
the hAExp = 0.33 baseline at the same frequency and flow), ``summary.md``,
and ``metadata.json`` (git commit, omc version, solver, tolerance, grids).
A failing case is recorded (metadata ``failures`` + stderr) without losing
the other cases' results: outputs are always written at the end, and the
script exits nonzero when any case failed.

Usage
-----
    python3.12 -m freq.sensitivity.run_review_sensitivity
    python3.12 -m freq.sensitivity.run_review_sensitivity --smoke
    python3.12 -m freq.sensitivity.run_review_sensitivity \
        --exponents 0.33 0.8 --frequencies 0.1 --flow_fractions 0.66
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from .._common import (
        MODEL_NAME_BY_CORE,
        clean_column_headers,
        compute_effective_stop_time,
        compute_number_of_intervals,
        csv_reaches_stop_time,
        find_power_column,
        fit_sine_least_squares,
        fit_window_convergence,
        load_steady_state_overrides,
        phase_relative_to_perturbation_start_deg,
        wrap_phase_rad,
    )
    from ..runFreqNominalParallel import (
        ALLOWED_OVERRIDE_KEYS,
        HX_DETAILED_STATE_OVERRIDE_KEYS,
    )
except ImportError:  # script-style execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from freq._common import (
        MODEL_NAME_BY_CORE,
        clean_column_headers,
        compute_effective_stop_time,
        compute_number_of_intervals,
        csv_reaches_stop_time,
        find_power_column,
        fit_sine_least_squares,
        fit_window_convergence,
        load_steady_state_overrides,
        phase_relative_to_perturbation_start_deg,
        wrap_phase_rad,
    )
    from freq.runFreqNominalParallel import (
        ALLOWED_OVERRIDE_KEYS,
        HX_DETAILED_STATE_OVERRIDE_KEYS,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]

MODEL_NAME = MODEL_NAME_BY_CORE["1r"]
DEFAULT_OUT_DIR = REPO_ROOT / "00runs" / "sensitivity-review-2026-09" / "freq"

#: Task-card grids: hAExp values and the three roll-off-bracketing frequencies.
DEFAULT_EXPONENTS = (0.0, 0.25, 0.33, 0.5, 0.8)
BASELINE_EXPONENT = 0.33
DEFAULT_FREQUENCIES = (0.005, 0.02, 0.1)
#: FF = 1 documents the exact nominal-flow invariance; 0.66 makes the
#: exponent observable (module docstring).
DEFAULT_FLOW_FRACTIONS = (1.0, 0.66)

#: Result columns kept by the omc -variableFilter (regex).
VARIABLE_FILTER = r"^(time|.*n_population\.n)$"

#: omc echoes this prefix (one line per quantity) when an ``-override=``
#: quantity could not be set. Rejections of the study-varied parameters below
#: invalidate the case (the echo may use the full override path
#: (``core1R.fuelchannel.hAExp``) or a bare name (``hAExp``), so both
#: spellings are matched); rejections of payload entries already at their
#: model default (e.g. ``heatLossEnabled``) are benign and tolerated exactly
#: as the production sweep tolerates them.
OVERRIDE_REJECT_MARKER = "not possible to override the following quantity:"

#: The physics parameters this study varies; omc rejection of any of them
#: (echoed as a full path or a bare name) means the case would run with
#: different physics than planned.
VARIED_OVERRIDE_QUANTITIES = ("core1R.fuelchannel.hAExp", "heatExchanger.hAExp")

METRICS_FIELDS = (
    "case_id",
    "freq_rad_s",
    "hAExp",
    "flow_fraction",
    "sin_mag_pcm",
    "stop_time_s",
    "settle_discard_s",
    "gain",
    "gain_dB",
    "phase_deg",
    "r_squared",
    "n_cycles",
    "conv_gain_rel_diff",
    "conv_phase_diff_deg",
    "delta_gain_dB_vs_baseline",
    "delta_phase_deg_vs_baseline",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "hA-exponent frequency-response sensitivity at 1.0 MW, 1R "
            "(reviewer response, TASK-20260901-01 Phase 1)."
        )
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--core_dir",
        type=Path,
        default=REPO_ROOT / "core",
        help="Directory containing SMD_MSR_Modelica.mo and MSRR.mo",
    )
    parser.add_argument("--omc", type=str, default="omc", help="OpenModelica compiler")
    parser.add_argument(
        "--exponents",
        type=float,
        nargs="+",
        default=list(DEFAULT_EXPONENTS),
        help="hAExp grid (default: 0.0 0.25 0.33 0.5 0.8)",
    )
    parser.add_argument(
        "--frequencies",
        type=float,
        nargs="+",
        default=list(DEFAULT_FREQUENCIES),
        help="Forcing frequencies in rad/s (default: 0.005 0.02 0.1)",
    )
    parser.add_argument(
        "--flow_fractions",
        type=float,
        nargs="+",
        default=list(DEFAULT_FLOW_FRACTIONS),
        help=(
            "Prescribed primary flow fractions (default: 1.0 0.66; at 1.0 "
            "the exponent cancels exactly -- see module docstring)"
        ),
    )
    parser.add_argument(
        "--sin_mag",
        type=float,
        default=1.0,
        help="Sinusoidal perturbation amplitude in pcm (default: 1.0)",
    )
    parser.add_argument(
        "--ss_time",
        type=float,
        default=2000.0,
        help="Perturbation start / settle time [s] (default: 2000)",
    )
    parser.add_argument(
        "--settle_discard",
        type=float,
        default=0.0,
        help=(
            "Settling discard after the perturbation start [s]: the fit window "
            "opens at ss_time + settle_discard and the stop time is extended "
            "so it still spans --min_cycles_after_ss periods (default: 0, the "
            "historical fit from the perturbation start; the corrected "
            "frequency-response protocol uses 6000 s at 1 MW)"
        ),
    )
    parser.add_argument(
        "--min_cycles_after_ss",
        type=float,
        default=8.0,
        help="Minimum forcing cycles after perturbation start (default: 8)",
    )
    parser.add_argument(
        "--base_stop_time",
        type=float,
        default=4000.0,
        help="Base stop time [s]; raised per frequency for cycle coverage",
    )
    parser.add_argument(
        "--output_intervals_per_second",
        type=float,
        default=10.0,
        help="Output cadence in intervals/s (default: 10, production value)",
    )
    parser.add_argument(
        "--tolerance", type=float, default=1e-6, help="Solver tolerance (default: 1e-6)"
    )
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help="Per-run omc wall-clock timeout (0 disables, default: 0)",
    )
    parser.add_argument(
        "--keep_build_artifacts",
        action="store_true",
        help="Keep OpenModelica build artifacts in each run directory.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Reduced end-to-end grid for tests: exponents {0.33, 0.8}, one "
            "frequency (0.1 rad/s), flow 0.66, short settle."
        ),
    )
    args = parser.parse_args(argv)

    if args.smoke:
        args.exponents = [0.33, 0.8]
        args.frequencies = [0.1]
        args.flow_fractions = [0.66]
        args.ss_time = 500.0
        args.min_cycles_after_ss = 6.0
        args.base_stop_time = 900.0

    for exponent in args.exponents:
        if not (0.0 <= exponent <= 1.0):
            parser.error(f"--exponents must lie in [0, 1]; got {exponent}")
    for freq in args.frequencies:
        if freq <= 0:
            parser.error(f"--frequencies must be > 0; got {freq}")
    for flow in args.flow_fractions:
        if not (0.0 < flow <= 1.0):
            parser.error(f"--flow_fractions must lie in (0, 1]; got {flow}")
    if args.sin_mag <= 0:
        parser.error("--sin_mag must be > 0")
    if args.ss_time <= 0 or args.base_stop_time <= args.ss_time:
        parser.error("--base_stop_time must exceed --ss_time (> 0)")
    if args.min_cycles_after_ss <= 0:
        parser.error("--min_cycles_after_ss must be > 0")
    if not (math.isfinite(args.settle_discard) and args.settle_discard >= 0):
        parser.error("--settle_discard must be finite and >= 0")
    return args


def _guard_out_dir(out_dir: Path) -> None:
    """Refuse the published record trees (00runs/freq, startup-*, transients-*)."""
    try:
        from helpers.published_tree_guard import refuse_published_tree_path
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from helpers.published_tree_guard import refuse_published_tree_path
    refuse_published_tree_path(out_dir)


def _run_results():
    try:
        from helpers import run_results as rr
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from helpers import run_results as rr
    return rr


def _fmt(value: float) -> str:
    return f"{float(value):.10g}"


def _case_tag(value: float) -> str:
    return _fmt(value).replace(".", "p").replace("-", "m")


def build_case_plan(args: argparse.Namespace) -> list[dict]:
    plan: list[dict] = []
    for flow in args.flow_fractions:
        for freq in args.frequencies:
            for exponent in args.exponents:
                stop_time = compute_effective_stop_time(
                    float(freq),
                    base_stop_time=args.base_stop_time,
                    ss_time=args.ss_time,
                    stop_time_mode="min_cycles_after_ss",
                    min_cycles_after_ss=args.min_cycles_after_ss,
                    settle_discard_s=args.settle_discard,
                )
                intervals = compute_number_of_intervals(
                    float(freq),
                    stop_time=stop_time,
                    output_interval_mode="fixed_rate",
                    output_intervals_per_second=args.output_intervals_per_second,
                    output_samples_per_period=6.0,
                    output_step_max=50.0,
                )
                plan.append(
                    {
                        "case_id": (
                            f"freq{_case_tag(freq)}_exp{_case_tag(exponent)}"
                            f"_ff{_case_tag(flow)}"
                        ),
                        "freq_rad_s": float(freq),
                        "hAExp": float(exponent),
                        "flow_fraction": float(flow),
                        "stop_time": float(stop_time),
                        "number_of_intervals": int(intervals),
                    }
                )
    return plan


def build_case_override(
    case: dict, args: argparse.Namespace, steady_state_overrides: dict[str, float]
) -> str:
    flow = case["flow_fraction"]
    parts = [
        "powerLevel=1",
        f"perturbationAmplitudePcm={_fmt(args.sin_mag)}",
        f"perturbationOmega={_fmt(case['freq_rad_s'])}",
        f"perturbationStartTime={_fmt(args.ss_time)}",
        f"primaryPump.freeConvFF={_fmt(flow)}",
        f"primaryPump.rampUpTo[1]={_fmt(flow)}",
        "secondaryPump.freeConvFF=1",
        f"core1R.fuelchannel.hAExp={_fmt(case['hAExp'])}",
        f"heatExchanger.hAExp={_fmt(case['hAExp'])}",
    ]
    if steady_state_overrides:
        if any(
            key in steady_state_overrides for key in HX_DETAILED_STATE_OVERRIDE_KEYS
        ):
            parts.append("heatExchanger.detailedStateInitWeight=1")
        for key in sorted(steady_state_overrides):
            value = steady_state_overrides[key]
            if isinstance(value, bool):
                parts.append(f"{key}={'true' if value else 'false'}")
            else:
                parts.append(f"{key}={_fmt(value)}")
    return ",".join(parts)


def _override_rejections(stdout_text: str, watched: set[str]) -> list[str]:
    """Study-varied quantities omc refused to override, in order.

    omc echoes the quantity as a full override path (``heatExchanger.hAExp``)
    or a bare name (``hAExp``); both spellings count as a match.
    """
    watched_bare = {key.rsplit(".", 1)[-1] for key in watched}
    rejected: list[str] = []
    for line in stdout_text.splitlines():
        if OVERRIDE_REJECT_MARKER not in line:
            continue
        name = line.split(OVERRIDE_REJECT_MARKER, 1)[1].strip()
        if name in watched or name.rsplit(".", 1)[-1] in watched_bare:
            rejected.append(name)
    return rejected


def run_case(
    case: dict,
    args: argparse.Namespace,
    steady_state_overrides: dict[str, float],
    library_path: Path,
    model_path: Path,
    runs_dir: Path,
) -> Path:
    """Simulate one case; return the result CSV path (raises on failure)."""
    work_dir = runs_dir / case["case_id"]
    work_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = case["case_id"]
    csv_path = work_dir / f"{file_prefix}_res.csv"
    if csv_path.exists():
        csv_path.unlink()

    override = build_case_override(case, args, steady_state_overrides)
    simflags = (
        f"-override={override} -variableFilter={shlex.quote(VARIABLE_FILTER)}"
    )
    from helpers.plant_config import lumped_load_file_text, lumped_plant_data_path

    plant_data_path = lumped_plant_data_path(library_path.parent)
    mos_text = (
        "// Generated by freq/sensitivity/run_review_sensitivity.py\n"
        + lumped_load_file_text([plant_data_path, library_path, model_path])
        + f"simulate({MODEL_NAME},"
        "startTime=0,"
        f"stopTime={_fmt(case['stop_time'])},"
        f"numberOfIntervals={case['number_of_intervals']},"
        f"tolerance={_fmt(args.tolerance)},"
        "method=dassl,"
        'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}",'
        f'simflags="{simflags}");\n'
    )
    mos_path = work_dir / f"{file_prefix}.mos"
    mos_path.write_text(mos_text)

    stdout_path = work_dir / "omc_stdout.log"
    stderr_path = work_dir / "omc_stderr.log"
    timeout = args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
    try:
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            proc = subprocess.run(
                [args.omc, mos_path.name],
                stdout=stdout,
                stderr=stderr,
                cwd=work_dir,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"omc timed out after {exc.timeout:g} s for {case['case_id']}"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"omc failed for {case['case_id']} (exit {proc.returncode}); "
            f"see {stderr_path}"
        )
    if not csv_path.exists():
        tail = stdout_path.read_text(errors="ignore")[-1000:]
        raise RuntimeError(
            f"omc exited 0 but produced no result CSV for {case['case_id']}\n{tail}"
        )
    stdout_text = stdout_path.read_text(errors="ignore")
    rejected = _override_rejections(stdout_text, set(VARIED_OVERRIDE_QUANTITIES))
    if rejected:
        raise RuntimeError(
            f"{case['case_id']}: override rejected by omc: {', '.join(rejected)}"
        )
    if not csv_reaches_stop_time(str(csv_path), case["stop_time"]):
        raise RuntimeError(
            f"{case['case_id']}: result CSV does not reach stopTime="
            f"{case['stop_time']:g} s (truncated run); refusing to fit"
        )

    if not args.keep_build_artifacts:
        keep = {csv_path.name, mos_path.name, stdout_path.name, stderr_path.name}
        for entry in work_dir.iterdir():
            if entry.name in keep:
                continue
            # rev022 M-4: best-effort cleanup -- a read-only file or a
            # symlink-to-dir inside an omc build tree must not turn a
            # SUCCESSFUL simulation into a case failure (the work_dir is
            # per-case scratch).
            try:
                if entry.is_symlink():  # link-to-dir: unlink, never rmtree
                    entry.unlink()
                elif entry.is_dir():  # omc build directories, not just loose files
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
            except OSError:
                pass
    return csv_path


def fit_case(csv_path: Path, case: dict, args: argparse.Namespace) -> dict:
    """Sine-fit one result CSV with the shared production fitter."""
    sim_data = pd.read_csv(csv_path)
    sim_data.columns = clean_column_headers(sim_data.columns)
    power_col = find_power_column(sim_data.columns)
    if power_col is None:
        raise RuntimeError(
            f"{case['case_id']}: no power column among {list(sim_data.columns)[:8]}"
        )
    time = sim_data["time"].to_numpy(dtype=float)
    power = sim_data[power_col].to_numpy(dtype=float)

    settle_discard = float(getattr(args, "settle_discard", 0.0))
    fit_start = args.ss_time + settle_discard
    mask = time >= fit_start
    fit = fit_sine_least_squares(
        time[mask] - fit_start,
        power[mask],
        case["freq_rad_s"],
        fit_trend=False,
    )
    # Two-halves agreement of the fit window (the corrected protocol's
    # settling check; freq/_common.py fit_window_convergence).
    halves = fit_window_convergence(
        time[mask] - fit_start, power[mask], case["freq_rad_s"], trend_order=0
    )
    if not fit.ok or fit.amplitude is None or fit.phase_rad is None:
        raise RuntimeError(
            f"{case['case_id']}: sine fit rejected: {fit.rejection_reason}"
        )

    gain = float(fit.amplitude) / (args.sin_mag * 1e-5)
    gain_db = 20.0 * math.log10(gain) if gain > 0 else -math.inf
    phase_deg = phase_relative_to_perturbation_start_deg(
        phase_rad=float(fit.phase_rad),
        freq_point=case["freq_rad_s"],
        fit_start=fit_start,
        perturbation_start=args.ss_time,
    )
    return {
        "case_id": case["case_id"],
        "freq_rad_s": case["freq_rad_s"],
        "hAExp": case["hAExp"],
        "flow_fraction": case["flow_fraction"],
        "sin_mag_pcm": args.sin_mag,
        "stop_time_s": case["stop_time"],
        "settle_discard_s": settle_discard,
        "gain": gain,
        "gain_dB": gain_db,
        "phase_deg": phase_deg,
        "r_squared": fit.r_squared,
        "n_cycles": fit.n_cycles,
        "conv_gain_rel_diff": float(halves.get("gain_rel_diff", math.nan)),
        "conv_phase_diff_deg": float(halves.get("phase_diff_deg", math.nan)),
        "delta_gain_dB_vs_baseline": math.nan,
        "delta_phase_deg_vs_baseline": math.nan,
    }


def apply_baseline_shifts(rows: list[dict]) -> None:
    """Fill gain/phase shifts vs the hAExp = 0.33 row at the same (freq, flow)."""
    baselines: dict[tuple[float, float], dict] = {}
    for row in rows:
        if abs(row["hAExp"] - BASELINE_EXPONENT) < 1e-12:
            baselines[(row["freq_rad_s"], row["flow_fraction"])] = row
    for row in rows:
        base = baselines.get((row["freq_rad_s"], row["flow_fraction"]))
        if base is None:
            continue
        row["delta_gain_dB_vs_baseline"] = row["gain_dB"] - base["gain_dB"]
        row["delta_phase_deg_vs_baseline"] = math.degrees(
            wrap_phase_rad(math.radians(row["phase_deg"] - base["phase_deg"]))
        )


def write_metrics_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(METRICS_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in METRICS_FIELDS})


def write_summary_markdown(rows: list[dict], out_path: Path, args: argparse.Namespace) -> None:
    lines = [
        "# 1R frequency-response sensitivity to the hA exponent (1.0 MW)",
        "",
        f"Perturbation: {args.sin_mag:g} pcm sine from t = {args.ss_time:g} s, "
        f"fit from t = {args.ss_time + args.settle_discard:g} s "
        f"(settling discard {args.settle_discard:g} s), solver tolerance "
        f"{args.tolerance:g}; "
        f"model `{MODEL_NAME}`; shifts are vs the hAExp = {BASELINE_EXPONENT:g} "
        "baseline at the same frequency and flow.",
        "",
        "At FF = 1 the power law is normalized (`1^hAExp = 1`), so those "
        "rows are invariant by construction; the reduced-flow rows carry the "
        "observable sensitivity.",
        "",
        "| case | freq [rad/s] | hAExp | FF | gain [dB] | phase [deg] "
        "| dGain [dB] | dPhase [deg] | R^2 | halves dG | halves dphi [deg] |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        def num(key: str, fmt: str = ".4g") -> str:
            value = row.get(key)
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                return "-"
            return format(float(value), fmt)

        lines.append(
            f"| {row['case_id']} | {row['freq_rad_s']:g} | {row['hAExp']:g} "
            f"| {row['flow_fraction']:g} | {num('gain_dB')} | {num('phase_deg')} "
            f"| {num('delta_gain_dB_vs_baseline')} "
            f"| {num('delta_phase_deg_vs_baseline')} | {num('r_squared', '.6f')} "
            f"| {num('conv_gain_rel_diff', '.2e')} | {num('conv_phase_diff_deg', '.3f')} |"
        )
    out_path.write_text("\n".join(lines) + "\n")


def write_metadata(
    out_dir: Path,
    args: argparse.Namespace,
    plan: list[dict],
    setpoint_table: Path,
    failures: list[dict] | None = None,
) -> None:
    rr = _run_results()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script": "freq.sensitivity.run_review_sensitivity",
        "task": "TASK-20260901-01 Phase 1 (reviewer item R1.3a)",
        "argv": sys.argv[1:],
        "git_info": rr.collect_git_info(REPO_ROOT),
        # Probe the exact --omc binary the cases ran with, not PATH omc.
        "omc_version": rr.probe_omc_version(omc_executable=str(args.omc)),
        "model_name": MODEL_NAME,
        "solver": "dassl",
        "tolerance": args.tolerance,
        "sin_mag_pcm": args.sin_mag,
        "ss_time_s": args.ss_time,
        "settle_discard_s": args.settle_discard,
        "min_cycles_after_ss": args.min_cycles_after_ss,
        "base_stop_time_s": args.base_stop_time,
        "output_intervals_per_second": args.output_intervals_per_second,
        "setpoint_table": str(setpoint_table),
        "baseline_exponent": BASELINE_EXPONENT,
        "exponents": list(args.exponents),
        "frequencies_rad_s": list(args.frequencies),
        "flow_fractions": list(args.flow_fractions),
        "smoke": bool(args.smoke),
        "cases": [
            {key: case[key] for key in
             ("case_id", "freq_rad_s", "hAExp", "flow_fraction", "stop_time",
              "number_of_intervals")}
            for case in plan
        ],
        "failures": list(failures or []),
        "notes": [
            "FF=1 rows are exactly invariant in hAExp (power law normalized "
            "at nominal flow).",
        ],
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out_dir.resolve()
    _guard_out_dir(out_dir)
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    core_dir = args.core_dir.resolve()
    library_path = core_dir / "SMD_MSR_Modelica.mo"
    model_path = core_dir / "MSRR.mo"
    for path in (library_path, model_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing Modelica source: {path}")

    setpoint_table = core_dir / "init" / "setpoints_1r.csv"
    steady_state_overrides = load_steady_state_overrides(
        table_path=str(setpoint_table),
        power=1.0,
        heat_loss=0,
        allowed_override_keys=ALLOWED_OVERRIDE_KEYS,
    )

    plan = build_case_plan(args)
    print(f"Sensitivity plan: {len(plan)} case(s) -> {out_dir}")

    rows: list[dict] = []
    failures: list[dict] = []
    for index, case in enumerate(plan, start=1):
        print(
            f"[{index}/{len(plan)}] {case['case_id']} "
            f"(stop {case['stop_time']:g} s) ...",
            flush=True,
        )
        # One failing case must not lose the other cases' completed work:
        # record the failure and keep going; outputs are always written.
        try:
            csv_path = run_case(
                case, args, steady_state_overrides, library_path, model_path, runs_dir
            )
            rows.append(fit_case(csv_path, case, args))
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {case['case_id']}: {message}", file=sys.stderr, flush=True)
            failures.append({"case_id": case["case_id"], "error": message})

    apply_baseline_shifts(rows)
    write_metrics_csv(rows, out_dir / "sensitivity_metrics.csv")
    write_summary_markdown(rows, out_dir / "summary.md", args)
    write_metadata(out_dir, args, plan, setpoint_table, failures=failures)
    print(f"Wrote {len(rows)} metric rows to {out_dir / 'sensitivity_metrics.csv'}")
    if failures:
        print(
            f"{len(failures)} of {len(plan)} case(s) failed; outputs written "
            "anyway (failures recorded in metadata.json).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
