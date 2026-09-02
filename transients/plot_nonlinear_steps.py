#!/usr/bin/env python3
"""Plot nonlinear step dynamics results for manuscript figures (1R vs 9R).

With ``--package segmented`` (default: ``legacy``) the CSVs are read from
the nested ``<outputs_dir>/segmented/<core>/`` tree written by
``run_nonlinear_steps --package segmented``, and columns resolve through the
DECLARED candidates in ``helpers/segmented_runs.py``
(``CSV_OVERLAY_COLUMNS_BY_CORE``, ``CSV_POWER_COLUMN_CANDIDATES``,
``CSV_FEEDBACK_COLUMNS_BY_CORE`` -- the 9R feedback is summed from
RF1..RF9 by this consumer). The 9R temperature panel rides the fixed
overlay columns ``TZout[1..4]`` / ``TPot`` / ``ToutPlenum`` (Ambiguity H,
TASK-20260823-04). Segmented readers coerce numerics tolerantly
(``pd.to_numeric(..., errors="coerce")``): omc occasionally writes one
stray non-numeric token into a wide 9R result CSV (observed
``983013.774829801=``), which must gap one point instead of killing the
panel. Legacy column matching and legacy loaders are untouched.

Temperature units (TASK-20260825-06 P3): since TASK-20260825-04 the
SegmentedMSR library is kelvin, so segmented result CSVs carry kelvin
temperatures. The segmented readers return those raw CSV values; this
module converts every segmented temperature array K->degC on the render
path (the ``_read_step_signals`` and ``plot_uhx`` segmented branches) so
axes stay in degC exactly as before the library change. Legacy rendering
is untouched.
"""

from __future__ import annotations

import argparse
import colorsys
import os
from pathlib import Path
import shutil
import sys

try:
    from .paths import default_transients_run_dir
    from .run_nonlinear_steps import (
        DEFAULT_PACKAGE,
        LEGACY_PACKAGE,
        PACKAGE_CHOICES,
        SEGMENTED_PACKAGE,
    )
except ImportError:
    from paths import default_transients_run_dir
    from run_nonlinear_steps import (
        DEFAULT_PACKAGE,
        LEGACY_PACKAGE,
        PACKAGE_CHOICES,
        SEGMENTED_PACKAGE,
    )


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

# 1R vs 9R distinguishability (TASK-20260901-01 P3 / reviewer R2.8):
# the pairs must separate WITHOUT relying on color alone, so each base color
# splits into a clearly darker 1R variant and a clearly lighter 9R variant
# (explicit HLS lightness shift, replacing the old 0.04 hue nudge), the 1R
# dash pattern is lengthened, and the linewidths differ (1R 1.6 / 9R 1.1).
# Darker+thicker long-dash vs lighter+thinner solid stays distinguishable
# in a grayscale rendering.
CORE_STYLE = {
    "1r": {
        "label": "1R",
        "line": (0, (6.0, 2.5)),
        "lightness_scale": 0.70,
        "linewidth": 1.6,
    },
    "9r": {
        "label": "9R",
        "line": "-",
        "lightness_scale": 1.50,
        "linewidth": 1.1,
    },
}

UHX_CASE = "uhx_trip"


def _norm(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def _scale_lightness(hex_color: str, lightness_scale: float) -> str:
    """Scale HLS lightness (hue/saturation preserved), clipped to [0, 0.92].

    Scales < 1 darken (1R), scales > 1 lighten (9R); the 0.92 cap keeps the
    lightened variant visible against a white background.
    """
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(max(l * float(lightness_scale), 0.0), 0.92)
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    r2 = int(round(r2 * 255))
    g2 = int(round(g2 * 255))
    b2 = int(round(b2 * 255))
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def _core_color(base_color: str, core_model: str) -> str:
    return _scale_lightness(base_color, CORE_STYLE[core_model]["lightness_scale"])


def _core_linewidth(core_model: str) -> float:
    return float(CORE_STYLE[core_model]["linewidth"])


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


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path
    (its legacy-compat contract); the fallback keeps script-style execution
    from transients/ working by putting the repo root on sys.path.
    """
    try:
        from helpers import segmented_runs as seg
    except ImportError:  # script-style execution from transients/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import segmented_runs as seg
    return seg


def _resolve_outputs_for_core(
    outputs_dir: Path,
    core_model: str,
    *,
    package: str = LEGACY_PACKAGE,
) -> Path:
    if package == SEGMENTED_PACKAGE:
        # Segmented runs nest under an extra segmented/ component below the
        # outputs root (Ambiguity J); accepting <root>/<core> too lets the
        # caller pass either the run root or the segmented/ dir itself.
        seg_core_dir = outputs_dir / "segmented" / core_model
        if seg_core_dir.is_dir():
            return seg_core_dir
        alt_core_dir = outputs_dir / core_model
        if alt_core_dir.is_dir():
            return alt_core_dir
        raise FileNotFoundError(
            f"Missing segmented outputs for core '{core_model}': "
            f"expected {seg_core_dir}"
        )
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


# ---------------------------------------------------------------------------
# Segmented column resolution (--package segmented). Every lookup consumes
# the DECLARED candidates from helpers/segmented_runs.py
# (CSV_OVERLAY_COLUMNS_BY_CORE / CSV_POWER_COLUMN_CANDIDATES /
# CSV_FEEDBACK_COLUMNS_BY_CORE); no column names are re-derived here.
# ---------------------------------------------------------------------------

#: Decay-power component signal on both SegmentedMSR rigs
#: (SegmentedMSR.Nuclear.PowerBlock.decayPower). Declared HERE instead of in
#: helpers/segmented_runs.py so that S1-approved shared surface stays
#: byte-stable; it is a plot-side UHX-panel concern only.
SEGMENTED_UHX_DECAY_COLUMN_CANDIDATES: tuple[str, ...] = ("pb.decayPower",)


def _require_column(
    columns: list[str],
    candidates: tuple[str, ...] | list[str],
    purpose: str,
    csv_path: Path,
    suffixes: tuple[str, ...] = (),
) -> str:
    resolved = find_column(columns, exact=tuple(candidates), suffix=tuple(suffixes))
    if resolved is None:
        raise KeyError(
            f"{csv_path}: no segmented column for {purpose} "
            f"(tried {list(candidates)})"
        )
    return resolved


def _numeric_series(df: pd.DataFrame, column: str) -> np.ndarray:
    """1-D float array of one CSV column, malformed cells coerced to NaN.

    REV-2fdd01d-01 (TASK-20260823-04): omc occasionally emits a stray
    non-numeric token into a single cell of the wide 9R result files
    (observed ``983013.774829801=``), and plain ``to_numpy(dtype=float)``
    then aborts the whole panel. ``errors="coerce"`` converts ONLY the
    malformed cells to NaN; downstream numpy mean/sum propagate NaN and
    matplotlib renders a line GAP there -- nothing is interpolated,
    zero-filled, or otherwise fabricated. Well-formed columns parse to
    exactly the same floats as before.
    """
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)


def _numeric_group(df: pd.DataFrame, columns: list[str]) -> np.ndarray:
    """2-D float array of a column group, malformed cells coerced to NaN."""
    coerced = df.loc[:, list(columns)].apply(
        lambda series: pd.to_numeric(series, errors="coerce")
    )
    return coerced.to_numpy(dtype=float)


def _resolve_group(
    columns: list[str],
    names: list[str] | tuple[str, ...],
    purpose: str,
    csv_path: Path,
) -> list[str]:
    return [
        _require_column(columns, (name,), f"{purpose} '{name}'", csv_path)
        for name in names
    ]


def segmented_temperature_columns(
    seg, core_model: str
) -> tuple[list[str], list[str]]:
    """Fixed-overlay columns for the (fuel-panel, slow-node-panel).

    Ambiguity H decision (TASK-20260823-04, R2): the 9R panel rides the rig's
    FIXED overlay columns -- zone outlets ``TZout[1..4]`` averaged for the
    salt/fuel panel and the upper-plenum storage state ``TPot`` as the slow
    thermal-mass analogue of the legacy graphite node. ``ToutPlenum`` stays
    unused on the panels (it duplicates the mixed-outlet information already
    visible through ``TZout``/``TPot`` deltas) but remains available in the
    CSVs. Zone-cell means computed from component signals were rejected.
    """
    if seg.normalize_core_key(core_model) == "1r":
        return ["TF1", "TF2"], ["TG"]
    return (
        [f"TZout[{idx}]" for idx in range(1, 5)],
        ["TPot"],
    )


def segmented_loop_temperature_columns(
    seg, core_model: str
) -> tuple[list[str], list[str]]:
    """(core-inlet, outlet) fixed-overlay columns for the UHX-trip panel."""
    if seg.normalize_core_key(core_model) == "1r":
        return ["TinCore"], ["ToutCore"]
    return ["TinCore"], ["ToutPlenum"]


def read_segmented_step_signals(
    csv_path: Path, core_model: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Read ``(t_s, p_kw, fuel_avg, slow_node, feedback_pcm)`` from a CSV.

    Power rides ``CSV_POWER_COLUMN_CANDIDATES``; the fuel panel averages the
    Ambiguity-H fixed overlay group (1R: TF1/TF2; 9R: TZout[1..4]); the slow
    node is TG (1R) / TPot (9R); feedback sums the per-core declared channels
    (9R: rf1..rf9.TotalTempFeedback) and converts to pcm. Stray non-numeric
    cells become NaN gaps (REV-2fdd01d-01); they never abort the panel.

    ``fuel_avg``/``slow_node`` are RAW CSV values, i.e. kelvin since
    TASK-20260825-04; the K->degC render shift happens in
    :func:`_read_step_signals` (TASK-20260825-06 P3).
    """
    seg = _segmented_helpers()
    df = pd.read_csv(csv_path)
    columns = list(df.columns)

    time_col = find_column(columns, exact=("time",), suffix=("time",))
    if time_col is None:
        raise KeyError(f"{csv_path}: missing time column")
    power_col = _require_column(
        columns,
        seg.CSV_POWER_COLUMN_CANDIDATES,
        "reactor power",
        csv_path,
        suffixes=("reactorpower", "fissionpower.p"),
    )
    fb_cols = _resolve_group(
        columns,
        seg.feedback_columns_for(core_model),
        "temperature-feedback channel",
        csv_path,
    )
    fuel_cols, slow_cols = segmented_temperature_columns(seg, core_model)
    fuel_resolved = _resolve_group(columns, fuel_cols, "fuel-panel column", csv_path)
    slow_resolved = _resolve_group(columns, slow_cols, "slow-node column", csv_path)

    t_s = _numeric_series(df, time_col)
    p_kw = to_kw(_numeric_series(df, power_col))
    fuel_avg = _numeric_group(df, fuel_resolved).mean(axis=1)
    slow_node = _numeric_group(df, slow_resolved).mean(axis=1)
    feedback_pcm = _numeric_group(df, fb_cols).sum(axis=1) * 1.0e5
    return t_s, p_kw, fuel_avg, slow_node, feedback_pcm


def read_segmented_uhx_signals(csv_path: Path, core_model: str) -> dict[str, np.ndarray]:
    """UHX-trip panel signals keyed like :func:`load_uhx_signals`.

    Total power rides ``CSV_POWER_COLUMN_CANDIDATES`` (pb.reactorPower);
    fission/decay ride the declared component-signal candidates; inlet/
    outlet/graphite use the fixed overlay columns (TinCore; ToutCore or
    ToutPlenum; TG or TPot per Ambiguity H); feedback is summed to pcm.
    Stray non-numeric cells become NaN gaps (REV-2fdd01d-01).

    ``fuel_in``/``fuel_out``/``graphite`` are RAW CSV values, i.e. kelvin
    since TASK-20260825-04; the K->degC render shift happens in
    :func:`plot_uhx` (TASK-20260825-06 P3).
    """
    seg = _segmented_helpers()
    df = pd.read_csv(csv_path)
    columns = list(df.columns)

    time_col = find_column(columns, exact=("time",), suffix=("time",))
    if time_col is None:
        raise KeyError(f"{csv_path}: missing time column")
    total_col = _require_column(
        columns,
        seg.CSV_POWER_COLUMN_CANDIDATES,
        "total reactor power",
        csv_path,
        suffixes=("reactorpower",),
    )
    fission_col = _require_column(
        columns,
        ("pb.fissionPower.P",),
        "fission power",
        csv_path,
        suffixes=("fissionpower.p",),
    )
    decay_col = _require_column(
        columns,
        SEGMENTED_UHX_DECAY_COLUMN_CANDIDATES,
        "decay power",
        csv_path,
        suffixes=("decaypower",),
    )
    fb_cols = _resolve_group(
        columns,
        seg.feedback_columns_for(core_model),
        "temperature-feedback channel",
        csv_path,
    )
    fuel_in_cols, fuel_out_cols = segmented_loop_temperature_columns(
        seg, core_model
    )
    _, graphite_cols = segmented_temperature_columns(seg, core_model)
    fuel_in_resolved = _resolve_group(
        columns, fuel_in_cols, "core-inlet column", csv_path
    )
    fuel_out_resolved = _resolve_group(
        columns, fuel_out_cols, "core-outlet column", csv_path
    )
    graphite_resolved = _resolve_group(
        columns, graphite_cols, "slow-node column", csv_path
    )

    return {
        "time": _numeric_series(df, time_col),
        "total_power": _numeric_series(df, total_col),
        "fission_power": _numeric_series(df, fission_col),
        "decay_power": _numeric_series(df, decay_col),
        "fuel_in": _numeric_group(df, fuel_in_resolved).mean(axis=1),
        "fuel_out": _numeric_group(df, fuel_out_resolved).mean(axis=1),
        "graphite": _numeric_group(df, graphite_resolved).mean(axis=1),
        "feedback_pcm": _numeric_group(df, fb_cols).sum(axis=1) * 1.0e5,
    }


def _k_to_c(temperature_k: np.ndarray) -> np.ndarray:
    """Kelvin -> degrees Celsius render-side unit policy.

    Segmented result CSVs report kelvin since TASK-20260825-04 (commit
    2a43887); NaN gaps pass through unchanged. Legacy loaders never route
    through here, so non-segmented rendering stays byte-identical.
    """
    return temperature_k - 273.15


def _read_step_signals(
    readers: dict[str, object] | None,
    core_model: str,
    csv_path: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Legacy loader pair by default; per-core segmented reader when given.

    This is the single K->degC render boundary shared by ``plot_steps`` and
    ``plot_flow``: segmented result CSVs report kelvin (TASK-20260825-04)
    and the panels must keep their pre-conversion degC scale
    (TASK-20260825-06 P3). The readers themselves return raw CSV values
    (kelvin); feedback (pcm) and power are unit-invariant here.
    """
    if readers is None:
        t_s, p_kw = load_power_time(csv_path)
        _, fuel_avg, graphite, feedback_pcm = load_fuel_graphite_feedback(
            csv_path
        )
        return t_s, p_kw, fuel_avg, graphite, feedback_pcm
    t_s, p_kw, fuel_avg, graphite, feedback_pcm = readers[core_model](csv_path)  # type: ignore[index]
    return (
        t_s,
        p_kw,
        _k_to_c(fuel_avg),
        _k_to_c(graphite),
        feedback_pcm,
    )


def plot_steps(
    out_dir: Path,
    outputs_by_core: dict[str, Path],
    core_models: list[str],
    *,
    readers: dict[str, object] | None = None,
) -> Path:
    step_time_s = 2000.0
    fig, axes = plt.subplots(4, 1, figsize=(10.5, 9.8), sharex=True)

    for case, label, base_color in STEP_CASES:
        for core_model in core_models:
            csv_path = outputs_by_core[core_model] / f"{case}_res.csv"
            t_s, p_kw, fuel_avg, graphite, feedback_pcm = _read_step_signals(
                readers, core_model, csv_path
            )
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
            width = _core_linewidth(core_model)
            tag = CORE_STYLE[core_model]["label"]
            curve_label = f"{label} ({tag})"

            axes[0].plot(t_rel[mask], p_kw[mask] * 1000.0, linewidth=width, linestyle=style, label=curve_label, color=color)
            axes[1].plot(t_rel[mask], (fuel_avg - fuel_ref)[mask], linewidth=width, linestyle=style, color=color)
            axes[2].plot(t_rel[mask], (graphite - graphite_ref)[mask], linewidth=width, linestyle=style, color=color)
            axes[3].plot(t_rel[mask], feedback_pcm[mask], linewidth=width, linestyle=style, color=color)

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


def plot_flow(
    out_dir: Path,
    outputs_by_core: dict[str, Path],
    core_models: list[str],
    *,
    readers: dict[str, object] | None = None,
) -> Path:
    step_time_s = 4000.0
    fig, axes = plt.subplots(4, 1, figsize=(10.5, 9.8), sharex=True)

    for case, label, base_color in FLOW_CASES:
        for core_model in core_models:
            csv_path = outputs_by_core[core_model] / f"{case}_res.csv"
            t_s, p_kw, fuel_avg, graphite, feedback_pcm = _read_step_signals(
                readers, core_model, csv_path
            )
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
            width = _core_linewidth(core_model)
            tag = CORE_STYLE[core_model]["label"]
            curve_label = f"{label} ({tag})"

            axes[0].plot(t_rel[mask], p_kw[mask] * 1000.0, linewidth=width, linestyle=style, label=curve_label, color=color)
            axes[1].plot(t_rel[mask], (fuel_avg - fuel_ref)[mask], linewidth=width, linestyle=style, color=color)
            axes[2].plot(t_rel[mask], (graphite - graphite_ref)[mask], linewidth=width, linestyle=style, color=color)
            axes[3].plot(t_rel[mask], feedback_pcm[mask], linewidth=width, linestyle=style, color=color)

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


def plot_uhx(
    out_dir: Path,
    outputs_by_core: dict[str, Path],
    core_models: list[str],
    *,
    readers: dict[str, object] | None = None,
) -> Path:
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
        if readers is None:
            signals = load_uhx_signals(csv_path)
        else:
            # Segmented temperatures arrive in kelvin (TASK-20260825-04);
            # shift to degC here so the panel keeps its degC scale
            # (TASK-20260825-06 P3). Powers/feedback are unit-invariant.
            signals = dict(readers[core_model](csv_path))  # type: ignore[index]
            signals["fuel_in"] = _k_to_c(signals["fuel_in"])
            signals["fuel_out"] = _k_to_c(signals["fuel_out"])
            signals["graphite"] = _k_to_c(signals["graphite"])

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
        width = _core_linewidth(core_model)
        tag = CORE_STYLE[core_model]["label"]

        mask = (t_rel_h >= -0.5) & (t_rel_h <= 4.0)
        axes[0].plot(
            t_rel_h[mask],
            norm_total[mask],
            color=_core_color(component_colors["total"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Total ({tag})",
        )
        axes[0].plot(
            t_rel_h[mask],
            norm_fission[mask],
            color=_core_color(component_colors["fission"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Fission ({tag})",
        )
        axes[0].plot(
            t_rel_h[mask],
            norm_decay[mask],
            color=_core_color(component_colors["decay"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Decay ({tag})",
        )

        axes[1].plot(
            t_rel_h[mask],
            (signals["fuel_in"] - float(signals["fuel_in"][base_mask].mean()))[mask],
            color=_core_color(component_colors["fuel_in"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Fuel Inlet ({tag})",
        )
        axes[1].plot(
            t_rel_h[mask],
            (signals["fuel_out"] - float(signals["fuel_out"][base_mask].mean()))[mask],
            color=_core_color(component_colors["fuel_out"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Fuel Outlet ({tag})",
        )
        axes[1].plot(
            t_rel_h[mask],
            (signals["graphite"] - float(signals["graphite"][base_mask].mean()))[mask],
            color=_core_color(component_colors["graphite"], core_model),
            linewidth=width,
            linestyle=style,
            label=f"Graphite ({tag})",
        )

        axes[2].plot(
            t_rel_h[mask],
            signals["feedback_pcm"][mask],
            color=_core_color(component_colors["feedback"], core_model),
            linewidth=width,
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
            "(default: 00runs/transients-<core_models>; segmented runs "
            "resolve <outputs_dir>/segmented/<core>)"
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
    parser.add_argument(
        "--package",
        type=str,
        choices=PACKAGE_CHOICES,
        default=DEFAULT_PACKAGE,
        help=(
            "Column set and output layout to read (default: legacy). "
            "'legacy' keeps the historical matching unchanged; 'segmented' "
            "resolves columns through the helpers/segmented_runs.py "
            "candidates (power via pb.reactorPower, 9R feedback summed "
            "from rf1..rf9.TotalTempFeedback, 9R temperature panel from "
            "the fixed overlay columns TZout[1..4]/TPot/ToutPlenum) under "
            "<outputs_dir>/segmented/<core>."
        ),
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
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    outputs_by_core = {
        core: _resolve_outputs_for_core(outputs_dir, core, package=package)
        for core in core_models
    }

    step_readers: dict[str, object] | None = None
    uhx_readers: dict[str, object] | None = None
    if package == SEGMENTED_PACKAGE:
        def _make_step_reader(core_model: str):
            def _read(csv_path: Path):
                return read_segmented_step_signals(csv_path, core_model)
            return _read

        def _make_uhx_reader(core_model: str):
            def _read(csv_path: Path):
                return read_segmented_uhx_signals(csv_path, core_model)
            return _read

        step_readers = {core: _make_step_reader(core) for core in core_models}
        uhx_readers = {core: _make_uhx_reader(core) for core in core_models}

    step_fig = plot_steps(fig_dir, outputs_by_core, core_models, readers=step_readers)
    flow_fig = plot_flow(fig_dir, outputs_by_core, core_models, readers=step_readers)
    uhx_fig = plot_uhx(fig_dir, outputs_by_core, core_models, readers=uhx_readers)

    print(f"Wrote: {step_fig}")
    print(f"Wrote: {flow_fig}")
    print(f"Wrote: {uhx_fig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
