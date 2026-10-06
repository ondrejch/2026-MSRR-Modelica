"""Load MSRR scenario YAML decks under ``data/scenarios/``.

YAML is the authored source of truth for startup / transients / frequency
tables. OpenModelica does not read these files. Runners load them for CLI
defaults, override payloads, and provenance SHAs. Structural array sizes
that ``-override=`` cannot change are emitted by
:mod:`helpers.emit_scenario_wrapper` into the run directory.

Validation is full-depth (see :mod:`helpers.data_validation` for the
shared primitives): raw decks are checked at load time, merged decks are
re-checked in ``_finalize``, ``extends:`` chains are cycle- and
depth-checked with the offending chain named, requested runner kind must
match the loaded scenario kind, and schedule/array invariants (monotonic
times, equal lengths, coupled structural sizes, physical domains) are
enforced.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

import yaml

from helpers.data_validation import (
    SUPPORTED_UNITS,
    check_quantity_tree,
    coerce_number,
    fail,
    require_list,
    require_mapping,
    require_number,
    require_numbers,
    require_strictly_increasing,
)
from helpers.plant_config import (
    ANNULAR_FLUID_MODES,
    FLOW_DIRECTIONS,
    POISON_MATURITY_APPROVED,
    POISON_MATURITY_VALUES,
    RADIAL_GEOMETRY_POLICIES,
    RADIAL_HX_MODEL_FINITE_CONDUCTANCE,
    RADIAL_HX_MODEL_NONE,
    data_root,
    file_digest,
    fingerprint,
    is_quantity,
    poison_dataset_record,
    quantity_value,
    repo_root,
)

_KIND_DIRS = {
    "startup": "startup",
    "transients": "transients",
    "frequency": "freq",
    "campaign": "campaigns",
    # TASK-20260914-01 P7 (plan §12 Phase G): exploratory intra-channel
    # radial demo scenarios (data/scenarios/radial_demos/) - decks that name
    # a committed SegmentedMSR.Demos vehicle and record its structural
    # radial/loop identity. Kept OUT of every existing campaign table (the
    # runners load only their own kind, so a radial_demo deck can never be
    # picked up by a startup/transients/frequency runner).
    "radial_demo": "radial_demos",
}

# The exploratory maturity the radial demo decks must declare (plan §14 /
# card unresolved-items note): demo vehicles carry SYNTHETIC placeholder
# geometry with no physical provenance - never a plant prediction.
RADIAL_DEMO_MATURITY = "exploratory_geometry"

# The committed exploratory vehicle package a radial_demo deck may name
# (refused otherwise by name: no other class carries a structural radial
# record, so a deck naming anything else would mislabel its run).
RADIAL_DEMO_VEHICLE_PREFIX = "SegmentedMSR.Demos."

# rev022 M-6: the tail after the ``SegmentedMSR.Demos.`` prefix must be a
# single-token identifier tail ([A-Za-z0-9_][A-Za-z0-9_.]* with no '/',
# '\', whitespace, or '..' segments) so a deck can never smuggle path
# separators or traversal into the runner's ``workdir / vehicle``
# executable path (see helpers/run_radial_demo._build_vehicle, whose
# stale-binary unlink must stay confined to the scratch workdir).
_RADIAL_DEMO_VEHICLE_TAIL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")

# Fail-closed whitelist of the OpenModelica generated-executable integration
# methods (the ``-s=<method>`` runtime flag, omc 1.27 house toolchain).
# Verified against omc v1.27.0-cmake: these six are the names the simulation
# runtime accepts AND can run from a prebuilt executable (the runtime also
# lists ``symSolver``/``symSolverSsc`` -- which require the ``--symSolver``
# translation flag, ``qss`` [experimental] and ``optimization`` -- which are
# not general integrators; all three are refused here). Deck
# ``numerics.method`` values outside this whitelist refuse at load time, and
# the demo runner re-refuses them before any build, so a fabricated or
# mistyped solver name can never reach an executable or a manifest. The
# committed demo value ``dassl`` is included (review rev019 Medium-high;
# planner resolution 7).
SUPPORTED_SIMULATION_METHODS = (
    "dassl",
    "ida",
    "cvode",
    "gbode",
    "euler",
    "rungekutta",
)

# TASK-20260906-02: r5x5_z10 (5x5-radial x 10-axial-segment 1R core) is its
# own YAML core key.
CLI_TO_YAML_CORE = {"1r": "r1", "9r": "r9", "1r10seg": "r1_10seg", "r5x5_z10": "r5x5_z10"}
YAML_TO_CLI_CORE = {"r1": "1r", "r9": "9r", "r1_10seg": "1r10seg", "r5x5_z10": "r5x5_z10"}

DEFAULT_STARTUP_SCENARIO = "startup"
DEFAULT_TRANSIENTS_SCENARIO = "results_ii"
DEFAULT_FREQUENCY_SCENARIO = "nominal_sweep"
PAPER_FREQUENCY_SCENARIO = "paper_nominal"
SEGMENTED_STARTUP_SCENARIO_ID = "segmented_simplified"

# Maximum number of ``extends:`` hops when resolving a scenario chain.
MAX_EXTENDS_DEPTH = 5

_FREQ_FLAG_ATTRS = (
    ("--freq_min", "freq_min"),
    ("--freq_max", "freq_max"),
    ("--num_freq", "num_freq"),
    ("--sin_mag", "sin_mag"),
    ("--sin_mag_auto", "sin_mag_auto"),
    ("--sin_mag_ref", "sin_mag_ref"),
    ("--sin_mag_min", "sin_mag_min"),
    ("--sin_mag_max", "sin_mag_max"),
    ("--ss_time", "ss_time"),
    ("--stop_time", "stop_time"),
    ("--stop_time_mode", "stop_time_mode"),
    ("--min_cycles_after_ss", "min_cycles_after_ss"),
    ("--output_interval_mode", "output_interval_mode"),
    ("--output_intervals_per_second", "output_intervals_per_second"),
    ("--low_power_threshold", "low_power_threshold"),
    ("--low_power_auto_ss_time_factor", "low_power_auto_ss_time_factor"),
    ("--low_power_auto_min_cycles_after_ss", "low_power_auto_min_cycles_after_ss"),
)


#: The plant whose scenario decks live in the shared ``data/scenarios/`` tree.
DEFAULT_PLANT_ID = "msrr"


def scenarios_root(root: Path | None = None, *, plant: str | None = None) -> Path:
    """Scenario deck root: ``data/scenarios/`` for the default plant, else the
    plant-scoped ``data/plants/<plant>/scenarios/`` tree (scaled plants carry
    their own decks because absolute powers and source strengths differ)."""

    if plant is None or plant == DEFAULT_PLANT_ID:
        return data_root(root) / "scenarios"
    return data_root(root) / "plants" / str(plant) / "scenarios"


def available_plants(root: Path | None = None) -> list[str]:
    """Plant ids under ``data/plants/`` (directories holding a plant.yaml)."""

    base = data_root(root) / "plants"
    return sorted(p.name for p in base.iterdir() if (p / "plant.yaml").is_file()) if base.is_dir() else []


def add_plant_argument(parser: Any) -> None:
    """The runners' shared ``--plant`` option (segmented package only)."""

    parser.add_argument(
        "--plant",
        type=str,
        default=DEFAULT_PLANT_ID,
        help=(
            f"Plant deck under data/plants/ (default: {DEFAULT_PLANT_ID}; available: "
            f"{', '.join(available_plants()) or DEFAULT_PLANT_ID}). A plant other than "
            f"{DEFAULT_PLANT_ID} requires --package segmented, loads its own generated "
            "core/generated/<plant>/SegmentedMSR_PlantData.mo, resolves scenarios in "
            "data/plants/<plant>/scenarios/ (no fallback to data/scenarios/), and writes "
            "default outputs under 00runs/segmented/<plant>/."
        ),
    )


def plant_package_refusal(plant: str | None, package: str) -> str | None:
    """Name an unsupported (plant, package) pair, or None."""

    plant = plant or DEFAULT_PLANT_ID
    if plant not in available_plants():
        return f"--plant {plant!r} is not a plant deck under data/plants/ (available: {available_plants()})"
    if plant != DEFAULT_PLANT_ID and str(package) != "segmented":
        return (f"--plant {plant!r} requires --package segmented: the legacy lumped models "
                f"are qualified for the {DEFAULT_PLANT_ID} plant only")
    return None


def scenario_plant_for_path(path: Path, root: Path | None = None) -> str | None:
    """Plant id of a deck under ``data/plants/<plant>/scenarios/``, else None."""

    try:
        rel = Path(path).resolve().relative_to((data_root(root) / "plants").resolve())
    except ValueError:
        return None
    parts = rel.parts
    return parts[0] if len(parts) > 2 and parts[1] == "scenarios" else None


def yaml_core_key(core_model: str) -> str:
    """Map CLI ``1r``/``9r``/``1r10seg``/``r5x5_z10`` onto the YAML
    ``r1``/``r9``/``r1_10seg``/``r5x5_z10`` core keys."""

    key = str(core_model).strip().lower()
    if key in CLI_TO_YAML_CORE:
        return CLI_TO_YAML_CORE[key]
    if key in YAML_TO_CLI_CORE:
        return key
    raise KeyError(f"unknown core_model {core_model!r}")


def cli_core_key(yaml_core: str) -> str:
    key = str(yaml_core).strip().lower()
    if key in YAML_TO_CLI_CORE:
        return YAML_TO_CLI_CORE[key]
    if key in CLI_TO_YAML_CORE:
        return key
    raise KeyError(f"unknown yaml core {yaml_core!r}")


def scenario_core_refusal(
    scenario: Mapping[str, Any],
    core_model: str,
    *,
    allow_unlisted: bool = False,
) -> str | None:
    """Name a (scenario, core) combination the deck does not list, or ``None``.

    Scenario decks declare the cores they apply to via ``applies_to``
    (``<plant>.<core>`` entries validated at load time by
    :func:`_validate_applies_to`). Runners call this after the CLI-to-YAML
    core-key conversion (:data:`CLI_TO_YAML_CORE`) and BEFORE wrapper
    generation or any simulation/omc process, so an unsupported combination
    fails with a named error instead of a mid-run failure (the
    poisoned-``subprocess.run`` refusal pattern of
    ``tests/test_10seg_legacy_refusal.py``). A deck without ``applies_to``
    is not enforced. ``allow_unlisted=True`` records a deliberate override
    (``--allow-unlisted-core``) and returns ``None``; callers must record
    that override in their run provenance.
    """

    applies = scenario.get("applies_to")
    if not isinstance(applies, list) or not applies:
        return None
    yaml_core = yaml_core_key(core_model)
    listed = {str(entry).rsplit(".", 1)[-1] for entry in applies}
    if yaml_core in listed or allow_unlisted:
        return None
    scenario_id = str(scenario.get("id") or "<unknown>")
    return (
        f"--core_model {core_model!r} ({yaml_core}) is not listed in "
        f"scenario {scenario_id!r} applies_to {sorted(applies)}; re-run "
        "with --allow-unlisted-core to override deliberately."
    )


# ---------------------------------------------------------------------------
# Shared segmented-only-core capability table + legacy-mode refusal helpers
# (TASK-20260908-01 P6 item 4). The four runner/emitter entry points
# (``startup/runMSRR.py``, ``freq/runFreqNominalParallel.py``,
# ``transients/run_nonlinear_steps.py``, ``helpers/emit_scenario_wrapper.py``)
# previously each hard-coded the same core ``choices`` tuple, the same
# per-core refusal clauses, and a near-identical refusal message; the tables
# and builders below are their single source of truth. The transients
# plotter consumes the vehicle-column capability through
# ``helpers.segmented_runs.ONE_R_COLUMN_SET_CORES`` instead (it has no
# legacy refusal). NO behavior change: the message shape, clause texts, and
# argparse ``--help`` rendering are byte-identical to the pre-consolidation
# strings, which ``tests/test_10seg_legacy_refusal.py`` pins while poisoning
# ``subprocess.run`` (refusals stay pre-omc).
# ---------------------------------------------------------------------------

#: CLI core keys accepted by every runner/parser -- single source of truth
#: for the ``choices=`` tuples (order matters: argparse renders it verbatim
#: as the ``{1r,9r,1r10seg,r5x5_z10}`` metavar that the refusal suites pin,
#: and :data:`CORE_KEYS` in ``helpers/segmented_runs.py`` mirrors the same
#: set on the segmented code path).
CORE_CHOICES: tuple[str, ...] = tuple(CLI_TO_YAML_CORE)

#: Cores that exist ONLY in the standalone SegmentedMSR package (no legacy
#: ``SMD_MSR_Modelica.mo``/``MSRR.mo`` vehicle): every runner refuses
#: ``--package legacy`` for them before any omc invocation.
SEGMENTED_ONLY_CORES: tuple[str, ...] = ("1r10seg", "r5x5_z10")

#: Per-core clause for the legacy-mode refusal message: the segmented-only
#: core plus the segmented trim rig that names its only supported path
#: (TASK-20260906-01 P3, extended by TASK-20260906-02 P3; startup/freq/
#: wrapper wording -- the transients pair below adds the trip companions).
SEGMENTED_ONLY_CORE_CLAUSES: dict[str, str] = {
    "1r10seg": (
        "the 1-channel 10-axial-segment 1R core is segmented-package "
        "only, SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS"
    ),
    "r5x5_z10": (
        "the 5x5-radial x 10-axial-segment 1R core is segmented-package "
        "only, SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS"
    ),
}

#: Transients variant of the clause table: names the trim/trip vehicle pair
#: (the legacy package ships no step/flow/UHX-trip vehicle for either core).
SEGMENTED_ONLY_CORE_TRIP_CLAUSES: dict[str, str] = {
    "1r10seg": (
        "the 1-channel 10-axial-segment 1R core is segmented-package "
        "only, SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS / "
        "R1MSRRuhx10SegTripThermalSS"
    ),
    "r5x5_z10": (
        "the 5x5-radial x 10-axial-segment 1R core is segmented-package "
        "only, SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS / "
        "R5x5Z10MSRRuhxTripThermalSS"
    ),
}


def segmented_only_core_clause(
    core_model: str,
    *,
    with_trip: bool = False,
) -> str:
    """Per-core clause naming a segmented-only core's supported path.

    ``with_trip=True`` selects the transients trim/trip pair wording;
    unknown cores fall back to the generic ``'<core>' is segmented-package
    only`` clause (the same fallback the four entry points carried before
    the consolidation).
    """

    table = (
        SEGMENTED_ONLY_CORE_TRIP_CLAUSES if with_trip else SEGMENTED_ONLY_CORE_CLAUSES
    )
    return table.get(core_model, f"{core_model!r} is segmented-package only")


def legacy_core_refusal_message(
    core_models: Sequence[str],
    *,
    flag: str,
    vehicle_noun: str,
    legacy_group_noun: str,
    with_trip: bool = False,
) -> str:
    """Assemble the legacy-mode refusal message for unsupported core(s).

    ``core_models`` carries the unsupported cores in request order
    (duplicates preserved -- the transients suite pins the exact naming).
    ``flag`` is the CLI flag as spelled by the caller (``--core_model``
    singular vs ``--core_models`` plural), ``vehicle_noun`` the workflow's
    vehicle phrase (e.g. ``vehicle``, ``nominal-trim vehicle``,
    ``step/flow/UHX-trip vehicle``, ``wrapper base vehicle``), and
    ``legacy_group_noun`` the legacy-vehicle group (e.g. ``legacy startup
    vehicles``, ``legacy bases``). The shape is pinned verbatim by
    ``tests/test_10seg_legacy_refusal.py``:

    ``--package legacy does not support <flag> <cores>: no legacy
    <vehicle_noun> for this core exists in SMD_MSR_Modelica.mo/MSRR.mo
    (<legacy_group_noun> exist only for 1r/9r; <clauses>). Re-run with
    --package segmented.``
    """

    names = " ".join(repr(str(core)) for core in core_models)
    clauses = "; ".join(
        segmented_only_core_clause(str(core), with_trip=with_trip)
        for core in core_models
    )
    return (
        f"--package legacy does not support {flag} {names}: no legacy "
        f"{vehicle_noun} for this core exists in SMD_MSR_Modelica.mo/"
        f"MSRR.mo ({legacy_group_noun} exist only for 1r/9r; {clauses}). "
        "Re-run with --package segmented."
    )


def legacy_core_refusal(
    core_models: str | Sequence[str],
    legacy_cores: "Mapping[str, Any] | Collection[str] | None",
    *,
    flag: str = "--core_model",
    vehicle_noun: str = "vehicle",
    legacy_group_noun: str = "legacy vehicles",
    with_trip: bool = False,
) -> str | None:
    """Name legacy-unsupported core(s), or ``None`` when all are supported.

    ``legacy_cores`` is the caller's legacy-vehicle table (mapping or
    collection of supported keys). An empty/``None`` table means legacy
    support cannot be determined (e.g. the startup runner's hermetic
    environment without the scenario tree) and yields no refusal -- the
    same semantics each entry point carried before the consolidation.
    The message is built by :func:`legacy_core_refusal_message`; callers
    keep their own ``--package`` check so segmented mode is never refused.
    Callers must invoke this BEFORE any omc invocation (the
    poisoned-``subprocess.run`` pattern of ``tests/test_10seg_legacy_refusal.py``).
    """

    if not legacy_cores:
        return None
    if isinstance(core_models, str):
        cores: list[str] = [core_models]
    else:
        cores = [str(core) for core in core_models]
    unsupported = [core for core in cores if core not in legacy_cores]
    if not unsupported:
        return None
    return legacy_core_refusal_message(
        unsupported,
        flag=flag,
        vehicle_noun=vehicle_noun,
        legacy_group_noun=legacy_group_noun,
        with_trip=with_trip,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a mapping")
    return loaded


def _physics_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if not str(key).startswith("_")}


def _finalize(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    _validate_scenario(raw, path)
    raw["_path"] = str(path)
    raw["_fingerprint"] = fingerprint(_physics_dict(raw))
    return raw


def load_scenario(
    scenario_id: str,
    *,
    kind: str | None = None,
    root: Path | None = None,
    plant: str | None = None,
    _chain: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load a scenario by id, resolving a same-kind ``extends:`` chain.

    ``_chain`` carries the resolved ancestor paths for cycle/depth
    detection; callers normally leave it empty. ``root`` stays
    unresolved when not given so the data tree is located through
    :mod:`helpers.data_resources` (checkout ``data/`` or the wheel's
    packaged ``msrr_data`` payload).
    """

    path = _find_scenario_path(scenario_id, kind=kind, root=root, plant=plant)
    return load_scenario_path(path, kind=kind, root=root, plant=plant, _chain=_chain)


def load_scenario_path(
    path: str | Path,
    *,
    kind: str | None = None,
    root: Path | None = None,
    plant: str | None = None,
    _chain: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load a scenario YAML file, resolving ``extends:`` against the deck tree.

    The raw file is validated first (identity fields). When a runner kind
    is requested, the file's declared ``kind`` must match it. ``extends``
    chains must stay within the same kind and within
    :data:`MAX_EXTENDS_DEPTH`; cyclic or overly deep chains fail with the
    offending chain named. ``root`` stays unresolved when not given so
    the data tree is located through :mod:`helpers.data_resources`.
    """

    file_path = Path(path)
    raw = _load_yaml(file_path)
    label = _repo_label(file_path, root)
    if plant is None:
        # A deck under data/plants/<plant>/scenarios/ resolves its extends
        # chain in that plant's tree.
        plant = scenario_plant_for_path(file_path, root)
    _validate_scenario_identity(raw, label, standalone=not raw.get("extends"))
    if kind is not None and raw.get("kind") != kind:
        fail(
            label,
            "kind",
            f"requested scenario kind {kind!r} but file declares {raw.get('kind')!r}",
        )
    extends = raw.get("extends")
    if extends:
        key = str(file_path.resolve())

        def chain_label(member: str) -> str:
            return _repo_label(Path(member), root)

        if key in _chain:
            chain = " -> ".join(chain_label(member) for member in (*_chain, key))
            fail(label, "extends", f"cyclic inheritance chain: {chain}")
        if len(_chain) > MAX_EXTENDS_DEPTH:
            chain = " -> ".join(chain_label(member) for member in _chain)
            fail(
                label,
                "extends",
                f"inheritance chain exceeds maximum depth {MAX_EXTENDS_DEPTH}: {chain}",
            )
        parent_kind = kind or str(raw.get("kind") or "")
        parent = load_scenario(
            str(extends), kind=parent_kind, root=root, plant=plant, _chain=(*_chain, key)
        )
        parent_id = str(parent.get("id") or extends)
        merged = _deep_merge(parent, raw)
        merged.pop("extends", None)
        merged["_parent_id"] = parent_id
        raw = merged
    _validate_scenario_plant(raw, label, plant)
    return _finalize(raw, file_path)


def _validate_scenario_plant(raw: Mapping[str, Any], label: str, plant: str | None) -> None:
    """A plant-scoped deck may only name its own plant in ``applies_to``."""

    if plant is None or plant == DEFAULT_PLANT_ID:
        return
    applies = raw.get("applies_to")
    if not isinstance(applies, list):
        return
    for index, entry in enumerate(applies):
        if str(entry).rsplit(".", 1)[0] != plant:
            fail(
                label,
                f"applies_to[{index}]",
                f"plant-scoped deck for {plant!r} lists {entry!r}; entries must be '{plant}.<core>'",
            )


def resolve_scenario(
    *,
    kind: str,
    scenario_id: str | None = None,
    scenario_file: str | Path | None = None,
    root: Path | None = None,
    plant: str | None = None,
) -> dict[str, Any]:
    """Load from ``--scenario_file`` when given, otherwise by id.

    ``plant`` (the runners' ``--plant``) selects the deck tree: the shared
    ``data/scenarios/`` for the default plant, else that plant's own
    ``data/plants/<plant>/scenarios/`` with no fallback to the shared decks.
    """

    if scenario_file:
        return load_scenario_path(scenario_file, kind=kind, root=root, plant=plant)
    if not scenario_id:
        raise ValueError("scenario_id or scenario_file is required")
    return load_scenario(str(scenario_id), kind=kind, root=root, plant=plant)


def _find_scenario_path(
    scenario_id: str,
    *,
    kind: str | None,
    root: Path | None,
    plant: str | None = None,
) -> Path:
    base = scenarios_root(root, plant=plant)
    if kind:
        directory = _KIND_DIRS.get(kind, kind)
        direct = base / directory / f"{scenario_id}.yaml"
        if direct.is_file():
            return direct
        # id in file may differ from filename (startup id "startup")
        for path in sorted((base / directory).glob("*.yaml")):
            loaded = _load_yaml(path)
            if loaded.get("id") == scenario_id:
                return path
        raise FileNotFoundError(f"scenario {scenario_id!r} not found under {base / directory}")
    matches: list[Path] = []
    for path in sorted(base.rglob("*.yaml")):
        loaded = _load_yaml(path)
        if loaded.get("id") == scenario_id:
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"scenario {scenario_id!r} not found under {base}")
    raise ValueError(f"scenario id {scenario_id!r} is ambiguous: {matches}")


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` onto ``base``; quantity envelopes are atomic nodes.

    A quantity envelope is a mapping carrying ``value`` (typically
    ``{value, unit[, doc][, source]}``). When the base node at a key is
    quantity-shaped and the overlay node is any mapping, the overlay node
    replaces the base node WHOLESALE -- no recursive merge -- for
    value-only, unit-only, doc-only, source-only, and any combination of
    semantic keys. Atomic replacement is deliberate: a partial overlay can
    no longer retag an inherited quantity while silently keeping the
    parent's unit or provenance (the audited failure mode: a kelvin value
    inheriting the parent's degC unit); a half-replaced envelope fails
    ``check_quantity`` downstream with the node path named instead.

    Tradeoff: metadata-only patching of an inherited quantity is NOT
    supported -- a child that wants new ``doc``/``source`` text must
    restate the full quantity (``value`` and ``unit`` included). An
    explicit metadata-patch mechanism is a possible future addition and is
    out of scope here.

    Non-quantity mappings (``numerics``, ``forcing``, ...) still deep-merge
    key by key; scalars, arrays, and full ``{value, unit}`` overlays replace
    wholesale exactly as before this rule.
    """
    out = dict(base)
    for key, value in overlay.items():
        # A quantity-shaped base node (carries 'value') is atomic: ANY mapping
        # overlay replaces it wholesale so a partial overlay (value without
        # unit, or a doc/source-only note) cannot inherit the parent's unit or
        # provenance -- it fails check_quantity downstream instead. Full
        # {value, unit} overlays always replaced wholesale; non-quantity
        # mappings still deep-merge recursively.
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
            and not ("value" in value and "unit" in value)
            and "value" not in out[key]
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


_CORE_KEYS = tuple(CLI_TO_YAML_CORE.values())
_CASE_FAMILIES = ("step", "flow", "uhx_trip")


def _validate_scenario_identity(raw: Mapping[str, Any], label: str, *, standalone: bool) -> None:
    if raw.get("schema_version") != 1:
        fail(
            label,
            "schema_version",
            f"unsupported scenario schema_version {raw.get('schema_version')!r} (expected 1)",
        )
    if raw.get("kind") not in _KIND_DIRS:
        fail(
            label,
            "kind",
            f"unknown scenario kind {raw.get('kind')!r}; known kinds: {sorted(_KIND_DIRS)}",
        )
    scenario_id = raw.get("id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        fail(label, "id", f"scenario needs a non-empty string id, got {scenario_id!r}")
    doc = raw.get("doc")
    if doc is not None and (not isinstance(doc, str) or not doc.strip()):
        fail(label, "doc", f"must be a non-empty string, got {doc!r}")
    if standalone and doc is None:
        fail(
            label,
            "doc",
            "required on scenarios that do not extend a parent"
            " (overlay fragments inherit the parent's documentation)",
        )
    extends = raw.get("extends")
    if extends is not None and (not isinstance(extends, str) or not extends.strip()):
        fail(label, "extends", f"must be a non-empty parent scenario id, got {extends!r}")


def _validate_scenario(raw: Mapping[str, Any], path: Path) -> None:
    """Full validation of a merged scenario deck.

    Identity fields, section shapes, quantity envelopes (units, finite
    values, physical domains, temperature conventions), schedule
    invariants (monotonic times, equal lengths), structural couplings,
    and per-kind required sections. See the module docstring.
    """

    label = _repo_label(Path(path))
    _validate_scenario_identity(raw, label, standalone=not raw.get("extends"))
    for name in (
        "numerics",
        "structural",
        "forcing",
        "grid",
        "init",
        "display",
        "package_support",
        "low_power_protocol",
        "poisons",
    ):
        if raw.get(name) is not None:
            require_mapping(raw[name], label, name, f"scenario section '{name}'")
    _validate_applies_to(raw, label)
    _validate_package_support(raw, label)
    check_quantity_tree(raw, label)
    kind = str(raw.get("kind"))
    if kind == "campaign":
        _validate_campaign(raw, label)
        return
    if kind == "radial_demo":
        _validate_radial_demo(raw, label)
        return
    if kind == "transients":
        if raw.get("cases") is None:
            fail(label, "cases", "transients scenarios require a 'cases' list")
        _validate_cases(raw["cases"], label)
    if kind == "frequency" and raw.get("grid") is None:
        fail(label, "grid", "frequency scenarios require a 'grid' section")
    if kind == "startup":
        _require_numerics(raw, label, ("stop_time_s", "number_of_intervals"))
        if raw.get("forcing") is None:
            fail(label, "forcing", "startup scenarios require a 'forcing' section")
    elif kind == "transients":
        _require_numerics(raw, label, ("stop_time_s",))
    _validate_scenario_poisons(raw, label)
    if raw.get("numerics") is not None:
        _validate_numerics(raw["numerics"], label)
    if raw.get("structural") is not None:
        _validate_structural(raw["structural"], raw, label)
    if raw.get("forcing") is not None:
        _validate_forcing(raw["forcing"], label)
    if raw.get("grid") is not None:
        _validate_grid(raw["grid"], label)
    if raw.get("powers_mw") is not None:
        _validate_powers(raw["powers_mw"], label)
    if raw.get("low_power_protocol") is not None:
        _validate_low_power_protocol(raw["low_power_protocol"], label)
    if raw.get("init") is not None:
        _validate_init(raw["init"], label)


# First-release poison controls (TASK-20260912-01 P1): ``fixed_start`` is
# REFUSED. The previous two-value enum let a deck record
# ``poisonInitialization: fixed_start`` in the run fingerprint while the
# override payload carried only the tracking flags -- the Modelica
# component then executed its default SteadyState initialization, so the
# metadata lied. Re-enable only with the full implementation (five
# nonnegative finite inventories validated, ``poisons.initMode`` and
# ``poisons.N_*_0`` overrides emitted, and a Modelica test that the values
# are used); until then the label is refused at load time and again in
# :func:`poison_run_bindings` for direct Python decks.
_POISON_INIT_MODES = ("steady_state",)


def _forcing_power_level(scenario: Mapping[str, Any] | None) -> Any:
    """Return the raw scenario ``forcing.powerLevel`` node, or ``None``.

    Quantity envelopes (``value``/``unit`` mappings) are unwrapped to
    their numeric value so the refusal check below sees the number the
    wrapper emitter would bind.
    """

    forcing = scenario.get("forcing") if isinstance(scenario, Mapping) else None
    if not isinstance(forcing, Mapping) or "powerLevel" not in forcing:
        return None
    node = forcing["powerLevel"]
    return quantity_value(node) if is_quantity(node) else node


def poison_full_power_refusal(power_level: Any) -> str | None:
    """Name a power level steady-state poison tracking cannot honor, or ``None``.

    TASK-20260912-01 P2 (first-release convention): steady-state poison
    tracking is restricted to the nominal full-power operating point,
    ``powerLevel = 1``. The reactors bind ``HomogeneousPoisons.n0 =
    powerLevel``, so at any other level the unperturbed model still
    evaluates ``F = F0`` and ``b = B0`` at FULL power while the plant
    runs elsewhere: the production/absorption scaling would silently
    re-use full-power yields and exposure. A generalized absolute-power
    normalization is an owner decision and is deliberately NOT
    implemented here.

    The comparison is exact parsed-float identity against ``1.0`` on a
    finite float (never a ``%.16g``-style string key): unparseable and
    non-finite levels are refused, so every accepted point is exactly
    the nominal one.
    """

    try:
        value = float(power_level)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value):
        return (
            "steady-state poison tracking requires a finite powerLevel = 1 "
            f"(full power); got {power_level!r}"
        )
    if value != 1.0:
        return (
            "steady-state poison tracking is restricted to full power "
            f"(powerLevel = 1) in the first release; refusing powerLevel = "
            f"{value!r} (reactors bind HomogeneousPoisons.n0 = powerLevel, so "
            "F = F0 and b = B0 would still evaluate at full power while the "
            "plant runs elsewhere)"
        )
    return None


def poison_maturity_refusal(maturity: Any) -> str | None:
    """Name a poison dataset the unflagged run cannot honor, or ``None``.

    TASK-20260912-01 P5: the poison dataset's machine-readable governance
    label (``poisons.maturity``, a required plant enum) decides whether a
    poison-on run needs the explicit development override
    ``--allow-unreviewed-poison-data``. Only the approved subset
    (:data:`helpers.plant_config.POISON_MATURITY_APPROVED`) may back a
    poison-on run without it; ``reduced_order_pending_review`` data cannot
    reach an un-flagged production run or publication approval. A missing
    or unparseable label is refused too (fail-closed), even though plant
    validation makes the label mandatory -- callers that bypass a full
    plant load must not gain a hole.
    """

    if not isinstance(maturity, str) or not maturity.strip():
        return (
            "poison dataset carries no machine-readable maturity label; "
            f"refusing the poison-on run (plant validation requires one of "
            f"{list(POISON_MATURITY_VALUES)}; approved for unflagged use: "
            f"{list(POISON_MATURITY_APPROVED)}). Re-run with "
            "--allow-unreviewed-poison-data to accept the data deliberately "
            "as an unreviewed development run."
        )
    if maturity in POISON_MATURITY_APPROVED:
        return None
    return (
        f"poison dataset maturity {maturity!r} is not approved for "
        f"production or publication use (approved: "
        f"{list(POISON_MATURITY_APPROVED)}); refusing the poison-on run. "
        "Re-run with --allow-unreviewed-poison-data to accept the "
        "reduced-order, pending-review data deliberately as a development "
        "run."
    )


def _validate_scenario_poisons(raw: Mapping[str, Any], label: str) -> None:
    """Optional scenario poison controls; default is tracking and feedback off."""

    block = raw.get("poisons")
    if block is None:
        return
    require_mapping(block, label, "poisons", "scenario section 'poisons'")
    tracking = block.get("tracking", False)
    feedback = block.get("feedback", False)
    if not isinstance(tracking, bool):
        fail(label, "poisons.tracking", f"must be a boolean, got {tracking!r}")
    if not isinstance(feedback, bool):
        fail(label, "poisons.feedback", f"must be a boolean, got {feedback!r}")
    if feedback and not tracking:
        fail(
            label,
            "poisons.feedback",
            "feedback cannot be true when tracking is false",
        )
    init = block.get("initialization", "steady_state")
    if init not in _POISON_INIT_MODES:
        fail(
            label,
            "poisons.initialization",
            f"must be one of {sorted(_POISON_INIT_MODES)}, got {init!r}",
        )
    dataset = block.get("dataset")
    if dataset is not None and (
        not isinstance(dataset, str) or not dataset.strip()
    ):
        fail(label, "poisons.dataset", f"must be a non-empty string, got {dataset!r}")
    if tracking and init == "steady_state":
        # TASK-20260912-01 P2: steady-state poison tracking is accepted only
        # at the nominal full-power operating point. Any explicit
        # forcing.powerLevel other than exactly 1 is refused at load time
        # (the deck claims a power the poison scaling cannot honor);
        # unparseable and non-finite levels are refused too, instead of
        # silently passing the old power <= 0 screen.
        power = _forcing_power_level(raw)
        if power is not None:
            power_refusal = poison_full_power_refusal(power)
            if power_refusal is not None:
                fail(label, "forcing.powerLevel", power_refusal)


def scenario_poison_controls(
    scenario: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return poison tracking/feedback/init/dataset with defaults off."""

    block: Mapping[str, Any] = {}
    if isinstance(scenario, Mapping):
        raw = scenario.get("poisons")
        if isinstance(raw, Mapping):
            block = raw
    tracking = bool(block.get("tracking", False))
    feedback = bool(block.get("feedback", False))
    dataset = block.get("dataset")
    return {
        "tracking": tracking,
        "feedback": feedback,
        "initialization": str(block.get("initialization", "steady_state")),
        "dataset": str(dataset).strip() if isinstance(dataset, str) else None,
    }


def join_override_payload(*parts: str) -> str:
    """Join non-empty Modelica ``-override=`` fragments with commas."""

    bits = [part.strip().strip(",") for part in parts if part and str(part).strip()]
    return ",".join(bits)


def _poison_dataset_mismatch(
    claimed: str | None,
    bound: str | None,
) -> str | None:
    """Name a poison-dataset label the bound dataset cannot honor, or ``None``."""

    if claimed is None:
        return None
    if not bound:
        return (
            f"scenario poisons.dataset {claimed!r} cannot be honored: the "
            "loaded plant deck carries no poisons.dataset_id, so the label "
            "would not name actual model data"
        )
    if claimed != bound:
        return (
            f"scenario poisons.dataset {claimed!r} does not match the loaded "
            f"plant dataset_id {bound!r}; the model binds "
            "SegmentedMSR_PlantData.Poisons, so the label must name the "
            "compiled dataset"
        )
    return None


def poison_dataset_refusal(
    scenario: Mapping[str, Any] | None,
    plant: Mapping[str, Any],
) -> str | None:
    """Name a scenario poison-dataset label the plant cannot honor, or ``None``.

    TASK-20260912-01 P1: a scenario ``poisons.dataset`` label is provenance
    only when it names the data the executable actually binds (the single
    compiled ``SegmentedMSR_PlantData.Poisons``, emitted from the plant
    deck's ``poisons.dataset_id``). Any other label is refused instead of
    being recorded as metadata next to a different compiled dataset.
    Fail-closed on a plant deck without a usable ``dataset_id`` too: a
    label that cannot be checked does not pass.
    """

    claimed = scenario_poison_controls(scenario)["dataset"]
    poisons = plant.get("poisons") if isinstance(plant, Mapping) else None
    bound = (
        str(poisons.get("dataset_id")).strip()
        if isinstance(poisons, Mapping) and isinstance(poisons.get("dataset_id"), str)
        else None
    )
    return _poison_dataset_mismatch(claimed, bound)


def poison_run_bindings(
    package: str,
    scenario: Mapping[str, Any] | None,
    *,
    plant_id: str = "msrr",
    root: Path | None = None,
    power_level: Any = None,
    allow_unreviewed_poison_data: bool = False,
) -> tuple[str, dict[str, object], bool]:
    """Return ``(override payload, fingerprint extras, tracking)``.

    Default-off returns ``("", {}, False)`` so existing fingerprints keep
    their historical shape. Every poison-on request is fail-closed
    (TASK-20260912-01 P1):

    - tracking on the legacy package is refused (the dormant
      ``SMD_MSR_Modelica.Nuclear.Poisons`` component is not a production
      binding);
    - feedback without tracking is refused (the Modelica vehicles refuse
      the same combination outside the conditional component);
    - any initialization other than ``steady_state`` is refused (the
      override payload cannot carry ``fixed_start``, so the label would
      not describe what executes);
    - a ``poisons.dataset`` label that differs from the loaded plant's
      ``dataset_id`` is refused before build or reuse;
    - any power level other than exactly 1 is refused (P2): the
      scenario's own ``forcing.powerLevel`` when present, plus the
      runner's explicit ``power_level`` (e.g. the frequency sweep's
      ``--power`` CLI value) so a CLI level cannot bypass the deck;
    - a dataset whose authored ``maturity`` is outside the approved
      subset is refused unless ``allow_unreviewed_poison_data`` is True
      (P5): the CLI runners expose that override as
      ``--allow-unreviewed-poison-data``. When the override is load-bearing
      (the dataset is genuinely unapproved) it is recorded in the manifest
      fields as ``allow_unreviewed_poison_data: true``, the same
      deliberate-override provenance pattern as ``allow_unlisted_core``.

    The fingerprint extras carry the plant-side truth alongside any
    scenario label: ``poisonDatasetId`` / ``poisonMaturity`` /
    ``poisonSourceDigest`` (see
    :func:`helpers.plant_config.poison_dataset_record`), so a manifest
    cannot claim a dataset different from the one the executable binds.
    """

    ctrl = scenario_poison_controls(scenario)
    tracking = bool(ctrl["tracking"])
    feedback = bool(ctrl["feedback"])
    if (tracking or feedback) and str(package) != "segmented":
        raise ValueError(
            "poison tracking is supported only on --package segmented; "
            "the legacy SMD_MSR_Modelica.Nuclear.Poisons component is not "
            "a production binding"
        )
    if not tracking and not feedback:
        return "", {}, False
    if feedback and not tracking:
        raise ValueError(
            "poison feedback requires poison tracking: refusing "
            f"feedback={feedback} with tracking={tracking} (the Modelica "
            "vehicles refuse the same combination at initialization)"
        )
    if ctrl["initialization"] != "steady_state":
        raise ValueError(
            f"poison initialization {ctrl['initialization']!r} is refused "
            "in the first release: the override payload cannot carry it, "
            "so a run labeled "
            f"{ctrl['initialization']!r} would still execute SteadyState"
        )
    for candidate, origin in (
        (_forcing_power_level(scenario), "scenario forcing.powerLevel"),
        (power_level, "power_level argument"),
    ):
        if candidate is None:
            continue
        power_refusal = poison_full_power_refusal(candidate)
        if power_refusal is not None:
            raise ValueError(f"{power_refusal} (refused via {origin})")
    try:
        record = poison_dataset_record(plant_id, root=root)
    except FileNotFoundError as exc:
        raise ValueError(
            f"poison-on run requires the plant poison dataset record "
            f"(plant_id={plant_id!r}): {exc}"
        ) from exc
    refusal = _poison_dataset_mismatch(ctrl["dataset"], record["poisonDatasetId"])
    if refusal is not None:
        raise ValueError(refusal)
    payload = (
        f"enablePoisonTracking={'true' if tracking else 'false'},"
        f"enablePoisonFeedback={'true' if feedback else 'false'}"
    )
    fields: dict[str, object] = {
        "poisonInitialization": ctrl["initialization"],
    }
    if ctrl["dataset"]:
        fields["poisonDataset"] = str(ctrl["dataset"])
    fields.update(record)
    maturity_refusal = poison_maturity_refusal(record.get("poisonMaturity"))
    if maturity_refusal is not None:
        if not allow_unreviewed_poison_data:
            raise ValueError(maturity_refusal)
        # The override is load-bearing: record the deliberate acceptance
        # next to the dataset identity (same pattern as allow_unlisted_core).
        fields["allow_unreviewed_poison_data"] = True
    return payload, fields, tracking


def _validate_applies_to(raw: Mapping[str, Any], label: str) -> None:
    applies = raw.get("applies_to")
    if applies is None:
        return
    require_list(applies, label, "applies_to", what="'applies_to'")
    for index, entry in enumerate(applies):
        if not isinstance(entry, str) or "." not in entry:
            fail(
                label,
                f"applies_to[{index}]",
                f"must be '<plant>.<core>' (e.g. msrr.r1), got {entry!r}",
            )
        core = entry.rsplit(".", 1)[1]
        if core not in _CORE_KEYS:
            fail(
                label,
                f"applies_to[{index}]",
                f"unknown core {core!r}; valid core keys: {list(_CORE_KEYS)}",
            )


def _validate_package_support(raw: Mapping[str, Any], label: str) -> None:
    support = raw.get("package_support")
    if support is None:
        return
    require_mapping(support, label, "package_support", "'package_support'")
    for package, node in support.items():
        if node is None:
            continue
        require_mapping(node, label, f"package_support.{package}", f"package_support entry '{package}'")
        vehicle_by_core = node.get("vehicle_by_core")
        if vehicle_by_core is not None:
            require_mapping(
                vehicle_by_core,
                label,
                f"package_support.{package}.vehicle_by_core",
                "'vehicle_by_core'",
            )
            for core, vehicle in vehicle_by_core.items():
                if core not in _CORE_KEYS:
                    fail(
                        label,
                        f"package_support.{package}.vehicle_by_core.{core}",
                        f"unknown core {core!r}; valid core keys: {list(_CORE_KEYS)}",
                    )
                if not isinstance(vehicle, str) or not vehicle.strip():
                    fail(
                        label,
                        f"package_support.{package}.vehicle_by_core.{core}",
                        f"must be a non-empty Modelica class name, got {vehicle!r}",
                    )
        needs_wrapper = node.get("needs_wrapper")
        if needs_wrapper is not None and not isinstance(needs_wrapper, bool):
            fail(
                label,
                f"package_support.{package}.needs_wrapper",
                f"must be a boolean, got {needs_wrapper!r}",
            )
        base_vehicle = node.get("base_vehicle")
        if base_vehicle is not None and (not isinstance(base_vehicle, str) or not base_vehicle.strip()):
            fail(
                label,
                f"package_support.{package}.base_vehicle",
                f"must be a non-empty vehicle name, got {base_vehicle!r}",
            )
        power_mw_required = node.get("power_mw_required")
        if power_mw_required is not None:
            require_number(
                power_mw_required,
                label,
                f"package_support.{package}.power_mw_required",
                exclusive_minimum=0.0,
            )


def _validate_campaign(raw: Mapping[str, Any], label: str) -> None:
    package = raw.get("package")
    if package is not None and package not in ("legacy", "segmented"):
        fail(
            label,
            "package",
            f"must be 'legacy' or 'segmented', got {package!r}",
        )
    cores = raw.get("cores")
    if cores is not None:
        require_list(cores, label, "cores", what="'cores'")
        for index, core in enumerate(cores):
            if core not in _CORE_KEYS:
                fail(
                    label,
                    f"cores[{index}]",
                    f"unknown core {core!r}; valid core keys: {list(_CORE_KEYS)}",
                )
    scenarios = raw.get("scenarios")
    if scenarios is None:
        fail(label, "scenarios", "campaign decks must name their composed scenarios")
    require_mapping(scenarios, label, "scenarios", "'scenarios'")
    for slot, scenario_id in scenarios.items():
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            fail(
                label,
                f"scenarios.{slot}",
                f"must be a non-empty scenario id, got {scenario_id!r}",
            )
    if raw.get("setpoints") is not None:
        require_mapping(raw["setpoints"], label, "setpoints", "'setpoints'")


# TASK-20260914-01 P7 (plan §12 Phase G item 3): the exploratory radial-demo
# scenario kind. The decks name a committed ``SegmentedMSR.Demos`` vehicle
# (the plan §14 ladder) and record the structural radial/loop identity that
# the runner publishes into the run manifest's fingerprint-active
# ``intra_channel_radial`` field. Every refusal names the full configuration
# path and the offending value (plan §9).
_RADIAL_DEMO_RADIAL_KEYS = (
    "enabled",
    "annular_fluid_mode",
    "annular_heat_exchanger_enabled",
    "annular_heat_exchanger_model",
    "geometry_policy",
    "flow_direction",
    "channel_flow_fractions",
    "radial_dataset_id",
    "radial_maturity",
    "radial_fingerprint",
    "annular_loop_fingerprint",
)

_RADIAL_DEMO_NUMERIC_KEYS = (
    "stop_time_s",
    "number_of_intervals",
    "tolerance",
    "max_step_size_s",
    "method",
)


@dataclass(frozen=True)
class RadialDemoVehicleContract:
    """The code-owned exact identity of one committed ``SegmentedMSR.Demos``
    demo vehicle (review rev019 "High — radial demo manifests can claim
    configuration that the vehicle did not execute", required correction
    option 2; planner resolution 1 minimum).

    The compiled Modelica classes bind their radial identity as hard-coded
    ``IntraChannelRadialConfig`` values, so the scenario deck's claimed
    identity is only truthful when it EQUALS the class's bound values field
    by field. This immutable map is that expected identity: the annular
    mode, the heat-exchanger selection, the flow direction, the per-loop
    ``channel_flow_fractions`` vector (whose LENGTH is the annular-loop
    channel count the vehicle binds -- exact vector equality enforces the
    channel count), the dataset id, the radial/loop fingerprints, the
    geometry policy, and the radial dataset maturity. Validation
    (:func:`radial_demo_contract_mismatches`) demands exact equality and
    refuses otherwise, naming the configuration path and the offending
    value; :func:`radial_demo_manifest_record` builds the published
    manifest identity FROM this map (never from the free-form deck claims),
    so a run manifest can only ever carry the identity the compiled vehicle
    actually binds. Adding a committed RADIAL demo vehicle REQUIRES adding
    its contract here; a committed ``Demos`` vehicle covered by a DIFFERENT
    demo contract requires an explicit :data:`NON_RADIAL_DEMO_VEHICLE_SCOPES`
    registration instead (fail-closed both ways: an unknown vehicle name
    refuses, and an unregistered Demos model refuses every digest
    derivation and deck load).
    """

    vehicle: str
    annular_fluid_mode: str
    annular_heat_exchanger_enabled: bool
    annular_heat_exchanger_model: str
    geometry_policy: str
    #: What ONE modeled coarse thermal channel represents in the compiled
    #: vehicle (review rev019 Phase 2; TASK-20260915-01 P2):
    #: 'literal_tube' (one physical tube) or 'aggregate_bundle'.
    channel_meaning: str
    #: Physical-channel multiplicity N_c the vehicle binds (1 = one literal
    #: tube; >= 2 = a bundle of that many identical parallel tubes).
    channel_multiplicity: int
    flow_direction: str
    #: Exact per-loop channel flow fractions the vehicle binds; ``None``
    #: for static vehicles (no loop exists, so no distribution is claimable
    #: -- the omission pattern the manifest record keeps).
    channel_flow_fractions: tuple[float, ...] | None
    radial_dataset_id: str
    radial_maturity: str
    radial_fingerprint: str
    annular_loop_fingerprint: str

    @property
    def channel_count(self) -> int | None:
        """The annular-loop channel count the vehicle binds per loop
        (``len(channel_flow_fractions)``); ``None`` for static vehicles."""

        if self.channel_flow_fractions is None:
            return None
        return len(self.channel_flow_fractions)


# ---------------------------------------------------------------------------
# Demo-fingerprint derivation (review rev020 Phase 5 item 2;
# TASK-20260916-01 P6). The committed ``SegmentedMSR.Demos`` vehicles
# covered by the intra-channel radial demo contract bind their radial
# identity as SHA-256 digests over the physics-bearing demo values. The
# digests are DERIVED from the committed Modelica bindings by
# the source parser below - never authored by hand: a source edit to any
# physics-bearing demo constant (geometry radii, physical lengths,
# materials, film coefficients, mass flow, plenum volumes, heat-exchanger
# conductance/temperatures, deposition fractions, flow fractions, flow
# direction, geometry policy, HX model identity, and the bound initial
# temperatures) moves ``radialFingerprint`` and/or ``annularLoopFingerprint``,
# and the new digests are re-bound in the vehicles, in this contract map,
# and in the committed ``data/scenarios/radial_demos/*.yaml`` decks (deck
# load refuses a stale identity, see
# :func:`radial_demo_contract_mismatches`).
#
# The two digests split the physics-bearing values the same way the
# production plant records do
# (``helpers.plant_config.radial_fingerprint`` / ``annular_loop_fingerprint``):
# the RADIAL digest covers the radial-dataset subset (geometry radii and the
# per-segment physical lengths, shared materials, interface films, heat
# deposition, geometry policy and volume tolerance, the physical-channel
# mapping, the dataset identity/maturity, and the bound radial-stack initial
# temperature ``T_radial_0``); the LOOP digest covers the annular-loop
# subset (annular mode, mass flow and command bound, flow direction, channel
# flow fractions, plenum and connecting-pipe volumes, the heat-exchanger
# state and parameters, and the bound loop initial temperature
# ``T_loop_0``). Together the two digests cover EVERY physics-bearing demo
# value: a circulation/heat-exchanger edit moves only the loop digest, a
# geometry/material edit moves only the radial digest. The digests are
# canonical SHA-256 over the parsed values (:func:`helpers.plant_config.
# fingerprint`), so source re-spelling that leaves the parsed physics
# unchanged does not move them.
# ---------------------------------------------------------------------------

# The physics-bearing fields one ``IntraChannelRadialConfig`` binding
# contributes to each digest: (Modelica field, kind). Kinds: "int"/"float"/
# "bool"/"str" scalars and "float_list" arrays (literal braces, a
# ``fill(value, n)`` with literal dims, or a named package constant). The
# fingerprint fields themselves are the digest OUTPUTS and are excluded;
# every other bound field appears in exactly one of the two subsets.
_RADIAL_DEMO_RADIAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("geometryPolicyCode", "int"),
    ("channelMeaning", "str"),
    ("channelMeaningCode", "int"),
    ("channelMultiplicity", "int"),
    ("physicalLength", "float_list"),
    ("fuelRadius", "float"),
    ("pipeOuterRadius", "float"),
    ("annulusOuterRadius", "float"),
    ("volumeTolerance", "float"),
    ("rhoPipe", "float"),
    ("cpPipe", "float"),
    ("kPipe", "float"),
    ("rhoAnnularFluid", "float"),
    ("cpAnnularFluid", "float"),
    ("kAnnularFluid", "float"),
    ("fuelPipeInterfaceCode", "int"),
    ("hFuelPipe", "float"),
    ("pipeFluidInterfaceCode", "int"),
    ("hPipeFluid", "float"),
    ("fluidModeratorInterfaceCode", "int"),
    ("hFluidModerator", "float"),
    ("depFracPipe", "float"),
    ("depFracAnnularFluid", "float"),
    ("radialMaturity", "str"),
    ("radialDatasetId", "str"),
)

# Annular-loop fields. All are OPTIONAL at the binding (static records bind
# none of them - the omission pattern; absent parses as None so a
# later-added field is caught, not silently ignored).
_RADIAL_DEMO_LOOP_FIELDS: tuple[tuple[str, str], ...] = (
    ("annularFluidModeCode", "int"),
    ("annularHeatExchangerEnabled", "bool"),
    ("annularHeatExchangerModel", "str"),
    ("nominalMassFlow", "float"),
    ("maxFlowCommand", "float"),
    ("flowDirectionCode", "int"),
    ("channelFlowFractions", "float_list"),
    ("supplyPlenumVolume", "float"),
    ("returnPlenumVolume", "float"),
    ("connectingPipeVolume", "float"),
    ("hxLoopSideVolume", "float"),
    ("hxUA", "float"),
    ("hxSinkTemperature", "float"),
    ("hxInitialTemperature", "float"),
)

# Numeric literal (a Real or Integer literal token; identifiers resolve
# through the package constants instead).
_DEMO_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")

# ``final constant <Type> <name>[<n>](each unit = "...") = <value> ["doc"];``
# (the committed constants carry description strings after the value; the
# value itself never contains a quote or semicolon).
_DEMO_CONSTANT_RE = re.compile(
    r"\bfinal constant\s+[\w.]+\s+(\w+)(?:\[(\d+)\])?"
    r"(?:\((?:each\s+)?unit\s*=\s*\"[^\"]*\"\))?\s*=\s*([^;\"]+?)"
    r"\s*(?:\"[^\"]*\"\s*)?;",
    re.DOTALL,
)

_DEMO_FIELD_ASSIGN_RE = re.compile(r"\b(\w+)\s*=\s*")


def _demo_modelica_path(source_path: str | Path | None) -> Path:
    """The committed Modelica source the demo digests derive from (the
    default resolves through :func:`helpers.plant_config.repo_root` at call
    time; a scratch copy can be passed to re-derive against mutated
    bindings)."""

    if source_path is not None:
        return Path(source_path)
    return repo_root() / "core" / "SegmentedMSR.mo"


def _demo_package_text(source_path: str | Path | None) -> tuple[Path, str]:
    path = _demo_modelica_path(source_path)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\n  package Demos\b(.*?)\n  end Demos;", text, re.DOTALL)
    if match is None:
        raise ValueError(
            f"{path}: the SegmentedMSR.Demos package was not found; the demo "
            "fingerprint digests cannot be derived"
        )
    return path, match.group(1)


def _demo_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on commas outside any bracket or string literal."""

    parts: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    start = 0
    for j, ch in enumerate(text):
        if in_string:
            # Backslash-escape-aware: a `\"` inside a string literal no
            # longer toggles string state (an even run of backslashes still
            # closes on `"`).
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:j])
            start = j + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _demo_scan_value(text: str, start: int) -> tuple[str, int]:
    """Consume one comma-separated record value starting at ``start``;
    return ``(token, index after the top-level comma)``. Brackets and
    string literals are skipped, so ``fill(synT0, 1, 10)`` and
    ``{0.04, 0.04}`` stay one token."""

    depth = 0
    in_string = False
    j = start
    while j < len(text):
        ch = text[j]
        if in_string:
            if ch == "\\":
                # Skip the escaped character (e.g. \" ) so a backslash-quoted
                # quote does not terminate the string literal early.
                j += 2
                continue
            if ch == '"':
                in_string = False
            j += 1
            continue
        if ch == '"':
            in_string = True
        elif ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "," and depth == 0:
            return text[start:j].strip(), j + 1
        j += 1
    return text[start:].strip(), len(text)


def _demo_constants(demos_text: str) -> dict[str, Any]:
    """The package-level ``final constant`` values of ``Demos`` (the syn*
    placeholders and the physical ``*FuelLengths`` arrays), resolved to
    Python values: Real scalars to float, arrays (literal braces or
    ``fill(value, n)`` with literal dims) to list[float]."""

    constants: dict[str, Any] = {}
    for match in _DEMO_CONSTANT_RE.finditer(demos_text):
        name, size, raw = match.groups()
        token = raw.strip()
        if token.startswith("{"):
            constants[name] = [
                float(part) for part in _demo_top_level_commas(token[1:-1])
            ]
        elif token.startswith("fill(") and token.endswith(")"):
            parts = _demo_top_level_commas(token[len("fill(") : -1])
            if len(parts) != 2 or not parts[1].isdigit():
                raise ValueError(
                    f"demo constant {name!r}: unsupported fill shape "
                    f"{token!r} (only fill(value, n) with a literal n is "
                    "supported; extend the parser)"
                )
            constants[name] = [float(parts[0])] * int(parts[1])
        else:
            constants[name] = float(token)
    return constants


def _demo_scalar(
    token: str | None,
    kind: str,
    constants: dict[str, Any],
    context: str,
) -> Any:
    """Resolve one parsed binding token to its typed digest value
    (fail-closed: an unresolvable reference or a shape the parser does not
    know raises instead of silently skipping the field)."""

    if token is None:
        return None
    token = token.strip()
    if kind == "str":
        if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        raise ValueError(f"{context}: expected a quoted string, got {token!r}")
    if kind == "bool":
        if token in ("true", "false"):
            return token == "true"
        raise ValueError(f"{context}: expected true/false, got {token!r}")
    if kind == "int":
        try:
            return int(token)
        except ValueError:
            raise ValueError(
                f"{context}: expected an integer literal, got {token!r}"
            ) from None
    if kind == "float":
        if _DEMO_NUMBER_RE.match(token):
            return float(token)
        if token in constants and isinstance(constants[token], float):
            return constants[token]
        raise ValueError(
            f"{context}: unresolvable Real reference {token!r} (extend the "
            "demo constant parser if this is a new package constant)"
        )
    if kind == "float_list":
        if token.startswith("{") and token.endswith("}"):
            parts = _demo_top_level_commas(token[1:-1])
            return [float(part) for part in parts]
        if token.startswith("fill(") and token.endswith(")"):
            parts = _demo_top_level_commas(token[len("fill(") : -1])
            if len(parts) != 2 or not parts[1].isdigit():
                raise ValueError(
                    f"{context}: unsupported fill shape {token!r}"
                )
            return [float(parts[0])] * int(parts[1])
        if token in constants and isinstance(constants[token], list):
            return list(constants[token])
        raise ValueError(
            f"{context}: unresolvable Real array reference {token!r}"
        )
    raise ValueError(f"{context}: unknown field kind {kind!r}")


def _demo_block_fields(block: str) -> dict[str, str]:
    """The ``field = value`` token pairs of one ``IntraChannelRadialConfig``
    binding body (string-literal aware; values keep brackets together)."""

    fields: dict[str, str] = {}
    in_string = False
    j = 0
    n = len(block)
    while j < n:
        ch = block[j]
        if in_string:
            if ch == '"':
                in_string = False
            j += 1
            continue
        if ch == '"':
            in_string = True
            j += 1
            continue
        match = _DEMO_FIELD_ASSIGN_RE.match(block, j)
        if match is None:
            j += 1
            continue
        value, j = _demo_scan_value(block, match.end())
        fields[match.group(1)] = value
    return fields


def _demo_body_assignments(
    body: str, field: str, constants: dict[str, Any]
) -> list[Any]:
    """The ``<field> = <value>`` assignments in one vehicle body in document
    order (the core/zone modifiers bind ``T_radial_0``/``T_loop_0`` OUTSIDE
    the ``IntraChannelRadialConfig`` record, one per binding site). A
    ``fill(value, ...)`` resolves its value and keeps the (whitespace-
    normalized) dimension tokens; anything else resolves as a scalar."""

    values: list[Any] = []
    in_string = False
    j = 0
    n = len(body)
    while j < n:
        ch = body[j]
        if in_string:
            if ch == '"':
                in_string = False
            j += 1
            continue
        if ch == '"':
            in_string = True
            j += 1
            continue
        match = _DEMO_FIELD_ASSIGN_RE.match(body, j)
        if match is None or match.group(1) != field:
            j += 1
            continue
        token, j = _demo_scan_value(body, match.end())
        token = token.strip()
        context = f"demo binding {field}"
        if token.startswith("fill(") and token.endswith(")"):
            parts = _demo_top_level_commas(token[len("fill(") : -1])
            value_token = parts[0]
            dims = [re.sub(r"\s+", "", part) for part in parts[1:]]
            resolved = _demo_scalar(value_token, "float", constants, context)
            values.append({"fill": resolved, "dims": dims})
        else:
            values.append(_demo_scalar(token, "float", constants, context))
    return values


def _demo_binding_blocks(body: str) -> list[str]:
    """The ``IntraChannelRadialConfig(...)`` binding blocks of one vehicle
    body, paren-matched with string-literal skipping."""

    blocks: list[str] = []
    for start in re.finditer(r"IntraChannelRadialConfig\(", body):
        index = start.end() - 1
        depth = 0
        in_string = False
        end = -1
        for j in range(index, len(body)):
            ch = body[j]
            if in_string:
                if ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            raise ValueError(
                "unbalanced IntraChannelRadialConfig binding in the Demos "
                "package"
            )
        blocks.append(body[index + 1 : end])
    return blocks


def _demo_required(
    fields: dict[str, str], field: str, kind: str,
    constants: dict[str, Any], context: str,
) -> Any:
    if field not in fields:
        raise ValueError(
            f"{context}: the physics-bearing field {field!r} is not bound; "
            "the demo fingerprint digests would silently drop it"
        )
    return _demo_scalar(fields[field], kind, constants, context)


def _demo_radial_site(
    fields: dict[str, str],
    constants: dict[str, Any],
    t_radial_0: Any,
    context: str,
) -> dict[str, Any]:
    """The radial-dataset digest input of one binding site (the production
    ``radial_fingerprint`` grouping, plus the bound initial temperature)."""

    values = {
        field: _demo_required(fields, field, kind, constants, context)
        for field, kind in _RADIAL_DEMO_RADIAL_FIELDS
    }
    return {
        "geometry": {
            "fuel_radius": values["fuelRadius"],
            "pipe_outer_radius": values["pipeOuterRadius"],
            "annulus_outer_radius": values["annulusOuterRadius"],
            "physical_length": values["physicalLength"],
        },
        "materials": {
            "rho_pipe": values["rhoPipe"],
            "cp_pipe": values["cpPipe"],
            "k_pipe": values["kPipe"],
            "rho_annular_fluid": values["rhoAnnularFluid"],
            "cp_annular_fluid": values["cpAnnularFluid"],
            "k_annular_fluid": values["kAnnularFluid"],
        },
        "interfaces": {
            "fuel_pipe_interface_code": values["fuelPipeInterfaceCode"],
            "h_fuel_pipe": values["hFuelPipe"],
            "pipe_fluid_interface_code": values["pipeFluidInterfaceCode"],
            "h_pipe_fluid": values["hPipeFluid"],
            "fluid_moderator_interface_code": values[
                "fluidModeratorInterfaceCode"
            ],
            "h_fluid_moderator": values["hFluidModerator"],
        },
        "heat_deposition": {
            "dep_frac_pipe": values["depFracPipe"],
            "dep_frac_annular_fluid": values["depFracAnnularFluid"],
        },
        "geometry_policy": values["geometryPolicyCode"],
        "volume_tolerance": values["volumeTolerance"],
        "channel_meaning": values["channelMeaning"],
        "channel_meaning_code": values["channelMeaningCode"],
        "channel_multiplicity": values["channelMultiplicity"],
        "maturity": values["radialMaturity"],
        "dataset_id": values["radialDatasetId"],
        "initial_state": {"t_radial_0": t_radial_0},
    }


def _demo_loop_site(
    fields: dict[str, str],
    constants: dict[str, Any],
    t_loop_0: Any,
    context: str,
) -> dict[str, Any]:
    """The annular-loop digest input of one binding site (the production
    ``annular_loop_fingerprint`` grouping, plus the bound initial loop
    temperature). Absent fields (static records) parse as None."""

    values = {
        field: _demo_scalar(fields.get(field), kind, constants, context)
        for field, kind in _RADIAL_DEMO_LOOP_FIELDS
    }
    return {
        "annular_fluid_mode": values["annularFluidModeCode"],
        "annular_loop": {
            "nominal_mass_flow": values["nominalMassFlow"],
            "max_flow_command": values["maxFlowCommand"],
            "flow_direction_code": values["flowDirectionCode"],
            "channel_flow_fractions": values["channelFlowFractions"],
            "supply_plenum_volume": values["supplyPlenumVolume"],
            "return_plenum_volume": values["returnPlenumVolume"],
            "connecting_pipe_volume": values["connectingPipeVolume"],
            "hx_loop_side_volume": values["hxLoopSideVolume"],
            "hx_ua": values["hxUA"],
            "hx_sink_temperature": values["hxSinkTemperature"],
            "hx_initial_temperature": values["hxInitialTemperature"],
        },
        "annular_heat_exchanger_enabled": values["annularHeatExchangerEnabled"],
        "annular_heat_exchanger_model": values["annularHeatExchangerModel"],
        "initial_state": {"t_loop_0": t_loop_0},
    }


def radial_demo_physics_values(
    source_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """The parsed physics-bearing demo values of every committed
    ``SegmentedMSR.Demos`` vehicle covered by the intra-channel radial demo
    contract (:data:`RADIAL_DEMO_VEHICLE_CONTRACTS`), as the canonical
    digest inputs.

    Returns a mapping from the fully qualified vehicle name to
    ``{"radial": <input>, "loop": <input>}``, where each input carries one
    entry per ``IntraChannelRadialConfig`` binding site in document order
    (the 9R vehicle binds four per-zone sites; every other vehicle one).
    The contract scope is explicit per vehicle: every model in the
    ``Demos`` package must be registered in exactly one demo contract
    scope (:func:`demo_vehicle_contract_scopes`), so a Demos vehicle
    covered by a different demo contract (e.g. the synthetic
    outer-fuel-annulus demonstration, which binds
    ``OuterFuelAnnulusConfig`` and no ``IntraChannelRadialConfig``) is
    excluded by name from this mapping -- never silently -- and an
    unregistered new Demos model fails closed here until it is registered.
    Fail-closed within the radial scope: a vehicle without a binding, a
    missing physics-bearing field, or an unresolvable reference raises
    instead of silently narrowing the digest.
    """

    path, demos_text = _demo_package_text(source_path)
    constants = _demo_constants(demos_text)
    scopes = _demo_vehicle_scopes(path, demos_text)
    values: dict[str, dict[str, Any]] = {}
    for name, body in re.findall(r"\bmodel (\w+)(.*?)\bend \1;", demos_text, re.DOTALL):
        vehicle = f"SegmentedMSR.Demos.{name}"
        if scopes[vehicle] != _INTRA_CHANNEL_RADIAL_DEMO_SCOPE:
            continue
        blocks = _demo_binding_blocks(body)
        if not blocks:
            raise ValueError(
                f"{path}: {vehicle} carries no "
                "IntraChannelRadialConfig binding; the demo fingerprint "
                "digests cannot be derived"
            )
        t_radials = _demo_body_assignments(body, "T_radial_0", constants)
        t_loops = _demo_body_assignments(body, "T_loop_0", constants)
        if len(t_radials) != len(blocks):
            raise ValueError(
                f"{path}: SegmentedMSR.Demos.{name} binds {len(blocks)} "
                f"radial record(s) but {len(t_radials)} T_radial_0 "
                "assignment(s); the initial-temperature pairing is ambiguous"
            )
        if t_loops and len(t_loops) != len(blocks):
            raise ValueError(
                f"{path}: SegmentedMSR.Demos.{name} binds {len(blocks)} "
                f"radial record(s) but {len(t_loops)} T_loop_0 assignment(s); "
                "the initial-temperature pairing is ambiguous"
            )
        radial_sites: list[dict[str, Any]] = []
        loop_sites: list[dict[str, Any]] = []
        for k, block in enumerate(blocks):
            context = f"SegmentedMSR.Demos.{name} binding site {k + 1}"
            fields = _demo_block_fields(block)
            t_radial_0 = t_radials[k] if t_radials else None
            t_loop_0 = t_loops[k] if t_loops else None
            if t_radial_0 is None:
                raise ValueError(
                    f"{context}: no T_radial_0 initial temperature is bound"
                )
            radial_sites.append(
                _demo_radial_site(fields, constants, t_radial_0, context)
            )
            loop_sites.append(_demo_loop_site(fields, constants, t_loop_0, context))
        values[vehicle] = {
            "radial": {"sites": radial_sites},
            "loop": {"sites": loop_sites},
        }
    return values


def radial_demo_physics_digests(
    source_path: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """The demo fingerprint digests derived from the committed bindings.

    Returns a mapping from the fully qualified ``SegmentedMSR.Demos``
    vehicle name to ``{"radial": <64-hex>, "loop": <64-hex>}`` - exactly the
    values the committed vehicles bind as ``radialFingerprint`` /
    ``annularLoopFingerprint`` (canonical SHA-256 over the parsed
    physics-bearing demo values, :func:`helpers.plant_config.fingerprint`).
    Covers exactly the intra-channel radial demo contract scope
    (:func:`radial_demo_physics_values`); a Demos vehicle registered under a
    different demo contract scope is excluded by name.
    Pass a scratch copy of ``core/SegmentedMSR.mo`` as ``source_path`` to
    re-derive against mutated bindings.
    """

    return {
        vehicle: {
            "radial": fingerprint(data["radial"]),
            "loop": fingerprint(data["loop"]),
        }
        for vehicle, data in radial_demo_physics_values(source_path).items()
    }


# The exact compiled identity of every committed ``SegmentedMSR.Demos``
# vehicle covered by the intra-channel radial demo contract
# (core/SegmentedMSR.mo, package Demos). These values are the
# single source of truth for what each class binds; a deck whose claimed
# identity differs refuses (fail-closed). Do not edit a contract without
# editing the corresponding Modelica class in the same change.
#
# Contract scope is explicit per vehicle: membership HERE is the
# intra-channel radial demo scope, and every other committed ``Demos``
# vehicle must be registered in :data:`NON_RADIAL_DEMO_VEHICLE_SCOPES`
# below (the scope that covers it instead) -- an unregistered ``Demos``
# model refuses every digest derivation and deck load until it is
# registered in exactly one of the two, so no demo vehicle is ever
# silently omitted from a contract scope.
#
# The radial/loop fingerprints below are NOT free-form labels: they are the
# SHA-256 digests over the physics-bearing demo values, derived from the
# committed Modelica bindings by :func:`radial_demo_physics_digests`
# (review rev020 Phase 5 item 2; TASK-20260916-01 P6). Re-derive them
# whenever a physics-bearing demo constant changes, and re-bind them here,
# in the Modelica vehicles, and in the committed decks in the same change -
# :func:`radial_demo_contract_mismatches` refuses a contract that has gone
# stale against the committed bindings.
#
# Geometry policy (review rev019 Phase 2; TASK-20260915-01 P2): every
# committed demo vehicle binds geometryPolicyCode = 2 (effective_thermal).
# The synthetic demo dimensions cannot satisfy the strict_physical checks
# (for the 1r10seg trio the single-tube geometric fuel volume
# N_c*pi*r_f^2*L_j with N_c = 1 is about a factor of 561 below the
# declared 0.04 m3 per-segment salt inventory, and the 0.020 m annulus
# outer radius exceeds the 0.020 m lattice pitch (cell inradius
# 0.010 m)), so strict_physical is
# not claimable by any executable demo configuration.
#
# NOTE (plan §14 ladder): the three deck-driven ladder vehicles are the
# 1r10seg trio. ``R9MSRRuhxRadialCirculating`` enables FOUR INDEPENDENT
# single-channel zone loops (one ClosedAnnularLoop per zone core Z1..Z4,
# each binding the per-loop fractions below) -- NOT a shared four-leg loop
# -- and ``R5x5Z10MSRRuhxRadialCirculating`` binds 25 equal 1/25
# fractions. This per-loop one-channel shape is the ONE 9R annular-loop
# interpretation (review rev019 Phase 6; TASK-20260915-01 P6 fallback):
# the R9 contract below is per zone loop, there is no shared annular
# return/HX anywhere, and plant-driven circulating radial 9R is refused
# fail-closed (helpers.plant_config refuses any enabled
# cores.r9.intra_channel_radial block), so these hard-coded synthetic
# records are the only executable circulating-9R configuration.
RADIAL_DEMO_VEHICLE_CONTRACTS: dict[str, RadialDemoVehicleContract] = {
    "SegmentedMSR.Demos.R1MSRRuhx10SegRadialStatic": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R1MSRRuhx10SegRadialStatic",
        annular_fluid_mode="static",
        annular_heat_exchanger_enabled=False,
        annular_heat_exchanger_model="none",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=None,
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="b4b1fa281a8db7d78a59561821249bd842b19e537c3b8df3372407c5aa3b9b6d",
        annular_loop_fingerprint="1b9b4cd6f0543cf055f5acd4c5d0f6bf3372c7e81e6a332e3be86d9ffae7d112",
    ),
    "SegmentedMSR.Demos.R1MSRRuhx10SegRadialCirculating": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R1MSRRuhx10SegRadialCirculating",
        annular_fluid_mode="circulating",
        annular_heat_exchanger_enabled=False,
        annular_heat_exchanger_model="none",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=(1.0,),
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="b4b1fa281a8db7d78a59561821249bd842b19e537c3b8df3372407c5aa3b9b6d",
        annular_loop_fingerprint="975c83f35523eb3fb25f75cf87937b9d1cf2529a56ab076bb68558e29ec29aa6",
    ),
    "SegmentedMSR.Demos.R1MSRRuhx10SegRadialCirculatingHX": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R1MSRRuhx10SegRadialCirculatingHX",
        annular_fluid_mode="circulating",
        annular_heat_exchanger_enabled=True,
        annular_heat_exchanger_model="finite_conductance_prescribed_sink",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=(1.0,),
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="b4b1fa281a8db7d78a59561821249bd842b19e537c3b8df3372407c5aa3b9b6d",
        annular_loop_fingerprint="d82b457281dcdbf6009aab6be555ad854a75611bd77d6fd12f65ffa8f438dbef",
    ),
    # Four independent single-channel zone loops (Z1..Z4), each binding the
    # per-loop fractions below; no shared annular return or heat exchanger.
    "SegmentedMSR.Demos.R9MSRRuhxRadialCirculating": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R9MSRRuhxRadialCirculating",
        annular_fluid_mode="circulating",
        annular_heat_exchanger_enabled=False,
        annular_heat_exchanger_model="none",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=(1.0,),
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="ddbb061ee9e5ac7000e099003832075bd6682a3d2034ccc9afe513227ead12b6",
        annular_loop_fingerprint="ad115433319c3a00b85e7a505e8f588be147db16c700f595b20382fbcf374f30",
    ),
    "SegmentedMSR.Demos.R5x5Z10MSRRuhxRadialCirculating": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R5x5Z10MSRRuhxRadialCirculating",
        annular_fluid_mode="circulating",
        annular_heat_exchanger_enabled=False,
        annular_heat_exchanger_model="none",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=(0.04,) * 25,
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="573dc5bc7e7dd03aa68ea5ced30ce2ae9d050b697136d6c1f464e90263f7ddfc",
        annular_loop_fingerprint="5cab4d9b110cdcc076f6b640151918f6e3de03522771627dd693920c334145e7",
    ),
    "SegmentedMSR.Demos.R1MSRRuhxRadialStatic": RadialDemoVehicleContract(
        vehicle="SegmentedMSR.Demos.R1MSRRuhxRadialStatic",
        annular_fluid_mode="static",
        annular_heat_exchanger_enabled=False,
        annular_heat_exchanger_model="none",
        geometry_policy="effective_thermal",
        channel_meaning="literal_tube",
        channel_multiplicity=1,
        flow_direction="bottom_to_top",
        channel_flow_fractions=None,
        radial_dataset_id="SYNTHETIC-P6-DEMO",
        radial_maturity="exploratory_geometry",
        radial_fingerprint="9fd024ad9b78c4411c5a3e12f0cf34cf997d85bec89c2527278da16a294f1f26",
        annular_loop_fingerprint="1b9b4cd6f0543cf055f5acd4c5d0f6bf3372c7e81e6a332e3be86d9ffae7d112",
    ),
}

# The scope token every :data:`RADIAL_DEMO_VEHICLE_CONTRACTS` member
# carries (module-private; the public classification surface is
# :func:`demo_vehicle_contract_scopes`).
_INTRA_CHANNEL_RADIAL_DEMO_SCOPE = "intra_channel_radial"

# Explicit membership registry for the committed ``SegmentedMSR.Demos``
# vehicles that a DIFFERENT demo contract covers (TASK-20260917-01 P4).
# Every model in the ``Demos`` package must be registered in EXACTLY ONE
# demo contract scope: membership in :data:`RADIAL_DEMO_VEHICLE_CONTRACTS`
# is the intra-channel radial scope, and an entry HERE names a vehicle
# outside that scope together with the demo contract scope that covers it
# instead. An unregistered ``Demos`` model fails closed (every digest
# derivation and deck load refuses, naming the vehicle) until it is
# registered in one of the two -- no committed demo vehicle is ever
# silently omitted from a contract scope, and a future third demo cannot
# inherit a contract it does not belong to. An entry here must not claim
# the intra-channel radial scope (that membership is expressed only by a
# full contract above), and its vehicle must not bind
# ``IntraChannelRadialConfig``: either would make the registration stale,
# and :func:`_demo_vehicle_scopes` refuses naming the vehicle.
NON_RADIAL_DEMO_VEHICLE_SCOPES: dict[str, str] = {
    # Synthetic core-vessel demonstration (TASK-20260917-01 P4): binds
    # Core.OuterFuelAnnulusConfig (geometryPolicyCode = 2 / effective_thermal,
    # datasetId "SYNTHETIC-P4-DEMO") inside the Vessel.CoreVesselAssembly
    # wrapper and carries NO IntraChannelRadialConfig binding by design, so
    # the intra-channel radial demo fingerprint digests do not apply to it
    # and it must not reuse them. Its identity is the outer-fuel-annulus
    # demo surface (its own committed config fields; the generated-record
    # contract follows in P7).
    "SegmentedMSR.Demos.R1_10SegOuterFuelAnnulusCavity": "outer_fuel_annulus",
    # Synthetic core-vessel FISSION demonstration (TASK-20260918-01 P7;
    # plan §6 Phase 7): the P4 demo's SIBLING with the outer-annulus
    # fission split ENABLED as a conservative whole-power split
    # (datasetId "SYNTHETIC-P7-FISSION-DEMO", every fission quantity
    # synthetic). Same scope: it binds the SAME OuterFuelAnnulusConfig
    # face inside the SAME CoreVesselAssembly wrapper and carries NO
    # IntraChannelRadialConfig binding - the intra-channel radial demo
    # fingerprint digests do not apply to it either.
    "SegmentedMSR.Demos.R1_10SegOuterFuelAnnulusFissionCavity":
        "outer_fuel_annulus",
}


def _demo_vehicle_scopes(path: Path, demos_text: str) -> dict[str, str]:
    """Classify every declared ``SegmentedMSR.Demos`` model into exactly one
    demo contract scope: :data:`_INTRA_CHANNEL_RADIAL_DEMO_SCOPE` for the
    vehicles with a full contract in :data:`RADIAL_DEMO_VEHICLE_CONTRACTS`,
    or the registered non-radial scope (:data:`NON_RADIAL_DEMO_VEHICLE_SCOPES`)
    for the vehicles a different demo contract covers.

    Fail-closed both ways -- each refusal names every offending vehicle:

    - a ``Demos`` model registered in NEITHER scope (an unregistered new
      demo vehicle must be classified in the same change, never silently
      omitted);
    - a vehicle registered in BOTH (a full contract AND a non-radial
      scope -- ambiguous ownership);
    - a stale non-radial registration (the vehicle no longer exists in the
      package, claims the intra-channel radial scope without a contract, or
      binds ``IntraChannelRadialConfig`` despite its exclusion);
    - a ``Demos`` model declared more than once.
    """

    problems: list[str] = []
    scopes: dict[str, str] = {}
    declared: set[str] = set()
    for name, body in re.findall(r"\bmodel (\w+)(.*?)\bend \1;", demos_text, re.DOTALL):
        vehicle = f"SegmentedMSR.Demos.{name}"
        declared.add(vehicle)
        in_radial = vehicle in RADIAL_DEMO_VEHICLE_CONTRACTS
        excluded_scope = NON_RADIAL_DEMO_VEHICLE_SCOPES.get(vehicle)
        if vehicle in scopes:
            problems.append(
                f"{path}: {vehicle} is declared more than once in the "
                "Demos package; the demo contract classification is "
                "ambiguous"
            )
        elif in_radial and excluded_scope is not None:
            problems.append(
                f"{path}: {vehicle} carries BOTH a full intra-channel "
                "radial demo contract and a non-radial scope registration "
                f"({excluded_scope!r}); exactly one demo contract scope "
                "must own each committed Demos vehicle"
            )
        elif in_radial:
            scopes[vehicle] = _INTRA_CHANNEL_RADIAL_DEMO_SCOPE
        elif excluded_scope is not None:
            if not excluded_scope or excluded_scope == _INTRA_CHANNEL_RADIAL_DEMO_SCOPE:
                problems.append(
                    f"{path}: {vehicle} registers the demo contract scope "
                    f"{excluded_scope!r}; a non-radial registration must "
                    "name its own scope, and intra-channel radial scope "
                    "membership is expressed only by a full "
                    "RADIAL_DEMO_VEHICLE_CONTRACTS entry"
                )
            elif _demo_binding_blocks(body):
                problems.append(
                    f"{path}: {vehicle} is registered outside the "
                    "intra-channel radial demo contract (scope "
                    f"{excluded_scope!r}) but binds "
                    "IntraChannelRadialConfig; the scope registration is "
                    "stale - re-register the vehicle in the scope that "
                    "matches its committed bindings"
                )
            else:
                scopes[vehicle] = excluded_scope
        else:
            problems.append(
                f"{path}: {vehicle} is registered in no demo contract "
                "scope; every SegmentedMSR.Demos vehicle must be member "
                "of exactly one (a full RADIAL_DEMO_VEHICLE_CONTRACTS "
                "entry for the intra-channel radial scope, or an explicit "
                "NON_RADIAL_DEMO_VEHICLE_SCOPES entry naming the scope "
                "that covers it) - add the matching registration in the "
                "same change as the new demo vehicle"
            )
    for vehicle in NON_RADIAL_DEMO_VEHICLE_SCOPES:
        if vehicle not in declared:
            problems.append(
                f"{path}: {vehicle} is registered in "
                "NON_RADIAL_DEMO_VEHICLE_SCOPES but is not declared in "
                "the Demos package; the registration is stale (remove it "
                "or restore the vehicle)"
            )
    if problems:
        raise ValueError("\n".join(problems))
    return scopes


def demo_vehicle_contract_scopes(
    source_path: str | Path | None = None,
) -> dict[str, str]:
    """The demo contract scope of every committed ``SegmentedMSR.Demos``
    vehicle: ``"intra_channel_radial"`` for the vehicles covered by the
    radial demo identity contract (:data:`RADIAL_DEMO_VEHICLE_CONTRACTS`),
    or the explicitly registered non-radial scope (e.g.
    ``"outer_fuel_annulus"``) for the vehicles a different demo contract
    covers. Fail-closed: the classification refuses an unregistered,
    ambiguously registered, or stale ``Demos`` vehicle, naming it
    (:func:`_demo_vehicle_scopes`). Pass a scratch copy of
    ``core/SegmentedMSR.mo`` as ``source_path`` to classify mutated
    bindings."""

    path, demos_text = _demo_package_text(source_path)
    return _demo_vehicle_scopes(path, demos_text)


def radial_demo_contract_mismatches(
    scenario: Mapping[str, Any],
    source_path: str | Path | None = None,
) -> list[tuple[str, str]]:
    """Exact-equality identity check of a radial_demo deck against the
    code-owned contract of the named committed ``SegmentedMSR.Demos``
    vehicle (review rev019 "High — radial demo manifests can claim
    configuration that the vehicle did not execute"; planner resolution 1
    minimum).

    Returns ``(configuration path, message)`` pairs; each message names the
    offending value and the compiled value it must equal. An empty list
    means the deck's claimed radial identity IS the vehicle's compiled
    identity. Unknown vehicle names refuse (fail-closed whitelist: only
    committed vehicles with a contract here are executable -- a Demos
    vehicle covered by a different demo contract, registered outside the
    intra-channel radial scope, is not executable as a radial_demo deck).
    The check also
    re-derives the demo fingerprint digests from the committed Modelica
    bindings (review rev020 Phase 5 item 2) and refuses when the code-owned
    contract fingerprints no longer match them - so a deck can never
    satisfy a contract that has gone stale against the physics-bearing demo
    values the vehicle now binds. Called at deck load time
    (:func:`_validate_radial_demo`) and again by the demo runner before any
    build, so a mutated dataset id, fingerprint, HX model, flow direction,
    flow distribution, or a stale contract can neither build nor publish.
    Pass ``source_path`` to re-derive against a scratch copy of
    ``core/SegmentedMSR.mo`` instead of the committed source.
    """

    scenario_id = str(scenario.get("id") or "<unknown>")
    vehicle = scenario.get("vehicle")
    contract = (
        RADIAL_DEMO_VEHICLE_CONTRACTS.get(vehicle)
        if isinstance(vehicle, str)
        else None
    )
    if contract is None:
        return [
            (
                "vehicle",
                f"{scenario_id!r} names demo vehicle {vehicle!r}, which has "
                "no committed SegmentedMSR.Demos identity contract; only "
                "the exact committed vehicle names are executable: "
                f"{sorted(RADIAL_DEMO_VEHICLE_CONTRACTS)}",
            )
        ]
    mismatches: list[tuple[str, str]] = []

    # The contract fingerprints must still equal the digests derived from
    # the committed bindings (review rev020 Phase 5 item 2): a physics-
    # bearing demo constant that changed without re-deriving the digests
    # makes every deck for that vehicle refuse, naming the divergence.
    source = _demo_modelica_path(source_path)
    derived = (
        radial_demo_physics_digests(source_path).get(vehicle)
        if isinstance(vehicle, str)
        else None
    )
    if derived is None:
        mismatches.append(
            (
                "vehicle",
                f"{scenario_id!r} names demo vehicle {vehicle!r}, whose "
                "radial fingerprint digest could not be derived from "
                f"{source} (the vehicle is absent from the Demos package "
                "there, or carries no IntraChannelRadialConfig binding); "
                "the identity contract cannot be verified",
            )
        )
    else:
        if contract.radial_fingerprint != derived["radial"]:
            mismatches.append(
                (
                    "radial_config.radial_fingerprint",
                    f"the code-owned contract fingerprint "
                    f"{contract.radial_fingerprint!r} does not equal the "
                    f"digest {derived['radial']!r} derived from the "
                    f"committed SegmentedMSR.Demos bindings in {source} "
                    "(the demo fingerprints are digests of the "
                    "physics-bearing demo values; a physics-bearing "
                    "constant changed - re-derive the digests and re-bind "
                    "them in core/SegmentedMSR.mo, this contract map, and "
                    "the committed decks)",
                )
            )
        if contract.annular_loop_fingerprint != derived["loop"]:
            mismatches.append(
                (
                    "radial_config.annular_loop_fingerprint",
                    f"the code-owned contract fingerprint "
                    f"{contract.annular_loop_fingerprint!r} does not equal "
                    f"the digest {derived['loop']!r} derived from the "
                    f"committed SegmentedMSR.Demos bindings in {source} "
                    "(the demo fingerprints are digests of the "
                    "physics-bearing demo values; a physics-bearing "
                    "constant changed - re-derive the digests and re-bind "
                    "them in core/SegmentedMSR.mo, this contract map, and "
                    "the committed decks)",
                )
            )
    radial = scenario.get("radial_config")
    if not isinstance(radial, Mapping):
        return [
            (
                "radial_config",
                "radial_demo decks require a 'radial_config' section",
            )
        ]

    def _check(path: str, claimed: Any, expected: Any) -> None:
        if claimed != expected:
            mismatches.append(
                (
                    path,
                    f"value {claimed!r} does not match the compiled identity "
                    f"of {vehicle!r} (expected {expected!r})",
                )
            )

    _check(
        "radial_config.annular_fluid_mode",
        radial.get("annular_fluid_mode", "static"),
        contract.annular_fluid_mode,
    )
    _check(
        "radial_config.annular_heat_exchanger_enabled",
        radial.get("annular_heat_exchanger_enabled", False),
        contract.annular_heat_exchanger_enabled,
    )
    _check(
        "radial_config.annular_heat_exchanger_model",
        radial.get("annular_heat_exchanger_model", RADIAL_HX_MODEL_NONE),
        contract.annular_heat_exchanger_model,
    )
    _check(
        "radial_config.geometry_policy",
        radial.get("geometry_policy", "strict_physical"),
        contract.geometry_policy,
    )
    _check(
        "radial_config.flow_direction",
        radial.get("flow_direction", "bottom_to_top"),
        contract.flow_direction,
    )
    _check(
        "radial_config.radial_dataset_id",
        radial.get("radial_dataset_id"),
        contract.radial_dataset_id,
    )
    _check(
        "radial_config.radial_maturity",
        radial.get("radial_maturity"),
        contract.radial_maturity,
    )
    _check(
        "radial_config.radial_fingerprint",
        radial.get("radial_fingerprint"),
        contract.radial_fingerprint,
    )
    _check(
        "radial_config.annular_loop_fingerprint",
        radial.get("annular_loop_fingerprint"),
        contract.annular_loop_fingerprint,
    )
    claimed_fractions = radial.get("channel_flow_fractions")
    expected_fractions = contract.channel_flow_fractions
    if expected_fractions is None:
        if claimed_fractions is not None:
            mismatches.append(
                (
                    "radial_config.channel_flow_fractions",
                    f"value {claimed_fractions!r} is refused: the static "
                    f"vehicle {vehicle!r} binds no annular loop and no flow "
                    "distribution (plan §10.12 omission pattern)",
                )
            )
    elif claimed_fractions is None:
        mismatches.append(
            (
                "radial_config.channel_flow_fractions",
                f"required: the circulating vehicle {vehicle!r} binds "
                f"{contract.channel_count} channel(s) with fractions "
                f"{list(expected_fractions)!r}",
            )
        )
    else:
        claimed = (
            [float(item) for item in claimed_fractions]
            if isinstance(claimed_fractions, list)
            else claimed_fractions
        )
        if claimed != list(expected_fractions):
            mismatches.append(
                (
                    "radial_config.channel_flow_fractions",
                    f"value {claimed_fractions!r} does not match the compiled "
                    f"channel distribution of {vehicle!r} (expected "
                    f"{list(expected_fractions)!r}: "
                    f"{contract.channel_count} channel(s))",
                )
            )
    return mismatches


def _validate_radial_demo(raw: Mapping[str, Any], label: str) -> None:
    """Full validation of a merged ``radial_demo`` deck (plan §14, §9).

    A radial_demo deck MUST name a committed ``SegmentedMSR.Demos`` vehicle
    (refused otherwise by name), MUST carry the exploratory_geometry
    maturity (synthetic placeholder geometry, no plant-prediction claims),
    and its ``radial_config`` block must form a valid plan §2.2 structural
    combination with non-empty radial/loop fingerprint identities. The
    numerics section (``stop_time_s`` and ``number_of_intervals``) is
    required. Finally, the deck's claimed radial identity must EQUAL the
    code-owned contract of the named vehicle field by field
    (:func:`radial_demo_contract_mismatches`; review rev019: a deck may not
    claim a dataset id, fingerprint, HX model, flow direction, or flow
    distribution the compiled vehicle does not bind). The decks are
    exploratory examples: they are deliberately NOT referenced by any
    campaign table, and the runner families cannot load them (kind-matched
    loaders refuse).
    """

    vehicle = raw.get("vehicle")
    if not isinstance(vehicle, str) or not vehicle.strip():
        fail(
            label,
            "vehicle",
            f"radial_demo decks need a non-empty vehicle class name, got {vehicle!r}",
        )
    elif not vehicle.startswith(RADIAL_DEMO_VEHICLE_PREFIX):
        fail(
            label,
            "vehicle",
            f"must name a committed exploratory demo vehicle "
            f"({RADIAL_DEMO_VEHICLE_PREFIX}<name>); got {vehicle!r}. Only the "
            "SegmentedMSR.Demos package carries a structural intra-channel "
            "radial record",
        )
    elif not _RADIAL_DEMO_VEHICLE_TAIL_RE.fullmatch(
        vehicle[len(RADIAL_DEMO_VEHICLE_PREFIX) :]
    ):
        # rev022 M-6: identifier-grammar confinement -- no '/', '\',
        # whitespace, or '..' segments may follow the package prefix, so
        # the name can never traverse outside the runner's workdir.
        fail(
            label,
            "vehicle",
            f"must name a committed exploratory demo vehicle as "
            f"{RADIAL_DEMO_VEHICLE_PREFIX}<identifier>, where <identifier> "
            "is a single-token identifier tail ([A-Za-z0-9_][A-Za-z0-9_.]* "
            "with no '/', '\\', whitespace, or '..' segments); got "
            f"{vehicle!r}",
        )
    maturity = raw.get("maturity")
    if maturity != RADIAL_DEMO_MATURITY:
        fail(
            label,
            "maturity",
            f"radial_demo decks must declare maturity "
            f"{RADIAL_DEMO_MATURITY!r} (synthetic placeholder geometry, no "
            f"plant-prediction claims), got {maturity!r}",
        )
    radial = raw.get("radial_config")
    if radial is None:
        fail(label, "radial_config", "radial_demo decks require a 'radial_config' section")
        return
    require_mapping(radial, label, "radial_config", "'radial_config'")
    unknown = sorted(str(key) for key in radial if key not in _RADIAL_DEMO_RADIAL_KEYS)
    if unknown:
        fail(
            label,
            "radial_config",
            f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_DEMO_RADIAL_KEYS)}",
        )
    enabled = radial.get("enabled")
    if enabled is not True:
        fail(
            label,
            "radial_config.enabled",
            f"radial_demo decks describe ENABLED stacks; got {enabled!r} "
            "(a disabled run needs no radial_demo deck - the production "
            "vehicles stay disabled by default)",
        )
    mode = radial.get("annular_fluid_mode", "static")
    if mode not in ANNULAR_FLUID_MODES:
        fail(
            label,
            "radial_config.annular_fluid_mode",
            f"must be one of {list(ANNULAR_FLUID_MODES)}, got {mode!r}",
        )
    hx_enabled = radial.get("annular_heat_exchanger_enabled", False)
    if not isinstance(hx_enabled, bool):
        fail(
            label,
            "radial_config.annular_heat_exchanger_enabled",
            f"must be a boolean, got {hx_enabled!r}",
        )
    if hx_enabled and mode != "circulating":
        fail(
            label,
            "radial_config.annular_heat_exchanger_enabled",
            f"true requires annular_fluid_mode='circulating' (plan §2.2); got {mode!r}",
        )
    policy = radial.get("geometry_policy", "strict_physical")
    if policy not in RADIAL_GEOMETRY_POLICIES:
        fail(
            label,
            "radial_config.geometry_policy",
            f"must be one of {list(RADIAL_GEOMETRY_POLICIES)}, got {policy!r}",
        )
    flow_direction = radial.get("flow_direction", "bottom_to_top")
    if flow_direction not in FLOW_DIRECTIONS:
        fail(
            label,
            "radial_config.flow_direction",
            f"must be one of {list(FLOW_DIRECTIONS)}, got {flow_direction!r}",
        )
    # HX model identity (review rev020 Phase 5 item 1; TASK-20260916-01
    # P2): bound to the deck's heat-exchanger state exactly like the plant
    # side -- enabled names the one implemented finite-conductance
    # prescribed-sink model, disabled carries the canonical "none".
    hx_model = radial.get("annular_heat_exchanger_model", RADIAL_HX_MODEL_NONE)
    expected_hx_model = (
        RADIAL_HX_MODEL_FINITE_CONDUCTANCE if hx_enabled else RADIAL_HX_MODEL_NONE
    )
    if hx_model != expected_hx_model:
        fail(
            label,
            "radial_config.annular_heat_exchanger_model",
            f"must be exactly '{RADIAL_HX_MODEL_FINITE_CONDUCTANCE}' while "
            f"annular_heat_exchanger_enabled=true and "
            f"'{RADIAL_HX_MODEL_NONE}' while false (the only implemented "
            f"heat-exchanger model; the structural switch is the enabled "
            f"boolean), got {hx_model!r}",
        )
    for key in ("radial_dataset_id", "radial_fingerprint", "annular_loop_fingerprint"):
        value = radial.get(key)
        if not isinstance(value, str) or not value.strip():
            fail(
                label,
                f"radial_config.{key}",
                f"must be a non-empty string identifying the committed demo "
                f"data, got {value!r}",
            )
    radial_maturity = radial.get("radial_maturity")
    if radial_maturity != RADIAL_DEMO_MATURITY:
        fail(
            label,
            "radial_config.radial_maturity",
            f"must be {RADIAL_DEMO_MATURITY!r} (the committed synthetic demo "
            f"datasets), got {radial_maturity!r}",
        )
    # Declared channel flow distribution (plan §6.4): REQUIRED in
    # circulating mode, forbidden in static mode (no loop exists - plan
    # §10.12: static carries no loop states, so loop data would be phantom).
    fractions = radial.get("channel_flow_fractions")
    if mode == "circulating":
        if fractions is None:
            fail(
                label,
                "radial_config.channel_flow_fractions",
                "required in circulating mode (one fraction per annular-loop "
                "channel; plan §6.4)",
            )
        else:
            require_list(
                fractions, label, "radial_config.channel_flow_fractions",
                what="'channel_flow_fractions'",
            )
            values = []
            for index, item in enumerate(fractions):
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    fail(
                        label,
                        f"radial_config.channel_flow_fractions[{index}]",
                        f"must be a number >= 0, got {item!r}",
                    )
                if float(item) < 0:
                    fail(
                        label,
                        f"radial_config.channel_flow_fractions[{index}]",
                        f"must be >= 0, got {item!r}",
                    )
                values.append(float(item))
            total = sum(values)
            if abs(total - 1.0) > 1e-6:
                fail(
                    label,
                    "radial_config.channel_flow_fractions",
                    f"must sum to 1 within 1e-6 (SegmentedMSR.Core.fracTol), "
                    f"got {total!r}",
                )
    elif fractions is not None:
        fail(
            label,
            "radial_config.channel_flow_fractions",
            f"channel flow fractions apply only to circulating mode (static "
            f"mode carries no loop distribution; plan §10.12), got {fractions!r}",
        )
    numerics = raw.get("numerics")
    if numerics is None:
        fail(label, "numerics", "radial_demo scenarios require a 'numerics' section")
        return
    require_mapping(numerics, label, "numerics", "'numerics'")
    unknown = sorted(str(key) for key in numerics if key not in _RADIAL_DEMO_NUMERIC_KEYS)
    if unknown:
        fail(
            label,
            "numerics",
            f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_DEMO_NUMERIC_KEYS)}",
        )
    for name in ("stop_time_s", "number_of_intervals"):
        if numerics.get(name) is None:
            fail(label, f"numerics.{name}", "required for radial_demo scenarios")
    _validate_numerics(numerics, label)
    # Exact-equality identity check against the code-owned committed vehicle
    # contract (review rev019 High — manifests can claim unexecuted
    # configuration). Runs after the well-formedness checks so a type error
    # is named before a mismatch; every refusal names the configuration path
    # and both the offending and the compiled value.
    for path, message in radial_demo_contract_mismatches(raw):
        fail(label, path, message)


def radial_demo_vehicle(scenario: Mapping[str, Any]) -> str:
    """The committed ``SegmentedMSR.Demos`` vehicle a radial_demo deck names."""

    vehicle = scenario.get("vehicle")
    if not isinstance(vehicle, str) or not vehicle.strip():
        raise ValueError(
            f"scenario {scenario.get('id')!r} is not a validated radial_demo "
            f"deck (vehicle field missing)"
        )
    return vehicle


def radial_demo_manifest_record(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """The fingerprint-active ``intra_channel_radial`` manifest record.

    Same key shape as ``helpers.plant_config.radial_manifest_record`` so
    run manifests carry the identical radial identity whether it came from
    the plant deck (production vehicles) or from a validated radial_demo
    scenario (the exploratory Demos vehicles, which bind their radial
    record in Modelica). Built FROM the code-owned vehicle contract
    (:data:`RADIAL_DEMO_VEHICLE_CONTRACTS`) -- NOT from the deck's claimed
    identity: the claims must equal the contract exactly
    (:func:`radial_demo_contract_mismatches`, enforced at deck load and
    again by the runner before any build), so the manifest can only ever
    publish the identity the compiled vehicle actually binds (review
    rev019 "High — radial demo manifests can claim configuration that the
    vehicle did not execute"). Circulating vehicles additionally carry
    ``channelFlowFractions`` (the exact plan §6.4 distribution the vehicle
    binds; absent on static vehicles -- the omission pattern).
    """

    radial = scenario.get("radial_config")
    if not isinstance(radial, Mapping) or radial.get("enabled") is not True:
        raise ValueError(
            f"scenario {scenario.get('id')!r} is not a validated radial_demo "
            "deck (radial_config.enabled is not true)"
        )
    vehicle = scenario.get("vehicle")
    contract = (
        RADIAL_DEMO_VEHICLE_CONTRACTS.get(vehicle)
        if isinstance(vehicle, str)
        else None
    )
    if contract is None:
        raise ValueError(
            f"scenario {scenario.get('id')!r} names demo vehicle {vehicle!r}, "
            "which has no committed SegmentedMSR.Demos identity contract; "
            "refusing to publish a manifest record for an unverified "
            "vehicle identity"
        )
    record: dict[str, Any] = {
        "enabled": True,
        "annularFluidMode": contract.annular_fluid_mode,
        "annularHeatExchangerEnabled": contract.annular_heat_exchanger_enabled,
        "annularHeatExchangerModel": contract.annular_heat_exchanger_model,
        "geometryPolicy": contract.geometry_policy,
        "channelMeaning": contract.channel_meaning,
        "channelMultiplicity": contract.channel_multiplicity,
        "flowDirection": contract.flow_direction,
        "radialDatasetId": contract.radial_dataset_id,
        "radialMaturity": contract.radial_maturity,
        "radialFingerprint": contract.radial_fingerprint,
        "annularLoopFingerprint": contract.annular_loop_fingerprint,
    }
    if contract.channel_flow_fractions is not None:
        record["channelFlowFractions"] = list(contract.channel_flow_fractions)
    return record


def _require_numerics(raw: Mapping[str, Any], label: str, names: tuple[str, ...]) -> None:
    numerics = raw.get("numerics")
    if numerics is None:
        fail(
            label,
            "numerics",
            f"{raw.get('kind')} scenarios require a 'numerics' section",
        )
    require_mapping(numerics, label, "numerics", "'numerics'")
    for name in names:
        if numerics.get(name) is None:
            fail(
                label,
                f"numerics.{name}",
                f"required for {raw.get('kind')} scenarios",
            )


def _validate_numerics(numerics: Mapping[str, Any], label: str) -> None:
    for name in (
        "stop_time_s",
        "uhx_stop_time_s",
        "segmented_trip_min_stop_time_s",
        "segmented_flow_min_stop_time_s",
        "tolerance",
        "max_step_size_s",
        "min_cycles_after_ss",
        "output_intervals_per_second",
        "step_output_interval_s",
    ):
        value = numerics.get(name)
        if value is not None:
            require_number(value, label, f"numerics.{name}", exclusive_minimum=0.0)
    start_time = numerics.get("start_time_s")
    if start_time is not None:
        require_number(start_time, label, "numerics.start_time_s", minimum=0.0)
    intervals = numerics.get("number_of_intervals")
    if intervals is not None:
        require_number(intervals, label, "numerics.number_of_intervals", minimum=1, integer=True)
    # Fail-closed solver-name whitelist (review rev019 Medium-high; planner
    # resolution 7): an unknown integration method would either fail at omc
    # runtime or -- worse -- silently run under a different method than the
    # deck and its manifest claim. Refuse at load time with the whitelist
    # named.
    method = numerics.get("method")
    if method is not None and method not in SUPPORTED_SIMULATION_METHODS:
        fail(
            label,
            "numerics.method",
            f"must be one of {list(SUPPORTED_SIMULATION_METHODS)} (the "
            f"OpenModelica generated-executable solver whitelist, "
            f"-s=<method>), got {method!r}",
        )
    stop_time_mode = numerics.get("stop_time_mode")
    if stop_time_mode is not None and stop_time_mode not in ("fixed", "min_cycles_after_ss"):
        fail(
            label,
            "numerics.stop_time_mode",
            f"must be 'fixed' or 'min_cycles_after_ss', got {stop_time_mode!r}",
        )


def _validate_structural(structural: Mapping[str, Any], raw: Mapping[str, Any], label: str) -> None:
    for name, value in structural.items():
        require_number(
            value, label, f"structural.{name}", minimum=1, integer=True
        )
    forcing = raw.get("forcing") or {}
    source = forcing.get("source") or {}
    if "numSourceSteps" in structural:
        times = source.get("times_s")
        if isinstance(times, list):
            expected = structural["numSourceSteps"]
            if expected != len(times):
                fail(
                    label,
                    "structural.numSourceSteps",
                    f"value {expected} does not match len(forcing.source.times_s) = {len(times)}",
                )
    reactivity = forcing.get("reactivity_pcm") or {}
    if "numExternalReactivitySteps" in structural:
        amplitudes = reactivity.get("amplitudes")
        if isinstance(amplitudes, list):
            expected = structural["numExternalReactivitySteps"]
            if expected != len(amplitudes):
                fail(
                    label,
                    "structural.numExternalReactivitySteps",
                    f"value {expected} does not match"
                    f" len(forcing.reactivity_pcm.amplitudes) = {len(amplitudes)}",
                )
    if "numUhxSteps" in structural:
        expected_steps = _uhx_schedule_length(forcing.get("uhx_demand"))
        if expected_steps is not None and structural["numUhxSteps"] != expected_steps:
            fail(
                label,
                "structural.numUhxSteps",
                f"value {structural['numUhxSteps']} does not match the UHX demand"
                f" schedule length ({expected_steps})",
            )
    if "numRampUp" in structural:
        pump = forcing.get("pump") or {}
        expected_ramps = structural["numRampUp"]
        ramp_to = pump.get("rampUpTo")
        if is_quantity(ramp_to):
            values = quantity_value(ramp_to)
            if isinstance(values, list) and expected_ramps != len(values):
                fail(
                    label,
                    "structural.numRampUp",
                    f"value {expected_ramps} does not match len(forcing.pump.rampUpTo.value) = {len(values)}",
                )
        for name in ("rampUpTime_s", "rampUpK_s"):
            values = pump.get(name)
            if isinstance(values, list) and expected_ramps != len(values):
                fail(
                    label,
                    "structural.numRampUp",
                    f"value {expected_ramps} does not match len(forcing.pump.{name}) = {len(values)}",
                )


def _uhx_schedule_length(node: Any) -> int | None:
    if not isinstance(node, Mapping):
        return None
    if "demand_W" in node and "times_s" in node:
        times = node["times_s"]
        return len(times) if isinstance(times, list) else None
    if "frac" in node:
        frac = node["frac"]
        return 1 + len(frac) if isinstance(frac, list) else None
    if "times_s" in node and "amplitudes_W" in node:
        times = node["times_s"]
        return len(times) if isinstance(times, list) else None
    return None


def _validate_forcing(forcing: Mapping[str, Any], label: str) -> None:
    source = forcing.get("source")
    if isinstance(source, Mapping):
        times = source.get("times_s")
        amplitudes_present = ("amplitudes" in source) or ("amplitudes_n_s" in source)
        if times is not None and not amplitudes_present:
            fail(
                label,
                "forcing.source.times_s",
                "needs a matching amplitude list ('amplitudes' or 'amplitudes_n_s')",
            )
        if times is not None:
            require_list(times, label, "forcing.source.times_s", what="'times_s'")
            time_values = require_numbers(times, label, "forcing.source.times_s", what="'times_s' entries")
            require_strictly_increasing(
                time_values, label, "forcing.source.times_s", what="source step times"
            )
        for key in ("amplitudes", "amplitudes_n_s"):
            amplitudes = source.get(key)
            if amplitudes is None:
                continue
            require_list(amplitudes, label, f"forcing.source.{key}", what=f"'{key}'")
            if times is not None and len(amplitudes) != len(times):
                fail(
                    label,
                    f"forcing.source.{key}",
                    f"length {len(amplitudes)} does not match"
                    f" len(forcing.source.times_s) = {len(times)}",
                )
            for index, item in enumerate(amplitudes):
                if isinstance(item, str):
                    if item in ("S", "strength"):
                        continue
                    coerced = coerce_number(item)
                    if isinstance(coerced, str):
                        fail(
                            label,
                            f"forcing.source.{key}[{index}]",
                            f"entries must be numbers or the 'S' (strength)"
                            f" placeholder, got {item!r}",
                        )
                else:
                    require_number(item, label, f"forcing.source.{key}[{index}]")
    reactivity = forcing.get("reactivity_pcm")
    if isinstance(reactivity, Mapping):
        times = reactivity.get("times_s")
        amplitudes = reactivity.get("amplitudes")
        if times is not None and amplitudes is None:
            fail(
                label,
                "forcing.reactivity_pcm.times_s",
                "needs a matching 'amplitudes' list",
            )
        if times is not None:
            require_list(times, label, "forcing.reactivity_pcm.times_s", what="'times_s'")
            time_values = require_numbers(
                times, label, "forcing.reactivity_pcm.times_s", what="'times_s' entries"
            )
            require_strictly_increasing(
                time_values, label, "forcing.reactivity_pcm.times_s", what="reactivity step times"
            )
        if amplitudes is not None:
            require_list(
                amplitudes, label, "forcing.reactivity_pcm.amplitudes", what="'amplitudes'"
            )
            require_numbers(
                amplitudes,
                label,
                "forcing.reactivity_pcm.amplitudes",
                what="'amplitudes' entries",
            )
            if times is not None and len(amplitudes) != len(times):
                fail(
                    label,
                    "forcing.reactivity_pcm.amplitudes",
                    f"length {len(amplitudes)} does not match"
                    f" len(forcing.reactivity_pcm.times_s) = {len(times)}",
                )
    uhx = forcing.get("uhx_demand")
    if isinstance(uhx, Mapping):
        _validate_uhx_demand(uhx, label, "forcing.uhx_demand")
    pump = forcing.get("pump")
    if isinstance(pump, Mapping):
        _validate_scenario_pump(pump, label)


def _validate_uhx_demand(node: Mapping[str, Any], label: str, path: str) -> None:
    if "frac" in node:
        require_list(node["frac"], label, f"{path}.frac", what="'frac'")
        frac_values = require_numbers(node["frac"], label, f"{path}.frac", what="'frac' entries")
        for index, value in enumerate(frac_values):
            if not 0.0 <= value <= 1.0:
                fail(
                    label,
                    f"{path}.frac[{index}]",
                    f"ramp fraction must be within [0, 1], got {value}",
                )
        final_w = node.get("final_W")
        if final_w is not None:
            require_number(final_w, label, f"{path}.final_W", minimum=0.0)
        delta_s = node.get("delta_s")
        if delta_s is not None:
            require_number(delta_s, label, f"{path}.delta_s", exclusive_minimum=0.0)
        ramp_start_s = node.get("ramp_start_s")
        if ramp_start_s is not None:
            require_number(ramp_start_s, label, f"{path}.ramp_start_s", minimum=0.0)
        fission_fraction = node.get("fission_fraction")
        if fission_fraction is not None:
            require_number(
                fission_fraction,
                label,
                f"{path}.fission_fraction",
                exclusive_minimum=0.0,
                maximum=1.0,
            )
    if "times_s" in node:
        amplitude_key = next(
            (key for key in ("demand_W", "amplitudes_W") if key in node), None
        )
        if amplitude_key is None and "frac" not in node:
            fail(
                label,
                f"{path}.times_s",
                "needs a matching amplitude list ('amplitudes_W' or 'demand_W')"
                " or a frac-form ramp",
            )
            return
        require_list(node["times_s"], label, f"{path}.times_s", what="'times_s'")
        time_values = require_numbers(
            node["times_s"], label, f"{path}.times_s", what="'times_s' entries"
        )
        require_strictly_increasing(time_values, label, f"{path}.times_s", what="UHX demand times")
        if amplitude_key is not None:
            require_list(
                node[amplitude_key], label, f"{path}.{amplitude_key}", what=f"'{amplitude_key}'"
            )
            demand_values = require_numbers(
                node[amplitude_key],
                label,
                f"{path}.{amplitude_key}",
                what=f"'{amplitude_key}' entries",
            )
            for index, value in enumerate(demand_values):
                if value < 0.0:
                    fail(
                        label,
                        f"{path}.{amplitude_key}[{index}]",
                        f"demand power must be non-negative, got {value}",
                    )
            if len(demand_values) != len(time_values):
                fail(
                    label,
                    f"{path}.{amplitude_key}",
                    f"length {len(demand_values)} does not match len({path}.times_s)"
                    f" = {len(time_values)}",
                )


def _validate_scenario_pump(pump: Mapping[str, Any], label: str) -> None:
    free_conv_ff = pump.get("freeConvFF")
    if is_quantity(free_conv_ff):
        value = float(quantity_value(free_conv_ff))
        if not 0.0 < value <= 1.0:
            fail(
                label,
                "forcing.pump.freeConvFF.value",
                f"free-convective flow-fraction floor must be within (0, 1], got {value}",
            )
    ramp_to = pump.get("rampUpTo")
    if is_quantity(ramp_to):
        values = quantity_value(ramp_to)
        if isinstance(values, list):
            for index, item in enumerate(values):
                value = float(item)
                if not 0.0 < value <= 1.0:
                    fail(
                        label,
                        f"forcing.pump.rampUpTo.value[{index}]",
                        f"ramp target must be within (0, 1], got {value}",
                    )
    times = pump.get("rampUpTime_s")
    rates = pump.get("rampUpK_s")
    if times is not None:
        require_list(times, label, "forcing.pump.rampUpTime_s", what="'rampUpTime_s'")
        time_values = require_numbers(
            times, label, "forcing.pump.rampUpTime_s", what="'rampUpTime_s' entries"
        )
        require_strictly_increasing(
            time_values, label, "forcing.pump.rampUpTime_s", what="pump ramp times"
        )
    if rates is not None:
        rate_values = require_numbers(
            rates, label, "forcing.pump.rampUpK_s", what="'rampUpK_s' entries"
        )
        for index, value in enumerate(rate_values):
            if value <= 0.0:
                fail(
                    label,
                    f"forcing.pump.rampUpK_s[{index}]",
                    f"ramp time constant must be positive, got {value}",
                )
    if times is not None and rates is not None and len(times) != len(rates):
        fail(
            label,
            "forcing.pump.rampUpK_s",
            f"length {len(rates)} does not match len(forcing.pump.rampUpTime_s) = {len(times)}",
        )
    trip_time_s = pump.get("tripTime_s")
    if trip_time_s is not None:
        require_number(trip_time_s, label, "forcing.pump.tripTime_s", exclusive_minimum=0.0)


def _validate_grid(grid: Mapping[str, Any], label: str) -> None:
    omega_min = grid.get("omega_min")
    if omega_min is not None:
        require_number(omega_min, label, "grid.omega_min", exclusive_minimum=0.0)
    omega_max = grid.get("omega_max")
    if omega_max is not None:
        require_number(omega_max, label, "grid.omega_max", exclusive_minimum=0.0)
    if omega_min is not None and omega_max is not None and not omega_min < omega_max:
        fail(
            label,
            "grid.omega_max",
            f"must be greater than grid.omega_min ({omega_min}), got {omega_max}",
        )
    omega = grid.get("omega")
    if omega is not None:
        require_list(omega, label, "grid.omega", what="'omega'")
        omega_values = require_numbers(omega, label, "grid.omega", what="'omega' entries")
        for index, value in enumerate(omega_values):
            if value <= 0.0:
                fail(
                    label,
                    f"grid.omega[{index}]",
                    f"omega must be positive (rad/s), got {value}",
                )
        if len(omega_values) > 1:
            require_strictly_increasing(
                omega_values, label, "grid.omega", what="omega points"
            )
    count = grid.get("n")
    if count is not None:
        require_number(count, label, "grid.n", minimum=1, integer=True)
    unit = grid.get("unit")
    if unit is not None and (not isinstance(unit, str) or unit not in SUPPORTED_UNITS):
        fail(
            label,
            "grid.unit",
            f"unsupported unit {unit!r}; supported units: {sorted(SUPPORTED_UNITS)}",
        )
    spacing = grid.get("spacing")
    if spacing is not None and not isinstance(spacing, str):
        fail(label, "grid.spacing", f"must be a string, got {spacing!r}")


def _validate_powers(powers: Any, label: str) -> None:
    require_list(powers, label, "powers_mw", what="'powers_mw'")
    for index in range(len(powers)):
        require_number(powers[index], label, f"powers_mw[{index}]", exclusive_minimum=0.0)


def _validate_low_power_protocol(low: Mapping[str, Any], label: str) -> None:
    for name in ("threshold_mw", "ss_factor", "nfloor", "min_cycles_after_ss"):
        value = low.get(name)
        if value is not None:
            require_number(value, label, f"low_power_protocol.{name}", exclusive_minimum=0.0)


def _validate_init(init: Mapping[str, Any], label: str) -> None:
    per_core = init.get("per_core")
    if per_core is None:
        return
    require_mapping(per_core, label, "init.per_core", "'init.per_core'")
    for core, node in per_core.items():
        if core not in _CORE_KEYS:
            fail(
                label,
                f"init.per_core.{core}",
                f"unknown core {core!r}; valid core keys: {list(_CORE_KEYS)}",
            )
        require_mapping(node, label, f"init.per_core.{core}", f"init.per_core entry '{core}'")
        for name in ("TF1", "TF2", "TG"):
            temperature = node.get(name)
            if temperature is not None and str(temperature.get("unit", "")) != "degC":
                fail(
                    label,
                    f"init.per_core.{core}.{name}.unit",
                    f"initial temperatures must be degC (plant convention),"
                    f" got {temperature.get('unit')!r}",
                )
    power_mw = init.get("power_mw")
    if power_mw is not None:
        require_number(power_mw, label, "init.power_mw", exclusive_minimum=0.0)


def _validate_cases(cases: Any, label: str) -> None:
    require_list(cases, label, "cases", what="'cases'")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        path = f"cases[{index}]"
        require_mapping(case, label, path, f"case entry 'cases[{index}]'")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            fail(label, f"cases[{index}].id", f"needs a non-empty string id, got {case_id!r}")
        if case_id in seen:
            fail(label, f"cases[{index}].id", f"duplicate case id {case_id!r}")
        seen.add(case_id)
        family = case.get("family")
        if family not in _CASE_FAMILIES:
            fail(
                label,
                f"cases[{index}].family",
                f"must be one of {list(_CASE_FAMILIES)}, got {family!r}",
            )
        if family == "step":
            if case.get("pcm") is None:
                fail(label, f"cases[{index}].pcm", "step cases need a 'pcm' amplitude")
            else:
                require_number(case["pcm"], label, f"cases[{index}].pcm")
            _check_seconds_field(case, "insert_s", label, f"cases[{index}]")
            _check_flow_fraction(case, label, index)
        elif family == "flow":
            if case.get("pcm") is None:
                fail(label, f"cases[{index}].pcm", "flow cases need a 'pcm' amplitude")
            else:
                require_number(case["pcm"], label, f"cases[{index}].pcm")
            for name in ("pump_trip_s", "insert_s"):
                _check_seconds_field(case, name, label, f"cases[{index}]")
            _check_flow_fraction(case, label, index)
            follow = case.get("uhx_demand_follows_flow")
            if follow is not None and not isinstance(follow, bool):
                fail(
                    label,
                    f"cases[{index}].uhx_demand_follows_flow",
                    f"must be true or false, got {follow!r}",
                )
        else:
            times = case.get("times_s")
            demand = case.get("demand_W")
            if times is None or demand is None:
                fail(
                    label,
                    f"cases[{index}].times_s",
                    "uhx_trip cases need 'times_s' and 'demand_W' schedules",
                )
                continue
            require_list(times, label, f"cases[{index}].times_s", what="'times_s'")
            time_values = require_numbers(
                times, label, f"cases[{index}].times_s", what="'times_s' entries"
            )
            require_strictly_increasing(
                time_values, label, f"cases[{index}].times_s", what="UHX trip times"
            )
            require_list(demand, label, f"cases[{index}].demand_W", what="'demand_W'")
            demand_values = require_numbers(
                demand, label, f"cases[{index}].demand_W", what="'demand_W' entries"
            )
            for demand_index, value in enumerate(demand_values):
                if value < 0.0:
                    fail(
                        label,
                        f"cases[{index}].demand_W[{demand_index}]",
                        f"demand power must be non-negative, got {value}",
                    )
            if len(time_values) != len(demand_values):
                fail(
                    label,
                    f"cases[{index}].demand_W",
                    f"length {len(demand_values)} does not match"
                    f" len(cases[{index}].times_s) = {len(time_values)}",
                )
            dhrs = case.get("dhrs")
            if dhrs is not None:
                require_mapping(dhrs, label, f"cases[{index}].dhrs", "'dhrs'")
                for name in ("bleed_frac", "max_frac"):
                    value = dhrs.get(name)
                    if value is not None:
                        require_number(
                            value, label, f"cases[{index}].dhrs.{name}", minimum=0.0, maximum=1.0
                        )
                time_s = dhrs.get("time_s")
                if time_s is not None:
                    require_number(time_s, label, f"cases[{index}].dhrs.time_s", exclusive_minimum=0.0)


def _check_flow_fraction(case: Mapping[str, Any], label: str, index: int) -> None:
    flow = case.get("flow")
    if flow is None:
        fail(label, f"cases[{index}].flow", "cases need a 'flow' fraction")
        return
    value = require_number(flow, label, f"cases[{index}].flow")
    if not 0.0 < value <= 1.0:
        fail(
            label,
            f"cases[{index}].flow",
            f"flow fraction must be within (0, 1], got {value}",
        )


def _check_seconds_field(case: Mapping[str, Any], name: str, label: str, path: str) -> None:
    value = case.get(name)
    if value is not None:
        require_number(value, label, f"{path}.{name}", minimum=0.0)


def list_scenarios(*, root: Path | None = None, plant: str | None = None) -> list[dict[str, str]]:
    base = scenarios_root(root, plant=plant)
    if not base.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(base.rglob("*.yaml")):
        loaded = _load_yaml(path)
        rows.append(
            {
                "id": str(loaded.get("id") or path.stem),
                "kind": str(loaded.get("kind") or ""),
                "path": str(path),
            }
        )
    return rows


def _repo_label(path: Path, root: Path | None = None) -> str:
    base = (root or repo_root()).resolve()
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return str(path)


def scenario_source_entry(scenario: Mapping[str, Any], *, root: Path | None = None) -> dict[str, str]:
    """Fingerprint of the merged scenario (comments excluded) for run manifests."""

    raw_path = scenario.get("_path")
    if raw_path:
        label = _repo_label(Path(str(raw_path)), root)
    else:
        label = f"data/scenarios/{scenario.get('kind')}/{scenario.get('id')}"
    digest = str(scenario.get("_fingerprint") or fingerprint(_physics_dict(scenario)))
    return {label: digest}


def merge_manifest_sources(
    base: Mapping[str, str] | Sequence[str] | None,
    extra: Mapping[str, str],
) -> dict[str, str] | list[str]:
    """Attach scenario (and optional wrapper) SHAs onto a plant-source map.

    A bare path list is left unchanged when ``extra`` is empty so hermetic
    tests keep their historical list-shaped ``source_files``. When extra
    fingerprints are present the list is promoted to a SHA mapping.
    """

    if isinstance(base, Mapping):
        out = dict(base)
        out.update(extra)
        return out
    if base is None:
        return dict(extra)
    if not extra:
        return list(base)
    out = {str(path): file_digest(path) for path in base}
    out.update(extra)
    return out


def wrapper_source_entry(path: str | Path, *, root: Path | None = None) -> dict[str, str]:
    file_path = Path(path)
    return {_repo_label(file_path, root): file_digest(file_path)}


def legacy_vehicle(scenario: Mapping[str, Any], core_model: str) -> str | None:
    support = (scenario.get("package_support") or {}).get("legacy") or {}
    vehicles = support.get("vehicle_by_core") or {}
    return vehicles.get(yaml_core_key(core_model))


def needs_generated_wrapper(scenario: Mapping[str, Any], package: str) -> bool:
    support = (scenario.get("package_support") or {}).get(package) or {}
    if support.get("needs_wrapper"):
        return True
    return False


def resolve_source_amplitudes(forcing: Mapping[str, Any]) -> list[float]:
    """Replace YAML ``S`` placeholders with ``source.strength``."""

    source = forcing.get("source") or {}
    strength = None
    if "strength" in source:
        strength = float(quantity_value(source["strength"]))
    raw = source.get("amplitudes_n_s")
    if raw is None:
        raw = source.get("amplitudes") or []
    out: list[float] = []
    for item in raw:
        if item in ("S", "strength"):
            if strength is None:
                raise ValueError("source amplitude 'S' requires source.strength")
            out.append(strength)
        else:
            out.append(float(item))
    return out


def resolve_uhx_demand(
    forcing: Mapping[str, Any] | None = None,
    case: Mapping[str, Any] | None = None,
) -> tuple[list[float], list[float]]:
    """Return ``(times_s, amplitudes_W)`` for a UHX demand stepper.

    Prefers an explicit ``times_s`` / ``demand_W`` pair (UHX-trip case) and
    otherwise builds the 1 + len(frac) ramp used by the to-power startups.
    """

    node: Mapping[str, Any]
    if case is not None and ("demand_W" in case or "frac" in case or "times_s" in case):
        node = case
    else:
        node = (forcing or {}).get("uhx_demand") or {}
    if "demand_W" in node and "times_s" in node:
        times = [float(item) for item in node["times_s"]]
        amps = [float(item) for item in node["demand_W"]]
        if len(times) != len(amps):
            raise ValueError("uhx demand times_s and demand_W must be the same length")
        return times, amps
    if "frac" in node:
        frac = [float(item) for item in node["frac"]]
        final = float(node["final_W"])
        start = float(node["ramp_start_s"])
        delta = float(node["delta_s"])
        times = [0.0] + [start + index * delta for index in range(len(frac))]
        amps = [0.0] + [final * item for item in frac]
        return times, amps
    if "times_s" in node and "amplitudes_W" in node:
        return (
            [float(item) for item in node["times_s"]],
            [float(item) for item in node["amplitudes_W"]],
        )
    raise ValueError("no UHX demand schedule in scenario forcing/case")


def startup_cli_table(
    *, root: Path | None = None, plant: str | None = None, all_plants: bool = False
) -> dict[str, dict[str, float | int]]:
    """``SCENARIOS``-shaped table: id -> stop_time / number_of_intervals.

    ``all_plants=True`` merges every plant's startup decks (the runner's
    ``--scenario`` choices); an id missing from the selected ``--plant``
    tree is refused when the scenario is resolved.
    """

    if all_plants:
        merged: dict[str, dict[str, float | int]] = {}
        for plant_id in [DEFAULT_PLANT_ID] + [p for p in available_plants(root) if p != DEFAULT_PLANT_ID]:
            for key, value in startup_cli_table(root=root, plant=plant_id).items():
                merged.setdefault(key, value)
        return merged
    out: dict[str, dict[str, float | int]] = {}
    for row in list_scenarios(root=root, plant=plant):
        if row["kind"] != "startup":
            continue
        loaded = load_scenario(row["id"], kind="startup", root=root, plant=plant)
        if loaded["id"] == SEGMENTED_STARTUP_SCENARIO_ID:
            continue
        numerics = loaded.get("numerics") or {}
        out[str(loaded["id"])] = {
            "stop_time": float(numerics["stop_time_s"]),
            "number_of_intervals": int(numerics["number_of_intervals"]),
        }
    return out


def startup_legacy_vehicles(*, root: Path | None = None) -> dict[str, dict[str, str]]:
    """``CORE_MODEL_TO_SCENARIO_MODEL``-shaped map from YAML package_support."""

    out: dict[str, dict[str, str]] = {}
    for row in list_scenarios(root=root):
        if row["kind"] != "startup":
            continue
        loaded = load_scenario(row["id"], kind="startup", root=root)
        if loaded["id"] == SEGMENTED_STARTUP_SCENARIO_ID:
            continue
        support = (loaded.get("package_support") or {}).get("legacy") or {}
        vehicles = support.get("vehicle_by_core") or {}
        mapped = {cli_core_key(key): str(value) for key, value in vehicles.items()}
        if mapped:
            out[str(loaded["id"])] = mapped
    return out


def startup_numerics(scenario: Mapping[str, Any]) -> dict[str, float | int]:
    numerics = scenario.get("numerics") or {}
    return {
        "stop_time": float(numerics["stop_time_s"]),
        "number_of_intervals": int(numerics["number_of_intervals"]),
    }


def segmented_startup_constants(*, root: Path | None = None) -> dict[str, float]:
    """Simplified segmented ``--scenario startup`` amplitudes from YAML."""

    loaded = load_scenario(SEGMENTED_STARTUP_SCENARIO_ID, kind="startup", root=root)
    forcing = loaded.get("forcing") or {}
    source_amps = resolve_source_amplitudes(forcing)
    rho = [float(item) for item in (forcing.get("reactivity_pcm") or {}).get("amplitudes") or []]
    return {
        "source_amplitude_n_s": float(source_amps[0]) if source_amps else 1.0e8,
        "hold_reactivity_pcm": float(rho[0]) if rho else -3500.0,
        "recover_reactivity_pcm": float(rho[1]) if len(rho) > 1 else 0.0,
    }


def transients_case_tables(
    scenario: Mapping[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """STEP_PCM / FLOW_CASES / UHX-trip table from ``results_ii`` (or overlay)."""

    data = scenario or load_scenario(DEFAULT_TRANSIENTS_SCENARIO, kind="transients", root=root)
    step_pcm: dict[str, float] = {}
    flow_cases: dict[str, float] = {}
    flow_follow: dict[str, bool] = {}
    # Dollar labels of step/flow insertions (TASK-20261002-01 P2): runners
    # apply them at each core's own 1 $ (helpers.circulation_dollar); the
    # case pcm is the reference-core amplitude.
    step_dollars: dict[str, float] = {}
    uhx: dict[str, Any] | None = None
    for row in data.get("cases") or []:
        family = row.get("family")
        case_id = str(row["id"])
        if family in ("step", "flow") and row.get("dollars") is not None:
            step_dollars[case_id] = float(row["dollars"])
        if family == "step":
            step_pcm[case_id] = float(row["pcm"])
        elif family == "flow":
            flow_cases[case_id] = float(row["flow"])
            flow_follow[case_id] = bool(row.get("uhx_demand_follows_flow", False))
        elif family == "uhx_trip":
            uhx = dict(row)
    numerics = data.get("numerics") or {}
    dollar = data.get("dollar_pcm")
    return {
        "scenario": data,
        "step_pcm": step_pcm,
        "flow_cases": flow_cases,
        # Per flow case: True when the UHX demand follows the pump to
        # flow x P at the trip (segmented follow vehicles); absent = False.
        "flow_follow": flow_follow,
        "step_dollars": step_dollars,
        "uhx_trip": uhx,
        "uhx_trip_case": str((uhx or {}).get("id") or "uhx_trip"),
        "dollar_pcm": float(quantity_value(dollar)) if dollar is not None else 604.887,
        "stop_time_s": float(numerics.get("stop_time_s", 10000.0)),
        "uhx_stop_time_s": float(numerics.get("uhx_stop_time_s", 18400.0)),
        "number_of_intervals": int(numerics.get("number_of_intervals", 10000)),
        "tolerance": float(numerics.get("tolerance", 1.0e-6)),
        "max_step_size_s": float(numerics.get("max_step_size_s", 0.02)),
        # Output interval of the legacy step/flow cases (0 = the uniform
        # number_of_intervals grid with full output).
        "step_output_interval_s": float(numerics.get("step_output_interval_s", 0.0)),
        "method": str(numerics.get("method", "dassl")),
        "segmented_trip_min_stop_time_s": float(
            numerics.get("segmented_trip_min_stop_time_s", 4500.0)
        ),
        "segmented_flow_min_stop_time_s": float(
            numerics.get("segmented_flow_min_stop_time_s", 4400.0)
        ),
    }


def transients_step_pcm(*, root: Path | None = None) -> dict[str, float]:
    return transients_case_tables(root=root)["step_pcm"]


def transients_flow_cases(*, root: Path | None = None) -> dict[str, float]:
    return transients_case_tables(root=root)["flow_cases"]


def frequency_arg_defaults(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Map a frequency scenario onto ``runFreqNominalParallel`` argparse fields."""

    grid = scenario.get("grid") or {}
    forcing = scenario.get("forcing") or {}
    numerics = scenario.get("numerics") or {}
    low = scenario.get("low_power_protocol") or {}
    out: dict[str, Any] = {}
    if "omega" in grid:
        omegas = [float(item) for item in grid["omega"]]
        out["freq_min"] = omegas[0]
        out["freq_max"] = omegas[-1]
        out["num_freq"] = len(omegas)
    if "omega_min" in grid:
        out["freq_min"] = float(grid["omega_min"])
    if "omega_max" in grid:
        out["freq_max"] = float(grid["omega_max"])
    if "n" in grid:
        out["num_freq"] = int(grid["n"])
    if "ss_time_s" in forcing:
        out["ss_time"] = float(forcing["ss_time_s"])
    if "sin_mag_pcm" in forcing:
        out["sin_mag"] = float(forcing["sin_mag_pcm"])
    if "sin_mag_mode" in forcing:
        out["sin_mag_auto"] = str(forcing["sin_mag_mode"]).lower() == "auto"
    if "sin_mag_ref_mw" in forcing:
        out["sin_mag_ref"] = float(forcing["sin_mag_ref_mw"])
    if "sin_mag_min_pcm" in forcing:
        out["sin_mag_min"] = float(forcing["sin_mag_min_pcm"])
    if "sin_mag_max_pcm" in forcing:
        out["sin_mag_max"] = float(forcing["sin_mag_max_pcm"])
    if "stop_time_s" in numerics:
        out["stop_time"] = float(numerics["stop_time_s"])
    if "stop_time_mode" in numerics:
        out["stop_time_mode"] = str(numerics["stop_time_mode"])
    if "min_cycles_after_ss" in numerics:
        out["min_cycles_after_ss"] = float(numerics["min_cycles_after_ss"])
    if "output_interval_mode" in numerics:
        out["output_interval_mode"] = str(numerics["output_interval_mode"])
    if "output_intervals_per_second" in numerics:
        out["output_intervals_per_second"] = float(numerics["output_intervals_per_second"])
    if "threshold_mw" in low:
        out["low_power_threshold"] = float(low["threshold_mw"])
    if "ss_factor" in low:
        out["low_power_auto_ss_time_factor"] = float(low["ss_factor"])
    if "min_cycles_after_ss" in low:
        out["low_power_auto_min_cycles_after_ss"] = float(low["min_cycles_after_ss"])
    return out


def explicit_option_names(argv: Sequence[str] | None) -> set[str]:
    """Return the ``--flag`` tokens present on a CLI argv list."""

    names: set[str] = set()
    tokens = list(argv) if argv is not None else list(sys.argv[1:])
    for token in tokens:
        if token.startswith("--"):
            names.add(token.split("=", 1)[0])
    return names


def apply_frequency_scenario_args(
    args: Any,
    scenario: Mapping[str, Any],
    argv: Sequence[str] | None,
) -> None:
    """Fill argparse fields from YAML when the matching flag was not passed."""

    defaults = frequency_arg_defaults(scenario)
    explicit = explicit_option_names(argv)
    for flag, attr in _FREQ_FLAG_ATTRS:
        if flag in explicit or attr not in defaults:
            continue
        setattr(args, attr, defaults[attr])


__all__ = [
    "CLI_TO_YAML_CORE",
    "CORE_CHOICES",
    "DEFAULT_FREQUENCY_SCENARIO",
    "DEFAULT_STARTUP_SCENARIO",
    "DEFAULT_TRANSIENTS_SCENARIO",
    "MAX_EXTENDS_DEPTH",
    "PAPER_FREQUENCY_SCENARIO",
    "RADIAL_DEMO_MATURITY",
    "RADIAL_DEMO_VEHICLE_PREFIX",
    "SEGMENTED_ONLY_CORES",
    "SEGMENTED_ONLY_CORE_CLAUSES",
    "SEGMENTED_ONLY_CORE_TRIP_CLAUSES",
    "SEGMENTED_STARTUP_SCENARIO_ID",
    "YAML_TO_CLI_CORE",
    "apply_frequency_scenario_args",
    "cli_core_key",
    "explicit_option_names",
    "frequency_arg_defaults",
    "legacy_core_refusal",
    "legacy_core_refusal_message",
    "legacy_vehicle",
    "list_scenarios",
    "load_scenario",
    "load_scenario_path",
    "merge_manifest_sources",
    "needs_generated_wrapper",
    "poison_dataset_refusal",
    "poison_full_power_refusal",
    "radial_demo_manifest_record",
    "radial_demo_vehicle",
    "resolve_scenario",
    "resolve_source_amplitudes",
    "resolve_uhx_demand",
    "scenario_core_refusal",
    "scenario_source_entry",
    "scenarios_root",
    "segmented_only_core_clause",
    "segmented_startup_constants",
    "startup_cli_table",
    "startup_legacy_vehicles",
    "startup_numerics",
    "transients_case_tables",
    "transients_flow_cases",
    "transients_step_pcm",
    "wrapper_source_entry",
    "yaml_core_key",
]
