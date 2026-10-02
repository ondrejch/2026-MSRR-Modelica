#!/usr/bin/env python3
"""Plot startup approach-to-criticality (Phases 1-4 only)."""

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
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Failed to import plotting dependencies (matplotlib/numpy/pandas). "
        "Use python3.12 or install compatible packages for this interpreter."
    ) from exc


PHASE_BOUNDS_S = (12.0 * 3600.0, 19.0 * 3600.0, 24.0 * 3600.0)
PHASE4_END_S_DEFAULT = 101700.0
MIN_FLOW_FOR_RHO0 = 1.0e-6
SOURCE_WINDOWS_FALLBACK_S = (
    (3600.0, 47400.0),
    (54000.0, 69000.0),
    (75600.0, 87000.0),
    (93600.0, 101400.0),
)
MIN_FISSION_POWER_PLOT_W = 1.0e-9
MIN_REACTIVITY_ABS_LOG_PCM = 1.0e-12
SCENARIO = "startup_to_1mw"
DEFAULT_OUT_NAME = "plot_startup_phase1to4.png"
DEFAULT_SIGNED_LOG_OUT_NAME = "plot_startup_phase1to4_signedlog.png"


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


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Plot startup approach-to-criticality behavior through Phase 4."
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
            "Path to startup CSV (default derived from the run "
            "definition, honoring --run_dir and the segmented/ probe)"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG file (default: <run_dir>/plot_startup_phase1to4.png)",
    )
    parser.add_argument(
        "--signed_log_out",
        type=Path,
        default=None,
        help=(
            "Output PNG for signed-log reactivity and log-power diagnostic "
            "(default: <run_dir>/plot_startup_phase1to4_signedlog.png)"
        ),
    )
    parser.add_argument(
        "--phase4_end_s",
        type=float,
        default=PHASE4_END_S_DEFAULT,
        help="Cutoff time [s] for Phase 1-4-only view",
    )
    parser.add_argument("--dpi", type=int, default=170, help="Figure DPI")
    parser.add_argument(
        "--fallback_source_strength",
        type=float,
        default=1.0e6,
        help="Source strength [n/s] used when source column is absent",
    )
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
    if args.signed_log_out is None:
        args.signed_log_out = args.run_dir / DEFAULT_SIGNED_LOG_OUT_NAME
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
    signed_log_out_path = args.signed_log_out.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    columns = list(df.columns)
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
    power_col = find_column(
        columns=columns,
        exact=("powerBlock.fissionPower.P",),
        suffix=("powerblock.fissionpower.p",),
    )

    missing_named: list[str] = []
    if time_col is None:
        missing_named.append("time")
    if n_pop_col is None:
        missing_named.append("pke.n_population.n")
    if ext_reactivity_col is None:
        missing_named.append("pke.externalReactivityIn")
    if power_col is None:
        missing_named.append("powerBlock.fissionPower.P")
    if missing_named:
        raise KeyError(f"Missing required columns: {missing_named}")

    source_col = pick_source_column(columns)

    t_s = df[time_col].to_numpy(dtype=float)
    phase_mask = t_s <= args.phase4_end_s
    if not np.any(phase_mask):
        raise ValueError(
            f"No data at or below phase4_end_s={args.phase4_end_s:g} s."
        )

    t_s = t_s[phase_mask]
    t_h = t_s / 3600.0
    n_pop = np.clip(df.loc[phase_mask, n_pop_col].to_numpy(dtype=float), 1e-30, None)
    ext_pcm = df.loc[phase_mask, ext_reactivity_col].to_numpy(dtype=float) * 1.0e5

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
        rho0_pcm = df.loc[phase_mask, rho0_col].to_numpy(dtype=float) * 1.0e5
    else:
        rho0_pcm = np.zeros_like(ext_pcm)
    if ff_col is not None and rho0_applied_col is None:
        fuel_flow_frac = df.loc[phase_mask, ff_col].to_numpy(dtype=float)
        rho0_active_pcm = np.where(fuel_flow_frac > MIN_FLOW_FOR_RHO0, rho0_pcm, 0.0)
    else:
        rho0_active_pcm = rho0_pcm
    ext_comp_pcm = ext_pcm + rho0_active_pcm
    fission_raw = df.loc[phase_mask, power_col].to_numpy(dtype=float)
    fission_w = fission_raw
    if source_col is not None:
        source_nps = df.loc[phase_mask, source_col].to_numpy(dtype=float)
        windows = source_windows(t_s, source_nps)
    else:
        source_nps, windows = source_from_fallback_schedule(
            t_s, source_strength=args.fallback_source_strength
        )

    fb_col = find_column(
        columns=columns,
        exact=("reactivityFeedback.TotalTempFeedback", "core1R.react.TotalTempFeedback"),
        suffix=("reactivityfeedback.totaltempfeedback", "react.totaltempfeedback"),
    )
    fb_pcm = (
        df.loc[phase_mask, fb_col].to_numpy(dtype=float) * 1.0e5
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
        model_pcm = df.loc[phase_mask, model_reactivity_col].to_numpy(dtype=float) * 1.0e5
    else:
        model_reactivity_label = "external+rho0+feedback (fallback)"
        model_pcm = ext_comp_pcm + np.nan_to_num(fb_pcm, nan=0.0)
    rho0dyn_col = find_column(
        columns=columns,
        exact=("pke.rho_0dyn",),
        suffix=("mpke.rho_0dyn", "pke.rho_0dyn"),
    )
    if rho0dyn_col is not None:
        rho0dyn_pcm = df.loc[phase_mask, rho0dyn_col].to_numpy(dtype=float) * 1.0e5
        total_pcm = model_pcm - rho0dyn_pcm
    else:
        total_pcm = np.full_like(model_pcm, np.nan)

    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)

    # Source-on spans are added before any legend is built so the
    # "Source on" patch reaches every axis legend.
    for i, (s0, s1) in enumerate(windows):
        label = "Source on" if i == 0 else None
        for axis in axes:
            axis.axvspan(s0 / 3600.0, s1 / 3600.0, color="#f2c14e", alpha=0.16, label=label)

    ax = axes[0]
    ax.semilogy(
        t_h,
        np.clip(fission_w, MIN_FISSION_POWER_PLOT_W, None),
        color="#1f77b4",
        linewidth=1.3,
        label="Fission power",
    )
    ax.set_ylim(bottom=MIN_FISSION_POWER_PLOT_W)
    ax.grid(True, which="both", alpha=0.3)
    ax.set_ylabel("Fission power [W, log]")
    ax.legend(loc="best")

    ax = axes[1]
    ax.plot(t_h, fission_w, color="#2ca02c", linewidth=1.3, label="Fission power")
    ax2 = ax.twinx()
    ax2.semilogy(t_h, source_nps + 1e-30, color="#8c564b", linewidth=1.1, label="Source rate")
    ax.grid(True, alpha=0.3)
    ax.set_ylabel("Power [W]")
    ax2.set_ylabel("Source [n/s, log]")
    left_handles, left_labels = ax.get_legend_handles_labels()
    right_handles, right_labels = ax2.get_legend_handles_labels()
    ax.legend(left_handles + right_handles, left_labels + right_labels, loc="best")

    ax = axes[2]
    ext_label = "External + $\\rho_0$ compensation" if rho0_col is not None else "External reactivity"
    ax.plot(t_h, ext_comp_pcm, color="#ff7f0e", linewidth=1.2, label=ext_label)
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
    ax.grid(True, alpha=0.3)
    ax.set_ylabel("Reactivity [pcm]")
    ax.legend(loc="best")

    for phase_idx, bound_s in enumerate(PHASE_BOUNDS_S, start=2):
        for axis in axes:
            axis.axvline(bound_s / 3600.0, color="#444444", linestyle="--", linewidth=0.9)
        axes[0].text(
            (bound_s / 3600.0) + 0.15,
            np.nanmax(np.clip(fission_w, 1e-30, None)) * 0.55,
            f"P{phase_idx}",
            fontsize=9,
        )

    t_end_h = float(t_h.max())
    for axis in axes:
        axis.set_xlim(0.0, t_end_h)

    axes[-1].set_xlabel("Time [h]")
    fig.suptitle("MSRR Startup Approach to Criticality (Phases 1-4)")
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)
    plt.close(fig)

    if not np.all(np.isnan(total_pcm)):
        log_power = np.log(np.clip(fission_w, MIN_FISSION_POWER_PLOT_W, None))
        signed_log_total = np.sign(total_pcm) * np.log(
            np.clip(np.abs(total_pcm), MIN_REACTIVITY_ABS_LOG_PCM, None)
        )

        fig2, axes2 = plt.subplots(2, 1, figsize=(14, 8.5), sharex=True)
        axp, axr = axes2

        # Source-on spans are added before any legend is built so the
        # "Source on" patch reaches both legends.
        for i, (s0, s1) in enumerate(windows):
            label = "Source on" if i == 0 else None
            for axis in axes2:
                axis.axvspan(
                    s0 / 3600.0, s1 / 3600.0, color="#f2c14e", alpha=0.16, label=label
                )

        axp.plot(t_h, log_power, color="#1f77b4", linewidth=1.4, label="$\\ln(P_{fission}[W])$")
        axp.grid(True, alpha=0.3)
        axp.set_ylabel("$\\ln(P_{fission}[W])$")
        axp.legend(loc="best")

        axr.plot(
            t_h,
            signed_log_total,
            color="#d62728",
            linewidth=1.4,
            label="$\\mathrm{sign}(\\rho_{total})\\,\\ln\\!\\left(|\\rho_{total}[pcm]|\\right)$",
        )
        axr.axhline(0.0, color="#555555", linewidth=0.8, linestyle=":")
        axr.grid(True, alpha=0.3)
        axr.set_ylabel("$\\mathrm{sign}(\\rho_{total})\\ln(|\\rho_{total}|)$")
        axr.set_xlabel("Time [h]")
        axr.legend(loc="best")

        for bound_s in PHASE_BOUNDS_S:
            for axis in axes2:
                axis.axvline(bound_s / 3600.0, color="#444444", linestyle="--", linewidth=0.9)

        for axis in axes2:
            axis.set_xlim(0.0, t_end_h)

        fig2.suptitle("MSRR Phases 1-4: Signed-Log Total Reactivity and Log Power")
        fig2.tight_layout(rect=[0, 0, 1, 0.965])
        signed_log_out_path.parent.mkdir(parents=True, exist_ok=True)
        fig2.savefig(signed_log_out_path, dpi=args.dpi)
        plt.close(fig2)
        print(f"Wrote: {signed_log_out_path}")
    else:
        print(
            "Signed-log figure skipped: total reactivity (rho_total) not "
            "available for this run (pke.rho_0dyn column missing or all NaN)."
        )

    print(f"Wrote: {out_path}")
    print(f"Phase-4 cutoff: {args.phase4_end_s:.1f} s ({args.phase4_end_s / 3600.0:.3f} h)")
    print(f"Source windows in plotted range [s]: {[(round(a, 1), round(b, 1)) for a, b in windows]}")
    print(f"Final plotted fission power: {float(fission_w[-1]):.6g} W")
    print(f"Fission power signal: {power_col} (W)")
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
