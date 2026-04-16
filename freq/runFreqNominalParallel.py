#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Run frequency response analysis for the MSRR nominal-trim model in parallel.

This script uses a dedicated non-startup scenario model:
    1r: MSRR.MSRRuhxNominalTrim
    9r: MSRR.MSRRuhxNominalTrim9R
and sweeps perturbation frequency using OpenModelica parameter overrides.

No source-file text patching is performed.

Usage:
    python runFreqNominalParallel.py [--power POWER] [--freq_min FREQ_MIN]
                                    [--freq_max FREQ_MAX] [--num_freq NUM_FREQ]
                                    [--sin_mag SIN_MAG] [--stop_time STOP_TIME]
                                    [--ss_time SS_TIME] [--n_jobs N_JOBS]
                                    [--base_dir BASE_DIR]
                                    [--model_name MODEL_NAME]
"""

import argparse
import csv
import math
import numpy as np
import os
import pandas as pd
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MPKE_PATH_BY_CORE = {
    "1r": "core1R.mpke",
    "9r": "msre9r.mpke",
}

ALLOWED_OVERRIDE_KEYS = {
    "fuelTempSetPointNode1",
    "fuelTempSetPointNode2",
    "graphiteTempSetPoint",
    "heatLossEnabled",
    "Tmix_0",
    "heatExchanger.TpIn_0",
    "heatExchanger.TpOut_0",
    "heatExchanger.TsIn_0",
    "heatExchanger.TsOut_0",
    "heatExchanger.T_PN1_0",
    "heatExchanger.T_PN2_0",
    "heatExchanger.T_PN3_0",
    "heatExchanger.T_PN4_0",
    "heatExchanger.T_TN1_0",
    "heatExchanger.T_TN2_0",
    "heatExchanger.T_SN1_0",
    "heatExchanger.T_SN2_0",
    "heatExchanger.T_SN3_0",
    "heatExchanger.T_SN4_0",
    "pipeHXtoUHX.T_0",
    "pipeUHXtoHX.T_0",
    "dhrs.T_0",
    "pipeDHRStoHX.T_0",
    "pipeHXtoCore.T_0",
    "pipeCoreToDHRS.T_0",
    "uhx.Tp_0",
}
HX_DETAILED_STATE_OVERRIDE_KEYS = {
    "heatExchanger.T_PN1_0",
    "heatExchanger.T_PN2_0",
    "heatExchanger.T_PN3_0",
    "heatExchanger.T_PN4_0",
    "heatExchanger.T_TN1_0",
    "heatExchanger.T_TN2_0",
    "heatExchanger.T_SN1_0",
    "heatExchanger.T_SN2_0",
    "heatExchanger.T_SN3_0",
    "heatExchanger.T_SN4_0",
}
for _idx in range(2, 10):
    ALLOWED_OVERRIDE_KEYS.add(f"TF1_0_regions[{_idx}]")
for _idx in range(2, 10):
    ALLOWED_OVERRIDE_KEYS.add(f"TF2_0_regions[{_idx}]")
for _idx in range(2, 10):
    ALLOWED_OVERRIDE_KEYS.add(f"TG_0_regions[{_idx}]")

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
STOP_TIME_MODE_CHOICES = ("fixed", "min_cycles_after_ss")
OUTPUT_INTERVAL_MODE_CHOICES = ("fixed_rate", "frequency_scaled")
LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT = 1.0
LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT = 1e-3
LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT = 1.0
LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT = 1e-2
LOW_POWER_AUTO_SS_TIME_FACTOR_DEFAULT = 400.0
LOW_POWER_AUTO_STOP_TAIL_MAX_DEFAULT = 5.0e4
LOW_POWER_AUTO_MIN_CYCLES_AFTER_SS_DEFAULT = 12.0

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
        for candidate in COLLECT_POWER_CANDIDATES:
            if item == candidate or item.endswith(candidate) or candidate in item:
                power_idx = idx
                break
        if power_idx >= 0:
            break
    if power_idx < 0:
        for idx, item in enumerate(cleaned):
            if (
                "npopulation" in item
                or "nompower" in item
                or "fissionpower" in item
            ):
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
        header_df = pd.read_csv(csv_path, nrows=0)
        header = list(header_df.columns)
        if not header:
            raise ValueError(f"CSV is empty: {csv_path}")
        time_idx, power_idx = find_collect_columns(header)
        selected_cols = [header[time_idx], header[power_idx]]
        reduced_df = pd.read_csv(csv_path, usecols=selected_cols)
        reduced_df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def build_simflags(
    override: str,
    *,
    simflags_extra: str = "",
    reduced_csv_for_collect: bool = False,
) -> str:
    simflag_parts = [f"-override={override}"]
    simflags_extra = simflags_extra.strip()
    if reduced_csv_for_collect and "-variableFilter" not in simflags_extra:
        simflag_parts.append(
            f"-variableFilter={shlex.quote(REDUCED_CSV_VARIABLE_FILTER)}"
        )
    if simflags_extra:
        simflag_parts.append(simflags_extra)
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


def default_cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def format_frequency_key(freq: float) -> str:
    """Format a frequency key exactly as persisted by the runner."""
    return f"{float(freq):.12g}"


def compute_effective_stop_time(
    freq_point: float,
    *,
    base_stop_time: float,
    ss_time: float,
    stop_time_mode: str,
    min_cycles_after_ss: float,
) -> float:
    if stop_time_mode == "fixed":
        return float(base_stop_time)
    if stop_time_mode != "min_cycles_after_ss":
        raise ValueError(f"Unsupported stop_time_mode: {stop_time_mode}")
    if freq_point <= 0:
        raise ValueError("frequency must be positive for dynamic stop-time mode")
    if min_cycles_after_ss <= 0:
        raise ValueError("min_cycles_after_ss must be > 0 for dynamic stop-time mode")

    required = ss_time + min_cycles_after_ss * (2.0 * math.pi / freq_point)
    return float(max(base_stop_time, math.ceil(required)))


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

    forcing_period = 2.0 * math.pi / freq_point
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
    return max(1, int(math.ceil(stop_time / output_step)))


def resolve_low_power_auto_protocol(
    *,
    power: float,
    low_power_threshold: float,
    disable_low_power_auto_time_horizon: bool,
    disable_low_power_auto_output_grid: bool,
    ss_time: float,
    stop_time: float,
    stop_time_mode: str,
    min_cycles_after_ss: float,
    output_interval_mode: str,
    low_power_auto_ss_time_factor: float,
    low_power_auto_stop_tail_max: float,
    low_power_auto_min_cycles_after_ss: float,
) -> dict[str, float | str | bool]:
    effective_ss_time = float(ss_time)
    effective_stop_time = float(stop_time)
    effective_stop_time_mode = str(stop_time_mode)
    effective_min_cycles_after_ss = float(min_cycles_after_ss)
    effective_output_interval_mode = str(output_interval_mode)
    auto_time_horizon_applied = False
    auto_output_grid_applied = False

    if 0 < power <= low_power_threshold:
        if not disable_low_power_auto_time_horizon:
            auto_ss_time = low_power_auto_ss_time_factor / power
            effective_ss_time = max(effective_ss_time, auto_ss_time)
            effective_stop_time = max(
                effective_stop_time,
                effective_ss_time
                + min(low_power_auto_stop_tail_max, effective_ss_time / 4.0),
            )
            effective_stop_time_mode = "min_cycles_after_ss"
            effective_min_cycles_after_ss = max(
                effective_min_cycles_after_ss,
                low_power_auto_min_cycles_after_ss,
            )
            auto_time_horizon_applied = True
        if not disable_low_power_auto_output_grid:
            effective_output_interval_mode = "frequency_scaled"
            auto_output_grid_applied = True

    return {
        "ss_time": float(effective_ss_time),
        "stop_time": float(effective_stop_time),
        "stop_time_mode": effective_stop_time_mode,
        "min_cycles_after_ss": float(effective_min_cycles_after_ss),
        "output_interval_mode": effective_output_interval_mode,
        "auto_time_horizon_applied": auto_time_horizon_applied,
        "auto_output_grid_applied": auto_output_grid_applied,
    }


def compute_base_sin_mag(
    *,
    sin_mag: float,
    sin_mag_auto: bool,
    sin_mag_ref: float,
    sin_mag_min: float,
    sin_mag_max: float,
    power: float,
) -> float:
    resolved = float(sin_mag)
    if sin_mag_auto:
        safe_power = max(power, 1.0e-6)
        scaled = sin_mag_ref / safe_power
        resolved = max(sin_mag_min, min(sin_mag_max, scaled))
    return float(resolved)


def should_apply_low_power_slowfreq_sin_mag_cap(
    *,
    power: float,
    low_power_threshold: float,
    sin_mag_auto: bool,
    sin_mag_logscale: bool,
    disable_low_power_slowfreq_sin_mag_cap: bool,
    low_power_slowfreq_sin_mag_cap: float,
    low_power_slowfreq_sin_mag_cap_freq: float,
) -> bool:
    return (
        sin_mag_auto
        and not sin_mag_logscale
        and not disable_low_power_slowfreq_sin_mag_cap
        and power <= low_power_threshold
        and low_power_slowfreq_sin_mag_cap > 0
        and low_power_slowfreq_sin_mag_cap_freq > 0
    )


def should_apply_low_power_allfreq_sin_mag_cap(
    *,
    power: float,
    sin_mag_auto: bool,
    sin_mag_logscale: bool,
    disable_low_power_allfreq_sin_mag_cap: bool,
    low_power_allfreq_sin_mag_cap: float,
    low_power_allfreq_sin_mag_cap_power: float,
) -> bool:
    return (
        sin_mag_auto
        and not sin_mag_logscale
        and not disable_low_power_allfreq_sin_mag_cap
        and power <= low_power_allfreq_sin_mag_cap_power
        and low_power_allfreq_sin_mag_cap > 0
        and low_power_allfreq_sin_mag_cap_power > 0
    )


def build_sin_mag_profile(
    freq_space: np.ndarray,
    *,
    base_sin_mag: float,
    sin_mag_logscale: bool,
    sin_mag_low: float,
    sin_mag_high: float,
    apply_low_power_slowfreq_cap: bool,
    low_power_slowfreq_sin_mag_cap: float,
    low_power_slowfreq_sin_mag_cap_freq: float,
) -> dict[float, float] | None:
    mapping: dict[float, float] | None = None
    if sin_mag_logscale:
        log_min = np.log10(float(freq_space[0]))
        log_max = np.log10(float(freq_space[-1]))
        denom = max(1e-12, log_max - log_min)
        mapping = {}
        for f in freq_space:
            freq_point = float(f)
            t = (np.log10(freq_point) - log_min) / denom
            mapping[freq_point] = sin_mag_low * (sin_mag_high / sin_mag_low) ** t

    if apply_low_power_slowfreq_cap:
        if mapping is None:
            mapping = {float(f): float(base_sin_mag) for f in freq_space}
        for f in freq_space:
            freq_point = float(f)
            if freq_point <= low_power_slowfreq_sin_mag_cap_freq:
                mapping[freq_point] = min(
                    mapping[freq_point],
                    float(low_power_slowfreq_sin_mag_cap),
                )

    if mapping is None:
        return None
    if all(abs(value - base_sin_mag) <= 1e-12 for value in mapping.values()):
        return None
    return mapping


def csv_reaches_stop_time(csv_path: str, stop_time: float) -> bool:
    """
    Return True if the CSV's last time value reaches (approximately) stop_time.
    Uses a light-weight tail read to avoid loading large files.
    """
    try:
        with open(csv_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return False
            read_size = min(size, 16384)
            handle.seek(-read_size, os.SEEK_END)
            tail = handle.read().decode("utf-8", errors="ignore")

        lines = [line for line in tail.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        last = lines[-1]
        last_time = float(last.split(",")[0].strip().strip('"'))
        return last_time >= 0.995 * stop_time
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_core_dir = str(repo_root / "core")
    parser = argparse.ArgumentParser(
        description="Run MSRR frequency response simulations using MSRRuhxNominalTrim (parallel)."
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
    parser.add_argument(
        "--sin_mag_auto",
        action="store_true",
        help=(
            "Scale sin_mag automatically with power using sin_mag_ref/power, "
            "clamped to [sin_mag_min, sin_mag_max]. By default, very-low-power "
            f"sweeps (power <= {LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT:g}) "
            f"are capped at {LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT:g} pcm for all bins, "
            "and low-power slow bins are additionally capped at "
            f"{LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT:g} pcm for "
            f"omega <= {LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT:g} rad/s."
        ),
    )
    parser.add_argument(
        "--sin_mag_ref",
        type=float,
        default=0.1,
        help="Reference power used for sin_mag_auto scaling (default: 0.1 MW).",
    )
    parser.add_argument(
        "--sin_mag_min",
        type=float,
        default=1.0,
        help="Minimum sin_mag when sin_mag_auto is enabled (default: 1.0 pcm).",
    )
    parser.add_argument(
        "--sin_mag_max",
        type=float,
        default=20.0,
        help="Maximum sin_mag when sin_mag_auto is enabled (default: 20.0 pcm).",
    )
    parser.add_argument(
        "--sin_mag_logscale",
        action="store_true",
        help=(
            "Log-scale sin_mag across the frequency sweep from sin_mag_low at "
            "freq_min to sin_mag_high at freq_max."
        ),
    )
    parser.add_argument(
        "--sin_mag_low",
        type=float,
        default=10.0,
        help="Low-frequency perturbation amplitude in pcm (default: 10.0).",
    )
    parser.add_argument(
        "--sin_mag_high",
        type=float,
        default=1.0,
        help="High-frequency perturbation amplitude in pcm (default: 1.0).",
    )
    parser.add_argument(
        "--low_power_allfreq_sin_mag_cap",
        type=float,
        default=LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT,
        help=(
            "Cap perturbation amplitude to this value for all frequencies when "
            "--sin_mag_auto is used at very low power (default: 1.0 pcm)."
        ),
    )
    parser.add_argument(
        "--low_power_allfreq_sin_mag_cap_power",
        type=float,
        default=LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT,
        help=(
            "Apply the very-low-power all-frequency sin_mag cap for power <= this "
            f"value in MW (default: {LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT:g})."
        ),
    )
    parser.add_argument(
        "--disable_low_power_allfreq_sin_mag_cap",
        action="store_true",
        help=(
            "Disable the default very-low-power all-frequency perturbation cap "
            "used with --sin_mag_auto."
        ),
    )
    parser.add_argument(
        "--low_power_slowfreq_sin_mag_cap",
        type=float,
        default=LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT,
        help=(
            "Cap per-frequency perturbation amplitude to this value for low-power "
            "slow bins when --sin_mag_auto is used (default: 1.0 pcm)."
        ),
    )
    parser.add_argument(
        "--low_power_slowfreq_sin_mag_cap_freq",
        type=float,
        default=LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT,
        help=(
            "Apply the low-power slow-frequency sin_mag cap for omega <= this "
            "value in rad/s (default: 1e-2)."
        ),
    )
    parser.add_argument(
        "--disable_low_power_slowfreq_sin_mag_cap",
        action="store_true",
        help=(
            "Disable the default low-power slow-frequency perturbation cap used "
            "with --sin_mag_auto."
        ),
    )
    parser.add_argument("--stop_time", type=float, default=10000.0,
                        help="Simulation stop time in seconds (default: 10000)")
    parser.add_argument(
        "--stop_time_mode",
        type=str,
        choices=STOP_TIME_MODE_CHOICES,
        default="fixed",
        help=(
            "How to determine stop time per frequency. 'fixed' uses --stop_time "
            "for all points; 'min_cycles_after_ss' enforces at least "
            "--min_cycles_after_ss cycles after perturbation start."
        ),
    )
    parser.add_argument(
        "--min_cycles_after_ss",
        type=float,
        default=12.0,
        help=(
            "Minimum number of post-perturbation sine cycles when "
            "--stop_time_mode=min_cycles_after_ss (default: 12.0)."
        ),
    )
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
        "--low_power_threshold",
        type=float,
        default=1e-2,
        help=(
            "Apply low-power forcing tweaks when power <= this threshold "
            "(default: 1e-2)."
        ),
    )
    parser.add_argument(
        "--low_power_auto_ss_time_factor",
        type=float,
        default=LOW_POWER_AUTO_SS_TIME_FACTOR_DEFAULT,
        help=(
            "Automatic low-power settle-time rule uses ss_time >= factor/power "
            f"(default: {LOW_POWER_AUTO_SS_TIME_FACTOR_DEFAULT:g})."
        ),
    )
    parser.add_argument(
        "--low_power_auto_stop_tail_max",
        type=float,
        default=LOW_POWER_AUTO_STOP_TAIL_MAX_DEFAULT,
        help=(
            "Maximum extra base stop-time tail added after the automatic "
            "low-power ss_time, in seconds "
            f"(default: {LOW_POWER_AUTO_STOP_TAIL_MAX_DEFAULT:g})."
        ),
    )
    parser.add_argument(
        "--low_power_auto_min_cycles_after_ss",
        type=float,
        default=LOW_POWER_AUTO_MIN_CYCLES_AFTER_SS_DEFAULT,
        help=(
            "Automatic low-power protocol enforces at least this many cycles "
            "after forcing "
            f"(default: {LOW_POWER_AUTO_MIN_CYCLES_AFTER_SS_DEFAULT:g})."
        ),
    )
    parser.add_argument(
        "--disable_low_power_auto_time_horizon",
        action="store_true",
        help=(
            "Disable the default low-power long-horizon protocol that raises "
            "ss_time/stop_time and enables dynamic post-forcing cycle coverage."
        ),
    )
    parser.add_argument(
        "--disable_low_power_auto_output_grid",
        action="store_true",
        help=(
            "Disable the default low-power frequency-scaled output grid and keep "
            "the requested output_interval_mode."
        ),
    )
    parser.add_argument(
        "--forcing_time_step",
        type=float,
        default=0.0,
        help=(
            "Explicit forcing-window event cadence [s] applied at all powers. "
            "Set >0 to inject periodic time events during the sinusoidal "
            "forcing window; keep 0 to use the model default."
        ),
    )
    parser.add_argument(
        "--low_power_forcing_step",
        type=float,
        default=1.0,
        help=(
            "Baseline forcing-window event cadence [s] for low-power runs "
            "(used for omega <= low_power_hifreq_split in mixed mode). "
            "Set <=0 to disable (default: 1.0)."
        ),
    )
    parser.add_argument(
        "--low_power_forcing_step_hifreq",
        type=float,
        default=0.05,
        help=(
            "High-frequency forcing-window event cadence [s] for low-power runs "
            "when omega > low_power_hifreq_split (default: 0.05)."
        ),
    )
    parser.add_argument(
        "--low_power_hifreq_split",
        type=float,
        default=1.0,
        help=(
            "Angular-frequency split [rad/s] for mixed low-power forcing cadence "
            "(default: 1.0)."
        ),
    )
    parser.add_argument(
        "--low_power_nfloor_pre",
        type=float,
        default=1e-9,
        help=(
            "Normalized neutron-population floor before forcing for low-power runs "
            "(default: 1e-9)."
        ),
    )
    parser.add_argument(
        "--low_power_nfloor_forcing",
        type=float,
        default=1e-9,
        help=(
            "Normalized neutron-population floor after perturbation start for "
            "low-power runs (default: 1e-9)."
        ),
    )
    parser.add_argument(
        "--disable_low_power_tweaks",
        action="store_true",
        help="Disable automatic low-power forcing-step/nFloor overrides.",
    )
    parser.add_argument(
        "--disable_low_power_mixed_forcing_step",
        action="store_true",
        help=(
            "Disable mixed low-power forcing cadence and use a single "
            "low_power_forcing_step for all frequencies."
        ),
    )
    parser.add_argument(
        "--simflags_extra",
        type=str,
        default="",
        help="Additional OpenModelica simflags appended after -override.",
    )
    parser.add_argument("--n_jobs", type=int, default=default_cpu_count(),
                        help=f"Number of parallel omc processes (default: {default_cpu_count()})")
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
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="Do not reuse existing *_res.csv results; always rerun each frequency.",
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
    if args.freq_max < args.freq_min:
        raise ValueError("--freq_max must be greater than or equal to --freq_min.")
    if args.freq_max == args.freq_min and args.num_freq != 1:
        raise ValueError("--freq_max must be greater than --freq_min.")
    if args.num_freq < 1:
        raise ValueError("--num_freq must be >= 1.")
    if args.stop_time <= 0:
        raise ValueError("--stop_time must be > 0.")
    if args.ss_time < 0:
        raise ValueError("--ss_time must be >= 0.")
    if args.ss_time >= args.stop_time:
        raise ValueError("--ss_time must be smaller than --stop_time.")
    if args.stop_time_mode != "fixed" and args.min_cycles_after_ss <= 0:
        raise ValueError("--min_cycles_after_ss must be > 0 when dynamic stop time is used.")
    if args.output_intervals_per_second <= 0:
        raise ValueError("--output_intervals_per_second must be > 0.")
    if args.output_interval_mode == "frequency_scaled":
        if args.output_samples_per_period <= 0:
            raise ValueError(
                "--output_samples_per_period must be > 0 when "
                "--output_interval_mode=frequency_scaled."
            )
    if args.n_jobs < 1:
        raise ValueError("--n_jobs must be >= 1.")
    if args.sin_mag <= 0:
        raise ValueError("--sin_mag must be > 0.")
    if args.sin_mag_auto:
        if args.sin_mag_ref <= 0:
            raise ValueError("--sin_mag_ref must be > 0 when --sin_mag_auto is used.")
        if args.sin_mag_min <= 0 or args.sin_mag_max <= 0:
            raise ValueError("--sin_mag_min and --sin_mag_max must be > 0.")
        if args.sin_mag_min > args.sin_mag_max:
            raise ValueError("--sin_mag_min must be <= --sin_mag_max.")
    if args.sin_mag_logscale:
        if args.sin_mag_low <= 0 or args.sin_mag_high <= 0:
            raise ValueError("--sin_mag_low and --sin_mag_high must be > 0.")
    low_power_allfreq_sin_mag_cap = float(
        getattr(
            args,
            "low_power_allfreq_sin_mag_cap",
            LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT,
        )
    )
    low_power_allfreq_sin_mag_cap_power = float(
        getattr(
            args,
            "low_power_allfreq_sin_mag_cap_power",
            LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT,
        )
    )
    low_power_slowfreq_sin_mag_cap = float(
        getattr(
            args,
            "low_power_slowfreq_sin_mag_cap",
            LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT,
        )
    )
    low_power_slowfreq_sin_mag_cap_freq = float(
        getattr(
            args,
            "low_power_slowfreq_sin_mag_cap_freq",
            LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT,
        )
    )
    if low_power_allfreq_sin_mag_cap <= 0:
        raise ValueError("--low_power_allfreq_sin_mag_cap must be > 0.")
    if low_power_allfreq_sin_mag_cap_power <= 0:
        raise ValueError("--low_power_allfreq_sin_mag_cap_power must be > 0.")
    if low_power_slowfreq_sin_mag_cap <= 0:
        raise ValueError("--low_power_slowfreq_sin_mag_cap must be > 0.")
    if low_power_slowfreq_sin_mag_cap_freq <= 0:
        raise ValueError("--low_power_slowfreq_sin_mag_cap_freq must be > 0.")
    if args.low_power_auto_ss_time_factor <= 0:
        raise ValueError("--low_power_auto_ss_time_factor must be > 0.")
    if args.low_power_auto_stop_tail_max < 0:
        raise ValueError("--low_power_auto_stop_tail_max must be >= 0.")
    if args.low_power_auto_min_cycles_after_ss <= 0:
        raise ValueError("--low_power_auto_min_cycles_after_ss must be > 0.")
    if args.low_power_hifreq_split < 0:
        raise ValueError("--low_power_hifreq_split must be >= 0.")
    if args.forcing_time_step < 0:
        raise ValueError("--forcing_time_step must be >= 0.")
    if args.low_power_nfloor_pre <= 0 or args.low_power_nfloor_forcing <= 0:
        raise ValueError("--low_power_nfloor_pre and --low_power_nfloor_forcing must be > 0.")


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
                    forcing_time_step: float,
                    low_power_mixed_forcing_step: bool,
                    low_power_forcing_step: float,
                    low_power_forcing_step_hifreq: float,
                    low_power_hifreq_split: float,
                    smd_library: str,
                    msrr_model: str,
                    smd_library_src: str,
                    msrr_model_src: str,
                    allow_reuse: bool,
                    cleanup_omc_artifacts: bool,
                    reduced_csv_for_collect: bool,
                    simflags_extra: str = "") -> dict:
    result = {
        "freq": freq_point,
        "success": False,
        "error": None,
        "warning": None,
        "forcing_step": None,
    }

    try:
        work_path = os.path.join(base_dir, f"freq{freq_point:08.5f}")
        os.makedirs(work_path, exist_ok=True)

        file_prefix = f"MSRR_freq{freq_point:08.5f}"
        csv_path = os.path.join(work_path, f"{file_prefix}_res.csv")
        tmp_csv_path = f"{csv_path}.tmp"
        if allow_reuse:
            if (
                os.path.exists(csv_path)
                and not os.path.exists(tmp_csv_path)
                and csv_reaches_stop_time(csv_path=csv_path, stop_time=stop_time)
            ):
                if reduced_csv_for_collect:
                    reduce_csv_to_collect_columns(csv_path)
                result["success"] = True
                result["warning"] = "existing result reused"
                return result
        if os.path.exists(tmp_csv_path):
            os.remove(tmp_csv_path)

        copyfile(smd_library_src, os.path.join(work_path, smd_library))
        copyfile(msrr_model_src, os.path.join(work_path, msrr_model))

        local_overrides = dict(steady_state_overrides or {})
        if forcing_time_step > 0:
            local_overrides["forcingTimeStep"] = forcing_time_step
            result["forcing_step"] = forcing_time_step
        if low_power_mixed_forcing_step:
            forcing_step = (
                low_power_forcing_step_hifreq
                if freq_point > low_power_hifreq_split
                else low_power_forcing_step
            )
            if forcing_step > 0:
                local_overrides["forcingTimeStep"] = forcing_step
                result["forcing_step"] = forcing_step

        override_parts = [
            f"powerLevel={power:.10g}",
            f"perturbationAmplitudePcm={sin_mag:.10g}",
            f"perturbationOmega={freq_point:.10g}",
            f"perturbationStartTime={ss_time:.10g}",
            "primaryPump.freeConvFF=1",
            "secondaryPump.freeConvFF=1",
        ]
        if local_overrides:
            if any(key in local_overrides for key in HX_DETAILED_STATE_OVERRIDE_KEYS):
                override_parts.append("heatExchanger.detailedStateInitWeight=1")
            for key in sorted(local_overrides):
                override_parts.append(f"{key}={format_override_value(local_overrides[key])}")
        override = ",".join(override_parts)

        simflags = build_simflags(
            override,
            simflags_extra=simflags_extra,
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
            if os.path.exists(csv_path):
                if reduced_csv_for_collect:
                    reduce_csv_to_collect_columns(csv_path)
                if csv_reaches_stop_time(csv_path=csv_path, stop_time=stop_time):
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
                        "simulation output did not reach requested stop_time; "
                        f"see {work_path}/omc_stdout.log and {work_path}/omc_stderr.log"
                    )
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
    if "startup" in resolved_model_name.lower():
        raise ValueError(
            "Frequency workflow must use nominal-trim models, not startup models. "
            f"Got: {resolved_model_name}"
        )

    requested_ss_time = float(args.ss_time)
    requested_stop_time = float(args.stop_time)
    requested_stop_time_mode = str(args.stop_time_mode)
    requested_min_cycles_after_ss = float(args.min_cycles_after_ss)
    requested_output_interval_mode = str(args.output_interval_mode)
    effective_protocol = resolve_low_power_auto_protocol(
        power=args.power,
        low_power_threshold=args.low_power_threshold,
        disable_low_power_auto_time_horizon=args.disable_low_power_auto_time_horizon,
        disable_low_power_auto_output_grid=args.disable_low_power_auto_output_grid,
        ss_time=requested_ss_time,
        stop_time=requested_stop_time,
        stop_time_mode=requested_stop_time_mode,
        min_cycles_after_ss=requested_min_cycles_after_ss,
        output_interval_mode=requested_output_interval_mode,
        low_power_auto_ss_time_factor=args.low_power_auto_ss_time_factor,
        low_power_auto_stop_tail_max=args.low_power_auto_stop_tail_max,
        low_power_auto_min_cycles_after_ss=args.low_power_auto_min_cycles_after_ss,
    )
    effective_ss_time = float(effective_protocol["ss_time"])
    effective_stop_time = float(effective_protocol["stop_time"])
    effective_stop_time_mode = str(effective_protocol["stop_time_mode"])
    effective_min_cycles_after_ss = float(
        effective_protocol["min_cycles_after_ss"]
    )
    effective_output_interval_mode = str(
        effective_protocol["output_interval_mode"]
    )
    low_power_auto_time_horizon_applied = bool(
        effective_protocol["auto_time_horizon_applied"]
    )
    low_power_auto_output_grid_applied = bool(
        effective_protocol["auto_output_grid_applied"]
    )

    freq_space = np.logspace(np.log10(args.freq_min),
                             np.log10(args.freq_max),
                             num=args.num_freq)
    stop_time_by_freq = {
        float(fp): compute_effective_stop_time(
            float(fp),
            base_stop_time=effective_stop_time,
            ss_time=effective_ss_time,
            stop_time_mode=effective_stop_time_mode,
            min_cycles_after_ss=effective_min_cycles_after_ss,
        )
        for fp in freq_space
    }
    number_of_intervals_by_freq = {
        float(fp): compute_number_of_intervals(
            float(fp),
            stop_time=stop_time_by_freq[float(fp)],
            output_interval_mode=effective_output_interval_mode,
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

    low_power_tweaks_applied = False
    low_power_mixed_forcing_step = False
    if (not args.disable_low_power_tweaks) and args.power <= args.low_power_threshold:
        if steady_state_overrides is None:
            steady_state_overrides = {}
        mpke_path = MPKE_PATH_BY_CORE[args.core_model]
        steady_state_overrides[f"{mpke_path}.nFloor"] = args.low_power_nfloor_pre
        steady_state_overrides[f"{mpke_path}.nFloorDuringForcing"] = (
            args.low_power_nfloor_forcing
        )
        steady_state_overrides[f"{mpke_path}.nFloorSwitchTime"] = effective_ss_time
        low_power_mixed_forcing_step = (
            not args.disable_low_power_mixed_forcing_step
            and args.low_power_forcing_step > 0
            and args.low_power_forcing_step_hifreq > 0
            and args.low_power_hifreq_split > 0
        )
        if args.low_power_forcing_step > 0 and not low_power_mixed_forcing_step:
            steady_state_overrides["forcingTimeStep"] = args.low_power_forcing_step
        low_power_tweaks_applied = True

    sin_mag = compute_base_sin_mag(
        sin_mag=args.sin_mag,
        sin_mag_auto=args.sin_mag_auto,
        sin_mag_ref=args.sin_mag_ref,
        sin_mag_min=args.sin_mag_min,
        sin_mag_max=args.sin_mag_max,
        power=args.power,
    )
    apply_low_power_allfreq_sin_mag_cap = (
        should_apply_low_power_allfreq_sin_mag_cap(
            power=args.power,
            sin_mag_auto=args.sin_mag_auto,
            sin_mag_logscale=args.sin_mag_logscale,
            disable_low_power_allfreq_sin_mag_cap=(
                args.disable_low_power_allfreq_sin_mag_cap
            ),
            low_power_allfreq_sin_mag_cap=args.low_power_allfreq_sin_mag_cap,
            low_power_allfreq_sin_mag_cap_power=(
                args.low_power_allfreq_sin_mag_cap_power
            ),
        )
    )
    if apply_low_power_allfreq_sin_mag_cap:
        sin_mag = min(sin_mag, float(args.low_power_allfreq_sin_mag_cap))
    apply_low_power_slowfreq_sin_mag_cap = (
        should_apply_low_power_slowfreq_sin_mag_cap(
            power=args.power,
            low_power_threshold=args.low_power_threshold,
            sin_mag_auto=args.sin_mag_auto,
            sin_mag_logscale=args.sin_mag_logscale,
            disable_low_power_slowfreq_sin_mag_cap=(
                args.disable_low_power_slowfreq_sin_mag_cap
            ),
            low_power_slowfreq_sin_mag_cap=args.low_power_slowfreq_sin_mag_cap,
            low_power_slowfreq_sin_mag_cap_freq=(
                args.low_power_slowfreq_sin_mag_cap_freq
            ),
        )
    )

    params_file = os.path.join(base_dir, "run_params.txt")
    with open(params_file, "w") as handle:
        handle.write(f"core_model\t{args.core_model}\n")
        handle.write(f"core_dir\t{os.path.abspath(args.core_dir)}\n")
        handle.write(f"model_name\t{resolved_model_name}\n")
        handle.write(f"power\t{args.power}\n")
        handle.write(f"freq_min\t{args.freq_min}\n")
        handle.write(f"freq_max\t{args.freq_max}\n")
        handle.write(f"num_freq\t{args.num_freq}\n")
        handle.write(f"sin_mag\t{sin_mag}\n")
        handle.write(f"sin_mag_auto\t{args.sin_mag_auto}\n")
        handle.write(f"sin_mag_logscale\t{args.sin_mag_logscale}\n")
        handle.write(f"sin_mag_low\t{args.sin_mag_low}\n")
        handle.write(f"sin_mag_high\t{args.sin_mag_high}\n")
        handle.write(
            "low_power_allfreq_sin_mag_cap\t"
            f"{args.low_power_allfreq_sin_mag_cap}\n"
        )
        handle.write(
            "low_power_allfreq_sin_mag_cap_power\t"
            f"{args.low_power_allfreq_sin_mag_cap_power}\n"
        )
        handle.write(
            "low_power_allfreq_sin_mag_cap_applied\t"
            f"{apply_low_power_allfreq_sin_mag_cap}\n"
        )
        handle.write(
            f"low_power_slowfreq_sin_mag_cap\t"
            f"{args.low_power_slowfreq_sin_mag_cap}\n"
        )
        handle.write(
            "low_power_slowfreq_sin_mag_cap_freq\t"
            f"{args.low_power_slowfreq_sin_mag_cap_freq}\n"
        )
        handle.write(
            "low_power_slowfreq_sin_mag_cap_applied\t"
            f"{apply_low_power_slowfreq_sin_mag_cap}\n"
        )
        handle.write(f"requested_stop_time\t{requested_stop_time}\n")
        handle.write(f"requested_stop_time_mode\t{requested_stop_time_mode}\n")
        handle.write(
            f"requested_min_cycles_after_ss\t{requested_min_cycles_after_ss}\n"
        )
        handle.write(
            f"requested_output_interval_mode\t{requested_output_interval_mode}\n"
        )
        handle.write(f"stop_time\t{effective_stop_time}\n")
        handle.write(f"stop_time_mode\t{effective_stop_time_mode}\n")
        handle.write(
            f"min_cycles_after_ss\t{effective_min_cycles_after_ss}\n"
        )
        handle.write(f"output_interval_mode\t{effective_output_interval_mode}\n")
        handle.write(
            f"output_intervals_per_second\t{args.output_intervals_per_second}\n"
        )
        handle.write(
            f"output_samples_per_period\t{args.output_samples_per_period}\n"
        )
        handle.write(f"output_step_max\t{args.output_step_max}\n")
        handle.write(f"requested_ss_time\t{requested_ss_time}\n")
        handle.write(f"ss_time\t{effective_ss_time}\n")
        handle.write(f"low_power_threshold\t{args.low_power_threshold}\n")
        handle.write(
            "low_power_auto_time_horizon_applied\t"
            f"{low_power_auto_time_horizon_applied}\n"
        )
        handle.write(
            "low_power_auto_output_grid_applied\t"
            f"{low_power_auto_output_grid_applied}\n"
        )
        handle.write(
            f"low_power_auto_ss_time_factor\t{args.low_power_auto_ss_time_factor}\n"
        )
        handle.write(
            "low_power_auto_stop_tail_max\t"
            f"{args.low_power_auto_stop_tail_max}\n"
        )
        handle.write(
            "low_power_auto_min_cycles_after_ss\t"
            f"{args.low_power_auto_min_cycles_after_ss}\n"
        )
        handle.write(f"low_power_forcing_step\t{args.low_power_forcing_step}\n")
        handle.write(
            f"low_power_forcing_step_hifreq\t{args.low_power_forcing_step_hifreq}\n"
        )
        handle.write(f"low_power_hifreq_split\t{args.low_power_hifreq_split}\n")
        handle.write(f"forcing_time_step\t{args.forcing_time_step}\n")
        handle.write(
            f"low_power_mixed_forcing_step\t{low_power_mixed_forcing_step}\n"
        )
        handle.write(f"low_power_nfloor_pre\t{args.low_power_nfloor_pre}\n")
        handle.write(f"low_power_nfloor_forcing\t{args.low_power_nfloor_forcing}\n")
        handle.write(f"low_power_tweaks_applied\t{low_power_tweaks_applied}\n")
        handle.write(f"simflags_extra\t{args.simflags_extra}\n")
        handle.write(f"heat_loss\t{args.heat_loss}\n")
        handle.write(f"using_steady_state_table\t{using_steady_table}\n")
        handle.write(f"steady_state_table\t{steady_state_table}\n")
        handle.write(f"no_reuse\t{args.no_reuse}\n")
        handle.write(f"cleanup_omc_artifacts\t{args.cleanup_omc_artifacts}\n")
        handle.write(f"reduced_csv_for_collect\t{args.reduced_csv_for_collect}\n")

    sin_mag_by_freq = build_sin_mag_profile(
        freq_space,
        base_sin_mag=sin_mag,
        sin_mag_logscale=args.sin_mag_logscale,
        sin_mag_low=args.sin_mag_low,
        sin_mag_high=args.sin_mag_high,
        apply_low_power_slowfreq_cap=apply_low_power_slowfreq_sin_mag_cap,
        low_power_slowfreq_sin_mag_cap=args.low_power_slowfreq_sin_mag_cap,
        low_power_slowfreq_sin_mag_cap_freq=args.low_power_slowfreq_sin_mag_cap_freq,
    )
    if sin_mag_by_freq is not None:
        mapping_path = os.path.join(base_dir, "sin_mag_by_freq.csv")
        with open(mapping_path, "w") as handle:
            handle.write("frequency_rad_s,sin_mag\n")
            for f in freq_space:
                handle.write(f"{f:.12g},{sin_mag_by_freq[float(f)]:.12g}\n")
    stop_time_mapping_path = os.path.join(base_dir, "stop_time_by_freq.csv")
    with open(stop_time_mapping_path, "w") as handle:
        handle.write("frequency_rad_s,stop_time_s\n")
        for f in freq_space:
            handle.write(
                f"{format_frequency_key(f)},{stop_time_by_freq[float(f)]:.12g}\n"
            )
    output_grid_mapping_path = os.path.join(base_dir, "output_grid_by_freq.csv")
    with open(output_grid_mapping_path, "w") as handle:
        handle.write(
            "frequency_rad_s,stop_time_s,number_of_intervals,output_step_s\n"
        )
        for f in freq_space:
            stop_time = stop_time_by_freq[float(f)]
            number_of_intervals = number_of_intervals_by_freq[float(f)]
            output_step = stop_time / number_of_intervals
            handle.write(
                f"{format_frequency_key(f)},{stop_time:.12g},"
                f"{number_of_intervals},{output_step:.12g}\n"
            )

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

    n_jobs = min(args.n_jobs, args.num_freq)

    print("MSRR Frequency Response — Nominal Trim (parallel)")
    print(f"  Core model:       {args.core_model}")
    print(f"  Model:            {resolved_model_name}")
    print(f"  Core dir:         {core_dir}")
    print(f"  Power level:      {args.power}")
    print(f"  Frequency range:  {args.freq_min:.4f} – {args.freq_max:.4f} rad/s")
    print(f"  Frequency points: {args.num_freq}")
    if args.sin_mag_logscale:
        print(
            f"  Perturbation:     log-scaled {args.sin_mag_low} to {args.sin_mag_high} pcm"
        )
    elif apply_low_power_allfreq_sin_mag_cap:
        print(
            "  Perturbation:     "
            f"{sin_mag:g} pcm for all bins (power <= "
            f"{args.low_power_allfreq_sin_mag_cap_power:g} MW auto cap)"
        )
    elif apply_low_power_slowfreq_sin_mag_cap:
        print(
            "  Perturbation:     "
            f"{args.low_power_slowfreq_sin_mag_cap:g} pcm for omega <= "
            f"{args.low_power_slowfreq_sin_mag_cap_freq:g} rad/s, "
            f"{sin_mag:g} pcm above"
        )
    else:
        print(f"  Perturbation:     {sin_mag} pcm")
    if effective_stop_time_mode == "fixed":
        print(f"  Stop time:        {effective_stop_time} s")
    else:
        extended = sum(
            1
            for value in stop_time_by_freq.values()
            if value > effective_stop_time + 1e-9
        )
        print(
            "  Stop time:        "
            f"base {effective_stop_time:g} s, "
            f"min {effective_min_cycles_after_ss:g} cycles "
            f"after forcing ({extended}/{len(freq_space)} points extended, "
            f"max {max(stop_time_by_freq.values()):g} s)"
        )
    if low_power_auto_time_horizon_applied and (
        abs(effective_ss_time - requested_ss_time) > 1e-9
        or abs(effective_stop_time - requested_stop_time) > 1e-9
        or effective_stop_time_mode != requested_stop_time_mode
    ):
        print(
            "  SS settle time:   "
            f"{effective_ss_time:g} s (requested {requested_ss_time:g} s)"
        )
    else:
        print(f"  SS settle time:   {effective_ss_time} s")
    if effective_output_interval_mode == "fixed_rate":
        print(
            "  Output grid:      "
            f"legacy {args.output_intervals_per_second:g} intervals/s"
        )
    else:
        output_steps = [
            stop_time_by_freq[float(f)] / number_of_intervals_by_freq[float(f)]
            for f in freq_space
        ]
        print(
            "  Output grid:      "
            f"frequency-scaled, {args.output_samples_per_period:g} samples/period "
            f"target, max step {args.output_step_max:g}s, "
            f"step range {min(output_steps):g}-{max(output_steps):g}s"
        )
    if low_power_auto_time_horizon_applied:
        print(
            "  Low-power horizon:"
            f" auto protocol active (factor={args.low_power_auto_ss_time_factor:g}, "
            f"tail_max={args.low_power_auto_stop_tail_max:g}s)"
        )
    else:
        print("  Low-power horizon: requested/manual")
    if low_power_tweaks_applied:
        if low_power_mixed_forcing_step:
            print(
                "  Low-power tweaks: "
                f"nFloor {args.low_power_nfloor_pre:g} -> "
                f"{args.low_power_nfloor_forcing:g} at t={effective_ss_time:g}s; "
                f"forcing step={args.low_power_forcing_step:g}s (omega <= "
                f"{args.low_power_hifreq_split:g}), "
                f"{args.low_power_forcing_step_hifreq:g}s (omega > "
                f"{args.low_power_hifreq_split:g})"
            )
        else:
            print(
                "  Low-power tweaks: "
                f"nFloor {args.low_power_nfloor_pre:g} -> "
                f"{args.low_power_nfloor_forcing:g} at t={effective_ss_time:g}s; "
                f"forcing step={args.low_power_forcing_step:g}s"
            )
    else:
        print("  Low-power tweaks: disabled/not applicable")
    if args.forcing_time_step > 0:
        print(f"  Forcing step:     {args.forcing_time_step:g} s")
    else:
        print("  Forcing step:     model default")
    print(f"  Extra simflags:   {args.simflags_extra or '(none)'}")
    print(f"  Base directory:   {base_dir}")
    print(f"  Reuse results:    {not args.no_reuse}")
    print(f"  Parallel jobs:    {n_jobs}")
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

    results: list[dict] = [None] * len(freq_space)  # type: ignore[assignment]
    completed = 0
    progress_every = max(1, len(freq_space) // 16)

    with ThreadPoolExecutor(max_workers=n_jobs) as executor:
        future_map = {
            executor.submit(
                run_single_freq,
                freq_point=fp,
                base_dir=base_dir,
                model_name=resolved_model_name,
                power=args.power,
                sin_mag=sin_mag_by_freq.get(float(freq_space[idx]), sin_mag)
                if sin_mag_by_freq
                else sin_mag,
                ss_time=effective_ss_time,
                stop_time=stop_time_by_freq[float(fp)],
                number_of_intervals=number_of_intervals_by_freq[float(fp)],
                steady_state_overrides=steady_state_overrides,
                forcing_time_step=args.forcing_time_step,
                low_power_mixed_forcing_step=low_power_mixed_forcing_step,
                low_power_forcing_step=args.low_power_forcing_step,
                low_power_forcing_step_hifreq=args.low_power_forcing_step_hifreq,
                low_power_hifreq_split=args.low_power_hifreq_split,
                smd_library=smd_library,
                msrr_model=msrr_model,
                smd_library_src=smd_library_src,
                msrr_model_src=msrr_model_src,
                allow_reuse=not args.no_reuse,
                cleanup_omc_artifacts=args.cleanup_omc_artifacts,
                reduced_csv_for_collect=args.reduced_csv_for_collect,
                simflags_extra=args.simflags_extra,
            ): idx
            for idx, fp in enumerate(freq_space)
        }

        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "freq": float(freq_space[idx]),
                    "success": False,
                    "error": str(exc),
                    "warning": None,
                }

            completed += 1
            if completed % progress_every == 0 or completed == len(freq_space):
                print(
                    f"  Progress: {completed}/{len(freq_space)} "
                    f"frequency runs completed"
                )

    succeeded = sum(1 for item in results if item["success"])
    failed = sum(1 for item in results if not item["success"])

    print("=" * 70)
    print(f"Completed: {succeeded} succeeded, {failed} failed out of {len(results)} total.")

    if failed > 0:
        print("\nFailed runs:")
        for item in results:
            if not item["success"]:
                print(f"  freq = {item['freq']:8.5f} rad/s — {item['error']}")

    print(f"\nResults are in: {base_dir}")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
