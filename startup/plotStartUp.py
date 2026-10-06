#!/usr/bin/env python3
"""Plot the legacy startup run outputs from a startup simulation CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from ._common import ensure_supported_python
    from .paths import default_startup_csv_path, default_startup_run_dir
except ImportError:  # script-style execution from startup/
    from _common import ensure_supported_python
    from paths import default_startup_csv_path, default_startup_run_dir


ensure_supported_python()


SCENARIO = "startup"


def _norm(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def find_column(
    columns: list[str],
    exact: tuple[str, ...] = (),
    suffix: tuple[str, ...] = (),
) -> str | None:
    cols_norm = {_norm(c): c for c in columns}

    for name in exact:
        key = _norm(name)
        if key in cols_norm:
            return cols_norm[key]

    for candidate in suffix:
        cand = _norm(candidate)
        for col in columns:
            if _norm(col).endswith(cand):
                return col

    return None


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Plot startup neutron population, external reactivity, and keff figures."
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core model for default run path and CSV name",
    )
    parser.add_argument(
        "--run_dir",
        type=Path,
        default=None,
        help=(
            "Run directory containing startup artifacts "
            "(default: 00runs/startup-startup-<core_model>)"
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Path to the startup result CSV (default derived from run definition)",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=None,
        help="Directory for generated plot PNGs (default: same startup run directory)",
    )
    parser.add_argument(
        "--assume_current_normalization",
        action="store_true",
        help=(
            "Interpret a result whose run manifest carries no source_normalization "
            "record with the CURRENT plant deck's LAMBDA, nu, P, E_f and eta_S. "
            "Without this flag such a result (e.g. a pre-2026-09-27 run, produced "
            "with the retired 1.58e20 divisor) is refused."
        ),
    )
    args = parser.parse_args()

    if args.run_dir is None:
        args.run_dir = default_startup_run_dir(
            repo_root,
            scenario=SCENARIO,
            core_model=args.core_model,
        )
    if args.csv is None:
        args.csv = default_startup_csv_path(
            repo_root,
            scenario=SCENARIO,
            core_model=args.core_model,
        )
    if args.out_dir is None:
        args.out_dir = args.run_dir
    return args


def startup_kinetics_constants() -> tuple[float, float, float]:
    """(LAMBDA [s], N0, eta_S) from the plant deck -- the values the legacy
    kinetics bind (MSRR_PlantData.Kinetics.LAMBDA / .nu / .energyPerFission /
    .sourceEffectiveness and nominalPower). The model's normalized source is
    eta_S*S/N0 with the full-power neutron population N0 = LAMBDA*nu*P/E_f
    (physics review 2026-09-27; this plotter formerly hard-coded LAMBDA =
    0.017 s, S = 1e6 n/s and the retired 1.58e20 divisor, ~1.2e7 off the
    model's source term). eta_S = 1 is an unsourced unit-importance
    assumption."""
    try:
        from helpers.plant_config import load_plant, quantity_value
    except ImportError:  # direct-script execution outside the repo root
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers.plant_config import load_plant, quantity_value
    plant = load_plant("msrr")
    lam = float(quantity_value(plant["kinetics"]["generation_time"]))
    nu = float(quantity_value(plant["kinetics"]["nu"]))
    power = float(quantity_value(plant["nominal_power"]))
    energy = float(quantity_value(plant["poisons"]["energy_per_fission"]))
    eta_s = float(quantity_value(plant["kinetics"]["source_effectiveness"]))
    return lam, lam * nu * power / energy, eta_s


def run_source_normalization(csv_path: Path, *, assume_current: bool = False) -> tuple[float, float, float]:
    """(LAMBDA, N0, eta_S) the result at *csv_path* was produced with.

    Read from the ``source_normalization`` record of the run manifest
    sidecar next to the CSV (written by startup/runMSRR.py since the rev032
    review). A result without it is refused unless *assume_current* is set,
    in which case the current deck's constants are used (with a warning):
    applying today's constants to a historical result silently would
    misinterpret it.
    """
    import sys

    try:
        from helpers.run_results import read_manifest_sidecar
    except ImportError:  # direct-script execution outside the repo root
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers.run_results import read_manifest_sidecar
    payload = read_manifest_sidecar(csv_path) or {}
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    record = manifest.get("source_normalization")
    if isinstance(record, dict):
        lam = float(record["generation_time_s"])
        n0 = float(record["full_power_population"])
        eta = float(record["source_effectiveness"])
        expected = lam * float(record["nu"]) * float(record["nominal_power_w"]) / float(record["energy_per_fission_j"])
        if not abs(n0 / expected - 1.0) < 1e-9:
            raise ValueError(f"{csv_path}: manifest source_normalization is inconsistent (N0 != LAMBDA*nu*P/E_f)")
        return lam, n0, eta
    if not assume_current:
        raise ValueError(
            f"{csv_path}: its run manifest records no source_normalization, so the "
            "constants it was produced with are unknown (a pre-2026-09-27 result used "
            "the retired 1.58e20 divisor). Rerun it, or pass "
            "--assume_current_normalization to interpret it with the current deck."
        )
    print(
        f"WARNING: {csv_path}: no source_normalization in its manifest; using the "
        "current plant deck's constants (--assume_current_normalization).",
        file=sys.stderr,
    )
    return startup_kinetics_constants()


def source_subtracted_reactivity(
    n_normalized, source_rate, lam: float, n0_pop: float, eta_s: float = 1.0
):
    """Subcritical source-multiplication estimate of 1 - k ~ -rho: at a
    source-driven steady state rho*n*N0/LAMBDA + eta_S*S = 0, so
    -rho = LAMBDA*eta_S*S/N with N = n*N0 the absolute population. Samples
    with no source are NaN (the estimate is undefined there)."""
    import numpy as np

    n_abs = np.asarray(n_normalized, dtype=float) * n0_pop
    s = eta_s * np.broadcast_to(np.asarray(source_rate, dtype=float), n_abs.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where((s > 0) & (n_abs > 0), lam * s / n_abs, np.nan)
    return out


def main() -> int:
    args = parse_args()
    csv_path = args.csv.resolve()
    if not csv_path.exists():
        print(f"ERROR: missing startup results CSV: {csv_path}")
        return 2

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    table = pd.read_csv(csv_path)
    columns = list(table.columns)

    time_col = find_column(columns=columns, exact=("time",))
    n_pop_col = find_column(
        columns=columns,
        exact=("pke.n_population.n",),
        suffix=("mpke.n_population.n", "pke.n_population.n"),
    )
    ext_reactivity_col = find_column(
        columns=columns,
        exact=("pke.externalReactivityIn",),
        suffix=("mpke.externalreactivityin", "pke.externalreactivityin"),
    )
    missing = []
    if time_col is None:
        missing.append("time")
    if n_pop_col is None:
        missing.append("pke.n_population.n")
    if ext_reactivity_col is None:
        missing.append("pke.externalReactivityIn")
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    generation_time, full_power_population, source_effectiveness = run_source_normalization(
        csv_path, assume_current=bool(args.assume_current_normalization)
    )
    source_col = find_column(
        columns=columns,
        exact=("sourceRate",),
        suffix=("core1r.sourcerate", "msre9r.sourcerate", "sourcerate"),
    )
    if source_col is None:
        # No constant fallback (rev032 review): the source is switched during
        # the startup schedule, so only the model's recorded rate is valid.
        raise KeyError("Missing required column: sourceRate (the model's time-dependent source rate)")
    source_rate = table[source_col].to_numpy(dtype=float)

    time_hours = table[time_col] / 3600.0
    neutron_population = table[n_pop_col] * full_power_population

    t0_index = int(np.argmin(np.abs(time_hours - 1.9)))
    # Clip the multiplication denominator like the sibling plotters: a
    # zero sample at the reference time would otherwise put inf/NaN in
    # the keff curves.
    n0 = float(np.clip(neutron_population.iloc[t0_index], 1.0e-30, None))
    multiplication = neutron_population / n0
    keff_relative = 1.0 - (1.0 / multiplication)
    keff_source_subtraction = 1.0 - source_subtracted_reactivity(
        table[n_pop_col].to_numpy(dtype=float),
        source_rate,
        generation_time,
        full_power_population,
        source_effectiveness,
    )

    x_ticks = list(range(31))

    def save_current_figure(filename: str) -> None:
        plt.gcf().set_size_inches(20, 10)
        plt.savefig(out_dir / filename)

    plt.figure(1)
    plt.box(True)
    plt.grid(True)
    plt.plot(time_hours, neutron_population, color="#0000FF", linewidth=1)
    plt.axvline(13.5, linestyle="--", linewidth=2)
    plt.axvline(19.5, linestyle="--", linewidth=2)
    plt.axvline(24.5, linestyle="--", linewidth=2)
    plt.ylabel("Neutron population (n x N0)")
    plt.text(5, 0.93, "Phase 1", fontsize=14, color="#FF0000", transform=plt.gca().get_xaxis_transform())
    plt.text(16, 0.93, "Phase 2", fontsize=14, color="#FF0000", transform=plt.gca().get_xaxis_transform())
    plt.text(21, 0.93, "Phase 3", fontsize=14, color="#FF0000", transform=plt.gca().get_xaxis_transform())
    plt.text(26, 0.93, "Phase 4", fontsize=14, color="#FF0000", transform=plt.gca().get_xaxis_transform())
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([0, 29])
    save_current_figure("plot1.png")

    plt.figure(2)
    plt.box(True)
    plt.grid(True)
    plt.plot(time_hours, neutron_population, color="#0000FF", linewidth=1)
    plt.ylabel("Neutron Population")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([0, 13.5])
    save_current_figure("plot2.png")

    plt.figure(3)
    plt.box(True)
    plt.grid(True)
    plt.plot(time_hours, neutron_population, color="#0000FF", linewidth=1)
    plt.ylabel("Neutron Population")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([13.5, 19.5])
    save_current_figure("plot3.png")

    plt.figure(4)
    plt.box(True)
    plt.grid(True)
    plt.plot(time_hours, neutron_population, color="#0000FF", linewidth=1)
    plt.ylabel("Neutron Population")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([19.5, 24.5])
    save_current_figure("plot4.png")

    plt.figure(5)
    plt.box(True)
    plt.grid(True)
    plt.plot(time_hours, neutron_population, color="#0000FF", linewidth=1)
    plt.ylabel("Neutron Population")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([24.5, 29])
    save_current_figure("plot5.png")

    plt.figure(6)
    plt.box(True)
    plt.grid(True)
    plt.plot(
        time_hours,
        table[ext_reactivity_col] * 1e5,
        color="#0000FF",
        linewidth=1,
    )
    plt.axvline(13.5, linestyle="--", linewidth=2)
    plt.axvline(19.5, linestyle="--", linewidth=2)
    plt.axvline(24.5, linestyle="--", linewidth=2)
    plt.ylabel("External Reactivity [pcm]")
    plt.text(5, 300, "Phase 1", fontsize=14, color="#FF0000")
    plt.text(16, 300, "Phase 2", fontsize=14, color="#FF0000")
    plt.text(21, 300, "Phase 3", fontsize=14, color="#FF0000")
    plt.text(26, 300, "Phase 4", fontsize=14, color="#FF0000")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([0, 29])
    save_current_figure("plot6.png")

    plt.figure(7)
    plt.box(True)
    plt.grid(True)
    plt.plot(
        time_hours,
        keff_relative,
        color="#0000FF",
        linewidth=1,
        label="k_eff (relative to n0)",
    )
    plt.plot(
        time_hours,
        keff_source_subtraction,
        label="k_eff (source subtraction)",
    )
    plt.axvline(13.5, linestyle="--", linewidth=2)
    plt.axvline(19.5, linestyle="--", linewidth=2)
    plt.axvline(24.5, linestyle="--", linewidth=2)
    plt.ylabel("Multiplication Factor")
    plt.legend(loc="best")
    plt.text(5, 0.2, "Phase 1", fontsize=14, color="#FF0000")
    plt.text(16, 0.2, "Phase 2", fontsize=14, color="#FF0000")
    plt.text(21, 0.2, "Phase 3", fontsize=14, color="#FF0000")
    plt.text(26, 0.2, "Phase 4", fontsize=14, color="#FF0000")
    plt.xticks(x_ticks)
    plt.xlabel("Time [h]")
    plt.xlim([1, 14])
    save_current_figure("plot7.png")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2) from None
