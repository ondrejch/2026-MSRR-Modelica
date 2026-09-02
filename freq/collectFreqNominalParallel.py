#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Collect and analyze frequency response results from the nominal MSRR model.
Parallel version using thread pools for curve fitting across frequency points.

Every requested frequency point is first verified against its provenance
sidecars before its sine fit enters the aggregate: both
``<prefix>_res.manifest.json`` and ``<prefix>_res.validation.json`` must
exist, the recorded fingerprint must match the manifest it accompanies, the
CSV bytes must match the hash recorded in the validation sidecar, the CSV
must pass full revalidation, and the manifest's forcing frequency and
perturbation amplitude must agree with the requested case.  Sweep-common
manifest fields (model, package, sources, workflow Python hashes, solver,
tolerance, output grid, setpoint table, power, perturbation start, backend,
workflow version, interpreter and OpenModelica versions) must agree across
all cases.  By default any missing or rejected point aborts the collection
with a nonzero exit and no aggregate is written.

Normal collection uses ``sweep_request.manifest.json`` as the immutable
campaign authority.  It defines the exact case set and each case's frequency,
amplitude, stop time, and output grid; case manifests must carry the same
request identity and case settings.  After that audit, verified case
manifests supply the fit phase reference and reported model/package/power
labels.  ``run_params.txt`` is compared with those verified manifests only as
an informational diagnostic in immutable-request mode: a disagreement is
recorded and ignored because it cannot redefine the campaign.

The explicitly selected legacy-request mode has weaker semantics:
``run_params.txt`` plus optional mapping CSVs define the requested grid, while
the verified case manifests remain authoritative for phase and labels.  In
that mode a run-parameter disagreement rejects the affected provenanced
points before any fit (fail closed).

Sweep-mapping files (``sin_mag_by_freq.csv`` / ``stop_time_by_freq.csv``)
are strictly validated when present: duplicate normalized frequency keys
are rejected (the historical last-row-wins behavior is gone), every
requested frequency needs exactly one amplitude entry, stop-time coverage
is required whenever the mapping is the selected stop-time policy, and
entries outside the requested grid are rejected unless
--allow_unknown_mapping_freqs is passed.  A mapping validation failure
exits with code 2 before any audit or fit.  The fit window is validated
the same way: an explicit --fit_start opening before the verified
perturbation start (minus a documented 1e-6 s slack) is a request-
validation error before any fit, as is a per-frequency stop-time entry
that does not clear the fit start.  --fit_start_allow_pre_forcing is the
separately named, explicitly unsafe diagnostic override; every aggregate
row produced through it is labeled non_transfer_function=True.

The aggregate itself is published as one claimed, self-verifying
generation.  The collector first takes an aggregate-level claim on the
``FreqResponseResults`` basename (the ``FreqResponseResults.claim``
companion file), so two concurrent collections of the same directory
serialize instead of interleaving their products.  Before anything new is
published, the prior current-name aggregate set (``FreqResponseResults.m``
/ ``.csv`` / ``.failures.json`` / ``.manifest.json``), any hidden staging
leftovers of interrupted collections, the prior current-name Bode plot
(whether or not a replacement is requested), and any stale failure table are moved
under ``00runs/tmp/quarantine/`` -- a strict failed collection therefore
leaves NO complete-looking aggregate at the current names, only its new
failure record.  Every output is then staged under a unique hidden name
in the results directory, hashed (SHA-256 + byte length), and published
by rename with the manifest LAST.  The manifest carries the generation
ID, the per-output digests, and a ``collector_python`` hash section (the
collector module, ``freq/_common.py``, ``freq/paths.py``, and
``helpers/run_results.py`` -- separate from the per-case
``workflow_python`` hashes), so readers can verify the aggregate before
consuming it (``verify_aggregate_outputs`` re-checks every advertised
digest).

Usage:
    python collectFreqNominalParallel.py [--results_dir RESULTS_DIR] [--plot]
                                        [--n_jobs N_JOBS]
                                        [--allow_partial]
                                        [--allow_legacy_unprovenanced]
                                        [--allow_unknown_mapping_freqs]

Escape hatches (both label their output; neither silently passes bad data):

- ``--allow_partial``: write a clearly labeled PARTIAL aggregate plus a
  machine-readable failure table instead of aborting; the exit code is
  then 0 only because the labeling carries the incomplete status.
- ``--allow_legacy_unprovenanced``: the only path that accepts case CSVs
  without usable sidecars; the aggregate is labeled as containing
  unprovenanced legacy data.
"""

import argparse
import json
import math
import os
import platform
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from ._common import (
        clean_column_headers,
        find_power_column,
        fit_sine_least_squares,
        format_frequency_key,
        format_matlab_assignment,
        phase_relative_to_perturbation_start_deg,
        read_run_params,
        wrap_phase_rad,
    )
    from .paths import default_freq_case_dir
    from . import sweep_manifest
except ImportError:
    from _common import (
        clean_column_headers,
        find_power_column,
        fit_sine_least_squares,
        format_frequency_key,
        format_matlab_assignment,
        phase_relative_to_perturbation_start_deg,
        read_run_params,
        wrap_phase_rad,
    )
    from paths import default_freq_case_dir
    import sweep_manifest


def default_cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def _run_results():
    """Lazily import the shared run-result provenance library.

    helpers/run_results.py owns manifests, fingerprints, output validation,
    and sidecar I/O; importing it lazily keeps the collector usable both as
    a package module and in script-style execution (same contract as the
    sweep runner's helper accessor).
    """
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from freq/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import run_results as rr
    return rr


# --- Collection provenance policy (card C3/C4) ------------------------------
#
# Case-level statuses.  Accepted statuses enter the aggregate; everything
# else is a failure.  ``unprovenanced`` marks points whose content cannot be
# tied to a validated request (missing sidecars, or a validation sidecar
# from before content hashing existed); it is excused only by
# --allow_legacy_unprovenanced.  Rejections (self-inconsistent or tampered
# provenance, failed revalidation, sweep disagreement, failed fit) are
# excused only by --allow_partial.

CASE_ACCEPTED = "accepted"
CASE_ACCEPTED_LEGACY = "accepted_legacy_unprovenanced"
CASE_MISSING_CSV = "missing_csv"
CASE_UNPROVENANCED = "unprovenanced"
CASE_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
CASE_CONTENT_HASH_MISMATCH = "content_hash_mismatch"
CASE_VALIDATION_FAILED = "validation_failed"
CASE_FREQUENCY_MISMATCH = "frequency_mismatch"
CASE_AMPLITUDE_MISMATCH = "amplitude_mismatch"
CASE_SWEEP_FIELD_MISMATCH = "sweep_field_mismatch"
CASE_RUN_PARAMS_MISMATCH = "run_params_mismatch"
CASE_FOREIGN_CAMPAIGN = "foreign_campaign"
CASE_REQUEST_MISMATCH = "request_mismatch"
CASE_UNEXPECTED = "unexpected_case"
CASE_FIT_FAILED = "fit_failed"

_CASE_ACCEPTED_STATUSES = frozenset({CASE_ACCEPTED, CASE_ACCEPTED_LEGACY})

COLLECTION_STATUS_COMPLETE = "complete"
COLLECTION_STATUS_PARTIAL = "partial"
COLLECTION_STATUS_LEGACY = "legacy_unprovenanced"

COLLECTION_MANIFEST_FILENAME = "FreqResponseResults.manifest.json"
COLLECTION_MANIFEST_SCHEMA_VERSION = 2
COLLECTION_FAILURE_SCHEMA_VERSION = 2
AGGREGATE_CSV_SCHEMA_VERSION = 2
SWEEP_REQUEST_SCHEMA_VERSION = sweep_manifest.SWEEP_REQUEST_SCHEMA_VERSION
COLLECTION_FAILURES_FILENAME = "FreqResponseResults.failures.json"

# --- Aggregate publication protocol (card P4 / review H2, H3, H4) ----------
#
# The aggregate is published as ONE claimed generation: an aggregate-level
# claim on the basename serializes concurrent collectors; the prior
# current-name set is quarantined before anything new is published; every
# output is staged under a unique hidden name and hashed; and the manifest
# -- carrying the generation ID, the per-output digests, and the
# collector_python hash section -- is published LAST.
AGGREGATE_BASENAME = "FreqResponseResults"
AGGREGATE_CLAIM_FILENAME = AGGREGATE_BASENAME + ".claim"
AGGREGATE_M_FILENAME = AGGREGATE_BASENAME + ".m"
AGGREGATE_CSV_FILENAME = AGGREGATE_BASENAME + ".csv"
AGGREGATE_PLOT_FILENAME = "BodePlot.png"

# Case-CSV revalidation mirrors the acceptance criteria the sweep runner
# (freq/runFreqNominalParallel.py) enforced when it wrote each sidecar.
# The two values are deliberately duplicated here: importing the runner
# module from the collector would be a heavy, unnecessary dependency.
COLLECT_VALIDATION_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)
COLLECT_VALIDATION_STOP_SLACK_RATIO = 0.005

# --- Manifest authority (card C3): run_params.txt agreement tolerances -----
#
# run_params.txt is an informational request log, while the per-case
# manifests are verified.  The collector therefore derives the perturbation
# start, power, model, and package identity from the accepted manifests and
# only requires run_params.txt to agree within these explicit numeric
# tolerances.  The runner writes run_params values via str(float) (full
# round-trip precision) and records the same numbers in the manifests, so
# agreement holds exactly for genuine sweeps; the tolerances exist to absorb
# formatting noise while still separating a stale request file (e.g. 2000 s
# vs 2500 s) from the verified manifests.
RUN_PARAMS_POWER_REL_TOL = 1e-9
RUN_PARAMS_POWER_ABS_TOL = 1e-12
RUN_PARAMS_TIME_REL_TOL = 1e-9
RUN_PARAMS_TIME_ABS_TOL = 1e-12

# --- Fit-start validation (review H1) ---------------------------------------
#
# Samples recorded before the perturbation begins carry no transfer-function
# information, so an effective fit window that opens before the verified
# perturbation start is a request error, not a per-point fit failure.  This
# absolute slack (seconds) absorbs float round-trip noise when an explicit
# --fit_start repeats the manifest value; anything earlier is rejected
# before any fit unless --fit_start_allow_pre_forcing (the separately named,
# explicitly unsafe diagnostic override) is passed.
FIT_START_PRE_FORCING_TOLERANCE_S = 1e-6


@dataclass
class CaseAudit:
    """Provenance verdict for one requested sweep frequency point.

    ``fingerprint_claimed`` is the fingerprint recorded in the case's
    manifest sidecar (what the collection expected to verify); it is the
    ``expected_fingerprint`` of the failure table.  ``fingerprint_recomputed``
    is the digest recomputed from the stored manifest itself.
    """

    freq: float
    frequency_key: str
    source_csv: str | None
    status: str
    reason: str | None = None
    fingerprint_claimed: str | None = None
    fingerprint_recomputed: str | None = None
    legacy: bool = False
    manifest: dict | None = field(default=None, repr=False, compare=False)

    @property
    def accepted(self) -> bool:
        return self.status in _CASE_ACCEPTED_STATUSES

    def to_dict(self) -> dict:
        return {
            "frequency_rad_s": self.freq,
            "frequency_key": self.frequency_key,
            "status": self.status,
            "reason": self.reason,
            "source_csv": self.source_csv,
            "expected_fingerprint": self.fingerprint_claimed,
            "recomputed_fingerprint": self.fingerprint_recomputed,
            "legacy_unprovenanced": self.legacy,
        }


def case_result_paths(results_dir: str, freq_point: float) -> tuple[str, str]:
    """Work directory and result CSV path for one frequency case."""
    work_path = os.path.join(results_dir, f"freq{freq_point:08.5f}")
    file_prefix = f"MSRR_freq{freq_point:08.5f}"
    return work_path, os.path.join(work_path, f"{file_prefix}_res.csv")


def audit_case(
    freq_point: float,
    results_dir: str,
    expected_sin_mag: float,
    *,
    allow_legacy: bool,
    request_manifest: dict | None = None,
    expected_stop_time: float | None = None,
    expected_intervals: int | None = None,
) -> CaseAudit:
    """Verify one requested frequency point against its sidecars and CSV.

    Checks run in a fixed order; the first failure decides the status:

    1. the case CSV exists (else ``missing_csv``);
    2. a readable manifest sidecar and a readable validation sidecar exist
       (else ``unprovenanced``);
    3. the recorded fingerprint equals the digest recomputed from the stored
       manifest (else ``fingerprint_mismatch`` — the sidecar was edited or
       corrupted);
    4. the validation sidecar carries a ``result_sha256`` and the CSV bytes
       still hash to it (absence: ``unprovenanced``; mismatch:
       ``content_hash_mismatch``);
    5. the CSV passes full revalidation with the criteria the runner used
       (else ``validation_failed``);
    6. the manifest's ``overrides["perturbationOmega"]`` matches the
       requested frequency and ``perturbation_amplitude`` matches the
       expected sweep amplitude (else ``frequency_mismatch`` /
       ``amplitude_mismatch``).

    With ``allow_legacy`` the two ``unprovenanced`` outcomes become
    ``accepted_legacy_unprovenanced`` instead; rejections are never excused
    by that flag.
    """
    rr = _run_results()
    key = format_frequency_key(freq_point)
    _work_path, data_file = case_result_paths(results_dir, freq_point)
    audit = CaseAudit(
        freq=float(freq_point),
        frequency_key=key,
        source_csv=None,
        status=CASE_MISSING_CSV,
    )

    if not os.path.exists(data_file):
        audit.reason = "result CSV not found"
        return audit
    audit.source_csv = data_file

    def _accept_legacy(reason: str) -> CaseAudit:
        if allow_legacy:
            audit.status = CASE_ACCEPTED_LEGACY
            audit.legacy = True
            audit.reason = f"{reason}; accepted under --allow_legacy_unprovenanced"
        else:
            audit.status = CASE_UNPROVENANCED
            audit.reason = reason
        return audit

    payload = rr.read_manifest_sidecar(data_file)
    if payload is None:
        return _accept_legacy(
            "manifest sidecar missing or unreadable "
            f"({rr.manifest_sidecar_path(data_file).name})"
        )

    stored_manifest = payload.get("manifest")
    recorded_fp = str(payload.get("fingerprint") or "").strip().lower()
    audit.fingerprint_claimed = recorded_fp or None
    if not isinstance(stored_manifest, dict):
        audit.status = CASE_FINGERPRINT_MISMATCH
        audit.reason = "manifest sidecar lacks a usable 'manifest' section"
        return audit

    recomputed_fp = rr.manifest_fingerprint(stored_manifest)
    audit.fingerprint_recomputed = recomputed_fp
    if recorded_fp != recomputed_fp:
        audit.status = CASE_FINGERPRINT_MISMATCH
        audit.reason = (
            "recorded sidecar fingerprint does not match its own manifest "
            "(sidecar edited or corrupted)"
        )
        return audit

    validation_payload = rr.read_validation_report(data_file)
    if validation_payload is None:
        return _accept_legacy(
            "validation sidecar missing or unreadable "
            f"({rr.validation_report_path(data_file).name})"
        )

    recorded_hash = validation_payload.get("result_sha256")
    if not recorded_hash:
        return _accept_legacy(
            "validation sidecar predates result content hashing; CSV bytes "
            "cannot be tied to the validated report"
        )

    try:
        actual_hash = rr.file_sha256(data_file)
    except OSError as exc:
        audit.status = CASE_VALIDATION_FAILED
        audit.reason = f"cannot hash result CSV: {exc}"
        return audit
    if str(recorded_hash).strip().lower() != actual_hash:
        audit.status = CASE_CONTENT_HASH_MISMATCH
        audit.reason = (
            "result CSV bytes differ from the validated content recorded in "
            "the validation sidecar"
        )
        return audit

    stop_time = stored_manifest.get("stop_time")
    try:
        stop_value = float(stop_time) if stop_time is not None else None
    except (TypeError, ValueError):
        stop_value = None
    report = rr.validate_result_csv(
        data_file,
        required_columns=COLLECT_VALIDATION_REQUIRED_COLUMNS,
        time_column="time",
        launch_timestamp=stored_manifest.get("launch_timestamp_utc"),
        requested_stop_time=stop_value,
        stop_time_slack_s=(
            COLLECT_VALIDATION_STOP_SLACK_RATIO * abs(stop_value)
            if stop_value is not None
            else 0.0
        ),
    )
    if not report.passed:
        audit.status = CASE_VALIDATION_FAILED
        audit.reason = "revalidation failed: " + (
            ", ".join(report.failed_checks) or "unknown"
        )
        return audit

    overrides = stored_manifest.get("overrides") or {}
    recorded_omega = overrides.get("perturbationOmega")
    try:
        omega_value = float(recorded_omega)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        omega_value = None
    if (
        omega_value is None
        or not math.isclose(
            omega_value, float(freq_point), rel_tol=1e-12, abs_tol=1e-9
        )
    ):
        audit.status = CASE_FREQUENCY_MISMATCH
        audit.reason = (
            f"manifest perturbationOmega {recorded_omega!r} does not match "
            f"requested frequency {float(freq_point):g} rad/s"
        )
        return audit

    recorded_amp = stored_manifest.get("perturbation_amplitude")
    try:
        amp_value = float(recorded_amp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        amp_value = None
    if (
        amp_value is None
        or not math.isclose(
            amp_value, float(expected_sin_mag), rel_tol=1e-9, abs_tol=1e-12
        )
    ):
        audit.status = CASE_AMPLITUDE_MISMATCH
        audit.reason = (
            f"manifest perturbation amplitude {recorded_amp!r} does not "
            f"match expected {float(expected_sin_mag):g} pcm"
        )
        return audit

    if request_manifest is not None:
        reference_problem = sweep_manifest.verify_case_reference(
            stored_manifest, request_manifest
        )
        if reference_problem:
            audit.status = CASE_FOREIGN_CAMPAIGN
            audit.reason = reference_problem
            return audit
        if expected_stop_time is not None:
            recorded_stop = _as_float_or_none(stored_manifest.get("stop_time"))
            if recorded_stop is None or not math.isclose(
                recorded_stop, float(expected_stop_time),
                rel_tol=1e-12, abs_tol=1e-9,
            ):
                audit.status = CASE_REQUEST_MISMATCH
                audit.reason = (
                    f"manifest stop_time {stored_manifest.get('stop_time')!r} "
                    f"does not match sweep request {expected_stop_time:g} s"
                )
                return audit
        if expected_intervals is not None:
            try:
                recorded_intervals = int(stored_manifest.get("number_of_intervals"))
            except (TypeError, ValueError):
                recorded_intervals = None
            if recorded_intervals != int(expected_intervals):
                audit.status = CASE_REQUEST_MISMATCH
                audit.reason = (
                    "manifest number_of_intervals "
                    f"{stored_manifest.get('number_of_intervals')!r} does not "
                    f"match sweep request {int(expected_intervals)}"
                )
                return audit

    audit.status = CASE_ACCEPTED
    audit.manifest = stored_manifest
    return audit


def sweep_common_fields(manifest: dict) -> dict:
    """Extract the sweep-common manifest fields that must agree across cases.

    Every field here is already written by ``build_run_manifest`` (helpers/
    run_results.py) and populated by the sweep runner, so this is comparison
    wiring only.  Intentionally per-frequency fields (stop time, interval
    count, forcing frequency, perturbation amplitude) stay recorded per
    accepted-case entry, not as common fields.
    """
    overrides = manifest.get("overrides") or {}
    return {
        "model_name": manifest.get("model_name"),
        "package_name": manifest.get("package_name"),
        "source_files": manifest.get("source_files"),
        "workflow_python": manifest.get("workflow_python"),
        "solver": manifest.get("solver"),
        "tolerance": manifest.get("tolerance"),
        "output_grid": manifest.get("output_grid"),
        "python_version": manifest.get("python_version"),
        "omc_version": manifest.get("omc_version"),
        "setpoint_table_path": manifest.get("setpoint_table_path"),
        "setpoint_table_sha256": manifest.get("setpoint_table_sha256"),
        "power": overrides.get("powerLevel"),
        "perturbation_start_time": overrides.get("perturbationStartTime"),
        "backend": manifest.get("backend"),
        "workflow_version": manifest.get("workflow_version"),
        "sweep_request": manifest.get("sweep_request"),
    }


def enforce_sweep_common_fields(audits: list[CaseAudit]) -> None:
    """Reject accepted cases whose sweep-common manifest fields disagree.

    The first accepted case carrying a manifest is the reference; every
    other accepted case must agree field-for-field (canonical-JSON equality,
    matching fingerprint semantics).  Disagreeing cases are downgraded to
    ``sweep_field_mismatch`` in place.  Cases without a manifest (legacy)
    are skipped.
    """
    rr = _run_results()
    reference: dict | None = None
    reference_freq: float | None = None
    for audit in audits:
        if not audit.accepted or audit.manifest is None:
            continue
        fields = sweep_common_fields(audit.manifest)
        if reference is None:
            reference = fields
            reference_freq = audit.freq
            continue
        differing = [
            name
            for name in sorted(fields)
            if rr.canonical_manifest_json(fields[name])
            != rr.canonical_manifest_json(reference[name])
        ]
        if differing:
            audit.status = CASE_SWEEP_FIELD_MISMATCH
            audit.legacy = False
            audit.reason = (
                "sweep-common fields differ from the reference case "
                f"({reference_freq:g} rad/s): " + ", ".join(differing)
            )


# --- Manifest authority (card C3) -------------------------------------------
#
# run_params.txt is an informational request log written after the sweep; the
# per-case manifests are verified.  The collector therefore derives the fit
# lower bound, the phase reference, and the power/model/package labels from
# the ACCEPTED manifests and only requires run_params.txt to agree.


def _as_float_or_none(value) -> float | None:
    """Best-effort float coercion; None for missing or unparseable values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ManifestAuthority:
    """Sweep-level identity derived from the first accepted case manifest.

    All compared sweep-common fields agree across accepted cases
    (``enforce_sweep_common_fields``), so the first accepted manifest with a
    provenance sidecar is representative for every field it carries.  Fields
    missing from that manifest stay None and fall back to run_params.txt at
    the call sites.
    """

    reference_frequency: float
    perturbation_start: float | None
    power: float | None
    model_name: str | None
    package_name: str | None
    package: str | None
    source_files: dict | None
    solver: str | None
    tolerance: float | None
    output_grid: str | None
    omc_version: str | None
    workflow_python: dict | None


def resolve_manifest_authority(audits: list[CaseAudit]) -> ManifestAuthority | None:
    """Derive the sweep authority from the first accepted case manifest.

    Returns None when no accepted case carries a manifest (for example a
    purely legacy collection under --allow_legacy_unprovenanced): there is
    then nothing verified to be authoritative and run_params.txt keeps its
    historical fallback role, explicitly recorded as such.
    """
    for audit in audits:
        if not audit.accepted or audit.manifest is None:
            continue
        manifest = audit.manifest
        overrides = manifest.get("overrides") or {}
        return ManifestAuthority(
            reference_frequency=audit.freq,
            perturbation_start=_as_float_or_none(
                overrides.get("perturbationStartTime")
            ),
            power=_as_float_or_none(overrides.get("powerLevel")),
            model_name=manifest.get("model_name"),
            package_name=manifest.get("package_name"),
            package=overrides.get("package"),
            source_files=manifest.get("source_files"),
            solver=manifest.get("solver"),
            tolerance=_as_float_or_none(manifest.get("tolerance")),
            output_grid=manifest.get("output_grid"),
            omc_version=manifest.get("omc_version"),
            workflow_python=manifest.get("workflow_python"),
        )
    return None


# (run_params.txt key, ManifestAuthority attribute, rel_tol, abs_tol) for the
# numeric agreement checks; text fields are compared exactly when present.
RUN_PARAMS_NUMERIC_CHECKS: tuple[tuple[str, str, float, float], ...] = (
    ("power", "power", RUN_PARAMS_POWER_REL_TOL, RUN_PARAMS_POWER_ABS_TOL),
    (
        "ss_time",
        "perturbation_start",
        RUN_PARAMS_TIME_REL_TOL,
        RUN_PARAMS_TIME_ABS_TOL,
    ),
)
RUN_PARAMS_TEXT_CHECKS: tuple[tuple[str, str], ...] = (
    ("model_name", "model_name"),
    ("package", "package"),
)


def check_run_params_agreement(
    params: dict, authority: ManifestAuthority
) -> dict:
    """Compare run_params.txt entries against the verified manifest values.

    Each check is recorded with both raw values and its outcome so the
    failure table and the collection manifest can show exactly which entry
    disagrees.  Checks are skipped (never disagreements) when run_params.txt
    does not carry the key or the manifests do not carry the field.
    """
    checks: list[dict] = []
    for key, attr, rel_tol, abs_tol in RUN_PARAMS_NUMERIC_CHECKS:
        manifest_value = getattr(authority, attr)
        if key not in params or manifest_value is None:
            checks.append(
                {
                    "field": key,
                    "run_params": params.get(key),
                    "manifest": manifest_value,
                    "status": "skipped",
                }
            )
            continue
        requested_value = _as_float_or_none(params[key])
        manifest_value = float(manifest_value)
        if requested_value is None:
            checks.append(
                {
                    "field": key,
                    "run_params": params[key],
                    "manifest": manifest_value,
                    "status": "skipped",
                    "note": "run_params value is not numeric",
                }
            )
            continue
        agreed = math.isclose(
            requested_value, manifest_value, rel_tol=rel_tol, abs_tol=abs_tol
        )
        checks.append(
            {
                "field": key,
                "run_params": requested_value,
                "manifest": manifest_value,
                "status": "agree" if agreed else "disagree",
            }
        )
    for key, attr in RUN_PARAMS_TEXT_CHECKS:
        manifest_value = getattr(authority, attr)
        if key not in params or manifest_value is None:
            checks.append(
                {
                    "field": key,
                    "run_params": params.get(key),
                    "manifest": manifest_value,
                    "status": "skipped",
                }
            )
            continue
        agreed = str(params[key]) == str(manifest_value)
        checks.append(
            {
                "field": key,
                "run_params": params[key],
                "manifest": manifest_value,
                "status": "agree" if agreed else "disagree",
            }
        )
    disagreements = [c["field"] for c in checks if c["status"] == "disagree"]
    return {
        "agrees": not disagreements,
        "checks": checks,
        "tolerances": {
            key: {"rel_tol": rel_tol, "abs_tol": abs_tol}
            for key, _attr, rel_tol, abs_tol in RUN_PARAMS_NUMERIC_CHECKS
        },
        "disagreements": disagreements,
    }


def apply_manifest_authority(
    audits: list[CaseAudit], params: dict
) -> tuple[ManifestAuthority | None, dict | None]:
    """Resolve sweep authority from verified manifests; fail closed on clash.

    When run_params.txt disagrees with the accepted manifests, every accepted
    case that carries a manifest is downgraded to ``run_params_mismatch``
    BEFORE any fit runs, so a stale request file can never steer the fit
    window, the phase reference, or the aggregate labels.  Returns the
    authority (or None without any provenanced case) and the agreement
    record (or None when no authority exists).
    """
    authority = resolve_manifest_authority(audits)
    if authority is None:
        return None, None
    agreement = check_run_params_agreement(params, authority)
    if not agreement["agrees"]:
        for audit in audits:
            if audit.accepted and audit.manifest is not None:
                audit.status = CASE_RUN_PARAMS_MISMATCH
                audit.legacy = False
                audit.reason = (
                    "run_params.txt disagrees with the verified case "
                    "manifests: " + ", ".join(agreement["disagreements"])
                )
    return authority, agreement


def collection_counts(
    audits: list[CaseAudit], *, requested_count: int | None = None
) -> dict[str, int]:
    """Expected/succeeded/failed bookkeeping, including unexpected cases."""
    expected = len(audits) if requested_count is None else int(requested_count)
    unexpected = sum(1 for audit in audits if audit.status == CASE_UNEXPECTED)
    expected_audits = [audit for audit in audits if audit.status != CASE_UNEXPECTED]
    accepted = sum(1 for audit in expected_audits if audit.accepted)
    failed = sum(1 for audit in expected_audits if not audit.accepted)
    counts = {
        "requested": expected,
        "succeeded": accepted,
        "accepted": sum(
            1 for audit in expected_audits if audit.status == CASE_ACCEPTED
        ),
        "accepted_legacy_unprovenanced": sum(
            1
            for audit in expected_audits
            if audit.status == CASE_ACCEPTED_LEGACY
        ),
        "failed": failed,
    }
    if unexpected:
        counts["unexpected"] = unexpected
        counts["blocking_failures"] = failed + unexpected
    return counts


def collection_label_lines(status: str, counts: dict[str, int]) -> list[str]:
    """Human-readable MATLAB header lines carrying the collection status."""
    line = (
        f"% Collection status: {status} "
        f"({counts['succeeded']} of {counts['requested']} requested points collected"
    )
    if counts.get("accepted_legacy_unprovenanced"):
        line += (
            f", {counts['accepted_legacy_unprovenanced']} without "
            "provenance sidecars"
        )
    if counts.get("failed"):
        line += f", {counts['failed']} failed"
    line += ")"
    lines = [line]
    if status == COLLECTION_STATUS_PARTIAL:
        lines.append(
            f"% PARTIAL COLLECTION — omitted points are listed in "
            f"{COLLECTION_FAILURES_FILENAME}"
        )
    if counts.get("accepted_legacy_unprovenanced"):
        lines.append(
            "% Contains data collected without provenance sidecars "
            "(--allow_legacy_unprovenanced)"
        )
    return lines


def build_failure_table_text(
    results_dir: str,
    audits: list[CaseAudit],
    counts: dict[str, int],
    collection_status: str,
    generation_id: str | None = None,
    quarantined_prior_artifacts: list[dict[str, str]] | None = None,
) -> str:
    """Build the machine-readable per-point audit table text.

    Every requested frequency appears exactly once, with its verification
    status, reason, source CSV, and the fingerprint expected from its
    sidecar, so the table doubles as a rerun worklist.  ``generation_id``
    ties the record to the collection generation that produced it.

    ``quarantined_prior_artifacts`` records the source -> quarantine-
    destination moves performed when this failure aborted a collection that
    displaced a prior aggregate (Item 1.3 / review010): each entry is a
    ``{"source", "destination"}`` pair, and the key uses the same
    ``provenance.quarantined_prior_artifacts`` field name the successful
    collection manifest uses.  ``None`` (the successful-collection path,
    where the quarantine happens later, at claim time) omits the section.
    """
    payload = {
        "schema_version": COLLECTION_FAILURE_SCHEMA_VERSION,
        "kind": "freq-response-collection-failures",
        "generation_id": generation_id,
        "results_dir": os.path.abspath(results_dir),
        "collection_status": collection_status,
        "counts": counts,
        "points": [audit.to_dict() for audit in audits],
        "written_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
    }
    if quarantined_prior_artifacts is not None:
        payload["provenance"] = {
            "quarantined_prior_artifacts": list(quarantined_prior_artifacts)
        }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_failure_table(
    results_dir: str,
    audits: list[CaseAudit],
    counts: dict[str, int],
    collection_status: str,
    generation_id: str | None = None,
    quarantined_prior_artifacts: list[dict[str, str]] | None = None,
) -> str:
    """Write the failure table atomically; returns its path."""
    path = os.path.join(results_dir, COLLECTION_FAILURES_FILENAME)
    _run_results().atomic_write_text(
        path,
        build_failure_table_text(
            results_dir,
            audits,
            counts,
            collection_status,
            generation_id=generation_id,
            quarantined_prior_artifacts=quarantined_prior_artifacts,
        ),
    )
    return path


# --- Aggregate publication helpers (P4 / review H2, H3, H4) -----------------


def _new_generation_id() -> str:
    """Unique UTC-stamped identifier for one aggregate generation."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _stage_text_output(
    results_dir: str, final_name: str, text: str
) -> tuple[str, str, int]:
    """Write ``text`` to a unique hidden staging file; hash the staged bytes.

    The staging file is created in ``results_dir`` -- the same filesystem as
    the final name, so publication is one ``os.replace`` -- under
    ``.<final_name>.<random>.staged``.  Returns ``(staged_path, sha256_hex,
    byte_length)`` computed from the staged bytes, so the aggregate manifest
    can advertise exactly what is about to be published.  A failed staging
    write removes the partial file.
    """
    rr = _run_results()
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{final_name}.", suffix=".staged", dir=str(results_dir)
    )
    staged_path = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        digest = rr.file_sha256(staged_path)
        size = staged_path.stat().st_size
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return str(staged_path), digest, size


def _stage_binary_output(
    results_dir: str, final_name: str, source_path: str | os.PathLike[str]
) -> tuple[str, str, int]:
    """Copy a prepared binary into a same-filesystem staged publication file."""
    rr = _run_results()
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{final_name}.", suffix=".staged", dir=str(results_dir)
    )
    staged_path = Path(staged_name)
    try:
        with open(source_path, "rb") as source, os.fdopen(descriptor, "wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        digest = rr.file_sha256(staged_path)
        size = staged_path.stat().st_size
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return str(staged_path), digest, size


def _render_plot_output(
    *,
    frequencies: list[float],
    gains_db: list[float],
    phases_deg: list[float],
    title: str,
) -> str:
    """Render a Bode plot to private in-repo scratch outside the results
    directory.

    Plotting may happen before the aggregate claim is acquired, but the
    prepared file lives under the repository scratch root (``00runs/tmp/``),
    so it is invisible to collectors scanning ``results_dir`` and never
    touches the system temporary directory.  While holding the claim,
    publication copies these bytes into a unique same-directory staging file
    via :func:`_stage_binary_output`.
    """
    rr = _run_results()
    scratch_dir = rr.default_quarantine_root().parent
    descriptor, prepared_name = tempfile.mkstemp(
        prefix="smd-msrr-BodePlot.", suffix=".png", dir=str(scratch_dir)
    )
    os.close(descriptor)
    prepared_path = Path(prepared_name)
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
        ax1.semilogx(
            frequencies, gains_db, "b-o", markersize=3, linewidth=1.2
        )
        ax1.set_ylabel("Gain (dB)")
        ax1.set_title(title)
        ax1.grid(True, which="both", linestyle="--", alpha=0.7)
        ax2.semilogx(
            frequencies, phases_deg, "r-o", markersize=3, linewidth=1.2
        )
        ax2.set_ylabel("Phase (degrees)")
        ax2.set_xlabel("Frequency (rad/s)")
        ax2.grid(True, which="both", linestyle="--", alpha=0.7)
        fig.tight_layout()
        fig.savefig(prepared_path, dpi=150, format="png")
        plt.close(fig)
    except BaseException:
        prepared_path.unlink(missing_ok=True)
        raise
    return str(prepared_path)


def _staging_leftover_paths(results_dir: str) -> list[Path]:
    """Hidden staging leftovers of interrupted collections in ``results_dir``.

    Covers both the ``.staged`` names of the claimed publication protocol and
    the ``.partial`` scratch names of ``atomic_write_text`` for every
    aggregate artifact, so a crashed run cannot litter the results directory
    with half-written hidden files.
    """
    leftovers: list[Path] = []
    root = Path(results_dir)
    for name in (
        AGGREGATE_M_FILENAME,
        AGGREGATE_CSV_FILENAME,
        COLLECTION_FAILURES_FILENAME,
        COLLECTION_MANIFEST_FILENAME,
        AGGREGATE_PLOT_FILENAME,
    ):
        for suffix in (".staged", ".partial"):
            leftovers.extend(root.glob(f".{name}.*{suffix}"))
    return sorted(set(leftovers))


def _aggregate_current_names(
    results_dir: str,
    *,
    regenerate_plot: bool,
) -> list[str]:
    """Current-name aggregate artifact paths one generation replaces (H2).

    The four current-name aggregate artifacts (``.m``, ``.csv``, failure
    table, manifest), every hidden staging leftover of an interrupted
    collection, and the prior ``BodePlot.png``.  The plot is always
    quarantined because it belongs to the prior aggregate generation.
    """
    candidates: list[str] = [
        os.path.join(results_dir, AGGREGATE_M_FILENAME),
        os.path.join(results_dir, AGGREGATE_CSV_FILENAME),
        os.path.join(results_dir, COLLECTION_FAILURES_FILENAME),
        os.path.join(results_dir, COLLECTION_MANIFEST_FILENAME),
    ]
    # The plot belongs to the aggregate generation.  Always quarantine the
    # prior current-name plot, whether or not the replacement requests one.
    candidates.append(os.path.join(results_dir, AGGREGATE_PLOT_FILENAME))
    candidates.extend(
        str(path) for path in _staging_leftover_paths(results_dir)
    )
    return candidates


def _pair_quarantine_moves(
    existing_sources: list[str], destinations: tuple[Path, ...]
) -> list[dict[str, str]]:
    """Pair pre-move source snapshots with quarantine destinations (review010 1.3).

    ``helpers.run_results.quarantine_existing_artifacts`` returns only the
    destination of every move and is shared by the startup, transients, and
    setpoint workflows, so its return type stays untouched.  The caller
    snapshots the candidate paths that exist BEFORE the move; the helper
    moves exactly those paths (sorted by path string, one destination per
    move in order), so zipping the pre-move snapshot with the returned
    destinations reproduces every source -> destination pair.  The snapshot
    must be taken before the move: re-checking existence afterwards would
    always find the sources already gone.  Each record is
    ``{"source": ..., "destination": ...}``.
    """
    if len(existing_sources) != len(destinations):
        # Defensive: the shared helper moved a different number of files
        # than the pre-move snapshot predicted.  Keep the destinations honest
        # instead of mis-pairing sources.
        return [
            {"source": "", "destination": str(destination)}
            for destination in destinations
        ]
    return [
        {"source": source, "destination": str(destination)}
        for source, destination in zip(existing_sources, destinations)
    ]


def _quarantine_current_aggregate(
    results_dir: str,
    *,
    regenerate_plot: bool,
) -> tuple[Path, ...]:
    """Move the prior current-name aggregate set out of the way (H2).

    Quarantines under ``00runs/tmp/quarantine/<utc-stamp>/``: the four
    current-name aggregate artifacts (``.m``, ``.csv``, failure table,
    manifest), every hidden staging leftover of an interrupted collection,
    and the prior ``BodePlot.png`` regardless of whether the new generation
    requests a replacement.  Returns the quarantine destination of every move
    (empty when nothing was present, as for a first collection).  The
    aggregate claim file is deliberately NOT touched: deleting it would let
    a third writer slip past a waiter still blocked on the old inode.
    """
    candidates = _aggregate_current_names(
        results_dir,
        regenerate_plot=regenerate_plot,
    )
    return _run_results().quarantine_existing_artifacts(candidates)


def _collector_python_hashes() -> dict[str, str]:
    """SHA-256 of the Python sources that shape the published aggregate (H4).

    Covers the collector module itself, ``freq/_common.py`` (the fitter and
    formatting helpers whose behavior the published values reflect),
    ``freq/paths.py`` (results-directory resolution), and
    ``helpers/run_results.py`` (the provenance/publication library).  This
    section is separate from the per-case ``workflow_python`` hashes recorded
    by the sweep runner: it describes the COLLECTOR that produced the
    aggregate, not the runners that produced the case CSVs.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    section: dict[str, str] = {}
    for path in (
        here,
        here.parent / "_common.py",
        here.parent / "paths.py",
        here.parent / "sweep_manifest.py",
        Path(rr.__file__).resolve(),
    ):
        resolved = path.resolve()
        try:
            label = resolved.relative_to(repo_root).as_posix()
        except ValueError:
            label = resolved.as_posix()
        section[label] = rr.file_sha256(resolved)
    return section


def verify_aggregate_outputs(
    results_dir: str, manifest_payload: dict | None = None
) -> list[str]:
    """Verify every output advertised by an aggregate manifest (P4 reader).

    Recomputes the SHA-256 digest and byte length of each entry in the
    manifest's ``outputs`` section and compares them against the published
    files in ``results_dir``.  Returns the list of problems found; an empty
    list means every advertised output is byte-identical to what the
    collector hashed at staging time.  A missing, unreadable, or hash-less
    manifest is itself reported as a problem, so callers fail closed on a
    pre-protocol aggregate instead of consuming it unverified.
    """
    rr = _run_results()
    if manifest_payload is None:
        manifest_path = os.path.join(results_dir, COLLECTION_MANIFEST_FILENAME)
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as exc:
            return [f"aggregate manifest unreadable ({manifest_path}): {exc}"]
        if not isinstance(loaded, dict):
            return [
                f"aggregate manifest is not a JSON object ({manifest_path})"
            ]
        manifest_payload = loaded
    outputs = manifest_payload.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        return ["aggregate manifest carries no outputs section to verify"]
    problems: list[str] = []
    for name in sorted(outputs):
        entry = outputs[name]
        path = os.path.join(results_dir, name)
        if (
            not isinstance(entry, dict)
            or "sha256" not in entry
            or "bytes" not in entry
        ):
            problems.append(f"{name}: manifest output entry lacks sha256/bytes")
            continue
        if not os.path.exists(path):
            problems.append(f"{name}: advertised output is missing")
            continue
        try:
            digest = rr.file_sha256(path)
            size = os.path.getsize(path)
        except OSError as exc:
            problems.append(f"{name}: cannot hash published output: {exc}")
            continue
        expected_digest = str(entry["sha256"]).strip().lower()
        if digest != expected_digest:
            problems.append(
                f"{name}: sha256 mismatch (manifest {expected_digest}, "
                f"file {digest})"
            )
        try:
            expected_size = int(entry["bytes"])
        except (TypeError, ValueError):
            problems.append(f"{name}: manifest byte length is not an integer")
            continue
        if size != expected_size:
            problems.append(
                f"{name}: byte length mismatch (manifest {expected_size}, "
                f"file {size})"
            )
    return problems


def parse_args(argv: list[str] | None = None):
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Collect MSRR frequency response results and compute Bode plot data (parallel)."
    )
    parser.add_argument(
        "--results_dir",
        type=str,
        default=None,
        help=(
            "Path to the frequency response results directory "
            "(default: 00runs/freq/<core_model>/power_<tag>)"
        ),
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core model used to derive default results_dir",
    )
    parser.add_argument(
        "--power",
        type=float,
        default=1.0,
        help="Power level used to derive default results_dir",
    )
    parser.add_argument(
        "--plot", action="store_true",
        help="Generate BodePlot.png as part of the atomic aggregate publication",
    )
    parser.add_argument(
        "--show_plot", action="store_true",
        help="Display the already-published plot interactively (requires --plot)",
    )
    parser.add_argument("--n_jobs", type=int, default=default_cpu_count(),
                        help=f"Number of parallel fitting jobs (default: {default_cpu_count()})")
    parser.add_argument(
        "--print_each",
        action="store_true",
        help="Print one output line per frequency point",
    )
    parser.add_argument(
        "--allow_partial",
        action="store_true",
        help=(
            "Write a clearly labeled PARTIAL aggregate (plus a machine-readable "
            "failure table) when some requested points fail verification, and "
            "exit 0; without this flag any failure aborts with no aggregate."
        ),
    )
    parser.add_argument(
        "--allow_legacy_request_manifest",
        action="store_true",
        help=(
            "LEGACY: allow collection without sweep_request.manifest.json, "
            "falling back to mutable run_params.txt. Outputs are explicitly "
            "labeled as legacy request authority."
        ),
    )
    parser.add_argument(
        "--allow_legacy_unprovenanced",
        action="store_true",
        help=(
            "Accept case CSVs that lack usable provenance sidecars, labeling "
            "the aggregate as containing unprovenanced legacy data; without "
            "this flag such cases are failures."
        ),
    )
    parser.add_argument(
        "--allow_unknown_mapping_freqs",
        action="store_true",
        help=(
            "Accept frequencies listed in a sweep mapping file "
            "(sin_mag_by_freq.csv / stop_time_by_freq.csv) that are not part "
            "of the requested frequency grid; they are ignored. Without this "
            "flag unknown mapping entries are rejected."
        ),
    )
    parser.add_argument(
        "--fit_start",
        type=float,
        default=None,
        help=(
            "Start time for fit window in seconds (default: perturbation "
            "start from the verified case manifests; run_params.txt "
            "ss_time when no provenanced case exists). Validated: a start "
            "earlier than the perturbation start minus "
            f"{FIT_START_PRE_FORCING_TOLERANCE_S:g} s is rejected with a "
            "request-validation error before any fit; pass "
            "--fit_start_allow_pre_forcing for the unsafe diagnostic "
            "override."
        ),
    )
    parser.add_argument(
        "--fit_start_allow_pre_forcing",
        action="store_true",
        help=(
            "UNSAFE/diagnostic: allow --fit_start to open the fit window "
            "before the verified perturbation start. Samples before forcing "
            "begins carry no transfer-function information, so every output "
            "row of such a collection is labeled non_transfer_function=True "
            "in the aggregate CSV, the collection manifest and .m header "
            "record the override, and the console prints a warning."
        ),
    )
    parser.add_argument(
        "--fit_end",
        type=float,
        default=None,
        help="End time for fit window in seconds (default: stop_time if set).",
    )
    parser.add_argument(
        "--fit_window_mode",
        type=str,
        choices=("fixed", "inverse_omega"),
        default="fixed",
        help=(
            "Fit-window mode: 'fixed' uses one window for all frequencies; "
            "'inverse_omega' scales window ~ 1/omega."
        ),
    )
    parser.add_argument(
        "--fit_window_ref_freq",
        type=float,
        default=1e-1,
        help="Reference angular frequency (rad/s) for inverse_omega mode.",
    )
    parser.add_argument(
        "--fit_window_ref_duration",
        type=float,
        default=500.0,
        help="Fit duration (s) at fit_window_ref_freq in inverse_omega mode.",
    )
    parser.add_argument(
        "--fit_trend",
        action="store_true",
        help=(
            "Include a linear trend term in each sine fit "
            "(default: off — only offset plus sine terms are fitted)."
        ),
    )
    parser.add_argument(
        "--fit_min_samples",
        type=int,
        default=10,
        help="Reject fits with fewer finite samples (default: 10).",
    )
    parser.add_argument(
        "--fit_min_cycles",
        type=float,
        default=0.25,
        help=(
            "Reject fits whose window spans fewer forcing periods "
            "(default: 0.25 cycles; use e.g. 0 to disable the floor)."
        ),
    )
    parser.add_argument(
        "--fit_cond_max",
        type=float,
        default=1e8,
        help=(
            "Reject fits whose design-matrix condition number exceeds this "
            "value (default: 1e8)."
        ),
    )
    parser.add_argument(
        "--claim_timeout_s",
        type=float,
        default=None,
        help=(
            "Maximum seconds to wait for the aggregate claim when another "
            "collection holds the FreqResponseResults basename; expiry "
            "raises ResultSlotClaimTimeout naming the holder. Default: "
            "wait indefinitely (previous behavior)."
        ),
    )
    args = parser.parse_args(argv)
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    if args.results_dir is None:
        args.results_dir = str(
            default_freq_case_dir(
                repo_root,
                core_model=args.core_model,
                power=args.power,
            )
        )
    return args


def resolve_fit_window(
    freq_point: float,
    fit_start: float,
    fit_end_limit: float | None,
    mode: str,
    ref_freq: float,
    ref_duration: float,
) -> tuple[float | None, float | None]:
    if mode == "inverse_omega":
        if freq_point <= 0:
            raise ValueError("frequency must be positive for inverse_omega fit window")
        if ref_freq <= 0 or ref_duration <= 0:
            raise ValueError("fit_window_ref_freq and fit_window_ref_duration must be positive")
        fit_window = ref_duration * (ref_freq / freq_point)
        fit_end = fit_start + fit_window
        if fit_end_limit is not None:
            fit_end = min(fit_end, fit_end_limit)
        return fit_end, fit_end - fit_start

    fit_end = fit_end_limit
    if fit_end is None:
        return None, None
    return fit_end, fit_end - fit_start


class MappingValidationError(ValueError):
    """A sweep-mapping file is malformed, incomplete, or inconsistent.

    Raised instead of the historical silent fallbacks: duplicate normalized
    frequency keys (previously last-row-wins), unreadable files or columns,
    non-finite values, missing coverage of the requested grid, and entries
    outside the requested grid (the latter excused only by the explicit
    --allow_unknown_mapping_freqs flag).
    """


def _read_freq_mapping(
    results_dir: str, filename: str, value_name: str
) -> dict[str, float] | None:
    """Read a per-frequency mapping CSV with strict validation (H5).

    Returns None only when the file is absent.  A present file must be a
    readable CSV with ``frequency_rad_s`` and ``value_name`` columns, hold
    parseable finite numbers, and contain no duplicate normalized frequency
    keys — a key listed twice used to be resolved by silent last-row-wins,
    which hid malformed sweeps.
    """
    mapping_path = os.path.join(results_dir, filename)
    if not os.path.exists(mapping_path):
        return None
    try:
        df = pd.read_csv(mapping_path)
    except Exception as exc:
        raise MappingValidationError(
            f"{filename}: unreadable CSV: {exc}"
        ) from exc
    if "frequency_rad_s" not in df.columns or value_name not in df.columns:
        raise MappingValidationError(
            f"{filename}: missing required columns (need 'frequency_rad_s' "
            f"and '{value_name}'; got {list(df.columns)})"
        )
    mapping: dict[str, float] = {}
    for freq_value, entry_value in df[
        ["frequency_rad_s", value_name]
    ].itertuples(index=False, name=None):
        try:
            key = format_frequency_key(freq_value)
        except (TypeError, ValueError) as exc:
            raise MappingValidationError(
                f"{filename}: unparseable frequency {freq_value!r}: {exc}"
            ) from exc
        if key in mapping:
            raise MappingValidationError(
                f"{filename}: duplicate frequency key {key!r} (the file "
                "lists one frequency more than once after key normalization; "
                "last-row-wins is no longer accepted)"
            )
        try:
            value = float(entry_value)
        except (TypeError, ValueError) as exc:
            raise MappingValidationError(
                f"{filename}: unparseable {value_name} {entry_value!r} for "
                f"frequency key {key!r}: {exc}"
            ) from exc
        if not math.isfinite(value):
            raise MappingValidationError(
                f"{filename}: non-finite {value_name} for frequency key "
                f"{key!r}"
            )
        mapping[key] = value
    return mapping


def read_sin_mag_mapping(results_dir: str) -> dict[str, float] | None:
    """Per-frequency perturbation amplitudes (``sin_mag_by_freq.csv``)."""
    return _read_freq_mapping(results_dir, "sin_mag_by_freq.csv", "sin_mag")


def read_stop_time_mapping(results_dir: str) -> dict[str, float] | None:
    """Per-frequency stop times (``stop_time_by_freq.csv``)."""
    return _read_freq_mapping(
        results_dir, "stop_time_by_freq.csv", "stop_time_s"
    )


def validate_frequency_mapping(
    mapping: dict[str, float],
    filename: str,
    requested_keys: list[str],
    *,
    value_name: str,
    require_coverage: bool,
    allow_unknown: bool,
) -> None:
    """Validate one mapping against the requested frequency grid (H5).

    Duplicate keys are already rejected by the reader, so coverage means
    exactly one entry per requested frequency.  ``require_coverage`` demands
    an entry for every requested frequency (always true for the amplitude
    mapping; for the stop-time mapping only when it is the selected
    stop-time policy).  Entries outside the requested grid are rejected
    unless ``allow_unknown`` (--allow_unknown_mapping_freqs).
    """
    missing = [key for key in requested_keys if key not in mapping]
    unknown = sorted(set(mapping) - set(requested_keys))
    if require_coverage and missing:
        raise MappingValidationError(
            f"{filename}: no {value_name} entry for requested frequency "
            f"key(s): {', '.join(missing)}"
        )
    if unknown and not allow_unknown:
        raise MappingValidationError(
            f"{filename}: entries for frequencies outside the requested "
            f"sweep grid: {', '.join(unknown)} (pass "
            "--allow_unknown_mapping_freqs to accept them)"
        )


def mapping_file_manifest(
    results_dir: str,
    sin_mag_map: dict[str, float] | None,
    stop_time_map: dict[str, float] | None,
    *,
    stop_time_mapping_selected: bool,
) -> dict:
    """Hash/summary record of the sweep-mapping files for the manifest.

    Only present files get an entry; ``selected`` states whether the mapping
    actually steered the collection (amplitude: whenever present; stop time:
    only when no single --fit_end overrides the per-frequency policy).
    """
    section: dict = {}
    for filename, mapping, selected in (
        ("sin_mag_by_freq.csv", sin_mag_map, sin_mag_map is not None),
        ("stop_time_by_freq.csv", stop_time_map, stop_time_mapping_selected),
    ):
        if mapping is None:
            section[filename] = None
            continue
        path = os.path.join(results_dir, filename)
        section[filename] = {
            "sha256": _run_results().file_sha256(path),
            "rows": len(mapping),
            "selected": bool(selected),
        }
    return section


def process_single_freq(freq_point: float, results_dir: str,
                        sin_mag: float, ss_time: float,
                        fit_start: float, fit_end: float | None,
                        *,
                        fit_trend: bool = False,
                        fit_min_samples: int = 10,
                        fit_min_cycles: float = 0.25,
                        fit_cond_max: float = 1e8) -> dict:
    """
    Read simulation CSV for a single frequency point, fit a sine wave,
    and return gain/phase results. Designed to be called by worker threads.

    The sine is fitted with the shared simultaneous linear least-squares
    fitter ``freq._common.fit_sine_least_squares`` (offset always estimated
    with the sine coefficients; optional linear trend via ``fit_trend``).

    Returns
    -------
    dict
        Result dictionary with keys: 'freq', 'success', 'gain', 'gain_dB',
        'phase_deg', 'r_squared', 'error', plus per-fit diagnostics
        ('n_samples', 'n_cycles', 'fit_start', 'fit_end', 'residual_rms',
        'residual_rms_unweighted', 'r_squared_unweighted', 'weighted_intervals',
        'uniformity_metric', 'condition_number', 'c0', 'c1',
        'rejection_reason') on success.  The weighted/unweighted residual pair
        mirrors ``freq._common.SineFitResult``: the primary pair is the fit's
        own objective, the unweighted pair always carries the raw-sample values,
        and both are populated whenever the solve produced coefficients (they
        stay NaN on the earlier rejections).
    """
    result = {
        'freq': freq_point, 'success': False, 'error': None,
        'gain': None, 'gain_dB': None, 'phase_deg': None, 'r_squared': None,
        'fit_start': fit_start, 'fit_end': fit_end, 'fit_window': None,
        'rejection_reason': None,
    }

    work_path = os.path.join(results_dir, f"freq{freq_point:08.5f}")
    file_prefix = f"MSRR_freq{freq_point:08.5f}"
    data_file = os.path.join(work_path, f"{file_prefix}_res.csv")

    if not os.path.exists(data_file):
        result['error'] = "missing CSV"
        return result

    try:
        # Read simulation results
        sim_data = pd.read_csv(data_file)

        # Clean column headers
        sim_data.columns = clean_column_headers(sim_data.columns)

        time = sim_data['time']

        # Find the power column — after header cleaning, try candidates
        power_col = find_power_column(sim_data.columns)

        if power_col is None:
            result['error'] = (
                f"cannot find power column; "
                f"available: {list(sim_data.columns)[:10]}")
            return result

        power = sim_data[power_col]

        # Select data within fit window
        actual_end = float(time.iloc[-1])
        if fit_end is None:
            effective_fit_end = actual_end
        else:
            effective_fit_end = min(float(fit_end), actual_end)
        result['fit_end'] = effective_fit_end
        result['fit_window'] = effective_fit_end - fit_start
        if effective_fit_end <= fit_start:
            result['error'] = "fit_end must be greater than fit_start"
            return result

        mask = (time >= fit_start) & (time <= effective_fit_end)
        time_fit = time.loc[mask].values - fit_start
        power_fit = power.loc[mask].values

        finite_mask = np.isfinite(time_fit) & np.isfinite(power_fit)
        time_fit = time_fit[finite_mask]
        power_fit = power_fit[finite_mask]

        # Never extend the window before forcing begins: a mixture of
        # unforced and forced samples has no transfer-function reading.
        # Sample-floor enforcement is delegated to the shared fitter
        # (min_samples=fit_min_samples), which records n_samples and an
        # explicit rejection reason instead of a caller-side bail-out.

        # Fit sine wave with frequency fixed using the shared simultaneous
        # linear least-squares fitter (offset fitted with the sine terms).
        fit = fit_sine_least_squares(
            time_fit,
            power_fit,
            freq_point,
            fit_trend=fit_trend,
            min_samples=fit_min_samples,
            min_cycles=fit_min_cycles,
            max_condition_number=fit_cond_max,
        )
        result['n_samples'] = fit.n_samples
        result['n_cycles'] = fit.n_cycles
        result['residual_rms'] = fit.residual_rms
        # M1 (REV008-09): carry the second explicit diagnostic pair plus the
        # weighting flag and grid uniformity metric.  Recorded before the
        # rejection check so an ill-conditioned solve that produced
        # coefficients still reports both views, like SineFitResult itself.
        result['residual_rms_unweighted'] = fit.residual_rms_unweighted
        result['r_squared_unweighted'] = fit.r_squared_unweighted
        result['weighted_intervals'] = fit.weighted_intervals
        result['uniformity_metric'] = fit.uniformity_metric
        result['condition_number'] = fit.condition_number
        if not fit.ok or fit.amplitude is None or fit.phase_rad is None:
            reason = fit.rejection_reason or "unknown sine-fit failure"
            result['rejection_reason'] = fit.rejection_reason
            result['error'] = f"sine fit rejected: {reason}"
            return result

        amplitude = float(fit.amplitude)
        phase_rad = float(fit.phase_rad)
        offset = fit.c0
        r_squared = fit.r_squared

        result['c0'] = offset
        result['c1'] = fit.c1
        result['fit_start_fitted'] = fit.fit_start + fit_start
        result['fit_end_fitted'] = fit.fit_end + fit_start
        result['rejection_reason'] = None

        # Gain = output amplitude / input amplitude
        # Input is sin_mag pcm = sin_mag * 1E-5 in dk/k
        gain = amplitude / (sin_mag * 1e-5)
        gain_dB = 20.0 * np.log10(gain) if gain > 0 else -np.inf
        phase_deg = phase_relative_to_perturbation_start_deg(
            phase_rad=phase_rad,
            freq_point=freq_point,
            fit_start=fit_start,
            perturbation_start=ss_time,
        )

        result['gain'] = gain
        result['gain_dB'] = gain_dB
        result['phase_deg'] = phase_deg
        result['r_squared'] = r_squared
        result['success'] = True

    except Exception as e:
        result['error'] = str(e)

    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results_dir = args.results_dir
    rr = _run_results()
    # One generation ID per collection run (P4): the published manifest and
    # the failure record carry it, so current-name artifacts can always be
    # told apart from anything left by an earlier generation.
    generation_id = _new_generation_id()

    # Immutable sweep request authority.  Normal collection refuses to infer
    # completeness from mutable run_params.txt.  A separately named legacy
    # flag preserves historical directories while labeling that weaker mode.
    params = read_run_params(results_dir)
    request_payload: dict | None = None
    request_error: str | None = None
    try:
        request_payload = sweep_manifest.load_sweep_request_manifest(results_dir)
    except sweep_manifest.SweepManifestError as exc:
        request_error = str(exc)
        if not args.allow_legacy_request_manifest:
            print(
                "Sweep request validation failed: "
                f"{exc}. Normal collection requires "
                f"{sweep_manifest.SWEEP_REQUEST_FILENAME}; use "
                "--allow_legacy_request_manifest only for historical data.",
                file=sys.stderr,
            )
            return 2

    if request_payload is not None:
        request = request_payload["request"]
        request_cases = list(request["cases"])
        freq_space = np.asarray(
            [float(case["frequency_rad_s"]) for case in request_cases],
            dtype=float,
        )
        requested_keys = [str(case["frequency_key"]) for case in request_cases]
        request_case_by_key = {
            str(case["frequency_key"]): case for case in request_cases
        }
        num_freq = len(freq_space)
        freq_min = min(float(value) for value in freq_space)
        freq_max = max(float(value) for value in freq_space)
        amplitudes = {
            key: float(request_case_by_key[key]["perturbation_amplitude_pcm"])
            for key in requested_keys
        }
        unique_amplitudes = set(amplitudes.values())
        sin_mag = next(iter(unique_amplitudes)) if len(unique_amplitudes) == 1 else float(
            request_cases[0]["perturbation_amplitude_pcm"]
        )
        sin_mag_map = amplitudes if len(unique_amplitudes) != 1 else None
        stop_time_map = {
            key: float(request_case_by_key[key]["stop_time_s"])
            for key in requested_keys
        }
        ss_time = float(request["perturbation_start_time_s"])
        stop_time = None
        fit_end_limit = args.fit_end
        stop_time_mapping_selected = args.fit_end is None
        mapping_files = {
            sweep_manifest.SWEEP_REQUEST_FILENAME: {
                "sha256": rr.file_sha256(
                    os.path.join(
                        results_dir, sweep_manifest.SWEEP_REQUEST_FILENAME
                    )
                ),
                "selected": True,
                "authority": True,
            }
        }
        perturbation_policy = {
            "mode": (
                "uniform" if len(unique_amplitudes) == 1
                else "per_frequency_request_manifest"
            ),
            "sin_mag_pcm": (
                float(sin_mag) if len(unique_amplitudes) == 1 else None
            ),
            "request_manifest": sweep_manifest.SWEEP_REQUEST_FILENAME,
        }
        request_authority = "sweep_request_manifest"
    else:
        # Explicit legacy mode only: reconstruct the requested grid from
        # run_params.txt and optional mapping CSVs.
        freq_min = params.get("freq_min", 1e-2)
        freq_max = params.get("freq_max", 1e1)
        num_freq = int(params.get("num_freq", 100))
        sin_mag = float(params.get("sin_mag", 1.0))
        ss_time = params.get("ss_time", 2000.0)
        stop_time = params.get("stop_time", None)
        fit_end_limit = args.fit_end if args.fit_end is not None else stop_time
        freq_space = np.logspace(
            np.log10(freq_min), np.log10(freq_max), num=num_freq
        )
        requested_keys = [
            format_frequency_key(float(fp)) for fp in freq_space
        ]
        request_case_by_key = {}
        try:
            sin_mag_map = read_sin_mag_mapping(results_dir)
            stop_time_map = read_stop_time_mapping(results_dir)
            stop_time_mapping_selected = (
                stop_time_map is not None and args.fit_end is None
            )
            if sin_mag_map is not None:
                validate_frequency_mapping(
                    sin_mag_map,
                    "sin_mag_by_freq.csv",
                    requested_keys,
                    value_name="sin_mag",
                    require_coverage=True,
                    allow_unknown=args.allow_unknown_mapping_freqs,
                )
            if stop_time_map is not None:
                validate_frequency_mapping(
                    stop_time_map,
                    "stop_time_by_freq.csv",
                    requested_keys,
                    value_name="stop_time_s",
                    require_coverage=stop_time_mapping_selected,
                    allow_unknown=args.allow_unknown_mapping_freqs,
                )
            mapping_files = mapping_file_manifest(
                results_dir,
                sin_mag_map,
                stop_time_map,
                stop_time_mapping_selected=stop_time_mapping_selected,
            )
        except MappingValidationError as exc:
            print(f"Mapping validation failed: {exc}", file=sys.stderr)
            return 2
        perturbation_policy = (
            {
                "mode": "per_frequency_mapping",
                "mapping_file": "sin_mag_by_freq.csv",
            }
            if sin_mag_map
            else {"mode": "uniform", "sin_mag_pcm": float(sin_mag)}
        )
        request_authority = "legacy_run_params"
        request_cases = []

    n_jobs = min(args.n_jobs, num_freq)

    def expected_amplitude(freq_point: float) -> float:
        """Perturbation amplitude requested for one point."""
        key = format_frequency_key(freq_point)
        if request_payload is not None:
            return float(request_case_by_key[key]["perturbation_amplitude_pcm"])
        if sin_mag_map:
            return float(sin_mag_map.get(key, sin_mag))
        return float(sin_mag)

    print(f"Collecting results from: {os.path.abspath(results_dir)}")
    print(f"Frequency range: {freq_min:.4f} to {freq_max:.4f} rad/s "
          f"({num_freq} points)")
    if sin_mag_map:
        print("Perturbation amplitude: per-frequency mapping")
    else:
        print(f"Perturbation amplitude: {sin_mag} pcm")
    print(f"Steady-state time (requested): {ss_time} s")
    print(
        "Phase reference: perturbation start from the immutable sweep "
        "request and verified case manifests"
    )
    if stop_time_map and args.fit_end is None:
        print("Stop time limit: per-frequency mapping")
    elif stop_time is not None:
        print(f"Stop time limit: {stop_time:g} s")
    print(
        "Sine fit: least squares "
        f"(trend {'on' if args.fit_trend else 'off'}, "
        f"min_samples={args.fit_min_samples}, "
        f"min_cycles={args.fit_min_cycles:g}, "
        f"cond_max={args.fit_cond_max:g})"
    )
    print(f"Parallel jobs: {n_jobs}")
    print(
        "Provenance: immutable sweep request and per-case sidecars required"
        + (
            "; legacy unprovenanced CSVs accepted"
            if args.allow_legacy_unprovenanced
            else ""
        )
        + ("; partial aggregates allowed" if args.allow_partial else "")
    )
    print("-" * 70)

    # ---- Provenance audit (C3/C4): verify every requested point first -----
    audits: list[CaseAudit] = [None] * len(freq_space)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=n_jobs) as executor:
        audit_map = {
            executor.submit(
                audit_case,
                float(fp),
                os.path.abspath(results_dir),
                expected_amplitude(float(fp)),
                allow_legacy=args.allow_legacy_unprovenanced,
                request_manifest=request_payload,
                expected_stop_time=(
                    float(
                        request_case_by_key[
                            format_frequency_key(float(fp))
                        ]["stop_time_s"]
                    )
                    if request_payload is not None else None
                ),
                expected_intervals=(
                    int(
                        request_case_by_key[
                            format_frequency_key(float(fp))
                        ]["number_of_intervals"]
                    )
                    if request_payload is not None else None
                ),
            ): idx
            for idx, fp in enumerate(freq_space)
        }
        for future in as_completed(audit_map):
            idx = audit_map[future]
            try:
                audits[idx] = future.result()
            except Exception as exc:  # defensive: audit itself must not crash
                audits[idx] = CaseAudit(
                    freq=float(freq_space[idx]),
                    frequency_key=format_frequency_key(freq_space[idx]),
                    source_csv=None,
                    status=CASE_VALIDATION_FAILED,
                    reason=f"provenance audit error: {exc}",
                )

    if request_payload is not None:
        expected_key_set = set(requested_keys)
        for directory in sorted(Path(results_dir).glob("freq*")):
            if not directory.is_dir():
                continue
            csv_candidates = sorted(directory.glob("*_res.csv"))
            if not csv_candidates:
                continue
            parsed_frequency: float | None = None
            for csv_candidate in csv_candidates:
                payload = rr.read_manifest_sidecar(csv_candidate)
                manifest = payload.get("manifest") if isinstance(payload, dict) else None
                overrides = manifest.get("overrides") if isinstance(manifest, dict) else None
                if isinstance(overrides, dict):
                    parsed_frequency = _as_float_or_none(
                        overrides.get("perturbationOmega")
                    )
                if parsed_frequency is not None:
                    break
            if parsed_frequency is None:
                try:
                    parsed_frequency = float(directory.name.removeprefix("freq"))
                except ValueError:
                    continue
            key = format_frequency_key(parsed_frequency)
            if key not in expected_key_set:
                audits.append(
                    CaseAudit(
                        freq=float(parsed_frequency),
                        frequency_key=key,
                        source_csv=str(csv_candidates[0]),
                        status=CASE_UNEXPECTED,
                        reason=(
                            "case is present in the campaign directory but is "
                            "not listed by the immutable sweep request"
                        ),
                    )
                )

    enforce_sweep_common_fields(audits)

    # ---- Manifest authority (C3): verified manifests are authoritative ----
    # The accepted manifests define the perturbation start (fit lower bound
    # and phase reference) and the power/model/package labels;
    # In immutable-request mode run_params.txt is informational and cannot
    # redefine or reject the campaign.  Only explicit legacy-request mode
    # treats disagreement as a fail-closed provenance error.
    authority = resolve_manifest_authority(audits)
    run_params_agreement = (
        check_run_params_agreement(params, authority)
        if authority is not None else None
    )
    if request_payload is None:
        authority, run_params_agreement = apply_manifest_authority(audits, params)

    counts = collection_counts(audits, requested_count=num_freq)
    for audit in audits:
        if not audit.accepted:
            print(
                f"  freq = {audit.freq:8.5f} rad/s | REJECTED — "
                f"{audit.status}: {audit.reason}"
            )
        elif args.print_each:
            print(f"  freq = {audit.freq:8.5f} rad/s | verified ({audit.status})")

    if authority is not None:
        label_power = (
            authority.power
            if authority.power is not None
            else params.get("power", 1.0)
        )
        label_model = authority.model_name
        label_package = authority.package
        label_package_name = authority.package_name
        perturbation_start = (
            authority.perturbation_start
            if authority.perturbation_start is not None
            else ss_time
        )
        authority_source = (
            "sweep_request_manifest"
            if request_payload is not None
            else "verified_case_manifests"
        )
        assert run_params_agreement is not None  # coupled by construction
        print(
            f"Manifest authority: reference case "
            f"{authority.reference_frequency:g} rad/s — perturbation start "
            f"{perturbation_start:g} s, power {label_power}, "
            f"model {label_model}, package {label_package}"
        )
        if run_params_agreement["agrees"]:
            print(
                "run_params.txt agreement: OK within the documented "
                "tolerances"
            )
        else:
            suffix = (
                " — ignored because the immutable sweep request is authoritative"
                if request_payload is not None
                else " — affected points rejected before any fit"
            )
            print(
                "run_params.txt DISAGREEMENT: "
                + ", ".join(run_params_agreement["disagreements"])
                + suffix
            )
    else:
        label_power = params.get("power", 1.0)
        label_model = None
        label_package = None
        label_package_name = None
        perturbation_start = ss_time
        authority_source = "run_params_fallback"
        print(
            "Manifest authority: none (no provenanced cases accepted); "
            "labels and phase reference fall back to run_params.txt"
        )

    # ---- Fit-start validation (H1): fail closed BEFORE any fit ------------
    # The default lower bound is the perturbation start recorded in the
    # verified manifests (run_params.txt fallback without provenanced
    # cases).  An explicit --fit_start may not open the window before the
    # perturbation start minus the documented slack: samples before forcing
    # begins carry no transfer-function information, so a pre-forcing
    # window is a request-validation error, not a per-point fit failure.
    # --fit_start_allow_pre_forcing is the separately named, explicitly
    # unsafe diagnostic override; its outputs are labeled as
    # non-transfer-function below.
    fit_start = (
        args.fit_start if args.fit_start is not None else perturbation_start
    )
    if (
        args.fit_start is not None
        and not args.fit_start_allow_pre_forcing
        and args.fit_start
        < perturbation_start - FIT_START_PRE_FORCING_TOLERANCE_S
    ):
        print(
            "Fit window validation failed: --fit_start "
            f"{args.fit_start:g} s is earlier than the verified "
            f"perturbation start {perturbation_start:g} s minus the "
            f"documented tolerance {FIT_START_PRE_FORCING_TOLERANCE_S:g} s; "
            "a fit window opening before forcing begins admits unforced "
            "samples and is rejected before any fit (use "
            "--fit_start_allow_pre_forcing for the unsafe diagnostic "
            "override).",
            file=sys.stderr,
        )
        return 2
    # Outputs produced through the unsafe override carry an explicit
    # non-transfer-function marker (aggregate CSV rows, manifest, .m
    # header, and the console notice below).
    pre_forcing_fit = bool(
        args.fit_start_allow_pre_forcing
        and args.fit_start is not None
        and args.fit_start < perturbation_start
    )

    # ---- Per-frequency fit-window validation (H1): request errors ---------
    # resolve_fit_window is applied to every accepted point BEFORE the fit
    # dispatch; a stop-time mapping entry (or an explicit --fit_end) that
    # does not clear the fit start is a request-validation error, not a
    # per-point fit failure (process_single_freq keeps its own check as
    # defense in depth).  The resolved windows feed the dispatch loop
    # unchanged.
    fit_windows: dict[int, float | None] = {}
    try:
        for idx, audit in enumerate(audits):
            if not audit.accepted:
                continue
            fp_limit = fit_end_limit
            if fp_limit is None and stop_time_map:
                fp_limit = stop_time_map.get(audit.frequency_key)
            fp_fit_end, _window = resolve_fit_window(
                freq_point=audit.freq,
                fit_start=fit_start,
                fit_end_limit=fp_limit,
                mode=args.fit_window_mode,
                ref_freq=args.fit_window_ref_freq,
                ref_duration=args.fit_window_ref_duration,
            )
            if fp_fit_end is not None and fp_fit_end <= fit_start:
                raise MappingValidationError(
                    f"fit window for {audit.frequency_key} rad/s closes at "
                    f"{fp_fit_end:g} s, at or before the fit start "
                    f"{fit_start:g} s (stop_time_by_freq.csv entry or "
                    "--fit_end); malformed request"
                )
            fit_windows[idx] = fp_fit_end
    except (MappingValidationError, ValueError) as exc:
        print(f"Fit window validation failed: {exc}", file=sys.stderr)
        return 2

    def abort_without_aggregate() -> int:
        # H2: a strict failure must leave NO complete-looking aggregate at
        # the current names.  Under the aggregate claim, the prior set (if
        # any) is quarantined first; only the new failure record is then
        # written, so the current names can never advertise a stale
        # complete aggregate next to an aborted collection.
        claim = rr.acquire_result_claim(
            os.path.join(results_dir, AGGREGATE_BASENAME),
            timeout_s=args.claim_timeout_s,
        )
        try:
            candidates = _aggregate_current_names(
                results_dir, regenerate_plot=False
            )
            # Snapshot the sources that exist BEFORE the quarantine move:
            # quarantine_existing_artifacts moves exactly the currently
            # existing candidates (sorted by path string) and returns one
            # destination per move in that order, so zipping this pre-move
            # snapshot with the returned destinations yields the true
            # source -> destination pairs.  An existence re-check after the
            # move would always find the sources gone.
            existing_sources = sorted(
                str(item) for item in candidates if os.path.lexists(item)
            )
            quarantined = _quarantine_current_aggregate(
                results_dir, regenerate_plot=False
            )
            quarantine_records = _pair_quarantine_moves(
                existing_sources, quarantined
            )
            failure_path = write_failure_table(
                results_dir,
                audits,
                counts,
                COLLECTION_STATUS_PARTIAL,
                generation_id=generation_id,
                quarantined_prior_artifacts=quarantine_records,
            )
        finally:
            claim.release()
        print("-" * 70)
        if quarantined:
            print(
                "Prior aggregate artifacts quarantined: "
                f"{len(quarantined)} file(s) moved out of the current names:"
            )
            for record in quarantine_records:
                source = record.get("source") or "(unpaired source)"
                print(f"  {source} -> {record['destination']}")
        print(
            f"Collection incomplete: {counts['succeeded']} of "
            f"{counts['requested']} requested points verified "
            f"({counts['failed']} failed)."
        )
        print(f"Failure table written to: {failure_path}")
        print(
            "No aggregate written; pass --allow_partial to write a labeled "
            "partial aggregate."
        )
        return 1

    if counts.get("blocking_failures", counts["failed"]) and not args.allow_partial:
        return abort_without_aggregate()
    if counts["succeeded"] == 0:
        # Nothing to collect -- an empty labeled aggregate would be noise.
        return abort_without_aggregate()

    # ---- Fit window and phase reference (manifest-authoritative) ----------
    # fit_start was resolved and validated above, before any abort path or
    # worker dispatch.
    print(f"Phase reference: perturbation start at {perturbation_start:g} s")
    if args.fit_window_mode == "fixed":
        if stop_time_map and args.fit_end is None:
            print(f"Fit window mode: fixed ({fit_start:g} to per-frequency end)")
        else:
            print(f"Fit window mode: fixed ({fit_start:g} to {fit_end_limit if fit_end_limit is not None else 'end'} s)")
    else:
        print(
            "Fit window mode: inverse_omega "
            f"({args.fit_window_ref_duration:g}s at {args.fit_window_ref_freq:g} rad/s)"
        )
        print(f"Fit window lower bound: {fit_start:g} s")
    if pre_forcing_fit:
        assert args.fit_start is not None  # pre_forcing_fit requires it
        print(
            "WARNING: --fit_start_allow_pre_forcing is active; the fit "
            f"window opens {perturbation_start - args.fit_start:g} s before "
            "the verified perturbation start. Rows from this collection are "
            "labeled non_transfer_function=True and are NOT transfer-"
            "function measurements."
        )

    # ---- Sine fits for verified points only --------------------------------
    # Per-frequency fit windows were resolved and validated before the
    # abort paths above (H1: malformed stop-time entries are request-
    # validation errors, not per-point fit failures).
    all_results: dict[int, dict] = {}
    progress_every = max(1, len(freq_space) // 16)
    completed = 0
    with ThreadPoolExecutor(max_workers=n_jobs) as executor:
        future_map = {}
        for idx, audit in enumerate(audits):
            if not audit.accepted:
                continue
            future = executor.submit(
                process_single_freq,
                freq_point=audit.freq,
                results_dir=os.path.abspath(results_dir),
                sin_mag=expected_amplitude(audit.freq),
                ss_time=perturbation_start,
                fit_start=fit_start,
                fit_end=fit_windows[idx],
                fit_trend=args.fit_trend,
                fit_min_samples=args.fit_min_samples,
                fit_min_cycles=args.fit_min_cycles,
                fit_cond_max=args.fit_cond_max,
            )
            future_map[future] = idx
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                all_results[idx] = future.result()
            except Exception as exc:
                all_results[idx] = {
                    "freq": audits[idx].freq,
                    "success": False,
                    "error": str(exc),
                    "gain": None,
                    "gain_dB": None,
                    "phase_deg": None,
                    "r_squared": None,
                }
            completed += 1
            if completed % progress_every == 0 or completed == len(future_map):
                print(
                    f"  Progress: {completed}/{len(future_map)} "
                    f"frequency fits completed"
                )

    # A rejected sine fit is a failed point too: the aggregate must never
    # quietly publish fewer points than the sweep requested (C3).
    for idx, fit_result in all_results.items():
        if not fit_result.get("success"):
            audits[idx].status = CASE_FIT_FAILED
            audits[idx].reason = fit_result.get("error") or "sine fit failed"

    counts = collection_counts(audits)
    if counts["failed"] and not args.allow_partial:
        return abort_without_aggregate()

    # Collect successful results (preserve frequency ordering)
    freq_list = []
    gain_list = []
    phase_list = []
    gain_dB_list = []
    fit_quality = []
    fit_start_list = []
    fit_end_list = []
    fit_window_list = []
    n_samples_list = []
    n_cycles_list = []
    residual_rms_list = []
    residual_rms_unweighted_list = []
    r_squared_unweighted_list = []
    weighted_intervals_list = []
    uniformity_metric_list = []
    condition_number_list = []
    c0_list = []
    c1_list = []

    for idx in sorted(all_results):
        r = all_results[idx]
        if r['success']:
            freq_list.append(r['freq'])
            gain_list.append(r['gain'])
            gain_dB_list.append(r['gain_dB'])
            phase_list.append(r['phase_deg'])
            fit_quality.append(r['r_squared'])
            fit_start_list.append(r['fit_start'])
            fit_end_list.append(r['fit_end'])
            fit_window_list.append(r['fit_window'])
            n_samples_list.append(int(r.get('n_samples', 0)))
            n_cycles_list.append(float(r.get('n_cycles', float('nan'))))
            residual_rms_list.append(
                float(r.get('residual_rms', float('nan'))))
            residual_rms_unweighted_list.append(
                float(r.get('residual_rms_unweighted', float('nan'))))
            r_squared_unweighted_list.append(
                float(r.get('r_squared_unweighted', float('nan'))))
            weighted_intervals_list.append(
                bool(r.get('weighted_intervals', False)))
            uniformity_metric_list.append(
                float(r.get('uniformity_metric', 0.0)))
            condition_number_list.append(
                float(r.get('condition_number', float('nan'))))
            c0_list.append(float(r.get('c0', float('nan'))))
            c1_list.append(float(r.get('c1', 0.0)))

            if args.print_each:
                print(f"  freq = {r['freq']:8.5f} rad/s | "
                      f"gain = {r['gain']:.4f} | "
                      f"gain_dB = {r['gain_dB']:.2f} dB | "
                      f"phase = {r['phase_deg']:.2f} deg | "
                      f"R² = {r['r_squared']:.6f} | "
                      f"fit_window = {r['fit_window']:.3f} s")
        else:
            print(f"  freq = {r['freq']:8.5f} rad/s | "
                  f"SKIPPED — {r['error']}")

    print("-" * 70)

    if counts.get("blocking_failures", counts["failed"]):
        collection_status = COLLECTION_STATUS_PARTIAL
    elif counts["accepted_legacy_unprovenanced"]:
        collection_status = COLLECTION_STATUS_LEGACY
    else:
        collection_status = COLLECTION_STATUS_COMPLETE

    print(f"Collection status: {collection_status}")
    print(
        f"Successfully processed {counts['succeeded']} / {counts['requested']} "
        f"requested frequency points "
        f"({counts['failed']} failed, "
        f"{counts['accepted_legacy_unprovenanced']} without sidecars)."
    )

    # Build the MATLAB .m aggregate text.  Labels are manifest-authoritative:
    # power, model, and package come from the verified case manifests when any
    # provenanced case was accepted.  Publication happens in the claimed,
    # staged block below (P4).
    m_file = os.path.join(results_dir, AGGREGATE_M_FILENAME)
    m_lines = [
        "% MSRR Frequency Response - Nominal Configuration\n",
        f"% Collection generation: {generation_id}\n",
    ]
    if label_model is not None:
        m_lines.append(f"% Model: {label_model}\n")
    if label_package is not None:
        m_lines.append(f"% Package: {label_package}\n")
    m_lines.extend(
        [
            f"% Power level: {label_power}\n",
            f"% Perturbation: {sin_mag} pcm\n",
            f"% Steady-state time: {perturbation_start} s\n\n",
            f"% Phase reference: perturbation start at {perturbation_start} s\n\n",
        ]
    )
    if pre_forcing_fit:
        m_lines.append(
            "% NON-TRANSFER-FUNCTION: fit window opens before the verified "
            "perturbation start (--fit_start_allow_pre_forcing); rows are "
            "not transfer-function measurements\n\n"
        )
    for label_line in collection_label_lines(collection_status, counts):
        m_lines.append(label_line + "\n")
    m_lines.append("\n")
    if args.fit_window_mode == "fixed":
        m_lines.append("% Fit window mode: fixed\n")
        m_lines.append(f"% Fit window: {fit_start} to {fit_end_limit}\n\n")
    else:
        m_lines.append("% Fit window mode: inverse_omega\n")
        m_lines.append(
            f"% Reference: {args.fit_window_ref_duration} s at "
            f"{args.fit_window_ref_freq} rad/s\n\n"
        )
    m_lines.append(format_matlab_assignment("freq", freq_list))
    m_lines.append(format_matlab_assignment("gain", gain_list))
    m_lines.append(format_matlab_assignment("gain_dB", gain_dB_list))
    m_lines.append(format_matlab_assignment("phase", phase_list))
    m_lines.append(format_matlab_assignment("R2", fit_quality))
    m_lines.append(format_matlab_assignment("fit_start_s", fit_start_list))
    m_lines.append(format_matlab_assignment("fit_end_s", fit_end_list))
    m_lines.append(format_matlab_assignment("fit_window_s", fit_window_list))
    m_text = "".join(m_lines)

    # Build the CSV aggregate text (published in the claimed block below).
    csv_file = os.path.join(results_dir, AGGREGATE_CSV_FILENAME)
    results_df = pd.DataFrame({
        'frequency_rad_s': freq_list,
        'gain': gain_list,
        'gain_dB': gain_dB_list,
        'phase_deg': phase_list,
        'R_squared': fit_quality,
        'fit_start_s': fit_start_list,
        'fit_end_s': fit_end_list,
        'fit_window_s': fit_window_list,
        'n_samples': n_samples_list,
        'n_cycles': n_cycles_list,
        'residual_rms': residual_rms_list,
        'residual_rms_unweighted': residual_rms_unweighted_list,
        'r_squared_unweighted': r_squared_unweighted_list,
        'weighted_intervals': weighted_intervals_list,
        'uniformity_metric': uniformity_metric_list,
        'condition_number': condition_number_list,
        'c0': c0_list,
        'c1': c1_list,
        'collection_status': [collection_status] * len(freq_list),
        # Review H1: rows produced through the unsafe pre-forcing override
        # carry an explicit non-transfer-function marker.
        'non_transfer_function': [pre_forcing_fit] * len(freq_list),
    })
    csv_text = results_df.to_csv(index=False)

    # Machine-readable failure table whenever any requested point is not in
    # the aggregate (partial or legacy-labeled collections included).  Built
    # here; staged, hashed, and published with the rest of the generation in
    # the claimed block below.
    failures_text: str | None = None
    failures_path = os.path.join(results_dir, COLLECTION_FAILURES_FILENAME)
    if counts["failed"] or counts["accepted_legacy_unprovenanced"]:
        failures_text = build_failure_table_text(
            results_dir,
            audits,
            counts,
            collection_status,
            generation_id=generation_id,
        )

    # Aggregate collection manifest: accepted case fingerprints plus the
    # collector settings that produced this aggregate.  Written last.
    reference_fields = None
    for audit in audits:
        if audit.accepted and audit.manifest is not None:
            reference_fields = sweep_common_fields(audit.manifest)
            break
    git_info = rr.collect_git_info(results_dir)
    # Honest provenance: report whether the ACCEPTED case sidecars themselves
    # hash the workflow Python sources that produced them (the manifest
    # ``workflow_python`` section).  Sidecars written before workflow Python
    # hashing landed record no such section, so the flag stays False for
    # those collections instead of claiming coverage the fingerprints do not
    # provide.  Legacy unprovenanced points carry no manifest at all and are
    # excluded from the decision.
    hashed_case_manifests = [
        audit.manifest
        for audit in audits
        if audit.accepted and audit.manifest is not None and not audit.legacy
    ]
    workflow_python_hashed = bool(hashed_case_manifests) and all(
        bool(case_manifest.get(rr.WORKFLOW_PYTHON_FILES_KEY))
        for case_manifest in hashed_case_manifests
    )
    if workflow_python_hashed:
        provenance_note = (
            "Case sidecars hash the workflow Python sources that produced "
            "them (manifest workflow_python section): the active runner "
            "module, shared helper modules, and helpers/run_results.py."
        )
    else:
        provenance_note = (
            "These case sidecars do not hash workflow Python sources; case "
            "fingerprints cover Modelica sources and request parameters "
            "only (sidecars predate workflow Python hashing or no case was "
            "provenanced; see helpers/run_results.py)."
        )
    manifest_payload = {
        "schema_version": COLLECTION_MANIFEST_SCHEMA_VERSION,
        "kind": "freq-response-collection",
        # P4 self-verifying aggregate identity: the generation ID labels this
        # publication; "outputs" (filled in at publication time) advertises
        # the SHA-256 + byte length of every staged product; collector_python
        # hashes the collector-side Python sources separately from the
        # per-case workflow_python hashes.
        "generation_id": generation_id,
        "aggregate_csv_schema_version": AGGREGATE_CSV_SCHEMA_VERSION,
        "collection_status": collection_status,
        "results_dir": os.path.abspath(results_dir),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "counts": counts,
        "sweep": {
            "freq_min": float(freq_min),
            "freq_max": float(freq_max),
            "num_freq": int(num_freq),
            # Manifest-authoritative labels (card C3): derived from the
            # verified case manifests; run_params.txt fallback is recorded
            # explicitly via authority_source.
            "power": label_power,
            "sin_mag": float(sin_mag),
            "ss_time": float(perturbation_start),
            "stop_time": (
                float(stop_time) if stop_time is not None else None
            ),
            "perturbation_policy": perturbation_policy,
            "model_name": label_model,
            "package": label_package,
            "package_name": label_package_name,
            "authority_source": authority_source,
            "mapping_files": mapping_files,
            "request_manifest": (
                {
                    "filename": sweep_manifest.SWEEP_REQUEST_FILENAME,
                    "schema_version": request_payload["schema_version"],
                    "campaign_id": request_payload["campaign_id"],
                    "fingerprint": request_payload["fingerprint"],
                }
                if request_payload is not None else None
            ),
            "legacy_request_authority": request_payload is None,
            "legacy_request_reason": (
                request_error if request_payload is None else None
            ),
        },
        "fit_settings": {
            "fit_start": float(fit_start),
            "fit_end_limit": (
                float(fit_end_limit) if fit_end_limit is not None else None
            ),
            "fit_window_mode": args.fit_window_mode,
            "fit_window_ref_freq": float(args.fit_window_ref_freq),
            "fit_window_ref_duration": float(args.fit_window_ref_duration),
            "fit_trend": bool(args.fit_trend),
            "fit_min_samples": int(args.fit_min_samples),
            "fit_min_cycles": float(args.fit_min_cycles),
            "fit_cond_max": float(args.fit_cond_max),
            # Review H1: the unsafe pre-forcing override is recorded, and
            # rows produced through it are explicitly labeled as not a
            # transfer-function measurement.
            "fit_start_pre_forcing_override": bool(
                args.fit_start_allow_pre_forcing
            ),
            "non_transfer_function": pre_forcing_fit,
        },
        "sweep_common_fields": reference_fields,
        "accepted_cases": [
            {
                "frequency_rad_s": audit.freq,
                "frequency_key": audit.frequency_key,
                "source_csv": audit.source_csv,
                "fingerprint": audit.fingerprint_claimed,
                "legacy_unprovenanced": audit.legacy,
            }
            for audit in audits
            if audit.accepted
        ],
        "points": [audit.to_dict() for audit in audits],
        "collector": {
            "module": "freq.collectFreqNominalParallel",
            "workflow_version": (
                f"freq-collectFreqNominalParallel/run_results-{rr.__version__}"
            ),
            "python_version": platform.python_version(),
            "git_commit": git_info.get("commit"),
            "git_dirty": git_info.get("dirty"),
            "claim_file": AGGREGATE_CLAIM_FILENAME,
        },
        "collector_python": _collector_python_hashes(),
        "provenance": {
            "case_sidecars_required": True,
            "allow_partial": bool(args.allow_partial),
            "allow_legacy_request_manifest": bool(
                args.allow_legacy_request_manifest
            ),
            "allow_legacy_unprovenanced": bool(
                args.allow_legacy_unprovenanced
            ),
            # Honest provenance: whether the accepted case sidecars hash the
            # workflow Python sources that produced them.  Derived from the
            # sidecars actually collected -- never assumed.
            "workflow_python_hashed": workflow_python_hashed,
            "note": provenance_note,
            # Manifest authority (card C3): where the sweep labels and the
            # perturbation start came from, and how run_params.txt compared
            # against the verified manifests (per-field checks with the
            # applied numeric tolerances).
            "manifest_authority": {
                "source": authority_source,
                "reference_frequency_rad_s": (
                    authority.reference_frequency
                    if authority is not None
                    else None
                ),
                "reference_frequency_key": (
                    format_frequency_key(authority.reference_frequency)
                    if authority is not None
                    else None
                ),
                "run_params_agreement": (
                    {
                        "agrees": run_params_agreement["agrees"],
                        "disagreements": run_params_agreement[
                            "disagreements"
                        ],
                        "checks": run_params_agreement["checks"],
                        "tolerances": run_params_agreement["tolerances"],
                    }
                    if run_params_agreement is not None
                    else None
                ),
            },
        },
    }
    manifest_file = os.path.join(results_dir, COLLECTION_MANIFEST_FILENAME)
    if pre_forcing_fit:
        manifest_payload["fit_settings"]["non_transfer_function_note"] = (
            "Fit window opens before the verified perturbation start "
            "(--fit_start_allow_pre_forcing); the aggregate rows are NOT "
            "transfer-function measurements."
        )

    prepared_plot: str | None = None
    plot_title: str | None = None
    if args.plot:
        title_suffix = ""
        if collection_status == COLLECTION_STATUS_PARTIAL:
            title_suffix = (
                f" — PARTIAL ({counts['succeeded']} of "
                f"{counts['requested']} points)"
            )
        elif collection_status == COLLECTION_STATUS_LEGACY:
            title_suffix = " — LEGACY UNPROVENANCED DATA"
        plot_title = (
            "MSRR Frequency Response — Nominal Power = "
            f"{label_power}, Perturbation = {sin_mag} pcm"
            f"{title_suffix}"
        )

    if args.plot:
        try:
            prepared_plot = _render_plot_output(
                frequencies=freq_list,
                gains_db=gain_dB_list,
                phases_deg=phase_list,
                title=plot_title or "MSRR Frequency Response",
            )
        except ImportError as exc:
            print(
                f"Plot generation requested but matplotlib is unavailable: {exc}",
                file=sys.stderr,
            )
            return 2
        except Exception as exc:
            print(
                f"Plot generation failed before publication: {exc}",
                file=sys.stderr,
            )
            return 1

    # ---- Publication (P4): claimed, staged, hashed, manifest last ---------
    # The whole generation is published under one aggregate-level claim on
    # the basename, so two concurrent collectors can never interleave their
    # products.  The prior current-name set is quarantined first (H2);
    # every output is staged under a unique hidden name and hashed (H3);
    # the data products are renamed into place and the manifest --
    # advertising every output digest -- lands LAST, so an interrupted
    # collection can never leave a manifest advertising aggregates it did
    # not finish writing.
    try:
        claim = rr.acquire_result_claim(
            os.path.join(results_dir, AGGREGATE_BASENAME),
            timeout_s=args.claim_timeout_s,
        )
    except BaseException:
        if prepared_plot is not None:
            Path(prepared_plot).unlink(missing_ok=True)
        raise
    staged_products: list[tuple[str, str, str, int]] = []
    try:
        quarantined_prior = _quarantine_current_aggregate(
            results_dir,
            regenerate_plot=args.plot,
        )
        staged_texts: list[tuple[str, str]] = [
            (AGGREGATE_M_FILENAME, m_text),
            (AGGREGATE_CSV_FILENAME, csv_text),
        ]
        if failures_text is not None:
            staged_texts.append((COLLECTION_FAILURES_FILENAME, failures_text))
        for final_name, text in staged_texts:
            staged_path, digest, size = _stage_text_output(
                results_dir, final_name, text
            )
            staged_products.append((final_name, staged_path, digest, size))
        if prepared_plot is not None:
            staged_path, digest, size = _stage_binary_output(
                results_dir, AGGREGATE_PLOT_FILENAME, prepared_plot
            )
            staged_products.append(
                (AGGREGATE_PLOT_FILENAME, staged_path, digest, size)
            )
        manifest_payload["outputs"] = {
            name: {"sha256": digest, "bytes": size}
            for name, _staged_path, digest, size in staged_products
        }
        manifest_payload["provenance"]["quarantined_prior_artifacts"] = [
            str(path) for path in quarantined_prior
        ]
        manifest_text = json.dumps(
            manifest_payload, indent=2, sort_keys=True
        ) + "\n"
        for final_name, staged_path, _digest, _size in staged_products:
            os.replace(staged_path, os.path.join(results_dir, final_name))
        print(f"Results saved to: {m_file}")
        print(f"Results saved to: {csv_file}")
        if failures_text is not None:
            print(f"Per-point provenance table written to: {failures_path}")
        if prepared_plot is not None:
            print(
                "Bode plot saved to: "
                + os.path.join(results_dir, AGGREGATE_PLOT_FILENAME)
            )
        rr.atomic_write_text(manifest_file, manifest_text)
        print(f"Collection manifest saved to: {manifest_file}")
    finally:
        for _name, staged_path, _digest, _size in staged_products:
            Path(staged_path).unlink(missing_ok=True)
        if prepared_plot is not None:
            Path(prepared_plot).unlink(missing_ok=True)
        claim.release()

    # Self-check: re-verify every published output against the digests the
    # manifest advertises -- the aggregate is self-verifying by construction.
    output_problems = verify_aggregate_outputs(results_dir, manifest_payload)
    if output_problems:
        for problem in output_problems:
            print(f"Aggregate output verification problem: {problem}",
                  file=sys.stderr)
        return 1

    if args.show_plot:
        if not args.plot:
            print("--show_plot requires --plot", file=sys.stderr)
            return 2
        try:
            import matplotlib.pyplot as plt
            image = plt.imread(
                os.path.join(results_dir, AGGREGATE_PLOT_FILENAME)
            )
            plt.figure(figsize=(10, 8))
            plt.imshow(image)
            plt.axis("off")
            plt.show()
        except ImportError as exc:
            print(f"Cannot display plot: {exc}", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
