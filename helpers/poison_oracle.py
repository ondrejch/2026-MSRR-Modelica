"""Independent five-state homogeneous poison equilibrium oracle.

Implements the global Te-135 / I-135 / Xe-135 / Pm-149 / Sm-149 balances
and the closed-form full-power equilibrium used to initialize
``SegmentedMSR.Nuclear.HomogeneousPoisons``. Assembled from the governing
equations, not by parsing Modelica text. The fuel salt is homogeneous:
this oracle has five global inventories and no spatial network.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

LN2 = math.log(2)

FLUX_AVERAGING_CORE_VOLUME = "core_volume"
FLUX_AVERAGING_TOTAL_FUEL = "total_fuel"

# Authored YAML uses nuclear-customary units; the DAE and PlantData are SI.
BARN_TO_M2 = 1.0e-28
FLUX_CM2_TO_M2 = 1.0e4  # 1/(cm2.s) -> 1/(m2.s)
UNIT_BARN = "b"
UNIT_FLUX_CM2 = "1/(cm2.s)"


def microscopic_xs_m2(node: Mapping[str, Any]) -> float:
    """Convert an authored microscopic cross-section to SI m2.

    YAML must use unit ``b`` (barn). 1 b = 1e-28 m2.
    """

    from helpers.plant_config import quantity_unit, quantity_value

    unit = quantity_unit(node)
    if unit != UNIT_BARN:
        raise ValueError(
            f"microscopic poison cross-section must use unit {UNIT_BARN!r} "
            f"(barns), got {unit!r}"
        )
    return float(quantity_value(node)) * BARN_TO_M2


def neutron_flux_per_m2_s(node: Mapping[str, Any]) -> float:
    """Convert an authored neutron flux to SI 1/(m2.s).

    YAML must use unit ``1/(cm2.s)`` (n/(cm2.s)). Factor 1e4.
    """

    from helpers.plant_config import quantity_unit, quantity_value

    unit = quantity_unit(node)
    if unit != UNIT_FLUX_CM2:
        raise ValueError(
            f"poison reference flux must use unit {UNIT_FLUX_CM2!r} "
            f"(n/(cm2.s)), got {unit!r}"
        )
    return float(quantity_value(node)) * FLUX_CM2_TO_M2


def absorption_rate_constant(
    sigma_a: float,
    phi0: float,
    n_over_n0: float,
    *,
    v_core: float,
    v_fuel: float,
    flux_averaging: str,
) -> float:
    """Volume-integrated effective absorption rate constant b_s [1/s]."""

    if v_fuel <= 0:
        raise ValueError("v_fuel must be > 0")
    if flux_averaging == FLUX_AVERAGING_CORE_VOLUME:
        if v_core <= 0:
            raise ValueError("v_core must be > 0 for core_volume flux averaging")
        if v_core > v_fuel * (1.0 + 1e-12):
            raise ValueError("v_core must not exceed v_fuel")
        return sigma_a * phi0 * (v_core / v_fuel) * n_over_n0
    if flux_averaging == FLUX_AVERAGING_TOTAL_FUEL:
        return sigma_a * phi0 * n_over_n0
    raise ValueError(f"unknown flux_averaging {flux_averaging!r}")


def fission_rate(fission_rate_0: float, n_over_n0: float) -> float:
    return fission_rate_0 * n_over_n0


def equilibrium_inventories(
    *,
    y_te: float,
    y_i: float,
    y_xe: float,
    y_pm: float,
    y_sm: float,
    lam_te: float,
    lam_i: float,
    lam_xe: float,
    lam_pm: float,
    b_xe: float,
    b_sm: float,
    k_rem_xe: float,
    k_rem_sm: float,
    fission_rate_0: float,
) -> dict[str, float]:
    """Closed-form full-power (n/n0 = 1) inventories [atoms]."""

    if lam_te <= 0 or lam_i <= 0 or lam_xe < 0 or lam_pm <= 0:
        raise ValueError("parent decay constants must be positive")
    if k_rem_xe < 0 or k_rem_sm < 0:
        raise ValueError("removal rates must be nonnegative")
    f0 = fission_rate_0
    n_te = y_te * f0 / lam_te
    n_i = (y_i * f0 + lam_te * n_te) / lam_i
    den_xe = lam_xe + b_xe + k_rem_xe
    if den_xe <= 0:
        raise ValueError("Xe equilibrium denominator must be > 0")
    n_xe = (y_xe * f0 + lam_i * n_i) / den_xe
    n_pm = y_pm * f0 / lam_pm
    den_sm = b_sm + k_rem_sm
    if den_sm <= 0:
        raise ValueError(
            "Sm equilibrium denominator must be > 0 "
            "(stable Sm-149 with zero absorption and zero removal has no "
            "finite positive-power steady state)"
        )
    n_sm = (y_sm * f0 + lam_pm * n_pm) / den_sm
    return {
        "N_Te": n_te,
        "N_I": n_i,
        "N_Xe": n_xe,
        "N_Pm": n_pm,
        "N_Sm": n_sm,
    }


def concentrations(inventories: Mapping[str, float], v_fuel: float) -> dict[str, float]:
    if v_fuel <= 0:
        raise ValueError("v_fuel must be > 0")
    return {key.replace("N_", "C_"): value / v_fuel for key, value in inventories.items()}


def raw_worth(sigma_a: float, concentration: float, sigma_a_fuel: float) -> float:
    if sigma_a_fuel <= 0:
        raise ValueError("sigma_a_fuel must be > 0")
    return -sigma_a * concentration / sigma_a_fuel


def worth_denominator(
    *,
    nu: float,
    fission_rate_0: float,
    phi0: float,
    v_core: float,
    v_fuel: float,
    flux_averaging: str,
) -> tuple[float, float]:
    """Derived (Sigma_f, Sigma_a) [1/m] of the homogeneous poison worth.

    Physics review 2026-09-27: the worth denominator is the critical-core
    one-group absorption Sigma_a = nu*Sigma_f, with Sigma_f derived from the
    same full-power fission rate and reference flux that set production and
    burnup, F0 = Sigma_f*phi0*V_avg (V_avg = v_core for core_volume, v_fuel
    for total_fuel). Mirrors SegmentedMSR.Nuclear.HomogeneousPoisons.
    """

    if nu <= 0 or fission_rate_0 <= 0 or phi0 <= 0:
        raise ValueError("nu, fission_rate_0 and phi0 must be > 0")
    if flux_averaging == FLUX_AVERAGING_CORE_VOLUME:
        v_avg = v_core
    elif flux_averaging == FLUX_AVERAGING_TOTAL_FUEL:
        v_avg = v_fuel
    else:
        raise ValueError(f"unknown flux_averaging {flux_averaging!r}")
    if v_avg <= 0:
        raise ValueError("the flux-averaging volume must be > 0")
    sigma_f = fission_rate_0 / (phi0 * v_avg)
    return sigma_f, nu * sigma_f


def balance_residuals(
    *,
    inventories: Mapping[str, float],
    y_te: float,
    y_i: float,
    y_xe: float,
    y_pm: float,
    y_sm: float,
    lam_te: float,
    lam_i: float,
    lam_xe: float,
    lam_pm: float,
    b_xe: float,
    b_sm: float,
    k_rem_xe: float,
    k_rem_sm: float,
    fission_rate: float,
) -> dict[str, float]:
    """Right-hand-side residuals of the five global inventory equations."""

    n_te = inventories["N_Te"]
    n_i = inventories["N_I"]
    n_xe = inventories["N_Xe"]
    n_pm = inventories["N_Pm"]
    n_sm = inventories["N_Sm"]
    f = fission_rate
    return {
        "res_Te": y_te * f - lam_te * n_te,
        "res_I": y_i * f + lam_te * n_te - lam_i * n_i,
        "res_Xe": y_xe * f + lam_i * n_i - (lam_xe + b_xe + k_rem_xe) * n_xe,
        "res_Pm": y_pm * f - lam_pm * n_pm,
        "res_Sm": y_sm * f + lam_pm * n_pm - (b_sm + k_rem_sm) * n_sm,
    }


def from_plant(
    plant: Mapping[str, Any],
    *,
    v_fuel: float,
    v_core: float,
    n_over_n0: float = 1.0,
) -> dict[str, Any]:
    """Evaluate the oracle against a loaded plant deck and aggregate volumes."""

    from helpers.plant_config import quantity_value

    poisons = plant["poisons"]

    def q(key: str) -> float:
        return float(quantity_value(poisons[key]))

    sigma_xe = microscopic_xs_m2(poisons["sigma_a_Xe"])
    sigma_sm = microscopic_xs_m2(poisons["sigma_a_Sm"])
    phi0 = neutron_flux_per_m2_s(poisons["phi0"])
    f0 = float(quantity_value(plant["nominal_power"])) / q("energy_per_fission")
    averaging = str(poisons["flux_averaging"])
    b_xe = absorption_rate_constant(
        sigma_xe,
        phi0,
        n_over_n0,
        v_core=v_core,
        v_fuel=v_fuel,
        flux_averaging=averaging,
    )
    b_sm = absorption_rate_constant(
        sigma_sm,
        phi0,
        n_over_n0,
        v_core=v_core,
        v_fuel=v_fuel,
        flux_averaging=averaging,
    )
    inventories = equilibrium_inventories(
        y_te=q("Te135_yield"),
        y_i=q("I135_yield"),
        y_xe=q("Xe135_yield"),
        y_pm=q("Pm149_yield"),
        y_sm=q("Sm149_yield"),
        lam_te=q("Te135_lambda"),
        lam_i=q("I135_lambda"),
        lam_xe=q("Xe135_lambda"),
        lam_pm=q("Pm149_lambda"),
        b_xe=b_xe,
        b_sm=b_sm,
        k_rem_xe=q("k_rem_Xe"),
        k_rem_sm=q("k_rem_Sm"),
        fission_rate_0=f0 * n_over_n0,
    )
    conc = concentrations(inventories, v_fuel)
    sigma_f, sigma_a_fuel = worth_denominator(
        nu=float(quantity_value(plant["kinetics"]["nu"])),
        fission_rate_0=f0,
        phi0=phi0,
        v_core=v_core,
        v_fuel=v_fuel,
        flux_averaging=averaging,
    )
    rho_xe = raw_worth(sigma_xe, conc["C_Xe"], sigma_a_fuel)
    rho_sm = raw_worth(sigma_sm, conc["C_Sm"], sigma_a_fuel)
    residuals = balance_residuals(
        inventories=inventories,
        y_te=q("Te135_yield"),
        y_i=q("I135_yield"),
        y_xe=q("Xe135_yield"),
        y_pm=q("Pm149_yield"),
        y_sm=q("Sm149_yield"),
        lam_te=q("Te135_lambda"),
        lam_i=q("I135_lambda"),
        lam_xe=q("Xe135_lambda"),
        lam_pm=q("Pm149_lambda"),
        b_xe=b_xe,
        b_sm=b_sm,
        k_rem_xe=q("k_rem_Xe"),
        k_rem_sm=q("k_rem_Sm"),
        fission_rate=f0 * n_over_n0,
    )
    return {
        "fission_rate_0": f0,
        "b_Xe": b_xe,
        "b_Sm": b_sm,
        "inventories": inventories,
        "concentrations": conc,
        "sigma_f": sigma_f,
        "sigma_a_fuel": sigma_a_fuel,
        "rho_Xe_raw": rho_xe,
        "rho_Sm_raw": rho_sm,
        "rho_poison_raw": rho_xe + rho_sm,
        "residuals": residuals,
        "dataset_id": poisons["dataset_id"],
        "yield_convention": poisons["yield_convention"],
        "yield_basis": dict(poisons.get("yield_basis") or {}),
        "flux_averaging": averaging,
    }
