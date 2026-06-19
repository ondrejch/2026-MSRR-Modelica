#!/usr/bin/env python3
"""Plot startup results with explicit neutron-source activity windows."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

try:
    from .paths import default_startup_csv_path, default_startup_run_dir
except ImportError:
    from paths import default_startup_csv_path, default_startup_run_dir


def _ensure_supported_python() -> None:
    """Re-exec under python3.12 when launched from an unsupported interpreter."""
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
except Exception as exc:  # pragma: no cover - import path error handling
    raise SystemExit(
        "Failed to import plotting dependencies (matplotlib/numpy/pandas). "
        "Use python3.12 or install compatible packages for this interpreter."
    ) from exc


SCENARIO = "startup"
DEFAULT_OUT_NAME = "plot_startup_with_source.png"


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
        description="Plot startup transients and explicitly mark source-on periods."
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
        help="Path to startup CSV (default derived from run definition)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG path (default: <run_dir>/plot_startup_with_source.png)",
    )
    parser.add_argument("--target_kw", type=float, default=100.0, help="Reference power target in kW")
    parser.add_argument("--dpi", type=int, default=160, help="Figure DPI")
    parser.add_argument(
        "--title",
        type=str,
        default="MSRR Startup with Source Windows",
        help="Figure title",
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


def extract_source_windows(time_s: np.ndarray, source_rate: np.ndarray) -> list[tuple[float, float]]:
    active = source_rate > 0
    windows: list[tuple[float, float]] = []
    start_idx: int | None = None

    for idx, is_active in enumerate(active):
        if is_active and start_idx is None:
            start_idx = idx
        elif (not is_active) and start_idx is not None:
            windows.append((float(time_s[start_idx]), float(time_s[idx])))
            start_idx = None

    if start_idx is not None:
        windows.append((float(time_s[start_idx]), float(time_s[-1])))

    return windows


def main() -> int:
    args = parse_args()
    csv_path = args.csv.resolve()
    out_path = args.out.resolve()

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    data = pd.read_csv(csv_path)

    columns = list(data.columns)
    time_col = find_column(columns=columns, exact=("time",))
    power_col = find_column(
        columns=columns,
        exact=("powerBlock.fissionPower.P",),
        suffix=("powerblock.fissionpower.p",),
    )
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
    if power_col is None:
        missing.append("powerBlock.fissionPower.P")
    if n_pop_col is None:
        missing.append("pke.n_population.n")
    if ext_reactivity_col is None:
        missing.append("pke.externalReactivityIn")
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    source_col = pick_source_column(columns)
    if source_col is None:
        raise KeyError(
            "Missing source column. Expected one of: "
            "constantNeutronSource.neutronEmsRateOut.nDot or pke.S.nDot"
        )

    time_s = data[time_col].to_numpy(dtype=float)
    time_h = time_s / 3600.0
    fission_power_kw = data[power_col].to_numpy(dtype=float) * 1000.0
    n_pop = data[n_pop_col].to_numpy(dtype=float)
    ext_reactivity_pcm = data[ext_reactivity_col].to_numpy(dtype=float) * 1.0e5
    source_rate = data[source_col].to_numpy(dtype=float)
    source_on_windows = extract_source_windows(time_s, source_rate)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    ax = axes[0]
    ax.plot(time_h, fission_power_kw, color="#1f77b4", linewidth=1.5, label="Fission power")
    ax.axhline(args.target_kw, color="#d62728", linestyle="--", linewidth=1.2, label=f"{args.target_kw:.1f} kW target")
    ax.set_ylabel("Power [kW]")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    ax = axes[1]
    ax.semilogy(time_h, np.clip(n_pop, 1e-30, None), color="#2ca02c", linewidth=1.4, label="Neutron population")
    ax.set_ylabel("n population [-]")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="best")

    ax = axes[2]
    ax.plot(time_h, ext_reactivity_pcm, color="#ff7f0e", linewidth=1.4, label="External reactivity")
    ax.set_ylabel("Reactivity [pcm]")
    ax.grid(True, alpha=0.3)

    ax_source = ax.twinx()
    ax_source.step(time_h, source_rate, where="post", color="#9467bd", linewidth=1.2, label="Source rate")
    ax_source.set_ylabel("Source [n/s]")

    for i, (start_s, end_s) in enumerate(source_on_windows):
        label = "Source on" if i == 0 else None
        for axis in axes:
            axis.axvspan(start_s / 3600.0, end_s / 3600.0, color="#f2c14e", alpha=0.18, label=label)

    handles_l, labels_l = ax.get_legend_handles_labels()
    handles_r, labels_r = ax_source.get_legend_handles_labels()
    axes[2].legend(handles_l + handles_r, labels_l + labels_r, loc="best")

    fig.suptitle(args.title)
    axes[-1].set_xlabel("Time [h]")
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)

    final_power_kw = float(fission_power_kw[-1])
    print(f"Wrote figure: {out_path}")
    print(f"Source windows: {[(round(a, 1), round(b, 1)) for a, b in source_on_windows]}")
    print(f"Final fission power: {final_power_kw:.4f} kW")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
