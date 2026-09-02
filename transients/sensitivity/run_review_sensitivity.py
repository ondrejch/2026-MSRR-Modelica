#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewer-response sensitivity study: hA exponent + linked property sets.

Answers ANUCENE-D-26-00942 reviewer items R1.3a/R1.3b (TASK-20260901-01
Phase 1): how does the FF^hAExp film-convection power law and the adopted
constant-property set change the 1R nominal-power reactivity-step response?

Modeling approach (documented per the task card)
------------------------------------------------
Every case is one legacy ``simulate()`` run of
``MSRR.MSRRuhxNominalTrimThermalSS`` (the same vehicle the production
transient step cases use), driven through OpenModelica ``-override=``
payloads only -- no source-file patching and no generated model variants.
The 1 MW setpoint row from ``core/init/setpoints_1r.csv`` is applied through
the shared ``transients.run_nonlinear_steps.load_setpoints`` /
``build_setpoint_override`` helpers (identical to the production step
route), the reactivity step of ``--step_pcm`` (default 100 pcm) fires at
``--step_time`` via ``externalReactivityAmplitude[2]`` /
``externalReactivityStepTime[2]``, and the new component parameters land as
``core1R.fuelchannel.hAExp=<e>,heatExchanger.hAExp=<e>`` (both power-law
twins are varied together). Each case runs in its own directory under the
output tree; OpenModelica build artifacts are removed after a successful
run, keeping the ``.mos``, the omc logs, and the result CSV.

Physics notes that shape the default grids
------------------------------------------
1. hA is normalized at nominal flow: ``hA = hAnom*FF^hAExp`` with FF = 1
   during the whole nominal-flow step, so ``FF^hAExp == 1`` for EVERY
   exponent and grid A at ``--flow_fractions 1.0`` is exactly degenerate by
   construction. That invariance is itself a reportable result, so the
   nominal-flow rows stay in the default grid; the default additionally
   runs the same exponent grid at a reduced prescribed flow (0.66, matching
   the production ``flow_66pct`` operating point) where the exponent is
   observable. Reduced-flow cases pin ``primaryPump.freeConvFF =
   primaryPump.rampUpTo[1] = FF`` so the flow fraction is exactly constant
   from t = 0.
2. The production 1R model sets ``kFuel = 0`` (axial conduction neglected),
   so the carded ``kFuel x {0.9, 1.1}`` rows are exactly inert
   (0.9*0 = 1.1*0 = 0). They are still run and reported -- the zero
   sensitivity is the honest answer -- and an explicitly labeled extra
   linked set scales the salt-side film conductances
   (``core1R.fuelchannel.hAnom`` + ``heatExchanger.hApNom``) by the same
   factors as the informative film-coefficient counterpart
   (disable with ``--skip_hanom_set``).

Metrics per run (written to ``sensitivity_metrics.csv`` + ``summary.md``,
one compact figure in ``summary.png``): peak power after the step, time of
peak, post-step steady power (tail mean), pre/post steady fuel outlet
delta-T, and two damping proxies -- the first-overshoot fraction
``(peak - post_ss)/post_ss`` and the decay ratio (second/first overshoot
above the post-step steady level; NaN when no second peak exists).
``metadata.json`` records git commit, omc version, solver, tolerance, and
the resolved grids.

Usage
-----
    python3.12 -m transients.sensitivity.run_review_sensitivity
    python3.12 -m transients.sensitivity.run_review_sensitivity --smoke
    python3.12 -m transients.sensitivity.run_review_sensitivity \
        --exponents 0.33 0.5 --flow_fractions 0.66 --stop_time 6000

Outputs default to ``00runs/sensitivity-review-2026-09/transients/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from ..run_nonlinear_steps import (
        build_mos,
        build_setpoint_override,
        load_setpoints,
    )
except ImportError:  # script-style execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from transients.run_nonlinear_steps import (
        build_mos,
        build_setpoint_override,
        load_setpoints,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]

MODEL_NAME = "MSRR.MSRRuhxNominalTrimThermalSS"
INIT_MODE_OVERRIDE = (
    "core1R.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState"
)

DEFAULT_OUT_DIR = REPO_ROOT / "00runs" / "sensitivity-review-2026-09" / "transients"

#: Grid A exponents (task card TASK-20260901-01 Phase 1).
DEFAULT_EXPONENTS = (0.0, 0.25, 0.33, 0.5, 0.8)
#: hAExp = 0.33 is the production default; used as the comparison baseline.
BASELINE_EXPONENT = 0.33
#: Grid A flow fractions: 1.0 documents the exact nominal-flow invariance
#: (FF^hAExp == 1), 0.66 is the reduced-flow point where hAExp is observable.
DEFAULT_FLOW_FRACTIONS = (1.0, 0.66)
#: Grid B linked-set scale factors (nominal 1.0 shared across sets).
DEFAULT_PROPERTY_SCALES = (0.9, 1.1)

#: Production 1R defaults the linked property sets scale (core/MSRR.mo
#: R1MSRRuhx instantiation + MSRR1R fuelchannel/heatExchanger bindings).
NOMINAL_RHO_FUEL = 2079.449700
NOMINAL_CP_FUEL = 2009.66
NOMINAL_K_FUEL = 0.0
NOMINAL_HA_NOM_CORE = 2.4916e04
NOMINAL_HA_NOM_HX_PRIMARY = 7.7966e04

#: Result columns kept by the omc -variableFilter (regex; '.' matches '.').
VARIABLE_FILTER = (
    "time|core1R.powerblock.reactorPower|core1R.tempOut.T"
    "|core1R.fuelchannel.fuelNode2.T|core1R.fuelchannel.hA"
    "|heatExchanger.hApn|primaryPump.flowFrac.FF"
)

METRICS_FIELDS = (
    "case_id",
    "grid",
    "hAExp",
    "flow_fraction",
    "property_set",
    "property_scale",
    "step_pcm",
    "pre_steady_power_W",
    "peak_power_W",
    "time_of_peak_s",
    "post_steady_power_W",
    "overshoot_fraction",
    "decay_ratio",
    "pre_steady_tout_C",
    "post_steady_tout_C",
    "delta_tout_C",
    "hA_W_per_K",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "hA-exponent + linked-property sensitivity study on the 1R "
            "nominal-power reactivity step (reviewer response, "
            "TASK-20260901-01 Phase 1)."
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
        help="Grid A hAExp values (default: 0.0 0.25 0.33 0.5 0.8)",
    )
    parser.add_argument(
        "--flow_fractions",
        type=float,
        nargs="+",
        default=list(DEFAULT_FLOW_FRACTIONS),
        help=(
            "Prescribed primary flow fractions for grid A (default: 1.0 0.66; "
            "at 1.0 the exponent is invisible by construction -- see module "
            "docstring)"
        ),
    )
    parser.add_argument(
        "--property_scales",
        type=float,
        nargs="+",
        default=list(DEFAULT_PROPERTY_SCALES),
        help="Grid B linked-set scale factors besides the shared nominal 1.0",
    )
    parser.add_argument(
        "--skip_gridB",
        action="store_true",
        help="Run only the hA-exponent grid (grid A).",
    )
    parser.add_argument(
        "--skip_hanom_set",
        action="store_true",
        help=(
            "Drop the extra hAnom linked set (the film-conductance surrogate "
            "for the inert kFuel=0 axis)."
        ),
    )
    parser.add_argument(
        "--step_pcm",
        type=float,
        default=100.0,
        help="External reactivity step amplitude in pcm (default: 100)",
    )
    parser.add_argument(
        "--step_time",
        type=float,
        default=2000.0,
        help="Reactivity step insertion time [s] (default: 2000)",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=10000.0,
        help="Simulation stop time [s] (default: 10000)",
    )
    parser.add_argument(
        "--number_of_intervals",
        type=int,
        default=10000,
        help="Output intervals (default: 10000)",
    )
    parser.add_argument(
        "--tolerance", type=float, default=1e-6, help="Solver tolerance (default: 1e-6)"
    )
    parser.add_argument(
        "--method", type=str, default="dassl", help="Solver method (default: dassl)"
    )
    parser.add_argument(
        "--max_step_size",
        type=float,
        default=0.02,
        help="Maximum solver step size (default: 0.02, production value)",
    )
    parser.add_argument(
        "--pre_window",
        type=float,
        default=500.0,
        help="Averaging window [s] before the step for the pre-step baseline",
    )
    parser.add_argument(
        "--tail_window",
        type=float,
        default=1000.0,
        help="Averaging window [s] at end of run for post-step steady values",
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
            "Reduced end-to-end grid for tests: exponents {0.33, 0.8} at "
            "flow 0.66, one rho/cP property run, short horizon."
        ),
    )
    args = parser.parse_args(argv)

    if args.smoke:
        args.exponents = [0.33, 0.8]
        args.flow_fractions = [0.66]
        args.property_scales = [1.1]
        args.skip_hanom_set = True
        args.step_time = 800.0
        args.stop_time = 2000.0
        args.number_of_intervals = 2000
        args.pre_window = 300.0
        args.tail_window = 300.0

    for exponent in args.exponents:
        if not (0.0 <= exponent <= 1.0):
            parser.error(f"--exponents must lie in [0, 1]; got {exponent}")
    for flow in args.flow_fractions:
        if not (0.0 < flow <= 1.0):
            parser.error(f"--flow_fractions must lie in (0, 1]; got {flow}")
    for scale in args.property_scales:
        if scale <= 0:
            parser.error(f"--property_scales must be > 0; got {scale}")
    if args.step_time <= args.pre_window:
        parser.error("--step_time must exceed --pre_window")
    if args.stop_time <= args.step_time + args.tail_window:
        parser.error("--stop_time must exceed --step_time + --tail_window")
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
    """Ordered case list; the fully nominal run is shared between grids."""
    plan: list[dict] = []
    seen: set[tuple] = set()

    def add(grid: str, exponent: float, flow: float, prop_set: str, scale: float,
            overrides: dict[str, float]) -> None:
        key = (round(exponent, 12), round(flow, 12), prop_set, round(scale, 12))
        if key in seen:
            return
        seen.add(key)
        case_id = (
            f"{grid}_exp{_case_tag(exponent)}_ff{_case_tag(flow)}"
            if prop_set == "nominal"
            else f"{grid}_{prop_set}_x{_case_tag(scale)}"
        )
        plan.append(
            {
                "case_id": case_id,
                "grid": grid,
                "hAExp": float(exponent),
                "flow_fraction": float(flow),
                "property_set": prop_set,
                "property_scale": float(scale),
                "extra_overrides": dict(overrides),
            }
        )

    for flow in args.flow_fractions:
        for exponent in args.exponents:
            add("gridA", exponent, flow, "nominal", 1.0, {})

    if not args.skip_gridB:
        # Shared nominal (may already exist through grid A).
        add("gridB", BASELINE_EXPONENT, 1.0, "nominal", 1.0, {})
        for scale in args.property_scales:
            add(
                "gridB", BASELINE_EXPONENT, 1.0, "rho_cp_fuel", scale,
                {
                    "rhoFuel": NOMINAL_RHO_FUEL * scale,
                    "scpFuel": NOMINAL_CP_FUEL * scale,
                },
            )
        for scale in args.property_scales:
            # kFuel = 0 in production: scaling is exactly inert; run it anyway
            # so the zero sensitivity is on the record (module docstring).
            add(
                "gridB", BASELINE_EXPONENT, 1.0, "kFuel", scale,
                {"kFuel": NOMINAL_K_FUEL * scale},
            )
        if not args.skip_hanom_set:
            for scale in args.property_scales:
                add(
                    "gridB", BASELINE_EXPONENT, 1.0, "hAnom", scale,
                    {
                        "core1R.fuelchannel.hAnom": NOMINAL_HA_NOM_CORE * scale,
                        "heatExchanger.hApNom": NOMINAL_HA_NOM_HX_PRIMARY * scale,
                    },
                )
    return plan


def build_case_overrides(
    case: dict, args: argparse.Namespace, setpoint_overrides: str
) -> str:
    flow = case["flow_fraction"]
    parts = [
        setpoint_overrides,
        f"primaryPump.freeConvFF={_fmt(flow)}",
        f"primaryPump.rampUpTo[1]={_fmt(flow)}",
        "secondaryPump.freeConvFF=1",
        "powerLevel=1",
        "perturbationAmplitudePcm=0",
        f"externalReactivityAmplitude[2]={_fmt(args.step_pcm)}",
        f"externalReactivityStepTime[2]={_fmt(args.step_time)}",
        f"core1R.fuelchannel.hAExp={_fmt(case['hAExp'])}",
        f"heatExchanger.hAExp={_fmt(case['hAExp'])}",
    ]
    for key in sorted(case["extra_overrides"]):
        parts.append(f"{key}={_fmt(case['extra_overrides'][key])}")
    return ",".join(parts)


def run_case(
    case: dict,
    args: argparse.Namespace,
    setpoint_overrides: str,
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

    overrides = build_case_overrides(case, args, setpoint_overrides)
    simflags = (
        f"-maxStepSize={_fmt(args.max_step_size)}"
        f" -override={overrides}"
        f" -variableFilter={shlex.quote(VARIABLE_FILTER)}"
    )
    mos_text = build_mos(
        library_path,
        model_path,
        MODEL_NAME,
        start_time=0.0,
        stop_time=args.stop_time,
        number_of_intervals=args.number_of_intervals,
        tolerance=args.tolerance,
        method=args.method,
        file_prefix=file_prefix,
        simflags=simflags,
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
    for key in ("hAExp", "hAnom", "hApNom", "rhoFuel", "scpFuel", "kFuel"):
        marker = f"not possible to override the following quantity: {key}"
        if marker in stdout_text:
            raise RuntimeError(
                f"{case['case_id']}: override rejected by omc: {key}"
            )

    if not args.keep_build_artifacts:
        keep = {
            csv_path.name,
            mos_path.name,
            stdout_path.name,
            stderr_path.name,
        }
        for entry in work_dir.iterdir():
            if entry.name not in keep and entry.is_file():
                entry.unlink()
    return csv_path


def _window_mean(time: np.ndarray, values: np.ndarray, lo: float, hi: float) -> float:
    mask = (time >= lo) & (time <= hi)
    if not mask.any():
        return math.nan
    return float(np.nanmean(values[mask]))


def compute_metrics(csv_path: Path, case: dict, args: argparse.Namespace) -> dict:
    df = pd.read_csv(csv_path)
    time = df["time"].to_numpy(dtype=float)
    power = df["core1R.powerblock.reactorPower"].to_numpy(dtype=float)
    tout_col = (
        "core1R.tempOut.T"
        if "core1R.tempOut.T" in df.columns
        else "core1R.fuelchannel.fuelNode2.T"
    )
    tout = df[tout_col].to_numpy(dtype=float)
    ha_col = "core1R.fuelchannel.hA"
    ha_value = float(df[ha_col].iloc[-1]) if ha_col in df.columns else math.nan

    step_t = args.step_time
    pre_lo = step_t - args.pre_window
    pre_power = _window_mean(time, power, pre_lo, step_t - 1.0)
    pre_tout = _window_mean(time, tout, pre_lo, step_t - 1.0)
    tail_lo = args.stop_time - args.tail_window
    post_power = _window_mean(time, power, tail_lo, args.stop_time)
    post_tout = _window_mean(time, tout, tail_lo, args.stop_time)

    post_mask = time > step_t
    peak_power = math.nan
    time_of_peak = math.nan
    overshoot_fraction = math.nan
    decay_ratio = math.nan
    if post_mask.any():
        t_post = time[post_mask]
        p_post = power[post_mask]
        idx_peak = int(np.nanargmax(p_post))
        peak_power = float(p_post[idx_peak])
        time_of_peak = float(t_post[idx_peak])
        if post_power and math.isfinite(post_power) and post_power > 0:
            overshoot_fraction = (peak_power - post_power) / post_power
        # Decay ratio: height of the SECOND local maximum above the post-step
        # steady level relative to the first (NaN when overdamped / no second
        # peak above the steady level).
        first_amp = peak_power - post_power
        if idx_peak + 2 < p_post.size and math.isfinite(post_power) and first_amp > 0:
            after_peak = p_post[idx_peak:]
            idx_min = int(np.nanargmin(after_peak[: max(2, after_peak.size)]))
            if idx_min > 0 and idx_min + 1 < after_peak.size:
                second_peak = float(np.nanmax(after_peak[idx_min:]))
                second_amp = second_peak - post_power
                if second_amp > 0 and second_amp < first_amp:
                    decay_ratio = second_amp / first_amp

    return {
        "case_id": case["case_id"],
        "grid": case["grid"],
        "hAExp": case["hAExp"],
        "flow_fraction": case["flow_fraction"],
        "property_set": case["property_set"],
        "property_scale": case["property_scale"],
        "step_pcm": args.step_pcm,
        "pre_steady_power_W": pre_power,
        "peak_power_W": peak_power,
        "time_of_peak_s": time_of_peak,
        "post_steady_power_W": post_power,
        "overshoot_fraction": overshoot_fraction,
        "decay_ratio": decay_ratio,
        "pre_steady_tout_C": pre_tout,
        "post_steady_tout_C": post_tout,
        "delta_tout_C": post_tout - pre_tout,
        "hA_W_per_K": ha_value,
    }


def write_metrics_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(METRICS_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in METRICS_FIELDS})


def write_summary_markdown(rows: list[dict], out_path: Path, args: argparse.Namespace) -> None:
    lines = [
        "# 1R reactivity-step sensitivity (hA exponent + linked properties)",
        "",
        f"Step: +{args.step_pcm:g} pcm at t = {args.step_time:g} s; "
        f"model `{MODEL_NAME}`; horizon {args.stop_time:g} s.",
        "",
        "At nominal prescribed flow (FF = 1) the hA power law is normalized "
        "(`FF^hAExp = 1`), so the exponent has no effect by construction; "
        "the reduced-flow rows quantify the actual sensitivity. `kFuel = 0` "
        "in the production model, so its scaled rows are exactly inert.",
        "",
        "| case | hAExp | FF | property set | scale | peak [MW] | t_peak [s] "
        "| steady [MW] | overshoot | decay ratio | dT_out [C] | hA [W/K] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        def num(key: str, scale: float = 1.0, fmt: str = ".4g") -> str:
            value = row.get(key)
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                return "-"
            return format(float(value) * scale, fmt)

        lines.append(
            f"| {row['case_id']} | {row['hAExp']:g} | {row['flow_fraction']:g} "
            f"| {row['property_set']} | {row['property_scale']:g} "
            f"| {num('peak_power_W', 1e-6)} | {num('time_of_peak_s')} "
            f"| {num('post_steady_power_W', 1e-6)} | {num('overshoot_fraction')} "
            f"| {num('decay_ratio')} | {num('delta_tout_C')} "
            f"| {num('hA_W_per_K')} |"
        )
    out_path.write_text("\n".join(lines) + "\n")


def write_summary_figure(
    rows: list[dict],
    traces: dict[str, tuple[np.ndarray, np.ndarray]],
    out_path: Path,
    args: argparse.Namespace,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_traces, ax_bars) = plt.subplots(1, 2, figsize=(11, 4.2))

    # Left panel: grid A power traces at the lowest requested flow fraction
    # (falls back to FF=1 when only nominal flow was requested).
    grid_a = [r for r in rows if r["grid"] == "gridA"]
    flow_for_traces = min((r["flow_fraction"] for r in grid_a), default=1.0)
    for row in sorted(grid_a, key=lambda r: r["hAExp"]):
        if row["flow_fraction"] != flow_for_traces:
            continue
        trace = traces.get(row["case_id"])
        if trace is None:
            continue
        time, power = trace
        ax_traces.plot(
            time - args.step_time,
            power * 1e-6,
            label=f"hAExp = {row['hAExp']:g}",
            linewidth=1.0,
        )
    ax_traces.set_xlim(-50.0, min(400.0, args.stop_time - args.step_time))
    ax_traces.set_xlabel("Time after step [s]")
    ax_traces.set_ylabel("Reactor power [MW]")
    ax_traces.set_title(
        f"+{args.step_pcm:g} pcm step at FF = {flow_for_traces:g}"
    )
    ax_traces.grid(True, linestyle="--", alpha=0.5)
    ax_traces.legend(fontsize=8)

    # Right panel: peak power per case.
    labels = [r["case_id"] for r in rows]
    peaks = [
        (r["peak_power_W"] * 1e-6 if math.isfinite(r["peak_power_W"]) else 0.0)
        for r in rows
    ]
    positions = np.arange(len(labels))
    ax_bars.bar(positions, peaks, color="tab:gray")
    ax_bars.set_xticks(positions)
    ax_bars.set_xticklabels(labels, rotation=75, fontsize=6, ha="right")
    ax_bars.set_ylabel("Peak power after step [MW]")
    ax_bars.set_title("Peak power by case")
    ax_bars.grid(True, axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def write_metadata(
    out_dir: Path, args: argparse.Namespace, plan: list[dict], setpoint_table: Path
) -> None:
    rr = _run_results()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script": "transients.sensitivity.run_review_sensitivity",
        "task": "TASK-20260901-01 Phase 1 (reviewer items R1.3a/R1.3b)",
        "argv": sys.argv[1:],
        "git_info": rr.collect_git_info(REPO_ROOT),
        "omc_version": rr.probe_omc_version(),
        "model_name": MODEL_NAME,
        "solver": args.method,
        "tolerance": args.tolerance,
        "max_step_size": args.max_step_size,
        "number_of_intervals": args.number_of_intervals,
        "stop_time_s": args.stop_time,
        "step_pcm": args.step_pcm,
        "step_time_s": args.step_time,
        "setpoint_table": str(setpoint_table),
        "exponents": list(args.exponents),
        "flow_fractions": list(args.flow_fractions),
        "property_scales": list(args.property_scales),
        "smoke": bool(args.smoke),
        "cases": [
            {key: case[key] for key in
             ("case_id", "grid", "hAExp", "flow_fraction", "property_set",
              "property_scale", "extra_overrides")}
            for case in plan
        ],
        "notes": [
            "FF=1 rows are exactly invariant in hAExp (power law normalized "
            "at nominal flow).",
            "kFuel=0 in the production 1R model; its scaled rows are inert "
            "by construction.",
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
    setpoints = load_setpoints(setpoint_table, power_level=1.0)
    setpoint_overrides = build_setpoint_override(setpoints, INIT_MODE_OVERRIDE)

    plan = build_case_plan(args)
    print(f"Sensitivity plan: {len(plan)} case(s) -> {out_dir}")

    rows: list[dict] = []
    traces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for index, case in enumerate(plan, start=1):
        print(f"[{index}/{len(plan)}] {case['case_id']} ...", flush=True)
        csv_path = run_case(
            case, args, setpoint_overrides, library_path, model_path, runs_dir
        )
        rows.append(compute_metrics(csv_path, case, args))
        df = pd.read_csv(csv_path, usecols=["time", "core1R.powerblock.reactorPower"])
        traces[case["case_id"]] = (
            df["time"].to_numpy(dtype=float),
            df["core1R.powerblock.reactorPower"].to_numpy(dtype=float),
        )

    write_metrics_csv(rows, out_dir / "sensitivity_metrics.csv")
    write_summary_markdown(rows, out_dir / "summary.md", args)
    write_summary_figure(rows, traces, out_dir / "summary.png", args)
    write_metadata(out_dir, args, plan, setpoint_table)
    print(f"Wrote {len(rows)} metric rows to {out_dir / 'sensitivity_metrics.csv'}")
    print(f"Summary figure: {out_dir / 'summary.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
