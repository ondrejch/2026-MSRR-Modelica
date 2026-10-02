#!/usr/bin/env python3
"""Emit a short Modelica wrapper that rebinds structural array sizes.

OpenModelica 1.27 ``-override=`` cannot resize arrays. Scenario YAML that
changes ``numUhxSteps`` (startup to-power ramps, UHX trip) compiles a thin
``extends Base(...)`` model into the run directory. Default CLI named
scenarios still use the committed dedicated vehicles in ``MSRR.mo``; this
emitter is the path for ``--scenario_file`` overlays, every segmented
startup scenario (the base ``startup`` included; on the four trim rigs the
wrapper also carries the lumped startup semantics, see
``_SEGMENTED_STARTUP_CORE``), and digit-parity tests against those
dedicated models.

Regenerate a wrapper::

    python3.12 -m helpers.emit_scenario_wrapper --scenario startup_to_100kw --core_model 1r --out 00runs/tmp/omc
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Mapping

from helpers.emit_modelica_plant import mo_array, mo_number
from helpers.plant_config import repo_root
from helpers.plant_config import quantity_value
from helpers.scenario_config import (
    CORE_CHOICES,
    legacy_core_refusal,
    resolve_scenario,
    resolve_source_amplitudes,
    resolve_uhx_demand,
    scenario_core_refusal,
    yaml_core_key,
)

GENERATED_HEADER = (
    "GENERATED from {source} - do not edit. "
    "python3.12 -m helpers.emit_scenario_wrapper"
)

# Dedicated committed vehicles already exist for these cases. The wrapper
# EXTENDS the unsized base (criticality / trim thermal-SS), matching the
# committed forks digit-for-digit.
_LUMPED_UHX_RAMP_BASE = {
    ("startup_to_100kw", "1r"): "MSRR.MSRRstartUpCriticality",
    ("startup_to_100kw", "9r"): "MSRR.MSRRstartUpCriticality9R",
    ("startup_to_1mw", "1r"): "MSRR.MSRRstartUpCriticality",
    ("startup_to_1mw", "9r"): "MSRR.MSRRstartUpCriticality9R",
}
_LUMPED_TRIP_BASE = {
    "1r": "MSRR.MSRRuhxNominalTrimThermalSS",
    "9r": "MSRR.MSRRuhxNominalTrim9RThermalSS",
}
_SEGMENTED_TRIM_BASE = {
    "1r": "SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS",
    "9r": "SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS",
    # 1-channel x 10-axial-segment 1R core (TASK-20260906-01); segmented
    # package only -- there is no legacy counterpart to wrap.
    "1r10seg": "SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS",
    # 5x5-radial x 10-axial-segment 1R core (TASK-20260906-02); segmented
    # package only -- there is no legacy counterpart to wrap.
    "r5x5_z10": "SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS",
}

#: Outer-annulus wrapper bases (TASK-20260917-01 P8; plan §10.6): the
#: plant-driven CoreVesselAssembly vehicles an ENABLED outer_fuel_annulus
#: dataset must execute (the runner contract refuses cores without an entry
#: before any wrapper is emitted; planner resolution 2 keeps the four
#: established bases above on the disabled path).
#:
#: TASK-20260923-01 P1 (rev024 §6 Phase 1): the coupled-steady-state
#: entry points at the dedicated production class
#: ``R1MSRRuhx10SegOuterAnnulusCoupledSS`` (both core init modes
#: SteadyState, zero perturbation fixed in the class); the bounded-startup
#: entry points at the shipped-defaults TrimThermalSS wrapper.
_SEGMENTED_OUTER_ANNULUS_BASE = {
    "1r10seg": (
        "SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusCoupledSS"
    ),
}
_SEGMENTED_OUTER_ANNULUS_BOUNDED_STARTUP_BASE = {
    "1r10seg": (
        "SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS"
    ),
}

#: Segmented startup wrappers on the four trim bases (review 2026-10-01
#: H2/H3). The trim rigs are 1 MW steady-state harnesses: FixedStart core at
#: the qualified 1 MW profile, SteadyState loop, PKE_T trim frozen at the
#: t = 0 pump flow. A startup wrapper gives them the zero-power startup
#: semantics of the lumped MSRRstartUpCriticality models:
#:   - isothermal core (and 9R upper plenum) at the scenario's
#:     ``init.per_core`` temperature, which must equal the deck's
#:     ``ssReferenceTemperature`` (the qualified feedback setpoints give zero
#:     feedback only there; asserted at initialization); the SteadyState loop
#:     then initializes isothermal too, since the demand is zero;
#:   - ``forcing.heat_loss`` -> ``heatLossEnabled`` with the surroundings at
#:     the startup temperature (the lumped heatLossTinf binding);
#:   - the reactivity staircase re-referenced like the lumped
#:     ``flowReferencedStaircase``. PKE_T freezes rhoTrim = loss(FF(0)) at the
#:     free-convection flow, so each step gets the offset
#:     loss(FF_ref) - loss(FF(0)); trim + offset = loss(FF_ref), the same net
#:     compensation as the lumped rho_0nom = loss(1) plus the offset
#:     -(loss(1) - loss(FF_stage)). FF_ref is the pump stage in effect at the
#:     step (``flow_dependent_critical``) or FF = 1
#:     (``nominal_flow_critical``). The loss is the closed-form steady state
#:     of the rig's own precursor network, evaluated on the executed
#:     parameters, and the wrapper asserts at initialization that it
#:     reproduces the frozen trim.
#: value: (PlantData core package, circulation-loss call with ``{ff}``,
#: thermal-start modifiers with ``{T}``).
_SEGMENTED_NETWORK_LOSS = (
    "SegmentedMSR.Nuclear.networkCirculationLossPcm(prec.beta, prec.lambda, "
    "prec.LAMBDA, prec.map.cellVol, prec.map.fSalt, prec.map.flowFrac, "
    "prec.impCell, prec.impLoop, prec.volLoop, prec.vDotNom, {ff})"
)
_SEGMENTED_ZONE_LOSS_9R = (
    "SegmentedMSR.Reactors.zoneChainCirculationLossPcm9R(prec.beta, "
    "prec.lambda, prec.LAMBDA, SegmentedMSR.Reactors.cellVolChain9R, "
    "SegmentedMSR.Reactors.fSaltGlobal9R, prec.impCell, "
    "SegmentedMSR.Reactors.flowFracRegions9R, SegmentedMSR.Reactors.cellStart9R, "
    "SegmentedMSR.Reactors.cellEnd9R, prec.volUP, prec.impPot, prec.impLoop, "
    "prec.volLoop, prec.vDotNom, {ff})"
)
_SEGMENTED_CORE_START = (
    "    core(",
    "      T_fuel_0 = fill({T}, map.nChan, map.nSeg),",
    "      T_mod_0 = fill({T}, map.nChan, map.nSeg))",
)
_SEGMENTED_ZONE_START_9R = tuple(
    line
    for zone in range(1, 5)
    for line in (
        f"    Z{zone}(",
        f"      T_fuel_0 = fill({{T}}, 1, mapZ{zone}.nSeg),",
        f"      T_mod_0 = fill({{T}}, 1, mapZ{zone}.nSeg))",
    )
) + ("    upperPlenum(T_0 = {T})",)
_SEGMENTED_STARTUP_CORE = {
    "1r": ("Core1R", _SEGMENTED_NETWORK_LOSS, _SEGMENTED_CORE_START),
    "1r10seg": ("Core1R_10Seg", _SEGMENTED_NETWORK_LOSS, _SEGMENTED_CORE_START),
    "r5x5_z10": ("CoreR5x5_Z10", _SEGMENTED_NETWORK_LOSS, _SEGMENTED_CORE_START),
    "9r": ("Core9R", _SEGMENTED_ZONE_LOSS_9R, _SEGMENTED_ZONE_START_9R),
}
#: Startup reactivity references the segmented wrapper can express
#: (``forcing.reactivity_pcm.reference``). ``historical_live_compensation``
#: needs the lumped live circulating-fuel compensation and has no segmented
#: counterpart, so it is refused.
SEGMENTED_STARTUP_REFERENCES = ("flow_dependent_critical", "nominal_flow_critical")
_KELVIN_OFFSET = 273.15


def _kelvin(quantity: Any) -> float:
    """A scenario temperature quantity in kelvin (degC or K)."""

    value = float(quantity_value(quantity))
    unit = str(quantity.get("unit", "")) if isinstance(quantity, Mapping) else ""
    if unit == "degC":
        return value + _KELVIN_OFFSET
    if unit == "K":
        return value
    raise ValueError(f"startup init temperature needs unit degC or K, got {unit!r}")


def _segmented_startup_temperature(
    scenario: Mapping[str, Any], core_model: str, plant_package: str
) -> str:
    """Modelica expression for the isothermal startup temperature [K].

    ``init.per_core.<core>`` (TF1, TF2, TG) when the scenario lists the core
    (it must be isothermal); otherwise the deck's ``ssReferenceTemperature``.
    """

    per_core = ((scenario.get("init") or {}).get("per_core") or {}).get(
        yaml_core_key(core_model)
    )
    if not per_core:
        return f"SegmentedMSR_PlantData.{plant_package}.ssReferenceTemperature"
    temps = [_kelvin(per_core[key]) for key in ("TF1", "TF2", "TG") if key in per_core]
    if not temps:
        raise ValueError(
            f"init.per_core.{yaml_core_key(core_model)} lists no TF1/TF2/TG temperature"
        )
    if max(temps) - min(temps) > 1e-6:
        raise ValueError(
            f"init.per_core.{yaml_core_key(core_model)} is not isothermal "
            f"({min(temps):.6f}..{max(temps):.6f} K): the segmented startup "
            "wrapper starts the core isothermally"
        )
    return mo_number(temps[-1])


def _segmented_startup_block(
    scenario: Mapping[str, Any],
    core_model: str,
    model_name: str,
    *,
    has_rho: bool,
) -> dict[str, list[str]]:
    """Declarations, modifiers and initial asserts of a segmented startup
    wrapper on a trim base (see :data:`_SEGMENTED_STARTUP_CORE`)."""

    plant_package, loss_call, start_lines = _SEGMENTED_STARTUP_CORE[core_model]
    forcing = scenario.get("forcing") or {}
    t_expr = _segmented_startup_temperature(scenario, core_model, plant_package)
    t_ref = f"SegmentedMSR_PlantData.{plant_package}.ssReferenceTemperature"
    decls = [
        "  parameter SegmentedMSR.Units.Temperature startupTemperature = "
        f"{t_expr}",
        '    "Isothermal zero-power startup temperature [K] (scenario '
        'init.per_core): core cells, 9R upper plenum and heat-loss surroundings";',
    ]
    modifiers: list[str] = []
    asserts = [
        f"  assert(startupTemperature > {t_ref} - 1e-6 and startupTemperature < {t_ref} + 1e-6,",
        f'    "{model_name}: the isothermal startup temperature must equal '
        f"{plant_package}.ssReferenceTemperature (the qualified feedback "
        'setpoints give zero feedback only there)");',
    ]
    if has_rho:
        policy = str(
            ((forcing.get("reactivity_pcm") or {}).get("reference"))
            or "flow_dependent_critical"
        )
        if policy not in SEGMENTED_STARTUP_REFERENCES:
            raise ValueError(
                f"startup reactivity reference {policy!r} is unsupported with "
                "--package segmented (supported: "
                f"{', '.join(SEGMENTED_STARTUP_REFERENCES)}; the live "
                "circulating-fuel compensation has no segmented counterpart)"
            )
        if policy == "flow_dependent_critical":
            reference = "startupStageFlow[startupStepStage[i]]"
            reference_doc = "the pump stage in effect at the step"
        else:
            reference = "1"
            reference_doc = "nominal flow FF = 1"
        decls += [
            "  final parameter Real startupStageFlow[primaryPump.numRampUp + 1] = "
            "cat(1, {primaryPump.freeConvFF}, primaryPump.rampUpTo)",
            '    "Primary-pump flow fraction of each stage (free convection, '
            'then each ramp target)";',
            "  final parameter Integer startupInitialStage = 1 + sum({(if "
            "primaryPump.rampUpTime[k] <= 0 then 1 else 0) for k in "
            "1:primaryPump.numRampUp})",
            '    "Pump stage in effect at t = 0, where PKE_T freezes its trim";',
            "  final parameter Integer startupStepStage[numExternalReactivitySteps] "
            "= {1 + sum({(if externalReactivityStepTime[i] >= "
            "primaryPump.rampUpTime[k] then 1 else 0) for k in "
            "1:primaryPump.numRampUp}) for i in 1:numExternalReactivitySteps}",
            '    "Pump stage in effect at each staircase step";',
            "  final parameter Real startupTrimLossPcm = "
            + loss_call.format(ff="startupStageFlow[startupInitialStage]"),
            '    "Frozen PKE_T trim [pcm]: the circulation loss at the t = 0 flow";',
            "  final parameter Real startupStaircaseOffsetPcm"
            "[numExternalReactivitySteps] = {"
            + loss_call.format(ff=reference)
            + " - startupTrimLossPcm for i in 1:numExternalReactivitySteps}",
            '    "Offset added to each staircase amplitude [pcm] '
            f"(forcing.reactivity_pcm.reference = {policy}: 0 pcm is critical "
            f'at {reference_doc})";',
        ]
        asserts += [
            "  assert(pke.rhoTrim*1e5 > startupTrimLossPcm - 1e-4 and "
            "pke.rhoTrim*1e5 < startupTrimLossPcm + 1e-4,",
            f'    "{model_name}: the closed-form circulation loss does not '
            'reproduce the frozen PKE_T trim");',
        ]
    if forcing.get("heat_loss"):
        modifiers += [
            "    heatLossEnabled = true",
            "    heatLossTinf = startupTemperature",
        ]
    modifiers += [line.format(T="startupTemperature") for line in start_lines]
    return {"decls": decls, "modifiers": modifiers, "initial_asserts": asserts}


def _source_label(scenario: Mapping[str, Any]) -> str:
    raw = scenario.get("_path")
    if not raw:
        return f"scenario {scenario.get('id')}"
    path = Path(raw)
    try:
        return path.resolve().relative_to(repo_root().resolve()).as_posix()
    except ValueError:
        return str(path)


def wrapper_model_name(
    scenario_id: str,
    core_model: str,
    *,
    case: str | None = None,
) -> str:
    """Standalone (not ``within``) class name safe as a filename stem.

    The scenario/case ids are sanitized into single-token stems (``.``,
    ``/`` and ``\\`` become ``_``) and each sanitized stem is then REQUIRED
    to match the plain-identifier grammar ``[A-Za-z0-9_]+`` (rev022 M-7):
    an id carrying spaces, quotes, newlines, dashes, or any other
    non-identifier character would otherwise land verbatim in
    ``model {model_name}`` and break the generated Modelica, so it is
    refused with a ``ValueError``.

    The mapping is a deliberate, deterministic many-to-one collapse: ids
    differing only in ``.``/``/``/``\\`` -- e.g. ``a.b``, ``a/b``, ``a_b``
    -- all map to the same stem ``a_b`` (pinned by
    ``tests/test_scenario_yaml.py``). Callers emitting several wrappers
    into one directory must therefore key artifacts by wrapper name, never
    by raw scenario id.
    """

    def _sanitize(value: str) -> str:
        # A scenario/case id may carry dotted sub-names; replace both dots
        # and path separators, then require the result to be a valid,
        # single-token filename stem (no ``/`` leaking into an emitted
        # Modelica name, and no spaces/quotes/newlines either: rev022 M-7).
        stem = value.replace(".", "_").replace("/", "_").replace("\\", "_")
        if not re.fullmatch(r"[A-Za-z0-9_]+", stem):
            raise ValueError(
                f"scenario/case id {value!r} does not sanitize to a "
                "single-token identifier stem ([A-Za-z0-9_]+ after mapping "
                "'.', '/', and \\' to '_'); refusing to emit a wrapper "
                "name from it"
            )
        return stem

    parts = ["MSRR_Scenario", _sanitize(scenario_id), core_model]
    if case:
        parts.append(_sanitize(str(case)))
    return "_".join(parts)


def _outer_annulus_base_for_policy(
    core_model: str,
    init_policy: "str | None" = None,
) -> str:
    """Outer-annulus wrapper base for ``core_model`` under ``init_policy``.

    TASK-20260923-01 P1: ``coupled_steady_state`` (the default, ``None``
    resolves it) extends the dedicated CoupledSS production class;
    ``bounded_startup`` extends the shipped-defaults TrimThermalSS twin.
    A ``KeyError`` here is a caller bug - the runner contract refuses
    unsupported cores before any wrapper is emitted - but the message
    names the feature for a direct CLI caller; unknown policy names
    raise ``ValueError``.
    """
    from helpers.segmented_runs import normalize_outer_annulus_init_policy

    policy = normalize_outer_annulus_init_policy(init_policy)
    table = (
        _SEGMENTED_OUTER_ANNULUS_BASE
        if policy == "coupled_steady_state"
        else _SEGMENTED_OUTER_ANNULUS_BOUNDED_STARTUP_BASE
    )
    try:
        return table[core_model]
    except KeyError:
        raise KeyError(
            f"no outer-annulus wrapper base for core {core_model!r} "
            "(the plan §10.6 CoreVesselAssembly vehicle set is "
            f"{sorted(_SEGMENTED_OUTER_ANNULUS_BASE)})"
        ) from None


def wrapper_base_model(
    scenario: Mapping[str, Any],
    core_model: str,
    *,
    package: str = "legacy",
    case: str | None = None,
    outer_annulus: bool = False,
    outer_annulus_init_policy: "str | None" = None,
) -> str:
    scenario_id = str(scenario.get("id") or "")
    if package == "segmented":
        if outer_annulus:
            # P8 (plan §10.6): an enabled outer_fuel_annulus dataset runs on
            # the CoreVesselAssembly wrapper vehicle. A KeyError here is a
            # caller bug - the runner contract refuses unsupported cores
            # before any wrapper is emitted - but the message names the
            # feature for a direct CLI caller. P1 (TASK-20260923-01)
            # routes the base through the initialization policy: the
            # coupled steady state extends CoupledSS, the bounded-startup
            # twin extends the shipped-defaults TrimThermalSS vehicle.
            return _outer_annulus_base_for_policy(
                core_model, init_policy=outer_annulus_init_policy
            )
        return _SEGMENTED_TRIM_BASE[core_model]
    if case == "uhx_trip" or scenario.get("kind") == "transients":
        return _LUMPED_TRIP_BASE[core_model]
    key = (scenario_id, core_model)
    if key in _LUMPED_UHX_RAMP_BASE:
        return _LUMPED_UHX_RAMP_BASE[key]
    support = (scenario.get("package_support") or {}).get("legacy") or {}
    explicit = (support.get("wrapper_base_by_core") or {}).get(yaml_core_key(core_model))
    if explicit:
        return str(explicit)
    if scenario.get("kind") == "startup":
        return {
            "1r": "MSRR.MSRRstartUpCriticality",
            "9r": "MSRR.MSRRstartUpCriticality9R",
        }[core_model]
    raise KeyError(
        f"no wrapper base for scenario {scenario_id!r} core {core_model!r} package {package!r}"
    )


def _uhx_case(scenario: Mapping[str, Any], case: str | None) -> Mapping[str, Any] | None:
    if scenario.get("kind") != "transients":
        return None
    wanted = case or "uhx_trip"
    for row in scenario.get("cases") or []:
        if row.get("id") == wanted or (
            row.get("family") == "uhx_trip" and wanted == "uhx_trip"
        ):
            return row
    return None


def emit_wrapper_text(
    scenario: Mapping[str, Any],
    *,
    core_model: str,
    package: str = "legacy",
    case: str | None = None,
    outer_annulus: bool = False,
    outer_annulus_init_policy: "str | None" = None,
) -> tuple[str, str]:
    """Return ``(model_name, Modelica text)`` for a structural wrapper.

    ``outer_annulus=True`` (segmented package; TASK-20260917-01 P8) extends
    the plan §10.6 outer-annulus vehicle instead of the established bare
    base, so a generated wrapper and a direct vehicle selection describe
    the same executed model. ``outer_annulus_init_policy``
    (TASK-20260923-01 P1) selects the coupled-steady-state CoupledSS base
    (default) or the bounded-startup TrimThermalSS twin.
    """

    model_name = wrapper_model_name(str(scenario["id"]), core_model, case=case)
    base = wrapper_base_model(
        scenario,
        core_model,
        package=package,
        case=case,
        outer_annulus=outer_annulus,
        outer_annulus_init_policy=outer_annulus_init_policy,
    )
    uhx_case = _uhx_case(scenario, case)
    times, amps = resolve_uhx_demand(scenario.get("forcing") or {}, uhx_case)
    n_steps = len(times)
    source = _source_label(scenario)
    lines = [
        f"// {GENERATED_HEADER.format(source=source)}",
        f'model {model_name} "generated scenario wrapper"',
        f"  extends {base}(",
        f"    numUhxSteps = {n_steps},",
        f"    uhxDemandStepTime = {mo_array(times)},",
        f"    uhxDemandAmplitude = {mo_array(amps)}",
    ]
    dhrs = (uhx_case or {}).get("dhrs") if uhx_case is not None else None
    if dhrs and package in ("legacy", "segmented"):
        time_s = float(dhrs.get("time_s", times[-1] if times else 4000.0))
        bleed = float(dhrs.get("bleed_frac", 0.005))
        max_frac = float(dhrs.get("max_frac", 0.1))
        power = (
            "SegmentedMSR_PlantData.nominalPower"
            if package == "segmented"
            else "MSRR_PlantData.nominalPower"
        )
        lines[-1] += ","
        lines.append("    dhrs(")
        lines.append(
            f"      DHRS_P_Bleed = {mo_number(bleed)} * powerLevel * {power},"
        )
        lines.append(
            f"      DHRS_MaxP_Rm = {mo_number(max_frac)} * powerLevel * {power},"
        )
        lines.append(f"      DHRS_time = {mo_number(time_s)})")
    decls: list[str] = []
    initial_asserts: list[str] = []
    if package == "segmented" and scenario.get("kind") == "startup":
        forcing = scenario.get("forcing") or {}
        source = forcing.get("source") or {}
        rho = forcing.get("reactivity_pcm") or {}
        source_times = [float(item) for item in source.get("times_s") or []]
        source_amps = resolve_source_amplitudes(forcing) if source_times else []
        rho_times = [float(item) for item in rho.get("times_s") or []]
        rho_amps = [float(item) for item in rho.get("amplitudes") or []]
        # Review 2026-10-01 H2/H3: the four trim bases get the zero-power
        # startup semantics of the lumped startup models (isothermal start,
        # heat loss, flow-referenced staircase). The outer-annulus wrapper
        # bases keep their own initialization policies unchanged.
        startup_block = (
            None
            if outer_annulus
            else _segmented_startup_block(
                scenario, core_model, model_name, has_rho=bool(rho_times and rho_amps)
            )
        )
        if startup_block is not None:
            # The SegmentedMSR Stepper is active FROM its step time, so a
            # step at t = 0 already holds during initialization; the lumped
            # schedule's -1 s entry (needed by the former strict stepper)
            # becomes 0 s here instead of violating InitiationTime min = 0.
            rho_times = [max(0.0, item) for item in rho_times]
            decls = startup_block["decls"]
            initial_asserts = startup_block["initial_asserts"]
        extra: list[str] = []
        if "powerLevel" in forcing:
            extra.append(
                f"    powerLevel = {mo_number(quantity_value(forcing['powerLevel']))}"
            )
        if "sine_pcm" in forcing:
            extra.append(
                f"    perturbationAmplitudePcm = {mo_number(quantity_value(forcing['sine_pcm']))}"
            )
        if source_times and source_amps:
            extra.append(f"    numSourceSteps = {len(source_times)}")
            extra.append(f"    sourceStepTime = {mo_array(source_times)}")
            extra.append(f"    sourceAmplitude = {mo_array(source_amps)}")
        if rho_times and rho_amps:
            extra.append(f"    numExternalReactivitySteps = {len(rho_times)}")
            extra.append(f"    externalReactivityStepTime = {mo_array(rho_times)}")
            amplitude = mo_array(rho_amps)
            if startup_block is not None:
                amplitude += " + startupStaircaseOffsetPcm"
            extra.append(f"    externalReactivityAmplitude = {amplitude}")
        pump = forcing.get("pump") or {}
        ramp_to = pump.get("rampUpTo")
        ramp_time = pump.get("rampUpTime_s")
        ramp_k = pump.get("rampUpK_s")
        free_conv = pump.get("freeConvFF")
        if ramp_to is not None and ramp_time is not None:
            to_vals = [float(item) for item in quantity_value(ramp_to)]
            time_vals = [float(item) for item in ramp_time]
            k_vals = (
                [float(item) for item in quantity_value(ramp_k)]
                if ramp_k is not None
                else [50.0] * len(to_vals)
            )
            extra.append("    primaryPump(")
            extra.append(f"      numRampUp = {len(to_vals)},")
            extra.append(f"      rampUpK = {mo_array(k_vals)},")
            extra.append(f"      rampUpTo = {mo_array(to_vals)},")
            if startup_block is not None and free_conv is not None:
                # The free-convection flow is the first pump stage, so the
                # flow-referenced staircase reads it from the executed pump.
                extra.append(f"      rampUpTime = {mo_array(time_vals)},")
                extra.append(
                    f"      freeConvFF = {mo_number(float(quantity_value(free_conv)))})"
                )
            else:
                extra.append(f"      rampUpTime = {mo_array(time_vals)})")
        if startup_block is not None:
            extra.extend(startup_block["modifiers"])
        if extra:
            lines[-1] += ","
            for index, item in enumerate(extra):
                is_last = index == len(extra) - 1
                stripped = item.rstrip()
                if (
                    not is_last
                    and not stripped.endswith("(")
                    and not stripped.endswith(",")
                ):
                    item = item + ","
                lines.append(item)
    lines.append("  );")
    if decls:
        # Declarations precede the extends clause (the lumped
        # MSRRstartUpCriticality layout); the header lines stay first.
        lines[2:2] = decls
    if initial_asserts:
        lines.append("initial equation")
        lines.extend(initial_asserts)
    lines.append(f"end {model_name};")
    lines.append("")
    return model_name, "\n".join(lines)


def emit_scenario_wrapper(
    scenario: Mapping[str, Any],
    dest_dir: str | Path,
    *,
    core_model: str,
    package: str = "legacy",
    case: str | None = None,
    outer_annulus: bool = False,
    outer_annulus_init_policy: "str | None" = None,
) -> tuple[Path, str]:
    """Write the wrapper into ``dest_dir`` and return ``(path, model_name)``."""

    model_name, text = emit_wrapper_text(
        scenario,
        core_model=core_model,
        package=package,
        case=case,
        outer_annulus=outer_annulus,
        outer_annulus_init_policy=outer_annulus_init_policy,
    )
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{model_name}.mo"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path, model_name


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="startup_to_100kw", help="Scenario YAML id")
    parser.add_argument("--scenario_file", default=None, help="Overlay scenario YAML path")
    parser.add_argument("--kind", default=None, help="startup / transients / frequency")
    parser.add_argument(
        "--core_model",
        choices=CORE_CHOICES,
        default="1r",
        help=(
            "Core model key. 1r10seg (1-channel x 10-axial-segment 1R core, "
            "TASK-20260906-01) and r5x5_z10 (5x5-radial x 10-axial-segment "
            "1R core, TASK-20260906-02) are segmented-package only."
        ),
    )
    parser.add_argument("--package", choices=("legacy", "segmented"), default="legacy")
    parser.add_argument("--case", default=None, help="Transients case id (uhx_trip)")
    parser.add_argument(
        "--allow-unlisted-core",
        "--allow_unlisted_core",
        dest="allow_unlisted_core",
        action="store_true",
        help=(
            "Deliberately emit a wrapper for a (scenario, core_model) "
            "combination the scenario YAML does not list in applies_to."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory to write the wrapper into (default: 00runs/tmp/omc)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # TASK-20260906-01 P3: legacy-mode refusal for cores without a legacy
    # wrapper base (1r10seg: the legacy package ships no 10-segment
    # vehicle; TASK-20260906-02 P3 adds r5x5_z10: no 5x5 vehicle). Clean
    # named error instead of a raw KeyError from the base lookups below;
    # every existing (package, core) combination behaves exactly as before.
    # TASK-20260908-01 P6 item 4: the membership decision and the message
    # text are delegated to the shared helpers in helpers/scenario_config
    # (same strings; the clause table lives there once).
    if args.package == "legacy":
        refusal = legacy_core_refusal(
            args.core_model,
            _LUMPED_TRIP_BASE,
            flag="--core_model",
            vehicle_noun="wrapper base vehicle",
            legacy_group_noun="legacy bases",
        )
        if refusal is not None:
            print(f"ERROR: {refusal}")
            return 2
    kind = args.kind
    if kind is None:
        kind = "transients" if args.case else "startup"
    scenario = resolve_scenario(
        kind=kind,
        scenario_id=args.scenario,
        scenario_file=args.scenario_file,
    )
    # Scenario applies_to enforcement (TASK-20260908-01 P2 item 4): refuse
    # the (scenario, core) combination BEFORE any wrapper is generated.
    refusal = scenario_core_refusal(
        scenario,
        args.core_model,
        allow_unlisted=bool(getattr(args, "allow_unlisted_core", False)),
    )
    if refusal is not None:
        print(f"ERROR: {refusal}")
        return 2
    dest = Path(args.out) if args.out else repo_root() / "00runs" / "tmp" / "omc"
    path, model_name = emit_scenario_wrapper(
        scenario,
        dest,
        core_model=args.core_model,
        package=args.package,
        case=args.case,
    )
    print(path)
    print(model_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
