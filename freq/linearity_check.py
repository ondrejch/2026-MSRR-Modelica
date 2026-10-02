#!/usr/bin/env python3
"""Half-amplitude linearity self-check for frequency-response points.

A transfer-function point is linear when halving the perturbation amplitude
leaves the gain and phase unchanged while the second-harmonic ratio
``|H2|/|H1|`` (a quadratic nonlinearity) halves.  This helper compares a
reference campaign against half-amplitude reruns of selected points.

1. Pick points (default: the grid point nearest the prior resonance
   ``omega_n(P)``, plus the lowest and highest grid frequency)::

       python3.12 -m freq.linearity_check suggest --reference <campaign_dir>

   It prints, per point, the runner flags to append to the ORIGINAL
   campaign command line (same core, power, table, and protocol flags):
   ``--freq_min W --freq_max W --num_freq 1 --sin_mag_scale 0.5
   --base_dir <half_root>/freq<key>``.  The protocol makes the settling
   discard, stop time, and amplitude functions of (core, P, omega) only,
   so the single-point rerun reproduces the reference case except for the
   halved amplitude.

2. Run those commands (``freq.runFreqNominalParallel``; no collection is
   needed), then compare::

       python3.12 -m freq.linearity_check compare \\
           --reference <campaign_dir> --half <half_root>/freq<key> [...]

``compare`` audits every case through the collector's provenance checks
(sidecars, content hash, request identity), requires the half run to carry
the reference's parent identity (``freq.sweep_manifest``: request, case, and
case-manifest identity -- sources, workflow Python, Git state, tool
versions, setpoint table and model version, numerics, overrides, estimator
settings; ``--sin_mag_scale`` is the one extra authorized difference of this
mode), requires the half case to use exactly 0.5x the reference amplitude
with the same fit start and stop time, refits both over the recorded fit
window, and passes a point when ``|G_half/G_ref - 1| <= --gain_tol``
(default 0.005), ``|phase_half - phase_ref| <= --phase_tol_deg`` (0.5),
and the harmonic test (:func:`h2_verdict`) passes: a reference
``|H2|/|H1|`` below ``--h2_floor`` (1e-4) is numerical noise (not
applicable); at or above it the half ratio must be finite and
``(H2/H1)_half / (H2/H1)_ref`` within ``--h2_band`` (0.35 to 0.65); a
nonfinite reference fails unless its window is below the measurable
harmonic threshold (under one forcing period or 10 samples).  Exit status
0 only when every compared point passes.

``campaign`` (approval checks of a converged sweep) reruns the band edges
and the measured resonance at half amplitude with their effective
discards and amplitudes; the runner refuses a check run that departs from
the parent identity before simulating it, and
:func:`evaluate_campaign_checks` refuses it again at verification.  A
point whose effective case changed after its check (an amplitude halving
by ``freq.refine_sweep`` after a failed check, or any later refinement)
or that newly enters the selection (the measured resonance moved) is
checked in a new attempt ``checks/linearity_half_amplitude_attempt_NN``;
each selected point is judged by the latest attempt that covers it, and
``campaign`` only simulates the points that lack a current check
(re-evaluation otherwise).  ``freq.refine_sweep`` runs the same stage
inside its loop (:func:`run_check_stage`).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

try:
    from . import collectFreqNominalParallel as collector
    from . import fr_protocol
    from . import lockin
    from . import refinement
    from . import sweep_manifest
    from ._common import (
        find_collect_columns,
        fit_sine_least_squares,
        fit_window_convergence,
        format_frequency_key,
        harmonic_ratio,
        phase_relative_to_perturbation_start_deg,
    )
except ImportError:  # script-style execution from freq/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from freq import collectFreqNominalParallel as collector
    from freq import fr_protocol
    from freq import lockin
    from freq import refinement
    from freq import sweep_manifest
    from freq._common import (
        find_collect_columns,
        fit_sine_least_squares,
        fit_window_convergence,
        format_frequency_key,
        harmonic_ratio,
        phase_relative_to_perturbation_start_deg,
    )

DEFAULT_GAIN_TOL = 0.005
DEFAULT_PHASE_TOL_DEG = 0.5
DEFAULT_H2_FLOOR = 1e-4
DEFAULT_H2_BAND = (0.35, 0.65)
#: Measurability of |H2|/|H1| (the preconditions of
#: ``freq._common.harmonic_ratio``): at least one forcing period and the
#: fitter's sample floor.
HARMONIC_MIN_CYCLES = 1.0
HARMONIC_MIN_SAMPLES = 10

#: ``h2_status`` values of :func:`compare_point`.
H2_PASS = "pass"
H2_FAIL = "fail"
H2_BELOW_FLOOR = "not_applicable_below_floor"
H2_BELOW_RESOLUTION = "not_applicable_below_resolution"
H2_NOT_MEASURABLE = "not_applicable_not_measurable"
H2_HALF_NONFINITE = "fail_half_nonfinite"
H2_REFERENCE_NONFINITE = "fail_reference_nonfinite"
H2_PASSING_STATUSES = frozenset(
    {H2_PASS, H2_BELOW_FLOOR, H2_BELOW_RESOLUTION, H2_NOT_MEASURABLE}
)
#: H2 statuses that block approval. Owner decision 2026-09-29: the harmonic
#: ratio band is INFORMATIONAL -- approval rests on the gain and phase
#: linearity tests, which are what the transfer function needs; the models
#: are accurate to a few percent, and an out-of-band ratio of a second
#: harmonic that is a small fraction of the fundamental does not threaten
#: that. A NONFINITE harmonic measurement (a broken fit) still blocks
#: (rev032 review).
H2_BLOCKING_STATUSES = frozenset({H2_HALF_NONFINITE, H2_REFERENCE_NONFINITE})
#: Resolution-aware H2 applicability: the ratio test applies only when the
#: half run's second harmonic relative to the mean power, ``|H2|/|H1| x
#: relative swing``, is at least this multiple of the solver's absolute
#: error scale relative to the mean power (the half case's tolerance / P).
#: 1x: in the 2026-09-28b campaign every checked point at or above 1x
#: passed (21 of 21) and every H2 failure lay at 0.01-0.71x; at the
#: 1e-8 tolerance of the 2026-09-28 H2 diagnostic the same failures passed
#: at 20x and more (freq/README.md, "Approval checks").
H2_RESOLUTION_MULTIPLE = 1.0


def h2_verdict(
    h2_ref: float,
    h2_half: float,
    *,
    reference_measurable: bool,
    h2_floor: float = DEFAULT_H2_FLOOR,
    h2_band: tuple[float, float] = DEFAULT_H2_BAND,
    half_component: float | None = None,
    half_error_scale: float | None = None,
    resolution_multiple: float = H2_RESOLUTION_MULTIPLE,
) -> tuple[str, float]:
    """``(h2_status, half/ref ratio)`` of the harmonic-halving test.

    - reference finite and below the floor: not applicable (noise);
    - reference finite at or above the floor: the half result must be finite
      and its ratio in ``h2_band``, else fail (a nonfinite half result
      is a failed measurement, never "not applicable"); the ratio test is
      not applied (``not_applicable_below_resolution``, ratio recorded)
      when the half run's second harmonic ``half_component`` (relative to
      the mean power) is below ``resolution_multiple`` x its solver error
      scale ``half_error_scale`` (tolerance / P);
    - reference nonfinite: fail, unless the reference window is explicitly
      below the measurable-harmonic threshold (``reference_measurable``
      False: under one forcing period or the sample floor).
    """
    ref = float(h2_ref)
    half = float(h2_half)
    if not math.isfinite(ref):
        return (H2_NOT_MEASURABLE if not reference_measurable else H2_REFERENCE_NONFINITE), math.nan
    if ref < float(h2_floor):
        return H2_BELOW_FLOOR, (half / ref if math.isfinite(half) and ref > 0 else math.nan)
    if not math.isfinite(half):
        return H2_HALF_NONFINITE, math.nan
    ratio = half / ref
    if (
        half_component is not None and half_error_scale is not None
        and math.isfinite(float(half_component)) and math.isfinite(float(half_error_scale))
        and float(half_component) < float(resolution_multiple) * float(half_error_scale)
    ):
        return H2_BELOW_RESOLUTION, ratio
    return (H2_PASS if h2_band[0] <= ratio <= h2_band[1] else H2_FAIL), ratio


def _case_estimator(case: dict) -> str:
    """The case's recorded fit estimator (older requests: from the gain
    reference; the lock-in estimator and the local-mean reference go
    together)."""
    recorded = case.get("fit_estimator")
    if recorded:
        return str(recorded)
    if case.get("gain_reference") == fr_protocol.GAIN_REFERENCE_LOCAL_MEAN:
        return fr_protocol.FIT_ESTIMATOR_LOCKIN
    return fr_protocol.FIT_ESTIMATOR_SINE_LS


def _load_request(results_dir: Path) -> dict:
    return sweep_manifest.load_sweep_request_manifest(results_dir)


def _case_fit_start(case: dict, request: dict) -> float:
    value = case.get("fit_start_s")
    if value is None:
        return float(request["request"]["perturbation_start_time_s"])
    return float(value)


def _audit(results_dir: Path, request: dict, case: dict):
    return collector.audit_case(
        float(case["frequency_rad_s"]),
        str(results_dir),
        float(case["perturbation_amplitude_pcm"]),
        allow_legacy=False,
        request_manifest=request,
        expected_stop_time=float(case["stop_time_s"]),
        expected_intervals=int(case["number_of_intervals"]),
        expected_fit_start=case.get("fit_start_s"),
    )


def _read_trace(csv_path: str) -> tuple[np.ndarray, np.ndarray]:
    with open(csv_path, encoding="utf-8") as handle:
        header = handle.readline().strip().split(",")
    time_idx, power_idx = find_collect_columns(header)
    data = np.loadtxt(csv_path, delimiter=",", skiprows=1, usecols=(time_idx, power_idx))
    data = np.atleast_2d(data)
    return data[:, 0], data[:, 1]


def fit_point(csv_path: str, freq: float, amplitude_pcm: float, fit_start: float,
              stop_time: float, perturbation_start: float, *, fit_trend: bool = False,
              trend_order: int = 0,
              gain_reference: str = fr_protocol.GAIN_REFERENCE_NOMINAL,
              power: float | None = None,
              fit_estimator: str = fr_protocol.FIT_ESTIMATOR_SINE_LS) -> dict:
    """Gain, phase, and |H2|/|H1| of one case over its recorded fit window.

    ``trend_order`` / ``gain_reference`` / ``fit_estimator`` are the case's
    recorded estimator (drift-regime points: linear trend, gain referenced to
    the window-mean power ``power``; lock-in points: freq/lockin.py, gain
    referenced to the local mean power); ``fit_trend`` forces at least a
    linear trend.
    """
    time, power_trace = _read_trace(csv_path)
    if fit_estimator == fr_protocol.FIT_ESTIMATOR_LOCKIN:
        return _fit_point_lockin(
            time, power_trace, freq, amplitude_pcm, fit_start, stop_time,
            perturbation_start, trend_order=max(int(trend_order), 1 if fit_trend else 0),
            gain_reference=gain_reference, power=power,
        )
    if fit_estimator != fr_protocol.FIT_ESTIMATOR_SINE_LS:
        return {"ok": False, "reason": f"unknown fit estimator {fit_estimator!r}"}
    mask = (time >= fit_start) & (time <= stop_time)
    t_rel = time[mask] - fit_start
    y = power_trace[mask]
    order = max(int(trend_order), 1 if fit_trend else 0)
    fit = fit_sine_least_squares(t_rel, y, freq, trend_order=order)
    if not fit.ok:
        return {"ok": False, "reason": f"sine fit rejected: {fit.rejection_reason}"}
    gain = float(fit.amplitude) / (float(amplitude_pcm) * fr_protocol.PCM)
    if gain_reference == fr_protocol.GAIN_REFERENCE_WINDOW_MEAN:
        if not power or fit.window_mean_level() <= 0:
            return {"ok": False, "reason": "window-mean gain reference needs a power"}
        gain *= float(power) / float(fit.window_mean_level())
    return {
        "ok": True,
        "gain": gain,
        "gain_reference": gain_reference,
        "trend_order": order,
        "phase_deg": phase_relative_to_perturbation_start_deg(
            float(fit.phase_rad), freq, fit_start, perturbation_start
        ),
        "h2_h1_ratio": harmonic_ratio(t_rel, y, freq, trend_order=order),
        # The second harmonic is measurable only over at least one full
        # forcing period with the fitter's sample floor (harmonic_ratio's own
        # preconditions); below that a NaN ratio is "not measurable", not a
        # failed measurement.
        "h2_measurable": bool(
            float(fit.n_cycles) >= HARMONIC_MIN_CYCLES
            and int(fit.n_samples) >= HARMONIC_MIN_SAMPLES
        ),
        "relative_swing": float(fit.amplitude) / float(fit.c0) if fit.c0 else math.nan,
        "n_cycles": float(fit.n_cycles),
        # The run's own two-halves settling metrics (the collector's
        # convergence test on the same window and estimator): the
        # linearity branch of freq.refine_sweep trusts a failed check only
        # when both runs are settled (fr_protocol.LINEARITY_SETTLE_EXCESS).
        "convergence": _convergence_record(t_rel, y, freq, order),
    }


def _fit_point_lockin(time, power_trace, freq: float, amplitude_pcm: float,
                      fit_start: float, stop_time: float, perturbation_start: float,
                      *, trend_order: int, gain_reference: str,
                      power: float | None) -> dict:
    """:func:`fit_point` for a lock-in case (freq/lockin.py)."""
    if gain_reference != fr_protocol.GAIN_REFERENCE_LOCAL_MEAN:
        return {"ok": False, "reason": "the lock-in estimator needs the local-mean gain reference"}
    if not power:
        return {"ok": False, "reason": "local-mean gain reference needs a power"}
    fit = lockin.lockin_fit(
        time, power_trace, freq, fit_start=fit_start, fit_end=stop_time,
        perturbation_start=perturbation_start, time_base=fit_start,
        trend_order=int(trend_order),
    )
    if not fit.ok:
        return {"ok": False, "reason": f"lock-in fit rejected: {fit.rejection_reason}"}
    halves = fit.halves
    return {
        "ok": True,
        "gain": float(fit.amplitude) * float(power) / (float(amplitude_pcm) * fr_protocol.PCM),
        "gain_reference": gain_reference,
        "fit_estimator": fr_protocol.FIT_ESTIMATOR_LOCKIN,
        "trend_order": int(trend_order),
        "phase_deg": phase_relative_to_perturbation_start_deg(
            float(fit.phase_rad), freq, fit_start, perturbation_start
        ),
        "h2_h1_ratio": float(fit.h2_h1_ratio),
        "h2_measurable": bool(
            float(fit.periods) >= HARMONIC_MIN_CYCLES
            and int(fit.n_samples) >= HARMONIC_MIN_SAMPLES
        ),
        # Already relative to the local mean power.
        "relative_swing": float(fit.amplitude),
        "n_cycles": float(fit.periods),
        "lockin": fit.record(),
        "convergence": {
            "evaluated": bool(halves.get("evaluated")),
            "gain_rel_diff": float(halves.get("gain_rel_diff", math.nan)),
            "phase_diff_deg": float(halves.get("phase_diff_deg", math.nan)),
            "excess": fr_protocol.convergence_excess(
                halves.get("gain_rel_diff"), halves.get("phase_diff_deg")
            ),
            "reason": str(halves.get("reason") or ""),
        },
    }


def _convergence_record(t_rel, y, freq: float, order: int) -> dict:
    metrics = fit_window_convergence(t_rel, y, freq, trend_order=order)
    excess = fr_protocol.convergence_excess(metrics["gain_rel_diff"], metrics["phase_diff_deg"])
    return {
        "evaluated": bool(metrics["evaluated"]),
        "gain_rel_diff": float(metrics["gain_rel_diff"]),
        "phase_diff_deg": float(metrics["phase_diff_deg"]),
        "excess": excess,
        "reason": str(metrics.get("reason") or ""),
    }


#: Paths a separately launched half-amplitude sweep (``compare`` mode,
#: ``--sin_mag_scale 0.5``) may change beyond the per-case authorized set.
SCALED_SWEEP_AUTHORIZED_REQUEST = ("policies.fr_protocol.sin_mag_scale",)
SCALED_SWEEP_AUTHORIZED_MANIFEST = ("fr_protocol.amplitude.sin_mag_scale",)


def _without_paths(payload: dict, paths: tuple[str, ...]) -> dict:
    copy = json.loads(json.dumps(payload))
    for path in paths:
        node = copy
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return copy


def compare_point(reference_dir: Path, ref_request: dict, half_dir: Path,
                  half_request: dict, key: str, *, gain_tol: float,
                  phase_tol_deg: float, h2_floor: float,
                  h2_band: tuple[float, float], fit_trend: bool = False,
                  base_request: dict | None = None,
                  scaled_sweep: bool = False) -> dict:
    """Compare one half-amplitude case with its reference case.

    Both runs must carry the same parent identity
    (``freq.sweep_manifest``): the half request against the reference
    request (``request_identity``), the half case against the reference case
    (``case_identity``: regime, trend order, gain reference, window, forcing
    cadence), the half case manifest against the reference case manifest
    (``manifest_identity``: sources, workflow Python, Git state, tool
    versions, setpoint identity, overrides, estimator settings), and both
    manifests against ``base_request`` (default: the reference request).
    Only the amplitude may differ, by exactly 0.5; ``scaled_sweep`` also
    authorizes the ``--sin_mag_scale`` record of a separately launched
    half-amplitude sweep (``compare`` mode).
    """
    ref_case = sweep_manifest.case_map(ref_request)[key]
    half_case = sweep_manifest.case_map(half_request)[key]
    record: dict = {"frequency_key": key, "frequency_rad_s": float(ref_case["frequency_rad_s"])}
    problems: list[str] = []
    ref_req, half_req = ref_request["request"], half_request["request"]
    anchor = (base_request or ref_request)["request"]
    request_authorized = SCALED_SWEEP_AUTHORIZED_REQUEST if scaled_sweep else ()
    for path in sweep_manifest.request_identity_differences(
        ref_req, half_req, authorized=request_authorized
    ):
        problems.append(f"request identity differs: {path}")
    for path in sweep_manifest.case_identity_differences(ref_case, half_case):
        problems.append(f"case identity differs: {path}")
    ref_amp = float(ref_case["perturbation_amplitude_pcm"])
    half_amp = float(half_case["perturbation_amplitude_pcm"])
    record["amplitude_ratio"] = half_amp / ref_amp
    if not math.isclose(half_amp / ref_amp, 0.5, rel_tol=1e-6):
        problems.append(f"amplitude ratio {half_amp / ref_amp:.8g} is not 0.5")
    ref_start, half_start = _case_fit_start(ref_case, ref_request), _case_fit_start(half_case, half_request)
    if not math.isclose(ref_start, half_start, rel_tol=1e-12, abs_tol=1e-6):
        problems.append(f"fit start differs ({ref_start:g} vs {half_start:g} s)")
    if not math.isclose(float(ref_case["stop_time_s"]), float(half_case["stop_time_s"]),
                        rel_tol=1e-12, abs_tol=1e-6):
        problems.append("stop time differs")
    audits = {"reference": _audit(reference_dir, ref_request, ref_case),
              "half": _audit(half_dir, half_request, half_case)}
    for label, audit in audits.items():
        if not audit.accepted:
            problems.append(f"{label} case rejected by provenance audit: {audit.status}: {audit.reason}")
    if not problems:
        manifests = {label: audit.manifest or {} for label, audit in audits.items()}
        manifest_authorized = SCALED_SWEEP_AUTHORIZED_MANIFEST if scaled_sweep else ()
        for path in sweep_manifest.manifest_identity_differences(
            _without_paths(manifests["reference"], manifest_authorized),
            _without_paths(manifests["half"], manifest_authorized),
        ):
            problems.append(f"case manifest identity differs: {path}")
        for label, manifest in manifests.items():
            for path in sweep_manifest.manifest_request_differences(
                _without_paths(anchor, request_authorized), manifest
            ):
                problems.append(f"{label} case manifest departs from the parent identity: {path}")
    if problems:
        record.update({"pass": False, "problems": problems})
        return record
    start = float(ref_req["perturbation_start_time_s"])
    fits = {}
    for label, case, audit in (("reference", ref_case, audits["reference"]),
                               ("half", half_case, audits["half"])):
        fits[label] = fit_point(
            str(audit.source_csv), float(case["frequency_rad_s"]),
            float(case["perturbation_amplitude_pcm"]), _case_fit_start(case, ref_request if label == "reference" else half_request),
            float(case["stop_time_s"]), start, fit_trend=fit_trend,
            trend_order=int(case.get("fit_trend_order") or 0),
            gain_reference=str(
                case.get("gain_reference") or fr_protocol.GAIN_REFERENCE_NOMINAL
            ),
            power=float(ref_req["power"]) if ref_req.get("power") is not None else None,
            fit_estimator=_case_estimator(case),
        )
        if not fits[label]["ok"]:
            problems.append(f"{label}: {fits[label]['reason']}")
    if problems:
        record.update({"pass": False, "problems": problems, "fits": fits})
        return record
    gain_ratio = fits["half"]["gain"] / fits["reference"]["gain"] - 1.0
    phase_diff = (fits["half"]["phase_deg"] - fits["reference"]["phase_deg"] + 180.0) % 360.0 - 180.0
    h2_ref, h2_half = fits["reference"]["h2_h1_ratio"], fits["half"]["h2_h1_ratio"]
    # Resolution of the half run's second harmonic: its size relative to the
    # mean power against the solver's absolute error scale (the half case's
    # tolerance x nominal 1 MW, relative to the mean power P).
    half_tolerance = sweep_manifest.case_solver_tolerance(half_req, half_case)
    power = float(half_req["power"]) if half_req.get("power") else math.nan
    half_component = float(h2_half) * float(fits["half"].get("relative_swing", math.nan))
    half_error_scale = (
        float(half_tolerance) / power
        if half_tolerance is not None and math.isfinite(power) and power > 0
        else math.nan
    )
    h2_status, h2_ratio = h2_verdict(
        h2_ref, h2_half,
        reference_measurable=bool(fits["reference"].get("h2_measurable", True)),
        h2_floor=h2_floor, h2_band=h2_band,
        half_component=half_component, half_error_scale=half_error_scale,
    )
    checks = {
        "gain": abs(gain_ratio) <= gain_tol,
        "phase": abs(phase_diff) <= phase_tol_deg,
        # Gating h2 = the harmonic MEASUREMENT is usable; the ratio band
        # verdict is recorded (h2_band_pass) but informational.
        "h2": h2_status not in H2_BLOCKING_STATUSES,
    }
    # Settledness of the pair (v2 linearity branch): both runs' two-halves
    # excess at most fr_protocol.LINEARITY_SETTLE_EXCESS; an unevaluable run
    # is not settled.
    excesses = [fits[label]["convergence"]["excess"] for label in ("reference", "half")]
    pair_excess = (
        max(excesses) if all(math.isfinite(value) for value in excesses) else math.inf
    )
    record.update(
        {
            "pass": all(checks.values()),
            "checks": checks,
            "linearity_excess": fr_protocol.linearity_excess(
                gain_ratio, phase_diff, gain_tol=gain_tol, phase_tol_deg=phase_tol_deg
            ),
            "pair_excess": pair_excess,
            "settled": bool(pair_excess <= fr_protocol.LINEARITY_SETTLE_EXCESS),
            "gain_rel_change": gain_ratio,
            "phase_change_deg": phase_diff,
            "h2_h1_reference": h2_ref,
            "h2_h1_half": h2_half,
            "h2_ratio_half_over_reference": h2_ratio,
            "h2_status": h2_status,
            "h2_band_pass": h2_status in H2_PASSING_STATUSES,
            "h2_band_gating": False,
            # Both numbers of the resolution test (the half run's second
            # harmonic and its solver error scale, relative to the mean
            # power) and the applied multiple.
            "h2_half_component": half_component,
            "h2_half_error_scale": half_error_scale,
            "h2_resolution_multiple": H2_RESOLUTION_MULTIPLE,
            "fits": fits,
        }
    )
    return record


def suggest_points(reference_dir: Path, prior_path: str | None = None) -> list[dict]:
    request = _load_request(reference_dir)
    req = request["request"]
    cases = sorted(sweep_manifest.case_map(request).values(), key=lambda c: float(c["frequency_rad_s"]))
    freqs = [float(c["frequency_rad_s"]) for c in cases]
    picks = {0, len(cases) - 1}
    note = None
    try:
        prior = fr_protocol.load_gain_prior(prior_path)
        omega_n = prior.resonance(str(req["core_model"]), float(req["power"])).omega_n_rad_s
        picks.add(int(np.argmin(np.abs(np.log(np.asarray(freqs) / omega_n)))))
    except (fr_protocol.PriorError, KeyError) as exc:
        note = f"no prior resonance ({exc}); suggesting the band edges only"
    out = []
    for idx in sorted(picks):
        case = cases[idx]
        key = str(case["frequency_key"])
        w = float(case["frequency_rad_s"])
        out.append(
            {
                "frequency_key": key,
                "frequency_rad_s": w,
                "runner_flags": (
                    f"--freq_min {w!r} --freq_max {w!r} --num_freq 1 "
                    f"--sin_mag_scale 0.5 --base_dir <half_root>/freq{key}"
                ),
                "note": note,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Campaign approval checks (``campaign`` subcommand; freq.verify_campaign)
# ---------------------------------------------------------------------------

CHECK_PURPOSE = refinement.PURPOSE_LINEARITY
CHECK_REPORT_FILENAME = "linearity_report.json"
CHECK_STATUS_PASS = "pass"
CHECK_STATUS_FAIL = "fail"
CHECK_STATUS_MISSING = "missing"
CHECK_STATUS_NOT_REQUIRED = "not_required"


def _aggregate_rows(results_dir: Path) -> list[dict]:
    import csv

    path = results_dir / collector.AGGREGATE_CSV_FILENAME
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_check_points(base_payload: dict, rows: list[dict]) -> list[dict]:
    """Band edges and the measured resonance point (gain maximum).

    The resonance point is the aggregate's gain maximum (the measured peak of
    the corrected model, not the prior's); where the peak lies below the band
    (1e-5 MW) it coincides with the low edge and the selection has two points.
    """
    cases = sorted(
        sweep_manifest.case_map(base_payload).values(),
        key=lambda case: float(case["frequency_rad_s"]),
    )
    gains = {}
    for row in rows:
        try:
            gains[format_frequency_key(float(row["frequency_rad_s"]))] = float(row["gain"])
        except (KeyError, TypeError, ValueError):
            continue
    keys = [str(case["frequency_key"]) for case in cases]
    picks: list[tuple[str, str]] = [(keys[0], "band_low")]
    finite = {key: value for key, value in gains.items() if key in keys and math.isfinite(value)}
    if finite:
        picks.append((max(finite, key=finite.get), "resonance"))
    picks.append((keys[-1], "band_high"))
    out: dict[str, dict] = {}
    for key, role in picks:
        entry = out.setdefault(key, {"frequency_key": key, "roles": []})
        entry["roles"].append(role)
    by_key = {str(case["frequency_key"]): case for case in cases}
    return [
        {**entry, "frequency_rad_s": float(by_key[key]["frequency_rad_s"])}
        for key, entry in sorted(out.items(), key=lambda item: float(by_key[item[0]]["frequency_rad_s"]))
    ]


def check_plan(results_dir: Path, base_payload: dict, sources: dict, selection: list[dict],
               *, attempt: int = 1) -> dict:
    """Half-amplitude check plan (attempt ``attempt``): each point's
    EFFECTIVE case (base or refined round) with its discard, half its
    amplitude, and half its effective target swing."""
    points = []
    for item in selection:
        source = sources[item["frequency_key"]]
        amplitude = float(source.case["perturbation_amplitude_pcm"])
        discard = float(source.case.get("settle_discard_s") or 0.0)
        target = sweep_manifest.case_target_swing(source.payload["request"], source.case)
        points.append(
            {
                "frequency_key": item["frequency_key"],
                "frequency_rad_s": float(source.case["frequency_rad_s"]),
                "settle_discard_s": discard,
                "previous_settle_discard_s": discard,
                "previous_round": int(source.round_index),
                "reason": "approval check: " + "+".join(item["roles"]),
                "perturbation_amplitude_pcm": 0.5 * amplitude,
                "previous_amplitude_pcm": amplitude,
                "amplitude_rule": refinement.AMPLITUDE_RULE_HALF,
                "effective_target_swing": None if target is None else 0.5 * float(target),
                "amplitude_halvings": sweep_manifest.case_amplitude_halvings(source.case),
            }
        )
    return refinement.build_plan(
        results_dir, base_payload, int(attempt), points, purpose=CHECK_PURPOSE
    )


def _load_attempts(root: Path, base_payload: dict) -> tuple[list[dict], list[str]]:
    """Check attempts with a published request (``[{attempt, directory,
    payload}]``, ascending) and the problems that make any of them invalid.

    The last attempt may still lack its request (a plan whose run has not
    published yet): it is skipped, not a problem.
    """
    problems: list[str] = []
    loaded: list[dict] = []
    try:
        attempts = refinement.check_attempt_dirs(root, CHECK_PURPOSE)
    except refinement.RefinementError as exc:
        return [], [str(exc)]
    for position, (attempt, directory) in enumerate(attempts):
        try:
            payload = sweep_manifest.load_sweep_request_manifest(directory)
        except sweep_manifest.SweepManifestError as exc:
            if position == len(attempts) - 1 and not (
                directory / sweep_manifest.SWEEP_REQUEST_FILENAME
            ).exists():
                continue
            problems.append(f"no valid check request in {directory}: {exc}")
            continue
        block = payload["request"].get("check")
        label = f"check attempt {attempt}"
        if not isinstance(block, dict) or block.get("purpose") != CHECK_PURPOSE:
            problems.append(f"{label}: check request carries no linearity check block")
        elif block.get("parent_request_fingerprint") != base_payload["fingerprint"]:
            problems.append(f"{label}: check request belongs to a different parent request")
        elif int(block.get("round", 1)) != int(attempt):
            problems.append(
                f"{label}: check request attempt {block.get('round')!r} does not match "
                "its directory"
            )
        # Parent identity (freq/sweep_manifest.py): every attempt must carry
        # the base request's identity.
        for path in sweep_manifest.request_identity_differences(
            base_payload["request"], payload["request"]
        ):
            problems.append(f"{label}: check request departs from the parent identity: {path}")
        if isinstance(block, dict):
            for point in block.get("points") or []:
                if str(point.get("amplitude_rule") or "") != refinement.AMPLITUDE_RULE_HALF:
                    problems.append(
                        f"{label}: check point {point.get('frequency_key')} is not a "
                        "half-amplitude run"
                    )
        loaded.append({"attempt": int(attempt), "directory": directory, "payload": payload})
    return loaded, problems


def _latest_by_key(loaded: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for entry in loaded:
        for key in sweep_manifest.case_map(entry["payload"]):
            latest[key] = entry
    return latest


def _stale_reason(entry: dict, key: str, source) -> str | None:
    """Why the latest check of ``key`` no longer checks its effective case."""
    block = entry["payload"]["request"].get("check") or {}
    point = next(
        (item for item in block.get("points") or [] if str(item.get("frequency_key")) == key),
        None,
    )
    if point is None:
        return "its check request lists no plan point"
    if int(point.get("previous_round", -1)) != int(source.round_index):
        return (
            f"planned against round {point.get('previous_round')!r}; the effective case "
            f"is round {source.round_index}"
        )
    if not math.isclose(
        float(point.get("previous_amplitude_pcm") or math.nan),
        float(source.case["perturbation_amplitude_pcm"]),
        rel_tol=1e-9,
    ):
        return "planned against another amplitude than the effective case's"
    return None


def pending_check_keys(results_dir: str | Path, base_payload: dict, sources: dict,
                       rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(selection, pending)``: the required points and those that lack a
    current check (no attempt covers them, or their latest attempt was
    planned against an earlier effective case)."""
    root = Path(results_dir).resolve()
    selection = select_check_points(base_payload, rows)
    loaded, _problems = _load_attempts(root, base_payload)
    latest = _latest_by_key(loaded)
    pending = []
    for item in selection:
        key = item["frequency_key"]
        entry = latest.get(key)
        if entry is None:
            pending.append({**item, "why": "not checked yet"})
            continue
        reason = _stale_reason(entry, key, sources[key])
        if reason:
            pending.append({**item, "why": reason})
    return selection, pending


def evaluate_campaign_checks(
    results_dir: str | Path,
    base_payload: dict,
    *,
    sources: dict | None = None,
    rows: list[dict] | None = None,
) -> dict:
    """Re-evaluate the half-amplitude approval checks of one sweep.

    Required when the base request declares them
    (``policies.fr_protocol.approval_checks``).  Check attempts live in
    ``<dir>/checks/linearity_half_amplitude`` (attempt 1) and
    ``..._attempt_NN``, each with its own immutable request (``check`` block
    naming the parent fingerprint and the attempt); every attempt must carry
    the parent identity.  The band edges and the CURRENT measured resonance
    point must each be covered, by the latest attempt that lists them,
    planned against their effective case (base or refined round), and pass
    :func:`compare_point` with the default tolerances.
    """
    root = Path(results_dir).resolve()
    policy = (base_payload["request"].get("policies") or {}).get("fr_protocol") or {}
    required = CHECK_PURPOSE in (policy.get("approval_checks") or [])
    record: dict = {
        "check": CHECK_PURPOSE,
        "required": required,
        "status": CHECK_STATUS_NOT_REQUIRED,
        "pass": None,
        "tolerances": {
            "gain_rel_change": DEFAULT_GAIN_TOL,
            "phase_change_deg": DEFAULT_PHASE_TOL_DEG,
            "h2_floor": DEFAULT_H2_FLOOR,
            "h2_band": list(DEFAULT_H2_BAND),
        },
        "selection": [],
        "attempts": [],
        "points": [],
        "problems": [],
    }
    if not required:
        return record
    try:
        if sources is None:
            sources, _rounds = refinement.resolve_case_sources(root, base_payload)
        if rows is None:
            rows = _aggregate_rows(root)
    except (OSError, refinement.RefinementError) as exc:
        record.update({"status": CHECK_STATUS_FAIL, "pass": False,
                       "problems": [f"cannot evaluate: {exc}"]})
        return record
    selection = select_check_points(base_payload, rows)
    record["selection"] = selection
    loaded, problems = _load_attempts(root, base_payload)
    record["attempts"] = [
        {
            "attempt": entry["attempt"],
            "directory": str(Path(entry["directory"]).relative_to(root)),
            "request_fingerprint": entry["payload"]["fingerprint"],
            "frequency_keys": sorted(sweep_manifest.case_map(entry["payload"])),
        }
        for entry in loaded
    ]
    if not loaded and not problems:
        directory = refinement.check_dir(root, CHECK_PURPOSE)
        record.update({"status": CHECK_STATUS_MISSING, "pass": False,
                       "problems": [f"no valid check request in {directory}"]})
        return record
    latest = _latest_by_key(loaded)
    missing = [item["frequency_key"] for item in selection if item["frequency_key"] not in latest]
    if missing:
        problems.append(
            "check run does not cover the required points: " + ", ".join(missing)
            + " (rerun the campaign checks after refinement)"
        )
    for item in selection:
        key = item["frequency_key"]
        entry = latest.get(key)
        source = sources.get(key)
        if entry is None or source is None:
            continue
        reason = _stale_reason(entry, key, source)
        if reason:
            problems.append(
                f"check point {key} (attempt {entry['attempt']}) was {reason} "
                "(rerun the campaign checks)"
            )
    points = []
    if not problems:
        for item in selection:
            key = item["frequency_key"]
            entry = latest[key]
            source = sources.get(key)
            if source is None:
                points.append({"frequency_key": key, "pass": False,
                               "problems": ["key not in the base request"]})
                continue
            point = compare_point(
                Path(source.results_dir), source.payload, Path(entry["directory"]),
                entry["payload"], key,
                gain_tol=DEFAULT_GAIN_TOL, phase_tol_deg=DEFAULT_PHASE_TOL_DEG,
                h2_floor=DEFAULT_H2_FLOOR, h2_band=DEFAULT_H2_BAND,
                base_request=base_payload,
            )
            point["attempt"] = entry["attempt"]
            point["reference_round"] = int(source.round_index)
            point["reference_amplitude_pcm"] = float(source.case["perturbation_amplitude_pcm"])
            point["reference_effective_target_swing"] = sweep_manifest.case_target_swing(
                source.payload["request"], source.case
            )
            points.append(point)
    record["points"] = points
    record["problems"] = problems
    passed = not problems and bool(points) and all(point.get("pass") for point in points)
    record["status"] = CHECK_STATUS_PASS if passed else CHECK_STATUS_FAIL
    record["pass"] = passed
    return record


def linearity_failures(record: dict) -> list[dict]:
    """Numerically failed points of an evaluated check record (the inputs of
    ``freq.fr_protocol.linearity_action``).

    ``kind`` is ``gain_phase`` (the gain or phase test failed; the H2 test
    may also have) or ``h2_only``.  A point rejected for provenance (no
    ``checks``) is not a numerical failure and is not listed.  Each entry
    carries the discrepancy (``gain_rel_change``, ``phase_change_deg``,
    ``linearity_excess``), the pair's settledness (``settled``,
    ``pair_excess``, both runs' ``convergence``), the reference amplitude
    and round, and the attempt.
    """
    out = []
    for point in record.get("points") or []:
        checks = point.get("checks") or {}
        if point.get("pass") or not checks:
            continue
        failed = [name for name in ("gain", "phase", "h2") if checks.get(name) is False]
        if not failed:
            continue
        fits = point.get("fits") or {}
        out.append(
            {
                "frequency_key": point["frequency_key"],
                "kind": "gain_phase" if {"gain", "phase"} & set(failed) else "h2_only",
                "failed": failed,
                "gain_rel_change": point.get("gain_rel_change"),
                "phase_change_deg": point.get("phase_change_deg"),
                "linearity_excess": point.get("linearity_excess"),
                "h2_status": point.get("h2_status"),
                "h2_ratio_half_over_reference": point.get("h2_ratio_half_over_reference"),
                "settled": bool(point.get("settled")),
                "pair_excess": point.get("pair_excess"),
                "convergence": {
                    label: (fits.get(label) or {}).get("convergence") for label in ("reference", "half")
                },
                "reference_amplitude_pcm": point.get("reference_amplitude_pcm"),
                "reference_round": point.get("reference_round"),
                "attempt": point.get("attempt"),
            }
        )
    return out


def _attempt_incomplete(directory: Path) -> bool:
    """A check attempt whose run did not finish: a plan but no published
    request, or a listed case without its result CSV."""
    if not (directory / refinement.PLAN_FILENAME).is_file():
        return False
    try:
        payload = sweep_manifest.load_sweep_request_manifest(directory)
    except sweep_manifest.SweepManifestError:
        return not (directory / sweep_manifest.SWEEP_REQUEST_FILENAME).exists()
    for case in payload["request"].get("cases") or []:
        _work, data_file = collector.case_result_paths(str(directory), float(case["frequency_rad_s"]))
        if not Path(data_file).is_file():
            return True
    return False


def run_check_stage(results_dir: str | Path, base_payload: dict, runner_cmd: list[str], *,
                    runner=None, sources: dict | None = None,
                    rows: list[dict] | None = None) -> dict:
    """Run the approval checks a sweep still lacks, then evaluate them.

    ``runner_cmd`` is the runner invocation of the base sweep without
    ``--base_dir`` / ``--refine_plan`` (``[python, -m,
    freq.runFreqNominalParallel, *args]``); ``runner(cmd) -> exit code``
    (default: :func:`run_command`).  An unfinished last attempt is rerun
    with its plan; the points without a current check get a new attempt.
    Returns the :func:`evaluate_campaign_checks` record plus ``ran`` (the
    attempts simulated) and ``runner_exit_code`` (nonzero: stopped).
    """
    runner = runner or run_command
    root = Path(results_dir).resolve()
    if sources is None:
        sources, _rounds = refinement.resolve_case_sources(root, base_payload)
    if rows is None:
        rows = _aggregate_rows(root)
    ran: list[dict] = []
    attempts = refinement.check_attempt_dirs(root, CHECK_PURPOSE)
    if attempts and _attempt_incomplete(attempts[-1][1]):
        attempt, directory = attempts[-1]
        plan_path = directory / refinement.PLAN_FILENAME
        code = runner([*runner_cmd, "--base_dir", str(directory), "--refine_plan", str(plan_path)])
        ran.append({"attempt": attempt, "directory": str(directory), "resumed": True,
                    "runner_exit_code": code})
        if code != 0:
            return {"ran": ran, "runner_exit_code": code, "pass": False,
                    "status": CHECK_STATUS_FAIL, "points": [], "problems": [
                        f"check attempt {attempt} failed to run (exit {code})"]}
    _selection, pending = pending_check_keys(root, base_payload, sources, rows)
    if pending:
        attempts = refinement.check_attempt_dirs(root, CHECK_PURPOSE)
        attempt = len(attempts) + 1
        if attempts and not (attempts[-1][1] / sweep_manifest.SWEEP_REQUEST_FILENAME).exists():
            attempt = attempts[-1][0]  # an attempt directory that never published
        plan = check_plan(root, base_payload, sources, pending, attempt=attempt)
        directory = refinement.plan_dir(plan)
        plan_path = refinement.write_plan(directory / refinement.PLAN_FILENAME, plan)
        code = runner([*runner_cmd, "--base_dir", str(directory), "--refine_plan", str(plan_path)])
        ran.append({"attempt": attempt, "directory": str(directory),
                    "frequency_keys": [item["frequency_key"] for item in pending],
                    "why": {item["frequency_key"]: item["why"] for item in pending},
                    "runner_exit_code": code})
        if code != 0:
            return {"ran": ran, "runner_exit_code": code, "pass": False,
                    "status": CHECK_STATUS_FAIL, "points": [], "problems": [
                        f"check attempt {attempt} failed to run (exit {code})"]}
    record = evaluate_campaign_checks(root, base_payload, sources=sources, rows=rows)
    record["ran"] = ran
    record["runner_exit_code"] = 0
    return record


def write_check_report(results_dir: str | Path, record: dict) -> Path:
    """Write the latest evaluation to ``checks/linearity_half_amplitude/``."""
    directory = refinement.check_dir(Path(results_dir).resolve(), CHECK_PURPOSE)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CHECK_REPORT_FILENAME
    path.write_text(json.dumps(record, indent=2, sort_keys=True, default=float) + "\n",
                    encoding="utf-8")
    return path


def campaign_main(args: argparse.Namespace, runner_args: list[str]) -> int:
    """Run the half-amplitude approval checks of one converged sweep."""
    root = Path(args.results_dir).expanduser().resolve()
    try:
        base_payload = _load_request(root)
        sources, _rounds = refinement.resolve_case_sources(root, base_payload)
        rows = _aggregate_rows(root)
    except (OSError, sweep_manifest.SweepManifestError, refinement.RefinementError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    cleaned: list[str] = []
    skip = False
    for index, token in enumerate(runner_args):
        if skip:
            skip = False
            continue
        if token == "--base_dir":
            if index + 1 >= len(runner_args) or (
                Path(runner_args[index + 1]).expanduser().resolve() != root
            ):
                print("ERROR: runner --base_dir must be the checked sweep", file=sys.stderr)
                return 2
            skip = True
            continue
        if token.startswith("--refine_plan") or token.startswith("--sin_mag_scale"):
            print(f"ERROR: runner argument {token} is set by the check", file=sys.stderr)
            return 2
        cleaned.append(token)
    try:
        if args.skip_run:
            record = evaluate_campaign_checks(root, base_payload, sources=sources, rows=rows)
        else:
            runner_cmd = [args.python, "-m", "freq.runFreqNominalParallel", *cleaned]

            def _run(cmd: list[str]) -> int:
                print("$ " + " ".join(cmd), flush=True)
                return run_command(cmd)

            record = run_check_stage(root, base_payload, runner_cmd, runner=_run,
                                     sources=sources, rows=rows)
    except refinement.RefinementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if record.get("runner_exit_code"):
        print(f"ERROR: half-amplitude check runs failed (exit {record['runner_exit_code']})",
              file=sys.stderr)
        return 1
    write_check_report(root, record)
    print(json.dumps(record, indent=2, sort_keys=True, default=float))
    return 0 if record["pass"] else 1


def run_command(cmd: list[str]) -> int:
    import subprocess

    return subprocess.run(cmd, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["campaign"]:
        own, runner_args = argv[1:], []
        if "--" in own:
            split = own.index("--")
            own, runner_args = own[:split], own[split + 1:]
        campaign = argparse.ArgumentParser(
            prog="linearity_check campaign",
            description=(
                "Approval checks of a converged (refined) sweep: rerun the band "
                "edges and the measured resonance point at half their effective "
                "amplitude with their effective discards into <dir>/checks/"
                f"{CHECK_PURPOSE} (later attempts: {CHECK_PURPOSE}"
                f"{refinement.CHECK_ATTEMPT_SUFFIX}NN; only points without a "
                "current check are simulated), then compare. Arguments after "
                "'--' are the base sweep's runner arguments."
            ),
        )
        campaign.add_argument("--results_dir", required=True)
        campaign.add_argument("--python", default=sys.executable)
        campaign.add_argument("--skip_run", action="store_true",
                              help="re-evaluate an existing check run without running it")
        return campaign_main(campaign.parse_args(own), runner_args)
    parser = argparse.ArgumentParser(
        description="Half-amplitude linearity self-check for frequency-response points."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "campaign",
        help=(
            "run and evaluate the approval checks of a converged sweep "
            "(see 'campaign --help')"
        ),
    )
    p_suggest = sub.add_parser("suggest", help="print the points and runner flags to rerun")
    p_suggest.add_argument("--reference", required=True, help="reference campaign directory")
    p_suggest.add_argument("--fr_prior", default="", help="gain prior (default: committed)")
    p_compare = sub.add_parser("compare", help="compare half-amplitude reruns with the reference")
    p_compare.add_argument("--reference", required=True, help="reference campaign directory")
    p_compare.add_argument("--half", required=True, nargs="+",
                           help="half-amplitude run directories (one or more)")
    p_compare.add_argument("--gain_tol", type=float, default=DEFAULT_GAIN_TOL)
    p_compare.add_argument("--phase_tol_deg", type=float, default=DEFAULT_PHASE_TOL_DEG)
    p_compare.add_argument("--h2_floor", type=float, default=DEFAULT_H2_FLOOR)
    p_compare.add_argument("--h2_band", type=float, nargs=2, default=list(DEFAULT_H2_BAND))
    p_compare.add_argument("--fit_trend", action="store_true",
                           help="force at least a linear trend term (drift-regime "
                                "points already use their recorded trend order)")
    p_compare.add_argument("--output", default="", help="write the JSON summary here")
    args = parser.parse_args(argv)

    if args.command == "suggest":
        for item in suggest_points(Path(args.reference), args.fr_prior or None):
            print(f"{item['frequency_key']}: {item['runner_flags']}")
            if item["note"]:
                print(f"  note: {item['note']}")
        return 0

    reference_dir = Path(args.reference).resolve()
    ref_request = _load_request(reference_dir)
    ref_keys = set(sweep_manifest.case_map(ref_request))
    points = []
    for half in args.half:
        half_dir = Path(half).resolve()
        half_request = _load_request(half_dir)
        for key in sorted(sweep_manifest.case_map(half_request)):
            if key not in ref_keys:
                points.append({"frequency_key": key, "pass": False,
                               "problems": [f"{half_dir}: key not in the reference campaign"]})
                continue
            points.append(
                compare_point(
                    reference_dir, ref_request, half_dir, half_request, key,
                    gain_tol=args.gain_tol, phase_tol_deg=args.phase_tol_deg,
                    h2_floor=args.h2_floor, h2_band=(args.h2_band[0], args.h2_band[1]),
                    fit_trend=args.fit_trend, scaled_sweep=True,
                )
            )
    summary = {
        "kind": "frequency-linearity-check",
        "reference": str(reference_dir),
        "tolerances": {
            "gain_rel_change": args.gain_tol,
            "phase_change_deg": args.phase_tol_deg,
            "h2_floor": args.h2_floor,
            "h2_band": list(args.h2_band),
        },
        "points": points,
        "pass": bool(points) and all(point.get("pass") for point in points),
    }
    text = json.dumps(summary, indent=2, sort_keys=True, default=float) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
