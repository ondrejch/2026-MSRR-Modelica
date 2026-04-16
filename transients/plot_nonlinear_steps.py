#!/usr/bin/env python3
"""Plot nonlinear step dynamics results for manuscript figures (1R vs 9R)."""

from __future__ import annotations

import argparse
import colorsys
import os
from pathlib import Path
import shutil
import sys

try:
    from .paths import default_transients_run_dir
except ImportError:
    from paths import default_transients_run_dir


def _ensure_supported_python() -> None:
    if sys.version_info < (3, 13):
        return
    if os.environ.get("MSRR_PLOT_REEXEC") == "1":
        return
    py312 = shutil.which("python3.12")
    if py312 is None:
        raise SystemExit(
            "Python 3.13 detected, but this environment's NumPy/Matplotlib build is not "
            "compatible. Run with python3.12 (or install matching 3.13 wheels)."
        )
    os.environ["MSRR_PLOT_REEXEC"] = "1"
    os.execv(py312, [py312, *sys.argv])


_ensure_supported_python()

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Failed to import plotting dependencies (matplotlib/numpy/pandas). "
        "Use python3.12 or install compatible packages for this interpreter."
    ) from exc


STEP_CASES = [
    ("step_2dol", "2.0 $", "#2ca02c"),
    ("step_1dol", "1.0 $", "#1f77b4"),
    ("step_0p5dol", "0.5 $", "#d62728"),
]

FLOW_CASES = [
    ("flow_100pct", "1.0× flow", "#2ca02c"),
    ("flow_66pct", "2/3× flow", "#1f77b4"),
    ("flow_33pct", "1/3× flow", "#d62728"),
]

CORE_STYLE = {
    "1r": {"label": "1R", "line": "--", "hue_shift": 0.0},
    "9r": {"label": "9R", "line": "-", "hue_shift": 0.04},
}

UHX_CASE = "uhx_trip"


def _norm(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def _shift_hue(hex_color: str, hue_shift: float) -> str:
    """Apply a small hue shift while preserving lightness/saturation."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + float(hue_shift)) % 1.0
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    r2 = int(round(r2 * 255))
    g2 = int(round(g2 * 255))
    b2 = int(round(b2 * 255))
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def _core_color(base_color: str, core_model: str) -> str:
    return _shift_hue(base_color, CORE_STYLE[core_model]["hue_shift"])


def _apply_case_core_legend(
    axis: plt.Axes,
    cases: list[tuple[str, str, str]],
    core_models: list[str],
    *,
    loc: str = "upper right",
    ncol: int = 2,
    fontsize: int = 9,
) -> None:
    """Apply deterministic legend order grouped by core model columns."""
    handles, labels = axis.get_legend_handles_labels()
    handle_by_label = dict(zip(labels, handles))
    ordered_labels: list[str] = []
    for core_model in core_models:
        tag = CORE_STYLE[core_model]["label"]
        for _, case_label, _ in cases:
            ordered_labels.append(f"{case_label} ({tag})")
    ordered_handles = [handle_by_label[label] for label in ordered_labels if label in handle_by_label]
    ordered_labels = [label for label in ordered_labels if label in handle_by_label]
    axis.legend(ordered_handles, ordered_labels, loc=loc, ncol=ncol, fontsize=fontsize)


def _resolve_outputs_for_core(outputs_dir: Path, core_model: str) -> Path:
    core_dir = outputs_dir / core_model
    if core_dir.is_dir():
        return core_dir
    # Backward compatibility: legacy 1R outputs lived directly in outputs_dir.
    if core_model == "1r" and outputs_dir.is_dir():
        return outputs_dir
    raise FileNotFoundError(
        f"Missing outputs for core '{core_model}': expected {core_dir}"
    )


def find_column(columns: list[str], exact: tuple[str, ...], suffix: tuple[str, ...]) -> str | None:
    cols_norm = {_norm(c): c for c in columns}
    for name in exact:
        key = _norm(name)
        if key in cols_norm:
            return cols_norm[key]
    for candidate in suffix:
        cand = _norm(candidate)
        for col in columns:
            col_norm = _norm(col)
            if col_norm.startswith("der("):
                continue
            if col_norm.endswith(cand):
                return col
    return None


def find_columns_by_suffix(columns: list[str], suffix: str) -> list[str]:
    suffix_norm = _norm(suffix)
    matched: list[str] = []
    for col in columns:
        col_norm = _norm(col)
        if col_norm.startswith("der("):
            continue
        if col_norm.endswith(suffix_norm):
            matched.append(col)
    return matched


def to_kw(series: np.ndarray) -> np.ndarray:
    max_abs = float(np.nanmax(np.abs(series)))
    if max_abs > 1.0e4:
        return series / 1000.0
    if max_abs < 100.0:
        return series * 1000.0
    return series


def load_power_time(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(csv_path)
    columns = list(df.columns)
    time_col = find_column(columns, exact=("time",), suffix=("time",))
    power_col = find_column(
        columns,
        exact=(
            "core1R.powerblock.fissionPower.P",
            "msre9r.powerblock.fissionPower.P",
            "powerBlock.fissionPower.P",
        ),
        suffix=("powerblock.fissionpower.p",),
    )
    if time_col is None or power_col is None:
        missing = []
        if time_col is None:
            missing.append("time")
        if power_col is None:
            missing.append("powerBlock.fissionPower.P")
        raise KeyError(f"{csv_path}: missing required columns: {missing}")
    t_s = df[time_col].to_numpy(dtype=float)
    p_kw = to_kw(df[power_col].to_numpy(dtype=float))
    return t_s, p_kw


def load_fuel_graphite_feedback(csv_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = pd.read_csv(csv_path)
    columns = list(df.columns)

    time_col = find_column(columns, exact=("time",), suffix=("time",))
    if time_col is None:
        raise KeyError(f"{csv_path}: missing time column")

    fuel1_cols = find_columns_by_suffix(columns, "fuelNode1.T")
    fuel2_cols = find_columns_by_suffix(columns, "fuelNode2.T")
    grap_cols = find_columns_by_suffix(columns, "grapNode.T")
    fb_col = find_column(
        columns,
        exact=(
            "core1R.react.TotalTempFeedback",
            "msre9r.sumFB.reactivityOut.rho",
            "react.TotalTempFeedback",
        ),
        suffix=("totaltempfeedback",),
    )
    fb_region_cols = find_columns_by_suffix(columns, "TotalTempFeedback")

    if not fuel1_cols or not fuel2_cols or not grap_cols:
        raise KeyError(f"{csv_path}: missing fuel/graphite temperature columns")
    if fb_col is None and not fb_region_cols:
        raise KeyError(f"{csv_path}: missing total temperature feedback column")

    fuel1 = df[fuel1_cols].to_numpy(dtype=float).mean(axis=1)
    fuel2 = df[fuel2_cols].to_numpy(dtype=float).mean(axis=1)
    graphite = df[grap_cols].to_numpy(dtype=float).mean(axis=1)
    fuel_avg = 0.5 * (fuel1 + fuel2)
    if fb_col is not None:
        feedback_raw = df[fb_col].to_numpy(dtype=float)
    else:
        # 9R fallback: sum all regional temperature-feedback channels.
        feedback_raw = df[fb_region_cols].to_numpy(dtype=float).sum(axis=1)
    feedback_pcm = feedback_raw * 1.0e5
    time_s = df[time_col].to_numpy(dtype=float)
    return time_s, fuel_avg, graphite, feedback_pcm


def load_uhx_signals(csv_path: Path) -> dict[str, np.ndarray]:
    df = pd.read_csv(csv_path)
    columns = list(df.columns)

    time_col = find_column(columns, exact=("time",), suffix=("time",))
    total_col = find_column(
        columns,
        exact=("core1R.powerblock.reactorPower", "msre9r.powerblock.reactorPower"),
        suffix=("powerblock.reactorpower",),
    )
    fission_col = find_column(
        columns,
        exact=("core1R.powerblock.fissionPower.P", "msre9r.powerblock.fissionPower.P"),
        suffix=("powerblock.fissionpower.p",),
    )
    decay_col = find_column(
        columns,
        exact=("core1R.powerblock.decayPower", "msre9r.powerblock.decayPower"),
        suffix=("powerblock.decaypower",),
    )
    fuel_in_col = find_column(
        columns,
        exact=("core1R.tempIn.T", "msre9r.tempIn.T"),
        suffix=("tempin.t",),
    )
    fuel_out_col = find_column(
        columns,
        exact=("core1R.tempOut.T", "msre9r.tempOut.T"),
        suffix=("tempout.t",),
    )
    grap_cols = find_columns_by_suffix(columns, "grapNode.T")
    feedback_col = find_column(
        columns,
        exact=("core1R.react.TotalTempFeedback", "msre9r.sumFB.reactivityOut.rho"),
        suffix=("totaltempfeedback",),
    )
    feedback_region_cols = find_columns_by_suffix(columns, "TotalTempFeedback")

    required = {
        "time": time_col,
        "reactorPower": total_col,
        "fissionPower": fission_col,
        "decayPower": decay_col,
        "tempIn": fuel_in_col,
        "tempOut": fuel_out_col,
        "feedback": feedback_col,
    }
    missing = [key for key, col in required.items() if col is None]
    if feedback_col is None and not feedback_region_cols:
        missing.append("feedback")
    if missing or not grap_cols:
        raise KeyError(f"{csv_path}: missing UHX columns {missing + (['grapNode'] if not grap_cols else [])}")

    if feedback_col is not None:
        feedback_raw = df[feedback_col].to_numpy(dtype=float)
    else:
        feedback_raw = df[feedback_region_cols].to_numpy(dtype=float).sum(axis=1)

    return {
        "time": df[time_col].to_numpy(dtype=float),
        "total_power": df[total_col].to_numpy(dtype=float),
        "fission_power": df[fission_col].to_numpy(dtype=float),
        "decay_power": df[decay_col].to_numpy(dtype=float),
        "fuel_in": df[fuel_in_col].to_numpy(dtype=float),
        "fuel_out": df[fuel_out_col].to_numpy(dtype=float),
        "graphite": df[grap_cols].to_numpy(dtype=float).mean(axis=1),
        "feedback_pcm": feedback_raw * 1.0e5,
    }


def plot_steps(out_dir: Path, outputs_by_core: dict[str, Path], core_models: list[str]) -> Path:
    step_time_s = 2000.0
    fig, axes = plt.subplots(4, 1, figsize=(10.5, 9.8), sharex=True)

    for case, label, base_color in STEP_CASES:
        for core_model in core_models:
            csv_path = outputs_by_core[core_model] / f"{case}_res.csv"
            t_s, p_kw = load_power_time(csv_path)
            _, fuel_avg, graphite, feedback_pcm = load_fuel_graphite_feedback(csv_path)
            t_rel = t_s - step_time_s
            mask = (t_rel >= -50) & (t_rel <= 400)
            base_mask = (t_rel >= -50) & (t_rel < 0)
            if np.any(base_mask):
                fuel_ref = float(fuel_avg[base_mask].mean())
                graphite_ref = float(graphite[base_mask].mean())
            else:
                fuel_ref = float(fuel_avg[0])
                graphite_ref = float(graphite[0])

            color = _core_color(base_color, core_model)
            style = CORE_STYLE[core_model]["line"]
            tag = CORE_STYLE[core_model]["label"]
            curve_label = f"{label} ({tag})"

            axes[0].plot(t_rel[mask], p_kw[mask] * 1000.0, linewidth=1.2, linestyle=style, label=curve_label, color=color)
            axes[1].plot(t_rel[mask], (fuel_avg - fuel_ref)[mask], linewidth=1.2, linestyle=style, color=color)
            axes[2].plot(t_rel[mask], (graphite - graphite_ref)[mask], linewidth=1.2, linestyle=style, color=color)
            axes[3].plot(t_rel[mask], feedback_pcm[mask], linewidth=1.2, linestyle=style, color=color)

    axes[0].set_title("Total Power")
    axes[0].set_ylabel("Power [W]")
    axes[0].legend(loc="upper right", ncol=2, fontsize=9)
    axes[0].grid(True, alpha=0.3)

    axes[1].set_title("Core Avg. Fuel Temperature Change")
    axes[1].set_ylabel("Delta Temperature [C]")
    axes[1].grid(True, alpha=0.3)

    axes[2].set_title("Core Graphite Temperature Change")
    axes[2].set_ylabel("Delta Temperature [C]")
    axes[2].grid(True, alpha=0.3)

    axes[3].set_title("Total Temperature Feedback")
    axes[3].set_ylabel("Feedback [pcm]")
    axes[3].set_xlabel("Time [s]")
    axes[3].grid(True, alpha=0.3)

    axes[-1].set_xlim(-50, 400)
    fig.tight_layout()
    out_path = out_dir / "MSRRstep_nominal.png"
    fig.savefig(out_path, dpi=180)
    return out_path


def plot_flow(out_dir: Path, outputs_by_core: dict[str, Path], core_models: list[str]) -> Path:
    step_time_s = 4000.0
    fig, axes = plt.subplots(4, 1, figsize=(10.5, 9.8), sharex=True)

    for case, label, base_color in FLOW_CASES:
        for core_model in core_models:
            csv_path = outputs_by_core[core_model] / f"{case}_res.csv"
            t_s, p_kw = load_power_time(csv_path)
            _, fuel_avg, graphite, feedback_pcm = load_fuel_graphite_feedback(csv_path)
            t_rel = t_s - step_time_s
            mask = (t_rel >= -50) & (t_rel <= 400)
            base_mask = (t_rel >= -50) & (t_rel < 0)
            if np.any(base_mask):
                fuel_ref = float(fuel_avg[base_mask].mean())
                graphite_ref = float(graphite[base_mask].mean())
            else:
                fuel_ref = float(fuel_avg[0])
                graphite_ref = float(graphite[0])

            color = _core_color(base_color, core_model)
            style = CORE_STYLE[core_model]["line"]
            tag = CORE_STYLE[core_model]["label"]
            curve_label = f"{label} ({tag})"

            axes[0].plot(t_rel[mask], p_kw[mask] * 1000.0, linewidth=1.2, linestyle=style, label=curve_label, color=color)
            axes[1].plot(t_rel[mask], (fuel_avg - fuel_ref)[mask], linewidth=1.2, linestyle=style, color=color)
            axes[2].plot(t_rel[mask], (graphite - graphite_ref)[mask], linewidth=1.2, linestyle=style, color=color)
            axes[3].plot(t_rel[mask], feedback_pcm[mask], linewidth=1.2, linestyle=style, color=color)

    axes[0].set_title("Total Power")
    axes[0].set_ylabel("Power [W]")
    _apply_case_core_legend(axes[0], FLOW_CASES, core_models, loc="upper right", ncol=2, fontsize=9)
    axes[0].grid(True, alpha=0.3)

    axes[1].set_title("Core Avg. Fuel Temperature Change")
    axes[1].set_ylabel("Delta Temperature [C]")
    axes[1].grid(True, alpha=0.3)

    axes[2].set_title("Core Graphite Temperature Change")
    axes[2].set_ylabel("Delta Temperature [C]")
    axes[2].grid(True, alpha=0.3)

    axes[3].set_title("Total Temperature Feedback")
    axes[3].set_ylabel("Feedback [pcm]")
    axes[3].set_xlabel("Time [s]")
    axes[3].grid(True, alpha=0.3)

    axes[-1].set_xlim(-50, 400)
    fig.tight_layout()
    out_path = out_dir / "MSRRstep_flow.png"
    fig.savefig(out_path, dpi=180)
    return out_path


def plot_uhx(out_dir: Path, outputs_by_core: dict[str, Path], core_models: list[str]) -> Path:
    step_time_s = 4000.0
    fig, axes = plt.subplots(3, 1, figsize=(10.5, 9.3), sharex=True)

    component_colors = {
        "total": "#d62728",
        "fission": "#1f77b4",
        "decay": "#2ca02c",
        "fuel_in": "#d62728",
        "fuel_out": "#1f77b4",
        "graphite": "#2ca02c",
        "feedback": "#9467bd",
    }

    for core_model in core_models:
        csv_path = outputs_by_core[core_model] / f"{UHX_CASE}_res.csv"
        signals = load_uhx_signals(csv_path)

        t_rel_s = signals["time"] - step_time_s
        t_rel_h = t_rel_s / 3600.0

        base_mask = (t_rel_h >= -0.5) & (t_rel_h < 0.0)
        base_total = float(signals["total_power"][base_mask].mean()) if np.any(base_mask) else float(signals["total_power"][0])
        if base_total == 0.0:
            base_total = float(signals["total_power"][0])

        norm_total = signals["total_power"] / base_total
        norm_fission = signals["fission_power"] / base_total
        norm_decay = signals["decay_power"] / base_total

        style = CORE_STYLE[core_model]["line"]
        tag = CORE_STYLE[core_model]["label"]

        mask = (t_rel_h >= -0.5) & (t_rel_h <= 4.0)
        axes[0].plot(
            t_rel_h[mask],
            norm_total[mask],
            color=_core_color(component_colors["total"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Total ({tag})",
        )
        axes[0].plot(
            t_rel_h[mask],
            norm_fission[mask],
            color=_core_color(component_colors["fission"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Fission ({tag})",
        )
        axes[0].plot(
            t_rel_h[mask],
            norm_decay[mask],
            color=_core_color(component_colors["decay"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Decay ({tag})",
        )

        axes[1].plot(
            t_rel_h[mask],
            (signals["fuel_in"] - float(signals["fuel_in"][base_mask].mean()))[mask],
            color=_core_color(component_colors["fuel_in"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Fuel Inlet ({tag})",
        )
        axes[1].plot(
            t_rel_h[mask],
            (signals["fuel_out"] - float(signals["fuel_out"][base_mask].mean()))[mask],
            color=_core_color(component_colors["fuel_out"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Fuel Outlet ({tag})",
        )
        axes[1].plot(
            t_rel_h[mask],
            (signals["graphite"] - float(signals["graphite"][base_mask].mean()))[mask],
            color=_core_color(component_colors["graphite"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Graphite ({tag})",
        )

        axes[2].plot(
            t_rel_h[mask],
            signals["feedback_pcm"][mask],
            color=_core_color(component_colors["feedback"], core_model),
            linewidth=1.2,
            linestyle=style,
            label=f"Total Feedback ({tag})",
        )

    axes[0].set_title("Normalized Power (relative to pre-trip baseline)")
    axes[0].set_ylabel("Normalized [-]")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="upper right", ncol=2, fontsize=9)

    axes[1].set_title("Core Temperature Change (relative to pre-trip baseline)")
    axes[1].set_ylabel("Delta Temperature [C]")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="upper right", ncol=2, fontsize=9)

    axes[2].set_title("Total Temperature Feedback")
    axes[2].set_ylabel("Feedback [pcm]")
    axes[2].set_xlabel("Time [h]")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend(loc="upper right", ncol=1, fontsize=9)

    axes[-1].set_xlim(-0.5, 4.0)
    fig.tight_layout()
    out_path = out_dir / "MSRR_uhx_trip.png"
    fig.savefig(out_path, dpi=180)
    return out_path


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Plot nonlinear step dynamics results.")
    parser.add_argument(
        "--outputs_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing simulation CSVs "
            "(default: 00runs/transients-<core_models>)"
        ),
    )
    parser.add_argument(
        "--fig_dir",
        type=Path,
        default=None,
        help=(
            "Directory for output figures "
            "(default: same as --outputs_dir)"
        ),
    )
    parser.add_argument(
        "--core_models",
        type=str,
        nargs="+",
        choices=tuple(CORE_STYLE.keys()),
        default=["1r", "9r"],
        help="Core models to overlay in plots (default: 1r 9r).",
    )
    args = parser.parse_args()
    if args.outputs_dir is None:
        args.outputs_dir = default_transients_run_dir(
            repo_root,
            core_models=args.core_models,
        )
    if args.fig_dir is None:
        args.fig_dir = args.outputs_dir
    return args


def main() -> int:
    args = parse_args()
    outputs_dir = args.outputs_dir.resolve()
    fig_dir = args.fig_dir.resolve()
    fig_dir.mkdir(parents=True, exist_ok=True)

    core_models = list(dict.fromkeys(args.core_models))
    outputs_by_core = {core: _resolve_outputs_for_core(outputs_dir, core) for core in core_models}

    step_fig = plot_steps(fig_dir, outputs_by_core, core_models)
    flow_fig = plot_flow(fig_dir, outputs_by_core, core_models)
    uhx_fig = plot_uhx(fig_dir, outputs_by_core, core_models)

    print(f"Wrote: {step_fig}")
    print(f"Wrote: {flow_fig}")
    print(f"Wrote: {uhx_fig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
