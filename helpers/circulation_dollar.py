"""Per-core 1 $: each model's own nominal-flow circulating-fuel beta_eff.

Physics review 2026-10-02 (M3, TASK-20261002-01 P2): Results II labelled its
reactivity steps in dollars but applied the 1R lumped value (604.887 pcm) to
every core. A model's own dollar is its static beta minus its own circulation
loss at nominal flow, and that loss depends on the core's precursor transport:

- legacy (lumped mPKE): plug-flow sweep and return at the core's own nominal
  transit times (``MSRR.mo`` ``nomTauCore`` / ``nomTauLoop``; the 9R uses its
  mesh volumes), so 1 $ = sum_i beta_i/(1 + (1 - exp(-lambda_i tau_l))/
  (lambda_i tau_c)). msrr: 1R 604.887 pcm, 9R 594.67 pcm (77.33 pcm loss);
  604.887 pcm was 1.017 $ on the lumped 9R.
- segmented (PKE_T + PrecursorNetwork): well-mixed salt cells in series per
  channel or zone, the 9R upper plenum, and the five well-mixed loop tanks.
  The steady-state inventory per group is linear in the bottom concentration,
  so it is solved exactly (two chain evaluations) instead of by the fixed
  point of ``helpers/check_p6b_gates.py``, whose topology it reproduces. msrr:
  1R 605.671, 9R 599.276, 10-seg and 5x5 603.523 pcm (losses 66.3294,
  72.7240, 68.4769 pcm, the QA SteadyTrimCheck anchors).

Reduced-flow cases keep the nominal-flow dollar of their core (documented
Results II convention); each flow case's own ``dollars`` label sets its
insertion (``core_flow_pcm``).
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from helpers.plant_config import load_plant, quantity_value, vol_loop_list

#: Runner core keys -> plant-deck core keys.
_DECK_CORE = {
    "1r": "r1",
    "9r": "r9",
    "1r10seg": "r1_10seg",
    "r5x5_z10": "r5x5_z10",
}

PACKAGES = ("legacy", "segmented")


def _floats(node: Any) -> list[float]:
    return [float(x) for x in quantity_value(node)]


def _kinetics(plant: Mapping[str, Any]) -> tuple[list[float], list[float], float]:
    kin = plant["kinetics"]
    beta = _floats(kin["beta"])
    lam = _floats(kin["lambda"])
    if len(beta) != len(lam):
        raise ValueError(f"kinetics beta/lambda length mismatch: {len(beta)} vs {len(lam)}")
    return beta, lam, float(quantity_value(kin["generation_time"]))


def plug_flow_loss_pcm(
    beta: Sequence[float], lam: Sequence[float], tau_core: float, tau_loop: float, ff: float = 1.0
) -> float:
    """Lumped mPKE steady circulation loss [pcm] at flow fraction ``ff``."""

    tc, tl = tau_core / ff, tau_loop / ff
    effective = sum(b / (1.0 + (1.0 - math.exp(-l * tl)) / (l * tc)) for b, l in zip(beta, lam))
    return 1e5 * (sum(beta) - effective)


def lumped_taus(plant: Mapping[str, Any], core: str) -> tuple[float, float]:
    """Nominal (tau_core, tau_loop) [s] the lumped mPKE of ``core`` uses."""

    key = _DECK_CORE[core]
    if key == "r1":
        kin = plant["kinetics"]
        return float(quantity_value(kin["lumped_tau_core"])), float(quantity_value(kin["lumped_tau_loop"]))
    if key == "r9":
        r9 = plant["cores"]["r9"]
        vdot = float(quantity_value(plant["primary_loop"]["vdot_fuel"]))
        vol_core = sum(_floats(r9["vol_F1"])) + sum(_floats(r9["vol_F2"]))
        total = float(quantity_value(plant["total_fuel_vol"]))
        return vol_core / vdot, (total - vol_core) / vdot
    raise ValueError(f"no lumped model for core {core!r} (legacy package runs 1r and 9r)")


Zone = tuple[float, list[tuple[float, float]]]


def _segmented_network(plant: Mapping[str, Any], core: str) -> tuple[list[Zone], float | None]:
    """(zones, upper-plenum volume) of the segmented precursor network.

    A zone is (flow fraction, [(cell volume, salt production share), ...]) in
    flow order. Shares are the normalized fission shares (the emitter's
    ``fSalt``, owner decision O5).
    """

    key = _DECK_CORE[core]
    deck = plant["cores"][key]
    if key == "r9":
        vol_f1, vol_f2 = _floats(deck["vol_F1"]), _floats(deck["vol_F2"])
        k1, k2 = _floats(deck["kFN1"]), _floats(deck["kFN2"])
        k_total = sum(k1) + sum(k2)
        zones: list[Zone] = []
        for frac, start, end in zip(
            _floats(deck["flow_frac_zones"]),
            quantity_value(deck["zone_start"]),
            quantity_value(deck["zone_end"]),
        ):
            cells: list[tuple[float, float]] = []
            for region in range(int(start), int(end) + 1):
                r = region - 1
                cells.append((vol_f1[r], k1[r] / k_total))
                cells.append((vol_f2[r], k2[r] / k_total))
            zones.append((frac, cells))
        return zones, float(quantity_value(deck["vol_upper_plenum"]))
    cell_vol = quantity_value(deck["cell_vol"])
    q_fiss = quantity_value(deck["q_fiss"])
    flow_frac = _floats(deck["flow_frac"])
    # The 1R / 10-seg decks author one channel as a flat array.
    if cell_vol and not isinstance(cell_vol[0], (list, tuple)):
        cell_vol, q_fiss = [cell_vol], [q_fiss]
    q_total = sum(float(q) for row in q_fiss for q in row)
    zones = [
        (frac, [(float(v), float(q) / q_total) for v, q in zip(vols, qs)])
        for frac, vols, qs in zip(flow_frac, cell_vol, q_fiss)
    ]
    return zones, None


def _chain(
    lam: float,
    prod: float,
    zones: Iterable[Zone],
    vol_up: float | None,
    vol_loop: Sequence[float],
    vdot: float,
    c_bottom: float,
) -> tuple[float, float, float]:
    """One pass round the circuit from bottom concentration ``c_bottom``:
    (core inventory, ex-core inventory, bottom concentration after the pass)."""

    core = 0.0
    c_mixed = 0.0
    for frac, cells in zones:
        q = vdot * frac
        c = c_bottom
        for vol, share in cells:
            x = (prod * share + q * c) / (lam + q / vol)
            core += x
            c = x / vol
        c_mixed += frac * c
    excore = 0.0
    c = c_mixed
    if vol_up:
        x = vdot * c / (lam + vdot / vol_up)
        excore += x
        c = x / vol_up
    for vol in vol_loop:
        x = vdot * c / (lam + vdot / vol)
        excore += x
        c = x / vol
    return core, excore, c


def segmented_loss_pcm(plant: Mapping[str, Any], core: str) -> float:
    """Segmented PrecursorNetwork steady circulation loss [pcm] at FF = 1."""

    beta, lam, gen_time = _kinetics(plant)
    zones, vol_up = _segmented_network(plant, core)
    vol_loop = vol_loop_list(plant)
    vdot = float(quantity_value(plant["primary_loop"]["vdot_fuel"]))
    effective = 0.0
    for b, l in zip(beta, lam):
        prod = b / gen_time
        # Linear in the bottom concentration: c_out = a + s * c_bottom.
        _, _, a = _chain(l, prod, zones, vol_up, vol_loop, vdot, 0.0)
        _, _, a1 = _chain(l, prod, zones, vol_up, vol_loop, vdot, 1.0)
        slope = a1 - a
        c_bottom = a / (1.0 - slope)
        core_inv, excore_inv, _ = _chain(l, prod, zones, vol_up, vol_loop, vdot, c_bottom)
        effective += b * core_inv / (core_inv + excore_inv)
    return 1e5 * (sum(beta) - effective)


def dollar_pcm(plant: Mapping[str, Any] | str, package: str, core: str) -> float:
    """1 $ [pcm] of ``core`` on ``package`` (nominal flow), unrounded.

    Step amplitudes round the product (``core_step_pcm``), so the 1R lumped
    cases reproduce the table's ``pcm`` entries exactly.
    """

    if isinstance(plant, str):
        plant = load_plant(plant)
    if package not in PACKAGES:
        raise ValueError(f"package must be one of {PACKAGES}, got {package!r}")
    if core not in _DECK_CORE:
        raise ValueError(f"unknown core {core!r}; expected one of {sorted(_DECK_CORE)}")
    beta, lam, _ = _kinetics(plant)
    if package == "legacy":
        loss = plug_flow_loss_pcm(beta, lam, *lumped_taus(plant, core))
    else:
        loss = segmented_loss_pcm(plant, core)
    return 1e5 * sum(beta) - loss


def core_step_pcm(
    step_pcm: Mapping[str, float],
    step_dollars: Mapping[str, float],
    dollar: float,
) -> dict[str, float]:
    """Per-core step amplitudes: dollar-labelled cases at this core's 1 $.

    Cases without a ``dollars`` label (fixed-pcm steps) keep their pcm.
    """

    return {
        case: (round(float(step_dollars[case]) * dollar, 3) if case in step_dollars else float(pcm))
        for case, pcm in step_pcm.items()
    }


def core_flow_pcm(
    flow_cases: Mapping[str, float],
    step_dollars: Mapping[str, float],
    dollar: float,
    default_pcm: float,
) -> dict[str, float]:
    """Per-core flow-case insertions: each labelled case at its own dollars.

    A flow case with a ``dollars`` label inserts that many of this core's
    nominal-flow dollars; an unlabelled one inserts ``default_pcm`` (the
    core's ``step_1dol`` amplitude, the former rule for every flow case).
    """

    return {
        case: (round(float(step_dollars[case]) * dollar, 3) if case in step_dollars else float(default_pcm))
        for case in flow_cases
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plant", default="msrr", help="plant deck id [msrr]")
    args = ap.parse_args(argv)
    plant = load_plant(args.plant)
    for package, cores in (("legacy", ("1r", "9r")), ("segmented", tuple(_DECK_CORE))):
        for core in cores:
            d = dollar_pcm(plant, package, core)
            print(f"{args.plant:10s} {package:9s} {core:9s} 1 $ = {d:.3f} pcm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
