#!/usr/bin/env python3
"""Overlay 1-region and 9-region MSRR Bode responses for selected powers."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

try:
    from .paths import (
        default_freq_plot_dir,
        default_freq_results_root,
        make_power_tag,
    )
except ImportError:
    from paths import default_freq_plot_dir, default_freq_results_root, make_power_tag


def default_results_root() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return default_freq_results_root(repo_root)


def default_out_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return default_freq_plot_dir(repo_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create overlay Bode plots comparing 1-region (blue) and "
            "9-region (red) frequency responses."
        )
    )
    parser.add_argument(
        "--powers",
        nargs="+",
        type=float,
        default=[0.1, 1.0],
        help="Power levels to plot (default: 0.1 1.0)",
    )
    parser.add_argument(
        "--results_root",
        type=Path,
        default=default_results_root(),
        help=(
            "Root directory containing per-core result folders: "
            "<results_root>/1r/power_<tag>/FreqResponseResults.csv and "
            "<results_root>/9r/power_<tag>/FreqResponseResults.csv"
        ),
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=default_out_dir(),
        help="Directory for output overlay figures",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="PNG DPI (default: 200)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display figures interactively in addition to saving",
    )
    return parser.parse_args()


def read_response_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing response CSV: {path}")

    df = pd.read_csv(path)
    required = {"frequency_rad_s", "gain_dB", "phase_deg"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            f"CSV {path} is missing required columns: {sorted(missing)}"
        )
    return df.sort_values("frequency_rad_s")


def build_csv_path(results_root: Path, core_model: str, power: float) -> Path:
    tag = make_power_tag(power)
    return results_root / core_model / f"power_{tag}" / "FreqResponseResults.csv"


def plot_single_power(
    power: float,
    results_root: Path,
    out_dir: Path,
    dpi: int,
    show: bool,
) -> Path:
    csv_1r = build_csv_path(results_root=results_root, core_model="1r", power=power)
    csv_9r = build_csv_path(results_root=results_root, core_model="9r", power=power)

    data_1r = read_response_csv(csv_1r)
    data_9r = read_response_csv(csv_9r)

    fig, (ax_gain, ax_phase) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    ax_gain.semilogx(
        data_1r["frequency_rad_s"],
        data_1r["gain_dB"],
        color="tab:blue",
        linewidth=1.8,
        marker="o",
        markersize=3,
        label="1-region (1r)",
    )
    ax_gain.semilogx(
        data_9r["frequency_rad_s"],
        data_9r["gain_dB"],
        color="tab:red",
        linewidth=1.8,
        marker="s",
        markersize=3,
        label="9-region (9r)",
    )
    ax_gain.set_ylabel("Gain (dB)")
    ax_gain.set_title(f"MSRR Bode Comparison at Power = {power:g} MW")
    ax_gain.grid(True, which="both", linestyle="--", alpha=0.6)
    ax_gain.legend(loc="best")

    ax_phase.semilogx(
        data_1r["frequency_rad_s"],
        data_1r["phase_deg"],
        color="tab:blue",
        linewidth=1.8,
        marker="o",
        markersize=3,
        label="1-region (1r)",
    )
    ax_phase.semilogx(
        data_9r["frequency_rad_s"],
        data_9r["phase_deg"],
        color="tab:red",
        linewidth=1.8,
        marker="s",
        markersize=3,
        label="9-region (9r)",
    )
    ax_phase.set_ylabel("Phase (deg)")
    ax_phase.set_xlabel("Frequency (rad/s)")
    ax_phase.grid(True, which="both", linestyle="--", alpha=0.6)
    ax_phase.legend(loc="best")

    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    tag = make_power_tag(power)
    out_path = out_dir / f"BodePlot_compare_1r_9r_power_{tag}.png"
    fig.savefig(out_path, dpi=dpi)

    if show:
        plt.show()
    else:
        plt.close(fig)

    return out_path


def main() -> int:
    args = parse_args()

    results_root = args.results_root.resolve()
    out_dir = args.out_dir.resolve()

    print(f"Results root: {results_root}")
    print(f"Output dir:   {out_dir}")
    print(f"Powers:       {', '.join(f'{p:g}' for p in args.powers)}")
    print("-" * 70)

    for power in args.powers:
        out_path = plot_single_power(
            power=power,
            results_root=results_root,
            out_dir=out_dir,
            dpi=args.dpi,
            show=args.show,
        )
        print(f"Saved: {out_path}")

    print("-" * 70)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
