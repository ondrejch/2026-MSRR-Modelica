#!/usr/bin/env python3
"""Immutable request manifests for frequency-response campaigns.

The request manifest is written before worker dispatch and is the authority
for campaign completeness.  ``run_params.txt`` and the mapping CSV files are
human-readable derivatives only.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SWEEP_REQUEST_FILENAME = "sweep_request.manifest.json"
SWEEP_REQUEST_SCHEMA_VERSION = 1
SWEEP_REQUEST_FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"


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


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.partial")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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


def publish_sweep_request_manifest(
    results_dir: str | os.PathLike[str],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish a request before dispatch, retaining identity for exact reruns."""
    path = Path(results_dir) / SWEEP_REQUEST_FILENAME
    candidate = build_sweep_request_manifest(request)
    if path.exists():
        try:
            existing = load_sweep_request_manifest(results_dir)
        except SweepManifestError:
            existing = None
        if existing is not None and existing["fingerprint"] == candidate["fingerprint"]:
            return existing
    _atomic_write(path, json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    return candidate


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
