#!/usr/bin/env python3
"""Synthetic accuracy tests for the shared least-squares frequency estimator.

Covers ``freq._common.fit_sine_least_squares`` (TASK-20260826-02 P1), the
replacement for the biased mean-subtract + ``(2/N)`` projection estimator.
All cases use synthetic signals with known amplitude ``A``, phase ``phi``
and DC offset ``c0``, so every expected value is derived from the model

    y(t) = c0 + c1*(t - t_c) + a*sin(w*t) + b*cos(w*t),   A = hypot(a, b),

plus linear algebra -- never from what the implementation happens to
return. The old projector is reimplemented locally below straight from its
published definition for the migration-comparison cases.

Card acceptance items ("Synthetic tests") and their tests:

1. integer-cycle uniform window ......... test_integer_cycle_window_matches_legacy_projection
2. partial-cycle window (1.37 periods) .. test_partial_cycle_window_recovers_known_parameters
3. less-than-one-cycle window ........... test_sub_cycle_window_rejected_then_recovered_when_floor_disabled
4. nonuniform timestamps ................ test_nonuniform_timestamps_use_interval_length_weights
5. nonzero DC offset .................... test_nonzero_dc_offset_is_fitted_jointly_with_sine_terms
6. linear drift, trend on vs off ........ test_linear_drift_trend_on_vs_off
7. additive noise Monte-Carlo ........... test_additive_noise_monte_carlo_mean_error_within_bar
8. positive and negative phase shifts ... test_positive_and_negative_phase_shifts_follow_existing_convention

Contract pins named on the card and covered additionally:

- c0 always fitted jointly with a, b .... tests 2, 5 (mean-subtract never used)
- trend flag defaults OFF (c1 == 0.0) ... test_default_trend_flag_and_predict_centering_contract
- t_c = fit-window midpoint ............. same test, via predict() extrapolation identity
- arctan2(b, a) convention carried
  through phase_relative_to_perturbation_start_deg ... test 8
- explicit failures with configurable floors ...
        test_explicit_failure_floors_and_configurability
  (cond > 1e8, n_samples < 10, n_cycles < 0.25, plus the invalid-frequency
  guard)

Tolerance bars are documented next to their assertions. Tight bars derive
from exact-model least-squares theory: with zero-noise data generated from
the fitted family itself the residual can be driven to machine precision,
so recovered parameters agree with truth to ~cond(X)*eps. Measured errors
during derivation were ~1e-15..1e-13 (integer/partial/uniform windows,
weighted nonuniform grids, floor-disabled sub-cycle windows alike); each
tight bar leaves several orders of margin for BLAS/library variation.

TASK-20260827-01 P3 additions (objective-matched residual diagnostics,
committed fitter SHA 323c46b):

D1. weighted fit: primary pair == weighted objective, unweighted pair
    kept raw, both recomputed from the public API, materiality pin ...
        test_weighted_fit_reports_objective_matched_primary_pair
D2. unweighted fit: primary pair is the unweighted pair (object
    identity) ... test_unweighted_fit_primary_pair_is_the_unweighted_pair
D3. ill_conditioned carries all four diagnostics finite; earlier
    rejections leave all four NaN ...
        test_rejection_diagnostics_finite_or_nan_by_reason
D4. noiseless weighted fit: both pairs near-perfect ...
        test_noiseless_weighted_fit_both_pairs_near_perfect
MC1. noisy irregular grid, T = 200 fixed-seed, 5xSE sandwich bar ...
        test_monte_carlo_noisy_irregular_grid_bias_within_bar
MC2. clustered samples ...
        test_monte_carlo_clustered_samples_bias_within_bar
MC3. endpoint-heavy one-sided noise, heteroscedastic sandwich ...
        test_monte_carlo_endpoint_heavy_noise_bias_within_bar
    deterministic +5A spike contrast ...
        test_weighted_spike_shift_matches_wls_prediction_and_is_smaller
MC4. noisy partial cycle ...
        test_monte_carlo_noisy_partial_cycle_bias_within_bar

P3 Monte-Carlo bars: 5xSE of the T-trial mean bias, with SE derived in
these tests from the fixed-grid weighted-design sandwich
Cov(beta_hat) = (X'WX)^-1 X' W Sigma W X (X'WX)^-1
(= sigma^2 (X'WX)^-1 (X'W^2 X) (X'WX)^-1 for homoscedastic noise) plus
the delta method at the true coefficients -- never from the
implementation. Each grid's in-test 5xSE is pinned against the card's
pre-validated bar (rel=0.10) so a silent construction change cannot
move the statistics; every construction reproduces the card's value
within ~3%.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

try:
    from freq._common import (
        fit_sine_least_squares,
        phase_relative_to_perturbation_start_deg,
        trapezoid_sample_weights,
        wrap_phase_rad,
    )
except ImportError:  # direct-script execution outside an installed checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from freq._common import (
        fit_sine_least_squares,
        phase_relative_to_perturbation_start_deg,
        trapezoid_sample_weights,
        wrap_phase_rad,
    )


# Taxonomy tag (tests/README.md): synthetic least-squares math, no omc, no
# network.
pytestmark = [pytest.mark.unit]


# Stable rejection-reason tokens emitted by fit_sine_least_squares. Their
# string values are caller-facing contract (callers match on category), so
# they are quoted literally here rather than imported.
REJ_INVALID_FREQUENCY = "invalid_frequency"
REJ_INSUFFICIENT_SAMPLES = "insufficient_samples"
REJ_SUB_CYCLE_WINDOW = "sub_cycle_window"
REJ_ILL_CONDITIONED = "ill_conditioned"

# Shared synthetic-signal geometry.
OMEGA = 2.0 * np.pi * 0.7  # rad/s forcing frequency (angular), arbitrary
PERIOD = 2.0 * np.pi / OMEGA
AMP = 3.0
PHASE = 0.8  # rad
OFFSET = 17.5

# Tight bars: exact-model recovery is machine precision; these leave >=5
# orders of margin (see module docstring).
BAR_AMP_REL = 1e-9
BAR_PHASE_ABS_RAD = 1e-8
BAR_OFFSET_ABS = 1e-8

# Loose bar for the single place the card allows one: a sub-cycle window
# fitted with the n_cycles floor disabled. Window length alone no longer
# forces rejection, but the caller has stepped outside the validated regime
# (no guarantee against drift/noise/window pathology). Actual measured
# recovery on this construction is ~1e-14.
BAR_SUB_CYCLE_LOOSE_REL = 1e-3


def _signal(
    times,
    omega: float,
    amplitude: float,
    phase_rad: float,
    offset: float = 0.0,
    slope: float = 0.0,
) -> np.ndarray:
    """Build y(t) = offset + slope*(t - midpoint) + A*sin(w*t + phi)."""
    t = np.asarray(times, dtype=float)
    t_mid = 0.5 * (float(t.min()) + float(t.max()))
    return (
        offset
        + slope * (t - t_mid)
        + amplitude * np.sin(omega * t + phase_rad)
    )


def _legacy_projection_fit(times, power, omega: float):
    """Reimplement the OLD estimator from its definition (review item C2):
    mean-subtract, then ``(2/N)`` projections onto sin/cos, phase via
    arctan2(b, a). Returns (amplitude, phase_rad, dc_offset). Used only as
    an independent reference in the migration comparisons."""
    t = np.asarray(times, dtype=float)
    p = np.asarray(power, dtype=float)
    offset = float(p.mean())
    y = p - offset
    s = np.sin(omega * t)
    c = np.cos(omega * t)
    a = (2.0 / len(y)) * (y @ s)
    b = (2.0 / len(y)) * (y @ c)
    return float(np.hypot(a, b)), float(np.arctan2(b, a)), offset


def _wrap_rad(x: float) -> float:
    """Independent [-pi, pi) wrapper, defined from first principles here so
    phase expectations do not lean on the module under test."""
    return float((x + math.pi) % (2.0 * math.pi) - math.pi)


def _design_sin_cos(times, omega: float):
    """[1, sin(w t), cos(w t)] column stack, e.g. for deriving expected LS
    contamination terms independently of the implementation."""
    t = np.asarray(times, dtype=float)
    return np.column_stack(
        [np.ones_like(t), np.sin(omega * t), np.cos(omega * t)]
    )


def _integer_cycle_times(n_samples: int, n_cycles: float) -> np.ndarray:
    """Half-open uniform grid covering exactly n_cycles periods."""
    return np.arange(n_samples) * (n_cycles * PERIOD / n_samples)


# ---------------------------------------------------------------------------
# TASK-20260827-01 P3 (objective-matched diagnostics + noisy irregular
# grids) shared derivations. The P3 Monte-Carlo bars are 5xSE of the
# T-trial mean bias. SE is derived HERE from the fixed grid, never from
# the implementation: at a fixed true frequency the estimator is linear in
# the data, hence unbiased, and its (a, b) covariance on the fixed design
# is the weighted-design sandwich
#
#     Cov(beta_hat) = (X'WX)^-1 X' W Sigma W X (X'WX)^-1,
#
# which for homoscedastic noise (Sigma = sigma^2 I) is the card's
# sigma^2 (X'WX)^-1 (X'W^2 X) (X'WX)^-1. With B = X diag(sqrt(w)), that
# is (B'B)^-1 B' diag(w sigma_i^2) B (B'B)^-1: the row-scaled solve
# (B'B)^-1 B' y has row-scaled noise variance diag(w sigma_i^2).
# Amplitude/phase variances come from the delta method at the TRUE
# coefficients (a, b) = (A cos phi, A sin phi); the SE of the T-trial
# mean is sqrt(Var/T).
# ---------------------------------------------------------------------------

MC_TRIALS = 200
MC_SIGMA = 0.03 * AMP  # same noise scale as the uniform-grid MC above


def _interval_weights(times: np.ndarray) -> np.ndarray:
    """Interval weights the fitter applies on a nonuniform grid:
    trapezoid quadrature weights normalized to mean 1 (so sum == n).
    Re-derived from the public helper so expectations never read the
    fitter's internals."""
    w = trapezoid_sample_weights(np.diff(np.asarray(times, dtype=float)))
    return w / w.mean()


def _wls_beta_cov(
    design: np.ndarray, weights: np.ndarray, sigma2
) -> np.ndarray:
    """Cov(beta_hat) of the WLS solve on a FIXED design matrix:
    (X'WX)^-1 X' W Sigma W X (X'WX)^-1. ``sigma2`` is a scalar variance
    (homoscedastic) or a per-sample variance vector (heteroscedastic)."""
    s2 = np.broadcast_to(np.asarray(sigma2, dtype=float), design.shape[0])
    B = design * np.sqrt(weights)[:, None]  # row-scaled design
    G = B.T @ B                             # = X'WX
    M = B.T @ ((weights * s2)[:, None] * B)  # = X'W Sigma W X
    Gi = np.linalg.inv(G)
    return Gi @ M @ Gi


def _amp_phase_se_bars(
    design: np.ndarray, weights: np.ndarray, sigma2, trials: int
) -> tuple[float, float]:
    """(5xSE mean amplitude bias, 5xSE mean phase bias) over ``trials``
    independent fixed-grid runs. Delta method on the (a, b) sandwich
    covariance at the true coefficients:

        Var(A_hat)   = (a^2 c_aa + 2 a b c_ab + b^2 c_bb) / A^2
        Var(psi_hat) = (b^2 c_aa - 2 a b c_ab + a^2 c_bb) / A^4
    """
    cov = _wls_beta_cov(design, weights, sigma2)
    c_aa, c_ab, c_bb = cov[1, 1], cov[1, 2], cov[2, 2]
    a_true = AMP * math.cos(PHASE)
    b_true = AMP * math.sin(PHASE)
    var_amp = (
        a_true**2 * c_aa + 2.0 * a_true * b_true * c_ab + b_true**2 * c_bb
    ) / AMP**2
    var_phase = (
        b_true**2 * c_aa - 2.0 * a_true * b_true * c_ab + a_true**2 * c_bb
    ) / AMP**4
    return (
        5.0 * math.sqrt(var_amp / trials),
        5.0 * math.sqrt(var_phase / trials),
    )


def _collect_bias_errors(
    rng: np.random.Generator,
    times: np.ndarray,
    clean_power: np.ndarray,
    noise_fn,
    trials: int = MC_TRIALS,
) -> tuple[np.ndarray, np.ndarray]:
    """``trials`` fixed-seed runs on the fixed grid using the caller's
    stream (the rng CONTINUES past any grid draws, so the whole test is
    one deterministic draw sequence). Returns (amplitude errors, wrapped
    phase errors) of the implementation's fits vs truth (AMP, PHASE)."""
    amp_errs: list[float] = []
    phase_errs: list[float] = []
    for _ in range(trials):
        power = clean_power + noise_fn(rng)
        fit = fit_sine_least_squares(times, power, OMEGA)
        assert fit.ok, fit.rejection_reason
        assert fit.amplitude is not None
        assert fit.phase_rad is not None
        amp_errs.append(fit.amplitude - AMP)
        phase_errs.append(wrap_phase_rad(fit.phase_rad - PHASE))
    return np.asarray(amp_errs), np.asarray(phase_errs)


def test_integer_cycle_window_matches_legacy_projection() -> None:
    """Card item 1 plus the migration note: on an integer-cycle uniform
    window the new LS fit and the old projection estimator agree to machine
    precision, and both hit truth.

    On a half-open grid spanning whole periods the design columns [1, sin,
    cos] are mutually orthogonal (the sin/cos/sin*cos sums vanish and
    sum(sin^2) = N/2 because N does not divide 2K), so both estimators are
    exact up to floating point. Derived agreement ~1e-14; bars leave >=5
    orders of margin.
    """
    times = _integer_cycle_times(n_samples=384, n_cycles=3.0)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)

    legacy_amp, legacy_phase, legacy_offset = _legacy_projection_fit(
        times, power, OMEGA
    )
    fit = fit_sine_least_squares(times, power, OMEGA)
    assert fit.rejection_reason == ""
    amplitude = fit.amplitude
    phase_rad = fit.phase_rad
    assert amplitude is not None
    assert phase_rad is not None

    # New estimator recovers truth ...
    assert amplitude == pytest.approx(AMP, rel=BAR_AMP_REL)
    assert phase_rad == pytest.approx(_wrap_rad(PHASE), abs=BAR_PHASE_ABS_RAD)
    assert fit.c0 == pytest.approx(OFFSET, abs=BAR_OFFSET_ABS)
    # ... and matches the old estimator within the tight migration bar.
    assert amplitude == pytest.approx(legacy_amp, rel=BAR_AMP_REL)
    assert abs(phase_rad - legacy_phase) <= BAR_PHASE_ABS_RAD
    assert abs(float(fit.c0) - legacy_offset) <= BAR_OFFSET_ABS

    # Recorded diagnostics describe the window honestly.
    assert fit.n_samples == 384
    assert fit.n_cycles == pytest.approx(3.0 * (384 - 1) / 384, rel=1e-9)
    assert fit.trend_enabled is False
    assert fit.c1 == 0.0
    assert fit.r_squared >= 1.0 - 1e-12
    assert fit.residual_rms <= 1e-11
    assert math.isfinite(fit.condition_number)
    assert fit.condition_number > 0


def test_partial_cycle_window_recovers_known_parameters() -> None:
    """Card item 2: a ~1.37-period window recovers known A, phi, c0, with
    c0 estimated jointly with the sine coefficients.

    With the frequency held at its true value and noiseless data from the
    fitted family, least squares is exact regardless of window length;
    only conditioning matters (measured cond(X) ~ 1.54 here). This is the
    regime where the old projector loses ~2% of amplitude at the same
    geometry -- asserted below as the motivating contrast, computed from
    the old definition, not from any production artifact.
    """
    n_periods = 1.37
    times = np.linspace(0.0, n_periods * PERIOD, 320)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)

    fit = fit_sine_least_squares(times, power, OMEGA)
    amplitude = fit.amplitude
    phase_rad = fit.phase_rad
    assert fit.rejection_reason == ""
    assert amplitude is not None
    assert phase_rad is not None

    assert amplitude == pytest.approx(AMP, rel=BAR_AMP_REL)
    assert phase_rad == pytest.approx(_wrap_rad(PHASE), abs=BAR_PHASE_ABS_RAD)
    assert fit.c0 == pytest.approx(OFFSET, abs=BAR_OFFSET_ABS)

    assert fit.n_samples == 320
    assert fit.n_cycles == pytest.approx(n_periods, rel=1e-9)
    assert fit.fit_start == pytest.approx(0.0, abs=1e-12)
    assert fit.fit_end == pytest.approx(n_periods * PERIOD, rel=1e-12)
    assert fit.r_squared >= 1.0 - 1e-12

    # Motivating contrast for the C2 fix: the old projector is biased here.
    legacy_amp, _, _ = _legacy_projection_fit(times, power, OMEGA)
    assert abs(legacy_amp - AMP) / AMP >= 5e-3  # measured ~1.95%


def test_sub_cycle_window_rejected_then_recovered_when_floor_disabled() -> None:
    """Card item 3: a less-than-one-cycle window fails the n_cycles floor by
    default, and recovers within a documented loose bar when the floor is
    disabled for that test.

    Loose-bar rationale: with min_cycles=0 a 0.15-period window is still an
    ordinary well-posed LS problem for exact-model data (no drift, no
    noise), so recovery is actually near-exact (measured errors ~1e-14,
    cond ~31); 1e-3 stays deliberately generous because callers disabling
    the floor accept windows the acceptance criteria do not vouch for
    (correlated drift or edge effects would inflate it).
    """
    n_periods = 0.15
    times = np.linspace(0.0, n_periods * PERIOD, 60)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)

    rejected = fit_sine_least_squares(times, power, OMEGA)
    assert not rejected.ok
    assert rejected.rejection_reason == REJ_SUB_CYCLE_WINDOW
    assert rejected.amplitude is None
    assert rejected.n_samples == 60
    assert rejected.n_cycles == pytest.approx(n_periods, rel=1e-9)

    allowed = fit_sine_least_squares(times, power, OMEGA, min_cycles=0.0)
    amplitude = allowed.amplitude
    phase_rad = allowed.phase_rad
    assert allowed.rejection_reason == ""
    assert amplitude is not None
    assert phase_rad is not None
    assert amplitude == pytest.approx(AMP, rel=BAR_SUB_CYCLE_LOOSE_REL)
    assert phase_rad == pytest.approx(
        _wrap_rad(PHASE), abs=BAR_SUB_CYCLE_LOOSE_REL
    )
    assert allowed.c0 == pytest.approx(OFFSET, abs=BAR_SUB_CYCLE_LOOSE_REL)


def test_nonuniform_timestamps_use_interval_length_weights() -> None:
    """Card item 4: irregular timestamps switch the solve to interval-length
    weights and stay exact; near-uniform grids remain unweighted.

    Weighting rescales design rows but cannot move the achievable solution
    when data lie exactly in the model span, so weighted recovery stays at
    machine precision; bars sit one order wider than the uniform-window
    ones to absorb the changed conditioning of the weighted design
    (measured cond ~1.45).
    """
    rng = np.random.default_rng(20260826)
    base_step = 4.25 * PERIOD / 420
    stretch = np.exp(rng.normal(0.0, 0.45, size=420)).clip(0.5, 2.5)
    times_irreg = np.cumsum(base_step * stretch) - base_step * stretch[0]
    power_irreg = _signal(times_irreg, OMEGA, AMP, PHASE, OFFSET)

    fit = fit_sine_least_squares(times_irreg, power_irreg, OMEGA)
    amplitude = fit.amplitude
    phase_rad = fit.phase_rad
    assert fit.rejection_reason == ""
    assert amplitude is not None
    assert phase_rad is not None
    assert fit.uniformity_metric > 1e-6
    assert fit.weighted_intervals is True
    assert amplitude == pytest.approx(AMP, rel=1e-8)
    assert phase_rad == pytest.approx(_wrap_rad(PHASE), abs=1e-8)
    assert fit.c0 == pytest.approx(OFFSET, abs=1e-7)

    # Jitter far below the documented 1e-6 threshold must not trigger weights.
    tiny = 1.0 + 5e-10 * rng.choice((-1.0, 1.0), size=420)
    times_uniformish = np.cumsum(base_step * tiny) - base_step * tiny[0]
    power_uniformish = _signal(times_uniformish, OMEGA, AMP, PHASE, OFFSET)
    fit_near = fit_sine_least_squares(times_uniformish, power_uniformish, OMEGA)
    amplitude_near = fit_near.amplitude
    assert fit_near.rejection_reason == ""
    assert amplitude_near is not None
    assert fit_near.uniformity_metric < 1e-6
    assert fit_near.weighted_intervals is False
    assert amplitude_near == pytest.approx(AMP, rel=BAR_AMP_REL)


def test_trapezoid_sample_weights_halve_endpoints() -> None:
    """Endpoint weights are half the adjacent interval, not a full interval."""
    dt = np.array([0.5, 1.0, 2.0])
    weights = trapezoid_sample_weights(dt)
    assert weights[0] == pytest.approx(0.25)
    assert weights[-1] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(0.5 * (0.5 + 1.0))
    assert weights[2] == pytest.approx(0.5 * (1.0 + 2.0))
    two = trapezoid_sample_weights(np.array([4.0]))
    assert two == pytest.approx(np.array([2.0, 2.0]))


def test_small_step_cluster_triggers_interval_weights() -> None:
    """max(|dt/median-1|) catches clustered small steps, not only large gaps.

    A grid of nearly equal intervals plus one 1e-6 s step has
    max(dt)/median(dt)-1 = 0 under the old one-sided metric, so it would
    have been treated as uniform. The symmetric metric must switch weighting
    on.
    """
    n = 80
    dt = np.full(n - 1, PERIOD / 20.0)
    dt[0] = 1e-6
    times = np.concatenate(([0.0], np.cumsum(dt)))
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    fit = fit_sine_least_squares(times, power, OMEGA)
    median_dt = float(np.median(dt))
    expected = float(np.max(np.abs(dt / median_dt - 1.0)))
    assert fit.ok
    assert fit.uniformity_metric == pytest.approx(expected, rel=1e-12)
    assert expected > 1e-6
    assert fit.weighted_intervals is True
    # Exact-in-span data still recover; weighting rescales rows only.
    assert fit.amplitude == pytest.approx(AMP, rel=1e-8)
    assert fit.phase_rad == pytest.approx(_wrap_rad(PHASE), abs=1e-8)


def test_mixed_duplicate_timestamps_collapse_and_fit() -> None:
    """A single duplicated stamp among otherwise positive intervals no
    longer rejects the fit: the duplicate collapses to its last row (the
    solver's pre-/post-event pair at an event boundary) and the remaining
    strictly increasing grid fits normally.
    """
    times = np.array(
        [0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    )
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    fit = fit_sine_least_squares(times, power, OMEGA, min_cycles=0.0)
    assert fit.ok
    assert fit.n_samples == 11
    assert fit.fit_start == pytest.approx(0.0)
    assert fit.fit_end == pytest.approx(1.0)


def test_nonzero_dc_offset_is_fitted_jointly_with_sine_terms() -> None:
    """Card item 5: a DC offset dominating the modulation is estimated
    together with a and b -- never by mean-subtract-then-project.

    Geometry: 1.37 periods with c0/A ~ 106 (offset 85.0 vs amplitude 0.8)
    and a negative phase (-0.35 rad), so this case doubles as another
    negative-shift coverage point. The incomplete-window offset leakage
    corrupts the old projector by percent level (measured 4.3% here) while
    the joint fit stays at machine precision (~1e-14 measured).
    """
    dc = 85.0
    amp_small = 0.8
    phase_neg = -0.35
    times = np.linspace(0.0, 1.37 * PERIOD, 320)
    power = _signal(times, OMEGA, amp_small, phase_neg, dc)

    fit = fit_sine_least_squares(times, power, OMEGA)
    amplitude = fit.amplitude
    phase_rad = fit.phase_rad
    assert fit.rejection_reason == ""
    assert amplitude is not None
    assert phase_rad is not None
    assert amplitude == pytest.approx(amp_small, rel=BAR_AMP_REL)
    assert phase_rad == pytest.approx(_wrap_rad(phase_neg), abs=BAR_PHASE_ABS_RAD)
    assert fit.c0 == pytest.approx(dc, abs=BAR_OFFSET_ABS)

    legacy_amp, _, _ = _legacy_projection_fit(times, power, OMEGA)
    assert abs(legacy_amp - amp_small) / amp_small >= 5e-3  # measured 4.3%


def test_linear_drift_trend_on_vs_off() -> None:
    """Card item 6: without the trend term a linear drift contaminates the
    sine coefficients in exactly predictable ways; with it the true
    parameters come back including c1 == slope.

    Because the sinusoid-plus-offset signal vector lies exactly in the span
    of the three-column design, the trend-OFF solution must equal the exact
    signal coefficients plus the LS projection of the drift onto those same
    columns. That expectation is derived here from linear algebra on the
    constructed arrays, independent of the code under test:

        beta_off = (c0, A*cos(phi), A*sin(phi)) + lstsq([1, sin, cos], drift)

    The chosen drift (total swing 2.0 across the window, comparable to A)
    induces a ~0.87% amplitude misstatement, asserted material at >= 0.5%.
    """
    n_periods = 2.3
    n_samples = 350
    times = np.linspace(0.0, n_periods * PERIOD, n_samples)
    span = float(times[-1] - times[0])
    slope = 2.0 / span  # drift spans 2.0 over the window (~ AMP)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET, slope=slope)

    off = fit_sine_least_squares(times, power, OMEGA)  # trend default OFF
    amplitude_off = off.amplitude
    phase_off = off.phase_rad
    assert off.rejection_reason == ""
    assert amplitude_off is not None
    assert phase_off is not None
    assert off.trend_enabled is False
    assert off.c1 == 0.0

    design = _design_sin_cos(times, OMEGA)
    drift = slope * (times - times.mean())
    g_sin, g_cos = np.linalg.lstsq(design, drift, rcond=None)[0][1:]
    expected_a = AMP * math.cos(PHASE) + float(g_sin)
    expected_b = AMP * math.sin(PHASE) + float(g_cos)
    expected_amp = math.hypot(expected_a, expected_b)

    # Trend-off contamination behaves exactly as linear algebra predicts...
    assert float(off.a_sin) == pytest.approx(expected_a, rel=1e-9)
    assert float(off.b_cos) == pytest.approx(expected_b, rel=1e-9)
    assert amplitude_off == pytest.approx(expected_amp, rel=1e-9)
    # ... and the contamination is material for this geometry.
    assert abs(expected_amp - AMP) / AMP >= 5e-3  # measured ~0.87%

    on = fit_sine_least_squares(times, power, OMEGA, fit_trend=True)
    amplitude_on = on.amplitude
    phase_on = on.phase_rad
    assert on.rejection_reason == ""
    assert amplitude_on is not None
    assert phase_on is not None
    assert on.trend_enabled is True
    assert amplitude_on == pytest.approx(AMP, rel=BAR_AMP_REL)
    assert phase_on == pytest.approx(_wrap_rad(PHASE), abs=BAR_PHASE_ABS_RAD)
    assert on.c0 == pytest.approx(OFFSET, abs=BAR_OFFSET_ABS)
    assert on.c1 == pytest.approx(slope, rel=1e-9)
    assert on.residual_rms <= 1e-11


def test_additive_noise_monte_carlo_mean_error_within_bar() -> None:
    """Card item 7: Monte-Carlo mean parameter errors stay within a
    documented propagation bar.

    With the frequency fixed at its true value the estimator is linear in
    the data, hence unbiased at any SNR and window length; E[a_hat] =
    A*cos(phi) exactly. On the large-N integer-cycle grid used here the
    design columns are essentially orthogonal, giving Var(a_hat or b_hat)
    ~= 2*sigma^2/N and Var(c0_hat) ~= sigma^2/N, so over T independent
    trials SE(mean) ~= sqrt(2*sigma^2 / N / T) (amplitude; the phase
    estimate divides the same scale by A). The bars below are five
    propagated standard errors each; the seed is fixed, and cross-platform
    BLAS variation contributes orders of magnitude less than that margin.
    """
    n_samples = 2400
    trials = 200
    sigma = 0.03 * AMP
    rng = np.random.default_rng(20260826)
    times = _integer_cycle_times(n_samples=n_samples, n_cycles=6.0)
    clean_power = _signal(times, OMEGA, AMP, PHASE, OFFSET)

    amp_errs = []
    phase_errs = []
    c0_errs = []
    for _ in range(trials):
        power = clean_power + rng.normal(0.0, sigma, size=n_samples)
        fit = fit_sine_least_squares(times, power, OMEGA)
        amplitude = fit.amplitude
        phase_rad = fit.phase_rad
        assert fit.ok
        assert amplitude is not None
        assert phase_rad is not None
        amp_errs.append(amplitude - AMP)
        phase_errs.append(wrap_phase_rad(phase_rad - PHASE))
        c0_errs.append(fit.c0 - OFFSET)

    se_amp = math.sqrt(2.0 * sigma**2 / n_samples / trials)
    se_phase = se_amp / AMP
    se_c0 = math.sqrt(sigma**2 / n_samples / trials)
    assert abs(float(np.mean(amp_errs))) <= 5.0 * se_amp
    assert abs(float(np.mean(phase_errs))) <= 5.0 * se_phase
    assert abs(float(np.mean(c0_errs))) <= 5.0 * se_c0


def test_positive_and_negative_phase_shifts_follow_existing_convention() -> None:
    """Card item 8: known positive AND negative phase shifts round-trip
    through the existing arctan2(b, a) convention and through
    ``phase_relative_to_perturbation_start_deg``.

    Mirrors the collector wiring (freq/collectFreqNominalParallel.py:289,
    :348): timestamps are shifted to the fit-window start (tau = t -
    fit_start), the fit runs on tau, and the conversion receives the
    ABSOLUTE fit_start. If the perturbation starts at P and the fit window
    at F0 > P, a physical shift phi appears in the shifted time base as
    psi = phi + w*(F0 - P), and the conversion must remove exactly
    w*(F0 - P) to hand back phi. Cases cover +37 deg, -125 deg and
    +170 deg; after adding the elapsed angle each lands strictly inside
    (-pi, pi) while passing through wrap-around along the way.
    """
    perturbation_start = 3.0
    fit_start_abs = perturbation_start + 2.7
    elapsed_angle = OMEGA * (fit_start_abs - perturbation_start)
    phase_degs = (37.0, 170.0, -125.0)

    for phase_deg in phase_degs:
        phase_nominal = math.radians(phase_deg)
        psi = phase_nominal + elapsed_angle
        tau = np.linspace(0.0, 3.0 * PERIOD, 500)
        power = _signal(tau, OMEGA, AMP, psi, OFFSET)

        fit = fit_sine_least_squares(tau, power, OMEGA)
        phase_rad = fit.phase_rad
        assert fit.ok, phase_deg
        assert phase_rad is not None, phase_deg
        # Raw convention: the fit represents A*sin(w*tau + phase) with
        # phase = atan2(b, a), recovered from the shifted time base.
        assert phase_rad == pytest.approx(
            _wrap_rad(psi), abs=BAR_PHASE_ABS_RAD
        ), phase_deg

        converted_deg = phase_relative_to_perturbation_start_deg(
            phase_rad=phase_rad,
            freq_point=OMEGA,
            fit_start=fit_start_abs,
            perturbation_start=perturbation_start,
        )
        expected_deg = math.degrees(_wrap_rad(phase_nominal))
        assert converted_deg == pytest.approx(expected_deg, abs=1e-7), phase_deg


def test_default_trend_flag_and_predict_centering_contract() -> None:
    """Contract pins from the card text: the trend flag defaults to OFF
    (c1 reported as exactly 0.0), and predictions evaluate the fitted model
    with t_c at the fit-window midpoint.

    A real slope rides on the window; with trend ON the fit absorbs it, and
    extrapolated evaluations compare predict() against the closed form
    built from the public fields with t_c := 0.5*(fit_start + fit_end). Any
    other internal centering would break that identity by
    c1*(t_c' - t_c) -- far above tolerance at the probed extrapolation
    points, so the midpoint semantics of both c0 and prediction are pinned
    together (c0 must be the value AT the window midpoint).
    """
    n_periods = 1.37
    times = np.linspace(0.0, n_periods * PERIOD, 320)
    span = float(times[-1] - times[0])
    slope = 0.4
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET, slope=slope)

    # Expected trend-off amplitude, derived exactly as in the drift test:
    # exact signal coefficients plus the drift projection onto [1, sin, cos].
    g_sin, g_cos = np.linalg.lstsq(
        _design_sin_cos(times, OMEGA), slope * (times - times.mean()),
        rcond=None,
    )[0][1:]
    expected_a = AMP * math.cos(PHASE) + float(g_sin)
    expected_b = AMP * math.sin(PHASE) + float(g_cos)
    expected_amp_off = math.hypot(expected_a, expected_b)

    default_fit = fit_sine_least_squares(times, power, OMEGA)
    amplitude_dflt = default_fit.amplitude
    assert default_fit.ok
    assert amplitude_dflt is not None
    assert default_fit.trend_enabled is False
    assert default_fit.c1 == 0.0
    assert amplitude_dflt == pytest.approx(expected_amp_off, rel=1e-9)
    assert abs(expected_amp_off - AMP) / AMP >= 2e-3  # measured ~0.44%

    trended = fit_sine_least_squares(times, power, OMEGA, fit_trend=True)
    amplitude_tr = trended.amplitude
    assert trended.ok
    assert amplitude_tr is not None
    assert trended.trend_enabled is True
    assert trended.c1 == pytest.approx(slope, rel=1e-9)
    assert amplitude_tr == pytest.approx(AMP, rel=BAR_AMP_REL)

    t_c = 0.5 * (trended.fit_start + trended.fit_end)
    probes = np.array(
        [
            trended.fit_start - 1.3 * span,
            t_c,
            trended.fit_end + 2.7 * span,
        ]
    )
    expected = (
        trended.c0
        + trended.c1 * (probes - t_c)
        + trended.a_sin * np.sin(OMEGA * probes)
        + trended.b_cos * np.cos(OMEGA * probes)
    )
    assert np.allclose(trended.predict(probes), expected, rtol=1e-9, atol=1e-9)

    # A rejected fit refuses to predict, naming its rejection reason.
    rejected = fit_sine_least_squares(times[:9], power[:9], OMEGA)
    assert not rejected.ok
    with pytest.raises(ValueError, match="insufficient_samples"):
        rejected.predict(np.array([0.0]))


def test_explicit_failure_floors_and_configurability() -> None:
    """Explicit-failure contract: insufficient samples, sub-cycle windows,
    ill-conditioned designs and invalid frequencies fail loudly with stable
    rejection reasons, and every floor is configurable upward or downward.
    Also pins sample hygiene: input order is neutralized, non-finite rows
    drop out of the sample count, and identical timestamps collapse to one
    sample.
    """
    good_times = _integer_cycle_times(n_samples=24, n_cycles=2.0)
    good_power = _signal(good_times, OMEGA, AMP, PHASE, OFFSET)

    # --- n_samples < 10 fails by default -----------------------------------
    short = fit_sine_least_squares(good_times[:9], good_power[:9], OMEGA)
    assert not short.ok
    assert short.rejection_reason == REJ_INSUFFICIENT_SAMPLES
    assert short.amplitude is None
    assert short.n_samples == 9
    assert short.n_cycles == pytest.approx(2.0 * 8.0 / 24.0, rel=1e-9)

    # --- floors are configurable upward (min_samples, min_cycles) ----------
    strict_samples = fit_sine_least_squares(
        good_times, good_power, OMEGA, min_samples=25
    )
    assert not strict_samples.ok
    assert strict_samples.rejection_reason == REJ_INSUFFICIENT_SAMPLES

    wide_times = np.linspace(0.0, 2.5 * PERIOD, 200)  # inclusive endpoint
    wide_power = _signal(wide_times, OMEGA, AMP, PHASE, OFFSET)
    strict_cycles = fit_sine_least_squares(
        wide_times, wide_power, OMEGA, min_cycles=3.0
    )
    assert not strict_cycles.ok
    assert strict_cycles.rejection_reason == REJ_SUB_CYCLE_WINDOW
    # Same data passes at the documented defaults and records n_cycles.
    accepted = fit_sine_least_squares(wide_times, wide_power, OMEGA)
    assert accepted.ok
    assert accepted.n_cycles == pytest.approx(2.5, rel=1e-9)

    # --- ill-conditioned design fails explicitly (cond > 1e8) ---------------
    # A 1e-5 rad angular span makes cos(w t) nearly parallel to the offset
    # column: cond scales like 1/span^2 (measured ~4.6e11 here), while the
    # column deviations stay far above double precision (~5e-11 >> eps),
    # so this is genuine ill-conditioning, not representational noise.
    tiny_span_times = np.linspace(0.0, 1e-5, 12)
    tiny_span_power = _signal(tiny_span_times, 1.0, AMP, PHASE, OFFSET)
    ill = fit_sine_least_squares(
        tiny_span_times, tiny_span_power, 1.0, min_cycles=0.0
    )
    assert not ill.ok
    assert ill.rejection_reason == REJ_ILL_CONDITIONED
    assert ill.condition_number > 1e8
    assert math.isfinite(ill.condition_number)
    assert ill.amplitude is None
    assert ill.n_samples == 12
    assert ill.n_cycles == pytest.approx(1e-5 / (2.0 * math.pi), rel=1e-6)

    # --- the condition ceiling itself is configurable downward --------------
    # Integer-cycle grids give cond ~ sqrt(1.5); a 0.5 ceiling must reject
    # what the documented 1e8 ceiling accepts.
    capped = fit_sine_least_squares(
        good_times, good_power, OMEGA, max_condition_number=0.5
    )
    assert not capped.ok
    assert capped.rejection_reason == REJ_ILL_CONDITIONED
    assert capped.condition_number > 0.5
    assert fit_sine_least_squares(good_times, good_power, OMEGA).ok

    # --- invalid frequency fails explicitly ---------------------------------
    for bad_freq in (0.0, -OMEGA, float("nan")):
        rejected_freq = fit_sine_least_squares(good_times, good_power, bad_freq)
        assert not rejected_freq.ok
        assert rejected_freq.rejection_reason == REJ_INVALID_FREQUENCY
        assert rejected_freq.n_samples == len(good_times)

    # --- identical timestamps collapse, then fail on sample count -----------
    # Twelve rows at one stamp are one sample after duplicate collapse, so
    # the reason is insufficient_samples with n_samples == 1.
    repeated_times = np.full(12, 42.0)
    repeated_power = _signal(repeated_times, OMEGA, AMP, PHASE, OFFSET)
    collapsed = fit_sine_least_squares(
        repeated_times, repeated_power, OMEGA, min_cycles=0.0
    )
    assert not collapsed.ok
    assert collapsed.rejection_reason == REJ_INSUFFICIENT_SAMPLES
    assert collapsed.n_samples == 1

    # --- sample hygiene: shuffling is neutral, non-finite rows drop out -----
    hyg_times = _integer_cycle_times(n_samples=220, n_cycles=2.0)
    hyg_power = _signal(hyg_times, OMEGA, AMP, PHASE, OFFSET)
    clean = fit_sine_least_squares(hyg_times, hyg_power, OMEGA)
    clean_amplitude = clean.amplitude
    clean_phase = clean.phase_rad
    assert clean.ok
    assert clean_amplitude is not None
    assert clean_phase is not None

    holed_power = hyg_power.copy()
    holed_power[37] = np.nan
    holed_power[101] = np.inf
    holed = fit_sine_least_squares(hyg_times, holed_power, OMEGA)
    holed_amplitude = holed.amplitude
    assert holed.ok
    assert holed_amplitude is not None
    assert holed.n_samples == 218
    assert holed_amplitude == pytest.approx(clean_amplitude, rel=1e-12)

    shuffle_rng = np.random.default_rng(20260827)
    order = shuffle_rng.permutation(len(hyg_times))
    shuffled = fit_sine_least_squares(hyg_times[order], hyg_power[order], OMEGA)
    shuffled_amplitude = shuffled.amplitude
    shuffled_phase = shuffled.phase_rad
    assert shuffled.ok
    assert shuffled_amplitude is not None
    assert shuffled_phase is not None
    # Distinct timestamps sort back identically, so the solve repeats
    # bit-for-bit whatever order the caller hands over.
    assert shuffled_amplitude == pytest.approx(clean_amplitude, rel=1e-12)
    assert shuffled_phase == pytest.approx(clean_phase, abs=1e-12)


def test_duplicate_event_timestamps_collapse_keep_last() -> None:
    """Solvers emit pre-/post-event rows at the same timestamp on event
    boundaries (the frequency-response CSVs repeat t = 0 and every
    perturbation event time). The fitter collapses each duplicate run to
    its last row — the post-event state — rather than rejecting the fit:
    a grid with duplicated stamps fits the clean deduplicated grid, and
    the dropped pre-event values never reach the solve.
    """
    times = _integer_cycle_times(n_samples=120, n_cycles=2.0)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    clean = fit_sine_least_squares(times, power, OMEGA)
    assert clean.ok

    # Pre-event rows (offset only, no perturbation) inserted immediately
    # before the clean rows at indices 10, 60 and 110 — the pair layout a
    # solver writes at an event boundary, pre-event row first.
    dup_t = np.array([times[10], times[60], times[110]])
    dirty_times = np.concatenate(
        (
            times[:10],
            dup_t[0:1],
            times[10:60],
            dup_t[1:2],
            times[60:110],
            dup_t[2:3],
            times[110:],
        )
    )
    dirty_power = np.concatenate(
        (
            power[:10],
            [OFFSET],
            power[10:60],
            [OFFSET],
            power[60:110],
            [OFFSET],
            power[110:],
        )
    )
    assert dirty_times.size == 123

    fit = fit_sine_least_squares(dirty_times, dirty_power, OMEGA)
    assert fit.ok
    assert fit.n_samples == 120
    # Keep-last resolves every duplicate run to the clean post-event row,
    # so the solve repeats the clean-grid fit.
    assert fit.amplitude == pytest.approx(clean.amplitude, rel=1e-12)
    assert fit.phase_rad == pytest.approx(clean.phase_rad, abs=1e-12)
    assert fit.c0 == pytest.approx(clean.c0, abs=1e-12)


# ---------------------------------------------------------------------------
# TASK-20260827-01 P3: objective-matched residual diagnostics (D1-D4) and
# Monte-Carlo bias bars on noisy irregular grids (MC1-MC4).
# ---------------------------------------------------------------------------


def test_weighted_fit_reports_objective_matched_primary_pair() -> None:
    """P3 D1: with interval weights active, the primary pair
    (residual_rms, r_squared) is the WEIGHTED residual statistic -- the
    objective the solve actually minimized -- while
    (residual_rms_unweighted, r_squared_unweighted) keep the raw-sample
    values, and both pairs are populated. Every expectation below is
    recomputed from the public API only: fit.predict(t) for the fitted
    curve and trapezoid_sample_weights (normalized to mean 1) for the
    weights --

        weighted:    rms = sqrt(sum(w r^2) / N),  ybar_w = sum(w y) / N
                     r^2 = 1 - sum(w r^2) / sum(w (y - ybar_w)^2)
        unweighted:  rms = sqrt(sum(r^2) / N)
                     r^2 = 1 - sum(r^2) / sum((y - y_mean)^2)

    The materiality pin at the end is the P3 regression guard: on this
    noisy irregular grid the two RMS values differ by ~3.7e-3 relative
    (measured during derivation). If the primary pair had silently stayed
    unweighted, that difference would be ~1e-16.
    """
    rng = np.random.default_rng(20260831)
    n = 480
    base_step = 4.0 * PERIOD / n
    stretch = np.exp(rng.normal(0.0, 0.45, size=n)).clip(0.5, 2.5)
    times = np.cumsum(base_step * stretch) - base_step * stretch[0]
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET) + rng.normal(
        0.0, MC_SIGMA, size=n
    )

    fit = fit_sine_least_squares(times, power, OMEGA)
    assert fit.ok
    assert fit.weighted_intervals is True
    assert fit.n_samples == n

    residuals = power - fit.predict(times)
    w = _interval_weights(times)

    # Unweighted pair: raw-sample formulas.
    ss_res_u = float(np.sum(residuals**2))
    rms_u = math.sqrt(ss_res_u / n)
    r2_u = 1.0 - ss_res_u / float(np.sum((power - power.mean()) ** 2))
    assert fit.residual_rms_unweighted == pytest.approx(rms_u, rel=1e-9)
    assert fit.r_squared_unweighted == pytest.approx(r2_u, rel=1e-9)

    # Primary pair: weighted formulas (the solve's own objective).
    ss_res_w = float(np.sum(w * residuals**2))
    rms_w = math.sqrt(ss_res_w / n)
    y_bar_w = float(np.sum(w * power)) / n
    ss_tot_w = float(np.sum(w * (power - y_bar_w) ** 2))
    r2_w = 1.0 - ss_res_w / ss_tot_w
    assert fit.residual_rms == pytest.approx(rms_w, rel=1e-9)
    assert fit.r_squared == pytest.approx(r2_w, rel=1e-9)

    # All four diagnostics populated ...
    for value in (
        fit.residual_rms,
        fit.r_squared,
        fit.residual_rms_unweighted,
        fit.r_squared_unweighted,
    ):
        assert math.isfinite(value)
    # ... and materially different on this noisy irregular grid.
    assert abs(fit.residual_rms - fit.residual_rms_unweighted) / rms_u >= 5e-4


def test_unweighted_fit_primary_pair_is_the_unweighted_pair() -> None:
    """P3 D2: on a uniform grid (weights inactive) the primary pair IS
    the raw-sample pair. Asserted as Python object identity -- the
    strongest form of "the pairs coincide whenever weighted_intervals is
    false" -- plus a sanity check that the shared pair is a real residual
    statistic on noisy data (not a placeholder).
    """
    rng = np.random.default_rng(20260832)
    times = np.linspace(0.0, 3.0 * PERIOD, 400)
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET) + rng.normal(
        0.0, MC_SIGMA, size=400
    )
    fit = fit_sine_least_squares(times, power, OMEGA)
    assert fit.ok
    assert fit.weighted_intervals is False
    assert fit.residual_rms is fit.residual_rms_unweighted
    assert fit.r_squared is fit.r_squared_unweighted
    assert fit.residual_rms > 0.0
    assert 0.0 <= fit.r_squared < 1.0


def test_rejection_diagnostics_finite_or_nan_by_reason() -> None:
    """P3 D3: diagnostic population follows the rejection point. The
    ill_conditioned rejection fires AFTER the solve, so all four
    diagnostics (both pairs) carry finite values from the rejected
    coefficients; the earlier rejections (insufficient_samples,
    sub_cycle_window, invalid_frequency) fire before any solve and leave
    every diagnostic NaN.
    """

    def four(fit) -> tuple[float, float, float, float]:
        return (
            fit.residual_rms,
            fit.r_squared,
            fit.residual_rms_unweighted,
            fit.r_squared_unweighted,
        )

    # ill_conditioned: the solve ran, so all four diagnostics are finite.
    # (Same geometry as test_explicit_failure_floors_and_configurability:
    # a 1e-5 rad angular span makes cos(w t) nearly collinear with the
    # offset column, cond ~ 4.6e11.)
    tiny_times = np.linspace(0.0, 1e-5, 12)
    ill = fit_sine_least_squares(
        tiny_times,
        _signal(tiny_times, 1.0, AMP, PHASE, OFFSET),
        1.0,
        min_cycles=0.0,
    )
    assert not ill.ok
    assert ill.rejection_reason == REJ_ILL_CONDITIONED
    assert ill.condition_number > 1e8
    assert all(math.isfinite(value) for value in four(ill))
    assert ill.residual_rms > 0.0
    assert ill.residual_rms_unweighted > 0.0

    # Earlier rejections: all four diagnostics stay NaN.
    good_times = _integer_cycle_times(n_samples=24, n_cycles=2.0)
    good_power = _signal(good_times, OMEGA, AMP, PHASE, OFFSET)
    sub_times = np.linspace(0.0, 0.15 * PERIOD, 60)
    collapsed_times = np.full(12, 42.0)
    early = [
        fit_sine_least_squares(good_times[:9], good_power[:9], OMEGA),
        fit_sine_least_squares(
            sub_times, _signal(sub_times, OMEGA, AMP, PHASE, OFFSET), OMEGA
        ),
        fit_sine_least_squares(
            collapsed_times,
            _signal(collapsed_times, OMEGA, AMP, PHASE, OFFSET),
            OMEGA, min_cycles=0.0,
        ),
        fit_sine_least_squares(good_times, good_power, 0.0),
    ]
    expected_reasons = (
        REJ_INSUFFICIENT_SAMPLES,
        REJ_SUB_CYCLE_WINDOW,
        # 12 identical stamps collapse to one sample -> insufficient_samples
        REJ_INSUFFICIENT_SAMPLES,
        REJ_INVALID_FREQUENCY,
    )
    for fit, reason in zip(early, expected_reasons):
        assert not fit.ok
        assert fit.rejection_reason == reason
        assert all(math.isnan(value) for value in four(fit))


def test_noiseless_weighted_fit_both_pairs_near_perfect() -> None:
    """P3 D4 (regression guard): on a noiseless irregular grid the data
    lie exactly in the model span, so BOTH diagnostic pairs report
    near-zero RMS and near-unity R^2. A placeholder or stale value in
    either pair (e.g. the primary pair left NaN, or the unweighted pair
    not recomputed from the same residuals) would break this.
    """
    rng = np.random.default_rng(20260833)
    n = 360
    base_step = 4.0 * PERIOD / n
    stretch = np.exp(rng.normal(0.0, 0.45, size=n)).clip(0.5, 2.5)
    times = np.cumsum(base_step * stretch) - base_step * stretch[0]
    power = _signal(times, OMEGA, AMP, PHASE, OFFSET)

    fit = fit_sine_least_squares(times, power, OMEGA)
    assert fit.ok
    assert fit.weighted_intervals is True
    assert fit.residual_rms <= 1e-11
    assert fit.r_squared >= 1.0 - 1e-12
    assert fit.residual_rms_unweighted <= 1e-11
    assert fit.r_squared_unweighted >= 1.0 - 1e-12


def test_monte_carlo_noisy_irregular_grid_bias_within_bar() -> None:
    """P3 MC1 (card headline): noisy irregular grid -- lognormal stretch
    N(0, 0.45^2) clipped to [0.5, 2.5], N = 1200 with a nominal 6-period
    base step (the random stretch pushes the realized window to ~6.5
    periods), iid noise sigma = 0.03 A. Over T = 200 fixed-seed trials
    the mean amplitude/phase bias stays within 5xSE of the mean, with SE
    derived in-test from the fixed-grid weighted-design sandwich (see the
    module docstring) -- never from the implementation.

    On this construction the in-test 5xSE evaluates to (1.36e-3,
    4.5e-4), agreeing with the card's pre-validated bar (1.4e-3,
    4.6e-4) within ~3%; the agreement is pinned (rel=0.10) so a silent
    construction change cannot move the statistics. Measured mean bias
    at derivation: (1.6e-6, 7.6e-5) -- far inside the bar.
    """
    n = 1200
    seed = 20260827
    rng = np.random.default_rng(seed)
    base_step = 6.0 * PERIOD / n
    stretch = np.exp(rng.normal(0.0, 0.45, size=n)).clip(0.5, 2.5)
    times = np.cumsum(base_step * stretch) - base_step * stretch[0]

    # The grid really is irregular, so the fitter's weighted branch is
    # the branch under test (the bar is a WEIGHTED-design bar).
    clean = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    probe = fit_sine_least_squares(times, clean, OMEGA)
    assert probe.ok
    assert probe.weighted_intervals is True
    assert probe.uniformity_metric > 1e-6

    weights = _interval_weights(times)
    design = _design_sin_cos(times, OMEGA)
    bar_amp, bar_phase = _amp_phase_se_bars(
        design, weights, MC_SIGMA**2, MC_TRIALS
    )
    assert bar_amp == pytest.approx(1.4e-3, rel=0.10)
    assert bar_phase == pytest.approx(4.6e-4, rel=0.10)

    # One seeded stream: the grid draws above first, then one noise
    # vector per trial (the rng continues past the grid draws).
    amp_errs, phase_errs = _collect_bias_errors(
        rng, times, clean, lambda r: r.normal(0.0, MC_SIGMA, size=n)
    )
    assert abs(float(np.mean(amp_errs))) <= bar_amp
    assert abs(float(np.mean(phase_errs))) <= bar_phase


def test_monte_carlo_clustered_samples_bias_within_bar() -> None:
    """P3 MC2: clustered samples -- 900 points in the first 30% of a
    6-period window plus 300 in the last 70%. The step at the cluster
    boundary jumps ~7x the dense-step size (max(|dt/median - 1|) ~ 6),
    so interval weights are active. Same iid noise and 200-trial
    fixed-seed protocol as MC1; on this design the in-test 5xSE
    evaluates to (1.85e-3, 6.3e-4), vs the card's pre-validated bar
    (1.9e-3, 6.4e-4). Measured mean bias at derivation:
    (-6.7e-5, 2.4e-4).
    """
    window = 6.0 * PERIOD
    times = np.concatenate(
        [
            np.linspace(0.0, 0.3 * window, 900, endpoint=False),
            np.linspace(0.3 * window, window, 300),
        ]
    )
    assert len(times) == 1200
    clean = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    probe = fit_sine_least_squares(times, clean, OMEGA)
    assert probe.ok
    assert probe.weighted_intervals is True

    weights = _interval_weights(times)
    design = _design_sin_cos(times, OMEGA)
    bar_amp, bar_phase = _amp_phase_se_bars(
        design, weights, MC_SIGMA**2, MC_TRIALS
    )
    assert bar_amp == pytest.approx(1.9e-3, rel=0.10)
    assert bar_phase == pytest.approx(6.4e-4, rel=0.10)

    rng = np.random.default_rng(20260828)
    amp_errs, phase_errs = _collect_bias_errors(
        rng, times, clean, lambda r: r.normal(0.0, MC_SIGMA, size=len(times))
    )
    assert abs(float(np.mean(amp_errs))) <= bar_amp
    assert abs(float(np.mean(phase_errs))) <= bar_phase


def test_monte_carlo_endpoint_heavy_noise_bias_within_bar() -> None:
    """P3 MC3: endpoint-heavy one-sided noise -- a 400-sample leading
    cluster in the first 15% of a 6-period window (800 interior samples
    in the remaining 85%) carries 20x the interior noise sigma
    (heteroscedastic). The bar is a WEIGHTED-fit bar (the fitter uses
    interval weights on this grid), so the SE uses the heteroscedastic
    sandwich (X'WX)^-1 X' W Sigma W X (X'WX)^-1 (= X' diag(w^2 sigma_i^2)
    X in the meat, per _wls_beta_cov) on the fixed grid. In-test 5xSE:
    (7.07e-3, 2.23e-3) vs the card's pre-validated
    bar (7.2e-3, 2.3e-3). Measured mean bias at derivation:
    (-3.4e-5, 2.9e-4).

    Per the card, no unweighted/weighted mean-bias RATIO is asserted from
    the Monte-Carlo itself (both means sit far below the MC scatter);
    the deterministic spike contrast is the separate
    test_weighted_spike_shift_matches_wls_prediction_and_is_smaller.
    """
    window = 6.0 * PERIOD
    times = np.concatenate(
        [
            np.linspace(0.0, 0.15 * window, 400, endpoint=False),
            np.linspace(0.15 * window, window, 800),
        ]
    )
    n = len(times)
    assert n == 1200
    clean = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    probe = fit_sine_least_squares(times, clean, OMEGA)
    assert probe.ok
    assert probe.weighted_intervals is True

    sigma2 = np.full(n, MC_SIGMA**2)
    sigma2[:400] = (20.0 * MC_SIGMA) ** 2
    weights = _interval_weights(times)
    design = _design_sin_cos(times, OMEGA)
    bar_amp, bar_phase = _amp_phase_se_bars(design, weights, sigma2, MC_TRIALS)
    assert bar_amp == pytest.approx(7.2e-3, rel=0.10)
    assert bar_phase == pytest.approx(2.3e-3, rel=0.10)

    sqrt_sigma2 = np.sqrt(sigma2)
    rng = np.random.default_rng(20260829)
    amp_errs, phase_errs = _collect_bias_errors(
        rng, times, clean, lambda r: r.normal(0.0, 1.0, size=n) * sqrt_sigma2
    )
    assert abs(float(np.mean(amp_errs))) <= bar_amp
    assert abs(float(np.mean(phase_errs))) <= bar_phase


def test_weighted_spike_shift_matches_wls_prediction_and_is_smaller() -> None:
    """P3 MC3 deterministic contrast: a fixed +5A spike on the MC3
    leading cluster. The WLS solve is linear in the data, so the weighted
    (a, b) shift must equal (X'WX)^-1 X'W dy EXACTLY (lstsq agrees with
    the closed form to ~1e-16), and the interval weights must DAMP the
    spike's influence relative to treating every sample equally: the
    unweighted prediction (X'X)^-1 X' dy shifts (a, b) by ~2x more
    (measured: (a, b)-norm ratio 1.78, phase ratio 1.87; the strict
    amplitude ratio is 1.49 because the spike's shift direction is partly
    antiparallel to (a, b)). All shifts are derived in-test from linear
    algebra on the fixed grid. This documents that the interval weights
    are density weights, not outlier-robust weights.
    """
    window = 6.0 * PERIOD
    times = np.concatenate(
        [
            np.linspace(0.0, 0.15 * window, 400, endpoint=False),
            np.linspace(0.15 * window, window, 800),
        ]
    )
    clean = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    dy = np.zeros_like(times)
    dy[:400] = 5.0 * AMP

    fit = fit_sine_least_squares(times, clean + dy, OMEGA)
    assert fit.ok
    assert fit.weighted_intervals is True

    weights = _interval_weights(times)
    design = _design_sin_cos(times, OMEGA)
    a_true = AMP * math.cos(PHASE)
    b_true = AMP * math.sin(PHASE)
    shift_w = np.linalg.solve(
        design.T @ (weights[:, None] * design), design.T @ (weights * dy)
    )
    shift_u = np.linalg.solve(design.T @ design, design.T @ dy)

    # The implementation's weighted solve is exactly the closed-form WLS
    # shift for the perturbed data.
    assert float(fit.a_sin) == pytest.approx(a_true + shift_w[1], rel=1e-9)
    assert float(fit.b_cos) == pytest.approx(b_true + shift_w[2], rel=1e-9)

    amp_shift_w = math.hypot(a_true + shift_w[1], b_true + shift_w[2]) - AMP
    amp_shift_u = math.hypot(a_true + shift_u[1], b_true + shift_u[2]) - AMP
    norm_w = math.hypot(shift_w[1], shift_w[2])
    norm_u = math.hypot(shift_u[1], shift_u[2])
    phase_w = math.atan2(b_true + shift_w[2], a_true + shift_w[1]) - PHASE
    phase_u = math.atan2(b_true + shift_u[2], a_true + shift_u[1]) - PHASE

    # The spike is material and the weighted shift is strictly smaller.
    assert norm_w > 0.0
    assert abs(amp_shift_w) < abs(amp_shift_u)
    assert norm_w < norm_u
    assert abs(phase_w) < abs(phase_u)
    # ~2x documented: the unweighted prediction overstates the spike's
    # influence on (a, b) and on the phase (measured 1.78 and 1.87).
    assert norm_u / norm_w == pytest.approx(2.0, abs=0.5)
    assert abs(phase_u) / abs(phase_w) == pytest.approx(2.0, abs=0.5)
    # Strict amplitude shift: a quarter or more smaller under weights
    # (measured 1.49x).
    assert abs(amp_shift_u) / abs(amp_shift_w) == pytest.approx(1.5, abs=0.25)


def test_monte_carlo_noisy_partial_cycle_bias_within_bar() -> None:
    """P3 MC4: noisy partial cycle -- uniform grid, 1.37 periods,
    N = 320, iid noise sigma = 0.03 A. The non-integer window is exactly
    where the old projector is biased (see
    test_partial_cycle_window_recovers_known_parameters), but the shared
    LS estimator is unbiased at the fixed true frequency, so the
    200-trial fixed-seed mean bias stays within 5xSE. Uniform grid ->
    weights = 1, and the sandwich reduces to sigma^2 (X'X)^-1. In-test
    5xSE: (2.54e-3, 8.7e-4) vs the card's pre-validated bar
    (2.6e-3, 8.8e-4). Measured mean bias at derivation:
    (1.3e-4, 4.8e-5).
    """
    times = np.linspace(0.0, 1.37 * PERIOD, 320)
    clean = _signal(times, OMEGA, AMP, PHASE, OFFSET)
    probe = fit_sine_least_squares(times, clean, OMEGA)
    assert probe.ok
    assert probe.weighted_intervals is False  # uniform grid: unweighted bar

    design = _design_sin_cos(times, OMEGA)
    bar_amp, bar_phase = _amp_phase_se_bars(
        design, np.ones_like(times), MC_SIGMA**2, MC_TRIALS
    )
    assert bar_amp == pytest.approx(2.6e-3, rel=0.10)
    assert bar_phase == pytest.approx(8.8e-4, rel=0.10)

    rng = np.random.default_rng(20260830)
    amp_errs, phase_errs = _collect_bias_errors(
        rng, times, clean, lambda r: r.normal(0.0, MC_SIGMA, size=320)
    )
    assert abs(float(np.mean(amp_errs))) <= bar_amp
    assert abs(float(np.mean(phase_errs))) <= bar_phase
