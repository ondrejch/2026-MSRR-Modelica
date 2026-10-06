#!/usr/bin/env python3
"""Immutable request manifests for frequency-response campaigns.

The request manifest is written before worker dispatch and is the authority
for campaign completeness.  ``run_params.txt`` and the mapping CSV files are
human-readable derivatives only.

The module also owns the canonical parent identity (``request_identity``,
``case_identity``, ``manifest_identity``, ``manifest_request_differences``)
that binds refinement rounds, approval-check runs, and every accepted case
manifest to the base sweep (see the section at the end of this file and
freq/README.md, "Parent identity").
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SWEEP_REQUEST_FILENAME = "sweep_request.manifest.json"
SWEEP_REQUEST_SCHEMA_VERSION = 1
SWEEP_REQUEST_FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"

# Bounded settle window for a concurrent winner that has created the
# manifest path but not yet atomically installed its content (zero-length
# placeholder). Persistently invalid manifests still fail closed after it.
_PUBLISH_SETTLE_TIMEOUT_S = 2.0
_PUBLISH_SETTLE_POLL_S = 0.01


class SweepManifestError(ValueError):
    """Raised when a sweep request manifest is absent, malformed, or altered."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_fingerprint(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()


def build_sweep_request_manifest(
    request: Mapping[str, Any],
    *,
    campaign_id: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = json.loads(canonical_json(dict(request)))
    fingerprint = request_fingerprint(normalized)
    return {
        "schema_version": SWEEP_REQUEST_SCHEMA_VERSION,
        "kind": "frequency-sweep-request",
        "campaign_id": campaign_id or f"sweep-{uuid.uuid4().hex}",
        "created_at_utc": created_at_utc
        or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "fingerprint_algorithm": SWEEP_REQUEST_FINGERPRINT_ALGORITHM,
        "fingerprint": fingerprint,
        "request": normalized,
    }


#: Per-case numeric fields that must convert to finite floats (checked when
#: present; ``fit_start_s`` / ``settle_discard_s`` / ``fit_cycles`` are the
#: settling-discard fit-start authority written by the FR protocol and are
#: absent from requests that predate it).
_CASE_NUMERIC_FIELDS = (
    "frequency_rad_s",
    "perturbation_amplitude_pcm",
    "stop_time_s",
    "output_step_s",
    "fit_start_s",
    "settle_discard_s",
    "fit_cycles",
)

#: Request-level numeric fields checked for finiteness when present.
_REQUEST_NUMERIC_FIELDS = (
    "power",
    "perturbation_start_time_s",
)


def _require_finite(case_ref: str, field: str, value: Any) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SweepManifestError(
            f"{case_ref}: field {field!r} is not a number ({value!r})"
        ) from exc
    if not math.isfinite(number):
        raise SweepManifestError(
            f"{case_ref}: field {field!r} must be finite (got {value!r})"
        )


def validate_sweep_request_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SWEEP_REQUEST_SCHEMA_VERSION:
        raise SweepManifestError(
            "unsupported sweep request schema_version "
            f"{payload.get('schema_version')!r}; expected "
            f"{SWEEP_REQUEST_SCHEMA_VERSION}"
        )
    if payload.get("kind") != "frequency-sweep-request":
        raise SweepManifestError("not a frequency-sweep-request manifest")
    request = payload.get("request")
    if not isinstance(request, dict):
        raise SweepManifestError("sweep request manifest lacks an object 'request'")
    recorded = str(payload.get("fingerprint") or "").strip().lower()
    computed = request_fingerprint(request)
    if recorded != computed:
        raise SweepManifestError(
            "sweep request fingerprint mismatch (manifest edited or corrupted)"
        )
    campaign_id = str(payload.get("campaign_id") or "").strip()
    if not campaign_id:
        raise SweepManifestError("sweep request manifest lacks campaign_id")
    for field in _REQUEST_NUMERIC_FIELDS:
        if request.get(field) is not None:
            _require_finite("request", field, request[field])
    cases = request.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SweepManifestError("sweep request contains no cases")
    keys: list[str] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise SweepManifestError(f"request case {index} is not an object")
        key = str(case.get("frequency_key") or "").strip()
        if not key:
            raise SweepManifestError(f"request case {index} lacks frequency_key")
        if key in keys:
            raise SweepManifestError(f"duplicate requested frequency key {key!r}")
        keys.append(key)
        for field in (
            "frequency_rad_s",
            "perturbation_amplitude_pcm",
            "stop_time_s",
            "number_of_intervals",
        ):
            if case.get(field) is None:
                raise SweepManifestError(
                    f"request case {key!r} lacks required field {field}"
                )
        for field in _CASE_NUMERIC_FIELDS:
            if case.get(field) is not None:
                _require_finite(f"request case {key!r}", field, case[field])
    return dict(payload)


def load_sweep_request_manifest(
    results_dir: str | os.PathLike[str],
) -> dict[str, Any]:
    path = Path(results_dir) / SWEEP_REQUEST_FILENAME
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise SweepManifestError(f"missing {SWEEP_REQUEST_FILENAME}") from None
    except (OSError, ValueError) as exc:
        raise SweepManifestError(
            f"cannot read {SWEEP_REQUEST_FILENAME}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SweepManifestError(f"{SWEEP_REQUEST_FILENAME} is not a JSON object")
    return validate_sweep_request_manifest(payload)


def _write_exclusive(path: Path, descriptor: int, text: str) -> None:
    """Install manifest bytes behind an O_EXCL-acquired descriptor atomically.

    The O_EXCL create makes the final path visible to concurrent readers
    before the bytes land, so the content is written to a same-directory
    temp file and moved onto the final name with ``os.replace`` (atomic on
    POSIX): a concurrent reader can only observe the zero-length
    placeholder or the complete manifest, never a partial JSON document.
    A failed install removes the (partial) temp and placeholder best effort
    so the directory is not poisoned with an invalid authority; a hard kill
    can still leave one behind, which the fail-closed read below refuses.
    """

    temp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        for victim in (temp, path):
            try:
                victim.unlink()
            except OSError:
                pass
        raise
    finally:
        os.close(descriptor)


def publish_sweep_request_manifest(
    results_dir: str | os.PathLike[str],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish a request before dispatch, retaining identity for exact reruns.

    The manifest is immutable campaign authority.  It is created with an
    atomic exclusive create (``os.open`` with ``O_CREAT | O_EXCL``), so two
    concurrent launches into a fresh directory cannot both win: exactly one
    create succeeds, and the loser falls back to reading and comparing the
    winner's manifest (rev022 M-2).  An existing manifest with a DIFFERENT
    request fingerprint is refused instead of silently replaced; a rerun
    with the identical request stays idempotent (returns the recorded
    manifest).  An existing manifest that cannot be read or validated is
    also refused (fail-closed) -- remove the file explicitly to start a new
    campaign in the same directory.
    """
    path = Path(results_dir) / SWEEP_REQUEST_FILENAME
    candidate = build_sweep_request_manifest(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pass  # a concurrent launch won the create; compare its manifest
        else:
            _write_exclusive(
                path, descriptor, json.dumps(candidate, indent=2, sort_keys=True) + "\n"
            )
            return candidate
    deadline = time.monotonic() + _PUBLISH_SETTLE_TIMEOUT_S
    first_error: SweepManifestError | None = None
    while True:
        try:
            existing = load_sweep_request_manifest(results_dir)
            break
        except SweepManifestError as exc:
            # The create can be visible to us before the winner's bytes are
            # (atomically) installed; settle briefly before failing closed.
            first_error = first_error or exc
            if time.monotonic() >= deadline:
                raise SweepManifestError(
                    f"existing {SWEEP_REQUEST_FILENAME} in {results_dir} is not a "
                    f"valid campaign authority ({first_error}); refusing to "
                    "replace it -- remove the file explicitly to start a new "
                    "campaign"
                ) from exc
            time.sleep(_PUBLISH_SETTLE_POLL_S)
    if existing["fingerprint"] == candidate["fingerprint"]:
        return existing
    raise SweepManifestError(
        f"existing {SWEEP_REQUEST_FILENAME} in {results_dir} records a "
        f"different request (recorded fingerprint "
        f"{str(existing['fingerprint'])[:12]}..., new request fingerprint "
        f"{candidate['fingerprint'][:12]}...); refusing to replace it -- "
        "point the new request at a fresh campaign directory"
    )


def case_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    validated = validate_sweep_request_manifest(payload)
    return {
        str(item["frequency_key"]): dict(item)
        for item in validated["request"]["cases"]
    }


def case_reference(payload: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_sweep_request_manifest(payload)
    return {
        "schema_version": validated["schema_version"],
        "campaign_id": validated["campaign_id"],
        "fingerprint": validated["fingerprint"],
    }


def verify_case_reference(
    case_manifest: Mapping[str, Any], request_manifest: Mapping[str, Any]
) -> str | None:
    expected = case_reference(request_manifest)
    actual = case_manifest.get("sweep_request")
    if not isinstance(actual, dict):
        return "case manifest lacks sweep_request identity"
    differing = [
        key for key, value in expected.items()
        if actual.get(key) != value
    ]
    if differing:
        return "case belongs to a foreign sweep request: " + ", ".join(differing)
    return None


# ---------------------------------------------------------------------------
# Canonical parent identity (refinement rounds, approval checks, collection)
# ---------------------------------------------------------------------------
#
# A refinement round (``<dir>/refine/round_NN``) and a half-amplitude
# approval check (``<dir>/checks/<purpose>``) are separate sweeps with their
# own immutable requests.  They may differ from their parent (base) request
# ONLY in the fields listed here as authorized; everything else -- sources,
# workflow Python, Git state, interpreter and OpenModelica versions, model
# and setpoint identity, numerics, every sampling / forcing / estimator
# policy, the per-case estimator settings -- is the parent identity and must
# be identical.  The same projection is applied before any simulation (the
# runner's ``--refine_plan`` mode) and again at collection and verification
# time (freq/refinement.py, freq/linearity_check.py, the collector, and
# freq/verify_campaign.py), so a round or check produced from a different
# checkout, setpoint table, tolerance, or estimator policy can never stand in
# for, or certify, the base sweep.

#: Request fields outside the parent identity: the case list (compared per
#: case), the plan blocks that make a request a round or check, and the
#: display path of the setpoint table (its SHA-256 and model version ARE
#: identity).
REQUEST_IDENTITY_EXCLUDED = ("cases", "refinement", "check", "setpoint_table_path")

#: Dotted request paths that summarize the case subset or name a file
#: location rather than its content (the prior's SHA-256 and id stay
#: identity).  ``approval_checks`` is declared by base sweeps only.
REQUEST_IDENTITY_DERIVED = (
    "policies.fr_protocol.fit_start_s",
    "policies.fr_protocol.fit_start_range_s",
    "policies.fr_protocol.settle_regime_counts",
    "policies.fr_protocol.amplitude_clamped_points",
    "policies.fr_protocol.approval_checks",
    "policies.fr_protocol.prior.path",
    "policies.fr_protocol.settle.prior.path",
)

#: Per-case request fields a round or check may change: the discard and
#: everything that follows from it (fit start, stop time, output grid, row
#: count), the amplitude, its clamp, effective target swing, and halving
#: count (a recorded measured-gain rescale, an amplitude halving, or the
#: half amplitude of a check), and the refinement round counter.  The
#: regime, trend order, gain reference, fit-window length, forcing cadence,
#: and rule discard are identity.
CASE_AUTHORIZED_FIELDS = (
    "perturbation_amplitude_pcm",
    "amplitude_clamped",
    "effective_target_swing",
    "amplitude_halvings",
    "solver_tolerance",
    "stop_time_s",
    "number_of_intervals",
    "output_step_s",
    "fit_start_s",
    "settle_discard_s",
    "settle_refine_round",
    "predicted_result_rows",
    "output_intervals_capped",
)

#: Case-manifest paths a round or check may change (same meaning as
#: :data:`CASE_AUTHORIZED_FIELDS`, plus the volatile launch time and the
#: case's own request reference).
MANIFEST_AUTHORIZED_PATHS = (
    "launch_timestamp_utc",
    # Per-case tolerance (tolerance_swing_scaled_v2): bound to the request
    # by manifest_request_differences (tolerance = request tolerance x the
    # case's recorded scale) and to the request case by the case binding.
    "tolerance",
    "fr_protocol.solver_tolerance_case",
    "sweep_request",
    "stop_time",
    "number_of_intervals",
    "perturbation_amplitude",
    "fr_protocol.settle.settle_discard_s",
    "fr_protocol.settle.fit_start_s",
    "fr_protocol.settle.stop_time_s",
    "fr_protocol.settle.refine_round",
    "fr_protocol.settle.previous_discard_s",
    "fr_protocol.settle.resonance_e_folds",
    "fr_protocol.settle.floor_e_folds",
    "fr_protocol.settle.predicted_result_rows",
    "fr_protocol.settle.output_intervals_capped",
    "fr_protocol.settle.prior.path",
    "fr_protocol.amplitude.applied_pcm",
    "fr_protocol.amplitude.clamped",
    "fr_protocol.amplitude.rule_id",
    "fr_protocol.amplitude.effective_target_swing",
    "fr_protocol.amplitude.halvings",
    "fr_protocol.amplitude.plan_amplitude_rule",
    "fr_protocol.amplitude.plan_previous_amplitude_pcm",
    "fr_protocol.amplitude.plan_measured_swing",
    "fr_protocol.amplitude.plan_halving_trigger",
    "fr_protocol.amplitude.plan_linearity_trigger",
    "fr_protocol.amplitude.prior.path",
    "setpoint_table_path",
)

#: Per-case overrides that vary with the frequency (never identity).
CASE_VARYING_OVERRIDES = ("perturbationOmega", "forcingTimeStep")

#: (case-manifest path, request path) pairs anchoring every accepted case
#: manifest to the recorded request identity.  Compared only when the
#: request records the field (requests written before a field existed stay
#: readable); a request that records it binds every case.
MANIFEST_REQUEST_ANCHORS = (
    ("model_name", "model_name"),
    ("package_name", "package_name"),
    ("source_files", "source_files"),
    ("workflow_python", "workflow_python"),
    ("git_commit", "git_commit"),
    ("git_dirty", "git_dirty"),
    ("python_version", "python_version"),
    ("omc_version", "omc_version"),
    ("workflow_version", "workflow_version"),
    ("solver", "numerics.solver"),
    ("tolerance", "numerics.tolerance"),
    ("output_grid", "numerics.output_grid"),
    ("setpoint_table_sha256", "setpoint_table_sha256"),
    ("setpoint_model_version", "setpoint_model_version"),
    ("core_maturity", "core_maturity"),
    ("core_physical_data_maturity", "core_physical_data_maturity"),
    ("overrides.powerLevel", "power"),
    ("overrides.perturbationStartTime", "perturbation_start_time_s"),
)

#: FR-protocol anchors: legacy case manifests carry the per-case
#: ``fr_protocol`` record; segmented manifests keep their historical shape
#: (no record), so these apply to non-segmented requests only.
MANIFEST_FR_PROTOCOL_ANCHORS = (
    ("fr_protocol.solver_tolerance", "policies.fr_protocol.solver_tolerance"),
)

#: Relative tolerance for derived floating-point case/manifest fields
#: (fit-window length, cycle count, natural-period fraction) that are
#: recomputed from shifted fit starts and stop times.
IDENTITY_FLOAT_REL_TOL = 1e-9

_MISSING = object()


def _path_get(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _path_delete(payload: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    node: Any = payload
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def _normalized(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _values_differ(left: Any, right: Any, *, float_tol: bool) -> bool:
    if (
        float_tol
        and isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        return not math.isclose(
            float(left), float(right), rel_tol=IDENTITY_FLOAT_REL_TOL, abs_tol=1e-12
        )
    return canonical_json(left) != canonical_json(right)


def _diff_paths(left: Any, right: Any, prefix: str, *, float_tol: bool) -> list[str]:
    """Dotted paths at which two JSON values differ (sorted, leaf-level)."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                out.append(path)
                continue
            out.extend(_diff_paths(left[key], right[key], path, float_tol=float_tol))
        return out
    return [prefix or "<root>"] if _values_differ(left, right, float_tol=float_tol) else []


def request_identity(
    request: Mapping[str, Any], *, authorized: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Canonical parent-identity projection of a sweep request body.

    ``request`` is the ``request`` object of a sweep request manifest (or
    the data about to be published).  The excluded and derived fields above
    and any caller-``authorized`` dotted paths are removed; everything else
    is identity.
    """
    projected = _normalized(dict(request))
    for name in REQUEST_IDENTITY_EXCLUDED:
        projected.pop(name, None)
    for path in (*REQUEST_IDENTITY_DERIVED, *authorized):
        _path_delete(projected, path)
    return projected


def request_identity_differences(
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    *,
    authorized: tuple[str, ...] = (),
) -> list[str]:
    """Dotted request paths at which ``child`` departs from ``parent``.

    An empty list means the child request (a refinement round, an approval
    check, or a separately launched half-amplitude sweep) carries exactly
    the parent identity.  Exact comparison: both requests are recorded by
    the same runner code path.
    """
    return _diff_paths(
        request_identity(parent, authorized=authorized),
        request_identity(child, authorized=authorized),
        "",
        float_tol=False,
    )


def case_identity(case: Mapping[str, Any]) -> dict[str, Any]:
    """Per-case estimator identity: the case minus the authorized fields."""
    projected = _normalized(dict(case))
    for name in CASE_AUTHORIZED_FIELDS:
        projected.pop(name, None)
    return projected


def case_identity_differences(
    reference_case: Mapping[str, Any], case: Mapping[str, Any]
) -> list[str]:
    """Case fields (outside the authorized set) at which two cases differ.

    Derived floats (window length, cycle count, natural-period fraction)
    are compared to :data:`IDENTITY_FLOAT_REL_TOL`.
    """
    return _diff_paths(
        case_identity(reference_case), case_identity(case), "", float_tol=True
    )


def manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Case-manifest identity: the manifest minus the authorized paths."""
    projected = _normalized(dict(manifest))
    for path in MANIFEST_AUTHORIZED_PATHS:
        _path_delete(projected, path)
    return projected


def manifest_identity_differences(
    reference_manifest: Mapping[str, Any], manifest: Mapping[str, Any]
) -> list[str]:
    """Manifest paths (outside the authorized set) at which two case
    manifests of the SAME frequency differ (a check case against its
    effective reference case)."""
    return _diff_paths(
        manifest_identity(reference_manifest),
        manifest_identity(manifest),
        "",
        float_tol=True,
    )


def manifest_tolerance_scale(manifest: Mapping[str, Any]) -> float:
    """Case tolerance scale a manifest records (1 before the field existed)."""
    record = _path_get(manifest, "fr_protocol.solver_tolerance_case")
    if isinstance(record, Mapping) and record.get("scale") is not None:
        try:
            return float(record["scale"])
        except (TypeError, ValueError):
            return math.nan
    return 1.0


def base_equivalent_tolerance(manifest: Mapping[str, Any]) -> Any:
    """The sweep tolerance a case manifest implies (its tolerance / scale).

    Every case of a sweep shares it (``sweep_common_fields``); a halved
    case or a check case records a scaled tolerance.
    """
    tolerance = manifest.get("tolerance")
    scale = manifest_tolerance_scale(manifest)
    if tolerance is None or scale == 1.0:
        return tolerance
    try:
        return float(f"{float(tolerance) / scale:.12g}")
    except (TypeError, ValueError, ZeroDivisionError):
        return tolerance


def _manifest_tolerance_expectation(
    request: Mapping[str, Any], manifest: Mapping[str, Any], request_tolerance: Any
) -> tuple[str | None, Any]:
    """(problem, expected manifest tolerance) under tolerance_swing_scaled_v2."""
    record = _path_get(manifest, "fr_protocol.solver_tolerance_case")
    if not isinstance(record, Mapping):
        return None, request_tolerance
    try:
        scale = float(record.get("scale"))
        base = float(request_tolerance)
    except (TypeError, ValueError):
        return "fr_protocol.solver_tolerance_case (malformed)", request_tolerance
    policy = _path_get(request, "policies.fr_protocol")
    target = policy.get("target_swing") if isinstance(policy, Mapping) else None
    effective = _path_get(manifest, "fr_protocol.amplitude.effective_target_swing")
    if target and effective not in (_MISSING, None):
        expected_scale = float(effective) / float(target)
    else:
        expected_scale = 1.0
    if not math.isclose(scale, expected_scale, rel_tol=1e-9):
        return (
            "fr_protocol.solver_tolerance_case.scale (expected effective / policy target "
            f"swing {expected_scale:.12g}, recorded {scale!r})",
            request_tolerance,
        )
    expected = base if scale == 1.0 else float(f"{base * scale:.12g}")
    if record.get("floor") is not None:
        # tolerance_swing_scaled_v3: clamped at the floor the request's
        # tolerance record declares.
        declared = _path_get(request, "policies.fr_protocol.solver_tolerance.case_floor")
        if declared is _MISSING or declared is None or not math.isclose(
            float(declared), float(record["floor"]), rel_tol=1e-12
        ):
            return (
                "fr_protocol.solver_tolerance_case.floor (not the request's case floor)",
                request_tolerance,
            )
        expected = max(float(record["floor"]), expected)
    recorded = record.get("tolerance")
    if recorded is None or not math.isclose(float(recorded), expected, rel_tol=1e-9):
        return (
            f"fr_protocol.solver_tolerance_case.tolerance (expected {expected!r}, "
            f"recorded {recorded!r})",
            request_tolerance,
        )
    return None, expected


def manifest_request_differences(
    request: Mapping[str, Any], manifest: Mapping[str, Any]
) -> list[str]:
    """Where a case manifest departs from the identity its (base) request records.

    Checks the :data:`MANIFEST_REQUEST_ANCHORS` pairs, the sweep-level
    settle constants (``policies.fr_protocol.settle``) inside the case's
    ``fr_protocol.settle`` record, and -- when the request records
    ``case_common_overrides`` -- that the case overrides equal them apart
    from the per-frequency :data:`CASE_VARYING_OVERRIDES`.
    """
    problems: list[str] = []
    fr_protocol_bound = str(request.get("package") or "") != "segmented"
    anchors = MANIFEST_REQUEST_ANCHORS + (
        MANIFEST_FR_PROTOCOL_ANCHORS if fr_protocol_bound else ()
    )
    for manifest_path, request_path in anchors:
        expected = _path_get(request, request_path)
        if expected is _MISSING:
            continue
        actual = _path_get(manifest, manifest_path)
        if actual is _MISSING:
            actual = None
        if manifest_path == "tolerance":
            # tolerance_swing_scaled_v2: a case below the policy target runs
            # with the request tolerance x its recorded scale; the scale
            # itself must be the case's effective / policy target swing.
            scale_problem, expected = _manifest_tolerance_expectation(request, manifest, expected)
            if scale_problem:
                problems.append(scale_problem)
                continue
        if _values_differ(expected, actual, float_tol=False):
            problems.append(f"{manifest_path} (request {request_path})")
    settle = _path_get(request, "policies.fr_protocol.settle")
    if fr_protocol_bound and isinstance(settle, Mapping) and settle:
        recorded = _path_get(manifest, "fr_protocol.settle")
        if not isinstance(recorded, Mapping):
            problems.append("fr_protocol.settle (absent)")
        else:
            for key in sorted(settle):
                if key == "prior":
                    expected_prior = {
                        k: v for k, v in dict(settle["prior"] or {}).items() if k != "path"
                    }
                    actual_prior = {
                        k: v for k, v in dict(recorded.get("prior") or {}).items() if k != "path"
                    }
                    if _values_differ(expected_prior, actual_prior, float_tol=False):
                        problems.append("fr_protocol.settle.prior")
                    continue
                if key not in recorded or _values_differ(
                    settle[key], recorded[key], float_tol=False
                ):
                    problems.append(f"fr_protocol.settle.{key}")
    common = _path_get(request, "case_common_overrides")
    if isinstance(common, Mapping):
        overrides = _path_get(manifest, "overrides")
        overrides = dict(overrides) if isinstance(overrides, Mapping) else {}
        for name in CASE_VARYING_OVERRIDES:
            overrides.pop(name, None)
        for path in _diff_paths(dict(common), overrides, "", float_tol=False):
            problems.append(f"overrides.{path}")
    return problems


# ---------------------------------------------------------------------------
# Per-case request-to-manifest binding (rev033 review)
# ---------------------------------------------------------------------------
#
# The request case records every per-point estimator decision the runner
# made; the case manifest records the same decisions in its ``fr_protocol``
# block.  ``audit_case`` (collector, verifier, linearity checks) requires
# them to agree field for field before any fit, so a case manifest cannot
# carry a regime, trend order, gain reference, fit estimator, window,
# forcing cadence, or amplitude clamp its request did not decide.  Request cases written
# before a field existed skip that field (their manifests are still bound
# on every field the request records).

#: (request-case field, case-manifest path) pairs.
CASE_MANIFEST_BINDINGS = (
    ("settle_regime", "fr_protocol.settle.settle_regime"),
    ("settle_rule_discard_s", "fr_protocol.settle.settle_rule_discard_s"),
    ("settle_discard_s", "fr_protocol.settle.settle_discard_s"),
    ("fit_start_s", "fr_protocol.settle.fit_start_s"),
    ("stop_time_s", "fr_protocol.settle.stop_time_s"),
    ("settle_refine_round", "fr_protocol.settle.refine_round"),
    ("fit_trend_order", "fr_protocol.settle.fit_trend_order"),
    ("gain_reference", "fr_protocol.settle.gain_reference"),
    ("fit_estimator", "fr_protocol.settle.fit_estimator"),
    ("fit_window_s", "fr_protocol.settle.fit_window_s"),
    ("natural_period_fraction", "fr_protocol.settle.natural_period_fraction"),
    ("fit_cycles", "fr_protocol.settle.fit_cycles"),
    ("forcing_time_step_s", "fr_protocol.settle.forcing_time_step_s"),
    ("predicted_result_rows", "fr_protocol.settle.predicted_result_rows"),
    ("output_intervals_capped", "fr_protocol.settle.output_intervals_capped"),
    ("perturbation_amplitude_pcm", "fr_protocol.amplitude.applied_pcm"),
    ("amplitude_clamped", "fr_protocol.amplitude.clamped"),
    ("effective_target_swing", "fr_protocol.amplitude.effective_target_swing"),
    ("amplitude_halvings", "fr_protocol.amplitude.halvings"),
    ("solver_tolerance", "tolerance"),
)

#: (request path, case-manifest path) pairs of the amplitude policy every
#: target-swing case records (the prior that shaped its amplitude).
AMPLITUDE_POLICY_BINDINGS = (
    ("policies.fr_protocol.prior.sha256", "fr_protocol.amplitude.prior.sha256"),
    ("policies.fr_protocol.prior.id", "fr_protocol.amplitude.prior.id"),
    ("policies.fr_protocol.target_swing", "fr_protocol.amplitude.target_swing"),
    ("policies.fr_protocol.target_swing_min_pcm", "fr_protocol.amplitude.min_pcm"),
    ("policies.fr_protocol.target_swing_max_pcm", "fr_protocol.amplitude.max_pcm"),
)

#: Clamp derivation for request cases written before ``amplitude_clamped``
#: existed: an amplitude within this relative distance of a bound is the
#: bound (the target-swing rule returns the bound itself when it clamps).
CLAMP_BOUND_REL_TOL = 1e-9

#: :func:`case_amplitude_clamp` result when the request cannot tell (a
#: half-amplitude check case of an older request: its manifest clamp is the
#: rule's decision for the base amplitude, not derivable from the request).
CLAMP_UNKNOWN = "<unknown>"


def _plan_point(request: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    for block_name in ("refinement", "check"):
        block = request.get(block_name)
        if isinstance(block, Mapping):
            for point in block.get("points") or []:
                if isinstance(point, Mapping) and str(point.get("frequency_key")) == key:
                    return point
    return None


def case_amplitude_clamp(request: Mapping[str, Any], case: Mapping[str, Any]) -> Any:
    """Amplitude-clamp direction (``None`` / ``"min"`` / ``"max"``) of a request case.

    The request case's explicit ``amplitude_clamped`` when recorded (new
    requests).  Otherwise derived from the request itself, so requests
    written before the field existed stay verifiable: a refinement point's
    recorded rescale clamp, else the case amplitude (divided by the
    request's ``sin_mag_scale``) against the target-swing bounds.  Returns
    ``None`` when the target-swing rule is not active, and
    :data:`CLAMP_UNKNOWN` for a half-amplitude check case of an older
    request.
    """
    if "amplitude_clamped" in case:
        return case.get("amplitude_clamped")
    policy = (request.get("policies") or {}).get("fr_protocol") or {}
    if not policy.get("target_swing_rule_active"):
        return None
    point = _plan_point(request, str(case.get("frequency_key")))
    if point is not None:
        rule = str(point.get("amplitude_rule") or "")
        if rule == "half_amplitude_check":
            return CLAMP_UNKNOWN
        if "amplitude_clamped" in point:
            return point.get("amplitude_clamped")
    try:
        amplitude = float(case["perturbation_amplitude_pcm"]) / float(
            policy.get("sin_mag_scale") or 1.0
        )
        low = float(policy["target_swing_min_pcm"])
        high = float(policy["target_swing_max_pcm"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return CLAMP_UNKNOWN
    if amplitude <= low * (1.0 + CLAMP_BOUND_REL_TOL):
        return "min"
    if amplitude >= high * (1.0 - CLAMP_BOUND_REL_TOL):
        return "max"
    return None


def case_target_swing(request: Mapping[str, Any], case: Mapping[str, Any]) -> float | None:
    """Effective target swing of a request case (``None``: no target-swing rule).

    The case's recorded ``effective_target_swing`` (the policy target
    divided by ``2 ** amplitude_halvings``; half of its reference's for a
    linearity check case); requests written before the field existed never
    halved, so their cases target the policy's ``target_swing``.
    """
    if "effective_target_swing" in case:
        value = case.get("effective_target_swing")
        return None if value is None else float(value)
    policy = (request.get("policies") or {}).get("fr_protocol") or {}
    if not policy.get("target_swing_rule_active") or policy.get("target_swing") is None:
        return None
    return float(policy["target_swing"])


def case_solver_tolerance(request: Mapping[str, Any], case: Mapping[str, Any]) -> Any:
    """Solver tolerance a request case runs with (tolerance_swing_scaled_v2).

    The case's recorded ``solver_tolerance``; requests written before the
    field existed ran every case at ``numerics.tolerance``.
    """
    if case.get("solver_tolerance") is not None:
        return float(case["solver_tolerance"])
    numerics = request.get("numerics") or {}
    return numerics.get("tolerance")


def case_tolerance_problem(request: Mapping[str, Any], case: Mapping[str, Any]) -> str | None:
    """Why a request case's ``solver_tolerance`` is not the request tolerance
    scaled by its effective / policy target swing (``None``: consistent or
    not recorded)."""
    if case.get("solver_tolerance") is None:
        return None
    base = (request.get("numerics") or {}).get("tolerance")
    policy = (request.get("policies") or {}).get("fr_protocol") or {}
    target = policy.get("target_swing") if policy.get("target_swing_rule_active") else None
    effective = case_target_swing(request, case)
    try:
        scale = float(effective) / float(target) if target and effective else 1.0
        expected = float(base) if math.isclose(scale, 1.0, rel_tol=1e-12) else float(
            f"{float(base) * scale:.12g}"
        )
        floor = ((policy.get("solver_tolerance") or {}).get("case_floor"))
        if floor is not None:
            expected = max(float(floor), expected)
    except (TypeError, ValueError, ZeroDivisionError):
        return "records a solver tolerance the request cannot derive"
    if not math.isclose(float(case["solver_tolerance"]), expected, rel_tol=1e-9):
        return (
            f"records solver tolerance {case['solver_tolerance']!r}; the request "
            f"tolerance {base!r} scaled by its effective target swing gives {expected!r}"
        )
    return None


def case_amplitude_halvings(case: Mapping[str, Any]) -> int:
    """Amplitude halvings behind a request case (0 before the field existed)."""
    return int(case.get("amplitude_halvings") or 0)


def _binding_values_differ(expected: Any, actual: Any) -> bool:
    if (
        isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
        and not isinstance(expected, bool)
        and not isinstance(actual, bool)
    ):
        return not math.isclose(float(expected), float(actual), rel_tol=1e-12, abs_tol=1e-12)
    return canonical_json(expected) != canonical_json(actual)


def case_manifest_binding_problems(
    request: Mapping[str, Any],
    case: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> list[str]:
    """Where a case manifest departs from the decisions its request case records.

    ``request`` is the request body the case belongs to (base, round, or
    check).  Compared: every :data:`CASE_MANIFEST_BINDINGS` field the request
    case records (``amplitude_clamped`` explicit or derived by
    :func:`case_amplitude_clamp`), the forcing cadence against the manifest
    override ``forcingTimeStep``, and -- for target-swing requests -- the
    amplitude rule and :data:`AMPLITUDE_POLICY_BINDINGS`.  Applies to case
    manifests that carry an ``fr_protocol`` record (legacy sweeps);
    segmented manifests keep their historical shape and return no problems.
    """
    if not isinstance(manifest.get("fr_protocol"), Mapping):
        return []
    problems: list[str] = []
    tolerance_problem = case_tolerance_problem(request, case)
    if tolerance_problem:
        problems.append(f"solver_tolerance ({tolerance_problem})")
    for field, manifest_path in CASE_MANIFEST_BINDINGS:
        if field == "amplitude_clamped":
            expected = case_amplitude_clamp(request, case)
            if expected == CLAMP_UNKNOWN:
                continue
        elif field not in case:
            continue
        else:
            expected = case[field]
        actual = _path_get(manifest, manifest_path)
        if actual is _MISSING:
            if field in ("amplitude_clamped", "effective_target_swing") and expected is None:
                continue  # non-target-swing records carry no clamp or target field
            problems.append(f"{field} (manifest {manifest_path} absent)")
            continue
        if _binding_values_differ(expected, actual):
            problems.append(f"{field} (request {expected!r}, manifest {manifest_path} {actual!r})")
    if "forcing_time_step_s" in case:
        step = float(case["forcing_time_step_s"] or 0.0)
        override = _path_get(manifest, "overrides.forcingTimeStep")
        if step > 0:
            if override is _MISSING or _binding_values_differ(step, override):
                problems.append(
                    "forcing_time_step_s (request "
                    f"{step!r}, manifest overrides.forcingTimeStep "
                    f"{None if override is _MISSING else override!r})"
                )
        elif override is not _MISSING:
            problems.append(
                f"forcing_time_step_s (request 0, manifest overrides.forcingTimeStep {override!r})"
            )
    policy = (request.get("policies") or {}).get("fr_protocol") or {}
    if "sin_mag_scale" in policy:
        actual = _path_get(manifest, "fr_protocol.amplitude.sin_mag_scale")
        if actual is _MISSING or _binding_values_differ(policy["sin_mag_scale"], actual):
            problems.append(
                "fr_protocol.amplitude.sin_mag_scale (request policies.fr_protocol.sin_mag_scale)"
            )
    if policy.get("target_swing_rule_active"):
        if _path_get(manifest, "fr_protocol.amplitude.rule") != "target_swing":
            problems.append("fr_protocol.amplitude.rule (request target_swing)")
        for request_path, manifest_path in AMPLITUDE_POLICY_BINDINGS:
            expected = _path_get(request, request_path)
            if expected is _MISSING:
                continue
            actual = _path_get(manifest, manifest_path)
            if actual is _MISSING or _binding_values_differ(expected, actual):
                problems.append(f"{manifest_path} (request {request_path})")
    return problems
