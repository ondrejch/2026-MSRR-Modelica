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

With ``--package segmented`` (default: ``legacy``, which keeps the historical
behavior byte-identical) the sweep instead drives the standalone
SegmentedMSR full-loop trim rigs

    1r: SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS
    9r: SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS

loading ONLY ``core/SegmentedMSR.mo``. Segmented mode requires
``--power 1.0`` (the rigs are trimmed full-power vehicles and no segmented
setpoint tables exist yet), ignores steady-state tables, routes low-power
nFloor overrides to the rig's PKE_T instance (``pke.nFloor*``), and skips the
legacy ``forcingTimeStep`` overrides (parameter absent from the rigs). Because
``omc`` 1.27 ``simulate()`` scripting is broken system-wide, segmented runs use
``buildModel(...)`` plus the generated executable directly.

No source-file text patching is performed.

Usage:
    python runFreqNominalParallel.py [--power POWER] [--freq_min FREQ_MIN]
                                    [--freq_max FREQ_MAX] [--num_freq NUM_FREQ]
                                    [--sin_mag SIN_MAG] [--stop_time STOP_TIME]
                                    [--ss_time SS_TIME] [--n_jobs N_JOBS]
                                    [--base_dir BASE_DIR]
                                    [--model_name MODEL_NAME]
                                    [--package {legacy,segmented}]
"""

import argparse
import os
import shlex
import sys
import numpy as np
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import copyfile

try:
    from ._common import (
        DEFAULT_PACKAGE,
        HEAT_LOSS_COLUMN,
        MODEL_NAME_BY_CORE,
        OUTPUT_INTERVAL_MODE_CHOICES,
        PACKAGE_CHOICES,
        SEGMENTED_PACKAGE,
        STOP_TIME_MODE_CHOICES,
        build_simflags,
        cleanup_omc_artifacts_in_dir,
        compute_effective_stop_time,
        compute_number_of_intervals,
        compute_output_step,
        csv_reaches_stop_time,
        default_steady_state_table,
        format_frequency_key,
        reduce_csv_to_collect_columns,
        format_override_value,
        load_steady_state_overrides,
    )
    from .paths import default_freq_case_dir
    from . import sweep_manifest
except ImportError:
    from _common import (
        DEFAULT_PACKAGE,
        HEAT_LOSS_COLUMN,
        MODEL_NAME_BY_CORE,
        OUTPUT_INTERVAL_MODE_CHOICES,
        PACKAGE_CHOICES,
        SEGMENTED_PACKAGE,
        STOP_TIME_MODE_CHOICES,
        build_simflags,
        cleanup_omc_artifacts_in_dir,
        compute_effective_stop_time,
        compute_number_of_intervals,
        compute_output_step,
        csv_reaches_stop_time,
        default_steady_state_table,
        format_frequency_key,
        reduce_csv_to_collect_columns,
        format_override_value,
        load_steady_state_overrides,
    )
    from paths import default_freq_case_dir
    import sweep_manifest


MPKE_PATH_BY_CORE = {
    "1r": "core1R.mpke",
    "9r": "msre9r.mpke",
}


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path (its
    legacy-compat contract); the fallback keeps script-style execution from
    freq/ working by putting the repo root on sys.path.
    """
    try:
        from helpers import segmented_runs as seg
    except ImportError:  # script-style execution from freq/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import segmented_runs as seg
    return seg


# PKE_T instance name on BOTH SegmentedMSR trim rigs
# (core/SegmentedMSR.mo, R1MSRRuhxTrimThermalSS / R9MSRRuhxTrimThermalSS):
# low-power nFloor overrides route to 'pke.nFloor*' instead of the legacy
# '<mpke>.nFloor' paths (plan §8.2: freq overrides port over unchanged).
SEGMENTED_PKE_INSTANCE_PREFIX = "pke"

#: buildModel() tolerance baked at BUILD time for segmented runs. omc 1.27
#: environment constraint: simulate() scripting is broken system-wide and
#: -rtol/-atol are NOT runtime flags; 1e-6 matches the legacy
#: simulate(tolerance=1E-6) sweep numerics.
SEGMENTED_BUILD_TOLERANCE = 1e-6

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

LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT = 1.0
LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT = 1e-3
LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT = 1.0
LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT = 1e-2
LOW_POWER_AUTO_SS_TIME_FACTOR_DEFAULT = 400.0
LOW_POWER_AUTO_STOP_TAIL_MAX_DEFAULT = 5.0e4
LOW_POWER_AUTO_MIN_CYCLES_AFTER_SS_DEFAULT = 12.0

# ---------------------------------------------------------------------------
# Run-provenance wiring (TASK-20260826-02 P3): per-case fingerprints and
# validated outputs. Existing results are reused only when a matching
# manifest sidecar exists beside them and the stored CSV still passes
# validation; stale or foreign results are quarantined instead of trusted.
# ---------------------------------------------------------------------------

#: Result columns the output validator insists on for frequency cases.
FREQ_RESULT_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)

#: Legacy ``csv_reaches_stop_time`` accepted a final sample at >= 99.5 % of
#: the requested stop; the validator reproduces that margin as a slack in
#: seconds so finished trajectories are not rejected over last-step rounding.
FREQ_VALIDATION_STOP_SLACK_RATIO = 0.005


def _run_results():
    """Lazily import the shared run-result provenance library.

    helpers/run_results.py owns manifests, fingerprints, output validation,
    and quarantine; importing it keeps the runner usable both as a package
    module and in script-style execution (same contract as
    :func:`_segmented_helpers`).
    """
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from freq/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import run_results as rr
    return rr


def _provenance_workflow_version() -> str:
    """Workflow implementation version recorded in every manifest."""
    return f"freq-runFreqNominalParallel/run_results-{_run_results().__version__}"


def _workflow_python_sources(package: str) -> list[str]:
    """Workflow Python modules whose bytes enter the run fingerprint.

    Complete by construction for this call site: the active runner module,
    ``freq/_common.py`` (constructs override plumbing and post-processes the
    result CSV before acceptance via ``reduce_csv_to_collect_columns``),
    ``freq/paths.py`` (shared result-path module), ``helpers/run_results.py``
    (the provenance library itself), and -- only when the segmented code path
    is selected -- ``helpers/segmented_runs.py``, which builds the segmented
    ``.mos``.  A byte change in any of these yields a new fingerprint even
    when the git commit/dirty state is unchanged, so dirty workflow Python
    can no longer share a result slot with committed code.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    helpers_dir = Path(rr.__file__).resolve().parent
    files = [
        here,
        here.parent / "_common.py",
        here.parent / "paths.py",
        here.parent / "sweep_manifest.py",
        Path(rr.__file__).resolve(),
    ]
    if str(package) == SEGMENTED_PACKAGE:
        files.append(helpers_dir / "segmented_runs.py")
    return [str(path) for path in files]


def _result_package_name(model_name: str) -> str:
    """Modelica package name for a fully qualified model."""
    return model_name.rsplit(".", 1)[0] if "." in model_name else model_name


def _validation_stop_slack(stop_time: float) -> float:
    """Stop-time slack (seconds) allowed when validating finished output."""
    return FREQ_VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _format_mismatch_summary(reason: str) -> list[str]:
    """Reason text split into console-safe indented lines."""
    lines = [line.strip() for line in reason.splitlines() if line.strip()]
    if len(lines) > 1:
        lines[1:] = [f"  {line}" for line in lines[1:]]
    elif not lines:
        lines = ["no detail reported"]
    return lines


def default_cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_core_dir = str(repo_root / "core")
    parser = argparse.ArgumentParser(
        description="Run MSRR frequency response simulations using MSRRuhxNominalTrim (parallel)."
    )
    parser.add_argument("--core_model", type=str, choices=("1r", "9r"), default="1r",
                        help="Core segmentation to simulate (default: 1r)")
    parser.add_argument("--package", type=str, choices=PACKAGE_CHOICES,
                        default=DEFAULT_PACKAGE,
                        help=(
                            "Model family to drive (default: legacy). "
                            "'legacy' loads SMD_MSR_Modelica.mo + MSRR.mo "
                            "nominal-trim models with unchanged behavior; "
                            "'segmented' drives the standalone SegmentedMSR "
                            "full-loop trim rigs (loads ONLY SegmentedMSR.mo), "
                            "requires --power 1.0 (no segmented setpoint tables "
                            "exist yet), ignores steady-state tables, routes "
                            "low-power nFloor overrides to pke.nFloor*, and "
                            "skips forcingTimeStep/heat-loss overrides that the "
                            "rigs do not expose. Segmented result CSVs report "
                            "temperatures in kelvin."
                        ))
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
            "(default: 00runs/freq/<core_model>/power_<tag>; segmented runs "
            "nest under an additional segmented/ component)"
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
        help=(
            "Do not reuse existing *_res.csv results; always rerun each "
            "frequency. Without this flag, an existing result is kept only "
            "when its provenance sidecar fingerprint matches the current "
            "request (model sources, overrides, numerics, forcing, setpoint "
            "table) AND the stored CSV passes output validation; otherwise "
            "the prior files are quarantined under 00runs/tmp/quarantine/ "
            "and the case reruns."
        ),
    )
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help=(
            "Maximum wall-clock seconds per omc simulation before that "
            "frequency is treated as failed (0 disables the timeout, default: 0)."
        ),
    )
    parser.add_argument(
        "--claim_timeout_s",
        type=float,
        default=None,
        help=(
            "Maximum seconds to wait for a busy per-case result-slot claim "
            "when another launch holds the same frequency slot; expiry "
            "raises ResultSlotClaimTimeout naming the holder. Default: "
            "wait indefinitely (previous behavior)."
        ),
    )
    args = parser.parse_args(argv)
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    if args.base_dir is None:
        args.base_dir = str(
            default_freq_case_dir(
                repo_root,
                core_model=args.core_model,
                power=args.power,
                package=str(getattr(args, "package", DEFAULT_PACKAGE)),
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
    if float(getattr(args, "omc_timeout_seconds", 0.0)) < 0:
        raise ValueError("--omc_timeout_seconds must be >= 0.")
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    if package == SEGMENTED_PACKAGE:
        # Ambiguity B (TASK-20260823-02): hard error away from the trimmed
        # point until segmented setpoint tables exist; the rigs carry their own
        # SteadyState init at full power, so other powers are meaningless.
        if abs(float(args.power) - 1.0) > 1e-9:
            raise ValueError(
                "--power must be 1.0 with --package segmented: the SegmentedMSR "
                "rigs are trimmed full-power vehicles and no segmented setpoint "
                "tables exist yet."
            )
        if float(getattr(args, "forcing_time_step", 0.0)) > 0:
            raise ValueError(
                "--forcing_time_step > 0 is unsupported with --package "
                "segmented: the forcingTimeStep parameter does not exist on "
                "SegmentedMSR rigs."
            )
        if int(getattr(args, "heat_loss", 0)) != 0:
            raise ValueError(
                "--heat_loss 1 is unsupported with --package segmented: no "
                "heatLossEnabled parameter exists on SegmentedMSR rigs."
            )
        if str(getattr(args, "steady_state_table", "") or "").strip():
            raise ValueError(
                "--steady_state_table is unsupported with --package segmented: "
                "no segmented setpoint tables exist yet."
            )
        model_name_override = getattr(args, "model_name", None)
        if model_name_override:
            raise ValueError(
                "--model_name cannot be combined with --package segmented; "
                "select the vehicle with --core_model {1r,9r}."
            )
        if getattr(args, "reduced_csv_for_collect", False):
            raise ValueError(
                "--reduced_csv_for_collect is unsupported with --package "
                "segmented: the freq collectors do not resolve segmented "
                "power columns yet (the rigs expose nOut / pb.reactorPower, "
                "not the legacy pkenpopulation* names)."
            )


def _captured_text(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _write_captured_process_output(
    stdout_path: str,
    stderr_path: str,
    stdout: object,
    stderr: object,
) -> None:
    with open(stdout_path, "w") as handle:
        handle.write(_captured_text(stdout))
    with open(stderr_path, "w") as handle:
        handle.write(_captured_text(stderr))


def build_freq_case_manifest(
    provenance_context: dict,
    *,
    freq_point: float,
    sin_mag: float,
    ss_time: float,
    stop_time: float,
    number_of_intervals: int,
    local_overrides: dict[str, float | bool] | None,
    detailed_state_init_weight_applied: bool,
) -> dict:
    """Build the canonical manifest for ONE frequency case.

    ``provenance_context`` carries everything static for the sweep (model
    identity, hashed sources, tool/revision state, numerics, setpoint table
    provenance, backend); case-dynamic fields arrive explicitly here. The
    normalized overrides mirror every knob that reaches the model -- including
    the angular forcing coordinate ``freq_point`` (rad/s), recorded as
    ``overrides["perturbationOmega"]`` exactly as it is passed through the
    ``-override=`` simulation flag -- so any change to them produces a new
    fingerprint.

    The top-level ``frequency_hz`` manifest field deliberately stays null in
    this workflow: the sweep coordinate is angular (rad/s), not Hz, and the
    value had previously been misfiled there.  Readers must take the forcing
    coordinate from ``overrides["perturbationOmega"]``.  Sidecars written by
    older revisions carry the same number under ``frequency_hz`` instead;
    they mismatch their request fingerprint once and are quarantined
    (fail-closed) before being resimulated.
    """
    rr = _run_results()
    overrides: dict[str, object] = {
        "core_model": str(provenance_context["core_model"]),
        "package": str(provenance_context["package"]),
        "powerLevel": float(provenance_context["power"]),
        # Angular forcing coordinate fed to the model knob perturbationOmega
        # (see the -override= list). The manifest's top-level frequency_hz
        # field stays null; this override is the authoritative record.
        "perturbationOmega": float(freq_point),
        "perturbationStartTime": float(ss_time),
        "primaryPump.freeConvFF": 1,
        "secondaryPump.freeConvFF": 1,
        "using_steady_state_table": bool(
            provenance_context["using_steady_state_table"]
        ),
        "heat_loss": int(provenance_context["heat_loss"]),
        "simflags_extra": str(provenance_context.get("simflags_extra", "")),
    }
    if provenance_context["reduced_csv_for_collect"]:
        overrides["reduced_csv_variable_filter"] = True
    if detailed_state_init_weight_applied:
        overrides["heatExchanger.detailedStateInitWeight"] = 1
    if local_overrides:
        overrides.update(local_overrides)

    manifest = rr.build_run_manifest(
        package_name=_result_package_name(str(provenance_context["model_name"])),
        model_name=str(provenance_context["model_name"]),
        source_files=provenance_context["source_files"],
        workflow_python_files=_workflow_python_sources(
            provenance_context["package"]
        ),
        overrides=overrides,
        solver=str(provenance_context["solver"]),
        tolerance=float(provenance_context["tolerance"]),
        start_time=0.0,
        stop_time=float(stop_time),
        number_of_intervals=int(number_of_intervals),
        output_grid=str(provenance_context["output_grid_label"]),
        perturbation_amplitude=float(sin_mag),
        setpoint_table_path=provenance_context.get("setpoint_table_path"),
        backend="local",
        omc_version=str(provenance_context["omc_version"]),
        git_info=provenance_context["git_info"],
        workflow_version=_provenance_workflow_version(),
    )
    request_reference = provenance_context.get("sweep_request")
    if request_reference is not None:
        manifest["sweep_request"] = dict(request_reference)
    return manifest


def _publish_and_accept_result(
    result: dict,
    *,
    csv_path: str,
    raw_csv_path: str | None = None,
    stop_time: float,
    work_path: str,
    keep_files: set[str],
    cleanup_omc_artifacts: bool,
    reduced_csv_for_collect: bool,
    provenance_context: dict | None,
    expected_manifest: dict | None,
    quarantine_root: str | None = None,
) -> bool:
    """Accept a freshly written result CSV under the active output policy.

    With a provenance context, the raw output (written to ``raw_csv_path``,
    the ``{csv}.tmp`` companion the simulation was directed at) must pass
    full output validation -- freshness against the recorded launch timestamp
    included -- before it is published atomically onto ``csv_path`` and both
    sidecars are written next to it; a rejected or interrupted run therefore
    never leaves a reusable final result.  Without a provenance context --
    direct programmatic calls only -- the historical stop-time tail check is
    kept unchanged and the simulation output is consumed where it lies.
    """
    raw_csv_path = raw_csv_path or csv_path
    if reduced_csv_for_collect:
        reduce_csv_to_collect_columns(raw_csv_path)

    if provenance_context is None or expected_manifest is None:
        if csv_reaches_stop_time(csv_path=raw_csv_path, stop_time=stop_time):
            if cleanup_omc_artifacts:
                cleanup_omc_artifacts_in_dir(work_path=work_path, keep_files=keep_files)
            result["success"] = True
            return True
        result["error"] = (
            "simulation output did not reach requested stop_time; "
            f"see {work_path}/omc_stdout.log"
        )
        return False

    rr = _run_results()
    try:
        rr.publish_validated_result(
            raw_csv_path,
            csv_path,
            manifest=expected_manifest,
            required_columns=FREQ_RESULT_REQUIRED_COLUMNS,
            time_column="time",
            requested_stop_time=float(stop_time),
            stop_time_slack_s=_validation_stop_slack(stop_time),
            quarantine_root=quarantine_root,
        )
    except rr.ResultRejectedError as exc:
        failed_checks = ", ".join(exc.report.failed_checks)
        lead = (
            "simulation output did not reach requested stop_time"
            if "final_time_reaches_requested_stop" in exc.report.failed_checks
            else "simulation output rejected by validation"
        )
        result["error"] = (
            f"{lead}; failed checks: {failed_checks}; see {work_path}"
        )
        return False
    except OSError as exc:
        result["error"] = (
            f"could not publish the validated result or write provenance "
            f"sidecars beside {csv_path}: {exc}"
        )
        return False
    if cleanup_omc_artifacts:
        cleanup_omc_artifacts_in_dir(work_path=work_path, keep_files=keep_files)
    result["success"] = True
    return True


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
                    simflags_extra: str = "",
                    omc_timeout_seconds: float | None = None,
                    package: str = DEFAULT_PACKAGE,
                    segmented_library: str = "",
                    segmented_library_src: str = "",
    *,
    provenance_context: dict | None = None,
    quarantine_root: str | None = None,
    claim_timeout_s: float | None = None) -> dict:
    result = {
        "freq": freq_point,
        "success": False,
        "error": None,
        "warning": None,
        "forcing_step": None,
    }

    claim = None
    try:
        work_path = os.path.join(base_dir, f"freq{freq_point:08.5f}")
        os.makedirs(work_path, exist_ok=True)

        file_prefix = f"MSRR_freq{freq_point:08.5f}"
        csv_path = os.path.join(work_path, f"{file_prefix}_res.csv")

        # Per-case override resolution must precede the reuse decision: the
        # effective override set (including case-specific forcing cadence)
        # is part of the request fingerprint.
        is_segmented = package == SEGMENTED_PACKAGE
        local_overrides: dict[str, float | bool] = dict(steady_state_overrides or {})
        expected_manifest: dict | None = None
        if not is_segmented and forcing_time_step > 0:
            local_overrides["forcingTimeStep"] = forcing_time_step
            result["forcing_step"] = forcing_time_step
        if not is_segmented and low_power_mixed_forcing_step:
            forcing_step = (
                low_power_forcing_step_hifreq
                if freq_point > low_power_hifreq_split
                else low_power_forcing_step
            )
            if forcing_step > 0:
                local_overrides["forcingTimeStep"] = forcing_step
                result["forcing_step"] = forcing_step

        if provenance_context is not None:
            rr = _run_results()
            # Same-slot claim (P5): a second concurrent launch of this slot
            # blocks here instead of quarantining this run's in-flight
            # *.tmp output; after the first writer publishes and releases,
            # the reuse check below finds the finished pair.  An explicit
            # --claim_timeout_s bounds the wait (M2 / REV008-10).
            claim = rr.acquire_result_claim(csv_path, timeout_s=claim_timeout_s)
            expected_manifest = build_freq_case_manifest(
                provenance_context,
                freq_point=freq_point,
                sin_mag=sin_mag,
                ss_time=ss_time,
                stop_time=stop_time,
                number_of_intervals=number_of_intervals,
                local_overrides=local_overrides,
                detailed_state_init_weight_applied=(
                    any(
                        key in local_overrides
                        for key in HX_DETAILED_STATE_OVERRIDE_KEYS
                    )
                ),
            )
            prepared = rr.prepare_result_path(
                csv_path,
                reuse_ok=bool(allow_reuse),
                expected_manifest=expected_manifest,
                required_columns=FREQ_RESULT_REQUIRED_COLUMNS,
                time_column="time",
                requested_stop_time=float(stop_time),
                stop_time_slack_s=_validation_stop_slack(stop_time),
                quarantine_root=quarantine_root,
            )
            result["reuse_action"] = prepared.action
            result["fingerprint"] = prepared.fingerprint_expected
            if prepared.can_reuse:
                if reduced_csv_for_collect:
                    reduce_csv_to_collect_columns(csv_path)
                result["success"] = True
                result["warning"] = "existing result reused"
                print(
                    f"freq {freq_point:.8g}: reused existing result "
                    f"(fingerprint {str(prepared.fingerprint_recorded)[:12]}... "
                    "matches the request)"
                )
                return result
            detail_lines = _format_mismatch_summary(prepared.reason)
            print(f"freq {freq_point:.8g}: rerunning — {detail_lines[0]}")
            for extra_line in detail_lines[1:]:
                print(extra_line)
            result["warning"] = "prior output rejected; rerun scheduled"
            # Production route: direct the simulation at the temporary
            # companion so the final path stays empty for the whole in-flight
            # window (publish happens through publish_validated_result).
            raw_csv_path = str(rr.make_tmp_path(csv_path))
        else:
            # Direct programmatic calls without a provenance context keep the
            # historical behavior (production sweeps always wire one in):
            # omc writes the final name directly; only a stale *.tmp left by
            # an earlier attempt is removed first.
            leftover_tmp_path = f"{csv_path}.tmp"
            raw_csv_path = csv_path
            if allow_reuse:
                if (
                    os.path.exists(csv_path)
                    and not os.path.exists(leftover_tmp_path)
                    and csv_reaches_stop_time(csv_path=csv_path, stop_time=stop_time)
                ):
                    if reduced_csv_for_collect:
                        reduce_csv_to_collect_columns(csv_path)
                    result["success"] = True
                    result["warning"] = "existing result reused"
                    return result
            if os.path.exists(leftover_tmp_path):
                os.remove(leftover_tmp_path)

        # SegmentedMSR standalone package: copy ONLY SegmentedMSR.mo into
        # the work dir (single loadFile; no SMD load, no double-load).
        if is_segmented:
            copyfile(
                segmented_library_src,
                os.path.join(work_path, segmented_library),
            )
        else:
            copyfile(smd_library_src, os.path.join(work_path, smd_library))
            copyfile(msrr_model_src, os.path.join(work_path, msrr_model))

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
        if provenance_context is not None and not is_segmented:
            # Production route: the omc result lands on the temporary
            # companion (never the final name) for validated atomic publish.
            simflags = f"{simflags} -r={os.path.basename(raw_csv_path)}"

        if is_segmented:
            # omc 1.27 environment constraint: simulate(...) scripting is
            # broken system-wide. Bake tolerance at BUILD time (-rtol/-atol
            # are NOT runtime flags) and drive the generated executable
            # directly afterwards; exactly ONE loadFile line (standalone
            # package, no double-load).
            seg = _segmented_helpers()
            run_script = (
                "// SMD-MSRR-dev Frequency Response (SegmentedMSR)\n"
                + "\n".join(seg.library_load_lines())
                + "\n"
                + f"buildModel({model_name}, tolerance = {SEGMENTED_BUILD_TOLERANCE:g});\n"
                + "getErrorString();\n"
            )
        else:
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

        try:
            omc_result = subprocess.run(
                ["omc", "--showErrorMessages", "runModelica.mos"],
                cwd=work_path,
                capture_output=True,
                text=True,
                timeout=(
                    omc_timeout_seconds
                    if omc_timeout_seconds and omc_timeout_seconds > 0
                    else None
                ),
            )
        except subprocess.TimeoutExpired as exc:
            stdout_log = os.path.join(work_path, "omc_stdout.log")
            stderr_log = os.path.join(work_path, "omc_stderr.log")
            _write_captured_process_output(
                stdout_log, stderr_log, exc.stdout, exc.stderr
            )
            result["error"] = (
                f"omc timed out after {omc_timeout_seconds:g} s; "
                f"see {stderr_log}"
            )
            return result

        stdout_log = os.path.join(work_path, "omc_stdout.log")
        stderr_log = os.path.join(work_path, "omc_stderr.log")
        _write_captured_process_output(
            stdout_log, stderr_log, omc_result.stdout, omc_result.stderr
        )

        if omc_result.returncode != 0:
            result["error"] = (
                f"omc returned exit code {omc_result.returncode}; "
                f"see {work_path}/omc_stderr.log"
            )
        elif is_segmented:
            exe_path = os.path.join(work_path, model_name)
            if not os.path.exists(exe_path):
                result["error"] = (
                    f"buildModel produced no executable {model_name}; "
                    f"see {work_path}/omc_stdout.log"
                )
            else:
                exe_cmd = [
                    os.path.join(".", model_name),
                    f"-stopTime={stop_time:.10g}",
                    # omc 1.27 runtime rejects -numberOfIntervals ("invalid
                    # command line option"); translate the legacy interval
                    # count into its equivalent equidistant output grid,
                    # stepSize = (stopTime - startTime)/numberOfIntervals
                    # (startTime is 0 for every sweep run).
                    f"-stepSize={stop_time / number_of_intervals:.17g}",
                    "-outputFormat=csv",
                    # Provenanced runs publish through the temporary
                    # companion; direct programmatic calls keep the
                    # historical final-name result path.
                    f"-r={os.path.basename(raw_csv_path if provenance_context is not None else csv_path)}",
                    *shlex.split(simflags),
                ]
                exe_stdout_log = os.path.join(work_path, "exe_stdout.log")
                exe_stderr_log = os.path.join(work_path, "exe_stderr.log")
                try:
                    exe_result = subprocess.run(
                        exe_cmd,
                        cwd=work_path,
                        capture_output=True,
                        text=True,
                        timeout=(
                            omc_timeout_seconds
                            if omc_timeout_seconds and omc_timeout_seconds > 0
                            else None
                        ),
                    )
                except subprocess.TimeoutExpired as exc:
                    _write_captured_process_output(
                        exe_stdout_log, exe_stderr_log, exc.stdout, exc.stderr
                    )
                    result["error"] = (
                        f"simulation executable timed out after "
                        f"{omc_timeout_seconds:g} s; see {exe_stderr_log}"
                    )
                    return result
                _write_captured_process_output(
                    exe_stdout_log, exe_stderr_log,
                    exe_result.stdout, exe_result.stderr,
                )
                if exe_result.returncode != 0:
                    result["error"] = (
                        f"simulation executable returned exit code "
                        f"{exe_result.returncode}; see {exe_stderr_log}"
                    )
                elif os.path.exists(raw_csv_path) or os.path.exists(csv_path):
                    rr_for_keep = _run_results()
                    keep = {
                        os.path.basename(csv_path),
                        "omc_stdout.log",
                        "omc_stderr.log",
                        "exe_stdout.log",
                        "exe_stderr.log",
                    }
                    if provenance_context is not None:
                        keep.add(str(rr_for_keep.manifest_sidecar_path(csv_path).name))
                        keep.add(str(rr_for_keep.validation_report_path(csv_path).name))
                        keep.add(str(rr_for_keep.make_claim_path(csv_path).name))
                    _publish_and_accept_result(
                        result,
                        csv_path=csv_path,
                        raw_csv_path=raw_csv_path,
                        stop_time=stop_time,
                        work_path=work_path,
                        keep_files=keep,
                        cleanup_omc_artifacts=cleanup_omc_artifacts,
                        reduced_csv_for_collect=reduced_csv_for_collect,
                        provenance_context=provenance_context,
                        expected_manifest=expected_manifest,
                        quarantine_root=quarantine_root,
                    )
                else:
                    result["error"] = (
                        "executable exited 0 but no output CSV found; "
                        f"see {work_path}/exe_stdout.log"
                    )
        else:
            if os.path.exists(raw_csv_path) or os.path.exists(csv_path):
                rr_for_keep = _run_results()
                keep = {
                    os.path.basename(csv_path),
                    "omc_stdout.log",
                    "omc_stderr.log",
                }
                if provenance_context is not None:
                    keep.add(str(rr_for_keep.manifest_sidecar_path(csv_path).name))
                    keep.add(str(rr_for_keep.validation_report_path(csv_path).name))
                    keep.add(str(rr_for_keep.make_claim_path(csv_path).name))
                _publish_and_accept_result(
                    result,
                    csv_path=csv_path,
                    raw_csv_path=raw_csv_path,
                    stop_time=stop_time,
                    work_path=work_path,
                    keep_files=keep,
                    cleanup_omc_artifacts=cleanup_omc_artifacts,
                    reduced_csv_for_collect=reduced_csv_for_collect,
                    provenance_context=provenance_context,
                    expected_manifest=expected_manifest,
                    quarantine_root=quarantine_root,
                )
            else:
                result["error"] = (
                    "omc exited 0 but no output CSV found; "
                    f"see {work_path}/omc_stdout.log"
                )

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if claim is not None:
            claim.release()

    return result


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    validate_args(args)
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    segmented = package == SEGMENTED_PACKAGE
    seg = _segmented_helpers() if segmented else None
    if segmented:
        resolved_model_name = seg.model_for(args.core_model)
    else:
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
    steady_state_overrides = None
    using_steady_table = False

    if segmented:
        # Ambiguity B: no segmented setpoint tables exist; suppress legacy
        # table application entirely and record the suppression.
        steady_state_table = "suppressed (segmented: no setpoint tables)"
    else:
        steady_state_table = (
            os.path.abspath(args.steady_state_table)
            if args.steady_state_table
            else default_steady_state_table(args.core_model)
        )

        if not args.disable_steady_state_table and os.path.exists(steady_state_table):
            steady_state_overrides = load_steady_state_overrides(
                table_path=steady_state_table,
                power=args.power,
                heat_loss=args.heat_loss,
                allowed_override_keys=ALLOWED_OVERRIDE_KEYS,
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
        nfloor_prefix = (
            SEGMENTED_PKE_INSTANCE_PREFIX if segmented
            else MPKE_PATH_BY_CORE[args.core_model]
        )
        steady_state_overrides[f"{nfloor_prefix}.nFloor"] = args.low_power_nfloor_pre
        steady_state_overrides[f"{nfloor_prefix}.nFloorDuringForcing"] = (
            args.low_power_nfloor_forcing
        )
        steady_state_overrides[f"{nfloor_prefix}.nFloorSwitchTime"] = effective_ss_time
        if not segmented:
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

    # NOTE: the sweep request log is written AFTER all per-frequency runs are
    # scheduled/completed (see bottom of this function) so it can never be
    # mistaken for a per-case reuse key. Authoritative provenance lives beside
    # each *_res.csv as *.manifest.json / *.validation.json sidecars.
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
    seg_library = ""
    seg_library_src = ""
    if segmented:
        # Standalone SegmentedMSR package: exactly ONE library file.
        seg_library = seg.SEGMENTED_PACKAGE_FILE
        seg_library_src = os.path.join(core_dir, seg_library)
        if not os.path.exists(seg_library_src):
            raise FileNotFoundError(
                f"Cannot find {seg_library_src}."
            )
        smd_library = ""
        msrr_model = ""
        smd_library_src = ""
        msrr_model_src = ""
    else:
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

    # Production sweeps always wire per-case provenance: reuse decisions are
    # fingerprint-gated, outputs are validated, and sidecars record both.
    # Environment probes degrade to their documented unknown/unavailable
    # states instead of aborting a sweep over a hiccup.
    rr_lib = _run_results()

    def _probe(call, default):
        try:
            return call()
        except Exception:  # noqa: BLE001 - provenance is best-effort metadata
            return default

    if segmented:
        model_source_files = [seg_library_src]
    else:
        model_source_files = [smd_library_src, msrr_model_src]
    provenance_context = {
        "core_model": str(args.core_model),
        "package": package,
        "model_name": resolved_model_name,
        "power": float(args.power),
        "source_files": model_source_files,
        "git_info": _probe(
            rr_lib.collect_git_info,
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        "omc_version": str(_probe(rr_lib.probe_omc_version, "unavailable")),
        "using_steady_state_table": bool(using_steady_table),
        "heat_loss": int(args.heat_loss),
        "setpoint_table_path": (
            os.path.abspath(steady_state_table) if using_steady_table else None
        ),
        "solver": "dassl",
        "tolerance": SEGMENTED_BUILD_TOLERANCE if segmented else 1e-6,
        "output_grid_label": (
            "segmented_executable_fixed_stepSize_from_intervals"
            if segmented
            else "legacy_simulate_numberOfIntervals"
        ),
        "simflags_extra": str(args.simflags_extra or ""),
        "reduced_csv_for_collect": bool(args.reduced_csv_for_collect),
    }

    # Immutable campaign authority.  This is published atomically before any
    # worker is submitted, so an interrupted sweep still records the exact
    # requested case set and every effective per-frequency horizon/amplitude.
    provenance_template = rr_lib.build_run_manifest(
        package_name=_result_package_name(resolved_model_name),
        model_name=resolved_model_name,
        source_files=model_source_files,
        workflow_python_files=_workflow_python_sources(package),
        overrides={
            "core_model": str(args.core_model),
            "package": package,
            "powerLevel": float(args.power),
            "perturbationStartTime": effective_ss_time,
            "heat_loss": int(args.heat_loss),
            "using_steady_state_table": bool(using_steady_table),
            "simflags_extra": str(args.simflags_extra or ""),
        },
        solver=provenance_context["solver"],
        tolerance=provenance_context["tolerance"],
        start_time=0.0,
        output_grid=provenance_context["output_grid_label"],
        setpoint_table_path=provenance_context["setpoint_table_path"],
        backend="local",
        omc_version=provenance_context["omc_version"],
        git_info=provenance_context["git_info"],
        workflow_version=_provenance_workflow_version(),
    )
    sweep_request_data = {
        "coordinate": {"name": "perturbationOmega", "unit": "rad/s"},
        "core_model": str(args.core_model),
        "package": package,
        "package_name": _result_package_name(resolved_model_name),
        "model_name": resolved_model_name,
        "power": float(args.power),
        "perturbation_start_time_s": float(effective_ss_time),
        "cases": [
            {
                "frequency_key": format_frequency_key(float(fp)),
                "frequency_rad_s": float(fp),
                "perturbation_amplitude_pcm": float(
                    sin_mag_by_freq.get(float(fp), sin_mag)
                    if sin_mag_by_freq else sin_mag
                ),
                "stop_time_s": float(stop_time_by_freq[float(fp)]),
                "number_of_intervals": int(
                    number_of_intervals_by_freq[float(fp)]
                ),
                "output_step_s": float(
                    stop_time_by_freq[float(fp)]
                    / number_of_intervals_by_freq[float(fp)]
                ),
            }
            for fp in freq_space
        ],
        "policies": {
            "requested_stop_time_s": float(requested_stop_time),
            "requested_stop_time_mode": requested_stop_time_mode,
            "effective_stop_time_mode": effective_stop_time_mode,
            "requested_min_cycles_after_ss": float(
                requested_min_cycles_after_ss
            ),
            "effective_min_cycles_after_ss": float(
                effective_min_cycles_after_ss
            ),
            "requested_output_interval_mode": requested_output_interval_mode,
            "effective_output_interval_mode": effective_output_interval_mode,
            "output_intervals_per_second": float(
                args.output_intervals_per_second
            ),
            "output_samples_per_period": float(args.output_samples_per_period),
            "output_step_max_s": float(args.output_step_max),
            "low_power_auto_time_horizon_applied": bool(
                low_power_auto_time_horizon_applied
            ),
            "low_power_auto_output_grid_applied": bool(
                low_power_auto_output_grid_applied
            ),
            "low_power_tweaks_applied": bool(low_power_tweaks_applied),
            "low_power_mixed_forcing_step": bool(
                low_power_mixed_forcing_step
            ),
            "forcing_time_step_s": float(args.forcing_time_step),
            "sin_mag_auto": bool(args.sin_mag_auto),
            "sin_mag_logscale": bool(args.sin_mag_logscale),
            "low_power_allfreq_sin_mag_cap_applied": bool(
                apply_low_power_allfreq_sin_mag_cap
            ),
            "low_power_slowfreq_sin_mag_cap_applied": bool(
                apply_low_power_slowfreq_sin_mag_cap
            ),
        },
        "numerics": {
            "solver": provenance_template["solver"],
            "tolerance": provenance_template["tolerance"],
            "output_grid": provenance_template["output_grid"],
        },
        "source_files": provenance_template["source_files"],
        "workflow_python": provenance_template["workflow_python"],
        "setpoint_table_path": provenance_template["setpoint_table_path"],
        "setpoint_table_sha256": provenance_template["setpoint_table_sha256"],
        "git_commit": provenance_template["git_commit"],
        "git_dirty": provenance_template["git_dirty"],
        "python_version": provenance_template["python_version"],
        "omc_version": provenance_template["omc_version"],
        "workflow_version": provenance_template["workflow_version"],
    }
    sweep_request_payload = sweep_manifest.publish_sweep_request_manifest(
        base_dir, sweep_request_data
    )
    provenance_context["sweep_request"] = sweep_manifest.case_reference(
        sweep_request_payload
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
    if segmented and low_power_tweaks_applied:
        print(
            "  Low-power tweaks: nFloor overrides routed to "
            f"{SEGMENTED_PKE_INSTANCE_PREFIX}.nFloor*; forcing-step overrides skipped"
        )
    elif low_power_tweaks_applied:
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
    print(
        "  Reuse results:    "
        + (
            "disabled (--no-reuse)"
            if args.no_reuse
            else "fingerprint-gated (provenance sidecars required)"
        )
    )
    print(f"  Parallel jobs:    {n_jobs}")
    if args.omc_timeout_seconds > 0:
        print(f"  OMC timeout:      {args.omc_timeout_seconds:g} s")
    claim_timeout = getattr(args, "claim_timeout_s", None)
    if claim_timeout is not None:
        print(
            "  Claim timeout:    "
            f"{claim_timeout:g} s on busy per-case result slots"
        )
    print(f"  Reduced CSV:      {args.reduced_csv_for_collect}")
    print(f"  Cleanup OMC:      {args.cleanup_omc_artifacts}")
    if segmented:
        print("  Package:          segmented (standalone SegmentedMSR)")
        print("  SS table:         suppressed (no segmented setpoint tables)")
    elif using_steady_table:
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
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                package=package,
                segmented_library=seg_library,
                segmented_library_src=seg_library_src,
                provenance_context=provenance_context,
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
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

    # Sweep request log — informational only. It is written after every
    # per-frequency case has been scheduled so it can never stand in as a
    # reuse key; authoritative provenance lives beside each *_res.csv in the
    # manifest/validation sidecars.
    params_file = os.path.join(base_dir, "run_params.txt")
    with open(params_file, "w") as handle:
        handle.write(f"core_model\t{args.core_model}\n")
        handle.write(f"package\t{package}\n")
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
