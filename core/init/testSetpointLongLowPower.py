#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test extended steady-state extraction for low power cases.

Runs the nominal-trim steady-state case at a single power level with an
extended stop time and reports steady-state drift metrics from the tail
of the simulation.

This is a GATE, not a diagnostic-only report (rev022 N-5): after each
per-core summary is printed, every key variable's relative tail drift
(|rel_delta| from :func:`compute_tail_drift`) is compared against
``TAIL_DRIFT_MAX_REL_DELTA``; any breach raises ``AssertionError`` (the
process exits nonzero), naming the variable, its measured drift, and the
threshold constant.  The requested power must lie inside the validated
setpoint envelope (fail-closed refusal otherwise, rev022 N-6), and the
case runs at the same long-horizon remedy the table generators would
apply for that power.
"""

import argparse
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from ._common import variable_filter_for_core
    from .generateSetpointTable import (
        MODEL_NAME_BY_CORE,
        resolve_long_horizon,
        run_steady_state_case,
        sanitize_power_tag,
        table_to_result_variable,
        validate_power_envelope,
    )
except ImportError:
    from _common import variable_filter_for_core
    from generateSetpointTable import (
        MODEL_NAME_BY_CORE,
        resolve_long_horizon,
        run_steady_state_case,
        sanitize_power_tag,
        table_to_result_variable,
        validate_power_envelope,
    )


def parse_args() -> argparse.Namespace:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    default_core_dir = os.path.join(repo_root, "core")

    parser = argparse.ArgumentParser(
        description="Test extended steady-state extraction for low-power cases."
    )
    parser.add_argument(
        "--power",
        type=float,
        default=0.1,
        help=(
            "Power level to test in p.u.; must lie inside the validated "
            "setpoint envelope [1e-5, 1.2] (out-of-envelope powers are "
            "refused; default: 0.1)"
        ),
    )
    parser.add_argument(
        "--stop_time_base",
        type=float,
        default=30000.0,
        help="Base stop time in seconds (default: 30000)",
    )
    parser.add_argument(
        "--stop_time_factor",
        type=float,
        default=10.0,
        help="Multiplier for stop time at the tested power (default: 10)",
    )
    parser.add_argument(
        "--tail_fraction",
        type=float,
        default=0.2,
        help="Fraction of tail samples for drift check (default: 0.2)",
    )
    parser.add_argument(
        "--tail_lines",
        type=int,
        default=200000,
        help="Number of CSV lines from end to analyze (default: 200000)",
    )
    parser.add_argument(
        "--work_root",
        type=str,
        default=str(
            Path(__file__).resolve().parents[2]
            / "00runs"
            / "tmp"
            / "setpoints_long_low_power"
        ),
        help=(
            "Working root for generated runs "
            "(default: <repo>/00runs/tmp/setpoints_long_low_power)"
        ),
    )
    parser.add_argument(
        "--analyze_only",
        action="store_true",
        help="Skip running omc and only analyze existing CSVs in work_root",
    )
    parser.add_argument(
        "--core_dir",
        type=str,
        default=default_core_dir,
        help="Directory containing core Modelica files (default: ../core)",
    )
    parser.add_argument(
        "--model_name_override",
        type=str,
        default=None,
        help="Override Modelica model name (e.g. MSRR.MSRRuhxNominalTrim9RThermalSS)",
    )
    parser.add_argument(
        "--simflags_extra",
        type=str,
        default="",
        help="Extra simflags passed to omc (e.g. -s=ida)",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="dassl",
        help="Integrator method for simulate() (default: dassl)",
    )
    parser.add_argument(
        "--heat_loss",
        action="store_true",
        help=(
            "Deprecated and rejected: heat loss is reserved for startup "
            "studies and is disabled for setpoint workflows."
        ),
    )
    parser.add_argument(
        "--core_models",
        type=str,
        default="1r,9r",
        help="Comma-separated core models to run (default: 1r,9r)",
    )
    return parser.parse_args()


def compute_tail_drift(time_vals: np.ndarray, series: np.ndarray, tail_fraction: float):
    finite_mask = np.isfinite(time_vals) & np.isfinite(series)
    time_vals = time_vals[finite_mask]
    series = series[finite_mask]

    n = len(time_vals)
    if n == 0:
        # Empty finite sample set: report NaNs instead of np.mean([]) /
        # np.polyfit on zero rows (rev021 §2.4 D10).
        return 0.0, float("nan"), float("nan")
    tail_count = max(10, int(n * tail_fraction))
    tail_count = min(tail_count, n)
    start = n - tail_count

    t = time_vals[start:]
    y = series[start:]

    if len(t) < 2:
        return 0.0, 0.0, float(np.mean(y))

    slope, intercept = np.polyfit(t, y, 1)
    delta = slope * (t[-1] - t[0])
    mean = float(np.mean(y))
    # Diagnostic-only relative drift: the series here are in degC, so the
    # ratio uses a kelvin mean (a ~570 degC mean would otherwise understate
    # the drift by ~mean_C/mean_K).
    mean_k = mean + 273.15
    rel_delta = float(delta / mean_k) if mean_k != 0 else float("inf")
    return float(slope), float(rel_delta), mean


#: Maximum tolerated relative tail drift per key variable: the absolute
#: least-squares drift across the tail window (slope x span) divided by the
#: tail mean, as computed by :func:`compute_tail_drift`.
#:
#: Evidence basis (rev022 N-5, quantified on the shipped converged cases):
#: analyzing all 22 review-2026-09 qualified setpoint cases (both cores,
#: all 11 tabulated powers; the 8 scalar key variables plus the 27 regional
#: arrays on 9r -- 473 probed series) gives a worst |rel_delta| of 6.8e-3
#: (1r at 1e-2 p.u., the shortest-horizon low-power point, still settling
#: within its 3e4 s base stop), 1.4e-3 for the 9r worst, and below 5.4e-4
#: at every long-horizon low-power point.  The bar sits ~3x above that
#: worst shipped metric (and ~14x above the 9r worst) while staying 2.5x
#: tighter than the generator's own 5% relative qualification bar; a solve
#: that misses its settling horizon drifts by O(1%) across the tail and
#: cannot hide under it.  A non-finite metric (NaN/inf) breaches too: a
#: gate that cannot measure must not pass.
TAIL_DRIFT_MAX_REL_DELTA = 2.0e-2


def check_tail_drift_thresholds(summary, core_model: str) -> None:
    """Raise AssertionError when any key variable's tail drift breaches.

    ``summary`` rows are the ``(key, var, slope, rel_delta, mean)`` tuples
    produced by :func:`analyze_steady_state`.  The failure names the
    threshold constant, its value, and every breaching variable with its
    measured relative drift (rev022 N-5).  Printing the summary is left to
    the caller, so the diagnostic value survives the failure.
    """
    breaches = [
        (key, var, rel_delta, mean)
        for key, var, _slope, rel_delta, mean in summary
        if (not math.isfinite(rel_delta)) or abs(rel_delta) > TAIL_DRIFT_MAX_REL_DELTA
    ]
    if breaches:
        lines = [
            f"Tail drift threshold exceeded for {core_model}: "
            f"TAIL_DRIFT_MAX_REL_DELTA = {TAIL_DRIFT_MAX_REL_DELTA:g} "
            f"({len(breaches)} of {len(summary)} key variables breached):"
        ]
        for key, var, rel_delta, mean in breaches:
            lines.append(
                f"  {key} -> {var}: rel_delta={rel_delta:.6e} mean={mean:.6f}"
            )
        raise AssertionError("\n".join(lines))


def read_last_time(csv_path: str) -> float:
    with open(csv_path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        if pos == 0:
            raise ValueError(f"Empty CSV: {csv_path}")
        line = b""
        while pos > 0:
            pos -= 1
            handle.seek(pos, os.SEEK_SET)
            char = handle.read(1)
            if char == b"\n" and line:
                break
            if char != b"\r":
                line = char + line
        text = line.decode(errors="ignore").strip()
        parts = text.split(",")
        if not parts:
            raise ValueError(f"Could not parse last line in {csv_path}")
        return float(parts[0])


def read_last_lines(csv_path: str, line_count: int) -> list[str]:
    if line_count <= 0:
        raise ValueError("line_count must be > 0")

    chunk_size = 1024 * 1024
    lines = []
    with open(csv_path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        file_size = handle.tell()
        block_end = file_size
        buffer = b""

        while block_end > 0 and len(lines) <= line_count:
            block_start = max(0, block_end - chunk_size)
            handle.seek(block_start, os.SEEK_SET)
            data = handle.read(block_end - block_start)
            buffer = data + buffer
            lines = buffer.splitlines()
            block_end = block_start

    if len(lines) > line_count:
        lines = lines[-line_count:]
    return [line.decode(errors="ignore") for line in lines if line]


def key_variables_for_core(core_model: str) -> list[str]:
    """Key variables the drift gate scores for one core model.

    The scalar setpoint keys apply to both cores.  The 9r table additionally
    exports the regional arrays ``TF1_0_regions[1..9]``, ``TF2_0_regions[1..9]``
    and ``TG_0_regions[1..9]`` (``setpoints_9r.csv``; mapping at
    ``generateSetpointTable.table_to_result_variable``) -- those ARE the
    exported 9r regional setpoints, so the gate scores them too (rev022 N-5).
    """
    key_vars = [
        "fuelTempSetPointNode1",
        "fuelTempSetPointNode2",
        "graphiteTempSetPoint",
        "heatExchanger.TpIn_0",
        "heatExchanger.TpOut_0",
        "heatExchanger.TsIn_0",
        "heatExchanger.TsOut_0",
        "uhx.Tp_0",
    ]
    if core_model == "9r":
        for idx in range(1, 10):
            key_vars.append(f"TF1_0_regions[{idx}]")
        for idx in range(1, 10):
            key_vars.append(f"TF2_0_regions[{idx}]")
        for idx in range(1, 10):
            key_vars.append(f"TG_0_regions[{idx}]")
    return key_vars


def analyze_steady_state(csv_path: str, core_model: str, tail_fraction: float, tail_lines: int):
    mapping = table_to_result_variable(core_model)
    key_vars = key_variables_for_core(core_model)

    # Every key variable must be mapped: a silent subset (the old zip of
    # filtered lists) would drop drift metrics for the missing columns
    # without complaint (rev021 §3.6 zip-pairing guard).
    missing = [k for k in key_vars if k not in mapping]
    if missing:
        raise ValueError(
            f"key variables absent from {core_model} mapping: {missing}"
        )
    pairs = [(k, mapping[k]) for k in key_vars]
    usecols = ["time"] + [var for _k, var in pairs]
    end_time = read_last_time(csv_path)
    tail_start_time = end_time * (1.0 - tail_fraction)

    tail_text_lines = read_last_lines(csv_path, tail_lines)
    if not tail_text_lines:
        raise ValueError(f"No tail lines read from {csv_path}")
    header = tail_text_lines[0]
    if "time" not in header:
        with open(csv_path, "r", encoding="utf-8", errors="ignore") as handle:
            header = handle.readline().strip()
        tail_text_lines.insert(0, header)

    from io import StringIO

    tail_buf = StringIO("\n".join(tail_text_lines))
    tail_df = pd.read_csv(tail_buf, usecols=usecols)
    tail_df = tail_df[tail_df["time"] >= tail_start_time]
    if tail_df.empty:
        raise ValueError(f"Tail window not captured in last {tail_lines} lines for {csv_path}")

    time_vals = tail_df["time"].values
    # D10: the analysis window is defined in TIME, but read_last_lines caps
    # the rows we even load -- if the last `tail_lines` rows do not cover
    # the requested tail span, the drift numbers are computed on a
    # truncated window and must be flagged.
    requested_span = end_time * tail_fraction
    analyzed_span = float(time_vals[-1] - time_vals[0]) if len(time_vals) > 1 else 0.0
    if analyzed_span < requested_span * (1.0 - 1e-9):
        print(
            f"  WARNING: analyzed tail span {analyzed_span:g} s < requested "
            f"{requested_span:g} s (last {tail_lines} lines of {csv_path}); "
            f"drift metrics cover a truncated window"
        )
    summary = []
    for key, var in pairs:
        series = tail_df[var].values
        slope, rel_delta, mean = compute_tail_drift(time_vals, series, tail_fraction=1.0)
        summary.append((key, var, slope, rel_delta, mean))

    return summary


def run_case(core_model: str, power: float, stop_time: float, work_root: str, core_dir: str,
             model_name_override: "str | None", simflags_extra: str, method: str):
    model_src = os.path.join(core_dir, "MSRR.mo")
    library_src = os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    model_name = (
        model_name_override.strip()
        if model_name_override is not None and model_name_override.strip()
        else MODEL_NAME_BY_CORE[core_model]
    )

    work_dir = os.path.join(work_root, core_model)
    os.makedirs(work_dir, exist_ok=True)

    variable_filter = variable_filter_for_core(core_model)
    # D6 + rev022 N-6: the long-horizon remedy that the table generators
    # apply (exact tabulated entries, plus the continuous low-power band
    # rule) must apply here too -- the whole point of the extended test is
    # to check convergence at the SAME horizons the table would use, not on
    # the shorter base horizon.
    stop_override, interval_override = resolve_long_horizon(core_model, power)
    effective_stop = max(stop_time, stop_override or 0.0)
    csv_path = run_steady_state_case(
        power=power,
        work_dir=work_dir,
        model_name=model_name,
        model_src=model_src,
        library_src=library_src,
        stop_time=effective_stop,
        variable_filter=variable_filter,
        simflags_extra=simflags_extra,
        method=method,
        number_of_intervals=interval_override,
    )
    return csv_path


def existing_csv_path(work_root: str, power: float, core_model: str) -> str:
    # Shared injective tag rendering (sanitize_power_tag, same convention
    # as the generators' case directories and manifest fingerprints).
    power_tag = sanitize_power_tag(power)
    case_dir = os.path.join(work_root, core_model, f"power_{power_tag}")
    csv_name = f"MSRR_ss_{power_tag}_res.csv"
    return os.path.join(case_dir, csv_name)


def main() -> None:
    args = parse_args()

    if args.heat_loss:
        raise ValueError(
            "Heat-loss setpoint generation is disabled. "
            "Use heat loss only in startup scenarios."
        )

    power = args.power
    # rev022 N-6: fail closed on out-of-envelope powers -- the same contract
    # as the generators' --powers (no validated setpoint data exists outside).
    validate_power_envelope(power)
    stop_time = args.stop_time_base * args.stop_time_factor
    print("Extended steady-state test")
    print(f"  power:          {power}")
    print(f"  stop time:      {stop_time}")
    print(f"  tail fraction:  {args.tail_fraction}")
    print(f"  work root:      {args.work_root}")
    print("=" * 72)

    results = {}
    core_models = [item.strip() for item in args.core_models.split(",") if item.strip()]
    if args.analyze_only:
        for core_model in core_models:
            csv_path = existing_csv_path(args.work_root, power, core_model)
            if not os.path.exists(csv_path):
                raise FileNotFoundError(f"Missing CSV: {csv_path}")
            results[core_model] = csv_path
            print(f"[{core_model}] CSV: {csv_path}")
    else:
        tasks = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            for core_model in core_models:
                fut = executor.submit(
                    run_case,
                    core_model,
                    power,
                    stop_time,
                    args.work_root,
                    os.path.abspath(args.core_dir),
                    args.model_name_override,
                    args.simflags_extra,
                    args.method,
                )
                tasks[fut] = core_model

            for fut in as_completed(tasks):
                core_model = tasks[fut]
                csv_path = fut.result()
                results[core_model] = csv_path
                print(f"[{core_model}] CSV: {csv_path}")

    print("=" * 72)
    for core_model in core_models:
        csv_path = results.get(core_model)
        if not csv_path:
            continue
        print(f"[{core_model}] tail drift summary")
        summary = analyze_steady_state(csv_path, core_model, args.tail_fraction, args.tail_lines)
        for key, var, slope, rel_delta, mean in summary:
            print(
                f"  {key:>24s} | slope={slope: .4e} "
                f"| rel_delta={rel_delta: .4e} | mean={mean: .6f}"
            )
        print("-" * 72)
        # rev022 N-5: the printed summary alone is not a verdict; enforce
        # the named threshold once the diagnostics are out.
        check_tail_drift_thresholds(summary, core_model)
        print(
            f"[{core_model}] tail drift within TAIL_DRIFT_MAX_REL_DELTA "
            f"= {TAIL_DRIFT_MAX_REL_DELTA:g}: PASS"
        )


if __name__ == "__main__":
    main()
