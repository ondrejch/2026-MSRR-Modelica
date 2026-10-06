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
import io
import math
import os
from pathlib import Path

try:
    from . import generateSetpointTable as base
    from ._common import variable_filter_for_core
except ImportError:
    import generateSetpointTable as base
    from _common import variable_filter_for_core

# Kept as a pinned module constant (tests pin it equal to the base
# generator's table).  Horizon resolution goes through
# ``base.resolve_long_horizon`` (rev022 N-6), which reads the base tables
# and adds the continuous low-power band rule.
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
        help=(
            "Comma-separated target powers (p.u.). Each value must lie "
            "inside the validated setpoint envelope [1e-5, 1.2] p.u.; "
            "out-of-envelope powers are refused (fail closed)."
        ),
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
    # Sentinel convention shared with the base generator (rev021 §3.6):
    # ``None`` means "use the default"; whitespace-only strings resolve the
    # same way.
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Path to MSRR.mo (default: <core_dir>/MSRR.mo).",
    )
    parser.add_argument(
        "--library",
        type=str,
        default=None,
        help="Path to SMD_MSR_Modelica.mo (default: <core_dir>/SMD_MSR_Modelica.mo).",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default=None,
        help="Modelica model name (default derived from --core_model).",
    )
    parser.add_argument(
        "--work_dir",
        type=str,
        default=str(Path(__file__).resolve().parents[2] / "00runs" / "tmp" / "setpoints_continuation"),
        help=(
            "Working directory for generated simulation cases "
            "(default: <repo>/00runs/tmp/setpoints_continuation)"
        ),
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
        help=(
            "Merge generated rows into an existing output CSV, replacing any "
            "matching (power, heatLossEnabled) keys."
        ),
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
        "--qualification_profile",
        choices=base.QUALIFICATION_PROFILES,
        default=base.DEFAULT_QUALIFICATION_PROFILE,
        help=(
            "Use diagnostic or publication-grade convergence qualification. "
            "Publication mode applies the same fail-safe tolerance clamps, "
            "residual-amplitude default, and 1000 s minimum tail as the "
            "direct generator."
        ),
    )
    parser.add_argument(
        "--conv_residual_amplitude_tol",
        type=float,
        default=None,
        help="Relative detrended p99-p01 residual-amplitude limit.",
    )
    parser.add_argument(
        "--tail_min_duration",
        type=float,
        default=0.0,
        help="Minimum physical duration of the scored tail in seconds.",
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
        help=(
            "Retry NLS mode for the fallback attempt, which runs when the "
            "primary attempt raises OR fails its late-window convergence "
            "checks (non-convergence is the retry's purpose, not just "
            "solver robustness; rev021 §2.4 D4). Empty disables the "
            "fallback attempt entirely."
        ),
    )
    parser.add_argument(
        "--no_feedback",
        action="store_true",
        help="Disable reactivity feedback during setpoint generation.",
    )
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help=(
            "Maximum wall-clock seconds per omc simulation before it is "
            "treated as failed (0 disables the timeout, default: 0)."
        ),
    )
    parser.add_argument(
        "--accept_unconverged",
        action="store_true",
        help=(
            "Write target rows whose late-window convergence checks failed "
            f"into the output table marked {base.QUALIFIED_COLUMN}=0. Without "
            "this flag such targets are treated as failures and excluded."
        ),
    )
    parser.add_argument(
        "--conv_window_tol",
        type=float,
        default=base.DEFAULT_CONVERGENCE_WINDOW_TOLERANCE,
        help=(
            "Relative tolerance on the difference between the two equal "
            "late-window means, divided by the engineering scale "
            "S=max(|mean|, family floor); where the family defines an "
            "absolute bar, the effective combined limit is the larger of "
            "the two bars, and a deviation is rejected only when it "
            "exceeds both "
            f"(default: {base.DEFAULT_CONVERGENCE_WINDOW_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_slope_tol",
        type=float,
        default=base.DEFAULT_CONVERGENCE_SLOPE_TOLERANCE,
        help=(
            "Relative tolerance on the least-squares trend projected across "
            "the full late window (slope times window duration, divided by "
            "the same engineering scale); where the family defines an "
            "absolute bar, the effective combined limit is the larger of "
            "the two bars "
            f"(default: {base.DEFAULT_CONVERGENCE_SLOPE_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_temperature_abs_tol",
        type=float,
        default=base.DEFAULT_TEMPERATURE_ABS_TOLERANCE,
        help=(
            "Absolute late-window acceptance bar for temperature columns, "
            "in the setpoint-table unit (degrees Celsius; kelvin shares the "
            f"numeric width) (default: {base.DEFAULT_TEMPERATURE_ABS_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_temperature_floor",
        type=float,
        default=base.DEFAULT_TEMPERATURE_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for temperature columns, "
            "S=max(|mean|, floor), in the same unit as "
            "--conv_temperature_abs_tol "
            f"(default: {base.DEFAULT_TEMPERATURE_SCALE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--conv_power_abs_tol",
        type=float,
        default=base.DEFAULT_POWER_ABS_TOLERANCE,
        help=(
            "Absolute late-window acceptance bar for power columns, in "
            f"watts (default: {base.DEFAULT_POWER_ABS_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_power_floor",
        type=float,
        default=base.DEFAULT_POWER_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for power columns in watts "
            f"(default: {base.DEFAULT_POWER_SCALE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--conv_population_floor",
        type=float,
        default=base.DEFAULT_POPULATION_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for normalized population columns "
            "(dimensionless; relative bars only) "
            f"(default: {base.DEFAULT_POPULATION_SCALE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--claim_timeout_s",
        type=float,
        default=None,
        help=(
            "Maximum seconds to wait for a busy result-slot claim when "
            "another launch holds the same power slot; expiry raises "
            "ResultSlotClaimTimeout naming the holder. Default: wait "
            "indefinitely (previous behavior)."
        ),
    )
    args = parser.parse_args()
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    return args


def n_column_for_core(core_model: str) -> str:
    if core_model == "9r":
        return "msre9r.mpke.n_population.n"
    return "core1R.mpke.n_population.n"


#: 9R scalar columns excluded from the continuation override chain.  MSRR.mo
#: binds ``TF1_0_regions[1]`` to ``fuelTempSetPointNode1`` (and TF2/TG
#: likewise) via ``cat(1, {fuelTempSetPointNode1}, ...[2:9])``, so once the
#: regional arrays are overridden the region-1 binding follows the regional
#: value and the scalar override is silently dead for region 1.  The
#: harmonized scalars are table-reporting columns (volume-weighted averages),
#: not the right continuation targets; the chain keeps the regional arrays
#: only (rev021 §2.4 D5).
NINE_R_CHAIN_SCALAR_COLUMNS = frozenset(
    {"fuelTempSetPointNode1", "fuelTempSetPointNode2", "graphiteTempSetPoint"}
)


def chain_init_overrides(
    core_model: str,
    overrides: dict[str, float] | None,
) -> dict[str, float] | None:
    """Strip the dead 9R scalar overrides from a continuation init set."""
    if overrides is None:
        return None
    if core_model != "9r":
        return dict(overrides)
    return {
        column: value
        for column, value in overrides.items()
        if column not in NINE_R_CHAIN_SCALAR_COLUMNS
    }


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
    # rev022 N-6: shared resolver -- exact tabulated entries plus the
    # continuous low-power band rule, so a new low power no longer silently
    # falls back to the base horizons.
    override_stop, interval_override = base.resolve_long_horizon(
        args.core_model, power
    )
    if override_stop is not None:
        stop_time = max(stop_time, override_stop)
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
    for index, (tag, this_stop_time, this_flags) in enumerate(attempts):
        # Hash this continuation module into the manifest so a byte change
        # to it (simflags, steps, stop-time overrides) moves the fingerprint.
        # The per-attempt simflags string enters the fingerprint too (D8):
        # the primary and the alternate-NLS retry simulate DIFFERENTLY.
        expected_manifest = base.build_setpoint_case_manifest(
            core_model=args.core_model,
            power=power,
            model_name=model_name,
            model_src=model_src,
            library_src=library_src,
            stop_time=this_stop_time,
            number_of_intervals=interval_override,
            init_overrides=init_overrides,
            feedback_on=feedback_on,
            method=args.method,
            variable_filter=variable_filter,
            simflags=this_flags,
            extra_workflow_python_files=[Path(__file__).resolve()],
        )
        try:
            csv_path = base.run_steady_state_case(
                power=power,
                work_dir=args.work_dir,
                model_name=model_name,
                model_src=model_src,
                library_src=library_src,
                stop_time=this_stop_time,
                variable_filter=variable_filter,
                init_overrides=init_overrides,
                feedback_on=feedback_on,
                simflags_extra=this_flags,
                method=args.method,
                number_of_intervals=interval_override,
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                expected_manifest=expected_manifest,
                validation_required_columns=tuple(requested_result_vars),
                validation_min_samples=int(args.tail_min_samples),
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )
            report = base.evaluate_tail_convergence(
                csv_path,
                state_columns=requested_result_vars,
                tail_fraction=args.tail_fraction,
                tail_min_samples=args.tail_min_samples,
                window_tolerance=args.conv_window_tol,
                slope_tolerance=args.conv_slope_tol,
                core_model=args.core_model,
                temperature_abs_tol=args.conv_temperature_abs_tol,
                temperature_floor=args.conv_temperature_floor,
                power_abs_tol=args.conv_power_abs_tol,
                power_floor=args.conv_power_floor,
                population_floor=args.conv_population_floor,
                qualification_profile=args.qualification_profile,
                residual_amplitude_tolerance=args.conv_residual_amplitude_tol,
                minimum_tail_duration_s=args.tail_min_duration,
                require_plant_signals=(
                    args.qualification_profile == "publication"
                ),
            )
            base.write_convergence_report(csv_path, report)
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
            row[base.QUALIFIED_COLUMN] = int(bool(report["passed"]))
            n_min, n_end = read_n_stats(csv_path, n_col)
            meta = {
                "attempt": tag,
                "csv_path": csv_path,
                "stop_time": this_stop_time,
                "simflags": this_flags,
                "n_min": n_min,
                "n_end": n_end,
                "convergence_passed": bool(report["passed"]),
                "convergence_report": str(base.convergence_report_path(csv_path)),
            }
            if not report["passed"] and index + 1 < len(attempts):
                # Non-converged primary (rev021 §2.4 D4): the longer-horizon
                # / alternate-NLS retry is exactly the remedy for
                # non-convergence, so run it instead of returning
                # qualified=0.  The retry launches into the same slot with
                # reuse_ok=False, quarantining this attempt's CSV before
                # re-simulating, so the published pair is the retry's.  If
                # the retry itself raises, the RuntimeError below surfaces
                # that failure (this unconverged row is not returned).
                print(
                    f"  power={power}: {tag} attempt failed late-window "
                    f"checks; running the next attempt "
                    f"(stop_time={attempts[index + 1][1]:g}, "
                    f"simflags={attempts[index + 1][2]!r})"
                )
                continue
            return row, meta
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    # The loop always terminates in one of: a ``return`` (the attempt
    # converged, or the FINAL attempt finished unconverged -- that row is
    # returned qualified=0 so main() applies its accept/exclude policy) or
    # an exception recorded in ``last_exc``.  An unconverged NON-FINAL
    # attempt only ``continue``s, so it can never fall out of the loop.
    if last_exc is None:
        raise RuntimeError(f"Failed to solve power={power} for unknown reason.")
    raise RuntimeError(f"Failed to solve power={power}: {last_exc}") from last_exc


def main() -> None:
    args = base.apply_qualification_profile(parse_args())

    if args.max_ratio <= 1.0:
        raise ValueError("--max_ratio must be > 1.")
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
    if args.omc_timeout_seconds < 0:
        raise ValueError("--omc_timeout_seconds must be >= 0.")
    if args.conv_window_tol < 0 or args.conv_slope_tol < 0:
        raise ValueError(
            "--conv_window_tol and --conv_slope_tol must be nonnegative."
        )
    if (
        args.conv_residual_amplitude_tol is not None
        and args.conv_residual_amplitude_tol < 0
    ):
        raise ValueError("--conv_residual_amplitude_tol must be nonnegative.")
    if args.tail_min_duration < 0:
        raise ValueError("--tail_min_duration must be nonnegative.")
    for flag_name in (
        "conv_temperature_abs_tol",
        "conv_temperature_floor",
        "conv_power_abs_tol",
        "conv_power_floor",
        "conv_population_floor",
    ):
        if getattr(args, flag_name) < 0:
            raise ValueError(f"--{flag_name.replace('_', '-')} must be nonnegative.")

    powers = base.parse_powers(args.powers)
    powers_desc = sorted(powers, reverse=True)

    core_dir = os.path.abspath(args.core_dir)
    # Same None-sentinel resolution as the base generator (rev021 §3.6).
    model_src = (
        os.path.abspath(args.model.strip())
        if args.model is not None and args.model.strip()
        else os.path.join(core_dir, "MSRR.mo")
    )
    library_src = (
        os.path.abspath(args.library.strip())
        if args.library is not None and args.library.strip()
        else os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    )
    model_name = (
        args.model_name.strip()
        if args.model_name is not None and args.model_name.strip()
        else base.MODEL_NAME_BY_CORE[args.core_model]
    )
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
    print(f"  Qualification profile: {args.qualification_profile}")
    print(
        f"  Convergence:           relative window/slope eps {args.conv_window_tol:g}/"
        f"{args.conv_slope_tol:g} on engineering scales; temperature abs "
        f"{args.conv_temperature_abs_tol:g} (table degC, floor {args.conv_temperature_floor:g}), "
        f"power abs {args.conv_power_abs_tol:g} W (floor {args.conv_power_floor:g}), "
        f"population floor {args.conv_population_floor:g}"
    )
    if args.accept_unconverged:
        print("  Unconverged:           accepted and marked qualified=0")
    else:
        print("  Unconverged:           target excluded from the output table")
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
    failures: list[tuple[float, str]] = []

    prev_target: float | None = None
    current_overrides: dict[str, float] | None = None
    last_good_overrides: dict[str, float] | None = None
    done_targets = 0
    for target_power in powers_desc:
        steps = continuation_steps(prev_target, target_power, args.max_ratio)
        print(f"Target power {target_power:g}: solving {len(steps)} continuation step(s)")

        try:
            for i, step_power in enumerate(steps, start=1):
                if current_overrides is None:
                    # For 9R the chain carries the regional arrays only; the
                    # harmonized scalars would be silently dead for region 1
                    # (D5).
                    current_overrides = chain_init_overrides(
                        args.core_model,
                        base.resolve_init_overrides(
                            args.core_model,
                            init_table,
                            step_power,
                            args.heat_loss,
                            args.init_temp,
                        ),
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

                current_overrides = chain_init_overrides(
                    args.core_model,
                    {col: row[col] for col in steady_state_columns},
                )
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
        except Exception as exc:
            failures.append((target_power, str(exc)))
            print(f"  ERROR: target power={target_power} failed: {exc}")
            current_overrides = (
                dict(last_good_overrides) if last_good_overrides is not None else None
            )
            continue

        # Final-step late-window qualification drives whether this target may
        # enter the production table.
        final_converged = bool(int(row.get(base.QUALIFIED_COLUMN, 1)))
        if not final_converged and not args.accept_unconverged:
            reason = (
                "late-window convergence checks failed; see "
                f"{meta.get('convergence_report', 'the case convergence report')}"
            )
            failures.append((target_power, reason))
            print(f"  ERROR: target power={target_power} unconverged ({reason})")
            current_overrides = (
                dict(last_good_overrides) if last_good_overrides is not None else None
            )
            continue
        if not final_converged:
            print(
                f"  WARNING: target power={target_power} failed late-window "
                f"checks; written with {base.QUALIFIED_COLUMN}=0"
            )

        last_good_overrides = (
            dict(current_overrides) if current_overrides is not None else None
        )
        rows.append(row)
        prev_target = target_power
        done_targets += 1
        print(f"  Completed target {done_targets}/{len(powers_desc)}")

    if not rows and failures:
        print("ERROR: no target powers succeeded; output table not written.")
        raise SystemExit(1)

    succeeded = len(rows)
    model_sources = base.model_sources_for(model_src, library_src)
    if args.append and os.path.exists(output_path):
        from helpers.setpoint_model_version import require_appendable

        require_appendable(output_path, sources=model_sources)
        existing = base.read_existing_table_rows(output_path, steady_state_columns)
        rows = base.merge_table_rows(existing, rows)
    else:
        # Output is typically consumed in ascending power order.
        rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))

    base.write_table(
        rows=rows,
        output_path=output_path,
        steady_state_columns=steady_state_columns,
        model_sources=model_sources,
        generator="core.init.generateSetpointTableContinuation",
        generation=base.generation_record(args, __file__),
    )

    os.makedirs(os.path.dirname(meta_path) or ".", exist_ok=True)
    # Metadata CSV is published atomically (unique temp name + os.replace),
    # matching the output table so an interrupted run leaves no partial file.
    buffer = io.StringIO()
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
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in meta_rows:
        writer.writerow(item)
    base._run_results().atomic_write_text(meta_path, buffer.getvalue())

    print("=" * 80)
    print(
        f"Wrote steady-state table: {output_path} "
        f"({succeeded}/{len(powers_desc)} targets succeeded)"
    )
    print(f"Wrote continuation metadata: {meta_path}")
    if failures:
        print(
            "Failed target powers (rerun the failed --powers with --append "
            "to fill gaps):"
        )
        for power, message in sorted(failures):
            print(f"  power={power}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
