#!/usr/bin/env python3
"""Plot diagnostics for the MSRR startup-to-1MW scenario (includes Phase 5)."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from ._common import (
        CORE_CHOICES,
        ensure_supported_python,
        add_package_argument,
        resolve_startup_csv_path,
        startup_plot_run_dir,
    )
    from .paths import default_startup_run_dir
except ImportError:  # script-style execution from startup/
    from _common import (
        CORE_CHOICES,
        ensure_supported_python,
        add_package_argument,
        resolve_startup_csv_path,
        startup_plot_run_dir,
    )
    from paths import default_startup_run_dir



ensure_supported_python()

try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
except Exception as exc:  # pragma: no cover - import path error handling
    raise SystemExit(
        "Failed to import plotting dependencies (matplotlib/numpy/pandas). "
        "Use python3.12 or install compatible packages for this interpreter."
    ) from exc


PHASE_BOUNDARIES_S = (12.0 * 3600.0, 19.0 * 3600.0, 24.0 * 3600.0, 101700.0)
MIN_FLOW_FOR_RHO0 = 1.0e-6
SOURCE_WINDOWS_FALLBACK_S = (
    (3600.0, 47400.0),
    (54000.0, 69000.0),
    (75600.0, 87000.0),
    (93600.0, 101400.0),
)
SCENARIO = "startup_to_1mw"
DEFAULT_OUT_NAME = "plot_startup_to1MW.png"


def _norm(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def find_column(
    columns: list[str],
    exact: tuple[str, ...] = (),
    suffix: tuple[str, ...] = (),
    contains: tuple[str, ...] = (),
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

    for token in contains:
        tok = _norm(token)
        for col in columns:
            if tok in _norm(col):
                return col

    return None


def to_kw(power_series: np.ndarray) -> np.ndarray:
    """Best-effort conversion to kW from typical W/MW outputs."""
    max_abs = float(np.nanmax(np.abs(power_series)))
    if max_abs > 1.0e4:
        # Likely W
        return power_series / 1000.0
    if max_abs < 100.0:
        # Likely MW
        return power_series * 1000.0
    # Likely already kW
    return power_series


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Plot MSRR.MSRRstartUpTo1MW / MSRR.MSRRstartUpTo1MW9R results."
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=CORE_CHOICES,
        default="1r",
        help="Core model for default run path and CSV name",
    )
    parser.add_argument(
        "--run_dir",
        type=Path,
        default=None,
        help=(
            "Run directory containing startup artifacts (default: "
            "00runs/startup-startup_to_1mw-<core_model>); the default CSV "
            "and output names resolve against this directory, and "
            "segmented-package runs (default "
            "00runs/segmented/startup-<scenario>-<core_model>; under an "
            "explicit --run_dir its segmented/ subdirectory) are probed "
            "automatically; a CSV in the segmented default directory keeps "
            "the default outputs beside it"
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help=(
            "Path to simulation CSV (default derived from the run "
            "definition, honoring --run_dir and the segmented/ probe)"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG file (default: <run_dir>/plot_startup_to1MW.png)",
    )
    parser.add_argument(
        "--target_kw",
        type=float,
        default=1000.0,
        help="Target fission power [kW]",
    )
    parser.add_argument(
        "--fission_fraction",
        type=float,
        default=0.95,
        help="Assumed fission fraction of total thermal power (for demand conversion)",
    )
    parser.add_argument(
        "--tail_window_s",
        type=float,
        default=3600.0,
        help="Window [s] at end used for average power",
    )
    parser.add_argument(
        "--fallback_source_strength",
        type=float,
        default=1.0e6,
        help="Source strength [n/s] used when source column is absent",
    )
    parser.add_argument("--dpi", type=int, default=170, help="Figure DPI")
    add_package_argument(parser)
    args = parser.parse_args()

    if args.run_dir is None:
        args.run_dir = default_startup_run_dir(
            repo_root,
            scenario=SCENARIO,
            core_model=args.core_model,
        )
    if args.csv is None:
        # Shared run-dir rule (startup/_common.py): an explicit --run_dir
        # relocates the default CSV; segmented-package runs live at
        # 00runs/segmented/startup-<scenario>-<core> by default, or under
        # <run_dir>/segmented/ (probed automatically for 1r/9r).
        args.csv = resolve_startup_csv_path(
            repo_root,
            scenario=SCENARIO,
            core_model=args.core_model,
            run_dir=args.run_dir,
            package=args.package,
        )
    # Review 2026-10-01 M6: a CSV in the segmented default run directory
    # keeps the default plot outputs beside it, outside 00runs/startup-*.
    args.run_dir = startup_plot_run_dir(
        repo_root,
        scenario=SCENARIO,
        core_model=args.core_model,
        run_dir=args.run_dir,
        csv=args.csv,
    )
    if args.out is None:
        args.out = args.run_dir / DEFAULT_OUT_NAME
    return args


def pick_source_column(columns: list[str]) -> str | None:
    return find_column(
        columns=columns,
        exact=(
            "sourceRate",
            "core1R.sourceRate",
            "msre9r.sourceRate",
            "constantNeutronSource.neutronEmsRateOut.nDot",
            "pke.S.nDot",
        ),
        suffix=(
            "sourcerate",
            "core1r.sourcerate",
            "msre9r.sourcerate",
            "sourcestepper.step.r",
            "constantneutronsource.neutronemsrateout.ndot",
            "pke.s.ndot",
        ),
    )


def source_windows(time_s: np.ndarray, source_rate: np.ndarray) -> list[tuple[float, float]]:
    active = source_rate > 0.0
    windows: list[tuple[float, float]] = []
    start_i: int | None = None
    for i, is_on in enumerate(active):
        if is_on and start_i is None:
            start_i = i
        elif (not is_on) and start_i is not None:
            windows.append((float(time_s[start_i]), float(time_s[i])))
            start_i = None
    if start_i is not None:
        windows.append((float(time_s[start_i]), float(time_s[-1])))
    return windows


def source_from_fallback_schedule(
    time_s: np.ndarray, source_strength: float
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    source_rate = np.zeros_like(time_s, dtype=float)
    windows: list[tuple[float, float]] = []
    for t0, t1 in SOURCE_WINDOWS_FALLBACK_S:
        windows.append((t0, t1))
        source_rate[(time_s >= t0) & (time_s < t1)] = source_strength
    return source_rate, windows


def main() -> int:
    args = parse_args()
    csv_path = args.csv.resolve()
    out_path = args.out.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)

    columns = list(df.columns)

    time_col = find_column(columns=columns, exact=("time",))
    power_col = find_column(
        columns=columns,
        exact=("powerBlock.reactorPower", "powerBlock.fissionPower.P"),
        suffix=("powerblock.reactorpower", "powerblock.fissionpower.p"),
    )
    ext_reactivity_col = find_column(
        columns=columns,
        exact=("pke.externalReactivityIn",),
        suffix=("mpke.externalreactivityin", "pke.externalreactivityin"),
    )
    n_pop_col = find_column(
        columns=columns,
        exact=("pke.n_population.n",),
        suffix=("mpke.n_population.n", "pke.n_population.n"),
    )

    missing_named: list[str] = []
    if time_col is None:
        missing_named.append("time")
    if power_col is None:
        missing_named.append("powerBlock.reactorPower")
    if ext_reactivity_col is None:
        missing_named.append("pke.externalReactivityIn")
    if n_pop_col is None:
        missing_named.append("pke.n_population.n")
    if missing_named:
        raise KeyError(f"Missing required columns: {missing_named}")

    source_col = pick_source_column(columns)

    # 1R names the channel fuelChannel.fuelNode1.T; 9R exposes the nine
    # channels directly as R1..R9 (R1 is the outermost channel).
    fuel1_col = find_column(
        columns=columns,
        exact=("fuelChannel.fuelNode1.T",),
        suffix=("fuelchannel.fuelnode1.t", "r1.fuelnode1.t"),
    )
    fuel2_col = find_column(
        columns=columns,
        exact=("fuelChannel.fuelNode2.T",),
        suffix=("fuelchannel.fuelnode2.t", "r1.fuelnode2.t"),
    )
    grap_col = find_column(
        columns=columns,
        exact=("fuelChannel.grapNode.T",),
        suffix=("fuelchannel.grapnode.t", "r1.grapnode.t"),
    )
    if fuel1_col is None or fuel2_col is None or grap_col is None:
        raise KeyError(
            "Missing required temperature columns matching "
            "fuelChannel.fuelNode1.T / R1.fuelNode1.T, fuelChannel.fuelNode2.T / "
            "R1.fuelNode2.T, fuelChannel.grapNode.T / R1.grapNode.T"
        )

    t_s = df[time_col].to_numpy(dtype=float)
    t_h = t_s / 3600.0
    power_kw = to_kw(df[power_col].to_numpy(dtype=float))
    use_total_power = _norm(power_col).endswith(_norm("powerBlock.reactorPower"))

    demand_col = find_column(
        columns=columns,
        exact=("uhx.PowerDemand.R", "stepper1.step.R", "uhxDemand.step.R"),
        suffix=("uhx.powerdemand.r", "stepper1.step.r", "uhxdemand.step.r"),
    )
    demand_total_kw = to_kw(df[demand_col].to_numpy(dtype=float)) if demand_col else None
    demand_plot_kw = demand_total_kw
    demand_label = "UHX demand"
    if not use_total_power and demand_total_kw is not None:
        demand_plot_kw = args.fission_fraction * demand_total_kw
        demand_label = f"UHX demand (fission-eq, {args.fission_fraction:.2f}x)"
    ext_pcm = df[ext_reactivity_col].to_numpy(dtype=float) * 1.0e5
    # Physics review 2026-09-27: mPKE exports the compensation it actually
    # applies (rho_0applied: the frozen nominal-flow rho_0nom by default);
    # plot that, ungated. Older runs (live convention) lack the column and
    # fall back to the flow-gated rho_0dyn reconstruction below.
    rho0_applied_col = find_column(
        columns=columns,
        exact=("pke.rho_0applied",),
        suffix=("mpke.rho_0applied", "pke.rho_0applied"),
    )
    rho0_col = rho0_applied_col or find_column(
        columns=columns,
        exact=("pke.rho_0sta", "pke.rho_0dyn"),
        suffix=("mpke.rho_0sta", "mpke.rho_0dyn", "pke.rho_0sta", "pke.rho_0dyn"),
    )
    ff_col = find_column(
        columns=columns,
        exact=("pke.fuelFlowFrac.FF",),
        suffix=("mpke.fuelflowfrac.ff", "pke.fuelflowfrac.ff"),
    )
    if rho0_col is not None:
        rho0_pcm = df[rho0_col].to_numpy(dtype=float) * 1.0e5
    else:
        rho0_pcm = np.zeros_like(ext_pcm)
    if ff_col is not None and rho0_applied_col is None:
        fuel_flow_frac = df[ff_col].to_numpy(dtype=float)
        rho0_active_pcm = np.where(fuel_flow_frac > MIN_FLOW_FOR_RHO0, rho0_pcm, 0.0)
    else:
        rho0_active_pcm = rho0_pcm
    ext_comp_pcm = ext_pcm + rho0_active_pcm
    n_pop = np.clip(df[n_pop_col].to_numpy(dtype=float), 1e-30, None)
    if source_col is not None:
        source_nps = df[source_col].to_numpy(dtype=float)
        windows = source_windows(t_s, source_nps)
    else:
        source_nps, windows = source_from_fallback_schedule(
            t_s, source_strength=args.fallback_source_strength
        )
    fuel1_c = df[fuel1_col].to_numpy(dtype=float)
    fuel2_c = df[fuel2_col].to_numpy(dtype=float)
    core_c = df[grap_col].to_numpy(dtype=float)

    fb_col = find_column(
        columns=columns,
        exact=("reactivityFeedback.TotalTempFeedback", "core1R.react.TotalTempFeedback"),
        suffix=("reactivityfeedback.totaltempfeedback", "react.totaltempfeedback"),
    )
    fb_pcm = (
        df[fb_col].to_numpy(dtype=float) * 1.0e5
        if fb_col is not None
        else np.full_like(ext_pcm, np.nan)
    )

    model_reactivity_col = find_column(
        columns=columns,
        exact=("pke.reactivity",),
        suffix=("mpke.reactivity", "pke.reactivity"),
    )
    if model_reactivity_col is not None:
        model_reactivity_label = model_reactivity_col
        model_pcm = df[model_reactivity_col].to_numpy(dtype=float) * 1.0e5
    else:
        model_reactivity_label = "external+rho0+feedback (fallback)"
        model_pcm = ext_comp_pcm + np.nan_to_num(fb_pcm, nan=0.0)
    rho0dyn_col = find_column(
        columns=columns,
        exact=("pke.rho_0dyn",),
        suffix=("mpke.rho_0dyn", "pke.rho_0dyn"),
    )
    if rho0dyn_col is not None:
        rho0dyn_pcm = df[rho0dyn_col].to_numpy(dtype=float) * 1.0e5
        total_pcm = model_pcm - rho0dyn_pcm
    else:
        total_pcm = np.full_like(model_pcm, np.nan)

    tail_mask = t_s >= (t_s[-1] - args.tail_window_s)
    if not np.any(tail_mask):
        tail_mask = np.ones_like(t_s, dtype=bool)
    tail_avg_kw = float(np.mean(power_kw[tail_mask]))

    fig, axes = plt.subplots(3, 1, figsize=(14, 11.5), sharex=False)

    # Source-on spans are added before any legend is built so the
    # "Source on" patch reaches every axis legend.
    for i, (ts0, ts1) in enumerate(windows):
        label = "Source on" if i == 0 else None
        for axis in axes:
            axis.axvspan(ts0 / 3600.0, ts1 / 3600.0, color="#f2c14e", alpha=0.15, label=label)

    ax = axes[0]
    power_label = "Total power" if use_total_power else "Fission power"
    ax.plot(t_h, power_kw, color="#1f77b4", linewidth=1.4, label=power_label)
    if demand_plot_kw is not None:
        ax.plot(
            t_h,
            demand_plot_kw,
            color="#9467bd",
            linewidth=1.2,
            linestyle="--",
            label=demand_label,
        )
    ax.axhline(args.target_kw, color="#d62728", linestyle="--", linewidth=1.2, label="Target")
    ax.axhline(
        tail_avg_kw,
        color="#2ca02c",
        linestyle=":",
        linewidth=1.2,
        label=f"Tail avg = {tail_avg_kw:.1f} kW",
    )
    for idx, t_phase in enumerate(PHASE_BOUNDARIES_S):
        ax.axvline(t_phase / 3600.0, color="#444444", linestyle="--", linewidth=0.9)
        if idx == 3:
            # nanmax: all-NaN power must not put the annotation at NaN.
            ax.text(
                (t_phase / 3600.0) + 0.15,
                0.90 * np.nanmax(power_kw),
                "Phase 5 start",
                fontsize=9,
            )
    ax.set_ylabel("Power [kW]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ax = axes[1]
    ax.plot(t_h, fuel1_c, color="#d62728", linewidth=1.2, label="Fuel node 1")
    ax.plot(t_h, fuel2_c, color="#ff7f0e", linewidth=1.2, label="Fuel node 2")
    ax.plot(t_h, core_c, color="#2ca02c", linewidth=1.2, label="Graphite/core")
    for t_phase in PHASE_BOUNDARIES_S:
        ax.axvline(t_phase / 3600.0, color="#444444", linestyle="--", linewidth=0.8)
    ax.set_ylabel("Temperature [C]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ax = axes[2]
    ext_label = "External + $\\rho_0$ compensation" if rho0_col is not None else "External reactivity"
    ax.plot(t_h, ext_comp_pcm, color="#ff7f0e", linewidth=1.2, label=ext_label)
    ax.plot(t_h, fb_pcm, color="#2ca02c", linewidth=1.2, label="Feedback reactivity")
    ax.plot(t_h, model_pcm, color="#9467bd", linewidth=1.2, label="Model reactivity")
    if not np.all(np.isnan(total_pcm)):
        ax.plot(
            t_h,
            total_pcm,
            color="#d62728",
            linewidth=1.2,
            label="Total reactivity",
        )
    ax.axhline(0.0, color="#555555", linewidth=0.8, linestyle=":")
    for t_phase in PHASE_BOUNDARIES_S:
        ax.axvline(t_phase / 3600.0, color="#444444", linestyle="--", linewidth=0.8)
    ax.set_ylabel("Reactivity [pcm]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, alpha=0.3)

    # Inset: Phase-5 feedback reactivity only.
    phase5_mask = t_s >= PHASE_BOUNDARIES_S[3]
    if np.any(phase5_mask) and not np.all(np.isnan(fb_pcm[phase5_mask])):
        fb_phase5 = fb_pcm[phase5_mask]
        t_phase5_h = t_h[phase5_mask]
        phase5_start_h = PHASE_BOUNDARIES_S[3] / 3600.0
        phase5_end_h = float(t_phase5_h[-1])
        x0_main, x1_main = ax.get_xlim()
        if x1_main > x0_main:
            inset_x0 = (phase5_start_h - x0_main) / (x1_main - x0_main)
            inset_x1 = (phase5_end_h - x0_main) / (x1_main - x0_main)
            inset_x0 = float(np.clip(inset_x0, 0.0, 0.98))
            inset_x1 = float(np.clip(inset_x1, inset_x0 + 0.02, 0.98))
            inset_w = inset_x1 - inset_x0
        else:
            inset_x0, inset_w = 0.58, 0.38
        inset = ax.inset_axes([inset_x0, 0.40, inset_w, 0.28])
        inset.plot(t_phase5_h, fb_phase5, color="#2ca02c", linewidth=1.2)
        inset.grid(True, alpha=0.25)
        inset.set_title("Phase 5: Feedback reactivity only", fontsize=8)
        inset.tick_params(axis="both", labelsize=7)
        inset.set_xlim(phase5_start_h, phase5_end_h)
        phase5_ticks = [x for x in ax.get_xticks() if phase5_start_h <= x <= phase5_end_h]
        if len(phase5_ticks) >= 2:
            inset.set_xticks(phase5_ticks)
        fb_min = float(np.nanmin(fb_phase5))
        fb_max = float(np.nanmax(fb_phase5))
        if np.isfinite(fb_min) and np.isfinite(fb_max):
            span = max(fb_max - fb_min, 1.0e-6)
            pad = 0.08 * span
            inset.set_ylim(fb_min - pad, fb_max + pad)

    ax.legend(loc="best")

    t_end_h = float(t_h.max())
    for axis in axes:
        axis.set_xlim(0.0, t_end_h)

    fig.suptitle("MSRR Startup to 1 MW (Phases 1–5)")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)

    final_kw = float(power_kw[-1])
    max_kw = float(np.max(power_kw))
    max_t_h = float(t_h[int(np.argmax(power_kw))])
    print(f"Wrote: {out_path}")
    print(f"Final {power_label.lower()}: {final_kw:.3f} kW")
    print(f"Tail avg power ({args.tail_window_s:.0f} s): {tail_avg_kw:.3f} kW")
    print(f"Peak power: {max_kw:.3f} kW at {max_t_h:.3f} h")
    print(f"Source windows [s]: {[(round(a, 1), round(b, 1)) for a, b in windows]}")
    if not np.all(np.isnan(total_pcm)):
        print(f"Total reactivity variable (rho_total): {model_reactivity_label} - {rho0dyn_col} [pcm]")
        print(f"Final total reactivity: {float(total_pcm[-1]):.6g} pcm")
    if rho0_col is not None:
        print(
            f"rho0 compensation variable: {rho0_col} "
            + ("(applied compensation, ungated)" if rho0_applied_col
               else f"(flow-gated with {ff_col if ff_col else 'no flow gate'})")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
