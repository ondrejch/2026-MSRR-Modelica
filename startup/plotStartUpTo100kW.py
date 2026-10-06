#!/usr/bin/env python3
"""Plot diagnostics for the MSRR startup-to-100kW scenario."""

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

MIN_FLOW_FOR_RHO0 = 1.0e-6
SCENARIO = "startup_to_100kw"
DEFAULT_OUT_NAME = "plot_startup_to100kW.png"


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


def to_kw(power_series: np.ndarray) -> np.ndarray:
    """Best-effort conversion to kW from typical W/MW outputs."""
    max_abs = float(np.nanmax(np.abs(power_series)))
    if max_abs > 1.0e4:
        # Current startup CSVs report power-like channels in W.
        return power_series / 1000.0
    if max_abs < 100.0:
        # Older runs occasionally stored these channels in MW.
        return power_series * 1000.0
    # Leave already-kW values alone.
    return power_series


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Plot MSRR.MSRRstartUpTo100kW / MSRR.MSRRstartUpTo100kW9R results."
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
            "00runs/startup-startup_to_100kw-<core_model>); the default CSV "
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
        help="Output PNG file (default: <run_dir>/plot_startup_to100kW.png)",
    )
    parser.add_argument(
        "--target_kw",
        type=float,
        default=100.0,
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
        default=2000.0,
        help="Window [s] at end used for average power",
    )
    parser.add_argument(
        "--nu_prod_at_100kw",
        type=float,
        default=3.1e15,
        help="Neutron production rate [n/s] at 100 kW",
    )
    parser.add_argument("--dpi", type=int, default=160, help="Figure DPI")
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
            "mpke.s.ndot",
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


def main() -> int:
    args = parse_args()
    csv_path = args.csv.resolve()
    out_path = args.out.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    if df.empty:
        # Header-only CSVs would otherwise fail later with a bare
        # IndexError/ValueError on t_s[-1] / np.argmax(power_kw).
        raise ValueError(f"No data rows in startup CSV: {csv_path}")

    columns = list(df.columns)
    time_col = find_column(columns=columns, exact=("time",))
    power_col = find_column(
        columns=columns,
        exact=("powerBlock.fissionPower.P",),
        suffix=("powerblock.fissionpower.p",),
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

    missing = []
    if time_col is None:
        missing.append("time")
    if power_col is None:
        missing.append("powerBlock.fissionPower.P")
    if ext_reactivity_col is None:
        missing.append("pke.externalReactivityIn")
    if n_pop_col is None:
        missing.append("pke.n_population.n")
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    source_col = pick_source_column(columns)
    if source_col is None:
        raise KeyError(
            "Missing source column. Expected one of: "
            "constantNeutronSource.neutronEmsRateOut.nDot or pke.S.nDot"
        )
    fuel1_col = find_column(
        columns=columns,
        exact=("fuelChannel.fuelNode1.T",),
        suffix=("fuelchannel.fuelnode1.t",),
    )
    fuel2_col = find_column(
        columns=columns,
        exact=("fuelChannel.fuelNode2.T",),
        suffix=("fuelchannel.fuelnode2.t",),
    )
    grap_col = find_column(
        columns=columns,
        exact=("fuelChannel.grapNode.T",),
        suffix=("fuelchannel.grapnode.t",),
    )
    if fuel1_col is None or fuel2_col is None or grap_col is None:
        raise KeyError(
            "Missing required temperature columns matching "
            "fuelChannel.fuelNode1.T, fuelChannel.fuelNode2.T, fuelChannel.grapNode.T"
        )

    t_s = df[time_col].to_numpy(dtype=float)
    t_h = t_s / 3600.0
    power_kw = to_kw(df[power_col].to_numpy(dtype=float))
    demand_col = find_column(
        columns=columns,
        exact=("uhx.PowerDemand.R", "stepper1.step.R", "uhxDemand.step.R"),
        suffix=("uhx.powerdemand.r", "stepper1.step.r", "uhxdemand.step.r"),
    )
    if demand_col is not None:
        # Demand and power channels share the same unit ambiguity in historical CSVs.
        demand_total_kw = to_kw(df[demand_col].to_numpy(dtype=float))
    else:
        demand_total_kw = None
    demand_fission_equiv_kw = (
        args.fission_fraction * demand_total_kw if demand_total_kw is not None else None
    )
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
    source_nps = df[source_col].to_numpy(dtype=float)
    windows = source_windows(t_s, source_nps)
    fuel1_c = df[fuel1_col].to_numpy(dtype=float)
    fuel2_c = df[fuel2_col].to_numpy(dtype=float)
    core_c = df[grap_col].to_numpy(dtype=float)

    fb_col = find_column(
        columns=columns,
        exact=("reactivityFeedback.TotalTempFeedback",),
        suffix=("reactivityfeedback.totaltempfeedback",),
    )
    if fb_col is not None:
        fb_pcm = df[fb_col].to_numpy(dtype=float) * 1.0e5
    else:
        fb_pcm = np.full_like(ext_pcm, np.nan)

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

    # Linear scaling based on user's reference point at 100 kW.
    fission_nps = (power_kw / args.target_kw) * args.nu_prod_at_100kw

    tail_mask = t_s >= (t_s[-1] - args.tail_window_s)
    if not np.any(tail_mask):
        tail_mask = np.ones_like(t_s, dtype=bool)
    tail_avg_kw = float(np.mean(power_kw[tail_mask]))

    fig, axes = plt.subplots(4, 1, figsize=(14, 14), sharex=False)

    # Source-on spans are added before any legend is built so the
    # "Source on" patch reaches every axis legend.
    for i, (ts0, ts1) in enumerate(windows):
        label = "Source on" if i == 0 else None
        for axis in axes:
            axis.axvspan(ts0 / 3600.0, ts1 / 3600.0, color="#f2c14e", alpha=0.15, label=label)

    ax = axes[0]
    ax.plot(t_h, power_kw, color="#1f77b4", linewidth=1.4, label="Fission power")
    if demand_fission_equiv_kw is not None:
        ax.plot(
            t_h,
            demand_fission_equiv_kw,
            color="#9467bd",
            linewidth=1.2,
            linestyle="--",
            label=f"UHX demand (fission-eq, {args.fission_fraction:.2f}x)",
        )
    ax.axhline(args.target_kw, color="#d62728", linestyle="--", linewidth=1.2, label="Target")
    ax.axhline(tail_avg_kw, color="#2ca02c", linestyle=":", linewidth=1.2, label=f"Tail avg = {tail_avg_kw:.2f} kW")
    ax.set_ylabel("Power [kW]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ax = axes[1]
    ax.plot(t_h, fuel1_c, color="#d62728", linewidth=1.2, label="Fuel node 1")
    ax.plot(t_h, fuel2_c, color="#ff7f0e", linewidth=1.2, label="Fuel node 2")
    ax.plot(t_h, core_c, color="#2ca02c", linewidth=1.2, label="Graphite/core")
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
            linestyle="--",
            label="Total reactivity $=\\rho_{model}-\\rho_{0,dyn}$",
        )
    ax.set_ylabel("Reactivity [pcm]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ax = axes[3]
    ax.semilogy(t_h, source_nps + 1e-30, color="#8c564b", linewidth=1.2, label="Source rate [n/s]")
    ax.semilogy(t_h, fission_nps + 1e-30, color="#17becf", linewidth=1.2, label="Neutron density [n/s]")
    # ax.semilogy(t_h, n_pop * args.nu_prod_at_100kw, color="#7f7f7f", linewidth=1.0, label="n_population scaled")
    ax.set_ylabel("Neutrons [n/s, log]")
    ax.set_xlabel("Time [h]")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best")
    ax.set_ylim(bottom=1e-2)

    t_end_h = float(t_h.max())
    for axis in axes:
        axis.set_xlim(0.0, t_end_h)

    fig.suptitle("MSRR Startup to 100 kW")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)

    final_kw = float(power_kw[-1])
    max_kw = float(np.max(power_kw))
    max_t_h = float(t_h[int(np.argmax(power_kw))])
    print(f"Wrote: {out_path}")
    print(f"Final power: {final_kw:.4f} kW")
    print(f"Tail avg power ({args.tail_window_s:.0f} s): {tail_avg_kw:.4f} kW")
    print(f"Peak power: {max_kw:.4f} kW at {max_t_h:.3f} h")
    print(f"Source windows [s]: {[(round(a, 1), round(b, 1)) for a, b in windows]}")
    if rho0_col is not None:
        print(
            f"rho0 compensation variable: {rho0_col} "
            + ("(applied compensation, ungated)" if rho0_applied_col
               else f"(flow-gated with {ff_col if ff_col else 'no flow gate'})")
        )
    if not np.all(np.isnan(total_pcm)):
        print(f"Total reactivity variable (rho_total): {model_reactivity_label} - {rho0dyn_col} [pcm]")
        print(f"Final total reactivity: {float(total_pcm[-1]):.6g} pcm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
