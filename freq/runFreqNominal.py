#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Run frequency response analysis for the MSRR nominal-trim model.
Serial version.

This script uses a dedicated non-startup scenario model:
    1r: MSRR.MSRRuhxNominalTrim
    9r: MSRR.MSRRuhxNominalTrim9R
and sweeps perturbation frequency using OpenModelica parameter overrides.

No source-file text patching is performed.

Usage:
    python runFreqNominal.py [--power POWER] [--freq_min FREQ_MIN]
                             [--freq_max FREQ_MAX] [--num_freq NUM_FREQ]
                             [--sin_mag SIN_MAG] [--stop_time STOP_TIME]
                             [--ss_time SS_TIME] [--base_dir BASE_DIR]
                             [--model_name MODEL_NAME]
"""

import argparse
import csv
import numpy as np
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from shutil import copyfile

try:
    from .paths import default_freq_case_dir
except ImportError:
    from paths import default_freq_case_dir


TABLE_OVERRIDE_NAME_MAP = {
    "reactivityFeedback.FuelTempSetPointNode1": "fuelTempSetPointNode1",
    "reactivityFeedback.FuelTempSetPointNode2": "fuelTempSetPointNode2",
    "reactivityFeedback.GrapTempSetPoint": "graphiteTempSetPoint",
}

MODEL_NAME_BY_CORE = {
    "1r": "MSRR.MSRRuhxNominalTrimNoTrips",
    "9r": "MSRR.MSRRuhxNominalTrim9RNoTrips",
}

ALLOWED_OVERRIDE_KEYS = {
    "fuelTempSetPointNode1",
    "fuelTempSetPointNode2",
    "graphiteTempSetPoint",
    "heatLossEnabled",
}

COLLECT_POWER_CANDIDATES = (
    "pkenpopulationn",
    "pkenpopulation",
    "powerblockfissionpowerp",
    "fuelchannelnompower",
    "npopulationn",
)

# Keep only time plus the response signal columns needed by collectFreqNominal*.
REDUCED_CSV_VARIABLE_FILTER = (
    r"^(time|.*n_population\.n|.*nPopulation\.n|.*fissionPowerP|.*nomPower)$"
)
OUTPUT_INTERVAL_MODE_CHOICES = ("fixed_rate", "frequency_scaled")

HEADER_CLEAN_REGEX = re.compile(r"[\[\]\.\(\)_']")
HEAT_LOSS_COLUMN = "heatLossEnabled"


def default_steady_state_table(core_model: str) -> str:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "core", "init", f"setpoints_{core_model}.csv")


def clean_header_for_collect(name: str) -> str:
    cleaned = HEADER_CLEAN_REGEX.sub("", name)
    return cleaned.replace(" ", "").lower()


def find_collect_columns(header: list[str]) -> tuple[int, int]:
    cleaned = [clean_header_for_collect(item.strip().strip('"')) for item in header]

    time_idx = -1
    for idx, item in enumerate(cleaned):
        if item == "time":
            time_idx = idx
            break
    if time_idx < 0:
        raise ValueError("Could not find 'time' column in result CSV.")

    power_idx = -1
    for idx, item in enumerate(cleaned):
        if item in COLLECT_POWER_CANDIDATES:
            power_idx = idx
            break
    if power_idx < 0:
        for idx, item in enumerate(cleaned):
            if "npopulation" in item or "nompower" in item:
                power_idx = idx
                break
    if power_idx < 0:
        raise ValueError(
            "Could not find a power column compatible with collectFreqNominal."
        )

    return time_idx, power_idx


def reduce_csv_to_collect_columns(csv_path: str) -> None:
    tmp_path = f"{csv_path}.tmp"
    try:
        with open(csv_path, newline="") as src, open(tmp_path, "w", newline="") as dst:
            reader = csv.reader(src)
            writer = csv.writer(dst)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"CSV is empty: {csv_path}")

            time_idx, power_idx = find_collect_columns(header)
            writer.writerow([header[time_idx], header[power_idx]])

            max_idx = max(time_idx, power_idx)
            for row in reader:
                if not row or len(row) <= max_idx:
                    continue
                writer.writerow([row[time_idx], row[power_idx]])

        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def build_simflags(
    override: str,
    *,
    reduced_csv_for_collect: bool = False,
) -> str:
    simflag_parts = [f"-override={override}"]
    if reduced_csv_for_collect:
        simflag_parts.append(
            f"-variableFilter={shlex.quote(REDUCED_CSV_VARIABLE_FILTER)}"
        )
    return " ".join(simflag_parts)


def cleanup_omc_artifacts_in_dir(work_path: str, keep_files: set[str]) -> None:
    for name in os.listdir(work_path):
        if name in keep_files:
            continue
        path = os.path.join(work_path, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def compute_output_step(
    freq_point: float,
    *,
    output_interval_mode: str,
    output_intervals_per_second: float,
    output_samples_per_period: float,
    output_step_max: float,
) -> float:
    if output_intervals_per_second <= 0:
        raise ValueError("output_intervals_per_second must be > 0")

    output_step = 1.0 / output_intervals_per_second
    if output_interval_mode == "fixed_rate":
        return float(output_step)
    if output_interval_mode != "frequency_scaled":
        raise ValueError(
            f"Unsupported output_interval_mode: {output_interval_mode}"
        )
    if freq_point <= 0:
        raise ValueError("frequency must be positive for frequency-scaled output")
    if output_samples_per_period <= 0:
        raise ValueError(
            "output_samples_per_period must be > 0 for frequency-scaled output"
        )

    forcing_period = 2.0 * np.pi / freq_point
    output_step = max(output_step, forcing_period / output_samples_per_period)
    if output_step_max > 0:
        output_step = min(output_step, output_step_max)
    return float(output_step)


def compute_number_of_intervals(
    freq_point: float,
    *,
    stop_time: float,
    output_interval_mode: str,
    output_intervals_per_second: float,
    output_samples_per_period: float,
    output_step_max: float,
) -> int:
    if stop_time <= 0:
        raise ValueError("stop_time must be > 0 when computing output intervals")
    output_step = compute_output_step(
        freq_point,
        output_interval_mode=output_interval_mode,
        output_intervals_per_second=output_intervals_per_second,
        output_samples_per_period=output_samples_per_period,
        output_step_max=output_step_max,
    )
    return max(1, int(np.ceil(stop_time / output_step)))


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_core_dir = str(repo_root / "core")

    parser = argparse.ArgumentParser(
        description="Run MSRR frequency response simulations using MSRRuhxNominalTrim (serial)."
    )
    parser.add_argument("--core_model", type=str, choices=("1r", "9r"), default="1r",
                        help="Core segmentation to simulate (default: 1r)")
    parser.add_argument("--core_dir", type=str, default=default_core_dir,
                        help="Directory containing core Modelica files (default: core)")
    parser.add_argument("--power", type=float, default=1.0,
                        help="Normalized nominal power level (default: 1.0)")
    parser.add_argument("--freq_min", type=float, default=1e-2,
                        help="Minimum frequency in rad/s (default: 0.01)")
    parser.add_argument("--freq_max", type=float, default=1e1,
                        help="Maximum frequency in rad/s (default: 10.0)")
    parser.add_argument("--num_freq", type=int, default=100,
                        help="Number of log-spaced frequency points (default: 100)")
    parser.add_argument("--sin_mag", type=float, default=1.0,
                        help="Sine reactivity perturbation amplitude in pcm (default: 1.0)")
    parser.add_argument("--stop_time", type=float, default=10000.0,
                        help="Simulation stop time in seconds (default: 10000)")
    parser.add_argument(
        "--output_interval_mode",
        type=str,
        choices=OUTPUT_INTERVAL_MODE_CHOICES,
        default="fixed_rate",
        help=(
            "How to choose numberOfIntervals. 'fixed_rate' keeps the legacy "
            "output_intervals_per_second rate. 'frequency_scaled' allows coarser "
            "output at low forcing frequency while preserving the legacy minimum "
            "time resolution."
        ),
    )
    parser.add_argument(
        "--output_intervals_per_second",
        type=float,
        default=10.0,
        help=(
            "Legacy output cadence in intervals/s, also used as the minimum "
            "time resolution in frequency_scaled mode (default: 10.0)."
        ),
    )
    parser.add_argument(
        "--output_samples_per_period",
        type=float,
        default=6.0,
        help=(
            "Target minimum samples per forcing period in frequency_scaled mode "
            "(default: 6.0)."
        ),
    )
    parser.add_argument(
        "--output_step_max",
        type=float,
        default=50.0,
        help=(
            "Maximum output time step in seconds for frequency_scaled mode. "
            "Set <=0 to disable the cap (default: 50.0)."
        ),
    )
    parser.add_argument("--ss_time", type=float, default=2000.0,
                        help="Perturbation activation time in seconds (default: 2000)")
    parser.add_argument(
        "--base_dir",
        type=str,
        default=None,
        help=(
            "Base directory for frequency response runs "
            "(default: 00runs/freq/<core_model>/power_<tag>)"
        ),
    )
    parser.add_argument("--model_name", type=str, default=None,
                        help="Modelica model to simulate "
                             "(default depends on --core_model)")
    parser.add_argument("--steady_state_table", type=str, default="",
                        help="CSV table for power-indexed steady-state overrides "
                             "(default: core/init/setpoints_<core_model>.csv)")
    parser.add_argument("--disable_steady_state_table", action="store_true",
                        help="Disable steady-state table overrides even if the table exists")
    parser.add_argument(
        "--heat_loss",
        type=int,
        choices=(0, 1),
        default=0,
        help="Select heat-loss-enabled rows from the steady-state table (0=no loss, 1=loss)",
    )
    parser.add_argument(
        "--cleanup_omc_artifacts",
        action="store_true",
        help="Delete OpenModelica temporary build/runtime files after successful runs",
    )
    parser.add_argument(
        "--reduced_csv_for_collect",
        action="store_true",
        help="Keep only time and power columns in each *_res.csv for collectFreqNominal",
    )
    args = parser.parse_args()
    if args.base_dir is None:
        args.base_dir = str(
            default_freq_case_dir(
                repo_root,
                core_model=args.core_model,
                power=args.power,
            )
        )
    return args


def validate_args(args: argparse.Namespace) -> None:
    """Validate sweep arguments before launching simulations."""
    if args.freq_min <= 0:
        raise ValueError("--freq_min must be > 0.")
    if args.freq_max <= 0:
        raise ValueError("--freq_max must be > 0.")
    if args.freq_max <= args.freq_min:
        raise ValueError("--freq_max must be greater than --freq_min.")
    if args.num_freq < 1:
        raise ValueError("--num_freq must be >= 1.")
    if args.stop_time <= 0:
        raise ValueError("--stop_time must be > 0.")
    if args.ss_time < 0:
        raise ValueError("--ss_time must be >= 0.")
    if args.ss_time >= args.stop_time:
        raise ValueError("--ss_time must be smaller than --stop_time.")
    if args.output_intervals_per_second <= 0:
        raise ValueError("--output_intervals_per_second must be > 0.")
    if args.output_interval_mode == "frequency_scaled":
        if args.output_samples_per_period <= 0:
            raise ValueError(
                "--output_samples_per_period must be > 0 when "
                "--output_interval_mode=frequency_scaled."
            )
    if args.sin_mag <= 0:
        raise ValueError("--sin_mag must be > 0.")


def load_steady_state_overrides(table_path: str, power: float, heat_loss: int) -> dict[str, float]:
    rows: list[tuple[float, dict[str, float]]] = []

    with open(table_path, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "power" not in reader.fieldnames:
            raise ValueError(
                f"Steady-state table {table_path} must contain a 'power' column."
            )

        has_heat_loss = HEAT_LOSS_COLUMN in reader.fieldnames
        for raw_row in reader:
            if not raw_row or raw_row.get("power", "").strip() == "":
                continue
            row_power = float(raw_row["power"])
            if has_heat_loss:
                raw_hl = (raw_row.get(HEAT_LOSS_COLUMN) or "").strip()
                if raw_hl == "":
                    continue
                try:
                    row_hl = int(float(raw_hl))
                except ValueError:
                    row_hl = 1 if raw_hl.lower() in ("true", "yes") else 0
                if row_hl != int(heat_loss):
                    continue
            values: dict[str, float] = {}
            for key, value in raw_row.items():
                if key in ("power", HEAT_LOSS_COLUMN) or value is None or value.strip() == "":
                    continue
                override_name = TABLE_OVERRIDE_NAME_MAP.get(key, key)
                if override_name not in ALLOWED_OVERRIDE_KEYS:
                    continue
                values[override_name] = float(value)
            if has_heat_loss:
                values[HEAT_LOSS_COLUMN] = bool(int(heat_loss))
            rows.append((row_power, values))

    if not rows:
        raise ValueError(f"Steady-state table {table_path} has no usable data rows.")

    rows.sort(key=lambda item: item[0])

    for row_power, values in rows:
        if abs(row_power - power) < 1e-12:
            return dict(values)

    if power <= rows[0][0]:
        return dict(rows[0][1])
    if power >= rows[-1][0]:
        return dict(rows[-1][1])

    for idx in range(1, len(rows)):
        low_power, low_values = rows[idx - 1]
        high_power, high_values = rows[idx]
        if low_power <= power <= high_power:
            span = high_power - low_power
            if span <= 0:
                return dict(low_values)
            weight = (power - low_power) / span
            result: dict[str, float] = {}
            for key, low_value in low_values.items():
                if key not in high_values:
                    continue
                if key == HEAT_LOSS_COLUMN:
                    result[key] = low_value
                    continue
                high_value = high_values[key]
                result[key] = low_value + weight * (high_value - low_value)
            return result

    return dict(rows[-1][1])


def format_override_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return f"{float(value):.16g}"


def run_single_freq(freq_point: float,
                    base_dir: str,
                    model_name: str,
                    power: float,
                    sin_mag: float,
                    ss_time: float,
                    stop_time: float,
                    number_of_intervals: int,
                    steady_state_overrides: dict[str, float] | None,
                    smd_library: str,
                    msrr_model: str,
                    smd_library_src: str,
                    msrr_model_src: str,
                    cleanup_omc_artifacts: bool,
                    reduced_csv_for_collect: bool) -> dict:
    result = {"freq": freq_point, "success": False, "error": None, "warning": None}

    try:
        work_path = os.path.join(base_dir, f"freq{freq_point:08.5f}")
        os.makedirs(work_path, exist_ok=True)

        copyfile(smd_library_src, os.path.join(work_path, smd_library))
        copyfile(msrr_model_src, os.path.join(work_path, msrr_model))

        file_prefix = f"MSRR_freq{freq_point:08.5f}"
        override_parts = [
            f"powerLevel={power:.10g}",
            f"perturbationAmplitudePcm={sin_mag:.10g}",
            f"perturbationOmega={freq_point:.10g}",
            f"perturbationStartTime={ss_time:.10g}",
            "primaryPump.freeConvFF=1",
            "secondaryPump.freeConvFF=1",
        ]
        if steady_state_overrides:
            for key in sorted(steady_state_overrides):
                override_parts.append(f"{key}={format_override_value(steady_state_overrides[key])}")
        override = ",".join(override_parts)

        simflags = build_simflags(
            override,
            reduced_csv_for_collect=reduced_csv_for_collect,
        )

        run_script = (
            f'// SMD-MSRR-dev Frequency Response\n'
            f'loadFile("{smd_library}");\n'
            f'loadFile("{msrr_model}");\n'
            f'simulate({model_name},'
            f'startTime=0,'
            f'stopTime={stop_time:.0f},'
            f'numberOfIntervals={number_of_intervals},'
            f'tolerance=1E-6,'
            f'method=dassl,'
            f'outputFormat="csv",'
            f'fileNamePrefix="{file_prefix}",'
            f'simflags="{simflags}");\n'
        )

        run_script_path = os.path.join(work_path, "runModelica.mos")
        with open(run_script_path, "w") as handle:
            handle.write(run_script)

        omc_result = subprocess.run(
            ["omc", "--showErrorMessages", "runModelica.mos"],
            cwd=work_path,
            capture_output=True,
            text=True,
        )

        with open(os.path.join(work_path, "omc_stdout.log"), "w") as handle:
            handle.write(omc_result.stdout)
        with open(os.path.join(work_path, "omc_stderr.log"), "w") as handle:
            handle.write(omc_result.stderr)

        if omc_result.returncode != 0:
            result["error"] = (
                f"omc returned exit code {omc_result.returncode}; "
                f"see {work_path}/omc_stderr.log"
            )
        else:
            csv_path = os.path.join(work_path, f"{file_prefix}_res.csv")
            if os.path.exists(csv_path):
                if reduced_csv_for_collect:
                    reduce_csv_to_collect_columns(csv_path)
                if cleanup_omc_artifacts:
                    keep = {
                        os.path.basename(csv_path),
                        "omc_stdout.log",
                        "omc_stderr.log",
                    }
                    cleanup_omc_artifacts_in_dir(work_path=work_path, keep_files=keep)
                result["success"] = True
            else:
                result["error"] = (
                    "omc exited 0 but no output CSV found; "
                    f"see {work_path}/omc_stdout.log"
                )

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main() -> None:
    args = parse_args()
    validate_args(args)
    resolved_model_name = args.model_name or MODEL_NAME_BY_CORE[args.core_model]

    freq_space = np.logspace(np.log10(args.freq_min),
                             np.log10(args.freq_max),
                             num=args.num_freq)
    number_of_intervals_by_freq = {
        float(fp): compute_number_of_intervals(
            float(fp),
            stop_time=args.stop_time,
            output_interval_mode=args.output_interval_mode,
            output_intervals_per_second=args.output_intervals_per_second,
            output_samples_per_period=args.output_samples_per_period,
            output_step_max=args.output_step_max,
        )
        for fp in freq_space
    }

    base_dir = os.path.abspath(args.base_dir)
    os.makedirs(base_dir, exist_ok=True)

    steady_state_table = (
        os.path.abspath(args.steady_state_table)
        if args.steady_state_table
        else default_steady_state_table(args.core_model)
    )
    steady_state_overrides = None
    using_steady_table = False

    if not args.disable_steady_state_table and os.path.exists(steady_state_table):
        steady_state_overrides = load_steady_state_overrides(
            table_path=steady_state_table,
            power=args.power,
            heat_loss=args.heat_loss,
        )
        using_steady_table = True
    if steady_state_overrides is None:
        if args.heat_loss:
            steady_state_overrides = {HEAT_LOSS_COLUMN: True}
    else:
        steady_state_overrides.setdefault(HEAT_LOSS_COLUMN, bool(args.heat_loss))

    params_file = os.path.join(base_dir, "run_params.txt")
    with open(params_file, "w") as handle:
        handle.write(f"core_model\t{args.core_model}\n")
        handle.write(f"core_dir\t{os.path.abspath(args.core_dir)}\n")
        handle.write(f"model_name\t{resolved_model_name}\n")
        handle.write(f"power\t{args.power}\n")
        handle.write(f"freq_min\t{args.freq_min}\n")
        handle.write(f"freq_max\t{args.freq_max}\n")
        handle.write(f"num_freq\t{args.num_freq}\n")
        handle.write(f"sin_mag\t{args.sin_mag}\n")
        handle.write(f"stop_time\t{args.stop_time}\n")
        handle.write(f"output_interval_mode\t{args.output_interval_mode}\n")
        handle.write(
            f"output_intervals_per_second\t{args.output_intervals_per_second}\n"
        )
        handle.write(
            f"output_samples_per_period\t{args.output_samples_per_period}\n"
        )
        handle.write(f"output_step_max\t{args.output_step_max}\n")
        handle.write(f"ss_time\t{args.ss_time}\n")
        handle.write(f"using_steady_state_table\t{using_steady_table}\n")
        handle.write(f"steady_state_table\t{steady_state_table}\n")
        handle.write(f"heat_loss\t{args.heat_loss}\n")
        handle.write(f"cleanup_omc_artifacts\t{args.cleanup_omc_artifacts}\n")
        handle.write(f"reduced_csv_for_collect\t{args.reduced_csv_for_collect}\n")

    core_dir = os.path.abspath(args.core_dir)
    smd_library = "SMD_MSR_Modelica.mo"
    msrr_model = "MSRR.mo"
    smd_library_src = os.path.join(core_dir, smd_library)
    msrr_model_src = os.path.join(core_dir, msrr_model)

    if not os.path.exists(smd_library_src):
        raise FileNotFoundError(
            f"Cannot find {smd_library_src}."
        )
    if not os.path.exists(msrr_model_src):
        raise FileNotFoundError(
            f"Cannot find {msrr_model_src}."
        )

    print("MSRR Frequency Response — Nominal Trim (serial)")
    print(f"  Core model:       {args.core_model}")
    print(f"  Model:            {resolved_model_name}")
    print(f"  Core dir:         {core_dir}")
    print(f"  Power level:      {args.power}")
    print(f"  Frequency range:  {args.freq_min:.4f} – {args.freq_max:.4f} rad/s")
    print(f"  Frequency points: {args.num_freq}")
    print(f"  Perturbation:     {args.sin_mag} pcm")
    print(f"  Stop time:        {args.stop_time} s")
    print(f"  SS settle time:   {args.ss_time} s")
    print(f"  Base directory:   {base_dir}")
    print(f"  Reduced CSV:      {args.reduced_csv_for_collect}")
    print(f"  Cleanup OMC:      {args.cleanup_omc_artifacts}")
    if using_steady_table:
        print(f"  SS table:         {steady_state_table} "
              f"({len(steady_state_overrides)} overrides)")
    elif args.disable_steady_state_table:
        print("  SS table:         disabled")
    else:
        print(f"  SS table:         not found at {steady_state_table} (not used)")
    print("=" * 70)

    succeeded = 0
    failed = 0

    for index, freq_point in enumerate(freq_space, start=1):
        print(f"[{index}/{len(freq_space)}] freq = {freq_point:8.5f} rad/s")
        result = run_single_freq(
            freq_point=freq_point,
            base_dir=base_dir,
            model_name=resolved_model_name,
            power=args.power,
            sin_mag=args.sin_mag,
            ss_time=args.ss_time,
            stop_time=args.stop_time,
            number_of_intervals=number_of_intervals_by_freq[float(freq_point)],
            steady_state_overrides=steady_state_overrides,
            smd_library=smd_library,
            msrr_model=msrr_model,
            smd_library_src=smd_library_src,
            msrr_model_src=msrr_model_src,
            cleanup_omc_artifacts=args.cleanup_omc_artifacts,
            reduced_csv_for_collect=args.reduced_csv_for_collect,
        )

        if result["success"]:
            succeeded += 1
            if result["warning"]:
                print(f"  ok (warning: {result['warning']})")
            else:
                print("  ok")
        else:
            failed += 1
            print(f"  failed: {result['error']}")

    print("=" * 70)
    print(f"Completed: {succeeded} succeeded, {failed} failed out of {len(freq_space)} total.")
    print(f"Results are in: {base_dir}")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
