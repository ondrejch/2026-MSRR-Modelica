"""Segmented-run shared mapping + standalone mos-builder helpers
(TASK-20260823-02 phase S1; plan §15 bullet 1 "scenario variants ... runner
integration").

Single source of truth for driving the standalone SegmentedMSR package from
the three runner families (freq / transients / startup):

- ``core_model`` key -> segmented vehicle class names (full-loop trim rigs and
  the S1 UHX-trip wrapper pair);
- the library file list (``SegmentedMSR_PlantData.mo`` then ``SegmentedMSR.mo``;
  no SMD load, no double-load of SegmentedMSR; the legacy startup builder
  emits lumped PlantData + SMD + MSRR loadFile lines, which segmented mode
  must not repeat);
- mos-text builders for check sweeps and buildModel smoke sessions;
- segmented CSV column candidates (fixed overlay columns + component signal
  names) for the downstream plot-side integration (S3);
- the result-variable contract (TASK-20260908-01 P4): per-workflow/core
  output column sets, the ``-variableFilter`` runtime-filter probe, the
  post-run projection fallback, and the contract compliance guard. P7
  (TASK-20260914-01) adds the opt-in intra-channel radial / annular-loop
  columns (plan §8): ``RadialRunShape`` selects the plan §8.1/§8.2 compact
  sets (and the §8.3 detailed families) on enabled runs; the disabled
  default column sets stay byte-identical.

Legacy-compat contract: runners keep their default legacy behavior untouched;
this module is imported ONLY on their segmented code path (CLI switch lands in
phases S2-S4). Structural-vs-runtime rule: variants needing different ARRAY
DIMENSIONS than the rig defaults (``numUhxSteps`` et al.) must use dedicated
wrapper MODELS (``R{1,9}MSRRuhxTripThermalSS``); everything else rides runtime
``-override=`` scalars or existing array elements.

Temperature-unit convention (TASK-20260825-04, commit 2a43887): the
SegmentedMSR library is fully kelvin, so EVERY temperature-bearing column of
a segmented result CSV reports kelvin (``TF1``/``TF2``/``TG``,
``TinCore``/``ToutCore``, ``TZout[i]``, ``TPot``, ``ToutPlenum``). Consumers
that render in degC must convert K->degC at their own boundary. The setpoint
tables under ``core/init/`` remain degC and are NOT loaded by any segmented
runner today; any future loader that starts reading them must convert
degC->K at that boundary.

Run (from repo root)::

    python3.12 -c "import helpers.segmented_runs as m; print(m.MODEL_BY_CORE)"
"""

from __future__ import annotations

import csv
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from helpers.data_validation import fail
from helpers.plant_config import (
    OUTER_ANNULUS_BLOCK_KEY,
    RADIAL_BLOCK_KEY,
    load_plant,
    outer_annulus_fission_active,
    outer_annulus_manifest_record,
    radial_loop_channel_count,
    radial_manifest_record,
    radial_stack_segment_count,
)
from helpers.scenario_config import CLI_TO_YAML_CORE

# ---------------------------------------------------------------------------
# Library files (PlantData first, then the standalone SegmentedMSR package).
# ---------------------------------------------------------------------------
SEGMENTED_PLANT_DATA_FILE = "SegmentedMSR_PlantData.mo"
SEGMENTED_PACKAGE_FILE = "SegmentedMSR.mo"

#: The complete library file list for segmented runs (tuple so callers cannot
#: mutate a shared list by accident). Load order: PlantData, then SegmentedMSR.
SEGMENTED_LIBRARY_FILES: tuple[str, ...] = (
    SEGMENTED_PLANT_DATA_FILE,
    SEGMENTED_PACKAGE_FILE,
)

#: Canonical core keys accepted across the runners. ``1r10seg`` (1-channel
#: x 10-axial-segment 1R core, TASK-20260906-01) and ``r5x5_z10``
#: (5x5-radial x 10-axial-segment 1R core, TASK-20260906-02) are
#: segmented-package only: the legacy SMD_MSR_Modelica.mo/MSRR.mo package
#: ships no vehicle for either, so the runners refuse ``--package legacy``
#: for them before any omc invocation.
CORE_KEYS: tuple[str, ...] = ("1r", "9r", "1r10seg", "r5x5_z10")

# ---------------------------------------------------------------------------
# Core maturity metadata (TASK-20260908-01 P2).
#
# Machine-readable provenance label per core. The values mirror the
# ``maturity:`` field of the corresponding YAML deck under
# ``data/plants/msrr/cores/``; ``helpers/plant_config.py`` validates the
# decks against the same owner-settable enum (``CORE_MATURITY_VALUES``) at
# load time. Runners record the label in the run manifest
# (``helpers/run_results.py`` ``core_maturity`` field) and the paper-facing
# plot scripts append the human-readable phrase to their figure captions
# (owner decision O3 wording). Caption/prose exposure is confined to
# plot-script labels, run metadata, README/manual text, and ``data/CATALOG.md``
# -- never ``latex/`` (journal-article freeze, owner decision O10).
# ---------------------------------------------------------------------------
CORE_MATURITY: dict[str, str] = {
    "1r": "reference_regression",
    "9r": "reference_regression",
    "1r10seg": "discretization_study",
    "r5x5_z10": "exploratory_geometry",
}

#: Physical-data maturity per core (second axis, rev031 review): mirrors the
#: ``physical_data_maturity:`` field of each core deck
#: (``helpers.plant_config.CORE_PHYSICAL_DATA_MATURITY_VALUES``).
CORE_PHYSICAL_DATA_MATURITY: dict[str, str] = {
    "1r": "legacy_inherited",
    "9r": "legacy_inherited",
    "1r10seg": "legacy_inherited",
    "r5x5_z10": "synthetic",
}

#: Human-readable caption phrases per maturity value (owner decision O3
#: default), keyed by the enum value (``helpers.plant_config.CORE_MATURITY_VALUES``).
CORE_MATURITY_LABELS: dict[str, str] = {
    "reference_regression": (
        "reference regression (legacy computational reference; "
        "not a physically validated dataset)"
    ),
    "discretization_study": (
        "discretization study (uniform first-cut partition; "
        "no independent spatial-profile validation)"
    ),
    "exploratory_geometry": (
        "exploratory geometry (placeholder lattice; "
        "surrogate graphite grade)"
    ),
}


#: Human-readable caption phrases per physical-data maturity value.
CORE_PHYSICAL_DATA_MATURITY_LABELS: dict[str, str] = {
    "reviewed_design_basis": "physical data from a reviewed design basis",
    "legacy_inherited": "physical data inherited, not yet source-reviewed",
    "pending_source_review": "physical data pending source review",
    "synthetic": "synthetic placeholder physical data",
}


def core_maturity_for(core_model: str) -> str:
    """Machine-readable (implementation) maturity label for ``core_model``."""
    return CORE_MATURITY[normalize_core_key(core_model)]


def core_physical_data_maturity_for(core_model: str) -> str:
    """Machine-readable physical-data maturity label for ``core_model``."""
    return CORE_PHYSICAL_DATA_MATURITY[normalize_core_key(core_model)]


def core_maturity_label(core_model: str) -> str:
    """Human-readable caption phrase for ``core_model``."""
    return CORE_MATURITY_LABELS[core_maturity_for(core_model)]


def core_maturity_footnote(core_models: "list[str] | tuple[str, ...]") -> str:
    """Caption footnote naming the maturity of every plotted core.

    Duplicate keys are collapsed in first-seen order, so overlay figures
    get a single phrase per core. Each core's phrase is on its own line
    (joined by ``";\n"``): with both maturity axes a two-core footnote on
    one line is about 17 in wide at 8 pt and was clipped at both edges of
    the 10 in figures, while one core's phrase is about 8.8 in.
    """

    keys = list(dict.fromkeys(normalize_core_key(core) for core in core_models))
    return "Core maturity: " + ";\n".join(
        f"{key}: {CORE_MATURITY_LABELS[CORE_MATURITY[key]]}, "
        f"{CORE_PHYSICAL_DATA_MATURITY_LABELS[CORE_PHYSICAL_DATA_MATURITY[key]]}"
        for key in keys
    ) + "."


# ---------------------------------------------------------------------------
# Intra-channel radial / annular-loop run metadata (TASK-20260914-01 P1;
# plan §6.8, §8.4).
# ---------------------------------------------------------------------------

def radial_manifest_fields(plant: "dict[str, Any]", core_model: str) -> "dict[str, Any] | None":
    """Radial/loop run-manifest fields for ``core_model`` (None when off).

    Returns ``None`` when the intra-channel radial stack is disabled for
    the core, so disabled-run manifests keep their historical field set
    and fingerprint -- the same field-omission pattern as the poison-off
    column sets (``POISON_RESULT_COLUMNS``). When enabled, the returned
    JSON-safe dict (annular mode, heat-exchanger state, flow direction,
    dataset identity/maturity, and the deterministic radial and
    annular-loop fingerprints) feeds
    ``helpers.run_results.build_run_manifest(radial_config=...)`` and is
    recorded as the fingerprint-active top-level ``intra_channel_radial``
    field, so result directories are never reused across different
    radial/circulation/distribution/HX configurations.

    ``plant`` is a loaded+validated plant deck (``helpers.plant_config
    .load_plant``); ``core_model`` accepts the CLI key (``1r``/``9r``/
    ``1r10seg``/``r5x5_z10``, normalized via :func:`normalize_core_key`)
    or the YAML key.
    """

    cli_key = normalize_core_key(core_model)
    yaml_key = CLI_TO_YAML_CORE.get(cli_key, cli_key)
    return radial_manifest_record(plant, yaml_key)


#: Production runner vehicles that carry the P1 annular-flow command
#: (top-level ``annularFlowCommand`` parameter default 1, with the
#: conditional ``ConstantFlowFraction`` source connected to
#: ``core.annFlowCmd``; TASK-20260916-01 P1). The 9R zone cores carry no
#: production flow-command path and plant-driven radial r9 is refused
#: outright, so the set is exactly the rank-compatible 1R family. A runner
#: selecting a core outside this set cannot execute a circulating radial
#: mode and must refuse (review rev020 Phase 2; TASK-20260916-01 P5).
PRODUCTION_ANNULAR_FLOW_COMMAND_CORES: tuple[str, ...] = (
    "1r",
    "1r10seg",
    "r5x5_z10",
)

#: The documented P1 default of the top-level ``annularFlowCommand``
#: parameter: the loop's nominal operating point (command value one scales
#: ``nominalMassFlow``; a runner-side runtime override of the parameter
#: would replace it as the effective command).
DEFAULT_ANNULAR_FLOW_COMMAND = 1.0


@dataclass(frozen=True)
class RadialRunContract:
    """Runner-facing radial/loop provenance for ONE segmented run (P5).

    Derived once per run by :func:`radial_run_contract` and consumed by the
    three production runners (startup / frequency / transients; the trip
    wrappers share those code paths): ``manifest_fields`` feeds
    ``build_run_manifest(radial_config=...)``, ``shape`` feeds
    ``result_variables_for(radial_shape=...)``, and
    ``annular_flow_command`` is recorded in the manifest overrides. The
    DISABLED default is the all-``None`` contract -- historical manifest
    shape and column set -- never a disabled-shape record.
    """

    manifest_fields: "dict[str, Any] | None" = None
    shape: "RadialRunShape | None" = None
    annular_flow_command: "float | None" = None

    @property
    def enabled(self) -> bool:
        """True when the selected core's radial stack is enabled."""
        return self.manifest_fields is not None


def radial_run_contract(
    core_model: str,
    *,
    plant: "dict[str, Any] | None" = None,
    plant_id: str = "msrr",
    root: "Path | None" = None,
    annular_flow_command: "float | int | str | None" = None,
) -> RadialRunContract:
    """Derive the radial manifest record and run shape for ONE segmented run.

    The single junction between a production runner and the intra-channel
    radial program (review rev020 Phase 2; TASK-20260916-01 P5): from the
    loaded plant (``plant=None`` loads the same catalog the segmented
    emitter consumes -- :func:`helpers.plant_config.load_plant` with the
    default ``plant_id``; ``root`` relocates the ``data/`` tree for fixture
    decks) and the selected core it derives:

    1. ``manifest_fields`` -- the production
       :func:`radial_manifest_fields` record (``None`` when the core is
       disabled, so disabled manifests keep their historical field set);
    2. ``shape`` -- the matching :class:`RadialRunShape` (circulation, HX
       state, ``n_chan``/``n_seg``; ``None`` when disabled, so disabled
       compact CSV column sets stay byte-identical);
    3. ``annular_flow_command`` -- the EFFECTIVE top-level
       ``annularFlowCommand`` the production vehicle executes (P1
       constant-only interface): the runner-supplied value when the
       runner's parsed runtime-override payload names the parameter, else
       the documented default 1; ``None`` when the core is disabled or the
       loop is static (no annular-flow connector exists there).

    Fail-closed: a runner/core combination that cannot execute the
    requested radial mode refuses BEFORE anything is returned, naming the
    configuration path and the offending value -- an enabled
    ``cores.r9.intra_channel_radial`` block (the 9R zone cores have no
    production annular-flow command path, and the four-entry generated
    record binds nowhere), a circulating request on a core outside
    :data:`PRODUCTION_ANNULAR_FLOW_COMMAND_CORES`, and a dedicated heat
    exchanger on a non-circulating loop. ``annular_flow_command`` accepts
    the runner's parsed ``-override=`` payload value (str/int/float); a
    non-finite or negative effective command refuses.
    """

    cli_key = normalize_core_key(core_model)
    yaml_key = CLI_TO_YAML_CORE.get(cli_key, cli_key)
    block_path = f"cores.{yaml_key}.{RADIAL_BLOCK_KEY}"
    if plant is None:
        plant = load_plant(plant_id, root=root)
    record = radial_manifest_fields(plant, cli_key)
    if record is None:
        # Disabled core: the historical manifest shape and column set.
        return RadialRunContract()
    label = f"plant {plant_id!r}"

    # --- fail-closed runner/core refusals (before ANY return) -------------
    if cli_key == "9r":
        fail(
            label,
            f"{block_path}.enabled",
            "the production runner refuses an enabled radial block for "
            "cores.r9: the executable 9R topology is four independent "
            "ONE-channel zone SegmentedCore instances with no production "
            "annular-flow command path, and the generated four-entry 9R "
            "record is preparatory-only data that binds nowhere; only the "
            f"1R-family cores accept an enabled radial block; got "
            f"enabled={record.get('enabled')!r}",
        )
    mode = str(record.get("annularFluidMode"))
    if (
        mode == "circulating"
        and cli_key not in PRODUCTION_ANNULAR_FLOW_COMMAND_CORES
    ):
        fail(
            label,
            f"{block_path}.annular_fluid.mode",
            f"the selected production runner vehicle for core {cli_key!r} "
            f"({model_for(cli_key)}) carries no production annular-flow "
            "command path, so it cannot execute the requested circulating "
            f"radial mode; got annularFluidMode={mode!r}",
        )
    hx_enabled = bool(record.get("annularHeatExchangerEnabled"))
    if hx_enabled and mode != "circulating":
        fail(
            label,
            f"{block_path}.annular_loop.heat_exchanger.enabled",
            "the dedicated heat exchanger requires the annular fluid "
            f"circulating (plan §2.2); got annularHeatExchangerEnabled="
            f"{hx_enabled!r} with annularFluidMode={mode!r}",
        )

    # --- effective annular-flow command (circulating runs only) -----------
    if mode != "circulating":
        command = None
    elif annular_flow_command is None:
        command = DEFAULT_ANNULAR_FLOW_COMMAND
    else:
        try:
            command = float(annular_flow_command)
        except (TypeError, ValueError):
            command = None
        if command is None or not math.isfinite(command) or command < 0.0:
            fail(
                label,
                "overrides.annularFlowCommand",
                f"the effective top-level annularFlowCommand must be a "
                f"finite number >= 0 (the P1 constant-only command drives "
                f"the closed loop's flow source; the nominal operating "
                f"point is {DEFAULT_ANNULAR_FLOW_COMMAND!r}), got "
                f"{annular_flow_command!r}",
            )

    shape = RadialRunShape(
        enabled=True,
        circulating=(mode == "circulating"),
        hx_enabled=hx_enabled,
        n_chan=radial_loop_channel_count(plant, yaml_key),
        n_seg=radial_stack_segment_count(plant, yaml_key),
    )
    return RadialRunContract(
        manifest_fields=record,
        shape=shape,
        annular_flow_command=command,
    )


# ---------------------------------------------------------------------------
# Optional OUTER-CORE FUEL ANNULUS run metadata (TASK-20260917-01 P8; plan
# §10.6, §11). A SEPARATE identity from the intra-channel radial stack above
# (card Constraints: no shared names, keys, commands, or fingerprints) - the
# same runner-integration PATTERN cloned, not a shared namespace.
# ---------------------------------------------------------------------------

#: Plant-driven runner vehicles that carry the P4 CoreVesselAssembly wrapper
#: (plan §10.6): the first production target's SS vehicle. The four
#: established ``MODEL_BY_CORE`` vehicles keep their bare ``SegmentedCore``
#: (planner resolution 2); an enabled outer-annulus dataset on any other
#: core refuses at :func:`outer_fuel_annulus_run_contract` (fail-closed:
#: enabled configuration on a vehicle that cannot execute it). 9R has no
#: entry here by name and is refused explicitly before anything else.
#:
#: TASK-20260923-01 P1 (rev024 §6 Phase 1 / §4 High-1): the coupled
#: steady-state entry points at the dedicated production class
#: ``R1MSRRuhx10SegOuterAnnulusCoupledSS`` (SteadyState core modes, zero
#: perturbation fixed in the class) - NOT the bare TrimThermalSS wrapper.
#: The TrimThermalSS wrapper keeps its shipped bounded-startup defaults
#: (FixedStart core cells, 1 pcm sine) as the separately named
#: bounded-startup vehicle/policy (:data:`OUTER_ANNULUS_BOUNDED_STARTUP_MODEL_BY_CORE`).
OUTER_ANNULUS_MODEL_BY_CORE: dict[str, str] = {
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusCoupledSS",
}

#: Bounded-startup outer-annulus vehicles (TASK-20260923-01 P1): the SAME
#: plan §10.6 CoreVesselAssembly wrapper with its SHIPPED defaults (FixedStart
#: core fuel/moderator init modes, 1 pcm sine perturbation) - the
#: bounded-settling configuration the pre-P1 runner executed. Kept under a
#: SEPARATELY NAMED vehicle/policy from the coupled-steady-state selection
#: (rev024 §6 Phase 1 item 4): the coupled contract of
#: :func:`outer_fuel_annulus_run_contract` points at
#: :data:`OUTER_ANNULUS_MODEL_BY_CORE`; this table is the bounded-startup
#: twin the runners resolve when the caller explicitly selects the
#: ``bounded_startup`` outer-annulus initialization policy.
OUTER_ANNULUS_BOUNDED_STARTUP_MODEL_BY_CORE: dict[str, str] = {
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS",
}

#: Outer-annulus initialization policies (TASK-20260923-01 P1; rev024 §6
#: Phase 1): the production-visible contract for the coupled steady state.
#: ``coupled_steady_state`` executes the dedicated CoupledSS vehicle (both
#: core init modes SteadyState, zero perturbation); ``bounded_startup``
#: executes the shipped-defaults TrimThermalSS wrapper (FixedStart core
#: cells, 1 pcm sine). The resolved policy string rides every run manifest
#: (``outerAnnulusInitPolicy`` inside the ``outer_fuel_annulus`` record)
#: and participates in the fingerprint, so a result directory is never
#: reusable across initialization policies. ``None`` resolves the shipped
#: default on the runner CLIs.
OUTER_ANNULUS_INIT_POLICIES: tuple[str, ...] = (
    "coupled_steady_state",
    "bounded_startup",
)

#: Default outer-annulus initialization policy on the runner CLIs
#: (TASK-20260923-01 P1): the coupled steady state is the production
#: default; the bounded-startup path stays available under its explicit
#: name.
DEFAULT_OUTER_ANNULUS_INIT_POLICY = "coupled_steady_state"

#: UHX-demand trip counterparts of the outer-annulus vehicles (plan §10.6:
#: "add a trip/transient counterpart only after the steady-state vehicle
#: passes energy, precursor, and cavity-duty checks" — shipped after the P8
#: SS + low-flow verification; the contract refuses when a selected core
#: has an SS vehicle but no trip counterpart).
#: The trip table is POLICY-INDEPENDENT by construction: the single trip
#: vehicle extends the bounded-startup base (``coreFuelInitMode =
#: coreModInitMode = FixedStart``, structural, not runtime-overridable),
#: so the trip case always executes the bounded-startup initialization
#: whatever step/flow policy the caller requested.
OUTER_ANNULUS_TRIP_MODEL_BY_CORE: dict[str, str] = {
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTripThermalSS",
}

#: Effective initialization policy recorded on ``uhx_trip`` per-case
#: manifests (TASK-20260925-01 P3, branch (ii): effective-policy
#: recording, NOT a coupled-SS trip vehicle). This constant is the
#: SINGLE SOURCE OF TRUTH for the trip relabel: the launch loop routes
#: every plan item through :func:`outer_annulus_policy_for_case` (which
#: returns this constant for the ``uhx_trip`` family), the per-case
#: manifest fallback in ``transients/run_nonlinear_steps.py``
#: (``build_case_manifest``) assigns this same constant, and the help
#: text names it -- no call site may spell the policy string inline.
#: Because the single :data:`OUTER_ANNULUS_TRIP_MODEL_BY_CORE` vehicle
#: always initializes ``FixedStart``, the trip per-case manifest records
#: this effective policy — not the requested step/flow policy — as the
#: fingerprint-active ``outerAnnulusInitPolicy`` override, keeping the
#: label truthful against the executed vehicle's init mode. Physics
#: consequence is nil (trip overrides zero the sine; the demand drop
#: fires at 4000 s after settling): manifest honesty, not results.
#: Branch (ii) was chosen over branch (i) (a new coupled-SS trip vehicle
#: with a nominal-at-t0 demand source) because a new vehicle needs the
#: full plan §10.6 verification battery (energy, precursor, cavity-duty
#: checks) plus translation/balance evidence for zero physics gain, while
#: a nominal-hold source on a trip vehicle would fight the {1e6 -> 0}
#: stepper schedule at init — new semantics to verify. Relabeling the
#: manifest is provenance-only: zero DAE/model delta.
OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY = "bounded_startup"


def outer_annulus_policy_for_case(
    contract: "OuterAnnulusRunContract", *, family: str
) -> "str | None":
    """Effective manifest policy for one case family under ``contract``.

    ``None`` on disabled contracts (disabled manifests keep their
    historical shape); the contract's requested ``init_policy`` for the
    step/flow families; :data:`OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY` for
    the ``uhx_trip`` family, whose single trip vehicle always
    initializes ``FixedStart``. Recorded only on enabled
    outer-annulus runs (absence == disabled default), so no per-case
    manifest records an ``outerAnnulusInitPolicy`` the executed
    vehicle's init mode does not implement.
    """
    if not contract.enabled:
        return None
    if str(family) == "uhx_trip":
        return OUTER_ANNULUS_TRIP_EFFECTIVE_POLICY
    return contract.init_policy


def outer_annulus_manifest_fields(plant: "dict[str, Any]") -> "dict[str, Any] | None":
    """Outer-annulus run-manifest fields for a loaded plant (None when off).

    Delegates to
    :func:`helpers.plant_config.outer_annulus_manifest_record` (P7):
    ``None`` when the ``outer_fuel_annulus`` block is absent or disabled, so
    disabled-run manifests keep their historical field set and fingerprint
    (the radial/loop field-omission pattern; planner resolution 6). When
    enabled, the returned JSON-safe dict names dataset id, fingerprint,
    maturity, topology/direction codes, cavity temperature, the inventory
    policy, and the annulus-fission record (TASK-20260918-01 P1, plan §5.1:
    fissionEnabled, fissionCouplingPolicy, BOTH fraction vectors, and BOTH
    the temperature-feedback and poison-exposure policies - explicit and
    manifest-visible whenever the annulus runs, including the
    fission-disabled state), and feeds
    ``helpers.run_results.build_run_manifest(outer_annulus_config=...)``.
    P2 note: the record's ``fissionEnabled`` is the CONFIGURED flag; the
    structurally-ACTIVE state is decided once by
    :func:`outer_fuel_annulus_run_contract` via
    ``helpers.plant_config.outer_annulus_fission_active``.
    """

    return outer_annulus_manifest_record(plant)


@dataclass(frozen=True)
class OuterAnnulusRunShape:
    """Structural outer-annulus shape of an enabled run (contract selection).

    ``n_chan``/``n_seg`` mirror the generated dataset's dimensions (the
    annulus is one azimuthally mixed channel per core axial segment);
    ``cavity_enabled`` records whether the fixed-temperature cavity and the
    loop-surface aggregate are active; ``fission_enabled`` (P7,
    TASK-20260918-01, plan §6 Phase 7 item 5; P2 TASK-20260922-03) records
    the EFFECTIVE (structurally-active) fission state -- the
    fissionActive decision
    (:func:`helpers.plant_config.outer_annulus_fission_active`:
    configured AND a nonzero heat-deposition distribution), which selects
    the compact FISSION column set
    (``OUTER_ANNULUS_FISSION_COMPACT_COLUMNS``) on top of the enabled
    base set. It is the ACTIVE image, not the manifest record's
    CONFIGURED ``fissionEnabled`` flag: a fission-enabled deck with an
    all-zero distribution carries ``fission_enabled=False`` here (the
    Modelica ``fissionOn`` switch folds the same way). The disabled
    default is expressed by passing ``None`` (no outer-annulus shape) to
    the contract resolver, which keeps the historical column set - never
    by a disabled-shape record.
    """

    enabled: bool = True
    n_chan: int = 0
    n_seg: int = 0
    cavity_enabled: bool = False
    fission_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.enabled:
            raise ValueError(
                "OuterAnnulusRunShape expresses an ENABLED outer-annulus "
                "run; pass outer_annulus_shape=None for the disabled default "
                "contract"
            )
        if self.n_chan <= 0 or self.n_seg <= 0:
            raise ValueError(
                "OuterAnnulusRunShape requires positive n_chan and n_seg "
                "(one azimuthally mixed annulus per core axial segment; "
                "plan §3.1)"
            )


@dataclass(frozen=True)
class OuterAnnulusRunContract:
    """Runner-facing outer-annulus provenance for ONE segmented run (P8).

    The outer-annulus clone of :class:`RadialRunContract`: derived once per
    run by :func:`outer_fuel_annulus_run_contract` and consumed by the three
    production runners. ``manifest_fields`` is the verbatim production
    :func:`outer_annulus_manifest_fields` record (the plant dataset
    identity surface, plan §11.1 - identical to
    ``helpers.plant_config.outer_annulus_manifest_record``) and feeds
    ``build_run_manifest(outer_annulus_config=...)``; ``shape`` feeds
    ``result_variables_for(outer_annulus_shape=...)``; ``vehicle`` /
    ``trip_vehicle`` are the runner-vehicle class names an ENABLED run must
    execute (the wrapper carries the assembly - the established
    ``MODEL_BY_CORE`` vehicles keep their bare cores, so the runner must
    select the wrapper by name). ``init_policy`` is the TASK-20260923-01
    P1 initialization-policy selection executed
    (``coupled_steady_state`` or ``bounded_startup``): the runners record
    it in the manifest ``overrides`` as ``outerAnnulusInitPolicy`` (the
    runner-request surface, alongside ``annularFlowCommand`` - the dataset
    record itself stays the verbatim plant identity) and the effective
    vehicle rides the manifest ``model_name``; both are
    fingerprint-active, so a result directory is never reusable across
    initialization policies. The DISABLED default is the all-``None``
    contract -- historical manifest shape, column set, and vehicle mapping
    -- never a disabled-shape record.
    """

    manifest_fields: "dict[str, Any] | None" = None
    shape: "OuterAnnulusRunShape | None" = None
    vehicle: "str | None" = None
    trip_vehicle: "str | None" = None
    init_policy: "str | None" = None

    @property
    def enabled(self) -> bool:
        """True when the plant's outer-annulus dataset is enabled."""
        return self.manifest_fields is not None


def normalize_outer_annulus_init_policy(
    value: "str | None",
) -> str:
    """Normalize/validate an outer-annulus initialization policy selection.

    ``None`` (no explicit selection) resolves the shipped default
    (:data:`DEFAULT_OUTER_ANNULUS_INIT_POLICY`); unknown names raise
    ``ValueError`` naming the accepted policies.
    """

    if value is None:
        return DEFAULT_OUTER_ANNULUS_INIT_POLICY
    policy = str(value).strip().lower()
    if policy not in OUTER_ANNULUS_INIT_POLICIES:
        raise ValueError(
            f"unknown outer-annulus initialization policy {value!r}; "
            f"expected one of {list(OUTER_ANNULUS_INIT_POLICIES)}"
        )
    return policy


def outer_annulus_model_for(
    core_model: str,
    *,
    init_policy: "str | None" = None,
) -> str:
    """Outer-annulus production vehicle name for ``core_model`` (P8).

    ``init_policy`` (TASK-20260923-01 P1) selects the coupled-steady-state
    vehicle (``coupled_steady_state``, the default) or the bounded-startup
    twin (``bounded_startup``); ``None`` resolves the default. Raises
    ``KeyError`` for cores without a plan §10.6 CoreVesselAssembly
    vehicle (the runner contract refuses those before any run; this lookup
    is for call sites that already hold an enabled contract) and
    ``ValueError`` for unknown policy names.
    """
    policy = normalize_outer_annulus_init_policy(init_policy)
    key = normalize_core_key(core_model)
    table = (
        OUTER_ANNULUS_MODEL_BY_CORE
        if policy == "coupled_steady_state"
        else OUTER_ANNULUS_BOUNDED_STARTUP_MODEL_BY_CORE
    )
    try:
        return table[key]
    except KeyError:
        raise KeyError(
            f"no outer-annulus wrapper vehicle for core {key!r}; the "
            f"supported P8 vehicle set is {sorted(OUTER_ANNULUS_MODEL_BY_CORE)}"
        ) from None


def outer_fuel_annulus_run_contract(
    core_model: str,
    *,
    plant: "dict[str, Any] | None" = None,
    plant_id: str = "msrr",
    root: "Path | None" = None,
    init_policy: "str | None" = None,
) -> OuterAnnulusRunContract:
    """Derive the outer-annulus manifest record, run shape, and vehicles.

    The single junction between a production runner and the outer-annulus
    program (TASK-20260917-01 P8; plan §11.2), cloned from
    :func:`radial_run_contract`: from the loaded plant (``plant=None`` loads
    the same catalog the segmented emitter consumes; ``root`` relocates the
    ``data/`` tree for fixture decks) it derives:

    1. ``manifest_fields`` -- the production
       :func:`outer_annulus_manifest_fields` record (``None`` when the
       block is absent/disabled, so disabled manifests keep their
       historical field set);
    2. ``shape`` -- the matching :class:`OuterAnnulusRunShape` (``None``
       when disabled, so disabled compact CSV column sets stay
       byte-identical; P2 carries the fissionACTIVE decision (configured
       AND nonzero heat-deposition distribution) as ``fission_enabled``
       so the fission compact columns attach exactly when the split is
       structurally live);
    3. ``vehicle`` / ``trip_vehicle`` -- the wrapper class names the runner
       must execute (``None`` when disabled: the established
       ``MODEL_BY_CORE`` / ``TRIP_MODEL_BY_CORE`` mapping stands);
    4. ``init_policy`` -- the TASK-20260923-01 P1 initialization-policy
       selection executed (``coupled_steady_state`` default, or the
       explicitly requested ``bounded_startup`` twin). The policy rides
       the runner manifests as the ``outerAnnulusInitPolicy`` override
       (the runner-request surface, alongside ``annularFlowCommand``)
       and the effective vehicle rides the manifest ``model_name``;
       both are fingerprint-active, so a result directory is never
       reusable across initialization policies. The plant dataset record
       itself (``manifest_fields``) stays the verbatim
       :func:`outer_annulus_manifest_fields` identity (plan §11.1).

    ``init_policy=None`` resolves the shipped default (coupled steady
    state); an unknown name raises ``ValueError`` before anything is
    derived.

    Fail-closed BEFORE anything is returned, naming the configuration path
    and the offending value (plan §12.6): a 9R selection (no outer-annulus
    vehicle exists and plant-driven 9R activation is refused by name), an
    enabled dataset whose ``first_production_target`` differs from the
    selected core (the executed vehicle would not carry the configuration
    the manifest claims - scenario/runner identity vs generated identity),
    and an enabled dataset on a core whose runner vehicle cannot execute
    it (only the plan §10.6 ``1r10seg`` wrapper exists in P8; ``1r`` and
    ``r5x5_z10`` are owner follow-ups).
    """

    cli_key = normalize_core_key(core_model)
    yaml_key = CLI_TO_YAML_CORE.get(cli_key, cli_key)
    block_path = OUTER_ANNULUS_BLOCK_KEY
    policy = normalize_outer_annulus_init_policy(init_policy)
    if plant is None:
        plant = load_plant(plant_id, root=root)
    record = outer_annulus_manifest_fields(plant)
    if record is None:
        # Disabled/absent dataset: the historical manifest shape, column
        # set, and vehicle mapping.
        return OuterAnnulusRunContract()
    label = f"plant {plant_id!r}"

    # --- fail-closed runner/core refusals (before ANY return) -------------
    if cli_key == "9r":
        fail(
            label,
            block_path,
            "the production runner refuses an enabled outer_fuel_annulus "
            "dataset for cores.r9 (9R): plant-driven 9R outer-annulus "
            "activation is refused fail-closed (plan §3.1; card A12 - the "
            "four 9R zone chains have no sourced zone-to-outer-boundary "
            "mapping) and no 9R runner vehicle carries the "
            "CoreVesselAssembly wrapper; got "
            f"firstProductionTarget={record.get('firstProductionTarget')!r}",
        )
    target = str(record.get("firstProductionTarget"))
    if target != yaml_key:
        fail(
            label,
            f"{block_path}.first_production_target",
            f"the enabled outer_fuel_annulus dataset targets core {target!r} "
            f"but the runner selected core {yaml_key!r}: the executed "
            "vehicle would not carry the configuration the run manifest "
            "claims (scenario identity must equal the generated identity - "
            "plan §11.1)",
        )
    try:
        vehicle = outer_annulus_model_for(core_model, init_policy=policy)
    except KeyError:
        fail(
            label,
            f"{block_path}.first_production_target",
            f"no production runner vehicle carries the outer-annulus "
            f"wrapper for core {cli_key!r} (an enabled configuration on a "
            "vehicle that cannot execute it is refused - plan §11.2); the "
            f"supported P8 vehicle set is "
            f"{sorted(OUTER_ANNULUS_MODEL_BY_CORE)} "
            "(the plan §10.6 first production target wrapper)",
        )
    trip_vehicle = OUTER_ANNULUS_TRIP_MODEL_BY_CORE.get(cli_key)
    if trip_vehicle is None:
        # An SS vehicle without a trip counterpart is the documented P8
        # state (plan §10.6: trip only after SS + low-flow verification);
        # the trip selection refuses later, at the trip-model lookup.
        trip_vehicle = None

    shape = OuterAnnulusRunShape(
        enabled=True,
        n_chan=int(record["nChan"]),
        n_seg=int(record["nSeg"]),
        cavity_enabled=bool(record["cavityEnabled"]),
        # P2 (TASK-20260922-03; review §4 High-2): the configured-vs-active
        # fission decision, made ONCE here via
        # helpers.plant_config.outer_annulus_fission_active (configured AND
        # a nonzero heat-deposition distribution -- the same conjunction
        # the Modelica fissionOn switch evaluates). The shape's
        # fission_enabled is the ACTIVE image (not the manifest record's
        # CONFIGURED fissionEnabled flag, which stays on the record with
        # both vectors), so every runner's result_variables_for call
        # appends the compact FISSION columns exactly when the split is
        # structurally live -- a fission-enabled all-zero deck rides the
        # enabled-no-fission shape, matching the folded Modelica outputs.
        fission_enabled=outer_annulus_fission_active(
            bool(record["fissionEnabled"]),
            record["fissionHeatDepositionFraction"],
        ),
    )
    # TASK-20260923-01 P1: the contract keeps the plant dataset record
    # verbatim (plan §11.1: the contract record IS the plant manifest
    # record - one identity surface), so the manifest record feeds
    # build_run_manifest(outer_annulus_config=...) unchanged. The
    # initialization policy is a RUNNER-request surface (like
    # annularFlowCommand): the runners record it as the
    # ``outerAnnulusInitPolicy`` manifest override and the effective
    # vehicle rides the manifest ``model_name`` - both fingerprint-active,
    # so a result directory is never reusable across policies while the
    # dataset record stays the verbatim plant identity.
    return OuterAnnulusRunContract(
        manifest_fields=record,
        shape=shape,
        vehicle=vehicle,
        trip_vehicle=trip_vehicle,
        init_policy=policy,
    )


# ---------------------------------------------------------------------------
# Vehicle names (core_model -> SegmentedMSR.Reactors classes).
# ---------------------------------------------------------------------------
#: Full-loop trimmed rigs -- freq sweeps, step/flow transients, startup rides.
MODEL_BY_CORE: dict[str, str] = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS",
    # 1-channel x 10-axial-segment 1R core (TASK-20260906-01): the segmented
    # package only (no legacy counterpart exists).
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS",
    # 5x5-radial (25-channel, triangular pitch) x 10-axial-segment 1R core
    # (TASK-20260906-02): the segmented package only (no legacy counterpart
    # exists).
    "r5x5_z10": "SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS",
}

#: UHX-demand trip vehicles (S1 wrapper pair; structural ``numUhxSteps = 2``
#: with demand {1e6 -> 0} W at t = {0, 4000} s and DHRS engagement at 4000 s,
#: mirroring core/MSRR.mo:1131-1152). These CANNOT be reached via overrides.
TRIP_MODEL_BY_CORE: dict[str, str] = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxTripThermalSS",
    # 10-segment companion of R1MSRRuhxTrimThermalSS (TASK-20260906-01).
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegTripThermalSS",
    # 10-segment 5x5-radial companion of R5x5Z10MSRRuhxTrimThermalSS
    # (TASK-20260906-02).
    "r5x5_z10": "SegmentedMSR.Reactors.R5x5Z10MSRRuhxTripThermalSS",
}


#: Flow-transient vehicles whose UHX demand follows the pump (Results II
#: flow cases with ``uhx_demand_follows_flow``; the scaled msrr_1gw plant):
#: the trim rigs with a structural ``numUhxSteps = 2`` demand {P,
#: uhxFollowFraction*P} at {0, uhxFollowTime}; the runner sets both at
#: runtime (defaults 1 / 1e12 s keep the demand at P).
FLOW_FOLLOW_MODEL_BY_CORE: dict[str, str] = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxFlowFollowThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxFlowFollowThermalSS",
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegFlowFollowThermalSS",
    "r5x5_z10": "SegmentedMSR.Reactors.R5x5Z10MSRRuhxFlowFollowThermalSS",
}


def normalize_core_key(core_model: str) -> str:
    """Normalize/validate a ``core_model`` CLI value ('1R' -> '1r')."""
    key = core_model.strip().lower()
    if key not in MODEL_BY_CORE:
        raise KeyError(
            f"Unknown segmented core_model {core_model!r}; "
            f"expected one of {list(MODEL_BY_CORE)}"
        )
    return key


def model_for(core_model: str) -> str:
    """Full-loop trim-rig vehicle name for ``core_model``."""
    return MODEL_BY_CORE[normalize_core_key(core_model)]


def trip_model_for(core_model: str) -> str:
    """UHX-trip scenario vehicle name for ``core_model``."""
    return TRIP_MODEL_BY_CORE[normalize_core_key(core_model)]


def flow_follow_model_for(core_model: str) -> str:
    """Flow-transient vehicle with a pump-following UHX demand for ``core_model``."""
    return FLOW_FOLLOW_MODEL_BY_CORE[normalize_core_key(core_model)]


# ---------------------------------------------------------------------------
# mos-text builders (omc 1.27 environment: simulate() scripting is broken
# system-wide; use buildModel(..., tolerance=...) then run the generated
# executable directly. Tolerance is baked at BUILD time; -rtol/-atol are NOT
# runtime flags. The built executable's file name equals the dotted class
# name, e.g. 'SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS'.)
# ---------------------------------------------------------------------------

def library_load_lines(indent: str = "") -> list[str]:
    """One ``loadFile`` line per library file, PlantData then SegmentedMSR."""
    return [f'{indent}loadFile("{name}");' for name in SEGMENTED_LIBRARY_FILES]


def build_check_mos(
    class_names: "list[str] | tuple[str, ...]",
    *,
    library_files: "tuple[str, ...] | list[str]" = SEGMENTED_LIBRARY_FILES,
) -> str:
    """Build a check-sweep .mos text.

    Loads ``library_files`` (pass ``(SMD_MSR_Modelica.mo,
    SegmentedMSR.mo)`` style tuples at the call site for side-by-side clash
    probes), then ``checkModel`` + ``getErrorString()`` for every class.
    MARK lines bracket each command pair so buffered errors cannot leak
    between sections when parsing omc output.
    """
    if not library_files:
        raise ValueError("library_files must name at least one .mo file")
    lines: list[str] = []
    for name in library_files:
        lines.append(f'print("MARK load_{name}\\n");')
        lines.append(f'loadFile("{name}");')
        lines.append("getErrorString();")
    for qual in class_names:
        lines.append(f'print("MARK chk {qual}\\n");')
        lines.append(f"checkModel({qual});")
        lines.append("getErrorString();")
    return "\n".join(lines) + "\n"


def modelica_instance(
    model_name: str,
    *,
    poison_tracking: bool = False,
    poison_feedback: bool = False,
) -> str:
    """Modelica instance expression for ``buildModel``.

    Poison flags are structural (``Evaluate=true``) on the vehicles, so
    they must be applied at build time. Default-off returns ``model_name``
    unchanged so existing mos-text assertions stay byte-identical.

    Fail-closed (TASK-20260912-01 P1): feedback without tracking raises.
    The vehicles' own feedback-implies-tracking assert sits outside the
    conditional ``HomogeneousPoisons`` component, but this builder refuses
    the invalid combination before an .mos file is ever written so no
    executable can be constructed from it.
    """

    if not poison_tracking and not poison_feedback:
        return model_name
    if poison_feedback and not poison_tracking:
        raise ValueError(
            f"refusing {model_name!r}: enablePoisonFeedback=true with "
            "enablePoisonTracking=false is not a valid combination "
            "(poison feedback requires poison tracking; the Modelica "
            "vehicles assert the same invariant at initialization)"
        )
    tracking = "true" if poison_tracking else "false"
    feedback = "true" if poison_feedback else "false"
    return (
        f"{model_name}(enablePoisonTracking={tracking},"
        f"enablePoisonFeedback={feedback})"
    )


# ---------------------------------------------------------------------------
# Build-bound runtime overrides (review 2026-10-01 M1).
#
# The poison switches are ``Evaluate=true`` on every segmented vehicle, so
# :func:`modelica_instance` binds them as buildModel modifiers. The generated
# executable reports a runtime ``-override`` of such a parameter as "not
# possible to override", which ``helpers.omc_log.check_overrides_applied``
# refuses. The runners therefore keep the requested poison values in the run
# manifest (the request identity) but hand the executable only the keys it
# can apply: a build-bound key is dropped from the runtime payload when its
# requested value equals the value the build script compiled in, and refused
# when it differs, so a request the executable does not carry never passes.
# ---------------------------------------------------------------------------

#: Vehicle parameters evaluated at translation that :func:`modelica_instance`
#: binds at build time, mapped to the vehicles' declared default -- the
#: compiled value when the build script binds no modifier
#: (``parameter Boolean enablePoison* = false annotation(Evaluate = true)``
#: on every SegmentedMSR vehicle).
BUILD_BOUND_PARAMETER_DEFAULTS: dict[str, str] = {
    "enablePoisonTracking": "false",
    "enablePoisonFeedback": "false",
}

_BUILD_MODEL_CALL = re.compile(
    r"buildModel\(\s*(?P<model>[A-Za-z_][\w.]*)\s*(?:\((?P<mods>[^()]*)\))?\s*,"
)


def build_bound_parameters(build_script: str) -> dict[str, str]:
    """Compiled values of the build-bound parameters in a buildModel script.

    Parses the single ``buildModel(<model>[(<modifiers>)], ...)`` call of
    ``build_script`` (the text :func:`build_smoke_mos` or the frequency
    runner writes) and returns :data:`BUILD_BOUND_PARAMETER_DEFAULTS`
    overlaid with the modifiers it binds (lower-cased values). Raises
    ``ValueError`` unless exactly one buildModel call is present.
    """

    calls = list(_BUILD_MODEL_CALL.finditer(build_script or ""))
    if len(calls) != 1:
        raise ValueError(
            "cannot determine the compiled values of the build-bound "
            f"parameters {sorted(BUILD_BOUND_PARAMETER_DEFAULTS)}: expected "
            f"exactly one buildModel(...) call in the build script, found "
            f"{len(calls)}"
        )
    compiled = dict(BUILD_BOUND_PARAMETER_DEFAULTS)
    for part in (calls[0].group("mods") or "").split(","):
        name, sep, value = part.partition("=")
        name = name.strip()
        if sep and name in compiled:
            compiled[name] = value.strip().lower()
    return compiled


def runtime_override_payload(payload: str, *, build_script: str) -> str:
    """The ``-override`` payload the generated executable receives.

    ``payload`` is the requested comma-separated ``key=value`` list (what the
    run manifest records). Build-bound keys
    (:data:`BUILD_BOUND_PARAMETER_DEFAULTS`) are removed when the requested
    value equals the value compiled in by ``build_script``
    (:func:`build_bound_parameters`); a differing value raises
    ``ValueError`` naming the key, the requested and the compiled value.
    Every other entry is kept verbatim and in order, so a payload without
    build-bound keys is returned unchanged (and ``build_script`` is not
    parsed).
    """

    if not payload:
        return payload
    entries = payload.split(",")
    names = [entry.partition("=")[0].strip() for entry in entries]
    if not any(name in BUILD_BOUND_PARAMETER_DEFAULTS for name in names):
        return payload
    compiled = build_bound_parameters(build_script)
    kept: list[str] = []
    for entry, name in zip(entries, names):
        if name not in compiled:
            kept.append(entry)
            continue
        requested = entry.partition("=")[2].strip().lower()
        if requested != compiled[name]:
            raise ValueError(
                f"runtime override {name}={requested} differs from the value "
                f"the build compiled in ({name}={compiled[name]}); {name} is "
                "evaluated at translation (Evaluate=true), so the executable "
                "cannot apply it -- bind it in the buildModel instance instead"
            )
    return ",".join(kept)


def build_smoke_mos(
    model_name: str,
    *,
    tolerance: float = 1e-10,
    extra_model_files: "tuple[str, ...] | list[str]" = (),
    poison_tracking: bool = False,
    poison_feedback: bool = False,
) -> str:
    """buildModel-only .mos text for one model (smoke-session builder).

    Run the produced executable afterwards, e.g.::

        ./<model_name> -outputFormat=csv -r=out.csv -stopTime=<N>

    (exit code 0 + result file present + no hard assertion == green smoke).
    ``extra_model_files`` are loaded after the library pair (scenario
    wrappers that resize ``numUhxSteps``).
    """
    extra_lines: list[str] = []
    for name in extra_model_files:
        extra_lines.append(f'print("MARK load_{name}\\n");')
        extra_lines.append(f'loadFile("{name}");')
        extra_lines.append("getErrorString();")
    return "\n".join(
        [
            'print("MARK load_seg\\n");',
            *library_load_lines(),
            "getErrorString();",
            *extra_lines,
            f'print("MARK build {model_name}\\n");',
            f"buildModel({modelica_instance(model_name, poison_tracking=poison_tracking, poison_feedback=poison_feedback)}, tolerance = {tolerance!r});",
            "getErrorString();",
        ]
    ) + "\n"


# ---------------------------------------------------------------------------
# Segmented CSV column candidates (plot-side integration, S3+).
#
# The rigs expose FIXED overlay result columns plus named component signals;
# these tuples are RESOLUTION CANDIDATES, not guarantees -- CSV headers may be
# quoted/decorated depending on the collector, so downstream matchers try them
# in order against cleaned headers.
# ---------------------------------------------------------------------------

#: Fixed overlay columns written by the rigs themselves (plan §9.4 naming).
CSV_OVERLAY_COLUMNS_BY_CORE: dict[str, tuple[str, ...]] = {
    "1r": (
        "nOut",
        "rhoCircPcm",
        "TF1",
        "TF2",
        "TG",
        "TinCore",
        "ToutCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
    # NOTE: no TF1/TF2/TG on the 9R rig -- zone outlets/plenum instead.
    "9r": (
        "nOut",
        "rhoCircPcm",
        "TZout[1]",
        "TZout[2]",
        "TZout[3]",
        "TZout[4]",
        "TPot",
        "ToutPlenum",
        "TinCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
    # 1r10seg (TASK-20260906-01): R1MSRRuhx10SegTrimThermalSS exposes the
    # SAME fixed 1R overlay column set as the 2-seg 1R rig -- TF1/TF2 are the
    # 5+5 fuel-node tap means and TG the 10-segment graphite mean (no
    # TZout/TPot/ToutPlenum on this rig).
    "1r10seg": (
        "nOut",
        "rhoCircPcm",
        "TF1",
        "TF2",
        "TG",
        "TinCore",
        "ToutCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
    # r5x5_z10 (TASK-20260906-02): R5x5Z10MSRRuhxTrimThermalSS exposes the
    # SAME fixed 1R overlay column set as the 2-seg 1R rig -- TF1/TF2 are
    # the 5+5 fuel-node tap means and TG the 10-segment graphite mean (no
    # TZout/TPot/ToutPlenum on this rig).
    "r5x5_z10": (
        "nOut",
        "rhoCircPcm",
        "TF1",
        "TF2",
        "TG",
        "TinCore",
        "ToutCore",
        "sumInW",
        "sumOutW",
        "eResid",
    ),
}

#: Reactor-power column candidates (legacy plots used fuelNode/grapNode paths;
#: segmented power lives on the PowerBlock).
CSV_POWER_COLUMN_CANDIDATES: tuple[str, ...] = (
    "pb.reactorPower",
    "pb.fissionPower.P",
)

#: Core-capability table (TASK-20260908-01 P6 item 4): the cores whose
#: segmented rig exposes the 1R fixed overlay column set declared above
#: (``TF1``/``TF2``/``TG`` fixed taps plus ``TinCore``/``ToutCore`` -- never
#: the 9R ``TZout[1..4]``/``TPot``/``ToutPlenum`` set). ``1r`` itself plus
#: the segmented-only cores whose rigs mirror it (see the per-core comments
#: on :data:`CSV_OVERLAY_COLUMNS_BY_CORE`). Consumers -- e.g. the transients
#: plotter's segmented column resolution -- take this table instead of
#: hard-coded membership tuples, so a future core's column surface is
#: declared here once.
ONE_R_COLUMN_SET_CORES: tuple[str, ...] = ("1r", "1r10seg", "r5x5_z10")

#: Total temperature-feedback column candidates per core (1R single feedback;
#: 9R aggregated as RF1..RF9 -- summing is the consumer's job).
CSV_FEEDBACK_COLUMNS_BY_CORE: dict[str, tuple[str, ...]] = {
    "1r": ("fb.TotalTempFeedback",),
    "9r": tuple(f"rf{i}.TotalTempFeedback" for i in range(1, 10)),
    # 1r10seg (TASK-20260906-01): single ReactivityFeedback instance ``fb``
    # (the 5+5 tap mapping lives inside the rig), exactly like 1R.
    "1r10seg": ("fb.TotalTempFeedback",),
    # r5x5_z10 (TASK-20260906-02): single ReactivityFeedback instance ``fb``
    # (the 5+5 tap mapping lives inside the rig), exactly like 1R.
    "r5x5_z10": ("fb.TotalTempFeedback",),
}


def overlay_columns_for(core_model: str) -> tuple[str, ...]:
    """Fixed overlay column names for ``core_model``."""
    return CSV_OVERLAY_COLUMNS_BY_CORE[normalize_core_key(core_model)]


def feedback_columns_for(core_model: str) -> tuple[str, ...]:
    """Temperature-feedback column candidates for ``core_model``."""
    return CSV_FEEDBACK_COLUMNS_BY_CORE[normalize_core_key(core_model)]


# ---------------------------------------------------------------------------
# Result-variable contract (TASK-20260908-01 P4).
#
# Routine 5x5 runs previously emitted the full simulation state as a CSV --
# 15,629 columns and 127,752,170 bytes for the recorded parity run -- while
# the runners and plotters consume a small fixed set of columns.  This
# section is the single source of truth for the *result-variable contract*:
# the minimal column set (time + required physical outputs + acceptance
# diagnostics + provenance fields) each workflow needs from each core's
# result CSV, and the two output paths that enforce it:
#
# 1. RUNTIME FILTER (preferred).  The generated OpenModelica executable
#    accepts ``-variableFilter=<regex>`` -- a result-writer-level filter.
#    It changes which columns are WRITTEN, never the integration itself:
#    the filtered trajectory carries the same rows/time grid as the
#    unfiltered one on the same build, so filtering introduces no event
#    surfaces and no extra solver buffer pressure.  Verified behavior on
#    omc 1.27.0: the runtime compiles the pattern as a POSIX EXTENDED
#    regular expression (no ``(?:...)`` -- such a pattern silently
#    degrades to the FULL wide output with exit 0, so the pattern builder
#    emits a plain capturing group), always writes ``time`` first, writes
#    the matched columns in its own order, and may emit alias-companion
#    names for alias-equivalent variable pairs.  Support is probed per
#    executable via its ``-help`` flag listing
#    (:func:`executable_supports_variable_filter`), so a runtime without
#    the flag falls back cleanly.
# 2. POST-RUN PROJECTION (fallback / repair).  When the installed runtime
#    does not support the filter, the wide file is written and then
#    immediately projected onto the contract columns
#    (:func:`project_contract_csv`); the raw wide bytes are replaced in
#    place (deleted) once the projection succeeds.  The projection is ALSO
#    the self-healing repair for a runtime filter that did not produce
#    exactly the contract shape (silent ``(?:`` failure or alias
#    companions): runners verify the written header with
#    :func:`missing_contract_columns` + :func:`extra_contract_columns` and
#    project whenever extras appear.  ``--full-result-output`` on the
#    runners restores today's wide output explicitly for diagnostics.
#
# The contract is a plain machine-readable table (:data:`RESULT_CONTRACT`)
# keyed ``workflow -> core_model -> variables``.  Physical variables come
# from the declared rig surface above (fixed overlay columns, power-column
# candidates, temperature-feedback columns), so the contract cannot drift
# from the plotters' resolution logic.  The ``qa`` and ``parity`` entries
# are reference-only: QA check sessions and the P3 kelvin-parity runs are
# driven by recorded omc commands, not the runners, and their recorded
# evidence-of-record is never rewritten here.
#
# Runners record the selected variable set in every run manifest
# (``result_variables`` + ``result_output_mode`` via
# ``helpers.run_results.build_run_manifest``), so provenance states exactly
# which columns a published CSV carries.
#
# Per-workflow/core contract column counts (P4 findings-loop repair: the
# segmented UHX-trip plot panel requires ``pb.decayPower``, so the
# transients workflow grew by one REQUIRED column per core -- verified
# present in every segmented vehicle's wide output; startup/freq contracts
# are unchanged because their plotters never consume decay power):
#
#   workflow    1r    9r    1r10seg    r5x5_z10
#   startup     14    24    14         14
#   freq        14    24    14         14
#   transients  15    25    15         15
#
# (The 9r contract is 10 columns larger than 1r before the repair, 24 vs
# 14: its rig replaces the 1R taps TF1/TF2/TG/ToutCore with the six
# zone-outlet/plenum columns TZout[1..4]/TPot/ToutPlenum (+2) and exposes
# nine feedback channels rf1..rf9 instead of one (+8); after the repair it
# is 25 vs 15.)
# ---------------------------------------------------------------------------

#: Default mode: emit exactly the contract variables.
RESULT_OUTPUT_MODE_CONTRACT = "contract"

#: Diagnostic mode behind ``--full-result-output``: today's wide output.
RESULT_OUTPUT_MODE_FULL = "full"

RESULT_OUTPUT_MODES = (RESULT_OUTPUT_MODE_CONTRACT, RESULT_OUTPUT_MODE_FULL)

#: The installed runtime honored ``-variableFilter`` (result written compact).
MECHANISM_RUNTIME_VARIABLE_FILTER = "runtime_variable_filter"

#: The runtime lacked the filter; the wide output was projected post-run and
#: the raw wide bytes were replaced in place.
MECHANISM_POST_RUN_PROJECTION = "post_run_projection"

#: The time column every contract starts with.
CONTRACT_TIME_COLUMN = "time"

#: Workflows driven by the runners wired to this contract.  ``qa`` and
#: ``parity`` sessions are recorded omc command sessions (see above), not
#: runner code paths.
CONTRACT_RUNNER_WORKFLOWS = ("startup", "freq", "transients")


def _physical_variables_for_core(core_model: str) -> tuple[str, ...]:
    """Contract variables for a runner workflow on ``core_model``."""
    return (
        CONTRACT_TIME_COLUMN,
        *overlay_columns_for(core_model),
        *CSV_POWER_COLUMN_CANDIDATES,
        *feedback_columns_for(core_model),
    )


def _runner_contract() -> dict[str, tuple[str, ...]]:
    return {core: _physical_variables_for_core(core) for core in CORE_KEYS}


#: Decay-power component signal on every segmented vehicle
#: (``SegmentedMSR.Nuclear.PowerBlock.decayPower``, declared/computed in the
#: same PowerBlock equations as ``reactorPower``/``fissionPower.P``).  The
#: segmented UHX-trip plot panel resolves it through
#: ``transients.plot_nonlinear_steps.SEGMENTED_UHX_DECAY_COLUMN_CANDIDATES``
#: and raises on a transients CSV without it, so the transients workflow
#: contract must carry it (P4 findings-loop repair).  Verified present in the
#: recorded wide output of every core's vehicles -- 1r trim rig, 9r trip
#: wrapper, 1r10seg trim rig, and the r5x5_z10 trim/trip pair -- because all
#: transients cases (steps and flows on the trim rigs, uhx_trip on the
#: ``extends``-based trip wrappers) instantiate the same PowerBlock.  It is
#: therefore REQUIRED individually, unlike the power taps, which stay an
#: any-of group.
SEGMENTED_DECAY_POWER_COLUMN = "pb.decayPower"


def _transient_variables_for_core(core_model: str) -> tuple[str, ...]:
    """Transients contract variables for ``core_model``.

    The physical set plus :data:`SEGMENTED_DECAY_POWER_COLUMN`, inserted
    directly after the power-column candidates so the PowerBlock signals
    stay adjacent in the projected CSV.  Startup/freq contracts do NOT
    carry it: their plotters never consume decay power, and the compact
    contract stays minimal per workflow.
    """
    return (
        CONTRACT_TIME_COLUMN,
        *overlay_columns_for(core_model),
        *CSV_POWER_COLUMN_CANDIDATES,
        SEGMENTED_DECAY_POWER_COLUMN,
        *feedback_columns_for(core_model),
    )


def _transients_contract() -> dict[str, tuple[str, ...]]:
    return {core: _transient_variables_for_core(core) for core in CORE_KEYS}


# QA check sessions (reference-only; recorded omc commands, never rewritten
# by the runners): the executable QA checks expose named acceptance
# diagnostics.  The SteadyTrimCheck family (QA.SteadyTrimCheck /
# ..10Seg / ..5x5Z10) taps the neutron population and the two trim
# diagnostics; the CoarseningConsistencyCheck family adds its signed
# difference diagnostics (the chattering-lesson form: signed differences,
# threshold evaluation confined to the tCheck assert bodies).
_QA_TRIM_VARIABLES: tuple[str, ...] = (
    CONTRACT_TIME_COLUMN,
    "pke.n_population.n",
    "rhoCircPcm",
    "devExpect",
)

_QA_COARSENING_10SEG_VARIABLES: tuple[str, ...] = (
    "dTf1",
    "dTf2",
    "dTg1",
    "dTg2",
    "dP",
    "dP10nom",
    "dP2nom",
)

_QA_COARSENING_R5X5_VARIABLES: tuple[str, ...] = (
    "dTf5",
    "dTf10",
    "dTg5",
    "dTg10",
    "dP",
    "dP5nom",
    "dP10nom",
)


def _parity_variables() -> tuple[str, ...]:
    """The pinned P3 parity acceptance columns.

    Single source of truth:
    ``helpers.segmented_parity_evidence.ACCEPTANCE_COLUMNS`` -- the
    evidence-of-record is read-only here.  The tuple is materialized when
    :data:`RESULT_CONTRACT` is built (i.e. at this module's import time);
    ``segmented_parity_evidence`` is stdlib-only and imports nothing from
    this module, so there is no import cycle and no heavy transitive pull.
    """
    from helpers import segmented_parity_evidence

    return tuple(segmented_parity_evidence.ACCEPTANCE_COLUMNS)


RESULT_CONTRACT: dict[str, dict[str, tuple[str, ...]]] = {
    # Runner workflows: the full per-core physical contract.  Transients
    # additionally requires the decay-power tap the segmented UHX-trip plot
    # panel resolves (see _transient_variables_for_core).
    "startup": _runner_contract(),
    "freq": _runner_contract(),
    "transients": _transients_contract(),
    # Reference-only sessions (recorded omc commands).  Entries exist only
    # for the (workflow, core) combinations that actually have recorded
    # sessions; absent combinations raise through result_variables_for.
    "qa": {
        "1r": _QA_TRIM_VARIABLES,
        "1r10seg": _QA_TRIM_VARIABLES + _QA_COARSENING_10SEG_VARIABLES,
        "r5x5_z10": _QA_TRIM_VARIABLES + _QA_COARSENING_R5X5_VARIABLES,
    },
    "parity": {
        # Rig C (R1MSRRuhx10SegTrimThermalSS) and rig D
        # (R5x5Z10MSRRuhxTrimThermalSS): the P3 pinned acceptance-field set.
        "1r10seg": _parity_variables(),
        "r5x5_z10": _parity_variables(),
    },
}


def has_result_contract(workflow: str, core_model: str) -> bool:
    """True when a contract entry exists for ``(workflow, core_model)``."""
    return (
        workflow in RESULT_CONTRACT
        and normalize_core_key(core_model) in RESULT_CONTRACT[workflow]
    )


#: Compact poison-inventory columns appended only when tracking is requested.
#: Default contracts stay poison-free so poison-off CSVs keep their shape.
POISON_RESULT_COLUMNS: tuple[str, ...] = (
    "rhoPoisonPcm",
    "poisons.rhoXePcm",
    "poisons.rhoSmPcm",
    "poisons.C_Xe",
    "poisons.C_Sm",
    "poisons.N_Te",
    "poisons.N_I",
    "poisons.N_Xe",
    "poisons.N_Pm",
    "poisons.N_Sm",
)

# ---------------------------------------------------------------------------
# Intra-channel radial / annular-loop result columns (TASK-20260914-01 P7;
# plan §8.1-§8.3).
#
# The enabled-path contract columns ride the SAME omission pattern as
# ``POISON_RESULT_COLUMNS``: the default (radial-disabled) column sets are
# byte-identical to the historical contract (``result_variables_for`` without
# a radial shape returns exactly the P4 columns), and enabled runs gain the
# radial/loop columns on top. The names are the flattened conditional
# aliases the P6 integration exposes on ``SegmentedMSR.Demos`` vehicles:
# ``SegmentedCore``'s plan §8.1 aggregates (``core.*``), the plan §8.2
# ``ClosedAnnularLoop`` diagnostics (``core.annularLoop.*``, circulating
# mode only), and the plan §8.3 detailed per-cell/per-channel families.
# Every temperature column is kelvin (library-wide convention).
# ---------------------------------------------------------------------------

#: Plan §8.1 compact RADIAL columns: the mean/max pipe and annular-fluid
#: temperatures, the two stored-energy totals, and the three radial interface
#: powers. Present on EVERY enabled vehicle (static and circulating alike).
RADIAL_COMPACT_COLUMNS: tuple[str, ...] = (
    "core.meanTpipe",
    "core.maxTpipe",
    "core.meanTann",
    "core.maxTann",
    "core.U_pipe_total",
    "core.U_annular_total",
    "core.Q_fuelPipe_tot",
    "core.Q_pipeFluid_tot",
    "core.Q_fluidModerator_tot",
)

#: Plan §8.2 compact LOOP columns (circulating mode only - ClosedAnnularLoop
#: exists only there). ``T_HX_in``/``T_HX_out``/``U_HX_loop`` are the HX-
#: conditional subset (:data:`LOOP_HX_COMPACT_COLUMNS`): the P5 structural
#: selection keeps them absent on an HX-disabled loop, so a contract for an
#: adiabatic-circulating run must NOT request them. ``Q_HX`` is always
#: present (exactly 0 on the adiabatic loop). ``balanceRHS`` is the
#: whole-loop physical balance RHS the §10.7/§10.9 closure consumes.
#: Deliberately NOT CSV columns: the channel flow-distribution minimum and
#: maximum (omc structurally folds the ``min``/``max`` of the distributor's
#: parameter vector, so they never reach a result CSV) and the plan's
#: cumulative removed-heat integral (NOT a loop state - the P4/P5 structural
#: state deltas stay exact). Both are reported through the run metadata
#: instead: the radial manifest record carries the declared channel flow
#: fractions, and the demo runner's run summary reports their min/max plus
#: the trapezoid integral of ``core.annularLoop.Q_HX`` (see
#: :func:`integrate_q_hx`).
LOOP_COMPACT_COLUMNS: tuple[str, ...] = (
    "core.annularLoop.mdot_tot",
    "core.annularLoop.T_supply",
    "core.annularLoop.T_return",
    "core.annularLoop.M_loop",
    "core.annularLoop.U_loop",
    "core.annularLoop.Q_HX",
    "core.annularLoop.T_chanOut_max",
    "core.annularLoop.balanceRHS",
)

#: The HX-conditional members of the plan §8.2 loop set: present ONLY when
#: the dedicated annular heat exchanger is enabled (house conditional-
#: variable pattern, ``ClosedAnnularLoop.T_HX_in``).
LOOP_HX_COMPACT_COLUMNS: tuple[str, ...] = (
    "core.annularLoop.T_HX_in",
    "core.annularLoop.T_HX_out",
    "core.annularLoop.U_HX_loop",
)


@dataclass(frozen=True)
class RadialRunShape:
    """Structural radial/loop shape of an enabled run (contract selection).

    Mirrors the plan §2.2 decisions exactly as the committed
    ``SegmentedMSR.Demos`` vehicles bind them: enabled (always true for a
    radial contract), circulating (``annularFluidModeCode = 2``), and the
    dedicated heat exchanger. ``n_chan``/``n_seg`` size the plan §8.3
    detailed per-cell column families. The disabled default is expressed by
    passing ``None`` (no radial shape) to the contract resolver, which keeps
    the historical column set - never by a disabled-shape record.
    """

    enabled: bool = True
    circulating: bool = False
    hx_enabled: bool = False
    n_chan: int = 0
    n_seg: int = 0

    def __post_init__(self) -> None:
        if not self.enabled:
            raise ValueError(
                "RadialRunShape expresses an ENABLED radial run; pass "
                "radial_shape=None for the disabled default contract"
            )
        if self.n_chan <= 0 or self.n_seg <= 0:
            raise ValueError(
                "RadialRunShape requires positive n_chan and n_seg (the "
                "plan §8.3 detailed families are per (channel, axial "
                "segment) cells)"
            )


def radial_contract_columns(shape: "RadialRunShape | None") -> tuple[str, ...]:
    """Compact radial/loop columns for ``shape`` (empty when ``None``).

    Static: :data:`RADIAL_COMPACT_COLUMNS`. Circulating: plus
    :data:`LOOP_COMPACT_COLUMNS`. HX-enabled circulating: plus the
    :data:`LOOP_HX_COMPACT_COLUMNS` conditional subset.
    """

    if shape is None:
        return ()
    columns = list(RADIAL_COMPACT_COLUMNS)
    if shape.circulating:
        columns.extend(LOOP_COMPACT_COLUMNS)
        if shape.hx_enabled:
            columns.extend(LOOP_HX_COMPACT_COLUMNS)
    return tuple(columns)


def radial_detailed_columns(shape: "RadialRunShape | None") -> tuple[str, ...]:
    """Plan §8.3 DETAILED optional columns for ``shape`` (empty when ``None``).

    The per-cell / per-channel flattened names of the P6 surfaces: the pipe
    and annular-fluid temperatures and stored energies per (channel, axial
    segment) cell, the per-cell radial interface powers, the circulating
    loop's per-cell inlet/outlet temperatures and local advective terms,
    the channel flows/outlets/profiles, and the plenum/HX states. Static
    runs carry ``stacks[c,j].Tann``/``U_ann`` (the stack-owned cells);
    circulating runs carry the loop-owned cell names instead. These are
    RESOLUTION CANDIDATES like the overlay columns: the runtime filter
    matches them exactly, and the post-run projection verifies presence.

    Deliberately NOT CSV columns (plan §8.3 "local resistances,
    conductances, and geometry diagnostics"): the stack's ``G_fuelSide``/
    ``G_modSide``/``R_*``/``V_*`` and the loop's channel fractions are
    DECLARED DATA - ``final parameter`` constants that omc structurally
    folds, so they never reach a result CSV in any output mode. They are
    reported through the generated PlantData record and the run manifest
    instead (the radial manifest record carries the dataset identity and
    fingerprints; the runner summary reports the declared distribution).

    Deliberately opt-in (plan §8.3 "detailed optional"): the compact
    contract stays minimal; the diagnostic ``full`` output mode captures
    everything anyway.
    """

    if shape is None:
        return ()
    columns: list[str] = []
    for c in range(1, shape.n_chan + 1):
        for j in range(1, shape.n_seg + 1):
            stack = f"core.stacks[{c},{j}]"
            columns.extend(
                (
                    f"{stack}.Tp",
                    f"{stack}.U_pipe",
                    f"{stack}.Q_fuelPipe",
                    f"{stack}.Q_pipeFluid",
                    f"{stack}.Q_fluidModerator",
                )
            )
            if shape.circulating:
                cell = f"core.annularLoop.chans[{c}].cells[{j}]"
                columns.extend(
                    (
                        f"core.TannCell[{c},{j}]",
                        f"{cell}.T_in.T",
                        f"{cell}.T_out.T",
                        f"{cell}.mdot_a",
                        f"{cell}.Q_advIn",
                        f"{cell}.Q_advOut",
                        f"{cell}.Q_in",
                        f"{cell}.Q_out",
                        f"{cell}.U",
                    )
                )
            else:
                columns.extend(
                    (
                        f"{stack}.Tann",
                        f"{stack}.U_ann",
                    )
                )
    if shape.circulating:
        for c in range(1, shape.n_chan + 1):
            chan = f"core.annularLoop.chans[{c}]"
            columns.extend(
                (
                    f"{chan}.mdotChan",
                    f"{chan}.chanOut.T",
                    f"{chan}.U_chan",
                    f"{chan}.Q_fromPipes",
                    f"{chan}.Q_toModerator",
                    f"{chan}.Q_dep_chan",
                )
            )
            columns.extend(
                f"{chan}.Tprof[{j}]" for j in range(1, shape.n_seg + 1)
            )
        columns.extend(
            (
                "core.annularLoop.supply.T",
                "core.annularLoop.supply.mDotTot",
                "core.annularLoop.returns.T",
                "core.annularLoop.returns.mDotTot",
                "core.annularLoop.driver.mdot",
            )
        )
        if shape.hx_enabled:
            columns.extend(
                (
                    "core.annularLoop.hxAnn.T_HX",
                    "core.annularLoop.hxAnn.Q_HX",
                    "core.annularLoop.hxAnn.QadvIn",
                    "core.annularLoop.hxAnn.QadvOut",
                    "core.annularLoop.hxAnn.U_HX",
                )
            )
    return tuple(columns)


# ---------------------------------------------------------------------------
# Outer-annulus result columns (TASK-20260917-01 P8; plan §11.3).
#
# The enabled-path compact columns ride the SAME omission pattern as
# ``POISON_RESULT_COLUMNS`` / the radial columns: the disabled column sets
# are byte-identical to the historical contract (``result_variables_for``
# without an outer-annulus shape returns exactly the pre-P8 columns), and
# enabled runs append these on top. The names are the production vehicle's
# exposed output columns: the CoupledSS vehicle inherits every plan §10.6
# output from its TrimThermalSS base (TASK-20260923-01 P1 extends-pair),
# so the column contract holds on both initialization policies;
# every temperature is kelvin (library-wide convention).
# ---------------------------------------------------------------------------

#: Plan §11.3 compact OUTER-ANNULUS columns: the plan's literal stems plus
#: its two named-by-description entries - the annulus delayed-neutron source
#: contribution (``DSOuterAnnulus``, the P5 composition's per-group DSannG
#: sum) and the total energy-balance residual including cavity heat
#: (``eResidTotal``). All are exposed by the vehicle whenever the annulus is
#: enabled (the cavity-facing powers report their structural zeros when the
#: fixed cavity itself is off), so ONE compact set covers every enabled
#: configuration. Present in this exact order on every workflow.
#:
#: P5 (TASK-20260918-01; plan §6 Phase 5 items 4-5) appends the
#: actual-vs-declared inventory decomposition (``VActiveCoreM3``,
#: ``VExternalLoopM3``, ``VFuelConfiguredM3``; ``VOuterAnnulusM3`` /
#: ``VFuelTotalM3`` above already carry the annulus and the modeled total)
#: and the whole-power deposition/accumulation set (``QDepositedCoreW``,
#: ``QDecayExternalLoopW``, ``QDHRSRemovedW``, ``UStoresTotalJ``,
#: ``dUStoresDtW``; the annulus decay/fission depositions, the cavity losses,
#: and the UHX removal are the stems above) - so a user can close the
#: whole-plant power balance from the CSV alone. ``eResidTotal`` keeps its
#: name (resolution 8): its meaning became the TRUE transient residual
#: (net outflows minus ``der`` of all modeled thermal stores, scaled by P).
OUTER_ANNULUS_COMPACT_COLUMNS: tuple[str, ...] = (
    "meanTOuterAnnulus",
    "maxTOuterAnnulus",
    "TOuterAnnulusIn",
    "TOuterAnnulusOut",
    "meanTVessel",
    "maxTVessel",
    "QGraphiteToOuterAnnulusW",
    "QOuterAnnulusToVesselW",
    "QVesselToCavityW",
    "QPrimaryLoopToCavityW",
    "QCavityControlW",
    "UOuterAnnulusJ",
    "UVesselJ",
    "VOuterAnnulusM3",
    "VFuelTotalM3",
    "fuelVolumeClosureResidualM3",
    "DSOuterAnnulus",
    "eResidTotal",
    "VActiveCoreM3",
    "VExternalLoopM3",
    "VFuelConfiguredM3",
    "QDepositedCoreW",
    "QDecayExternalLoopW",
    "QDHRSRemovedW",
    "UStoresTotalJ",
    "dUStoresDtW",
)


#: P7 (TASK-20260918-01; plan §6 Phase 7 item 5) compact FISSION columns:
#: the vehicle's delivered fission-split powers plus the P4 policy-visibility
#: outputs, appended AFTER :data:`OUTER_ANNULUS_COMPACT_COLUMNS` ONLY when the
#: run's outer-annulus fission split is structurally ACTIVE
#: (``OuterAnnulusRunShape.fission_enabled`` -- the P2 fissionActive
#: decision: configured AND a nonzero heat-deposition distribution, the
#: same conjunction the Modelica ``fissionOn`` switch evaluates). The
#: enabled-NO-fission output shape stays byte-identical
#: to the pre-fission contract (the 26 committed stems), and the disabled
#: contract stays column-free - one explicit contract per state, never a
#: versioned column reshape on the identity paths. All six stems exist on the
#: SS/trip wrapper vehicles whenever the split is live (the two policy outputs
#: are conditional on the annulus, which the split requires); every temperature
#: is kelvin (library-wide convention).
OUTER_ANNULUS_FISSION_COMPACT_COLUMNS: tuple[str, ...] = (
    "QFissionActiveCoreW",
    "QFissionOuterAnnulusW",
    "QDecayOuterAnnulusW",
    "fissionPowerClosureResidualW",
    "VIrrEffM3",
    "annulusFeedbackPcm",
)


def outer_annulus_contract_columns(
    shape: "OuterAnnulusRunShape | None",
) -> tuple[str, ...]:
    """Compact outer-annulus columns for ``shape`` (empty when ``None``).

    Plan §11.3: every enabled run appends the full compact list (the
    vehicle's cavity-facing columns carry their documented structural zeros
    when the fixed cavity itself is off, so no cavity-conditional split
    exists). P7 (plan §6 Phase 7 item 5) plus P2 (TASK-20260922-03): a
    fission-ACTIVE dataset (``shape.fission_enabled`` -- configured AND a
    nonzero heat-deposition distribution) additionally appends the compact
    fission column set, so the enabled-no-fission output shape stays
    byte-identical to the pre-fission contract and the fission columns
    attach exactly when the split is structurally live (a fission-enabled
    all-zero deck carries the base set only, matching the folded Modelica
    conditional outputs).
    """

    if shape is None:
        return ()
    if shape.fission_enabled:
        return (
            OUTER_ANNULUS_COMPACT_COLUMNS
            + OUTER_ANNULUS_FISSION_COMPACT_COLUMNS
        )
    return OUTER_ANNULUS_COMPACT_COLUMNS


def integrate_q_hx(
    times: "Sequence[float]",
    q_hx: "Sequence[float]",
) -> float:
    """Cumulative annular-HX heat removed [J] (plan §8.2) from sampled
    ``Q_HX`` [W] columns: the composite trapezoid of ``q_hx`` over ``times``.

    The dedicated HX's cumulative removed heat is deliberately NOT a loop
    state (the P4/P5 structural state deltas stay exact); it is this
    post-run integration - the same convention the P6 conservation tests
    use for their independent trapezoids. ``Q_HX`` positive removes heat
    from the annular loop (plan §3.13), so the integral is the removed
    energy. Raises ``ValueError`` on length-mismatched or empty inputs.
    """

    if len(times) != len(q_hx):
        raise ValueError(
            f"integrate_q_hx: times ({len(times)}) and q_hx ({len(q_hx)}) "
            "must have the same length"
        )
    if not times:
        raise ValueError("integrate_q_hx: empty sample series")
    total = 0.0
    for i in range(len(times) - 1):
        total += 0.5 * (float(q_hx[i]) + float(q_hx[i + 1])) * (
            float(times[i + 1]) - float(times[i])
        )
    return total


def result_variables_for(
    workflow: str,
    core_model: str,
    *,
    poison_tracking: bool = False,
    radial_shape: "RadialRunShape | None" = None,
    outer_annulus_shape: "OuterAnnulusRunShape | None" = None,
    detailed: bool = False,
) -> tuple[str, ...]:
    """Contract variables for ``(workflow, core_model)``.

    Raises ``KeyError`` with a named message for combinations without a
    contract (currently only reference-workflow combinations such as
    ``("qa", "9r")`` -- every runner workflow covers every core).
    ``poison_tracking`` appends :data:`POISON_RESULT_COLUMNS`; default
    contracts stay poison-free. ``radial_shape`` (P7; plan §8) appends the
    radial/loop compact columns (and, with ``detailed=True``, the plan
    §8.3 detailed families) for an ENABLED run; ``None`` keeps the
    historical column set byte-identical, so the disabled default never
    changes shape or fingerprint.     ``outer_annulus_shape`` (P8; plan §11.3)
    appends :data:`OUTER_ANNULUS_COMPACT_COLUMNS` for an ENABLED
    outer-annulus run and, when that shape's ``fission_enabled`` marks the
    fissionACTIVE state (P2; P7)
    (:data:`OUTER_ANNULUS_FISSION_COMPACT_COLUMNS` on top of it);
    ``None`` keeps the historical column set
    byte-identical (the same omission pattern).
    """
    key = normalize_core_key(core_model)
    try:
        base = RESULT_CONTRACT[workflow][key]
    except KeyError:
        raise KeyError(
            f"no result-variable contract for workflow {workflow!r} core "
            f"{key!r}; available workflows: {sorted(RESULT_CONTRACT)}, "
            f"available cores per workflow: "
            f"{ {w: sorted(c) for w, c in RESULT_CONTRACT.items()} }"
        ) from None
    extras: list[str] = []
    if poison_tracking:
        extras.extend(POISON_RESULT_COLUMNS)
    extras.extend(radial_contract_columns(radial_shape))
    extras.extend(outer_annulus_contract_columns(outer_annulus_shape))
    if detailed:
        extras.extend(radial_detailed_columns(radial_shape))
    if extras:
        return base + tuple(extras)
    return base


def result_output_mode_for(full_result_output: bool) -> str:
    """Map the ``--full-result-output`` CLI decision to a manifest mode."""
    return (
        RESULT_OUTPUT_MODE_FULL
        if full_result_output
        else RESULT_OUTPUT_MODE_CONTRACT
    )


def variable_filter_regex(variables: "tuple[str, ...] | list[str]") -> str:
    """Build the ``-variableFilter`` regex selecting exactly ``variables``.

    The OpenModelica simulation runtime compiles the filter as a POSIX
    extended regular expression (verified on omc 1.27.0): POSIX ERE has no
    non-capturing groups, and a ``(?:...)`` pattern is silently treated as
    non-matching -- the runtime then writes the FULL wide output and exits 0
    (empirically established; see the P4 phase report).  The pattern
    therefore uses ONE plain capturing group of ``re.escape``-escaped exact
    names (``.`` and ``[``/``]`` become ``\\.``/``\\[``/``\\]``, which
    POSIX ERE defines), which the runtime honors: the written header was
    verified to carry exactly the requested set, in the runtime's own order
    with ``time`` always first.

    Known runtime quirk (omc 1.27.0): requesting one member of an
    alias-equivalent variable pair can also emit the alias's name (e.g.
    ``pb.reactorPower`` pulls in ``sumInW``, which the rig defines as
    ``sumInW = pb.reactorPower``).  Runners therefore verify the written
    header after the run and repair extras through the post-run projection
    instead of trusting the filter blindly
    (:func:`missing_contract_columns` + :func:`extra_contract_columns`).
    """
    names = [str(name) for name in variables]
    if not names:
        raise ValueError("variable_filter_regex requires at least one variable")
    return "^(" + "|".join(re.escape(name) for name in names) + ")$"


def executable_supports_variable_filter(
    exe_path: "str | os.PathLike[str]",
    *,
    timeout_s: float = 60.0,
) -> bool:
    """Probe whether a generated executable's runtime knows ``-variableFilter``.

    Runs ``<exe> -help`` (the simulation runtime prints its flag listing and
    exits before simulating -- verified on omc 1.27; the exit code is
    nonzero, so only the text is inspected) and searches for the flag.  Any
    failure (missing executable, timeout, OSError, or an environment that
    cannot answer the help call) resolves to ``False`` so callers fall back
    to the post-run projection instead of failing the run.
    """
    try:
        completed = subprocess.run(
            [str(exe_path), "-help"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception:  # noqa: BLE001 - the probe must never abort a run
        return False
    text = (completed.stdout or "") + (completed.stderr or "")
    return "-variableFilter" in text


def _clean_header_cell(cell: str) -> str:
    stripped = cell.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        stripped = stripped[1:-1].strip()
    return stripped


def read_csv_header(csv_path: "str | os.PathLike[str]") -> list[str]:
    """Return the cleaned first-row header cells of a result CSV."""
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as h:
        reader = csv.reader(h)
        header = next(reader, None)
    return [_clean_header_cell(cell) for cell in (header or [])]


def missing_contract_columns(
    csv_path: "str | os.PathLike[str]",
    variables: "tuple[str, ...] | list[str]",
) -> list[str]:
    """Contract columns absent from a result CSV's header (empty list = pass).

    The power-column candidates form an any-of group: at least one of
    :data:`CSV_POWER_COLUMN_CANDIDATES` must be present (the plot collectors
    accept any one), while every other contract variable is required
    individually.  A missing column after a contract-mode run means the
    filter or projection silently dropped acceptance data -- callers must
    fail loudly rather than publish.
    """
    header = {name.casefold() for name in read_csv_header(csv_path)}
    wanted = [str(name) for name in variables]
    power_group = {str(p).casefold() for p in CSV_POWER_COLUMN_CANDIDATES}
    missing: list[str] = []
    power_seen = False
    for name in wanted:
        folded = name.casefold()
        if folded in power_group:
            power_seen = power_seen or folded in header
            continue
        if folded not in header:
            missing.append(name)
    if wanted and not power_seen and power_group & {n.casefold() for n in wanted}:
        missing.append("(any of) " + ", ".join(sorted(power_group)))
    return missing


def extra_contract_columns(
    csv_path: "str | os.PathLike[str]",
    variables: "tuple[str, ...] | list[str]",
) -> list[str]:
    """Header columns NOT in the requested contract set (empty list = pass).

    Symmetric complement of :func:`missing_contract_columns`: in contract
    mode the published CSV must carry EXACTLY the contract columns, so a
    runtime filter that silently failed (full wide output, exit 0 -- the
    POSIX-ERE ``(?:`` pitfall) or that emits alias-companion columns is
    detected instead of being published.  Comparison is case-folded on the
    cleaned header names, matching :func:`missing_contract_columns`.
    """
    header = read_csv_header(csv_path)
    wanted = {str(name).casefold() for name in variables}
    return [name for name in header if name.casefold() not in wanted]


@dataclass(frozen=True)
class ProjectionReport:
    """Outcome of :func:`project_result_csv` (for run logs and provenance)."""

    source_path: Path
    columns_before: int
    columns_after: int
    rows_written: int
    variables_found: tuple[str, ...]
    variables_missing: tuple[str, ...]
    bytes_before: int
    bytes_after: int

    def describe(self) -> str:
        return (
            f"projected {self.source_path.name}: {self.columns_before} -> "
            f"{self.columns_after} columns, {self.bytes_before} -> "
            f"{self.bytes_after} bytes, {self.rows_written} data rows"
        )


def project_result_csv(
    csv_path: "str | os.PathLike[str]",
    variables: "tuple[str, ...] | list[str]",
    optional_any_of: "tuple[str, ...] | list[str] | None" = None,
) -> ProjectionReport:
    """Project a wide result CSV in place onto the contract columns.

    Streams the source row-by-row (multi-GB trajectories never load into
    memory), writes ``<source>.contract-tmp`` beside it, and only on success
    replaces the source via ``os.replace`` -- the raw wide bytes are deleted
    (regenerable via ``--full-result-output``), never half-written.  Column
    resolution is first-match on the cleaned header (Modelica headers may be
    quoted); every data row is kept, so time coverage and row counts are
    unchanged and the downstream stop-time tail checks still apply.

    ``optional_any_of`` names an any-of candidate group (contract runner
    workflows: :data:`CSV_POWER_COLUMN_CANDIDATES`).  Members of the group
    may be absent from the header individually -- a rig exposes one of the
    power taps, and :func:`missing_contract_columns` accepts any one -- but
    the projection still fails when NONE of the requested group members is
    present.  Every column outside the group is required.

    Raises ``ValueError`` when a required contract variable is absent from
    the header (the run would silently lose acceptance data) or the file is
    empty.
    """
    source = Path(csv_path)
    variables = [str(name) for name in variables]
    bytes_before = source.stat().st_size
    tmp_target = source.with_name(source.name + ".contract-tmp")
    optional_group: frozenset[str] | None = None
    if optional_any_of is not None:
        optional_group = frozenset(str(n).casefold() for n in optional_any_of)
    try:
        with open(
            source, "r", encoding="utf-8-sig", errors="replace", newline=""
        ) as src, open(tmp_target, "w", encoding="utf-8", newline="") as dst:
            reader = csv.reader(src)
            writer = csv.writer(dst)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"result CSV is empty: {source}")
            cleaned = [_clean_header_cell(cell) for cell in header]
            folded_index: dict[str, int] = {}
            for position, name in enumerate(cleaned):
                # First occurrence wins; duplicates beyond it are dropped.
                folded_index.setdefault(name.casefold(), position)
            resolved: list[int] = []
            projected_names: list[str] = []
            missing: list[str] = []
            group_seen = False
            for name in variables:
                folded = name.casefold()
                idx = folded_index.get(folded)
                if idx is None:
                    if optional_group is not None and folded in optional_group:
                        # Candidate tap absent on this rig: tolerated
                        # individually; the group check below still fires
                        # when the whole group is absent.
                        continue
                    missing.append(name)
                    continue
                resolved.append(idx)
                projected_names.append(name)
                if optional_group is not None and folded in optional_group:
                    group_seen = True
            if missing:
                raise ValueError(
                    f"cannot project {source.name}: contract variables "
                    f"absent from the header: {missing}"
                )
            if (
                optional_group is not None
                and not group_seen
                and optional_group & {n.casefold() for n in variables}
            ):
                raise ValueError(
                    f"cannot project {source.name}: none of the any-of "
                    f"columns {sorted(optional_group)} are present"
                )
            max_idx = max(resolved)
            writer.writerow([cleaned[idx] for idx in resolved])
            rows = 0
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                rows += 1
                if len(row) <= max_idx:
                    # Keep the row shape honest: pad short rows so the
                    # projected CSV stays rectangular for validation.
                    row = row + [""] * (max_idx + 1 - len(row))
                writer.writerow([row[idx] for idx in resolved])
    except BaseException:
        try:
            tmp_target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    bytes_after = tmp_target.stat().st_size
    os.replace(str(tmp_target), str(source))
    return ProjectionReport(
        source_path=source,
        columns_before=len(cleaned),
        columns_after=len(projected_names),
        rows_written=rows,
        variables_found=tuple(projected_names),
        variables_missing=(),
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def project_contract_csv(
    csv_path: "str | os.PathLike[str]",
    variables: "tuple[str, ...] | list[str]",
) -> ProjectionReport:
    """Project a result CSV onto the runner-workflow contract columns.

    Contract-aware wrapper around :func:`project_result_csv`: the power
    columns (:data:`CSV_POWER_COLUMN_CANDIDATES`) form an any-of group
    exactly as in :func:`missing_contract_columns`, so the projection and
    the post-run compliance guard share one set of column semantics.
    Every other requested column is required.
    """
    return project_result_csv(
        csv_path,
        variables,
        optional_any_of=CSV_POWER_COLUMN_CANDIDATES,
    )
