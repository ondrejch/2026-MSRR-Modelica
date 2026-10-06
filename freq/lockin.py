"""Lock-in estimator for slow-mode-dominated frequency-response points.

Rule id ``lockin_local_mean_v1`` (freq/README.md, "Lock-in regime").

At low power the lumped plants carry a slow power-temperature oscillation
(the resonance of :mod:`freq.fr_protocol`, ``omega_n ~ sqrt(P)``) that the
sine switch-on excites and that, at 1e-5 MW, grows instead of decaying.
For a forcing frequency well above that mode (``omega >= 10 omega_n``) the
forced response is separated from it by removing the local mean power:

1. ``m(t)``: centered moving average of the raw population over exactly one
   forcing period ``T = 2 pi / omega``, computed from the exact integral of
   the piecewise-linear interpolant of the samples (nonuniform grids), so a
   stationary forced component averages to (almost) zero while the slow
   mode passes through almost unchanged (gain ``1 - (pi omega_s/omega)^2/6``).
2. ``r(t) = n(t) / m(t) - 1``: the fluctuation relative to the local mean.
   The low-power kinetics are bilinear (the forced amplitude follows the
   current mean power), so this normalization removes the slow amplitude
   modulation that makes the two halves of a nominal-power fit disagree.
3. Least-squares fit of ``r`` with the collector's sine fitter
   (:func:`freq._common.fit_sine_least_squares`: ``a sin + b cos`` plus a
   constant and a linear trend) over an even number of whole forcing
   periods; the two halves are fitted separately for the convergence check.

Leakage correction.  The one-period average of the forced product
``m * f`` is not exactly zero when ``m`` varies: to first order it equals
``m' f' / omega^2`` (``f`` the fitted relative forced component), and the
discrete average of ``f`` leaves a small residual on coarse grids.  Both are
removed by a short fixed-point iteration ``m <- (MA(n) - m' f'/omega^2) /
(1 + MA_d(f))`` with ``m'`` from the current ``m`` and ``f`` from the
current fit.  On a stationary signal every correction is zero and the
estimate equals the ordinary sine fit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    from ._common import (
        SineFitResult,
        _prepare_fit_grid,
        fit_sine_least_squares,
        harmonic_ratio,
        wrap_phase_rad,
    )
    from .fr_protocol import FIT_ESTIMATOR_LOCKIN, LOCKIN_TREND_ORDER
except ImportError:  # script-style execution from freq/
    from _common import (  # type: ignore[no-redef]
        SineFitResult,
        _prepare_fit_grid,
        fit_sine_least_squares,
        harmonic_ratio,
        wrap_phase_rad,
    )
    from fr_protocol import FIT_ESTIMATOR_LOCKIN, LOCKIN_TREND_ORDER  # type: ignore[no-redef]

#: Estimator identifier (request cases, case manifests, aggregate rows).
LOCKIN_RULE_ID = FIT_ESTIMATOR_LOCKIN
#: Trend order of the relative-fluctuation fit.  The one-period average
#: passes a slow mode of relative amplitude ``A_s`` at ``omega_s`` with gain
#: ``1 - (pi omega_s / omega)^2 / 6``; the remainder is a slow ramp inside the
#: window whose projection biases the forced amplitude by up to
#: ``2 (pi^2/6) (omega_s/omega)^3 A_s``.  The linear term removes it (the
#: curvature remainder is ``(omega_s/omega)^4`` smaller).
DEFAULT_LOCKIN_TREND_ORDER = LOCKIN_TREND_ORDER
#: Fixed-point iterations of the leakage correction.
LOCKIN_ITERATIONS = 3

#: Rejection reasons (stable prefixes).
REJECT_LOCKIN_WINDOW = "lockin_window_too_short"
REJECT_LOCKIN_MEAN = "lockin_nonpositive_local_mean"
REJECT_LOCKIN_FIT = "lockin_fit_rejected"
REJECT_LOCKIN_INPUT = "lockin_invalid_input"

#: Relative slack when counting whole forcing periods in a window.
_PERIOD_SLACK = 1e-9


@dataclass
class LockinResult:
    """Outcome of :func:`lockin_fit` (check ``ok`` first)."""

    ok: bool = False
    rejection_reason: str = ""
    #: Sine fit of the relative fluctuation over the lock-in window (caller
    #: time base ``t - time_base``); amplitude/phase are relative (1 = 100 %).
    fit: SineFitResult | None = None
    amplitude: float = math.nan
    phase_rad: float = math.nan
    #: Time average of the local mean ``m`` over the lock-in window.
    mean_level: float = math.nan
    local_mean_min: float = math.nan
    local_mean_max: float = math.nan
    window_start: float = math.nan
    window_end: float = math.nan
    periods: int = 0
    n_samples: int = 0
    h2_h1_ratio: float = math.nan
    #: Largest relative leakage correction applied (diagnostic).
    leakage_correction_max: float = math.nan
    halves: dict[str, Any] = field(default_factory=dict)
    trend_order: int = DEFAULT_LOCKIN_TREND_ORDER

    def record(self) -> dict[str, Any]:
        """JSON-safe summary (manifests, check records)."""
        def num(value: float) -> float | None:
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None

        return {
            "rule_id": LOCKIN_RULE_ID,
            "ok": bool(self.ok),
            "rejection_reason": self.rejection_reason,
            "window_start_s": num(self.window_start),
            "window_end_s": num(self.window_end),
            "periods": int(self.periods),
            "n_samples": int(self.n_samples),
            "mean_level": num(self.mean_level),
            "local_mean_min": num(self.local_mean_min),
            "local_mean_max": num(self.local_mean_max),
            "leakage_correction_max": num(self.leakage_correction_max),
            "trend_order": int(self.trend_order),
        }


def _cumulative_integral(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Running trapezoid integral of the piecewise-linear interpolant."""
    out = np.empty_like(t, dtype=float)
    out[0] = 0.0
    np.cumsum(0.5 * (y[1:] + y[:-1]) * np.diff(t), out=out[1:])
    return out


def _integral_at(t: np.ndarray, y: np.ndarray, cum: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Exact integral of the linear interpolant from ``t[0]`` to each ``x``."""
    k = np.clip(np.searchsorted(t, x, side="right") - 1, 0, t.size - 2)
    dt = t[k + 1] - t[k]
    frac = (x - t[k]) / dt
    return cum[k] + dt * (frac * y[k] + 0.5 * frac * frac * (y[k + 1] - y[k]))


def period_moving_average(t: np.ndarray, y: np.ndarray, x: np.ndarray, period: float) -> np.ndarray:
    """Centered one-period moving average of the samples ``(t, y)`` at ``x``.

    ``(I(x + T/2) - I(x - T/2)) / T`` with ``I`` the exact integral of the
    piecewise-linear interpolant.  The caller guarantees
    ``t[0] <= x - T/2`` and ``x + T/2 <= t[-1]``.
    """
    cum = _cumulative_integral(t, y)
    half = 0.5 * float(period)
    return (_integral_at(t, y, cum, x + half) - _integral_at(t, y, cum, x - half)) / float(period)


def _halves(t_rel: np.ndarray, r: np.ndarray, freq: float, split_rel: float,
            *, trend_order: int, min_samples: int, min_cycles: float,
            max_condition_number: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "evaluated": False,
        "reason": "",
        "gain_rel_diff": math.nan,
        "gain_rel_diff_raw": math.nan,
        "phase_diff_deg": math.nan,
        "half_cycles": math.nan,
    }
    first = t_rel < split_rel
    second = ~first
    kwargs = dict(
        trend_order=trend_order,
        min_samples=min_samples,
        min_cycles=min_cycles,
        max_condition_number=max_condition_number,
    )
    fit_a = fit_sine_least_squares(t_rel[first], r[first], freq, **kwargs)
    fit_b = fit_sine_least_squares(t_rel[second], r[second], freq, **kwargs)
    out["half_cycles"] = float(fit_a.n_cycles) if math.isfinite(fit_a.n_cycles) else math.nan
    if not fit_a.ok or not fit_b.ok:
        out["reason"] = (
            "half-window lock-in fit rejected: "
            f"{fit_a.rejection_reason or 'ok'} / {fit_b.rejection_reason or 'ok'}"
        )
        return out
    amp_a, amp_b = float(fit_a.amplitude), float(fit_b.amplitude)
    if not (amp_a > 0 and math.isfinite(amp_a)):
        out["reason"] = "first-half amplitude is zero"
        return out
    # r is already normalized by the local mean: the raw and the
    # level-normalized amplitude ratios coincide.
    out["gain_rel_diff_raw"] = amp_b / amp_a - 1.0
    out["gain_rel_diff"] = out["gain_rel_diff_raw"]
    out["phase_diff_deg"] = math.degrees(
        wrap_phase_rad(float(fit_b.phase_rad) - float(fit_a.phase_rad))
    )
    out["evaluated"] = True
    return out


def lockin_fit(
    time_data,
    power_data,
    freq_point: float,
    *,
    fit_start: float,
    fit_end: float | None,
    perturbation_start: float,
    time_base: float,
    trend_order: int = DEFAULT_LOCKIN_TREND_ORDER,
    min_samples: int = 10,
    min_cycles: float = 0.25,
    max_condition_number: float = 1e8,
    iterations: int = LOCKIN_ITERATIONS,
) -> LockinResult:
    """Lock-in estimate of the forced response at ``freq_point``.

    ``time_data`` / ``power_data``: the case record (absolute seconds and raw
    population), including at least half a forcing period before
    ``fit_start``.  The lock-in window opens at ``max(fit_start, t_first +
    T/2, perturbation_start + T/2)`` (the one-period average never reaches
    before the forcing starts), closes at ``min(fit_end, t_last - T/2)``, and
    is trimmed to an even number of whole forcing periods from its start
    (two equal halves).  Phases are returned in the fitter convention in the
    time base ``t - time_base`` (the collector passes ``fit_start`` and
    converts with :func:`freq._common.phase_relative_to_perturbation_start_deg`).
    """
    result = LockinResult(trend_order=int(trend_order))
    w = float(freq_point)
    if not (math.isfinite(w) and w > 0):
        result.rejection_reason = f"{REJECT_LOCKIN_INPUT}: frequency {freq_point!r}"
        return result
    t, y = _prepare_fit_grid(time_data, power_data)
    if t.size < 2:
        result.rejection_reason = f"{REJECT_LOCKIN_INPUT}: fewer than two samples"
        return result
    period = 2.0 * math.pi / w
    half = 0.5 * period
    lo = max(float(fit_start), float(t[0]) + half, float(perturbation_start) + half)
    hi = float(t[-1]) - half
    if fit_end is not None:
        hi = min(hi, float(fit_end))
    n_periods = int(math.floor((hi - lo) / period + _PERIOD_SLACK)) if hi > lo else 0
    n_even = n_periods - (n_periods % 2)
    if n_even < 2:
        result.rejection_reason = (
            f"{REJECT_LOCKIN_WINDOW}: {max(hi - lo, 0.0) / period:.3g} forcing period(s) "
            "between the fit start and the record end after the half-period "
            "margins (need 2)"
        )
        return result
    w_end = lo + n_even * period
    slack = _PERIOD_SLACK * period
    # Samples the one-period averages touch (plus one neighbor each side).
    i0 = max(int(np.searchsorted(t, lo - half - slack, side="left")) - 1, 0)
    i1 = min(int(np.searchsorted(t, w_end + half + slack, side="right")) + 1, t.size)
    ts, ys = t[i0:i1], y[i0:i1]
    in_window = (ts >= lo - slack) & (ts <= w_end + slack)
    tw, nw = ts[in_window], ys[in_window]
    result.window_start, result.window_end = float(lo), float(w_end)
    result.periods = int(n_even)
    result.n_samples = int(tw.size)
    if tw.size < max(int(min_samples), 3):
        result.rejection_reason = (
            f"{REJECT_LOCKIN_WINDOW}: {tw.size} sample(s) in the lock-in window"
        )
        return result

    ma_n = period_moving_average(ts, ys, tw, period)
    if not np.all(np.isfinite(ma_n)) or np.any(ma_n <= 0):
        result.rejection_reason = f"{REJECT_LOCKIN_MEAN}: nonpositive one-period average"
        return result
    t_rel = tw - float(time_base)
    ts_rel = ts - float(time_base)
    fit_kwargs = dict(
        trend_order=int(trend_order),
        min_samples=min_samples,
        min_cycles=min_cycles,
        max_condition_number=max_condition_number,
    )

    local_mean = ma_n
    fit = None
    correction_max = 0.0
    for step in range(int(iterations) + 1):
        r = nw / local_mean - 1.0
        fit = fit_sine_least_squares(t_rel, r, w, **fit_kwargs)
        if not fit.ok:
            result.rejection_reason = f"{REJECT_LOCKIN_FIT}: {fit.rejection_reason}"
            result.fit = fit
            return result
        if step == int(iterations):
            break
        a, b = float(fit.a_sin), float(fit.b_cos)
        forced_all = a * np.sin(w * ts_rel) + b * np.cos(w * ts_rel)
        discrete_leak = period_moving_average(ts, forced_all, tw, period)
        forced_slope = w * (a * np.cos(w * t_rel) - b * np.sin(w * t_rel))
        mean_slope = np.gradient(local_mean, tw)
        corrected = (ma_n - mean_slope * forced_slope / (w * w)) / (1.0 + discrete_leak)
        if not np.all(np.isfinite(corrected)) or np.any(corrected <= 0):
            result.rejection_reason = f"{REJECT_LOCKIN_MEAN}: nonpositive corrected local mean"
            return result
        correction_max = float(np.max(np.abs(corrected / ma_n - 1.0)))
        local_mean = corrected

    assert fit is not None
    r = nw / local_mean - 1.0
    span = float(tw[-1] - tw[0])
    mean_level = (
        float(np.sum(0.5 * (local_mean[1:] + local_mean[:-1]) * np.diff(tw))) / span
        if span > 0 else float(np.mean(local_mean))
    )
    result.fit = fit
    result.amplitude = float(fit.amplitude)
    result.phase_rad = float(fit.phase_rad)
    result.mean_level = mean_level
    result.local_mean_min = float(np.min(local_mean))
    result.local_mean_max = float(np.max(local_mean))
    result.leakage_correction_max = correction_max
    result.h2_h1_ratio = harmonic_ratio(t_rel, r, w, trend_order=int(trend_order))
    result.halves = _halves(
        t_rel, r, w, (lo + 0.5 * n_even * period) - float(time_base),
        trend_order=int(trend_order), min_samples=int(min_samples),
        min_cycles=float(min_cycles), max_condition_number=float(max_condition_number),
    )
    if not (mean_level > 0 and math.isfinite(mean_level)):
        result.rejection_reason = f"{REJECT_LOCKIN_MEAN}: window mean {mean_level!r}"
        return result
    result.ok = True
    return result
