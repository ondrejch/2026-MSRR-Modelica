#!/usr/bin/env python3
"""Compare drift-regime and full-discard estimates against an expected matrix.

The drift-regime validation (``submit_drift_validation.py``) is judged
against an immutable **matrix manifest** written before dispatch
(``drift_validation_matrix.json``: every expected cell -- core, power,
point, variant, frequency, case directory, rule regime and discard,
feasibility --, the pinned Git commit, and the setpoint tables' SHA-256 and
model version).  Nothing is discovered: the report covers exactly the
matrix, fail-closed:

1. **Coverage.**  Every feasible cell must be present exactly once in its
   directory ``<root>/<core>/power_<tag>/<label>_<variant>/``; infeasible
   cells must be absent (recorded as infeasible); any other sweep directory
   under the root is an unknown cell, and two directories whose requests
   name the same (core, power, frequency, variant) are duplicates.  A point
   none of whose cells exists is reported as a missing point.
2. **Cell provenance.**  Each cell's request must name the matrix cell
   (core, power, frequency key, variant markers ``drift_regime_disabled`` /
   ``drift_window_fraction``, the rule's regime for the case), the matrix
   Git commit (clean tree unless the matrix allows dirty), and the matrix
   setpoint SHA-256 and model version for its core.  Its aggregate must
   verify through its collection manifest
   (``collectFreqNominalParallel.verify_aggregate_outputs``: every advertised
   output hash), belong to the cell's request (fingerprint), and carry
   exactly one row for the cell's frequency; its generation ID and CSV
   SHA-256 are recorded.  ``freq.verify_campaign`` is called for every cell
   and must report ``publication_eligible`` (provenance, completeness,
   parent identity); a per-cell ``*_verify.json`` / ``*_status.json`` left by
   the gateway job is recorded as evidence.
3. **Identity.**  All variants of a point must share the canonical request
   identity (``freq.sweep_manifest.request_identity``), differing only in
   the drift/full and window-fraction settle fields; all cells must share
   the tool identity (sources, workflow Python, Git, interpreter,
   OpenModelica, workflow version, solver, output grid), and all cells of a
   core the model and setpoint identity.
4. **Comparison.**  Per point, the comparisons the matrix plans:
   ``drift_vs_full`` -- the drift estimate (default window, gain per unit
   window-mean power) against the forced full discard under the same
   normalization (gating; at low power G/P does not depend on the operating
   point), plus the published values (informational) -- and
   ``window_doubling`` -- the doubled window against the default window.
   Each must agree within ``|dG/G| <= 0.5 %`` and ``|dphase| <= 0.5 deg``
   with every compared variant converged.

The report passes only when every expected feasible cell is valid, every
point's planned comparisons pass, and no coverage, provenance, or identity
problem exists; a missing, invalid, or unconverged cell makes its point
``fail`` or ``inconclusive``, and either fails the report.  Per-cell
problems are kept in the report as evidence.  The recommended drift
operating-point bound ``ceil(1.5 * max|shift| / 1 %) %`` is offered only for
a passing report.

    python3.12 helpers/paper-rerun/compare_drift_validation.py \\
        --root 00runs/drift-validation-<id> \\
        --matrix <root>/drift_validation_matrix.json [--matrix-sha256 <hex>] \\
        --output <root>/drift_validation_report_strict.json \\
        --markdown <root>/drift_validation_report_strict.md

Exit status: 0 pass; 1 fail or inconclusive; 2 invalid input (missing,
malformed, or tampered matrix manifest).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from freq import sweep_manifest  # noqa: E402
from freq._common import format_frequency_key  # noqa: E402

REPORT_KIND = "drift-regime-validation-report"
REPORT_SCHEMA_VERSION = 2
MATRIX_KIND = "drift-validation-matrix"
MATRIX_SCHEMA_VERSION = 1
MATRIX_FILENAME = "drift_validation_matrix.json"
REPORT_BASENAME = "drift_validation_report_strict"
GAIN_TOL = 0.005
PHASE_TOL_DEG = 0.5
BOUND_MARGIN = 1.5
VARIANT_FULL = "full"
VARIANT_DRIFT = "drift_phi0p1"
VARIANT_DRIFT_DOUBLED = "drift_phi0p2"
VARIANTS = (VARIANT_DRIFT, VARIANT_DRIFT_DOUBLED, VARIANT_FULL)
COMPARISON_DRIFT_VS_FULL = "drift_vs_full"
COMPARISON_WINDOW_DOUBLING = "window_doubling"
#: Variant markers in the request: (drift_regime_disabled, drift_window_fraction).
VARIANT_MARKERS = {
    VARIANT_DRIFT: (False, 0.1),
    VARIANT_DRIFT_DOUBLED: (False, 0.2),
    VARIANT_FULL: (True, 0.1),
}
#: Request paths the variants of one point may differ in (the intended
#: drift/full and window-fraction settle fields, and the lock-in fields that
#: ``--settle_force_full`` switches off with the drift regime); everything
#: else is the canonical identity (freq.sweep_manifest.request_identity).
VARIANT_AUTHORIZED = (
    "policies.fr_protocol.settle.drift_enabled",
    "policies.fr_protocol.settle.drift_regime_disabled",
    "policies.fr_protocol.settle.drift_window_fraction",
    "policies.fr_protocol.settle.drift_window_s",
    "policies.fr_protocol.settle.drift_trend_order",
    "policies.fr_protocol.settle.lockin_enabled",
    "policies.fr_protocol.settle.lockin_min_omega_ratio",
    "policies.fr_protocol.settle.lockin_estimator",
    "policies.fr_protocol.settle.lockin_trend_order",
)
#: Request paths every cell of the matrix must share.
TOOL_IDENTITY_FIELDS = (
    "source_files",
    "workflow_python",
    "git_commit",
    "git_dirty",
    "python_version",
    "omc_version",
    "workflow_version",
    "numerics.solver",
    "numerics.output_grid",
    "package",
)
#: Request paths every cell of one core must share.
CORE_IDENTITY_FIELDS = (
    "model_name",
    "package_name",
    "setpoint_table_sha256",
    "setpoint_model_version",
    "core_maturity",
    "core_physical_data_maturity",
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_MISSING = "missing"


class MatrixError(ValueError):
    """The matrix manifest is missing, malformed, or altered."""


# ---------------------------------------------------------------------------
# Matrix manifest (written by submit_drift_validation.py before dispatch)
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def matrix_fingerprint(manifest: dict) -> str:
    body = {key: value for key, value in manifest.items() if key != "fingerprint"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_matrix(manifest: dict) -> dict:
    sealed = json.loads(_canonical(manifest))
    sealed.pop("fingerprint", None)
    sealed["fingerprint"] = matrix_fingerprint(sealed)
    return sealed


def validate_matrix(manifest: Any) -> dict:
    """Structural and fingerprint validation; returns the manifest."""
    if not isinstance(manifest, dict):
        raise MatrixError("matrix manifest is not a JSON object")
    if manifest.get("kind") != MATRIX_KIND or manifest.get("schema_version") != MATRIX_SCHEMA_VERSION:
        raise MatrixError(f"not a {MATRIX_KIND} (schema {MATRIX_SCHEMA_VERSION}) manifest")
    if str(manifest.get("fingerprint") or "") != matrix_fingerprint(manifest):
        raise MatrixError("matrix manifest fingerprint mismatch (edited or corrupted)")
    commit = str(manifest.get("commit") or "")
    if len(commit) != 40:
        raise MatrixError("matrix manifest must pin a full 40-character Git commit")
    cells = manifest.get("cells")
    points = manifest.get("points")
    if not isinstance(cells, list) or not cells or not isinstance(points, list) or not points:
        raise MatrixError("matrix manifest lists no cells or points")
    keys: set[str] = set()
    directories: set[str] = set()
    identities: set[tuple] = set()
    for cell in cells:
        for name in ("key", "core", "power", "label", "variant", "frequency_rad_s",
                     "frequency_key", "directory", "regime", "feasible"):
            if name not in cell:
                raise MatrixError(f"matrix cell {cell.get('key')!r} lacks {name!r}")
        if cell["variant"] not in VARIANTS:
            raise MatrixError(f"matrix cell {cell['key']}: unknown variant {cell['variant']!r}")
        if cell["key"] in keys or cell["directory"] in directories:
            raise MatrixError(f"matrix cell {cell['key']} is listed twice")
        identity = (cell["core"], float(cell["power"]), cell["frequency_key"], cell["variant"])
        if identity in identities:
            raise MatrixError(f"matrix cell {cell['key']} duplicates another cell's identity")
        keys.add(cell["key"])
        directories.add(cell["directory"])
        identities.add(identity)
    listed: set[str] = set()
    for point in points:
        for key in point.get("cells") or []:
            if key not in keys:
                raise MatrixError(f"matrix point {point.get('point')!r} names unknown cell {key}")
            if key in listed:
                raise MatrixError(f"matrix cell {key} belongs to two points")
            listed.add(key)
    if listed != keys:
        raise MatrixError("matrix cells not covered by exactly one point: "
                          + ", ".join(sorted(keys - listed)))
    for core, record in (manifest.get("setpoints") or {}).items():
        if not isinstance(record, dict) or "sha256" not in record:
            raise MatrixError(f"matrix setpoint record for {core} lacks sha256")
    return manifest


def load_matrix(path: str | Path, *, expected_sha256: str | None = None) -> dict:
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise MatrixError(f"cannot read matrix manifest {target}: {exc}") from exc
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256.strip().lower():
        raise MatrixError(f"matrix manifest {target} does not have the expected SHA-256")
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MatrixError(f"matrix manifest {target} is not JSON: {exc}") from exc
    return validate_matrix(manifest)


def publish_matrix(path: str | Path, manifest: dict) -> dict:
    """Write the sealed manifest once; an existing one must be identical."""
    sealed = validate_matrix(seal_matrix(manifest))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = load_matrix(target)
        if existing["fingerprint"] != sealed["fingerprint"]:
            raise MatrixError(
                f"{target} holds a different matrix ({existing['fingerprint'][:12]}...); "
                "the matrix manifest is immutable -- use a new campaign id"
            )
        return existing
    text = json.dumps(sealed, indent=2, sort_keys=True) + "\n"
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        temp.unlink(missing_ok=True)
        return publish_matrix(path, manifest)
    os.close(fd)
    os.replace(temp, target)
    return sealed


def matrix_file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _float(row: dict, name: str) -> float:
    try:
        return float(row.get(name, "nan"))
    except (TypeError, ValueError):
        return math.nan


def _finite_or_none(value: float) -> float | None:
    return value if isinstance(value, float) and math.isfinite(value) else None


def _wrap_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _path_get(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _collector():
    import importlib

    return importlib.import_module("freq.collectFreqNominalParallel")


def _verifier():
    import importlib

    return importlib.import_module("freq.verify_campaign")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request_variant(request: dict) -> tuple[bool, float] | None:
    settle = _path_get(request, "policies.fr_protocol.settle") or {}
    if not isinstance(settle, dict):
        return None
    return bool(settle.get("drift_regime_disabled")), round(float(settle.get("drift_window_fraction") or 0.0), 6)


def evaluate_cell(root: Path, cell: dict, matrix: dict) -> dict[str, Any]:
    """Provenance, aggregate, verifier, and metrics of one expected cell."""
    directory = root / cell["directory"]
    record: dict[str, Any] = {
        "key": cell["key"],
        "directory": cell["directory"],
        "variant": cell["variant"],
        "status": "valid",
        "problems": [],
        "collected": False,
    }
    problems = record["problems"]
    try:
        payload = sweep_manifest.load_sweep_request_manifest(directory)
    except sweep_manifest.SweepManifestError as exc:
        record.update({"status": STATUS_MISSING if not directory.exists() else "invalid"})
        problems.append(f"no valid sweep request: {exc}")
        return record
    request = payload["request"]
    record["request_fingerprint"] = payload["fingerprint"]
    record["_request"] = request
    cases = request.get("cases") or []
    if len(cases) != 1:
        problems.append(f"request lists {len(cases)} cases, expected one")
        record["status"] = "invalid"
        return record
    case = cases[0]
    record["_case"] = case
    record["frequency_rad_s"] = float(case["frequency_rad_s"])
    # Cell identity against the matrix.
    if str(request.get("core_model")) != cell["core"]:
        problems.append(f"core {request.get('core_model')!r} is not the matrix core {cell['core']!r}")
    if not math.isclose(float(request.get("power") or math.nan), float(cell["power"]), rel_tol=1e-12):
        problems.append(f"power {request.get('power')!r} is not the matrix power {cell['power']!r}")
    if str(case.get("frequency_key")) != str(cell["frequency_key"]):
        problems.append(
            f"frequency key {case.get('frequency_key')!r} is not the matrix key {cell['frequency_key']!r}"
        )
    marker = _request_variant(request)
    if marker != VARIANT_MARKERS[cell["variant"]]:
        problems.append(f"request settle markers {marker!r} are not variant {cell['variant']!r}")
    if str(case.get("settle_regime")) != str(cell["regime"]):
        problems.append(
            f"the rule put the case in the {case.get('settle_regime')!r} regime, "
            f"the matrix expects {cell['regime']!r}"
        )
    if str(request.get("git_commit") or "") != str(matrix["commit"]):
        problems.append(f"git commit {request.get('git_commit')!r} is not the matrix commit")
    if request.get("git_dirty") is not False and not matrix.get("allow_dirty"):
        problems.append(f"git_dirty is {request.get('git_dirty')!r} (the matrix requires a clean tree)")
    setpoint = (matrix.get("setpoints") or {}).get(cell["core"])
    if setpoint is not None:
        if request.get("setpoint_table_sha256") != setpoint.get("sha256"):
            problems.append("setpoint table SHA-256 is not the matrix table's")
        if "model_version" in setpoint and request.get("setpoint_model_version") != setpoint.get("model_version"):
            problems.append("setpoint model version is not the matrix table's")
    # Aggregate: verified through its collection manifest and output hashes.
    collector = _collector()
    aggregate_problems = collector.verify_aggregate_outputs(str(directory))
    if aggregate_problems:
        problems.extend(f"aggregate: {problem}" for problem in aggregate_problems)
    collection = _load_json(directory / collector.COLLECTION_MANIFEST_FILENAME) or {}
    record["collection_generation_id"] = collection.get("generation_id")
    record["aggregate_csv_sha256"] = (
        ((collection.get("outputs") or {}).get(collector.AGGREGATE_CSV_FILENAME) or {}).get("sha256")
    )
    request_ref = ((collection.get("sweep") or {}).get("request_manifest") or {})
    if collection and request_ref.get("fingerprint") != payload["fingerprint"]:
        problems.append("aggregate was collected for another sweep request")
    # Campaign verifier (strict: provenance, completeness, parent identity).
    try:
        summary = _verifier().verify_campaign(str(directory))
    except Exception as exc:  # noqa: BLE001 - a crashing verifier is a failed cell
        summary = None
        problems.append(f"freq.verify_campaign failed: {exc}")
    if summary is not None:
        record["verifier"] = {
            "publication_eligible": summary.get("publication_eligible"),
            "fit_convergence_pass": summary.get("fit_convergence_pass"),
            "swing_check_pass": summary.get("swing_check_pass"),
            "rejected": [item.get("status") for item in summary.get("rejected") or []],
            "refinement_error": (summary.get("refinement") or {}).get("error"),
            "refined_points": len((summary.get("refinement") or {}).get("refined_points") or []),
        }
        if summary.get("publication_eligible") is not True:
            problems.append("freq.verify_campaign: publication_eligible is not true")
    # Gateway evidence (recorded, never trusted over the local verification).
    logs = root / "logs"
    record["remote_verify_evidence"] = _load_json(logs / f"{cell['key']}_verify.json") is not None
    record["remote_status"] = _load_json(logs / f"{cell['key']}_status.json")
    # Metrics: exactly one aggregate row for the cell's frequency.
    rows: list[dict] = []
    aggregate = directory / collector.AGGREGATE_CSV_FILENAME
    if aggregate.is_file():
        with aggregate.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    matching = [row for row in rows if _row_key(row) == str(cell["frequency_key"])]
    if len(rows) != 1 or len(matching) != 1:
        problems.append(f"aggregate has {len(rows)} rows ({len(matching)} for the cell frequency)")
    else:
        row = matching[0]
        gain = _float(row, "gain")
        shift = _float(row, "operating_point_offset")
        level = 1.0 + shift if math.isfinite(shift) else math.nan
        window_mean = str(row.get("gain_reference") or "") == "window_mean_power"
        record.update(
            {
                "collected": True,
                "gain": gain,
                "gain_nominal_reference": gain * level if window_mean else gain,
                "gain_window_mean_reference": gain if window_mean else gain / level,
                "phase_deg": _float(row, "phase_deg"),
                "h2_h1_ratio": _float(row, "h2_h1_ratio"),
                "operating_point_offset": shift,
                "relative_swing": _float(row, "relative_swing"),
                "n_cycles": _float(row, "n_cycles"),
                "converged": str(row.get("converged", "")).strip().lower() == "true",
                "convergence_reason": str(row.get("convergence_reason") or ""),
                "refined": str(row.get("refined", "")).strip().lower() == "true",
                "settle_discard_history_s": str(row.get("settle_discard_history_s") or ""),
            }
        )
    record["settle_regime"] = case.get("settle_regime")
    record["settle_discard_s"] = case.get("settle_discard_s")
    record["fit_window_s"] = case.get("fit_window_s")
    record["omega_n_rad_s"] = _path_get(request, "policies.fr_protocol.settle.omega_n_rad_s")
    if problems:
        record["status"] = "invalid"
    return record


def _row_key(row: dict) -> str:
    try:
        return format_frequency_key(float(row.get("frequency_rad_s")))
    except (TypeError, ValueError):
        return str(row.get("frequency_rad_s"))


def _delta(candidate: dict, reference: dict, gain_key: str,
           reference_gain_key: str | None = None) -> dict:
    gain = (
        candidate.get(gain_key, math.nan)
        / reference.get(reference_gain_key or gain_key, math.nan)
        - 1.0
    )
    phase = _wrap_deg(candidate.get("phase_deg", math.nan) - reference.get("phase_deg", math.nan))
    h2_ref = reference.get("h2_h1_ratio", math.nan)
    h2 = candidate.get("h2_h1_ratio", math.nan)
    both_converged = bool(candidate.get("converged")) and bool(reference.get("converged"))
    finite = math.isfinite(gain) and math.isfinite(phase)
    within = finite and abs(gain) <= GAIN_TOL and abs(phase) <= PHASE_TOL_DEG
    return {
        "gain_rel_diff": _finite_or_none(gain),
        "phase_diff_deg": _finite_or_none(phase),
        "h2_h1_ratio_ratio": _finite_or_none(h2 / h2_ref) if h2_ref else None,
        "operating_point_shift_diff": _finite_or_none(
            candidate.get("operating_point_offset", math.nan)
            - reference.get("operating_point_offset", math.nan)
        ),
        "both_converged": both_converged,
        "status": (STATUS_PASS if within else STATUS_FAIL) if both_converged else STATUS_INCONCLUSIVE,
    }


def _discover_sweeps(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob(f"*/power_*/*/{sweep_manifest.SWEEP_REQUEST_FILENAME}"))


def compare(root: Path, matrix: dict) -> dict[str, Any]:
    """Fail-closed comparison of the validation tree against the matrix."""
    root = Path(root)
    problems: list[str] = []
    cells_by_key = {cell["key"]: cell for cell in matrix["cells"]}
    expected_dirs = {cell["directory"]: cell for cell in matrix["cells"]}
    # Coverage: unknown directories, present infeasible cells, duplicates.
    seen_identity: dict[tuple, str] = {}
    for directory in _discover_sweeps(root):
        relative = directory.relative_to(root).as_posix()
        cell = expected_dirs.get(relative)
        if cell is None:
            problems.append(f"unknown cell {relative} (not in the matrix)")
        elif not cell["feasible"]:
            problems.append(f"cell {cell['key']} is infeasible in the matrix but present")
        try:
            payload = sweep_manifest.load_sweep_request_manifest(directory)
            request = payload["request"]
            case = (request.get("cases") or [{}])[0]
            marker = _request_variant(request)
            variant = next((name for name, value in VARIANT_MARKERS.items() if value == marker), None)
            identity = (str(request.get("core_model")), float(request.get("power")),
                        str(case.get("frequency_key")), variant)
        except (sweep_manifest.SweepManifestError, TypeError, ValueError, IndexError):
            continue
        if identity in seen_identity:
            problems.append(
                f"duplicate variant: {relative} and {seen_identity[identity]} both hold "
                f"{identity[0]} {identity[1]:g} MW {identity[2]} rad/s {identity[3]}"
            )
        else:
            seen_identity[identity] = relative
    records: dict[str, dict] = {}
    for cell in matrix["cells"]:
        if not cell["feasible"]:
            continue
        records[cell["key"]] = evaluate_cell(root, cell, matrix)
    # Tool identity across the matrix; core identity across each core.
    valid = [records[key] for key in records if "_request" in records[key]]
    if valid:
        reference = valid[0]["_request"]
        for record in valid[1:]:
            for field in TOOL_IDENTITY_FIELDS:
                if _canonical(_path_get(record["_request"], field)) != _canonical(_path_get(reference, field)):
                    problems.append(f"tool identity: {record['key']} differs from {valid[0]['key']} in {field}")
                    record["status"] = "invalid"
                    record["problems"].append(f"tool identity differs: {field}")
    by_core: dict[str, dict] = {}
    for record in valid:
        core = cells_by_key[record["key"]]["core"]
        reference = by_core.setdefault(core, record)
        if reference is record:
            continue
        for field in CORE_IDENTITY_FIELDS:
            if _canonical(_path_get(record["_request"], field)) != _canonical(_path_get(reference["_request"], field)):
                problems.append(f"core identity: {record['key']} differs from {reference['key']} in {field}")
                record["status"] = "invalid"
                record["problems"].append(f"core identity differs: {field}")
    points_report = []
    for point in matrix["points"]:
        members = {cells_by_key[key]["variant"]: cells_by_key[key] for key in point["cells"]}
        feasible = {variant: cell for variant, cell in members.items() if cell["feasible"]}
        present = {variant: records[cell["key"]] for variant, cell in feasible.items()}
        entry: dict[str, Any] = {
            "point": point["point"],
            "core_model": point["core"],
            "power_mw": point["power"],
            "label": point["label"],
            "frequency_key": point["frequency_key"],
            "frequency_rad_s": point["frequency_rad_s"],
            "comparisons_planned": list(point["comparisons"]),
            "infeasible_cells": [cell["key"] for cell in members.values() if not cell["feasible"]],
            "cells": {variant: {k: v for k, v in record.items() if not k.startswith("_")}
                      for variant, record in present.items()},
            "problems": [],
        }
        omega_n = next((r.get("omega_n_rad_s") for r in present.values() if r.get("omega_n_rad_s")), None)
        entry["omega_over_omega_n"] = (
            float(point["frequency_rad_s"]) / float(omega_n) if omega_n else None
        )
        if present and all(record["status"] == STATUS_MISSING for record in present.values()):
            entry["status"] = STATUS_MISSING
            entry["problems"].append("every cell of the point is missing")
            points_report.append(entry)
            continue
        for variant, record in present.items():
            if record["status"] != "valid":
                entry["problems"].append(f"{variant}: " + "; ".join(record["problems"]))
        # Canonical identity across the variants of the point.
        requests = [(variant, record["_request"]) for variant, record in present.items() if "_request" in record]
        for variant, request in requests[1:]:
            differing = sweep_manifest.request_identity_differences(
                requests[0][1], request, authorized=VARIANT_AUTHORIZED
            )
            if differing:
                entry["problems"].append(
                    f"{variant} departs from {requests[0][0]} in the canonical identity: "
                    + ", ".join(differing)
                )
        usable = {
            variant: record for variant, record in present.items()
            if record["status"] == "valid" and record.get("collected")
        }
        statuses = []
        if COMPARISON_DRIFT_VS_FULL in point["comparisons"]:
            drift, full = usable.get(VARIANT_DRIFT), usable.get(VARIANT_FULL)
            if drift and full:
                entry["drift_vs_full_same_normalization"] = _delta(drift, full, "gain_window_mean_reference")
                entry["drift_vs_full_published"] = _delta(
                    drift, full, "gain_window_mean_reference", "gain_nominal_reference"
                )
                statuses.append(entry["drift_vs_full_same_normalization"]["status"])
            else:
                entry["drift_vs_full_same_normalization"] = {"status": STATUS_INCONCLUSIVE}
                statuses.append(STATUS_INCONCLUSIVE)
        if COMPARISON_WINDOW_DOUBLING in point["comparisons"]:
            drift, doubled = usable.get(VARIANT_DRIFT), usable.get(VARIANT_DRIFT_DOUBLED)
            if drift and doubled:
                entry["window_doubling"] = _delta(doubled, drift, "gain_window_mean_reference")
                statuses.append(entry["window_doubling"]["status"])
            else:
                entry["window_doubling"] = {"status": STATUS_INCONCLUSIVE}
                statuses.append(STATUS_INCONCLUSIVE)
        if not statuses:
            entry["problems"].append("the matrix plans no comparison for this point")
            statuses.append(STATUS_INCONCLUSIVE)
        if entry["problems"] or STATUS_FAIL in statuses:
            entry["status"] = STATUS_FAIL
        elif STATUS_INCONCLUSIVE in statuses:
            entry["status"] = STATUS_INCONCLUSIVE
        else:
            entry["status"] = STATUS_PASS
        points_report.append(entry)
    shifts: dict[str, float] = {}
    for record in valid:
        cell = cells_by_key[record["key"]]
        if cell["variant"] in (VARIANT_DRIFT, VARIANT_DRIFT_DOUBLED) and record.get("collected"):
            shift = abs(record.get("operating_point_offset", math.nan))
            if math.isfinite(shift):
                name = f"{cell['core']}@{float(cell['power']):g}MW/{cell['variant']}"
                shifts[name] = max(shifts.get(name, 0.0), shift)
    counts = {status: sum(1 for p in points_report if p["status"] == status)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE, STATUS_MISSING)}
    counts["points"] = len(points_report)
    expected_feasible = [cell["key"] for cell in matrix["cells"] if cell["feasible"]]
    coverage = {
        "expected_cells": len(matrix["cells"]),
        "expected_feasible_cells": len(expected_feasible),
        "infeasible_cells": sorted(cell["key"] for cell in matrix["cells"] if not cell["feasible"]),
        "valid_cells": sum(1 for key in expected_feasible if records[key]["status"] == "valid"),
        "missing_cells": sorted(key for key in expected_feasible if records[key]["status"] == STATUS_MISSING),
        "invalid_cells": sorted(key for key in expected_feasible if records[key]["status"] == "invalid"),
        "missing_points": sorted(p["point"] for p in points_report if p["status"] == STATUS_MISSING),
    }
    passed = (
        not problems
        and bool(points_report)
        and counts[STATUS_PASS] == len(points_report)
        and coverage["valid_cells"] == len(expected_feasible)
    )
    max_shift = max(shifts.values()) if shifts else None
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "matrix": {
            "fingerprint": matrix["fingerprint"],
            "campaign_id": matrix.get("campaign_id"),
            "commit": matrix["commit"],
            "reconstructed": bool(matrix.get("reconstructed")),
            "setpoints": matrix.get("setpoints"),
        },
        "tolerances": {"gain_rel_diff": GAIN_TOL, "phase_diff_deg": PHASE_TOL_DEG},
        "coverage": coverage,
        "points": points_report,
        "counts": counts,
        "max_drift_operating_point_shift": shifts,
        "max_drift_operating_point_shift_overall": max_shift,
        "recommended_drift_operating_point_bound": (
            math.ceil(BOUND_MARGIN * max_shift / 0.01) * 0.01
            if passed and max_shift is not None else None
        ),
        "current_drift_operating_point_bound": 0.2,
        "problems": problems,
        "pass": passed,
        "status": STATUS_PASS if passed else (
            STATUS_INCONCLUSIVE
            if not problems and counts[STATUS_FAIL] == 0 and counts[STATUS_MISSING] == 0
            and not coverage["invalid_cells"]
            else STATUS_FAIL
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    def pct(value):
        return "--" if value is None else f"{100.0 * value:+.3f} %"

    def deg(value):
        return "--" if value is None else f"{value:+.3f}"

    coverage = report["coverage"]
    lines = [
        "# Drift-regime validation (matrix-checked)",
        "",
        f"Overall: **{report['status']}**. Matrix {report['matrix']['fingerprint'][:12]}... "
        f"(commit {report['matrix']['commit'][:12]}"
        + (", reconstructed" if report["matrix"]["reconstructed"] else "")
        + f"); cells {coverage['valid_cells']}/{coverage['expected_feasible_cells']} valid, "
        f"{len(coverage['missing_cells'])} missing, {len(coverage['invalid_cells'])} invalid, "
        f"{len(coverage['infeasible_cells'])} infeasible by plan; points {report['counts']['points']} "
        f"(pass {report['counts']['pass']}, fail {report['counts']['fail']}, "
        f"inconclusive {report['counts']['inconclusive']}, missing {report['counts']['missing']}). "
        f"Tolerances |dG/G| <= {100 * GAIN_TOL:g} %, |dphase| <= {PHASE_TOL_DEG:g} deg.",
        "",
        "| Core | P (MW) | Point | omega (rad/s) | omega/omega_n | drift-full dG (same norm.) | "
        "drift-full dG (published) | drift-full dphase (deg) | 2x window dG | "
        "2x window dphase (deg) | drift shift | full shift | Status |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for point in report["points"]:
        same = point.get("drift_vs_full_same_normalization") or {}
        published = point.get("drift_vs_full_published") or {}
        doubling = point.get("window_doubling") or {}
        drift = point["cells"].get(VARIANT_DRIFT, {})
        full = point["cells"].get(VARIANT_FULL, {})
        ratio = point.get("omega_over_omega_n")
        lines.append(
            f"| {point['core_model'].upper()} | {float(point['power_mw']):g} | {point['label']} | "
            f"{float(point['frequency_rad_s']):.4g} | {'--' if ratio is None else f'{ratio:.0f}'} | "
            f"{pct(same.get('gain_rel_diff'))} | {pct(published.get('gain_rel_diff'))} | "
            f"{deg(same.get('phase_diff_deg'))} | {pct(doubling.get('gain_rel_diff'))} | "
            f"{deg(doubling.get('phase_diff_deg'))} | "
            f"{pct(_finite_or_none(drift.get('operating_point_offset', math.nan)))} | "
            f"{pct(_finite_or_none(full.get('operating_point_offset', math.nan)))} | "
            f"{point['status']} |"
        )
    lines += [
        "",
        "Largest drift-regime operating-point shift: "
        + (
            "--" if report["max_drift_operating_point_shift_overall"] is None
            else f"{100 * report['max_drift_operating_point_shift_overall']:.2f} %"
        )
        + "; recommended bound: "
        + (
            "none (the report does not pass)"
            if report["recommended_drift_operating_point_bound"] is None
            else f"{100 * report['recommended_drift_operating_point_bound']:.0f} %"
        )
        + f" (current {100 * report['current_drift_operating_point_bound']:.0f} %).",
    ]
    point_problems = [
        f"- {point['point']}: {problem}"
        for point in report["points"] for problem in point.get("problems", [])
    ]
    if report["problems"] or point_problems:
        lines += ["", "Problems:", *[f"- {problem}" for problem in report["problems"]], *point_problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", required=True, help="validation campaign directory")
    parser.add_argument("--matrix", default="",
                        help=f"matrix manifest (default: <root>/{MATRIX_FILENAME})")
    parser.add_argument("--matrix-sha256", default="",
                        help="expected SHA-256 of the matrix manifest file")
    parser.add_argument("--output", default="", help="JSON report path (default: print)")
    parser.add_argument("--markdown", default="", help="Markdown table path")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2
    matrix_path = Path(args.matrix).expanduser() if args.matrix else root / MATRIX_FILENAME
    try:
        matrix = load_matrix(matrix_path, expected_sha256=args.matrix_sha256 or None)
    except MatrixError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    report = compare(root, matrix)
    text = json.dumps(report, indent=2, sort_keys=True, default=float) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
    print(f"drift validation: {report['status']}", file=sys.stderr)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
