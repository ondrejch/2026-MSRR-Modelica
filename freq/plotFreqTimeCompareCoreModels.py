#!/usr/bin/env python3
"""Create an annotated time-domain comparison for a single MSRR frequency run."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from .paths import (
        default_freq_results_root,
        default_freq_time_compare_out_path,
        make_power_tag,
    )
except ImportError:
    from paths import default_freq_results_root, default_freq_time_compare_out_path, make_power_tag


COLOR_1R = "#4E79A7"
COLOR_9R = "#2F8F83"
COLOR_FORCING = "#444444"


def make_freq_tag(freq: float) -> str:
    """Return the repository frequency tag used in result directory names."""
    return f"{freq:08.5f}"


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[1]


def default_results_root() -> Path:
    """Return the default location of frequency run artifacts."""
    return default_freq_results_root(repo_root())


def default_out_path() -> Path:
    """Return the default output path for the selected run definition."""
    return default_freq_time_compare_out_path(repo_root(), power=1.0, freq=0.1)


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Plot an annotated time-domain comparison for one 1R/9R frequency "
            "response case."
        )
    )
    parser.add_argument(
        "--results_root",
        type=Path,
        default=default_results_root(),
        help=(
            "Root directory containing <results_root>/1r and <results_root>/9r "
            "frequency-response results."
        ),
    )
    parser.add_argument(
        "--power",
        type=float,
        default=1.0,
        help="Nominal power level in MW (default: 1.0).",
    )
    parser.add_argument(
        "--freq",
        type=float,
        default=0.1,
        help="Angular forcing frequency in rad/s (default: 0.1).",
    )
    parser.add_argument(
        "--window_start_periods",
        type=float,
        default=10.0,
        help=(
            "Start the plotted window this many forcing periods after the "
            "forcing onset (default: 10)."
        ),
    )
    parser.add_argument(
        "--window_periods",
        type=float,
        default=4.0,
        help="Number of forcing periods to plot (default: 4).",
    )
    parser.add_argument(
        "--ss_time",
        type=float,
        default=None,
        help="Forcing start time override in seconds (default: read run_params.txt).",
    )
    parser.add_argument(
        "--sin_mag_pcm",
        type=float,
        default=None,
        help=(
            "External reactivity forcing amplitude in pcm "
            "(default: read run_params.txt)."
        ),
    )
    parser.add_argument(
        "--out_path",
        type=Path,
        default=None,
        help=(
            "PNG output path "
            "(default: 00runs/freq/plots/freq_time_compare_power_<tag>_omega_<tag>.png)"
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="PNG DPI (default: 300).",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the plot interactively after saving.",
    )
    args = parser.parse_args()
    if args.out_path is None:
        args.out_path = default_freq_time_compare_out_path(
            repo_root(),
            power=args.power,
            freq=args.freq,
        )
    return args


def build_case_dir(results_root: Path, core_model: str, power: float) -> Path:
    """Return the case directory for one core model and power."""
    return results_root / core_model / f"power_{make_power_tag(power)}"


def build_trace_path(results_root: Path, core_model: str, power: float, freq: float) -> Path:
    """Return the raw result CSV path for one frequency point."""
    freq_tag = make_freq_tag(freq)
    return (
        build_case_dir(results_root, core_model, power)
        / f"freq{freq_tag}"
        / f"MSRR_freq{freq_tag}_res.csv"
    )


def read_run_params(case_dir: Path) -> dict[str, float | str]:
    """Read key-value pairs from a run_params.txt file."""
    params: dict[str, float | str] = {}
    params_path = case_dir / "run_params.txt"
    if not params_path.exists():
        return params

    with params_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            parts = raw_line.rstrip().split("\t")
            if len(parts) != 2:
                continue
            key, value = parts
            try:
                params[key] = float(value)
            except ValueError:
                params[key] = value
    return params


def read_fit_summary(case_dir: Path, freq: float) -> pd.Series:
    """Return the fitted gain/phase row nearest to the requested frequency."""
    fit_path = case_dir / "FreqResponseResults.csv"
    if not fit_path.exists():
        raise FileNotFoundError(f"Missing fit summary CSV: {fit_path}")

    data = pd.read_csv(fit_path)
    if "frequency_rad_s" not in data.columns:
        raise ValueError(f"Missing frequency_rad_s column in {fit_path}")

    row = data.loc[(data["frequency_rad_s"] - freq).abs().idxmin()]
    return row


def read_time_trace(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read time and normalized power, dropping duplicate time stamps."""
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing time-trace CSV: {csv_path}")

    data = pd.read_csv(csv_path)
    if data.shape[1] < 2:
        raise ValueError(f"Expected time plus one response column in {csv_path}")

    time = data.iloc[:, 0].to_numpy(dtype=float)
    power = data.iloc[:, 1].to_numpy(dtype=float)

    # OpenModelica CSVs often repeat event times; keep the final value for each
    # repeated stamp so the plotted traces remain clean.
    keep = np.concatenate([time[1:] != time[:-1], [True]])
    return time[keep], power[keep]


def window_time_trace(
    time: np.ndarray,
    power: np.ndarray,
    start_s: float,
    stop_s: float,
    ss_time: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the selected plot window with time referenced to forcing onset."""
    mask = (time >= start_s) & (time <= stop_s)
    if not np.any(mask):
        raise ValueError(
            f"No samples found in requested window [{start_s}, {stop_s}] s."
        )
    return time[mask] - ss_time, power[mask]


def build_annotation(
    power_mw: float,
    freq_rad_s: float,
    fit_1r: pd.Series,
    fit_9r: pd.Series,
    start_periods: float,
    window_periods: float,
) -> str:
    """Return the figure annotation block."""
    freq_hz = freq_rad_s / (2.0 * math.pi)
    period_s = 2.0 * math.pi / freq_rad_s
    return "\n".join(
        [
            f"Nominal power = {power_mw:g} MW",
            rf"$\omega={freq_rad_s:.3g}$ rad/s, $f={freq_hz:.4f}$ Hz, $T={period_s:.1f}$ s",
            f"Window: {window_periods:g} periods after {start_periods:g} periods of settling",
            (
                f"1R: {fit_1r['gain_dB']:.2f} dB, {fit_1r['phase_deg']:.1f} deg, "
                f"R^2={fit_1r['R_squared']:.4f}"
            ),
            (
                f"9R: {fit_9r['gain_dB']:.2f} dB, {fit_9r['phase_deg']:.1f} deg, "
                f"R^2={fit_9r['R_squared']:.4f}"
            ),
        ]
    )


def marker_stride(sample_count: int, target_markers: int = 120) -> int:
    """Return a readable marker stride for dense time traces."""
    return max(1, math.ceil(sample_count / target_markers))


def main() -> int:
    """Generate the annotated time-domain comparison figure."""
    args = parse_args()

    results_root = args.results_root.resolve()
    out_path = args.out_path.resolve()

    case_1r = build_case_dir(results_root, "1r", args.power)
    case_9r = build_case_dir(results_root, "9r", args.power)

    params = read_run_params(case_1r)
    ss_time = float(args.ss_time) if args.ss_time is not None else float(
        params.get("ss_time", 2000.0)
    )
    sin_mag_pcm = float(args.sin_mag_pcm) if args.sin_mag_pcm is not None else float(
        params.get("sin_mag", 1.0)
    )

    period_s = 2.0 * math.pi / args.freq
    window_start_s = ss_time + args.window_start_periods * period_s
    window_stop_s = window_start_s + args.window_periods * period_s

    trace_1r = build_trace_path(results_root, "1r", args.power, args.freq)
    trace_9r = build_trace_path(results_root, "9r", args.power, args.freq)

    time_1r, power_1r = read_time_trace(trace_1r)
    time_9r, power_9r = read_time_trace(trace_9r)

    x_1r, y_1r = window_time_trace(time_1r, power_1r, window_start_s, window_stop_s, ss_time)
    x_9r, y_9r = window_time_trace(time_9r, power_9r, window_start_s, window_stop_s, ss_time)

    fit_1r = read_fit_summary(case_1r, args.freq)
    fit_9r = read_fit_summary(case_9r, args.freq)

    forcing_time = np.linspace(x_1r.min(), x_1r.max(), 1200)
    forcing_pcm = sin_mag_pcm * np.sin(args.freq * forcing_time)
    sample_stride = marker_stride(max(len(x_1r), len(x_9r)))

    fig, (ax_forcing, ax_power) = plt.subplots(
        2,
        1,
        figsize=(10.5, 6.2),
        sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.9]},
    )

    ax_forcing.plot(
        forcing_time,
        forcing_pcm,
        color=COLOR_FORCING,
        linewidth=1.7,
    )
    ax_forcing.axhline(0.0, color="0.7", linewidth=0.8)
    ax_forcing.set_ylabel("External\nreactivity (pcm)")
    ax_forcing.grid(True, linestyle="--", alpha=0.45)
    ax_forcing.text(
        0.015,
        0.88,
        rf"$\Delta \rho_{{ext}}={sin_mag_pcm:g}\,\mathrm{{pcm}}\sin(\omega t)$",
        transform=ax_forcing.transAxes,
        fontsize=10,
        va="top",
        ha="left",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.92},
    )

    ax_power.plot(
        x_9r,
        100.0 * (y_9r - 1.0),
        color=COLOR_9R,
        linewidth=2.1,
        label="9R",
        zorder=2,
    )
    ax_power.plot(
        x_1r,
        100.0 * (y_1r - 1.0),
        color=COLOR_1R,
        linewidth=2.0,
        linestyle="--",
        label="1R",
        zorder=2,
    )
    ax_power.plot(
        x_9r,
        100.0 * (y_9r - 1.0),
        linestyle="none",
        marker="o",
        markersize=3.0,
        markerfacecolor="white",
        markeredgecolor=COLOR_9R,
        markeredgewidth=0.8,
        markevery=slice(0, None, sample_stride),
        label="_nolegend_",
        zorder=3,
    )
    ax_power.plot(
        x_1r,
        100.0 * (y_1r - 1.0),
        linestyle="none",
        marker="s",
        markersize=2.9,
        markerfacecolor="white",
        markeredgecolor=COLOR_1R,
        markeredgewidth=0.8,
        markevery=slice(0, None, sample_stride),
        label="_nolegend_",
        zorder=3,
    )
    ax_power.axhline(0.0, color="0.7", linewidth=0.8)
    ax_power.set_xlabel("Time after forcing onset (s)")
    ax_power.set_ylabel("Power deviation\nfrom nominal (%)")
    ax_power.grid(True, linestyle="--", alpha=0.45)
    ax_power.legend(loc="upper left", frameon=True)
    ax_power.text(
        0.985,
        0.05,
        build_annotation(
            power_mw=args.power,
            freq_rad_s=args.freq,
            fit_1r=fit_1r,
            fit_9r=fit_9r,
            start_periods=args.window_start_periods,
            window_periods=args.window_periods,
        ),
        transform=ax_power.transAxes,
        fontsize=9.4,
        va="bottom",
        ha="right",
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "alpha": 0.95},
    )

    fig.suptitle(
        "Representative nominal-power frequency-response run",
        fontsize=14,
        y=0.985,
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.965))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi, bbox_inches="tight")
    print(f"Saved: {out_path}")

    if args.show:
        plt.show()
    else:
        plt.close(fig)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
