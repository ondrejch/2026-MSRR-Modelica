#!/usr/bin/env python3
"""Run nonlinear step transients for Results II plots (1R and 9R).

With ``--package segmented`` (default: ``legacy``, which keeps the historical
behavior byte-identical) the runner instead drives the standalone SegmentedMSR
vehicles from ``helpers/segmented_runs.py``:

    1r: SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS   (steps + flows)
        SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS   (uhx_trip)
    9r: SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS   (steps + flows)
        SegmentedMSR.Reactors.R9MSRRuhxTripThermalSS   (uhx_trip)
    1r10seg: SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS (steps + flows)
             SegmentedMSR.Reactors.R1MSRRuhx10SegTripThermalSS (uhx_trip)
    r5x5_z10: SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS (steps + flows)
              SegmentedMSR.Reactors.R5x5Z10MSRRuhxTripThermalSS (uhx_trip)

loading ``SegmentedMSR_PlantData.mo`` then ``core/SegmentedMSR.mo``.
``1r10seg`` (1-channel x 10-axial-segment 1R core, TASK-20260906-01) and
``r5x5_z10`` (5x5-radial x 10-axial-segment 1R core, TASK-20260906-02) are
segmented-package only: the legacy package has no 10-segment or 5x5
vehicle, so ``--package legacy --core_models {1r10seg,r5x5_z10}`` is
rejected with a named error before any omc invocation.
Because ``omc`` 1.27 ``simulate()`` scripting is broken system-wide,
segmented runs use ``buildModel(...)`` plus the generated executable
directly, translating the requested interval count into an equidistant
output grid (``-stepSize``; omc 1.27 rejects ``-numberOfIntervals=`` at
runtime). Legacy-only machinery (setpoints CSV
inputs, init-mode overrides, the 9R loop-setpoint harmonization) is rejected
loudly in segmented mode: the rigs carry their own trimmed SteadyState init.
The ``uhx_trip`` case rides the S1 trip wrappers (structural
``numUhxSteps = 2``, unreachable via runtime overrides) with a >=4500 s
horizon floor so every gate run integrates through the t=4000 s demand drop;
segmented flow cases carry the analogous >=4400 s floor because their
overrides insert the 1-$ reactivity step at t=4000 s (mirrored from legacy)
and the flow plot window spans (t - 4000) in [-50, 400] s. Like the legacy
route, the segmented step and flow cases write the fine
``--step_output_interval`` grid (0.01 s by default; review 2026-10-01 M2)
through the compact result-variable contract; ``uhx_trip`` keeps the
``--number_of_intervals`` grid.

Every transient result CSV carries provenance sidecars
(helpers/run_results.py): prior artifacts are quarantined under
00runs/tmp/quarantine/ before launch -- an executable exiting 0 without
producing output can no longer let a pre-existing CSV pass silently --
and each fresh CSV is validated before its manifest and validation
sidecars are written beside it. Programmatic callers of run_case /
run_case_segmented without a provenance context keep the historical
behavior; both CLI drivers always supply one.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from shutil import copyfile
import shlex
import subprocess
import sys

try:
    from .paths import default_segmented_transients_run_dir, default_transients_run_dir
except ImportError:
    from paths import default_segmented_transients_run_dir, default_transients_run_dir

try:
    from helpers.scenario_config import (
        CORE_CHOICES,
        DEFAULT_TRANSIENTS_SCENARIO,
        add_plant_argument,
        plant_package_refusal,
        join_override_payload,
        legacy_core_refusal,
        merge_manifest_sources,
        resolve_scenario,
        scenario_core_refusal,
        scenario_source_entry,
        transients_case_tables,
        wrapper_source_entry,
    )
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.scenario_config import (
        CORE_CHOICES,
        DEFAULT_TRANSIENTS_SCENARIO,
        add_plant_argument,
        plant_package_refusal,
        join_override_payload,
        legacy_core_refusal,
        merge_manifest_sources,
        resolve_scenario,
        scenario_core_refusal,
        scenario_source_entry,
        transients_case_tables,
        wrapper_source_entry,
    )

try:
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        QUALIFIED_COLUMN,
        handle_missing_verdict_column,
        require_qualified_row,
        table_policy_record,
    )
    from helpers.setpoint_model_version import check_table_model_version
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.setpoint_provenance import (
        DEFAULT_POLICY,
        QUALIFIED_COLUMN,
        handle_missing_verdict_column,
        require_qualified_row,
        table_policy_record,
    )
    from helpers.setpoint_model_version import check_table_model_version


_RESULTS_II = transients_case_tables()
STEP_PCM = _RESULTS_II["step_pcm"]
FLOW_CASES = _RESULTS_II["flow_cases"]
UHX_TRIP_CASE = _RESULTS_II["uhx_trip_case"]

# ---------------------------------------------------------------------------
# Model-package selection (TASK-20260823-04 phase R2; mirrors the landed freq
# precedent, commit 408e08a): the CLI exposes --package {legacy,segmented}
# with DEFAULT LEGACY so every pre-existing invocation keeps byte-identical
# behavior. 'segmented' drives the standalone SegmentedMSR package through
# helpers/segmented_runs.py (single source of truth for vehicle names and
# library files; imported ONLY on the segmented code path).
# ---------------------------------------------------------------------------
LEGACY_PACKAGE = "legacy"
SEGMENTED_PACKAGE = "segmented"
PACKAGE_CHOICES = (LEGACY_PACKAGE, SEGMENTED_PACKAGE)
DEFAULT_PACKAGE = LEGACY_PACKAGE

#: Segmented-mode per-core configuration. The vehicles themselves resolve at
#: run time through helpers.segmented_runs.MODEL_BY_CORE (steps + flows) and
#: TRIP_MODEL_BY_CORE (uhx_trip) -- both rigs expose the matched override
#: surface this module emits (powerLevel, perturbationAmplitudePcm,
#: externalReactivityAmplitude[2]/externalReactivityStepTime[2], pump
#: freeConvFF/rampUpTo[1]/rampUpTime[1]/tripTime), so ONE route per case
#: family serves both cores.
SEGMENTED_CORE_CONFIG = {
    "1r": {"label": "1R"},
    "9r": {"label": "9R"},
    # 1-channel x 10-axial-segment 1R core (TASK-20260906-01); segmented
    # package only -- the legacy package has no 10-segment vehicle.
    "1r10seg": {"label": "1R10Seg"},
    # 5x5-radial x 10-axial-segment 1R core (TASK-20260906-02); segmented
    # package only -- the legacy package has no 5x5 vehicle.
    "r5x5_z10": {"label": "R5x5Z10"},
}

#: UHX-trip SHORT-horizon floor for segmented runs. The S1 wrapper pair drops
#: the UHX demand at t=4000 s (structural numUhxSteps=2), so a meaningful gate
#: must integrate past it. Legacy mode keeps its full multi-hour floor
#: max(stop_time, 4000 + 4*3600 s); segmented mode raises to this floor
#: instead (with a printed notice). Full-window trip overlays remain explicit
#: scripts under 00runs/segmented-msr-validation/scenarios/, never pytest.
#: Value comes from data/scenarios/transients/results_ii.yaml.
SEGMENTED_TRIP_MIN_STOP_TIME_S = _RESULTS_II["segmented_trip_min_stop_time_s"]

#: External-reactivity insertion times mirrored from the legacy routes:
#: nominal-flow steps fire at t=2000 s, variable-flow steps at t=4000 s.
SEGMENTED_STEP_INSERT_TIME_S = 2000.0
SEGMENTED_FLOW_INSERT_TIME_S = 4000.0
#: Pump trip time of the segmented flow cases (and of the follow vehicles'
#: UHX demand step).
SEGMENTED_FLOW_TRIP_TIME_S = 2000.0

#: Variable-flow SHORT-horizon floor for segmented runs (REV-2fdd01d-02,
#: TASK-20260823-04). The flow overrides insert the 1-$ step at
#: t=4000 s -- kept byte-identical to the legacy route so segmented and
#: legacy flow cases stay physically comparable -- and ``plot_flow`` draws
#: the window (t - 4000) in [-50, 400] s. A gate run stopped before
#: t=4400 s therefore produces an empty flow panel (observed with the
#: AC-4 smoke's --stop_time 2500), so segmented mode raises short flow
#: horizons to this floor (with a printed notice) exactly like the trip
#: floor above; the overrides themselves are NOT rescheduled.
SEGMENTED_FLOW_MIN_STOP_TIME_S = _RESULTS_II["segmented_flow_min_stop_time_s"]


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path
    (its legacy-compat contract); the fallback keeps script-style execution
    from transients/ working by putting the repo root on sys.path.
    """
    try:
        from helpers import segmented_runs as seg
    except ImportError:  # script-style execution from transients/
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import segmented_runs as seg
    return seg


def _legacy_maturity_fields(core_model: str) -> dict:
    from helpers.plant_config import core_maturity_labels

    return core_maturity_labels(core_model)


def build_segmented_step_overrides(step_pcm: float) -> str:
    """Nominal-flow reactivity-step overrides for the segmented trim rigs.

    Mirrors the legacy-9R override route (same parameter names and insertion
    time): pumps pinned at forced circulation, sine perturbation disabled,
    and a single reactivity step of ``step_pcm`` at t=2000 s via EXISTING
    array element [2] (runtime overrides cannot resize arrays).
    """
    return (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        f"externalReactivityAmplitude[2]={step_pcm:.10g},"
        f"externalReactivityStepTime[2]={SEGMENTED_STEP_INSERT_TIME_S:.10g}"
    )


def build_segmented_flow_overrides(
    flow_frac: float, step_1dol_pcm: float | None = None
) -> str:
    """Variable-flow overrides for the segmented trim rigs.

    Pump route: full flow until the trip at t=2000 s, then an exponential
    coast-down to ``flow_frac`` (``freeConvFF = flow_frac``; ``rampUpTo[1] =
    1`` is the pre-trip TARGET flow fraction), plus the 1-$ reactivity step
    at t=4000 s. The segmented rigs start the ramp complete
    (``Pump.startRamped``), so FF(0) = 1.

    Physics review 2026-09-27: this route formerly bound ``rampUpTo[1] =
    1 - flow_frac`` (mirroring the legacy route), but ``Pump`` treats
    ``rampUpTo`` as the target FF, not the increment - so the pre-trip flow
    was ``1 - flow_frac`` and the "trip" moved FF from 1 - f to f (for f = 1
    the loop stood still until t=2000 s and then restarted). The legacy
    lumped route in ``main()`` is unchanged by owner decision (segmented-only
    fix; the review-2026-09 lumped campaign is the frozen baseline).
    """
    pcm = STEP_PCM["step_1dol"] if step_1dol_pcm is None else float(step_1dol_pcm)
    return (
        f"primaryPump.freeConvFF={flow_frac},"
        "secondaryPump.freeConvFF=1,"
        "primaryPump.rampUpTo[1]=1.0,"
        "primaryPump.rampUpTime[1]=0,"
        f"primaryPump.tripTime={SEGMENTED_FLOW_TRIP_TIME_S:.10g},"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        f"externalReactivityAmplitude[2]={pcm:.10g},"
        f"externalReactivityStepTime[2]={SEGMENTED_FLOW_INSERT_TIME_S:.10g}"
    )


def build_segmented_uhx_trip_overrides() -> str:
    """UHX-trip overrides for the S1 trip wrappers.

    The demand-drop schedule ({1e6 W -> 0} at {0, 4000} s) and DHRS
    engagement are STRUCTURAL on ``TRIP_MODEL_BY_CORE`` vehicles
    (numUhxSteps=2 cannot be reached via -override); only the pump/power
    pins ride runtime overrides.
    """
    return (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "primaryPump.rampUpTo[1]=1.0,"
        "primaryPump.rampUpTime[1]=0,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0"
    )


def build_segmented_run_plan(
    seg,
    core_model: str,
    *,
    stop_time: float,
    trip_stop_time: float,
    flow_stop_time: float | None = None,
    step_pcm: dict[str, float] | None = None,
    flow_cases: dict[str, float] | None = None,
    uhx_trip_case: str | None = None,
    poison_payload: str = "",
    step_model: str | None = None,
    trip_model_override: str | None = None,
    flow_follow: dict[str, bool] | None = None,
    flow_pcm: dict[str, float] | None = None,
) -> list[dict]:
    """Ordered per-core segmented case map (unit-testable selection gate).

    Cases follow the legacy order (STEP_PCM, then FLOW_CASES, then
    uhx_trip). Steps and flows ride ``MODEL_BY_CORE``; uhx_trip rides
    ``TRIP_MODEL_BY_CORE``. ``step_model`` / ``trip_model_override``
    (TASK-20260917-01 P8) substitute the plan §10.6 outer-annulus wrapper
    vehicles an enabled ``outer_fuel_annulus`` dataset must execute (None
    keeps the established maps). ``flow_stop_time=None`` falls back to
    ``stop_time``; the CLI driver passes the REV-2fdd01d-02 floor
    (``max(stop_time, SEGMENTED_FLOW_MIN_STOP_TIME_S)``) explicitly, just
    as it floors ``trip_stop_time`` for the t=4000 s demand drop.
    ``flow_pcm`` maps flow cases to their insertion (``core_flow_pcm_for``:
    each case's ``dollars`` label at the core's 1 $); cases it omits, or all
    cases when it is None, insert ``step_pcm["step_1dol"]``.
    """
    steps = STEP_PCM if step_pcm is None else step_pcm
    flows = FLOW_CASES if flow_cases is None else flow_cases
    trip_case = UHX_TRIP_CASE if uhx_trip_case is None else uhx_trip_case
    step_model = step_model if step_model is not None else seg.model_for(core_model)
    trip_model = (
        trip_model_override
        if trip_model_override is not None
        else seg.trip_model_for(core_model)
    )
    effective_flow_stop = (
        float(stop_time) if flow_stop_time is None else float(flow_stop_time)
    )
    follow = dict(flow_follow or {})
    if step_model != seg.model_for(core_model) and any(follow.values()):
        raise ValueError(
            "uhx_demand_follows_flow flow cases run the trim-rig follow vehicles; "
            f"they cannot combine with the substituted step vehicle {step_model!r}"
        )

    plan: list[dict] = []
    for case, pcm in steps.items():
        plan.append(
            {
                "case": case,
                "family": "step",
                "model": step_model,
                "overrides": join_override_payload(
                    build_segmented_step_overrides(pcm), poison_payload
                ),
                "stop_time": float(stop_time),
            }
        )
    for case, flow_frac in flows.items():
        overrides = build_segmented_flow_overrides(
            flow_frac, step_1dol_pcm=(flow_pcm or {}).get(case, steps["step_1dol"])
        )
        model = step_model
        if follow.get(case):
            # The UHX demand drops to flow_frac x P at the pump trip (the
            # follow vehicle's second demand step), so the coast-down does
            # not keep pulling full power out of the plant.
            model = seg.flow_follow_model_for(core_model)
            overrides += (
                f",uhxFollowFraction={float(flow_frac):.10g},"
                f"uhxFollowTime={SEGMENTED_FLOW_TRIP_TIME_S:.10g}"
            )
        plan.append(
            {
                "case": case,
                "family": "flow",
                "model": model,
                "overrides": join_override_payload(overrides, poison_payload),
                "stop_time": effective_flow_stop,
            }
        )
    plan.append(
        {
            "case": trip_case,
            "family": "uhx_trip",
            "model": trip_model,
            "overrides": join_override_payload(
                build_segmented_uhx_trip_overrides(), poison_payload
            ),
            "stop_time": float(trip_stop_time),
        }
    )
    return plan


def csv_reaches_stop_time(csv_path: str | Path, stop_time: float) -> bool:
    """Return True when the CSV's final data row reaches ~stop_time.

    Local mirror of freq/_common.csv_reaches_stop_time (Ambiguity I,
    TASK-20260823-04): backward tail read that brackets the newline STARTING
    the final NON-EMPTY row and parses that row WHOLE, because SegmentedMSR
    9R result rows are tens of KB wide and a fixed window would slice
    mid-row. Returns False for empty/header-only/single-line files, malformed
    times, or newline-free tails beyond the 1 MB scan cap.
    """
    try:
        with open(csv_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return False
            chunk_size = 16384
            max_scan = 1 << 20
            pos = size
            data = b""
            found = False
            while pos > 0:
                if len(data) > max_scan:
                    return False
                step = min(chunk_size, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
                idx = data.rstrip(b"\n").rfind(b"\n")
                if idx != -1:
                    found = True
                    break
            if not found:
                return False
            handle.seek(pos + idx + 1)
            last = handle.read().decode("utf-8", errors="ignore")
        last = last.strip()
        if not last:
            return False
        last_time = float(last.split(",")[0].strip().strip('"'))
        return last_time >= 0.995 * stop_time
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Run-provenance wiring (TASK-20260826-02 P4, sharing helpers/run_results.py
# with the startup and frequency workflows): per-case manifests are recorded
# beside each <case>_res.csv, any prior result artifacts are quarantined
# BEFORE launch -- closing the stale-acceptance hole where an executable or
# omc exited 0 without producing output and a pre-existing CSV silently
# passed -- and every fresh CSV must pass validation (freshness against this
# run's launch timestamp included) before both sidecars are written.
#
# Callers of run_case / run_case_segmented that omit ``provenance_context``
# keep the historical byte-behavior (no lifecycle, no sidecars); the CLI
# drivers always supply one. One documented exception rides the legacy
# simulate() context: stop-time coverage stays unenforced there because the
# route never had a tail check (preserved byte-for-byte by its layout pins).
# ---------------------------------------------------------------------------
TRANSIENTS_RESULT_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)

#: ``-variableFilter`` for the legacy step and flow cases written on the fine
#: ``--step_output_interval`` grid: exactly the columns the step figures and
#: their tables read (``transients/plot_nonlinear_steps.py``), so a 0.01 s grid
#: over the full horizon stays a few hundred MB per case instead of the
#: ~5 GB of full output. A plain capturing group, because the OpenModelica
#: runtime compiles the filter as POSIX ERE (no ``(?:...)``).
LEGACY_STEP_RESULT_FILTER = (
    "^(time"
    "|(core1R|msre9r)\\.powerblock\\.(fissionPower\\.P|reactorPower|decayPower)"
    "|(core1R|msre9r)\\.mpke\\.n_population\\.n"
    "|core1R\\.fuelchannel\\.(fuelNode1|fuelNode2|grapNode)\\.T"
    "|msre9r\\.R[1-9]\\.(fuelNode1|fuelNode2|grapNode)\\.T"
    "|core1R\\.react\\.TotalTempFeedback"
    "|msre9r\\.RF[1-9]\\.TotalTempFeedback"
    "|msre9r\\.sumFB\\.reactivityOut\\.rho"
    "|(core1R|msre9r)\\.(tempIn|tempOut)\\.T"
    "|(primaryPump|secondaryPump)\\.flowFrac\\.FF"
    ")$"
)


def legacy_step_output(stop_time: float, step_output_interval: float, number_of_intervals: int) -> tuple[int, str, dict]:
    """Output grid of one legacy step/flow case.

    Returns ``(number_of_intervals, extra_simflags, provenance_fields)``. A
    positive ``step_output_interval`` [s] writes a uniform grid of that
    spacing with :data:`LEGACY_STEP_RESULT_FILTER`, which resolves the
    sub-second prompt bursts of the large steps (a 1 s grid under-draws the
    2 $ peak by about 30x). Zero keeps ``number_of_intervals`` and the full
    output.
    """
    if step_output_interval <= 0.0:
        return int(number_of_intervals), "", {}
    intervals = max(1, int(round(float(stop_time) / float(step_output_interval))))
    return (
        intervals,
        # Shell-quoted: simulate() hands simflags to the executable through
        # a shell, where | ( ) $ would otherwise be interpreted.
        f" -variableFilter={shlex.quote(LEGACY_STEP_RESULT_FILTER)}",
        {
            "number_of_intervals": intervals,
            "result_variable_filter": LEGACY_STEP_RESULT_FILTER,
        },
    )


def segmented_step_output(
    stop_time: float, step_output_interval: float, number_of_intervals: int
) -> tuple[int, dict]:
    """Output grid of one segmented step/flow case (review 2026-10-01 M2).

    The segmented twin of :func:`legacy_step_output`: a positive
    ``step_output_interval`` [s] writes a uniform grid of that spacing
    (``-stepSize = stop_time / intervals``), which resolves the sub-second
    prompt bursts (a 1 s grid draws the 1R 2 $ peak about 28x too low). The
    column restriction is the result-variable contract the segmented route
    already writes (15 columns on the 1R-shaped cores, 25 on 9R), so the
    fine grid requires the runtime ``-variableFilter``
    (:func:`run_case_segmented` ``require_runtime_filter``) and is refused
    with ``--full-result-output``. Returns ``(number_of_intervals,
    provenance_fields)``; zero keeps ``number_of_intervals`` and records
    nothing extra, so coarse-grid manifests keep their shape.
    """
    if step_output_interval <= 0.0:
        return int(number_of_intervals), {}
    intervals = max(1, int(round(float(stop_time) / float(step_output_interval))))
    return intervals, {"number_of_intervals": intervals}

#: Mirrors the historical >= 99.5 % stop-reach rule used by the segmented
#: tail check so finished trajectories are not rejected over rounding.
TRANSIENTS_VALIDATION_STOP_SLACK_RATIO = 0.005


def _run_results():
    """Lazily import the shared run-result provenance library."""
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from transients/
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import run_results as rr
    return rr


def _provenance_workflow_version() -> str:
    """Workflow implementation version recorded in every manifest."""
    return f"transients-run_nonlinear_steps/run_results-{_run_results().__version__}"


def _workflow_python_sources(package: str) -> list[str]:
    """Workflow Python modules whose bytes enter the run fingerprint.

    Complete by construction for this call site: the active runner module,
    ``transients/paths.py`` (shared result-path module), and
    ``helpers/run_results.py`` (the provenance library itself) -- plus
    ``helpers/segmented_runs.py`` (which builds the segmented ``.mos`` and
    owns the result-variable contract, TASK-20260908-01 P4) only when the
    segmented code path is selected.  A byte change in any of these yields a
    new fingerprint even when the git commit/dirty state is unchanged, so
    dirty workflow Python can no longer share a result slot with committed
    code.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    helpers_dir = Path(rr.__file__).resolve().parent
    files = [
        here,
        here.parent / "paths.py",
        Path(rr.__file__).resolve(),
        helpers_dir / "plant_config.py",
        helpers_dir / "scenario_config.py",
    ]
    if str(package) == SEGMENTED_PACKAGE:
        files.append(helpers_dir / "segmented_runs.py")
    return [str(path) for path in files]


def _probe(call, default):
    """Best-effort environment probes; degrade to ``default`` on any hiccup."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - provenance metadata must not abort runs
        return default


def _validation_stop_slack(stop_time: float) -> float:
    """Stop-time slack (seconds) allowed when validating finished output."""
    return TRANSIENTS_VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _transients_scenario(args: argparse.Namespace):
    data = getattr(args, "_scenario_data", None)
    if data is not None:
        return data
    return resolve_scenario(
        kind="transients",
        scenario_id=getattr(args, "scenario", DEFAULT_TRANSIENTS_SCENARIO),
        scenario_file=getattr(args, "scenario_file", None),
        plant=getattr(args, "plant", None),
    )


def _tables_for_args(args: argparse.Namespace) -> dict:
    return transients_case_tables(_transients_scenario(args))


def core_step_pcm_for(
    tables: dict, package: str, core_model: str, plant: str | None = None
) -> tuple[dict[str, float], float]:
    """Step amplitudes for one core: dollar-labelled cases at its own 1 $.

    Physics review 2026-10-02 M3 (TASK-20261002-01 P2): the Results II
    dollar labels are applied at each model's own nominal-flow beta_eff
    (helpers.circulation_dollar) instead of the 1R lumped 604.887 pcm for
    every core. Returns (case -> pcm rounded to 1e-3, that core's 1 $ in
    pcm).
    """
    from helpers.circulation_dollar import core_step_pcm, dollar_pcm

    dollar = dollar_pcm(plant or "msrr", package, core_model.strip().lower())
    steps = core_step_pcm(tables["step_pcm"], tables.get("step_dollars") or {}, dollar)
    return steps, dollar


def core_flow_pcm_for(tables: dict, core_steps: dict[str, float], dollar: float) -> dict[str, float]:
    """Flow-case insertions for one core: each case's ``dollars`` label at
    that core's 1 $ (review follow-up: the labels were parsed but every flow
    case inserted ``step_1dol``); unlabelled cases keep ``step_1dol``."""
    from helpers.circulation_dollar import core_flow_pcm

    return core_flow_pcm(
        tables["flow_cases"], tables.get("step_dollars") or {}, dollar, core_steps.get("step_1dol")
    )


def _legacy_source_files(
    core_dir: Path,
    library_path: Path,
    model_path: Path,
    plant_data_path: Path,
    scenario=None,
    wrapper_path: str | Path | None = None,
    plant_file: str | Path | None = None,
):
    try:
        import yaml

        from helpers.plant_config import fingerprint, lumped_manifest_source_files

        files = lumped_manifest_source_files(core_dir)
        extra = {}
        if scenario is not None:
            extra.update(scenario_source_entry(scenario))
        if wrapper_path:
            extra.update(wrapper_source_entry(wrapper_path))
        if plant_file:
            plant_path = Path(plant_file)
            extra[str(plant_path)] = fingerprint(
                yaml.safe_load(plant_path.read_text(encoding="utf-8"))
            )
        return merge_manifest_sources(files, extra) if extra else files
    except Exception:  # noqa: BLE001 - hermetic tests may lack a full plant deck
        return [str(plant_data_path), str(library_path), str(model_path)]


def _parse_override_payload(payload: str) -> dict[str, str]:
    """Split an ``a=1,b=2`` override payload into a plain string-keyed dict.

    Works for array keys too (``externalReactivityAmplitude[2]=589.069``);
    values stay as text because only the canonical JSON form is hashed.
    """
    return dict(part.split("=", 1) for part in payload.split(",") if "=" in part)


def _static_provenance(
    *,
    package: str,
    solver: str,
    tolerance: float,
    number_of_intervals: int,
    max_step_size: float,
    source_files: list[str],
    label: str,
    enforce_stop_coverage: bool,
    min_samples: int | None,
) -> dict:
    """Static per-invocation fields shared by every case manifest.

    Tool/revision probes degrade to their documented unknown/unavailable
    states instead of aborting a sweep over a hiccup.
    """
    rr = _run_results()
    return {
        "package": package,
        "solver": str(solver),
        "tolerance": float(tolerance),
        "number_of_intervals": int(number_of_intervals),
        "max_step_size": float(max_step_size),
        "source_files": (
            dict(source_files)
            if isinstance(source_files, dict)
            else [str(path) for path in source_files]
        ),
        "label": str(label),
        "enforce_stop_coverage": bool(enforce_stop_coverage),
        "min_samples": min_samples,
        "omc_version": _probe(rr.probe_omc_version, "unavailable"),
        "git_info": _probe(
            rr.collect_git_info,
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        "setpoint_table_path": None,
        # Setpoint qualification policy state (TASK-20260911-01 P4); set
        # per core next to setpoint_table_path after the table is loaded.
        "setpoint_policy": None,
        "setpoint_policy_exception": None,
    }


def build_case_manifest(provenance_context: dict, *, case: str, model_name: str, overrides_payload: str, stop_time: float) -> dict:
    """Build the canonical manifest for ONE transient case.

    ``provenance_context`` carries the invocation-static fields; the case
    name, vehicle, override payload, and horizon enter through keyword
    arguments. Every override key/value that reaches the model is part of
    the fingerprint; the launch timestamp is recorded but volatile.
    """
    rr = _run_results()
    overrides: dict[str, object] = {
        "case": str(case),
        "package": str(provenance_context["package"]),
        "core_model": str(provenance_context.get("core_model", "")),
        "max_step_size": float(provenance_context["max_step_size"]),
        "sync_loop_setpoints": bool(
            provenance_context.get("sync_loop_setpoints", False)
        ),
        **_parse_override_payload(overrides_payload),
    }
    if provenance_context.get("allow_unlisted_core", False):
        # Recorded only when set (absence == no override), so default-path
        # manifests keep their historical shape and fingerprint.
        overrides["allow_unlisted_core"] = True
    result_variable_filter = provenance_context.get("result_variable_filter")
    if result_variable_filter is not None:
        # Recorded only on fine-grid step/flow runs (absence == full output),
        # so default manifests keep their historical shape and fingerprint.
        overrides["result_variable_filter"] = str(result_variable_filter)
    poison_fields = provenance_context.get("poison_fields")
    if poison_fields:
        overrides.update(poison_fields)
    # Requested outer-annulus initialization policy (TASK-20260923-01 P1:
    # the runner-request surface recording which production vehicle
    # executed - CoupledSS vs the bounded-startup twin; the effective
    # vehicle rides the manifest model_name). Recorded only on enabled
    # outer-annulus runs (absence == disabled default), fingerprint-active
    # so results are never reused across initialization policies.
    # TASK-20260925-01 P3 branch (ii): the uhx_trip per-case override is
    # the EFFECTIVE policy (``bounded_startup``) -- the single
    # ``OUTER_ANNULUS_TRIP_MODEL_BY_CORE`` vehicle always initializes
    # FixedStart, whatever step/flow policy was requested. The ``case``
    # keyword selects the trip override when it names the run's trip
    # case (the module-level ``UHX_TRIP_CASE``); the launch loop
    # additionally routes every plan item through
    # ``seg.outer_annulus_policy_for_case`` so custom scenario decks
    # with a renamed trip case id stay truthful.
    outer_annulus_init_policy = provenance_context.get(
        "outer_annulus_init_policy"
    )
    if case == UHX_TRIP_CASE and outer_annulus_init_policy is not None:
        # Effective-policy relabel for the trip case: truthful against
        # the executed FixedStart trip vehicle (single source of truth:
        # OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY in
        # helpers/segmented_runs.py; resolved per case in the launch
        # loop via ``seg.outer_annulus_policy_for_case``). ``None``
        # (disabled runs) keeps the historical shape.
        outer_annulus_init_policy = _segmented_helpers().OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY
    if outer_annulus_init_policy is not None:
        overrides["outerAnnulusInitPolicy"] = str(outer_annulus_init_policy)
    radial_annular_flow_command = provenance_context.get(
        "radial_annular_flow_command"
    )
    if radial_annular_flow_command is not None:
        # Effective top-level annularFlowCommand of the circulating radial
        # run (P1 constant-only interface; TASK-20260916-01 P5): the case
        # payload's value when it names the parameter, else the
        # helper-derived default -- recorded only on circulating runs
        # (absence == no annular-flow connector).
        payload_command = _parse_override_payload(overrides_payload).get(
            "annularFlowCommand"
        )
        if payload_command is not None:
            overrides["annularFlowCommand"] = float(payload_command)
        else:
            overrides["annularFlowCommand"] = float(radial_annular_flow_command)
    setpoint_path = provenance_context.get("setpoint_table_path")
    return rr.build_run_manifest(
        package_name=model_name.rsplit(".", 1)[0] if "." in model_name else model_name,
        model_name=str(model_name),
        source_files=provenance_context["source_files"],
        workflow_python_files=_workflow_python_sources(
            provenance_context["package"]
        ),
        overrides=overrides,
        solver=str(provenance_context["solver"]),
        tolerance=float(provenance_context["tolerance"]),
        start_time=0.0,
        stop_time=float(stop_time),
        number_of_intervals=int(provenance_context["number_of_intervals"]),
        output_grid=(
            "segmented_executable_fixed_stepSize_from_intervals"
            if provenance_context["package"] == SEGMENTED_PACKAGE
            else "legacy_simulate_numberOfIntervals"
        ),
        setpoint_table_path=setpoint_path,
        setpoint_policy=provenance_context.get("setpoint_policy"),
        setpoint_policy_exception=provenance_context.get(
            "setpoint_policy_exception"
        ),
        backend="local",
        omc_version=str(provenance_context.get("omc_version", "unavailable")),
        git_info=provenance_context.get(
            "git_info",
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        core_maturity=provenance_context.get("core_maturity"),
        core_physical_data_maturity=provenance_context.get("core_physical_data_maturity"),
        radial_config=provenance_context.get("radial_config"),
        outer_annulus_config=provenance_context.get("outer_annulus_config"),
        result_variables=provenance_context.get("result_variables"),
        result_output_mode=provenance_context.get("result_output_mode"),
        workflow_version=_provenance_workflow_version(),
    )


def _quarantine_prior_result(
    expected_csv: Path,
    *,
    manifest: dict,
    provenance_context: dict,
    quarantine_root: str | Path | None = None,
) -> None:
    """Move any prior artifacts for this slot into the quarantine area."""
    rr = _run_results()
    prepared = rr.prepare_result_path(
        expected_csv,
        reuse_ok=False,
        expected_manifest=manifest,
        required_columns=TRANSIENTS_RESULT_REQUIRED_COLUMNS,
        quarantine_root=quarantine_root,
    )
    if prepared.quarantined:
        destination = prepared.quarantined[0].parent
        print(
            f"  [{provenance_context.get('label', '')}] quarantined "
            f"{len(prepared.quarantined)} prior result artifact(s) to "
            f"{destination}"
        )


def _record_validated_result(
    expected_csv: Path,
    *,
    manifest: dict,
    provenance_context: dict,
    quarantine_root: str | Path | None = None,
) -> None:
    """Validate a fresh CSV; on success publish it and write both sidecars.

    The raw output is expected at the ``{expected_csv}.tmp`` companion the
    simulation was directed at; a passing report publishes those bytes onto
    ``expected_csv`` atomically before the sidecars are written.  A rejected
    temporary output is parked under the quarantine area instead, so a
    failed or interrupted run never leaves a reusable final result.  Raises
    RuntimeError listing the failed check names otherwise.
    """
    rr = _run_results()
    try:
        rr.publish_validated_result(
            rr.make_tmp_path(expected_csv),
            expected_csv,
            manifest=manifest,
            required_columns=TRANSIENTS_RESULT_REQUIRED_COLUMNS,
            time_column="time",
            requested_stop_time=(
                float(manifest.get("stop_time") or 0.0)
                if provenance_context.get("enforce_stop_coverage", True)
                else None
            ),
            stop_time_slack_s=_validation_stop_slack(float(manifest.get("stop_time") or 0.0)),
            min_samples=provenance_context.get("min_samples", rr.DEFAULT_MIN_SAMPLES),
            quarantine_root=quarantine_root,
        )
    except rr.ResultRejectedError as exc:
        failed_checks = ", ".join(exc.report.failed_checks)
        raise RuntimeError(
            f"simulation output rejected by result validation for "
            f"{expected_csv.name}; failed checks: {failed_checks}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"simulation completed but the validated result or its "
            f"provenance sidecars could not be published beside "
            f"{expected_csv.name}: {exc}"
        ) from exc


def run_case_segmented(
    omc: str,
    model_name: str,
    mos_text: str,
    workdir: Path,
    *,
    case_prefix: str,
    stop_time: float,
    number_of_intervals: int,
    max_step_size: float,
    overrides: str,
    omc_timeout_seconds: float | None = None,
    skip_build: bool = False,
    provenance_context: dict | None = None,
    quarantine_root: str | Path | None = None,
    claim_timeout_s: float | None = None,
    result_variables: "list[str] | tuple[str, ...] | None" = None,
    require_runtime_filter: bool = False,
) -> None:
    """Two-stage segmented execution for one case (omc 1.27 constraints).

    Stage 1 runs ``omc <case_prefix>.mos`` (single loadFile +
    ``buildModel(..., tolerance=...)``; tolerance is baked at BUILD time --
    ``-rtol/-atol`` are NOT runtime flags). Stage 2 drives the generated
    executable directly with ``-outputFormat=csv -stopTime=N
    -stepSize=(stop/intervals)`` (omc 1.27 rejects
    ``-numberOfIntervals=`` at runtime) plus the ``-override=`` payload and
    the ``-maxStepSize=`` cap. On the provenanced route the executable is
    pointed at the temporary companion ``-r=<case>_res.csv.tmp``, which is
    validated and then published atomically as ``<case>_res.csv``; without
    provenance the historical final name ``-r=<case>_res.csv`` is used
    directly. Fails loudly on nonzero exits, a missing
    executable/result CSV, or a trajectory that does not reach stop_time.

    With ``provenance_context`` the result slot is claimed exclusively, then
    cleared through quarantine BEFORE anything launches; the fresh output is
    validated and published atomically before both provenance sidecars are
    written beside it.  Omitting the context keeps the historical behavior
    for direct programmatic calls.

    Result-output policy (TASK-20260908-01 P4): when ``result_variables`` is
    given (contract mode), the generated executable is probed for
    ``-variableFilter`` support -- if present the runtime writes the compact
    contract output directly (result-writer-level only; the trajectory's
    time grid is unchanged).  The written header is then verified to be
    EXACTLY contract-shaped (missing + extra column checks): a runtime
    filter that silently failed (POSIX-ERE pattern pitfall -> full wide
    output) or that emitted alias-companion extras is repaired by the
    post-run projection, and a runtime without filter support goes straight
    to that projection.  ``None`` keeps the historical wide output.  The
    realized mechanism is surfaced in the run logs; the selected set itself
    is in the case manifest.

    REV-2fdd01d-03: ``skip_build=True`` still writes the case .mos (the
    per-case buildModel record stays complete) but SKIPS the omc stage so
    the caller can reuse an executable built earlier in the same workdir;
    the executable-existence check remains as the safety net.

    ``require_runtime_filter=True`` (the fine step/flow output grid, review
    2026-10-01 M2) passes ``-variableFilter`` without the probe's fallback:
    the post-run projection would first write the full wide output on the
    fine grid (gigabytes per case), so a runtime that cannot filter must fail
    the case (it rejects the unknown flag) instead. It requires contract
    mode; with ``result_variables=None`` it raises ``ValueError``.
    """
    if require_runtime_filter and result_variables is None:
        raise ValueError(
            f"[{case_prefix}] the fine output grid requires the "
            "result-variable contract: full wide output on that grid would "
            "be gigabytes per case."
        )
    timeout = (
        omc_timeout_seconds
        if omc_timeout_seconds and omc_timeout_seconds > 0
        else None
    )
    # Review 2026-10-01 M1: the poison switches are build-bound (Evaluate =
    # true; bound as buildModel modifiers in mos_text). The manifest keeps
    # the requested payload; the executable receives only the keys it can
    # apply, and a requested value that differs from the compiled one raises
    # here, before anything is built or launched.
    runtime_overrides = _segmented_helpers().runtime_override_payload(
        overrides, build_script=mos_text
    )
    expected_csv = workdir / f"{case_prefix}_res.csv"
    result_tmp_csv = _run_results().make_tmp_path(expected_csv)
    manifest: dict | None = None
    claim = None
    if provenance_context is not None:
        manifest = build_case_manifest(
            provenance_context,
            case=case_prefix,
            model_name=model_name,
            overrides_payload=overrides,
            stop_time=float(stop_time),
        )
        # Same-slot claim (P5): a second concurrent launch of this slot
        # blocks here instead of quarantining this run's in-flight *.tmp.
        # --claim_timeout_s bounds the wait (M2 / REV008-10).
        claim = _run_results().acquire_result_claim(
            expected_csv, timeout_s=claim_timeout_s
        )
    try:
        if manifest is not None and provenance_context is not None:
            _quarantine_prior_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )

        mos_path = workdir / f"{case_prefix}.mos"
        mos_path.write_text(mos_text)

        omc_stdout_path = workdir / f"{case_prefix}_omc_stdout.log"
        if not skip_build:
            omc_stderr_path = workdir / f"{case_prefix}_omc_stderr.log"
            try:
                with omc_stdout_path.open("w") as stdout, omc_stderr_path.open("w") as stderr:
                    result = subprocess.run(
                        [omc, mos_path.name],
                        stdout=stdout,
                        stderr=stderr,
                        cwd=workdir,
                        timeout=timeout,
                    )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"OMC timed out after {exc.timeout:g} s for {mos_path.name}"
                ) from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"OMC failed for {mos_path.name} (exit {result.returncode})"
                )

        exe_path = workdir / model_name
        if not exe_path.exists():
            if skip_build:
                raise RuntimeError(
                    f"no executable {model_name} to reuse for {case_prefix} "
                    f"(build was skipped)"
                )
            stdout_tail = omc_stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"buildModel produced no executable {model_name} for "
                f"{case_prefix}\n{stdout_tail}"
            )

        # Result-output policy (P4): probe the executable's runtime for
        # -variableFilter support.  Supported -> the runtime writes the
        # compact contract output directly; unsupported -> the wide
        # temporary output is projected post-run.  Both mechanisms publish
        # identical columns.
        rc = _segmented_helpers()
        contract_mode = result_variables is not None
        runtime_filter_applied = False
        if contract_mode:
            assert result_variables is not None
            if require_runtime_filter:
                runtime_filter_applied = True
                print(
                    f"  [{case_prefix}] output mechanism: "
                    f"{rc.MECHANISM_RUNTIME_VARIABLE_FILTER} (required on "
                    "the fine output grid)"
                )
            elif rc.executable_supports_variable_filter(exe_path):
                runtime_filter_applied = True
                print(
                    f"  [{case_prefix}] output mechanism: "
                    f"{rc.MECHANISM_RUNTIME_VARIABLE_FILTER}"
                )
            else:
                print(
                    f"  [{case_prefix}] output mechanism: "
                    f"{rc.MECHANISM_POST_RUN_PROJECTION}"
                )

        if number_of_intervals < 1:
            raise ValueError(
                f"[{case_prefix}] --number_of_intervals must be >= 1 "
                f"(got {number_of_intervals}); -stepSize is undefined."
            )
        exe_cmd = [
            f"./{model_name}",
            f"-stopTime={stop_time:.10g}",
            # Translate the requested interval count into an equidistant output
            # grid (startTime is 0 for every transient case).
            f"-stepSize={stop_time / number_of_intervals:.17g}",
            "-outputFormat=csv",
            # Provenanced runs publish through the temporary companion;
            # direct programmatic calls keep the historical final name.
            (
                f"-r={result_tmp_csv.name}"
                if manifest is not None
                else f"-r={case_prefix}_res.csv"
            ),
            f"-maxStepSize={max_step_size:.10g}",
        ]
        if runtime_overrides:
            exe_cmd.append(f"-override={runtime_overrides}")
        if runtime_filter_applied:
            assert result_variables is not None
            exe_cmd.append(
                f"-variableFilter={rc.variable_filter_regex(result_variables)}"
            )
        exe_stdout_path = workdir / f"{case_prefix}_exe_stdout.log"
        exe_stderr_path = workdir / f"{case_prefix}_exe_stderr.log"
        try:
            with exe_stdout_path.open("w") as stdout, exe_stderr_path.open("w") as stderr:
                exe_result = subprocess.run(
                    exe_cmd,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=workdir,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"simulation executable timed out after {exc.timeout:g} s "
                f"for {case_prefix}"
            ) from exc
        if exe_result.returncode != 0:
            stderr_tail = exe_stderr_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"simulation executable failed for {case_prefix} "
                f"(exit {exe_result.returncode})\n{stderr_tail}"
            )
        # Review 2026-10-01 M1 (the lumped run_case's physics-review B1 /
        # rev033 guards): refuse a case whose runtime overrides the
        # executable dropped ("not found" / not overridable) or whose log
        # records a fatal assertion violation -- the pinned build exits 0
        # after both. Raises RuntimeError before anything is published.
        from helpers.omc_log import check_overrides_applied

        check_overrides_applied(
            exe_stdout_path.read_text(errors="ignore")
            + "\n"
            + exe_stderr_path.read_text(errors="ignore"),
            label=f"{case_prefix} ({model_name})",
        )

        raw_csv = result_tmp_csv if manifest is not None else expected_csv
        if manifest is not None and not raw_csv.exists():
            # An engine that ignored the -r= redirect leaves the result at
            # the final name (historical layout); validate it in place.
            raw_csv = expected_csv
        if not raw_csv.exists():
            stdout_tail = exe_stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"executable exited 0 but produced no result CSV for "
                f"{case_prefix}: {expected_csv.name}\n{stdout_tail}"
            )
        # Result-output policy (P4): the published file must be EXACTLY
        # contract-shaped.  Without runtime filter support the wide output
        # is projected onto the contract columns in place; a runtime filter
        # that did not produce exactly the contract shape (silent POSIX-ERE
        # failure -> full wide output, or alias-companion extras) is
        # repaired the same way.  The raw wide bytes are replaced --
        # regenerable via --full-result-output -- and contract compliance
        # is proven before anything is published.
        if contract_mode:
            assert result_variables is not None
            missing = rc.missing_contract_columns(raw_csv, result_variables)
            extras = rc.extra_contract_columns(raw_csv, result_variables)
            if extras and not missing:
                if runtime_filter_applied:
                    print(
                        f"  [{case_prefix}] runtime filter output not "
                        f"contract-shaped ({len(extras)} extra columns); "
                        "downgrading to post-run projection."
                    )
                report = rc.project_contract_csv(raw_csv, result_variables)
                print(f"  [{case_prefix}] {report.describe()}")
                missing = rc.missing_contract_columns(
                    raw_csv, result_variables
                )
                extras = rc.extra_contract_columns(raw_csv, result_variables)
            if missing or extras:
                raise RuntimeError(
                    f"contract-mode result CSV for {case_prefix} is not "
                    f"contract-shaped: missing={missing}; "
                    f"extras={extras[:8]}"
                    + (f" (+{len(extras) - 8} more)" if len(extras) > 8 else "")
                )
        if not csv_reaches_stop_time(raw_csv, stop_time):
            raise RuntimeError(
                f"simulation output did not reach requested stop_time for "
                f"{case_prefix}; see {exe_stderr_path.name}"
            )
        if manifest is not None and provenance_context is not None:
            _record_validated_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
    finally:
        if claim is not None:
            claim.release()


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsupported argument combinations before any simulation starts.

    Legacy invocations pass trivially (zero behavior change). Segmented mode
    hard-errors every legacy-only knob (DECISION, card R2: hard-error over
    loud-skip, matching the landed freq precedent of Ambiguity B): the
    setpoints CSV inputs, the init-mode overrides they feed, and the 9R
    loop-setpoint harmonization do not exist on the trimmed SegmentedMSR
    rigs, which carry their own SteadyState init.
    """
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    if getattr(args, "full_result_output", False) and package != SEGMENTED_PACKAGE:
        # The wide output IS the legacy default; the diagnostic flag exists
        # only to escape the segmented result-variable contract (P4).
        raise ValueError(
            "--full_result_output is only meaningful with --package "
            "segmented: legacy transient runs always write their "
            "historical (wide) result CSVs."
        )
    if (
        getattr(args, "outer_annulus_init_policy", None) is not None
        and package != SEGMENTED_PACKAGE
    ):
        # The initialization-policy lever selects an outer-annulus
        # production vehicle; there is no legacy vehicle to select.
        raise ValueError(
            "--outer-annulus-init-policy is only meaningful with --package "
            "segmented: legacy transient runs keep their historical "
            "vehicles."
        )
    if package != SEGMENTED_PACKAGE:
        return
    if getattr(args, "out_dir", None) is not None:
        # Review 2026-10-01 M6: 00runs/transients-* is the published pre-fix
        # record; segmented runs never write into it.
        from helpers.published_tree_guard import refuse_published_tree_output

        refuse_published_tree_output(args.out_dir, flag="--out_dir")
    if args.stop_time <= 0:
        raise ValueError("--stop_time must be > 0 with --package segmented.")
    if args.number_of_intervals < 1:
        raise ValueError(
            "--number_of_intervals must be >= 1 with --package segmented."
        )
    if args.tolerance <= 0:
        raise ValueError(
            "--tolerance must be > 0 with --package segmented (it is baked "
            "into buildModel at BUILD time; -rtol/-atol are not runtime "
            "flags)."
        )
    if args.method.strip().lower() != "dassl":
        raise ValueError(
            "--method is unsupported with --package segmented: only the "
            "built-in dassl route of the generated executables is exercised."
        )
    if args.setpoints_csv is not None:
        raise ValueError(
            "--setpoints_csv is unsupported with --package segmented: no "
            "segmented setpoint tables exist; the rigs carry their own "
            "trimmed SteadyState init."
        )
    repo_root = Path(__file__).resolve().parents[1]
    for core_key in ("1r", "9r"):
        default_csv = repo_root / "core" / "init" / f"setpoints_{core_key}.csv"
        provided = getattr(args, f"setpoints_csv_{core_key}").resolve()
        if provided != default_csv.resolve():
            raise ValueError(
                f"--setpoints_csv_{core_key} is unsupported with --package "
                "segmented: no segmented setpoint tables exist; the rigs "
                "carry their own trimmed SteadyState init."
            )
    if args.no_sync_loop_setpoints:
        raise ValueError(
            "--no_sync_loop_setpoints is unsupported with --package "
            "segmented: the 9R loop-setpoint harmonization path does not "
            "exist in segmented mode."
        )
    if (
        getattr(args, "full_result_output", False)
        and float(getattr(args, "step_output_interval", 0.0)) > 0.0
    ):
        # Review 2026-10-01 M2: the step and flow cases write the fine
        # --step_output_interval grid through the result-variable contract;
        # full wide output on that grid would be gigabytes per case.
        raise ValueError(
            "--full-result-output cannot be combined with the fine step "
            f"output grid (--step_output_interval {args.step_output_interval:g} "
            "s) with --package segmented: the wide output on that grid would "
            "be gigabytes per case. Pass --step_output_interval 0 to write "
            "the --number_of_intervals grid with full output."
        )


# Shared primary/secondary-loop initialization fields. These should represent the
# same plant hardware between 1R and 9R runs at a given nominal operating point.
SHARED_LOOP_SETPOINT_KEYS = (
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
)

CORE_CONFIG = {
    "1r": {
        "label": "1R",
        "setpoints_file": "setpoints_1r.csv",
        "init_mode_override": "core1R.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState",
        # Physics review 2026-09-27 (B1.5): 1R steps use the same
        # setpoint-override route as 9R (the nominal thermal-SS trim with an
        # explicit external step). The former dedicated
        # MSRR.Transients.R1fullSteps.* models carried the legacy PlantData
        # trims (570/580.41/570 degC) and pumps ramping from ~1 % flow at
        # t = 0, a protocol the 9R steps never used (and, with the
        # circulating-fuel compensation frozen at nominal flow, a +70 pcm
        # start-up kick).
        "step_models": {},
        "step_via_overrides": True,
        "nominal_thermal_model": "MSRR.MSRRuhxNominalTrimThermalSS",
        "uhx_model": "MSRR.MSRRuhxTripThermalSS",
        "uhx_extra_overrides": "",
    },
    "9r": {
        "label": "9R",
        "setpoints_file": "setpoints_9r.csv",
        "init_mode_override": "msre9r.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState",
        # Dedicated R9fullSteps models fail to build in current branch.
        # Use nominal thermal-SS model with explicit external step overrides instead.
        "step_models": {},
        "step_via_overrides": True,
        "nominal_thermal_model": "MSRR.MSRRuhxNominalTrim9RThermalSS",
        "uhx_model": "MSRR.MSRRuhxTrip9RThermalSS",
        "uhx_extra_overrides": "",
    },
}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run nonlinear step dynamics simulations.")
    parser.add_argument(
        "--core_dir",
        type=Path,
        default=repo_root / "core",
        help="Path to core Modelica directory",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for CSV/logs "
            "(default: 00runs/transients-<core_models>; segmented runs "
            "default to 00runs/segmented/transients-<core_models>/<core> and "
            "nest an explicit --out_dir as <out_dir>/segmented/<core>, "
            "refusing one inside the published 00runs/transients-*, "
            "00runs/startup-* or 00runs/freq/ trees)"
        ),
    )
    parser.add_argument(
        "--core_models",
        type=str,
        nargs="+",
        choices=CORE_CHOICES,
        default=["1r", "9r"],
        help=(
            "Core models to run (default: 1r 9r). 1r10seg "
            "(1-channel x 10-axial-segment 1R core) and r5x5_z10 "
            "(5x5-radial x 10-axial-segment 1R core) are supported only "
            "with --package segmented."
        ),
    )
    parser.add_argument(
        "--package",
        type=str,
        choices=PACKAGE_CHOICES,
        default=DEFAULT_PACKAGE,
        help=(
            "Model family to drive (default: legacy). 'legacy' loads "
            "SMD_MSR_Modelica.mo + MSRR.mo with unchanged behavior; "
            "'segmented' drives the standalone SegmentedMSR trim/trip rigs "
            "(loads SegmentedMSR_PlantData.mo then SegmentedMSR.mo; "
            "buildModel + direct executable "
            "because omc 1.27 simulate() scripting is broken). Homogeneous "
            "poison tracking is off by default (scenario poisons: block; "
            "not a spatial poison network). Ignores the "
            "legacy setpoints CSVs and the 9R loop-setpoint harmonization "
            "(the rigs carry their own trimmed SteadyState init), and rides "
            "the TRIP_MODEL_BY_CORE wrappers for uhx_trip with a >=4500 s "
            "horizon floor plus >=4400 s flow-case floors covering the "
            "t=4000 s reactivity insertion. Segmented result CSVs report "
            "temperatures in kelvin. 1r10seg and r5x5_z10 are "
            "segmented-package only: the legacy package has no 10-segment "
            "or 5x5 vehicle and rejects --core_models 1r10seg / r5x5_z10 "
            "before any omc invocation. When the loaded plant's "
            "outer_fuel_annulus dataset is enabled, the segmented 1r10seg "
            "cases run the plan 10.6 CoreVesselAssembly wrapper vehicles "
            "-- by default SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusCoupledSS for the step/flow cases "
            "(both core init modes SteadyState, zero perturbation; "
            "uhx_trip on the TripThermalSS trip counterpart when "
            "shipped), or the shipped-defaults SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusTrimThermalSS twin under "
            "--outer-annulus-init-policy bounded_startup "
            "(TASK-20260923-01 P1) -- and the manifests/compact columns "
            "carry the outer-annulus identity (P8); step/flow cases "
            "record the requested policy as the fingerprint-active "
            "outerAnnulusInitPolicy manifest override, while uhx_trip "
            "always records the effective trip policy "
            "(helpers.OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY, value "
            "'bounded_startup'; TASK-20260925-01 P3: the single trip "
            "counterpart initializes FixedStart) and the effective "
            "vehicle rides the "
            "manifest model_name; the disabled shipped "
            "dataset keeps the historical vehicles, manifests, and columns."
        ),
    )
    add_plant_argument(parser)
    parser.add_argument(
        "--setpoints_csv",
        type=Path,
        default=None,
        help="Deprecated alias for --setpoints_csv_1r (legacy mode only).",
    )
    parser.add_argument(
        "--setpoints_csv_1r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_1r.csv",
        help="Setpoints CSV for 1R 1 MW initialization (legacy mode only)",
    )
    parser.add_argument(
        "--setpoints_csv_9r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_9r.csv",
        help="Setpoints CSV for 9R 1 MW initialization (legacy mode only)",
    )
    parser.add_argument(
        "--no_sync_loop_setpoints",
        action="store_true",
        help=(
            "Do not harmonize 9R shared loop initialization fields to the 1R "
            "nominal setpoint row (legacy mode only)."
        ),
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=DEFAULT_TRANSIENTS_SCENARIO,
        help=(
            "Transients YAML id under data/scenarios/transients/ "
            f"(default: {DEFAULT_TRANSIENTS_SCENARIO})"
        ),
    )
    parser.add_argument(
        "--scenario_file",
        type=str,
        default=None,
        help="Optional scenario YAML overlay (case table and numerics)",
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
    parser.add_argument("--omc", type=str, default="omc", help="OpenModelica compiler")
    parser.add_argument(
        "--stop_time",
        type=float,
        default=_RESULTS_II["stop_time_s"],
        help="Stop time [s]",
    )
    parser.add_argument(
        "--number_of_intervals",
        type=int,
        default=_RESULTS_II["number_of_intervals"],
        help="Number of output intervals",
    )
    parser.add_argument(
        "--step_output_interval",
        type=float,
        default=_RESULTS_II["step_output_interval_s"],
        help=(
            "Output interval [s] of the step and flow cases (default from "
            "the scenario, 0.01 s), both packages. The legacy cases then "
            "write only the columns the step figures read (-variableFilter); "
            "the segmented cases write their result-variable contract "
            "through the runtime -variableFilter (required; refused with "
            "--full-result-output). The fine grid resolves the sub-second "
            "prompt bursts. 0 uses --number_of_intervals (legacy: with full "
            "output). The UHX-trip case always uses --number_of_intervals."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=_RESULTS_II["tolerance"],
        help=(
            "Solver tolerance (segmented mode bakes it into buildModel at "
            "BUILD time; -rtol/-atol are not runtime flags)"
        ),
    )
    parser.add_argument(
        "--method",
        type=str,
        default=_RESULTS_II["method"],
        help=(
            "Solver method (legacy mode only; segmented mode exercises the "
            "built-in dassl route of the generated executables)"
        ),
    )
    parser.add_argument(
        "--max_step_size",
        type=float,
        default=_RESULTS_II["max_step_size_s"],
        help="Maximum solver step size",
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
            "another launch holds the same transient case; expiry raises "
            "ResultSlotClaimTimeout naming the holder. Default: wait "
            "indefinitely (previous behavior)."
        ),
    )
    parser.add_argument(
        "--allow-unlisted-core",
        "--allow_unlisted_core",
        dest="allow_unlisted_core",
        action="store_true",
        help=(
            "Deliberately run a (scenario, core_models) combination the "
            "scenario YAML does not list in applies_to. The override is "
            "recorded in the per-case run manifests "
            "(allow_unlisted_core) instead of being silent."
        ),
    )
    parser.add_argument(
        "--allow-unreviewed-poison-data",
        "--allow_unreviewed_poison_data",
        dest="allow_unreviewed_poison_data",
        action="store_true",
        help=(
            "Development override: allow a poison-on run (tracking or "
            "feedback) whose authored poison dataset maturity is not "
            "approved for production or publication use (the committed "
            "dataset is reduced_order_pending_review). The override is "
            "recorded in the per-case run manifests "
            "(allow_unreviewed_poison_data) instead of being silent. "
            "Poison-off runs do not need it."
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
            "15,000+ columns on the 5x5 core) for every case instead of "
            "the default result-variable contract (time + the physical "
            "outputs, acceptance diagnostics, and provenance taps the "
            "workflows consume). The selected set is recorded in every "
            "per-case manifest (result_variables / result_output_mode). "
            "Rejected with --package legacy, which keeps its historical "
            "wide output."
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
            "SteadyState, zero perturbation; uhx_trip on the "
            "TripThermalSS trip counterpart when shipped); "
            "'bounded_startup' executes the shipped-defaults "
            "TrimThermalSS twin (SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusTrimThermalSS: FixedStart core "
            "cells, 1 pcm sine). Step/flow per-case manifests record "
            "the requested policy as the fingerprint-active "
            "outerAnnulusInitPolicy override; uhx_trip always records "
            "the effective trip policy "
            "(helpers.OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY, value "
            "'bounded_startup'; TASK-20260925-01 "
            "P3: the single trip counterpart initializes FixedStart) "
            "and the effective vehicle rides the manifest model_name."
        ),
    )
    args = parser.parse_args()
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    refusal = plant_package_refusal(args.plant, args.package)
    if refusal:
        parser.error(refusal)
    if args.out_dir is None:
        if args.package == SEGMENTED_PACKAGE:
            # Review 2026-10-01 M6: the segmented default lives under
            # 00runs/segmented/, outside the published 00runs/transients-*
            # record, and needs no further segmented/ component.
            args.out_dir = default_segmented_transients_run_dir(
                repo_root,
                core_models=args.core_models,
                **({} if args.plant == "msrr" else {"plant": args.plant}),
            )
            args._segmented_default_out_dir = True
        else:
            args.out_dir = default_transients_run_dir(
                repo_root,
                core_models=args.core_models,
            )
    try:
        args._scenario_data = resolve_scenario(
            kind="transients",
            scenario_id=args.scenario,
            scenario_file=args.scenario_file,
            plant=args.plant,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def load_setpoints(
    csv_path: Path,
    power_level: float = 1.0,
    *,
    policy: str = DEFAULT_POLICY,
) -> dict[str, float]:
    """Load the setpoint overrides for *power_level* from a setpoints CSV.

    Rows carry a convergence verdict in the generator's ``qualified`` column
    (core/init/generateSetpointTable.py): ``qualified=0`` marks a row that
    FAILED the late-window convergence checks and was kept only via
    ``--accept_unconverged``.  Such rows are rejected here -- they cannot
    initialize a production run -- and so is any verdict that does not
    parse to exactly 1 (e.g. a hand-edited 2, 1.5, or -1).  Tables
    written before that column existed carry no verdict; their
    load follows the *policy* parameter (the shared module's branch (a),
    the only policy-dependent branch): ``strict`` (the default) refuses
    naming the CSV; ``legacy-compatible`` proceeds with a loud warning.
    Policy selection is an explicit library parameter -- no CLI flag.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing setpoints CSV: {csv_path}")
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not rows:
        raise ValueError(f"Setpoints CSV is empty: {csv_path}")
    has_qualified = QUALIFIED_COLUMN in fieldnames
    if not has_qualified:
        handle_missing_verdict_column(csv_path, policy=policy)
    # The table must belong to the current lumped model (its model-version
    # sidecar; helpers.setpoint_model_version): strict refuses a stale table.
    check_table_model_version(csv_path, policy=policy)
    match = None
    for row in rows:
        try:
            if abs(float(row.get("power", "nan")) - power_level) < 1e-9:
                match = row
                break
        except ValueError:
            continue
    if match is None:
        raise ValueError(f"No setpoints row found for power={power_level} in {csv_path}")
    if has_qualified:
        require_qualified_row(match, csv_path, power_level)

    def get(key: str) -> float:
        val = match.get(key)
        if val is None:
            raise KeyError(f"Missing column '{key}' in {csv_path}")
        return float(val)

    overrides = {
        "fuelTempSetPointNode1": get("fuelTempSetPointNode1"),
        "fuelTempSetPointNode2": get("fuelTempSetPointNode2"),
        "graphiteTempSetPoint": get("graphiteTempSetPoint"),
        "heatExchanger.TpIn_0": get("heatExchanger.TpIn_0"),
        "heatExchanger.TpOut_0": get("heatExchanger.TpOut_0"),
        "heatExchanger.TsIn_0": get("heatExchanger.TsIn_0"),
        "heatExchanger.TsOut_0": get("heatExchanger.TsOut_0"),
        "heatExchanger.T_PN1_0": get("heatExchanger.T_PN1_0"),
        "heatExchanger.T_PN2_0": get("heatExchanger.T_PN2_0"),
        "heatExchanger.T_PN3_0": get("heatExchanger.T_PN3_0"),
        "heatExchanger.T_PN4_0": get("heatExchanger.T_PN4_0"),
        "heatExchanger.T_TN1_0": get("heatExchanger.T_TN1_0"),
        "heatExchanger.T_TN2_0": get("heatExchanger.T_TN2_0"),
        "heatExchanger.T_SN1_0": get("heatExchanger.T_SN1_0"),
        "heatExchanger.T_SN2_0": get("heatExchanger.T_SN2_0"),
        "heatExchanger.T_SN3_0": get("heatExchanger.T_SN3_0"),
        "heatExchanger.T_SN4_0": get("heatExchanger.T_SN4_0"),
        "pipeHXtoUHX.T_0": get("pipeHXtoUHX.T_0"),
        "pipeUHXtoHX.T_0": get("pipeUHXtoHX.T_0"),
        "dhrs.T_0": get("dhrs.T_0"),
        "pipeDHRStoHX.T_0": get("pipeDHRStoHX.T_0"),
        "pipeHXtoCore.T_0": get("pipeHXtoCore.T_0"),
        "pipeCoreToDHRS.T_0": get("pipeCoreToDHRS.T_0"),
        "uhx.Tp_0": get("uhx.Tp_0"),
    }
    # 9R setpoint tables include regional initial temperatures; apply them when present.
    for prefix in ("TF1_0_regions", "TF2_0_regions", "TG_0_regions"):
        for idx in range(1, 10):
            key = f"{prefix}[{idx}]"
            raw = (match.get(key) or "").strip()
            if raw:
                overrides[key] = float(raw)
    # M-9 (rev022, TASK-20260921-01 P10; corrected by the physics review
    # 2026-09-27, B1.3): core/MSRR.mo now binds all nine elements of
    # TF1_0_regions/TF2_0_regions/TG_0_regions directly to the PlantData
    # region profile, so every element - region 1 included - is overridable
    # and the shell scalars no longer feed the 9R core at all.  (Under the
    # former cat(1, {scalar}, ...[2:9]) binding OMC refused the [1]
    # override, and deleting the scalar made region 1 fall back to the plant
    # data.)  With region 1 applied from the table, the scalar override is
    # dead; dropping it leaves the solution identical.  The 1R table carries
    # no regional columns: the scalars stay the direct 1R trim.
    for scalar_key, region_key in (
        ("fuelTempSetPointNode1", "TF1_0_regions[1]"),
        ("fuelTempSetPointNode2", "TF2_0_regions[1]"),
        ("graphiteTempSetPoint", "TG_0_regions[1]"),
    ):
        if region_key in overrides:
            del overrides[scalar_key]
    tmix_raw = (match.get("Tmix_0") or "").strip()
    if tmix_raw:
        overrides["Tmix_0"] = float(tmix_raw)
    return overrides


def build_setpoint_override(setpoints: dict[str, float], init_mode_override: str) -> str:
    setpoint_overrides = ",".join(f"{k}={v:.10g}" for k, v in setpoints.items())
    return (
        f"{setpoint_overrides},"
        "heatExchanger.detailedStateInitWeight=1,"
        f"{init_mode_override}"
    )


def build_mos(
    library_path: Path,
    model_path: Path,
    model_name: str,
    start_time: float,
    stop_time: float,
    number_of_intervals: int,
    tolerance: float,
    method: str,
    file_prefix: str,
    simflags: str,
    plant_data_path: Path | None = None,
) -> str:
    from helpers.plant_config import lumped_load_file_text

    load_files = [library_path, model_path]
    if plant_data_path is not None:
        load_files = [plant_data_path, library_path, model_path]
    return (
        "// Generated by transients/run_nonlinear_steps.py\n"
        + lumped_load_file_text(load_files)
        + f"simulate({model_name},"
        f"startTime={start_time:.10g},"
        f"stopTime={stop_time:.10g},"
        f"numberOfIntervals={number_of_intervals},"
        f"tolerance={tolerance:.10g},"
        f"method={method},"
        f'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}",'
        f'simflags="{simflags}");\n'
    )


def run_case(
    omc: str,
    mos_text: str,
    mos_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    workdir: Path,
    expected_csv: Path,
    omc_timeout_seconds: float | None = None,
    provenance_context: dict | None = None,
    quarantine_root: str | Path | None = None,
    claim_timeout_s: float | None = None,
) -> None:
    """Run one ``simulate()`` case via omc scripting.

    With ``provenance_context`` the result slot is claimed exclusively, then
    cleared through quarantine BEFORE anything launches; the fresh output is
    validated and published atomically before both provenance sidecars are
    written beside it.  Omitting the context keeps the historical behavior
    for direct programmatic calls.
    """
    rr = _run_results()
    manifest: dict | None = None
    claim = None
    if provenance_context is not None:
        manifest = build_case_manifest(
            provenance_context,
            case=mos_path.stem,
            model_name=str(provenance_context["model_name"]),
            overrides_payload=str(provenance_context["overrides_payload"]),
            stop_time=float(provenance_context["stop_time"]),
        )
        # Same-slot claim (P5): a second concurrent launch of this slot
        # blocks here instead of quarantining this run's in-flight *.tmp.
        # --claim_timeout_s bounds the wait (M2 / REV008-10).
        claim = rr.acquire_result_claim(expected_csv, timeout_s=claim_timeout_s)
    try:
        if manifest is not None and provenance_context is not None:
            _quarantine_prior_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
        raw_csv = rr.make_tmp_path(expected_csv) if manifest is not None else expected_csv
        mos_path.write_text(mos_text)
        try:
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                result = subprocess.run(
                    [omc, str(mos_path)],
                    stdout=stdout,
                    stderr=stderr,
                    cwd=workdir,
                    timeout=(
                        omc_timeout_seconds
                        if omc_timeout_seconds and omc_timeout_seconds > 0
                        else None
                    ),
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"OMC timed out after {exc.timeout:g} s for {mos_path.name}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(f"OMC failed for {mos_path.name} (exit {result.returncode})")
        # Physics review 2026-09-27 (B1): refuse a run whose setpoint-table
        # overrides OMC silently dropped (formerly 9R Tmix_0 / region [1]).
        from helpers.omc_log import check_overrides_applied

        check_overrides_applied(
            stdout_path.read_text(errors="ignore") + stderr_path.read_text(errors="ignore"),
            label=mos_path.name,
        )
        produced_csv = raw_csv
        if manifest is not None and not produced_csv.exists():
            # An engine that ignored the -r= redirect leaves the result at
            # the final name (historical layout); validate it in place.
            produced_csv = expected_csv
        if not produced_csv.exists():
            stdout_tail = stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"OMC did not produce expected CSV: {expected_csv.name}\n"
                f"{stdout_tail}"
            )
        if manifest is not None and provenance_context is not None:
            _record_validated_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
    finally:
        if claim is not None:
            claim.release()


def _main_legacy(args: argparse.Namespace) -> int:
    core_dir = args.core_dir.resolve()
    library_path = core_dir / "SMD_MSR_Modelica.mo"
    model_path = core_dir / "MSRR.mo"
    from helpers.plant_config import lumped_plant_data_path

    plant_data_path = lumped_plant_data_path(core_dir)

    if not library_path.exists():
        raise FileNotFoundError(f"Missing library file: {library_path}")
    if not model_path.exists():
        raise FileNotFoundError(f"Missing model file: {model_path}")
    if not plant_data_path.exists():
        raise FileNotFoundError(f"Missing plant-data file: {plant_data_path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    setpoint_paths = {
        "1r": args.setpoints_csv_1r.resolve(),
        "9r": args.setpoints_csv_9r.resolve(),
    }
    if args.setpoints_csv is not None:
        setpoint_paths["1r"] = args.setpoints_csv.resolve()

    # Production CLI runs always wire per-case provenance: prior result
    # artifacts are quarantined before launch, outputs are validated, and
    # manifest/validation sidecars record everything beside each CSV. The
    # legacy simulate() route never had a tail check, so stop-time coverage
    # stays unenforced there to preserve its byte-pinned acceptance
    # behavior; freshness against this run's launch stamp still applies.
    legacy_static = _static_provenance(
        package=LEGACY_PACKAGE,
        solver=str(args.method),
        tolerance=float(args.tolerance),
        number_of_intervals=int(args.number_of_intervals),
        max_step_size=float(args.max_step_size),
        source_files=_legacy_source_files(
            core_dir,
            library_path,
            model_path,
            plant_data_path,
            scenario=_transients_scenario(args),
            plant_file=getattr(args, "plant_file", None),
        ),

        label="",
        enforce_stop_coverage=False,
        min_samples=None,
    )
    if getattr(args, "allow_unlisted_core", False):
        legacy_static["allow_unlisted_core"] = True

    tables = _tables_for_args(args)
    flow_cases = tables["flow_cases"]
    uhx_trip_case = tables["uhx_trip_case"]
    uhx_floor = float(tables["uhx_stop_time_s"])
    simflags_base = f"-maxStepSize={args.max_step_size:.10g}"
    core_models = list(dict.fromkeys(args.core_models))
    shared_loop_setpoints: dict[str, float] | None = None
    if not args.no_sync_loop_setpoints:
        shared_loop_setpoints = load_setpoints(
            setpoint_paths["1r"], power_level=1.0, policy=DEFAULT_POLICY
        )

    for core_model in core_models:
        config = CORE_CONFIG[core_model]
        core_out_dir = out_dir / core_model
        core_out_dir.mkdir(parents=True, exist_ok=True)

        core_provenance = {
            **legacy_static,
            "label": config["label"],
            "core_model": core_model,
            # Both maturity axes on legacy runs too (rev032 review), read
            # from the core deck (no segmented-helper import on this path).
            **_legacy_maturity_fields(core_model),
            "setpoint_table_path": str(setpoint_paths[core_model]),
            "sync_loop_setpoints": not args.no_sync_loop_setpoints,
        }

        setpoints = load_setpoints(
            setpoint_paths[core_model], power_level=1.0, policy=DEFAULT_POLICY
        )
        # Setpoint qualification policy state (TASK-20260911-01 P4): the
        # active policy mode and the legacy exception (this core's table
        # lacks the verdict column) ride the provenance context into every
        # per-case manifest, next to setpoint_table_path.
        policy_record = table_policy_record(
            setpoint_paths[core_model], policy=DEFAULT_POLICY
        )
        core_provenance["setpoint_policy"] = policy_record["policy"]
        core_provenance["setpoint_policy_exception"] = policy_record[
            "legacy_exception"
        ]
        if core_model == "9r" and shared_loop_setpoints is not None:
            for key in SHARED_LOOP_SETPOINT_KEYS:
                if key in shared_loop_setpoints:
                    setpoints[key] = shared_loop_setpoints[key]
            print("[9R] Harmonized shared loop setpoints to 1R nominal row.")
        setpoint_overrides = build_setpoint_override(
            setpoints=setpoints,
            init_mode_override=config["init_mode_override"],
        )

        core_steps, core_dollar = core_step_pcm_for(tables, "legacy", core_model)
        flow_insert = core_flow_pcm_for(tables, core_steps, core_dollar)
        print(f"[{config['label']}] 1 $ = {core_dollar:.3f} pcm (this core's nominal-flow beta_eff).")
        print(f"[{config['label']}] Running nominal-flow step insertions...")
        for case in core_steps:
            model_name = config["step_models"].get(case, config["nominal_thermal_model"])
            mos_path = core_out_dir / f"{case}.mos"
            stdout_path = core_out_dir / f"{case}_omc_stdout.log"
            stderr_path = core_out_dir / f"{case}_omc_stderr.log"
            if config.get("step_via_overrides", False):
                overrides = (
                    f"{setpoint_overrides},"
                    "primaryPump.freeConvFF=1,"
                    "secondaryPump.freeConvFF=1,"
                    "powerLevel=1,"
                    "perturbationAmplitudePcm=0,"
                    f"externalReactivityAmplitude[2]={core_steps[case]:.10g},"
                    "externalReactivityStepTime[2]=2000"
                )
                simflags = (
                    f"{simflags_base} -override={overrides}"
                    # Production route: result lands on the *.tmp companion
                    # for validated atomic publication (P5).
                    f" -r={case}_res.csv.tmp"
                )
                overrides_payload = overrides
            else:
                simflags = (
                    f"{simflags_base} "
                    "-override=externalReact.stepTime[2]=2000"
                    f" -r={case}_res.csv.tmp"
                )
                overrides_payload = "externalReact.stepTime[2]=2000"
            case_intervals, filter_flags, output_fields = legacy_step_output(
                args.stop_time,
                float(getattr(args, "step_output_interval", 0.0)),
                args.number_of_intervals,
            )
            simflags += filter_flags
            mos_text = build_mos(
                library_path,
                model_path,
                model_name,
                start_time=0.0,
                stop_time=args.stop_time,
                number_of_intervals=case_intervals,
                tolerance=args.tolerance,
                method=args.method,
                file_prefix=case,
                simflags=simflags,
                plant_data_path=plant_data_path,
            )
            run_case(
                args.omc,
                mos_text,
                mos_path,
                stdout_path,
                stderr_path,
                core_out_dir,
                core_out_dir / f"{case}_res.csv",
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                provenance_context={
                    **core_provenance,
                    **output_fields,
                    "model_name": model_name,
                    "overrides_payload": overrides_payload,
                    "stop_time": float(args.stop_time),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )

        print(f"[{config['label']}] Running variable-flow step insertions...")
        for case, flow_frac in flow_cases.items():
            mos_path = core_out_dir / f"{case}.mos"
            stdout_path = core_out_dir / f"{case}_omc_stdout.log"
            stderr_path = core_out_dir / f"{case}_omc_stderr.log"
            # Pump route (physics review 2026-09-27, A3): full flow from t = 0
            # (rampUpTo[1] = 1 is the TARGET flow fraction; startRamped makes
            # the t = 0 stage complete at t = 0, so the SteadyState trim is
            # solved at FF = 1), then the trip at t = 2000 s coasts down to
            # freeConvFF = flow_frac. The former rampUpTo[1] = 1 - flow_frac
            # ran the loop at 1 - f before the "trip" (for f = 1 the loop
            # stood still until t = 2000 s and then restarted).
            overrides = (
                f"{setpoint_overrides},"
                f"primaryPump.freeConvFF={flow_frac},"
                "secondaryPump.freeConvFF=1,"
                "primaryPump.rampUpTo[1]=1.0,"
                "primaryPump.rampUpTime[1]=0,"
                "primaryPump.startRamped=true,"
                "primaryPump.tripTime=2000,"
                "powerLevel=1,"
                "perturbationAmplitudePcm=0,"
                f"externalReactivityAmplitude[2]={flow_insert[case]:.10g},"
                "externalReactivityStepTime[2]=4000"
            )
            case_intervals, filter_flags, output_fields = legacy_step_output(
                args.stop_time,
                float(getattr(args, "step_output_interval", 0.0)),
                args.number_of_intervals,
            )
            mos_text = build_mos(
                library_path,
                model_path,
                config["nominal_thermal_model"],
                start_time=0.0,
                stop_time=args.stop_time,
                number_of_intervals=case_intervals,
                tolerance=args.tolerance,
                method=args.method,
                file_prefix=case,
                simflags=(
                    f"{simflags_base} -override={overrides}"
                    f" -r={case}_res.csv.tmp"
                    f"{filter_flags}"
                ),
                plant_data_path=plant_data_path,
            )
            run_case(
                args.omc,
                mos_text,
                mos_path,
                stdout_path,
                stderr_path,
                core_out_dir,
                core_out_dir / f"{case}_res.csv",
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                provenance_context={
                    **core_provenance,
                    **output_fields,
                    "model_name": config["nominal_thermal_model"],
                    "overrides_payload": overrides,
                    "stop_time": float(args.stop_time),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )

        print(f"[{config['label']}] Running UHX-trip scenario...")
        uhx_prefix = uhx_trip_case
        mos_path = core_out_dir / f"{uhx_trip_case}.mos"
        stdout_path = core_out_dir / f"{uhx_trip_case}_omc_stdout.log"
        stderr_path = core_out_dir / f"{uhx_trip_case}_omc_stderr.log"
        uhx_overrides = (
            f"{setpoint_overrides},"
            "primaryPump.freeConvFF=1,"
            "secondaryPump.freeConvFF=1,"
            "primaryPump.rampUpTo[1]=1.0,"
            "primaryPump.rampUpTime[1]=0,"
            "powerLevel=1,"
            "perturbationAmplitudePcm=0"
            f"{config['uhx_extra_overrides']}"
        )
        uhx_stop_time = max(float(args.stop_time), uhx_floor)
        mos_text = build_mos(
            library_path,
            model_path,
            config["uhx_model"],
            start_time=0.0,
            stop_time=uhx_stop_time,
            number_of_intervals=args.number_of_intervals,
            tolerance=args.tolerance,
            method=args.method,
            file_prefix=uhx_prefix,
            simflags=(
                f"{simflags_base} -override={uhx_overrides}"
                f" -r={uhx_prefix}_res.csv.tmp"
            ),
            plant_data_path=plant_data_path,
        )
        run_case(
            args.omc,
            mos_text,
            mos_path,
            stdout_path,
            stderr_path,
            core_out_dir,
            core_out_dir / f"{uhx_prefix}_res.csv",
            omc_timeout_seconds=(
                args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
            ),
            provenance_context={
                **core_provenance,
                "model_name": config["uhx_model"],
                "overrides_payload": uhx_overrides,
                "stop_time": float(uhx_stop_time),
            },
            claim_timeout_s=getattr(args, "claim_timeout_s", None),
        )

        print(f"[{config['label']}] Completed. Outputs: {core_out_dir}")

    print(f"All requested runs completed. Outputs in: {out_dir}")
    return 0


def _main_segmented(args: argparse.Namespace) -> int:
    """Segmented-mode driver (standalone SegmentedMSR package).

    Loads ``SegmentedMSR_PlantData.mo`` then ``SegmentedMSR.mo`` per case dir
    and follows the landed freq execution route:
    ``buildModel(..., tolerance=args.tolerance)`` via
    omc scripting, then the generated executable directly with
    ``-stepSize`` translating the requested interval count. Output
    convention (Ambiguity J, TASK-20260823-04; review 2026-10-01 M6): the
    default root is ``00runs/segmented/transients-<core_models>/`` holding
    ``<core_model>/`` directly; an explicit ``--out_dir`` nests results
    under an extra ``segmented/<core_model>/`` component so they can never
    collide with legacy transients outputs. File names inside each core dir
    stay identical to legacy (``<case>_res.csv``).

    REV-2fdd01d-03: every case still gets its .mos written (complete
    per-case buildModel record), but each DISTINCT ``item['model']`` is
    built via omc only ONCE per core dir; the later cases sharing that
    model reuse the already-built executable.
    """
    from helpers.plant_config import (
        copy_segmented_sources,
        segmented_manifest_source_files,
    )

    seg = _segmented_helpers()
    core_dir = args.core_dir.resolve()

    # Review 2026-10-01 M6: the default root (00runs/segmented/
    # transients-<cores>) holds <core>/ directly; an explicit --out_dir keeps
    # the <out_dir>/segmented/<core>/ nesting (validate_args refused one
    # inside a published record tree).
    if getattr(args, "_segmented_default_out_dir", False):
        out_dir = args.out_dir.resolve()
    else:
        out_dir = args.out_dir.resolve() / "segmented"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeout = args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
    core_models = list(dict.fromkeys(args.core_models))

    # Production CLI runs always wire per-case provenance (quarantine before
    # launch, validated outputs, sidecars beside every CSV). Unlike the
    # documented legacy simulate() hatch (_main_legacy), segmented keeps the
    # library's default sample floor via _record_validated_result.
    tables = _tables_for_args(args)
    extra_scenario = scenario_source_entry(_transients_scenario(args))
    try:
        source_files = merge_manifest_sources(
            segmented_manifest_source_files(
                core_dir, plant_id=str(getattr(args, "plant", None) or "msrr")
            ),
            extra_scenario,
        )
    except Exception:  # noqa: BLE001 - hermetic tests may lack a full plant deck
        source_files = extra_scenario
    segmented_static = _static_provenance(
        package=SEGMENTED_PACKAGE,
        solver="dassl",
        tolerance=float(args.tolerance),
        number_of_intervals=int(args.number_of_intervals),
        max_step_size=float(args.max_step_size),
        source_files=source_files,
        label="",
        enforce_stop_coverage=True,
        min_samples=_run_results().DEFAULT_MIN_SAMPLES,
    )
    if getattr(args, "allow_unlisted_core", False):
        segmented_static["allow_unlisted_core"] = True

    from helpers.scenario_config import poison_run_bindings

    poison_payload, poison_fields, poison_tracking = poison_run_bindings(
        SEGMENTED_PACKAGE,
        _transients_scenario(args),
        allow_unreviewed_poison_data=bool(
            getattr(args, "allow_unreviewed_poison_data", False)
        ),
    )
    if poison_fields:
        segmented_static["poison_fields"] = poison_fields

    # Radial provenance (review rev020 Phase 2; TASK-20260916-01 P5): ONE
    # plant load shared by every per-core contract derivation below. The
    # same catalog the segmented emitter consumes is the provenance source;
    # a deck that cannot be loaded refuses the run fail-closed (the radial
    # state of the core must be known before any manifest can be built).
    from helpers.plant_config import load_plant

    radial_plant = load_plant(str(getattr(args, "plant", None) or "msrr"))

    for core_model in core_models:
        config = SEGMENTED_CORE_CONFIG[core_model]
        # Radial provenance + runner/core refusal (review rev020 Phase 2;
        # TASK-20260916-01 P5): one helper derives, from the loaded plant
        # and the selected core, the radial manifest record and the
        # result-contract run shape -- and refuses, fail-closed (named
        # configuration path and offending value), a runner/core
        # combination that cannot execute the requested radial mode, BEFORE
        # any per-case manifest can be published. The transients case
        # payloads carry no annularFlowCommand override, so the effective
        # top-level annularFlowCommand (P1 constant-only interface) is the
        # P1 default 1 on circulating runs. Disabled cores return the
        # all-None contract, so disabled manifests and column sets keep
        # their historical shape. The uhx_trip wrapper cases share this
        # derivation (their vehicles extend the same Trim rigs).
        radial = seg.radial_run_contract(core_model, plant=radial_plant)
        # Outer-annulus provenance + runner/core refusal (TASK-20260917-01
        # P8; plan §11.2; rev024 §6 Phase 1 / TASK-20260923-01 P1): the same
        # loaded plant feeds the outer-annulus contract, which refuses
        # fail-closed (9R, an enabled dataset on a vehicle that cannot
        # execute it, an enabled dataset targeting a different core than
        # the runner selected, an unknown policy name) BEFORE any per-case
        # manifest can be published. The enabled dataset executes the plan
        # §10.6 wrapper vehicles (CoupledSS for the step/flow cases under
        # the default coupled_steady_state policy, the shipped-defaults
        # TrimThermalSS twin under the explicit bounded_startup policy;
        # the trip counterpart for uhx_trip when shipped). The policy and
        # the effective vehicle ride the manifest record
        # (fingerprint-active). Disabled cores return the all-None
        # contract, so disabled manifests, column sets, and case models
        # keep their historical shape.
        outer = seg.outer_fuel_annulus_run_contract(
            core_model,
            plant=radial_plant,
            init_policy=getattr(args, "outer_annulus_init_policy", None),
        )
        # Result-output policy (TASK-20260908-01 P4): segmented runs default
        # to the result-variable contract; --full-result-output restores the
        # wide output explicitly for diagnostics.  The selected set is
        # recorded in every per-case manifest either way.
        rc = _segmented_helpers()
        if getattr(args, "full_result_output", False):
            result_variables = None
            result_output_mode = rc.RESULT_OUTPUT_MODE_FULL
        else:
            result_variables = list(
                rc.result_variables_for(
                    "transients",
                    core_model,
                    poison_tracking=poison_tracking,
                    radial_shape=radial.shape,
                    outer_annulus_shape=outer.shape,
                )
            )
            result_output_mode = rc.RESULT_OUTPUT_MODE_CONTRACT
        core_provenance = {
            **segmented_static,
            "label": config["label"],
            "core_model": core_model,
            "core_maturity": seg.core_maturity_for(core_model),
            "core_physical_data_maturity": seg.core_physical_data_maturity_for(core_model),
            "sync_loop_setpoints": False,
            "result_variables": result_variables,
            "result_output_mode": result_output_mode,
            "radial_config": radial.manifest_fields,
            "radial_annular_flow_command": radial.annular_flow_command,
            "outer_annulus_config": outer.manifest_fields,
            # Requested outer-annulus initialization policy (TASK-20260923-01
            # P1): None on disabled cores, so disabled per-case manifests
            # keep their historical shape; the policy selects the CoupledSS
            # vs bounded-startup production vehicle, whose name rides the
            # per-case manifest model_name. TASK-20260925-01 P3 branch
            # (ii): the uhx_trip per-case override records the EFFECTIVE
            # policy (``bounded_startup`` -- the single trip vehicle
            # always initializes FixedStart), resolved per case in the
            # launch loop below via
            # ``seg.outer_annulus_policy_for_case``; the step/flow cases
            # record the requested policy unchanged.
            "outer_annulus_init_policy": (
                outer.init_policy if outer.enabled else None
            ),
        }
        trip_stop_time = max(
            float(args.stop_time),
            float(tables["segmented_trip_min_stop_time_s"]),
        )
        if trip_stop_time > float(args.stop_time):
            print(
                f"[{config['label']}] Segmented uhx_trip horizon raised to "
                f"{trip_stop_time:g} s to cover the t=4000 s UHX demand drop."
            )
        # REV-2fdd01d-02: same short-horizon floor pattern for the flow
        # cases, whose 1-$ insertion fires at t=4000 s (legacy-mirrored
        # overrides) while the plot window needs data through t=4400 s.
        flow_stop_time = max(
            float(args.stop_time),
            float(tables["segmented_flow_min_stop_time_s"]),
        )
        if flow_stop_time > float(args.stop_time):
            print(
                f"[{config['label']}] Segmented flow-case horizon raised to "
                f"{flow_stop_time:g} s to cover the t="
                f"{SEGMENTED_FLOW_INSERT_TIME_S:g} s reactivity insertion."
            )
        core_steps, core_dollar = core_step_pcm_for(
            tables, "segmented", core_model, plant=getattr(args, "plant", None)
        )
        print(f"[{config['label']}] 1 $ = {core_dollar:.3f} pcm (this core's nominal-flow beta_eff).")
        plan = build_segmented_run_plan(
            seg,
            core_model,
            stop_time=float(args.stop_time),
            trip_stop_time=trip_stop_time,
            flow_stop_time=flow_stop_time,
            step_pcm=core_steps,
            flow_cases=tables["flow_cases"],
            flow_pcm=core_flow_pcm_for(tables, core_steps, core_dollar),
            poison_payload=poison_payload,
            uhx_trip_case=tables["uhx_trip_case"],
            step_model=outer.vehicle if outer.enabled else None,
            trip_model_override=outer.trip_vehicle if outer.enabled else None,
            flow_follow=tables.get("flow_follow"),
        )
        core_out_dir = out_dir / core_model
        core_out_dir.mkdir(parents=True, exist_ok=True)
        copy_segmented_sources(
            core_dir,
            core_out_dir,
            plant_id=str(getattr(args, "plant", None) or "msrr"),
        )

        print(
            f"[{config['label']}] Running segmented nonlinear steps "
            f"({len(plan)} cases, package=segmented)..."
        )
        # REV-2fdd01d-03: the six steps + three flows share the core's trim
        # rig and uhx_trip rides its own wrapper, so omc runs once per
        # DISTINCT model per core dir; later cases reuse the executable.
        built_models: set[str] = set()
        step_output_interval = float(getattr(args, "step_output_interval", 0.0))
        for item in plan:
            # Review 2026-10-01 M2: the step and flow cases write the fine
            # --step_output_interval grid (the legacy route's fb804dc fix);
            # uhx_trip keeps the --number_of_intervals grid.
            if item["family"] in ("step", "flow"):
                case_intervals, output_fields = segmented_step_output(
                    float(item["stop_time"]),
                    step_output_interval,
                    int(args.number_of_intervals),
                )
            else:
                case_intervals, output_fields = int(args.number_of_intervals), {}
            mos_text = seg.build_smoke_mos(
                item["model"],
                tolerance=float(args.tolerance),
                poison_tracking=poison_tracking,
                poison_feedback="enablePoisonFeedback=true" in poison_payload,
            )
            first_build = item["model"] not in built_models
            print(
                f"  [{config['label']}] {item['case']}: {item['model']} "
                f"(stop {item['stop_time']:g} s, "
                f"grid {float(item['stop_time']) / case_intervals:g} s"
                + (")" if first_build else ", reusing built executable)")
            )
            # TASK-20260925-01 P3 branch (ii): the uhx_trip per-case
            # manifest records the EFFECTIVE policy (the shared
            # ``seg.OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY`` constant --
            # single source of truth, resolved per case below), not the
            # requested step/flow policy; step/flow cases record the
            # requested policy. ``None`` (disabled dataset) keeps the
            # historical manifest shape on every case.
            case_policy = (
                seg.outer_annulus_policy_for_case(
                    outer, family=str(item["family"])
                )
                if outer.enabled
                else None
            )
            run_case_segmented(
                args.omc,
                item["model"],
                mos_text,
                core_out_dir,
                case_prefix=item["case"],
                stop_time=item["stop_time"],
                number_of_intervals=case_intervals,
                max_step_size=args.max_step_size,
                overrides=item["overrides"],
                omc_timeout_seconds=timeout,
                skip_build=not first_build,
                provenance_context={
                    **core_provenance,
                    **output_fields,
                    "outer_annulus_init_policy": case_policy,
                    "model_name": item["model"],
                    "overrides_payload": item["overrides"],
                    "stop_time": float(item["stop_time"]),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
                result_variables=result_variables,
                require_runtime_filter=bool(output_fields),
            )
            built_models.add(item["model"])

        print(f"[{config['label']}] Completed. Outputs: {core_out_dir}")

    print(f"All requested runs completed. Outputs in: {out_dir}")
    return 0


def _legacy_core_refusal(args: argparse.Namespace) -> str | None:
    """Name a core that has no legacy vehicle, before any omc invocation.

    TASK-20260906-01 P3: ``1r10seg`` exists only in the standalone
    SegmentedMSR package; the legacy SMD_MSR_Modelica.mo/MSRR.mo library
    ships no 10-segment step/flow/UHX-trip vehicle. A legacy-mode request
    for such a core hard-errors at the top of :func:`main` (clean exit 2,
    named message) instead of failing later on a raw ``KeyError`` in
    ``CORE_CONFIG`` / the setpoints path. TASK-20260906-02 P3 adds the
    same refusal for ``r5x5_z10`` (the legacy package ships no 5x5
    transient vehicle). Returns the refusal message, or ``None`` when
    every requested core has a legacy vehicle (segmented mode is never
    refused).

    TASK-20260908-01 P6 item 4: the membership decision and the message
    text are delegated to the shared helpers in ``helpers/scenario_config``
    (same strings; the trip-pair clause table lives there once).
    """
    if str(getattr(args, "package", DEFAULT_PACKAGE)) == SEGMENTED_PACKAGE:
        return None
    return legacy_core_refusal(
        list(args.core_models),
        CORE_CONFIG,
        flag="--core_models",
        vehicle_noun="step/flow/UHX-trip vehicle",
        legacy_group_noun="legacy transient vehicles",
        with_trip=True,
    )


def main() -> int:
    args = parse_args()
    validate_args(args)
    refusal = _legacy_core_refusal(args)
    if refusal is not None:
        print(f"ERROR: {refusal}")
        return 2
    # Scenario applies_to enforcement (TASK-20260908-01 P2 item 4): every
    # selected core must be listed in the scenario deck (after the
    # CLI-to-YAML key conversion) BEFORE wrapper generation, run-dir
    # creation, or any external process.
    allow_unlisted = bool(getattr(args, "allow_unlisted_core", False))
    scenario_data = _transients_scenario(args)
    try:
        from helpers.scenario_config import poison_run_bindings

        poison_run_bindings(
            str(getattr(args, "package", DEFAULT_PACKAGE)),
            scenario_data,
            allow_unreviewed_poison_data=bool(
                getattr(args, "allow_unreviewed_poison_data", False)
            ),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    for core_model in list(dict.fromkeys(args.core_models)):
        scenario_refusal = scenario_core_refusal(
            scenario_data,
            core_model,
            allow_unlisted=allow_unlisted,
        )
        if scenario_refusal is not None:
            print(f"ERROR: {scenario_refusal}")
            return 2
    if args.package == SEGMENTED_PACKAGE:
        return _main_segmented(args)
    return _main_legacy(args)


if __name__ == "__main__":
    raise SystemExit(main())
