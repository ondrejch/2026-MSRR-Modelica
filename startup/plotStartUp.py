#!/usr/bin/env python3
"""Plot the legacy startup run outputs from a startup simulation CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .paths import default_startup_csv_path, default_startup_run_dir
except ImportError:
    from paths import default_startup_csv_path, default_startup_run_dir


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

    mean_neutron_generation_time = 0.017
    source_strength = 1e6
    reference_neutron_flux = 1.58e20

    time_hours = table[time_col] / 3600.0
    neutron_population = table[n_pop_col] * reference_neutron_flux

    t0_index = int(np.argmin(np.abs(time_hours - 1.9)))
    n0 = neutron_population.iloc[t0_index]
    multiplication = neutron_population / n0
    keff_relative = 1.0 - (1.0 / multiplication)
    keff_source_subtraction = 1.0 - (
        (mean_neutron_generation_time * source_strength) / neutron_population
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
    plt.ylabel("Neutron Population")
    plt.text(5, 3.7e6, "Phase 1", fontsize=14, color="#FF0000")
    plt.text(16, 3.7e6, "Phase 2", fontsize=14, color="#FF0000")
    plt.text(21, 3.7e6, "Phase 3", fontsize=14, color="#FF0000")
    plt.text(26, 3.7e6, "Phase 4", fontsize=14, color="#FF0000")
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
    raise SystemExit(main())
