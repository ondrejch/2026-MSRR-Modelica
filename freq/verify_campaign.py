#!/usr/bin/env python3
"""Read-only verification summary for one frequency campaign directory.

The summary carries the four explicit verification fields introduced with
the rev014 review remediation (TASK-20260908-01 P1), alongside the legacy
label that the 24 existing verification reports were cut with:

- ``provenance_complete``: the report's own provenance is complete (kind,
  results directory, and the sweep campaign id/fingerprint are present);
- ``campaign_complete``: the sweep ran completely -- accepted == expected,
  no rejected/missing/unexpected points, no aggregate problems;
- ``numerical_quality_pass`` / ``numerical_quality``: the recorded fit
  statistics (minimum ``R_squared`` of the aggregate table and friends)
  evaluated against the owner threshold table (``helpers.numerical_quality``,
  owner decision O2), with an explicit waiver record field;
- ``publication_approved``: provenance AND campaign completeness AND no
  poison-feedback manifest uses a poison dataset whose recorded maturity
  is outside the approved subset (TASK-20260912-01 P5: pending-review data
  cannot pass publication even when numerical quality is green; the
  offending manifests are named in ``poison_data_problems``) AND every
  check the REQUEST's protocol makes mandatory evaluated to exactly
  ``True`` (rev032 review; :func:`protocol_requirements`): convergence and
  numerical quality under the ``prior`` settle rule, the realized-swing
  check when the target-swing rule is active, and every declared approval
  check.  A mandatory check that could not be evaluated (missing aggregate
  or column, unknown verdict, incomplete row coverage) fails; a check that
  does not apply may be ``None`` but never ``False``.  ``approval_gates``
  / ``approval_blockers`` name each gate;

- ``publication_eligible``: the LEGACY derived/provenance field, kept
  bit-for-bit compatible with the previously published reports so old and
  new summaries remain comparable. It means provenance + campaign
  completeness only and carries NO numerical-quality content; the README
  and the rebaseline evidence say the same.

- ``fit_convergence`` / ``fit_convergence_pass`` (FR protocol A1): the
  per-point settling check recorded by the collector (two halves of each
  fit window must agree, and the fitted mean power must sit at the
  nominal power), re-evaluated here from the recorded metrics against the
  documented default tolerances (``freq._common.CONVERGENCE_*``), never
  from the collector's own verdict alone.  Non-converged points are listed
  with the failed criteria; points whose settling discard the runner had
  to cap (requests older than the settle_prior_v2 rule) are listed
  separately (``settle_capped``).  Points whose gain is referenced to the
  window-mean power (drift regime) are checked against the documented
  drift operating-point bound instead of the nominal one (the REQUEST
  case decides; a relabeled aggregate row fails).  When convergence is not
  mandatory, an aggregate that predates the convergence columns evaluates
  to ``not_evaluated`` (pass None); when it is mandatory it fails.
  Non-convergence is numerical-quality content: it fails
  ``publication_approved`` but, by the contract above, never the legacy
  ``publication_eligible`` field.

- ``swing_check`` / ``swing_check_pass``: the realized swing of every
  point (``relative_swing``) against its effective target swing (the
  target-swing rule's, halved once per amplitude halving) and the band
  ``fr_protocol.SWING_BAND`` (clamped amplitudes exempt on their side),
  re-evaluated here; a failure blocks ``publication_approved``;
- ``approval_checks`` / ``approval_checks_pass``: the half-amplitude checks
  a base request declares (``policies.fr_protocol.approval_checks``),
  re-compared here from ``<results_dir>/checks/linearity_half_amplitude``
  and its later attempts ``..._attempt_NN``
  (``freq.linearity_check.evaluate_campaign_checks``); missing, stale, or
  failing checks block ``publication_approved``;
- ``refinement``: refinement rounds of ``freq.refine_sweep``
  (``<results_dir>/refine/round_NN``).  Every refined point is audited
  against its round's immutable request (freq/refinement.py), every round
  against the base request's parent identity (freq/sweep_manifest.py), and
  every accepted case manifest against the base request
  (``parent_identity_mismatch``); a malformed round fails the verification
  closed (``refinement.error``), and the refined points are listed with
  their final round and discard history.
- ``core_maturity`` / ``core_physical_data_maturity``: both maturity axes
  as the base request records them.

Default exit status is unchanged -- 0 only when ``publication_eligible``
holds, so existing dispatchers keyed on the exit code keep their
semantics. ``--exit_policy converged`` exits 0 only when
``publication_eligible`` holds AND the convergence check evaluated to pass
(``not_evaluated`` fails);
``--exit_policy publication_approved`` is the strict publication mode:
exit 0 only when ``publication_approved`` is true.
Do not overload the legacy ``publication_eligible`` meaning.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

try:
    from . import collectFreqNominalParallel as collector
    from . import linearity_check
    from . import refinement
    from . import sweep_manifest
    from .fr_protocol import (
        RELAXED_OPERATING_POINT_REFERENCES,
        SWING_BAND,
        swing_verdict,
    )
    from ._common import (
        CONVERGENCE_DRIFT_OPERATING_POINT_TOL,
        CONVERGENCE_GAIN_TOL,
        CONVERGENCE_OPERATING_POINT_TOL,
        CONVERGENCE_PHASE_TOL_DEG,
        CONVERGENCE_REASON_NOT_EVALUABLE,
        convergence_verdict,
    )
    from helpers import numerical_quality as nq
    from helpers.plant_config import POISON_MATURITY_APPROVED
except ImportError:
    import collectFreqNominalParallel as collector
    import linearity_check
    import refinement
    import sweep_manifest
    from fr_protocol import (
        RELAXED_OPERATING_POINT_REFERENCES,
        SWING_BAND,
        swing_verdict,
    )
    from _common import (
        CONVERGENCE_DRIFT_OPERATING_POINT_TOL,
        CONVERGENCE_GAIN_TOL,
        CONVERGENCE_OPERATING_POINT_TOL,
        CONVERGENCE_PHASE_TOL_DEG,
        CONVERGENCE_REASON_NOT_EVALUABLE,
        convergence_verdict,
    )
    from helpers import numerical_quality as nq
    from helpers.plant_config import POISON_MATURITY_APPROVED

#: Aggregate columns carrying the recorded convergence metrics.
CONVERGENCE_COLUMNS = (
    "conv_gain_rel_diff",
    "conv_phase_diff_deg",
    "operating_point_offset",
    "converged",
)

FIT_CONVERGENCE_PASS = "pass"
FIT_CONVERGENCE_FAIL = "fail"
FIT_CONVERGENCE_NOT_EVALUATED = "not_evaluated"
FIT_CONVERGENCE_NOT_APPLICABLE = "not_applicable"


def _provenance_complete(campaign_reference: dict, root: Path) -> bool:
    """The sweep's identifying provenance is present and non-empty.

    The sweep campaign id and fingerprint (the ``campaign`` reference block
    recorded in the summary) must both be present; the results directory is
    carried verbatim from the invocation.
    """

    block = campaign_reference if isinstance(campaign_reference, dict) else {}
    return bool(
        str(block.get("campaign_id") or "").strip()
        and str(block.get("fingerprint") or "").strip()
        and str(root).strip()
    )


def _poison_feedback_maturity_problems(
    root: Path, extra_dirs: list[Path] | None = None
) -> list[dict]:
    """Name campaign manifests whose poison feedback used unapproved data.

    TASK-20260912-01 P5: publication approval refuses poison-coupled
    results -- per-case manifests that enable poison feedback from a
    dataset whose recorded ``poisonMaturity`` is outside the approved
    subset
    (:data:`helpers.plant_config.POISON_MATURITY_APPROVED`), and fails
    closed when a feedback manifest records no maturity label at all (the
    P1 manifest contract makes the field mandatory on every poison-on
    run, so its absence cannot be assumed benign). Track-only poison
    manifests are development data, not poison-coupled results, and keep
    passing; the legacy ``publication_eligible`` field is untouched.
    """

    rr = collector._run_results()
    problems: list[dict] = []
    case_dirs = sorted(root.glob("freq*"))
    for extra in extra_dirs or []:
        case_dirs.extend(sorted(Path(extra).glob("freq*")))
    for directory in case_dirs:
        if not directory.is_dir():
            continue
        for csv_path in sorted(directory.glob("*_res.csv")):
            payload = rr.read_manifest_sidecar(csv_path)
            manifest = payload.get("manifest") if isinstance(payload, dict) else None
            overrides = manifest.get("overrides") if isinstance(manifest, dict) else None
            if not isinstance(overrides, dict):
                continue
            feedback = overrides.get("enablePoisonFeedback")
            feedback_on = feedback is True or (
                isinstance(feedback, str) and feedback.lower() == "true"
            )
            if not feedback_on:
                continue
            maturity = overrides.get("poisonMaturity")
            if not isinstance(maturity, str) or not maturity.strip():
                reason = (
                    "poison feedback manifest records no poisonMaturity "
                    "label; approval cannot be established (fail-closed)"
                )
            elif maturity not in POISON_MATURITY_APPROVED:
                reason = (
                    f"poison feedback used dataset maturity {maturity!r}, "
                    f"which is not in the approved set "
                    f"{sorted(POISON_MATURITY_APPROVED)}; pending-review "
                    "data cannot pass publication approval"
                )
            else:
                continue
            problems.append(
                {
                    "source_csv": str(csv_path),
                    "poison_dataset_id": overrides.get("poisonDatasetId"),
                    "poison_maturity": maturity,
                    "reason": reason,
                }
            )
    return problems


def _row_float(row: dict, name: str) -> float:
    try:
        return float(row.get(name, "nan"))
    except (TypeError, ValueError):
        return math.nan


def _row_bool(row: dict, name: str) -> bool:
    return str(row.get(name, "")).strip().lower() in ("true", "1", "yes")


#: Recorded verdict spellings the verifier understands; anything else in a
#: ``converged`` cell is an unknown verdict and fails the point.
_VERDICT_TRUE = ("true", "1", "yes")
_VERDICT_FALSE = ("false", "0", "no")

#: settle rule of the current protocol (convergence applies) and the rule id
#: whose aggregates must also carry the per-point estimator columns.
SETTLE_RULE_PRIOR = "prior"
SETTLE_RULE_ID_V2 = "settle_prior_v2"
#: Columns the current (settle_prior_v2) aggregates must carry in addition
#: to :data:`CONVERGENCE_COLUMNS`.
CONVERGENCE_ESTIMATOR_COLUMNS = ("gain_reference",)
#: Required as well when the request cases record their fit estimator
#: (lock-in regime, freq/lockin.py).
FIT_ESTIMATOR_COLUMN = "fit_estimator"
SWING_COLUMNS = ("relative_swing",)


def protocol_requirements(request: dict) -> dict:
    """Which checks a request's protocol makes mandatory for approval.

    - ``convergence``: the settling discard rule ``prior`` (every settle
      rule id; ``settle_prior_v2`` also requires the estimator columns);
    - ``swing``: the target-swing amplitude rule is active;
    - ``approval_checks``: the checks the base request declares;
    - ``quality``: the numerical-quality statistic, for current-protocol
      requests.
    Checks listed here must evaluate to exactly ``True`` for
    ``publication_approved``; checks that do not apply may be not evaluated
    (``None``) but never ``False``.
    """
    policy = ((request.get("request") or {}).get("policies") or {}).get("fr_protocol") or {}
    settle = policy.get("settle") if isinstance(policy.get("settle"), dict) else {}
    current = str(policy.get("settle_rule") or "") == SETTLE_RULE_PRIOR
    return {
        "convergence": current,
        "estimator_columns": current and str(settle.get("rule_id") or "") == SETTLE_RULE_ID_V2,
        "swing": bool(policy.get("target_swing_rule_active")),
        "approval_checks": [str(name) for name in policy.get("approval_checks") or []],
        "quality": current,
    }


def _read_aggregate(root: Path) -> tuple[list[str] | None, list[dict] | None, str | None]:
    aggregate = root / collector.AGGREGATE_CSV_FILENAME
    if not aggregate.is_file():
        return None, None, "no aggregate CSV"
    try:
        with aggregate.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        return None, None, f"unreadable aggregate: {exc}"
    return fieldnames, rows, None


def _row_key(row: dict) -> str:
    freq = _row_float(row, "frequency_rad_s")
    return (
        collector.format_frequency_key(freq)
        if math.isfinite(freq)
        else str(row.get("frequency_rad_s"))
    )


def _coverage_problems(rows: list[dict], expected_keys: set[str]) -> list[str]:
    """Every requested point exactly once, and nothing else."""
    seen: dict[str, int] = {}
    for row in rows:
        key = _row_key(row)
        seen[key] = seen.get(key, 0) + 1
    problems = []
    missing = sorted(expected_keys - set(seen), key=lambda k: float(k) if _is_number(k) else 0.0)
    if missing:
        problems.append("aggregate lacks rows for " + ", ".join(missing))
    extra = sorted(set(seen) - expected_keys)
    if extra:
        problems.append("aggregate has rows outside the request: " + ", ".join(extra))
    duplicated = sorted(key for key, count in seen.items() if count > 1)
    if duplicated:
        problems.append("aggregate repeats rows for " + ", ".join(duplicated))
    return problems


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _fit_convergence(
    root: Path,
    cases: dict,
    *,
    required: bool = False,
    estimator_columns: bool = False,
    expected_gain_reference: dict | None = None,
    expected_fit_estimator: dict | None = None,
) -> dict:
    """Re-evaluate the recorded per-point convergence metrics.

    Tolerances are the documented defaults, independent of the tolerances
    the collector was invoked with; disagreements with the collector's
    recorded verdict are listed (``collector_verdict_disagreements``).

    With ``required`` (the request's protocol makes convergence mandatory,
    :func:`protocol_requirements`) the check fails -- never "not evaluated"
    -- when the aggregate or any required column is missing, when a row
    carries an unknown recorded verdict, when the rows do not cover every
    requested point exactly once, or when a row's gain reference differs
    from its effective request case (``expected_gain_reference``); the
    drift operating-point bound applies only to rows the REQUEST marks as
    window-mean or local-mean referenced.  ``expected_fit_estimator`` (keys
    of request cases that record ``fit_estimator``) additionally requires the
    aggregate's ``fit_estimator`` column and a matching row per key.
    """
    tolerances = {
        "gain_rel_diff": CONVERGENCE_GAIN_TOL,
        "phase_diff_deg": CONVERGENCE_PHASE_TOL_DEG,
        "operating_point_offset": CONVERGENCE_OPERATING_POINT_TOL,
        "operating_point_offset_window_mean_reference": (
            CONVERGENCE_DRIFT_OPERATING_POINT_TOL
        ),
        "operating_point_offset_local_mean_reference": (
            CONVERGENCE_DRIFT_OPERATING_POINT_TOL
        ),
    }
    settle_capped = sorted(
        key for key, case in cases.items() if bool(case.get("settle_capped"))
    )
    record: dict = {
        "status": FIT_CONVERGENCE_NOT_APPLICABLE,
        "pass": None,
        "required": bool(required),
        "tolerances": tolerances,
        "points": 0,
        "converged": 0,
        "non_converged": [],
        "collector_verdict_disagreements": [],
        "problems": [],
        "settle_capped": settle_capped,
    }

    def failed(note: str) -> dict:
        record.update({"status": FIT_CONVERGENCE_FAIL, "pass": False, "note": note})
        record["problems"].append(note)
        return record

    fieldnames, rows, error = _read_aggregate(root)
    if error is not None:
        if required or error != "no aggregate CSV":
            return failed(error)
        record["note"] = error
        return record
    required_columns = list(CONVERGENCE_COLUMNS) + (
        list(CONVERGENCE_ESTIMATOR_COLUMNS) if estimator_columns else []
    ) + ([FIT_ESTIMATOR_COLUMN] if expected_fit_estimator else [])
    missing = [name for name in required_columns if name not in fieldnames]
    if missing:
        note = "aggregate lacks the convergence columns: " + ", ".join(missing)
        if required:
            return failed(note)
        record.update(
            {
                "status": FIT_CONVERGENCE_NOT_EVALUATED,
                "note": "aggregate predates the convergence metrics (missing "
                "columns: " + ", ".join(missing) + ")",
            }
        )
        return record
    problems = _coverage_problems(rows, set(cases)) if required else []
    non_converged: list[dict] = []
    disagreements: list[str] = []
    for row in rows:
        freq = _row_float(row, "frequency_rad_s")
        key = _row_key(row)
        gain_diff = _row_float(row, "conv_gain_rel_diff")
        phase_diff = _row_float(row, "conv_phase_diff_deg")
        op_offset = _row_float(row, "operating_point_offset")
        recorded_reason = str(row.get("convergence_reason") or "")
        metrics = {
            "evaluated": math.isfinite(gain_diff) and math.isfinite(phase_diff),
            "gain_rel_diff": gain_diff,
            "phase_diff_deg": phase_diff,
            "reason": recorded_reason,
        }
        row_reference = str(row.get("gain_reference") or "")
        if expected_gain_reference is not None and key in expected_gain_reference:
            authority_reference = str(expected_gain_reference[key])
        else:
            authority_reference = row_reference
        window_mean_reference = authority_reference in RELAXED_OPERATING_POINT_REFERENCES
        converged, reason = convergence_verdict(
            metrics,
            operating_point_offset=(op_offset if math.isfinite(op_offset) else None),
            gain_tol=tolerances["gain_rel_diff"],
            phase_tol_deg=tolerances["phase_diff_deg"],
            operating_point_tol=(
                tolerances["operating_point_offset_window_mean_reference"]
                if window_mean_reference
                else tolerances["operating_point_offset"]
            ),
        )
        if not metrics["evaluated"] and not reason.startswith(CONVERGENCE_REASON_NOT_EVALUABLE):
            reason = f"{CONVERGENCE_REASON_NOT_EVALUABLE}: {recorded_reason}"
        reasons = [reason] if not converged else []
        verdict_text = str(row.get("converged", "")).strip().lower()
        if verdict_text not in _VERDICT_TRUE + _VERDICT_FALSE:
            reasons.append(f"unknown_verdict({row.get('converged')!r})")
        elif (verdict_text in _VERDICT_TRUE) != converged:
            disagreements.append(key)
        if (
            required
            and expected_gain_reference is not None
            and estimator_columns
            and row_reference != authority_reference
        ):
            reasons.append(
                f"gain_reference_mismatch(aggregate {row_reference!r}, "
                f"request {authority_reference!r})"
            )
        if expected_fit_estimator and key in expected_fit_estimator:
            row_estimator = str(row.get(FIT_ESTIMATOR_COLUMN) or "")
            if row_estimator != str(expected_fit_estimator[key]):
                reasons.append(
                    f"fit_estimator_mismatch(aggregate {row_estimator!r}, "
                    f"request {expected_fit_estimator[key]!r})"
                )
        if reasons:
            non_converged.append(
                {
                    "frequency_key": key,
                    "frequency_rad_s": freq if math.isfinite(freq) else None,
                    "reason": ";".join(reasons),
                    # NaN (not evaluable) is reported as null (strict JSON).
                    "conv_gain_rel_diff": gain_diff if math.isfinite(gain_diff) else None,
                    "conv_phase_diff_deg": phase_diff if math.isfinite(phase_diff) else None,
                    "operating_point_offset": op_offset if math.isfinite(op_offset) else None,
                    "settle_capped": _row_bool(row, "settle_capped"),
                    "settle_regime": str(row.get("settle_regime") or ""),
                    "gain_reference": row_reference,
                    "settle_discard_s": (
                        _row_float(row, "settle_discard_s")
                        if math.isfinite(_row_float(row, "settle_discard_s"))
                        else None
                    ),
                    "refine_round": str(row.get("refine_round") or "0"),
                }
            )
    passed = not non_converged and not problems
    record.update(
        {
            "status": FIT_CONVERGENCE_PASS if passed else FIT_CONVERGENCE_FAIL,
            "pass": passed,
            "points": len(rows),
            "converged": len(rows) - len(non_converged),
            "non_converged": non_converged,
            "collector_verdict_disagreements": disagreements,
            "problems": problems,
        }
    )
    return record


def _swing_check(root: Path, request: dict, audits: list, cases: dict | None = None,
                 sources: dict | None = None) -> dict:
    """Re-evaluate the realized swing of every aggregate row (target-swing rule).

    Independent of the collector's verdict: ``relative_swing`` from the
    aggregate, the target and clamp status from each point's effective
    REQUEST case (``effective_target_swing``: the policy target halved once
    per amplitude halving; the policy target for requests that predate the
    field), and the documented band ``fr_protocol.SWING_BAND``.
    When the rule is active the check fails (never "not evaluated") on a
    missing aggregate or column, a nonfinite swing, or rows that do not
    cover every requested point exactly once.
    """
    policy = (request["request"].get("policies") or {}).get("fr_protocol") or {}
    target = policy.get("target_swing") if policy.get("target_swing_rule_active") else None
    record: dict = {
        "applicable": target is not None,
        "target_swing": float(target) if target is not None else None,
        "band": list(SWING_BAND),
        "status": FIT_CONVERGENCE_NOT_APPLICABLE,
        "pass": None,
        "failed_points": [],
        "problems": [],
    }
    if target is None:
        return record
    fieldnames, rows, error = _read_aggregate(root)
    if error is not None:
        record.update({"status": FIT_CONVERGENCE_FAIL, "pass": False, "note": error,
                       "problems": [error]})
        return record
    missing = [name for name in SWING_COLUMNS if name not in fieldnames]
    if missing:
        note = "aggregate lacks the swing columns: " + ", ".join(missing)
        record.update({"status": FIT_CONVERGENCE_FAIL, "pass": False, "note": note,
                       "problems": [note]})
        return record
    problems = _coverage_problems(rows, set(cases)) if cases is not None else []
    # Clamp exemptions come from the effective REQUEST case (rev033
    # review): explicit ``amplitude_clamped``, or derived from the request
    # for requests that predate the field; never from the case manifest.
    # The target is the effective REQUEST case's (the policy target halved
    # once per amplitude halving; the policy target for requests that
    # predate the field), never the aggregate's own column.
    clamps = {}
    targets = {}
    for key, case in (cases or {}).items():
        source = (sources or {}).get(key)
        effective_case = source.case if source is not None else case
        effective_request = source.payload["request"] if source is not None else request["request"]
        clamp = sweep_manifest.case_amplitude_clamp(effective_request, effective_case)
        clamps[key] = None if clamp == sweep_manifest.CLAMP_UNKNOWN else clamp
        targets[key] = sweep_manifest.case_target_swing(effective_request, effective_case)
    failed = []
    for row in rows:
        key = _row_key(row)
        case_target = targets.get(key, float(target))
        in_band, ratio = swing_verdict(
            _row_float(row, "relative_swing"),
            float(case_target) if case_target is not None else math.nan,
            clamped=clamps.get(key),
        )
        if not in_band:
            failed.append(
                {
                    "frequency_key": key,
                    "relative_swing": (
                        _row_float(row, "relative_swing")
                        if math.isfinite(_row_float(row, "relative_swing")) else None
                    ),
                    "effective_target_swing": case_target,
                    "swing_ratio": ratio if math.isfinite(ratio) else None,
                    "amplitude_clamped": clamps.get(key),
                }
            )
    passed = not failed and not problems
    record.update(
        {
            "status": FIT_CONVERGENCE_PASS if passed else FIT_CONVERGENCE_FAIL,
            "pass": passed,
            "points": len(rows),
            "failed_points": failed,
            "halved_points": {
                key: value for key, value in sorted(targets.items())
                if value is not None and not math.isclose(float(value), float(target), rel_tol=1e-12)
            },
            "problems": problems,
        }
    )
    return record


def _check_verdict(value, required: bool) -> bool:
    """Approval gate: exactly ``True`` when required, never ``False`` otherwise."""
    return value is True if required else value is not False


def verify_campaign(results_dir: str) -> dict:
    root = Path(results_dir).resolve()
    request = sweep_manifest.load_sweep_request_manifest(root)
    cases = sweep_manifest.case_map(request)
    refinement_error: str | None = None
    try:
        sources, rounds = refinement.resolve_case_sources(root, request)
    except refinement.RefinementError as exc:
        # A malformed refinement round fails closed: audit the base cases
        # only and block publication below.
        refinement_error = str(exc)
        sources, rounds = {}, []
    audits = []
    for key, case in cases.items():
        source = sources.get(key)
        source_case = source.case if source is not None else case
        audits.append(
            collector.audit_case(
                float(source_case["frequency_rad_s"]),
                source.results_dir if source is not None else str(root),
                float(source_case["perturbation_amplitude_pcm"]),
                allow_legacy=False,
                request_manifest=source.payload if source is not None else request,
                expected_stop_time=float(source_case["stop_time_s"]),
                expected_intervals=int(source_case["number_of_intervals"]),
                expected_fit_start=source_case.get("fit_start_s"),
            )
        )
    collector.enforce_sweep_common_fields(
        audits, ignore_fields=("sweep_request",) if rounds else ()
    )
    collector.enforce_parent_identity(audits, request)

    expected_keys = set(cases)
    unexpected: list[dict] = []
    for directory in sorted(root.glob("freq*")):
        if not directory.is_dir():
            continue
        # Same resolution order as the collector's unexpected-case detection
        # (shared helper): sidecar overrides first, then the directory-name
        # fallback, so the verifier cannot miss a case the collector flags.
        omega, source_csv = collector.case_directory_frequency(directory)
        if omega is None:
            continue
        key = collector.format_frequency_key(omega)
        if key not in expected_keys:
            unexpected.append(
                {
                    "frequency_key": key,
                    "frequency_rad_s": omega,
                    "source_csv": str(source_csv),
                }
            )

    for info in rounds:
        for directory in sorted(info.directory.glob("freq*")):
            if not directory.is_dir():
                continue
            omega, source_csv = collector.case_directory_frequency(directory)
            if omega is None:
                continue
            key = collector.format_frequency_key(omega)
            if key not in info.keys:
                unexpected.append(
                    {
                        "frequency_key": key,
                        "frequency_rad_s": omega,
                        "source_csv": str(source_csv),
                        "refinement_round": int(info.index),
                    }
                )

    aggregate_problems = collector.verify_aggregate_outputs(str(root))
    if refinement_error is not None:
        aggregate_problems = [
            *aggregate_problems,
            f"refinement rounds are not a valid authority: {refinement_error}",
        ]
    poison_data_problems = _poison_feedback_maturity_problems(
        root, [info.directory for info in rounds]
    )
    accepted = sum(1 for audit in audits if audit.accepted)
    summary = {
        "schema_version": 1,
        "kind": "frequency-campaign-verification",
        "campaign": sweep_manifest.case_reference(request),
        "results_dir": str(root),
        "expected": len(audits),
        "accepted": accepted,
        "missing": [
            audit.frequency_key
            for audit in audits
            if audit.status == collector.CASE_MISSING_CSV
        ],
        "rejected": [
            audit.to_dict() for audit in audits if not audit.accepted
        ],
        "unexpected": unexpected,
        "aggregate_problems": aggregate_problems,
        "poison_data_problems": poison_data_problems,
        # Both maturity axes of the core (rev032 review), as the base
        # request records them (None for requests that predate the fields).
        "core_maturity": request["request"].get("core_maturity"),
        "core_physical_data_maturity": request["request"].get(
            "core_physical_data_maturity"
        ),
        "refinement": {
            "error": refinement_error,
            "rounds": [info.summary(root) for info in rounds],
            "refined_points": [
                {
                    "frequency_key": key,
                    "final_round": int(source.round_index),
                    "final_settle_discard_s": float(
                        source.history[-1]["settle_discard_s"]
                    ),
                    "settle_discard_history_s": [
                        float(entry["settle_discard_s"]) for entry in source.history
                    ],
                    "amplitude_history_pcm": [
                        float(entry["perturbation_amplitude_pcm"]) for entry in source.history
                    ],
                    "final_effective_target_swing": source.history[-1].get(
                        "effective_target_swing"
                    ),
                    "amplitude_halvings": source.history[-1].get("amplitude_halvings", 0),
                }
                for key, source in sorted(
                    sources.items(),
                    key=lambda item: float(item[1].case["frequency_rad_s"]),
                )
                if source.refined
            ],
        },
    }
    # Legacy derived/provenance field, kept bit-for-bit compatible with the
    # previously published verification reports (provenance + campaign
    # completeness; NO numerical-quality content). The exit status is keyed
    # on it, so its semantics must not change silently.
    summary["publication_eligible"] = (
        accepted == len(audits)
        and not summary["rejected"]
        and not unexpected
        and not aggregate_problems
    )
    # Explicit split fields (rev014 review remediation, TASK-20260908-01 P1).
    summary["provenance_complete"] = _provenance_complete(
        sweep_manifest.case_reference(request), root
    )
    summary["campaign_complete"] = (
        accepted == len(audits)
        and not summary["missing"]
        and not summary["rejected"]
        and not unexpected
        and not aggregate_problems
    )
    try:
        quality = nq.evaluate_quality(
            nq.read_aggregate_fit_statistics(root / "FreqResponseResults.csv"),
            regime=nq.DEFAULT_QUALITY_REGIME,
            scope=str(root),
            waivers=(),
        )
    except nq.QualityError as exc:
        # A corrupt/unreadable aggregate (or an unknown quality regime) is a
        # numerical-quality FAILURE REPORT, not a verifier crash: record the
        # explicit failure so publication approval fails closed and the
        # offending aggregate is named in the summary.
        quality = {
            "regime": nq.DEFAULT_QUALITY_REGIME,
            "status": nq.QUALITY_STATUS_FAIL,
            "pass": False,
            "min_r_squared": None,
            "points": None,
            "thresholds": None,
            "waiver": None,
            "error": str(exc),
        }
    summary["numerical_quality"] = quality
    summary["numerical_quality_status"] = quality["status"]
    summary["numerical_quality_pass"] = quality["pass"]
    # FR protocol A1: settling/convergence check (numerical-quality content;
    # never folded into the legacy publication_eligible field).
    requirements = protocol_requirements(request)
    summary["approval_requirements"] = requirements
    estimator_cases = (
        {key: source.case for key, source in sources.items()} if sources else dict(cases)
    )
    convergence = _fit_convergence(
        root,
        cases,
        required=requirements["convergence"],
        estimator_columns=requirements["estimator_columns"],
        expected_fit_estimator={
            key: str(case["fit_estimator"])
            for key, case in estimator_cases.items()
            if case.get("fit_estimator")
        } or None,
        expected_gain_reference=(
            {
                key: str(source.case.get("gain_reference") or "nominal_power")
                for key, source in sources.items()
            }
            if sources
            else {
                key: str(case.get("gain_reference") or "nominal_power")
                for key, case in cases.items()
            }
        ),
    )
    summary["fit_convergence"] = convergence
    summary["fit_convergence_status"] = convergence["status"]
    summary["fit_convergence_pass"] = convergence["pass"]
    # Realized swing (target-swing rule; the prior gains are not trusted) and
    # the half-amplitude approval checks the base request declares.
    swing = _swing_check(root, request, audits, cases, sources)
    summary["swing_check"] = swing
    summary["swing_check_pass"] = swing["pass"]
    approval = linearity_check.evaluate_campaign_checks(
        root, request, sources=sources or None
    ) if refinement_error is None else {
        "check": linearity_check.CHECK_PURPOSE,
        "required": True,
        "status": linearity_check.CHECK_STATUS_FAIL,
        "pass": False,
        "problems": ["refinement rounds are not a valid authority"],
    }
    unknown_checks = [
        name for name in requirements["approval_checks"]
        if name != linearity_check.CHECK_PURPOSE
    ]
    if unknown_checks:
        approval = dict(approval)
        approval.update(
            {
                "pass": False,
                "status": linearity_check.CHECK_STATUS_FAIL,
                "problems": [
                    *(approval.get("problems") or []),
                    "the request declares approval checks this verifier cannot "
                    "evaluate: " + ", ".join(unknown_checks),
                ],
            }
        )
    summary["approval_checks"] = approval
    summary["approval_checks_pass"] = approval["pass"]
    # Request-aware approval (rev032 review): every check the request's
    # protocol makes mandatory must be exactly True -- "not evaluated"
    # (None) no longer passes; checks that do not apply may be None but
    # never False.
    gates = {
        "provenance_complete": bool(summary["provenance_complete"]),
        "campaign_complete": bool(summary["campaign_complete"]),
        "numerical_quality": _check_verdict(quality["pass"], requirements["quality"]),
        "fit_convergence": _check_verdict(convergence["pass"], requirements["convergence"]),
        "swing_check": _check_verdict(swing["pass"], requirements["swing"]),
        "approval_checks": _check_verdict(
            approval["pass"], bool(requirements["approval_checks"])
        ),
        # TASK-20260912-01 P5: pending-review poison data cannot pass
        # publication approval, even when numerical quality is green.
        "poison_data": not poison_data_problems,
    }
    summary["approval_gates"] = gates
    summary["approval_blockers"] = sorted(name for name, ok in gates.items() if not ok)
    summary["publication_approved"] = all(gates.values())
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--exit_policy",
        choices=("legacy_eligible", "converged", "publication_approved"),
        default="legacy_eligible",
        help=(
            "legacy_eligible (default): exit 0 iff publication_eligible "
            "(provenance + campaign completeness, no numerical quality). "
            "converged: exit 0 iff publication_eligible and the fit-"
            "convergence (settling) check evaluated to pass (not evaluated "
            "fails). "
            "publication_approved: exit 0 iff publication_approved is true."
        ),
    )
    args = parser.parse_args(argv)
    payload = verify_campaign(args.results_dir)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.exit_policy == "publication_approved":
        return 0 if payload["publication_approved"] else 1
    if args.exit_policy == "converged":
        # Exactly True: a sweep whose convergence was not evaluated fails.
        return (
            0
            if payload["publication_eligible"]
            and payload["fit_convergence_pass"] is True
            else 1
        )
    return 0 if payload["publication_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
