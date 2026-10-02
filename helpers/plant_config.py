"""Load, merge, and validate MSRR plant YAML decks.

YAML under ``data/plants/<id>/`` is the authored source of truth. This
module never writes YAML back (comments would be stripped). Modelica
bindings are produced by :mod:`helpers.emit_modelica_plant`. The data
tree is located by :mod:`helpers.data_resources` (source checkout or the
packaged ``msrr_data`` wheel payload).

Validation is full-depth: ``load_plant`` checks the raw deck (identity
plus every included file) and the merged result (identity, quantity
envelopes, supported units, finite/physical values, coupled array
lengths, 9R zone bounds, required material properties, and the
structural sections the emitter binds).
The shared primitives live in :mod:`helpers.data_validation`; the JSON
Schemas in ``data/schema/`` document the same contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from helpers.data_resources import (
    resolve_data_root,
    resolve_repo_root,
)
from helpers.data_validation import (
    check_quantity,
    check_quantity_tree,
    coerce_number,
    fail,
    require_mapping,
)

LUMPED_LIBRARY_FILE = "SMD_MSR_Modelica.mo"
LUMPED_MODEL_FILE = "MSRR.mo"
LUMPED_PLANT_DATA_FILE = "MSRR_PlantData.mo"
SEGMENTED_PACKAGE_FILE = "SegmentedMSR.mo"
SEGMENTED_PLANT_DATA_FILE = "SegmentedMSR_PlantData.mo"


def repo_root(start: Path | None = None) -> Path:
    """Return the directory containing ``core/`` and ``helpers/``.

    Source checkout / editable install: discovered by walking upward for
    ``pyproject.toml`` + ``core/`` + ``helpers/`` (historical behavior).
    Wheel install: the parent of the installed ``core`` package (see
    :mod:`helpers.data_resources`).
    """

    return resolve_repo_root(start)


def data_root(root: Path | None = None) -> Path:
    """Return the plant/scenario/schema data directory.

    Explicit ``root`` override maps to ``<root>/data`` (runners and tests
    with synthetic trees). Otherwise the source checkout's ``data/`` is
    used, falling back to the wheel's packaged ``msrr_data`` payload;
    unsupported install modes fail with an actionable
    :class:`~helpers.data_resources.DataUnavailableError`.
    """

    return resolve_data_root(root)


def plants_root(root: Path | None = None) -> Path:
    return data_root(root) / "plants"


def is_quantity(obj: Any) -> bool:
    return isinstance(obj, dict) and "value" in obj and "unit" in obj


def quantity_value(obj: Any) -> Any:
    if is_quantity(obj):
        return coerce_number(obj["value"])
    return obj


def quantity_unit(obj: Mapping[str, Any]) -> str:
    if not is_quantity(obj):
        raise TypeError("not a quantity object")
    return str(obj["unit"])


def canonical_json(value: Any) -> str:
    """Deterministic JSON for SHA-256 (comments already gone after YAML parse).

    ``allow_nan=False`` keeps a NaN/Inf value from silently producing a
    non-standard JSON token (``NaN``/``Infinity``) that would break
    round-tripping and cross-tool hash comparison; it raises instead.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        raise ValueError(f"empty YAML file: {path}")
    return loaded


def _resolve_includes(spec: Any, base_dir: Path, root: Path | None = None) -> Any:
    if isinstance(spec, str):
        base_dir_resolved = base_dir.resolve()
        path = (base_dir / spec).resolve()
        if not path.is_relative_to(base_dir_resolved):
            raise ValueError(
                f"include {spec!r} resolves outside the plant directory: {path}"
            )
        if not path.is_file():
            raise FileNotFoundError(path)
        loaded = _load_yaml(path)
        if not isinstance(loaded, dict):
            raise TypeError(f"{path} must contain a mapping")
        check_quantity_tree(loaded, _display_repo_relative(path, root))
        return loaded
    if isinstance(spec, dict):
        return {key: _resolve_includes(value, base_dir, root) for key, value in spec.items()}
    raise TypeError(f"include spec must be a path or mapping, got {type(spec).__name__}")


def load_plant(plant_id: str = "msrr", *, root: Path | None = None) -> dict[str, Any]:
    """Load ``data/plants/<id>/plant.yaml``, resolve ``includes``, and validate.

    Validation runs on the raw deck (identity fields plus every included
    file) and on the merged result (full nested requirements).
    """

    plant_dir = plants_root(root) / plant_id
    plant_path = plant_dir / "plant.yaml"
    if not plant_path.is_file():
        raise FileNotFoundError(plant_path)
    label = _display_repo_relative(plant_path, root)
    raw = _load_yaml(plant_path)
    if not isinstance(raw, dict):
        raise TypeError(f"{plant_path} must contain a mapping")
    _validate_plant_identity(raw, label)
    includes = raw.get("includes")
    if not isinstance(includes, dict):
        fail(
            label,
            "includes",
            f"must be a mapping of section name -> include path, got {type(includes).__name__}",
        )
    merged = dict(raw)
    resolved = _resolve_includes(includes, plant_dir, root)
    if not isinstance(resolved, dict):
        raise TypeError("resolved includes must be a mapping")
    merged.update(resolved)
    del merged["includes"]
    validate_plant(merged, label=label + " (merged)")
    return merged


def _validate_plant_identity(plant: Mapping[str, Any], label: str) -> None:
    if plant.get("schema_version") != 1:
        fail(
            label,
            "schema_version",
            f"unsupported plant schema_version {plant.get('schema_version')!r} (expected 1)",
        )
    if plant.get("kind") != "plant":
        fail(label, "kind", f"plant YAML kind must be 'plant', got {plant.get('kind')!r}")
    plant_id = plant.get("id")
    if not isinstance(plant_id, str) or not plant_id.strip():
        fail(label, "id", f"plant YAML needs a non-empty string id, got {plant_id!r}")
    # plant_id is embedded into generated Modelica names, file paths, and
    # campaign identifiers; reject anything outside the safe charset so a
    # deck cannot smuggle path separators, glob metacharacters, or leading
    # punctuation into those derived names.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", plant_id):
        fail(
            label,
            "id",
            f"plant id must match [A-Za-z0-9][A-Za-z0-9_.-]*, got {plant_id!r}",
        )
    display_name = plant.get("display_name")
    if display_name is not None and (
        not isinstance(display_name, str) or not display_name.strip()
    ):
        fail(label, "display_name", f"must be a non-empty string, got {display_name!r}")
    includes = plant.get("includes")
    if includes is None:
        return
    require_mapping(includes, label, "includes", "'includes'")
    for key, spec in includes.items():
        _check_include_spec(spec, label, f"includes.{key}")


def _check_include_spec(spec: Any, label: str, path: str) -> None:
    if isinstance(spec, str):
        if not spec.strip():
            fail(label, path, "include path must be a non-empty string")
        return
    if isinstance(spec, dict):
        for key, value in spec.items():
            _check_include_spec(value, label, f"{path}.{key}")
        return
    fail(label, path, f"include spec must be a path string or mapping, got {type(spec).__name__}")


_PLANT_SECTIONS = (
    "kinetics",
    "decay_heat",
    "materials",
    "primary_loop",
    "secondary_loop",
    "pumps",
    "poisons",
)
_PROVENANCE_SECTIONS = ("kinetics", "decay_heat", "materials", "poisons")
_POISON_YIELD_CONVENTIONS = ("independent", "chain_entry")
#: Per-isotope yield bases (poisons.yield_basis). A cumulative chain-entry
#: yield folds unmodeled precursors into a chain's FIRST modeled member; on a
#: member with a modeled parent it would count that parent's production twice.
POISON_YIELD_ISOTOPES = ("Te135", "I135", "Xe135", "Pm149", "Sm149")
POISON_CHAIN_HEADS = ("Te135", "Pm149")
_POISON_YIELD_BASES = ("independent", "cumulative_chain_entry")
_POISON_FLUX_AVERAGING = ("core_volume", "total_fuel")
_POISON_INVENTORY_UNITS = ("atoms",)
_POISON_MICROSCOPIC_XS_UNIT = "b"
_POISON_FLUX_UNIT = "1/(cm2.s)"
_POISON_POSITIVE_KEYS = (
    "Te135_lambda",
    "I135_lambda",
    "Xe135_lambda",
    "Pm149_lambda",
    "sigma_a_Xe",
    "sigma_a_Sm",
    "sigma_a_fuel",
    "phi0",
    "energy_per_fission",
)
_POISON_NONNEGATIVE_KEYS = (
    "Te135_yield",
    "I135_yield",
    "Xe135_yield",
    "Pm149_yield",
    "Sm149_yield",
    "k_rem_Xe",
    "k_rem_Sm",
)
_MATERIAL_NAMES = ("fuel", "graphite", "coolant", "hx_tube")

# ---------------------------------------------------------------------------
# Optional intra-channel radial stack (TASK-20260914-01 P1; plan §2.2, §6).
#
# Three independent structural decisions, translation-time only:
#   1. whether the intra-channel radial stack exists (``enabled``);
#   2. whether the annular fluid circulates (``annular_fluid.mode``);
#   3. whether a dedicated annular-loop heat exchanger exists
#      (``annular_loop.heat_exchanger.enabled``).
# Valid combinations only (plan §2.2):
#   Disabled            -> mode N/A, HX refused
#   Enabled + Static    -> HX refused
#   Enabled + Circulating
#   Enabled + Circulating + HX
# ``annularHeatExchangerEnabled=true`` is refused unless radial segmentation
# AND annular circulation are both enabled. The Modelica twin of this table
# lives on ``SegmentedMSR.Core.SegmentedCore`` (asserts), so a direct
# -override bypass is also refused. The flag is authored per core under
# ``intra_channel_radial`` (NEVER on ``ChannelMap``: that record stays
# geometry-only and its ``enableRadialMod`` is inter-channel moderator
# conduction - a different feature).
# ---------------------------------------------------------------------------

#: YAML key of the per-core radial block.
RADIAL_BLOCK_KEY = "intra_channel_radial"

#: Annular-fluid structural modes with the integer codes emitted into
#: ``SegmentedMSR_PlantData.IntraChannelRadial*`` and consumed by
#: ``SegmentedMSR.Core.IntraChannelRadialConfig.annularFluidModeCode``
#: (omc 1.27: structural switches ride integer codes).
ANNULAR_FLUID_MODES = ("static", "circulating")
ANNULAR_FLUID_MODE_CODES = {"static": 1, "circulating": 2}

#: Shared structural annular flow direction (plan §3.9: one direction for
#: all channels in the first implementation).
FLOW_DIRECTIONS = ("bottom_to_top", "top_to_bottom")
FLOW_DIRECTION_CODES = {"bottom_to_top": 1, "top_to_bottom": 2}

#: Radial geometry policy (plan §2.4): ``strict_physical`` requires the
#: declared thermal fuel volumes to agree with the geometric volumes from
#: the shared radii AND the physical-channel mapping to close (fuel-volume,
#: lattice-packing, and material-volume closure; review rev019 Phase 2,
#: TASK-20260915-01 P2); ``effective_thermal`` permits a reported
#: discrepancy.
RADIAL_GEOMETRY_POLICIES = ("strict_physical", "effective_thermal")
RADIAL_GEOMETRY_POLICY_CODES = {"strict_physical": 1, "effective_thermal": 2}

#: Physical-channel meaning (review rev019 Phase 2; TASK-20260915-01 P2):
#: what ONE modeled coarse thermal channel represents. ``literal_tube`` is
#: exactly one physical fuel tube per modeled channel
#: (``channel_multiplicity`` = 1); ``aggregate_bundle`` is a bundle of
#: ``channel_multiplicity`` identical parallel physical tubes per modeled
#: channel. The integer code mirrors the omc-1.27 house style
#: (``IntraChannelRadialConfig.channelMeaningCode``).
RADIAL_CHANNEL_MEANINGS = ("literal_tube", "aggregate_bundle")
RADIAL_CHANNEL_MEANING_CODES = {"literal_tube": 1, "aggregate_bundle": 2}

#: Lattice-geometry declaration for the ``strict_physical`` contract
#: (review rev020 Phase 4 option 2; TASK-20260916-01 P4): what the coarse
#: ``channel_map`` lattice metadata (``pitch`` and the derived cell area)
#: represents. ``physical`` = the deck authors a REAL tube lattice whose
#: geometry is physical closure data; ``placeholder`` = the metadata is a
#: numerical surrogate that does not control heat capacity (the shipped
#: decks' documented status). The declaration lives on
#: ``cores.<core>.channel_map.lattice_geometry`` -- next to the pitch it
#: qualifies. An ABSENT declaration canonicalizes to
#: :data:`RADIAL_LATTICE_GEOMETRY_DEFAULT` (``placeholder``), so shipped
#: cores cannot claim ``strict_physical``.
RADIAL_LATTICE_GEOMETRY_VALUES = ("placeholder", "physical")
RADIAL_LATTICE_GEOMETRY_DEFAULT = "placeholder"

#: Radial interface selection (plan §6.5): an explicit perfect-contact mode
#: instead of encoding contact as an arbitrarily large film coefficient.
RADIAL_INTERFACE_MODES = ("perfect", "film")
RADIAL_INTERFACE_MODE_CODES = {"perfect": 0, "film": 1}

#: Heat-exchanger model identity (review rev020 Phase 5 item 1,
#: TASK-20260916-01 P2): the ONLY implemented annular-loop heat-exchanger
#: model. The generated ``annularHeatExchangerModel`` string is an identity
#: label, never a selector -- the Modelica structural switch is the
#: ``annularHeatExchangerEnabled`` boolean (integer
#: ``annularHeatExchangerCode``). An enabled heat exchanger must name
#: exactly ``RADIAL_HX_MODEL_FINITE_CONDUCTANCE``; a disabled one carries
#: the canonical ``RADIAL_HX_MODEL_NONE`` (an absent field canonicalizes
#: to it). Any other value refuses fail-closed.
RADIAL_HX_MODEL_NONE = "none"
RADIAL_HX_MODEL_FINITE_CONDUCTANCE = "finite_conductance_prescribed_sink"

#: Annular channel-flow-fraction tolerance; mirrors
#: ``SegmentedMSR.Core.fracTol`` (core/SegmentedMSR.mo, fracTol = 1e-6).
RADIAL_FRACTION_TOLERANCE = 1e-6

#: Default relative tolerance of the ``strict_physical`` declared-vs-geometric
#: fuel-volume check (plan §2.4); overridable per block via
#: ``intra_channel_radial.volume_tolerance``.
RADIAL_VOLUME_TOLERANCE_DEFAULT = 1e-6

#: Optional shared material records backing the radial stack
#: (``shared/materials.yaml``). Present records are always validated
#: (rho/cp/k strictly positive, provenance enforced by the shared
#: materials pass); they are REQUIRED only for enabled radial decks.
_RADIAL_MATERIAL_NAMES = ("channel_pipe", "annular_fluid")

#: Emitter order of the per-core radial packages (mirrors the core-record
#: emission order in helpers.emit_modelica_plant).
_RADIAL_CORE_ORDER = ("r1", "r1_10seg", "r5x5_z10", "r9")

#: Core-maturity enums (TASK-20260908-01 P2, owner-settable; split into two
#: axes after the rev031 external review, which found the single
#: ``reference`` label read as "physically validated").
#:
#: Implementation maturity (``maturity``): ``reference_regression`` marks the
#: legacy computational reference the regression and parity checks are built
#: on (NOT a physically validated dataset); ``discretization_study`` marks a
#: uniform first-cut partition with no independent spatial-profile
#: validation; ``exploratory_geometry`` marks a placeholder lattice on a
#: surrogate graphite grade.
#:
#: Physical-data maturity (``physical_data_maturity``):
#: ``reviewed_design_basis`` = values traced to a reviewed external design
#: basis; ``legacy_inherited`` = values inherited from the former Modelica
#: implementation, not yet source-reviewed; ``pending_source_review`` = a
#: source is named but its review is open; ``synthetic`` = placeholder
#: values with no physical provenance.
#:
#: ``helpers/segmented_runs.py`` mirrors the committed values per CLI core
#: key for run manifests and plot labels.
CORE_MATURITY_VALUES = ("reference_regression", "discretization_study", "exploratory_geometry")
CORE_PHYSICAL_DATA_MATURITY_VALUES = (
    "reviewed_design_basis",
    "legacy_inherited",
    "pending_source_review",
    "synthetic",
)

#: Poison-data maturity enum (TASK-20260912-01 P5): every poison dataset
#: carries a machine-readable ``maturity`` label, validated at plant-load
#: time. ``reference`` marks data that passed the owner's scientific review
#: of pedigree, spectrum, and the reduced-order worth coefficient;
#: ``reduced_order_pending_review`` marks the current reduced-order first-cut
#: dataset, which is NOT approved for production or publication use. The
#: generated ``SegmentedMSR_PlantData.Poisons`` constant mirrors the value
#: beside the dataset identity.
POISON_MATURITY_VALUES = ("reference", "reduced_order_pending_review")

#: Poison-maturity subset accepted for production/publication use WITHOUT the
#: explicit ``--allow-unreviewed-poison-data`` development override
#: (TASK-20260912-01 P5). Only an owner-reviewed data-change record may move
#: a dataset into this set; roles must not promote maturity.
POISON_MATURITY_APPROVED = ("reference",)
_R9_REGION_ARRAYS = (
    "vol_F1",
    "vol_F2",
    "vol_G",
    "hA",
    "kFN1",
    "kFN2",
    "kHT1",
    "kHT2",
    "IF1",
    "IF2",
    "IG",
    "LF1",
    "LF2",
    "ArF1",
    "ArF2",
)
_R9_ZONE_ARRAYS = (
    "flow_frac_zones",
    "Ac_zones",
    "region_trip_time",
    "zone_start",
    "zone_end",
    "n_seg_zone",
)
_PRIMARY_VOLUME_ORDER = ("pipeCoreToDHRS", "DHRS", "pipeDHRStoHX", "HXprimary", "pipeHXtoCore")
_PIPE_GEOMETRY_KEYS = ("volFracNode", "Ac", "L", "Ar", "e", "Tinf", "T_0")
_NODE_GEOMETRY_KEYS = ("Ac", "L", "Ar", "e", "Tinf", "T_0")

# ---------------------------------------------------------------------------
# Optional outer-core fuel annulus / reactor vessel / thermostated cavity
# (TASK-20260917-01 P7; plan §7).
#
# ONE plant-level authored block: ``data/plants/msrr/shared/core_vessel.yaml``,
# wired into ``plant.yaml`` ``includes:`` under the pinned key
# ``outer_fuel_annulus``. It is a sibling DATASET of the intra-channel radial
# stack with a separate identity - separate YAML key, separate generated
# packages (``SegmentedMSR_PlantData.OuterFuelAnnulus*``), separate
# fingerprints - and shares no names, keys, commands, or fingerprints with
# the radial stack or its dedicated loop (card Constraints). The annulus
# carries primary fuel and follows the primary pump; the validator enforces
# the plan §7.2 rules fail-closed, every refusal naming the configuration
# path and the offending value.
# ---------------------------------------------------------------------------

#: YAML key of the plant-level outer-annulus block (card Constraints name pin).
OUTER_ANNULUS_BLOCK_KEY = "outer_fuel_annulus"

#: Geometry policies (plan §3.4): the same two meanings as the radial stack's
#: policies, but a separate field and identity for this feature.
OUTER_ANNULUS_GEOMETRY_POLICIES = ("strict_physical", "effective_thermal")
OUTER_ANNULUS_GEOMETRY_POLICY_CODES = {"strict_physical": 1, "effective_thermal": 2}

#: Series-flow topology codes (plan §6.1; planner resolution 7): 1 = outer
#: annulus disabled (established core inlet/outlet behavior), 2 = active core
#: followed by the outer annulus, 3 = outer annulus followed by the active
#: core. The disabled generated record always carries code 1.
OUTER_ANNULUS_SERIES_CODES = (1, 2, 3)
OUTER_ANNULUS_DISABLED_SERIES_CODE = 1

#: Annulus axial direction codes (plan §6.1): 1 = segment 1 -> nSeg,
#: 2 = segment nSeg -> 1.
OUTER_ANNULUS_DIRECTION_CODES = (1, 2)

#: Graphite -> annulus interface parameterizations (the Modelica
#: ``graphiteAnnulusInterfaceCode`` switch): 0 = the authored G_modAnn
#: matrix IS the conductance (the P1 mapping policy), 1 = a film coefficient.
OUTER_ANNULUS_GRAPHITE_INTERFACE_MODES = ("authored_matrix", "film")
OUTER_ANNULUS_GRAPHITE_INTERFACE_CODES = {"authored_matrix": 0, "film": 1}

#: Annulus -> vessel contact modes (the Modelica
#: ``annulusVesselInterfaceCode`` switch): 0 = perfect thermal contact,
#: 1 = film coefficient.
OUTER_ANNULUS_CONTACT_MODES = ("perfect", "film")
OUTER_ANNULUS_CONTACT_MODE_CODES = {"perfect": 0, "film": 1}

#: Vessel -> cavity transfer modes (plan §5.7; the Modelica
#: ``cavityModeCode`` switch): 1 = linear UA only, 2 = radiation only,
#: 3 = linear + radiation.
OUTER_ANNULUS_CAVITY_MODES = ("ua", "radiation", "ua_and_radiation")
OUTER_ANNULUS_CAVITY_MODE_CODES = {"ua": 1, "radiation": 2, "ua_and_radiation": 3}

#: Cavity-exposed EXTERNAL primary-loop surfaces - the Python mirror of
#: ``SegmentedMSR.Core.CavityLoopSurface`` (plan §9.2). Unknown names and
#: duplicate assignments refuse. Secondary-loop surfaces are deliberately
#: absent (plan §4.7: they are not cavity surfaces unless a reviewed layout
#: says so).
OUTER_ANNULUS_CAVITY_LOOP_SURFACES = {
    "pipeCoreToDHRS": 1,
    "dhrs": 2,
    "pipeDHRStoHX": 3,
    "pipeHXtoCore": 4,
    "heatExchanger": 5,
}

#: Cores that may carry an ENABLED outer-annulus dataset (plan §3.1). 9R
#: refuses production activation until a physical zone-to-outer-boundary
#: mapping exists (card A12); a DISABLED 9R-targeted block stays accepted as
#: preparatory data (the radial ``IntraChannelRadial9R`` precedent).
OUTER_ANNULUS_SUPPORTED_CORES = ("r1", "r1_10seg", "r5x5_z10")

#: Inventory-allocation policies (plan §4.10; the P1 decision record):
#: ``split_existing`` carves the annulus out of the recorded legacy in-vessel
#: term and preserves the total; ``revise_total`` requires a sourced revised
#: total and every dependent update.
OUTER_ANNULUS_INVENTORY_POLICIES = ("split_existing", "revise_total")

#: Production-enablement statuses (the P1 decision record's governance
#: field). An ``enabled: true`` deck cannot carry ``status: blocked``.
OUTER_ANNULUS_ENABLEMENT_STATUSES = ("blocked", "enabled")

#: Default relative tolerance of the geometry/volume closure checks
#: (plan §7.2); overridable via ``outer_fuel_annulus.volume_tolerance``.
OUTER_ANNULUS_VOLUME_TOLERANCE_DEFAULT = 1e-6

#: Absolute tolerance of the inventory-closure checks [m3] (the P1 record's
#: documented in-repo warning tolerance; the committed tree closes at a
#: 4e-7 m3 residual: 0.4 + 0.1000004 vs 0.5).
OUTER_ANNULUS_INVENTORY_TOLERANCE = 1e-6

#: Annulus-fission coupling policies (plan §5.1; TASK-20260918-01 P1):
#: ``local_same_fraction`` is the ONLY first-release policy - elementwise
#: ``f^S_a,j = f^Q_a,j`` is a validation rule (tolerance-considered
#: equality), never a schema collapse: the two fraction names stay distinct
#: because fission-event location and energy deposition are not generally
#: identical physical quantities (planner resolutions 10/17). A second
#: coupling policy is a future owner decision; unknown names refuse.
OUTER_ANNULUS_FISSION_COUPLING_POLICIES = ("local_same_fraction",)
OUTER_ANNULUS_FISSION_COUPLING_POLICY_CODES = {"local_same_fraction": 1}

#: Annulus temperature-feedback policies (plan §5.6): ``zero_credit`` is the
#: ONLY first-release policy - an explicit, fingerprinted, manifest-visible
#: no-credit policy (annulus fission fuel temperature contributes no
#: reactivity feedback), never a silent assumption. Weighted policies are a
#: future owner decision; unknown names refuse.
OUTER_ANNULUS_FISSION_FEEDBACK_POLICIES = ("zero_credit",)
OUTER_ANNULUS_FISSION_FEEDBACK_POLICY_CODES = {"zero_credit": 1}

#: Annulus poison flux-exposure policies (plan §5.5): ``zero_credit`` is the
#: ONLY first-release policy - the homogeneous poison model keeps its
#: whole-reactor fission-rate production and credits no annulus absorption
#: exposure (the effective irradiated volume stays the active-core volume
#: until a reviewed weighted policy arrives). Weighted policies are a future
#: owner decision; unknown names refuse.
OUTER_ANNULUS_FISSION_EXPOSURE_POLICIES = ("zero_credit",)
OUTER_ANNULUS_FISSION_EXPOSURE_POLICY_CODES = {"zero_credit": 1}

#: Elementwise-equality tolerance of the two fission fraction vectors under
#: ``local_same_fraction`` [1] (absolute; plan §5.1/§5.2 "tolerance-
#: considered equality"). Fraction entries are O(1e-3..1e-1), so 1e-9 is a
#: tight float-noise bound - materially different vectors refuse.
OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE = 1e-9

#: Minimum retained active-core share of the whole-reactor fission split
#: [1] (absolute; plan §5.2; TASK-20260922-03 P1). A fission fraction
#: vector ``f`` is accepted only when ``1 - math.fsum(f) >=
#: OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE`` -- the annulus split
#: must leave the active core a strictly positive share bounded away
#: from zero by more than float-accumulation noise. ``math.fsum`` keeps
#: the summation error at ~1 ulp (~1e-16 for O(10)-entry O(0.1)
#: vectors), so 1e-9 sits orders of magnitude above the summation noise
#: on both the Python and the Modelica side while accepting every
#: physically meaningful split. Both Python fission validators (the
#: ``load_plant`` validator and the ``outer_annulus_config`` record
#: builder) enforce this ONE shared rule, and the Modelica
#: ``CoreVesselAssembly`` fission-check block mirrors it fail-closed
#: with the same documented constant
#: (``OuterFuelAnnulusConfig.minRetainedCoreShare``).
OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE = 1e-9

#: Axial-grid decision-record section tag per YAML core key (REV-478da49-01
#: fix). Canonical spelling of the P1 elevations section is
#: ``axial_grid_<core-tag>`` with the user-facing core tag --
#: ``r1_10seg`` -> ``1r10seg`` -- matching the repo's core-tag map
#: ``helpers.scenario_config.YAML_TO_CLI_CORE`` (mirrored here because
#: scenario_config imports this module, so the map cannot be imported at
#: module level). The shipped deck (``data/plants/msrr/shared/core_vessel.yaml``),
#: ``data/schema/plant.schema.json``, and ``data/CATALOG.md`` author
#: ``axial_grid_1r10seg``. The bare YAML core-key spelling
#: (``axial_grid_r1_10seg``) is accepted as an alias so a deck authored
#: either way still hits the axial-grid consistency check.
OUTER_ANNULUS_AXIAL_GRID_CORE_TAGS = {
    "r1": "1r",
    "r1_10seg": "1r10seg",
    "r5x5_z10": "r5x5_z10",
    "r9": "9r",
}

_OUTER_ANNULUS_TOP_KEYS = frozenset(
    {
        "enabled",
        "doc",
        "dataset_id",
        "maturity",
        "geometry_policy",
        "volume_tolerance",
        "production_enablement",
        "inventory_policy",
        "inventory",
        "topology",
        "first_production_target",
        "geometry",
        "moderator_coupling",
        "interfaces",
        "vessel_material",
        "fuel_conductivity",
        "cavity",
        "precursor_importance",
        "initialization",
        "fission",
    }
)
_OUTER_ANNULUS_GEOMETRY_KEYS = frozenset(
    {
        "graphite_outer_radius",
        "vessel_inner_radius",
        "vessel_outer_radius",
        "physical_length",
        "annulus_volume",
    }
)
_OUTER_ANNULUS_TOPOLOGY_KEYS = frozenset(
    {
        "series_location_code",
        "flow_direction_code",
        "enabled_series_location_code",
        "enabled_series_location_status",
        "flow_direction_status",
    }
)
_OUTER_ANNULUS_MODCOUPLING_KEYS = frozenset({"mapping_policy", "conductance"})
_OUTER_ANNULUS_INTERFACE_KEYS = frozenset({"graphite_to_fuel", "fuel_to_vessel"})
_OUTER_ANNULUS_VESSEL_KEYS = frozenset({"identity", "rho", "cp", "k"})
_OUTER_ANNULUS_CAVITY_KEYS = frozenset(
    {
        "enabled",
        "temperature",
        "mode",
        "vessel_UA",
        "vessel_area",
        "vessel_effective_emissivity",
        "loop_surfaces",
    }
)
_OUTER_ANNULUS_PRECURSOR_KEYS = frozenset({"policy", "weights"})
_OUTER_ANNULUS_INIT_KEYS = frozenset({"annulus_temperature", "vessel_temperature"})
_OUTER_ANNULUS_ENABLEMENT_KEYS = frozenset({"status", "reasons"})
_OUTER_ANNULUS_FISSION_KEYS = frozenset(
    {
        "enabled",
        "coupling_policy",
        "source_fraction",
        "heat_deposition_fraction",
        "annulus_temperature_feedback_policy",
        "annulus_temperature_feedback",
        "poison_flux_exposure_policy",
        "poison_flux_exposure",
    }
)
_OUTER_ANNULUS_FISSION_FEEDBACK_KEYS = frozenset({"weights", "coefficient_alpha"})
_OUTER_ANNULUS_FISSION_EXPOSURE_KEYS = frozenset({"weights"})
_OUTER_ANNULUS_INVENTORY_KEYS = frozenset(
    {
        "legacy_term",
        "subtraction",
        "total_retained",
        "total_revised",
        "dependents_when_enabled",
        "active_core_cell_volume",
    }
)

#: Keys the outer-annulus block must NEVER carry at any depth (card
#: Constraints forbidden-identity list; plan §7.2 "no overlap with the
#: intra-channel annular-fluid configuration namespace"). The top-level key
#: pin already excludes them; this scan gives a domain-specific refusal when
#: one appears nested.
_OUTER_ANNULUS_FORBIDDEN_KEYS = frozenset(
    {
        "intra_channel_radial",
        "annular_fluid",
        "annular_loop",
        "annFlowCmd",
        "annularFlowCommand",
        "radialFingerprint",
        "annularLoopFingerprint",
        "radial_fingerprint",
        "annular_loop_fingerprint",
    }
)


def validate_plant(plant: Mapping[str, Any], *, label: str = "plant YAML") -> None:
    """Full validation of a merged plant deck (see module docstring).

    Checks: identity block; quantity envelopes everywhere (envelope keys,
    supported units, finite values, physical domains, degC temperature
    convention); required ``doc``+``source`` provenance on the plant
    identity quantities and the shared physics sections (kinetics,
    decay_heat, materials); coupled array lengths against
    num_groups/num_regions/n_zones/n_chan/n_seg; required material
    sections (``fuel``, ``graphite``, ``coolant``, ``hx_tube``, each with
    ``rho`` and ``cp``); 9R zone bounds and flow fractions; the structural
    sections the emitter binds; the optional per-core
    ``intra_channel_radial`` block (TASK-20260914-01 P1): the three
    structural decisions and valid combinations of plan §2.2 plus the
    radial geometry/material/flow/distribution/plenum/heat-exchanger
    schemas and labeled refusals of plan §9; and the optional plant-level
    ``outer_fuel_annulus`` block (TASK-20260917-01 P7): the plan §7.2 rules
    for the outer-core fuel annulus / reactor vessel / fixed-cavity dataset
    (structural codes, exact array lengths, physical domains, ordered
    radii, provenance, inventory closure, and the supported-core refusal),
    all BEFORE any Modelica translation, every refusal naming the
    configuration path and the offending value.
    """

    _validate_plant_identity(plant, label)
    if "includes" in plant:
        fail(label, "includes", "raw include map must be resolved before validation (use load_plant)")
    for name in ("nominal_power", "total_fuel_vol"):
        node = plant.get(name)
        if node is None:
            fail(label, name, "missing required plant quantity")
        check_quantity(node, label, name, require_provenance=True)
    for name in _PLANT_SECTIONS:
        if name not in plant:
            fail(label, name, "missing required plant section")
        require_mapping(plant[name], label, name, f"plant section '{name}'")
    cores = plant.get("cores")
    if not isinstance(cores, Mapping):
        fail(label, "cores", f"'cores' must be a mapping, got {type(cores).__name__}")
    for core in ("r1", "r9"):
        if core not in cores:
            fail(label, f"cores.{core}", "missing required core section")
        require_mapping(cores[core], label, f"cores.{core}", f"core '{core}'")
    check_quantity_tree(plant, label)
    for name in _PROVENANCE_SECTIONS:
        check_quantity_tree(plant[name], label, name, require_provenance=True)
    _validate_kinetics_arrays(plant["kinetics"], label)
    _validate_decay_heat_arrays(plant["decay_heat"], label)
    _validate_materials(plant["materials"], label)
    _validate_r1(plant["cores"]["r1"], label)
    if "r1_10seg" in cores:
        require_mapping(cores["r1_10seg"], label, "cores.r1_10seg", "core 'r1_10seg'")
        _validate_r1_10seg(cores["r1_10seg"], label, r1=plant["cores"]["r1"])
    if "r5x5_z10" in cores:
        require_mapping(cores["r5x5_z10"], label, "cores.r5x5_z10", "core 'r5x5_z10'")
        _validate_r5x5_z10(
            cores["r5x5_z10"],
            label,
            r1_hanom=_r1_hanom_total(cores, label),
            r1_cell_vol=_r1_cell_vol_total(cores, label),
        )
    _validate_r9(plant["cores"]["r9"], label)
    _validate_pumps(plant["pumps"], label)
    _validate_primary_loop(plant["primary_loop"], label)
    _validate_secondary_loop(plant["secondary_loop"], label)
    _validate_poisons(plant, label)
    _validate_intra_channel_radial(plant, label)
    _validate_outer_fuel_annulus(plant, label)


def _required_quantity(section: Mapping[str, Any], key: str, label: str, path: str) -> Any:
    node = section.get(key)
    if node is None:
        fail(label, f"{path}.{key}", "missing required quantity")
    if not is_quantity(node):
        fail(
            label,
            f"{path}.{key}",
            f"must be a quantity {{value, unit}}, got {type(node).__name__}",
        )
    return node


def _required_section(section: Mapping[str, Any], key: str, label: str, path: str) -> Mapping[str, Any]:
    node = section.get(key)
    if node is None:
        fail(label, f"{path}.{key}", "missing required section")
    require_mapping(node, label, f"{path}.{key}", f"section '{path}.{key}'")
    return node


def _structural_int(quantity_node: Any, label: str, path: str) -> int:
    value = quantity_value(quantity_node)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (
        isinstance(value, float) and not value.is_integer()
    ):
        fail(label, f"{path}.value", f"must be an integer, got {value!r}")
    return int(value)


def _array_length(quantity_node: Any, label: str, path: str) -> int:
    value = quantity_value(quantity_node)
    if not isinstance(value, list):
        fail(label, f"{path}.value", f"must be an array, got {type(value).__name__}")
    return len(value)


def _validate_poisons(plant: Mapping[str, Any], label: str) -> None:
    """Validate the homogeneous poison dataset (no topology-sized arrays).

    TASK-20260912-01 P5: the governance label ``poisons.maturity`` is a
    required enum (:data:`POISON_MATURITY_VALUES`); an unknown or missing
    value is a named load-time deck error, the same contract the core
    decks carry under :data:`CORE_MATURITY_VALUES`.
    """

    poisons = plant.get("poisons")
    if poisons is None:
        fail(label, "poisons", "missing required plant section")
    require_mapping(poisons, label, "poisons", "plant section 'poisons'")
    dataset_id = poisons.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        fail(label, "poisons.dataset_id", "must be a non-empty string")
    maturity = poisons.get("maturity")
    if not isinstance(maturity, str) or maturity not in POISON_MATURITY_VALUES:
        fail(
            label,
            "poisons.maturity",
            f"must be one of {list(POISON_MATURITY_VALUES)}, got {maturity!r}",
        )
    convention = poisons.get("yield_convention")
    if convention not in _POISON_YIELD_CONVENTIONS:
        fail(
            label,
            "poisons.yield_convention",
            f"must be one of {sorted(_POISON_YIELD_CONVENTIONS)}, got {convention!r}",
        )
    _validate_poison_yield_basis(poisons, convention, label)
    averaging = poisons.get("flux_averaging")
    if averaging not in _POISON_FLUX_AVERAGING:
        fail(
            label,
            "poisons.flux_averaging",
            f"must be one of {sorted(_POISON_FLUX_AVERAGING)}, got {averaging!r}",
        )
    inventory = poisons.get("inventory_unit")
    if inventory not in _POISON_INVENTORY_UNITS:
        fail(
            label,
            "poisons.inventory_unit",
            f"must be one of {sorted(_POISON_INVENTORY_UNITS)}, got {inventory!r}",
        )
    for key in _POISON_POSITIVE_KEYS:
        node = _required_quantity(poisons, key, label, "poisons")
        value = quantity_value(node)
        if isinstance(value, list) or not isinstance(value, (int, float)) or float(value) <= 0:
            fail(label, f"poisons.{key}.value", f"must be a positive scalar, got {value!r}")
    for key in _POISON_NONNEGATIVE_KEYS:
        node = _required_quantity(poisons, key, label, "poisons")
        value = quantity_value(node)
        if isinstance(value, list) or not isinstance(value, (int, float)) or float(value) < 0:
            fail(
                label,
                f"poisons.{key}.value",
                f"must be a nonnegative scalar, got {value!r}",
            )
    for key in ("sigma_a_Xe", "sigma_a_Sm"):
        unit = quantity_unit(poisons[key])
        if unit != _POISON_MICROSCOPIC_XS_UNIT:
            fail(
                label,
                f"poisons.{key}.unit",
                f"must be {_POISON_MICROSCOPIC_XS_UNIT!r} (barns), got {unit!r}",
            )
    flux_unit = quantity_unit(poisons["phi0"])
    if flux_unit != _POISON_FLUX_UNIT:
        fail(
            label,
            "poisons.phi0.unit",
            f"must be {_POISON_FLUX_UNIT!r} (n/(cm2.s)), got {flux_unit!r}",
        )
    topology_keys = [
        key
        for key in poisons
        if "frac" in str(key).lower() and "poison" in str(key).lower()
    ]
    if topology_keys:
        fail(
            label,
            f"poisons.{topology_keys[0]}",
            "topology-sized poison source/worth arrays are not permitted "
            "under the homogeneous inventory model",
        )
    energy = float(quantity_value(poisons["energy_per_fission"]))
    power = float(quantity_value(plant["nominal_power"]))
    if energy <= 0 or power <= 0:
        fail(label, "poisons.energy_per_fission.value", "cannot derive a positive fission rate")
    chain_135 = (
        float(quantity_value(poisons["Te135_yield"]))
        + float(quantity_value(poisons["I135_yield"]))
        + float(quantity_value(poisons["Xe135_yield"]))
    )
    if chain_135 <= 0:
        fail(
            label,
            "poisons.Te135_yield.value",
            "Te+I+Xe independent yields must sum to a positive chain yield",
        )


def _validate_kinetics_arrays(kinetics: Mapping[str, Any], label: str) -> None:
    n_groups = _structural_int(
        _required_quantity(kinetics, "num_groups", label, "kinetics"), label, "kinetics.num_groups"
    )
    if n_groups != 6:
        fail(
            label,
            "kinetics.num_groups.value",
            f"must be 6 (the model has six delayed-neutron groups), got {n_groups}",
        )
    for name in ("lambda", "beta"):
        node = _required_quantity(kinetics, name, label, "kinetics")
        length = _array_length(node, label, f"kinetics.{name}")
        if length != n_groups:
            fail(
                label,
                f"kinetics.{name}.value",
                f"length {length} does not match kinetics.num_groups ({n_groups})",
            )
    for name in (
        "generation_time", "n_floor", "source_scale", "nu", "source_effectiveness",
        "lumped_tau_core", "lumped_tau_loop",
    ):
        _required_quantity(kinetics, name, label, "kinetics")
    nu = float(quantity_value(kinetics["nu"]))
    if not (nu > 1.0 and nu < 5.0):
        fail(label, "kinetics.nu.value", f"neutrons per fission must lie in (1, 5), got {nu}")
    eta_s = float(quantity_value(kinetics["source_effectiveness"]))
    if not (eta_s > 0.0 and eta_s < 10.0):
        fail(
            label,
            "kinetics.source_effectiveness.value",
            f"source effectiveness must lie in (0, 10), got {eta_s}",
        )
    feedback = _required_section(kinetics, "feedback", label, "kinetics")
    for name in ("a_F", "a_G"):
        _required_quantity(feedback, name, label, "kinetics.feedback")


def _validate_poison_yield_basis(poisons: Mapping[str, Any], convention: str, label: str) -> None:
    """``poisons.yield_basis``: the machine-readable per-isotope yield basis.

    Required under ``chain_entry`` (one entry per modeled isotope); optional
    under ``independent``, where every entry must then be ``independent``.
    ``cumulative_chain_entry`` is allowed only on a chain's first modeled
    member (:data:`POISON_CHAIN_HEADS`).
    """
    basis = poisons.get("yield_basis")
    if basis is None:
        if convention == "chain_entry":
            fail(label, "poisons.yield_basis", "required when yield_convention is chain_entry")
        return
    if not isinstance(basis, Mapping):
        fail(label, "poisons.yield_basis", "must be a mapping isotope -> basis")
    keys = sorted(str(k) for k in basis)
    if keys != sorted(POISON_YIELD_ISOTOPES):
        fail(label, "poisons.yield_basis", f"must name exactly {list(POISON_YIELD_ISOTOPES)}, got {keys}")
    for isotope in POISON_YIELD_ISOTOPES:
        value = basis[isotope]
        if value not in _POISON_YIELD_BASES:
            fail(label, f"poisons.yield_basis.{isotope}", f"must be one of {list(_POISON_YIELD_BASES)}, got {value!r}")
        if value == "cumulative_chain_entry" and isotope not in POISON_CHAIN_HEADS:
            fail(
                label,
                f"poisons.yield_basis.{isotope}",
                "cumulative_chain_entry is allowed only on a chain's first modeled member "
                f"{list(POISON_CHAIN_HEADS)}: {isotope} has a modeled parent whose decay already "
                "feeds it, so a cumulative yield would count that production twice",
            )
        if value != "independent" and convention == "independent":
            fail(
                label,
                f"poisons.yield_basis.{isotope}",
                "yield_convention independent requires every basis to be independent",
            )


def _validate_decay_heat_arrays(decay: Mapping[str, Any], label: str) -> None:
    n_groups = _structural_int(
        _required_quantity(decay, "num_groups", label, "decay_heat"), label, "decay_heat.num_groups"
    )
    if n_groups != 3:
        fail(
            label,
            "decay_heat.num_groups.value",
            f"must be 3 (three-group ANS-94 decay-heat fit), got {n_groups}",
        )
    for name in ("DHYG", "DHlamG"):
        node = _required_quantity(decay, name, label, "decay_heat")
        length = _array_length(node, label, f"decay_heat.{name}")
        if length != n_groups:
            fail(
                label,
                f"decay_heat.{name}.value",
                f"length {length} does not match decay_heat.num_groups ({n_groups})",
            )
    _required_quantity(decay, "nom_frac", label, "decay_heat")


def _validate_materials(materials: Mapping[str, Any], label: str) -> None:
    """Require the four named materials, each with ``rho`` and ``cp``.

    Mirrors ``data/schema/plant.schema.json``: ``required`` names
    fuel/graphite/coolant/hx_tube, ``propertyNames`` closes the set to
    those four plus the two optional radial-stack records, and every
    material carries ``rho`` and ``cp`` (``k`` stays optional for the four
    established records). The emitter binds these keys directly; without
    this check a hole surfaced later as a ``KeyError`` instead of a deck
    error.

    TASK-20260914-01 P1: the optional radial records ``channel_pipe`` and
    ``annular_fluid`` additionally REQUIRE ``k`` (both materials conduct in
    the radial stack) and, when present, must be strictly positive in all
    three properties (zero-conductivity conventions of fuel/coolant do not
    transfer to a conducting pipe/annulus). Presence alone is validated
    here; enabled radial decks additionally REQUIRE both records (see
    :func:`_validate_intra_channel_radial`).
    """

    known = _MATERIAL_NAMES + _RADIAL_MATERIAL_NAMES
    unknown = sorted(str(name) for name in materials if name not in known)
    if unknown:
        fail(
            label,
            "materials",
            f"unknown material name(s) {unknown}; valid names: {list(known)}",
        )
    for name in _MATERIAL_NAMES:
        node = materials.get(name)
        if node is None:
            fail(label, f"materials.{name}", "missing required material section")
            continue
        require_mapping(node, label, f"materials.{name}", f"material '{name}'")
        for key in ("rho", "cp"):
            _required_quantity(node, key, label, f"materials.{name}")
    for name in _RADIAL_MATERIAL_NAMES:
        node = materials.get(name)
        if node is None:
            continue
        require_mapping(node, label, f"materials.{name}", f"material '{name}'")
        for key in ("rho", "cp", "k"):
            _required_quantity(node, key, label, f"materials.{name}")
        for key in ("rho", "cp", "k"):
            value = quantity_value(node[key])
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                fail(
                    label,
                    f"materials.{name}.{key}.value",
                    f"must be strictly positive for a radial-stack material, got {value!r}",
                )


def _validate_maturity(core: Mapping[str, Any], label: str, base: str) -> None:
    """Require the machine-readable ``maturity`` label on a core deck.

    Every core deck carries the owner-settable enum value (see
    :data:`CORE_MATURITY_VALUES`); an unknown or missing value is a named
    load-time deck error. The label is mirrored into run manifests as
    ``core_maturity`` via ``helpers/segmented_runs.py``.
    """

    maturity = core.get("maturity")
    if not isinstance(maturity, str) or maturity not in CORE_MATURITY_VALUES:
        fail(
            label,
            f"{base}.maturity",
            f"must be one of {list(CORE_MATURITY_VALUES)}, got {maturity!r}",
        )
    physical = core.get("physical_data_maturity")
    if not isinstance(physical, str) or physical not in CORE_PHYSICAL_DATA_MATURITY_VALUES:
        fail(
            label,
            f"{base}.physical_data_maturity",
            f"must be one of {list(CORE_PHYSICAL_DATA_MATURITY_VALUES)}, got {physical!r}",
        )


def _compare_f_salt_to_q_fiss(
    q_value: Any,
    f_value: Any,
    total: float,
    label: str,
    path: str,
) -> None:
    """Recursively compare an authored ``f_salt`` tree against normalized ``q_fiss``."""

    if isinstance(q_value, list):
        if not isinstance(f_value, list) or len(f_value) != len(q_value):
            fail(label, path, "f_salt shape must match the q_fiss shape")
        for index, (q_item, f_item) in enumerate(zip(q_value, f_value)):
            _compare_f_salt_to_q_fiss(q_item, f_item, total, label, f"{path}[{index}]")
        return
    if isinstance(f_value, bool) or not isinstance(f_value, (int, float)):
        fail(label, path, f"must be numeric, got {f_value!r}")
    expected = float(q_value) / total
    if abs(float(f_value) - expected) > 1e-12:
        fail(
            label,
            path,
            f"authored f_salt must equal the normalized q_fiss share"
            f" {expected!r} within 1e-12, got {float(f_value)!r}",
        )


def _validate_f_salt(core: Mapping[str, Any], label: str, base: str) -> None:
    """Authored ``f_salt``, when present, must equal normalized ``q_fiss``.

    Owner decision O5 (TASK-20260908-01 P2): the emitter derives ``fSalt``
    as the normalized ``q_fiss`` array (9R normalizer precedent, matching
    the ``ChannelMap`` sum-to-one assert at ``core/SegmentedMSR.mo:1553``).
    A deck-authored ``f_salt`` may only restate that value (element-wise
    within 1e-12), so a future nonuniform heat profile can never silently
    diverge from its salt fraction. The committed decks author no
    ``f_salt``; this check guards the contract before one appears.
    """

    f_salt = core.get("f_salt")
    if f_salt is None:
        return
    if not is_quantity(f_salt):
        fail(
            label,
            f"{base}.f_salt",
            f"must be a quantity {{value, unit}}, got {type(f_salt).__name__}",
        )
    if str(quantity_unit(f_salt)) != "1":
        fail(
            label,
            f"{base}.f_salt.unit",
            f"must be '1' (dimensionless salt fraction), got {quantity_unit(f_salt)!r}",
        )
    q_node = _required_quantity(core, "q_fiss", label, base)
    total = _flat_sum(q_node, label, f"{base}.q_fiss")
    if total <= 0.0:
        fail(label, f"{base}.q_fiss.value", f"must sum to a positive total, got {total!r}")
    _compare_f_salt_to_q_fiss(
        quantity_value(q_node),
        quantity_value(f_salt),
        total,
        label,
        f"{base}.f_salt.value",
    )


def _validate_r1(r1: Mapping[str, Any], label: str) -> None:
    _validate_maturity(r1, label, "cores.r1")
    _validate_f_salt(r1, label, "cores.r1")
    n_chan = _structural_int(
        _required_quantity(r1, "n_chan", label, "cores.r1"), label, "cores.r1.n_chan"
    )
    n_seg = _structural_int(_required_quantity(r1, "n_seg", label, "cores.r1"), label, "cores.r1.n_seg")
    if n_chan != 1:
        fail(
            label,
            "cores.r1.n_chan.value",
            f"must be 1 (the 1R model is a single channel), got {n_chan}",
        )
    if n_seg != 2:
        fail(
            label,
            "cores.r1.n_seg.value",
            f"must be 2 (legacy fuelNode1/fuelNode2 salt pair), got {n_seg}",
        )
    for name in ("cell_vol", "q_fiss", "q_mod", "kHT", "IF"):
        node = _required_quantity(r1, name, label, "cores.r1")
        length = _array_length(node, label, f"cores.r1.{name}")
        if length != n_seg:
            fail(
                label,
                f"cores.r1.{name}.value",
                f"length {length} does not match cores.r1.n_seg ({n_seg})",
            )
    flow_frac = _required_quantity(r1, "flow_frac", label, "cores.r1")
    length = _array_length(flow_frac, label, "cores.r1.flow_frac")
    if length != n_chan:
        fail(
            label,
            "cores.r1.flow_frac.value",
            f"length {length} does not match cores.r1.n_chan ({n_chan})",
        )
    for name in ("vol_graphite", "hAnom", "kG", "hAExp", "heat_loss_tinf", "IG"):
        _required_quantity(r1, name, label, "cores.r1")
    for name in ("trim", "channel_geom", "channel_map"):
        _required_section(r1, name, label, "cores.r1")
    geom = _required_section(r1, "channel_geom", label, "cores.r1")
    for name in ("Ac", "e"):
        _required_quantity(geom, name, label, "cores.r1.channel_geom")
    for name in ("LF", "ArF"):
        node = _required_quantity(geom, name, label, "cores.r1.channel_geom")
        length = _array_length(node, label, f"cores.r1.channel_geom.{name}")
        if length != n_seg:
            fail(
                label,
                f"cores.r1.channel_geom.{name}.value",
                f"length {length} does not match cores.r1.n_seg ({n_seg})",
            )
    channel_map = _required_section(r1, "channel_map", label, "cores.r1")
    if channel_map.get("pitch_type") is None:
        fail(label, "cores.r1.channel_map.pitch_type", "missing required metadata field")
    for name in ("pitch", "dz"):
        _required_quantity(channel_map, name, label, "cores.r1.channel_map")
    for name in ("chanR", "xy"):
        node = _required_quantity(channel_map, name, label, "cores.r1.channel_map")
        length = _array_length(node, label, f"cores.r1.channel_map.{name}")
        if length != n_chan:
            fail(
                label,
                f"cores.r1.channel_map.{name}.value",
                f"length {length} does not match cores.r1.n_chan ({n_chan})",
            )
    trim = _required_section(r1, "trim", label, "cores.r1")
    for name in ("TF1", "TF2", "TG"):
        _required_quantity(trim, name, label, "cores.r1.trim")


def _validate_r1_10seg(
    r1_10seg: Mapping[str, Any],
    label: str,
    *,
    r1: Mapping[str, Any] | None = None,
) -> None:
    """Structural + sum-invariant checks for the 1-channel x 10-segment core.

    Same shape as :func:`_validate_r1` with ``n_seg == 10``; the product
    feedback API stays 2-node, so ``IF`` length is pinned to 2 (segments
    1-5 / 6-10 map to nodes 1 / 2) rather than to ``n_seg``.

    Sum invariants (TASK-20260908-01 P6 item 3, mirroring the ``_flat_sum``
    pattern of :func:`_validate_r5x5_z10`): the uniform first-cut partition
    of the r1 lumped totals is enforced at load time -- total salt volume
    0.4 m3, total fission-salt heat fraction 0.93, total moderator heat
    fraction 0.07 (== ``kG``), total heat-transfer split 1.0, and the
    single-channel ``flow_frac`` sum 1.0. When ``r1`` is given, the 5+5
    coarsening anchor is enforced too: grouping segments 1-5 / 6-10 must
    reproduce the r1 2-segment ``cell_vol``/``q_fiss``/``q_mod``/``kHT``
    values within 1e-9 -- the property that makes the
    CoarseningConsistencyCheck10Seg steady-state comparison meaningful.
    """
    _validate_maturity(r1_10seg, label, "cores.r1_10seg")
    _validate_f_salt(r1_10seg, label, "cores.r1_10seg")
    n_chan = _structural_int(
        _required_quantity(r1_10seg, "n_chan", label, "cores.r1_10seg"), label, "cores.r1_10seg.n_chan"
    )
    n_seg = _structural_int(
        _required_quantity(r1_10seg, "n_seg", label, "cores.r1_10seg"), label, "cores.r1_10seg.n_seg"
    )
    if n_chan != 1:
        fail(
            label,
            "cores.r1_10seg.n_chan.value",
            f"must be 1 (the 1R-10-seg model is a single channel), got {n_chan}",
        )
    if n_seg != 10:
        fail(
            label,
            "cores.r1_10seg.n_seg.value",
            f"must be 10 (10-segment axial resolution), got {n_seg}",
        )
    for name in ("cell_vol", "q_fiss", "q_mod", "kHT"):
        node = _required_quantity(r1_10seg, name, label, "cores.r1_10seg")
        length = _array_length(node, label, f"cores.r1_10seg.{name}")
        if length != n_seg:
            fail(
                label,
                f"cores.r1_10seg.{name}.value",
                f"length {length} does not match cores.r1_10seg.n_seg ({n_seg})",
            )
    if_node = _required_quantity(r1_10seg, "IF", label, "cores.r1_10seg")
    if_length = _array_length(if_node, label, "cores.r1_10seg.IF")
    if if_length != 2:
        fail(
            label,
            "cores.r1_10seg.IF.value",
            f"length {if_length} does not match the 2-node feedback API (segments 1-5 / 6-10 map to nodes 1 / 2)",
        )
    flow_frac = _required_quantity(r1_10seg, "flow_frac", label, "cores.r1_10seg")
    length = _array_length(flow_frac, label, "cores.r1_10seg.flow_frac")
    if length != n_chan:
        fail(
            label,
            "cores.r1_10seg.flow_frac.value",
            f"length {length} does not match cores.r1_10seg.n_chan ({n_chan})",
        )
    for name in ("vol_graphite", "hAnom", "kG", "hAExp", "heat_loss_tinf", "IG"):
        _required_quantity(r1_10seg, name, label, "cores.r1_10seg")
    for name in ("trim", "channel_geom", "channel_map"):
        _required_section(r1_10seg, name, label, "cores.r1_10seg")
    geom = _required_section(r1_10seg, "channel_geom", label, "cores.r1_10seg")
    for name in ("Ac", "e"):
        _required_quantity(geom, name, label, "cores.r1_10seg.channel_geom")
    for name in ("LF", "ArF"):
        node = _required_quantity(geom, name, label, "cores.r1_10seg.channel_geom")
        length = _array_length(node, label, f"cores.r1_10seg.channel_geom.{name}")
        if length != n_seg:
            fail(
                label,
                f"cores.r1_10seg.channel_geom.{name}.value",
                f"length {length} does not match cores.r1_10seg.n_seg ({n_seg})",
            )
    channel_map = _required_section(r1_10seg, "channel_map", label, "cores.r1_10seg")
    if channel_map.get("pitch_type") is None:
        fail(label, "cores.r1_10seg.channel_map.pitch_type", "missing required metadata field")
    for name in ("pitch", "dz"):
        _required_quantity(channel_map, name, label, "cores.r1_10seg.channel_map")
    for name in ("chanR", "xy"):
        node = _required_quantity(channel_map, name, label, "cores.r1_10seg.channel_map")
        length = _array_length(node, label, f"cores.r1_10seg.channel_map.{name}")
        if length != n_chan:
            fail(
                label,
                f"cores.r1_10seg.channel_map.{name}.value",
                f"length {length} does not match cores.r1_10seg.n_chan ({n_chan})",
            )
    trim = _required_section(r1_10seg, "trim", label, "cores.r1_10seg")
    for name in ("TF1", "TF2", "TG"):
        _required_quantity(trim, name, label, "cores.r1_10seg.trim")
    _validate_r1_10seg_sum_invariants(
        r1_10seg,
        label,
        r1_cell_vol=_r1_cell_vol_total({"r1": r1}, label) if r1 is not None else 0.4,
    )
    if r1 is not None:
        _validate_r1_10seg_coarsening_anchor(r1_10seg, r1, label)


def _flat_number(node_value: Any, label: str, path: str) -> float:
    """Coerce one numeric (non-boolean) leaf to float, else a named deck error."""
    if isinstance(node_value, bool) or not isinstance(node_value, (int, float)):
        fail(label, path, f"must be numeric, got {node_value!r}")
    return float(node_value)


def _validate_r1_10seg_sum_invariants(
    r1_10seg: Mapping[str, Any],
    label: str,
    *,
    r1_cell_vol: float = 0.4,
) -> None:
    """Load-time sum invariants of the uniform first-cut r1_10seg partition.

    The totals pin the r1 lumped values the 10-segment partition divides
    (owner-approved first cut, 2026-09-06): cell_vol sums to 0.4 m3,
    q_fiss to 0.93, q_mod to 0.07 -- which must equal the deck's own
    ``kG`` lumped graphite fraction exactly as in :func:`_validate_r5x5_z10`
    -- and kHT to 1.0. ``flow_frac`` (single channel) sums to 1.0.
    Tolerance 1e-9 matches the 5x5 validator's ``_flat_sum`` bars.
    """

    base = "cores.r1_10seg"
    for name, expected in (
        ("cell_vol", r1_cell_vol),
        ("q_fiss", 0.93),
        ("kHT", 1.0),
    ):
        node = _required_quantity(r1_10seg, name, label, base)
        total = _flat_sum(node, label, f"{base}.{name}")
        # Relative bar: scaled plants carry totals far above 1 (float sums).
        if abs(total - expected) > 1e-9 * max(1.0, abs(expected)):
            fail(
                label,
                f"{base}.{name}.value",
                f"sum {total!r} does not match the r1 lumped total {expected} within 1e-9 (relative)",
            )
    q_mod_total = _flat_sum(
        _required_quantity(r1_10seg, "q_mod", label, base), label, f"{base}.q_mod"
    )
    if abs(q_mod_total - 0.07) > 1e-9:
        fail(
            label,
            f"{base}.q_mod.value",
            f"sum {q_mod_total!r} does not match the r1 lumped total 0.07 within 1e-9",
        )
    kg_value = quantity_value(_required_quantity(r1_10seg, "kG", label, base))
    if isinstance(kg_value, bool) or not isinstance(kg_value, (int, float)):
        fail(label, f"{base}.kG.value", f"must be a scalar number, got {kg_value!r}")
    if abs(q_mod_total - float(kg_value)) > 1e-9:
        fail(
            label,
            f"{base}.q_mod.value",
            f"sum {q_mod_total!r} does not equal cores.r1_10seg.kG ({kg_value!r}) within 1e-9",
        )
    flow_total = _flat_sum(
        _required_quantity(r1_10seg, "flow_frac", label, base), label, f"{base}.flow_frac"
    )
    if abs(flow_total - 1.0) > 1e-9:
        fail(
            label,
            f"{base}.flow_frac.value",
            f"sum {flow_total!r} does not match the single-channel total 1.0 within 1e-9",
        )


def _validate_r1_10seg_coarsening_anchor(
    r1_10seg: Mapping[str, Any],
    r1: Mapping[str, Any],
    label: str,
) -> None:
    """5+5 coarsening anchor: segments 1-5 / 6-10 reproduce the r1 values.

    For ``cell_vol``, ``q_fiss``, ``q_mod``, and ``kHT``, the sums of the
    first five and the last five segment entries must equal the r1
    2-segment values within 1e-9. This is the anchor that lets the
    CoarseningConsistencyCheck10Seg compare the 10-segment steady state
    against the 2-segment product core at solver tolerance (K_mod = 0
    first cut: a symmetry replica of the 2-segment system).
    """

    base = "cores.r1_10seg"
    for name in ("cell_vol", "q_fiss", "q_mod", "kHT"):
        values = quantity_value(_required_quantity(r1_10seg, name, label, base))
        if not isinstance(values, list) or len(values) != 10:
            fail(
                label,
                f"{base}.{name}.value",
                f"must be a 10-entry array for the 5+5 coarsening anchor,"
                f" got {values!r}",
            )
        coarse = (
            sum(_flat_number(item, label, f"{base}.{name}.value[{index}]") for index, item in enumerate(values[:5])),
            sum(_flat_number(item, label, f"{base}.{name}.value[{index + 5}]") for index, item in enumerate(values[5:])),
        )
        r1_values = quantity_value(_required_quantity(r1, name, label, "cores.r1"))
        if not isinstance(r1_values, list) or len(r1_values) != 2:
            fail(
                label,
                f"cores.r1.{name}.value",
                f"must be a 2-entry array (the r1 2-segment coarsening anchor),"
                f" got {r1_values!r}",
            )
        expected = tuple(
            _flat_number(item, label, f"cores.r1.{name}.value[{index}]")
            for index, item in enumerate(r1_values)
        )
        if abs(coarse[0] - expected[0]) > 1e-9 or abs(coarse[1] - expected[1]) > 1e-9:
            fail(
                label,
                f"{base}.{name}.value",
                f"5+5 coarsening anchor {[coarse[0], coarse[1]]} does not"
                f" reproduce the r1 2-segment values {list(expected)}"
                f" within 1e-9",
            )


def _matrix_shape(quantity_node: Any, n_rows: int, n_cols: int, label: str, path: str) -> None:
    """Check that the quantity value is a homogeneous [n_rows][n_cols] matrix."""
    value = quantity_value(quantity_node)
    if not isinstance(value, list):
        fail(label, f"{path}.value", f"must be a matrix, got {type(value).__name__}")
    if len(value) != n_rows:
        fail(label, f"{path}.value", f"must have {n_rows} rows, got {len(value)}")
    for row_index, row in enumerate(value):
        if not isinstance(row, list):
            fail(
                label,
                f"{path}.value[{row_index}]",
                f"must be an array of {n_cols} entries, got {type(row).__name__}",
            )
        if len(row) != n_cols:
            fail(label, f"{path}.value[{row_index}]", f"must have {n_cols} entries, got {len(row)}")


def _flat_sum(quantity_node: Any, label: str, path: str) -> float:
    """Sum every numeric leaf of a scalar, 1-D, or 2-D quantity value."""
    total = 0.0

    def visit(item: Any, item_path: str) -> None:
        nonlocal total
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{item_path}[{index}]")
        elif isinstance(item, bool) or not isinstance(item, (int, float)):
            fail(label, item_path, f"must be numeric, got {item!r}")
        else:
            total += float(item)

    visit(quantity_value(quantity_node), f"{path}.value")
    return total


def _r1_hanom_total(cores: Mapping[str, Any], label: str) -> float:
    """The r1 lumped channel UA the segmented partitions must sum to."""
    node = _required_quantity(cores["r1"], "hAnom", label, "cores.r1")
    return float(_flat_sum(node, label, "cores.r1.hAnom"))


def _r1_cell_vol_total(cores: Mapping[str, Any], label: str) -> float:
    """The r1 lumped core salt volume the segmented partitions must sum to
    (0.4 m3 on the MSRR deck; read from cores.r1 so scaled plants follow)."""
    node = _required_quantity(cores["r1"], "cell_vol", label, "cores.r1")
    return float(_flat_sum(node, label, "cores.r1.cell_vol"))


def _validate_r5x5_z10(
    r5x5_z10: Mapping[str, Any],
    label: str,
    *,
    r1_hanom: float = 4565.0,
    r1_cell_vol: float = 0.4,
) -> None:
    """Structural checks for the 25-channel x 10-axial-segment core.

    Mirrors :func:`_validate_r1_10seg` for the 5x5 staggered-triangular
    map (TASK-20260906-02): 2-D [n_chan][n_seg] cell/heat fractions,
    1-D [n_chan] per-channel arrays, the informational ``pitch_type``
    pinned to "Triangular", and the strictly positive ``k_mod``
    (W/(m.K)) the product assembly binds as ``K_mod``, whose structured
    ``provenance`` correlation record must recompute (see
    :func:`_validate_k_mod_provenance`). The sum invariants pin the uniform
    25x10 partition of the r1 lumped totals.
    """
    base = "cores.r5x5_z10"
    _validate_maturity(r5x5_z10, label, base)
    _validate_f_salt(r5x5_z10, label, base)
    n_chan = _structural_int(
        _required_quantity(r5x5_z10, "n_chan", label, base), label, f"{base}.n_chan"
    )
    n_seg = _structural_int(
        _required_quantity(r5x5_z10, "n_seg", label, base), label, f"{base}.n_seg"
    )
    if n_chan != 25:
        fail(
            label,
            f"{base}.n_chan.value",
            f"must be 25 (5 x 5 staggered triangular lattice), got {n_chan}",
        )
    if n_seg != 10:
        fail(
            label,
            f"{base}.n_seg.value",
            f"must be 10 (10-segment axial resolution), got {n_seg}",
        )
    for name in ("cell_vol", "q_fiss", "q_mod"):
        node = _required_quantity(r5x5_z10, name, label, base)
        _matrix_shape(node, n_chan, n_seg, label, f"{base}.{name}")
    for name in ("hAnom", "flow_frac"):
        node = _required_quantity(r5x5_z10, name, label, base)
        length = _array_length(node, label, f"{base}.{name}")
        if length != n_chan:
            fail(
                label,
                f"{base}.{name}.value",
                f"length {length} does not match cores.r5x5_z10.n_chan ({n_chan})",
            )
    kht = _required_quantity(r5x5_z10, "kHT", label, base)
    length = _array_length(kht, label, f"{base}.kHT")
    if length != n_seg:
        fail(
            label,
            f"{base}.kHT.value",
            f"length {length} does not match cores.r5x5_z10.n_seg ({n_seg})",
        )
    if_node = _required_quantity(r5x5_z10, "IF", label, base)
    if_length = _array_length(if_node, label, f"{base}.IF")
    if if_length != 2:
        fail(
            label,
            f"{base}.IF.value",
            f"length {if_length} does not match the 2-node feedback API (segments 1-5 / 6-10 map to nodes 1 / 2)",
        )
    for name, expected in (
        ("cell_vol", r1_cell_vol),
        ("q_fiss", 0.93),
        ("kHT", 1.0),
        ("flow_frac", 1.0),
        # The r1 deck's channel total (TASK-20261001-01 recomputation), read
        # from cores.r1 rather than pinned, so the partition follows it.
        ("hAnom", r1_hanom),
    ):
        node = _required_quantity(r5x5_z10, name, label, base)
        total = _flat_sum(node, label, f"{base}.{name}")
        # Relative bar: scaled plants carry totals far above 1 (float sums).
        if abs(total - expected) > 1e-9 * max(1.0, abs(expected)):
            fail(
                label,
                f"{base}.{name}.value",
                f"sum {total!r} does not match the r1 lumped total {expected} within 1e-9 (relative)",
            )
    q_mod_total = _flat_sum(
        _required_quantity(r5x5_z10, "q_mod", label, base), label, f"{base}.q_mod"
    )
    if abs(q_mod_total - 0.07) > 1e-9:
        fail(
            label,
            f"{base}.q_mod.value",
            f"sum {q_mod_total!r} does not match the r1 lumped total 0.07 within 1e-9",
        )
    kg_value = quantity_value(_required_quantity(r5x5_z10, "kG", label, base))
    if isinstance(kg_value, bool) or not isinstance(kg_value, (int, float)):
        fail(label, f"{base}.kG.value", f"must be a scalar number, got {kg_value!r}")
    if abs(q_mod_total - float(kg_value)) > 1e-9:
        fail(
            label,
            f"{base}.q_mod.value",
            f"sum {q_mod_total!r} does not equal cores.r5x5_z10.kG ({kg_value!r}) within 1e-9",
        )
    for name in ("vol_graphite", "hAnom", "kG", "hAExp", "heat_loss_tinf", "IG"):
        _required_quantity(r5x5_z10, name, label, base)
    for name in ("trim", "channel_geom", "channel_map"):
        _required_section(r5x5_z10, name, label, base)
    geom = _required_section(r5x5_z10, "channel_geom", label, base)
    for name in ("Ac", "e"):
        _required_quantity(geom, name, label, f"{base}.channel_geom")
    for name in ("LF", "ArF"):
        node = _required_quantity(geom, name, label, f"{base}.channel_geom")
        length = _array_length(node, label, f"{base}.channel_geom.{name}")
        if length != n_seg:
            fail(
                label,
                f"{base}.channel_geom.{name}.value",
                f"length {length} does not match cores.r5x5_z10.n_seg ({n_seg})",
            )
    channel_map = _required_section(r5x5_z10, "channel_map", label, base)
    if channel_map.get("pitch_type") is None:
        fail(label, f"{base}.channel_map.pitch_type", "missing required metadata field")
    if channel_map["pitch_type"] != "Triangular":
        fail(
            label,
            f"{base}.channel_map.pitch_type",
            f"must be 'Triangular' (the 5x5 map is a staggered triangular lattice),"
            f" got {channel_map['pitch_type']!r}",
        )
    for name in ("pitch", "dz"):
        _required_quantity(channel_map, name, label, f"{base}.channel_map")
    chan_r = _required_quantity(channel_map, "chanR", label, f"{base}.channel_map")
    length = _array_length(chan_r, label, f"{base}.channel_map.chanR")
    if length != n_chan:
        fail(
            label,
            f"{base}.channel_map.chanR.value",
            f"length {length} does not match cores.r5x5_z10.n_chan ({n_chan})",
        )
    xy = _required_quantity(channel_map, "xy", label, f"{base}.channel_map")
    _matrix_shape(xy, n_chan, 2, label, f"{base}.channel_map.xy")
    k_mod_value = quantity_value(_required_quantity(r5x5_z10, "k_mod", label, base))
    if (
        isinstance(k_mod_value, bool)
        or not isinstance(k_mod_value, (int, float))
        or k_mod_value != k_mod_value
        or k_mod_value == float("inf")
        or k_mod_value <= 0.0
    ):
        fail(
            label,
            f"{base}.k_mod.value",
            f"must be a positive finite number (W/(m.K)), got {k_mod_value!r}",
        )
    _validate_k_mod_provenance(_required_quantity(r5x5_z10, "k_mod", label, base), label, base)
    trim = _required_section(r5x5_z10, "trim", label, base)
    for name in ("TF1", "TF2", "TG"):
        _required_quantity(trim, name, label, f"{base}.trim")


def _validate_k_mod_provenance(k_mod: Mapping[str, Any], label: str, base: str) -> None:
    """Validate the structured ``k_mod`` provenance record (P2 item 5).

    The record must recompute: evaluating the stored correlation
    coefficients (``c0 + c1*T + c2*T^2``, T in degC) at the stored
    evaluation temperature must reproduce the stored unrounded result,
    the stored value must equal the recorded rounded result, and the
    rounded result must sit within half a rounding step of the unrounded
    one. Editing a coefficient, the evaluation temperature, or a result
    inconsistently therefore fails at load time with a named deck error.
    """

    path = f"{base}.k_mod.provenance"
    prov = k_mod.get("provenance")
    if not isinstance(prov, Mapping):
        fail(
            label,
            path,
            "missing structured provenance record (report, correlation"
            " coefficients, evaluation temperature, results); got"
            f" {type(prov).__name__}",
        )
    required_text = (
        "kind",
        "report",
        "authors",
        "material_grade",
        "grade_caveat",
        "uncertainty_note",
        "evaluation_temperature_note",
        "rounding_note",
    )
    for key in required_text:
        value = prov.get(key)
        if not isinstance(value, str) or not value.strip():
            fail(label, f"{path}.{key}", f"must be a non-empty string, got {value!r}")
    year = prov.get("year")
    if isinstance(year, bool) or not isinstance(year, int):
        fail(label, f"{path}.year", f"must be an integer, got {year!r}")
    grade_confirmed = prov.get("grade_confirmed")
    if not isinstance(grade_confirmed, bool):
        fail(label, f"{path}.grade_confirmed", f"must be a boolean, got {grade_confirmed!r}")
    uncertainty = _provenance_number(prov, "uncertainty_frac", label, path)
    if not 0.0 < uncertainty < 1.0:
        fail(label, f"{path}.uncertainty_frac", f"must be within (0, 1), got {uncertainty!r}")
    valid_range = prov.get("valid_range_degC")
    if not isinstance(valid_range, list) or len(valid_range) != 2:
        fail(
            label,
            f"{path}.valid_range_degC",
            f"must be a two-entry [low, high] list, got {valid_range!r}",
        )
    low = _provenance_number(valid_range, 0, label, f"{path}.valid_range_degC")
    high = _provenance_number(valid_range, 1, label, f"{path}.valid_range_degC")
    if not low < high:
        fail(
            label,
            f"{path}.valid_range_degC",
            f"must be ascending [low, high], got [{low}, {high}]",
        )
    correlation = prov.get("correlation")
    if not isinstance(correlation, Mapping):
        fail(label, f"{path}.correlation", f"must be a mapping, got {type(correlation).__name__}")
    for key in ("form", "coefficient_units"):
        text = correlation.get(key)
        if not isinstance(text, str) or not text.strip():
            fail(label, f"{path}.correlation.{key}", f"must be a non-empty string, got {text!r}")
    c0 = _provenance_number(correlation, "c0", label, f"{path}.correlation")
    c1 = _provenance_number(correlation, "c1", label, f"{path}.correlation")
    c2 = _provenance_number(correlation, "c2", label, f"{path}.correlation")
    t_eval = _provenance_number(prov, "evaluation_temperature_degC", label, path)
    if t_eval <= -273.15:
        fail(
            label,
            f"{path}.evaluation_temperature_degC",
            f"must be above absolute zero, got {t_eval!r}",
        )
    unrounded = _provenance_number(prov, "result_unrounded", label, path)
    rounded = _provenance_number(prov, "result_rounded", label, path)
    step = _provenance_number(prov, "rounding_step", label, path)
    if step <= 0.0:
        fail(label, f"{path}.rounding_step", f"must be positive, got {step!r}")
    recomputed = c0 + c1 * t_eval + c2 * t_eval * t_eval
    if abs(recomputed - unrounded) > 1e-9:
        fail(
            label,
            f"{path}.result_unrounded",
            f"{recomputed!r} (correlation evaluated at"
            f" evaluation_temperature_degC = {t_eval!r}) does not match the"
            f" stored result {unrounded!r} within 1e-9",
        )
    stored = float(quantity_value(k_mod))
    if abs(stored - rounded) > 1e-12:
        fail(
            label,
            f"{base}.k_mod.value",
            f"must equal provenance.result_rounded ({rounded!r}) within 1e-12,"
            f" got {stored!r}",
        )
    if abs(unrounded - rounded) > step / 2.0 + 1e-12:
        fail(
            label,
            f"{path}.result_rounded",
            f"{rounded!r} is not the nearest {step!r}-step rounding of the"
            f" unrounded result {unrounded!r}",
        )


def _provenance_number(
    node: Any,
    key: int | str,
    label: str,
    path: str,
) -> float:
    """Read one finite number out of a provenance mapping or list."""

    if isinstance(node, Mapping):
        value = node.get(key)
    else:
        value = node[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        fail(label, path, f"must be a finite number, got {value!r}")
    return float(value)


def _zone_ints(node: Any, label: str, path: str) -> list[int]:
    values = quantity_value(node)
    if not isinstance(values, list):
        fail(label, f"{path}.value", f"must be an array, got {type(values).__name__}")
    out: list[int] = []
    for index, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, (int, float)) or (
            isinstance(item, float) and not float(item).is_integer()
        ):
            fail(label, f"{path}.value[{index}]", f"must be an integer region index, got {item!r}")
        out.append(int(item))
    return out


def _validate_r9(r9: Mapping[str, Any], label: str) -> None:
    _validate_maturity(r9, label, "cores.r9")
    n_regions = _structural_int(
        _required_quantity(r9, "n_regions", label, "cores.r9"), label, "cores.r9.n_regions"
    )
    n_zones = _structural_int(
        _required_quantity(r9, "n_zones", label, "cores.r9"), label, "cores.r9.n_zones"
    )
    if n_regions != 9:
        fail(
            label,
            "cores.r9.n_regions.value",
            f"must be 9 (nine-region 9R mesh), got {n_regions}",
        )
    if n_zones != 4:
        fail(
            label,
            "cores.r9.n_zones.value",
            f"must be 4 (four series flow zones), got {n_zones}",
        )
    for name in _R9_REGION_ARRAYS:
        node = _required_quantity(r9, name, label, "cores.r9")
        length = _array_length(node, label, f"cores.r9.{name}")
        if length != n_regions:
            fail(
                label,
                f"cores.r9.{name}.value",
                f"length {length} does not match cores.r9.n_regions ({n_regions})",
            )
    trim = _required_section(r9, "trim", label, "cores.r9")
    for name in ("TF1", "TF2", "TG"):
        _required_quantity(trim, name, label, "cores.r9.trim")
    for name in ("TF1_regions", "TF2_regions", "TG_regions"):
        node = _required_quantity(trim, name, label, "cores.r9.trim")
        length = _array_length(node, label, f"cores.r9.trim.{name}")
        if length != n_regions:
            fail(
                label,
                f"cores.r9.trim.{name}.value",
                f"length {length} does not match cores.r9.n_regions ({n_regions})",
            )
    for name in _R9_ZONE_ARRAYS:
        node = _required_quantity(r9, name, label, "cores.r9")
        length = _array_length(node, label, f"cores.r9.{name}")
        if length != n_zones:
            fail(
                label,
                f"cores.r9.{name}.value",
                f"length {length} does not match cores.r9.n_zones ({n_zones})",
            )
    _validate_r9_zone_bounds(r9, label, n_regions)
    if r9.get("graphite_split_policy") != "raw_kHT_sum":
        fail(
            label,
            "cores.r9.graphite_split_policy",
            f"must be 'raw_kHT_sum' (graphite-direct share is the raw kHT sum),"
            f" got {r9.get('graphite_split_policy')!r}",
        )
    mixing_pot = _required_section(r9, "mixing_pot", label, "cores.r9")
    for name in ("Ac", "L", "Ar", "Tmix_0"):
        _required_quantity(mixing_pot, name, label, "cores.r9.mixing_pot")
    _required_quantity(r9, "region_coast_down_K", label, "cores.r9")
    _required_quantity(r9, "e", label, "cores.r9")
    _validate_r9_flow_frac_zones(r9, label)


def _validate_r9_zone_bounds(r9: Mapping[str, Any], label: str, n_regions: int) -> None:
    starts = _zone_ints(r9["zone_start"], label, "cores.r9.zone_start")
    ends = _zone_ints(r9["zone_end"], label, "cores.r9.zone_end")
    segs = _zone_ints(r9["n_seg_zone"], label, "cores.r9.n_seg_zone")
    if starts[0] != 1:
        fail(
            label,
            "cores.r9.zone_start.value[0]",
            f"zone numbering is 1-based: the first zone must start at region 1, got {starts[0]}",
        )
    if ends[-1] != n_regions:
        fail(
            label,
            "cores.r9.zone_end.value[-1]",
            f"the last zone must end at region {n_regions} (n_regions), got {ends[-1]}",
        )
    for index, (start, end) in enumerate(zip(starts, ends)):
        if not 1 <= start <= end <= n_regions:
            fail(
                label,
                f"cores.r9.zone_start.value[{index}]",
                f"zone {index + 1} bounds [{start}, {end}] are outside 1..{n_regions}",
            )
        if index > 0:
            if start <= starts[index - 1]:
                fail(
                    label,
                    f"cores.r9.zone_start.value[{index}]",
                    f"zone starts must strictly increase: {start} does not follow {starts[index - 1]}",
                )
            if end <= ends[index - 1]:
                fail(
                    label,
                    f"cores.r9.zone_end.value[{index}]",
                    f"zone ends must strictly increase: {end} does not follow {ends[index - 1]}",
                )
            if start != ends[index - 1] + 1:
                fail(
                    label,
                    f"cores.r9.zone_start.value[{index}]",
                    f"zones must be contiguous: zone {index + 1} starts at region {start}"
                    f" but zone {index} ends at region {ends[index - 1]}",
                )
        expected_cells = 2 * (end - start + 1)
        if segs[index] != expected_cells:
            fail(
                label,
                f"cores.r9.n_seg_zone.value[{index}]",
                f"zone {index + 1} spans {end - start + 1} region(s) so needs {expected_cells}"
                f" salt cells (2 per region), got {segs[index]}",
            )


def _validate_r9_flow_frac_zones(r9: Mapping[str, Any], label: str) -> None:
    fracs = [float(item) for item in quantity_value(r9["flow_frac_zones"])]
    for index, frac in enumerate(fracs):
        if frac <= 0.0:
            fail(
                label,
                f"cores.r9.flow_frac_zones.value[{index}]",
                f"zone flow fraction must be positive, got {frac}",
            )
    total = sum(fracs)
    if abs(total - 1.0) > 1.0e-9:
        fail(
            label,
            "cores.r9.flow_frac_zones.value",
            f"zone flow fractions must sum to 1, got {total!r}",
        )


def radial_loop_channel_count(plant: Mapping[str, Any], core_key: str) -> int:
    """Annular-loop channel count the radial distribution must match.

    One annular leg per segmented channel: ``n_chan`` for r1 / r1_10seg /
    r5x5_z10. For the segmented 9R core the count is ``n_zones`` = 4 -- the
    size of the PREPARATORY-ONLY four-entry ``IntraChannelRadial9R`` record
    (TASK-20260915-01 P6 fallback): the executable 9R topology is FOUR
    independent ONE-channel zone ``SegmentedCore`` instances
    (R9MSRRuhxTrimThermalSS mapZ1..mapZ4, ``nChan`` = 1 each), so the
    four-entry record binds nowhere and an enabled
    ``cores.r9.intra_channel_radial`` block is refused fail-closed (see
    ``_validate_one_radial_block``); the per-region fuel mesh inside each
    zone stays inside one of the four legs.
    """

    core = plant["cores"][core_key]
    if core_key == "r9":
        node = _required_quantity(core, "n_zones", "cores.r9 (radial)", "cores.r9")
        path = "cores.r9.n_zones"
    else:
        node = _required_quantity(core, "n_chan", f"cores.{core_key} (radial)", f"cores.{core_key}")
        path = f"cores.{core_key}.n_chan"
    return _structural_int(node, "cores (radial channel count)", path)


def radial_stack_segment_count(plant: Mapping[str, Any], core_key: str) -> int:
    """Axial segment count the per-segment radial record fields must match.

    The explicit per-segment physical radial length
    (``intra_channel_radial.geometry.physical_length``, review rev019
    Phase 2 / planner resolution 6) carries one entry per core axial
    segment: ``n_seg`` for r1 / r1_10seg / r5x5_z10, and the summed
    ``n_seg_zone`` for the segmented 9R core (the zone chains interleave
    the per-region F1/F2 cells, so the record-level segment count is
    ``sum(n_seg_zone)``). On the P6 fallback the r9 record is
    preparatory-only data (an enabled ``cores.r9.intra_channel_radial``
    block is refused), so no per-zone slicing is emitted or bound.
    """

    core = plant["cores"][core_key]
    if core_key == "r9":
        node = _required_quantity(core, "n_seg_zone", "cores.r9 (radial)", "cores.r9")
        path = "cores.r9.n_seg_zone.value"
        values = quantity_value(node)
        if not isinstance(values, list):
            fail(
                "cores.r9 (radial)",
                path,
                f"must be an array, got {values!r}",
            )
        total = 0
        for index, item in enumerate(values):
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                fail(
                    "cores.r9 (radial)",
                    f"{path}[{index}]",
                    f"must be a positive integer segment count, got {item!r}",
                )
            total += item
        return total
    node = _required_quantity(core, "n_seg", f"cores.{core_key} (radial)", f"cores.{core_key}")
    path = f"cores.{core_key}.n_seg"
    return _structural_int(node, "cores (radial segment count)", path)


def _radial_authored_fuel_lengths(
    plant: Mapping[str, Any],
    core_key: str,
    label: str,
) -> list[float]:
    """The core's authored per-segment physical fuel-length chain.

    The provenance basis the explicit ``physical_length`` record field must
    match under ``strict_physical`` (review rev019: the stack must not
    reuse ``map.dz`` -- moderator center spacing -- unless a check proves
    equality; 1r10seg is 0.1575 m per segment vs ``dz`` = 0.14 m).
    Shapes follow the per-core conventions of
    :func:`_radial_declared_fuel_volumes`: r1/r1_10seg/r5x5_z10 carry the
    per-segment ``channel_geom.LF`` array (shared across channels for
    r5x5_z10), and r9 carries the interleaved per-region
    ``LF1[i], LF2[i]`` chain (18 entries, zone-slicable).
    """

    core = plant["cores"][core_key]

    def _leaves(node: Any, path: str) -> list[float]:
        value = quantity_value(node)
        if not isinstance(value, list):
            fail(label, f"{path}.value", f"must be an array, got {value!r}")
        return [float(item) for item in value]

    if core_key in ("r1", "r1_10seg", "r5x5_z10"):
        geom = _required_section(core, "channel_geom", label, f"cores.{core_key}")
        return _leaves(
            _required_quantity(geom, "LF", label, f"cores.{core_key}.channel_geom"),
            f"cores.{core_key}.channel_geom.LF",
        )
    if core_key == "r9":
        lf1 = _leaves(
            _required_quantity(core, "LF1", label, f"cores.{core_key}"),
            f"cores.{core_key}.LF1",
        )
        lf2 = _leaves(
            _required_quantity(core, "LF2", label, f"cores.{core_key}"),
            f"cores.{core_key}.LF2",
        )
        chain: list[float] = []
        for first, second in zip(lf1, lf2):
            chain.extend([first, second])
        return chain
    raise AssertionError(f"unsupported radial core key {core_key!r}")


def _radial_declared_fuel_volumes(
    plant: Mapping[str, Any],
    core_key: str,
    label: str,
) -> list[tuple[float, float]]:
    """Per-cell ``(physical_fuel_length, declared_salt_volume)`` pairs.

    Inputs to the ``strict_physical`` geometry check (review rev019 Phase
    2, TASK-20260915-01 P2): the geometric fuel volume
    ``N_c*pi*r_f^2*L_j`` -- multiplicity ``N_c`` times the shared fuel
    radius and the per-segment physical radial length ``L_j`` -- must
    reproduce the DECLARED thermal salt inventory per stack cell within
    tolerance. Shapes follow the per-core conventions: r1/r1_10seg pair
    per-segment ``channel_geom.LF`` entries with the ``cell_vol`` array;
    r5x5_z10 pairs the one shared per-segment ``LF`` with each of its 25
    per-channel ``cell_vol`` rows; r9 pairs the per-region ``LF1``/``LF2``
    with ``vol_F1``/``vol_F2`` (the interleaved F1/F2 zone-chain cells).
    """

    core = plant["cores"][core_key]

    if core_key in ("r1", "r1_10seg"):
        geom = _required_section(core, "channel_geom", label, f"cores.{core_key}")
        lf = [
            float(item)
            for item in quantity_value(
                _required_quantity(geom, "LF", label, f"cores.{core_key}.channel_geom")
            )
        ]
        vols = quantity_value(_required_quantity(core, "cell_vol", label, f"cores.{core_key}"))
        if not isinstance(vols, list) or len(vols) != len(lf):
            fail(
                label,
                f"cores.{core_key}.cell_vol.value",
                f"must be a per-segment array matching channel_geom.LF "
                f"({len(lf)} entries), got {vols!r}",
            )
        return list(zip(lf, (float(item) for item in vols)))
    if core_key == "r5x5_z10":
        geom = _required_section(core, "channel_geom", label, f"cores.{core_key}")
        lf = [
            float(item)
            for item in quantity_value(
                _required_quantity(geom, "LF", label, f"cores.{core_key}.channel_geom")
            )
        ]
        vols = quantity_value(_required_quantity(core, "cell_vol", label, f"cores.{core_key}"))
        if not isinstance(vols, list):
            fail(label, f"cores.{core_key}.cell_vol.value", "must be a per-channel matrix")
        pairs: list[tuple[float, float]] = []
        for row in vols:
            if not isinstance(row, list) or len(row) != len(lf):
                fail(
                    label,
                    f"cores.{core_key}.cell_vol.value",
                    f"must be a per-channel array of {len(lf)} per-segment "
                    f"volumes matching channel_geom.LF, got {row!r}",
                )
            pairs.extend(zip(lf, (float(item) for item in row)))
        return pairs
    if core_key == "r9":
        lf1 = [
            float(item)
            for item in quantity_value(_required_quantity(core, "LF1", label, f"cores.{core_key}"))
        ]
        lf2 = [
            float(item)
            for item in quantity_value(_required_quantity(core, "LF2", label, f"cores.{core_key}"))
        ]
        vol1 = quantity_value(_required_quantity(core, "vol_F1", label, f"cores.{core_key}"))
        vol2 = quantity_value(_required_quantity(core, "vol_F2", label, f"cores.{core_key}"))
        if (
            not isinstance(vol1, list)
            or not isinstance(vol2, list)
            or len(vol1) != len(lf1)
            or len(vol2) != len(lf2)
        ):
            fail(
                label,
                f"cores.{core_key}.vol_F1.value",
                "per-region fuel-node volumes must match the LF1/LF2 array lengths",
            )
        pairs = []
        for first, second in zip(zip(lf1, vol1), zip(lf2, vol2)):
            pairs.extend([(first[0], float(first[1])), (second[0], float(second[1]))])
        return pairs
    raise AssertionError(f"unsupported radial core key {core_key!r}")


def _radial_deposited_fractions_total(plant: Mapping[str, Any], core_key: str) -> float:
    """Total authored direct-deposition share (fuel + moderator) of a core.

    The established per-core heat split stays the authority for the
    fuel/moderator shares (owner decision 8: default annular/pipe
    fractions are zero). Used by the radial ``heat_deposition`` sum-to-1
    invariant: ``f_fuel + f_moderator + f_pipe + f_annular = 1``.

    r9 basis is the MODEL-LEVEL total, exactly 1: the segmented 9R zone
    maps normalize LOCALLY by ``powSplit9R[z]`` (core/SegmentedMSR.mo
    ~:2103), so each zone's fuel+moderator shares sum to exactly 1. Since
    the 2026-09-20 B3 Sigma=1 renormalization (TASK-20260920-01 P8,
    rev021 finding B3) the RAW region arrays themselves are the
    renormalized values and sum to 1 - the former 1.000649
    ``raw_kHT_sum`` bookkeeping surplus (~0.065%) was removed at the data
    level - so the model-level and raw bases agree (a uniform scale
    cancels in the per-zone normalization either way).
    """

    if core_key == "r9":
        return 1.0
    core = plant["cores"][core_key]
    label = f"cores.{core_key} (radial deposition check)"
    total = 0.0
    for name in ("q_fiss", "q_mod"):
        total += _flat_sum(
            _required_quantity(core, name, label, f"cores.{core_key}"),
            label,
            f"cores.{core_key}.{name}",
        )
    return total


_RADIAL_TOP_KEYS = frozenset(
    {
        "enabled",
        "maturity",
        "dataset_id",
        "geometry_policy",
        "volume_tolerance",
        "channel_meaning",
        "channel_multiplicity",
        "geometry",
        "annular_fluid",
        "annular_loop",
        "interfaces",
        "heat_deposition",
    }
)
_RADIAL_GEOMETRY_KEYS = frozenset(
    {"fuel_radius", "pipe_outer_radius", "annulus_outer_radius", "physical_length"}
)
_RADIAL_INTERFACE_KEYS = frozenset({"fuel_pipe", "pipe_fluid", "fluid_moderator"})
_RADIAL_LOOP_KEYS = frozenset(
    {
        "nominal_mass_flow",
        "max_flow_command",
        "channel_flow_fractions",
        "supply_plenum_volume",
        "return_plenum_volume",
        "connecting_pipe_volume",
        "heat_exchanger",
    }
)
_RADIAL_HX_KEYS = frozenset(
    {
        "enabled",
        "model",
        "ua",
        "loop_side_volume",
        "sink_temperature",
        "initial_temperature",
    }
)
_RADIAL_DEPOSITION_KEYS = frozenset({"pipe_fraction", "annular_fluid_fraction"})


def _radial_positive_quantity(
    node: Any,
    label: str,
    path: str,
    *,
    unit: str,
    allow_zero: bool = False,
) -> float:
    """Validate a quantity leaf as a finite positive (or nonnegative) scalar."""

    value = quantity_value(node)
    if isinstance(value, list) or isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(label, f"{path}.value", f"must be a finite scalar, got {value!r}")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        comparison = ">=" if allow_zero else ">"
        fail(label, f"{path}.value", f"must be {comparison} 0, got {number!r}")
    actual_unit = quantity_unit(node)
    if actual_unit != unit:
        fail(label, f"{path}.unit", f"must be {unit!r}, got {actual_unit!r}")
    return number


def _validate_one_radial_block(
    plant: Mapping[str, Any],
    core_key: str,
    label: str,
) -> None:
    """Validate one core's ``intra_channel_radial`` block (plan §2.2, §6, §9).

    Absent block -> disabled defaults (all shipped cores). Present block:
    every physics quantity must be a provenance-carrying envelope; the
    three structural decisions must form a valid plan §2.2 combination;
    enabled stacks require geometry/materials/interfaces with positive
    values; circulating loops require a valid mass-flow definition with
    ``sum(channel_flow_fractions) = 1`` within
    :data:`RADIAL_FRACTION_TOLERANCE`. Every refusal names the full dotted
    configuration path and the offending value (plan §9).
    """

    base = f"cores.{core_key}.{RADIAL_BLOCK_KEY}"
    core = plant["cores"][core_key]
    block = core.get(RADIAL_BLOCK_KEY)
    # Lattice-geometry declaration (review rev020 Phase 4 option 2;
    # TASK-20260916-01 P4): validated on EVERY core wherever declared,
    # BEFORE the radial-block early return - a typo'd value must refuse,
    # never silently canonicalize to the placeholder default. The
    # declaration lives on the core's channel_map (next to the pitch it
    # qualifies; the radial block's key set is pinned to the JSON schema
    # twin, so the declaration is channel_map-only by design).
    channel_map = core.get("channel_map")
    map_lattice = (
        channel_map.get("lattice_geometry")
        if isinstance(channel_map, Mapping)
        else None
    )
    if map_lattice is not None and (
        not isinstance(map_lattice, str)
        or map_lattice not in RADIAL_LATTICE_GEOMETRY_VALUES
    ):
        fail(
            label,
            f"cores.{core_key}.channel_map.lattice_geometry",
            f"must be one of {list(RADIAL_LATTICE_GEOMETRY_VALUES)}, "
            f"got {map_lattice!r}",
        )
    if block is None:
        return
    require_mapping(block, label, base, f"core '{core_key}' radial block")
    unknown = sorted(str(key) for key in block if key not in _RADIAL_TOP_KEYS)
    if unknown:
        fail(label, base, f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_TOP_KEYS)}")
    check_quantity_tree(block, label, base, require_provenance=True)

    enabled = block.get("enabled")
    if not isinstance(enabled, bool):
        fail(label, f"{base}.enabled", f"must be a boolean, got {enabled!r}")

    # --- P6 rank guard (review rev019 Phase 6; planner resolution 4
    #     FALLBACK; TASK-20260915-01) ----------------------------------------
    # PERMANENT refusal: plant-driven intra-channel radial activation of r9
    # is refused fail-closed. The generated r9 radial contract is ONE
    # four-entry parallel-channel record (channelFlowFractions sized by
    # radial_loop_channel_count = cores.r9.n_zones = 4), but the executable
    # 9R topology is FOUR independent ONE-channel zone SegmentedCore
    # instances (mapZ1..mapZ4, nChan = 1 each): the four-entry record cannot
    # bind to any zone core. P6 chose the review's fallback (one
    # unambiguous interpretation = the plant side never drives circulating
    # 9R radial data; the 1R-family activation is unchanged) over the
    # zone-indexed record path, so this guard does not move again: the
    # generated four-entry record is PREPARATORY-ONLY data that binds
    # nowhere. A future owner decision to make circulating 9R plant-driven
    # would replace the r9 block with a zone-indexed schema (four per-zone
    # sub-blocks, generated records IntraChannelRadial9R_Z1..Z4 with
    # channelFlowFractions[1] and per-zone n_seg_zone slices) and re-base
    # this guard into per-zone validation. Disabled r9 blocks stay accepted
    # (preparatory data; the disabled record canonicalizes identity to ""
    # so no phantom data). Division of labor with the Modelica layer: this
    # is the PRIMARY refusal before translation; in-model, a wrong-length
    # record surfaces as a translation-time dimension refusal on the loop's
    # w[nChan] binding (AnnularLoop.ParallelChannelDistributor), and the
    # enabled-path size(radial.physicalLength, 1) == nSeg assert on
    # SegmentedCore.
    if core_key == "r9" and enabled:
        fail(
            label,
            f"{base}.enabled",
            "plant-driven intra-channel radial activation of cores.r9 is "
            "refused fail-closed (TASK-20260915-01 P6 fallback: circulating "
            "radial 9R is never plant-driven): the generated 9R contract is "
            "the four-entry parallel-channel record (channel_flow_fractions "
            "sized by cores.r9.n_zones = 4), which is preparatory-only data "
            "- the executable 9R topology is four independent ONE-channel "
            "zone SegmentedCore instances (n_chan = 1), so the four-entry "
            "record cannot bind to any zone core; only the 1R-family cores "
            f"accept an enabled radial block; got enabled={enabled!r}",
        )

    # --- optional scalars -------------------------------------------------
    maturity = block.get("maturity")
    if maturity is not None:
        if not isinstance(maturity, str) or maturity not in CORE_MATURITY_VALUES:
            fail(
                label,
                f"{base}.maturity",
                f"must be one of {list(CORE_MATURITY_VALUES)}, got {maturity!r}",
            )
        if enabled and not maturity.strip():
            fail(label, f"{base}.maturity", "must be a non-empty string when enabled")
    elif enabled:
        fail(
            label,
            f"{base}.maturity",
            "missing required maturity label on an enabled radial block "
            f"(one of {list(CORE_MATURITY_VALUES)})",
        )
    dataset_id = block.get("dataset_id")
    if dataset_id is not None:
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            fail(label, f"{base}.dataset_id", f"must be a non-empty string, got {dataset_id!r}")
    elif enabled:
        fail(label, f"{base}.dataset_id", "missing required dataset identity on an enabled radial block")
    policy = block.get("geometry_policy")
    if policy is None:
        policy = "strict_physical"
    if policy not in RADIAL_GEOMETRY_POLICIES:
        fail(
            label,
            f"{base}.geometry_policy",
            f"must be one of {list(RADIAL_GEOMETRY_POLICIES)}, got {policy!r}",
        )
    if "volume_tolerance" in block:
        _radial_positive_quantity(
            block["volume_tolerance"],
            label,
            f"{base}.volume_tolerance",
            unit="1",
            allow_zero=False,
        )

    # --- annular fluid mode ----------------------------------------------
    annular_fluid = block.get("annular_fluid", {})
    if annular_fluid is None:
        annular_fluid = {}
    require_mapping(annular_fluid, label, f"{base}.annular_fluid", "'annular_fluid'")
    unknown = sorted(str(key) for key in annular_fluid if key not in ("mode", "flow_direction"))
    if unknown:
        fail(
            label,
            f"{base}.annular_fluid",
            f"unknown key(s) {unknown}; valid keys: ['flow_direction', 'mode']",
        )
    mode = annular_fluid.get("mode", "static")
    if mode not in ANNULAR_FLUID_MODES:
        fail(
            label,
            f"{base}.annular_fluid.mode",
            f"must be one of {list(ANNULAR_FLUID_MODES)}, got {mode!r}",
        )
    flow_direction = annular_fluid.get("flow_direction", "bottom_to_top")
    if flow_direction not in FLOW_DIRECTIONS:
        fail(
            label,
            f"{base}.annular_fluid.flow_direction",
            f"must be one of {list(FLOW_DIRECTIONS)}, got {flow_direction!r}",
        )

    # --- valid-combination table (plan §2.2) ------------------------------
    if mode == "circulating" and not enabled:
        fail(
            label,
            f"{base}.annular_fluid.mode",
            f"'circulating' requires intra_channel_radial.enabled=true "
            f"(valid combinations: disabled, enabled+static, "
            f"enabled+circulating, enabled+circulating+heat exchanger); "
            f"got enabled={enabled!r}",
        )

    # --- heat exchanger ----------------------------------------------------
    annular_loop = block.get("annular_loop")
    if annular_loop is not None:
        require_mapping(annular_loop, label, f"{base}.annular_loop", "'annular_loop'")
        unknown = sorted(str(key) for key in annular_loop if key not in _RADIAL_LOOP_KEYS)
        if unknown:
            fail(
                label,
                f"{base}.annular_loop",
                f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_LOOP_KEYS)}",
            )
        hx = annular_loop.get("heat_exchanger")
        hx_enabled = False
        if hx is not None:
            require_mapping(hx, label, f"{base}.annular_loop.heat_exchanger", "'heat_exchanger'")
            unknown = sorted(str(key) for key in hx if key not in _RADIAL_HX_KEYS)
            if unknown:
                fail(
                    label,
                    f"{base}.annular_loop.heat_exchanger",
                    f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_HX_KEYS)}",
                )
            hx_enabled = hx.get("enabled", False)
            if not isinstance(hx_enabled, bool):
                fail(
                    label,
                    f"{base}.annular_loop.heat_exchanger.enabled",
                    f"must be a boolean, got {hx_enabled!r}",
                )
    else:
        hx = None
        hx_enabled = False
    if hx_enabled and not enabled:
        fail(
            label,
            f"{base}.annular_loop.heat_exchanger.enabled",
            f"true requires the intra-channel radial stack enabled AND the "
            f"annular fluid circulating (plan §2.2); got enabled={enabled!r}, "
            f"annular_fluid.mode={mode!r}",
        )
    if hx_enabled and mode != "circulating":
        fail(
            label,
            f"{base}.annular_loop.heat_exchanger.enabled",
            f"true requires annular_fluid.mode='circulating' (plan §2.2); "
            f"got {mode!r}",
        )

    # --- HX model identity (review rev020 Phase 5 item 1;
    #     TASK-20260916-01 P2) --------------------------------------------
    # The plant string must name the model the executable actually selects.
    # The Modelica structural switch is the annularHeatExchangerEnabled
    # boolean (integer annularHeatExchangerCode); the generated
    # annularHeatExchangerModel string is an identity label, never a
    # selector. Enabled names exactly the one implemented
    # finite-conductance prescribed-sink model; disabled carries the
    # canonical "none" (an absent field canonicalizes to it). Every other
    # value refuses fail-closed with the path and the offending value.
    if hx is not None:
        model = hx.get("model")
        if hx_enabled:
            if model != RADIAL_HX_MODEL_FINITE_CONDUCTANCE:
                fail(
                    label,
                    f"{base}.annular_loop.heat_exchanger.model",
                    f"must be exactly "
                    f"'{RADIAL_HX_MODEL_FINITE_CONDUCTANCE}' (the only "
                    f"implemented heat-exchanger model) while "
                    f"heat_exchanger.enabled=true, got {model!r}",
                )
        elif model is not None and model != RADIAL_HX_MODEL_NONE:
            fail(
                label,
                f"{base}.annular_loop.heat_exchanger.model",
                f"must be absent or '{RADIAL_HX_MODEL_NONE}' while "
                f"heat_exchanger.enabled is false (the generated "
                f"annularHeatExchangerModel string must match the "
                f"annularHeatExchangerEnabled boolean), got {model!r}",
            )
    # Loop data (flow, fractions, plena, HX parameters) exists only for a
    # circulating loop (plan §10.12: static mode has no loop/plenum/driver
    # states, so authored loop data would be phantom).
    if annular_loop is not None and not (enabled and mode == "circulating"):
        fail(
            label,
            f"{base}.annular_loop",
            f"annular-loop data requires enabled=true and "
            f"annular_fluid.mode='circulating'; got enabled={enabled!r}, "
            f"mode={mode!r}",
        )

    # --- enabled-only sub-blocks ------------------------------------------
    # channel_meaning/channel_multiplicity describe the physical-channel
    # mapping of an ENABLED stack (review rev019 Phase 2); on a disabled
    # block they would be phantom data (plan §10.12 discipline).
    for key in (
        "geometry",
        "interfaces",
        "heat_deposition",
        "channel_meaning",
        "channel_multiplicity",
    ):
        if key in block and not enabled:
            fail(
                label,
                f"{base}.{key}",
                f"'{key}' data is only meaningful on an enabled radial stack; "
                f"got enabled={enabled!r} (remove the section or set enabled=true)",
            )

    if not enabled:
        return

    # --- enabled stack: physical-channel mapping (review rev019 Phase 2;
    #     TASK-20260915-01 P2) ---------------------------------------------
    meaning = block.get("channel_meaning") or "literal_tube"
    if meaning not in RADIAL_CHANNEL_MEANINGS:
        fail(
            label,
            f"{base}.channel_meaning",
            f"must be one of {list(RADIAL_CHANNEL_MEANINGS)}, got {meaning!r}",
        )
    multiplicity_node = block.get("channel_multiplicity")
    if multiplicity_node is None:
        multiplicity = 1
    else:
        multiplicity = _structural_int(
            multiplicity_node, label, f"{base}.channel_multiplicity"
        )
        _radial_positive_quantity(
            multiplicity_node,
            label,
            f"{base}.channel_multiplicity",
            unit="1",
        )
    if (meaning == "literal_tube") != (multiplicity == 1):
        fail(
            label,
            f"{base}.channel_meaning",
            f"'literal_tube' is exactly one physical tube per modeled coarse "
            f"channel (channel_multiplicity = 1) and 'aggregate_bundle' "
            f"requires channel_multiplicity >= 2; got channel_meaning="
            f"{meaning!r} with channel_multiplicity={multiplicity!r}",
        )

    # --- enabled stack: geometry ------------------------------------------
    geometry = block.get("geometry")
    if geometry is None:
        fail(label, f"{base}.geometry", "missing required geometry on an enabled radial block")
    require_mapping(geometry, label, f"{base}.geometry", "'geometry'")
    unknown = sorted(str(key) for key in geometry if key not in _RADIAL_GEOMETRY_KEYS)
    if unknown:
        fail(
            label,
            f"{base}.geometry",
            f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_GEOMETRY_KEYS)}",
        )
    radii: dict[str, float] = {}
    for key in ("fuel_radius", "pipe_outer_radius", "annulus_outer_radius"):
        radii[key] = _radial_positive_quantity(
            _required_quantity(geometry, key, label, f"{base}.geometry"),
            label,
            f"{base}.geometry.{key}",
            unit="m",
        )
    if not radii["fuel_radius"] < radii["pipe_outer_radius"] < radii["annulus_outer_radius"]:
        fail(
            label,
            f"{base}.geometry.annulus_outer_radius.value",
            f"radii must satisfy 0 < fuel_radius < pipe_outer_radius < "
            f"annulus_outer_radius, got "
            f"fuel_radius={radii['fuel_radius']!r}, "
            f"pipe_outer_radius={radii['pipe_outer_radius']!r}, "
            f"annulus_outer_radius={radii['annulus_outer_radius']!r}",
        )
    # Explicit per-segment physical radial length (review rev019 Phase 2;
    # planner resolution 6): the stack length is stated on the record, never
    # reused from map.dz (moderator center spacing) unless a check proves
    # equality for that core. 1r10seg fails that proof (LF = 0.1575 m vs
    # dz = 0.14 m), so its record carries its own value.
    n_segments = radial_stack_segment_count(plant, core_key)
    length_node = _required_quantity(
        geometry, "physical_length", label, f"{base}.geometry"
    )
    if quantity_unit(length_node) != "m":
        fail(
            label,
            f"{base}.geometry.physical_length.unit",
            f"must be 'm', got {quantity_unit(length_node)!r}",
        )
    lengths_value = quantity_value(length_node)
    if not isinstance(lengths_value, list):
        fail(
            label,
            f"{base}.geometry.physical_length.value",
            f"must be a per-segment array of {n_segments} lengths, "
            f"got {lengths_value!r}",
        )
    if len(lengths_value) != n_segments:
        fail(
            label,
            f"{base}.geometry.physical_length.value",
            f"must be a {n_segments}-entry array (one physical radial length "
            f"per core axial segment, matching n_seg), got {len(lengths_value)} entries",
        )
    physical_lengths: list[float] = []
    for index, item in enumerate(lengths_value):
        physical_lengths.append(
            _radial_positive_quantity(
                {"value": item, "unit": "m"},
                label,
                f"{base}.geometry.physical_length.value[{index}]",
                unit="m",
            )
        )
    if policy == "strict_physical":
        tolerance_node = block.get("volume_tolerance")
        tolerance = (
            RADIAL_VOLUME_TOLERANCE_DEFAULT
            if tolerance_node is None
            else float(quantity_value(tolerance_node))
        )
        # (0) Contract narrowing (review rev020 Phase 4 option 2; planner
        #     resolution 4; TASK-20260916-01 P4): strict_physical claims
        #     complete physical-cell closure. This validator closes fuel
        #     volume and per-tube packing but CANNOT close the aggregate
        #     moderator inventory or the N_c-scaled conduction sections
        #     against the lattice cells (no physical lattice dataset
        #     exists), so strict_physical is refused outright for an
        #     aggregate bundle and for a placeholder lattice;
        #     effective_thermal stays the honest policy for both.
        if meaning == "aggregate_bundle":
            fail(
                label,
                f"{base}.channel_meaning",
                f"strict_physical geometry is refused for "
                f"channel_meaning='aggregate_bundle': the modeled channel "
                f"aggregates channel_multiplicity={multiplicity!r} physical "
                f"tubes, and the validator cannot close the aggregate "
                f"moderator inventory or the N_c-scaled axial conduction "
                f"sections against the N_c lattice cells (review rev020 "
                f"Phase 4 option 2; no physical lattice dataset exists); "
                f"use geometry_policy='effective_thermal', or model one "
                f"physical tube per modeled channel "
                f"(channel_meaning='literal_tube', "
                f"channel_multiplicity=1); got "
                f"channel_meaning='aggregate_bundle'",
            )
        lattice_geometry = (
            map_lattice
            if map_lattice is not None
            else RADIAL_LATTICE_GEOMETRY_DEFAULT
        )
        if lattice_geometry != "physical":
            fail(
                label,
                f"cores.{core_key}.channel_map.lattice_geometry",
                f"strict_physical geometry requires an explicit "
                f"lattice_geometry: physical declaration next to the "
                f"lattice metadata it qualifies; an absent declaration "
                f"canonicalizes to '{RADIAL_LATTICE_GEOMETRY_DEFAULT}' "
                f"(the coarse channel_map pitch/chanR metadata is a "
                f"documented placeholder that does not control heat "
                f"capacity), and a placeholder lattice cannot back a "
                f"strict_physical claim (review rev020 Phase 4 option 2); "
                f"declare "
                f"cores.{core_key}.channel_map.lattice_geometry: physical "
                f"for a real lattice, or use "
                f"geometry_policy='effective_thermal'; got "
                f"lattice_geometry={map_lattice!r} (None = absent), "
                f"canonicalized to {lattice_geometry!r}",
            )
        # (1) The stated physical length must BE the core's authored fuel
        #     length (the channel_geom LF chain) - the check that proves the
        #     record length is the physical fuel length and not a reused
        #     moderator spacing.
        authored_lengths = _radial_authored_fuel_lengths(plant, core_key, label)
        for index, (stated, authored) in enumerate(zip(physical_lengths, authored_lengths)):
            if abs(stated - authored) > tolerance * abs(authored):
                fail(
                    label,
                    f"{base}.geometry.physical_length.value[{index}]",
                    f"strict_physical geometry: the stated per-segment "
                    f"physical radial length {stated!r} m does not match the "
                    f"core's authored fuel length {authored!r} m "
                    f"(channel_geom LF chain) within relative tolerance "
                    f"{tolerance!r}; the stack length must be the core's "
                    f"physical fuel length, not map.dz (moderator center "
                    f"spacing) unless proven equal for this core",
                )
        # (2) Fuel-volume closure WITH the physical-channel multiplicity:
        #     V_fuel = N_c*pi*r_f^2*L_j must reproduce the declared salt
        #     inventory per stack cell.
        for index, (length_j, declared_j) in enumerate(
            _radial_declared_fuel_volumes(plant, core_key, label)
        ):
            geometric = (
                multiplicity
                * math.pi
                * radii["fuel_radius"] ** 2
                * physical_lengths[index % len(physical_lengths)]
            )
            if abs(geometric - declared_j) > tolerance * declared_j:
                fail(
                    label,
                    f"{base}.geometry.fuel_radius.value",
                    f"strict_physical geometry: geometric fuel volume "
                    f"N_c*pi*r_f^2*L_j = {geometric!r} m3 "
                    f"(channel_multiplicity={multiplicity!r}, "
                    f"L_j={physical_lengths[index % len(physical_lengths)]!r} m) "
                    f"does not reproduce the declared salt inventory "
                    f"{declared_j!r} m3 within relative tolerance {tolerance!r}",
                )
        # (3) Lattice packing + (4) complete material-volume closure. The
        #     r9 core carries no plant-level channel_map (its per-zone
        #     lattices live in the Modelica zone adapters, and plant-driven
        #     circulating 9R is refused outright on the P6 fallback), so
        #     the envelope checks run only where the lattice data exist in
        #     the plant deck; the SegmentedCore asserts cover every bound
        #     topology in-model.
        channel_map = core.get("channel_map")
        if isinstance(channel_map, Mapping):
            pitch = _radial_positive_quantity(
                _required_quantity(channel_map, "pitch", label, f"cores.{core_key}.channel_map"),
                label,
                f"cores.{core_key}.channel_map.pitch",
                unit="m",
            )
            envelope = radii["annulus_outer_radius"]
            if 2.0 * envelope > pitch:
                fail(
                    label,
                    f"{base}.geometry.annulus_outer_radius.value",
                    f"strict_physical lattice packing: annulus outer diameter "
                    f"{2.0 * envelope!r} m exceeds the lattice pitch "
                    f"{pitch!r} m (cell inradius {pitch / 2.0!r} m); the annulus "
                    f"cannot fit its lattice cell",
                )
            xy_value = quantity_value(channel_map.get("xy", {"value": []}))
            centers: list[tuple[float, float]] = []
            if isinstance(xy_value, list) and len(xy_value) > 0:
                centers = [
                    (float(point[0]), float(point[1])) for point in xy_value
                ]
            for c in range(len(centers)):
                for d in range(c + 1, len(centers)):
                    distance = math.sqrt(
                        (centers[d][0] - centers[c][0]) ** 2
                        + (centers[d][1] - centers[c][1]) ** 2
                    )
                    if 2.0 * envelope > distance:
                        fail(
                            label,
                            f"{base}.geometry.annulus_outer_radius.value",
                            f"strict_physical lattice packing: channel "
                            f"annulus envelopes overlap (2*annulus_outer_radius "
                            f"{2.0 * envelope!r} m exceeds the center "
                            f"distance {distance!r} m of channels {c}, {d})",
                        )
            pitch_type = str(
                channel_map.get("pitch_type", "Square") or "Square"
            )
            cell_area = (
                pitch * pitch
                if pitch_type == "Square"
                else (math.sqrt(3.0) / 2.0) * pitch * pitch
            )
            # Complete material-volume closure: ONE tube's full radial
            # build-up (fuel + pipe + annular fluid = pi*annOut^2) must fit
            # the represented lattice cell area, leaving a nonnegative
            # moderator area. The bundle's N_c tubes occupy N_c such
            # physical lattice cells (the coarse placeholder cell
            # aggregates them) - the closure is per tube, NOT scaled by
            # channel_multiplicity (review rev019 Phase 2;
            # TASK-20260915-01 P2).
            occupied = math.pi * envelope**2
            if occupied > cell_area:
                fail(
                    label,
                    f"{base}.geometry.annulus_outer_radius.value",
                    f"strict_physical material-volume closure: one tube's "
                    f"full envelope pi*annulus_outer_radius^2 = {occupied!r} "
                    f"m2 exceeds the lattice cell area {cell_area!r} m2 "
                    f"(pitch_type {pitch_type!r}); no moderator area would "
                    f"remain in the tube's lattice cell",
                )
    elif "volume_tolerance" in block:
        fail(
            label,
            f"{base}.volume_tolerance",
            f"volume_tolerance applies only to geometry_policy='strict_physical', "
            f"got policy {policy!r}",
        )

    # --- enabled stack: shared materials ----------------------------------
    materials = plant["materials"]
    for name in _RADIAL_MATERIAL_NAMES:
        if materials.get(name) is None:
            fail(
                label,
                f"materials.{name}",
                f"missing required material section for the enabled radial "
                f"stack on cores.{core_key}",
            )

    # --- enabled stack: interfaces ----------------------------------------
    interfaces = block.get("interfaces")
    if interfaces is None:
        fail(label, f"{base}.interfaces", "missing required interfaces on an enabled radial block")
    require_mapping(interfaces, label, f"{base}.interfaces", "'interfaces'")
    unknown = sorted(str(key) for key in interfaces if key not in _RADIAL_INTERFACE_KEYS)
    if unknown:
        fail(
            label,
            f"{base}.interfaces",
            f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_INTERFACE_KEYS)}",
        )
    for name in ("fuel_pipe", "pipe_fluid", "fluid_moderator"):
        node = interfaces.get(name)
        if node is None:
            fail(
                label,
                f"{base}.interfaces.{name}",
                f"missing required interface on an enabled radial block",
            )
            continue
        require_mapping(node, label, f"{base}.interfaces.{name}", f"interface '{name}'")
        unknown = sorted(str(key) for key in node if key not in ("mode", "h"))
        if unknown:
            fail(
                label,
                f"{base}.interfaces.{name}",
                f"unknown key(s) {unknown}; valid keys: ['h', 'mode']",
            )
        iface_mode = node.get("mode")
        if iface_mode not in RADIAL_INTERFACE_MODES:
            fail(
                label,
                f"{base}.interfaces.{name}.mode",
                f"must be one of {list(RADIAL_INTERFACE_MODES)}, got {iface_mode!r}",
            )
        if iface_mode == "film":
            h_node = node.get("h")
            if h_node is None:
                fail(
                    label,
                    f"{base}.interfaces.{name}.h",
                    f"missing required film coefficient for mode='film'",
                )
            _radial_positive_quantity(h_node, label, f"{base}.interfaces.{name}.h", unit="W/(m2.K)")
        elif "h" in node:
            fail(
                label,
                f"{base}.interfaces.{name}.h",
                f"a film coefficient must not accompany mode='perfect' "
                f"(declare the contact explicitly); got h="
                f"{quantity_value(node['h'])!r}",
            )

    # --- enabled stack: deposition ----------------------------------------
    deposition = block.get("heat_deposition", {})
    require_mapping(deposition, label, f"{base}.heat_deposition", "'heat_deposition'")
    unknown = sorted(str(key) for key in deposition if key not in _RADIAL_DEPOSITION_KEYS)
    if unknown:
        fail(
            label,
            f"{base}.heat_deposition",
            f"unknown key(s) {unknown}; valid keys: {sorted(_RADIAL_DEPOSITION_KEYS)}",
        )
    fractions = 0.0
    for key in ("pipe_fraction", "annular_fluid_fraction"):
        node = deposition.get(key)
        if node is None:
            continue
        value = _radial_positive_quantity(
            node, label, f"{base}.heat_deposition.{key}", unit="1", allow_zero=True
        )
        if value > 1.0:
            fail(
                label,
                f"{base}.heat_deposition.{key}.value",
                f"must be within [0, 1], got {value!r}",
            )
        fractions += value
    total = _radial_deposited_fractions_total(plant, core_key) + fractions
    if abs(total - 1.0) > 1e-9:
        fail(
            label,
            f"{base}.heat_deposition",
            f"deposition fractions must sum to 1: authored fuel+moderator "
            f"share {_radial_deposited_fractions_total(plant, core_key)!r} plus "
            f"radial pipe+annular shares {fractions!r} gives {total!r}",
        )

    # --- circulating loop: valid mass-flow definition (plan §6.4, §9) ------
    if mode == "circulating":
        loop = block.get("annular_loop")
        if loop is None:
            fail(
                label,
                f"{base}.annular_loop",
                "missing required annular_loop section in circulating mode",
            )
        _radial_positive_quantity(
            _required_quantity(loop, "nominal_mass_flow", label, f"{base}.annular_loop"),
            label,
            f"{base}.annular_loop.nominal_mass_flow",
            unit="kg/s",
        )
        max_cmd = _radial_positive_quantity(
            _required_quantity(loop, "max_flow_command", label, f"{base}.annular_loop"),
            label,
            f"{base}.annular_loop.max_flow_command",
            unit="1",
        )
        if max_cmd < 1.0:
            fail(
                label,
                f"{base}.annular_loop.max_flow_command.value",
                f"must be >= 1 so the nominal operating point u=1 stays inside "
                f"the commanded bound, got {max_cmd!r}",
            )
        fractions_node = _required_quantity(
            loop, "channel_flow_fractions", label, f"{base}.annular_loop"
        )
        values = quantity_value(fractions_node)
        n_expected = radial_loop_channel_count(plant, core_key)
        if not isinstance(values, list) or len(values) != n_expected:
            fail(
                label,
                f"{base}.annular_loop.channel_flow_fractions.value",
                f"must be a {n_expected}-entry array (one fraction per annular "
                f"loop channel), got {values!r}",
            )
        for index, item in enumerate(values):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or float(item) < 0:
                fail(
                    label,
                    f"{base}.annular_loop.channel_flow_fractions.value[{index}]",
                    f"must be >= 0, got {item!r}",
                )
        total = sum(float(item) for item in values)
        if abs(total - 1.0) > RADIAL_FRACTION_TOLERANCE:
            fail(
                label,
                f"{base}.annular_loop.channel_flow_fractions.value",
                f"must sum to 1 within {RADIAL_FRACTION_TOLERANCE!r} "
                f"(SegmentedMSR.Core.fracTol), got {total!r}",
            )
        for key in ("supply_plenum_volume", "return_plenum_volume"):
            _radial_positive_quantity(
                _required_quantity(loop, key, label, f"{base}.annular_loop"),
                label,
                f"{base}.annular_loop.{key}",
                unit="m3",
            )
        _radial_positive_quantity(
            _required_quantity(loop, "connecting_pipe_volume", label, f"{base}.annular_loop"),
            label,
            f"{base}.annular_loop.connecting_pipe_volume",
            unit="m3",
            allow_zero=True,
        )
        if hx_enabled:
            hx_node = loop["heat_exchanger"]
            _radial_positive_quantity(
                _required_quantity(hx_node, "ua", label, f"{base}.annular_loop.heat_exchanger"),
                label,
                f"{base}.annular_loop.heat_exchanger.ua",
                unit="W/K",
            )
            _radial_positive_quantity(
                _required_quantity(
                    hx_node, "loop_side_volume", label, f"{base}.annular_loop.heat_exchanger"
                ),
                label,
                f"{base}.annular_loop.heat_exchanger.loop_side_volume",
                unit="m3",
            )
            for key in ("sink_temperature", "initial_temperature"):
                node = _required_quantity(
                    hx_node, key, label, f"{base}.annular_loop.heat_exchanger"
                )
                unit = quantity_unit(node)
                if unit != "degC":
                    fail(
                        label,
                        f"{base}.annular_loop.heat_exchanger.{key}.unit",
                        f"must be 'degC' (plant temperature convention; the "
                        f"segmented emitter converts to kelvin), got {unit!r}",
                    )


def _validate_intra_channel_radial(plant: Mapping[str, Any], label: str) -> None:
    """Validate every core's optional ``intra_channel_radial`` block.

    TASK-20260914-01 P1 (plan §12 Phase A): the three structural decisions
    and their valid combinations, the radial geometry/material/flow/
    distribution/plenum/heat-exchanger schemas, and the labeled refusals
    of plan §9 - all before any Modelica translation. Disabled cores
    (the block absent everywhere in the shipped decks) keep the
    established direct fuel-to-moderator path and default to the disabled
    configuration.
    """

    cores = plant.get("cores")
    if not isinstance(cores, Mapping):
        return
    for core_key in sorted(cores):
        _validate_one_radial_block(plant, str(core_key), label)


def _outer_annulus_scan_forbidden_keys(node: Any, path: str, found: list[str]) -> None:
    """Collect every forbidden identity key under ``node`` (card Constraints)."""

    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key) in _OUTER_ANNULUS_FORBIDDEN_KEYS:
                found.append(child)
            _outer_annulus_scan_forbidden_keys(value, child, found)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _outer_annulus_scan_forbidden_keys(item, f"{path}[{index}]", found)


def _outer_annulus_pinned(
    node: Mapping[str, Any],
    keys: frozenset[str],
    label: str,
    path: str,
) -> None:
    """Refuse unknown keys in a pinned outer-annulus section.

    ``doc_*`` / ``source_*`` free-text provenance siblings are always
    allowed (the P1 decision-record convention).
    """

    unknown = sorted(
        str(key)
        for key in node
        if str(key) not in keys
        and not str(key).startswith(("doc_", "source_"))
    )
    if unknown:
        fail(
            label,
            path,
            f"unknown key(s) {unknown}; valid keys: {sorted(keys)}"
            f" (plus doc_*/source_* provenance siblings)",
        )


def _outer_annulus_optional_quantity(
    section: Mapping[str, Any],
    key: str,
    label: str,
    path: str,
):
    """Sourced quantity node under ``key``, or ``None`` when absent/valueless.

    A valueless node (the P1 ``status: unsupported`` convention: keys other
    than the quantity vocabulary, no ``value``) reads as NOT sourced. A
    non-mapping, non-quantity node is a shape error.
    """

    node = section.get(key)
    if node is None:
        return None
    if isinstance(node, Mapping) and "value" not in node:
        return None
    if not is_quantity(node):
        fail(
            label,
            f"{path}.{key}",
            f"must be a quantity {{value, unit}}, got {type(node).__name__}",
        )
    return node


def _outer_annulus_required_quantity(
    section: Mapping[str, Any],
    key: str,
    label: str,
    path: str,
    *,
    what: str,
):
    """Required sourced quantity on an ENABLED deck (plan §7.1/§7.2).

    Refusals name the configuration path and the offending node: absent,
    ``status: unsupported`` valueless placeholders, ``value: null``
    placeholders, and non-quantity shapes are all refused with distinct
    messages - no enabled configuration may reach Modelica with incomplete
    data.
    """

    node = section.get(key)
    if node is None:
        fail(
            label,
            f"{path}.{key}",
            f"missing required {what} on an enabled outer_fuel_annulus deck",
        )
    if is_quantity(node):
        return node
    if isinstance(node, Mapping):
        if "value" not in node:
            fail(
                label,
                f"{path}.{key}",
                "unsupported placeholder on an enabled deck: the node carries "
                f"no 'value' (keys present: {sorted(node)}); an enabled "
                "configuration must carry the sourced quantity with doc+source "
                "provenance before it reaches Modelica (plan §7.1/§7.2)",
            )
        if node.get("value") is None:
            fail(
                label,
                f"{path}.{key}",
                "null placeholder on an enabled deck: value=null; an enabled "
                "configuration must carry the sourced quantity with doc+source "
                "provenance before it reaches Modelica (plan §7.1/§7.2)",
            )
    fail(
        label,
        f"{path}.{key}",
        f"must be a quantity {{value, unit}}, got {type(node).__name__}",
    )


def _outer_annulus_positive_entries(
    node: Any,
    n_expected: int,
    label: str,
    path: str,
    *,
    unit: str,
    allow_zero: bool = False,
) -> list[float]:
    """Validate a quantity array of exactly ``n_expected`` positive entries."""

    if quantity_unit(node) != unit:
        fail(
            label,
            f"{path}.unit",
            f"must be {unit!r}, got {quantity_unit(node)!r}",
        )
    values = quantity_value(node)
    if not isinstance(values, list):
        fail(
            label,
            f"{path}.value",
            f"must be a {n_expected}-entry array, got {type(values).__name__}",
        )
    if len(values) != n_expected:
        fail(
            label,
            f"{path}.value",
            f"length {len(values)} does not match the required {n_expected} entries",
        )
    numbers = [
        _radial_positive_quantity(
            {"value": item, "unit": unit},
            label,
            f"{path}.value[{index}]",
            unit=unit,
            allow_zero=allow_zero,
        )
        for index, item in enumerate(values)
    ]
    return numbers


def _outer_annulus_core_cell_vol_total(
    plant: Mapping[str, Any],
    core_key: str,
    label: str,
) -> float:
    """Total authored channel-region salt volume of one core [m3]."""

    core = plant["cores"][core_key]
    node = _required_quantity(core, "cell_vol", label, f"cores.{core_key}")
    return _flat_sum(node, label, f"cores.{core_key}.cell_vol")


def _outer_annulus_core_cell_vol_segments(
    plant: Mapping[str, Any],
    core_key: str,
    label: str,
    n_seg: int,
) -> list[float]:
    """Per-segment authored salt volumes of a ONE-channel core [m3].

    The P0 per-elevation split_existing closure compares the carved active
    volumes against these legacy per-segment values. Only defined for a
    one-channel core whose ``cell_vol`` value is a flat per-segment list of
    exactly ``n_seg`` entries (the source data that support the
    per-elevation closure); anything else is refused with the path.
    """

    core = plant["cores"][core_key]
    node = _required_quantity(core, "cell_vol", label, f"cores.{core_key}")
    value = quantity_value(node)
    if not isinstance(value, list) or len(value) != n_seg:
        fail(
            label,
            f"cores.{core_key}.cell_vol.value",
            f"the per-elevation split_existing closure needs a flat "
            f"{n_seg}-entry per-segment cell_vol list on the one-channel "
            f"target core, got {value!r}",
        )
    segments = [float(item) for item in value]
    if any(item <= 0.0 for item in segments):
        fail(
            label,
            f"cores.{core_key}.cell_vol.value",
            f"every per-segment legacy cell volume must be positive, got {segments!r}",
        )
    return segments


def _validate_outer_fuel_annulus(plant: Mapping[str, Any], label: str) -> None:
    """Validate the optional plant-level ``outer_fuel_annulus`` block (plan §7).

    TASK-20260917-01 P7 (plan §7.2, §12.6): strict fail-closed validation of
    the outer-core fuel annulus / reactor vessel / fixed-cavity dataset.
    Absent block -> disabled defaults (the emitter emits nothing). Present
    block: every physics quantity is a provenance-carrying envelope; the
    structural codes form valid plan §6.1 combinations; enabled decks carry
    COMPLETE geometry, topology, provenance, and inventory data before any
    Modelica translation; supported-core activation refuses 9R by name
    (card A12). Every refusal names the full dotted configuration path and
    the offending value.

    Shipped state (P1 decision record, ``enabled: false``): valueless
    ``status: unsupported`` nodes are permitted placeholders, sourced values
    in the always-present sections are validated but inert, and the
    enabled-path-only sections (``geometry``, ``volume_tolerance``,
    ``cavity.mode``/``cavity.loop_surfaces``/``cavity.enabled``) are
    refused as phantom data.

    Annulus fission (plan §5.1/§5.2; TASK-20260918-01 P1): an optional
    ``fission`` block. Absent -> fission disabled with zero fraction vectors
    and the first-release policies. Present: finite/nonnegative entries,
    exact ``n_seg`` lengths, STRICT retained-share ``1-fsum(f) >= 1e-9``
    (``1 - math.fsum(f) >= OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE``)
    on BOTH fraction vectors, elementwise ``f^S = f^Q`` under ``local_same_fraction`` (the
    only first-release coupling policy) PLUS the per-segment
    same-active-branch requirement (TASK-20260923-01 P2: exact-zero branch
    semantics - one-sided positive/zero pairs refuse even inside the
    equality tolerance), nonzero fractions illegal whenever
    the annulus or the fission feature is disabled, unsupported
    feedback/exposure policy names refused, complete fields + provenance on
    any fission-enabled deck, and sourced weighted-policy
    weights/coefficient placeholders refused (unimplemented in the first
    release). No silent renormalization; every refusal names the path and
    the offending value.
    """

    block = plant.get(OUTER_ANNULUS_BLOCK_KEY)
    if block is None:
        return
    base = OUTER_ANNULUS_BLOCK_KEY
    require_mapping(block, label, base, "plant section 'outer_fuel_annulus'")

    # --- forbidden-identity namespace scan (before key pinning, so the
    #     message names the feature-conflict rule, not just "unknown key") ---
    forbidden: list[str] = []
    _outer_annulus_scan_forbidden_keys(block, base, forbidden)
    if forbidden:
        fail(
            label,
            forbidden[0],
            "reuses a forbidden intra-channel-radial / annular-loop identity "
            "key (card Constraints: the outer annulus shares no names, keys, "
            "commands, or fingerprints with the intra-channel radial stack or "
            "its dedicated loop)",
        )

    # --- key pinning + quantity envelopes (shape/unit/finiteness only) -----
    unknown = sorted(
        str(key)
        for key in block
        if str(key) not in _OUTER_ANNULUS_TOP_KEYS
        and not str(key).startswith(("doc_", "source_", "axial_grid_"))
    )
    if unknown:
        fail(
            label,
            base,
            f"unknown key(s) {unknown}; valid keys: {sorted(_OUTER_ANNULUS_TOP_KEYS)}"
            " (plus doc_*/source_* siblings and axial_grid_* decision-record"
            " sections)",
        )
    check_quantity_tree(block, label, base)

    enabled = block.get("enabled", False)
    if not isinstance(enabled, bool):
        fail(label, f"{base}.enabled", f"must be a boolean, got {enabled!r}")

    # --- first production target + supported-core refusal (BEFORE the
    #     enabled-provenance walk and every data-completeness check, so an
    #     unsupported core is named even on an otherwise-incomplete deck) ---
    target = block.get("first_production_target")
    if not isinstance(target, str) or not target.strip():
        fail(
            label,
            f"{base}.first_production_target",
            f"must be a non-empty core key (the generated "
            f"SegmentedMSR_PlantData.OuterFuelAnnulus* package is keyed by "
            f"it), got {target!r}",
        )
    cores = plant.get("cores")
    if not isinstance(cores, Mapping) or target not in cores:
        fail(
            label,
            f"{base}.first_production_target",
            f"names core {target!r} which is not a section of the plant deck"
            f" (known: {sorted(cores) if isinstance(cores, Mapping) else 'none'})",
        )
    if enabled and target not in OUTER_ANNULUS_SUPPORTED_CORES:
        if target == "r9":
            fail(
                label,
                f"{base}.first_production_target",
                "unsupported core: plant-driven outer-annulus activation of "
                "cores.r9 (9R) is refused fail-closed (plan §3.1; card A12): "
                "the four 9R zone chains have no sourced physical-elevation "
                "and zone-to-outer-boundary mapping, so no boundary "
                "conductance dataset exists; only the 1R-family cores "
                f"{list(OUTER_ANNULUS_SUPPORTED_CORES)} accept an enabled "
                f"outer_fuel_annulus dataset; got {target!r}",
            )
        fail(
            label,
            f"{base}.first_production_target",
            f"unsupported core {target!r} for an enabled outer_fuel_annulus "
            f"dataset; supported cores: {list(OUTER_ANNULUS_SUPPORTED_CORES)}",
        )
    n_chan = radial_loop_channel_count(plant, target)
    n_seg = radial_stack_segment_count(plant, target)

    # --- enabled decks: complete provenance on EVERY quantity in the block
    #     (plan §7.2); runs AFTER the path-specific completeness checks below
    #     so those named refusals win over the bulk walk ---------------------
    if enabled:
        check_quantity_tree(block, label, base, require_provenance=True)

    # --- identity + governance ---------------------------------------------
    dataset_id = block.get("dataset_id")
    if dataset_id is not None:
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            fail(
                label,
                f"{base}.dataset_id",
                f"must be a non-empty string, got {dataset_id!r}",
            )
    elif enabled:
        fail(label, f"{base}.dataset_id", "missing required dataset identity on an enabled deck")
    maturity = block.get("maturity")
    if maturity is not None:
        if not isinstance(maturity, str) or maturity not in CORE_MATURITY_VALUES:
            fail(
                label,
                f"{base}.maturity",
                f"must be one of {list(CORE_MATURITY_VALUES)}, got {maturity!r}",
            )
    elif enabled:
        fail(
            label,
            f"{base}.maturity",
            "missing required maturity label on an enabled deck "
            f"(one of {list(CORE_MATURITY_VALUES)})",
        )
    policy = block.get("geometry_policy")
    if policy is None:
        policy = "strict_physical"
    if policy not in OUTER_ANNULUS_GEOMETRY_POLICIES:
        fail(
            label,
            f"{base}.geometry_policy",
            f"must be one of {list(OUTER_ANNULUS_GEOMETRY_POLICIES)}, got {policy!r}",
        )
    enablement = block.get("production_enablement")
    enablement_status = ""
    if enablement is not None:
        require_mapping(
            enablement, label, f"{base}.production_enablement", "'production_enablement'"
        )
        _outer_annulus_pinned(
            enablement,
            _OUTER_ANNULUS_ENABLEMENT_KEYS,
            label,
            f"{base}.production_enablement",
        )
        status = enablement.get("status")
        if status not in OUTER_ANNULUS_ENABLEMENT_STATUSES:
            fail(
                label,
                f"{base}.production_enablement.status",
                f"must be one of {list(OUTER_ANNULUS_ENABLEMENT_STATUSES)}, got {status!r}",
            )
        enablement_status = str(status)
        reasons = enablement.get("reasons")
        if reasons is not None and (
            not isinstance(reasons, list)
            or any(not isinstance(item, str) or not item.strip() for item in reasons)
        ):
            fail(
                label,
                f"{base}.production_enablement.reasons",
                "must be a list of non-empty strings, got {reasons!r}".format(reasons=reasons),
            )
        if enabled and enablement_status != "enabled":
            fail(
                label,
                f"{base}.production_enablement.status",
                f"cannot enable the dataset while the recorded enablement "
                f"status is {enablement_status!r}: resolve the recorded "
                f"blockers (sourced geometry, topology, cavity, and vessel "
                f"data) and record status: enabled first",
            )
    elif enabled:
        fail(
            label,
            f"{base}.production_enablement",
            "missing required production-enablement record on an enabled deck",
        )

    # --- topology (plan §6.1 structural codes) ------------------------------
    topology = block.get("topology")
    if topology is None:
        fail(label, f"{base}.topology", "missing required topology section")
    require_mapping(topology, label, f"{base}.topology", "'topology'")
    _outer_annulus_pinned(topology, _OUTER_ANNULUS_TOPOLOGY_KEYS, label, f"{base}.topology")
    series = topology.get("series_location_code")
    if isinstance(series, bool) or not isinstance(series, int) or series not in OUTER_ANNULUS_SERIES_CODES:
        fail(
            label,
            f"{base}.topology.series_location_code",
            f"must be one of {list(OUTER_ANNULUS_SERIES_CODES)} (plan §6.1), got {series!r}",
        )
    if not enabled and series != OUTER_ANNULUS_DISABLED_SERIES_CODE:
        fail(
            label,
            f"{base}.topology.series_location_code",
            f"a disabled outer_fuel_annulus deck records the disabled series "
            f"code {OUTER_ANNULUS_DISABLED_SERIES_CODE} (plan §6.1); got "
            f"{series!r} with enabled=false",
        )
    if enabled and series == OUTER_ANNULUS_DISABLED_SERIES_CODE:
        fail(
            label,
            f"{base}.topology.series_location_code",
            "code 1 is the DISABLED topology (plan §6.1): an enabled deck "
            "records 2 (active core followed by the outer annulus) or 3 "
            f"(outer annulus followed by the active core); got {series!r}",
        )
    direction = topology.get("flow_direction_code")
    if direction is not None:
        if (
            isinstance(direction, bool)
            or not isinstance(direction, int)
            or direction not in OUTER_ANNULUS_DIRECTION_CODES
        ):
            fail(
                label,
                f"{base}.topology.flow_direction_code",
                f"must be one of {list(OUTER_ANNULUS_DIRECTION_CODES)} (plan §6.1), got {direction!r}",
            )
    elif enabled:
        fail(
            label,
            f"{base}.topology.flow_direction_code",
            "missing required flow direction on an enabled deck (plan §6.1 "
            "direction codes 1 = segment 1 -> nSeg or 2 = nSeg -> 1); the "
            "valueless unsupported marker is a placeholder a production "
            "dataset may not carry",
        )

    # --- axial-grid consistency for the target core (both states, when the
    #     matching decision-record section is authored) ----------------------
    # Canonical section spelling: ``axial_grid_<core-tag>`` with the
    # user-facing core tag of the target core
    # (OUTER_ANNULUS_AXIAL_GRID_CORE_TAGS: r1_10seg -> 1r10seg) -- the
    # spelling the shipped deck, the schema, and the catalog author
    # (``axial_grid_1r10seg``). The bare YAML core-key spelling
    # (``axial_grid_r1_10seg``) stays accepted as an alias so a deck authored
    # either way still hits this check; each authored spelling is validated
    # and every refusal names the authored configuration path. Sections for
    # other cores stay inert decision-record data.
    target_key = str(target)
    core_tag = OUTER_ANNULUS_AXIAL_GRID_CORE_TAGS.get(target_key, target_key)
    grid_suffixes = [core_tag] if core_tag == target_key else [core_tag, target_key]
    grid_sections: list[tuple[str, Mapping[str, Any]]] = []
    for suffix in grid_suffixes:
        section_name = f"axial_grid_{suffix}"
        node = block.get(section_name)
        if isinstance(node, Mapping):
            grid_sections.append((section_name, node))
    for grid_name, grid in grid_sections:
        grid_n_seg = grid.get("n_seg")
        if isinstance(grid_n_seg, int) and not isinstance(grid_n_seg, bool):
            if grid_n_seg != n_seg:
                fail(
                    label,
                    f"{base}.{grid_name}.n_seg",
                    f"does not match the target core's n_seg "
                    f"(cores.{target}.n_seg = {n_seg}); got {grid_n_seg!r}",
                )
        lf_node = _outer_annulus_optional_quantity(grid, "lf_per_segment", label, f"{base}.{grid_name}")
        if lf_node is not None:
            if quantity_unit(lf_node) != "m":
                fail(
                    label,
                    f"{base}.{grid_name}.lf_per_segment.unit",
                    f"must be 'm', got {quantity_unit(lf_node)!r}",
                )
            lf_value = quantity_value(lf_node)
            if isinstance(lf_value, list) or isinstance(lf_value, bool) or not isinstance(lf_value, (int, float)):
                fail(
                    label,
                    f"{base}.{grid_name}.lf_per_segment.value",
                    f"must be a scalar per-segment length, got {lf_value!r}",
                )
            authored_lengths = _radial_authored_fuel_lengths(plant, target, label)
            expected_total = sum(authored_lengths)
            authored_total = float(lf_value) * n_seg
            if abs(authored_total - expected_total) > 1e-9:
                fail(
                    label,
                    f"{base}.{grid_name}.lf_per_segment.value",
                    f"per-segment length {float(lf_value)!r} m x {n_seg} "
                    f"segments = {authored_total!r} m does not match the "
                    f"target core's authored fuel-length chain "
                    f"(cores.{target} LF sum {expected_total!r} m) within 1e-9",
                )

    # --- enabled-path-only sections are phantom data on a disabled deck -----
    for key in ("geometry", "volume_tolerance"):
        if key in block and not enabled:
            fail(
                label,
                f"{base}.{key}",
                f"enabled-path data on a disabled outer_fuel_annulus deck: "
                f"'{key}' is only meaningful when enabled=true; remove the "
                f"section or set enabled=true; got enabled={enabled!r}",
            )

    # --- geometry (plan §7.1/§7.2) ------------------------------------------
    radii: dict[str, float] = {}
    physical_lengths: list[float] = []
    annulus_volumes: list[float] = []
    if "geometry" in block:
        geometry = block["geometry"]
        require_mapping(geometry, label, f"{base}.geometry", "'geometry'")
        _outer_annulus_pinned(geometry, _OUTER_ANNULUS_GEOMETRY_KEYS, label, f"{base}.geometry")
        for key in ("graphite_outer_radius", "vessel_inner_radius", "vessel_outer_radius"):
            node = _outer_annulus_optional_quantity(geometry, key, label, f"{base}.geometry")
            if node is None:
                if enabled:
                    _outer_annulus_required_quantity(
                        geometry, key, label, f"{base}.geometry", what=key
                    )
                continue
            radii[key] = _radial_positive_quantity(
                node, label, f"{base}.geometry.{key}", unit="m"
            )
        if len(radii) == 3 and not (
            0.0 < radii["graphite_outer_radius"]
            < radii["vessel_inner_radius"]
            < radii["vessel_outer_radius"]
        ):
            fail(
                label,
                f"{base}.geometry.vessel_outer_radius.value",
                "radii must satisfy 0 < graphite_outer_radius < "
                "vessel_inner_radius < vessel_outer_radius, got "
                f"graphite_outer_radius={radii['graphite_outer_radius']!r}, "
                f"vessel_inner_radius={radii['vessel_inner_radius']!r}, "
                f"vessel_outer_radius={radii['vessel_outer_radius']!r}",
            )
        length_node = _outer_annulus_optional_quantity(
            geometry, "physical_length", label, f"{base}.geometry"
        )
        if length_node is not None:
            physical_lengths = _outer_annulus_positive_entries(
                length_node, n_seg, label, f"{base}.geometry.physical_length", unit="m"
            )
        elif enabled:
            _outer_annulus_required_quantity(
                geometry, "physical_length", label, f"{base}.geometry", what="per-segment physical length"
            )
        ann_vol_node = _outer_annulus_optional_quantity(
            geometry, "annulus_volume", label, f"{base}.geometry"
        )
        if ann_vol_node is not None:
            if policy == "strict_physical":
                fail(
                    label,
                    f"{base}.geometry.annulus_volume",
                    "authored annulus volumes are refused under "
                    "geometry_policy='strict_physical' (the cylindrical law "
                    "V_j = pi*(r_vi^2 - r_g^2)*L_j is authoritative; plan "
                    "§7.2 strict-physical volume closure); authored volumes "
                    "are an effective_thermal disclosure only",
                )
            annulus_volumes = _outer_annulus_positive_entries(
                ann_vol_node, n_seg, label, f"{base}.geometry.annulus_volume", unit="m3", allow_zero=False
            )
        elif enabled and policy == "effective_thermal":
            # P0 (TASK-20260918-01; plan §6 Phase 0): under effective_thermal
            # the authored per-segment volumes ARE the executable cell
            # storage volume (the thermal cells' mass, heat capacity, stored
            # energy, and decay weighting consume them; the Modelica assembly
            # refuses zero entries) - so they are REQUIRED data, not an
            # optional disclosure.
            _outer_annulus_required_quantity(
                geometry, "annulus_volume", label, f"{base}.geometry",
                what="authored per-segment annulus storage volume",
            )
    elif enabled:
        fail(label, f"{base}.geometry", "missing required geometry on an enabled outer_fuel_annulus deck")
    if enabled and "volume_tolerance" in block:
        _radial_positive_quantity(
            block["volume_tolerance"],
            label,
            f"{base}.volume_tolerance",
            unit="1",
            allow_zero=False,
        )
        if policy == "effective_thermal":
            fail(
                label,
                f"{base}.volume_tolerance",
                "volume_tolerance applies only to "
                "geometry_policy='strict_physical', got policy "
                f"{policy!r}",
            )

    # Geometric volumes from the ordered radii and lengths (both states when
    # the data are sourced; positive annulus and vessel volumes, plan §7.2).
    annulus_volumes_final: list[float] | None = None
    if len(radii) == 3 and physical_lengths:
        geometric = [
            math.pi * (radii["vessel_inner_radius"] ** 2 - radii["graphite_outer_radius"] ** 2) * length_j
            for length_j in physical_lengths
        ]
        if any(volume <= 0.0 for volume in geometric):
            fail(
                label,
                f"{base}.geometry.vessel_inner_radius.value",
                "the cylindrical annulus volumes pi*(r_vi^2 - r_g^2)*L_j must "
                f"be positive, got {geometric!r} from radii {radii!r}",
            )
        vessel_volumes = [
            math.pi * (radii["vessel_outer_radius"] ** 2 - radii["vessel_inner_radius"] ** 2) * length_j
            for length_j in physical_lengths
        ]
        if any(volume <= 0.0 for volume in vessel_volumes):
            fail(
                label,
                f"{base}.geometry.vessel_outer_radius.value",
                "the cylindrical vessel volumes pi*(r_vo^2 - r_vi^2)*L_j must "
                f"be positive, got {vessel_volumes!r} from radii {radii!r}",
            )
        annulus_volumes_final = geometric
    if annulus_volumes:
        annulus_volumes_final = annulus_volumes

    # --- moderator coupling + interfaces ------------------------------------
    moderator = block.get("moderator_coupling")
    if moderator is None:
        moderator = {}
    require_mapping(moderator, label, f"{base}.moderator_coupling", "'moderator_coupling'")
    _outer_annulus_pinned(
        moderator, _OUTER_ANNULUS_MODCOUPLING_KEYS, label, f"{base}.moderator_coupling"
    )
    mapping_policy = moderator.get("mapping_policy", "authored_matrix")
    if mapping_policy != "authored_matrix":
        fail(
            label,
            f"{base}.moderator_coupling.mapping_policy",
            "must be 'authored_matrix' (the ONLY supported policy: the "
            "G_modAnn boundary conductances are authored or derived from a "
            "separately reviewed graphite outer-boundary geometry, NEVER "
            "inferred from the inter-channel ChannelMap neighbor table - "
            f"plan §4.2; the P1 record states authored_matrix), got {mapping_policy!r}",
        )
    conductance_node = _outer_annulus_optional_quantity(
        moderator, "conductance", label, f"{base}.moderator_coupling"
    )
    conductance_values: list[list[float]] | None = None
    if conductance_node is not None:
        _matrix_shape(
            conductance_node, n_chan, n_seg, label, f"{base}.moderator_coupling.conductance"
        )
        conductance_values = [
            [
                _radial_positive_quantity(
                    {"value": cell, "unit": "W/K"},
                    label,
                    f"{base}.moderator_coupling.conductance.value[{r}][{c}]",
                    unit="W/K",
                    allow_zero=True,
                )
                for c, cell in enumerate(row)
            ]
            for r, row in enumerate(quantity_value(conductance_node))
        ]

    interfaces = block.get("interfaces")
    if interfaces is None:
        if enabled:
            fail(label, f"{base}.interfaces", "missing required interfaces on an enabled outer_fuel_annulus deck")
        interfaces = {}
    require_mapping(interfaces, label, f"{base}.interfaces", "'interfaces'")
    _outer_annulus_pinned(interfaces, _OUTER_ANNULUS_INTERFACE_KEYS, label, f"{base}.interfaces")
    graphite_iface = interfaces.get("graphite_to_fuel")
    if graphite_iface is None and enabled:
        fail(
            label,
            f"{base}.interfaces.graphite_to_fuel",
            "missing required graphite-to-annulus interface on an enabled deck",
        )
    if graphite_iface is not None:
        require_mapping(
            graphite_iface, label, f"{base}.interfaces.graphite_to_fuel", "interface 'graphite_to_fuel'"
        )
        _outer_annulus_pinned(
            graphite_iface, frozenset({"mode", "h"}), label, f"{base}.interfaces.graphite_to_fuel"
        )
        graphite_mode = graphite_iface.get("mode", "film")
        if graphite_mode not in OUTER_ANNULUS_GRAPHITE_INTERFACE_MODES:
            fail(
                label,
                f"{base}.interfaces.graphite_to_fuel.mode",
                f"must be one of {list(OUTER_ANNULUS_GRAPHITE_INTERFACE_MODES)}, got {graphite_mode!r}",
            )
        # P0 (TASK-20260918-01; plan §6 Phase 0): the Python and Modelica
        # contracts must AGREE on the supported interface modes. The
        # Modelica assembly (CoreVesselAssembly) refuses the film modes -
        # the coefficients are unsourced and no film interface is
        # implemented - so an ENABLED deck may not declare one here either
        # (a deck that passed Python and failed in Modelica is exactly the
        # disagreement this closes). Film stays a documented future
        # extension; the shipped DISABLED deck keeps its placeholder.
        if enabled and graphite_mode == "film":
            fail(
                label,
                f"{base}.interfaces.graphite_to_fuel.mode",
                "mode='film' is unsupported on an enabled deck: no film "
                "interface is implemented in the Modelica assembly "
                "(CoreVesselAssembly refuses graphiteAnnulusInterfaceCode = "
                "1) and the film coefficient is unsourced - an enabled deck "
                "declares mode='authored_matrix' with the sourced G_modAnn "
                "conductance matrix; film is a documented future extension "
                "(plan §6 Phase 0)",
            )
        h_node = _outer_annulus_optional_quantity(
            graphite_iface, "h", label, f"{base}.interfaces.graphite_to_fuel"
        )
        h_graphite = (
            _radial_positive_quantity(
                h_node, label, f"{base}.interfaces.graphite_to_fuel.h", unit="W/(m2.K)"
            )
            if h_node is not None
            else 0.0
        )
        if enabled:
            if graphite_mode == "authored_matrix":
                if conductance_values is None:
                    fail(
                        label,
                        f"{base}.moderator_coupling.conductance",
                        "missing required boundary-conductance matrix on an "
                        "enabled deck with the authored_matrix interface mode "
                        "(plan §4.2: G_modAnn is authored, never inferred)",
                    )
                if not any(any(cell > 0.0 for cell in row) for row in conductance_values):
                    fail(
                        label,
                        f"{base}.moderator_coupling.conductance.value",
                        "an enabled outer annulus needs at least one positive "
                        "G_modAnn entry (interior channels of a multi-channel "
                        "core may be zero, but a fully-zero matrix couples "
                        "nothing); got all-zero",
                    )
                if h_graphite > 0.0:
                    fail(
                        label,
                        f"{base}.interfaces.graphite_to_fuel.h",
                        "a film coefficient must not accompany the "
                        "authored_matrix interface mode (the authored matrix "
                        "IS the graphite-to-annulus conductance; declare ONE "
                        f"parameterization); got h={h_graphite!r}",
                    )
            else:  # film
                # Unreachable for a validated enabled deck: the P0 refusal
                # above rejects enabled film decks before this branch (no
                # film interface is implemented in the Modelica assembly).
                # A DISABLED deck may keep the film placeholder with a
                # valueless h (the shipped decision-record convention).
                pass
    fuel_vessel_iface = interfaces.get("fuel_to_vessel")
    if fuel_vessel_iface is None and enabled:
        fail(
            label,
            f"{base}.interfaces.fuel_to_vessel",
            "missing required annulus-to-vessel interface on an enabled deck",
        )
    if fuel_vessel_iface is not None:
        require_mapping(
            fuel_vessel_iface, label, f"{base}.interfaces.fuel_to_vessel", "interface 'fuel_to_vessel'"
        )
        _outer_annulus_pinned(
            fuel_vessel_iface, frozenset({"mode", "h"}), label, f"{base}.interfaces.fuel_to_vessel"
        )
        fv_mode = fuel_vessel_iface.get("mode", "perfect")
        if fv_mode not in OUTER_ANNULUS_CONTACT_MODES:
            fail(
                label,
                f"{base}.interfaces.fuel_to_vessel.mode",
                f"must be one of {list(OUTER_ANNULUS_CONTACT_MODES)}, got {fv_mode!r}",
            )
        # P0: same enabled-deck alignment as graphite_to_fuel above - the
        # Modelica assembly refuses the annulus/vessel film mode
        # (annulusVesselInterfaceCode = 1), so an enabled deck may not
        # declare it (film = documented future extension).
        if enabled and fv_mode == "film":
            fail(
                label,
                f"{base}.interfaces.fuel_to_vessel.mode",
                "mode='film' is unsupported on an enabled deck: no film "
                "interface is implemented in the Modelica assembly "
                "(CoreVesselAssembly refuses annulusVesselInterfaceCode = "
                "1) and the film coefficient is unsourced - an enabled deck "
                "declares mode='perfect' (the perfect-contact port pair); "
                "film is a documented future extension (plan §6 Phase 0)",
            )
        fv_h_node = _outer_annulus_optional_quantity(
            fuel_vessel_iface, "h", label, f"{base}.interfaces.fuel_to_vessel"
        )
        fv_h = (
            _radial_positive_quantity(
                fv_h_node, label, f"{base}.interfaces.fuel_to_vessel.h", unit="W/(m2.K)"
            )
            if fv_h_node is not None
            else 0.0
        )
        if fv_mode == "film":
            # Unreachable for a validated enabled deck: the P0 refusal above
            # rejects enabled film decks (no film interface is implemented
            # in the Modelica assembly). A DISABLED deck may keep the film
            # placeholder with a valueless h.
            pass
        elif fv_h_node is not None:
            fail(
                label,
                f"{base}.interfaces.fuel_to_vessel.h",
                "a film coefficient must not accompany mode='perfect' (the "
                "annulus outer surface and the vessel inner surface are ONE "
                "connected port pair; declare the contact explicitly); got "
                f"h={fv_h!r}",
            )

    # --- vessel material ------------------------------------------------------
    vessel_material = block.get("vessel_material")
    if vessel_material is None:
        if enabled:
            fail(
                label,
                f"{base}.vessel_material",
                "missing required vessel-material section on an enabled deck",
            )
        vessel_material = {}
    require_mapping(
        vessel_material, label, f"{base}.vessel_material", "'vessel_material'"
    )
    _outer_annulus_pinned(
        vessel_material, _OUTER_ANNULUS_VESSEL_KEYS, label, f"{base}.vessel_material"
    )
    identity = vessel_material.get("identity")
    if identity is not None and (not isinstance(identity, str) or not identity.strip()):
        fail(
            label,
            f"{base}.vessel_material.identity",
            f"must be a non-empty string, got {identity!r}",
        )
    vessel_props: dict[str, float] = {}
    for key, unit in (("rho", "kg/m3"), ("cp", "J/(kg.K)"), ("k", "W/(m.K)")):
        node = _outer_annulus_optional_quantity(vessel_material, key, label, f"{base}.vessel_material")
        if node is None:
            if enabled:
                _outer_annulus_required_quantity(
                    vessel_material, key, label, f"{base}.vessel_material", what=key
                )
            continue
        vessel_props[key] = _radial_positive_quantity(
            node, label, f"{base}.vessel_material.{key}", unit=unit
        )

    # --- annulus fuel conductivity (REV-21c79f7-01; TASK-20260918-01) --------
    # The annulus cells' radial resistance consumes the PRIMARY FUEL
    # conductivity. This is a separate datum from the generated
    # Materials.kFuel placeholder (0 = axial salt conduction off,
    # PLAN23-08): an enabled deck must carry a strictly positive sourced
    # value or the assembly's annular-conduction resistance aborts at
    # initialization. The shipped disabled deck records the valueless
    # `status: unsupported` placeholder; sourced values on a disabled deck
    # are validated but inert.
    fuel_k_node = _outer_annulus_optional_quantity(
        block, "fuel_conductivity", label, base
    )
    fuel_k: float | None = None
    if fuel_k_node is not None:
        if quantity_unit(fuel_k_node) != "W/(m.K)":
            fail(
                label,
                f"{base}.fuel_conductivity.unit",
                "must be 'W/(m.K)', got "
                f"{quantity_unit(fuel_k_node)!r}",
            )
        raw = quantity_value(fuel_k_node)
        if isinstance(raw, list) or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            fail(
                label,
                f"{base}.fuel_conductivity.value",
                f"must be a finite scalar, got {raw!r}",
            )
        fuel_k = float(raw)
        if enabled and fuel_k <= 0.0:
            fail(
                label,
                f"{base}.fuel_conductivity.value",
                "the enabled deck's annulus radial resistance consumes the "
                "primary-fuel conductivity: fuel_conductivity must be > 0 "
                f"(a sourced physical value), got {fuel_k!r} W/(m.K). The "
                "generated Materials.kFuel = 0 placeholder records the "
                "AXIAL salt conduction being off (PLAN23-08) and must never "
                "drive the annulus radial resistance",
            )
        if not enabled and fuel_k < 0.0:
            fail(
                label,
                f"{base}.fuel_conductivity.value",
                f"must be >= 0 on a disabled deck (inert placeholder data), got {fuel_k!r}",
            )
    elif enabled:
        fail(
            label,
            f"{base}.fuel_conductivity",
            "missing required fuel_conductivity on an enabled deck: the "
            "annulus cells' radial resistance consumes the primary-fuel "
            "conductivity (a sourced value in W/(m.K); the generated "
            "Materials.kFuel = 0 axial-conduction-off placeholder is not a "
            "radial datum and must never drive an enabled annulus)",
        )

    # --- cavity (plan §5.7, §9.2; card: kelvin after conversion) -------------
    cavity = block.get("cavity")
    if cavity is None:
        if enabled:
            fail(label, f"{base}.cavity", "missing required cavity section on an enabled deck")
        cavity = {}
    require_mapping(cavity, label, f"{base}.cavity", "'cavity'")
    _outer_annulus_pinned(cavity, _OUTER_ANNULUS_CAVITY_KEYS, label, f"{base}.cavity")
    cavity_enabled = bool(cavity.get("enabled", False))
    if cavity_enabled and not enabled:
        fail(
            label,
            f"{base}.cavity.enabled",
            "the fixed-temperature cavity requires the outer_fuel_annulus "
            f"enabled; got cavity.enabled=true with enabled={enabled!r}",
        )
    cavity_temperature = None
    cavity_mode = None
    cavity_ua: list[float] = []
    cavity_area: list[float] = []
    cavity_eps: list[float] = []
    loop_surfaces: list[str] = []
    temperature_node = _outer_annulus_optional_quantity(cavity, "temperature", label, f"{base}.cavity")
    if temperature_node is not None:
        if quantity_unit(temperature_node) != "degC":
            fail(
                label,
                f"{base}.cavity.temperature.unit",
                "must be 'degC' (the plant temperature convention; the "
                "segmented emitter converts to kelvin - plan §7.2), got "
                f"{quantity_unit(temperature_node)!r}",
            )
        cavity_temperature = float(quantity_value(temperature_node))
        if cavity_temperature + 273.15 <= 0.0:
            fail(
                label,
                f"{base}.cavity.temperature.value",
                "the fixed cavity temperature must be positive in kelvin "
                f"after conversion, got {cavity_temperature!r} degC",
            )
    mode = cavity.get("mode")
    surfaces = cavity.get("loop_surfaces")
    for key, node in (
        ("vessel_UA", cavity.get("vessel_UA")),
        ("vessel_area", cavity.get("vessel_area")),
        ("vessel_effective_emissivity", cavity.get("vessel_effective_emissivity")),
    ):
        if node is None or (isinstance(node, Mapping) and "value" not in node):
            continue
        if not enabled or not cavity_enabled:
            fail(
                label,
                f"{base}.cavity.{key}",
                "sourced cavity-surface data requires the fixed-temperature "
                "cavity enabled (cavity.enabled=true); sourced values on an "
                "adiabatic vessel exterior are phantom data",
            )
    if not enabled and (mode is not None or surfaces is not None):
        fail(
            label,
            f"{base}.cavity",
            "enabled-path data on a disabled outer_fuel_annulus deck: "
            "'cavity.mode' / 'cavity.loop_surfaces' are only meaningful when "
            f"enabled=true; got enabled={enabled!r}",
        )
    if cavity_enabled:
        if temperature_node is None:
            _outer_annulus_required_quantity(
                cavity, "temperature", label, f"{base}.cavity", what="fixed cavity temperature"
            )
        if mode is None:
            fail(
                label,
                f"{base}.cavity.mode",
                "missing required vessel-to-cavity transfer mode on an "
                f"enabled cavity (one of {list(OUTER_ANNULUS_CAVITY_MODES)})",
            )
        if mode not in OUTER_ANNULUS_CAVITY_MODES:
            fail(
                label,
                f"{base}.cavity.mode",
                f"must be one of {list(OUTER_ANNULUS_CAVITY_MODES)}, got {mode!r}",
            )
        cavity_mode = mode
        uses_ua = mode in ("ua", "ua_and_radiation")
        uses_radiation = mode in ("radiation", "ua_and_radiation")
        for key, required_flag, unit in (
            ("vessel_UA", uses_ua, "W/K"),
            ("vessel_area", uses_radiation, "m2"),
            ("vessel_effective_emissivity", uses_radiation, "1"),
        ):
            node = _outer_annulus_optional_quantity(cavity, key, label, f"{base}.cavity")
            if node is None:
                if required_flag:
                    fail(
                        label,
                        f"{base}.cavity.{key}",
                        f"missing required per-segment {key} array for cavity "
                        f"mode={mode!r} (plan §7.2: no heat-transfer mode with "
                        "missing required parameters)",
                    )
                continue
            if not required_flag:
                fail(
                    label,
                    f"{base}.cavity.{key}",
                    f"authored {key} under cavity mode={mode!r} is ambiguous "
                    "(zero/absent parameters for disabled modes where "
                    "ambiguity would otherwise result - plan §7.2); remove "
                    "the values or change the mode",
                )
            entries = _outer_annulus_positive_entries(
                node, n_seg, label, f"{base}.cavity.{key}", unit=unit, allow_zero=True
            )
            if key == "vessel_UA":
                cavity_ua = entries
            elif key == "vessel_area":
                cavity_area = entries
            else:
                for index, entry in enumerate(entries):
                    if entry > 1.0:
                        fail(
                            label,
                            f"{base}.cavity.{key}.value[{index}]",
                            f"emissivity must lie within its domain [0, 1], got {entry!r}",
                        )
                cavity_eps = entries
                if uses_radiation and not any(entry > 0.0 for entry in cavity_eps):
                    fail(
                        label,
                        f"{base}.cavity.{key}.value",
                        "cavity mode "
                        f"{mode!r} needs a positive effective emissivity (a "
                        "zero-emissivity radiative surface transfers nothing)",
                    )
        if surfaces is not None:
            if not isinstance(surfaces, list) or any(
                not isinstance(name, str) or not name.strip() for name in surfaces
            ):
                fail(
                    label,
                    f"{base}.cavity.loop_surfaces",
                    f"must be a list of surface names, got {surfaces!r}",
                )
            seen: set[str] = set()
            for name in surfaces:
                if name not in OUTER_ANNULUS_CAVITY_LOOP_SURFACES:
                    fail(
                        label,
                        f"{base}.cavity.loop_surfaces",
                        f"unknown cavity-exposed loop surface {name!r}; the "
                        f"known external primary-loop surfaces are "
                        f"{sorted(OUTER_ANNULUS_CAVITY_LOOP_SURFACES)} "
                        "(SegmentedMSR.Core.CavityLoopSurface; secondary-loop "
                        "surfaces are not cavity surfaces - plan §9.2)",
                    )
                if name in seen:
                    fail(
                        label,
                        f"{base}.cavity.loop_surfaces",
                        f"duplicate cavity-exposed surface assignment {name!r} "
                        "(each surface appears at most once - plan §9.2)",
                    )
                seen.add(name)
            loop_surfaces = list(surfaces)

    # --- precursor importance (plan §4.8) ------------------------------------
    precursor = block.get("precursor_importance")
    if precursor is None:
        if enabled:
            fail(
                label,
                f"{base}.precursor_importance",
                "missing required precursor-importance section on an enabled deck",
            )
        precursor = {}
    require_mapping(
        precursor, label, f"{base}.precursor_importance", "'precursor_importance'"
    )
    _outer_annulus_pinned(
        precursor, _OUTER_ANNULUS_PRECURSOR_KEYS, label, f"{base}.precursor_importance"
    )
    precursor_policy = precursor.get("policy")
    if precursor_policy is not None and (
        not isinstance(precursor_policy, str) or not precursor_policy.strip()
    ):
        fail(
            label,
            f"{base}.precursor_importance.policy",
            f"must be a non-empty string, got {precursor_policy!r}",
        )
    weights_node = _outer_annulus_optional_quantity(
        precursor, "weights", label, f"{base}.precursor_importance"
    )
    if weights_node is not None:
        _outer_annulus_positive_entries(
            weights_node, n_seg, label, f"{base}.precursor_importance.weights", unit="1", allow_zero=True
        )

    # --- annulus fission (plan §5.1/§5.2; TASK-20260918-01 P1) ----------------
    # The conservative SPLIT of the existing total fission source (NEVER an
    # additive source). Absent block -> fission disabled with zero fraction
    # vectors and the first-release policies (the enabled no-fission decks
    # keep their shape; the shipped disabled deck names the policy record).
    # Present block: strict fail-closed validation - finite/nonnegative
    # entries, exact n_seg lengths, the shared retained-core-share rule
    # (1 - math.fsum(f) >= OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE)
    # on both vectors,
    # elementwise f^S = f^Q under local_same_fraction (tolerance-considered
    # equality OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE plus the
    # same-active-branch requirement, TASK-20260923-01 P2), nonzero
    # fractions
    # illegal while the annulus or the fission feature is disabled,
    # unsupported policy names refused, complete provenance on any
    # fission-enabled deck - and NO silent renormalization of malformed
    # data. Every refusal names the full dotted configuration path and the
    # offending value.
    fission = block.get("fission")
    fission_enabled = False
    if fission is not None:
        require_mapping(fission, label, f"{base}.fission", "'fission'")
        _outer_annulus_pinned(
            fission, _OUTER_ANNULUS_FISSION_KEYS, label, f"{base}.fission"
        )
        fission_enabled = fission.get("enabled", False)
        if not isinstance(fission_enabled, bool):
            fail(
                label,
                f"{base}.fission.enabled",
                f"must be a boolean, got {fission_enabled!r}",
            )
        if fission_enabled and not enabled:
            fail(
                label,
                f"{base}.fission.enabled",
                "annulus fission requires the outer_fuel_annulus enabled "
                f"(the split delivers annulus fission power and precursor "
                f"production the disabled fold cannot carry); got "
                f"fission.enabled=true with enabled={enabled!r}",
            )
        coupling = fission.get("coupling_policy")
        if coupling is None:
            if fission_enabled:
                fail(
                    label,
                    f"{base}.fission.coupling_policy",
                    "missing required coupling_policy on a fission-enabled "
                    f"deck (one of {list(OUTER_ANNULUS_FISSION_COUPLING_POLICIES)})",
                )
        elif coupling not in OUTER_ANNULUS_FISSION_COUPLING_POLICIES:
            fail(
                label,
                f"{base}.fission.coupling_policy",
                f"must be one of {list(OUTER_ANNULUS_FISSION_COUPLING_POLICIES)} "
                "(plan §5.1; local_same_fraction is the only first-release "
                "coupling policy - a second policy is a future owner "
                f"decision), got {coupling!r}",
            )
        fraction_vectors: dict[str, list[float]] = {}
        for fraction_key in ("source_fraction", "heat_deposition_fraction"):
            node = _outer_annulus_optional_quantity(
                fission, fraction_key, label, f"{base}.fission"
            )
            if node is None:
                if fission_enabled:
                    _outer_annulus_required_quantity(
                        fission,
                        fraction_key,
                        label,
                        f"{base}.fission",
                        what=f"the {fraction_key} distribution",
                    )
                continue
            entries = _outer_annulus_positive_entries(
                node,
                n_seg,
                label,
                f"{base}.fission.{fraction_key}",
                unit="1",
                allow_zero=True,
            )
            fraction_sum = math.fsum(entries)
            if not (
                0.0 <= fraction_sum
                and 1.0 - fraction_sum
                >= OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE
            ):
                fail(
                    label,
                    f"{base}.fission.{fraction_key}.value",
                    f"the fraction sum {fraction_sum!r} violates the retained-share rule "
                    f"1 - fsum(f) >= {OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE!r} "
                    f"(the fraction sum {fraction_sum!r} violates the strict "
                    "0 <= sum_j f_a,j < 1 bound with the retained-share margin; "
                    "plan §5.2: the active core "
                    "keeps the retained share F_c = 1 - F_a >= "
                    f"{OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE!r}; a "
                    "sum >= 1 - and any sum inside the retained-share margin "
                    "- would leave a zero-power core); no silent "
                    "renormalization is applied - fix the authored "
                    "distribution",
                )
            if any(entry > 0.0 for entry in entries) and not (enabled and fission_enabled):
                fail(
                    label,
                    f"{base}.fission.{fraction_key}.value",
                    f"nonzero fission fractions {entries!r} require BOTH the "
                    f"outer_fuel_annulus enabled and fission.enabled=true; "
                    f"got enabled={enabled!r}, fission.enabled="
                    f"{fission_enabled!r} (all fission fractions must be zero "
                    "when the feature or the annulus fission is disabled - "
                    "plan §6 Phase 1)",
                )
            fraction_vectors[fraction_key] = entries
        # local_same_fraction (the only first-release coupling policy):
        # elementwise f^S = f^Q within the tolerance-considered equality
        # bound, PLUS the per-segment same-active-branch requirement
        # (TASK-20260923-01 P2, rev024 §4 Medium-1: exact-zero branch
        # semantics - a source-positive/heat-zero segment would arm the
        # precursor-production split while the thermal split stays folded,
        # and a heat-positive/source-zero segment would deposit fission
        # power with no fission events, even when the per-entry gap sits
        # inside the equality tolerance). The distinct names stay
        # (planner resolution 10); the equality is a validation rule, not
        # a schema collapse.
        if (
            (coupling or OUTER_ANNULUS_FISSION_COUPLING_POLICIES[0])
            == "local_same_fraction"
            and "source_fraction" in fraction_vectors
            and "heat_deposition_fraction" in fraction_vectors
        ):
            source_entries = fraction_vectors["source_fraction"]
            heat_entries = fraction_vectors["heat_deposition_fraction"]
            for index, (s_entry, q_entry) in enumerate(zip(source_entries, heat_entries)):
                if abs(s_entry - q_entry) > OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE:
                    fail(
                        label,
                        f"{base}.fission.source_fraction.value[{index}]",
                        "the local_same_fraction policy requires elementwise "
                        f"f^S = f^Q (plan §5.1): source_fraction[{index}] "
                        f"{s_entry!r} != heat_deposition_fraction[{index}] "
                        f"{q_entry!r} beyond the "
                        f"{OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE!r} equality "
                        "tolerance - the equality is a validation rule, the "
                        "two vectors stay distinct data",
                    )
                if (s_entry <= 0.0) != (q_entry <= 0.0):
                    fail(
                        label,
                        f"{base}.fission.source_fraction.value[{index}]",
                        "the local_same_fraction policy requires the same "
                        "active/inactive branch per segment (plan §5.1): "
                        f"source_fraction[{index}] {s_entry!r} and "
                        f"heat_deposition_fraction[{index}] {q_entry!r} "
                        "disagree on the active/inactive branch - a "
                        "source-positive/heat-zero segment would arm the "
                        "precursor-production split while the thermal split "
                        "stays folded, and a heat-positive/source-zero "
                        "segment would deposit fission power with no fission "
                        "events, even inside the equality tolerance (the "
                        "tolerance accommodates serialization noise, it "
                        "never authorizes different structural branches)",
                    )
        # Policies (plan §5.5/§5.6): explicit, fingerprinted, zero-credit
        # first release. A fission-enabled deck must NAME both policies
        # (a silent default would be exactly the unspoken assumption the
        # plan refuses); on a fission-disabled deck the authored value is
        # validated decision-record data.
        for policy_key, policies in (
            ("annulus_temperature_feedback_policy", OUTER_ANNULUS_FISSION_FEEDBACK_POLICIES),
            ("poison_flux_exposure_policy", OUTER_ANNULUS_FISSION_EXPOSURE_POLICIES),
        ):
            policy_value = fission.get(policy_key)
            if policy_value is None:
                if fission_enabled:
                    fail(
                        label,
                        f"{base}.fission.{policy_key}",
                        "missing required policy on a fission-enabled deck "
                        f"(one of {list(policies)}); a fission-enabled deck "
                        "must name its feedback/exposure policy explicitly "
                        "(plan §5.5/§5.6: zero credit is acceptable only as "
                        "an EXPLICIT policy, never a silent assumption)",
                    )
                continue
            if policy_value not in policies:
                fail(
                    label,
                    f"{base}.fission.{policy_key}",
                    f"must be one of {list(policies)} (the only first-release "
                    "policy; the weighted alternatives are unimplemented "
                    "future extensions - plan §5.5/§5.6), got "
                    f"{policy_value!r}",
                )
        # Optional weighted-policy placeholder nodes: valueless
        # 'status: unsupported' nodes are the permitted decision-record
        # placeholders; SOURCED values refuse (the weighted policies are
        # unimplemented in the first release - the zero_credit policy
        # credits no annulus feedback and no annulus exposure, so sourced
        # weights/coefficient values are phantom data).
        feedback_node = fission.get("annulus_temperature_feedback")
        if feedback_node is not None:
            require_mapping(
                feedback_node,
                label,
                f"{base}.fission.annulus_temperature_feedback",
                "'annulus_temperature_feedback'",
            )
            _outer_annulus_pinned(
                feedback_node,
                _OUTER_ANNULUS_FISSION_FEEDBACK_KEYS,
                label,
                f"{base}.fission.annulus_temperature_feedback",
            )
            feedback_weights = _outer_annulus_optional_quantity(
                feedback_node,
                "weights",
                label,
                f"{base}.fission.annulus_temperature_feedback",
            )
            if feedback_weights is not None:
                _outer_annulus_positive_entries(
                    feedback_weights,
                    n_seg,
                    label,
                    f"{base}.fission.annulus_temperature_feedback.weights",
                    unit="1",
                    allow_zero=True,
                )
                fail(
                    label,
                    f"{base}.fission.annulus_temperature_feedback.weights",
                    "sourced feedback weights are unsupported in the first "
                    "release: the weighted annulus temperature-feedback "
                    "policy is unimplemented (plan §5.6; the zero_credit "
                    "policy credits no annulus feedback) - carry the "
                    "valueless 'status: unsupported' placeholder instead; a "
                    "weighted policy is a future owner decision",
                )
            alpha_node = _outer_annulus_optional_quantity(
                feedback_node,
                "coefficient_alpha",
                label,
                f"{base}.fission.annulus_temperature_feedback",
            )
            if alpha_node is not None:
                _radial_positive_quantity(
                    alpha_node,
                    label,
                    f"{base}.fission.annulus_temperature_feedback.coefficient_alpha",
                    unit="1/K",
                    allow_zero=True,
                )
                fail(
                    label,
                    f"{base}.fission.annulus_temperature_feedback.coefficient_alpha",
                    "a sourced feedback coefficient is unsupported in the "
                    "first release: the weighted annulus temperature-feedback "
                    "policy is unimplemented (plan §5.6; the zero_credit "
                    "policy credits no annulus feedback) - carry the "
                    "valueless 'status: unsupported' placeholder instead; a "
                    "weighted policy is a future owner decision",
                )
        exposure_node = fission.get("poison_flux_exposure")
        if exposure_node is not None:
            require_mapping(
                exposure_node,
                label,
                f"{base}.fission.poison_flux_exposure",
                "'poison_flux_exposure'",
            )
            _outer_annulus_pinned(
                exposure_node,
                _OUTER_ANNULUS_FISSION_EXPOSURE_KEYS,
                label,
                f"{base}.fission.poison_flux_exposure",
            )
            exposure_weights = _outer_annulus_optional_quantity(
                exposure_node,
                "weights",
                label,
                f"{base}.fission.poison_flux_exposure",
            )
            if exposure_weights is not None:
                _outer_annulus_positive_entries(
                    exposure_weights,
                    n_seg,
                    label,
                    f"{base}.fission.poison_flux_exposure.weights",
                    unit="1",
                    allow_zero=True,
                )
                fail(
                    label,
                    f"{base}.fission.poison_flux_exposure.weights",
                    "sourced exposure weights are unsupported in the first "
                    "release: the weighted poison flux-exposure policy is "
                    "unimplemented (plan §5.5; the zero_credit policy "
                    "credits no annulus absorption exposure) - carry the "
                    "valueless 'status: unsupported' placeholder instead; a "
                    "weighted policy is a future owner decision",
                )

    # --- initialization (kelvin after conversion; plan §7.1/§7.2) -------------
    initialization = block.get("initialization")
    if initialization is None:
        if enabled:
            fail(
                label,
                f"{base}.initialization",
                "missing required initialization section on an enabled deck",
            )
        initialization = {}
    require_mapping(
        initialization, label, f"{base}.initialization", "'initialization'"
    )
    _outer_annulus_pinned(
        initialization, _OUTER_ANNULUS_INIT_KEYS, label, f"{base}.initialization"
    )
    for key in ("annulus_temperature", "vessel_temperature"):
        node = _outer_annulus_optional_quantity(initialization, key, label, f"{base}.initialization")
        if node is None:
            if enabled:
                _outer_annulus_required_quantity(
                    initialization, key, label, f"{base}.initialization", what=key
                )
            continue
        if quantity_unit(node) != "degC":
            fail(
                label,
                f"{base}.initialization.{key}.unit",
                "must be 'degC' (the plant temperature convention; the "
                f"segmented emitter converts to kelvin), got {quantity_unit(node)!r}",
            )
        value = quantity_value(node)
        if isinstance(value, list) or isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(
                label,
                f"{base}.initialization.{key}.value",
                f"must be a scalar temperature (the generated record binds "
                f"one initial temperature per medium), got {value!r}",
            )

    # --- inventory closure (plan §4.10, §7.2; the P1 split_existing record) --
    inventory_policy = block.get("inventory_policy")
    if inventory_policy is not None and inventory_policy not in OUTER_ANNULUS_INVENTORY_POLICIES:
        fail(
            label,
            f"{base}.inventory_policy",
            f"must be one of {list(OUTER_ANNULUS_INVENTORY_POLICIES)} (plan "
            f"§4.10; exactly one recorded policy), got {inventory_policy!r}",
        )
    if enabled:
        if inventory_policy is None:
            fail(
                label,
                f"{base}.inventory_policy",
                "missing required inventory-allocation policy on an enabled "
                f"deck (one of {list(OUTER_ANNULUS_INVENTORY_POLICIES)})",
            )
        inventory = block.get("inventory")
        if inventory is None:
            fail(
                label,
                f"{base}.inventory",
                "missing required inventory record on an enabled deck",
            )
        require_mapping(inventory, label, f"{base}.inventory", "'inventory'")
        _outer_annulus_pinned(
            inventory, _OUTER_ANNULUS_INVENTORY_KEYS, label, f"{base}.inventory"
        )
        cell_vol_total = _outer_annulus_core_cell_vol_total(plant, target, label)
        loop_total = sum(vol_loop_list(plant))
        plant_total = float(quantity_value(plant["total_fuel_vol"]))
        # P0 (TASK-20260918-01; plan §6 Phase 0): the carve is EXECUTABLE
        # data on ANY enabled deck - the authored active-core cell-volume
        # matrix the embedded core and precursor network consume as the
        # ChannelMap cellVol (the assembly asserts the elementwise identity
        # fail-closed). Positive entries; policy-specific closure below.
        carve_node = _outer_annulus_required_quantity(
            inventory,
            "active_core_cell_volume",
            label,
            f"{base}.inventory",
            what="carved active-core cell-volume matrix",
        )
        if quantity_unit(carve_node) != "m3":
            fail(
                label,
                f"{base}.inventory.active_core_cell_volume.unit",
                f"must be 'm3', got {quantity_unit(carve_node)!r}",
            )
        carve_value = quantity_value(carve_node)
        if not isinstance(carve_value, list) or not all(
            isinstance(row, list) for row in carve_value
        ):
            fail(
                label,
                f"{base}.inventory.active_core_cell_volume.value",
                f"must be an [n_chan][n_seg] matrix, got {type(carve_value).__name__}",
            )
        _matrix_shape(
            carve_node, n_chan, n_seg, label,
            f"{base}.inventory.active_core_cell_volume",
        )
        active_cell_volumes = [
            [
                _radial_positive_quantity(
                    {"value": cell, "unit": "m3"},
                    label,
                    f"{base}.inventory.active_core_cell_volume.value[{r}][{c}]",
                    unit="m3",
                )
                for c, cell in enumerate(row)
            ]
            for r, row in enumerate(carve_value)
        ]
        active_sum = sum(sum(row) for row in active_cell_volumes)
        if inventory_policy == "split_existing":
            legacy_node = _outer_annulus_required_quantity(
                inventory, "legacy_term", label, f"{base}.inventory", what="legacy in-vessel volume term"
            )
            legacy = _radial_positive_quantity(
                legacy_node, label, f"{base}.inventory.legacy_term", unit="m3"
            )
            subtraction = inventory.get("subtraction")
            if not isinstance(subtraction, Mapping) or not str(
                subtraction.get("expression") or ""
            ).strip():
                fail(
                    label,
                    f"{base}.inventory.subtraction.expression",
                    "split_existing requires the recorded subtraction "
                    "expression (the legacy term the annulus is carved from)",
                )
            retained_node = _outer_annulus_required_quantity(
                inventory, "total_retained", label, f"{base}.inventory", what="retained total volume"
            )
            retained = _radial_positive_quantity(
                retained_node, label, f"{base}.inventory.total_retained", unit="m3"
            )
            if abs(legacy - cell_vol_total) > 1e-9:
                fail(
                    label,
                    f"{base}.inventory.legacy_term.value",
                    f"the recorded legacy term {legacy!r} m3 does not match "
                    f"the target core's authored channel-region salt volume "
                    f"(cores.{target}.cell_vol sum {cell_vol_total!r} m3) "
                    "within 1e-9; the split_existing subtraction carves the "
                    "annulus out of THAT term",
                )
            if abs(retained - plant_total) > 1e-9:
                fail(
                    label,
                    f"{base}.inventory.total_retained.value",
                    f"the retained total {retained!r} m3 must equal the plant "
                    f"cross-check total_fuel_vol {plant_total!r} m3 within "
                    "1e-9 (split_existing preserves the total; plan §4.10)",
                )
            if abs(retained - legacy - loop_total) > OUTER_ANNULUS_INVENTORY_TOLERANCE:
                fail(
                    label,
                    f"{base}.inventory.total_retained.value",
                    f"inventory closure: retained {retained!r} m3 minus the "
                    f"legacy term {legacy!r} m3 = {retained - legacy!r} m3 does "
                    f"not match the external primary-loop volume "
                    f"{loop_total!r} m3 within "
                    f"{OUTER_ANNULUS_INVENTORY_TOLERANCE!r} m3",
                )
            if annulus_volumes_final is not None:
                annulus_total = sum(annulus_volumes_final)
                active_total = legacy - annulus_total
                if active_total <= 0.0:
                    fail(
                        label,
                        f"{base}.geometry.annulus_volume",
                        f"total-fuel inventory closure: the outer-annulus "
                        f"volume {annulus_total!r} m3 does not fit inside the "
                        f"legacy in-vessel term {legacy!r} m3 (the "
                        "split_existing subtraction must leave a positive "
                        "channel-region salt volume; adding the annulus "
                        "on top would double-count fuel, decay heat, "
                        "residence time, and poison dilution - plan §4.10)",
                    )
                # P0: actual whole-volume closure on the ACTUAL modeled
                # volumes: sum(V_active) + sum(V_annulus) + V_loop = retained.
                closure = active_sum + annulus_total + loop_total
                if abs(closure - retained) > OUTER_ANNULUS_INVENTORY_TOLERANCE:
                    fail(
                        label,
                        f"{base}.inventory.active_core_cell_volume.value",
                        f"inventory closure: sum(active_core_cell_volume) "
                        f"{active_sum!r} m3 + annulus "
                        f"{annulus_total!r} m3 + external loop "
                        f"{loop_total!r} m3 = {closure!r} m3 does not equal "
                        f"the retained total {retained!r} m3 within "
                        f"{OUTER_ANNULUS_INVENTORY_TOLERANCE!r} m3 - the "
                        "split_existing carve must close on the ACTUAL modeled "
                        "volumes (plan §6 Phase 0)",
                    )
                if n_chan == 1:
                    # Per-elevation closure against the target core's authored
                    # per-segment cell volumes (the source data that support
                    # it: a one-channel deck's cell_vol is per-segment).
                    legacy_segments = _outer_annulus_core_cell_vol_segments(
                        plant, target, label, n_seg
                    )
                    for index, legacy_j in enumerate(legacy_segments):
                        remainder = (
                            active_cell_volumes[0][index]
                            + annulus_volumes_final[index]
                            - legacy_j
                        )
                        if abs(remainder) > 1e-9 * legacy_j:
                            fail(
                                label,
                                f"{base}.inventory.active_core_cell_volume.value[0][{index}]",
                                f"per-elevation inventory closure: V_active,j "
                                f"{active_cell_volumes[0][index]!r} m3 + "
                                f"V_annulus,j {annulus_volumes_final[index]!r} "
                                f"m3 != V_legacy,j {legacy_j!r} m3 within "
                                f"{1e-9 * legacy_j!r} m3 - each legacy cell is "
                                "carved against its OWN elevation's annulus "
                                "volume (plan §6 Phase 0)",
                            )
                        if active_cell_volumes[0][index] <= 0.0:
                            fail(
                                label,
                                f"{base}.inventory.active_core_cell_volume.value[0][{index}]",
                                f"the carved active-core remainder "
                                f"{active_cell_volumes[0][index]!r} m3 must be "
                                f"positive (this elevation's annulus volume "
                                f"{annulus_volumes_final[index]!r} m3 consumes "
                                f"the legacy cell {legacy_j!r} m3)",
                            )
            elif n_chan == 1:
                fail(
                    label,
                    f"{base}.geometry.annulus_volume",
                    "the split_existing per-elevation closure needs the "
                    "sourced annulus geometry (the carve is checked against "
                    "each elevation's annulus volume); complete "
                    "outer_fuel_annulus.geometry first",
                )
        else:  # revise_total
            revised_node = _outer_annulus_required_quantity(
                inventory, "total_revised", label, f"{base}.inventory", what="revised total volume"
            )
            revised = _radial_positive_quantity(
                revised_node, label, f"{base}.inventory.total_revised", unit="m3"
            )
            if annulus_volumes_final is None:
                fail(
                    label,
                    f"{base}.inventory.total_revised",
                    "the revise_total closure needs the sourced geometry "
                    "(the annulus volume enters the revised total); complete "
                    "outer_fuel_annulus.geometry first",
                )
            revised_total = cell_vol_total + sum(annulus_volumes_final) + loop_total
            if abs(revised_total - revised) > OUTER_ANNULUS_INVENTORY_TOLERANCE:
                fail(
                    label,
                    f"{base}.inventory.total_revised.value",
                    f"inventory closure: active core {cell_vol_total!r} + "
                    f"outer annulus {sum(annulus_volumes_final)!r} + external "
                    f"loop {loop_total!r} = {revised_total!r} m3 does not "
                    f"match the recorded revised total {revised!r} m3 within "
                    f"{OUTER_ANNULUS_INVENTORY_TOLERANCE!r} m3",
                )
            # P0: the channels keep the LEGACY volumes under revise_total
            # (the annulus adds on top of a revised total - nothing is
            # carved out), so the executable matrix the embedded core
            # consumes must BE the authored channel-region volume chain.
            if abs(active_sum - cell_vol_total) > 1e-9:
                fail(
                    label,
                    f"{base}.inventory.active_core_cell_volume.value",
                    f"revise_total keeps the legacy channel volumes (the "
                    f"annulus is additional), so sum(active_core_cell_volume) "
                    f"{active_sum!r} m3 must equal the target core's authored "
                    f"channel-region salt volume (cores.{target}.cell_vol sum "
                    f"{cell_vol_total!r} m3) within 1e-9",
                )

    # --- strict-physical volume closure where claimed (plan §7.2) ------------
    if enabled and policy == "strict_physical" and len(radii) == 3 and physical_lengths:
        tolerance = (
            OUTER_ANNULUS_VOLUME_TOLERANCE_DEFAULT
            if "volume_tolerance" not in block
            else float(quantity_value(block["volume_tolerance"]))
        )
        authored_lengths = _radial_authored_fuel_lengths(plant, target, label)
        if len(authored_lengths) != n_seg:
            fail(
                label,
                f"cores.{target}.channel_geom",
                f"the target core's authored fuel-length chain has "
                f"{len(authored_lengths)} entries, expected {n_seg}",
            )
        for index, (stated, authored) in enumerate(zip(physical_lengths, authored_lengths)):
            if abs(stated - authored) > tolerance * abs(authored):
                fail(
                    label,
                    f"{base}.geometry.physical_length.value[{index}]",
                    "strict-physical closure: the stated per-segment annulus "
                    f"length {stated!r} m does not match the core's authored "
                    f"fuel length {authored!r} m (channel_geom LF chain) "
                    f"within relative tolerance {tolerance!r}; the annulus is "
                    "divided into the same physical elevations as the core "
                    "(plan §3.1)",
                )


def _validate_pumps(pumps: Mapping[str, Any], label: str) -> None:
    for name in ("tripK", "freeConvFF", "tripTime"):
        _required_quantity(pumps, name, label, "pumps")
    for branch in ("primary", "secondary"):
        node = _required_section(pumps, branch, label, "pumps")
        n_ramp = _structural_int(
            _required_quantity(node, "numRampUp", label, f"pumps.{branch}"),
            label,
            f"pumps.{branch}.numRampUp",
        )
        if n_ramp < 1:
            fail(label, f"pumps.{branch}.numRampUp.value", f"must be >= 1, got {n_ramp}")
        for name in ("rampUpK", "rampUpTo", "rampUpTime"):
            ramp = _required_quantity(node, name, label, f"pumps.{branch}")
            length = _array_length(ramp, label, f"pumps.{branch}.{name}")
            if length != n_ramp:
                fail(
                    label,
                    f"pumps.{branch}.{name}.value",
                    f"length {length} does not match pumps.{branch}.numRampUp ({n_ramp})",
                )


def _validate_primary_loop(primary: Mapping[str, Any], label: str) -> None:
    _required_quantity(primary, "vdot_fuel", label, "primary_loop")
    volumes = _required_section(primary, "volumes", label, "primary_loop")
    for name in _PRIMARY_VOLUME_ORDER:
        _required_quantity(volumes, name, label, "primary_loop.volumes")
    pipes = _required_section(primary, "pipes", label, "primary_loop")
    for name in ("pipeCoreToDHRS", "pipeDHRStoHX", "pipeHXtoCore"):
        pipe = _required_section(pipes, name, label, f"primary_loop.pipes.{name}")
        for key in _PIPE_GEOMETRY_KEYS:
            _required_quantity(pipe, key, label, f"primary_loop.pipes.{name}")
    dhrs = _required_section(primary, "dhrs", label, "primary_loop")
    for key in ("tK", "max_remove", "bleed", "engage_time", *_NODE_GEOMETRY_KEYS):
        _required_quantity(dhrs, key, label, "primary_loop.dhrs")


def _validate_secondary_loop(secondary: Mapping[str, Any], label: str) -> None:
    _required_quantity(secondary, "vdot_coolant", label, "secondary_loop")
    hx = _required_section(secondary, "hx", label, "secondary_loop")
    for name in (
        "vol_P",
        "vol_T",
        "vol_S",
        "hApNom",
        "hAsNom",
        "hAExp",
        "AcShell",
        "AcTube",
        "ArShell",
        "L_shell",
        "L_tube",
        "e",
        "Tinf",
        "TpIn_0",
        "TpOut_0",
        "TsIn_0",
        "TsOut_0",
    ):
        _required_quantity(hx, name, label, "secondary_loop.hx")
    uhx = _required_section(secondary, "uhx", label, "secondary_loop")
    for name in ("vol", "Tp_0"):
        _required_quantity(uhx, name, label, "secondary_loop.uhx")
    pipes = _required_section(secondary, "pipes", label, "secondary_loop")
    for name in ("HXtoUHX", "UHXtoHX"):
        pipe = _required_section(pipes, name, label, f"secondary_loop.pipes.{name}")
        for key in ("vol", *_PIPE_GEOMETRY_KEYS):
            _required_quantity(pipe, key, label, f"secondary_loop.pipes.{name}")


def walk_quantities(obj: Any, prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Return (dotted_path, quantity_dict) for every quantity in a nested mapping."""

    found: list[tuple[str, dict[str, Any]]] = []
    if is_quantity(obj):
        found.append((prefix, dict(obj)))
        return found
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).startswith("_"):
                continue
            child = f"{prefix}.{key}" if prefix else str(key)
            found.extend(walk_quantities(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            child = f"{prefix}[{index}]"
            found.extend(walk_quantities(value, child))
    return found


def vol_loop_list(plant: Mapping[str, Any]) -> list[float]:
    """Primary-loop volumes in the named connect order used by SegmentedMSR.Reactors."""

    volumes = plant["primary_loop"]["volumes"]
    order = (
        "pipeCoreToDHRS",
        "DHRS",
        "pipeDHRStoHX",
        "HXprimary",
        "pipeHXtoCore",
    )
    return [float(quantity_value(volumes[name])) for name in order]


def r9_derived(plant: Mapping[str, Any]) -> dict[str, Any]:
    """Derived 9R chains matching SegmentedMSR.Reactors expressions (raw arrays)."""

    r9 = plant["cores"]["r9"]
    vol_f1 = [float(x) for x in quantity_value(r9["vol_F1"])]
    vol_f2 = [float(x) for x in quantity_value(r9["vol_F2"])]
    kfn1 = [float(x) for x in quantity_value(r9["kFN1"])]
    kfn2 = [float(x) for x in quantity_value(r9["kFN2"])]
    kht1 = [float(x) for x in quantity_value(r9["kHT1"])]
    kht2 = [float(x) for x in quantity_value(r9["kHT2"])]
    h_a = [float(x) for x in quantity_value(r9["hA"])]
    tf1 = [float(x) for x in quantity_value(r9["trim"]["TF1_regions"])]
    tf2 = [float(x) for x in quantity_value(r9["trim"]["TF2_regions"])]
    n_regions = int(quantity_value(r9["n_regions"]))
    cell_vol_chain = []
    k_cell_chain = []
    q_mod_chain = []
    u_cell_chain = []
    for i in range(n_regions):
        cell_vol_chain.extend([vol_f1[i], vol_f2[i]])
        k_cell_chain.extend([kfn1[i], kfn2[i]])
        q_mod = (kht1[i] + kht2[i]) / 2.0
        q_mod_chain.extend([q_mod, q_mod])
        u_cell_chain.extend([kht1[i] * h_a[i], kht2[i] * h_a[i]])
    pred_offset = [
        h_a[i] * (kht2[i] / (kht1[i] + kht2[i])) * (tf2[i] - tf1[i])
        for i in range(n_regions)
    ]
    return {
        "cell_vol_chain": cell_vol_chain,
        "k_cell_chain": k_cell_chain,
        "q_mod_chain": q_mod_chain,
        "u_cell_chain": u_cell_chain,
        "f_salt_normalizer": sum(k_cell_chain),
        "q_mod_total": sum(q_mod_chain),
        "pred_offset_W": pred_offset,
        "pred_offset_total_W": sum(pred_offset),
        "vol_G_total": sum(float(x) for x in quantity_value(r9["vol_G"])),
    }


def lumped_generated_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "core" / "generated"


#: CLI core key -> core deck key (mirrors helpers.scenario_config.CLI_TO_YAML_CORE,
#: restated here so legacy runners resolve deck metadata without importing
#: the segmented helpers).
_CLI_CORE_DECKS = {"1r": "r1", "9r": "r9", "1r10seg": "r1_10seg", "r5x5_z10": "r5x5_z10"}


def core_maturity_labels(core_model: str, plant: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Both maturity labels of a CLI core, read from its deck.

    ``{"core_maturity": <implementation maturity>,
    "core_physical_data_maturity": <physical-data maturity>}`` -- the
    manifest fields every run records (rev032 review: legacy 1R/9R runs
    included, not only segmented ones).
    """
    key = _CLI_CORE_DECKS.get(str(core_model).strip().lower())
    if key is None:
        raise ValueError(f"unknown core model {core_model!r}; expected one of {sorted(_CLI_CORE_DECKS)}")
    core = (plant or load_plant("msrr"))["cores"][key]
    return {
        "core_maturity": str(core["maturity"]),
        "core_physical_data_maturity": str(core["physical_data_maturity"]),
    }


def source_normalization_record(plant: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The external-source normalization a startup run applies.

    nomS = eta_S*S/N0 with N0 = LAMBDA*nu*P/E_f (legacy PKE/mPKE and
    SegmentedMSR PKE_T). Recorded in startup run manifests so a plotter can
    interpret a result with the constants it was produced with, not the
    current checkout's (rev032 review).
    """
    plant = plant or load_plant("msrr")
    kinetics = plant["kinetics"]
    lam = float(quantity_value(kinetics["generation_time"]))
    nu = float(quantity_value(kinetics["nu"]))
    power = float(quantity_value(plant["nominal_power"]))
    energy = float(quantity_value(plant["poisons"]["energy_per_fission"]))
    return {
        "rule": "nomS = eta_S*S/N0; N0 = LAMBDA*nu*P/E_f",
        "generation_time_s": lam,
        "nu": nu,
        "nominal_power_w": power,
        "energy_per_fission_j": energy,
        "source_effectiveness": float(quantity_value(kinetics["source_effectiveness"])),
        "full_power_population": lam * nu * power / energy,
    }


def lumped_plant_data_path(
    core_dir: str | Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Return the lumped ``MSRR_PlantData.mo`` path.

    Prefers ``<core_dir>/generated/MSRR_PlantData.mo``. A sibling copy in
    ``core_dir`` is accepted for workdir layouts that flatten generated
    files next to ``MSRR.mo``.
    """

    if core_dir is not None:
        core = Path(core_dir)
        generated = core / "generated" / LUMPED_PLANT_DATA_FILE
        if generated.is_file():
            return generated
        sibling = core / LUMPED_PLANT_DATA_FILE
        if sibling.is_file():
            return sibling
        return generated
    return lumped_generated_dir(root) / LUMPED_PLANT_DATA_FILE


def lumped_library_paths(
    core_dir: str | Path,
    *,
    root: Path | None = None,
) -> list[Path]:
    """PlantData, then SMD library, then ``MSRR.mo`` (omc load order)."""

    core = Path(core_dir)
    return [
        lumped_plant_data_path(core, root=root),
        core / LUMPED_LIBRARY_FILE,
        core / LUMPED_MODEL_FILE,
    ]


def require_lumped_library_paths(
    core_dir: str | Path,
    *,
    root: Path | None = None,
) -> list[Path]:
    paths = lumped_library_paths(core_dir, root=root)
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "missing lumped Modelica sources (load PlantData first): "
            + ", ".join(str(path) for path in missing)
        )
    return paths


def lumped_load_file_text(files: Sequence[str | Path]) -> str:
    """``loadFile`` lines in PlantData → SMD → MSRR order."""

    return "".join(f'loadFile("{path}");\n' for path in files)


def copy_lumped_sources(
    core_dir: str | Path,
    workdir: str | Path,
    *,
    root: Path | None = None,
) -> list[Path]:
    """Copy PlantData + SMD + MSRR into ``workdir`` (PlantData flattened)."""

    dest_dir = Path(workdir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dests: list[Path] = []
    for src in require_lumped_library_paths(core_dir, root=root):
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        dests.append(dest)
    return dests


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def poisons_include_path(
    plant_id: str = "msrr",
    *,
    root: Path | None = None,
) -> Path:
    """Resolve the authored poison include file backing a plant deck.

    TASK-20260912-01 P1: the digest source for poison-on run provenance.
    Reads the plant identity file's ``includes:`` map (the same map
    :func:`load_plant` resolves) and returns the poison YAML path. Raises
    ``FileNotFoundError`` when the deck declares no poison include or the
    file is absent -- a poison-on run cannot name a source digest without
    an authored poison file.
    """

    plant_dir = plants_root(root) / plant_id
    identity_path = plant_dir / "plant.yaml"
    raw = _load_yaml(identity_path)
    includes = raw.get("includes") if isinstance(raw, dict) else None
    spec = includes.get("poisons") if isinstance(includes, dict) else None
    if not isinstance(spec, str) or not spec.strip():
        raise FileNotFoundError(
            f"{identity_path} declares no 'poisons' include; the poison "
            "dataset identity cannot be resolved"
        )
    plant_dir_resolved = plant_dir.resolve()
    path = (plant_dir / spec).resolve()
    if not path.is_relative_to(plant_dir_resolved):
        raise ValueError(
            f"poison include {spec!r} in {identity_path} resolves outside the "
            f"plant directory: {path}"
        )
    if not path.is_file():
        raise FileNotFoundError(
            f"poison include file {path} declared by {identity_path} is missing"
        )
    return path


def poison_dataset_record(
    plant_id: str = "msrr",
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Identity of the poison dataset a segmented run actually binds.

    Loads the plant deck (full validation) and returns the fields run
    manifests record on every poison-on run (TASK-20260912-01 P1, tightened
    by P5):

    - ``poisonDatasetId``: the ``dataset_id`` the generated
      ``SegmentedMSR_PlantData.Poisons`` was emitted from;
    - ``poisonMaturity``: the authored governance label (a required enum
      since P5 -- :data:`POISON_MATURITY_VALUES` -- validated at load time,
      so the field is always present and never fabricated here);
    - ``poisonSourceDigest``: SHA-256 of the authored poison include file
      bytes (see :func:`poisons_include_path`).

    These are plant-side truth, not scenario claims: a run manifest that
    carries them cannot name a dataset different from the one the
    executable binds.
    """

    plant = load_plant(plant_id, root=root)
    poisons = plant["poisons"]
    return {
        "poisonDatasetId": str(poisons["dataset_id"]),
        "poisonMaturity": str(poisons["maturity"]),
        "poisonSourceDigest": file_digest(poisons_include_path(plant_id, root=root)),
    }


def _radial_material_scalars(materials: Mapping[str, Any], name: str) -> dict[str, float]:
    node = materials.get(name)
    if not isinstance(node, Mapping):
        return {"rho": 0.0, "cp": 0.0, "k": 0.0}
    return {
        key: float(quantity_value(node[key])) if key in node else 0.0
        for key in ("rho", "cp", "k")
    }


def radial_config(plant: Mapping[str, Any], core_key: str) -> dict[str, Any]:
    """Normalized intra-channel radial configuration of one core (JSON-safe).

    The single source of truth consumed by the emitter
    (:mod:`helpers.emit_modelica_plant`) and by the manifest/fingerprint
    helpers in :mod:`helpers.segmented_runs`. Returns the DISABLED default
    configuration for a core whose block is absent or ``enabled: false``
    (all shipped decks), so the existing generated fields of the disabled
    records stay byte-identical. The physical-channel mapping keys
    (review rev019 Phase 2; TASK-20260915-01 P2) -- ``channel_meaning``,
    ``channel_multiplicity`` (N_c), and the explicit per-segment
    ``geometry.physical_length`` (L_j; sized by
    :func:`radial_stack_segment_count`, never map.dz unless proven equal
    for the core) -- ride their canonical defaults on a disabled record.
    Physics quantities keep their AUTHORED values (HX temperatures in
    plant-degC; the segmented emitter converts to kelvin). The
    ``annular_heat_exchanger_model`` string is binding-enforced to match
    ``annular_heat_exchanger_enabled`` (review rev020 Phase 5 item 1):
    enabled -> ``finite_conductance_prescribed_sink``, disabled ->
    ``none``; any authored value outside those two identities refuses.
    Call after :func:`load_plant` so the block is already validated.
    """

    core = plant["cores"][core_key]
    n_chan = radial_loop_channel_count(plant, core_key)
    n_seg = radial_stack_segment_count(plant, core_key)
    cfg: dict[str, Any] = {
        "enabled": False,
        "annular_fluid_mode": "static",
        "annular_heat_exchanger_enabled": False,
        "annular_heat_exchanger_model": "none",
        "geometry_policy": "strict_physical",
        "volume_tolerance": RADIAL_VOLUME_TOLERANCE_DEFAULT,
        "channel_meaning": "literal_tube",
        "channel_multiplicity": 1,
        "maturity": "",
        "dataset_id": "",
        "geometry": {
            "fuel_radius": 0.0,
            "pipe_outer_radius": 0.0,
            "annulus_outer_radius": 0.0,
            "physical_length": [0.0] * n_seg,
        },
        "materials": {
            "channel_pipe": {"rho": 0.0, "cp": 0.0, "k": 0.0},
            "annular_fluid": {"rho": 0.0, "cp": 0.0, "k": 0.0},
        },
        "interfaces": {
            "fuel_pipe": {"mode": "perfect", "h": 0.0},
            "pipe_fluid": {"mode": "perfect", "h": 0.0},
            "fluid_moderator": {"mode": "perfect", "h": 0.0},
        },
        "annular_loop": {
            "nominal_mass_flow": 0.0,
            "max_flow_command": 0.0,
            "flow_direction": "bottom_to_top",
            "channel_flow_fractions": [0.0] * n_chan,
            "supply_plenum_volume": 0.0,
            "return_plenum_volume": 0.0,
            "connecting_pipe_volume": 0.0,
            "heat_exchanger": {
                "ua": 0.0,
                "loop_side_volume": 0.0,
                "sink_temperature": 0.0,
                "initial_temperature": 0.0,
            },
        },
        "heat_deposition": {"pipe_fraction": 0.0, "annular_fluid_fraction": 0.0},
    }
    block = core.get(RADIAL_BLOCK_KEY)
    if not isinstance(block, Mapping) or not bool(block.get("enabled")):
        # Disabled (block absent or enabled: false): the pure canonical
        # disabled defaults stand regardless of any staged sub-blocks, so
        # every disabled generated record is identical and additive.
        return cfg
    cfg["enabled"] = True
    cfg["maturity"] = str(block.get("maturity") or "")
    cfg["dataset_id"] = str(block.get("dataset_id") or "")
    cfg["geometry_policy"] = str(block.get("geometry_policy") or "strict_physical")
    cfg["channel_meaning"] = str(block.get("channel_meaning") or "literal_tube")
    if "channel_multiplicity" in block:
        cfg["channel_multiplicity"] = int(quantity_value(block["channel_multiplicity"]))
    if "volume_tolerance" in block:
        cfg["volume_tolerance"] = float(quantity_value(block["volume_tolerance"]))
    annular_fluid = block.get("annular_fluid") or {}
    cfg["annular_fluid_mode"] = str(annular_fluid.get("mode") or "static")
    loop_enabled = cfg["annular_fluid_mode"] == "circulating"
    if "flow_direction" in annular_fluid:
        cfg["annular_loop"]["flow_direction"] = str(annular_fluid["flow_direction"])
    if "geometry" in block:
        geometry = block["geometry"]
        for key in ("fuel_radius", "pipe_outer_radius", "annulus_outer_radius"):
            cfg["geometry"][key] = float(quantity_value(geometry[key]))
        cfg["geometry"]["physical_length"] = [
            float(item) for item in quantity_value(geometry["physical_length"])
        ]
    cfg["materials"] = {
        name: _radial_material_scalars(plant["materials"], name)
        for name in _RADIAL_MATERIAL_NAMES
    }
    if "interfaces" in block:
        for name in ("fuel_pipe", "pipe_fluid", "fluid_moderator"):
            node = block["interfaces"][name]
            iface = {"mode": str(node.get("mode")), "h": 0.0}
            if iface["mode"] == "film" and "h" in node:
                iface["h"] = float(quantity_value(node["h"]))
            cfg["interfaces"][name] = iface
    if "heat_deposition" in block:
        for key in ("pipe_fraction", "annular_fluid_fraction"):
            if key in block["heat_deposition"]:
                cfg["heat_deposition"][key] = float(
                    quantity_value(block["heat_deposition"][key])
                )
    if loop_enabled:
        loop = block["annular_loop"]
        loop_cfg = cfg["annular_loop"]
        loop_cfg["nominal_mass_flow"] = float(quantity_value(loop["nominal_mass_flow"]))
        loop_cfg["max_flow_command"] = float(quantity_value(loop["max_flow_command"]))
        loop_cfg["channel_flow_fractions"] = [
            float(item) for item in quantity_value(loop["channel_flow_fractions"])
        ]
        loop_cfg["supply_plenum_volume"] = float(quantity_value(loop["supply_plenum_volume"]))
        loop_cfg["return_plenum_volume"] = float(quantity_value(loop["return_plenum_volume"]))
        loop_cfg["connecting_pipe_volume"] = float(
            quantity_value(loop["connecting_pipe_volume"])
        )
        hx = loop.get("heat_exchanger") or {}
        cfg["annular_heat_exchanger_enabled"] = bool(hx.get("enabled", False))
        cfg["annular_heat_exchanger_model"] = str(
            hx.get("model") or RADIAL_HX_MODEL_NONE
        )
        # Generator binding (review rev020 Phase 5 item 1;
        # TASK-20260916-01 P2): the emitted annularHeatExchangerModel
        # string must match the annularHeatExchangerEnabled boolean --
        # enabled names exactly the one implemented finite-conductance
        # model, disabled is the canonical "none". Refuse fail-closed
        # (named path + value) so no consumer of this function (the
        # segmented emitter, the manifest helpers) can carry an identity
        # the boolean does not select, even for a plant that skipped
        # load_plant validation.
        expected_model = (
            RADIAL_HX_MODEL_FINITE_CONDUCTANCE
            if cfg["annular_heat_exchanger_enabled"]
            else RADIAL_HX_MODEL_NONE
        )
        if cfg["annular_heat_exchanger_model"] != expected_model:
            fail(
                f"cores.{core_key}",
                f"{RADIAL_BLOCK_KEY}.annular_loop.heat_exchanger.model",
                f"the generated annularHeatExchangerModel string must match "
                f"the annularHeatExchangerEnabled boolean "
                f"(enabled -> '{RADIAL_HX_MODEL_FINITE_CONDUCTANCE}', "
                f"disabled -> '{RADIAL_HX_MODEL_NONE}'); got "
                f"{cfg['annular_heat_exchanger_model']!r} with "
                f"annular_heat_exchanger_enabled="
                f"{cfg['annular_heat_exchanger_enabled']!r}",
            )
        hx_cfg = loop_cfg["heat_exchanger"]
        if cfg["annular_heat_exchanger_enabled"]:
            hx_cfg["ua"] = float(quantity_value(hx["ua"]))
            hx_cfg["loop_side_volume"] = float(quantity_value(hx["loop_side_volume"]))
            hx_cfg["sink_temperature"] = float(quantity_value(hx["sink_temperature"]))
            hx_cfg["initial_temperature"] = float(quantity_value(hx["initial_temperature"]))
    return cfg


def radial_fingerprint(cfg: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of the radial-stack data subset of ``cfg``.

    Covers the in-core radial stack: geometry (including the explicit
    per-segment ``physical_length``), shared materials, interface
    parameters, deposition shares, geometry policy and tolerance, the
    physical-channel mapping (``channel_meaning``/``channel_multiplicity``;
    review rev019 Phase 2), and the dataset identity/maturity (plan §6.8).
    The annular-loop data ride :func:`annular_loop_fingerprint` so
    circulation and heat-exchanger changes do not move the radial identity.

    DISABLED configs (block absent or ``enabled: false``) keep the
    HISTORICAL payload exactly - their canonical defaults are inert, and
    the disabled generated records (including their fingerprint constants)
    stay byte-identical to the pre-P2 emission (TASK-20260915-01 P2
    disabled-path parity).
    """

    if not cfg["enabled"]:
        return fingerprint(
            {
                "geometry": {
                    key: cfg["geometry"][key]
                    for key in ("fuel_radius", "pipe_outer_radius", "annulus_outer_radius")
                },
                "materials": cfg["materials"],
                "interfaces": cfg["interfaces"],
                "heat_deposition": cfg["heat_deposition"],
                "geometry_policy": cfg["geometry_policy"],
                "volume_tolerance": cfg["volume_tolerance"],
                "maturity": cfg["maturity"],
                "dataset_id": cfg["dataset_id"],
            }
        )
    return fingerprint(
        {
            "geometry": cfg["geometry"],
            "materials": cfg["materials"],
            "interfaces": cfg["interfaces"],
            "heat_deposition": cfg["heat_deposition"],
            "geometry_policy": cfg["geometry_policy"],
            "volume_tolerance": cfg["volume_tolerance"],
            "channel_meaning": cfg["channel_meaning"],
            "channel_multiplicity": cfg["channel_multiplicity"],
            "maturity": cfg["maturity"],
            "dataset_id": cfg["dataset_id"],
        }
    )


def annular_loop_fingerprint(cfg: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of the annular-loop data subset of ``cfg``.

    Covers annular mode, flow direction, mass flow and command bound,
    channel flow fractions, plenum and connecting-pipe volumes, and the
    heat-exchanger state and parameters (plan §6.4, §6.6, §6.8).
    """

    return fingerprint(
        {
            "annular_fluid_mode": cfg["annular_fluid_mode"],
            "annular_loop": cfg["annular_loop"],
            "annular_heat_exchanger_enabled": cfg["annular_heat_exchanger_enabled"],
            "annular_heat_exchanger_model": cfg["annular_heat_exchanger_model"],
        }
    )


def radial_manifest_record(plant: Mapping[str, Any], core_key: str) -> dict[str, Any] | None:
    """Manifest metadata for one core's radial/loop configuration.

    ``None`` when the feature is disabled for the core, so disabled-run
    manifests keep their historical shape and fingerprint (the same
    field-omission pattern as the poison-off column sets in
    ``helpers.segmented_runs``). When enabled, the record is JSON-safe and
    fingerprint-active: ``helpers.run_results.build_run_manifest`` records
    it under the top-level ``intra_channel_radial`` field. Temperatures are
    authored plant-degC (the manifest records authored data, not the
    emitter's kelvin conversion).
    """

    cfg = radial_config(plant, core_key)
    if not cfg["enabled"]:
        return None
    record: dict[str, Any] = {
        "enabled": True,
        "annularFluidMode": str(cfg["annular_fluid_mode"]),
        "annularHeatExchangerEnabled": bool(cfg["annular_heat_exchanger_enabled"]),
        "annularHeatExchangerModel": str(cfg["annular_heat_exchanger_model"]),
        "geometryPolicy": str(cfg["geometry_policy"]),
        "channelMeaning": str(cfg["channel_meaning"]),
        "channelMultiplicity": int(cfg["channel_multiplicity"]),
        "flowDirection": str(cfg["annular_loop"]["flow_direction"]),
        "radialDatasetId": str(cfg["dataset_id"]),
        "radialMaturity": str(cfg["maturity"]),
        "radialFingerprint": radial_fingerprint(cfg),
        "annularLoopFingerprint": annular_loop_fingerprint(cfg),
    }
    if str(cfg["annular_fluid_mode"]) == "circulating":
        # Review rev020 Phase 5 item 3 (TASK-20260916-01 P5): the
        # circulating production manifest record carries the exact plan
        # §6.4 flow distribution the vehicle binds, matching the documented
        # demo shape (``radial_demo_manifest_record``); absent on static
        # runs (the omission pattern -- static mode has no loop flow).
        record["channelFlowFractions"] = [
            float(item) for item in cfg["annular_loop"]["channel_flow_fractions"]
        ]
    return record


def outer_annulus_config(plant: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalized outer-annulus configuration (JSON-safe), or ``None``.

    The single source of truth consumed by the emitter
    (:mod:`helpers.emit_modelica_plant` — the generated
    ``SegmentedMSR_PlantData.OuterFuelAnnulus*`` package) and, in P8, by the
    manifest/fingerprint helpers cloned from
    :func:`radial_manifest_record` / ``radial_run_contract``. Returns
    ``None`` when the authored block is absent (no outer-annulus dataset:
    nothing is emitted, the emitter's conditional-core pattern).

    The shipped DISABLED block yields the canonical disabled record whose
    physics fields are the canonical zeros/neutral switches (the radial
    disabled discipline: staged physics values are never mirrored) but whose
    DATASET-IDENTITY fields are mirrored from the authored deck —
    ``dataset_id``, ``maturity``, ``geometry_policy``, ``inventory_policy``,
    and the production-enablement status are meaningful decision-record
    fields while disabled, and the disabled generated record names them.
    Enabled decks must be complete (validated by
    :func:`_validate_outer_fuel_annulus`); temperatures stay AUTHORED degC
    in this dict (the emitter converts to kelvin, the manifest records
    authored data).

    Fail-closed even for a plant that skipped :func:`load_plant`: an enabled
    configuration with a disabled topology code, an unsupported core, or an
    unknown policy refuses here with a named path and value (the generator
    binding guard of :func:`radial_config`).
    """

    block = plant.get(OUTER_ANNULUS_BLOCK_KEY)
    if not isinstance(block, Mapping):
        return None
    label = f"{OUTER_ANNULUS_BLOCK_KEY} (config)"
    enabled = bool(block.get("enabled"))
    target = block.get("first_production_target")
    if not isinstance(target, str) or not target.strip():
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.first_production_target",
            f"must be a non-empty core key, got {target!r}",
        )
    if target not in plant.get("cores", {}):
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.first_production_target",
            f"names core {target!r} which is not a section of the plant deck",
        )
    if enabled and target not in OUTER_ANNULUS_SUPPORTED_CORES:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.first_production_target",
            f"unsupported core {target!r} for an enabled outer_fuel_annulus "
            f"dataset (9R activation is refused fail-closed; supported "
            f"cores: {list(OUTER_ANNULUS_SUPPORTED_CORES)})",
        )
    n_chan = radial_loop_channel_count(plant, target)
    n_seg = radial_stack_segment_count(plant, target)
    cfg: dict[str, Any] = {
        "enabled": False,
        "first_production_target": target,
        "n_chan": int(n_chan),
        "n_seg": int(n_seg),
        "dataset_id": "",
        "maturity": "",
        "geometry_policy": "strict_physical",
        "volume_tolerance": OUTER_ANNULUS_VOLUME_TOLERANCE_DEFAULT,
        "inventory_policy": "",
        "production_enablement_status": "",
        "series_location_code": OUTER_ANNULUS_DISABLED_SERIES_CODE,
        "flow_direction_code": 1,
        "geometry": {
            "graphite_outer_radius": 0.0,
            "vessel_inner_radius": 0.0,
            "vessel_outer_radius": 0.0,
            "physical_length": [0.0] * int(n_seg),
            "annulus_volume": [0.0] * int(n_seg),
            "active_core_cell_volume": [
                [0.0] * int(n_seg) for _ in range(int(n_chan))
            ],
            "conductance": [[0.0] * int(n_seg) for _ in range(int(n_chan))],
        },
        "moderator_coupling": {
            "mapping_policy": "authored_matrix",
            "interface_mode": "authored_matrix",
            "h": 0.0,
        },
        "interfaces": {"fuel_to_vessel": {"mode": "perfect", "h": 0.0}},
        "vessel_material": {"identity": "", "rho": 0.0, "cp": 0.0, "k": 0.0},
        # REV-21c79f7-01 (TASK-20260918-01): the annulus radial conductivity
        # of the primary fuel [W/(m.K)]. The canonical disabled value is the
        # 0 placeholder (never consumed - the annulus folds away); an enabled
        # deck REQUIRES a positive sourced value (the validator refuses
        # missing/zero by name).
        "fuel_conductivity": 0.0,
        "cavity": {
            "enabled": False,
            "temperature": 0.0,
            "mode": "ua",
            "mode_code": 1,
            "vessel_ua": [0.0] * int(n_seg),
            "vessel_area": [0.0] * int(n_seg),
            "vessel_emissivity": [0.0] * int(n_seg),
            "loop_surfaces": [],
            "loop_surface_codes": [],
        },
        "precursor_importance": {"policy": "", "weights": [0.0] * int(n_seg)},
        "initialization": {"annulus_temperature": 0.0, "vessel_temperature": 0.0},
        # Annulus fission (plan §5.1; TASK-20260918-01 P1): the canonical
        # DISABLED fission record - feature off, zero fraction vectors, the
        # first-release policies. An authored block is mirrored in BOTH
        # states (dataset-identity convention: the disabled generated record
        # and fingerprint name the recorded policies; nonzero vectors on any
        # disabled state refuse below and in the validator).
        "fission": {
            "enabled": False,
            "coupling_policy": "local_same_fraction",
            "coupling_policy_code": 1,
            "source_fraction": [0.0] * int(n_seg),
            "heat_deposition_fraction": [0.0] * int(n_seg),
            "annulus_temperature_feedback_policy": "zero_credit",
            "annulus_temperature_feedback_policy_code": 1,
            "poison_flux_exposure_policy": "zero_credit",
            "poison_flux_exposure_policy_code": 1,
        },
    }
    # Mirrored dataset-identity fields (both states).
    cfg["dataset_id"] = str(block.get("dataset_id") or "")
    cfg["maturity"] = str(block.get("maturity") or "")
    cfg["geometry_policy"] = str(block.get("geometry_policy") or "strict_physical")
    cfg["inventory_policy"] = str(block.get("inventory_policy") or "")
    enablement = block.get("production_enablement")
    if isinstance(enablement, Mapping):
        cfg["production_enablement_status"] = str(enablement.get("status") or "")
    # --- annulus fission (plan §5.1; TASK-20260918-01 P1) --------------------
    # Mirrored in BOTH states: the disabled generated record and fingerprint
    # name the recorded fission policies (the dataset-identity convention).
    # Fail-closed even for callers that skipped load_plant: unsupported
    # policy names, a fission-enabled deck on a disabled annulus, missing
    # fission-enabled fields, and nonzero vectors on any disabled state
    # refuse here with the path and the offending value.
    fission = block.get("fission")
    if fission is not None:
        if not isinstance(fission, Mapping):
            fail(
                label,
                f"{OUTER_ANNULUS_BLOCK_KEY}.fission",
                f"must be a mapping, got {type(fission).__name__}",
            )
        fission_cfg = cfg["fission"]
        fission_enabled = bool(fission.get("enabled", False))
        if fission_enabled and not enabled:
            fail(
                label,
                f"{OUTER_ANNULUS_BLOCK_KEY}.fission.enabled",
                "annulus fission requires the outer_fuel_annulus enabled; got "
                f"fission.enabled=true with enabled={enabled!r}",
            )
        coupling = str(fission.get("coupling_policy") or "local_same_fraction")
        if coupling not in OUTER_ANNULUS_FISSION_COUPLING_POLICIES:
            fail(
                label,
                f"{OUTER_ANNULUS_BLOCK_KEY}.fission.coupling_policy",
                f"must be one of {list(OUTER_ANNULUS_FISSION_COUPLING_POLICIES)} "
                f"(plan §5.1), got {coupling!r}",
            )
        if fission_enabled:
            # A fission-enabled deck must name BOTH distributions and BOTH
            # policies explicitly (the validator refuses first for a loaded
            # deck; these defensive refusals cover skipped-validation
            # callers).
            for fraction_key in ("source_fraction", "heat_deposition_fraction"):
                node = fission.get(fraction_key)
                if not isinstance(node, Mapping) or "value" not in node:
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{fraction_key}",
                        "missing required fission fraction distribution on a "
                        "fission-enabled deck (plan §5.1; the validator "
                        "refuses the same deck by name)",
                    )
            for policy_key, policies in (
                ("annulus_temperature_feedback_policy", OUTER_ANNULUS_FISSION_FEEDBACK_POLICIES),
                ("poison_flux_exposure_policy", OUTER_ANNULUS_FISSION_EXPOSURE_POLICIES),
            ):
                if fission.get(policy_key) is None:
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{policy_key}",
                        f"missing required policy on a fission-enabled deck "
                        f"(one of {list(policies)}; plan §5.5/§5.6)",
                    )
        fission_cfg["enabled"] = fission_enabled
        fission_cfg["coupling_policy"] = coupling
        fission_cfg["coupling_policy_code"] = int(
            OUTER_ANNULUS_FISSION_COUPLING_POLICY_CODES[coupling]
        )
        for fraction_key in ("source_fraction", "heat_deposition_fraction"):
            node = _outer_annulus_optional_quantity(
                fission, fraction_key, label, f"{OUTER_ANNULUS_BLOCK_KEY}.fission"
            )
            if node is not None:
                values = quantity_value(node)
                if not isinstance(values, list) or len(values) != int(n_seg):
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{fraction_key}.value",
                        f"must be a {n_seg}-entry array, got {values!r}",
                    )
                numbers = [float(item) for item in values]
                if any(not math.isfinite(item) or item < 0.0 for item in numbers):
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{fraction_key}.value",
                        f"every entry must be finite and >= 0, got {values!r}",
                    )
                fraction_sum = math.fsum(numbers)
                if not (
                    0.0 <= fraction_sum
                    and 1.0 - fraction_sum
                    >= OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE
                ):
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{fraction_key}.value",
                        f"the fraction sum {fraction_sum!r} violates the retained-share rule "
                        f"1 - fsum(f) >= {OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE!r} "
                        f"(the fraction sum {fraction_sum!r} violates the strict "
                        "0 <= sum_j f_a,j < 1 bound with the retained-share margin; "
                        "plan §5.2: the active core "
                        "keeps the retained share F_c = 1 - F_a >= "
                        f"{OUTER_ANNULUS_FISSION_MIN_RETAINED_CORE_SHARE!r}; "
                        "no silent renormalization - fix the authored "
                        "distribution)",
                    )
                if any(item > 0.0 for item in numbers) and not (enabled and fission_enabled):
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{fraction_key}.value",
                        f"nonzero fission fractions {values!r} require BOTH "
                        f"the outer_fuel_annulus enabled and fission.enabled="
                        f"true; got enabled={enabled!r}, fission.enabled="
                        f"{fission_enabled!r} (all fission fractions must be "
                        "zero when the feature or the annulus fission is "
                        "disabled)",
                    )
                fission_cfg[fraction_key] = numbers
        for policy_key, code_key, code_map in (
            (
                "annulus_temperature_feedback_policy",
                "annulus_temperature_feedback_policy_code",
                OUTER_ANNULUS_FISSION_FEEDBACK_POLICY_CODES,
            ),
            (
                "poison_flux_exposure_policy",
                "poison_flux_exposure_policy_code",
                OUTER_ANNULUS_FISSION_EXPOSURE_POLICY_CODES,
            ),
        ):
            authored = fission.get(policy_key)
            if authored is None:
                continue
            policy_value = str(authored)
            if policy_value not in code_map:
                fail(
                    label,
                    f"{OUTER_ANNULUS_BLOCK_KEY}.fission.{policy_key}",
                    "unsupported policy name (the only first-release policy "
                    f"is zero_credit - plan §5.5/§5.6), got {policy_value!r}",
                )
            fission_cfg[policy_key] = policy_value
            fission_cfg[code_key] = int(code_map[policy_value])
        # local_same_fraction equality (the enabled-deck check mirrors the
        # validator: elementwise f^S = f^Q within tolerance PLUS the
        # per-segment same-active-branch requirement; the disabled state
        # carries zeros by the refusal above).
        if (
            fission_cfg["coupling_policy"] == "local_same_fraction"
            and fission_cfg["enabled"]
        ):
            for index, (s_entry, q_entry) in enumerate(
                zip(
                    fission_cfg["source_fraction"],
                    fission_cfg["heat_deposition_fraction"],
                )
            ):
                if abs(s_entry - q_entry) > OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE:
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.source_fraction.value[{index}]",
                        "the local_same_fraction policy requires elementwise "
                        f"f^S = f^Q: source_fraction[{index}] {s_entry!r} != "
                        f"heat_deposition_fraction[{index}] {q_entry!r} beyond "
                        f"the {OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE!r} "
                        "equality tolerance",
                    )
                if (s_entry <= 0.0) != (q_entry <= 0.0):
                    fail(
                        label,
                        f"{OUTER_ANNULUS_BLOCK_KEY}.fission.source_fraction.value[{index}]",
                        "the local_same_fraction policy requires the same "
                        "active/inactive branch per segment: "
                        f"source_fraction[{index}] {s_entry!r} and "
                        f"heat_deposition_fraction[{index}] {q_entry!r} "
                        "disagree on the active/inactive branch - a "
                        "source-positive/heat-zero segment would arm the "
                        "precursor-production split while the thermal split "
                        "stays folded, and a heat-positive/source-zero "
                        "segment would deposit fission power with no fission "
                        "events, even inside the equality tolerance",
                    )
    if not enabled:
        return cfg

    # --- enabled overlay (the deck is validated; defensive refusals here
    #     cover callers that skipped load_plant) ---------------------------
    if not isinstance(block.get("enabled"), bool):
        fail(label, f"{OUTER_ANNULUS_BLOCK_KEY}.enabled", "must be a boolean")
    series = (block.get("topology") or {}).get("series_location_code") if isinstance(
        block.get("topology"), Mapping
    ) else None
    if series not in OUTER_ANNULUS_SERIES_CODES or series == OUTER_ANNULUS_DISABLED_SERIES_CODE:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.topology.series_location_code",
            f"an enabled deck records series code 2 or 3 (plan §6.1), got {series!r}",
        )
    direction = (block.get("topology") or {}).get("flow_direction_code") if isinstance(
        block.get("topology"), Mapping
    ) else None
    if direction not in OUTER_ANNULUS_DIRECTION_CODES:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.topology.flow_direction_code",
            f"an enabled deck records direction code 1 or 2 (plan §6.1), got {direction!r}",
        )
    if cfg["inventory_policy"] not in OUTER_ANNULUS_INVENTORY_POLICIES:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.inventory_policy",
            f"must be one of {list(OUTER_ANNULUS_INVENTORY_POLICIES)} on an "
            f"enabled deck, got {cfg['inventory_policy']!r}",
        )
    if cfg["geometry_policy"] not in OUTER_ANNULUS_GEOMETRY_POLICIES:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.geometry_policy",
            f"must be one of {list(OUTER_ANNULUS_GEOMETRY_POLICIES)}, got "
            f"{cfg['geometry_policy']!r}",
        )
    if "volume_tolerance" in block:
        cfg["volume_tolerance"] = float(quantity_value(block["volume_tolerance"]))
    cfg["enabled"] = True
    cfg["series_location_code"] = int(series)
    cfg["flow_direction_code"] = int(direction)
    geometry = block["geometry"]
    cfg["geometry"]["graphite_outer_radius"] = float(quantity_value(geometry["graphite_outer_radius"]))
    cfg["geometry"]["vessel_inner_radius"] = float(quantity_value(geometry["vessel_inner_radius"]))
    cfg["geometry"]["vessel_outer_radius"] = float(quantity_value(geometry["vessel_outer_radius"]))
    cfg["geometry"]["physical_length"] = [
        float(item) for item in quantity_value(geometry["physical_length"])
    ]
    if "annulus_volume" in geometry and isinstance(geometry["annulus_volume"], Mapping) and "value" in geometry["annulus_volume"]:
        cfg["geometry"]["annulus_volume"] = [
            float(item) for item in quantity_value(geometry["annulus_volume"])
        ]
    # P0: the executable carved active-core cell-volume matrix (an enabled
    # split_existing deck carries it under `inventory`; the validator
    # requires the positive [n_chan][n_seg] matrix with actual closure).
    carve_node = (block.get("inventory") or {}).get("active_core_cell_volume") if isinstance(
        block.get("inventory"), Mapping
    ) else None
    if isinstance(carve_node, Mapping) and "value" in carve_node:
        cfg["geometry"]["active_core_cell_volume"] = [
            [float(cell) for cell in row]
            for row in quantity_value(carve_node)
        ]
    elif enabled:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.inventory.active_core_cell_volume",
            "an enabled outer_fuel_annulus deck requires the executable "
            "active_core_cell_volume matrix (the active-core channel volumes "
            "the embedded core and precursor network consume as the "
            "ChannelMap cellVol; split_existing carves it from the legacy "
            "term, revise_total authors the legacy chain; plan §6 Phase 0)",
        )
    moderator = block.get("moderator_coupling") or {}
    graphite_iface = (block.get("interfaces") or {}).get("graphite_to_fuel") or {}
    graphite_mode = str(graphite_iface.get("mode", "film"))
    cfg["moderator_coupling"]["mapping_policy"] = str(
        moderator.get("mapping_policy") or "authored_matrix"
    )
    cfg["moderator_coupling"]["interface_mode"] = graphite_mode
    # Defensive P0 refusals (the validator refuses these first for a loaded
    # deck; this covers callers that skipped load_plant).
    if enabled and graphite_mode == "film":
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.interfaces.graphite_to_fuel.mode",
            "mode='film' is unsupported on an enabled deck (no film "
            "interface is implemented in the Modelica assembly; film is a "
            "documented future extension) - declare mode='authored_matrix'",
        )
    if graphite_mode == "authored_matrix":
        cfg["geometry"]["conductance"] = [
            [float(cell) for cell in row]
            for row in quantity_value(moderator["conductance"])
        ]
        cfg["moderator_coupling"]["h"] = 0.0
    else:
        h_node = graphite_iface.get("h")
        cfg["moderator_coupling"]["h"] = float(quantity_value(h_node))
    fuel_vessel_iface = (block.get("interfaces") or {}).get("fuel_to_vessel") or {}
    fv_mode = str(fuel_vessel_iface.get("mode", "perfect"))
    if enabled and fv_mode == "film":
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.interfaces.fuel_to_vessel.mode",
            "mode='film' is unsupported on an enabled deck (no film "
            "interface is implemented in the Modelica assembly; film is a "
            "documented future extension) - declare mode='perfect'",
        )
    fv_h = fuel_vessel_iface.get("h")
    cfg["interfaces"]["fuel_to_vessel"] = {
        "mode": fv_mode,
        "h": float(quantity_value(fv_h)) if fv_mode == "film" and fv_h is not None else 0.0,
    }
    vessel_material = block["vessel_material"]
    cfg["vessel_material"]["identity"] = str(vessel_material.get("identity") or "")
    for key in ("rho", "cp", "k"):
        cfg["vessel_material"][key] = float(quantity_value(vessel_material[key]))
    # REV-21c79f7-01 (TASK-20260918-01): the annulus radial conductivity of
    # the primary fuel. The validator has already refused a missing or
    # nonpositive value on an enabled deck (naming the path); read it here
    # for the generated record (kFuel) and the fingerprint.
    fuel_k_node = _outer_annulus_optional_quantity(block, "fuel_conductivity", label, label)
    if fuel_k_node is None:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.fuel_conductivity",
            "missing required fuel_conductivity on an enabled outer_fuel_annulus "
            "deck (the annulus radial resistance consumes the primary-fuel "
            "conductivity; refuse missing/placeholder values by name)",
        )
    cfg["fuel_conductivity"] = float(quantity_value(fuel_k_node))
    if cfg["fuel_conductivity"] <= 0.0:
        fail(
            label,
            f"{OUTER_ANNULUS_BLOCK_KEY}.fuel_conductivity.value",
            f"an enabled deck requires a positive sourced fuel_conductivity, got "
            f"{cfg['fuel_conductivity']!r} W/(m.K) (the generated Materials.kFuel "
            "= 0 axial-conduction placeholder must never drive the annulus "
            "radial resistance)",
        )
    cavity = block.get("cavity") or {}
    cavity_enabled = bool(cavity.get("enabled", False))
    cfg["cavity"]["enabled"] = cavity_enabled
    if cavity_enabled:
        cfg["cavity"]["temperature"] = float(quantity_value(cavity["temperature"]))
        cfg["cavity"]["mode"] = str(cavity["mode"])
        cfg["cavity"]["mode_code"] = int(OUTER_ANNULUS_CAVITY_MODE_CODES[cfg["cavity"]["mode"]])
        uses_ua = cfg["cavity"]["mode"] in ("ua", "ua_and_radiation")
        uses_radiation = cfg["cavity"]["mode"] in ("radiation", "ua_and_radiation")
        if uses_ua:
            cfg["cavity"]["vessel_ua"] = [float(x) for x in quantity_value(cavity["vessel_UA"])]
        if uses_radiation:
            cfg["cavity"]["vessel_area"] = [float(x) for x in quantity_value(cavity["vessel_area"])]
            cfg["cavity"]["vessel_emissivity"] = [
                float(x) for x in quantity_value(cavity["vessel_effective_emissivity"])
            ]
        surfaces = cavity.get("loop_surfaces") or []
        cfg["cavity"]["loop_surfaces"] = [str(name) for name in surfaces]
        cfg["cavity"]["loop_surface_codes"] = [
            int(OUTER_ANNULUS_CAVITY_LOOP_SURFACES[str(name)]) for name in surfaces
        ]
    precursor = block.get("precursor_importance") or {}
    cfg["precursor_importance"]["policy"] = str(precursor.get("policy") or "")
    weights_node = precursor.get("weights")
    if isinstance(weights_node, Mapping) and "value" in weights_node:
        cfg["precursor_importance"]["weights"] = [
            float(item) for item in quantity_value(weights_node)
        ]
    initialization = block["initialization"]
    cfg["initialization"]["annulus_temperature"] = float(
        quantity_value(initialization["annulus_temperature"])
    )
    cfg["initialization"]["vessel_temperature"] = float(
        quantity_value(initialization["vessel_temperature"])
    )
    return cfg


def outer_annulus_fingerprint(cfg: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of the outer-annulus dataset subset of ``cfg``.

    Covers exactly the plan §7.3 fingerprint fields: geometry (the ordered
    radii, the per-segment physical lengths, the authored annulus volumes,
    the executable carved active-core cell-volume matrix (P0), and the
    G_modAnn mapping conductances), the graphite/annulus and
    annulus/vessel interface parameterizations, topology and direction (the
    plan §6.1 codes), vessel material (identity and rho/cp/k), the annulus
    primary-fuel radial conductivity (REV-21c79f7-01), cavity data
    (state, temperature, mode, per-segment UA/area/emissivity, and the
    exposed loop surfaces), initialization, precursor importance (policy and
    weights), the inventory-allocation policy, the annulus-fission record
    (P1, plan §5.1: enabled flag, coupling policy, BOTH fraction vectors,
    and BOTH the temperature-feedback and poison-exposure policies), and
    maturity + dataset identity. Moving any covered field moves the digest
    (plan §12.1 fingerprint sensitivity; P6 category 12).

    ``first_production_target`` and the per-core dimensions are deliberately
    NOT covered: they select the generated package NAME (and the record's
    ``nChan``/``nSeg``), not the dataset physics identity.
    """

    return fingerprint(
        {
            "geometry": {
                "graphite_outer_radius": cfg["geometry"]["graphite_outer_radius"],
                "vessel_inner_radius": cfg["geometry"]["vessel_inner_radius"],
                "vessel_outer_radius": cfg["geometry"]["vessel_outer_radius"],
                "physical_length": cfg["geometry"]["physical_length"],
                "annulus_volume": cfg["geometry"]["annulus_volume"],
                "active_core_cell_volume": cfg["geometry"]["active_core_cell_volume"],
                "conductance": cfg["geometry"]["conductance"],
            },
            "moderator_coupling": cfg["moderator_coupling"],
            "interfaces": cfg["interfaces"],
            "topology": {
                "series_location_code": cfg["series_location_code"],
                "flow_direction_code": cfg["flow_direction_code"],
            },
            "vessel_material": cfg["vessel_material"],
            "fuel_conductivity": cfg["fuel_conductivity"],
            "cavity": cfg["cavity"],
            "initialization": cfg["initialization"],
            "precursor_importance": cfg["precursor_importance"],
            "inventory_policy": cfg["inventory_policy"],
            "fission": cfg["fission"],
            "maturity": cfg["maturity"],
            "dataset_id": cfg["dataset_id"],
        }
    )


def outer_annulus_fission_active(
    configured: bool,
    heat_deposition_fraction: Sequence[float],
) -> bool:
    """Effective (structurally-active) outer-annulus fission state (P2).

    TASK-20260922-03 P2 (review §4 High-2): the ONE configured-vs-active
    decision. ``configured`` is the authored ``fission.enabled`` flag;
    the split is STRUCTURALLY live only when the feature is configured
    AND the authored heat-deposition distribution is nonzero
    (``math.fsum(fQ) > 0``) -- the exact conjunction the Modelica
    ``fissionOn`` switch evaluates
    (``outerOn and fissionEnabled and sum(heatDepositionFraction) > 0``),
    so a fission-enabled deck with an all-zero distribution folds to the
    established no-fission equations in Modelica and carries no fission
    columns in Python. The manifest record keeps the CONFIGURED flag
    (``fissionEnabled``) plus both vectors; the runner contract
    (:func:`helpers.segmented_runs.outer_fuel_annulus_run_contract`)
    calls this once and drives the run shape and result columns from the
    answer. Entries are validated nonnegative upstream, so the sum test
    is the any-positive test.
    """

    return bool(configured) and math.fsum(float(x) for x in heat_deposition_fraction) > 0.0


def outer_annulus_manifest_record(plant: Mapping[str, Any]) -> dict[str, Any] | None:
    """Manifest metadata for the outer-annulus configuration (None when off).

    ``None`` when the block is absent or disabled, so disabled-run manifests
    keep their historical shape and fingerprint (the same field-omission
    pattern as ``radial_manifest_record``; planner resolution 6). When
    enabled, the JSON-safe record feeds
    ``helpers.run_results.build_run_manifest(outer_annulus_config=...)`` in
    P8 and is the identity surface the scenario layer must not contradict
    (plan §11.1): dataset id, fingerprint, maturity, topology and direction
    codes, cavity temperature, the inventory policy, and the annulus-fission
    record (P1, plan §5.1: enabled flag, coupling policy, BOTH fraction
    vectors, and BOTH the temperature-feedback and poison-exposure policies
    - explicit and manifest-visible whenever the annulus runs, including the
    fission-disabled state, so zero fission is a named configuration and
    never a silent default). ``fissionEnabled`` here is the CONFIGURED
    (authored) flag -- not the structurally-active state; the active
    decision is :func:`outer_annulus_fission_active` (P2). Temperatures
    are authored plant-degC (the manifest records authored data; the
    generated Modelica record binds kelvin).
    """

    cfg = outer_annulus_config(plant)
    if cfg is None or not cfg["enabled"]:
        return None
    fission = cfg["fission"]
    return {
        "enabled": True,
        "firstProductionTarget": str(cfg["first_production_target"]),
        "nChan": int(cfg["n_chan"]),
        "nSeg": int(cfg["n_seg"]),
        "seriesLocationCode": int(cfg["series_location_code"]),
        "flowDirectionCode": int(cfg["flow_direction_code"]),
        "geometryPolicy": str(cfg["geometry_policy"]),
        "outerAnnulusDatasetId": str(cfg["dataset_id"]),
        "outerAnnulusMaturity": str(cfg["maturity"]),
        "outerAnnulusFingerprint": outer_annulus_fingerprint(cfg),
        "inventoryPolicy": str(cfg["inventory_policy"]),
        "productionEnablementStatus": str(cfg["production_enablement_status"]),
        "cavityEnabled": bool(cfg["cavity"]["enabled"]),
        "cavityTemperatureC": float(cfg["cavity"]["temperature"]),
        "cavityLoopSurfaces": [str(name) for name in cfg["cavity"]["loop_surfaces"]],
        "fissionEnabled": bool(fission["enabled"]),
        "fissionCouplingPolicy": str(fission["coupling_policy"]),
        "fissionSourceFraction": [float(x) for x in fission["source_fraction"]],
        "fissionHeatDepositionFraction": [
            float(x) for x in fission["heat_deposition_fraction"]
        ],
        "fissionAnnulusTemperatureFeedbackPolicy": str(
            fission["annulus_temperature_feedback_policy"]
        ),
        "fissionPoisonFluxExposurePolicy": str(
            fission["poison_flux_exposure_policy"]
        ),
    }


def _display_repo_relative(path: Path, root: Path | None = None) -> str:
    base = (root or repo_root()).resolve()
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return str(path)


def lumped_manifest_source_files(
    core_dir: str | Path,
    *,
    plant_id: str = "msrr",
    root: Path | None = None,
) -> dict[str, str]:
    """SHA-256 map for run manifests: PlantData.mo, SMD, MSRR, plus plant YAML."""

    hashed: dict[str, str] = {}
    for path in require_lumped_library_paths(core_dir, root=root):
        hashed[_display_repo_relative(path, root)] = file_digest(path)
    hashed[f"data/plants/{plant_id}"] = fingerprint(load_plant(plant_id, root=root))
    return hashed


def write_stub_plant_data(core_dir: str | Path) -> Path:
    """Write a dummy ``MSRR_PlantData.mo`` for hermetic tests (no omc)."""

    path = Path(core_dir) / "generated" / LUMPED_PLANT_DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'package MSRR_PlantData "stub"; end MSRR_PlantData;\n',
        encoding="utf-8",
    )
    return path


#: The plant whose generated packages live directly in ``core/generated/``.
DEFAULT_PLANT_ID = "msrr"


def plant_generated_subdir(plant_id: str = DEFAULT_PLANT_ID) -> Path:
    """Generated-package location of a plant relative to ``core/``.

    The default plant keeps ``core/generated/``; every other plant writes
    ``core/generated/<plant_id>/`` (same file and package names, so a run
    directory loads whichever plant was copied into it).
    """

    if plant_id == DEFAULT_PLANT_ID:
        return Path("generated")
    return Path("generated") / str(plant_id)


def segmented_generated_dir(root: Path | None = None, *, plant_id: str = DEFAULT_PLANT_ID) -> Path:
    return (root or repo_root()) / "core" / plant_generated_subdir(plant_id)


def segmented_plant_data_path(
    core_dir: str | Path | None = None,
    *,
    root: Path | None = None,
    plant_id: str = DEFAULT_PLANT_ID,
) -> Path:
    """Return the segmented ``SegmentedMSR_PlantData.mo`` path of a plant.

    Prefers ``<core_dir>/generated[/<plant_id>]/SegmentedMSR_PlantData.mo``.
    A sibling copy in ``core_dir`` is accepted for flattened workdir layouts
    of the default plant only (a scaled plant must come from its own
    generated directory).
    """

    if core_dir is not None:
        core = Path(core_dir)
        generated = core / plant_generated_subdir(plant_id) / SEGMENTED_PLANT_DATA_FILE
        if generated.is_file() or plant_id != DEFAULT_PLANT_ID:
            return generated
        sibling = core / SEGMENTED_PLANT_DATA_FILE
        if sibling.is_file():
            return sibling
        return generated
    return segmented_generated_dir(root, plant_id=plant_id) / SEGMENTED_PLANT_DATA_FILE


def segmented_library_paths(
    core_dir: str | Path,
    *,
    root: Path | None = None,
    plant_id: str = DEFAULT_PLANT_ID,
) -> list[Path]:
    """PlantData, then ``SegmentedMSR.mo`` (omc load order)."""

    core = Path(core_dir)
    return [
        segmented_plant_data_path(core, root=root, plant_id=plant_id),
        core / SEGMENTED_PACKAGE_FILE,
    ]


def require_segmented_library_paths(
    core_dir: str | Path,
    *,
    root: Path | None = None,
    plant_id: str = DEFAULT_PLANT_ID,
) -> list[Path]:
    paths = segmented_library_paths(core_dir, root=root, plant_id=plant_id)
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "missing segmented Modelica sources (load PlantData first): "
            + ", ".join(str(path) for path in missing)
        )
    return paths


def segmented_load_file_text(files: Sequence[str | Path]) -> str:
    """``loadFile`` lines in PlantData → SegmentedMSR order."""

    return "".join(f'loadFile("{path}");\n' for path in files)


def copy_segmented_sources(
    core_dir: str | Path,
    workdir: str | Path,
    *,
    root: Path | None = None,
    plant_id: str = DEFAULT_PLANT_ID,
) -> list[Path]:
    """Copy PlantData + SegmentedMSR into ``workdir`` (PlantData flattened).

    ``plant_id`` selects which plant's generated PlantData is copied; the
    destination name is always ``SegmentedMSR_PlantData.mo``.
    """

    dest_dir = Path(workdir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dests: list[Path] = []
    for src in require_segmented_library_paths(core_dir, root=root, plant_id=plant_id):
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        dests.append(dest)
    return dests


def segmented_manifest_source_files(
    core_dir: str | Path,
    *,
    plant_id: str = "msrr",
    root: Path | None = None,
) -> dict[str, str]:
    """SHA-256 map: PlantData.mo, SegmentedMSR.mo, plus plant YAML."""

    hashed: dict[str, str] = {}
    for path in require_segmented_library_paths(core_dir, root=root, plant_id=plant_id):
        hashed[_display_repo_relative(path, root)] = file_digest(path)
    hashed[f"data/plants/{plant_id}"] = fingerprint(load_plant(plant_id, root=root))
    return hashed


def write_stub_segmented_plant_data(core_dir: str | Path) -> Path:
    """Write a dummy ``SegmentedMSR_PlantData.mo`` for hermetic tests (no omc)."""

    path = Path(core_dir) / "generated" / SEGMENTED_PLANT_DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'package SegmentedMSR_PlantData "stub"\nend SegmentedMSR_PlantData;\n',
        encoding="utf-8",
    )
    return path


__all__ = [
    "DEFAULT_PLANT_ID",
    "plant_generated_subdir",
    "ANNULAR_FLUID_MODES",
    "ANNULAR_FLUID_MODE_CODES",
    "CORE_MATURITY_VALUES",
    "FLOW_DIRECTIONS",
    "FLOW_DIRECTION_CODES",
    "LUMPED_LIBRARY_FILE",
    "LUMPED_MODEL_FILE",
    "LUMPED_PLANT_DATA_FILE",
    "OUTER_ANNULUS_BLOCK_KEY",
    "OUTER_ANNULUS_CAVITY_LOOP_SURFACES",
    "OUTER_ANNULUS_CAVITY_MODES",
    "OUTER_ANNULUS_CAVITY_MODE_CODES",
    "OUTER_ANNULUS_CONTACT_MODES",
    "OUTER_ANNULUS_CONTACT_MODE_CODES",
    "OUTER_ANNULUS_DIRECTION_CODES",
    "OUTER_ANNULUS_ENABLEMENT_STATUSES",
    "OUTER_ANNULUS_FISSION_COUPLING_POLICIES",
    "OUTER_ANNULUS_FISSION_COUPLING_POLICY_CODES",
    "OUTER_ANNULUS_FISSION_EXPOSURE_POLICIES",
    "OUTER_ANNULUS_FISSION_EXPOSURE_POLICY_CODES",
    "OUTER_ANNULUS_FISSION_FEEDBACK_POLICIES",
    "OUTER_ANNULUS_FISSION_FEEDBACK_POLICY_CODES",
    "OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE",
    "OUTER_ANNULUS_GEOMETRY_POLICIES",
    "OUTER_ANNULUS_GEOMETRY_POLICY_CODES",
    "OUTER_ANNULUS_GRAPHITE_INTERFACE_MODES",
    "OUTER_ANNULUS_GRAPHITE_INTERFACE_CODES",
    "OUTER_ANNULUS_INVENTORY_POLICIES",
    "OUTER_ANNULUS_INVENTORY_TOLERANCE",
    "OUTER_ANNULUS_SERIES_CODES",
    "OUTER_ANNULUS_SUPPORTED_CORES",
    "OUTER_ANNULUS_VOLUME_TOLERANCE_DEFAULT",
    "POISON_MATURITY_APPROVED",
    "POISON_MATURITY_VALUES",
    "RADIAL_BLOCK_KEY",
    "RADIAL_CHANNEL_MEANINGS",
    "RADIAL_CHANNEL_MEANING_CODES",
    "RADIAL_FRACTION_TOLERANCE",
    "RADIAL_GEOMETRY_POLICIES",
    "RADIAL_GEOMETRY_POLICY_CODES",
    "RADIAL_INTERFACE_MODES",
    "RADIAL_INTERFACE_MODE_CODES",
    "RADIAL_LATTICE_GEOMETRY_DEFAULT",
    "RADIAL_LATTICE_GEOMETRY_VALUES",
    "RADIAL_VOLUME_TOLERANCE_DEFAULT",
    "SEGMENTED_PACKAGE_FILE",
    "SEGMENTED_PLANT_DATA_FILE",
    "annular_loop_fingerprint",
    "canonical_json",
    "copy_lumped_sources",
    "copy_segmented_sources",
    "data_root",
    "file_digest",
    "fingerprint",
    "is_quantity",
    "load_plant",
    "lumped_generated_dir",
    "lumped_library_paths",
    "lumped_load_file_text",
    "lumped_manifest_source_files",
    "lumped_plant_data_path",
    "outer_annulus_config",
    "outer_annulus_fingerprint",
    "outer_annulus_manifest_record",
    "plants_root",
    "poison_dataset_record",
    "poisons_include_path",
    "quantity_unit",
    "quantity_value",
    "r9_derived",
    "radial_config",
    "radial_fingerprint",
    "radial_loop_channel_count",
    "radial_manifest_record",
    "radial_stack_segment_count",
    "repo_root",
    "require_lumped_library_paths",
    "require_segmented_library_paths",
    "segmented_generated_dir",
    "segmented_library_paths",
    "segmented_load_file_text",
    "segmented_manifest_source_files",
    "segmented_plant_data_path",
    "validate_plant",
    "vol_loop_list",
    "walk_quantities",
    "write_stub_plant_data",
    "write_stub_segmented_plant_data",
]
