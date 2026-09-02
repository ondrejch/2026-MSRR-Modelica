#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a power-indexed steady-state initialization table.

For each requested power level, this script simulates nominal-trim operation with
zero sinusoidal perturbation and averages late-time temperatures used as
feedback setpoint overrides in downstream analyses.  Every row must pass
late-window convergence checks before it enters the output table: the two
half-window means must agree and the least-squares trend must vanish, each
judged by the combined rule ``d <= max(d_abs, eps * S_x)`` with the
engineering-scale denominator ``S_x = max(|mean|, family floor)`` -- the
relative bar ``eps * S_x`` and the family absolute bar ``d_abs`` are
alternatives, so a deviation is rejected only when it exceeds BOTH (the
peak-to-peak span is reported as a diagnostic only); samples must be finite;
and the late window must hold at least ``--tail_min_samples`` samples.  Rows
that fail are either excluded or written marked ``qualified=0`` with
``--accept_unconverged``.
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static-analysis only
    from helpers.run_results import ResultSlotClaim

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = str(_REPO_ROOT / "00runs" / "tmp" / "setpoints")

try:
    from ._common import mos_escape, variable_filter_for_core
except ImportError:  # script-style execution from core/init/
    from _common import mos_escape, variable_filter_for_core


MODEL_NAME_BY_CORE = {
    "1r": "MSRR.MSRRuhxNominalTrimNoTrips",
    "9r": "MSRR.MSRRuhxNominalTrim9RNoTrips",
}

# Keep setpoint generation behavior explicit and predictable.
# If a special model/stop-time is needed for a particular power, pass it via CLI.
STOP_TIME_OVERRIDES: dict[str, dict[str, float]] = {
    # Low-power 1R points also require long horizons for n/setpoint convergence.
    "1r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    },
    # Low-power 9R points require long horizons to settle near n/setpoint ~= 1.
    "9r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    }
}
NUMBER_OF_INTERVALS_OVERRIDES: dict[str, dict[str, int]] = {
    # Keep long-horizon runs tractable while retaining tail-mean fidelity.
    "1r": {
        "0p00001": 20_000,  # 1e-5 @ 1e8 s
        "0p00010": 50_000,  # 1e-4 @ 1e7 s
        "0p00100": 50_000,  # 1e-3 @ 1e6 s
    },
    "9r": {
        "0p00001": 20_000,  # 1e-5 @ 1e8 s
        "0p00010": 50_000,  # 1e-4 @ 1e7 s
        "0p00100": 50_000,  # 1e-3 @ 1e6 s
    },
}
MODEL_NAME_OVERRIDES: dict[str, dict[str, str]] = {}

# Region-volume weights used to summarize 9R region temperatures into
# core-representative scalar setpoints (comparable to 1R scalar fields).
R9_VOL_F1 = (
    0.003795391373,
    0.012869720861,
    0.007038081469,
    0.008797601837,
    0.021767608195,
    0.011889109660,
    0.014880331986,
    0.059823315476,
    0.040594513827,
)
R9_VOL_F2 = (
    0.003971456514,
    0.008772341956,
    0.007038081469,
    0.017142787894,
    0.014880331986,
    0.011889109660,
    0.028956494924,
    0.034788134316,
    0.068118358784,
)
R9_VOL_G = (
    0.03488047800,
    0.10533760200,
    0.08002416000,
    0.10244745000,
    0.17818736400,
    0.13543456200,
    0.17330364000,
    0.47895127800,
    0.46943522400,
)

# ---------------------------------------------------------------------------
# Convergence qualification of generated rows
# ---------------------------------------------------------------------------

#: Column appended to output tables marking late-window qualification:
#: ``1`` = all convergence checks passed, ``0`` = accepted via
#: ``--accept_unconverged`` (or a historical row that predates the checks).
QUALIFIED_COLUMN = "qualified"

CONVERGENCE_SCHEMA_VERSION = 4

#: Schema history: v3 reports the combined relative-or-absolute acceptance
#: rule (review C1 / REV008-01) -- each scored metric is judged against the
#: effective limit ``max(eps * S_x, absolute bar)`` and records the relative
#: limit, the absolute bar, that effective limit, and the accepting or
#: rejecting decision branch.  v2 verdicts followed the older fail-both
#: rule (both applicable bars had to clear), so v2 and v3 verdicts on the
#: same data can differ and must never be compared directly.

#: Default relative tolerance for the difference between the two equal
#: late-window means (per requested state and plant signal), applied against
#: an engineering-scale denominator -- not the observed peak-to-peak span.
DEFAULT_CONVERGENCE_WINDOW_TOLERANCE = 0.05

#: Default relative tolerance for the least-squares trend projected across
#: the full late window (per requested state and plant signal), against the
#: same engineering-scale denominator.
DEFAULT_CONVERGENCE_SLOPE_TOLERANCE = 0.05

# Physical families assign every converged signal an engineering scale
# ``S_x = max(|mean|, floor)`` and optionally an absolute acceptance bar.
# Each scored metric follows ONE combined rule, ``d <= max(d_abs, eps * S_x)``:
# the relative bar carries ALL of the large-signal strictness (it is the only
# limit that scales with the signal), while the absolute bar is a NEAR-ZERO
# rescue alone -- under ``max`` it can never reject a deviation the relative
# bar already accepted, and it changes the verdict only where
# ``d_abs > eps * S_x``, i.e. at scales so small the floor-scaled relative
# bar would be tighter than the physical unit of the signal.

CONVERGENCE_FAMILY_TEMPERATURE = "temperature"
CONVERGENCE_FAMILY_POPULATION = "population"
CONVERGENCE_FAMILY_POWER = "power"
CONVERGENCE_FAMILY_GENERIC = "generic"

#: Absolute acceptance bar for temperature-family columns, expressed in the
#: setpoint-table temperature unit.  The tables hold degrees Celsius; kelvin
#: shares the numeric width, so the bar transfers unchanged between them.
DEFAULT_TEMPERATURE_ABS_TOLERANCE = 1.0

#: Engineering-scale floor for temperature columns (same unit as above).
DEFAULT_TEMPERATURE_SCALE_FLOOR = 1.0

#: Absolute acceptance bar for power-family columns, in watts.
DEFAULT_POWER_ABS_TOLERANCE = 1.0

#: Engineering-scale floor for power columns, in watts.
DEFAULT_POWER_SCALE_FLOOR = 1.0

#: Engineering-scale floor for normalized neutron-population columns
#: (dimensionless); mirrors the model's numerical population floor n >= 1e-9.
#: Population columns carry no absolute bar -- the family judges drift
#: relative to its own level only.
DEFAULT_POPULATION_SCALE_FLOOR = 1e-9

#: Safety floor for columns no heuristic maps to a physical family; those
#: columns use the relative bars only.
DEFAULT_GENERIC_SCALE_FLOOR = 1e-9


QUALIFICATION_PROFILES = ("diagnostic", "publication")
DEFAULT_QUALIFICATION_PROFILE = "diagnostic"

# Publication-profile defaults are intentionally conservative candidate gates.
# Projects may tighten them through the existing CLI options, but publication
# mode never silently relaxes them.  Final scientific acceptance remains an
# owner decision recorded in the convergence sidecar.
PUBLICATION_WINDOW_TOLERANCE = 0.002
PUBLICATION_SLOPE_TOLERANCE = 0.002
PUBLICATION_RESIDUAL_AMPLITUDE_TOLERANCE = 0.005
PUBLICATION_TEMPERATURE_ABS_TOLERANCE = 0.5
PUBLICATION_POWER_ABS_TOLERANCE = 1000.0
PUBLICATION_MIN_TAIL_DURATION_S = 1000.0


def apply_qualification_profile(args: argparse.Namespace) -> argparse.Namespace:
    """Apply shared defaults and fail-safe clamps for a qualification profile.

    Both the direct and continuation setpoint entry points call this helper so
    ``--qualification_profile publication`` has one definition everywhere.
    Publication mode may tighten caller-provided settings, but never relaxes
    them; diagnostic mode preserves the supplied tolerances.
    """
    # Programmatic callers and older test fixtures may provide a Namespace
    # created before qualification profiles existed.
    if not hasattr(args, "qualification_profile"):
        args.qualification_profile = DEFAULT_QUALIFICATION_PROFILE
    if not hasattr(args, "conv_residual_amplitude_tol"):
        args.conv_residual_amplitude_tol = None
    if not hasattr(args, "tail_min_duration"):
        args.tail_min_duration = 0.0

    if args.qualification_profile == "publication":
        args.conv_window_tol = min(
            args.conv_window_tol, PUBLICATION_WINDOW_TOLERANCE
        )
        args.conv_slope_tol = min(
            args.conv_slope_tol, PUBLICATION_SLOPE_TOLERANCE
        )
        args.conv_temperature_abs_tol = min(
            args.conv_temperature_abs_tol,
            PUBLICATION_TEMPERATURE_ABS_TOLERANCE,
        )
        args.conv_power_abs_tol = min(
            args.conv_power_abs_tol, PUBLICATION_POWER_ABS_TOLERANCE
        )
        if args.conv_residual_amplitude_tol is None:
            args.conv_residual_amplitude_tol = (
                PUBLICATION_RESIDUAL_AMPLITUDE_TOLERANCE
            )
        args.tail_min_duration = max(
            args.tail_min_duration, PUBLICATION_MIN_TAIL_DURATION_S
        )
    return args


def classify_convergence_family(column: str) -> str:
    """Map a result-variable name onto its convergence family.

    The input is a Modelica result-variable name -- exactly what
    :func:`evaluate_tail_convergence` scores: the mapped VALUES of
    :func:`table_to_result_variable` (for example
    ``heatExchanger.T_in_pFluid.T`` via the trailing ``.T``,
    ``heatExchanger.T_PN1`` via ``.t_``, and ``pipeHXtoUHX.tempPi`` via
    ``temp``), not the table-key column labels. Table keys such as
    ``heatExchanger.TpIn_0`` are never passed through this matcher (they
    would classify as generic); they only name the output-table columns.

    Name heuristics over the known Modelica signal conventions:

    - ``*.n_population.n`` -> population (normalized; relative bars only);
    - power channels ``*.powerblock.reactorPower`` (scalar) and trailing
      ``.P`` connectors such as ``*.powerblock.fissionPower.P`` -> power
      (relative bars plus an absolute watt bar);
    - trailing ``.T`` nodes, ``T_*`` exchanger states, or ``temp*``
      pipe/HX temperatures -> temperature (relative bars plus an absolute
      bar in the setpoint-table temperature unit);
    - anything else -> generic relative-only family with a small safety
      floor.

    Derived pair-difference series bypass the family bars entirely: they are
    reported diagnostically, see :func:`evaluate_tail_convergence`.
    """
    lowered = column.strip().lower()
    if "n_population" in lowered:
        return CONVERGENCE_FAMILY_POPULATION
    if lowered.endswith(".p") or lowered.endswith("reactorpower"):
        return CONVERGENCE_FAMILY_POWER
    if lowered.endswith(".t") or ".t_" in lowered or "temp" in lowered:
        return CONVERGENCE_FAMILY_TEMPERATURE
    return CONVERGENCE_FAMILY_GENERIC

#: Relative slack on the requested stop time accepted when validating a
#: finished result CSV (final sampled time >= stop * (1 - ratio)), mirroring
#: the frequency and startup runners.
VALIDATION_STOP_SLACK_RATIO = 0.005

_PLANT_CORE_PREFIXES = ("core1R", "msre9r")

# Plant-level residual candidates. Columns are matched against whichever are
# present in the reduced per-power CSV; a group with no present column is
# skipped with an explicit note (segmented vs lumped names differ).
PLANT_POPULATION_COLUMNS: tuple[str, ...] = tuple(
    f"{prefix}.mpke.n_population.n" for prefix in _PLANT_CORE_PREFIXES
)
PLANT_CORE_TEMPERATURE_COLUMNS: tuple[str, ...] = (
    "core1R.fuelchannel.fuelNode1.T",
    "core1R.fuelchannel.grapNode.T",
    "msre9r.R1.fuelNode1.T",
    "msre9r.R1.grapNode.T",
)
PLANT_POWER_MINUEND_COLUMNS: tuple[str, ...] = tuple(
    f"{prefix}.powerblock.reactorPower" for prefix in _PLANT_CORE_PREFIXES
)
PLANT_POWER_SUBTRAHEND_COLUMNS: tuple[str, ...] = tuple(
    f"{prefix}.powerblock.fissionPower.P" for prefix in _PLANT_CORE_PREFIXES
)

def table_to_result_variable(core_model: str) -> dict[str, str]:
    if core_model == "1r":
        mapping = {
            "fuelTempSetPointNode1": "core1R.fuelchannel.fuelNode1.T",
            "fuelTempSetPointNode2": "core1R.fuelchannel.fuelNode2.T",
            "graphiteTempSetPoint": "core1R.fuelchannel.grapNode.T",
            "heatExchanger.TpIn_0": "heatExchanger.T_in_pFluid.T",
            "heatExchanger.TpOut_0": "heatExchanger.T_out_pFluid.T",
            "heatExchanger.TsIn_0": "heatExchanger.T_in_sFluid.T",
            "heatExchanger.TsOut_0": "heatExchanger.T_out_sFluid.T",
            "heatExchanger.T_PN1_0": "heatExchanger.T_PN1",
            "heatExchanger.T_PN2_0": "heatExchanger.T_PN2",
            "heatExchanger.T_PN3_0": "heatExchanger.T_PN3",
            "heatExchanger.T_PN4_0": "heatExchanger.T_out_pFluid.T",
            "heatExchanger.T_TN1_0": "heatExchanger.T_TN1",
            "heatExchanger.T_TN2_0": "heatExchanger.T_TN2",
            "heatExchanger.T_SN1_0": "heatExchanger.T_SN1",
            "heatExchanger.T_SN2_0": "heatExchanger.T_SN2",
            "heatExchanger.T_SN3_0": "heatExchanger.T_SN3",
            "heatExchanger.T_SN4_0": "heatExchanger.T_out_sFluid.T",
            "pipeHXtoUHX.T_0": "pipeHXtoUHX.tempPi",
            "pipeUHXtoHX.T_0": "pipeUHXtoHX.tempPi",
            "dhrs.T_0": "dhrs.tempOut.T",
            "pipeDHRStoHX.T_0": "pipeDHRStoHX.tempPi",
            "pipeHXtoCore.T_0": "pipeHXtoCore.tempPi",
            "pipeCoreToDHRS.T_0": "pipeCoreToDHRS.tempPi",
            "uhx.Tp_0": "uhx.tempOut.T",
        }
        return mapping

    mapping = {
        "fuelTempSetPointNode1": "msre9r.R1.fuelNode1.T",
        "fuelTempSetPointNode2": "msre9r.R1.fuelNode2.T",
        "graphiteTempSetPoint": "msre9r.R1.grapNode.T",
        "Tmix_0": "msre9r.upperPlenum.T",
        "heatExchanger.TpIn_0": "heatExchanger.T_in_pFluid.T",
        "heatExchanger.TpOut_0": "heatExchanger.T_out_pFluid.T",
        "heatExchanger.TsIn_0": "heatExchanger.T_in_sFluid.T",
        "heatExchanger.TsOut_0": "heatExchanger.T_out_sFluid.T",
        "heatExchanger.T_PN1_0": "heatExchanger.T_PN1",
        "heatExchanger.T_PN2_0": "heatExchanger.T_PN2",
        "heatExchanger.T_PN3_0": "heatExchanger.T_PN3",
        "heatExchanger.T_PN4_0": "heatExchanger.T_out_pFluid.T",
        "heatExchanger.T_TN1_0": "heatExchanger.T_TN1",
        "heatExchanger.T_TN2_0": "heatExchanger.T_TN2",
        "heatExchanger.T_SN1_0": "heatExchanger.T_SN1",
        "heatExchanger.T_SN2_0": "heatExchanger.T_SN2",
        "heatExchanger.T_SN3_0": "heatExchanger.T_SN3",
        "heatExchanger.T_SN4_0": "heatExchanger.T_out_sFluid.T",
        "pipeHXtoUHX.T_0": "pipeHXtoUHX.tempPi",
        "pipeUHXtoHX.T_0": "pipeUHXtoHX.tempPi",
        "dhrs.T_0": "dhrs.tempOut.T",
        "pipeDHRStoHX.T_0": "pipeDHRStoHX.tempPi",
        "pipeHXtoCore.T_0": "pipeHXtoCore.tempPi",
        "pipeCoreToDHRS.T_0": "pipeCoreToDHRS.tempPi",
        "uhx.Tp_0": "uhx.tempOut.T",
    }
    for idx in range(1, 10):
        mapping[f"TF1_0_regions[{idx}]"] = f"msre9r.R{idx}.fuelNode1.T"
    for idx in range(1, 10):
        mapping[f"TF2_0_regions[{idx}]"] = f"msre9r.R{idx}.fuelNode2.T"
    for idx in range(1, 10):
        mapping[f"TG_0_regions[{idx}]"] = f"msre9r.R{idx}.grapNode.T"
    return mapping


def parse_args() -> argparse.Namespace:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    default_core_dir = os.path.join(repo_root, "core")

    parser = argparse.ArgumentParser(
        description="Generate steady-state setpoint table vs power for nominal initialization."
    )
    parser.add_argument(
        "--powers",
        type=str,
        default="1e-5, 1e-4, 1e-3, 1e-2, 0.1, 0.2,0.4,0.6,0.8,1.0,1.2",
        help="Comma-separated power values, e.g. 1e-5,1e-4,1e-3,1e-2,0.1,0.2,0.4,0.6,0.8,1.0,1.2",
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core segmentation to simulate (default: 1r)",
    )
    parser.add_argument(
        "--core_dir",
        type=str,
        default=default_core_dir,
        help="Directory containing core Modelica files (default: ../core)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Path to MSRR model file (default: <core_dir>/MSRR.mo)",
    )
    parser.add_argument(
        "--library",
        type=str,
        default=None,
        help="Path to SMD_MSR_Modelica.mo (default: <core_dir>/SMD_MSR_Modelica.mo)",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default=None,
        help="Modelica model to simulate (default depends on --core_model)",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=30000.0,
        help="Simulation stop time in seconds (default: 30000)",
    )
    parser.add_argument(
        "--tail_fraction",
        type=float,
        default=0.2,
        help="Fraction of final time points to average for steady state (default: 0.2)",
    )
    parser.add_argument(
        "--tail_min_samples",
        type=int,
        default=1000,
        help="Minimum number of samples from simulation tail to average (default: 1000)",
    )
    parser.add_argument(
        "--qualification_profile",
        choices=QUALIFICATION_PROFILES,
        default=DEFAULT_QUALIFICATION_PROFILE,
        help=(
            "Convergence policy: diagnostic preserves the historical "
            "mean/trend checks; publication also requires plant signal "
            "coverage, a minimum physical tail duration, and detrended "
            "residual-amplitude limits."
        ),
    )
    parser.add_argument(
        "--conv_residual_amplitude_tol",
        type=float,
        default=None,
        help=(
            "Relative limit for the detrended 99th-minus-1st percentile "
            "amplitude. Disabled in diagnostic mode by default; publication "
            f"default: {PUBLICATION_RESIDUAL_AMPLITUDE_TOLERANCE}."
        ),
    )
    parser.add_argument(
        "--tail_min_duration",
        type=float,
        default=0.0,
        help=(
            "Minimum physical duration of the scored tail in seconds "
            f"(publication minimum: {PUBLICATION_MIN_TAIL_DURATION_S:g})."
        ),
    )
    parser.add_argument(
        "--work_dir",
        type=str,
        default=DEFAULT_WORK_DIR,
        help=(
            "Working directory for generated simulation cases "
            "(default: <repo>/00runs/tmp/setpoints)"
        ),
    )
    parser.add_argument(
        "--reuse_csv",
        action="store_true",
        help=(
            "Reuse existing per-power CSVs in work_dir only when their "
            "recorded provenance fingerprint matches the current request "
            "and the contents pass validation; anything else is quarantined "
            "and resimulated"
        ),
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
        "--append",
        action="store_true",
        help=(
            "Merge generated rows into an existing output CSV, replacing any "
            "matching (power, heatLossEnabled) keys"
        ),
    )
    parser.add_argument(
        "--accept_unconverged",
        action="store_true",
        help=(
            "Write power rows whose late-window convergence checks failed "
            f"into the output table marked {QUALIFIED_COLUMN}=0. Without "
            "this flag such rows are excluded from the table and reported."
        ),
    )
    parser.add_argument(
        "--conv_window_tol",
        type=float,
        default=DEFAULT_CONVERGENCE_WINDOW_TOLERANCE,
        help=(
            "Relative tolerance on the difference between the two equal "
            "late-window means, divided by the engineering scale "
            "S=max(|mean|, family floor); where the family defines an "
            "absolute bar, the effective combined limit is the LARGER of "
            "the relative bar and that absolute bar, and a deviation is "
            "rejected only when it exceeds both "
            f"(default: {DEFAULT_CONVERGENCE_WINDOW_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_slope_tol",
        type=float,
        default=DEFAULT_CONVERGENCE_SLOPE_TOLERANCE,
        help=(
            "Relative tolerance on the least-squares trend projected across "
            "the full late window (slope times window duration, divided by "
            "the same engineering scale); where the family defines an "
            "absolute bar, the effective combined limit is the larger of "
            "the two bars "
            f"(default: {DEFAULT_CONVERGENCE_SLOPE_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_temperature_abs_tol",
        type=float,
        default=DEFAULT_TEMPERATURE_ABS_TOLERANCE,
        help=(
            "Absolute late-window acceptance bar for temperature columns, "
            "in the setpoint-table unit (degrees Celsius; kelvin shares the "
            f"numeric width) (default: {DEFAULT_TEMPERATURE_ABS_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_temperature_floor",
        type=float,
        default=DEFAULT_TEMPERATURE_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for temperature columns, "
            "S=max(|mean|, floor), in the same unit as "
            "--conv_temperature_abs_tol "
            f"(default: {DEFAULT_TEMPERATURE_SCALE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--conv_power_abs_tol",
        type=float,
        default=DEFAULT_POWER_ABS_TOLERANCE,
        help=(
            "Absolute late-window acceptance bar for power columns, in "
            f"watts (default: {DEFAULT_POWER_ABS_TOLERANCE})"
        ),
    )
    parser.add_argument(
        "--conv_power_floor",
        type=float,
        default=DEFAULT_POWER_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for power columns in watts "
            f"(default: {DEFAULT_POWER_SCALE_FLOOR})"
        ),
    )
    parser.add_argument(
        "--conv_population_floor",
        type=float,
        default=DEFAULT_POPULATION_SCALE_FLOOR,
        help=(
            "Engineering-scale floor for normalized population columns "
            "(dimensionless; relative bars only) "
            f"(default: {DEFAULT_POPULATION_SCALE_FLOOR})"
        ),
    )
    feedback_group = parser.add_mutually_exclusive_group()
    feedback_group.add_argument(
        "--no_feedback",
        action="store_true",
        help="Disable reactivity feedback during steady-state generation",
    )
    parser.add_argument(
        "--init_temp",
        type=float,
        default=570.0,
        help="Initial setpoint temperature for steady-state runs (degC, default: 570)",
    )
    parser.add_argument(
        "--init_from",
        type=str,
        default="",
        help="CSV path to use for initialization overrides (matches by power and heatLossEnabled if present)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output CSV path (default: core/init/setpoints_<core_model>.csv)",
    )
    parser.add_argument(
        "--n_jobs",
        type=int,
        default=max(1, os.cpu_count() or 1),
        help="Number of parallel omc jobs (default: CPU count; use 1 for serial)",
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


def parse_powers(powers_text: str) -> list[float]:
    parts = [item.strip() for item in powers_text.split(",") if item.strip()]
    if not parts:
        raise ValueError("No power values provided.")
    values = sorted({float(item) for item in parts})

    filtered: list[float] = []
    skipped_zero = 0
    for value in values:
        if abs(value) < 1e-12:
            skipped_zero += 1
            continue
        if value < 0:
            raise ValueError(f"Negative power is nonphysical: {value}")
        filtered.append(value)

    if skipped_zero > 0:
        print(f"Skipping {skipped_zero} zero-power entries (nonphysical for setpoint generation).")
    if not filtered:
        raise ValueError("No non-zero power values provided after filtering.")
    return filtered


def sanitize_power_tag(power: float) -> str:
    text = f"{power:.5f}"
    return text.replace("-", "m").replace(".", "p")


# ---------------------------------------------------------------------------
# Shared run-result provenance (helpers/run_results.py)
# ---------------------------------------------------------------------------


def _run_results():
    """Lazily import the shared run-result provenance library."""
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from core/init/
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from helpers import run_results as rr
    return rr


def _probe(call, default):
    """Best-effort environment probes; degrade to ``default`` on any hiccup."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - provenance metadata must not abort runs
        return default


def _validation_stop_slack(stop_time: float) -> float:
    """Stop-time slack (seconds) allowed when validating finished output."""
    return VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _workflow_python_sources() -> list[str]:
    """Workflow Python modules whose bytes enter the run fingerprint.

    Complete by construction for direct base invocations: the active runner
    module (``generateSetpointTable.py``, which also backs the continuation
    script's manifests via ``build_setpoint_case_manifest``),
    ``core/init/_common.py`` (``mos_escape`` / ``variable_filter_for_core``
    shape the request and the ``.mos`` payload), and
    ``helpers/run_results.py`` (the provenance library itself).  Setpoint
    generation has no segmented code path.  A byte change in any of these
    yields a new fingerprint even when the git commit/dirty state is
    unchanged.  Manifests built through the continuation generator append
    ``core/init/generateSetpointTableContinuation.py`` itself via the
    ``extra_workflow_python_files`` argument of
    :func:`build_setpoint_case_manifest`, so a byte change there moves the
    fingerprint too.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    return [
        str(here),
        str(here.parent / "_common.py"),
        str(Path(rr.__file__).resolve()),
    ]


def _feedback_override_keys(model_name: str) -> tuple[str, str]:
    """Core-specific reactivity-feedback override keys for a model name.

    Mirrors the branch in :func:`run_steady_state_case` so the recorded
    manifest always names the same overrides the ``.mos`` actually contains.
    """
    if "9R" in model_name or "9r" in model_name:
        return ("msre9r.aF", "msre9r.aG")
    return ("core1R.a_F", "core1R.a_G")


def build_setpoint_case_manifest(
    *,
    core_model: str,
    power: float,
    model_name: str,
    model_src: str,
    library_src: str,
    stop_time: float,
    number_of_intervals: int | None,
    init_overrides: dict[str, float] | None,
    feedback_on: bool,
    method: str = "dassl",
    variable_filter: str = "",
    extra_workflow_python_files: Sequence[Path | str] | None = None,
) -> dict:
    """Assemble the canonical provenance manifest for one per-power case.

    Every ingredient that reaches the simulation enters the request
    fingerprint: hashed Modelica sources, power level, perturbation setup,
    feedback toggles, initialization overrides, integrator, tolerance, time
    span, output interval count, and reduced-output variable filter.  The
    wall-clock launch stamp is recorded but excluded from the fingerprint.

    ``extra_workflow_python_files`` appends caller-owned workflow modules to
    the canonical runner/helper set before hashing; duplicate paths are
    ignored.  The continuation generator passes its own module here so a
    byte change to it also moves the fingerprint; direct invocations keep
    the base list.
    """
    rr = _run_results()

    if number_of_intervals is None:
        intervals = int(float(stop_time) * 10)
    else:
        intervals = int(number_of_intervals)

    overrides: dict[str, object] = {
        "core_model": str(core_model),
        "powerLevel": float(power),
        "perturbationAmplitudePcm": 0,
        "perturbationOmega": 0.01,
        "perturbationStartTime": float(stop_time) + 1.0,
        "variable_filter": str(variable_filter),
    }
    if not feedback_on:
        for key in _feedback_override_keys(model_name):
            overrides[key] = 0
    if init_overrides:
        for key in sorted(init_overrides):
            overrides[key] = float(init_overrides[key])

    workflow_files = _workflow_python_sources()
    if extra_workflow_python_files:
        for extra_path in extra_workflow_python_files:
            resolved = str(Path(extra_path).resolve())
            if resolved not in workflow_files:
                workflow_files.append(resolved)

    return rr.build_run_manifest(
        package_name="MSRR",
        model_name=str(model_name),
        source_files=[library_src, model_src],
        workflow_python_files=workflow_files,
        overrides=overrides,
        solver=str(method),
        tolerance=1e-6,
        start_time=0.0,
        stop_time=float(stop_time),
        number_of_intervals=intervals,
        output_grid="simulate_numberOfIntervals",
        backend="local",
        omc_version=_probe(rr.probe_omc_version, "unavailable"),
        git_info=_probe(
            rr.collect_git_info,
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        workflow_version=f"setpoints-generateSetpointTable/run_results-{rr.__version__}",
    )


def power_key(power: float) -> str:
    # Canonical CSV power token. write_table, --append merge, and --init_from
    # all use this so keys survive a write/read round-trip.
    return f"{float(power):.10g}"


def load_init_table(
    init_path: str,
    steady_state_columns: list[str],
) -> dict[tuple[str, int | None], dict[str, float]]:
    init_table: dict[tuple[str, int | None], dict[str, float]] = {}
    if not init_path:
        return init_table
    if not os.path.exists(init_path):
        raise FileNotFoundError(f"Init table not found: {init_path}")
    with open(init_path, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Init table has no header: {init_path}")
        has_heat_loss = "heatLossEnabled" in reader.fieldnames
        for raw in reader:
            if not raw or (raw.get("power") or "").strip() == "":
                continue
            power = float(raw["power"])
            key = power_key(power)
            heat_flag = None
            if has_heat_loss:
                heat_flag = int(float(raw.get("heatLossEnabled", "0")))
            overrides: dict[str, float] = {}
            for column in steady_state_columns:
                val = (raw.get(column) or "").strip()
                if val == "":
                    continue
                overrides[column] = float(val)
            init_table[(key, heat_flag)] = overrides
    return init_table


def select_init_overrides(
    init_table: dict[tuple[str, int | None], dict[str, float]],
    power: float,
    heat_loss: bool,
) -> dict[str, float] | None:
    if not init_table:
        return None
    key = power_key(power)
    heat_key = int(bool(heat_loss))
    match = init_table.get((key, heat_key))
    if match is not None:
        return match
    return init_table.get((key, None))


def default_init_overrides(core_model: str, init_temp: float) -> dict[str, float]:
    overrides = {
        "fuelTempSetPointNode1": init_temp,
        "fuelTempSetPointNode2": init_temp,
        "graphiteTempSetPoint": init_temp,
    }
    if core_model == "9r":
        for idx in range(1, 10):
            overrides[f"TF1_0_regions[{idx}]"] = init_temp
            overrides[f"TF2_0_regions[{idx}]"] = init_temp
            overrides[f"TG_0_regions[{idx}]"] = init_temp
        overrides["Tmix_0"] = init_temp
    return overrides


def resolve_init_overrides(
    core_model: str,
    init_table: dict[tuple[str, int | None], dict[str, float]],
    power: float,
    heat_loss: bool,
    init_temp: float,
) -> dict[str, float] | None:
    init_row = select_init_overrides(init_table, power, heat_loss)
    if init_row is not None:
        return init_row
    return default_init_overrides(core_model, init_temp)


def _captured_text(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def write_captured_process_output(
    stdout_path: str,
    stderr_path: str,
    stdout: object,
    stderr: object,
) -> None:
    """Write subprocess capture buffers, including TimeoutExpired payloads."""
    with open(stdout_path, "w") as handle:
        handle.write(_captured_text(stdout))
    with open(stderr_path, "w") as handle:
        handle.write(_captured_text(stderr))


def run_steady_state_case(
    power: float,
    work_dir: str,
    model_name: str,
    model_src: str,
    library_src: str,
    stop_time: float,
    variable_filter: str,
    init_overrides: dict[str, float] | None = None,
    feedback_on: bool = True,
    simflags_extra: str = "",
    method: str = "dassl",
    number_of_intervals: int | None = None,
    omc_timeout_seconds: float | None = None,
    expected_manifest: dict | None = None,
    quarantine_root: str | None = None,
    validation_required_columns: tuple[str, ...] = (),
    validation_min_samples: int | None = None,
    result_claim: "ResultSlotClaim | None" = None,
    claim_timeout_s: float | None = None,
) -> str:
    """Run one per-power steady-state case, optionally provenanced.

    ``result_claim`` accepts an already-held slot claim so the caller
    (:func:`run_power_case`) can span the whole per-power case -- prepare,
    reuse decision, simulation, validation, publication, and sidecar
    writes -- with ONE acquisition.  It is only meaningful together with
    ``expected_manifest`` (the claim guards a provenanced slot); without
    it, the provenanced path acquires the claim itself and releases it in
    ``finally``, so the two entry shapes never nest the same ``flock``.
    ``claim_timeout_s`` bounds the self-acquired wait (``None`` waits
    indefinitely, the historical behavior; expiry raises
    ``ResultSlotClaimTimeout`` naming the holder) -- M2 / REV008-10.
    """
    if result_claim is not None and expected_manifest is None:
        raise ValueError(
            "result_claim requires expected_manifest: the claim guards a "
            "provenanced result slot, so an unfingerprinted request must "
            "not consume one"
        )
    power_tag = sanitize_power_tag(power)
    case_dir = os.path.join(work_dir, f"power_{power_tag}")
    os.makedirs(case_dir, exist_ok=True)

    model_name_file = "MSRR.mo"
    library_name_file = "SMD_MSR_Modelica.mo"

    copyfile(model_src, os.path.join(case_dir, model_name_file))
    copyfile(library_src, os.path.join(case_dir, library_name_file))

    file_prefix = f"MSRR_ss_{power_tag}"
    override = (
        f"powerLevel={power:.10g},"
        f"perturbationAmplitudePcm=0,"
        f"perturbationOmega=0.01,"
        f"perturbationStartTime={stop_time + 1:.10g}"
    )

    if not feedback_on:
        if "9R" in model_name or "9r" in model_name:
            # Disable reactivity feedback for 9R core
            override += ",msre9r.aF=0,msre9r.aG=0"
        else:
            # Disable reactivity feedback for 1R core (default)
            override += ",core1R.a_F=0,core1R.a_G=0"

    if init_overrides:
        for key in sorted(init_overrides):
            override += f",{key}={init_overrides[key]:.16g}"

    variable_filter = mos_escape(variable_filter.strip())
    simflags_extra = simflags_extra.strip()
    if number_of_intervals is None:
        intervals = int(stop_time * 10)
    else:
        intervals = int(number_of_intervals)
    if intervals <= 0:
        raise ValueError(f"number_of_intervals must be > 0, got {intervals}")

    result_csv_path = os.path.join(case_dir, f"{file_prefix}_res.csv")
    # Production route (provenanced): the omc result lands on the temporary
    # companion (never the final name) for validated atomic publication.
    result_tmp_path = f"{result_csv_path}.tmp"
    result_flag = (
        f" -r={os.path.basename(result_tmp_path)}"
        if expected_manifest is not None
        else ""
    )

    mos_text = (
        f'loadFile("{library_name_file}");\n'
        f'loadFile("{model_name_file}");\n'
        f'simulate({model_name},'
        f'startTime=0,'
        f'stopTime={stop_time:.0f},'
        f'numberOfIntervals={intervals},'
        f'tolerance=1E-6,'
        f'method="{method}",'
        f'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}",'
        f'simflags="-override={override} -variableFilter=\\"{variable_filter}\\"'
        f'{result_flag}'
        f'{f" {simflags_extra}" if simflags_extra else ""}");\n'
    )

    mos_path = os.path.join(case_dir, "runModelica.mos")
    with open(mos_path, "w") as handle:
        handle.write(mos_text)

    if expected_manifest is not None:
        if result_claim is not None:
            # Caller-held claim (REV008-02): run_power_case acquired the
            # slot's flock above its first prepare_result_path and holds it
            # across the whole case body, so reuse the held claim as-is --
            # reacquiring the same flock here would deadlock this process
            # against itself.
            return _run_steady_state_case_body(
                power=power,
                case_dir=case_dir,
                mos_path=mos_path,
                result_csv_path=result_csv_path,
                result_tmp_path=result_tmp_path,
                stop_time=stop_time,
                expected_manifest=expected_manifest,
                quarantine_root=quarantine_root,
                validation_required_columns=validation_required_columns,
                validation_min_samples=validation_min_samples,
                omc_timeout_seconds=omc_timeout_seconds,
            )
        rr = _run_results()
        # Same-slot claim (P5): a second concurrent launch of this slot
        # blocks here instead of quarantining this run's in-flight *.tmp.
        claim = rr.acquire_result_claim(
            result_csv_path, timeout_s=claim_timeout_s
        )
        try:
            return _run_steady_state_case_body(
                power=power,
                case_dir=case_dir,
                mos_path=mos_path,
                result_csv_path=result_csv_path,
                result_tmp_path=result_tmp_path,
                stop_time=stop_time,
                expected_manifest=expected_manifest,
                quarantine_root=quarantine_root,
                validation_required_columns=validation_required_columns,
                validation_min_samples=validation_min_samples,
                omc_timeout_seconds=omc_timeout_seconds,
            )
        finally:
            claim.release()

    try:
        result = subprocess.run(
            ["omc", "--showErrorMessages", "runModelica.mos"],
            cwd=case_dir,
            capture_output=True,
            text=True,
            timeout=omc_timeout_seconds if omc_timeout_seconds and omc_timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_log = os.path.join(case_dir, "omc_stdout.log")
        stderr_log = os.path.join(case_dir, "omc_stderr.log")
        write_captured_process_output(stdout_log, stderr_log, exc.stdout, exc.stderr)
        raise RuntimeError(
            f"omc timed out after {exc.timeout:g} s for power={power}. "
            f"See {stderr_log}."
        ) from exc

    stdout_log = os.path.join(case_dir, "omc_stdout.log")
    stderr_log = os.path.join(case_dir, "omc_stderr.log")
    write_captured_process_output(stdout_log, stderr_log, result.stdout, result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"omc failed for power={power} with exit code {result.returncode}. "
            f"See {case_dir}/omc_stderr.log."
        )

    csv_path = result_csv_path
    if not os.path.exists(csv_path):
        raise RuntimeError(
            f"omc succeeded but output CSV missing for power={power}. "
            f"Expected: {csv_path}"
        )

    return csv_path


def _run_steady_state_case_body(
    *,
    power: float,
    case_dir: str,
    mos_path: str,
    result_csv_path: str,
    result_tmp_path: str,
    stop_time: float,
    expected_manifest: dict,
    quarantine_root: str | None,
    validation_required_columns: tuple[str, ...],
    validation_min_samples: int | None,
    omc_timeout_seconds: float | None,
) -> str:
    """Provenanced steady-state case: simulate, validate, publish atomically.

    The caller holds the slot's exclusive claim for the whole span, so the
    quarantine-before-launch and the atomic publication can never interleave
    with a concurrent same-slot launch.  omc writes the temporary companion;
    only output that passes result validation is published onto the final
    path before both sidecars are written beside it, so a failed or
    interrupted run never leaves a reusable final result.
    """
    rr = _run_results()
    rr.prepare_result_path(
        result_csv_path,
        reuse_ok=False,
        expected_manifest=expected_manifest,
        required_columns=("time", *validation_required_columns),
        requested_stop_time=float(stop_time),
        stop_time_slack_s=_validation_stop_slack(stop_time),
        quarantine_root=quarantine_root,
    )

    try:
        result = subprocess.run(
            ["omc", "--showErrorMessages", "runModelica.mos"],
            cwd=case_dir,
            capture_output=True,
            text=True,
            timeout=omc_timeout_seconds if omc_timeout_seconds and omc_timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_log = os.path.join(case_dir, "omc_stdout.log")
        stderr_log = os.path.join(case_dir, "omc_stderr.log")
        write_captured_process_output(stdout_log, stderr_log, exc.stdout, exc.stderr)
        raise RuntimeError(
            f"omc timed out after {exc.timeout:g} s for power={power}. "
            f"See {stderr_log}."
        ) from exc

    stdout_log = os.path.join(case_dir, "omc_stdout.log")
    stderr_log = os.path.join(case_dir, "omc_stderr.log")
    write_captured_process_output(stdout_log, stderr_log, result.stdout, result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"omc failed for power={power} with exit code {result.returncode}. "
            f"See {case_dir}/omc_stderr.log."
        )

    raw_csv_path = (
        result_tmp_path
        if os.path.exists(result_tmp_path)
        else result_csv_path
    )
    if not os.path.exists(raw_csv_path):
        raise RuntimeError(
            f"omc succeeded but output CSV missing for power={power}. "
            f"Expected: {result_tmp_path}"
        )

    try:
        rr.publish_validated_result(
            raw_csv_path,
            result_csv_path,
            manifest=expected_manifest,
            required_columns=("time", *validation_required_columns),
            requested_stop_time=float(stop_time),
            stop_time_slack_s=_validation_stop_slack(stop_time),
            min_samples=(
                max(int(validation_min_samples), 2)
                if validation_min_samples is not None
                else None
            ),
            quarantine_root=quarantine_root,
        )
    except rr.ResultRejectedError as exc:
        raise RuntimeError(
            f"simulation output rejected by validation for power={power}; "
            f"failed checks: {', '.join(exc.report.failed_checks)}. "
            f"See {case_dir}."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"simulation finished but the validated result or its provenance "
            f"sidecars could not be published beside {result_csv_path}: {exc}"
        ) from exc

    return result_csv_path


def _count_data_rows(path: str) -> int:
    with open(path, "rb") as handle:
        header = handle.readline()
        if not header:
            raise ValueError(f"Empty CSV: {path}")
        count = 0
        last_byte = b"\n"
        while True:
            buf = handle.read(1024 * 1024)
            if not buf:
                break
            count += buf.count(b"\n")
            last_byte = buf[-1:]
        if count == 0:
            return 0
        if last_byte != b"\n":
            count += 1
        return count


def _tail_start_index(
    sample_count: int,
    tail_fraction: float,
    tail_min_samples: int,
) -> int:
    """First row index of the averaging tail (matches read_tail_means)."""
    tail_size = max(int(sample_count * tail_fraction), tail_min_samples)
    tail_size = min(tail_size, sample_count)
    return sample_count - tail_size


def read_tail_means(
    csv_path: str,
    variable_names: list[str],
    tail_fraction: float,
    tail_min_samples: int,
) -> dict[str, float]:
    sample_count = _count_data_rows(csv_path)
    if sample_count == 0:
        raise ValueError(f"No data rows in CSV: {csv_path}")

    start_index = _tail_start_index(sample_count, tail_fraction, tail_min_samples)

    with open(csv_path, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Empty CSV: {csv_path}")

        names = [item.strip().strip('"') for item in header]
        name_to_index = {name: idx for idx, name in enumerate(names)}
        missing = [name for name in variable_names if name not in name_to_index]
        if missing:
            raise ValueError(
                f"Missing result variables in {csv_path}: {', '.join(missing)}"
            )

        sums = {name: 0.0 for name in variable_names}
        count = 0
        for idx, row in enumerate(reader):
            if idx < start_index:
                continue
            if not row:
                continue
            for name in variable_names:
                sums[name] += float(row[name_to_index[name]])
            count += 1

    if count == 0:
        raise ValueError(f"No tail data rows in CSV: {csv_path}")

    return {name: sums[name] / float(count) for name in variable_names}


# ---------------------------------------------------------------------------
# Late-window convergence reporting
# ---------------------------------------------------------------------------


def _stationarity_metrics(
    values: list[float],
    times: list[float],
) -> dict[str, float | bool]:
    """Raw late-window statistics for one series (no normalization applied).

    The window is split into two equal halves (``values[:n//2]`` and
    ``values[n - n//2:]``; the middle sample of an odd-sized window belongs
    to neither average).  Returned statistics:

    - the two half-window means and the full-window mean;
    - the peak-to-peak span -- reported as a diagnostic only, since
      acceptance uses engineering-scale denominators (see
      :func:`evaluate_tail_convergence`);
    - the window duration and the centered-time ordinary-least-squares
      slope (the derivative estimate; centering keeps conditioning sane for
      long-horizon runs with large absolute time stamps);
    - the absolute window-mean difference and the projected trend drift
      ``|slope| * window duration`` -- the two deviations that the family
      bars compare against the engineering scale.
    """
    n = len(values)
    half = n // 2
    first_window = values[:half]
    second_window = values[n - half:]
    mean1 = sum(first_window) / len(first_window)
    mean2 = sum(second_window) / len(second_window)
    mean_all = sum(values) / n
    span = max(values) - min(values)
    duration = times[-1] - times[0]

    # Centered-time ordinary least squares (keeps conditioning sane for
    # long-horizon runs with large absolute time stamps).
    t_center = sum(times) / n
    s_xx = 0.0
    s_xy = 0.0
    for value, time_value in zip(values, times):
        dt = time_value - t_center
        s_xx += dt * dt
        s_xy += dt * (value - mean_all)
    slope = s_xy / s_xx if s_xx > 0.0 else 0.0

    # Robust oscillation measure after removing the fitted linear trend.
    # The 99th-minus-1st percentile rejects persistent cycles without making
    # one isolated numerical spike the acceptance metric.
    residuals = sorted(
        value - (mean_all + slope * (time_value - t_center))
        for value, time_value in zip(values, times)
    )

    def percentile(sorted_values: list[float], quantile: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = quantile * (len(sorted_values) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return sorted_values[lower]
        fraction = position - lower
        return (
            sorted_values[lower] * (1.0 - fraction)
            + sorted_values[upper] * fraction
        )

    robust_residual_amplitude = (
        percentile(residuals, 0.99) - percentile(residuals, 0.01)
    )

    return {
        "tail_mean": mean_all,
        "first_window_mean": mean1,
        "second_window_mean": mean2,
        "tail_span": span,
        "window_duration_s": abs(duration),
        "least_squares_slope_per_s": slope,
        "window_mean_difference": abs(mean1 - mean2),
        "projected_trend_drift": abs(slope) * abs(duration),
        "detrended_residual_p99_minus_p01": robust_residual_amplitude,
        "all_finite": True,
    }


def convergence_family_config(
    *,
    temperature_abs_tol: float = DEFAULT_TEMPERATURE_ABS_TOLERANCE,
    temperature_floor: float = DEFAULT_TEMPERATURE_SCALE_FLOOR,
    power_abs_tol: float = DEFAULT_POWER_ABS_TOLERANCE,
    power_floor: float = DEFAULT_POWER_SCALE_FLOOR,
    population_floor: float = DEFAULT_POPULATION_SCALE_FLOOR,
) -> dict[str, tuple[float, float | None]]:
    """Resolve per-family ``(scale floor, absolute bar)`` pairs.

    Families without a defined absolute bar carry ``None`` and impose only
    the relative bars.  Units: temperature floor/bar in the setpoint-table
    temperature unit (degrees Celsius; kelvin shares the numeric width),
    power in watts, population dimensionless; the generic family is fixed.
    """
    return {
        CONVERGENCE_FAMILY_TEMPERATURE: (
            float(temperature_floor),
            float(temperature_abs_tol),
        ),
        CONVERGENCE_FAMILY_POPULATION: (float(population_floor), None),
        CONVERGENCE_FAMILY_POWER: (float(power_floor), float(power_abs_tol)),
        CONVERGENCE_FAMILY_GENERIC: (
            float(DEFAULT_GENERIC_SCALE_FLOOR),
            None,
        ),
    }


def combined_tolerance_verdict(
    raw: float,
    *,
    relative_eps: float,
    scale: float,
    absolute_bar: float | None,
) -> dict[str, float | str | None]:
    """Apply the combined relative-or-absolute rule to one raw deviation.

    The single documented acceptance rule (review C1 / REV008-01), applied
    separately to ``window_mean_difference`` and ``projected_trend_drift``::

        d <= max(d_abs, eps * S_x)      # relative-or-absolute
        S_x = max(|tail_mean|, floor)   # engineering scale, passed in here

    The two bars are ALTERNATIVES, not a both-must-clear pair: the metric
    passes when either the relative bar (``eps * S_x``) or the family's
    absolute bar (``d_abs``) accepts it.  The relative bar keeps
    large-amplitude signals honest against their own level and carries all
    of the large-signal strictness; the absolute bar is a near-zero rescue
    only -- under ``max`` it can never reject a deviation the relative bar
    already accepted, and it decides the verdict only for quantities sitting
    near the family floor, where the floor-scaled relative bar would
    otherwise be tighter than the physical unit of the signal.  A deviation
    is rejected only when it exceeds BOTH applicable limits.

    Returns the record stored in every scored check's detail:

    - ``relative_limit``: ``eps * S_x`` (the relative bar in physical units);
    - ``absolute_limit``: the family absolute bar, or ``None`` when the
      family defines none (relative bars only);
    - ``effective_limit``: ``max(eps * S_x, d_abs)`` -- the combined limit
      actually applied;
    - ``decision_branch``: ``"relative"`` when the relative bar accepted the
      metric (including every family without an absolute bar),
      ``"absolute_rescue"`` when only the absolute bar accepted it (the
      documented near-zero rescue), ``"rejected"`` when the deviation
      exceeds both applicable limits, or ``None`` when the deviation is not
      finite (the separate finiteness failure reports that case).
    """
    relative_limit = float(relative_eps) * float(scale)
    absolute = None if absolute_bar is None else float(absolute_bar)
    effective = (
        relative_limit if absolute is None else max(relative_limit, absolute)
    )
    raw = float(raw)
    if not math.isfinite(raw):
        branch: str | None = None
    elif raw <= effective:
        branch = (
            "absolute_rescue"
            if absolute is not None
            and absolute > relative_limit
            and raw > relative_limit
            else "relative"
        )
    else:
        branch = "rejected"
    return {
        "relative_limit": relative_limit,
        "absolute_limit": absolute,
        "effective_limit": effective,
        "decision_branch": branch,
    }


def convergence_report_path(csv_path: str) -> Path:
    """Path of ``{stem}.convergence.json`` beside a per-power result CSV."""
    return Path(csv_path).with_suffix(".convergence.json")


def write_convergence_report(csv_path: str, report: dict) -> Path:
    """Persist one convergence report beside its per-power CSV (atomically)."""
    sidecar = convergence_report_path(csv_path)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    _run_results().atomic_write_text(sidecar, payload)
    return sidecar


def evaluate_tail_convergence(
    csv_path: str,
    *,
    state_columns: list[str],
    tail_fraction: float,
    tail_min_samples: int,
    window_tolerance: float,
    slope_tolerance: float,
    core_model: str | None = None,
    temperature_abs_tol: float = DEFAULT_TEMPERATURE_ABS_TOLERANCE,
    temperature_floor: float = DEFAULT_TEMPERATURE_SCALE_FLOOR,
    power_abs_tol: float = DEFAULT_POWER_ABS_TOLERANCE,
    power_floor: float = DEFAULT_POWER_SCALE_FLOOR,
    population_floor: float = DEFAULT_POPULATION_SCALE_FLOOR,
    qualification_profile: str = DEFAULT_QUALIFICATION_PROFILE,
    residual_amplitude_tolerance: float | None = None,
    minimum_tail_duration_s: float = 0.0,
    require_plant_signals: bool = False,
) -> dict:
    """Build the per-power convergence report for a finished result CSV.

    The averaging tail (same window :func:`read_tail_means` uses) is split
    into two equal windows.  Every requested state column, plus each plant
    signal present, must satisfy its family's combined acceptance rule:

    - ``|mean(W1) - mean(W2)| <= max(window_tolerance * S_x, absolute bar)``
      and ``|slope| * duration <= max(slope_tolerance * S_x, absolute bar)``
      with the engineering scale ``S_x = max(|mean|, family floor)`` -- the
      combined relative-or-absolute rule of :func:`combined_tolerance_verdict`;
      and
    - all samples in the window must be finite.

    Where the family defines an absolute bar (temperature and power), the
    relative and absolute bars are alternatives and the effective combined
    limit is the LARGER of the two; only a deviation exceeding both is
    rejected.  Relative bars carry all of the large-signal strictness;
    absolute bars are a near-zero rescue alone -- they can never reject a
    deviation the relative bar already accepted, and they decide the
    verdict only for quantities near zero.  Each check's detail records the
    raw deviation, the relative limit, the absolute bar, the effective
    combined limit, and the decision branch that accepted or rejected the
    metric.

    Families (:func:`classify_convergence_family`): temperature columns add
    an absolute bar in the setpoint-table temperature unit (degrees Celsius;
    kelvin shares the numeric width); normalized population columns are
    judged relative only; power columns add an absolute watt bar; unmapped
    columns fall back to the relative bars with a small safety floor.  The
    peak-to-peak span stays a reported diagnostic -- it no longer forms the
    acceptance denominator.

    Plant groups: neutron-population drift and core-temperature drift as
    before; each present reactor/fission power column is stationarity
    checked under the power family (hard gate).  The former
    ``net_energy_residual`` group is retired: ``powerblock.reactorPower -
    powerblock.fissionPower.P`` is dominated by decay heat and nonprompt-power
    bookkeeping and is NOT a plant energy balance, so the pair difference is
    reported only by the diagnostic group
    ``decay_or_nonprompt_power_stationarity`` (status ``"noted"``), which
    never affects qualification.

    Plant groups whose columns are absent are skipped with an explicit note
    instead of failing -- lumped and segmented models name them differently.
    A late window holding fewer than ``tail_min_samples`` samples fails
    every scored check (insufficient sampling is reported explicitly, never
    silently absorbed).
    """
    sample_count = _count_data_rows(csv_path)
    if sample_count == 0:
        raise ValueError(f"No data rows in CSV: {csv_path}")
    start_index = _tail_start_index(sample_count, tail_fraction, tail_min_samples)

    plant_population = next(iter(PLANT_POPULATION_COLUMNS))
    wanted: list[str] = list(state_columns)
    for candidate in (
        *PLANT_POPULATION_COLUMNS,
        *PLANT_CORE_TEMPERATURE_COLUMNS,
        *PLANT_POWER_MINUEND_COLUMNS,
        *PLANT_POWER_SUBTRAHEND_COLUMNS,
    ):
        if candidate not in wanted:
            wanted.append(candidate)

    times: list[float] = []
    series: dict[str, list[float]] = {name: [] for name in wanted}
    nonfinite_counts: dict[str, int] = {name: 0 for name in wanted}
    nonfinite_time = 0
    present: set[str] = set()

    with open(csv_path, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Empty CSV: {csv_path}")
        names = [item.strip().strip('"') for item in header]
        name_to_index = {name: idx for idx, name in enumerate(names)}
        time_index = name_to_index.get("time")
        if time_index is None:
            raise ValueError(f"Missing 'time' column in {csv_path}")
        wanted_indices = {
            name: name_to_index[name] for name in wanted if name in name_to_index
        }
        present.update(wanted_indices)

        for row_number, row in enumerate(reader):
            if row_number < start_index:
                continue
            if not row or all(not cell.strip() for cell in row):
                continue
            try:
                time_value = float(row[time_index])
            except (IndexError, TypeError, ValueError):
                nonfinite_time += 1
                time_value = math.nan
            if not math.isfinite(time_value):
                nonfinite_time += 1
            times.append(time_value)
            for name, index in wanted_indices.items():
                try:
                    value = float(row[index])
                except (IndexError, TypeError, ValueError):
                    value = math.nan
                if not math.isfinite(value):
                    nonfinite_counts[name] += 1
                    value = math.nan
                series[name].append(value)

    n_tail = len(times)
    half = n_tail // 2
    enough_samples = n_tail >= 2 and half >= 1 and (n_tail - half) >= 1
    insufficient_samples = n_tail < tail_min_samples
    timestamp_issue = (
        "non-finite 'time' samples in the late window" if nonfinite_time else None
    )

    subtrahend_prefix = {"1r": "core1R", "9r": "msre9r"}.get(core_model or "")
    plant_power_subtrahend = (
        f"{subtrahend_prefix}.powerblock.fissionPower.P"
        if subtrahend_prefix is not None
        else next(
            (name for name in PLANT_POWER_SUBTRAHEND_COLUMNS if name in present),
            PLANT_POWER_SUBTRAHEND_COLUMNS[0],
        )
    )

    family_config = convergence_family_config(
        temperature_abs_tol=temperature_abs_tol,
        temperature_floor=temperature_floor,
        power_abs_tol=power_abs_tol,
        power_floor=power_floor,
        population_floor=population_floor,
    )

    #: Recorded on every computed check; readers must never mistake the
    #: pair difference for an energy-conservation statement.
    POWER_PAIR_NOTE = (
        "diagnostic only: powerblock.reactorPower - "
        "powerblock.fissionPower.P reflects decay "
        "heat and nonprompt-power bookkeeping between plant channels; it "
        "is not a plant energy balance and does not gate qualification"
    )

    def make_check(
        label: str,
        column_label: str,
        metrics: dict[str, float | bool] | None,
        family: str,
        *,
        gated: bool = True,
        skip_reason: str | None = None,
    ) -> dict:
        if skip_reason is not None:
            return {
                "group": label,
                "column": column_label,
                "status": "skipped",
                "reason": skip_reason,
            }
        source = metrics or {}
        detail: dict[str, object] = dict(source)
        detail["family"] = family
        detail["gated"] = gated

        entry: dict = {"group": label, "column": column_label}
        failures: list[str] = []

        if source:
            floor, abs_bar = family_config[family]
            scale = max(abs(float(source["tail_mean"])), floor)  # S_x
            span = float(source["tail_span"])
            detail["scale_denominator"] = scale
            detail["absolute_tolerance_applied"] = abs_bar
            detail["derivative_estimate"] = (
                "least-squares slope over the late window multiplied by "
                "its duration"
            )
            for raw_key, human_label, relative_eps in (
                (
                    "window_mean_difference",
                    "window-mean difference",
                    window_tolerance,
                ),
                (
                    "projected_trend_drift",
                    "projected trend drift",
                    slope_tolerance,
                ),
            ):
                raw = float(source[raw_key])  # type: ignore[arg-type]
                detail[f"{raw_key}_engineering_relative"] = raw / scale
                # Peak-to-peak span: diagnostic only.
                detail[f"{raw_key}_span_fraction"] = (
                    (raw / span) if span > 0.0 else None
                )
                # Combined relative-or-absolute rule (review C1): one rule
                # per metric, with the limits and the accepting/rejecting
                # branch recorded verbatim.
                verdict = combined_tolerance_verdict(
                    raw,
                    relative_eps=relative_eps,
                    scale=scale,
                    absolute_bar=abs_bar,
                )
                detail[f"{raw_key}_relative_limit"] = verdict["relative_limit"]
                detail[f"{raw_key}_absolute_limit"] = verdict["absolute_limit"]
                detail[f"{raw_key}_effective_limit"] = verdict["effective_limit"]
                detail[f"{raw_key}_decision_branch"] = verdict["decision_branch"]
                if gated and verdict["decision_branch"] == "rejected":
                    failures.append(
                        f"{human_label} exceeds the combined tolerance "
                        f"(effective limit {verdict['effective_limit']:g})"
                    )

            if gated and residual_amplitude_tolerance is not None:
                raw = float(source["detrended_residual_p99_minus_p01"])
                verdict = combined_tolerance_verdict(
                    raw,
                    relative_eps=residual_amplitude_tolerance,
                    scale=scale,
                    absolute_bar=abs_bar,
                )
                detail["detrended_residual_relative_eps"] = (
                    residual_amplitude_tolerance
                )
                detail["detrended_residual_relative_limit"] = verdict[
                    "relative_limit"
                ]
                detail["detrended_residual_absolute_limit"] = verdict[
                    "absolute_limit"
                ]
                detail["detrended_residual_effective_limit"] = verdict[
                    "effective_limit"
                ]
                detail["detrended_residual_decision_branch"] = verdict[
                    "decision_branch"
                ]
                if verdict["decision_branch"] == "rejected":
                    failures.append(
                        "detrended residual amplitude exceeds the combined "
                        f"tolerance (effective limit "
                        f"{verdict['effective_limit']:g})"
                    )

            if gated:
                if not source.get("all_finite", True):
                    failures.append("non-finite samples in the late window")
                if timestamp_issue:
                    failures.append(timestamp_issue)
                entry["status"] = "fail" if failures else "pass"
            else:
                # Diagnostic-only entry (power pair): measured, explained,
                # and excluded from the qualification verdict regardless of
                # how large its deviations are.
                entry["status"] = "noted"
                entry["note"] = POWER_PAIR_NOTE
        else:
            entry["status"] = "skipped"

        if failures:
            entry["reason"] = "; ".join(failures)
        entry["detail"] = detail
        return entry

    checks: list[dict] = []

    def evaluate_group(
        label: str,
        columns: list[tuple[str, str]],
        absent_note: str | None = None,
        *,
        gated: bool = True,
    ) -> None:
        """Check each listed (series_name, report_label) pair of the group."""
        for series_name, report_label in columns:
            absent = series_name not in present
            values = series.get(series_name, [])
            if absent:
                checks.append(
                    make_check(
                        label,
                        report_label,
                        None,
                        classify_convergence_family(series_name),
                        gated=gated,
                        skip_reason=(
                            f"column absent: {absent_note}" if absent_note
                            else f"column absent: {series_name}"
                        ),
                    )
                )
                continue
            if not enough_samples or len(values) != n_tail:
                checks.append(
                    make_check(
                        label,
                        report_label,
                        None,
                        classify_convergence_family(series_name),
                        gated=gated,
                        skip_reason=(
                            "late window has too few usable samples "
                            f"({n_tail} rows)"
                        ),
                    )
                )
                continue
            metrics = _stationarity_metrics(values, times)
            nonfinite = nonfinite_counts.get(series_name, 0)
            if nonfinite:
                metrics["all_finite"] = False  # type: ignore[assignment]
                metrics["nonfinite_samples"] = nonfinite
            checks.append(
                make_check(
                    label,
                    report_label,
                    metrics,
                    classify_convergence_family(series_name),
                    gated=gated,
                )
            )

    evaluate_group("requested_states", [(name, name) for name in state_columns])

    population_present = [c for c in PLANT_POPULATION_COLUMNS if c in present]
    if population_present:
        evaluate_group(
            "population_drift",
            [(population_present[0], population_present[0])],
            absent_note=", ".join(PLANT_POPULATION_COLUMNS),
        )
    else:
        checks.append(
            {
                "group": "population_drift",
                "status": "fail" if require_plant_signals else "skipped",
                "reason": "column absent: no neutron-population channel "
                f"(looked for {', '.join(PLANT_POPULATION_COLUMNS)})",
            }
        )

    temperature_present = [
        c for c in PLANT_CORE_TEMPERATURE_COLUMNS if c in present
    ]
    if temperature_present:
        evaluate_group(
            "core_temperature_drift",
            [(name, name) for name in temperature_present],
        )
    else:
        checks.append(
            {
                "group": "core_temperature_drift",
                "status": "fail" if require_plant_signals else "skipped",
                "reason": "column absent: no core temperature channel "
                f"(looked for {', '.join(PLANT_CORE_TEMPERATURE_COLUMNS)})",
            }
        )

    power_minuend_present = [
        c for c in PLANT_POWER_MINUEND_COLUMNS if c in present
    ]
    power_pair_available = bool(power_minuend_present) and (
        plant_power_subtrahend in present
    )

    # Hard gate: each reactor/fission power channel settles on its own,
    # under the power family's relative and absolute watt bars.
    power_channels = [
        (name, name)
        for name in (*power_minuend_present, plant_power_subtrahend)
        if name in present
    ]
    if power_channels:
        evaluate_group("plant_power_stationarity", power_channels)
    else:
        missing = [
            name
            for name in (*PLANT_POWER_MINUEND_COLUMNS, plant_power_subtrahend)
            if name not in present
        ]
        checks.append(
            {
                "group": "plant_power_stationarity",
                "status": "fail" if require_plant_signals else "skipped",
                "reason": "column absent: no plant power channels "
                f"(missing {', '.join(missing)})",
            }
        )

    # Former 'net_energy_residual' group, retired: the pair difference is
    # dominated by decay heat / nonprompt-power bookkeeping, is not a plant
    # energy balance, and no longer participates in the hard gate.
    if power_pair_available:
        minuend_name = power_minuend_present[0]
        residual_values = [
            (a - b) if (math.isfinite(a) and math.isfinite(b)) else math.nan
            for a, b in zip(series[minuend_name], series[plant_power_subtrahend])
        ]
        residual_label = f"{minuend_name} - {plant_power_subtrahend}"
        series[residual_label] = residual_values
        present.add(residual_label)
        nonfinite_counts[residual_label] = sum(
            1 for v in residual_values if not math.isfinite(v)
        )
        evaluate_group(
            "decay_or_nonprompt_power_stationarity",
            [(residual_label, residual_label)],
            gated=False,
        )
    else:
        missing = [
            name
            for name in (*PLANT_POWER_MINUEND_COLUMNS, plant_power_subtrahend)
            if name not in present
        ]
        checks.append(
            {
                "group": "decay_or_nonprompt_power_stationarity",
                "status": "skipped",
                "reason": "column absent: reactor-power pair incomplete "
                f"(missing {', '.join(missing)})",
            }
        )

    # Insufficient sampling is a failure, never a silent shrink of the
    # requested late window down to whatever rows happen to exist.
    insufficient_note = (
        f"late window holds {n_tail} samples but tail_min_samples="
        f"{tail_min_samples} were requested; refusing to score an "
        "undersampled window"
    )
    if insufficient_samples:
        for check_entry in checks:
            status = check_entry.get("status")
            if status in ("pass", "fail"):
                check_entry["status"] = "fail"
                reason = check_entry.get("reason", "")
                check_entry["reason"] = (
                    f"{reason}; {insufficient_note}" if reason else insufficient_note
                )

    tail_duration_s = (
        abs(times[-1] - times[0])
        if len(times) >= 2 and all(math.isfinite(t) for t in (times[0], times[-1]))
        else 0.0
    )
    insufficient_duration = tail_duration_s < float(minimum_tail_duration_s)
    if insufficient_duration:
        duration_note = (
            f"late window duration {tail_duration_s:g} s is shorter than "
            f"the required {minimum_tail_duration_s:g} s"
        )
        checks.append(
            {
                "group": "tail_duration",
                "column": "time",
                "status": "fail",
                "reason": duration_note,
                "detail": {
                    "duration_s": tail_duration_s,
                    "required_s": float(minimum_tail_duration_s),
                    "gated": True,
                },
            }
        )

    any_pass = any(c["status"] == "pass" for c in checks)
    any_fail = any(c["status"] == "fail" for c in checks)
    passed = any_pass and not any_fail

    report = {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "kind": "setpoint-tail-convergence",
        "qualification_profile": qualification_profile,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "result_csv": os.path.basename(csv_path),
        "passed": passed,
        "tolerances": {
            "window_mean_relative_eps": window_tolerance,
            "projected_trend_relative_eps": slope_tolerance,
            "temperature_absolute_bar": temperature_abs_tol,
            "temperature_scale_floor": temperature_floor,
            "power_absolute_bar": power_abs_tol,
            "power_scale_floor": power_floor,
            "population_scale_floor": population_floor,
            "generic_scale_floor": DEFAULT_GENERIC_SCALE_FLOOR,
            "detrended_residual_relative_eps": residual_amplitude_tolerance,
            "minimum_tail_duration_s": float(minimum_tail_duration_s),
            "required_plant_signals": bool(require_plant_signals),
        },
        "denominator_policy": (
            "Acceptance denominators are engineering scales "
            "S_x = max(|mean|, family floor), not the peak-to-peak span: "
            "temperature floors/bars in the setpoint-table temperature unit "
            "(degrees Celsius, same numeric width as kelvin), power floors/"
            "bars in watts, population dimensionless (relative only), "
            "unmapped columns relative-only against a small safety floor.  "
            "Each scored metric follows the combined relative-or-absolute "
            "rule d <= max(d_abs, eps * S_x): the effective limit is the "
            "larger of the relative bar and the family absolute bar, and "
            "only a deviation exceeding both is rejected.  The span is "
            "still reported per check as a diagnostic."
        ),
        "derivative_estimate": (
            "Least-squares slope of each series over the late window on "
            "centered time, multiplied by the window duration before the "
            "bars are applied."
        ),
        "power_pair_policy": (
            "powerblock.reactorPower - powerblock.fissionPower.P is "
            "dominated by decay heat and "
            "nonprompt-power bookkeeping; the pair difference is reported "
            "under decay_or_nonprompt_power_stationarity as a diagnostic "
            "(status 'noted') and is NOT a plant energy balance.  Reactor "
            "and fission power columns are each stationarity checked "
            "separately under the power family."
        ),
        "insufficient_tail_samples": (
            {"samples": n_tail, "required": tail_min_samples}
            if insufficient_samples
            else None
        ),
        "tail": {
            "sample_count_total": sample_count,
            "start_index": start_index,
            "samples": n_tail,
            # Both scored windows hold exactly `half` samples (equal slices of
            # n//2; the middle sample is dropped when n_tail is odd), matching
            # _stationarity_metrics.
            "first_window_samples": half,
            "second_window_samples": half,
            "tail_fraction": tail_fraction,
            "tail_min_samples": tail_min_samples,
            "duration_s": tail_duration_s,
            "minimum_duration_s": float(minimum_tail_duration_s),
        },
        "checks": checks,
    }

    if core_model is not None:
        report["core_model"] = str(core_model)
    return report


def _weighted_region_average(
    row: dict[str, float],
    *,
    prefix: str,
    weights: tuple[float, ...],
) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for idx, weight in enumerate(weights, start=1):
        key = f"{prefix}[{idx}]"
        if key not in row:
            raise KeyError(f"Missing 9R regional setpoint '{key}' while computing weighted average.")
        weighted_sum += float(row[key]) * weight
        total_weight += weight
    if total_weight <= 0.0:
        raise ValueError("Invalid 9R weighting with non-positive total volume.")
    return weighted_sum / total_weight


def _apply_9r_scalar_harmonization(row: dict[str, float]) -> None:
    # Keep 9R scalar columns physically representative of the whole core,
    # not just region 1, so they are comparable to 1R scalar setpoints.
    row["fuelTempSetPointNode1"] = _weighted_region_average(
        row,
        prefix="TF1_0_regions",
        weights=R9_VOL_F1,
    )
    row["fuelTempSetPointNode2"] = _weighted_region_average(
        row,
        prefix="TF2_0_regions",
        weights=R9_VOL_F2,
    )
    row["graphiteTempSetPoint"] = _weighted_region_average(
        row,
        prefix="TG_0_regions",
        weights=R9_VOL_G,
    )


def build_table_row(
    core_model: str,
    power: float,
    tail_means: dict[str, float],
    table_mapping: dict[str, str],
    heat_loss: bool,
) -> dict[str, float]:
    row = {"power": power, "heatLossEnabled": 1 if heat_loss else 0}
    for column, result_variable in table_mapping.items():
        row[column] = tail_means[result_variable]
    if core_model == "9r":
        _apply_9r_scalar_harmonization(row)
    return row


def write_table(
    rows: list[dict[str, float]],
    output_path: str,
    steady_state_columns: list[str],
) -> None:
    """Write the setpoint table atomically.

    The rows land under a unique temporary name in the destination directory
    first (``tempfile.mkstemp``) and are moved onto ``output_path`` with
    :func:`os.replace` once written, flushed, and fsynced, so an interrupted
    run can never leave a truncated or partially written table at the final
    path and two concurrent writers cannot clobber each other's temp file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    header = ["power", "heatLossEnabled"] + steady_state_columns + [QUALIFIED_COLUMN]
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{os.path.basename(output_path)}.", suffix=".partial",
        dir=os.path.dirname(output_path) or ".",
    )
    tmp_path = str(tmp_name)
    try:
        with os.fdopen(descriptor, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            for row in rows:
                formatted = {"power": power_key(row["power"])}
                formatted["heatLossEnabled"] = str(int(row.get("heatLossEnabled", 0)))
                for column in steady_state_columns:
                    formatted[column] = f"{row[column]:.16g}"
                formatted[QUALIFIED_COLUMN] = str(int(bool(row.get(QUALIFIED_COLUMN, True))))
                writer.writerow(formatted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, output_path)
    except BaseException:
        # Never leave the stray temp file behind on failure or interruption.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def table_row_key(row: dict[str, float]) -> tuple[str, int]:
    return (power_key(row["power"]), int(row.get("heatLossEnabled", 0)))


def read_existing_table_rows(
    output_path: str,
    steady_state_columns: list[str],
) -> list[dict[str, float]]:
    with open(output_path, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Existing output CSV has no header: {output_path}")
        if "heatLossEnabled" not in reader.fieldnames:
            raise ValueError(
                "Existing output CSV missing 'heatLossEnabled' column. "
                "Regenerate it before appending."
            )
        existing: list[dict[str, float]] = []
        for raw in reader:
            if not raw or (raw.get("power") or "").strip() == "":
                continue
            parsed: dict[str, float] = {
                "power": float(raw["power"]),
                "heatLossEnabled": int(float(raw.get("heatLossEnabled", "0"))),
            }
            for column in steady_state_columns:
                val = (raw.get(column) or "").strip()
                if val == "":
                    raise ValueError(
                        f"Existing output CSV has blank '{column}' for "
                        f"power={raw.get('power')!r}, "
                        f"heatLossEnabled={raw.get('heatLossEnabled')!r} in "
                        f"{output_path}. Fix or regenerate it before appending."
                    )
                parsed[column] = float(val)
            # Rows from tables written before convergence qualification carry
            # no verdict; they count as unqualified rather than silently
            # inheriting one.
            raw_qualified = (raw.get(QUALIFIED_COLUMN) or "").strip()
            parsed[QUALIFIED_COLUMN] = (
                int(float(raw_qualified)) if raw_qualified != "" else 0
            )
            existing.append(parsed)
        return existing


def merge_table_rows(
    existing: list[dict[str, float]],
    new_rows: list[dict[str, float]],
) -> list[dict[str, float]]:
    """Replace existing rows that share (power, heatLossEnabled) with *new_rows*."""
    merged: dict[tuple[str, int], dict[str, float]] = {}
    for row in existing:
        merged[table_row_key(row)] = row
    for row in new_rows:
        merged[table_row_key(row)] = row
    rows = list(merged.values())
    rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))
    return rows


def run_power_case(
    power: float,
    work_dir: str,
    core_model: str,
    model_name: str,
    model_src: str,
    library_src: str,
    stop_time: float,
    variable_names: list[str],
    tail_fraction: float,
    tail_min_samples: int,
    table_mapping: dict[str, str],
    reuse_csv: bool,
    heat_loss: bool,
    init_overrides: dict[str, float] | None,
    feedback_on: bool,
    omc_timeout_seconds: float | None = None,
    use_provenance: bool = False,
    convergence_window_tolerance: float = DEFAULT_CONVERGENCE_WINDOW_TOLERANCE,
    convergence_slope_tolerance: float = DEFAULT_CONVERGENCE_SLOPE_TOLERANCE,
    convergence_temperature_abs_tol: float = DEFAULT_TEMPERATURE_ABS_TOLERANCE,
    convergence_temperature_floor: float = DEFAULT_TEMPERATURE_SCALE_FLOOR,
    convergence_power_abs_tol: float = DEFAULT_POWER_ABS_TOLERANCE,
    convergence_power_floor: float = DEFAULT_POWER_SCALE_FLOOR,
    convergence_population_floor: float = DEFAULT_POPULATION_SCALE_FLOOR,
    qualification_profile: str = DEFAULT_QUALIFICATION_PROFILE,
    convergence_residual_amplitude_tolerance: float | None = None,
    minimum_tail_duration_s: float = 0.0,
    require_plant_signals: bool = False,
    quarantine_root: str | None = None,
    claim_timeout_s: float | None = None,
) -> dict[str, float]:
    power_tag = sanitize_power_tag(power)
    interval_override = NUMBER_OF_INTERVALS_OVERRIDES.get(core_model, {}).get(power_tag)
    variable_filter = variable_filter_for_core(core_model)
    csv_path = os.path.join(
        work_dir,
        f"power_{power_tag}",
        f"MSRR_ss_{power_tag}_res.csv",
    )

    # Requested-result provenance: with use_provenance, an existing per-power
    # CSV is reused only when its manifest sidecar fingerprint matches this
    # exact request and the stored output passes validation; anything else is
    # quarantined and resimulated.  Without it (direct programmatic calls and
    # the historical unit-test surface) the plain existence-based reuse below
    # is kept unchanged.
    manifest: dict | None = None
    claim = None
    requested_vars = sorted(set(variable_names) | set(table_mapping.values()))
    try:
        if use_provenance:
            rr = _run_results()
            # Same-slot claim (review C2 / REV008-02): acquired ABOVE the
            # first prepare_result_path so two concurrent launches of one
            # power slot serialize before either quarantine/reuse decision,
            # and held through manifest build, reuse/quarantine decision,
            # simulation, validation, publication, and the sidecar writes
            # below -- released in ``finally`` only once the complete
            # published pair is visible.  Mirrors the claims-before-
            # quarantine invariant of the freq/startup/transient paths.
            # --claim_timeout_s bounds the wait (M2 / REV008-10).
            claim = rr.acquire_result_claim(csv_path, timeout_s=claim_timeout_s)
            manifest = build_setpoint_case_manifest(
                core_model=core_model,
                power=power,
                model_name=model_name,
                model_src=model_src,
                library_src=library_src,
                stop_time=stop_time,
                number_of_intervals=interval_override,
                init_overrides=init_overrides,
                feedback_on=feedback_on,
                variable_filter=variable_filter,
            )
            prepared = rr.prepare_result_path(
                csv_path,
                reuse_ok=bool(reuse_csv),
                expected_manifest=manifest,
                required_columns=("time", *requested_vars),
                requested_stop_time=float(stop_time),
                stop_time_slack_s=_validation_stop_slack(stop_time),
                min_samples=max(int(tail_min_samples), 2),
                quarantine_root=quarantine_root,
            )
            can_reuse = prepared.can_reuse
            if prepared.quarantined:
                print(
                    f"  power={power}: prior result artifacts quarantined to "
                    f"{prepared.quarantined[0].parent}"
                )
        else:
            can_reuse = bool(reuse_csv) and os.path.exists(csv_path)

        if not can_reuse:
            csv_path = run_steady_state_case(
                power=power,
                work_dir=work_dir,
                model_name=model_name,
                model_src=model_src,
                library_src=library_src,
                stop_time=stop_time,
                variable_filter=variable_filter,
                init_overrides=init_overrides,
                feedback_on=feedback_on,
                number_of_intervals=interval_override,
                omc_timeout_seconds=omc_timeout_seconds,
                expected_manifest=manifest,
                quarantine_root=quarantine_root,
                validation_required_columns=tuple(requested_vars) if manifest else (),
                validation_min_samples=int(tail_min_samples),
                result_claim=claim,
                claim_timeout_s=claim_timeout_s,
            )

        row_qualified = True
        unconverged_reason = ""
        if manifest is not None:
            report = evaluate_tail_convergence(
                csv_path,
                state_columns=requested_vars,
                tail_fraction=tail_fraction,
                tail_min_samples=tail_min_samples,
                window_tolerance=convergence_window_tolerance,
                slope_tolerance=convergence_slope_tolerance,
                core_model=core_model,
                temperature_abs_tol=convergence_temperature_abs_tol,
                temperature_floor=convergence_temperature_floor,
                power_abs_tol=convergence_power_abs_tol,
                power_floor=convergence_power_floor,
                population_floor=convergence_population_floor,
                qualification_profile=qualification_profile,
                residual_amplitude_tolerance=(
                    convergence_residual_amplitude_tolerance
                ),
                minimum_tail_duration_s=minimum_tail_duration_s,
                require_plant_signals=require_plant_signals,
            )
            sidecar = write_convergence_report(csv_path, report)
            row_qualified = bool(report["passed"])
            if not row_qualified:
                failed = [
                    f"{item.get('column', item.get('group', 'unknown'))}: "
                    f"{item.get('reason', item['status'])}"
                    for item in report["checks"]
                    if item["status"] == "fail"
                ]
                unconverged_reason = "; ".join(failed) or "late-window checks failed"
                print(
                    f"  power={power}: late-window checks FAILED "
                    f"({unconverged_reason}); report: {sidecar}"
                )

        tail_means = read_tail_means(
            csv_path=csv_path,
            variable_names=variable_names,
            tail_fraction=tail_fraction,
            tail_min_samples=tail_min_samples,
        )
        row = build_table_row(
            core_model=core_model,
            power=power,
            tail_means=tail_means,
            table_mapping=table_mapping,
            heat_loss=heat_loss,
        )
        # Non-float bookkeeping columns ride outside build_table_row's float map;
        # main() pops the diagnostic before writing the production table.
        row[QUALIFIED_COLUMN] = int(row_qualified)  # type: ignore[assignment]
        if not row_qualified:
            row["_unconverged_reason"] = unconverged_reason  # type: ignore[assignment]
        return row
    finally:
        # One acquisition per provenanced case (review C2): the claim is
        # released only after the complete published pair (result CSV plus
        # manifest/validation sidecars) is visible at the final slot -- or
        # on any failure, so a crashed case never wedges the slot.
        if claim is not None:
            claim.release()


def main() -> None:
    args = apply_qualification_profile(parse_args())

    if not (0.0 < args.tail_fraction <= 1.0):
        raise ValueError("--tail_fraction must be in (0, 1].")
    if args.tail_min_samples <= 0:
        raise ValueError("--tail_min_samples must be > 0.")
    if args.n_jobs <= 0:
        raise ValueError("--n_jobs must be > 0.")
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
    if args.heat_loss:
        raise ValueError(
            "Heat-loss setpoint generation is disabled. "
            "Use heat loss only in startup scenarios."
        )

    powers = parse_powers(args.powers)
    core_dir = os.path.abspath(args.core_dir)
    model_src = os.path.abspath(args.model) if args.model else os.path.join(core_dir, "MSRR.mo")
    library_src = (
        os.path.abspath(args.library)
        if args.library
        else os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    )
    base_model_name = args.model_name or MODEL_NAME_BY_CORE[args.core_model]
    work_dir = os.path.abspath(args.work_dir)
    if args.output.strip():
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), f"setpoints_{args.core_model}.csv")
        )

    if not os.path.exists(model_src):
        raise FileNotFoundError(f"Cannot find model file: {model_src}")
    if not os.path.exists(library_src):
        raise FileNotFoundError(f"Cannot find library file: {library_src}")

    os.makedirs(work_dir, exist_ok=True)

    table_mapping = table_to_result_variable(args.core_model)
    steady_state_columns = list(table_mapping.keys())
    requested_result_vars = sorted(set(table_mapping.values()))
    ncpu = min(args.n_jobs, len(powers))
    init_table = {}
    if args.init_from.strip():
        init_table = load_init_table(args.init_from, steady_state_columns)

    print("Generating MSRR steady-state table")
    print(f"  Core model:  {args.core_model}")
    print(f"  Model file:  {model_src}")
    print(f"  Library:     {library_src}")
    print(f"  Model name:  {base_model_name}")
    print(f"  Powers:      {powers}")
    print(f"  Stop time:   {args.stop_time}")
    print(f"  Tail window: {args.tail_fraction * 100:.1f}% (min {args.tail_min_samples} samples)")
    print(f"  Qualification profile: {args.qualification_profile}")
    print(
        f"  Convergence: relative window/slope eps {args.conv_window_tol:g}/"
        f"{args.conv_slope_tol:g} on engineering scales; temperature abs "
        f"{args.conv_temperature_abs_tol:g} (table degC, floor {args.conv_temperature_floor:g}), "
        f"power abs {args.conv_power_abs_tol:g} W (floor {args.conv_power_floor:g}), "
        f"population floor {args.conv_population_floor:g}"
    )
    print(
        "  Oscillation: "
        + (
            "diagnostic only"
            if args.conv_residual_amplitude_tol is None
            else (
                "detrended p99-p01 gate, relative eps "
                f"{args.conv_residual_amplitude_tol:g}"
            )
        )
        + f"; minimum tail duration {args.tail_min_duration:g} s"
    )
    print(f"  Work dir:    {work_dir}")
    print(f"  Reuse CSVs:  {args.reuse_csv} (fingerprint-checked when enabled)")
    if args.accept_unconverged:
        print("  Unconverged: accepted and marked qualified=0")
    else:
        print("  Unconverged: excluded from the output table")
    print("  Heat loss:   False (startup-only, disabled for power setpoints)")
    print(f"  Append:      {args.append}")
    print(f"  Init temp:   {args.init_temp}")
    feedback_on = not args.no_feedback
    print(f"  Feedback:   {'on' if feedback_on else 'off'}")
    print(f"  Init from:   {args.init_from or '(none)'}")
    print(f"  Output:      {output_path}")
    print(f"  Parallel:    {ncpu} jobs (threaded)")
    if args.omc_timeout_seconds > 0:
        print(f"  OMC timeout: {args.omc_timeout_seconds:g} s per case")
    if getattr(args, "claim_timeout_s", None) is not None:
        print(
            f"  Claim timeout: {args.claim_timeout_s:g} s on busy power slots"
        )
    print("=" * 72)
    rows = []
    failures: list[tuple[float, str]] = []
    total = len(powers)
    with ThreadPoolExecutor(max_workers=ncpu) as executor:
        future_map = {
            executor.submit(
                run_power_case,
                power=power,
                work_dir=work_dir,
                core_model=args.core_model,
                model_name=MODEL_NAME_OVERRIDES.get(args.core_model, {}).get(
                    sanitize_power_tag(power),
                    base_model_name,
                ),
                model_src=model_src,
                library_src=library_src,
                stop_time=STOP_TIME_OVERRIDES.get(args.core_model, {}).get(
                    sanitize_power_tag(power),
                    args.stop_time,
                ),
                variable_names=requested_result_vars,
                tail_fraction=args.tail_fraction,
                tail_min_samples=args.tail_min_samples,
                table_mapping=table_mapping,
                reuse_csv=args.reuse_csv,
                heat_loss=args.heat_loss,
                init_overrides=resolve_init_overrides(
                    args.core_model,
                    init_table,
                    power,
                    args.heat_loss,
                    args.init_temp,
                ),
                feedback_on=feedback_on,
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                use_provenance=True,
                convergence_window_tolerance=args.conv_window_tol,
                convergence_slope_tolerance=args.conv_slope_tol,
                convergence_temperature_abs_tol=args.conv_temperature_abs_tol,
                convergence_temperature_floor=args.conv_temperature_floor,
                convergence_power_abs_tol=args.conv_power_abs_tol,
                convergence_power_floor=args.conv_power_floor,
                convergence_population_floor=args.conv_population_floor,
                qualification_profile=args.qualification_profile,
                convergence_residual_amplitude_tolerance=(
                    args.conv_residual_amplitude_tol
                ),
                minimum_tail_duration_s=args.tail_min_duration,
                require_plant_signals=(
                    args.qualification_profile == "publication"
                ),
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            ): power
            for power in powers
        }
        done = 0
        for future in as_completed(future_map):
            power = future_map[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                failures.append((power, str(exc)))
                print(f"  ERROR: power={power} failed: {exc}")
            done += 1
            print(f"  Progress: {done}/{total} cases complete")

    if not rows and failures:
        print("ERROR: no setpoint cases succeeded; output table not written.")
        for power, message in sorted(failures):
            print(f"  power={power}: {message}")
        raise SystemExit(1)

    # Late-window qualification: failing rows stay out of the production
    # table unless explicitly accepted; either way they are reported.
    table_rows: list[dict[str, float]] = []
    unconverged: list[tuple[float, str]] = []
    for row in rows:
        reason = row.pop("_unconverged_reason", "")
        qualified = int(row.get(QUALIFIED_COLUMN, 1)) == 1
        if not qualified:
            # Collected independently of table membership so the which-powers
            # summary below fires in both exclude and accept modes
            # (--accept_unconverged still writes these rows, marked 0).
            unconverged.append((float(row["power"]), str(reason)))
        if qualified or args.accept_unconverged:
            table_rows.append(row)

    if unconverged:
        if args.accept_unconverged:
            print(
                f"{len(unconverged)} of {len(rows)} case(s) failed late-window "
                f"checks and are written with {QUALIFIED_COLUMN}=0:"
            )
        else:
            print(
                f"{len(unconverged)} of {len(rows)} case(s) failed late-window "
                "checks and are excluded from the output table:"
            )
        for power, reason in sorted(unconverged):
            print(f"  power={power}: {reason}")

    if not table_rows:
        print("ERROR: no qualified setpoint cases; output table not written.")
        if not args.accept_unconverged:
            print(
                "Rerun with a longer --stop_time, or pass --accept_unconverged "
                "to keep failing rows marked as unqualified."
            )
        raise SystemExit(1)

    succeeded = len(table_rows)
    if args.append and os.path.exists(output_path):
        existing = read_existing_table_rows(output_path, steady_state_columns)
        table_rows = merge_table_rows(existing, table_rows)
    else:
        table_rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))
    write_table(
        rows=table_rows,
        output_path=output_path,
        steady_state_columns=steady_state_columns,
    )

    print("=" * 72)
    print(
        f"Wrote steady-state table: {output_path} "
        f"({succeeded}/{total} cases succeeded"
        + (
            f"; {len(unconverged)} accepted unqualified)"
            if unconverged
            else ")"
        )
    )
    if failures or (unconverged and not args.accept_unconverged):
        print("Cases needing attention:")
        for power, message in sorted(failures):
            print(f"  power={power}: {message}")
        if unconverged and not args.accept_unconverged:
            for power, _reason in sorted(unconverged):
                print(
                    f"  power={power}: rerun after a longer --stop_time, or "
                    "pass --accept_unconverged to mark it as unqualified"
                )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
