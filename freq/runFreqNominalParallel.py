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
    1r10seg: SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS
    r5x5_z10: SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS

loading ``SegmentedMSR_PlantData.mo`` then ``core/SegmentedMSR.mo``.
``1r10seg`` (1-channel x 10-axial-segment 1R core, TASK-20260906-01) and
``r5x5_z10`` (5x5-radial x 10-axial-segment 1R core, TASK-20260906-02) are
segmented-package only: the legacy package has no 10-segment or 5x5
nominal-trim vehicle, so ``--package legacy --core_model {1r10seg,r5x5_z10}``
is rejected with a named error before any omc invocation.
Segmented mode requires ``--power 1.0`` (the rigs are trimmed full-power
vehicles and no segmented setpoint tables exist yet), ignores steady-state
tables, routes low-power
nFloor overrides to the rig's PKE_T instance (``pke.nFloor*``), and skips the
legacy ``forcingTimeStep`` overrides (parameter absent from the rigs). Because
``omc`` 1.27 ``simulate()`` scripting is broken system-wide, segmented runs use
``buildModel(...)`` plus the generated executable directly.

No source-file text patching is performed.

Measurement protocol (legacy package, freq/fr_protocol.py): with
``--sin_mag_auto`` the default ``target_swing`` rule sets a per-frequency
amplitude for a target relative power swing from the committed gain prior
(``data/scenarios/freq/fr_gain_prior.json``), and the default ``prior``
settle rule (``settle_prior_v2``) chooses the settling discard PER
FREQUENCY: the full ``T_full = k / min(zeta*omega_n, sigma_floor)`` (no
cap), or -- where the N-cycle window is at most
``--settle_drift_window_fraction`` of the natural period and ``T_full``
exceeds the slow-mode discard ``k / sigma_floor`` -- the drift regime
(slow-mode discard, window ``phi * 2 pi / omega_n``, linear trend, gain
referenced to the window-mean power).  At those powers, full-regime points
with ``omega >= 10 omega_n`` are lock-in points (``lockin_local_mean_v1``,
freq/lockin.py): the same discard and stop time as the full regime, the
forced fluctuation measured relative to the one-period local mean power so
the slow free mode is rejected.  ``--stop_time_mode
min_cycles_after_ss`` keeps ``--min_cycles_after_ss`` cycles after the
discard.  The regime, discard, fit start, and estimator are recorded per
case in the sweep request and case manifests as the collector's authority.
Cases whose predicted result rows exceed ``--max_case_rows`` are refused
before any simulation; event-sampled cases get an output grid capped at
``--max_output_intervals``.  ``--refine_plan`` runs one refinement round of
``freq.refine_sweep`` (or one approval-check run), refused before any
simulation when it would not carry the parent sweep's identity
(freq/sweep_manifest.py).  ``--settle_force_full`` disables the drift and
lock-in regimes for the drift-regime validation matrix
(helpers/paper-rerun/submit_drift_validation.py).  ``--settle_rule none --sin_mag_auto_rule
inverse_power`` restores the historical behavior.

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
import json
import math
import os
import shlex
import sys
import numpy as np
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import copyfile

try:
    from helpers.power_tags import (
        POWER_TAG_FORMAT_VERSION,
        require_finite,
        require_finite_nonnegative,
        require_finite_positive,
    )
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        table_policy_record,
    )
    from helpers.scenario_config import add_plant_argument, plant_package_refusal
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
        frequency_case_dir_name,
        frequency_file_prefix,
        reduce_csv_to_collect_columns,
        format_override_value,
        load_steady_state_overrides,
    )
    from .paths import default_freq_case_dir, make_power_tag
    from . import fr_protocol
    from . import refinement
    from . import sweep_manifest
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.power_tags import (
        POWER_TAG_FORMAT_VERSION,
        require_finite,
        require_finite_nonnegative,
        require_finite_positive,
    )
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        table_policy_record,
    )
    from helpers.scenario_config import add_plant_argument, plant_package_refusal
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
        frequency_case_dir_name,
        frequency_file_prefix,
        reduce_csv_to_collect_columns,
        format_override_value,
        load_steady_state_overrides,
    )
    from paths import default_freq_case_dir, make_power_tag
    import fr_protocol
    import refinement
    import sweep_manifest


#: Instance that owns the low-power neutron-floor parameters (nFloor,
#: nFloorDuringForcing, nFloorSwitchTime).  The core assemblies bind their
#: mPKE's floor parameters (``mpke(nFloor = nFloor, ...)``), so the former
#: ``<core>.mpke.nFloor*`` keys were "not possible to override" in every
#: low-power run (review-2026-09 logs) and the physics-review override guard
#: (helpers.omc_log.check_overrides_applied) now refuses them; the
#: overridable parameters are the assembly-level ones.
MPKE_PATH_BY_CORE = {
    "1r": "core1R",
    "9r": "msre9r",
}


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path (its
    legacy-compat contract); the fallback keeps script-style execution from
    freq/ working by putting the repo root on sys.path.  The module also owns
    the result-variable contract (TASK-20260908-01 P4): the per-workflow/core
    column sets, the ``-variableFilter`` probe/regex, the post-run projection,
    and the compliance guard.
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
# Physics review 2026-09-27 (B1.3): all nine region elements are table
# overrides (MSRR.R9MSRRuhx binds the arrays directly now, so region 1 is
# overridable; the former [2..9]-only whitelist left region 1 on the shell
# scalars, i.e. the table's core AVERAGE).
for _idx in range(1, 10):
    ALLOWED_OVERRIDE_KEYS.add(f"TF1_0_regions[{_idx}]")
for _idx in range(1, 10):
    ALLOWED_OVERRIDE_KEYS.add(f"TF2_0_regions[{_idx}]")
for _idx in range(1, 10):
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
    ``.mos`` and owns the result-variable contract (TASK-20260908-01 P4).
    A byte change in any of these yields a new fingerprint even when the git
    commit/dirty state is unchanged, so dirty workflow Python can no longer
    share a result slot with committed code.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    helpers_dir = Path(rr.__file__).resolve().parent
    files = [
        here,
        here.parent / "_common.py",
        here.parent / "paths.py",
        here.parent / "sweep_manifest.py",
        here.parent / "fr_protocol.py",
        here.parent / "refinement.py",
        Path(rr.__file__).resolve(),
        helpers_dir / "plant_config.py",
        helpers_dir / "scenario_config.py",
        helpers_dir / "power_tags.py",
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


def _mos_tolerance_text(tolerance: float) -> str:
    """simulate() tolerance literal; the historical ``1E-6`` text is kept
    byte-identical for the default tolerance (P >= 0.01 MW)."""
    value = float(tolerance)
    return "1E-6" if value == 1e-6 else f"{value:.12g}"


def effective_forcing_step(
    freq_point: float,
    *,
    segmented: bool,
    steady_state_overrides: dict | None,
    forcing_time_step: float,
    low_power_mixed_forcing_step: bool,
    low_power_forcing_step: float,
    low_power_forcing_step_hifreq: float,
    low_power_hifreq_split: float,
) -> float:
    """Forcing-window event cadence ``forcingTimeStep`` one case will run with.

    Mirrors the per-case override resolution of :func:`run_single_freq`
    (sweep-level low-power step, then ``--forcing_time_step``, then the
    mixed low/high-frequency cadence); 0 means no forcing events.  Used for
    the output-grid cap and the result-row budget.
    """
    if segmented:
        return 0.0
    step = float((steady_state_overrides or {}).get("forcingTimeStep", 0.0) or 0.0)
    if forcing_time_step > 0:
        step = float(forcing_time_step)
    if low_power_mixed_forcing_step:
        mixed = (
            low_power_forcing_step_hifreq
            if float(freq_point) > low_power_hifreq_split
            else low_power_forcing_step
        )
        if mixed > 0:
            step = float(mixed)
    return float(step)


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


def resolve_fr_protocol(
    args: argparse.Namespace,
    *,
    package: str,
    freq_space,
    resolved_model_name: str | None = None,
) -> dict:
    """Resolve the settling discard and the auto amplitude rule for a sweep.

    The prior-based rules (``--settle_rule prior``, ``--sin_mag_auto_rule
    target_swing``) are calibrated for the legacy lumped 1R/9R vehicles
    only; segmented sweeps resolve to ``none`` / ``inverse_power`` with a
    recorded note.  Raises :class:`fr_protocol.PriorError` when a required
    prior cannot be loaded or does not cover the core.
    """
    segmented = str(package) == SEGMENTED_PACKAGE
    core = str(args.core_model)
    power = float(args.power)
    requested_settle = str(
        getattr(args, "settle_rule", fr_protocol.DEFAULT_SETTLE_RULE)
    )
    requested_amp = str(
        getattr(args, "sin_mag_auto_rule", fr_protocol.DEFAULT_SIN_MAG_RULE)
    )
    notes: list[str] = []
    effective_settle = requested_settle
    effective_amp = requested_amp
    if segmented:
        if requested_settle != fr_protocol.SETTLE_RULE_NONE:
            notes.append(
                "segmented package: the gain prior is calibrated for the "
                "legacy lumped vehicles only; settle rule resolved to 'none'"
            )
        if requested_amp != fr_protocol.SIN_MAG_RULE_INVERSE_POWER and args.sin_mag_auto:
            notes.append(
                "segmented package: target_swing amplitude rule resolved to "
                "'inverse_power' (no segmented gain prior)"
            )
        effective_settle = fr_protocol.SETTLE_RULE_NONE
        effective_amp = fr_protocol.SIN_MAG_RULE_INVERSE_POWER
    amplitude_active = bool(args.sin_mag_auto) and (
        effective_amp == fr_protocol.SIN_MAG_RULE_TARGET_SWING
    )
    prior = None
    if effective_settle == fr_protocol.SETTLE_RULE_PRIOR or amplitude_active:
        prior = fr_protocol.load_gain_prior(
            str(getattr(args, "fr_prior", "") or "") or None
        )
        prior_vehicle = prior.vehicle(core)
        if (
            resolved_model_name
            and prior_vehicle
            and str(resolved_model_name) != str(prior_vehicle)
        ):
            notes.append(
                f"gain prior was measured on {prior_vehicle}; this sweep "
                f"runs {resolved_model_name}"
            )
    if effective_settle == fr_protocol.SETTLE_RULE_PRIOR:
        settle = fr_protocol.settle_decision(
            prior,
            core,
            power,
            e_folds=float(
                getattr(args, "settle_e_folds", fr_protocol.DEFAULT_SETTLE_E_FOLDS)
            ),
            drift_window_fraction=float(
                getattr(
                    args,
                    "settle_drift_window_fraction",
                    fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION,
                )
            ),
            allow_drift=not bool(getattr(args, "settle_force_full", False)),
        )
    else:
        settle = fr_protocol.no_settle()
    target_kwargs = {
        "target_swing": float(
            getattr(args, "target_swing", fr_protocol.DEFAULT_TARGET_SWING)
        ),
        "min_pcm": float(
            getattr(
                args,
                "target_swing_min_pcm",
                fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM,
            )
        ),
        "max_pcm": float(
            getattr(
                args,
                "target_swing_max_pcm",
                fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM,
            )
        ),
    }
    amplitudes = (
        fr_protocol.target_swing_profile(prior, core, power, freq_space, **target_kwargs)
        if amplitude_active
        else None
    )
    return {
        "prior": prior,
        "settle": settle,
        "requested_settle_rule": requested_settle,
        "settle_rule": effective_settle,
        "requested_sin_mag_auto_rule": requested_amp,
        "sin_mag_auto_rule": effective_amp,
        "amplitude_active": amplitude_active,
        "amplitudes": amplitudes,
        "target_kwargs": target_kwargs,
        "sin_mag_scale": float(getattr(args, "sin_mag_scale", 1.0)),
        "notes": notes,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_core_dir = str(repo_root / "core")
    # Shared core-choices table (TASK-20260908-01 P6 item 4); the same
    # lazy-import pattern the scenario-args block below uses.
    try:
        from helpers.scenario_config import CORE_CHOICES
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers.scenario_config import CORE_CHOICES
    parser = argparse.ArgumentParser(
        description="Run MSRR frequency response simulations using MSRRuhxNominalTrim (parallel)."
    )
    parser.add_argument("--core_model", type=str, choices=CORE_CHOICES, default="1r",
                        help="Core segmentation to simulate: 1r, 9r, 1r10seg "
                             "(1-channel x 10-axial-segment 1R core; segmented "
                             "package only -- the legacy package has no "
                             "10-segment vehicle), or r5x5_z10 (5x5-radial x "
                             "10-axial-segment 1R core; segmented package only "
                             "-- the legacy package has no 5x5 vehicle). "
                             "Default: 1r")
    parser.add_argument("--package", type=str, choices=PACKAGE_CHOICES,
                        default=DEFAULT_PACKAGE,
                        help=(
                            "Model family to drive (default: legacy). "
                            "'legacy' loads SMD_MSR_Modelica.mo + MSRR.mo "
                            "nominal-trim models with unchanged behavior; "
                            "'segmented' drives the standalone SegmentedMSR "
                             "full-loop trim rigs (loads "
                             "SegmentedMSR_PlantData.mo then SegmentedMSR.mo), "
                            "requires --power 1.0 (no segmented setpoint tables "
                            "exist yet), ignores steady-state tables, routes "
                            "low-power nFloor overrides to pke.nFloor*, and "
                             "skips forcingTimeStep/heat-loss overrides that the "
                             "rigs do not expose. Homogeneous poison tracking "
                             "is off by default (scenario poisons: block; not "
                             "a spatial poison network). Segmented result CSVs report "
                             "temperatures in kelvin. 1r10seg and r5x5_z10 "
                             "are segmented-package only: the legacy package "
                             "has no 10-segment or 5x5 vehicle and rejects "
                             "--core_model 1r10seg / r5x5_z10 before any omc "
                             "invocation. When the loaded plant's "
                             "outer_fuel_annulus dataset is enabled, the "
                             "segmented 1r10seg sweep runs the plan 10.6 "
                             "CoreVesselAssembly wrapper vehicle -- by "
                             "default SegmentedMSR.Reactors."
                             "R1MSRRuhx10SegOuterAnnulusCoupledSS (both "
                             "core init modes SteadyState, zero "
                             "perturbation), or the shipped-defaults "
                             "SegmentedMSR.Reactors."
                             "R1MSRRuhx10SegOuterAnnulusTrimThermalSS "
                             "twin under --outer-annulus-init-policy "
                             "bounded_startup (TASK-20260923-01 P1) -- "
                             "and the manifest/compact columns carry the "
                             "outer-annulus identity (P8); the requested "
                             "policy rides the fingerprint-active "
                             "outerAnnulusInitPolicy manifest override "
                             "and the effective vehicle rides the "
                             "manifest model_name; the disabled "
                             "shipped dataset keeps the historical vehicles, "
                             "manifests, and columns."
                         ))
    add_plant_argument(parser)
    parser.add_argument("--core_dir", type=str, default=default_core_dir,
                        help="Directory containing core Modelica files (default: core)")
    parser.add_argument(
        "--scenario",
        type=str,
        default="nominal_sweep",
        help=(
            "Frequency YAML id under data/scenarios/freq/ "
            "(default: nominal_sweep; paper_nominal is the 80-point campaign grid)"
        ),
    )
    parser.add_argument(
        "--scenario_file",
        type=str,
        default=None,
        help="Optional scenario YAML overlay for grid/forcing/numerics",
    )
    parser.add_argument(
        "--plant_file",
        type=str,
        default=None,
        help=(
            "Optional plant.yaml path recorded in the run manifest. "
            "Modelica still loads the checked-in PlantData.mo."
        ),
    )
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
            "Choose the perturbation amplitude automatically per "
            "--sin_mag_auto_rule. Default rule (target_swing, legacy "
            "package): per-frequency amplitude "
            "sin_mag = clamp(1e5 * s * P / |G_prior(omega, P)|, "
            "[--target_swing_min_pcm, --target_swing_max_pcm]) pcm so the "
            "predicted relative power swing dn/n0 = |G| drho / P equals "
            "--target_swing s (default 0.01), with |G_prior| from the "
            "committed gain prior (--fr_prior). The inverse_power rule is "
            "the historical sin_mag_ref/power scaling clamped to "
            "[sin_mag_min, sin_mag_max]; under it, very-low-power sweeps "
            f"(power <= {LOW_POWER_ALLFREQ_SIN_MAG_CAP_POWER_DEFAULT:g}) "
            f"are capped at {LOW_POWER_ALLFREQ_SIN_MAG_CAP_DEFAULT:g} pcm for all bins, "
            "and low-power slow bins are additionally capped at "
            f"{LOW_POWER_SLOWFREQ_SIN_MAG_CAP_DEFAULT:g} pcm for "
            f"omega <= {LOW_POWER_SLOWFREQ_SIN_MAG_CAP_FREQ_DEFAULT:g} rad/s."
        ),
    )
    parser.add_argument(
        "--sin_mag_auto_rule",
        type=str,
        choices=fr_protocol.SIN_MAG_RULE_CHOICES,
        default=fr_protocol.DEFAULT_SIN_MAG_RULE,
        help=(
            "Amplitude rule applied by --sin_mag_auto (default: "
            f"{fr_protocol.DEFAULT_SIN_MAG_RULE}). 'target_swing' sets a "
            "per-frequency amplitude for a target relative power swing "
            "from the gain prior (legacy package only; segmented sweeps "
            "keep inverse_power); 'inverse_power' is the historical "
            "sin_mag_ref/power rule with its low-power caps."
        ),
    )
    parser.add_argument(
        "--target_swing",
        type=float,
        default=fr_protocol.DEFAULT_TARGET_SWING,
        help=(
            "Target relative power swing dn/n0 for the target_swing "
            f"amplitude rule (default: {fr_protocol.DEFAULT_TARGET_SWING:g})."
        ),
    )
    parser.add_argument(
        "--target_swing_min_pcm",
        type=float,
        default=fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM,
        help=(
            "Lower amplitude clamp of the target_swing rule in pcm "
            f"(default: {fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM:g})."
        ),
    )
    parser.add_argument(
        "--target_swing_max_pcm",
        type=float,
        default=fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM,
        help=(
            "Upper amplitude clamp of the target_swing rule in pcm "
            f"(default: {fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM:g})."
        ),
    )
    parser.add_argument(
        "--fr_prior",
        type=str,
        default="",
        help=(
            "Gain-prior JSON used by the target_swing amplitude rule and "
            "the prior settle rule (default: "
            f"data/scenarios/freq/{fr_protocol.DEFAULT_PRIOR_FILENAME}). "
            "Its path and SHA-256 are recorded in the sweep request and "
            "per-case manifests."
        ),
    )
    parser.add_argument(
        "--sin_mag_scale",
        type=float,
        default=1.0,
        help=(
            "Multiply every resolved perturbation amplitude by this factor "
            "(default: 1.0). Use 0.5 for the half-amplitude linearity "
            "check (freq/linearity_check.py)."
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
            "Minimum number of sine cycles in the fit window when "
            "--stop_time_mode=min_cycles_after_ss (default: 12.0). The fit "
            "window opens after the settling discard (--settle_rule), so "
            "the stop time is max(stop_time, ss_time + T_d + N*2*pi/omega)."
        ),
    )
    parser.add_argument(
        "--settle_rule",
        type=str,
        choices=fr_protocol.SETTLE_RULE_CHOICES,
        default=fr_protocol.DEFAULT_SETTLE_RULE,
        help=(
            "Settling discard after the perturbation start (default: "
            f"{fr_protocol.DEFAULT_SETTLE_RULE}). 'prior' (legacy package, "
            "rule settle_prior_v2): per frequency, the full discard "
            "T_full = ceil(k / min(zeta*omega_n, sigma_floor)) (no cap) or, "
            "where the N-cycle window is at most --settle_drift_window_fraction "
            "of the natural period and T_full exceeds the slow-mode discard "
            "T_floor = ceil(k / sigma_floor), the drift regime (T_floor "
            "discard, window fraction*2*pi/omega_n, linear trend, gain "
            "referenced to the window-mean power). zeta*omega_n and "
            "sigma_floor come from the gain prior, k = --settle_e_folds; the "
            "per-case regime, discard, and fit start are recorded in the "
            "sweep request and case manifests as the collector's authority. "
            "'none' keeps the historical fit start at the perturbation start "
            "(segmented sweeps always use 'none')."
        ),
    )
    parser.add_argument(
        "--settle_e_folds",
        type=float,
        default=fr_protocol.DEFAULT_SETTLE_E_FOLDS,
        help=(
            "Decay e-folds k of the settling discards T_full and T_floor "
            f"(default: {fr_protocol.DEFAULT_SETTLE_E_FOLDS:g}; minimum "
            f"{fr_protocol.MIN_SETTLE_E_FOLDS:g})."
        ),
    )
    parser.add_argument(
        "--settle_drift_window_fraction",
        type=float,
        default=fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION,
        help=(
            "Drift regime threshold phi: a point is in the drift regime when "
            "its N-cycle fit window is at most phi natural periods "
            "(omega >= (N/phi)*omega_n); its window is then phi*2*pi/omega_n "
            f"(default: {fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION:g}, "
            f"maximum {fr_protocol.MAX_DRIFT_WINDOW_FRACTION:g})."
        ),
    )
    parser.add_argument(
        "--settle_force_full",
        action="store_true",
        help=(
            "Disable the drift and lock-in regimes: every point gets the "
            "full discard T_full, no trend term, the sine-fit estimator, and "
            "the nominal-power gain reference. "
            "For the drift-regime validation matrix "
            "(helpers/paper-rerun/submit_drift_validation.py); recorded as "
            "drift_regime_disabled in the sweep request and case manifests. "
            "Not a campaign setting."
        ),
    )
    parser.add_argument(
        "--tolerance_rule",
        type=str,
        choices=fr_protocol.TOLERANCE_RULE_CHOICES,
        default=fr_protocol.DEFAULT_TOLERANCE_RULE,
        help=(
            "Solver relative tolerance rule for legacy sweeps (default: "
            f"{fr_protocol.DEFAULT_TOLERANCE_RULE}). 'power_scaled_v2': "
            f"tolerance = max({fr_protocol.TOLERANCE_V2_FLOOR:g}, min(--solver_tolerance [1e-8], "
            "1e-4 * target_swing * P)) = min(1e-8, 1e-6 * P) at the 1 %% "
            "target, so DASSL's absolute error scale for the neutron-population "
            "state (tolerance x nominal 1) stays at 1e-4 of the target swing "
            "(1e-8 for P >= 0.01 MW, 1e-9 at 1e-3 MW, the floor below); halved "
            "and check cases scale it with their target swing, clamped at the "
            "floor. 'power_scaled' (v1): min(--solver_tolerance [1e-6], 0.01 * "
            "target_swing * P). 'fixed': --solver_tolerance at every power. "
            "Recorded in the sweep request and case manifests."
        ),
    )
    parser.add_argument(
        "--solver_tolerance",
        type=float,
        default=None,
        help=(
            "Base solver relative tolerance (default: "
            f"{fr_protocol.TOLERANCE_V2_BASE:g} for power_scaled_v2, "
            f"{fr_protocol.DEFAULT_BASE_TOLERANCE:g} -- the historical sweep "
            "value -- for power_scaled and fixed). Segmented sweeps keep their "
            "build-time tolerance."
        ),
    )
    parser.add_argument(
        "--max_case_rows",
        type=float,
        default=fr_protocol.DEFAULT_MAX_CASE_ROWS,
        help=(
            "Refuse the sweep (exit 2, before any simulation) when a case's "
            "predicted result rows (output grid + 2 rows per forcing event) "
            f"exceed this budget (default: {fr_protocol.DEFAULT_MAX_CASE_ROWS:g})."
        ),
    )
    parser.add_argument(
        "--max_output_intervals",
        type=int,
        default=fr_protocol.DEFAULT_MAX_OUTPUT_INTERVALS,
        help=(
            "Cap on numberOfIntervals for cases whose fit window is sampled "
            "by forcing events (low-power forcing cadence active); the "
            "output grid then only resolves the pre-forcing settle "
            f"(default: {fr_protocol.DEFAULT_MAX_OUTPUT_INTERVALS}; <=0 "
            "disables the cap)."
        ),
    )
    parser.add_argument(
        "--refine_plan",
        type=str,
        default=None,
        help=(
            "Refinement round plan JSON written by freq.refine_sweep: run "
            "only the listed frequencies of the parent sweep with the "
            "listed (longer) settling discards into the round directory "
            "<parent>/refine/round_NN (the --base_dir). The round's "
            "immutable sweep request records the plan and the parent "
            "request fingerprint; all other arguments must be those of the "
            "parent sweep."
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
            "default to 00runs/segmented/freq/<core_model>/power_<tag> and "
            "refuse a --base_dir inside the published 00runs/freq/, "
            "00runs/startup-* or 00runs/transients-* trees)"
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
    parser.add_argument(
        "--allow-unlisted-core",
        "--allow_unlisted_core",
        dest="allow_unlisted_core",
        action="store_true",
        help=(
            "Deliberately sweep a (scenario, core_model) combination the "
            "scenario YAML does not list in applies_to. The override is "
            "recorded in the sweep and per-case manifests "
            "(allow_unlisted_core) instead of being silent."
        ),
    )
    parser.add_argument(
        "--allow-unreviewed-poison-data",
        "--allow_unreviewed_poison_data",
        dest="allow_unreviewed_poison_data",
        action="store_true",
        help=(
            "Development override: allow a poison-on sweep (tracking or "
            "feedback) whose authored poison dataset maturity is not "
            "approved for production or publication use (the committed "
            "dataset is reduced_order_pending_review). The override is "
            "recorded in the per-case manifests "
            "(allow_unreviewed_poison_data) instead of being silent. "
            "Poison-off sweeps do not need it."
        ),
    )
    parser.add_argument(
        "--full-result-output",
        "--full_result_output",
        dest="full_result_output",
        action="store_true",
        help=(
            "Diagnostic only (segmented package): write the FULL wide "
            "result CSV (every state/derivative/parameter column -- "
            "15,000+ columns on the 5x5 core) for every frequency instead "
            "of the default result-variable contract (time + the physical "
            "outputs, acceptance diagnostics, and provenance taps the "
            "workflows consume). The selected set is recorded in every "
            "per-case manifest (result_variables / result_output_mode). "
            "Rejected with --package legacy, which keeps its historical "
            "wide output (use --reduced_csv_for_collect there)."
        ),
    )
    parser.add_argument(
        "--outer-annulus-init-policy",
        "--outer_annulus_init_policy",
        dest="outer_annulus_init_policy",
        type=str,
        default=None,
        choices=("coupled_steady_state", "bounded_startup"),
        help=(
            "Outer-annulus initialization policy (segmented package, "
            "enabled 1r10seg dataset only; TASK-20260923-01 P1): "
            "'coupled_steady_state' (default) executes the dedicated "
            "CoupledSS production vehicle (SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusCoupledSS: both core init modes "
            "SteadyState, zero perturbation); 'bounded_startup' executes "
            "the shipped-defaults TrimThermalSS twin (SegmentedMSR."
            "Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS: "
            "FixedStart core cells, 1 pcm sine). The requested policy "
            "is recorded in the run manifest as the fingerprint-active "
            "outerAnnulusInitPolicy override and the effective vehicle "
            "rides the manifest model_name."
        ),
    )
    args = parser.parse_args(argv)
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    try:
        try:
            from helpers.scenario_config import (
                apply_frequency_scenario_args,
                resolve_scenario,
            )
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from helpers.scenario_config import (
                apply_frequency_scenario_args,
                resolve_scenario,
            )

        refusal = plant_package_refusal(args.plant, str(getattr(args, "package", DEFAULT_PACKAGE)))
        if refusal:
            parser.error(refusal)
        args._scenario_data = resolve_scenario(
            kind="frequency",
            scenario_id=args.scenario,
            scenario_file=args.scenario_file,
            plant=args.plant,
        )
        apply_frequency_scenario_args(
            args,
            args._scenario_data,
            argv if argv is not None else sys.argv[1:],
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if args.base_dir is None:
        args.base_dir = str(
            default_freq_case_dir(
                repo_root,
                core_model=args.core_model,
                power=args.power,
                package=str(getattr(args, "package", DEFAULT_PACKAGE)),
                **(
                    {}
                    if str(getattr(args, "plant", None) or "msrr") == "msrr"
                    else {"plant": str(args.plant)}
                ),
            )
        )
    return args


def validate_args(args: argparse.Namespace) -> None:
    """Validate sweep arguments before launching simulations."""
    require_finite_positive(args.power, "--power")
    require_finite_positive(args.freq_min, "--freq_min")
    require_finite_positive(args.freq_max, "--freq_max")
    require_finite_positive(args.stop_time, "--stop_time")
    require_finite_nonnegative(args.ss_time, "--ss_time")
    require_finite_positive(args.sin_mag, "--sin_mag")
    require_finite_positive(
        args.output_intervals_per_second, "--output_intervals_per_second"
    )
    for name in (
        "min_cycles_after_ss",
        "output_samples_per_period",
        "output_step_max",
        "sin_mag_ref",
        "sin_mag_min",
        "sin_mag_max",
        "sin_mag_low",
        "sin_mag_high",
        "low_power_allfreq_sin_mag_cap",
        "low_power_allfreq_sin_mag_cap_power",
        "low_power_slowfreq_sin_mag_cap",
        "low_power_slowfreq_sin_mag_cap_freq",
        "low_power_auto_ss_time_factor",
        "low_power_auto_stop_tail_max",
        "low_power_auto_min_cycles_after_ss",
        "low_power_hifreq_split",
        "forcing_time_step",
        "low_power_nfloor_pre",
        "low_power_nfloor_forcing",
        "omc_timeout_seconds",
        "tolerance",
    ):
        if hasattr(args, name) and getattr(args, name) is not None:
            require_finite(getattr(args, name), f"--{name}")
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
    # FR protocol knobs (settling discard + target-swing amplitude).  Read
    # through getattr so programmatic Namespaces built before these flags
    # existed keep validating.
    for name, default in (
        ("settle_e_folds", fr_protocol.DEFAULT_SETTLE_E_FOLDS),
        ("settle_drift_window_fraction", fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION),
        ("max_case_rows", fr_protocol.DEFAULT_MAX_CASE_ROWS),
        ("target_swing", fr_protocol.DEFAULT_TARGET_SWING),
        ("target_swing_min_pcm", fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM),
        ("target_swing_max_pcm", fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM),
        ("sin_mag_scale", 1.0),
    ):
        require_finite(getattr(args, name, default), f"--{name}")
    if float(getattr(args, "settle_e_folds", fr_protocol.DEFAULT_SETTLE_E_FOLDS)) < (
        fr_protocol.MIN_SETTLE_E_FOLDS
    ):
        raise ValueError(
            f"--settle_e_folds must be >= {fr_protocol.MIN_SETTLE_E_FOLDS:g} "
            "(the protocol requires T_d >= 3/(zeta*omega_n))."
        )
    drift_fraction = float(
        getattr(
            args,
            "settle_drift_window_fraction",
            fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION,
        )
    )
    if not 0.0 < drift_fraction <= fr_protocol.MAX_DRIFT_WINDOW_FRACTION:
        raise ValueError(
            "--settle_drift_window_fraction must be in (0, "
            f"{fr_protocol.MAX_DRIFT_WINDOW_FRACTION:g}]."
        )
    if float(getattr(args, "max_case_rows", fr_protocol.DEFAULT_MAX_CASE_ROWS)) <= 0:
        raise ValueError("--max_case_rows must be > 0.")
    if getattr(args, "solver_tolerance", None) is None:
        args.solver_tolerance = fr_protocol.default_base_tolerance(
            str(getattr(args, "tolerance_rule", fr_protocol.DEFAULT_TOLERANCE_RULE))
        )
    base_tolerance = float(args.solver_tolerance)
    if not (math.isfinite(base_tolerance) and 0.0 < base_tolerance < 1e-2):
        raise ValueError("--solver_tolerance must be finite and in (0, 1e-2).")
    target_swing = float(getattr(args, "target_swing", fr_protocol.DEFAULT_TARGET_SWING))
    if not 0.0 < target_swing <= 0.5:
        raise ValueError("--target_swing must be in (0, 0.5].")
    swing_min = float(
        getattr(args, "target_swing_min_pcm", fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM)
    )
    swing_max = float(
        getattr(args, "target_swing_max_pcm", fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM)
    )
    if swing_min <= 0 or swing_max <= 0 or swing_min > swing_max:
        raise ValueError(
            "--target_swing_min_pcm and --target_swing_max_pcm must be > 0 "
            "with min <= max."
        )
    if float(getattr(args, "sin_mag_scale", 1.0)) <= 0:
        raise ValueError("--sin_mag_scale must be > 0.")
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    if getattr(args, "full_result_output", False) and package != SEGMENTED_PACKAGE:
        # The wide output IS the legacy default; the diagnostic flag exists
        # only to escape the segmented result-variable contract (P4).
        raise ValueError(
            "--full_result_output is only meaningful with --package "
            "segmented: legacy frequency sweeps keep their historical "
            "wide output (use --reduced_csv_for_collect to reduce it)."
        )
    if (
        getattr(args, "outer_annulus_init_policy", None) is not None
        and package != SEGMENTED_PACKAGE
    ):
        # The initialization-policy lever selects an outer-annulus
        # production vehicle; there is no legacy vehicle to select.
        raise ValueError(
            "--outer-annulus-init-policy is only meaningful with --package "
            "segmented: legacy frequency sweeps keep their historical "
            "vehicles."
        )
    if package == SEGMENTED_PACKAGE:
        if getattr(args, "base_dir", None):
            # Review 2026-10-01 M6: 00runs/freq/ is the published pre-fix
            # record; segmented sweeps (default 00runs/segmented/freq/...)
            # never write into it.
            from helpers.published_tree_guard import refuse_published_tree_output

            refuse_published_tree_output(args.base_dir, flag="--base_dir")
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
                "select the vehicle with --core_model "
                "{1r,9r,1r10seg,r5x5_z10}."
            )
        if getattr(args, "reduced_csv_for_collect", False):
            raise ValueError(
                "--reduced_csv_for_collect is unsupported with --package "
                "segmented: the result-variable contract is already compact "
                "(the collector reads its nOut column), and the legacy "
                "reduction would keep the PowerBlock W column instead."
            )
        if (
            not getattr(args, "full_result_output", False)
            and "-variableFilter" in str(getattr(args, "simflags_extra", "") or "")
        ):
            raise ValueError(
                "--simflags_extra must not carry -variableFilter with "
                "--package segmented: the result-variable contract owns "
                "output filtering (use --full_result_output for the "
                "unfiltered wide output)."
            )


def _captured_text(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _segmented_exe_guard_error(exe_result: object, *, label: str) -> str | None:
    """Run guard of the segmented executable stage (review 2026-10-01 M1).

    The lumped route checks the omc ``simulate()`` log with
    :func:`helpers.omc_log.check_overrides_applied`; on the segmented route
    the simulation runs in the generated executable, so its captured output
    is checked the same way: a runtime override the executable dropped
    ("not found" / not overridable) or a fatal assertion violation rejects
    the case even though the pinned build exits 0. Returns the refusal
    message, or ``None`` when the run passes.
    """
    from helpers.omc_log import check_overrides_applied

    try:
        check_overrides_applied(
            _captured_text(getattr(exe_result, "stdout", ""))
            + "\n"
            + _captured_text(getattr(exe_result, "stderr", "")),
            label=label,
        )
    except RuntimeError as exc:
        return str(exc)
    return None


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


def _case_common_overrides(
    provenance_context: dict,
    steady_state_overrides: dict | None,
    *,
    ss_time: float,
    reference_freq: float,
) -> dict:
    """The override set every case of the sweep records (request identity).

    Built with the same code path as the per-case manifests
    (:func:`build_freq_case_manifest`, local overrides as in
    :func:`run_single_freq`), minus the per-frequency keys
    (``perturbationOmega``, ``forcingTimeStep``).
    """
    local_overrides: dict[str, float | bool] = dict(steady_state_overrides or {})
    local_overrides.pop("forcingTimeStep", None)
    if provenance_context.get("poison_tracking"):
        local_overrides["enablePoisonTracking"] = True
        local_overrides["enablePoisonFeedback"] = bool(
            provenance_context.get("poison_feedback")
        )
    manifest = build_freq_case_manifest(
        provenance_context,
        freq_point=float(reference_freq),
        sin_mag=1.0,
        ss_time=float(ss_time),
        stop_time=float(ss_time) + 1.0,
        number_of_intervals=1,
        local_overrides=local_overrides,
        detailed_state_init_weight_applied=any(
            key in local_overrides for key in HX_DETAILED_STATE_OVERRIDE_KEYS
        ),
    )
    overrides = dict(manifest["overrides"])
    for name in sweep_manifest.CASE_VARYING_OVERRIDES:
        overrides.pop(name, None)
    return overrides


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
    # Per-case solver tolerance (tolerance_swing_scaled_v2) when the sweep's
    # FR record carries one; the sweep tolerance otherwise.
    case_tolerance = float(provenance_context["tolerance"])
    fr_case_record = (provenance_context.get("fr_protocol_by_key") or {}).get(
        format_frequency_key(float(freq_point))
    )
    if isinstance(fr_case_record, dict) and fr_case_record.get("solver_tolerance_case"):
        case_tolerance = float(fr_case_record["solver_tolerance_case"]["tolerance"])
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
    if provenance_context.get("allow_unlisted_core", False):
        # Recorded only when set (absence == no override), so default-path
        # manifests keep their historical shape and fingerprint.
        overrides["allow_unlisted_core"] = True
    if detailed_state_init_weight_applied:
        overrides["heatExchanger.detailedStateInitWeight"] = 1
    if local_overrides:
        overrides.update(local_overrides)
    poison_fields = provenance_context.get("poison_fields")
    if poison_fields:
        overrides.update(poison_fields)
    # Requested outer-annulus initialization policy (TASK-20260923-01 P1:
    # the runner-request surface recording which production vehicle
    # executed - CoupledSS vs the bounded-startup twin; the effective
    # vehicle rides the manifest model_name). Recorded only on enabled
    # outer-annulus runs (absence == disabled default), fingerprint-active
    # so results are never reused across initialization policies.
    outer_annulus_init_policy = provenance_context.get(
        "outer_annulus_init_policy"
    )
    if outer_annulus_init_policy is not None:
        overrides["outerAnnulusInitPolicy"] = str(outer_annulus_init_policy)
    radial_annular_flow_command = provenance_context.get(
        "radial_annular_flow_command"
    )
    if radial_annular_flow_command is not None:
        # Effective top-level annularFlowCommand of the circulating radial
        # run (P1 constant-only interface; helper-derived default 1, or a
        # local_overrides value when the case names the parameter) --
        # recorded in the overrides provenance only on circulating runs
        # (absence == no annular-flow connector; TASK-20260916-01 P5).
        payload_command = overrides.get("annularFlowCommand")
        if payload_command is None:
            overrides["annularFlowCommand"] = float(radial_annular_flow_command)
        elif isinstance(payload_command, (int, float, str)):
            overrides["annularFlowCommand"] = float(payload_command)

    manifest = rr.build_run_manifest(
        package_name=_result_package_name(str(provenance_context["model_name"])),
        model_name=str(provenance_context["model_name"]),
        source_files=provenance_context["source_files"],
        workflow_python_files=_workflow_python_sources(
            provenance_context["package"]
        ),
        overrides=overrides,
        solver=str(provenance_context["solver"]),
        tolerance=case_tolerance,
        start_time=0.0,
        stop_time=float(stop_time),
        number_of_intervals=int(number_of_intervals),
        output_grid=str(provenance_context["output_grid_label"]),
        perturbation_amplitude=float(sin_mag),
        setpoint_table_path=provenance_context.get("setpoint_table_path"),
        setpoint_policy=provenance_context.get("setpoint_policy"),
        setpoint_policy_exception=provenance_context.get(
            "setpoint_policy_exception"
        ),
        backend="local",
        omc_version=str(provenance_context["omc_version"]),
        git_info=provenance_context["git_info"],
        core_maturity=provenance_context.get("core_maturity"),
        core_physical_data_maturity=provenance_context.get("core_physical_data_maturity"),
        radial_config=provenance_context.get("radial_config"),
        outer_annulus_config=provenance_context.get("outer_annulus_config"),
        result_variables=provenance_context.get("result_variables"),
        result_output_mode=provenance_context.get("result_output_mode"),
        workflow_version=_provenance_workflow_version(),
    )
    request_reference = provenance_context.get("sweep_request")
    if request_reference is not None:
        manifest["sweep_request"] = dict(request_reference)
    # FR protocol record (settling discard / fit start authority and the
    # amplitude decision), present only when the sweep wired one (legacy
    # package); absent keys keep the historical manifest shape.
    fr_records = provenance_context.get("fr_protocol_by_key")
    if fr_records:
        record = fr_records.get(format_frequency_key(float(freq_point)))
        if record is not None:
            manifest["fr_protocol"] = json.loads(json.dumps(record))
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
    result_variables: "list[str] | tuple[str, ...] | None" = None,
    runtime_filter_applied: bool = False,
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

    Result-output policy (TASK-20260908-01 P4): when ``result_variables`` is
    given (contract mode) the published file must be EXACTLY contract-shaped
    (missing + extra column checks on the written header): a runtime filter
    that silently failed (POSIX-ERE pattern pitfall -> full wide output) or
    that emitted alias-companion extras is repaired by the post-run
    projection -- the raw wide bytes are replaced in place (regenerable via
    ``--full-result-output``) -- and a runtime without filter support goes
    straight to that projection.  The realized mechanism is surfaced in the
    run logs; the selected set itself is in the per-case manifest.
    """
    raw_csv_path = raw_csv_path or csv_path
    if reduced_csv_for_collect:
        reduce_csv_to_collect_columns(raw_csv_path)
    if result_variables is not None:
        rc = _segmented_helpers()
        missing = rc.missing_contract_columns(raw_csv_path, result_variables)
        extras = rc.extra_contract_columns(raw_csv_path, result_variables)
        if extras and not missing:
            # Repair path: the runtime filter silently failed (full wide
            # output) or emitted alias-companion extras -- project the file
            # onto the contract columns in place so the published result is
            # exactly contract-shaped (raw wide bytes are replaced --
            # regenerable via --full-result-output).
            if runtime_filter_applied:
                print(
                    f"  [{work_path}] runtime filter output not "
                    f"contract-shaped ({len(extras)} extra columns); "
                    "downgrading to post-run projection."
                )
            report = rc.project_contract_csv(raw_csv_path, result_variables)
            print(f"  [{work_path}] {report.describe()}")
            missing = rc.missing_contract_columns(raw_csv_path, result_variables)
            extras = rc.extra_contract_columns(raw_csv_path, result_variables)
        if missing or extras:
            result["error"] = (
                "contract-mode result CSV is not contract-shaped: "
                f"missing={missing}; extras={extras[:8]}"
                + (
                    f" (+{len(extras) - 8} more)"
                    if len(extras) > 8
                    else ""
                )
                + f"; see {work_path}"
            )
            return False

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
    plant_data_src: str = "",
    plant_id: str = "msrr",
    provenance_context: dict | None = None,
    quarantine_root: str | None = None,
    claim_timeout_s: float | None = None,
    tolerance: float = 1e-6) -> dict:
    result = {
        "freq": freq_point,
        "success": False,
        "error": None,
        "warning": None,
        "forcing_step": None,
    }

    claim = None
    try:
        work_path = os.path.join(base_dir, frequency_case_dir_name(freq_point))
        os.makedirs(work_path, exist_ok=True)

        file_prefix = frequency_file_prefix(freq_point)
        csv_path = os.path.join(work_path, f"{file_prefix}_res.csv")

        # Per-case override resolution must precede the reuse decision: the
        # effective override set (including case-specific forcing cadence)
        # is part of the request fingerprint.
        is_segmented = package == SEGMENTED_PACKAGE
        local_overrides: dict[str, float | bool] = dict(steady_state_overrides or {})
        if provenance_context and provenance_context.get("poison_tracking"):
            local_overrides["enablePoisonTracking"] = True
            local_overrides["enablePoisonFeedback"] = bool(
                provenance_context.get("poison_feedback")
            )
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

        # SegmentedMSR: copy PlantData then SegmentedMSR.mo (no SMD load).
        if is_segmented:
            from helpers.plant_config import copy_segmented_sources

            copy_segmented_sources(
                Path(segmented_library_src).parent, work_path, plant_id=plant_id
            )
        else:
            from helpers.plant_config import (
                LUMPED_PLANT_DATA_FILE,
                lumped_plant_data_path,
            )

            plant_src = plant_data_src or str(
                lumped_plant_data_path(Path(smd_library_src).parent)
            )
            copyfile(plant_src, os.path.join(work_path, LUMPED_PLANT_DATA_FILE))
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
            # directly afterwards; PlantData then SegmentedMSR (no SMD load,
            # no double-load of SegmentedMSR).
            seg = _segmented_helpers()
            instance = seg.modelica_instance(
                model_name,
                poison_tracking=bool(
                    (provenance_context or {}).get("poison_tracking")
                ),
                poison_feedback=bool(
                    (provenance_context or {}).get("poison_feedback")
                ),
            )
            run_script = (
                "// SMD-MSRR-dev Frequency Response (SegmentedMSR)\n"
                + "\n".join(seg.library_load_lines())
                + "\n"
                + f"buildModel({instance}, tolerance = {SEGMENTED_BUILD_TOLERANCE:g});\n"
                + "getErrorString();\n"
            )
            # Review 2026-10-01 M1: the poison switches are build-bound
            # (Evaluate = true; bound in the buildModel instance above). The
            # manifest keeps the requested override set; the executable
            # receives only the keys it can apply, and a requested value
            # that differs from the compiled one raises here, before omc.
            exe_simflags = build_simflags(
                seg.runtime_override_payload(override, build_script=run_script),
                simflags_extra=simflags_extra,
                reduced_csv_for_collect=reduced_csv_for_collect,
            )
        else:
            from helpers.plant_config import LUMPED_PLANT_DATA_FILE, lumped_load_file_text

            run_script = (
                f'// SMD-MSRR-dev Frequency Response\n'
                + lumped_load_file_text(
                    [LUMPED_PLANT_DATA_FILE, smd_library, msrr_model]
                )
                + f'simulate({model_name},'
                f'startTime=0,'
                f'stopTime={stop_time:.17g},'
                f'numberOfIntervals={number_of_intervals},'
                f'tolerance={_mos_tolerance_text(tolerance)},'
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

        if omc_result.returncode == 0 and not is_segmented:
            # Physics review 2026-09-27 (B1): refuse a case whose setpoint-table
            # overrides OMC silently dropped (formerly the 9R Tmix_0, "not
            # found" in every run). heatLossEnabled and *.initMode are
            # structural and tolerated (helpers.omc_log). The segmented route
            # only builds here; its executable stage runs the same check
            # (_segmented_exe_guard_error, review 2026-10-01 M1).
            from helpers.omc_log import check_overrides_applied

            with open(stdout_log, errors="ignore") as _out, open(stderr_log, errors="ignore") as _err:
                _omc_text = _out.read() + _err.read()
            try:
                check_overrides_applied(_omc_text, label=f"freq case {work_path}")
            except RuntimeError as exc:
                result["error"] = str(exc)
                return result

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
                # Result-output policy (P4): probe the executable's runtime
                # for -variableFilter support.  Supported -> the runtime
                # writes the compact contract output directly; unsupported
                # -> _publish_and_accept_result projects the wide output
                # post-run.  Both mechanisms publish identical columns.
                case_result_variables = (
                    (provenance_context or {}).get("result_variables") or None
                )
                runtime_filter_applied = False
                exe_filter: str | None = None
                if case_result_variables is not None:
                    rc = _segmented_helpers()
                    if rc.executable_supports_variable_filter(exe_path):
                        runtime_filter_applied = True
                        exe_filter = rc.variable_filter_regex(
                            case_result_variables
                        )
                        print(
                            f"freq: output mechanism "
                            f"{rc.MECHANISM_RUNTIME_VARIABLE_FILTER} "
                            f"({len(case_result_variables)} contract variables)"
                        )
                    else:
                        print(
                            f"freq: output mechanism "
                            f"{rc.MECHANISM_POST_RUN_PROJECTION} "
                            f"({len(case_result_variables)} contract variables)"
                        )
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
                    *shlex.split(exe_simflags),
                ]
                if runtime_filter_applied:
                    exe_cmd.append(f"-variableFilter={exe_filter}")
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
                elif (
                    guard_error := _segmented_exe_guard_error(
                        exe_result, label=f"freq case {work_path}"
                    )
                ) is not None:
                    result["error"] = guard_error
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
                        result_variables=case_result_variables,
                        runtime_filter_applied=runtime_filter_applied,
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


def _legacy_core_refusal(args: argparse.Namespace) -> str | None:
    """Name a core that has no legacy vehicle, before any omc invocation.

    TASK-20260906-01 P3: ``1r10seg`` exists only in the standalone
    SegmentedMSR package; the legacy SMD_MSR_Modelica.mo/MSRR.mo library
    ships no 10-segment nominal-trim vehicle. A legacy-mode request for
    such a core hard-errors at the top of :func:`main` (named message,
    ``SystemExit(2)``) instead of failing later on a raw ``KeyError`` in
    ``MODEL_NAME_BY_CORE``. TASK-20260906-02 P3 adds the same refusal for
    ``r5x5_z10`` (the legacy package ships no 5x5 nominal-trim vehicle).
    Returns the refusal message, or ``None`` when the core has a legacy
    vehicle (segmented mode is never refused).

    TASK-20260908-01 P6 item 4: the membership decision and the message
    text are delegated to the shared helpers in ``helpers/scenario_config``
    (same strings; the clause table and message shape live there once).
    """
    if str(getattr(args, "package", DEFAULT_PACKAGE)) == SEGMENTED_PACKAGE:
        return None
    try:
        from helpers.scenario_config import legacy_core_refusal
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers.scenario_config import legacy_core_refusal
    return legacy_core_refusal(
        str(args.core_model),
        MODEL_NAME_BY_CORE,
        flag="--core_model",
        vehicle_noun="nominal-trim vehicle",
        legacy_group_noun="legacy freq vehicles",
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    validate_args(args)
    refusal = _legacy_core_refusal(args)
    if refusal is not None:
        print(f"ERROR: {refusal}")
        raise SystemExit(2)
    # Scenario applies_to enforcement (TASK-20260908-01 P2 item 4): the
    # selected core must be listed in the scenario deck (after the
    # CLI-to-YAML key conversion) BEFORE any simulation or external process.
    scenario_data = getattr(args, "_scenario_data", None)
    if scenario_data is not None:
        try:
            from helpers.scenario_config import scenario_core_refusal
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from helpers.scenario_config import scenario_core_refusal
        scenario_refusal = scenario_core_refusal(
            scenario_data,
            str(args.core_model),
            allow_unlisted=bool(getattr(args, "allow_unlisted_core", False)),
        )
        if scenario_refusal is not None:
            print(f"ERROR: {scenario_refusal}")
            raise SystemExit(2)
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    segmented = package == SEGMENTED_PACKAGE
    seg = _segmented_helpers() if segmented else None
    outer_annulus_contract = None
    if segmented:
        # Outer-annulus provenance + runner/core refusal (TASK-20260917-01
        # P8; plan §11.2): derived BEFORE the vehicle selection so an
        # enabled outer_fuel_annulus dataset executes the plan §10.6
        # CoreVesselAssembly wrapper. Fail-closed refusals (9R, an enabled
        # dataset on a vehicle that cannot execute it, an enabled dataset
        # targeting a different core than the runner selected) happen inside
        # the contract, BEFORE the campaign template or any per-case
        # manifest can be published. The disabled/absent dataset returns
        # the all-None contract: historical manifest shape, column set, and
        # vehicle mapping. ONE plant load shared with the radial contract
        # below.
        from helpers.plant_config import load_plant as _load_contract_plant

        contract_plant = _load_contract_plant(str(getattr(args, "plant", None) or "msrr"))
        outer_annulus_contract = seg.outer_fuel_annulus_run_contract(
            str(args.core_model),
            plant=contract_plant,
            init_policy=getattr(args, "outer_annulus_init_policy", None),
        )
        resolved_model_name = (
            outer_annulus_contract.vehicle
            if outer_annulus_contract.enabled
            else seg.model_for(args.core_model)
        )
    else:
        yaml_vehicle = None
        scenario_data = getattr(args, "_scenario_data", None)
        if scenario_data is not None:
            try:
                from helpers.scenario_config import legacy_vehicle
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from helpers.scenario_config import legacy_vehicle
            # Fail closed on a malformed scenario deck: legacy_vehicle
            # returns None only when the scenario specifies no vehicle for
            # this core (the historical map then applies).  A malformed
            # deck raises here instead of silently running a different
            # model under the old broad-except fallback.
            yaml_vehicle = legacy_vehicle(scenario_data, args.core_model)
        resolved_model_name = (
            args.model_name or yaml_vehicle or MODEL_NAME_BY_CORE[args.core_model]
        )
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
    # Refinement round (freq.refine_sweep): the plan fixes the frequency
    # subset and the longer per-point discards; the parent request stays
    # the authority for the case set and amplitudes (freq/refinement.py).
    refine_plan = None
    refine_parent_payload = None
    refine_points: dict[str, dict] = {}
    if getattr(args, "refine_plan", None):
        try:
            refine_plan = refinement.load_plan(args.refine_plan)
            refine_parent_payload = refinement.load_plan_parent(refine_plan)
        except refinement.RefinementError as exc:
            print(f"ERROR: refinement plan: {exc}")
            raise SystemExit(2)
        expected_round_dir = refinement.plan_dir(refine_plan).resolve()
        if Path(os.path.abspath(args.base_dir)).resolve() != expected_round_dir:
            print(
                "ERROR: refinement plan: --base_dir must be the plan's directory "
                f"{expected_round_dir} (got {os.path.abspath(args.base_dir)})"
            )
            raise SystemExit(2)
        refine_points = {
            str(point["frequency_key"]): dict(point)
            for point in refine_plan["points"]
        }
        freq_space = np.asarray(
            sorted(float(point["frequency_rad_s"]) for point in refine_plan["points"]),
            dtype=float,
        )
    # FR protocol (settling discard + target-swing amplitude) is resolved
    # BEFORE any horizon, amplitude, or manifest is fixed: the discard moves
    # the fit start and extends the stop times, and both are recorded in the
    # immutable sweep request as the collector's fit-start authority.
    try:
        fr_resolution = resolve_fr_protocol(
            args,
            package=package,
            freq_space=freq_space,
            resolved_model_name=resolved_model_name,
        )
    except fr_protocol.PriorError as exc:
        print(f"ERROR: frequency-response protocol: {exc}")
        raise SystemExit(2)
    settle = fr_resolution["settle"]
    # Solver tolerance rule (legacy package): power-scaled so the population
    # state's absolute error scale stays below the target swing; recorded
    # in the request (numerics.tolerance, policies.fr_protocol) and in every
    # case manifest (tolerance, fingerprint-active).
    if segmented:
        tolerance_record = {
            "rule": "segmented_build_time",
            "rule_id": None,
            "tolerance": float(SEGMENTED_BUILD_TOLERANCE),
        }
    else:
        try:
            tolerance_record = fr_protocol.solver_tolerance(
                float(args.power),
                rule=str(
                    getattr(args, "tolerance_rule", fr_protocol.DEFAULT_TOLERANCE_RULE)
                ),
                base=(
                    None if getattr(args, "solver_tolerance", None) is None
                    else float(args.solver_tolerance)
                ),
                target_swing=float(
                    getattr(args, "target_swing", fr_protocol.DEFAULT_TARGET_SWING)
                ),
            )
        except fr_protocol.PriorError as exc:
            print(f"ERROR: solver tolerance: {exc}")
            raise SystemExit(2)
    if refine_plan is not None and settle.rule != fr_protocol.SETTLE_RULE_PRIOR:
        print("ERROR: refinement plan: refinement requires --settle_rule prior")
        raise SystemExit(2)
    # Per-frequency settle rule (settle_prior_v2): regime, discard, fit
    # start, stop time, and the collector's estimator per point.
    point_settle_by_freq: dict[float, fr_protocol.PointSettle] = {}
    try:
        for fp in freq_space:
            key = format_frequency_key(float(fp))
            point = refine_points.get(key)
            point_settle_by_freq[float(fp)] = fr_protocol.point_settle(
                settle,
                float(fp),
                ss_time=effective_ss_time,
                base_stop_time=effective_stop_time,
                stop_time_mode=effective_stop_time_mode,
                min_cycles_after_ss=effective_min_cycles_after_ss,
                refine_discard_s=(
                    float(point["settle_discard_s"]) if point is not None else None
                ),
                refine_round=(
                    (
                        int(refine_plan["round"])
                        if refinement.plan_purpose(refine_plan) == refinement.PURPOSE_REFINE
                        else int(point["previous_round"])
                    )
                    if point is not None
                    else 0
                ),
                previous_discard_s=(
                    float(point["previous_settle_discard_s"])
                    if point is not None
                    else None
                ),
            )
    except fr_protocol.PriorError as exc:
        print(f"ERROR: frequency-response protocol: {exc}")
        raise SystemExit(2)
    fit_start_by_freq = {
        fp: point.fit_start_s for fp, point in point_settle_by_freq.items()
    }
    stop_time_by_freq = {
        fp: point.stop_time_s for fp, point in point_settle_by_freq.items()
    }
    empty_windows = [
        float(fp)
        for fp in freq_space
        if stop_time_by_freq[float(fp)] <= fit_start_by_freq[float(fp)]
    ]
    if empty_windows:
        first = point_settle_by_freq[empty_windows[0]]
        print(
            "ERROR: the settling discard leaves no fit window: fit start "
            f"{first.fit_start_s:g} s (perturbation start {effective_ss_time:g} s "
            f"+ discard {first.discard_s:g} s) is at or after the stop time "
            f"of {len(empty_windows)} point(s) (e.g. {empty_windows[0]:g} "
            "rad/s). Use --stop_time_mode min_cycles_after_ss, raise "
            "--stop_time, or pass --settle_rule none."
        )
        raise SystemExit(2)
    fit_cycles_by_freq = {
        fp: point.fit_cycles for fp, point in point_settle_by_freq.items()
    }
    distinct_fit_starts = sorted(set(fit_start_by_freq.values()))
    fit_start_time = float(distinct_fit_starts[0])

    base_dir = os.path.abspath(args.base_dir)
    os.makedirs(base_dir, exist_ok=True)
    steady_state_overrides = None
    using_steady_table = False
    #: Setpoint qualification policy state for the consumed table
    #: (TASK-20260911-01 P4, owner decision 2): policy mode + legacy
    #: exception, recorded in the per-case manifests and run_params.txt.
    #: None when no table was consumed (segmented suppression / disabled /
    #: missing file) -- the record stays paired with setpoint_table_path.
    setpoint_policy_record = None

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
                policy=DEFAULT_POLICY,
            )
            using_steady_table = True
            setpoint_policy_record = table_policy_record(
                steady_state_table, policy=DEFAULT_POLICY
            )
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
    amplitude_decisions = fr_resolution["amplitudes"]
    if fr_resolution["amplitude_active"]:
        # Target-swing rule: per-frequency amplitudes from the gain prior
        # replace the inverse-power base and its low-power caps (the caps
        # were patches for the inverse-power rule; the swing target bounds
        # the nonlinearity directly).
        apply_low_power_allfreq_sin_mag_cap = False
        apply_low_power_slowfreq_sin_mag_cap = False
        sin_mag_by_freq = {
            float(fp): float(amplitude_decisions[float(fp)].sin_mag_pcm)
            for fp in freq_space
        }
        sin_mag = float(max(sin_mag_by_freq.values()))
    sin_mag_scale = float(fr_resolution["sin_mag_scale"])
    if sin_mag_scale != 1.0:
        sin_mag = float(sin_mag) * sin_mag_scale
        if sin_mag_by_freq:
            sin_mag_by_freq = {
                key: float(value) * sin_mag_scale
                for key, value in sin_mag_by_freq.items()
            }

    def _case_amplitude(freq_point: float) -> float:
        return float(
            sin_mag_by_freq.get(float(freq_point), sin_mag)
            if sin_mag_by_freq
            else sin_mag
        )

    # Output grid, forcing cadence, and the per-case row budget.  Cases whose
    # fit window is sampled by forcing events (low-power cadence) get a
    # capped output grid: the grid then only resolves the pre-forcing
    # settle, and a 4e7 s settle at a period/6 step (the review-2026-09
    # 1e-5 MW protocol grid, 7.5e7-3.8e8 intervals) cannot recur.
    forcing_step_by_freq = {
        float(fp): effective_forcing_step(
            float(fp),
            segmented=segmented,
            steady_state_overrides=steady_state_overrides,
            forcing_time_step=args.forcing_time_step,
            low_power_mixed_forcing_step=low_power_mixed_forcing_step,
            low_power_forcing_step=args.low_power_forcing_step,
            low_power_forcing_step_hifreq=args.low_power_forcing_step_hifreq,
            low_power_hifreq_split=args.low_power_hifreq_split,
        )
        for fp in freq_space
    }
    max_output_intervals = int(
        getattr(args, "max_output_intervals", fr_protocol.DEFAULT_MAX_OUTPUT_INTERVALS)
    )
    number_of_intervals_by_freq: dict[float, int] = {}
    output_intervals_capped_by_freq: dict[float, bool] = {}
    for fp in freq_space:
        fp_value = float(fp)
        intervals = compute_number_of_intervals(
            fp_value,
            stop_time=stop_time_by_freq[fp_value],
            output_interval_mode=effective_output_interval_mode,
            output_intervals_per_second=args.output_intervals_per_second,
            output_samples_per_period=args.output_samples_per_period,
            output_step_max=args.output_step_max,
        )
        capped = bool(
            max_output_intervals > 0
            and forcing_step_by_freq[fp_value] > 0
            and intervals > max_output_intervals
        )
        number_of_intervals_by_freq[fp_value] = (
            max_output_intervals if capped else int(intervals)
        )
        output_intervals_capped_by_freq[fp_value] = capped
    predicted_rows_by_freq = {
        float(fp): fr_protocol.predicted_case_rows(
            stop_time_s=stop_time_by_freq[float(fp)],
            ss_time=effective_ss_time,
            number_of_intervals=number_of_intervals_by_freq[float(fp)],
            forcing_step_s=forcing_step_by_freq[float(fp)],
        )
        for fp in freq_space
    }
    max_case_rows = float(
        getattr(args, "max_case_rows", fr_protocol.DEFAULT_MAX_CASE_ROWS)
    )
    over_budget = [
        float(fp) for fp in freq_space if predicted_rows_by_freq[float(fp)] > max_case_rows
    ]
    if over_budget:
        worst = max(over_budget, key=lambda fp: predicted_rows_by_freq[fp])
        print(
            f"ERROR: {len(over_budget)} case(s) exceed the result-row budget "
            f"--max_case_rows {max_case_rows:g} (e.g. {worst:g} rad/s: "
            f"{predicted_rows_by_freq[worst]:.4g} predicted rows = "
            f"{number_of_intervals_by_freq[worst]} output intervals + 2 x "
            f"{fr_protocol.forcing_event_count(stop_time_by_freq[worst], effective_ss_time, forcing_step_by_freq[worst])}"
            f" forcing events over {stop_time_by_freq[worst] - effective_ss_time:g} s "
            "of forcing); nothing was launched."
        )
        raise SystemExit(2)
    if refine_parent_payload is not None:
        parent_request = refine_parent_payload["request"]
        parent_cases = sweep_manifest.case_map(refine_parent_payload)
        identity = {
            "core_model": str(args.core_model),
            "package": package,
            "model_name": resolved_model_name,
            "power": float(args.power),
            "perturbation_start_time_s": float(effective_ss_time),
        }
        differing = [
            name for name, value in identity.items()
            if parent_request.get(name) != value
        ]
        for fp in freq_space:
            key = format_frequency_key(float(fp))
            parent_amp = float(parent_cases[key]["perturbation_amplitude_pcm"])
            if not math.isclose(
                _case_amplitude(float(fp)), parent_amp, rel_tol=1e-9, abs_tol=1e-15
            ):
                differing.append(f"perturbation_amplitude_pcm[{key}]")
        if differing:
            print(
                "ERROR: refinement plan: this invocation differs from the parent "
                "sweep in " + ", ".join(differing) + "; pass the parent's runner "
                "arguments unchanged."
            )
            raise SystemExit(2)
        # Per-point plan amplitudes (measured-gain rescale of a refinement
        # round, or the half amplitude of a linearity check) replace the
        # rule amplitude only after the invocation was shown to reproduce
        # the parent's rule amplitudes above.
        plan_amplitudes = {
            float(fp): refinement.plan_amplitude(
                refine_points[format_frequency_key(float(fp))]
            )
            for fp in freq_space
        }
        if any(value is not None for value in plan_amplitudes.values()):
            sin_mag_by_freq = {
                float(fp): (
                    float(plan_amplitudes[float(fp)])
                    if plan_amplitudes[float(fp)] is not None
                    else _case_amplitude(float(fp))
                )
                for fp in freq_space
            }
            sin_mag = float(max(sin_mag_by_freq.values()))

    # The amplitude mapping always mirrors the executed campaign: a uniform
    # rerun into a reused base_dir previously left a stale nonuniform
    # sin_mag_by_freq.csv behind, which the collector then rejected against
    # the fresh request grid. Rewrite it on every run (uniform rows carry
    # the base amplitude); the immutable sweep request stays authoritative.
    sin_mag_mapping_path = os.path.join(base_dir, "sin_mag_by_freq.csv")
    with open(sin_mag_mapping_path, "w") as handle:
        handle.write("frequency_rad_s,sin_mag\n")
        for f in freq_space:
            amplitude = (
                sin_mag_by_freq[float(f)] if sin_mag_by_freq else sin_mag
            )
            handle.write(
                f"{format_frequency_key(f)},{amplitude:.12g}\n"
            )
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
        from helpers.plant_config import require_segmented_library_paths

        require_segmented_library_paths(
            core_dir, plant_id=str(getattr(args, "plant", None) or "msrr")
        )
        seg_library = seg.SEGMENTED_PACKAGE_FILE
        seg_library_src = os.path.join(core_dir, seg_library)
        smd_library = ""
        msrr_model = ""
        smd_library_src = ""
        msrr_model_src = ""
        plant_data_src = ""
    else:
        from helpers.plant_config import lumped_plant_data_path

        smd_library = "SMD_MSR_Modelica.mo"
        msrr_model = "MSRR.mo"
        smd_library_src = os.path.join(core_dir, smd_library)
        msrr_model_src = os.path.join(core_dir, msrr_model)
        plant_data_src = str(lumped_plant_data_path(core_dir))

        if not os.path.exists(smd_library_src):
            raise FileNotFoundError(
                f"Cannot find {smd_library_src}."
            )
        if not os.path.exists(msrr_model_src):
            raise FileNotFoundError(
                f"Cannot find {msrr_model_src}."
            )
        if not os.path.exists(plant_data_src):
            raise FileNotFoundError(
                f"Cannot find {plant_data_src}."
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
        try:
            from helpers.plant_config import segmented_manifest_source_files

            model_source_files = segmented_manifest_source_files(
                core_dir, plant_id=str(getattr(args, "plant", None) or "msrr")
            )
        except Exception:  # noqa: BLE001 - hermetic tests may lack a plant deck
            model_source_files = [seg_library_src]
    else:
        try:
            from helpers.plant_config import lumped_manifest_source_files

            model_source_files = lumped_manifest_source_files(core_dir)
        except Exception:  # noqa: BLE001 - hermetic tests may lack a plant deck
            model_source_files = [plant_data_src, smd_library_src, msrr_model_src]
    extra_sources = {}
    scenario_data = getattr(args, "_scenario_data", None)
    if scenario_data is not None:
        try:
            from helpers.scenario_config import scenario_source_entry

            extra_sources.update(scenario_source_entry(scenario_data))
        except Exception:  # noqa: BLE001 - provenance must not abort the sweep
            pass
    plant_file = getattr(args, "plant_file", None)
    if plant_file:
        try:
            import yaml

            from helpers.plant_config import fingerprint

            extra_sources[str(plant_file)] = fingerprint(
                yaml.safe_load(Path(plant_file).read_text(encoding="utf-8"))
            )
        except Exception as exc:
            # --plant_file's documented promise is recorded provenance; a
            # silent omission would break the manifest's source list, so a
            # fingerprint failure fails the run (never a silent `pass`).
            print(
                f"ERROR: --plant_file {plant_file} could not be "
                f"fingerprinted for the run manifest: {exc}"
            )
            raise SystemExit(2)
    if extra_sources:
        try:
            from helpers.scenario_config import merge_manifest_sources

            model_source_files = merge_manifest_sources(model_source_files, extra_sources)
        except Exception:  # noqa: BLE001 - provenance must not abort the sweep
            pass
    # Both maturity axes for every run (rev032 review): segmented runs keep
    # the segmented_runs mapping; legacy 1R/9R runs read their core deck
    # (helpers.plant_config.core_maturity_labels) -- segmented_runs stays a
    # segmented-path-only import.  Fingerprint-active manifest fields.
    if segmented:
        maturity_labels = {
            "core_maturity": seg.core_maturity_for(str(args.core_model)),
            "core_physical_data_maturity": seg.core_physical_data_maturity_for(
                str(args.core_model)
            ),
        }
    else:
        from helpers.plant_config import core_maturity_labels

        maturity_labels = core_maturity_labels(str(args.core_model))
    provenance_context = {
        "core_model": str(args.core_model),
        "package": package,
        "model_name": resolved_model_name,
        "power": float(args.power),
        "core_maturity": maturity_labels["core_maturity"],
        "core_physical_data_maturity": maturity_labels["core_physical_data_maturity"],
        "allow_unlisted_core": bool(
            getattr(args, "allow_unlisted_core", False)
        ),
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
        # Setpoint qualification policy state (TASK-20260911-01 P4): the
        # active policy mode and the legacy exception (table lacks the
        # verdict column) for the consumed table -- None when no table was
        # consumed, keeping the record paired with setpoint_table_path.
        "setpoint_policy": (
            setpoint_policy_record["policy"] if setpoint_policy_record else None
        ),
        "setpoint_policy_exception": (
            setpoint_policy_record["legacy_exception"]
            if setpoint_policy_record
            else None
        ),
        "solver": "dassl",
        "tolerance": (
            SEGMENTED_BUILD_TOLERANCE if segmented else float(tolerance_record["tolerance"])
        ),
        "output_grid_label": (
            "segmented_executable_fixed_stepSize_from_intervals"
            if segmented
            else "legacy_simulate_numberOfIntervals"
        ),
        "simflags_extra": str(args.simflags_extra or ""),
        "reduced_csv_for_collect": bool(args.reduced_csv_for_collect),
    }
    # FR protocol record per case (legacy package): the settling discard /
    # fit start (the collector's fit-start authority) and the amplitude
    # decision, fingerprint-active through the per-case manifest.  Segmented
    # manifests keep their historical shape (the prior rules do not apply).
    fr_prior = fr_resolution["prior"]
    fr_prior_reference = fr_prior.reference() if fr_prior is not None else None
    policy_target_swing = (
        float(fr_resolution["target_kwargs"]["target_swing"])
        if fr_resolution["amplitude_active"]
        else None
    )

    def _case_target_swing(key: str) -> tuple[float | None, int]:
        """(effective target swing, halvings) of one case: the plan point's
        record, else the policy target (base sweep, no halving)."""
        point = refine_points.get(key)
        if policy_target_swing is None:
            return None, 0
        if point is not None and "effective_target_swing" in point:
            target = point.get("effective_target_swing")
            return (
                None if target is None else float(target),
                int(point.get("amplitude_halvings") or 0),
            )
        return policy_target_swing, 0

    fr_protocol_by_key: dict[str, dict] | None = None
    case_tolerance_by_key: dict[str, float] = {}
    if not segmented:
        fr_protocol_by_key = {}
        for fp in freq_space:
            fp_value = float(fp)
            key = format_frequency_key(fp_value)
            if amplitude_decisions is not None:
                amplitude_record = amplitude_decisions[fp_value].record(
                    prior=fr_prior_reference or {},
                    **fr_resolution["target_kwargs"],
                )
            else:
                amplitude_record = {
                    "rule": (
                        fr_protocol.SIN_MAG_RULE_INVERSE_POWER
                        if args.sin_mag_auto
                        else "fixed"
                    ),
                }
            amplitude_record["sin_mag_scale"] = sin_mag_scale
            amplitude_record["applied_pcm"] = _case_amplitude(fp_value)
            plan_point = refine_points.get(key)
            if plan_point is not None and plan_point.get("perturbation_amplitude_pcm") is not None:
                amplitude_record["plan_amplitude_rule"] = plan_point.get("amplitude_rule")
                amplitude_record["plan_previous_amplitude_pcm"] = plan_point.get(
                    "previous_amplitude_pcm"
                )
                amplitude_record["plan_measured_swing"] = plan_point.get("measured_swing")
                if plan_point.get("amplitude_rule") == refinement.AMPLITUDE_RULE_RESCALE:
                    amplitude_record["clamped"] = plan_point.get("amplitude_clamped")
                    amplitude_record["rule_id"] = fr_protocol.AMPLITUDE_RESCALE_RULE_ID
                elif (
                    plan_point.get("amplitude_rule") == refinement.AMPLITUDE_RULE_KEEP
                    and "amplitude_clamped" in plan_point
                ):
                    # A kept amplitude keeps the clamp of the case it
                    # replaces (a previous rescale's clamp, not the rule's).
                    amplitude_record["clamped"] = plan_point.get("amplitude_clamped")
            # Effective target swing of the case: the policy target halved
            # once per amplitude halving (freq/README.md, "Amplitude
            # halving"); a plan point names it for the case it produces.
            effective_target, halvings = _case_target_swing(key)
            amplitude_record["effective_target_swing"] = effective_target
            amplitude_record["halvings"] = int(halvings)
            if plan_point is not None and (
                plan_point.get("amplitude_rule") in refinement.HALVING_RULES
                or plan_point.get("amplitude_rule") == refinement.AMPLITUDE_RULE_RESTORE
            ):
                amplitude_record["clamped"] = plan_point.get("amplitude_clamped")
                amplitude_record["rule_id"] = str(plan_point.get("amplitude_rule"))
                amplitude_record["plan_halving_trigger"] = plan_point.get("halving_trigger")
                if plan_point.get("linearity_trigger"):
                    amplitude_record["plan_linearity_trigger"] = plan_point.get(
                        "linearity_trigger"
                    )
            # Per-case solver tolerance (tolerance_swing_scaled_v2): the
            # request tolerance x effective / policy target swing, so the
            # absolute error scale stays the base sweep's fraction of the
            # swing through halvings and half-amplitude checks.
            try:
                case_tolerance, case_scale = fr_protocol.case_solver_tolerance(
                    float(tolerance_record["tolerance"]), policy_target_swing, effective_target,
                    floor=tolerance_record.get("case_floor"),
                )
            except fr_protocol.PriorError as exc:
                print(f"ERROR: case {key}: {exc}")
                raise SystemExit(2)
            case_tolerance_by_key[key] = case_tolerance
            settle_record = settle.record()
            settle_record.update(point_settle_by_freq[fp_value].record())
            settle_record.update(
                {
                    "perturbation_start_s": float(effective_ss_time),
                    "forcing_time_step_s": float(forcing_step_by_freq[fp_value]),
                    "predicted_result_rows": int(predicted_rows_by_freq[fp_value]),
                    "output_intervals_capped": bool(
                        output_intervals_capped_by_freq[fp_value]
                    ),
                }
            )
            fr_protocol_by_key[key] = {
                "schema_version": 2,
                "settle": settle_record,
                "amplitude": amplitude_record,
                "solver_tolerance": dict(tolerance_record),
                "solver_tolerance_case": {
                    "rule_id": (
                        tolerance_record.get("case_rule_id")
                        or fr_protocol.CASE_TOLERANCE_RULE_ID
                    ),
                    "request_tolerance": float(tolerance_record["tolerance"]),
                    "scale": float(case_scale),
                    "tolerance": float(case_tolerance),
                    **(
                        {
                            "floor": float(tolerance_record["case_floor"]),
                            "clamped": bool(
                                case_tolerance
                                > float(tolerance_record["tolerance"]) * case_scale * (1 + 1e-9)
                            ),
                        }
                        if tolerance_record.get("case_floor") is not None
                        else {}
                    ),
                },
            }
        provenance_context["fr_protocol_by_key"] = fr_protocol_by_key
    # Result-output policy (TASK-20260908-01 P4): segmented sweeps default to
    # the result-variable contract; --full-result-output restores the wide
    # output explicitly for diagnostics.  The selected set is recorded in
    # every per-case manifest either way.
    if segmented:
        rc = _segmented_helpers()
        from helpers.scenario_config import poison_run_bindings

        _poison_payload, poison_fields, poison_tracking = poison_run_bindings(
            package,
            scenario_data,
            power_level=float(args.power),
            allow_unreviewed_poison_data=bool(
                getattr(args, "allow_unreviewed_poison_data", False)
            ),
        )
        from helpers.scenario_config import scenario_poison_controls

        poison_ctrl = scenario_poison_controls(scenario_data)
        provenance_context["poison_fields"] = poison_fields
        provenance_context["poison_tracking"] = poison_tracking
        provenance_context["poison_feedback"] = bool(poison_ctrl["feedback"])
        # Radial provenance + runner/core refusal (review rev020 Phase 2;
        # TASK-20260916-01 P5): one helper derives, from the loaded plant
        # and the selected core, the radial manifest record and the
        # result-contract run shape -- and refuses, fail-closed (named
        # configuration path and offending value), a runner/core
        # combination that cannot execute the requested radial mode, BEFORE
        # the campaign template or any per-case manifest can be published.
        # The frequency workflow has no generic annular-flow override
        # channel, so the effective top-level annularFlowCommand is the P1
        # default 1 on circulating runs (recorded per case in the manifest
        # overrides). Disabled cores return the all-None contract, so
        # disabled manifests and column sets keep their historical shape.
        radial = seg.radial_run_contract(
            str(args.core_model), plant=contract_plant
        )
        provenance_context["radial_config"] = radial.manifest_fields
        provenance_context["radial_annular_flow_command"] = (
            radial.annular_flow_command
        )
        # Outer-annulus identity (P8; derived before the vehicle selection
        # above; TASK-20260923-01 P1 policy): the fingerprint-active
        # outer_fuel_annulus manifest record and the §11.3 compact-column
        # shape (both None on disabled cores, so the historical
        # template/case shapes are unchanged), plus the requested
        # initialization policy (None on disabled cores, so disabled
        # manifests keep their historical shape; the policy selects the
        # CoupledSS vs bounded-startup production vehicle, whose name
        # rides the manifest model_name).
        provenance_context["outer_annulus_config"] = (
            outer_annulus_contract.manifest_fields
            if outer_annulus_contract is not None
            else None
        )
        provenance_context["outer_annulus_shape"] = (
            outer_annulus_contract.shape
            if outer_annulus_contract is not None
            else None
        )
        provenance_context["outer_annulus_init_policy"] = (
            outer_annulus_contract.init_policy
            if outer_annulus_contract is not None
            and outer_annulus_contract.enabled
            else None
        )
        if getattr(args, "full_result_output", False):
            provenance_context["result_variables"] = None
            provenance_context["result_output_mode"] = rc.RESULT_OUTPUT_MODE_FULL
        else:
            provenance_context["result_variables"] = list(
                rc.result_variables_for(
                    "freq",
                    str(args.core_model),
                    poison_tracking=poison_tracking,
                    radial_shape=radial.shape,
                    outer_annulus_shape=provenance_context.get(
                        "outer_annulus_shape"
                    ),
                )
            )
            provenance_context["result_output_mode"] = rc.RESULT_OUTPUT_MODE_CONTRACT
    else:
        from helpers.scenario_config import poison_run_bindings

        poison_run_bindings(
            package,
            scenario_data,
            power_level=float(args.power),
            allow_unreviewed_poison_data=bool(
                getattr(args, "allow_unreviewed_poison_data", False)
            ),
        )

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
            **(
                {"allow_unlisted_core": True}
                if provenance_context["allow_unlisted_core"]
                else {}
            ),
        },
        solver=provenance_context["solver"],
        tolerance=provenance_context["tolerance"],
        start_time=0.0,
        output_grid=provenance_context["output_grid_label"],
        setpoint_table_path=provenance_context["setpoint_table_path"],
        backend="local",
        omc_version=provenance_context["omc_version"],
        git_info=provenance_context["git_info"],
        core_maturity=provenance_context["core_maturity"],
        core_physical_data_maturity=provenance_context.get("core_physical_data_maturity"),
        # The campaign request records the radial identity too (None on
        # disabled cores, so the historical template shape is unchanged);
        # the per-case effective annularFlowCommand lives in the per-case
        # manifest overrides (TASK-20260916-01 P5). The outer-annulus
        # dataset identity rides the same pattern (P8).
        radial_config=provenance_context.get("radial_config"),
        outer_annulus_config=provenance_context.get("outer_annulus_config"),
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
                "perturbation_amplitude_pcm": _case_amplitude(float(fp)),
                "stop_time_s": float(stop_time_by_freq[float(fp)]),
                "number_of_intervals": int(
                    number_of_intervals_by_freq[float(fp)]
                ),
                "output_step_s": float(
                    stop_time_by_freq[float(fp)]
                    / number_of_intervals_by_freq[float(fp)]
                ),
                # Fit-start authority (settling discard): the collector
                # opens the fit window here, never earlier.  The regime,
                # trend order, and gain reference select the collector's
                # estimator for this point (settle_prior_v2).
                "fit_start_s": float(fit_start_by_freq[float(fp)]),
                "settle_discard_s": float(point_settle_by_freq[float(fp)].discard_s),
                "settle_rule_discard_s": float(
                    point_settle_by_freq[float(fp)].rule_discard_s
                ),
                "settle_regime": point_settle_by_freq[float(fp)].regime,
                "settle_refine_round": int(
                    point_settle_by_freq[float(fp)].refine_round
                ),
                "fit_trend_order": int(
                    point_settle_by_freq[float(fp)].fit_trend_order
                ),
                "gain_reference": point_settle_by_freq[float(fp)].gain_reference,
                "fit_estimator": point_settle_by_freq[float(fp)].fit_estimator,
                "fit_window_s": float(point_settle_by_freq[float(fp)].fit_window_s),
                "natural_period_fraction": point_settle_by_freq[
                    float(fp)
                ].natural_period_fraction,
                "fit_cycles": float(fit_cycles_by_freq[float(fp)]),
                "forcing_time_step_s": float(forcing_step_by_freq[float(fp)]),
                "predicted_result_rows": int(predicted_rows_by_freq[float(fp)]),
                "output_intervals_capped": bool(
                    output_intervals_capped_by_freq[float(fp)]
                ),
                # Amplitude-clamp direction of this case (rev033 review):
                # the swing-check exemption is taken from the request, and
                # audit_case binds it to the case manifest's record.
                "amplitude_clamped": (
                    (fr_protocol_by_key or {})
                    .get(format_frequency_key(float(fp)), {})
                    .get("amplitude", {})
                    .get("clamped")
                ),
                # Effective target swing and amplitude halvings of this case
                # (the paper states the final target per point).
                "effective_target_swing": _case_target_swing(
                    format_frequency_key(float(fp))
                )[0],
                "amplitude_halvings": _case_target_swing(
                    format_frequency_key(float(fp))
                )[1],
                **(
                    {"solver_tolerance": case_tolerance_by_key[format_frequency_key(float(fp))]}
                    if format_frequency_key(float(fp)) in case_tolerance_by_key
                    else {}
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
            "fr_protocol": {
                "schema_version": 1,
                "requested_settle_rule": fr_resolution["requested_settle_rule"],
                "settle_rule": fr_resolution["settle_rule"],
                "settle": settle.record(),
                "fit_start_s": fit_start_time,
                "fit_start_range_s": [
                    float(distinct_fit_starts[0]),
                    float(distinct_fit_starts[-1]),
                ],
                "settle_regime_counts": {
                    regime: sum(
                        1
                        for point in point_settle_by_freq.values()
                        if point.regime == regime
                    )
                    for regime in sorted(
                        {point.regime for point in point_settle_by_freq.values()}
                    )
                },
                "max_case_rows": float(max_case_rows),
                "max_output_intervals": int(max_output_intervals),
                "solver_tolerance": dict(tolerance_record),
                # Campaign approval checks (freq.verify_campaign): a base
                # sweep of the prior protocol is publication-approved only
                # with passing half-amplitude checks at the band edges and
                # the measured resonance (freq.linearity_check campaign).
                "approval_checks": (
                    [refinement.PURPOSE_LINEARITY]
                    if settle.rule == fr_protocol.SETTLE_RULE_PRIOR
                    and fr_resolution["amplitude_active"]
                    and refine_plan is None
                    else []
                ),
                "requested_sin_mag_auto_rule": fr_resolution[
                    "requested_sin_mag_auto_rule"
                ],
                "sin_mag_auto_rule": fr_resolution["sin_mag_auto_rule"],
                "target_swing_rule_active": bool(
                    fr_resolution["amplitude_active"]
                ),
                "target_swing": fr_resolution["target_kwargs"]["target_swing"],
                "target_swing_min_pcm": fr_resolution["target_kwargs"]["min_pcm"],
                "target_swing_max_pcm": fr_resolution["target_kwargs"]["max_pcm"],
                "amplitude_clamped_points": (
                    sum(
                        1
                        for decision in amplitude_decisions.values()
                        if decision.clamped
                    )
                    if amplitude_decisions is not None
                    else 0
                ),
                "sin_mag_scale": sin_mag_scale,
                "prior": fr_prior_reference,
                "notes": list(fr_resolution["notes"]),
            },
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
        # Parent identity (freq/sweep_manifest.py, rev032 review): the
        # setpoint table's model version, both maturity axes, and the
        # override set every case shares (setpoint values, neutron floors,
        # pump and heat-loss switches, output filter) -- all compared with
        # every refinement round, approval check, and case manifest.
        "setpoint_model_version": provenance_template.get("setpoint_model_version"),
        "core_maturity": provenance_context["core_maturity"],
        "core_physical_data_maturity": provenance_context[
            "core_physical_data_maturity"
        ],
        "case_common_overrides": _case_common_overrides(
            provenance_context,
            steady_state_overrides,
            ss_time=effective_ss_time,
            reference_freq=float(freq_space[0]),
        ),
        "git_commit": provenance_template["git_commit"],
        "git_dirty": provenance_template["git_dirty"],
        "python_version": provenance_template["python_version"],
        "omc_version": provenance_template["omc_version"],
        "workflow_version": provenance_template["workflow_version"],
    }
    if refine_plan is not None:
        # Refinement round: the request names its parent request and the
        # per-point discards it replaces (freq/refinement.py authority chain).
        sweep_request_data[refinement.plan_request_key(refine_plan)] = (
            refinement.plan_record(refine_plan)
        )
        # Parent identity (freq/sweep_manifest.py): a round or check whose
        # request departs from the parent outside the authorized discard /
        # amplitude fields is refused BEFORE anything is published or run.
        identity_problems = refinement.plan_identity_problems(
            refine_plan, refine_parent_payload, sweep_request_data
        )
        if identity_problems:
            print(
                "ERROR: refinement plan: this run would not carry the parent "
                "sweep's identity (freq/sweep_manifest.py parent identity); "
                "differing: " + "; ".join(identity_problems)
                + ". Run it from the parent's checkout with the parent's "
                "runner arguments."
            )
            raise SystemExit(2)
    try:
        sweep_request_payload = sweep_manifest.publish_sweep_request_manifest(
            base_dir, sweep_request_data
        )
    except sweep_manifest.SweepManifestError as exc:
        # Campaign authority is immutable: an existing manifest recording a
        # DIFFERENT request (or an unreadable one) is refused instead of
        # silently replaced, so two concurrent launches with different
        # requests can no longer overwrite each other's authority. A rerun
        # with the identical request stays idempotent.
        print(
            f"ERROR: refusing to publish the sweep request in {base_dir}: "
            f"{exc}"
        )
        raise SystemExit(2)
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
    if fr_resolution["amplitude_active"]:
        applied = [_case_amplitude(float(fp)) for fp in freq_space]
        clamped_points = sum(
            1 for decision in amplitude_decisions.values() if decision.clamped
        )
        print(
            "  Perturbation:     target swing "
            f"{fr_resolution['target_kwargs']['target_swing']:g} from gain prior, "
            f"{min(applied):.4g}-{max(applied):.4g} pcm "
            f"(clamp [{fr_resolution['target_kwargs']['min_pcm']:g}, "
            f"{fr_resolution['target_kwargs']['max_pcm']:g}] pcm, "
            f"{clamped_points} point(s) clamped)"
            + (f", x{sin_mag_scale:g} scale" if sin_mag_scale != 1.0 else "")
        )
    elif args.sin_mag_logscale:
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
    if settle.rule == fr_protocol.SETTLE_RULE_PRIOR:
        regime_counts = {
            regime: sum(
                1 for point in point_settle_by_freq.values() if point.regime == regime
            )
            for regime in (
                fr_protocol.SETTLE_REGIME_FULL,
                fr_protocol.SETTLE_REGIME_DRIFT,
                fr_protocol.SETTLE_REGIME_LOCKIN,
            )
        }
        kappa = settle.drift_ratio(effective_min_cycles_after_ss)
        print(
            "  Settle discard:   "
            f"T_full = {settle.full_discard_s:g} s "
            f"(k={settle.e_folds:g} / sigma_eff={settle.sigma_eff_s_inv:.4g} 1/s; "
            f"omega_n={settle.omega_n_rad_s:.4g} rad/s, zeta={settle.zeta:.3g}, "
            f"{settle.source}); T_floor = {settle.floor_discard_s:g} s; "
            f"{regime_counts[fr_protocol.SETTLE_REGIME_FULL]} full, "
            f"{regime_counts[fr_protocol.SETTLE_REGIME_DRIFT]} drift, "
            f"{regime_counts[fr_protocol.SETTLE_REGIME_LOCKIN]} lock-in point(s)"
            + (
                f" (lock-in for {fr_protocol.LOCKIN_MIN_OMEGA_RATIO:g} omega_n <= omega "
                "outside the drift window: T_full, local-mean gain reference)"
                if regime_counts[fr_protocol.SETTLE_REGIME_LOCKIN]
                else ""
            )
            + (
                f" (drift for omega >= {kappa:g} omega_n = "
                f"{kappa * settle.omega_n_rad_s:.4g} rad/s, window "
                f"{settle.drift_window_s:.4g} s, linear trend, window-mean "
                "gain reference)"
                if kappa is not None
                else ""
            )
            + f"; fit windows open at {distinct_fit_starts[0]:g}"
            + (
                f"-{distinct_fit_starts[-1]:g} s"
                if len(distinct_fit_starts) > 1
                else " s"
            )
        )
        if refine_plan is not None:
            print(
                f"  Plan:             {refinement.plan_purpose(refine_plan)} "
                f"round {refine_plan['round']} of "
                f"{refine_plan['parent_results_dir']} ({len(refine_points)} "
                "point(s); plan discards and amplitudes, same fit-window lengths)"
            )
        print(
            "  Solver tolerance: "
            f"{tolerance_record['tolerance']:g} ({tolerance_record['rule']}; "
            "population abs. error scale / target swing = "
            + (
                f"{tolerance_record['abs_error_to_swing']:.3g})"
                if tolerance_record.get("abs_error_to_swing") is not None
                else "n/a)"
            )
        )
        capped_grids = sum(1 for flag in output_intervals_capped_by_freq.values() if flag)
        print(
            "  Case budget:      "
            f"max {max(predicted_rows_by_freq.values()):.4g} predicted rows "
            f"(budget {max_case_rows:g}); {capped_grids} output grid(s) capped "
            f"at {max_output_intervals} intervals (event-sampled windows)"
        )
    else:
        print(
            "  Settle discard:   none (fit window opens at the perturbation "
            f"start {fit_start_time:g} s)"
        )
    for note in fr_resolution["notes"]:
        print(f"  FR protocol note: {note}")
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
                plant_data_src=plant_data_src,
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
                plant_id=str(getattr(args, "plant", None) or "msrr"),
                provenance_context=provenance_context,
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
                tolerance=float(
                    case_tolerance_by_key.get(
                        format_frequency_key(float(fp)), tolerance_record["tolerance"]
                    )
                ),
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
        handle.write(f"power_tag\t{make_power_tag(float(args.power))}\n")
        handle.write(
            f"power_tag_format_version\t{POWER_TAG_FORMAT_VERSION}\n"
        )
        handle.write(f"freq_min\t{args.freq_min}\n")
        handle.write(f"freq_max\t{args.freq_max}\n")
        handle.write(f"num_freq\t{args.num_freq}\n")
        handle.write(f"sin_mag\t{sin_mag}\n")
        handle.write(f"sin_mag_auto\t{args.sin_mag_auto}\n")
        handle.write(
            f"sin_mag_auto_rule\t{fr_resolution['sin_mag_auto_rule']}\n"
        )
        handle.write(
            "target_swing_rule_active\t"
            f"{fr_resolution['amplitude_active']}\n"
        )
        handle.write(
            f"target_swing\t{fr_resolution['target_kwargs']['target_swing']}\n"
        )
        handle.write(
            "target_swing_min_pcm\t"
            f"{fr_resolution['target_kwargs']['min_pcm']}\n"
        )
        handle.write(
            "target_swing_max_pcm\t"
            f"{fr_resolution['target_kwargs']['max_pcm']}\n"
        )
        handle.write(f"sin_mag_scale\t{sin_mag_scale}\n")
        handle.write(f"settle_rule\t{settle.rule}\n")
        handle.write(
            "settle_rule_id\t"
            f"{fr_protocol.SETTLE_RECORD_RULE_ID if settle.rule == fr_protocol.SETTLE_RULE_PRIOR else 'none'}\n"
        )
        handle.write(f"settle_e_folds\t{settle.e_folds}\n")
        handle.write(f"settle_full_discard_s\t{settle.full_discard_s}\n")
        handle.write(f"settle_floor_discard_s\t{settle.floor_discard_s}\n")
        handle.write(f"settle_drift_window_fraction\t{settle.drift_window_fraction}\n")
        handle.write(f"settle_drift_window_s\t{settle.drift_window_s}\n")
        handle.write(
            "settle_discard_s_range\t"
            f"{min(p.discard_s for p in point_settle_by_freq.values())}.."
            f"{max(p.discard_s for p in point_settle_by_freq.values())}\n"
        )
        handle.write(
            "settle_drift_points\t"
            f"{sum(1 for p in point_settle_by_freq.values() if p.regime == fr_protocol.SETTLE_REGIME_DRIFT)}\n"
        )
        handle.write(
            "settle_lockin_points\t"
            f"{sum(1 for p in point_settle_by_freq.values() if p.regime == fr_protocol.SETTLE_REGIME_LOCKIN)}\n"
        )
        handle.write(f"fit_start_time\t{fit_start_time}\n")
        handle.write(f"fit_start_time_max\t{distinct_fit_starts[-1]}\n")
        handle.write(
            "refine_round\t"
            f"{refine_plan['round'] if refine_plan is not None else 0}\n"
        )
        handle.write(f"tolerance_rule\t{tolerance_record['rule']}\n")
        handle.write(f"tolerance\t{tolerance_record['tolerance']}\n")
        handle.write(f"max_case_rows\t{max_case_rows}\n")
        handle.write(f"max_output_intervals\t{max_output_intervals}\n")
        handle.write(
            "fr_prior\t"
            + (
                f"{fr_prior_reference['path']}\n"
                if fr_prior_reference
                else "none\n"
            )
        )
        handle.write(
            "fr_prior_sha256\t"
            + (
                f"{fr_prior_reference['sha256']}\n"
                if fr_prior_reference
                else "none\n"
            )
        )
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
        if setpoint_policy_record is not None:
            # Setpoint qualification policy state (TASK-20260911-01 P4):
            # recorded only when a table was consumed, paired with
            # steady_state_table above.
            handle.write(
                f"setpoint_policy\t{setpoint_policy_record['policy']}\n"
            )
            handle.write(
                "setpoint_policy_exception\t"
                f"{setpoint_policy_record['legacy_exception']}\n"
            )
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
