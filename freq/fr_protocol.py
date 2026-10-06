"""Frequency-response measurement protocol: gain prior, amplitude, settle discard.

This module owns the two protocol rules the legacy (lumped 1R/9R) sweep
runner applies before it launches any simulation:

1. **Perturbation amplitude for a target relative swing** (``target_swing``
   rule of ``--sin_mag_auto``).  For a sweep at power ``P`` (MW) the
   reactivity amplitude of the point at ``omega`` is chosen so that the
   predicted relative power swing ``dn/n0 = |G(omega, P)| * drho / P`` equals
   the target ``s`` (default 1 %)::

       drho = s * P / |G_prior(omega, P)|           (unit reactivity dk/k)
       sin_mag_pcm = clamp(1e5 * drho, [min_pcm, max_pcm])

   ``|G_prior|`` comes from a committed prior table of measured gains (see
   :func:`load_gain_prior`), in normalized power (1 = 1 MW) per unit
   reactivity -- the unit of the ``gain`` column of
   ``FreqResponseResults.csv``.

2. **Settling discard** (``prior`` settle rule, record ``settle_prior_v2``).
   Switching the sine on at the perturbation start excites the lightly
   damped power/temperature resonance (``omega_n``, decay rate
   ``sigma_res = zeta * omega_n``) with a free-mode amplitude about equal
   to the forced one, plus a slow mode the half-power width does not see
   (``sigma_floor``).  The discard is chosen PER FREQUENCY:

       T_full  = ceil(k / min(sigma_res, sigma_floor))    (k e-folds, k >= 3)
       T_floor = ceil(k / sigma_floor)

   - **full** regime (default): ``T_d = T_full`` -- the free mode is
     decayed by ``k`` e-folds before the window opens; the window keeps
     ``N`` forcing cycles after the discard.  No cap: at 1e-5 MW this is
     ~2e7 s of forcing.
   - **drift** regime: when ``T_full > T_floor`` (``sigma_res <
     sigma_floor``, i.e. P <~ 0.07 MW with the committed prior) AND the
     N-cycle window is at most a fraction ``phi`` (default 0.1) of the
     natural period ``T_n = 2 pi / omega_n``, i.e. ``omega >= kappa *
     omega_n`` with ``kappa = N / phi`` (120 for N = 12), only
     ``T_d = T_floor`` is discarded and the window is ``phi * T_n`` long.
     Over such a window the undecayed free mode is a slow drift of the
     mean power: the collector removes it with a linear trend term and
     references the gain to the window-mean power (the low-power kinetics
     are bilinear, so the forced amplitude follows the mean population).
     The derivation and the bias bound are in freq/README.md ("Drift
     regime").
   - Refinement (``freq.refine_sweep``): a point that fails the collector's
     convergence check is rerun with ``T_d' = max(2 T_d, T_full)`` and the
     same fit-window length, in a refinement round directory with its own
     immutable request (see :func:`refine_discard`).

   Every decision is recorded per case (regime, discard, fit start, trend
   order, gain reference, refinement round) in the sweep request and the
   case manifests; nothing is capped or truncated silently.

The prior table is loaded once and validated; every decision records the
prior path and SHA-256 so the sweep request and the per-case manifests name
the exact data that shaped them.

Interpolation of the prior gain (documented in ``freq/README.md``):

- ``log |G|`` is interpolated linearly in ``log omega`` within each table
  power and linearly in ``log P`` between the two bracketing table powers.
  Two interpolants are formed -- one along lines of constant ``omega``
  (plain bilinear) and one along lines of constant ``omega / sqrt(P)``
  (the measured resonance scaling ``omega_n ~ sqrt(P)``, so the peak moves
  continuously instead of splitting into two humps) -- and the larger gain
  is used.  Taking the larger gain never predicts a smaller gain than
  either interpolant, i.e. it errs toward a smaller amplitude (smaller
  swing).  At the table powers and frequencies both interpolants reproduce
  the table exactly.
- Frequencies outside the table range use the nearest edge gain (constant
  extrapolation); powers outside the table range scale the nearest edge
  curve by ``P / P_edge`` (zero-power scaling ``|G| ~ P``).  Both cases are
  flagged ``extrapolated``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Prior-table contract.
PRIOR_SCHEMA_VERSION = 1
PRIOR_KIND = "frequency-gain-prior"
DEFAULT_PRIOR_FILENAME = "fr_gain_prior.json"

#: One pcm in unit reactivity (dk/k).
PCM = 1.0e-5

#: Settle-discard rule names (``--settle_rule``).
SETTLE_RULE_PRIOR = "prior"
SETTLE_RULE_NONE = "none"
SETTLE_RULE_CHOICES = (SETTLE_RULE_PRIOR, SETTLE_RULE_NONE)
DEFAULT_SETTLE_RULE = SETTLE_RULE_PRIOR
#: Minimum e-folds accepted by the protocol (T_d >= 3/(zeta*omega_n)).
MIN_SETTLE_E_FOLDS = 3.0
DEFAULT_SETTLE_E_FOLDS = 3.0
#: Drift regime: the fit window may span at most this fraction of the
#: natural period 2*pi/omega_n (freq/README.md, "Drift regime").
DEFAULT_DRIFT_WINDOW_FRACTION = 0.1
MAX_DRIFT_WINDOW_FRACTION = 0.2
#: Trend order fitted for drift-regime points (linear; the README shows the
#: quadratic term is not needed at phi = 0.1).
DRIFT_TREND_ORDER = 1

#: Per-point settle regimes recorded in the request cases.
SETTLE_REGIME_FULL = "full"
SETTLE_REGIME_DRIFT = "drift"
SETTLE_REGIME_NONE = "none"
#: Lock-in regime (freq/README.md, "Lock-in regime"): the timing of the full
#: regime (T_full, same stop time, same refinement) with the lock-in
#: estimator of freq/lockin.py, for points with
#: ``omega >= LOCKIN_MIN_OMEGA_RATIO * omega_n`` at powers where the drift
#: regime is available (the slow free mode is not reliably damped within
#: T_full there; at 1e-5 MW it grows).
SETTLE_REGIME_LOCKIN = "lockin"
LOCKIN_MIN_OMEGA_RATIO = 10.0

#: Gain references recorded per case (collector estimator selection).
GAIN_REFERENCE_NOMINAL = "nominal_power"
GAIN_REFERENCE_WINDOW_MEAN = "window_mean_power"
#: Lock-in points: the forced fluctuation relative to the one-period local
#: mean power, times the manifest power.
GAIN_REFERENCE_LOCAL_MEAN = "local_mean_power"
#: Gain references whose operating-point bound is the relaxed
#: ``freq._common.CONVERGENCE_DRIFT_OPERATING_POINT_TOL`` (the relative
#: transfer function barely depends on the mean power there).
RELAXED_OPERATING_POINT_REFERENCES = (GAIN_REFERENCE_WINDOW_MEAN, GAIN_REFERENCE_LOCAL_MEAN)

#: Fit estimators recorded per case (``fit_estimator``): the fixed-frequency
#: sine fit of the raw signal (every regime but lock-in) and the lock-in
#: estimator (``lockin_local_mean_v1``).
FIT_ESTIMATOR_SINE_LS = "sine_ls_v1"
FIT_ESTIMATOR_LOCKIN = "lockin_local_mean_v1"
FIT_ESTIMATOR_CHOICES = (FIT_ESTIMATOR_SINE_LS, FIT_ESTIMATOR_LOCKIN)
#: Trend order of the lock-in relative-fluctuation fit (a linear term removes
#: the residual slow ramp the one-period average leaves).
LOCKIN_TREND_ORDER = 1

#: Refinement escalation factor on the discard (T_d' = max(f*T_d, T_full)).
REFINE_DISCARD_FACTOR = 2.0

#: Per-case result-row budget (output grid + 2 rows per forcing event).
#: The legacy vehicles no longer carry never-trimmed delay histories (the
#: pump trip times are closed-form since the physics review 2026-09-27),
#: so the 2^26-entry delay ring-buffer limit of the review-2026-09
#: campaign no longer binds; the budget bounds CSV size and collector
#: memory (1e8 rows ~ 4.4 GB reduced CSV).
DEFAULT_MAX_CASE_ROWS = 1.0e8
#: Output-grid cap for cases whose fit window is sampled by forcing
#: events (low-power forcing cadence active).
DEFAULT_MAX_OUTPUT_INTERVALS = 2_000_000

#: ``--sin_mag_auto`` amplitude rules (``--sin_mag_auto_rule``).
SIN_MAG_RULE_TARGET_SWING = "target_swing"
SIN_MAG_RULE_INVERSE_POWER = "inverse_power"
SIN_MAG_RULE_CHOICES = (SIN_MAG_RULE_TARGET_SWING, SIN_MAG_RULE_INVERSE_POWER)
DEFAULT_SIN_MAG_RULE = SIN_MAG_RULE_TARGET_SWING
DEFAULT_TARGET_SWING = 0.01
DEFAULT_TARGET_SWING_MIN_PCM = 0.01
DEFAULT_TARGET_SWING_MAX_PCM = 10.0

#: Solver tolerance rule (``--tolerance_rule``).  OpenModelica's DASSL uses
#: ``atol = tolerance * nominal`` per state; the neutron-population state
#: keeps nominal 1 (normalized power, 1 = 1 MW), so at the 1e-6 sweep
#: tolerance its absolute error scale is 1e-6 while the 1 % target swing is
#: ``0.01 * P`` -- at or below it for P <= 1e-4 MW.  ``power_scaled`` keeps
#: the absolute error scale at most ``TOLERANCE_SWING_RESOLUTION`` of the
#: target swing: ``tolerance = min(base, resolution * swing * P)``
#: (1e-6 for P >= 0.01 MW, unchanged; 1e-7 / 1e-8 / 1e-9 at 1e-3 / 1e-4 /
#: 1e-5 MW).
#:
#: ``power_scaled_v2`` (default since the 2026-09-28 H2 diagnostic; rule id
#: ``tolerance_power_scaled_v2``) keeps the error scale at
#: ``TOLERANCE_V2_SWING_RESOLUTION`` (1e-4) of the target swing, 100x below
#: v1: ``tolerance = max(TOLERANCE_V2_FLOOR, min(1e-8, 1e-4 * swing * P))``,
#: i.e. ``min(1e-8, 1e-6 * P)`` at the default 1 % target swing (1e-8 for
#: P >= 0.01 MW, 1e-9 at 1e-3 MW, 1e-10 at 1e-4 MW, 1e-11 at 1e-5 MW = the
#: floor).  At the v1 value 1e-6 the 0.1-0.8 MW sweeps carried solver noise
#: at the 0.5 % gain level and a gain bias of up to 3.7 % near the
#: resonance (freq/README.md, "Solver tolerance").
TOLERANCE_RULE_POWER_SCALED = "power_scaled"
TOLERANCE_RULE_POWER_SCALED_V2 = "power_scaled_v2"
TOLERANCE_RULE_FIXED = "fixed"
TOLERANCE_RULE_CHOICES = (
    TOLERANCE_RULE_POWER_SCALED_V2, TOLERANCE_RULE_POWER_SCALED, TOLERANCE_RULE_FIXED,
)
DEFAULT_TOLERANCE_RULE = TOLERANCE_RULE_POWER_SCALED_V2
#: Base tolerance of ``power_scaled`` (v1) and ``fixed`` (the historical
#: sweep value), and of ``power_scaled_v2``.
DEFAULT_BASE_TOLERANCE = 1.0e-6
TOLERANCE_V2_BASE = 1.0e-8
TOLERANCE_SWING_RESOLUTION = 0.01
TOLERANCE_V2_SWING_RESOLUTION = 1.0e-4
#: Floor of ``power_scaled_v2`` (the rule's own value at 1e-5 MW): short
#: single-point runs of both cores at 1e-5 and 1e-4 MW, 0.01 and 10 rad/s,
#: completed at 1e-8 ... 1e-12 without solver failures, 1e-12 at 0.6x-1.9x
#: the 1e-8 simulation time (freq/README.md, "Solver tolerance"); 1e-11 keeps
#: one decade of that verified margin.
TOLERANCE_V2_FLOOR = 1.0e-11
TOLERANCE_RECORD_RULE_ID = "tolerance_power_scaled_v1"
TOLERANCE_RECORD_RULE_ID_V2 = "tolerance_power_scaled_v2"


def default_base_tolerance(rule: str) -> float:
    """Base tolerance of ``rule`` when ``--solver_tolerance`` is not given."""
    return TOLERANCE_V2_BASE if rule == TOLERANCE_RULE_POWER_SCALED_V2 else DEFAULT_BASE_TOLERANCE

#: Realized-swing band (per point, collector + verify): the measured relative
#: swing A / y_mean must lie within [lo, hi] x the target swing.  The prior
#: gain comes from the review-2026-09 (pre-physics-review) model, so the
#: realized swing is verified per point instead of trusted; a point outside
#: the band is rerun by freq.refine_sweep with the amplitude rescaled by the
#: measured gain.  A clamped amplitude that cannot reach the target is
#: exempt on its clamp side.
SWING_BAND = (0.5, 1.5)
AMPLITUDE_RESCALE_RULE_ID = "measured_gain_rescale_v1"

#: Amplitude halving (freq.refine_sweep; freq/README.md, "Amplitude
#: halving").  A refinement round may halve a point's amplitude AND its
#: effective target swing, keeping the discard, when (a) a discard
#: escalation left it unconverged without reducing its convergence excess
#: (:func:`convergence_excess`) by at least ``HALVING_MIN_IMPROVEMENT``, or
#: a previous halving did reduce it by that factor but not enough; or (b)
#: the linearity branch (:func:`linearity_action`) asks for it after a
#: failed half-amplitude check on a settled pair.  A halving never takes the
#: amplitude below the target-swing floor (``target_swing_min_pcm``) or a
#: case tolerance below ``MIN_CASE_SOLVER_TOLERANCE``.  ``v2`` (this
#: steering) replaced ``v1`` (halve on every failed check, 2026-09-28b
#: campaign); the halving operation itself is unchanged.
AMPLITUDE_HALVING_RULE_ID = "amplitude_halving_v2"
HALVING_MIN_IMPROVEMENT = 2.0
#: Steering actions of :func:`refinement_action` / :func:`linearity_action`.
REFINE_ACTION_DISCARD = "discard"
REFINE_ACTION_HALVE = "halve"
#: Linearity branch only: escalate the discard of an unsettled check pair
#: (amplitude kept), restore the amplitude a halving removed (and escalate
#: the discard), or report the point (no remedy).
REFINE_ACTION_SETTLE = "settle"
REFINE_ACTION_RESTORE = "restore"
REFINE_ACTION_NONE = "none"
#: What produced a point's current case (:func:`refinement_action` input).
LAST_ACTION_BASE = "base"
LAST_ACTION_DISCARD = "discard"
LAST_ACTION_HALVE = "halve"
LAST_ACTION_AMPLITUDE = "amplitude"
#: ``halving_trigger`` values recorded on a halving plan point.
HALVING_TRIGGER_DISCARD_STALL = "discard_stall"
HALVING_TRIGGER_HALVING_PROGRESS = "halving_progress"
#: v1 (every failed check halved); kept to read 2026-09-28b rounds.
HALVING_TRIGGER_LINEARITY = "linearity_check"
#: ``linearity_trigger`` values (v2 linearity branch, :func:`linearity_action`).
LINEARITY_TRIGGER_SETTLE = "linearity_settle"
LINEARITY_TRIGGER_PROBE = "linearity_probe"
LINEARITY_TRIGGER_NONLINEAR = "linearity_nonlinear"
LINEARITY_TRIGGER_UNSETTLED_AMPLITUDE = "linearity_unsettled_amplitude"
LINEARITY_TRIGGER_SETTLING_LIMITED = "linearity_settling_limited"
LINEARITY_TRIGGER_DISCARD_PROGRESS = "linearity_discard_progress"
LINEARITY_TRIGGER_H2_UNRESOLVED = "linearity_h2_only"
LINEARITY_TRIGGER_SETTLE_STALLED = "linearity_settle_stalled"
#: A check pair is SETTLED when both runs' two-halves convergence excess
#: (:func:`convergence_excess`, collector tolerances) is at most this: the
#: halves agree within a quarter of the 0.5 % / 0.5 deg bar (0.125 % /
#: 0.125 deg).  The linearity verdict of an unsettled pair is not trusted
#: for steering: its discard escalates first.
LINEARITY_SETTLE_EXCESS = 0.25

#: Per-case solver tolerance (legacy sweeps; ``tolerance_swing_scaled_v2``):
#: a case whose effective target swing is below the policy target (an
#: amplitude halving, or the half-amplitude check run) runs with the
#: request tolerance scaled by ``effective / policy`` target, so the
#: population state's absolute error scale stays the same fraction of the
#: swing as in the base sweep.  Base cases keep the request tolerance
#: exactly.  A case tolerance below ``MIN_CASE_SOLVER_TOLERANCE`` is refused.
CASE_TOLERANCE_RULE_ID = "tolerance_swing_scaled_v2"
MIN_CASE_SOLVER_TOLERANCE = 1.0e-10
#: ``tolerance_swing_scaled_v3`` (sweeps of ``power_scaled_v2``): the same
#: scaling, clamped at ``MIN_CASE_SOLVER_TOLERANCE_V3`` (the sweep floor)
#: instead of refused; the clamp is recorded.  With the v2 sweep tolerance
#: the error scale is 1e-4 of the target swing at every power down to the
#: 1e-5 MW floor, so a clamped check or halved case stays far below the v1
#: resolution criterion; a halving is refused only when its check would
#: exceed that criterion (``TOLERANCE_SWING_RESOLUTION`` of its swing).
CASE_TOLERANCE_RULE_ID_V3 = "tolerance_swing_scaled_v3"
MIN_CASE_SOLVER_TOLERANCE_V3 = TOLERANCE_V2_FLOOR

#: Record-format identifiers written into the manifests.
SETTLE_RECORD_RULE_ID = "settle_prior_v2"
AMPLITUDE_RECORD_RULE_ID = "target_swing_v1"


class PriorError(ValueError):
    """The gain-prior table is missing, malformed, or cannot serve a request."""


def default_prior_path() -> Path:
    """Committed prior table under ``data/scenarios/freq/``.

    Resolved through ``helpers.scenario_config.scenarios_root`` so source
    checkouts and wheel installs (``msrr_data``) find the same file.
    """
    try:
        from helpers.scenario_config import scenarios_root
    except ImportError:  # script-style execution from freq/
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers.scenario_config import scenarios_root
    return Path(scenarios_root()) / "freq" / DEFAULT_PRIOR_FILENAME


def _finite_positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PriorError(f"{label}: not a number ({value!r})") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise PriorError(f"{label}: must be finite and > 0 (got {value!r})")
    return number


def _strictly_increasing(values: list[float], label: str) -> None:
    for left, right in zip(values, values[1:]):
        if not right > left:
            raise PriorError(f"{label}: values must be strictly increasing")


@dataclass(frozen=True)
class _CoreTable:
    core: str
    vehicle: str | None
    omega: tuple[float, ...]
    powers: tuple[float, ...]
    log_gain: tuple[tuple[float, ...], ...]
    resonance_powers: tuple[float, ...]
    resonance_omega_n: tuple[float, ...]
    resonance_sigma: tuple[float, ...]
    resonance_zeta: tuple[float, ...]
    resonance_method: tuple[str, ...]


def _validate_core(core: str, block: Mapping[str, Any]) -> _CoreTable:
    label = f"cores.{core}"
    omega = [_finite_positive(v, f"{label}.omega_rad_s") for v in block.get("omega_rad_s") or []]
    powers = [_finite_positive(v, f"{label}.powers_mw") for v in block.get("powers_mw") or []]
    if len(omega) < 2 or len(powers) < 1:
        raise PriorError(f"{label}: needs >= 2 frequencies and >= 1 power")
    _strictly_increasing(omega, f"{label}.omega_rad_s")
    _strictly_increasing(powers, f"{label}.powers_mw")
    gains = block.get("gain")
    if not isinstance(gains, list) or len(gains) != len(powers):
        raise PriorError(f"{label}.gain: one row per power required")
    log_gain: list[tuple[float, ...]] = []
    for idx, row in enumerate(gains):
        if not isinstance(row, list) or len(row) != len(omega):
            raise PriorError(f"{label}.gain[{idx}]: one value per frequency required")
        log_gain.append(
            tuple(math.log(_finite_positive(v, f"{label}.gain[{idx}]")) for v in row)
        )
    rows = block.get("resonance")
    if not isinstance(rows, list) or not rows:
        raise PriorError(f"{label}.resonance: at least one row required")
    parsed = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PriorError(f"{label}.resonance[{idx}] is not an object")
        parsed.append(
            (
                _finite_positive(row.get("power_mw"), f"{label}.resonance[{idx}].power_mw"),
                _finite_positive(row.get("omega_n_rad_s"), f"{label}.resonance[{idx}].omega_n_rad_s"),
                _finite_positive(row.get("sigma_s_inv"), f"{label}.resonance[{idx}].sigma_s_inv"),
                _finite_positive(row.get("zeta"), f"{label}.resonance[{idx}].zeta"),
                str(row.get("method") or ""),
            )
        )
    parsed.sort(key=lambda item: item[0])
    _strictly_increasing([item[0] for item in parsed], f"{label}.resonance.power_mw")
    return _CoreTable(
        core=core,
        vehicle=(str(block["vehicle"]) if block.get("vehicle") else None),
        omega=tuple(omega),
        powers=tuple(powers),
        log_gain=tuple(log_gain),
        resonance_powers=tuple(item[0] for item in parsed),
        resonance_omega_n=tuple(item[1] for item in parsed),
        resonance_sigma=tuple(item[2] for item in parsed),
        resonance_zeta=tuple(item[3] for item in parsed),
        resonance_method=tuple(item[4] for item in parsed),
    )


def _interp_sorted(x: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """Piecewise-linear interpolation with constant extrapolation."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + t * (ys[hi] - ys[lo])


def _loglog_interp_extrap(x: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """log-log interpolation; power-law continuation of the end segments."""
    lx = math.log(x)
    lxs = tuple(math.log(v) for v in xs)
    lys = tuple(math.log(v) for v in ys)
    if len(xs) == 1:
        return ys[0]
    if lx < lxs[0]:
        slope = (lys[1] - lys[0]) / (lxs[1] - lxs[0])
        return math.exp(lys[0] + slope * (lx - lxs[0]))
    if lx > lxs[-1]:
        slope = (lys[-1] - lys[-2]) / (lxs[-1] - lxs[-2])
        return math.exp(lys[-1] + slope * (lx - lxs[-1]))
    return math.exp(_interp_sorted(lx, lxs, lys))


@dataclass(frozen=True)
class GainEstimate:
    gain: float
    extrapolated: bool
    bracket_powers_mw: tuple[float, float]


@dataclass(frozen=True)
class ResonanceEstimate:
    omega_n_rad_s: float
    sigma_res_s_inv: float
    zeta: float
    sigma_floor_s_inv: float | None
    sigma_eff_s_inv: float
    source: str
    extrapolated: bool


@dataclass(frozen=True)
class GainPrior:
    """Validated prior table (see module docstring for the contract)."""

    path: str
    sha256: str
    prior_id: str
    sigma_floor_s_inv: float | None
    cores: Mapping[str, _CoreTable] = field(repr=False)

    def reference(self) -> dict[str, Any]:
        """Identity record written into the sweep request and manifests."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "id": self.prior_id,
            "schema_version": PRIOR_SCHEMA_VERSION,
        }

    def _table(self, core: str) -> _CoreTable:
        table = self.cores.get(str(core))
        if table is None:
            raise PriorError(
                f"gain prior {self.path} carries no table for core "
                f"{core!r} (available: {sorted(self.cores)})"
            )
        return table

    def vehicle(self, core: str) -> str | None:
        return self._table(core).vehicle

    def _log_gain_at_power_index(self, table: _CoreTable, k: int, omega: float) -> float:
        lw = tuple(math.log(v) for v in table.omega)
        return _interp_sorted(math.log(omega), lw, table.log_gain[k])

    def gain(self, core: str, omega: float, power: float) -> GainEstimate:
        """Prior gain |G(omega, P)| (normalized power per unit reactivity)."""
        table = self._table(core)
        omega = float(omega)
        power = float(power)
        if not (math.isfinite(omega) and omega > 0 and math.isfinite(power) and power > 0):
            raise PriorError(f"prior gain needs omega > 0 and power > 0 (got {omega!r}, {power!r})")
        extrapolated = not (table.omega[0] <= omega <= table.omega[-1])
        powers = table.powers
        if power <= powers[0] or power >= powers[-1] or len(powers) == 1:
            k = 0 if power <= powers[0] else len(powers) - 1
            edge = powers[k]
            if not math.isclose(power, edge, rel_tol=1e-12):
                extrapolated = True
            log_g = self._log_gain_at_power_index(table, k, omega) + math.log(power / edge)
            return GainEstimate(math.exp(log_g), extrapolated, (edge, edge))
        hi = next(idx for idx, value in enumerate(powers) if value >= power)
        lo = hi - 1
        if math.isclose(power, powers[hi], rel_tol=1e-12):
            return GainEstimate(
                math.exp(self._log_gain_at_power_index(table, hi, omega)),
                extrapolated,
                (powers[hi], powers[hi]),
            )
        p_lo, p_hi = powers[lo], powers[hi]
        t = (math.log(power) - math.log(p_lo)) / (math.log(p_hi) - math.log(p_lo))
        plain = (1.0 - t) * self._log_gain_at_power_index(table, lo, omega) + t * (
            self._log_gain_at_power_index(table, hi, omega)
        )
        sheared = (1.0 - t) * self._log_gain_at_power_index(
            table, lo, omega * math.sqrt(p_lo / power)
        ) + t * self._log_gain_at_power_index(table, hi, omega * math.sqrt(p_hi / power))
        return GainEstimate(math.exp(max(plain, sheared)), extrapolated, (p_lo, p_hi))

    def resonance(self, core: str, power: float) -> ResonanceEstimate:
        """Resonance parameters at ``power`` (log-log interpolation of rows).

        Outside the resonance-row power range the end segments continue as
        power laws; such estimates are flagged ``extrapolated``.
        """
        table = self._table(core)
        power = float(power)
        omega_n = _loglog_interp_extrap(power, table.resonance_powers, table.resonance_omega_n)
        sigma = _loglog_interp_extrap(power, table.resonance_powers, table.resonance_sigma)
        extrapolated = not (
            table.resonance_powers[0] * (1 - 1e-12)
            <= power
            <= table.resonance_powers[-1] * (1 + 1e-12)
        )
        exact = [
            method
            for p_row, method in zip(table.resonance_powers, table.resonance_method)
            if math.isclose(p_row, power, rel_tol=1e-9)
        ]
        source = exact[0] if exact else ("power_law_extrapolation" if extrapolated else "log_log_interpolation")
        floor = self.sigma_floor_s_inv
        sigma_eff = min(sigma, floor) if floor is not None else sigma
        return ResonanceEstimate(
            omega_n_rad_s=omega_n,
            sigma_res_s_inv=sigma,
            zeta=sigma / omega_n,
            sigma_floor_s_inv=floor,
            sigma_eff_s_inv=sigma_eff,
            source=source,
            extrapolated=extrapolated,
        )


def load_gain_prior(path: str | Path | None = None) -> GainPrior:
    """Load and validate a gain-prior JSON table (default: the committed one)."""
    prior_path = Path(path) if path else default_prior_path()
    try:
        raw = prior_path.read_bytes()
    except OSError as exc:
        raise PriorError(f"cannot read gain prior {prior_path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PriorError(f"gain prior {prior_path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PriorError(f"gain prior {prior_path} is not a JSON object")
    if payload.get("schema_version") != PRIOR_SCHEMA_VERSION:
        raise PriorError(
            f"gain prior {prior_path}: unsupported schema_version "
            f"{payload.get('schema_version')!r} (expected {PRIOR_SCHEMA_VERSION})"
        )
    if payload.get("kind") != PRIOR_KIND:
        raise PriorError(f"gain prior {prior_path}: kind must be {PRIOR_KIND!r}")
    cores_block = payload.get("cores")
    if not isinstance(cores_block, dict) or not cores_block:
        raise PriorError(f"gain prior {prior_path}: no core tables")
    settle = payload.get("settle_model") or {}
    floor_raw = settle.get("sigma_floor_s_inv") if isinstance(settle, dict) else None
    floor = _finite_positive(floor_raw, "settle_model.sigma_floor_s_inv") if floor_raw is not None else None
    cores = {str(core): _validate_core(str(core), block) for core, block in cores_block.items()}
    return GainPrior(
        path=str(prior_path.resolve()),
        sha256=hashlib.sha256(raw).hexdigest(),
        prior_id=str(payload.get("id") or ""),
        sigma_floor_s_inv=floor,
        cores=cores,
    )


# ---------------------------------------------------------------------------
# Settle discard (per frequency)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettleDecision:
    """Sweep-level settle constants (prior-derived; one per sweep).

    The per-frequency discard, fit window, and estimator follow from these
    constants through :func:`point_settle`.
    """

    rule: str
    e_folds: float | None = None
    omega_n_rad_s: float | None = None
    zeta: float | None = None
    sigma_res_s_inv: float | None = None
    sigma_floor_s_inv: float | None = None
    sigma_eff_s_inv: float | None = None
    full_discard_s: float = 0.0
    floor_discard_s: float = 0.0
    drift_window_fraction: float | None = None
    drift_window_s: float | None = None
    drift_enabled: bool = False
    drift_disabled: bool = False
    source: str | None = None
    extrapolated: bool = False
    prior: Mapping[str, Any] | None = None

    @property
    def lockin_enabled(self) -> bool:
        """Lock-in regime available (same powers as the drift regime)."""
        return bool(self.drift_enabled and self.omega_n_rad_s)

    def drift_ratio(self, min_cycles: float) -> float | None:
        """``kappa = N / phi``: drift regime for ``omega >= kappa * omega_n``."""
        if not self.drift_enabled or not self.drift_window_fraction:
            return None
        return float(min_cycles) / float(self.drift_window_fraction)

    def record(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "rule_id": SETTLE_RECORD_RULE_ID if self.rule == SETTLE_RULE_PRIOR else None,
            "e_folds": self.e_folds,
            "omega_n_rad_s": self.omega_n_rad_s,
            "zeta": self.zeta,
            "sigma_res_s_inv": self.sigma_res_s_inv,
            "sigma_floor_s_inv": self.sigma_floor_s_inv,
            "sigma_eff_s_inv": self.sigma_eff_s_inv,
            "full_discard_s": float(self.full_discard_s),
            "floor_discard_s": float(self.floor_discard_s),
            "drift_window_fraction": self.drift_window_fraction,
            "drift_window_s": self.drift_window_s,
            "drift_enabled": bool(self.drift_enabled),
            # --settle_force_full (drift-regime validation): every point in
            # the full regime; recorded so such a sweep is never mistaken for
            # a protocol-default one (it is part of the parent identity).
            "drift_regime_disabled": bool(self.drift_disabled),
            "drift_trend_order": DRIFT_TREND_ORDER if self.drift_enabled else None,
            # Lock-in regime (freq/README.md, "Lock-in regime"): enabled with
            # the drift regime; omega >= ratio * omega_n outside the drift
            # window.
            "lockin_enabled": bool(self.lockin_enabled),
            "lockin_min_omega_ratio": LOCKIN_MIN_OMEGA_RATIO if self.lockin_enabled else None,
            "lockin_estimator": FIT_ESTIMATOR_LOCKIN if self.lockin_enabled else None,
            "lockin_trend_order": LOCKIN_TREND_ORDER if self.lockin_enabled else None,
            "refine_discard_factor": REFINE_DISCARD_FACTOR,
            "resonance_source": self.source,
            "resonance_extrapolated": bool(self.extrapolated),
            "prior": dict(self.prior) if self.prior else None,
        }


def no_settle() -> SettleDecision:
    """Legacy behavior: the fit window opens at the perturbation start."""
    return SettleDecision(rule=SETTLE_RULE_NONE)


def settle_decision(
    prior: GainPrior,
    core: str,
    power: float,
    *,
    e_folds: float = DEFAULT_SETTLE_E_FOLDS,
    drift_window_fraction: float = DEFAULT_DRIFT_WINDOW_FRACTION,
    allow_drift: bool = True,
) -> SettleDecision:
    """Sweep constants of the per-frequency rule (no cap, no fallback).

    ``T_full = ceil(k / min(zeta*omega_n, sigma_floor))`` and ``T_floor =
    ceil(k / sigma_floor)``; the drift regime is available only when
    ``T_full > T_floor`` (the resonance decays slower than the slow-mode
    floor), with the drift window ``phi * 2 pi / omega_n``.
    ``allow_drift=False`` (runner ``--settle_force_full``, the drift-regime
    validation reference) puts every point in the full regime and records
    ``drift_regime_disabled``.
    """
    e_folds = float(e_folds)
    phi = float(drift_window_fraction)
    if not math.isfinite(e_folds) or e_folds < MIN_SETTLE_E_FOLDS:
        raise PriorError(
            f"settle e-folds must be >= {MIN_SETTLE_E_FOLDS:g} "
            f"(T_d >= 3/(zeta*omega_n)); got {e_folds!r}"
        )
    if not (math.isfinite(phi) and 0.0 < phi <= MAX_DRIFT_WINDOW_FRACTION):
        raise PriorError(
            "drift window fraction must be in (0, "
            f"{MAX_DRIFT_WINDOW_FRACTION:g}]; got {drift_window_fraction!r}"
        )
    res = prior.resonance(core, power)
    full = float(math.ceil(e_folds / res.sigma_eff_s_inv))
    floor_sigma = res.sigma_floor_s_inv
    floor = float(math.ceil(e_folds / floor_sigma)) if floor_sigma else full
    drift_enabled = bool(allow_drift) and floor_sigma is not None and full > floor
    return SettleDecision(
        rule=SETTLE_RULE_PRIOR,
        e_folds=e_folds,
        omega_n_rad_s=res.omega_n_rad_s,
        zeta=res.zeta,
        sigma_res_s_inv=res.sigma_res_s_inv,
        sigma_floor_s_inv=res.sigma_floor_s_inv,
        sigma_eff_s_inv=res.sigma_eff_s_inv,
        full_discard_s=full,
        floor_discard_s=floor,
        drift_window_fraction=phi,
        drift_window_s=float(phi * 2.0 * math.pi / res.omega_n_rad_s),
        drift_enabled=bool(drift_enabled),
        drift_disabled=not bool(allow_drift),
        source=res.source,
        extrapolated=res.extrapolated,
        prior=prior.reference(),
    )


@dataclass(frozen=True)
class PointSettle:
    """Discard, fit window, and estimator of one frequency point."""

    freq_rad_s: float
    regime: str
    discard_s: float
    rule_discard_s: float
    fit_start_s: float
    stop_time_s: float
    fit_trend_order: int
    gain_reference: str
    natural_period_fraction: float | None = None
    resonance_e_folds: float | None = None
    floor_e_folds: float | None = None
    refine_round: int = 0
    previous_discard_s: float | None = None
    fit_estimator: str = FIT_ESTIMATOR_SINE_LS

    @property
    def fit_window_s(self) -> float:
        return float(self.stop_time_s) - float(self.fit_start_s)

    @property
    def fit_cycles(self) -> float:
        return fit_cycles(self.freq_rad_s, self.fit_start_s, self.stop_time_s)

    def record(self) -> dict[str, Any]:
        return {
            "settle_regime": self.regime,
            "settle_discard_s": float(self.discard_s),
            "settle_rule_discard_s": float(self.rule_discard_s),
            "fit_start_s": float(self.fit_start_s),
            "stop_time_s": float(self.stop_time_s),
            "fit_window_s": float(self.fit_window_s),
            "fit_cycles": float(self.fit_cycles),
            "fit_trend_order": int(self.fit_trend_order),
            "gain_reference": self.gain_reference,
            "fit_estimator": self.fit_estimator,
            "natural_period_fraction": self.natural_period_fraction,
            "resonance_e_folds": self.resonance_e_folds,
            "floor_e_folds": self.floor_e_folds,
            "refine_round": int(self.refine_round),
            "previous_discard_s": (
                float(self.previous_discard_s)
                if self.previous_discard_s is not None
                else None
            ),
        }


def _stop_time_rule(freq: float, **kwargs: Any) -> float:
    try:
        from ._common import compute_effective_stop_time
    except ImportError:  # script-style execution from freq/
        from _common import compute_effective_stop_time
    return float(compute_effective_stop_time(float(freq), **kwargs))


def point_settle(
    decision: SettleDecision,
    freq: float,
    *,
    ss_time: float,
    base_stop_time: float,
    stop_time_mode: str,
    min_cycles_after_ss: float,
    refine_discard_s: float | None = None,
    refine_round: int = 0,
    previous_discard_s: float | None = None,
) -> PointSettle:
    """Per-frequency discard and fit window (rule ``settle_prior_v2``).

    - rule ``none``: no discard, historical stop time.
    - drift regime (``decision.drift_enabled``, ``min_cycles_after_ss`` stop
      mode, and ``2 pi N / omega <= phi * T_n``): ``T_d = T_floor``, window
      ``phi * T_n``, linear trend, window-mean gain reference.
    - full regime otherwise: ``T_d = T_full``, stop time
      ``max(stop_time, ceil(ss + T_d + N * 2 pi / omega))`` (or the fixed
      stop time), no trend, nominal-power gain reference.
    - lock-in regime (``decision.lockin_enabled``, not drift, and
      ``omega >= LOCKIN_MIN_OMEGA_RATIO * omega_n``): the full regime's
      discard and stop time exactly (same simulation), estimator
      ``lockin_local_mean_v1`` with a linear trend of the relative
      fluctuation and the local-mean gain reference.

    ``refine_discard_s`` (a refinement round) replaces the rule discard; it
    must not be shorter, and the fit-window length of the rule is kept (the
    window moves later by the discard increase).
    """
    freq = float(freq)
    ss_time = float(ss_time)
    stop_kwargs = dict(
        base_stop_time=float(base_stop_time),
        ss_time=ss_time,
        stop_time_mode=str(stop_time_mode),
        min_cycles_after_ss=float(min_cycles_after_ss),
    )
    if decision.rule != SETTLE_RULE_PRIOR:
        if refine_discard_s is not None:
            raise PriorError("refinement requires the prior settle rule")
        stop = _stop_time_rule(freq, settle_discard_s=0.0, **stop_kwargs)
        return PointSettle(
            freq_rad_s=freq,
            regime=SETTLE_REGIME_NONE,
            discard_s=0.0,
            rule_discard_s=0.0,
            fit_start_s=ss_time,
            stop_time_s=stop,
            fit_trend_order=0,
            gain_reference=GAIN_REFERENCE_NOMINAL,
        )
    n_window = 2.0 * math.pi * float(min_cycles_after_ss) / freq
    drift = bool(
        decision.drift_enabled
        and str(stop_time_mode) == "min_cycles_after_ss"
        and decision.drift_window_s is not None
        and n_window <= float(decision.drift_window_s) * (1.0 + 1e-12)
    )
    estimator = FIT_ESTIMATOR_SINE_LS
    if drift:
        regime = SETTLE_REGIME_DRIFT
        rule_discard = float(decision.floor_discard_s)
        window = max(float(decision.drift_window_s), n_window)
        rule_stop = float(math.ceil(ss_time + rule_discard + window))
        trend_order = DRIFT_TREND_ORDER
        reference = GAIN_REFERENCE_WINDOW_MEAN
    else:
        lockin = bool(
            decision.lockin_enabled
            and freq >= LOCKIN_MIN_OMEGA_RATIO * float(decision.omega_n_rad_s) * (1.0 - 1e-12)
        )
        rule_discard = float(decision.full_discard_s)
        rule_stop = _stop_time_rule(freq, settle_discard_s=rule_discard, **stop_kwargs)
        if lockin:
            regime = SETTLE_REGIME_LOCKIN
            trend_order = LOCKIN_TREND_ORDER
            reference = GAIN_REFERENCE_LOCAL_MEAN
            estimator = FIT_ESTIMATOR_LOCKIN
        else:
            regime = SETTLE_REGIME_FULL
            trend_order = 0
            reference = GAIN_REFERENCE_NOMINAL
    discard = rule_discard
    stop = rule_stop
    if refine_discard_s is not None:
        refine_discard_s = float(refine_discard_s)
        if not math.isfinite(refine_discard_s) or refine_discard_s < rule_discard - 1e-9:
            raise PriorError(
                f"refinement discard {refine_discard_s!r} s at {freq:g} rad/s is "
                f"shorter than the rule discard {rule_discard:g} s"
            )
        discard = refine_discard_s
        stop = rule_stop + (discard - rule_discard)
    omega_n = decision.omega_n_rad_s
    window_s = stop - (ss_time + discard)
    return PointSettle(
        freq_rad_s=freq,
        regime=regime,
        discard_s=float(discard),
        rule_discard_s=float(rule_discard),
        fit_start_s=float(ss_time + discard),
        stop_time_s=float(stop),
        fit_trend_order=int(trend_order),
        gain_reference=reference,
        natural_period_fraction=(
            float(omega_n * window_s / (2.0 * math.pi)) if omega_n else None
        ),
        resonance_e_folds=(
            float(decision.sigma_res_s_inv * discard)
            if decision.sigma_res_s_inv
            else None
        ),
        floor_e_folds=(
            float(decision.sigma_floor_s_inv * discard)
            if decision.sigma_floor_s_inv
            else None
        ),
        refine_round=int(refine_round),
        previous_discard_s=previous_discard_s,
        fit_estimator=estimator,
    )


def refine_discard(
    current_discard_s: float,
    full_discard_s: float,
    *,
    factor: float = REFINE_DISCARD_FACTOR,
) -> float:
    """Next refinement discard ``ceil(max(factor * T_d, T_full))``.

    A drift-regime point first jumps to the full resonance discard (the
    physical remedy when the drift treatment is not enough); every later
    round doubles the discard (``k`` -> ``2k`` e-folds).
    """
    current = float(current_discard_s)
    full = float(full_discard_s)
    if not (math.isfinite(current) and current > 0 and math.isfinite(full) and full >= 0):
        raise PriorError(
            f"refinement needs a positive current discard (got {current_discard_s!r})"
        )
    return float(math.ceil(max(float(factor) * current, full)))


def convergence_excess(
    gain_rel_diff: Any,
    phase_diff_deg: Any,
    *,
    gain_tol: float | None = None,
    phase_tol_deg: float | None = None,
) -> float:
    """Two-halves disagreement in units of its tolerance.

    ``max(|dG| / gain_tol, |dphi| / phase_tol_deg)`` of the collector's
    convergence metrics (default tolerances: the collector's
    ``freq._common.CONVERGENCE_GAIN_TOL`` / ``CONVERGENCE_PHASE_TOL_DEG``);
    below 1 both halves agree.  NaN when either metric is missing or
    nonfinite (the point is not evaluable).
    """
    if gain_tol is None or phase_tol_deg is None:
        try:
            from ._common import CONVERGENCE_GAIN_TOL, CONVERGENCE_PHASE_TOL_DEG
        except ImportError:  # script-style execution from freq/
            from _common import CONVERGENCE_GAIN_TOL, CONVERGENCE_PHASE_TOL_DEG
        gain_tol = CONVERGENCE_GAIN_TOL if gain_tol is None else gain_tol
        phase_tol_deg = CONVERGENCE_PHASE_TOL_DEG if phase_tol_deg is None else phase_tol_deg
    try:
        gain = abs(float(gain_rel_diff))
        phase = abs(float(phase_diff_deg))
    except (TypeError, ValueError):
        return math.nan
    if not (math.isfinite(gain) and math.isfinite(phase)):
        return math.nan
    return max(gain / float(gain_tol), phase / float(phase_tol_deg))


def refinement_action(
    last_action: str,
    *,
    previous_excess: float,
    current_excess: float,
    halves_failed: bool,
    min_improvement: float = HALVING_MIN_IMPROVEMENT,
) -> tuple[str, str | None, float]:
    """Next lever for an unconverged point: ``(action, trigger, improvement)``.

    Discard escalation comes first (a base or amplitude-rescale case always
    escalates the discard).  After a discard escalation the point's
    amplitude is halved when the escalation did not reduce the convergence
    excess by ``min_improvement`` (the residual is not a settling
    transient); after a halving it is halved again while halving keeps
    reducing the excess by that factor, and the discard escalates when it
    did not.  Only a two-halves failure (``halves_failed``: gain or phase)
    can trigger a halving; an operating-point or not-evaluable failure, or
    an unknown excess (rounds recorded before the metrics were), escalates
    the discard.  ``improvement`` is ``previous_excess / current_excess``
    (NaN when unknown).
    """
    previous = float(previous_excess)
    current = float(current_excess)
    improvement = (
        previous / current
        if math.isfinite(previous) and math.isfinite(current) and current > 0
        else math.nan
    )
    if not halves_failed or not math.isfinite(improvement):
        return REFINE_ACTION_DISCARD, None, improvement
    if last_action == LAST_ACTION_DISCARD and improvement < float(min_improvement):
        return REFINE_ACTION_HALVE, HALVING_TRIGGER_DISCARD_STALL, improvement
    if last_action == LAST_ACTION_HALVE and improvement >= float(min_improvement):
        return REFINE_ACTION_HALVE, HALVING_TRIGGER_HALVING_PROGRESS, improvement
    return REFINE_ACTION_DISCARD, None, improvement


def linearity_excess(
    gain_rel_change: Any,
    phase_change_deg: Any,
    *,
    gain_tol: float = 0.005,
    phase_tol_deg: float = 0.5,
) -> float:
    """Half-amplitude check discrepancy in units of its tolerance.

    ``max(|G_half/G_ref - 1| / gain_tol, |dphi| / phase_tol_deg)`` (the
    check's default 0.5 % / 0.5 deg); below 1 the gain and phase tests
    pass.  NaN when either value is missing or nonfinite.
    """
    return convergence_excess(
        gain_rel_change, phase_change_deg, gain_tol=gain_tol, phase_tol_deg=phase_tol_deg
    )


def _dominant_sign(entry: Mapping[str, Any]) -> float:
    """Sign of the component that sets an entry's linearity excess."""
    try:
        gain = float(entry.get("gain_rel_change"))
        phase = float(entry.get("phase_change_deg"))
    except (TypeError, ValueError):
        return math.nan
    if not (math.isfinite(gain) and math.isfinite(phase)):
        return math.nan
    value = gain if abs(gain) / 0.005 >= abs(phase) / 0.5 else phase
    return math.copysign(1.0, value) if value else 0.0


def linearity_action(
    history: list[Mapping[str, Any]],
    current: Mapping[str, Any],
    *,
    min_improvement: float = HALVING_MIN_IMPROVEMENT,
) -> tuple[str, str, dict[str, Any]]:
    """Next lever for a point whose half-amplitude check failed (v2).

    ``current`` describes the failed check: ``kind`` (``gain_phase`` or
    ``h2_only``), ``settled`` (both runs' two-halves excess at most
    :data:`LINEARITY_SETTLE_EXCESS`), ``pair_excess`` (the larger of the two
    runs' convergence excesses), ``linearity_excess`` (:func:`linearity_excess`),
    ``gain_rel_change`` / ``phase_change_deg``, and
    ``reference_amplitude_pcm``.  ``history`` holds the earlier decisions of
    the same point, oldest first, each with the same fields plus the
    ``action`` it caused.  Returns ``(action, trigger, detail)``:

    1. an unsettled pair escalates the discard of both runs (``settle``),
       unless the previous decision was already a settle escalation that did
       not reduce ``pair_excess`` by ``min_improvement``: then the
       non-stationarity is amplitude-driven and a gain/phase failure is
       halved (``linearity_unsettled_amplitude``; an ``h2_only`` failure is
       reported, ``linearity_settle_stalled``);
    2. a settled ``h2_only`` failure is reported (``none``,
       ``linearity_h2_only``): no lever of this rule addresses it;
    3. a settled gain/phase failure with no earlier settled failure is
       halved once (``linearity_probe``);
    4. after a halving (the latest earlier settled failure was followed by
       ``halve``): a discrepancy reduced by at least ``min_improvement``
       with an unchanged sign is amplitude-driven -- halve again
       (``linearity_nonlinear``); a flat, grown, or sign-flipped one is
       settling-limited -- restore the previous amplitude and escalate the
       discard (``restore``, ``linearity_settling_limited``);
    5. after a discard lever (``settle`` / ``restore`` / ``discard``) the
       discrepancy is compared with the latest earlier settled failure at
       the same amplitude: any reduction continues the discard lever
       (``discard``, ``linearity_discard_progress``; a settling transient
       shrinks by ``exp(dT / tau)`` per escalation, less than 2x while the
       discard is short of its time constant); no reduction probes the
       amplitude again (``halve``, ``linearity_probe``).

    ``detail`` records the compared entries and the improvement factor.
    """
    detail: dict[str, Any] = {"improvement": math.nan, "compared_with": None}
    kind = str(current.get("kind") or "gain_phase")
    previous = history[-1] if history else None
    if not current.get("settled"):
        if previous is not None and previous.get("action") == REFINE_ACTION_SETTLE:
            try:
                improvement = float(previous.get("pair_excess")) / float(current.get("pair_excess"))
            except (TypeError, ValueError, ZeroDivisionError):
                improvement = math.nan
            detail.update(improvement=improvement, compared_with="previous_settle")
            if math.isfinite(improvement) and improvement < float(min_improvement):
                if kind == "h2_only":
                    return REFINE_ACTION_NONE, LINEARITY_TRIGGER_SETTLE_STALLED, detail
                return REFINE_ACTION_HALVE, LINEARITY_TRIGGER_UNSETTLED_AMPLITUDE, detail
        return REFINE_ACTION_SETTLE, LINEARITY_TRIGGER_SETTLE, detail
    if kind == "h2_only":
        return REFINE_ACTION_NONE, LINEARITY_TRIGGER_H2_UNRESOLVED, detail
    settled = [entry for entry in history if entry.get("settled")]
    if not settled:
        return REFINE_ACTION_HALVE, LINEARITY_TRIGGER_PROBE, detail
    last = settled[-1]
    try:
        current_excess = float(current.get("linearity_excess"))
    except (TypeError, ValueError):
        current_excess = math.nan
    if last.get("action") == REFINE_ACTION_HALVE:
        try:
            improvement = float(last.get("linearity_excess")) / current_excess
        except (TypeError, ValueError, ZeroDivisionError):
            improvement = math.nan
        same_sign = _dominant_sign(last) == _dominant_sign(current)
        detail.update(improvement=improvement, compared_with="before_halving",
                      same_sign=bool(same_sign))
        if math.isfinite(improvement) and improvement >= float(min_improvement) and same_sign:
            return REFINE_ACTION_HALVE, LINEARITY_TRIGGER_NONLINEAR, detail
        return REFINE_ACTION_RESTORE, LINEARITY_TRIGGER_SETTLING_LIMITED, detail
    amplitude = current.get("reference_amplitude_pcm")
    same_amplitude = [
        entry for entry in settled
        if amplitude is not None and entry.get("reference_amplitude_pcm") is not None
        and math.isclose(float(entry["reference_amplitude_pcm"]), float(amplitude), rel_tol=1e-9)
    ]
    if same_amplitude:
        try:
            improvement = float(same_amplitude[-1].get("linearity_excess")) / current_excess
        except (TypeError, ValueError, ZeroDivisionError):
            improvement = math.nan
        detail.update(improvement=improvement, compared_with="same_amplitude")
        if math.isfinite(improvement) and improvement > 1.0:
            return REFINE_ACTION_DISCARD, LINEARITY_TRIGGER_DISCARD_PROGRESS, detail
    return REFINE_ACTION_HALVE, LINEARITY_TRIGGER_PROBE, detail


def case_solver_tolerance(
    request_tolerance: float,
    policy_target_swing: float | None,
    effective_target_swing: float | None,
    *,
    floor: float | None = None,
) -> tuple[float, float]:
    """``(tolerance, scale)`` of one case (per-case tolerance rules).

    ``scale = effective / policy`` target swing (1 without a target-swing
    rule or at the policy target); the tolerance is the request tolerance
    times ``scale``, rounded to 12 significant digits.  ``floor`` given
    (``tolerance_swing_scaled_v3``): the tolerance is clamped at it.
    Without a floor (``tolerance_swing_scaled_v2``) a tolerance below
    :data:`MIN_CASE_SOLVER_TOLERANCE` raises :class:`PriorError`.
    """
    base = float(request_tolerance)
    scale = 1.0
    if policy_target_swing and effective_target_swing:
        scale = float(effective_target_swing) / float(policy_target_swing)
        if math.isclose(scale, 1.0, rel_tol=1e-12):
            scale = 1.0
    if not (math.isfinite(scale) and 0.0 < scale <= 1.0 + 1e-12):
        raise PriorError(f"case tolerance scale {scale!r} is not in (0, 1]")
    if scale == 1.0:
        return base, 1.0
    tolerance = float(f"{base * scale:.12g}")
    if floor is not None:
        return max(float(floor), tolerance), float(f"{scale:.12g}")
    if tolerance < MIN_CASE_SOLVER_TOLERANCE * (1.0 - 1e-12):
        raise PriorError(
            f"case tolerance {tolerance:.3g} (request {base:g} x {scale:g}) is below "
            f"the floor {MIN_CASE_SOLVER_TOLERANCE:g}"
        )
    return tolerance, float(f"{scale:.12g}")


def halve_amplitude(
    current_pcm: float,
    effective_target_swing: float,
    *,
    min_pcm: float = DEFAULT_TARGET_SWING_MIN_PCM,
) -> tuple[float, float]:
    """``(amplitude, effective target swing)`` of one halving (exactly 0.5x).

    Refuses (:class:`PriorError`) a halving below the target-swing floor
    ``min_pcm``: the halving rule stops there.
    """
    current = float(current_pcm)
    target = float(effective_target_swing)
    if not (math.isfinite(current) and current > 0 and math.isfinite(target) and target > 0):
        raise PriorError(
            f"amplitude halving needs a positive amplitude and target swing "
            f"(got {current_pcm!r}, {effective_target_swing!r})"
        )
    halved = 0.5 * current
    if halved < float(min_pcm) * (1.0 - 1e-12):
        raise PriorError(
            f"halving {current:.6g} pcm would go below the target-swing floor "
            f"{float(min_pcm):g} pcm"
        )
    return halved, 0.5 * target


def forcing_event_count(stop_time_s: float, ss_time: float, forcing_step_s: float) -> int:
    """Forcing-window time events (``sample(ss_time, step)``) up to the stop."""
    step = float(forcing_step_s)
    if step <= 0:
        return 0
    return int(math.floor((float(stop_time_s) - float(ss_time)) / step)) + 1


def predicted_case_rows(
    *,
    stop_time_s: float,
    ss_time: float,
    number_of_intervals: int,
    forcing_step_s: float,
) -> int:
    """Result rows of one case: output grid plus two rows per forcing event.

    OpenModelica writes the pre- and post-event values of every time event
    (the forcing cadence ``sample``) in addition to the output grid; the
    ``review-2026-09`` case CSVs match ``intervals + 1 + 2 * events``.
    """
    return int(number_of_intervals) + 1 + 2 * forcing_event_count(
        stop_time_s, ss_time, forcing_step_s
    )


# ---------------------------------------------------------------------------
# Amplitude for a target relative swing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmplitudeDecision:
    """Perturbation amplitude of one frequency point (target-swing rule)."""

    freq_rad_s: float
    sin_mag_pcm: float
    unclamped_pcm: float
    prior_gain: float
    predicted_swing: float
    clamped: str | None
    extrapolated: bool

    def record(self, *, target_swing: float, min_pcm: float, max_pcm: float,
               prior: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "rule": SIN_MAG_RULE_TARGET_SWING,
            "rule_id": AMPLITUDE_RECORD_RULE_ID,
            "target_swing": float(target_swing),
            "min_pcm": float(min_pcm),
            "max_pcm": float(max_pcm),
            "prior_gain": float(self.prior_gain),
            "unclamped_pcm": float(self.unclamped_pcm),
            "sin_mag_pcm": float(self.sin_mag_pcm),
            "predicted_swing": float(self.predicted_swing),
            "clamped": self.clamped,
            "prior_extrapolated": bool(self.extrapolated),
            "prior": dict(prior),
        }


def target_swing_amplitude(
    prior: GainPrior,
    core: str,
    power: float,
    freq: float,
    *,
    target_swing: float = DEFAULT_TARGET_SWING,
    min_pcm: float = DEFAULT_TARGET_SWING_MIN_PCM,
    max_pcm: float = DEFAULT_TARGET_SWING_MAX_PCM,
) -> AmplitudeDecision:
    """``sin_mag = clamp(1e5 * s * P / |G_prior(omega, P)|, [min, max])`` pcm."""
    target_swing = float(target_swing)
    if not (math.isfinite(target_swing) and 0.0 < target_swing <= 0.5):
        raise PriorError(f"target swing must be in (0, 0.5]; got {target_swing!r}")
    if not (0.0 < float(min_pcm) <= float(max_pcm)) or not math.isfinite(float(max_pcm)):
        raise PriorError(
            f"target-swing amplitude bounds must satisfy 0 < min <= max; got "
            f"[{min_pcm!r}, {max_pcm!r}]"
        )
    estimate = prior.gain(core, freq, power)
    unclamped = target_swing * float(power) / estimate.gain / PCM
    clamped: str | None = None
    value = unclamped
    if value < float(min_pcm):
        value, clamped = float(min_pcm), "min"
    elif value > float(max_pcm):
        value, clamped = float(max_pcm), "max"
    return AmplitudeDecision(
        freq_rad_s=float(freq),
        sin_mag_pcm=float(value),
        unclamped_pcm=float(unclamped),
        prior_gain=float(estimate.gain),
        predicted_swing=float(estimate.gain * value * PCM / float(power)),
        clamped=clamped,
        extrapolated=estimate.extrapolated,
    )


def target_swing_profile(
    prior: GainPrior,
    core: str,
    power: float,
    freqs: Iterable[float],
    **kwargs: float,
) -> dict[float, AmplitudeDecision]:
    return {
        float(f): target_swing_amplitude(prior, core, power, float(f), **kwargs)
        for f in freqs
    }


def solver_tolerance(
    power: float,
    *,
    rule: str = DEFAULT_TOLERANCE_RULE,
    base: float | None = None,
    target_swing: float = DEFAULT_TARGET_SWING,
    resolution: float | None = None,
) -> dict[str, Any]:
    """Relative tolerance of one sweep and its record (see the constants).

    ``power_scaled_v2``: ``max(TOLERANCE_V2_FLOOR, min(base,
    resolution * target_swing * P))`` with ``P`` the normalized power (MW),
    ``base`` 1e-8 and ``resolution`` 1e-4 by default.  ``power_scaled``
    (v1): ``min(base, resolution * target_swing * P)`` with 1e-6 / 0.01.
    ``fixed``: ``base`` (1e-6 by default).  The population state's absolute
    error scale is ``tolerance * 1``; the record states its ratio to the
    target swing ``target_swing * P`` and, for v2, the floor and the
    per-case rule (:func:`case_solver_tolerance`).
    """
    power = float(power)
    base = float(default_base_tolerance(rule) if base is None else base)
    if not (math.isfinite(base) and 0.0 < base < 1.0):
        raise PriorError(f"base tolerance must be in (0, 1); got {base!r}")
    floor = None
    if rule == TOLERANCE_RULE_FIXED:
        tolerance = base
        rule_id = None
        resolution = TOLERANCE_SWING_RESOLUTION if resolution is None else float(resolution)
    elif rule in (TOLERANCE_RULE_POWER_SCALED, TOLERANCE_RULE_POWER_SCALED_V2):
        if not (math.isfinite(power) and power > 0):
            raise PriorError(f"power-scaled tolerance needs a power > 0; got {power!r}")
        v2 = rule == TOLERANCE_RULE_POWER_SCALED_V2
        if resolution is None:
            resolution = TOLERANCE_V2_SWING_RESOLUTION if v2 else TOLERANCE_SWING_RESOLUTION
        resolution = float(resolution)
        # Three significant digits: 1e-3 MW gives exactly 1e-07 (v1).
        tolerance = float(f"{min(base, resolution * float(target_swing) * power):.3g}")
        if v2:
            floor = TOLERANCE_V2_FLOOR
            tolerance = max(floor, tolerance)
        rule_id = TOLERANCE_RECORD_RULE_ID_V2 if v2 else TOLERANCE_RECORD_RULE_ID
    else:
        raise PriorError(f"unknown tolerance rule {rule!r}")
    swing_abs = float(target_swing) * power if power > 0 else math.nan
    record = {
        "rule": rule,
        "rule_id": rule_id,
        "base_tolerance": base,
        "tolerance": float(tolerance),
        "target_swing": float(target_swing),
        "swing_resolution": float(resolution),
        "population_abs_error_scale": float(tolerance),
        "target_swing_abs": swing_abs,
        "abs_error_to_swing": (
            float(tolerance) / swing_abs if swing_abs and swing_abs > 0 else None
        ),
    }
    if rule == TOLERANCE_RULE_POWER_SCALED_V2:
        # v2 records its floor and the per-case rule its halved and check
        # cases follow (clamped at the floor instead of refused).
        record.update(
            {
                "floor": float(floor),
                "case_rule_id": CASE_TOLERANCE_RULE_ID_V3,
                "case_floor": float(MIN_CASE_SOLVER_TOLERANCE_V3),
            }
        )
    return record


def swing_verdict(
    relative_swing: float,
    target_swing: float,
    *,
    clamped: str | None = None,
    band: tuple[float, float] = SWING_BAND,
) -> tuple[bool, float]:
    """``(in_band, ratio)`` of a realized swing against the target.

    ``ratio = relative_swing / target_swing``; in band when
    ``band[0] <= ratio <= band[1]``, or when the amplitude was clamped at
    ``max`` (``min``) and the swing falls short of (exceeds) the target.
    A non-finite swing is out of band.
    """
    swing = float(relative_swing)
    target = float(target_swing)
    if not (math.isfinite(swing) and math.isfinite(target) and target > 0):
        return False, math.nan
    ratio = swing / target
    if band[0] <= ratio <= band[1]:
        return True, ratio
    if clamped == "max" and ratio < band[0]:
        return True, ratio
    if clamped == "min" and ratio > band[1]:
        return True, ratio
    return False, ratio


def rescale_amplitude(
    current_pcm: float,
    relative_swing: float,
    *,
    target_swing: float = DEFAULT_TARGET_SWING,
    min_pcm: float = DEFAULT_TARGET_SWING_MIN_PCM,
    max_pcm: float = DEFAULT_TARGET_SWING_MAX_PCM,
) -> tuple[float, str | None]:
    """Amplitude that brings the measured swing to the target (clamped).

    The response is linear in the amplitude over the band, so the measured
    gain gives ``drho' = drho * target / measured``.  Returns ``(pcm,
    clamped)``, rounded to 12 significant digits.
    """
    current = float(current_pcm)
    swing = float(relative_swing)
    if not (math.isfinite(current) and current > 0 and math.isfinite(swing) and swing > 0):
        raise PriorError(
            f"amplitude rescale needs a positive amplitude and swing "
            f"(got {current_pcm!r}, {relative_swing!r})"
        )
    value = current * float(target_swing) / swing
    clamped: str | None = None
    if value < float(min_pcm):
        value, clamped = float(min_pcm), "min"
    elif value > float(max_pcm):
        value, clamped = float(max_pcm), "max"
    return float(f"{value:.12g}"), clamped


def fit_cycles(freq: float, fit_start: float, stop_time: float) -> float:
    """Forcing periods between the fit start and the stop time."""
    return (float(stop_time) - float(fit_start)) * float(freq) / (2.0 * math.pi)
