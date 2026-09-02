#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test extended steady-state extraction for low power cases.

Runs the nominal-trim steady-state case at a single power level with an
extended stop time and reports steady-state drift metrics from the tail
of the simulation.
"""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from ._common import variable_filter_for_core
    from .generateSetpointTable import (
        MODEL_NAME_BY_CORE,
        run_steady_state_case,
        table_to_result_variable,
    )
except ImportError:
    from _common import variable_filter_for_core
    from generateSetpointTable import MODEL_NAME_BY_CORE, table_to_result_variable, run_steady_state_case


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
        help="Power level to test (default: 0.1)",
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
        default="",
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
    rel_delta = float(delta / mean) if mean != 0 else float("inf")
    return float(slope), float(rel_delta), mean


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


def analyze_steady_state(csv_path: str, core_model: str, tail_fraction: float, tail_lines: int):
    mapping = table_to_result_variable(core_model)
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

    result_vars = [mapping[k] for k in key_vars if k in mapping]
    usecols = ["time"] + result_vars
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
    summary = []
    for key, var in zip(key_vars, result_vars):
        series = tail_df[var].values
        slope, rel_delta, mean = compute_tail_drift(time_vals, series, tail_fraction=1.0)
        summary.append((key, var, slope, rel_delta, mean))

    return summary


def run_case(core_model: str, power: float, stop_time: float, work_root: str, core_dir: str,
             model_name_override: str, simflags_extra: str, method: str):
    model_src = os.path.join(core_dir, "MSRR.mo")
    library_src = os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    model_name = model_name_override or MODEL_NAME_BY_CORE[core_model]

    work_dir = os.path.join(work_root, core_model)
    os.makedirs(work_dir, exist_ok=True)

    variable_filter = variable_filter_for_core(core_model)
    csv_path = run_steady_state_case(
        power=power,
        work_dir=work_dir,
        model_name=model_name,
        model_src=model_src,
        library_src=library_src,
        stop_time=stop_time,
        variable_filter=variable_filter,
        simflags_extra=simflags_extra,
        method=method,
    )
    return csv_path


def existing_csv_path(work_root: str, power: float, core_model: str) -> str:
    power_tag = f"{power:.5f}".replace("-", "m").replace(".", "p")
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


if __name__ == "__main__":
    main()
