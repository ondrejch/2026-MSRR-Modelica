#!/usr/bin/env python3
"""Emit Modelica PlantData packages from ``data/plants/<id>/`` YAML.

The generated files are derived artifacts. Do not edit them by hand.
Regenerate::

    python3.12 -m helpers.emit_modelica_plant

``--check`` verifies both generated packages and ``data/CATALOG.md`` against the committed files without writing.

Lumped output is in degC (MSRR.mo convention). Segmented output is in
kelvin: every quantity annotated degC is converted with
T_K = T_C + 273.15 -- the trim setpoints, initial and ambient
temperatures, and every loop/pipe/HX temperature field via ``_q_k``;
the radial and outer-annulus sourced temperatures via ``kelvin_or_zero``.
Poison microscopic
cross-sections are authored in barns and reference flux in n/(cm2.s);
the segmented emitter converts those to m2 and 1/(m2.s) before writing
``SegmentedMSR_PlantData.Poisons``.

Each written PlantData package is stamped with the short git revision of
the checkout that ran the emitter (first line, ``// Emitter git
revision:``). The stamp is informational and checkout-dependent, so the
``--check`` comparison ignores that line: a stamp left by another commit
-- or ``unknown`` from a gitless archive extract or wheel install --
never fails the check.

Package members are emitted as ``final constant`` (not ``parameter``):
Modelica forbids non-constant package variables, and OpenModelica v1.27
rejects PlantData otherwise. Values are literals, so ``constant`` loses
nothing (``-override=`` never targets PlantData).

``fSalt`` contract (owner decision O5, TASK-20260908-01 P2): every
segmented record derives ``fSalt`` as the NORMALIZED ``q_fiss`` array
(9R normalizer precedent, matching the ``ChannelMap`` sum-to-one assert),
so the salt fraction can never silently diverge from the authored heat
split when profiles become nonuniform.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from helpers.git_checkout import is_real_git_checkout
from helpers.plant_config import (
    DEFAULT_PLANT_ID,
    ANNULAR_FLUID_MODE_CODES,
    FLOW_DIRECTION_CODES,
    OUTER_ANNULUS_CAVITY_MODE_CODES,
    OUTER_ANNULUS_CONTACT_MODE_CODES,
    OUTER_ANNULUS_FISSION_COUPLING_POLICY_CODES,
    OUTER_ANNULUS_FISSION_EXPOSURE_POLICY_CODES,
    OUTER_ANNULUS_FISSION_FEEDBACK_POLICY_CODES,
    OUTER_ANNULUS_GEOMETRY_POLICY_CODES,
    OUTER_ANNULUS_GRAPHITE_INTERFACE_CODES,
    POISON_MATURITY_APPROVED,
    RADIAL_CHANNEL_MEANING_CODES,
    RADIAL_GEOMETRY_POLICY_CODES,
    RADIAL_INTERFACE_MODE_CODES,
    annular_loop_fingerprint,
    data_root,
    is_quantity,
    load_plant,
    outer_annulus_config,
    plant_generated_subdir,
    outer_annulus_fingerprint,
    quantity_unit,
    quantity_value,
    r9_derived,
    radial_config,
    radial_fingerprint,
    radial_loop_channel_count,
    radial_stack_segment_count,
    repo_root,
    vol_loop_list,
    walk_quantities,
)
from helpers.poison_oracle import microscopic_xs_m2, neutron_flux_per_m2_s
from helpers.segmented_runs import CORE_MATURITY_LABELS

GENERATED_HEADER = (
    "GENERATED from data/plants/{plant_id} - do not edit. "
    "Regenerate: python3.12 -m helpers.emit_modelica_plant"
)
KELVIN_OFFSET = 273.15

#: Per-core PlantData record tag for the radial configuration packages,
#: mirroring the core-record names (Core1R, Core1R_10Seg, CoreR5x5_Z10,
#: Core9R). Emission order follows the core-record order.
RADIAL_CORE_TAGS = {
    "r1": "1R",
    "r1_10seg": "1R_10Seg",
    "r5x5_z10": "R5x5_Z10",
    "r9": "9R",
}


def mo_number(value: Any) -> str:
    """Format a Python number as a Modelica literal.

    ``format(x, '.15g')``: 15 significant digits, faithful for the
    authored deck values but NOT a guaranteed IEEE-double round-trip
    (``repr`` needs up to 17 digits; the emitted literal may differ from
    ``value`` beyond the 15th significant digit). Non-finite inputs
    raise: ``inf``/``nan`` would otherwise emit invalid Modelica
    literals silently.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(
            f"mo_number: non-finite value {value!r} cannot be a Modelica literal"
        )
    if number == 0.0:
        return "0"
    text = format(number, ".15g")
    if text.endswith(".0"):
        return text[:-2]
    return text


def mo_array(values: Sequence[Any]) -> str:
    return "{" + ", ".join(mo_number(v) for v in values) + "}"


def mo_matrix(rows: Sequence[Sequence[Any]]) -> str:
    """Modelica row-major matrix literal (``[a, b; c, d]``)."""

    return (
        "["
        + "; ".join(", ".join(mo_number(cell) for cell in row) for row in rows)
        + "]"
    )


def _q(node: Mapping[str, Any], *keys: str) -> Any:
    current: Any = node
    for key in keys:
        current = current[key]
    return quantity_value(current)


def _qty(node: Mapping[str, Any], *keys: str) -> Any:
    current: Any = node
    for key in keys:
        current = current[key]
    return current


def _as_kelvin(obj: Any) -> Any:
    """Return a quantity's numeric value, converting degC → K when annotated."""

    value = quantity_value(obj)
    if is_quantity(obj) and str(quantity_unit(obj)) == "degC":
        if isinstance(value, list):
            return [float(item) + KELVIN_OFFSET for item in value]
        return float(value) + KELVIN_OFFSET
    return value


def _q_k(node: Mapping[str, Any], *keys: str) -> Any:
    return _as_kelvin(_qty(node, *keys))


def _derived_f_salt(q_fiss: Any) -> Any:
    """Normalize the authored ``q_fiss`` shares into the ``fSalt`` array.

    Owner decision O5 (TASK-20260908-01 P2): ``fSalt[c][s] =
    q_fiss[c][s] / sum(q_fiss)``, element-wise with the authored shape
    preserved (1-D for the 10Seg-style records' per-segment view, 2-D for
    the 5x5 record). For the current uniform decks this is bit-identical
    to the historical ``1.0/n`` construction, so the committed generated
    packages are unchanged.
    """

    def total_of(value: Any) -> float:
        if isinstance(value, list):
            return sum(total_of(item) for item in value)
        return float(value)

    total = total_of(q_fiss)
    if total <= 0.0:
        raise ValueError("q_fiss must sum to a positive total to derive fSalt")

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        return float(value) / total

    return walk(q_fiss)


def _geom_lines(node: Mapping[str, Any], keys: Sequence[str], indent: str) -> list[str]:
    return [
        f"{indent}final constant Real {key} = {mo_number(_q(node, key))};"
        for key in keys
    ]


def _geom_lines_k(node: Mapping[str, Any], keys: Sequence[str], indent: str) -> list[str]:
    return [
        f"{indent}final constant Real {key} = {mo_number(_q_k(node, key))};"
        for key in keys
    ]


_PIPE_KEYS = ("volFracNode", "Ac", "L", "Ar", "e", "Tinf", "T_0")


def emit_lumped_package(plant: Mapping[str, Any]) -> str:
    plant_id = str(plant["id"])
    kinetics = plant["kinetics"]
    decay = plant["decay_heat"]
    materials = plant["materials"]
    primary = plant["primary_loop"]
    secondary = plant["secondary_loop"]
    pumps = plant["pumps"]
    r1 = plant["cores"]["r1"]
    r9 = plant["cores"]["r9"]
    loop_vols = vol_loop_list(plant)
    n_g = int(_q(kinetics, "num_groups"))
    n_dh = int(_q(decay, "num_groups"))
    n_reg = int(_q(r9, "n_regions"))
    n_z = int(_q(r9, "n_zones"))

    lines = [
        f'package MSRR_PlantData',
        f'  "{GENERATED_HEADER.format(plant_id=plant_id)}"',
        f'  final constant String plantId = "{plant_id}";',
        "  package Kinetics",
        f"    final constant Integer numGroups = {n_g};",
        f"    final constant Real lambda[numGroups] = {mo_array(_q(kinetics, 'lambda'))};",
        f"    final constant Real beta[numGroups] = {mo_array(_q(kinetics, 'beta'))};",
        f"    final constant Real LAMBDA = {mo_number(_q(kinetics, 'generation_time'))};",
        f"    final constant Real nFloor = {mo_number(_q(kinetics, 'n_floor'))};",
        f"    final constant Real sourceScale = {mo_number(_q(kinetics, 'source_scale'))};",
        f"    final constant Real nu = {mo_number(_q(kinetics, 'nu'))};",
        f"    final constant Real sourceEffectiveness = {mo_number(_q(kinetics, 'source_effectiveness'))};",
        f"    final constant Real energyPerFission = {mo_number(_q(plant['poisons'], 'energy_per_fission'))};",
        f"    final constant Real fullPowerPopulation = {mo_number(_full_power_population(plant))};",
        f"    final constant Real nomTauCore = {mo_number(_q(kinetics, 'lumped_tau_core'))};",
        f"    final constant Real nomTauLoop = {mo_number(_q(kinetics, 'lumped_tau_loop'))};",
        f"    final constant Real a_F = {mo_number(_q(kinetics, 'feedback', 'a_F'))};",
        f"    final constant Real a_G = {mo_number(_q(kinetics, 'feedback', 'a_G'))};",
        "  end Kinetics;",
        "  package DecayHeat",
        f"    final constant Integer numGroups = {n_dh};",
        f"    final constant Real DHYG[numGroups] = {mo_array(_q(decay, 'DHYG'))};",
        f"    final constant Real DHlamG[numGroups] = {mo_array(_q(decay, 'DHlamG'))};",
        f"    final constant Real nomFrac = {mo_number(_q(decay, 'nom_frac'))};",
        "  end DecayHeat;",
        "  package Materials",
        f"    final constant Real rhoFuel = {mo_number(_q(materials, 'fuel', 'rho'))};",
        f"    final constant Real cpFuel = {mo_number(_q(materials, 'fuel', 'cp'))};",
        f"    final constant Real kFuel = {mo_number(_q(materials, 'fuel', 'k'))};",
        f"    final constant Real rhoGrap = {mo_number(_q(materials, 'graphite', 'rho'))};",
        f"    final constant Real cpGrap = {mo_number(_q(materials, 'graphite', 'cp'))};",
        f"    final constant Real rhoCoolant = {mo_number(_q(materials, 'coolant', 'rho'))};",
        f"    final constant Real cpCoolant = {mo_number(_q(materials, 'coolant', 'cp'))};",
        f"    final constant Real kCoolant = {mo_number(_q(materials, 'coolant', 'k'))};",
        f"    final constant Real rhoHXtube = {mo_number(_q(materials, 'hx_tube', 'rho'))};",
        f"    final constant Real cpHXtube = {mo_number(_q(materials, 'hx_tube', 'cp'))};",
        "  end Materials;",
        "  package PrimaryLoop",
        f"    final constant Real vdotFuel = {mo_number(_q(primary, 'vdot_fuel'))};",
        f"    final constant Real volLoop[5] = {mo_array(loop_vols)};",
        f"    final constant Real dhrsMaxRemove = {mo_number(_q(primary, 'dhrs', 'max_remove'))};",
        # dhrsTK is SECONDS (YAML dhrs.tK, unit s): the DHRS actuation time
        # constant, bound as DHRS_tK by core/MSRR.mo and core/SegmentedMSR.mo.
        # The name predates the unit-aware records and every PlantData
        # consumer binds it; renaming would churn them all, so the unit
        # rides as the emitted inline comment instead.
        f"    final constant Real dhrsTK = {mo_number(_q(primary, 'dhrs', 'tK'))};"
        "  // s: DHRS actuation time constant (YAML dhrs.tK, unit s, not kelvin)",
        f"    final constant Real dhrsEngageTime = {mo_number(_q(primary, 'dhrs', 'engage_time'))};",
        f"    final constant Real dhrsBleed = {mo_number(_q(primary, 'dhrs', 'bleed'))};",
        *_geom_lines(primary["dhrs"], ("Ac", "L", "Ar", "e", "Tinf", "T_0"), "    "),
        "    package PipeCoreToDHRS",
        *_geom_lines(primary["pipes"]["pipeCoreToDHRS"], _PIPE_KEYS, "      "),
        "    end PipeCoreToDHRS;",
        "    package PipeDHRStoHX",
        *_geom_lines(primary["pipes"]["pipeDHRStoHX"], _PIPE_KEYS, "      "),
        "    end PipeDHRStoHX;",
        "    package PipeHXtoCore",
        *_geom_lines(primary["pipes"]["pipeHXtoCore"], _PIPE_KEYS, "      "),
        "    end PipeHXtoCore;",
        "  end PrimaryLoop;",
        "  package SecondaryLoop",
        f"    final constant Real vdotCoolant = {mo_number(_q(secondary, 'vdot_coolant'))};",
        f"    final constant Real hxVolP = {mo_number(_q(secondary, 'hx', 'vol_P'))};",
        f"    final constant Real hxVolT = {mo_number(_q(secondary, 'hx', 'vol_T'))};",
        f"    final constant Real hxVolS = {mo_number(_q(secondary, 'hx', 'vol_S'))};",
        f"    final constant Real hApNom = {mo_number(_q(secondary, 'hx', 'hApNom'))};",
        f"    final constant Real hAsNom = {mo_number(_q(secondary, 'hx', 'hAsNom'))};",
        f"    final constant Real hAExp = {mo_number(_q(secondary, 'hx', 'hAExp'))};",
        f"    final constant Real uhxVol = {mo_number(_q(secondary, 'uhx', 'vol'))};",
        f"    final constant Real pipeHXtoUHXvol = {mo_number(_q(secondary, 'pipes', 'HXtoUHX', 'vol'))};",
        f"    final constant Real pipeUHXtoHXvol = {mo_number(_q(secondary, 'pipes', 'UHXtoHX', 'vol'))};",
        "    package HX",
        *_geom_lines(
            secondary["hx"],
            (
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
            ),
            "      ",
        ),
        "    end HX;",
        "    package UHX",
        f"      final constant Real Tp_0 = {mo_number(_q(secondary, 'uhx', 'Tp_0'))};",
        "    end UHX;",
        "    package PipeHXtoUHX",
        *_geom_lines(secondary["pipes"]["HXtoUHX"], _PIPE_KEYS, "      "),
        "    end PipeHXtoUHX;",
        "    package PipeUHXtoHX",
        *_geom_lines(secondary["pipes"]["UHXtoHX"], _PIPE_KEYS, "      "),
        "    end PipeUHXtoHX;",
        "  end SecondaryLoop;",
        "  package Pumps",
        f"    final constant Real tripK = {mo_number(_q(pumps, 'tripK'))};",
        f"    final constant Real freeConvFF = {mo_number(_q(pumps, 'freeConvFF'))};",
        f"    final constant Real tripTime = {mo_number(_q(pumps, 'tripTime'))};",
        f"    final constant Integer primaryNumRampUp = {int(_q(pumps, 'primary', 'numRampUp'))};",
        f"    final constant Real primaryRampUpK[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpK'))};",
        f"    final constant Real primaryRampUpTo[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpTo'))};",
        f"    final constant Real primaryRampUpTime[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpTime'))};",
        f"    final constant Integer secondaryNumRampUp = {int(_q(pumps, 'secondary', 'numRampUp'))};",
        f"    final constant Real secondaryRampUpK[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpK'))};",
        f"    final constant Real secondaryRampUpTo[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpTo'))};",
        f"    final constant Real secondaryRampUpTime[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpTime'))};",
        "  end Pumps;",
        "  package Core1R",
        f"    final constant Integer nChan = {int(_q(r1, 'n_chan'))};",
        f"    final constant Integer nSeg = {int(_q(r1, 'n_seg'))};",
        f"    final constant Real cellVol[nSeg] = {mo_array(_q(r1, 'cell_vol'))};",
        f"    final constant Real volGN = {mo_number(_q(r1, 'vol_graphite'))};",
        f"    final constant Real hAnom = {mo_number(_q(r1, 'hAnom'))};",
        f"    final constant Real qFiss[nSeg] = {mo_array(_q(r1, 'q_fiss'))};",
        f"    final constant Real kG = {mo_number(_q(r1, 'kG'))};",
        f"    final constant Real kHT[nSeg] = {mo_array(_q(r1, 'kHT'))};",
        f"    final constant Real IF1 = {mo_number(_q(r1, 'IF')[0])};",
        f"    final constant Real IF2 = {mo_number(_q(r1, 'IF')[1])};",
        f"    final constant Real IG = {mo_number(_q(r1, 'IG'))};",
        f"    final constant Real hAExp = {mo_number(_q(r1, 'hAExp'))};",
        f"    final constant Real flowFrac = {mo_number(_q(r1, 'flow_frac')[0])};",
        f"    final constant Real Ac = {mo_number(_q(r1, 'channel_geom', 'Ac'))};",
        f"    final constant Real LF1 = {mo_number(_q(r1, 'channel_geom', 'LF')[0])};",
        f"    final constant Real LF2 = {mo_number(_q(r1, 'channel_geom', 'LF')[1])};",
        f"    final constant Real ArF1 = {mo_number(_q(r1, 'channel_geom', 'ArF')[0])};",
        f"    final constant Real ArF2 = {mo_number(_q(r1, 'channel_geom', 'ArF')[1])};",
        f"    final constant Real e = {mo_number(_q(r1, 'channel_geom', 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q(r1, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q(r1, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q(r1, 'trim', 'TG'))};",
        f"    final constant Real heatLossTinf = {mo_number(_q(r1, 'heat_loss_tinf'))};",
        "  end Core1R;",
        "  package Core9R",
        f"    final constant Integer nRegions = {n_reg};",
        f"    final constant Integer nZones = {n_z};",
        f"    final constant Integer zoneStart[nZones] = {mo_array(_q(r9, 'zone_start'))};",
        f"    final constant Integer zoneEnd[nZones] = {mo_array(_q(r9, 'zone_end'))};",
        f"    final constant Integer nSegZone[nZones] = {mo_array(_q(r9, 'n_seg_zone'))};",
        f"    final constant Real volF1[nRegions] = {mo_array(_q(r9, 'vol_F1'))};",
        f"    final constant Real volF2[nRegions] = {mo_array(_q(r9, 'vol_F2'))};",
        f"    final constant Real volG[nRegions] = {mo_array(_q(r9, 'vol_G'))};",
        f"    final constant Real volUpperPlenum = {mo_number(_q(r9, 'vol_upper_plenum'))};",
        f"    final constant Real hA[nRegions] = {mo_array(_q(r9, 'hA'))};",
        f"    final constant Real kFN1[nRegions] = {mo_array(_q(r9, 'kFN1'))};",
        f"    final constant Real kFN2[nRegions] = {mo_array(_q(r9, 'kFN2'))};",
        f"    final constant Real kHT1[nRegions] = {mo_array(_q(r9, 'kHT1'))};",
        f"    final constant Real kHT2[nRegions] = {mo_array(_q(r9, 'kHT2'))};",
        f"    final constant Real flowFracRegions[nZones] = {mo_array(_q(r9, 'flow_frac_zones'))};",
        f"    final constant Real IF1[nRegions] = {mo_array(_q(r9, 'IF1'))};",
        f"    final constant Real IF2[nRegions] = {mo_array(_q(r9, 'IF2'))};",
        f"    final constant Real IG[nRegions] = {mo_array(_q(r9, 'IG'))};",
        f"    final constant Real LF1[nRegions] = {mo_array(_q(r9, 'LF1'))};",
        f"    final constant Real LF2[nRegions] = {mo_array(_q(r9, 'LF2'))};",
        f"    final constant Real Ac[nZones] = {mo_array(_q(r9, 'Ac_zones'))};",
        f"    final constant Real ArF1[nRegions] = {mo_array(_q(r9, 'ArF1'))};",
        f"    final constant Real ArF2[nRegions] = {mo_array(_q(r9, 'ArF2'))};",
        f"    final constant Real e = {mo_number(_q(r9, 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q(r9, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q(r9, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q(r9, 'trim', 'TG'))};",
        f"    final constant Real TF1_0_regions[nRegions] = {mo_array(_q(r9, 'trim', 'TF1_regions'))};",
        f"    final constant Real TF2_0_regions[nRegions] = {mo_array(_q(r9, 'trim', 'TF2_regions'))};",
        f"    final constant Real TG_0_regions[nRegions] = {mo_array(_q(r9, 'trim', 'TG_regions'))};",
        f"    final constant Real Tmix_0 = {mo_number(_q(r9, 'mixing_pot', 'Tmix_0'))};",
        f"    final constant Real mixingPotAc = {mo_number(_q(r9, 'mixing_pot', 'Ac'))};",
        f"    final constant Real mixingPotL = {mo_number(_q(r9, 'mixing_pot', 'L'))};",
        f"    final constant Real mixingPotAr = {mo_number(_q(r9, 'mixing_pot', 'Ar'))};",
        f"    final constant Real regionTripTime[nZones] = {mo_array(_q(r9, 'region_trip_time'))};",
        f"    final constant Real regionCoastDownK = {mo_number(_q(r9, 'region_coast_down_K'))};",
        "  end Core9R;",
        f"  final constant Real nominalPower = {mo_number(_q(plant, 'nominal_power'))};",
        f"  final constant Real totalFuelVol = {mo_number(_q(plant, 'total_fuel_vol'))};",
        "end MSRR_PlantData;",
        "",
    ]
    return "\n".join(lines)


def _film_law_lines(core: Mapping[str, Any]) -> list[str]:
    """Two-branch core film-law constants (SegmentedMSR.Core.filmFlowFactor).

    hA/hAnom = max(FF^hAExp, hALamFrac*FF^hALamExp). A deck without the
    optional keys hA_lam_frac / hA_lam_exp (every msrr core) emits
    hALamFrac = 0, the single power law, and the unused hALamExp = 1/3.
    """

    lam_frac = quantity_value(core["hA_lam_frac"]) if "hA_lam_frac" in core else 0.0
    lam_exp = quantity_value(core["hA_lam_exp"]) if "hA_lam_exp" in core else 1.0 / 3.0
    return [
        f"    final constant Real hALamFrac = {mo_number(lam_frac)};",
        f"    final constant Real hALamExp = {mo_number(lam_exp)};",
    ]


def emit_segmented_package(plant: Mapping[str, Any]) -> str:
    """Kelvin twin of the lumped package, plus ChannelMap ranks for SegmentedMSR."""

    plant_id = str(plant["id"])
    kinetics = plant["kinetics"]
    decay = plant["decay_heat"]
    materials = plant["materials"]
    primary = plant["primary_loop"]
    secondary = plant["secondary_loop"]
    pumps = plant["pumps"]
    r1 = plant["cores"]["r1"]
    r9 = plant["cores"]["r9"]
    derived = r9_derived(plant)
    loop_vols = vol_loop_list(plant)
    n_g = int(_q(kinetics, "num_groups"))
    n_dh = int(_q(decay, "num_groups"))
    n_reg = int(_q(r9, "n_regions"))
    n_z = int(_q(r9, "n_zones"))
    n_chan = int(_q(r1, "n_chan"))
    n_seg = int(_q(r1, "n_seg"))
    # Owner decision O5: derived as the normalized q_fiss (bit-identical to
    # the historical 1.0/n_seg construction for the uniform r1 deck).
    f_salt = _derived_f_salt(_q(r1, "q_fiss"))
    xy = quantity_value(r1["channel_map"]["xy"])

    lines = [
        "package SegmentedMSR_PlantData",
        f'  "{GENERATED_HEADER.format(plant_id=plant_id)} Temperatures in kelvin."',
        f'  final constant String plantId = "{plant_id}";',
        "  package Kinetics",
        f"    final constant Integer numGroups = {n_g};",
        f"    final constant Real lambda[numGroups] = {mo_array(_q(kinetics, 'lambda'))};",
        f"    final constant Real beta[numGroups] = {mo_array(_q(kinetics, 'beta'))};",
        f"    final constant Real LAMBDA = {mo_number(_q(kinetics, 'generation_time'))};",
        f"    final constant Real genTime = LAMBDA;",
        f"    final constant Real nFloor = {mo_number(_q(kinetics, 'n_floor'))};",
        f"    final constant Real sourceScale = {mo_number(_q(kinetics, 'source_scale'))};",
        f"    final constant Real nu = {mo_number(_q(kinetics, 'nu'))};",
        f"    final constant Real sourceEffectiveness = {mo_number(_q(kinetics, 'source_effectiveness'))};",
        f"    final constant Real energyPerFission = {mo_number(_q(plant['poisons'], 'energy_per_fission'))};",
        f"    final constant Real fullPowerPopulation = {mo_number(_full_power_population(plant))};",
        f"    final constant Real nomTauCore = {mo_number(_q(kinetics, 'lumped_tau_core'))};",
        f"    final constant Real nomTauLoop = {mo_number(_q(kinetics, 'lumped_tau_loop'))};",
        f"    final constant Real a_F = {mo_number(_q(kinetics, 'feedback', 'a_F'))};",
        f"    final constant Real a_G = {mo_number(_q(kinetics, 'feedback', 'a_G'))};",
        "  end Kinetics;",
        "  package DecayHeat",
        f"    final constant Integer numGroups = {n_dh};",
        f"    final constant Real DHYG[numGroups] = {mo_array(_q(decay, 'DHYG'))};",
        f"    final constant Real DHlamG[numGroups] = {mo_array(_q(decay, 'DHlamG'))};",
        f"    final constant Real nomFrac = {mo_number(_q(decay, 'nom_frac'))};",
        "  end DecayHeat;",
        "  package Materials",
        f"    final constant Real rhoFuel = {mo_number(_q(materials, 'fuel', 'rho'))};",
        f"    final constant Real cpFuel = {mo_number(_q(materials, 'fuel', 'cp'))};",
        f"    final constant Real kFuel = {mo_number(_q(materials, 'fuel', 'k'))};",
        f"    final constant Real rhoGrap = {mo_number(_q(materials, 'graphite', 'rho'))};",
        f"    final constant Real cpGrap = {mo_number(_q(materials, 'graphite', 'cp'))};",
        f"    final constant Real rhoCoolant = {mo_number(_q(materials, 'coolant', 'rho'))};",
        f"    final constant Real cpCoolant = {mo_number(_q(materials, 'coolant', 'cp'))};",
        f"    final constant Real kCoolant = {mo_number(_q(materials, 'coolant', 'k'))};",
        f"    final constant Real rhoHXtube = {mo_number(_q(materials, 'hx_tube', 'rho'))};",
        f"    final constant Real cpHXtube = {mo_number(_q(materials, 'hx_tube', 'cp'))};",
        "  end Materials;",
        "  package PrimaryLoop",
        f"    final constant Real vdotFuel = {mo_number(_q(primary, 'vdot_fuel'))};",
        f"    final constant Real volLoop[5] = {mo_array(loop_vols)};",
        f"    final constant Real dhrsMaxRemove = {mo_number(_q(primary, 'dhrs', 'max_remove'))};",
        # dhrsTK is SECONDS (see emit_lumped_package): the name is bound as
        # DHRS_tK by core/SegmentedMSR.mo -- comment, not rename.
        f"    final constant Real dhrsTK = {mo_number(_q(primary, 'dhrs', 'tK'))};"
        "  // s: DHRS actuation time constant (YAML dhrs.tK, unit s, not kelvin)",
        f"    final constant Real dhrsEngageTime = {mo_number(_q(primary, 'dhrs', 'engage_time'))};",
        f"    final constant Real dhrsBleed = {mo_number(_q(primary, 'dhrs', 'bleed'))};",
        *_geom_lines_k(primary["dhrs"], ("Ac", "L", "Ar", "e", "Tinf", "T_0"), "    "),
        "    package PipeCoreToDHRS",
        *_geom_lines_k(primary["pipes"]["pipeCoreToDHRS"], _PIPE_KEYS, "      "),
        "    end PipeCoreToDHRS;",
        "    package PipeDHRStoHX",
        *_geom_lines_k(primary["pipes"]["pipeDHRStoHX"], _PIPE_KEYS, "      "),
        "    end PipeDHRStoHX;",
        "    package PipeHXtoCore",
        *_geom_lines_k(primary["pipes"]["pipeHXtoCore"], _PIPE_KEYS, "      "),
        "    end PipeHXtoCore;",
        "  end PrimaryLoop;",
        "  package SecondaryLoop",
        f"    final constant Real vdotCoolant = {mo_number(_q(secondary, 'vdot_coolant'))};",
        f"    final constant Real hxVolP = {mo_number(_q(secondary, 'hx', 'vol_P'))};",
        f"    final constant Real hxVolT = {mo_number(_q(secondary, 'hx', 'vol_T'))};",
        f"    final constant Real hxVolS = {mo_number(_q(secondary, 'hx', 'vol_S'))};",
        f"    final constant Real hApNom = {mo_number(_q(secondary, 'hx', 'hApNom'))};",
        f"    final constant Real hAsNom = {mo_number(_q(secondary, 'hx', 'hAsNom'))};",
        f"    final constant Real hAExp = {mo_number(_q(secondary, 'hx', 'hAExp'))};",
        f"    final constant Real uhxVol = {mo_number(_q(secondary, 'uhx', 'vol'))};",
        f"    final constant Real pipeHXtoUHXvol = {mo_number(_q(secondary, 'pipes', 'HXtoUHX', 'vol'))};",
        f"    final constant Real pipeUHXtoHXvol = {mo_number(_q(secondary, 'pipes', 'UHXtoHX', 'vol'))};",
        "    package HX",
        *_geom_lines_k(
            secondary["hx"],
            (
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
            ),
            "      ",
        ),
        "    end HX;",
        "    package UHX",
        f"      final constant Real Tp_0 = {mo_number(_q_k(secondary, 'uhx', 'Tp_0'))};",
        "    end UHX;",
        "    package PipeHXtoUHX",
        *_geom_lines_k(secondary["pipes"]["HXtoUHX"], _PIPE_KEYS, "      "),
        "    end PipeHXtoUHX;",
        "    package PipeUHXtoHX",
        *_geom_lines_k(secondary["pipes"]["UHXtoHX"], _PIPE_KEYS, "      "),
        "    end PipeUHXtoHX;",
        "  end SecondaryLoop;",
        "  package Pumps",
        f"    final constant Real tripK = {mo_number(_q(pumps, 'tripK'))};",
        f"    final constant Real freeConvFF = {mo_number(_q(pumps, 'freeConvFF'))};",
        f"    final constant Real tripTime = {mo_number(_q(pumps, 'tripTime'))};",
        f"    final constant Integer primaryNumRampUp = {int(_q(pumps, 'primary', 'numRampUp'))};",
        f"    final constant Real primaryRampUpK[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpK'))};",
        f"    final constant Real primaryRampUpTo[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpTo'))};",
        f"    final constant Real primaryRampUpTime[primaryNumRampUp] = {mo_array(_q(pumps, 'primary', 'rampUpTime'))};",
        f"    final constant Integer secondaryNumRampUp = {int(_q(pumps, 'secondary', 'numRampUp'))};",
        f"    final constant Real secondaryRampUpK[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpK'))};",
        f"    final constant Real secondaryRampUpTo[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpTo'))};",
        f"    final constant Real secondaryRampUpTime[secondaryNumRampUp] = {mo_array(_q(pumps, 'secondary', 'rampUpTime'))};",
        "  end Pumps;",
        "  package Core1R",
        f"    final constant Integer nChan = {n_chan};",
        f"    final constant Integer nSeg = {n_seg};",
        f"    final constant Real cellVol[nChan, nSeg] = {mo_matrix([_q(r1, 'cell_vol')])};",
        f"    final constant Real volGN = {mo_number(_q(r1, 'vol_graphite'))};",
        f"    final constant Real hAnom = {mo_number(_q(r1, 'hAnom'))};",
        f"    final constant Real qFiss[nChan, nSeg] = {mo_matrix([_q(r1, 'q_fiss')])};",
        f"    final constant Real qMod[nChan, nSeg] = {mo_matrix([_q(r1, 'q_mod')])};",
        f"    final constant Real fSalt[nChan, nSeg] = {mo_matrix([f_salt])};",
        f"    final constant Real kG = {mo_number(_q(r1, 'kG'))};",
        f"    final constant Real kHT[nSeg] = {mo_array(_q(r1, 'kHT'))};",
        f"    final constant Real IF1 = {mo_number(_q(r1, 'IF')[0])};",
        f"    final constant Real IF2 = {mo_number(_q(r1, 'IF')[1])};",
        f"    final constant Real IG = {mo_number(_q(r1, 'IG'))};",
        f"    final constant Real hAExp = {mo_number(_q(r1, 'hAExp'))};",
        *_film_law_lines(r1),
        f"    final constant Real flowFrac[nChan] = {mo_array(_q(r1, 'flow_frac'))};",
        f"    final constant Real Ac = {mo_number(_q(r1, 'channel_geom', 'Ac'))};",
        f"    final constant Real LF1 = {mo_number(_q(r1, 'channel_geom', 'LF')[0])};",
        f"    final constant Real LF2 = {mo_number(_q(r1, 'channel_geom', 'LF')[1])};",
        f"    final constant Real ArF1 = {mo_number(_q(r1, 'channel_geom', 'ArF')[0])};",
        f"    final constant Real ArF2 = {mo_number(_q(r1, 'channel_geom', 'ArF')[1])};",
        f"    final constant Real e = {mo_number(_q(r1, 'channel_geom', 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q_k(r1, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q_k(r1, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q_k(r1, 'trim', 'TG'))};",
        f"    final constant Real heatLossTinf = {mo_number(_q_k(r1, 'heat_loss_tinf'))};",
        f"    final constant Real pitch = {mo_number(_q(r1, 'channel_map', 'pitch'))};",
        f"    final constant Real dz = {mo_number(_q(r1, 'channel_map', 'dz'))};",
        f"    final constant Real chanR[nChan] = {mo_array(_q(r1, 'channel_map', 'chanR'))};",
        f"    final constant Real xy[nChan, 2] = {mo_matrix(xy)};",
        *_segmented_steady_state_lines(r1, "r1", n_chan, n_seg),
        "  end Core1R;",
        "  package Core9R",
        f"    final constant Integer nRegions = {n_reg};",
        f"    final constant Integer nZones = {n_z};",
        f"    final constant Integer zoneStart[nZones] = {mo_array(_q(r9, 'zone_start'))};",
        f"    final constant Integer zoneEnd[nZones] = {mo_array(_q(r9, 'zone_end'))};",
        f"    final constant Integer nSegZone[nZones] = {mo_array(_q(r9, 'n_seg_zone'))};",
        f"    final constant Real volF1[nRegions] = {mo_array(_q(r9, 'vol_F1'))};",
        f"    final constant Real volF2[nRegions] = {mo_array(_q(r9, 'vol_F2'))};",
        f"    final constant Real volG[nRegions] = {mo_array(_q(r9, 'vol_G'))};",
        f"    final constant Real volUpperPlenum = {mo_number(_q(r9, 'vol_upper_plenum'))};",
        f"    final constant Real hA[nRegions] = {mo_array(_q(r9, 'hA'))};",
        # Segmented-only (review 2026-10-01 M5): the 9R zone cores bind it as
        # their film exponent; the lumped Core9R package carries no exponent.
        f"    final constant Real hAExp = {mo_number(_q(r9, 'hAExp'))};",
        *_film_law_lines(r9),
        f"    final constant Real kFN1[nRegions] = {mo_array(_q(r9, 'kFN1'))};",
        f"    final constant Real kFN2[nRegions] = {mo_array(_q(r9, 'kFN2'))};",
        f"    final constant Real kHT1[nRegions] = {mo_array(_q(r9, 'kHT1'))};",
        f"    final constant Real kHT2[nRegions] = {mo_array(_q(r9, 'kHT2'))};",
        f"    final constant Real flowFracRegions[nZones] = {mo_array(_q(r9, 'flow_frac_zones'))};",
        f"    final constant Real IF1[nRegions] = {mo_array(_q(r9, 'IF1'))};",
        f"    final constant Real IF2[nRegions] = {mo_array(_q(r9, 'IF2'))};",
        f"    final constant Real IG[nRegions] = {mo_array(_q(r9, 'IG'))};",
        f"    final constant Real LF1[nRegions] = {mo_array(_q(r9, 'LF1'))};",
        f"    final constant Real LF2[nRegions] = {mo_array(_q(r9, 'LF2'))};",
        f"    final constant Real Ac[nZones] = {mo_array(_q(r9, 'Ac_zones'))};",
        f"    final constant Real ArF1[nRegions] = {mo_array(_q(r9, 'ArF1'))};",
        f"    final constant Real ArF2[nRegions] = {mo_array(_q(r9, 'ArF2'))};",
        f"    final constant Real e = {mo_number(_q(r9, 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q_k(r9, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q_k(r9, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q_k(r9, 'trim', 'TG'))};",
        f"    final constant Real TF1Regions[nRegions] = {mo_array(_q_k(r9, 'trim', 'TF1_regions'))};",
        f"    final constant Real TF2Regions[nRegions] = {mo_array(_q_k(r9, 'trim', 'TF2_regions'))};",
        f"    final constant Real TGRegions[nRegions] = {mo_array(_q_k(r9, 'trim', 'TG_regions'))};",
        f"    final constant Real Tmix_0 = {mo_number(_q_k(r9, 'mixing_pot', 'Tmix_0'))};",
        f"    final constant Real mixingPotAc = {mo_number(_q(r9, 'mixing_pot', 'Ac'))};",
        f"    final constant Real mixingPotL = {mo_number(_q(r9, 'mixing_pot', 'L'))};",
        f"    final constant Real mixingPotAr = {mo_number(_q(r9, 'mixing_pot', 'Ar'))};",
        f"    final constant Real regionTripTime[nZones] = {mo_array(_q(r9, 'region_trip_time'))};",
        f"    final constant Real regionCoastDownK = {mo_number(_q(r9, 'region_coast_down_K'))};",
        f"    final constant Real predOffsetTotal = {mo_number(derived['pred_offset_total_W'])};",
        *_segmented_steady_state_lines(r9, "r9", 1, 0),
        "  end Core9R;",
        f"  final constant Real nominalPower = {mo_number(_q(plant, 'nominal_power'))};",
        f"  final constant Real totalFuelVol = {mo_number(_q(plant, 'total_fuel_vol'))};",
        *_poison_package_lines(plant),
        *_radial_package_lines(plant),
        *_outer_annulus_package_lines(plant),
        "end SegmentedMSR_PlantData;",
        "",
    ]
    if "r1_10seg" in plant["cores"]:
        index = lines.index("  end Core1R;")
        lines[index + 1 : index + 1] = _core1r_10seg_lines(plant["cores"]["r1_10seg"])
    if "r5x5_z10" in plant["cores"]:
        anchor = "  end Core1R_10Seg;" if "r1_10seg" in plant["cores"] else "  end Core1R;"
        index = lines.index(anchor)
        lines[index + 1 : index + 1] = _core_r5x5_z10_lines(plant["cores"]["r5x5_z10"])
    return "\n".join(lines)


def _segmented_steady_state_lines(core: Mapping[str, Any], kind: str, n_chan: int, n_seg: int) -> list[str]:
    """Segmented-only qualified steady state (physics review 2026-09-27).

    Emits, in kelvin, the per-cell FixedStart temperatures the segmented trim
    rigs start from and the feedback-tap setpoints derived from them, so a rig
    starts at its steady state with zero initial temperature feedback. Tap
    rules mirror the rigs' feedback wiring: 1R - the two cells and the mean
    graphite; 10-seg/5x5 - the means of segments 1..nSeg/2 and nSeg/2+1..nSeg
    and the mean graphite (the 25 5x5 channels are identical replicas, so the
    per-segment arrays are broadcast to every channel row); 9R - per region the
    two owning cells and the mass-weighted mean of its two graphite nodes,
    weights ``kHT_node/(kHT1+kHT2)`` (review 2026-10-01 M4: each graphite
    half-node carries that share of the region's graphite mass, matching
    ``SegmentedMSR.Reactors.htShareChain9R`` and the rigs' feedback taps).
    """

    block = core["segmented_steady_state"]
    t_fuel = [float(v) for v in _as_kelvin(block["T_fuel"])]
    t_mod = [float(v) for v in _as_kelvin(block["T_mod"])]
    t_ref = float(_as_kelvin(block["reference_temperature"]))
    lines = [f"    final constant Real ssReferenceTemperature = {mo_number(t_ref)};"]
    if kind == "r9":
        n_reg = len(t_fuel) // 2
        tf1 = [t_fuel[2 * r] for r in range(n_reg)]
        tf2 = [t_fuel[2 * r + 1] for r in range(n_reg)]
        k_ht1 = [float(v) for v in _q(core, "kHT1")]
        k_ht2 = [float(v) for v in _q(core, "kHT2")]
        share1 = [k_ht1[r] / (k_ht1[r] + k_ht2[r]) for r in range(n_reg)]
        share2 = [k_ht2[r] / (k_ht1[r] + k_ht2[r]) for r in range(n_reg)]
        tg = [share1[r] * t_mod[2 * r] + share2[r] * t_mod[2 * r + 1] for r in range(n_reg)]
        lines += [
            f"    final constant Real ssTFuelChain[2*nRegions] = {mo_array(t_fuel)};",
            f"    final constant Real ssTModChain[2*nRegions] = {mo_array(t_mod)};",
            f"    final constant Real ssTF1Regions[nRegions] = {mo_array(tf1)};",
            f"    final constant Real ssTF2Regions[nRegions] = {mo_array(tf2)};",
            f"    final constant Real ssTGRegions[nRegions] = {mo_array(tg)};",
            f"    final constant Real ssTPlenum = {mo_number(float(_as_kelvin(block['T_plenum'])))};",
        ]
        return lines
    if kind == "r1":
        tf1, tf2 = t_fuel[0], t_fuel[1]
    else:
        half = n_seg // 2
        tf1 = sum(t_fuel[:half]) / half
        tf2 = sum(t_fuel[half:]) / (n_seg - half)
    tg = sum(t_mod) / len(t_mod)
    lines += [
        f"    final constant Real ssTFuel[nChan, nSeg] = {mo_matrix([t_fuel] * n_chan)};",
        f"    final constant Real ssTMod[nChan, nSeg] = {mo_matrix([t_mod] * n_chan)};",
        f"    final constant Real ssTF1 = {mo_number(tf1)};",
        f"    final constant Real ssTF2 = {mo_number(tf2)};",
        f"    final constant Real ssTG = {mo_number(tg)};",
    ]
    return lines


def _full_power_population(plant: Mapping[str, Any]) -> float:
    """Full-power neutron population N0 = LAMBDA*nu*P/E_f [neutrons].

    Physics review 2026-09-27: SegmentedMSR PKE_T and the legacy
    SMD_MSR_Modelica PKE/mPKE normalize an absolute external source S [n/s]
    as S/N0 (the former 1.58e20 divisor, still emitted as the LEGACY
    ``sourceScale``, is ~8.7e6x too large). Emitted for documentation and
    cross-checks in both PlantData packages; the kinetics blocks recompute the
    same product from their own LAMBDA so a LAMBDA override stays consistent.
    """

    kinetics = plant["kinetics"]
    lam = float(_q(kinetics, "generation_time"))
    nu = float(_q(kinetics, "nu"))
    power = float(_q(plant, "nominal_power"))
    energy = float(_q(plant["poisons"], "energy_per_fission"))
    return lam * nu * power / energy


def _poison_package_lines(plant: Mapping[str, Any]) -> list[str]:
    """SegmentedMSR_PlantData.Poisons: homogeneous five-isotope bindings."""

    poisons = plant["poisons"]
    energy = float(_q(poisons, "energy_per_fission"))
    power = float(_q(plant, "nominal_power"))
    fission_rate_0 = power / energy
    averaging = str(poisons["flux_averaging"])
    averaging_code = 1 if averaging == "core_volume" else 2
    # TASK-20260912-01 P5: the governance label is a required plant enum
    # (helpers.plant_config.POISON_MATURITY_VALUES). Expose it beside the
    # dataset identity -- as a comment and a constant -- so the compiled
    # PlantData names the maturity of the data it binds.
    maturity = str(poisons["maturity"])
    maturity_note = (
        ""
        if maturity in POISON_MATURITY_APPROVED
        else (
            " Not approved for production or publication use without the"
            " explicit --allow-unreviewed-poison-data development override."
        )
    )
    return [
        f"  // Poison-data maturity (TASK-20260912-01 P5): {maturity}."
        f"{maturity_note}",
        "  package Poisons",
        f'    final constant String datasetId = "{poisons["dataset_id"]}";',
        f'    final constant String maturity = "{maturity}";',
        f'    final constant String yieldConvention = "{poisons["yield_convention"]}";',
        f'    final constant String fluxAveraging = "{averaging}";',
        f"    final constant Integer fluxAveragingCode = {averaging_code};",
        f'    final constant String inventoryUnit = "{poisons["inventory_unit"]}";',
        f"    final constant Real Te135_lambda = {mo_number(_q(poisons, 'Te135_lambda'))};",
        f"    final constant Real I135_lambda = {mo_number(_q(poisons, 'I135_lambda'))};",
        f"    final constant Real Xe135_lambda = {mo_number(_q(poisons, 'Xe135_lambda'))};",
        f"    final constant Real Pm149_lambda = {mo_number(_q(poisons, 'Pm149_lambda'))};",
        f"    final constant Real Te135_yield = {mo_number(_q(poisons, 'Te135_yield'))};",
        f"    final constant Real I135_yield = {mo_number(_q(poisons, 'I135_yield'))};",
        f"    final constant Real Xe135_yield = {mo_number(_q(poisons, 'Xe135_yield'))};",
        f"    final constant Real Pm149_yield = {mo_number(_q(poisons, 'Pm149_yield'))};",
        f"    final constant Real Sm149_yield = {mo_number(_q(poisons, 'Sm149_yield'))};",
        f"    final constant Real sigma_a_Xe = {mo_number(microscopic_xs_m2(poisons['sigma_a_Xe']))};",
        f"    final constant Real sigma_a_Sm = {mo_number(microscopic_xs_m2(poisons['sigma_a_Sm']))};",
        "    // LEGACY sigma_a_fuel: not read by the current library (the worth",
        "    // denominator is derived, Sigma_a = nu*F0/(phi0*V_avg); physics review",
        "    // 2026-09-27); emitted for pre-2026-09-27 library copies only.",
        f"    final constant Real sigma_a_fuel = {mo_number(_q(poisons, 'sigma_a_fuel'))};",
        f"    final constant Real phi0 = {mo_number(neutron_flux_per_m2_s(poisons['phi0']))};",
        f"    final constant Real energyPerFission = {mo_number(_q(poisons, 'energy_per_fission'))};",
        f"    final constant Real fissionRate0 = {mo_number(fission_rate_0)};",
        f"    final constant Real k_rem_Xe = {mo_number(_q(poisons, 'k_rem_Xe'))};",
        f"    final constant Real k_rem_Sm = {mo_number(_q(poisons, 'k_rem_Sm'))};",
        "  end Poisons;",
    ]


def _radial_core_package_lines(
    plant_id: str,
    core_key: str,
    cfg: Mapping[str, Any],
    n_chan: int,
    n_seg: int,
) -> list[str]:
    """Lines for one additive ``IntraChannelRadial<CoreTag>`` PlantData package.

    TASK-20260914-01 P1 (plan §6.8): the per-core radial configuration as
    generated constants. The package carries the three structural
    decisions of plan §2.2, the physical-channel mapping of review rev019
    Phase 2 (TASK-20260915-01 P2: ``channelMeaning``/``channelMeaningCode``
    -- what one modeled coarse thermal channel represents, the multiplicity
    ``channelMultiplicity`` = N_c, and the explicit per-segment physical
    radial length ``physicalLength`` -- one entry per core axial segment,
    never map.dz unless proven equal for the core), the
    geometry/material/interface/flow/plenum/HX data, deposition shares,
    dataset identity/maturity, and the two deterministic fingerprints.
    Temperatures are emitted in kelvin (authored degC converted here; only
    meaningful on enabled decks). Disabled decks emit the canonical
    disabled defaults (zeros/neutral strings), so the existing core-record
    literals are untouched and the new fields carry their defaults.
    """

    tag = RADIAL_CORE_TAGS[core_key]
    pkg = f"IntraChannelRadial{tag}"
    loop = cfg["annular_loop"]
    hx = loop["heat_exchanger"]
    enabled = bool(cfg["enabled"])
    mode = str(cfg["annular_fluid_mode"])
    geom = cfg["geometry"]
    interfaces = cfg["interfaces"]
    deposition = cfg["heat_deposition"]
    physical_lengths = [float(item) for item in geom["physical_length"]]

    def kelvin_or_zero(deg_c: float) -> float:
        # Authored plant temperatures are degC; the segmented file is kelvin.
        # Disabled records carry 0 exactly (never a converted default).
        return float(deg_c) + KELVIN_OFFSET if enabled else 0.0

    return [
        f"  package {pkg}",
        f'    "{GENERATED_HEADER.format(plant_id=plant_id)}'
        f" Intra-channel radial stack configuration for Core{tag} "
        f'(data/plants/{plant_id}/cores/{core_key}, TASK-20260914-01 P1). '
        'Temperatures in kelvin."',
        f"    final constant Boolean enableIntraChannelRadial = "
        f"{'true' if enabled else 'false'};",
        f'    final constant String annularFluidMode = "{mode}";',
        f"    final constant Integer annularFluidModeCode = "
        f"{ANNULAR_FLUID_MODE_CODES[mode]};",
        f"    final constant Boolean annularHeatExchangerEnabled = "
        f"{'true' if cfg['annular_heat_exchanger_enabled'] else 'false'};",
        f'    final constant String annularHeatExchangerModel = '
        f'"{cfg["annular_heat_exchanger_model"]}";',
        f'    final constant String geometryPolicy = "{cfg["geometry_policy"]}";',
        f"    final constant Integer geometryPolicyCode = "
        f"{RADIAL_GEOMETRY_POLICY_CODES[cfg['geometry_policy']]};",
        f'    final constant String channelMeaning = "{cfg["channel_meaning"]}";',
        f"    final constant Integer channelMeaningCode = "
        f"{RADIAL_CHANNEL_MEANING_CODES[cfg['channel_meaning']]};",
        f"    final constant Integer channelMultiplicity = "
        f"{int(cfg['channel_multiplicity'])};",
        f"    final constant Real physicalLength[{n_seg}] = "
        f"{mo_array(physical_lengths)};",
        f'    final constant String radialMaturity = "{cfg["maturity"]}";',
        f'    final constant String radialDatasetId = "{cfg["dataset_id"]}";',
        f"    final constant String radialFingerprint = "
        f"\"{radial_fingerprint(cfg)}\";",
        f"    final constant String annularLoopFingerprint = "
        f"\"{annular_loop_fingerprint(cfg)}\";",
        f"    final constant Real fuelRadius = {mo_number(geom['fuel_radius'])};",
        f"    final constant Real pipeOuterRadius = {mo_number(geom['pipe_outer_radius'])};",
        f"    final constant Real annulusOuterRadius = {mo_number(geom['annulus_outer_radius'])};",
        f"    final constant Real volumeTolerance = {mo_number(cfg['volume_tolerance'])};",
        f"    final constant Real rhoPipe = {mo_number(cfg['materials']['channel_pipe']['rho'])};",
        f"    final constant Real cpPipe = {mo_number(cfg['materials']['channel_pipe']['cp'])};",
        f"    final constant Real kPipe = {mo_number(cfg['materials']['channel_pipe']['k'])};",
        f"    final constant Real rhoAnnularFluid = "
        f"{mo_number(cfg['materials']['annular_fluid']['rho'])};",
        f"    final constant Real cpAnnularFluid = "
        f"{mo_number(cfg['materials']['annular_fluid']['cp'])};",
        f"    final constant Real kAnnularFluid = "
        f"{mo_number(cfg['materials']['annular_fluid']['k'])};",
        f"    final constant Integer fuelPipeInterfaceCode = "
        f"{RADIAL_INTERFACE_MODE_CODES[interfaces['fuel_pipe']['mode']]};",
        f"    final constant Real hFuelPipe = {mo_number(interfaces['fuel_pipe']['h'])};",
        f"    final constant Integer pipeFluidInterfaceCode = "
        f"{RADIAL_INTERFACE_MODE_CODES[interfaces['pipe_fluid']['mode']]};",
        f"    final constant Real hPipeFluid = {mo_number(interfaces['pipe_fluid']['h'])};",
        f"    final constant Integer fluidModeratorInterfaceCode = "
        f"{RADIAL_INTERFACE_MODE_CODES[interfaces['fluid_moderator']['mode']]};",
        f"    final constant Real hFluidModerator = "
        f"{mo_number(interfaces['fluid_moderator']['h'])};",
        f"    final constant Real nominalMassFlow = {mo_number(loop['nominal_mass_flow'])};",
        f"    final constant Real maxFlowCommand = {mo_number(loop['max_flow_command'])};",
        f'    final constant String flowDirection = "{loop["flow_direction"]}";',
        f"    final constant Integer flowDirectionCode = "
        f"{FLOW_DIRECTION_CODES[loop['flow_direction']]};",
        f"    final constant Real channelFlowFractions[{n_chan}] = "
        f"{mo_array(loop['channel_flow_fractions'])};",
        f"    final constant Real supplyPlenumVolume = "
        f"{mo_number(loop['supply_plenum_volume'])};",
        f"    final constant Real returnPlenumVolume = "
        f"{mo_number(loop['return_plenum_volume'])};",
        f"    final constant Real connectingPipeVolume = "
        f"{mo_number(loop['connecting_pipe_volume'])};",
        f"    final constant Real hxLoopSideVolume = {mo_number(hx['loop_side_volume'])};",
        f"    final constant Real hxUA = {mo_number(hx['ua'])};",
        f"    final constant Real hxSinkTemperature = {mo_number(kelvin_or_zero(hx['sink_temperature']))};",
        f"    final constant Real hxInitialTemperature = "
        f"{mo_number(kelvin_or_zero(hx['initial_temperature']))};",
        f"    final constant Real depFracPipe = {mo_number(deposition['pipe_fraction'])};",
        f"    final constant Real depFracAnnularFluid = "
        f"{mo_number(deposition['annular_fluid_fraction'])};",
        f"  end {pkg};",
    ]


def _radial_package_lines(plant: Mapping[str, Any]) -> list[str]:
    """Per-core radial configuration packages, appended additively.

    Emitted for every core section in :data:`RADIAL_CORE_TAGS` order so the
    committed disabled decks still bind a complete, byte-stable
    configuration record (the vehicles select per core in Phase F).
    """

    lines: list[str] = []
    for core_key in RADIAL_CORE_TAGS:
        cores = plant["cores"]
        if core_key not in cores:
            continue
        n_chan = int(radial_loop_channel_count(plant, core_key))
        n_seg = int(radial_stack_segment_count(plant, core_key))
        lines.extend(_radial_core_package_lines(
            str(plant["id"]), core_key, radial_config(plant, core_key), n_chan, n_seg
        ))
    return lines


def _outer_annulus_package_lines(plant: Mapping[str, Any]) -> list[str]:
    """The single ``OuterFuelAnnulus<CoreTag>`` PlantData package (P7).

    TASK-20260917-01 P7 (plan §7.3): the outer-core fuel annulus / reactor
    vessel / fixed-cavity dataset of the authored ``outer_fuel_annulus``
    block as generated constants - a dedicated ADDITIVE sibling of the
    per-core radial packages, keyed by the block's first production target
    (the radial tag map: ``OuterFuelAnnulus1R_10Seg`` for the shipped
    ``r1_10seg`` target). The package binds every field of
    ``SegmentedMSR.Core.OuterFuelAnnulusConfig`` (the P4 record) plus the
    manifest-facing identity strings, so the P8 vehicle can bind every
    generated field and the run manifests can name the dataset identity.
    Temperatures are emitted in kelvin (authored degC converted here; zeros
    on the disabled record - never a converted default). A disabled block
    emits the canonical disabled record (zeros/neutral structural switches)
    that still names its dataset identity, inventory policy, fission
    policies, and fingerprints; a deck without the block emits nothing (the
    emitter's conditional-core pattern).

    TASK-20260918-01 P1 (plan §5.1): the record also carries the annulus-
    fission dataset - fissionEnabled, the fissionCouplingPolicyCode
    (local_same_fraction = 1), BOTH fraction vectors (sourceFraction =
    fission events, heatDepositionFraction = prompt fission power;
    elementwise equal under local_same_fraction), and the two policy codes
    (annulusTemperatureFeedbackPolicyCode / poisonFluxExposurePolicyCode,
    zero_credit = 1). The zero-value disabled fields are canonical; the
    power splitters that consume them are P2.
    """

    cfg = outer_annulus_config(plant)
    if cfg is None:
        return []
    plant_id = str(plant["id"])
    enabled = bool(cfg["enabled"])
    target = str(cfg["first_production_target"])
    tag = RADIAL_CORE_TAGS[target]
    pkg = f"OuterFuelAnnulus{tag}"
    n_chan = int(cfg["n_chan"])
    n_seg = int(cfg["n_seg"])
    geom = cfg["geometry"]
    cavity = cfg["cavity"]
    interfaces = cfg["interfaces"]
    fission = cfg["fission"]
    loop_surfaces = [str(name) for name in cavity["loop_surfaces"]]
    surface_codes = [int(code) for code in cavity["loop_surface_codes"]]

    def kelvin_or_zero(deg_c: float) -> float:
        # Authored plant temperatures are degC; the segmented file is kelvin.
        # Disabled records carry 0 exactly (never a converted default).
        return float(deg_c) + KELVIN_OFFSET if enabled else 0.0

    if surface_codes:
        surface_code_literal = mo_array(surface_codes)
        surface_name_literal = (
            "{" + ", ".join(f'"{name}"' for name in loop_surfaces) + "}"
        )
    else:
        surface_code_literal = "fill(0, 0)"
        surface_name_literal = 'fill("", 0)'

    return [
        f"  package {pkg}",
        f'    "{GENERATED_HEADER.format(plant_id=plant_id)} '
        f"Outer-core fuel annulus / reactor-vessel / fixed-cavity "
        f"configuration for Core{tag} "
        f"(data/plants/{plant_id}/shared/core_vessel.yaml; first production "
        f"target {target}; TASK-20260917-01 P7). Temperatures in kelvin.\"",
        f"    final constant Boolean enabled = {'true' if enabled else 'false'};",
        f"    final constant Integer nChan = {n_chan};",
        f"    final constant Integer nSeg = {n_seg};",
        f'    final constant String firstProductionTarget = "{target}";',
        f"    final constant Integer seriesLocationCode = "
        f"{int(cfg['series_location_code'])};",
        f"    final constant Integer flowDirectionCode = "
        f"{int(cfg['flow_direction_code'])};",
        f'    final constant String geometryPolicy = "{cfg["geometry_policy"]}";',
        f"    final constant Integer geometryPolicyCode = "
        f"{OUTER_ANNULUS_GEOMETRY_POLICY_CODES[cfg['geometry_policy']]};",
        f'    final constant String outerAnnulusMaturity = "{cfg["maturity"]}";',
        f'    final constant String outerAnnulusDatasetId = "{cfg["dataset_id"]}";',
        f"    final constant String outerAnnulusFingerprint = "
        f"\"{outer_annulus_fingerprint(cfg)}\";",
        f"    final constant Real physicalLength[{n_seg}] = "
        f"{mo_array(geom['physical_length'])};",
        f"    final constant Real graphiteOuterRadius = "
        f"{mo_number(geom['graphite_outer_radius'])};",
        f"    final constant Real vesselInnerRadius = "
        f"{mo_number(geom['vessel_inner_radius'])};",
        f"    final constant Real vesselOuterRadius = "
        f"{mo_number(geom['vessel_outer_radius'])};",
        f"    final constant Real annulusVolume[{n_seg}] = "
        f"{mo_array(geom['annulus_volume'])};",
        f"    final constant Real activeCoreCellVolume[{n_chan}, {n_seg}] = "
        f"{mo_matrix(geom['active_core_cell_volume'])};",
        f"    final constant Real G_modAnn[{n_chan}, {n_seg}] = "
        f"{mo_matrix(geom['conductance'])};",
        f"    final constant Integer graphiteAnnulusInterfaceCode = "
        f"{OUTER_ANNULUS_GRAPHITE_INTERFACE_CODES[cfg['moderator_coupling']['interface_mode']]};",
        f"    final constant Real hGraphiteAnnulus = "
        f"{mo_number(cfg['moderator_coupling']['h'])};",
        f"    final constant Integer annulusVesselInterfaceCode = "
        f"{OUTER_ANNULUS_CONTACT_MODE_CODES[interfaces['fuel_to_vessel']['mode']]};",
        f"    final constant Real hAnnulusVessel = "
        f"{mo_number(interfaces['fuel_to_vessel']['h'])};",
        f"    final constant Real rhoVessel = "
        f"{mo_number(cfg['vessel_material']['rho'])};",
        f"    final constant Real cpVessel = "
        f"{mo_number(cfg['vessel_material']['cp'])};",
        f"    final constant Real kVessel = "
        f"{mo_number(cfg['vessel_material']['k'])};",
        # REV-21c79f7-01 (TASK-20260918-01): the annulus radial conductivity
        # of the primary fuel - a separate datum from Materials.kFuel (the
        # axial-conduction-off placeholder). 0 on the disabled record (never
        # consumed); the validator requires a positive sourced value on any
        # enabled deck and the assembly asserts it before the cells evaluate.
        f"    final constant Real kFuel = "
        f"{mo_number(cfg['fuel_conductivity'])};",
        f"    final constant Boolean cavityEnabled = "
        f"{'true' if cavity['enabled'] else 'false'};",
        f"    final constant Real cavityTemperature = "
        f"{mo_number(kelvin_or_zero(cavity['temperature']))};",
        f"    final constant Integer cavityModeCode = {int(cavity['mode_code'])};",
        f"    final constant Real vesselCavityUA[{n_seg}] = "
        f"{mo_array(cavity['vessel_ua'])};",
        f"    final constant Real vesselArea[{n_seg}] = "
        f"{mo_array(cavity['vessel_area'])};",
        f"    final constant Real vesselEmissivity[{n_seg}] = "
        f"{mo_array(cavity['vessel_emissivity'])};",
        f"    final constant Integer nCavityLoopSurfaces = {len(loop_surfaces)};",
        f"    final constant Integer cavityLoopSurfaceCode[nCavityLoopSurfaces] = "
        f"{surface_code_literal};",
        f"    final constant String cavityLoopSurfaceName[nCavityLoopSurfaces] = "
        f"{surface_name_literal};",
        f"    final constant Real precursorImportance[{n_seg}] = "
        f"{mo_array(cfg['precursor_importance']['weights'])};",
        f"    final constant Real T_0_annulus = "
        f"{mo_number(kelvin_or_zero(cfg['initialization']['annulus_temperature']))};",
        f"    final constant Real T_0_vessel = "
        f"{mo_number(kelvin_or_zero(cfg['initialization']['vessel_temperature']))};",
        f"    final constant Real volumeTolerance = "
        f"{mo_number(cfg['volume_tolerance'])};",
        # Annulus fission (plan §5.1; TASK-20260918-01 P1): the conservative
        # SPLIT of the total fission source - data + identity only (the
        # splitters are P2, the precursor split P3). The canonical disabled
        # record carries fissionEnabled = false and zero fraction vectors;
        # under local_same_fraction the two vectors agree elementwise (a
        # validation rule, not a schema collapse). Fraction sums satisfy the STRICT
        # retained-share rule 1-fsum(f) >= 1e-9; nonzero vectors on any disabled
        # state refuse in the loader.
        f"    final constant Boolean fissionEnabled = "
        f"{'true' if fission['enabled'] else 'false'};",
        f"    final constant Integer fissionCouplingPolicyCode = "
        f"{OUTER_ANNULUS_FISSION_COUPLING_POLICY_CODES[fission['coupling_policy']]};",
        f"    final constant Real sourceFraction[{n_seg}] = "
        f"{mo_array(fission['source_fraction'])};",
        f"    final constant Real heatDepositionFraction[{n_seg}] = "
        f"{mo_array(fission['heat_deposition_fraction'])};",
        f"    final constant Integer annulusTemperatureFeedbackPolicyCode = "
        f"{OUTER_ANNULUS_FISSION_FEEDBACK_POLICY_CODES[fission['annulus_temperature_feedback_policy']]};",
        f"    final constant Integer poisonFluxExposurePolicyCode = "
        f"{OUTER_ANNULUS_FISSION_EXPOSURE_POLICY_CODES[fission['poison_flux_exposure_policy']]};",
        f"    // Manifest-facing identity (plan section 7.3): the dataset's "
        f"inventory-allocation policy and production-enablement status ride "
        f"beside the record fields the vehicle binds (the Modelica record "
        f"carries no such fields; the runner manifest and this generated "
        f"identity must agree - plan section 11.1).",
        f'    final constant String inventoryPolicy = "{cfg["inventory_policy"]}";',
        f'    final constant String productionEnablementStatus = '
        f'"{cfg["production_enablement_status"]}";',
        f"  end {pkg};",
    ]


def _core1r_10seg_lines(r1_10seg: Mapping[str, Any]) -> list[str]:
    """Lines for the additive ``Core1R_10Seg`` record (1 channel x 10 axial segments).

    Mirrors the ``Core1R`` field order in :func:`emit_segmented_package`;
    ``LF``/``ArF`` are emitted as ``[nSeg]`` arrays rather than ``LF1``/
    ``LF2`` scalars, following the existing ``kHT[nSeg]`` array convention
    (the segmented assembly does not bind per-segment ``LF``/``ArF`` —
    provenance only).
    """
    n_chan = int(_q(r1_10seg, "n_chan"))
    n_seg = int(_q(r1_10seg, "n_seg"))
    # Owner decision O5: derived as the normalized q_fiss (bit-identical to
    # the historical 1.0/n_seg construction for the uniform 10Seg deck).
    f_salt = _derived_f_salt(_q(r1_10seg, "q_fiss"))
    xy = quantity_value(r1_10seg["channel_map"]["xy"])
    maturity = str(r1_10seg.get("maturity") or "")
    maturity_line = (
        [
            "  // Core maturity (TASK-20260908-01 P2): "
            f"{maturity} - {CORE_MATURITY_LABELS.get(maturity, maturity)}."
        ]
        if maturity
        else []
    )
    return [
        *maturity_line,
        "  package Core1R_10Seg",
        f"    final constant Integer nChan = {n_chan};",
        f"    final constant Integer nSeg = {n_seg};",
        f"    final constant Real cellVol[nChan, nSeg] = {mo_matrix([_q(r1_10seg, 'cell_vol')])};",
        f"    final constant Real volGN = {mo_number(_q(r1_10seg, 'vol_graphite'))};",
        f"    final constant Real hAnom = {mo_number(_q(r1_10seg, 'hAnom'))};",
        f"    final constant Real qFiss[nChan, nSeg] = {mo_matrix([_q(r1_10seg, 'q_fiss')])};",
        f"    final constant Real qMod[nChan, nSeg] = {mo_matrix([_q(r1_10seg, 'q_mod')])};",
        f"    final constant Real fSalt[nChan, nSeg] = {mo_matrix([f_salt])};",
        f"    final constant Real kG = {mo_number(_q(r1_10seg, 'kG'))};",
        f"    final constant Real kHT[nSeg] = {mo_array(_q(r1_10seg, 'kHT'))};",
        f"    final constant Real IF1 = {mo_number(_q(r1_10seg, 'IF')[0])};",
        f"    final constant Real IF2 = {mo_number(_q(r1_10seg, 'IF')[1])};",
        f"    final constant Real IG = {mo_number(_q(r1_10seg, 'IG'))};",
        f"    final constant Real hAExp = {mo_number(_q(r1_10seg, 'hAExp'))};",
        *_film_law_lines(r1_10seg),
        f"    final constant Real flowFrac[nChan] = {mo_array(_q(r1_10seg, 'flow_frac'))};",
        f"    final constant Real Ac = {mo_number(_q(r1_10seg, 'channel_geom', 'Ac'))};",
        f"    final constant Real LF[nSeg] = {mo_array(_q(r1_10seg, 'channel_geom', 'LF'))};",
        f"    final constant Real ArF[nSeg] = {mo_array(_q(r1_10seg, 'channel_geom', 'ArF'))};",
        f"    final constant Real e = {mo_number(_q(r1_10seg, 'channel_geom', 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q_k(r1_10seg, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q_k(r1_10seg, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q_k(r1_10seg, 'trim', 'TG'))};",
        f"    final constant Real heatLossTinf = {mo_number(_q_k(r1_10seg, 'heat_loss_tinf'))};",
        f"    final constant Real pitch = {mo_number(_q(r1_10seg, 'channel_map', 'pitch'))};",
        f"    final constant Real dz = {mo_number(_q(r1_10seg, 'channel_map', 'dz'))};",
        f"    final constant Real chanR[nChan] = {mo_array(_q(r1_10seg, 'channel_map', 'chanR'))};",
        f"    final constant Real xy[nChan, 2] = {mo_matrix(xy)};",
        *_segmented_steady_state_lines(r1_10seg, "r1_10seg", n_chan, n_seg),
        "  end Core1R_10Seg;",
    ]


def _core_r5x5_z10_lines(r5x5_z10: Mapping[str, Any]) -> list[str]:
    """Lines for the additive ``CoreR5x5_Z10`` record (25 channels x 10 axial segments).

    Mirrors the ``Core1R_10Seg`` field order; the map is the 5x5 staggered
    triangular lattice of TASK-20260906-02. Differences from the 10Seg
    record: ``cellVol``/``qFiss``/``qMod`` are 2-D [nChan, nSeg] (already
    2-D in the YAML), ``hAnom`` is a ``Real[nChan]`` array (SegmentedCore
    binds ``hAnom[nChan]``, unlike the 10Seg scalar), ``fSalt`` is derived
    as the normalized ``q_fiss`` (owner decision O5; bit-identical to the
    historical uniform 1/(nChan*nSeg) per-cell construction for this
    first-cut deck), and the record carries the new
    ``final constant Real K_mod`` (non-zero graphite conductivity,
    decision 6; the 10Seg record does not emit K_mod). The product
    assembly (P2) binds ``K_mod``, ``enableRadialMod = true``, and
    ``vol_GN/nChan`` from this record.
    """
    n_chan = int(_q(r5x5_z10, "n_chan"))
    n_seg = int(_q(r5x5_z10, "n_seg"))
    # Owner decision O5: derived as the normalized q_fiss (bit-identical to
    # the historical 1.0/(n_chan*n_seg) construction for the uniform 5x5
    # deck).
    f_salt = _derived_f_salt(_q(r5x5_z10, "q_fiss"))
    xy = quantity_value(r5x5_z10["channel_map"]["xy"])
    k_mod = float(_q(r5x5_z10, "k_mod"))
    k_mod_literal = format(k_mod, ".15g")
    if "." not in k_mod_literal:
        k_mod_literal += ".0"
    maturity = str(r5x5_z10.get("maturity") or "")
    maturity_line = (
        [
            "  // Core maturity (TASK-20260908-01 P2): "
            f"{maturity} - {CORE_MATURITY_LABELS.get(maturity, maturity)}."
        ]
        if maturity
        else []
    )
    return [
        *maturity_line,
        "  package CoreR5x5_Z10",
        f"    final constant Integer nChan = {n_chan};",
        f"    final constant Integer nSeg = {n_seg};",
        f"    final constant Real cellVol[nChan, nSeg] = {mo_matrix(_q(r5x5_z10, 'cell_vol'))};",
        f"    final constant Real volGN = {mo_number(_q(r5x5_z10, 'vol_graphite'))};",
        f"    final constant Real hAnom[nChan] = {mo_array(_q(r5x5_z10, 'hAnom'))};",
        f"    final constant Real qFiss[nChan, nSeg] = {mo_matrix(_q(r5x5_z10, 'q_fiss'))};",
        f"    final constant Real qMod[nChan, nSeg] = {mo_matrix(_q(r5x5_z10, 'q_mod'))};",
        f"    final constant Real fSalt[nChan, nSeg] = {mo_matrix(f_salt)};",
        f"    final constant Real kG = {mo_number(_q(r5x5_z10, 'kG'))};",
        f"    final constant Real kHT[nSeg] = {mo_array(_q(r5x5_z10, 'kHT'))};",
        f"    final constant Real IF1 = {mo_number(_q(r5x5_z10, 'IF')[0])};",
        f"    final constant Real IF2 = {mo_number(_q(r5x5_z10, 'IF')[1])};",
        f"    final constant Real IG = {mo_number(_q(r5x5_z10, 'IG'))};",
        f"    final constant Real hAExp = {mo_number(_q(r5x5_z10, 'hAExp'))};",
        *_film_law_lines(r5x5_z10),
        f"    final constant Real flowFrac[nChan] = {mo_array(_q(r5x5_z10, 'flow_frac'))};",
        f"    final constant Real Ac = {mo_number(_q(r5x5_z10, 'channel_geom', 'Ac'))};",
        f"    final constant Real LF[nSeg] = {mo_array(_q(r5x5_z10, 'channel_geom', 'LF'))};",
        f"    final constant Real ArF[nSeg] = {mo_array(_q(r5x5_z10, 'channel_geom', 'ArF'))};",
        f"    final constant Real e = {mo_number(_q(r5x5_z10, 'channel_geom', 'e'))};",
        f"    final constant Real TF1 = {mo_number(_q_k(r5x5_z10, 'trim', 'TF1'))};",
        f"    final constant Real TF2 = {mo_number(_q_k(r5x5_z10, 'trim', 'TF2'))};",
        f"    final constant Real TG = {mo_number(_q_k(r5x5_z10, 'trim', 'TG'))};",
        f"    final constant Real heatLossTinf = {mo_number(_q_k(r5x5_z10, 'heat_loss_tinf'))};",
        f"    final constant Real pitch = {mo_number(_q(r5x5_z10, 'channel_map', 'pitch'))};",
        f"    final constant Real dz = {mo_number(_q(r5x5_z10, 'channel_map', 'dz'))};",
        f"    final constant Real chanR[nChan] = {mo_array(_q(r5x5_z10, 'channel_map', 'chanR'))};",
        f"    final constant Real xy[nChan, 2] = {mo_matrix(xy)};",
        f"    final constant Real K_mod = {k_mod_literal};",
        *_segmented_steady_state_lines(r5x5_z10, "r5x5_z10", n_chan, n_seg),
        "  end CoreR5x5_Z10;",
    ]


def generated_dir(root: Path | None = None, plant_id: str = "msrr") -> Path:
    """``core/generated/`` for the default plant, ``core/generated/<plant>/`` else."""

    return (root or repo_root()) / "core" / plant_generated_subdir(plant_id)


def catalog_path(plant_id: str = "msrr") -> Path:
    """``data/CATALOG.md`` for the default plant, ``data/plants/<plant>/CATALOG.md`` else."""

    if plant_id == DEFAULT_PLANT_ID:
        return data_root() / "CATALOG.md"
    return data_root() / "plants" / plant_id / "CATALOG.md"


#: C3 provenance stamp written as the first line of every generated
#: PlantData package: the short git revision of the checkout that ran the
#: emitter, or ``unknown`` outside a real Git checkout (unpacked archive
#: extract, wheel install, git unavailable). The value is
#: checkout-dependent, so the ``--check`` comparison ignores the line
#: (see :func:`_without_emitter_stamp`): a stamp left by another commit
#: never fails the check.
EMITTER_STAMP_PREFIX = "// Emitter git revision: "


def _emitter_revision() -> str:
    """Short HEAD revision of the checkout running this emitter.

    ``unknown`` when the tree carrying ``helpers/`` is not a real Git
    checkout, following the classification of :mod:`helpers.git_checkout`
    -- an extract nested inside a checkout must not inherit the enclosing
    repository's identity. Deliberately uncacheable: one cheap ``git
    rev-parse`` per call keeps the value correct when ``repo_root`` is
    redirected (e.g. by tests).
    """

    root = repo_root()
    if not is_real_git_checkout(root):
        return "unknown"
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,  # rev022 M-5: bounded like the other git probes
        )
    except (OSError, subprocess.TimeoutExpired):
        # An unavailable OR wedged (timeout, rev022 M-5) git classifies as
        # ``unknown`` -- the conservative emitter-stamp fallback.
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _emitter_stamp_line() -> str:
    return (
        f"{EMITTER_STAMP_PREFIX}{_emitter_revision()}"
        " (informational; --check ignores this line)"
    )


def _stamped_package_text(text: str) -> str:
    """Prepend the emitter-revision stamp line to a generated package."""

    return _emitter_stamp_line() + "\n" + text


def _without_emitter_stamp(text: str) -> str:
    """Drop emitter-stamp comment lines for the ``--check`` comparison.

    Applied to BOTH sides (regenerated text and committed file), so the
    stamp cannot churn the check across commits or on gitless trees.
    """

    return "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(EMITTER_STAMP_PREFIX)
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` atomically (temp file + ``os.replace``).

    A plain ``write_text`` lets a concurrent reader observe a partially
    written generated file; the temp file is fully written before the
    atomic rename. A pre-existing target keeps its mode (matching the
    previous ``write_text``); a new file takes the umask-derived mode
    (``mkstemp`` starts at 0600).
    """

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        try:
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            mask = os.umask(0)
            os.umask(mask)
            mode = 0o666 & ~mask
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_packages(
    plant_id: str = "msrr",
    *,
    root: Path | None = None,
) -> dict[str, Path]:
    plant = load_plant(plant_id, root=root)
    out_dir = generated_dir(root, plant_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    lumped = out_dir / "MSRR_PlantData.mo"
    _atomic_write_text(
        lumped, _stamped_package_text(emit_lumped_package(plant))
    )
    written["lumped"] = lumped
    segmented = out_dir / "SegmentedMSR_PlantData.mo"
    _atomic_write_text(
        segmented, _stamped_package_text(emit_segmented_package(plant))
    )
    written["segmented"] = segmented
    return written


def emit_catalog(plant: Mapping[str, Any]) -> str:
    lines = [
        "# Plant-data catalog",
        "",
        f"Generated from plant `{plant.get('id')}`. Every quantity path, unit, and `doc` field.",
        "Regenerate with `python3.12 -m helpers.emit_modelica_plant --catalog`.",
        "",
        "| Path | Unit | Value (abbrev.) | Doc |",
        "|---|---|---|---|",
    ]
    for path, qty in walk_quantities(plant):
        unit = qty.get("unit", "")
        value = qty.get("value")
        if isinstance(value, list):
            if value and isinstance(value[0], (list, tuple)):
                shown = str(value)
            else:
                shown = "[" + ", ".join(mo_number(v) for v in value[:3])
                if len(value) > 3:
                    shown += f", … ({len(value)})"
                shown += "]"
        else:
            shown = mo_number(value)
        doc = str(qty.get("doc") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{path}` | {unit} | `{shown}` | {doc} |")
    lines.append("")
    # Core-maturity metadata (TASK-20260908-01 P2): the machine-readable
    # label every core deck carries (validated by helpers.plant_config;
    # mirrored per CLI core key in helpers/segmented_runs.CORE_MATURITY).
    lines.append("## Core maturity")
    lines.append("")
    # Two axes since the rev031 review: implementation maturity and
    # physical-data maturity (physical_data_maturity).
    lines.append("| Core | Maturity | Physical data |")
    lines.append("|---|---|---|")
    cores = plant.get("cores") or {}
    for core_key in sorted(cores):
        core = cores[core_key]
        maturity = core.get("maturity") if isinstance(core, Mapping) else None
        physical = core.get("physical_data_maturity") if isinstance(core, Mapping) else None
        shown = str(maturity) if maturity else "missing"
        shown_physical = str(physical) if physical else "missing"
        lines.append(f"| `{core_key}` | {shown} | {shown_physical} |")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plant",
        default="msrr",
        help="Plant id under data/plants/ (default: msrr). The default plant writes "
        "core/generated/ and data/CATALOG.md; any other plant writes "
        "core/generated/<plant>/ and data/plants/<plant>/CATALOG.md",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Also write data/CATALOG.md from quantity doc fields; "
        "--check verifies the catalog with or without this flag",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if core/generated/MSRR_PlantData.mo, "
        "core/generated/SegmentedMSR_PlantData.mo, or data/CATALOG.md "
        "differs from committed files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    # Data resolves through the resource layer (checkout data/ or the
    # packaged msrr_data payload); ``root`` only pins the core/generated
    # comparison target. Loading with an explicit root would bypass the
    # wheel's packaged data location.
    plant = load_plant(args.plant)
    # The written (and compared) package text carries the C3 emitter
    # revision stamp as its first line; the comparison filters it so the
    # stamp never churns --check (see EMITTER_STAMP_PREFIX).
    lumped_text = _stamped_package_text(emit_lumped_package(plant))
    segmented_text = _stamped_package_text(emit_segmented_package(plant))
    out_dir = generated_dir(root, args.plant)
    targets = {
        out_dir / "MSRR_PlantData.mo": lumped_text,
        out_dir / "SegmentedMSR_PlantData.mo": segmented_text,
    }
    if args.check or args.catalog:
        targets[catalog_path(args.plant)] = emit_catalog(plant)
    if args.check:
        mismatched = []
        for path, text in targets.items():
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            if _without_emitter_stamp(existing) != _without_emitter_stamp(text):
                mismatched.append(str(path))
        if mismatched:
            print("generated files out of date: " + ", ".join(mismatched))
            return 1
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, text)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
