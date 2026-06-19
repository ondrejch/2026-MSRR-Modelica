#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Continuation-based steady-state setpoint generator.

Compared to generateSetpointTable.py, this script solves powers in descending
order and uses intermediate geometric power steps to improve convergence at
low power. It can also request OpenModelica steady-state early termination.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

try:
    from . import generateSetpointTable as base
except ImportError:
    import generateSetpointTable as base

STOP_TIME_OVERRIDES: dict[str, dict[str, float]] = {
    # Low-power 1R points also require long horizons for n/setpoint convergence.
    "1r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    },
    # Keep continuation defaults aligned with long-horizon low-power 9R behavior.
    "9r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    }
}


def parse_args() -> argparse.Namespace:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    default_core_dir = os.path.join(repo_root, "core")

    parser = argparse.ArgumentParser(
        description=(
            "Generate power-indexed setpoints using descending-power continuation "
            "with optional intermediate steps and steady-state stop flags."
        )
    )
    parser.add_argument(
        "--powers",
        type=str,
        default="1e-5,1e-4,1e-3,1e-2,0.1,0.2,0.4,0.6,0.8,1.0,1.2",
        help="Comma-separated target powers.",
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core segmentation to simulate.",
    )
    parser.add_argument(
        "--core_dir",
        type=str,
        default=default_core_dir,
        help="Directory containing core Modelica files (default: ../core).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Path to MSRR.mo (default: <core_dir>/MSRR.mo).",
    )
    parser.add_argument(
        "--library",
        type=str,
        default="",
        help="Path to SMD_MSR_Modelica.mo (default: <core_dir>/SMD_MSR_Modelica.mo).",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default="",
        help="Modelica model name (default derived from --core_model).",
    )
    parser.add_argument(
        "--work_dir",
        type=str,
        default="/tmp/msrr_setpoint_table_continuation",
        help="Working directory for generated simulation cases.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output CSV path (default: core/init/setpoints_<core_model>.csv).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append generated rows to an existing output CSV.",
    )
    parser.add_argument(
        "--meta",
        type=str,
        default="",
        help="Optional path for continuation metadata CSV.",
    )
    parser.add_argument(
        "--heat_loss",
        action="store_true",
        help=(
            "Deprecated for power-dependent setpoints. "
            "Heat loss should only be used in startup scenarios."
        ),
    )
    parser.add_argument(
        "--init_temp",
        type=float,
        default=570.0,
        help="Initial uniform setpoint temperature (degC).",
    )
    parser.add_argument(
        "--init_from",
        type=str,
        default="",
        help="CSV path used for initialization overrides (matched by power/heat flag).",
    )
    parser.add_argument(
        "--tail_fraction",
        type=float,
        default=0.2,
        help="Fraction of final samples used for steady-state averaging.",
    )
    parser.add_argument(
        "--tail_min_samples",
        type=int,
        default=1000,
        help="Minimum number of tail samples used for steady-state averaging.",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="dassl",
        help="Integrator method for simulate() call.",
    )
    parser.add_argument(
        "--base_stop_time",
        type=float,
        default=30000.0,
        help="Nominal stop time for higher-power steps (s).",
    )
    parser.add_argument(
        "--low_stop_time",
        type=float,
        default=200000.0,
        help="Long stop time for low-power steps (s).",
    )
    parser.add_argument(
        "--low_power_threshold",
        type=float,
        default=0.01,
        help="Power threshold at or below which low_stop_time is used.",
    )
    parser.add_argument(
        "--retry_stop_time",
        type=float,
        default=300000.0,
        help="Stop time used for fallback retry attempt.",
    )
    parser.add_argument(
        "--max_ratio",
        type=float,
        default=2.0,
        help="Maximum ratio between continuation steps (>=1).",
    )
    parser.add_argument(
        "--enable_steady_state",
        action="store_true",
        help="Enable OMC -steadyState stop criterion (recommended).",
    )
    parser.add_argument(
        "--steady_state_tol",
        type=float,
        default=1e-6,
        help="Tolerance used with -steadyStateTol when steady-state is enabled.",
    )
    parser.add_argument(
        "--simflags_extra",
        type=str,
        default="",
        help="Additional simflags passed to omc (applied to all steps).",
    )
    parser.add_argument(
        "--retry_nls",
        type=str,
        default="mixed",
        help="Retry NLS mode if primary attempt fails (empty disables retry).",
    )
    parser.add_argument(
        "--no_feedback",
        action="store_true",
        help="Disable reactivity feedback during setpoint generation.",
    )
    return parser.parse_args()


def variable_filter_for_core(core_model: str) -> str:
    if core_model == "9r":
        return (
            r"^(time|msre9r\.upperPlenum\.T|msre9r\.R[1-9]\.(fuelNode1|fuelNode2|grapNode)\.T|"
            r"heatExchanger\.T_(in|out)_(p|s)Fluid\.T|heatExchanger\.T_[PST]N[1-4]|"
            r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\.tempPi|"
            r"dhrs\.tempOut\.T|uhx\.tempOut\.T|"
            r"msre9r\.mpke\.n_population\.n|msre9r\.reactorPower\.P|powerBlock\.fissionPower\.P)$"
        )
    return (
        r"^(time|core1R\.fuelchannel\.(fuelNode1|fuelNode2|grapNode)\.T|"
        r"heatExchanger\.T_(in|out)_(p|s)Fluid\.T|heatExchanger\.T_[PST]N[1-4]|"
        r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\.tempPi|"
        r"dhrs\.tempOut\.T|uhx\.tempOut\.T|"
        r"core1R\.mpke\.n_population\.n|core1R\.reactorPower\.P|powerBlock\.fissionPower\.P)$"
    )


def n_column_for_core(core_model: str) -> str:
    if core_model == "9r":
        return "msre9r.mpke.n_population.n"
    return "core1R.mpke.n_population.n"


def should_use_low_stop_time(power: float, threshold: float) -> bool:
    return power <= threshold + 1e-15


def build_simflags(user_flags: str, steady_state: bool, steady_state_tol: float, retry_nls: str = "") -> str:
    parts: list[str] = []
    user_flags = user_flags.strip()
    if user_flags:
        parts.append(user_flags)
    if steady_state:
        parts.append(f"-steadyState -steadyStateTol={steady_state_tol:.16g}")
    if retry_nls:
        parts.append(f"-nls={retry_nls}")
    return " ".join(parts).strip()


def continuation_steps(prev_power: float | None, target_power: float, max_ratio: float) -> list[float]:
    if prev_power is None:
        return [target_power]
    if prev_power <= 0 or target_power <= 0:
        return [target_power]
    ratio = max(prev_power / target_power, target_power / prev_power)
    if ratio <= max_ratio + 1e-12:
        return [target_power]
    n_intervals = int(math.ceil(math.log(ratio) / math.log(max_ratio)))
    if n_intervals < 1:
        n_intervals = 1
    values: list[float] = []
    for i in range(1, n_intervals + 1):
        frac = i / n_intervals
        values.append(prev_power * ((target_power / prev_power) ** frac))
    values[-1] = target_power
    return values


def read_n_stats(csv_path: str, n_col: str) -> tuple[float, float]:
    with open(csv_path, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Empty CSV: {csv_path}")
        clean = [h.strip().strip('"') for h in header]
        if n_col not in clean:
            raise ValueError(f"Column not found in {csv_path}: {n_col}")
        idx = clean.index(n_col)
        n_min = float("inf")
        n_last = float("nan")
        for row in reader:
            if not row:
                continue
            n_val = float(row[idx])
            n_min = min(n_min, n_val)
            n_last = n_val
    if not math.isfinite(n_min):
        raise ValueError(f"No data rows in CSV: {csv_path}")
    return n_min, n_last


def run_one_step(
    *,
    power: float,
    init_overrides: dict[str, float] | None,
    args: argparse.Namespace,
    model_name: str,
    model_src: str,
    library_src: str,
    variable_filter: str,
    requested_result_vars: list[str],
    table_mapping: dict[str, str],
    feedback_on: bool,
    n_col: str,
) -> tuple[dict[str, float], dict[str, object]]:
    stop_time = args.low_stop_time if should_use_low_stop_time(power, args.low_power_threshold) else args.base_stop_time
    power_tag = base.sanitize_power_tag(power)
    override_stop = STOP_TIME_OVERRIDES.get(args.core_model, {}).get(power_tag)
    if override_stop is not None:
        stop_time = max(stop_time, override_stop)
    interval_override = getattr(base, "NUMBER_OF_INTERVALS_OVERRIDES", {}).get(args.core_model, {}).get(power_tag)
    primary_flags = build_simflags(
        user_flags=args.simflags_extra,
        steady_state=args.enable_steady_state,
        steady_state_tol=args.steady_state_tol,
    )

    attempts: list[tuple[str, float, str]] = [("primary", stop_time, primary_flags)]
    if args.retry_nls.strip():
        retry_flags = build_simflags(
            user_flags=args.simflags_extra,
            steady_state=args.enable_steady_state,
            steady_state_tol=args.steady_state_tol,
            retry_nls=args.retry_nls.strip(),
        )
        attempts.append(("retry", max(stop_time, args.retry_stop_time), retry_flags))

    last_exc: Exception | None = None
    for tag, this_stop_time, this_flags in attempts:
        try:
            csv_path = base.run_steady_state_case(
                power=power,
                work_dir=args.work_dir,
                model_name=model_name,
                model_src=model_src,
                library_src=library_src,
                stop_time=this_stop_time,
                variable_filter=variable_filter,
                heat_loss=args.heat_loss,
                init_overrides=init_overrides,
                feedback_on=feedback_on,
                simflags_extra=this_flags,
                method=args.method,
                number_of_intervals=interval_override,
            )
            tail_means = base.read_tail_means(
                csv_path=csv_path,
                variable_names=requested_result_vars,
                tail_fraction=args.tail_fraction,
                tail_min_samples=args.tail_min_samples,
            )
            row = base.build_table_row(
                core_model=args.core_model,
                power=power,
                tail_means=tail_means,
                table_mapping=table_mapping,
                heat_loss=args.heat_loss,
            )
            n_min, n_end = read_n_stats(csv_path, n_col)
            meta = {
                "attempt": tag,
                "csv_path": csv_path,
                "stop_time": this_stop_time,
                "simflags": this_flags,
                "n_min": n_min,
                "n_end": n_end,
            }
            return row, meta
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    if last_exc is None:
        raise RuntimeError(f"Failed to solve power={power} for unknown reason.")
    raise RuntimeError(f"Failed to solve power={power}: {last_exc}") from last_exc


def main() -> None:
    args = parse_args()

    if args.max_ratio < 1.0:
        raise ValueError("--max_ratio must be >= 1.")
    if args.tail_min_samples <= 0:
        raise ValueError("--tail_min_samples must be > 0.")
    if not (0.0 < args.tail_fraction <= 1.0):
        raise ValueError("--tail_fraction must be in (0, 1].")
    if args.base_stop_time <= 0 or args.low_stop_time <= 0 or args.retry_stop_time <= 0:
        raise ValueError("Stop times must be > 0.")
    if args.heat_loss:
        raise ValueError(
            "Heat-loss setpoint generation is disabled. "
            "Use heat loss only in startup scenarios."
        )

    powers = base.parse_powers(args.powers)
    powers_desc = sorted(powers, reverse=True)

    core_dir = os.path.abspath(args.core_dir)
    model_src = os.path.abspath(args.model) if args.model.strip() else os.path.join(core_dir, "MSRR.mo")
    library_src = os.path.abspath(args.library) if args.library.strip() else os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    model_name = args.model_name.strip() or base.MODEL_NAME_BY_CORE[args.core_model]
    feedback_on = not args.no_feedback

    if args.output.strip():
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), f"setpoints_{args.core_model}.csv")
        )

    work_dir = os.path.abspath(args.work_dir)
    args.work_dir = work_dir
    os.makedirs(work_dir, exist_ok=True)

    if not os.path.exists(model_src):
        raise FileNotFoundError(f"Cannot find model file: {model_src}")
    if not os.path.exists(library_src):
        raise FileNotFoundError(f"Cannot find library file: {library_src}")

    table_mapping = base.table_to_result_variable(args.core_model)
    steady_state_columns = list(table_mapping.keys())
    requested_result_vars = sorted(set(table_mapping.values()))
    variable_filter = variable_filter_for_core(args.core_model)
    n_col = n_column_for_core(args.core_model)

    init_table = {}
    if args.init_from.strip():
        init_table = base.load_init_table(args.init_from, steady_state_columns)

    if args.meta.strip():
        meta_path = os.path.abspath(args.meta)
    else:
        meta_path = os.path.join(
            work_dir,
            f"continuation_meta_{args.core_model}_heat{int(bool(args.heat_loss))}.csv",
        )

    print("Generating MSRR steady-state table (continuation mode)")
    print(f"  Core model:            {args.core_model}")
    print(f"  Model file:            {model_src}")
    print(f"  Library file:          {library_src}")
    print(f"  Model name:            {model_name}")
    print(f"  Target powers:         {powers}")
    print(f"  Continuation order:    {powers_desc}")
    print(f"  Max step ratio:        {args.max_ratio}")
    print(f"  Base stop time:        {args.base_stop_time}")
    print(f"  Low stop time:         {args.low_stop_time} (for power <= {args.low_power_threshold})")
    print(f"  Retry stop time:       {args.retry_stop_time}")
    print(f"  Method:                {args.method}")
    print(f"  Steady-state flags:    {args.enable_steady_state} (tol={args.steady_state_tol})")
    print(f"  Retry NLS:             {args.retry_nls or '(disabled)'}")
    print("  Heat loss:             False (startup-only, disabled for power setpoints)")
    print(f"  Feedback:              {'on' if feedback_on else 'off'}")
    print(f"  Init temp:             {args.init_temp}")
    print(f"  Init from:             {args.init_from or '(none)'}")
    print(f"  Work dir:              {work_dir}")
    print(f"  Output:                {output_path}")
    print(f"  Meta output:           {meta_path}")
    print("=" * 80)

    rows: list[dict[str, float]] = []
    meta_rows: list[dict[str, object]] = []

    prev_target: float | None = None
    current_overrides: dict[str, float] | None = None
    done_targets = 0
    for target_power in powers_desc:
        steps = continuation_steps(prev_target, target_power, args.max_ratio)
        print(f"Target power {target_power:g}: solving {len(steps)} continuation step(s)")

        for i, step_power in enumerate(steps, start=1):
            if current_overrides is None:
                current_overrides = base.resolve_init_overrides(
                    args.core_model,
                    init_table,
                    step_power,
                    args.heat_loss,
                    args.init_temp,
                )
            print(f"  Step {i}/{len(steps)} at power={step_power:.12g}")
            row, meta = run_one_step(
                power=step_power,
                init_overrides=current_overrides,
                args=args,
                model_name=model_name,
                model_src=model_src,
                library_src=library_src,
                variable_filter=variable_filter,
                requested_result_vars=requested_result_vars,
                table_mapping=table_mapping,
                feedback_on=feedback_on,
                n_col=n_col,
            )

            current_overrides = {col: row[col] for col in steady_state_columns}
            meta_rows.append(
                {
                    "core_model": args.core_model,
                    "heatLossEnabled": int(bool(args.heat_loss)),
                    "target_power": target_power,
                    "step_power": step_power,
                    "attempt": meta["attempt"],
                    "stop_time": meta["stop_time"],
                    "n_min": meta["n_min"],
                    "n_end": meta["n_end"],
                    "csv_path": meta["csv_path"],
                    "simflags": meta["simflags"],
                }
            )

        rows.append(row)
        prev_target = target_power
        done_targets += 1
        print(f"  Completed target {done_targets}/{len(powers_desc)}")

    # Output is typically consumed in ascending power order.
    rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))

    if args.append and os.path.exists(output_path):
        with open(output_path, newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"Existing output CSV has no header: {output_path}")
            if "heatLossEnabled" not in reader.fieldnames:
                raise ValueError(
                    "Existing output CSV missing 'heatLossEnabled' column. "
                    "Regenerate it before appending."
                )
            existing = []
            for raw in reader:
                if not raw or (raw.get("power") or "").strip() == "":
                    continue
                parsed = {
                    "power": float(raw["power"]),
                    "heatLossEnabled": int(float(raw.get("heatLossEnabled", "0"))),
                }
                for column in steady_state_columns:
                    val = (raw.get(column) or "").strip()
                    if val == "":
                        continue
                    parsed[column] = float(val)
                existing.append(parsed)
        rows = existing + rows
        rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))

    base.write_table(
        rows=rows,
        output_path=output_path,
        steady_state_columns=steady_state_columns,
    )

    os.makedirs(os.path.dirname(meta_path) or ".", exist_ok=True)
    with open(meta_path, "w", newline="") as handle:
        fieldnames = [
            "core_model",
            "heatLossEnabled",
            "target_power",
            "step_power",
            "attempt",
            "stop_time",
            "n_min",
            "n_end",
            "csv_path",
            "simflags",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in meta_rows:
            writer.writerow(item)

    print("=" * 80)
    print(f"Wrote steady-state table: {output_path}")
    print(f"Wrote continuation metadata: {meta_path}")


if __name__ == "__main__":
    main()
