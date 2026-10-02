"""Run-result provenance: manifests, fingerprints, and output validation.

This library centralizes the provenance handling shared by the MSRR
simulation workflows (frequency sweeps, startup runs, nonlinear transients,
and setpoint generation).  It answers one question -- *may an existing
result CSV stand in for a simulation that has not run yet?* -- and provides
the bookkeeping around it:

1. ``build_run_manifest`` records the canonical description of a single
   simulation request: SHA-256 digests of every loaded Modelica source
   file and of the workflow Python sources that shape the request (the
   active runner module plus the shared helpers that construct overrides,
   resolve paths, and post-process results), revision-control state,
   interpreter and OpenModelica versions, package/model identity,
   normalized command-line overrides, setpoint-table provenance,
   numerical settings, forcing parameters, execution backend, and
   workflow version.
2. ``manifest_fingerprint`` reduces that manifest to a deterministic
   SHA-256 digest of its canonical JSON form.  Volatile fields (the launch
   timestamp) are excluded, so identical requests match while any change to
   sources, solver, setpoints, forcing, overrides, or tools produces a new
   fingerprint.
3. ``validate_result_csv`` performs acceptance checks on a finished result
   CSV: freshness relative to the recorded launch timestamp, well-formed
   header and body, required and unique column names, monotonically
   nondecreasing ``time``, finiteness of required quantities, requested
   stop-time coverage, and a minimum sample count.
4. ``prepare_result_path`` implements the surrounding lifecycle: an
   existing CSV may be reused only when a matching manifest sidecar exists
   and validation passes; otherwise prior files -- including leftover
   ``*.tmp`` outputs and stale sidecars -- are moved under
   ``00runs/tmp/quarantine/<utc-stamp>/`` so a fresh run starts clean.
   On a successful reuse any leftover ``*.tmp`` companion of that same slot
   is cleaned up as well, while the validated CSV and both sidecars stay
   untouched.

Sidecar layout beside a result file named ``<prefix>_res.csv``::

    <prefix>_res.manifest.json      {"fingerprint", "algorithm", "manifest"}
    <prefix>_res.validation.json     outcome of ``validate_result_csv``,
                                    including ``result_sha256`` -- the SHA-256
                                    of the result bytes that were validated -- so
                                    later readers can tie the report to the exact
                                    file contents

Callers publish results atomically by writing ``<prefix>_res.csv.tmp`` and
calling :func:`atomic_publish` once output finishes, so readers never
observe half-written data.  :func:`publish_validated_result` packages the
full sequence -- validate the temporary output, publish it onto the final
name only when it passes, then write both sidecars -- and
:func:`acquire_result_claim` gives one launch exclusive access to a slot so
two concurrent same-slot launches cannot quarantine each other's in-flight
output.

This module deliberately touches no workflow runner; runners adopt it in
dedicated integration work.

Example::

    manifest = build_run_manifest(package_name="MSRR",
                                  model_name="MSRR.MSRRuhxNominalTrimNoTrips",
                                  source_files=[core_src],
                                  overrides={"power": 1.0},
                                  stop_time=1200.0,
                                  workflow_version="freq-runner/1")
    outcome = prepare_result_path(csv,
                                  reuse_ok=args.reuse,
                                  expected_manifest=manifest)
    if outcome.can_reuse:
        return  # existing CSV validated against the identical request
    # Fresh decision: the prior slot was just quarantined -- simulate, then
    # publish through the temporary companion before writing any sidecar.
    simulate_for_request(manifest)              # raw output lands in *.tmp
    atomic_publish(make_tmp_path(csv), csv)     # finished result -> final name
    report = validate_result_csv(csv,
                                 launch_timestamp=manifest["launch_timestamp_utc"],
                                 requested_stop_time=manifest["stop_time"])
    if not report.passed:
        raise RuntimeError(f"result rejected: {report.failed_checks}")
    write_result_sidecars(csv, manifest, report)

The compact publication protocol used by the runners::

    claim = acquire_result_claim(csv)           # exclusive same-slot access
    try:
        prepared = prepare_result_path(...)     # reuse or quarantine
        if prepared.can_reuse:
            return
        simulate_for_request(manifest)          # raw output lands in *.tmp
        publish_validated_result(               # validate temp, publish, sidecar
            make_tmp_path(csv), csv, manifest=manifest, ...)
    finally:
        claim.release()
"""

from __future__ import annotations

import csv
import errno
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from dataclasses import replace as _dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # POSIX advisory locking; unavailable on platforms without fcntl.
    import fcntl
except ImportError:  # pragma: no cover - the project targets POSIX hosts
    fcntl = None  # type: ignore[assignment]

__version__ = "1"

__all__ = [
    "__version__",
    "ACTION_FRESH",
    "ACTION_REUSE",
    "ANNULAR_LOOP_FINGERPRINT_KEY",
    "BACKEND_CHOICES",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_MTIME_TOLERANCE_S",
    "DEFAULT_REQUIRED_COLUMNS",
    "MANIFEST_ALGORITHM",
    "RADIAL_FINGERPRINT_KEY",
    "RADIAL_MANIFEST_KEY",
    "RESULT_CLAIM_SUFFIX",
    "RESULT_TMP_SUFFIX",
    "SCHEMA_VERSION",
    "VOLATILE_MANIFEST_KEYS",
    "WORKFLOW_PYTHON_FILES_KEY",
    "ManifestDifference",
    "PreparedResult",
    "ResultRejectedError",
    "ResultSlotClaim",
    "ResultSlotClaimTimeout",
    "ValidationCheck",
    "ValidationReport",
    "acquire_result_claim",
    "atomic_publish",
    "atomic_write_text",
    "build_run_manifest",
    "canonical_manifest_json",
    "collect_git_info",
    "default_quarantine_root",
    "diff_manifests",
    "file_sha256",
    "format_manifest_differences",
    "make_claim_path",
    "make_tmp_path",
    "manifest_fingerprint",
    "manifest_sidecar_path",
    "normalize_overrides",
    "parse_timestamp",
    "prepare_result_path",
    "probe_omc_version",
    "publish_validated_result",
    "quarantine_existing_artifacts",
    "radial_reuse_conflict",
    "read_manifest_sidecar",
    "read_validation_report",
    "tmp_path_for",
    "validate_result_csv",
    "validation_report_path",
    "write_manifest_sidecar",
    "write_result_sidecars",
    "write_validation_sidecar",
]

# Canonical manifest schema revision (bumped only when the layout changes).
SCHEMA_VERSION = 1

# Manifest section carrying SHA-256 digests of the workflow Python sources
# that shaped the request (active runner module plus shared helper modules).
# Unlike the git commit/dirty pair -- which merely observes the tree -- these
# digests participate in the fingerprint, so a byte change in workflow code
# produces a new fingerprint even when the revision-control state is
# unchanged.
WORKFLOW_PYTHON_FILES_KEY = "workflow_python"

# Identifier recorded inside sidecars describing how fingerprints are formed.
MANIFEST_ALGORITHM = "sha256-canonical-json-v1"

# Manifest fields that legitimately differ between runs of the *same*
# request and are therefore excluded from the fingerprint.
VOLATILE_MANIFEST_KEYS = frozenset({"launch_timestamp_utc"})

BACKEND_CHOICES = ("local", "ssh")

DEFAULT_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)
DEFAULT_MIN_SAMPLES = 2

# Filesystem mtime granularity can trail the wall clock by milliseconds on
# container filesystems; freshness comparisons absorb this much jitter while
# still rejecting results written before their recorded launch instant.
DEFAULT_MTIME_TOLERANCE_S = 0.05

# Top-level manifest field recording the intra-channel radial stack /
# annular-loop configuration of a run (TASK-20260914-01 P1; see
# ``helpers.segmented_runs.radial_manifest_fields``). The field is
# fingerprint-active and present ONLY on radial-enabled runs (absence ==
# disabled), so a result directory whose recorded manifest carries it is
# NEVER reusable for a request that does not carry the identical record
# (radial_reuse_conflict, card A16 / plan §6.8, §8.4).
RADIAL_MANIFEST_KEY = "intra_channel_radial"

# The two deterministic fingerprints inside the radial record whose
# disagreement names the reuse conflict most specifically.
RADIAL_FINGERPRINT_KEY = "radialFingerprint"
ANNULAR_LOOP_FINGERPRINT_KEY = "annularLoopFingerprint"

RESULT_TMP_SUFFIX = ".tmp"

# Companion suffix of the per-result exclusive claim file guarding one slot
# against two concurrent same-slot launches.
RESULT_CLAIM_SUFFIX = ".claim"

ACTION_REUSE = "reuse"
ACTION_FRESH = "fresh"

_QUARANTINE_PARTS = ("00runs", "tmp", "quarantine")

MAX_DETAIL_EXAMPLES = 5


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_timestamp(value: object) -> float | None:
    """Convert an epoch number or ISO-8601 instant to epoch seconds.

    Naive timestamps are interpreted as UTC.  Blank and ``None`` inputs map
    to ``None`` so optional comparisons can be skipped cleanly.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("timestamps must be numeric or ISO-8601 strings")
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"timestamp {value!r} is not finite")
        return result
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"timestamp {value!r} is neither a number nor ISO-8601 text"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def file_sha256(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 hex digest of a file's contents, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_omc_version(
    timeout_seconds: float = 15.0,
    omc_executable: str = "omc",
) -> str:
    """Best-effort OpenModelica version string, ``"unavailable"`` on failure.

    Runs ``<omc_executable> --version`` with a bounded timeout.  Never raises:
    any error resolves to the literal string ``"unavailable"`` so manifests can
    always record something meaningful.  Pass ``omc_executable`` to probe the
    exact binary a run used (e.g. a ``--omc`` CLI value) instead of whatever
    ``omc`` happens to resolve to on ``PATH``.
    """
    try:
        completed = subprocess.run(
            [str(omc_executable), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return "unavailable"
    text = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    if not text:
        return "unavailable"
    return text.splitlines()[0].strip() or "unavailable"


def _looks_like_project_root(path: Path) -> bool:
    """True when ``path`` is an unpacked project tree, git or not."""
    return (
        (path / "pyproject.toml").is_file()
        and (path / "core").is_dir()
        and (path / "helpers").is_dir()
    )


def _find_repo_root(start: Path) -> Path | None:
    """Locate the project root walking upward from ``start``.

    The nearest directory that looks like this project (``pyproject.toml``
    plus ``core/`` and ``helpers/``) wins, so an unpacked source archive
    nested inside some other git checkout still uses its own tree. A bare
    ``.git`` directory is the fallback when those markers are absent.
    """
    try:
        resolved = start.resolve()
    except OSError:
        resolved = start
    git_hit: Path | None = None
    for candidate in (resolved, *resolved.parents):
        if _looks_like_project_root(candidate):
            return candidate
        if git_hit is None and (candidate / ".git").exists():
            git_hit = candidate
    return git_hit


_MODULE_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


def collect_git_info(repo_hint: Path | str | None = None) -> dict[str, Any]:
    """Inspect revision-control state for provenance recording.

    Returns ``{"commit": <hex>, "dirty": <bool>, "detected": True}`` when a
    git tree is visible -- searching upward from ``repo_hint`` (defaulting
    to this file's repository).  Without a visible ``.git``, or when git
    queries fail, returns
    ``{"commit": "unknown", "dirty": None, "detected": False}``.
    """
    start = (
        Path(repo_hint) if repo_hint is not None else Path(__file__).resolve().parent
    )
    root = _find_repo_root(start)
    if root is None:
        return {"commit": "unknown", "dirty": None, "detected": False}
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None, "detected": False}
    return {
        "commit": head.stdout.strip() or "unknown",
        "dirty": bool(status.stdout.strip()),
        "detected": True,
    }


# ---------------------------------------------------------------------------
# Manifest construction
# ---------------------------------------------------------------------------


def normalize_overrides(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize CLI overrides into JSON-safe dicts with string keys.

    Accepted leaf types are ``None``, booleans, integers, floats, strings,
    plus nested mappings/sequences of those.  Containers become plain
    dicts/lists with keys coerced to ``str``; anything else raises
    ``TypeError`` instead of being silently reshaped, keeping fingerprints
    honest.  Ordering is irrelevant to fingerprints because hashes come
    from sorted-key serializations.
    """

    def walk(node: Any, where: str) -> Any:
        if node is None or isinstance(node, (bool, int, float, str)):
            return node
        if isinstance(node, Mapping):
            out: dict[str, Any] = {}
            for key, item in node.items():
                child = f"{where}.{key}" if where else str(key)
                out[str(key)] = walk(item, child)
            return out
        if isinstance(node, Sequence) and not isinstance(
            node, (str, bytes, bytearray)
        ):
            return [walk(item, f"{where}[{index}]") for index, item in enumerate(node)]
        raise TypeError(
            f"override value at '{where}' has unsupported type "
            f"{type(node).__name__}; use null, bool, int, float, str, or "
            "nested containers of those"
        )

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(
            f"overrides must be a mapping or None, got {type(value).__name__}"
        )
    return walk(value, "")


def _optional_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be a real number or None") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer or None")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be an integer or None") from None


def _display_repo_relative(path: Path) -> str:
    """Render a path relative to the repository when possible."""
    if _MODULE_REPO_ROOT is not None:
        try:
            return path.resolve().relative_to(_MODULE_REPO_ROOT).as_posix()
        except ValueError:
            pass
    return str(path)


def _coerce_hash_mapping(source_files: Mapping[str, str]) -> dict[str, str]:
    hashed: dict[str, str] = {}
    for label, digest in source_files.items():
        lowered = str(digest).lower()
        if len(lowered) != 64 or any(c not in "0123456789abcdef" for c in lowered):
            raise ValueError(
                f"precomputed hash for source '{label}' is not a SHA-256 hex digest"
            )
        hashed[str(label)] = lowered
    return hashed


def _hash_source_paths(
    paths: Iterable[str | os.PathLike[str]],
    *,
    description: str = "model source file",
) -> dict[str, str]:
    hashed: dict[str, str] = {}
    seen: set[Path] = set()
    for item in paths:
        path = Path(item).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{description} not found: {item}")
        if path in seen:
            continue
        seen.add(path)
        hashed[_display_repo_relative(path)] = file_sha256(path)
    return hashed


def _normalize_git_info(git_info: Mapping[str, Any]) -> dict[str, Any]:
    dirty = git_info.get("dirty")
    return {
        "commit": str(git_info.get("commit", "unknown")),
        "dirty": bool(dirty) if dirty is not None else None,
        "detected": bool(git_info.get("detected", False)),
    }


def build_run_manifest(
    *,
    package_name: str,
    model_name: str,
    source_files: Sequence[str | os.PathLike[str]] | Mapping[str, str] | None = None,
    workflow_python_files: Sequence[str | os.PathLike[str]]
    | Mapping[str, str]
    | None = None,
    overrides: Mapping[str, Any] | None = None,
    solver: str | None = None,
    tolerance: float | None = None,
    start_time: float | None = None,
    stop_time: float | None = None,
    number_of_intervals: int | None = None,
    output_grid: str | None = None,
    frequency_hz: float | None = None,
    perturbation_amplitude: float | None = None,
    setpoint_table_path: str | os.PathLike[str] | None = None,
    setpoint_table_sha256: str | None = None,
    setpoint_table_row: int | None = None,
    setpoint_policy: str | None = None,
    setpoint_policy_exception: bool | None = None,
    backend: str = "local",
    omc_version: str | None = None,
    python_version: str | None = None,
    git_info: Mapping[str, Any] | None = None,
    core_maturity: str | None = None,
    core_physical_data_maturity: str | None = None,
    radial_config: Mapping[str, Any] | None = None,
    outer_annulus_config: Mapping[str, Any] | None = None,
    result_variables: Sequence[str] | None = None,
    result_output_mode: str | None = None,
    workflow_version: str = f"run_results/{__version__}",
    launch_timestamp: float | str | None = None,
) -> dict[str, Any]:
    """Assemble the canonical run manifest dictionary.

    Every parameter becomes one top-level manifest field; see the module
    docstring for the full layout.  The returned dict is JSON-ready, and its
    fingerprint (:func:`manifest_fingerprint`) changes whenever any
    non-volatile ingredient changes -- altered Modelica source bytes, edited
    workflow Python sources (runner or shared helper modules, hashed in the
    ``workflow_python`` section), a different solver/tolerance/time
    span/output grid, changed forcing frequency or amplitude, modified
    overrides, a different setpoint table or selected row, backend choice,
    and interpreter/tool versions.

    Parameters
    ----------
    package_name, model_name:
        Simulation package identity and fully qualified model name.
    source_files:
        Files whose contents participate in the fingerprint.  Either a list
        of paths (hashed here; duplicate paths ignored) or a ready mapping
        of ``{label: sha256_hex}`` when hashes were already collected.
        ``None`` records an empty section.
    workflow_python_files:
        Workflow Python sources whose bytes participate in the fingerprint,
        recorded under :data:`WORKFLOW_PYTHON_FILES_KEY` with the same dual
        shape as ``source_files``.  Callers pass an explicit, complete list:
        the active runner module, ``helpers/run_results.py``, the shared
        path/config modules used to build the request, and every module
        that constructs overrides or post-processes the result CSV before
        acceptance (``helpers/segmented_runs.py`` on segmented code paths).
        ``None`` records an empty section -- historic sidecars then fail
        closed against hash-carrying manifests through the ordinary
        fingerprint mismatch path instead of being silently accepted.
    overrides:
        Command-line / override dictionary, normalized via
        :func:`normalize_overrides`.
    setpoint_table_path, setpoint_table_sha256, setpoint_table_row:
        Provenance of the steady-state setpoint table backing the request;
        ``None`` when unused.  The hash is computed from the file when a
        path is given and no explicit hash is supplied.
    setpoint_policy:
        Active setpoint qualification policy for the consumed table
        (``"legacy-compatible"`` or ``"strict"``; see
        ``helpers/setpoint_provenance.py``).  Recorded as a top-level
        ``setpoint_policy`` manifest field when given -- the producers pass
        it only when a setpoint table was actually consumed, so manifests
        without a table keep their historical shape and fingerprint.
        When the table has a model-version sidecar, the manifest also
        records ``setpoint_model_version`` (the version named there, or
        ``None`` for an unversioned table).
    setpoint_policy_exception:
        True when the legacy exception applied: the consumed table carries
        no ``qualified`` verdict column and the ``legacy-compatible``
        policy let the load proceed.  Recorded as a top-level
        ``setpoint_policy_exception`` field only when True (absence == no
        exception); requires ``setpoint_policy``.
    backend:
        Execution backend: one of :data:`BACKEND_CHOICES`.
    omc_version:
        Explicit OpenModelica version string; ``None`` probes
        ``omc --version`` (yielding ``"unavailable"`` when absent).
    python_version:
        Defaults to the running interpreter version.
    git_info:
        Precomputed revision-control state (shape produced by
        :func:`collect_git_info`); ``None`` collects current state.  Pass a
        stub mapping for hermetic fingerprints.
    core_maturity:
        Optional machine-readable core-maturity label (TASK-20260908-01
        P2; see ``helpers/segmented_runs.CORE_MATURITY``).  Recorded as a
        top-level ``core_maturity`` manifest field when given; ``None``
        omits the field entirely, so manifests that predate the metadata
        program keep their historical shape and fingerprint.
    radial_config:
        Optional JSON-safe mapping identifying the intra-channel radial
        stack / annular-loop configuration of the run (TASK-20260914-01
        P1; see ``helpers.segmented_runs.radial_manifest_fields`` and
        ``helpers.plant_config.radial_manifest_record``).  Recorded as a
        top-level ``intra_channel_radial`` manifest field when given and
        it participates in the fingerprint, so results are never reused
        across different radial / circulation / flow-distribution /
        heat-exchanger configurations (plan §6.8, §8.4).  ``None`` omits
        the field entirely: the disabled default produces no new
        fingerprint-active fields, so disabled-run manifests keep their
        historical shape and fingerprint (the same omission pattern as
        poison-off columns).
    outer_annulus_config:
        Optional JSON-safe mapping identifying the OUTER-CORE fuel-annulus
        / reactor-vessel / fixed-cavity dataset of the run (P8 of
        TASK-20260917-01; see ``helpers.segmented_runs
        .outer_annulus_manifest_fields`` and ``helpers.plant_config
        .outer_annulus_manifest_record``).  Recorded as a top-level
        ``outer_fuel_annulus`` manifest field when given and it
        participates in the fingerprint, so results are never reused
        across different outer-annulus datasets (dataset id, fingerprint,
        maturity, topology/direction codes, cavity state/temperature,
        inventory policy - plan §11.2).  ``None`` omits the field
        entirely: the disabled default produces no new fingerprint-active
        fields, so disabled-run manifests keep their historical shape and
        fingerprint (the same omission pattern as ``radial_config``).
    result_variables:
        Optional selected result-variable set (TASK-20260908-01 P4; see
        ``helpers/segmented_runs.py`` ``RESULT_CONTRACT``).  Recorded as a
        top-level ``result_variables`` list and it participates in the
        fingerprint: the published CSV's columns are part of the request.
        ``None`` omits the field (legacy and direct programmatic paths keep
        their historical manifest shape and wide output).
    result_output_mode:
        Optional output mode recorded alongside ``result_variables``
        (``"contract"`` or ``"full"`` -- the diagnostic
        ``--full-result-output`` path).  ``None`` omits the field.  How the
        mode was mechanically realized (runtime filter vs post-run
        projection) is a post-build toolchain detail; runners surface it in
        their run logs rather than in the fingerprinted request.
    workflow_version:
        Version string identifying the calling workflow implementation
        (for example ``"freq-runFreqNominalParallel/3"``).
    launch_timestamp:
        Wall-clock instant the run was initiated (ISO-8601 text or epoch
        seconds).  Defaults to now; recorded but excluded from the
        fingerprint.
    """
    if backend not in BACKEND_CHOICES:
        raise ValueError(
            f"backend must be one of {list(BACKEND_CHOICES)}, got {backend!r}"
        )

    if source_files is None:
        source_section: dict[str, str] = {}
    elif isinstance(source_files, Mapping):
        source_section = _coerce_hash_mapping(source_files)
    else:
        source_section = _hash_source_paths(source_files)

    if workflow_python_files is None:
        workflow_python_section: dict[str, str] = {}
    elif isinstance(workflow_python_files, Mapping):
        workflow_python_section = _coerce_hash_mapping(workflow_python_files)
    else:
        workflow_python_section = _hash_source_paths(
            workflow_python_files, description="workflow Python source file"
        )

    git_section = (
        _normalize_git_info(git_info) if git_info is not None else collect_git_info()
    )

    if launch_timestamp is None:
        launch_iso = _utc_now_iso()
    else:
        epoch = parse_timestamp(launch_timestamp)
        if epoch is None:
            raise ValueError(f"launch_timestamp {launch_timestamp!r} is blank")
        launch_iso = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )

    interval_count = _optional_int(number_of_intervals, "number_of_intervals")
    if interval_count is not None and interval_count <= 0:
        raise ValueError("number_of_intervals must be positive when provided")
    setpoint_row_value = _optional_int(setpoint_table_row, "setpoint_table_row")

    result_variables_value: list[str] | None = None
    if result_variables is not None:
        result_variables_value = [str(name) for name in result_variables]
        if not result_variables_value:
            raise ValueError(
                "result_variables must name at least one column when provided"
            )
        if any(not name.strip() for name in result_variables_value):
            raise ValueError("result_variables entries must be non-empty strings")
    if result_output_mode is not None and result_variables_value is None:
        # A mode without a variable set is only meaningful for the explicit
        # wide-output diagnostic; contract mode always names its variables.
        if str(result_output_mode) != "full":
            raise ValueError(
                "result_output_mode 'contract' requires result_variables; "
                "pass the contract column set or use mode 'full'"
            )

    setpoint_display: str | None = None
    setpoint_hash_value: str | None = (
        setpoint_table_sha256.lower() if setpoint_table_sha256 else None
    )
    if setpoint_table_path is not None:
        setpoint_path = Path(setpoint_table_path).resolve()
        setpoint_hash_value = (
            file_sha256(setpoint_path)
            if setpoint_hash_value is None
            else setpoint_hash_value
        )
        setpoint_display = _display_repo_relative(setpoint_path)
    elif setpoint_table_sha256 is not None:
        raise ValueError("setpoint_table_sha256 requires setpoint_table_path")
    elif setpoint_row_value is not None:
        raise ValueError("setpoint_table_row requires setpoint_table_path")

    if setpoint_policy_exception is not None and setpoint_policy is None:
        raise ValueError("setpoint_policy_exception requires setpoint_policy")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_name": str(package_name),
        "model_name": str(model_name),
        "source_files": source_section,
        "workflow_python": workflow_python_section,
        "git_commit": git_section["commit"],
        "git_dirty": git_section["dirty"],
        "python_version": python_version or platform.python_version(),
        "omc_version": omc_version if omc_version is not None else probe_omc_version(),
        "overrides": normalize_overrides(overrides),
        "setpoint_table_path": setpoint_display,
        "setpoint_table_sha256": setpoint_hash_value,
        "setpoint_table_row": setpoint_row_value,
        "solver": None if solver is None else str(solver),
        "tolerance": _optional_float(tolerance, "tolerance"),
        "start_time": _optional_float(start_time, "start_time"),
        "stop_time": _optional_float(stop_time, "stop_time"),
        "number_of_intervals": interval_count,
        "output_grid": None if output_grid is None else str(output_grid),
        "frequency_hz": _optional_float(frequency_hz, "frequency_hz"),
        "perturbation_amplitude": _optional_float(
            perturbation_amplitude, "perturbation_amplitude"
        ),
        "backend": backend,
        "workflow_version": str(workflow_version),
        "launch_timestamp_utc": launch_iso,
    }
    if core_maturity is not None:
        manifest["core_maturity"] = str(core_maturity)
    if core_physical_data_maturity is not None:
        # Second maturity axis (rev031 review): physical-data provenance.
        manifest["core_physical_data_maturity"] = str(core_physical_data_maturity)
    if radial_config is not None:
        # Fingerprint-active only when the radial stack is enabled: the
        # normalization keeps JSON-safe leaves (bool/int/float/str) and
        # rejects anything else loudly instead of reshaping silently.
        manifest["intra_channel_radial"] = normalize_overrides(radial_config)
    if outer_annulus_config is not None:
        # Fingerprint-active only when the outer-annulus dataset is enabled
        # (the same field-omission pattern as intra_channel_radial).
        manifest["outer_fuel_annulus"] = normalize_overrides(outer_annulus_config)
    if setpoint_policy is not None:
        manifest["setpoint_policy"] = str(setpoint_policy)
    if setpoint_table_path is not None:
        # Model version the consumed table was generated for (its
        # <stem>.model_version.json sidecar; helpers.setpoint_model_version).
        # Recorded only when the table has a sidecar, so manifests of tables
        # without one keep their historical shape and fingerprint.
        from helpers.setpoint_model_version import sidecar_path, table_model_version

        consumed = Path(setpoint_table_path).resolve()
        if sidecar_path(consumed).is_file():
            manifest["setpoint_model_version"] = table_model_version(consumed)
    if setpoint_policy_exception is not None and bool(setpoint_policy_exception):
        # Absence == no exception (qualified table, or a strict-mode load
        # that would have refused an absent column before reaching here).
        manifest["setpoint_policy_exception"] = True
    if result_variables_value is not None:
        manifest["result_variables"] = result_variables_value
    if result_output_mode is not None:
        manifest["result_output_mode"] = str(result_output_mode)
    return manifest


# ---------------------------------------------------------------------------
# Canonical JSON, fingerprints, difference reporting
# ---------------------------------------------------------------------------


def canonical_manifest_json(manifest: Mapping[str, Any]) -> str:
    """Serialize a manifest deterministically: sorted keys, compact separators."""
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of a manifest, ignoring volatile fields.

    The digest covers canonical JSON of every non-volatile field, capturing
    model sources, tool versions, overrides, setpoint provenance, numerics,
    forcing parameters, and backend identically across runs of the same
    request while ignoring wall-clock noise.
    """
    core = {
        key: val for key, val in manifest.items() if key not in VOLATILE_MANIFEST_KEYS
    }
    return hashlib.sha256(canonical_manifest_json(core).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ManifestDifference:
    """One differing manifest field (volatile keys are never reported)."""

    field_path: str
    expected: Any
    recorded: Any
    recorded_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field_path,
            "expected": self.expected,
            "recorded": self.recorded,
            "recorded_present": self.recorded_present,
        }


def _values_equal(left: Any, right: Any) -> bool:
    """Representation-aware equality matching canonical-JSON semantics.

    ``manifest_fingerprint`` hashes the canonical ``json.dumps`` rendering of
    each field, so equality must agree with that rendering: booleans remain
    distinct from every number, and leaves compare through their serialized
    form so ``1`` and ``1.0`` (rendered ``1`` vs ``1.0``, hence hashed
    differently) surface as differing fields instead of masking a real
    request delta behind ``(no manifest differences)``.  Values that cannot
    be canonically serialized count as differing; valid manifests never hold
    them because computing their fingerprint would have raised first.
    """
    left_is_bool = isinstance(left, bool)
    right_is_bool = isinstance(right, bool)
    if left_is_bool or right_is_bool:
        return left_is_bool and right_is_bool and left == right
    try:
        return canonical_manifest_json(left) == canonical_manifest_json(right)
    except (TypeError, ValueError):
        return False


def diff_manifests(
    expected: Mapping[str, Any], recorded: Mapping[str, Any]
) -> tuple[ManifestDifference, ...]:
    """Compare two manifests field-by-field; volatile keys are excluded.

    Nested sections produce dotted field paths.  Fields expected but absent
    from ``recorded`` yield entries with ``recorded_present=False``.  Extra
    keys present only in ``recorded`` are ignored so schema growth never
    invalidates historical results retroactively.
    """
    differences: list[ManifestDifference] = []

    def walk(exp: Mapping[str, Any], rec: Mapping[str, Any], prefix: str) -> None:
        for key in sorted(exp):
            if key in VOLATILE_MANIFEST_KEYS:
                continue
            child = f"{prefix}.{key}" if prefix else str(key)
            exp_val = exp[key]
            if key not in rec:
                differences.append(ManifestDifference(child, exp_val, None, False))
                continue
            rec_val = rec[key]
            if isinstance(exp_val, Mapping) and isinstance(rec_val, Mapping):
                walk(exp_val, rec_val, child)
            elif not _values_equal(exp_val, rec_val):
                differences.append(ManifestDifference(child, exp_val, rec_val, True))

    walk(expected, recorded, "")
    return tuple(differences)


def format_manifest_differences(differences: Iterable[ManifestDifference]) -> str:
    """Human-readable block listing differing manifest fields (or ``none``)."""
    items = list(differences)
    if not items:
        return "(no manifest differences)"

    def render(value: object) -> str:
        text = repr(value)
        return text if len(text) <= 120 else text[:117] + "..."

    lines: list[str] = []
    for item in items:
        recorded = "<absent>" if not item.recorded_present else render(item.recorded)
        lines.append(
            f"- {item.field_path}: expected={render(item.expected)}, "
            f"recorded={recorded}"
        )
    return "\n".join(lines)


def radial_reuse_conflict(
    expected: Mapping[str, Any] | None,
    recorded: Mapping[str, Any] | None,
) -> str | None:
    """Name a radial/loop fingerprint reuse conflict, or ``None``.

    The reuse-refusal rule of card A16 (plan §6.8, §8.4): a result
    directory must not be reused across different radial / circulation /
    flow-distribution / heat-exchanger fingerprints. ``diff_manifests``
    already refuses an expected radial record against a recorded manifest
    lacking it (the fingerprint walk covers every *expected* key), but it
    deliberately ignores extra *recorded* keys so schema growth never
    invalidates historical results retroactively -- which would let a
    radial-configured result stand in for a request that names no radial
    record. This guard closes that one-sided hole explicitly:

    - the expected request carries :data:`RADIAL_MANIFEST_KEY` and the
      recorded manifest does not -> conflict (a disabled/unclaimed request
      cannot reuse a radial-configured directory);
    - the recorded manifest carries it and the expected request does not
      -> conflict;
    - both carry it but the records differ -> conflict, naming the two
      deterministic fingerprints
      (:data:`RADIAL_FINGERPRINT_KEY` / :data:`ANNULAR_LOOP_FINGERPRINT_KEY`)
      plus the full differing records;
    - both absent, or byte-identical records -> ``None`` (the ordinary
      fingerprint comparison remains the authority).

    Historical manifests never carry the field (it landed with the radial
    program), so the one-sided refusals cannot invalidate any pre-radial
    result.
    """

    expected_record = (
        expected.get(RADIAL_MANIFEST_KEY)
        if isinstance(expected, Mapping)
        else None
    )
    recorded_record = (
        recorded.get(RADIAL_MANIFEST_KEY)
        if isinstance(recorded, Mapping)
        else None
    )
    if expected_record is None and recorded_record is None:
        return None
    if expected_record is None or recorded_record is None:
        side = "expected" if expected_record is not None else "recorded"
        return (
            f"{RADIAL_MANIFEST_KEY} fingerprint mismatch: the {side} "
            "manifest carries an intra-channel radial / annular-loop "
            "configuration the other side does not; a result directory is "
            "not reusable across radial / circulation / flow-distribution / "
            "heat-exchanger fingerprints (plan §6.8, §8.4). "
            f"expected={expected_record!r}; recorded={recorded_record!r}"
        )
    try:
        same = canonical_manifest_json(expected_record) == canonical_manifest_json(
            recorded_record
        )
    except (TypeError, ValueError):
        same = False
    if same:
        return None
    return (
        f"{RADIAL_MANIFEST_KEY} fingerprint mismatch: the recorded "
        "intra-channel radial / annular-loop configuration differs from "
        "the requested one; a result directory is not reusable across "
        "radial / circulation / flow-distribution / heat-exchanger "
        "fingerprints (plan §6.8, §8.4). "
        f"expected.{RADIAL_FINGERPRINT_KEY}="
        f"{expected_record.get(RADIAL_FINGERPRINT_KEY)!r}, "
        f"expected.{ANNULAR_LOOP_FINGERPRINT_KEY}="
        f"{expected_record.get(ANNULAR_LOOP_FINGERPRINT_KEY)!r}, "
        f"recorded.{RADIAL_FINGERPRINT_KEY}="
        f"{recorded_record.get(RADIAL_FINGERPRINT_KEY)!r}, "
        f"recorded.{ANNULAR_LOOP_FINGERPRINT_KEY}="
        f"{recorded_record.get(ANNULAR_LOOP_FINGERPRINT_KEY)!r}"
    )


# ---------------------------------------------------------------------------
# Sidecar paths and persistence
# ---------------------------------------------------------------------------


def make_tmp_path(result_path: str | os.PathLike[str]) -> Path:
    """Temporary companion path for in-progress result writing."""
    return Path(f"{result_path}{RESULT_TMP_SUFFIX}")


# Convenience alias.
tmp_path_for = make_tmp_path


def manifest_sidecar_path(result_path: str | os.PathLike[str]) -> Path:
    """Path of ``{stem}.manifest.json`` beside a result CSV."""
    return Path(result_path).with_suffix(".manifest.json")


def validation_report_path(result_path: str | os.PathLike[str]) -> Path:
    """Path of ``{stem}.validation.json`` beside a result CSV."""
    return Path(result_path).with_suffix(".validation.json")


def atomic_publish(tmp_path: str | os.PathLike[str], final_path: str | os.PathLike[str]) -> None:
    """Move a completed temporary output onto its final name via os.replace."""
    os.replace(str(tmp_path), str(final_path))


def atomic_write_text(target: str | os.PathLike[str], text: str) -> None:
    """Write text onto ``target`` atomically.

    The bytes land under a unique temporary name in the destination
    directory first (``tempfile.mkstemp``) and are moved onto the final
    name with :func:`os.replace` only once the file is written and flushed,
    so concurrent readers observe either the previous content or the
    complete new content -- never a partial file.  Used for aggregate
    outputs (collection CSVs, MATLAB files, manifests, setpoint tables) and
    available to any workflow that publishes a small text artifact.
    """
    _write_atomic(Path(target), text)


def _serialize_pretty(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, scratch_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".partial", dir=str(target.parent)
    )
    scratch = Path(scratch_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(str(scratch), str(target))
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def read_manifest_sidecar(result_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Load a manifest sidecar payload; ``None`` when absent or unreadable."""
    sidecar = manifest_sidecar_path(result_path)
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def read_validation_report(result_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Load a validation sidecar; ``None`` when absent or unreadable."""
    sidecar = validation_report_path(result_path)
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_manifest_sidecar(
    result_path: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    fingerprint: str | None = None,
) -> Path:
    """Persist the canonical manifest beside the result CSV atomically."""
    if fingerprint is None:
        fingerprint = manifest_fingerprint(manifest)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": MANIFEST_ALGORITHM,
        "fingerprint": fingerprint.lower(),
        "manifest": dict(manifest),
        "written_at_utc": _utc_now_iso(),
    }
    sidecar = manifest_sidecar_path(result_path)
    _write_atomic(sidecar, _serialize_pretty(payload))
    return sidecar


def write_validation_sidecar(
    result_path: str | os.PathLike[str],
    report: ValidationReport | Mapping[str, Any],
) -> Path:
    """Persist a validation report beside the result CSV atomically."""
    payload = (
        report.to_payload_dict()
        if isinstance(report, ValidationReport)
        else dict(report)
    )
    sidecar = validation_report_path(result_path)
    _write_atomic(sidecar, _serialize_pretty(payload))
    return sidecar


def write_result_sidecars(
    result_path: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    validation_report: ValidationReport | Mapping[str, Any] | None = None,
    fingerprint: str | None = None,
) -> tuple[Path, Path | None]:
    """Convenience writer producing both sidecars; returns their paths."""
    manifest_sidecar = write_manifest_sidecar(result_path, manifest, fingerprint)
    report_sidecar = (
        write_validation_sidecar(result_path, validation_report)
        if validation_report is not None
        else None
    )
    return manifest_sidecar, report_sidecar


# ---------------------------------------------------------------------------
# Exclusive per-result claims (P5 same-slot claim)
# ---------------------------------------------------------------------------


def make_claim_path(result_path: str | os.PathLike[str]) -> Path:
    """Companion claim-file path guarding one result slot from concurrency."""
    return Path(f"{result_path}{RESULT_CLAIM_SUFFIX}")


class ResultSlotClaimTimeout(RuntimeError):
    """Raised by :func:`acquire_result_claim` when the wait budget expires."""


class ResultSlotClaim:
    """Held exclusive advisory claim on one result slot (cross-process safe).

    The claim is an ``flock`` on the ``<result>.claim`` companion file.  The
    kernel drops it when the descriptor closes -- including on process exit
    -- so a crashed writer never leaves a stale claim behind.  ``release``
    is idempotent; the lock file itself is intentionally kept on disk,
    because deleting it would let a third writer create a fresh inode and
    slip past a waiter that still blocks on the old one.
    """

    def __init__(self, lock_path: Path, fd: int) -> None:
        self.lock_path = lock_path
        self._fd: int | None = fd

    def release(self) -> None:
        """Release the claim; safe to call more than once."""
        if fcntl is None:  # pragma: no cover - project targets POSIX hosts
            self._fd = None
            return
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass  # unlocking is best-effort; close still drops the lock
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "ResultSlotClaim":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.release()
        return False


def _claim_holder_pid(lock_path: Path) -> int | None:
    """Best-effort pid of the recorded lock holder (for error messages)."""
    try:
        for line in lock_path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if text.startswith("pid="):
                return int(text[4:].strip() or 0)
    except OSError:
        return None
    return None


def acquire_result_claim(
    result_path: str | os.PathLike[str],
    *,
    timeout_s: float | None = None,
    poll_seconds: float = 0.05,
) -> ResultSlotClaim:
    """Claim exclusive write access to one result slot, blocking until free.

    Two concurrent launches of the same slot would otherwise quarantine each
    other's in-flight output: each ``prepare_result_path`` decision moves
    prior artifacts (including the other launch's half-written ``*.tmp``)
    out of the way before its own simulation.  The claim is an ``flock`` on
    the ``.claim`` companion file, so a second same-slot launch waits here
    -- at most ``timeout_s`` seconds (``None`` waits indefinitely) -- and
    proceeds only after the first writer published and released.  Its own
    reuse check then finds the finished pair instead of racing it.

    The kernel releases the lock when the descriptor closes, including on
    process exit, so a crashed writer cannot leave a stale claim behind.
    Raises :class:`ResultSlotClaimTimeout` when the wait budget expires.
    """
    if fcntl is None:  # pragma: no cover - project targets POSIX hosts
        raise RuntimeError(
            "exclusive result-slot claims require POSIX flock; no compatible "
            "lock primitive is available on this platform"
        )
    lock_path = make_claim_path(result_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = None if timeout_s is None else time.monotonic() + float(timeout_s)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                os.close(fd)
                raise
            expired = deadline is not None and time.monotonic() >= deadline
            if expired:
                os.close(fd)
                holder = _claim_holder_pid(lock_path)
                detail = f" (holder pid {holder})" if holder else ""
                raise ResultSlotClaimTimeout(
                    "another writer holds the result slot "
                    f"({lock_path}); gave up after {timeout_s:g} s{detail}"
                ) from exc
            time.sleep(max(0.0, float(poll_seconds)))
    try:
        os.ftruncate(fd, 0)
        os.write(
            fd,
            (
                f"pid={os.getpid()}\n"
                f"acquired_at_utc={_utc_now_iso()}\n"
            ).encode("utf-8"),
        )
    except OSError:
        pass  # the payload is informational only; the lock itself is held
    return ResultSlotClaim(lock_path, fd)


# ---------------------------------------------------------------------------
# Validated atomic publication (P5)
# ---------------------------------------------------------------------------


class ResultRejectedError(RuntimeError):
    """A finished simulation output failed result validation.

    Carries the :class:`ValidationReport` in :attr:`report` so callers can
    name the failed checks.  When the raw output lived at a temporary
    companion path it has been moved under the quarantine area, so no
    reusable final result is left behind.
    """

    def __init__(self, report: ValidationReport, message: str | None = None) -> None:
        self.report = report
        failed = ", ".join(report.failed_checks) or "unknown"
        super().__init__(
            message
            or (
                "simulation output rejected by result validation for "
                f"{Path(report.result_path).name}; failed checks: {failed}"
            )
        )


def publish_validated_result(
    tmp_path: str | os.PathLike[str],
    final_path: str | os.PathLike[str],
    *,
    manifest: Mapping[str, Any],
    required_columns: Sequence[str] = DEFAULT_REQUIRED_COLUMNS,
    duplicate_column_allowlist: Sequence[str] = (),
    time_column: str = "time",
    mtime_tolerance_s: float = DEFAULT_MTIME_TOLERANCE_S,
    requested_stop_time: float | None = None,
    stop_time_slack_s: float = 0.0,
    min_samples: int | None = DEFAULT_MIN_SAMPLES,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> ValidationReport:
    """Validate a finished raw output and publish it atomically.

    The documented order of events for one result slot:

    1. the simulation is directed at ``tmp_path`` (``make_tmp_path(final)``)
       so the final path stays empty for the whole in-flight window;
    2. the raw output is validated in place against the request manifest;
    3. only a passing report publishes the bytes onto ``final_path`` via
       :func:`atomic_publish`, then both provenance sidecars are written
       beside the published file.

    A failed validation never publishes: the temporary output moves under
    the quarantine area (falling back to an in-place unlink) and
    :class:`ResultRejectedError` carries the report, so a failed or
    interrupted run never leaves a reusable final result.  The returned
    report is retargeted to the final path; its ``result_sha256`` binds the
    sidecar verdict to the exact published bytes.

    Compatibility: when the temporary companion does not exist but the
    final file does -- a simulation engine that ignored ``-r=``, or the
    historical direct-write layout still exercised by direct programmatic
    callers -- the final file is validated in place and the sidecars are
    written beside it without any move; a rejected file is left untouched.
    Production runners always direct their output at the temporary
    companion, so this branch only serves legacy callers and omc versions
    that ignore the result-path flag.
    """
    raw = Path(tmp_path)
    final = Path(final_path)
    raw_is_final = raw == final
    raw_exists = _lexists(raw)
    if raw_exists:
        source = raw
    elif not raw_is_final and _lexists(final):
        # Legacy compatibility: the engine wrote the final name directly, so
        # the finished bytes are validated where they already stand.
        source = final
    else:
        # Nothing was produced at all; validating the absent path yields a
        # report that names the failing checks on the uniform rejection path.
        source = raw

    report = validate_result_csv(
        source,
        required_columns=required_columns,
        duplicate_column_allowlist=duplicate_column_allowlist,
        time_column=time_column,
        launch_timestamp=manifest.get("launch_timestamp_utc"),
        mtime_tolerance_s=mtime_tolerance_s,
        requested_stop_time=requested_stop_time,
        stop_time_slack_s=stop_time_slack_s,
        min_samples=min_samples,
    )

    if not report.passed:
        if source == raw and not raw_is_final:
            # The unusable temporary output must never reach the final name;
            # park the raw bytes under the quarantine area for forensics.  A
            # refused quarantine (filesystem error) falls back to an in-place
            # unlink so the failure path can never leave a reusable artifact.
            # A rejected legacy file already sitting at the final name is not
            # ours to move and stays untouched.
            try:
                quarantine_existing_artifacts([raw], quarantine_root=quarantine_root)
            except (OSError, ValueError):
                try:
                    Path(raw).unlink(missing_ok=True)
                except OSError:
                    pass
        raise ResultRejectedError(
            report,
            message=(
                "simulation output rejected by result validation for "
                f"{Path(final_path).name}; failed checks: "
                + (", ".join(report.failed_checks) or "unknown")
            ),
        )

    if source is not final:
        atomic_publish(raw, final)
    # The validated bytes and the published bytes are identical (rename, not
    # rewrite), so the recorded content hash stays correct; only the recorded
    # path is retargeted from the temporary companion to the final slot.
    report = _dataclass_replace(report, result_path=Path(final_path).resolve())
    write_result_sidecars(final, manifest, report)
    return report


# ---------------------------------------------------------------------------
# Result CSV validation
# ---------------------------------------------------------------------------

CHECK_FRESHNESS = "mtime_after_launch"
CHECK_HEADER = "header_nonempty"
CHECK_DATA_ROWS = "has_data_rows"
CHECK_REQUIRED_COLUMNS = "required_columns_present"
CHECK_DUPLICATE_COLUMNS = "duplicate_columns_accepted"
CHECK_TIME_MONOTONIC = "time_monotonic_nondecreasing"
CHECK_FINITE = "required_columns_finite"
CHECK_ROW_SHAPE = "rows_match_header_width"
CHECK_STOP_REACHED = "final_time_reaches_requested_stop"
CHECK_SAMPLE_COUNT = "minimum_sample_count"


@dataclass(frozen=True)
class ValidationCheck:
    """One named validator outcome."""

    name: str
    status: str  # "pass" | "fail" | "skipped"
    detail: Any = None

    def to_payload(self) -> dict[str, Any]:
        return {"check": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class ValidationReport:
    """Aggregate outcome of :func:`validate_result_csv`.

    ``passed`` is true only when at least one check ran and none failed;
    skipped checks contribute no evidence, and a report in which every
    check was skipped conservatively fails.

    ``result_sha256`` carries the SHA-256 of the validated file's bytes (or
    ``None`` when the file could not be read).  Sidecars persist it so that
    downstream consumers -- e.g. result collectors -- can verify that the
    CSV on disk is byte-identical to the content that passed validation.
    """

    result_path: Path
    checks: tuple[ValidationCheck, ...]
    n_data_rows: int = 0
    first_time: float | None = None
    final_time: float | None = None
    result_sha256: str | None = None

    @property
    def passed(self) -> bool:
        statuses = [check.status for check in self.checks]
        if not statuses or all(status == "skipped" for status in statuses):
            return False
        return "fail" not in statuses

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.status == "fail")

    def to_payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "run-result-validation",
            "result_file": str(self.result_path),
            "result_sha256": self.result_sha256,
            "n_data_rows": self.n_data_rows,
            "first_time": self.first_time,
            "final_time": self.final_time,
            "passed": self.passed,
            "failed_checks": list(self.failed_checks),
            "checks": [check.to_payload() for check in self.checks],
            "validated_at_utc": _utc_now_iso(),
        }


def _clean_header_cell(cell: str) -> str:
    stripped = cell.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        stripped = stripped[1:-1].strip()
    return stripped


def _to_float(cell: str) -> float | None:
    text = cell.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class _Scanner:
    """Collect header/body statistics in exactly one pass over the CSV."""

    __slots__ = (
        "path",
        "required_names",
        "time_column_name",
        "allowlisted",
        "decrease_tolerance",
        "open_error",
        "header_cells",
        "lower_names",
        "n_data_rows",
        "malformed_rows",
        "first_time",
        "last_time",
        "monotonic_violations",
        "first_monotonic_violation",
        "finite_problems",
        "missing_required",
        "required_index_by_folded",
        "offender_duplicates",
        "allowlisted_duplicates",
        "time_index",
        "time_from_fallback",
    )

    def __init__(
        self,
        path: Path,
        *,
        required_names: Sequence[str],
        time_column_name: str,
        allowlist_casefolded: frozenset[str],
        decrease_tolerance: float,
    ) -> None:
        self.path = path
        self.required_names = [str(name) for name in required_names]
        self.time_column_name = str(time_column_name)
        self.allowlisted = allowlist_casefolded
        self.decrease_tolerance = decrease_tolerance

        self.open_error: str | None = None
        self.header_cells: list[str] = []
        self.lower_names: list[str] = []
        self.n_data_rows = 0
        self.malformed_rows = 0
        self.first_time: float | None = None
        self.last_time: float | None = None
        self.monotonic_violations = 0
        self.first_monotonic_violation: tuple[int, float, float] | None = None
        self.finite_problems: list[tuple[int, str, str]] = []

        self.missing_required: list[str] = []
        self.required_index_by_folded: dict[str, int] = {}
        self.offender_duplicates: dict[str, int] = {}
        self.allowlisted_duplicates: dict[str, int] = {}
        self.time_index: int | None = None
        self.time_from_fallback = False

    # -- scanning ----------------------------------------------------------

    def run(self) -> "_Scanner":
        try:
            handle = open(
                self.path, "r", encoding="utf-8-sig", errors="replace", newline=""
            )
        except OSError as exc:
            self.open_error = f"{type(exc).__name__}: {exc}"
            return self

        with handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            self.header_cells = [_clean_header_cell(cell) for cell in (header or [])]
            self.lower_names = [cell.casefold() for cell in self.header_cells]
            if header is None:
                return self
            self._resolve_columns()

            previous_time: float | None = None
            for raw_row in reader:
                if not raw_row or all(not cell.strip() for cell in raw_row):
                    continue
                self.n_data_rows += 1
                row_number = self.n_data_rows
                if len(raw_row) < len(self.header_cells):
                    self.malformed_rows += 1

                for index in self._wanted_indices():
                    label = (
                        self.header_cells[index]
                        if index < len(self.header_cells)
                        else ""
                    )
                    if index >= len(raw_row):
                        self.finite_problems.append((row_number, label, "missing cell"))
                        continue
                    value = _to_float(raw_row[index])
                    if value is None:
                        snippet = raw_row[index][:80]
                        self.finite_problems.append(
                            (row_number, label, snippet or "blank cell")
                        )
                        continue
                    if index != self.time_index:
                        continue
                    if self.first_time is None:
                        self.first_time = value
                    previous_time, self.last_time = self.last_time, value
                    if (
                        previous_time is not None
                        and value < previous_time - self.decrease_tolerance
                    ):
                        self.monotonic_violations += 1
                        if self.first_monotonic_violation is None:
                            self.first_monotonic_violation = (
                                row_number,
                                previous_time,
                                value,
                            )
        return self

    # -- helpers -----------------------------------------------------------

    def _resolve_columns(self) -> None:
        for original in self.required_names:
            folded = original.casefold()
            if folded in self.lower_names:
                self.required_index_by_folded[folded] = self.lower_names.index(folded)
            else:
                self.missing_required.append(original)

        folded_dups = Counter(self.lower_names)
        first_seen: dict[str, int] = {}
        for position, folded in enumerate(self.lower_names):
            first_seen.setdefault(folded, position)
        for folded, count in sorted(folded_dups.items()):
            if count <= 1:
                continue
            display = self.header_cells[first_seen[folded]]
            if folded in self.allowlisted:
                self.allowlisted_duplicates[display] = count
            else:
                self.offender_duplicates[display] = count

        wanted = self.time_column_name.strip().casefold()
        if wanted in self.lower_names:
            self.time_index = self.lower_names.index(wanted)
            self.time_from_fallback = False
        else:
            # OpenModelica result CSVs always lead with the time column; fall
            # back to column 0 when no column is literally named like that.
            self.time_index = 0 if self.header_cells else None
            self.time_from_fallback = self.time_index is not None

    def _wanted_indices(self) -> list[int]:
        indices = set(self.required_index_by_folded.values())
        if self.time_index is not None:
            indices.add(self.time_index)
        return sorted(indices)


def validate_result_csv(
    result_path: str | os.PathLike[str],
    *,
    required_columns: Sequence[str] = DEFAULT_REQUIRED_COLUMNS,
    duplicate_column_allowlist: Sequence[str] = (),
    time_column: str = "time",
    launch_timestamp: float | str | None = None,
    mtime_tolerance_s: float = DEFAULT_MTIME_TOLERANCE_S,
    requested_stop_time: float | None = None,
    stop_time_slack_s: float = 0.0,
    min_samples: int | None = DEFAULT_MIN_SAMPLES,
) -> ValidationReport:
    """Validate a finished simulation result CSV.

    Every acceptance check appears exactly once in the returned report,
    with a stable ordering:

    - ``mtime_after_launch``: modification time lies later than the recorded
      launch timestamp (skipped when no timestamp is available);
      ``mtime_tolerance_s`` defaults to :data:`DEFAULT_MTIME_TOLERANCE_S`,
      absorbing filesystem timestamp granularity on container filesystems.
    - ``header_nonempty`` / ``has_data_rows``: structural sanity.
    - ``required_columns_present``: case-insensitive name lookup.
    - ``duplicate_columns_accepted``: repeated header names fail unless every
      duplicated name appears in ``duplicate_column_allowlist``.
    - ``time_monotonic_nondecreasing``: sampled times never go backwards.
    - ``required_columns_finite``: every required-column cell parses as a
      finite floating-point value (missing or blank cells included).
    - ``rows_match_header_width``: truncated rows shorter than the header
      are rejected.
    - ``final_time_reaches_requested_stop``: last sampled time clears the
      requested stop minus ``stop_time_slack_s`` (skipped without a stop).
    - ``minimum_sample_count``: enough data rows (skipped when unspecified).

    Statuses are ``pass`` / ``fail`` / ``skipped``;
    :attr:`ValidationReport.passed` requires at least one executed check and
    no failures, so an unreadable or empty file always fails overall.
    Column-name matching is case-insensitive throughout (quoted Modelica
    headers occasionally drift in letter case between solvers); duplicates
    are judged on the folded names too.

    The report also records the SHA-256 of the result file's bytes in
    ``result_sha256`` (``None`` when the file cannot be read) so the
    validation sidecar binds its verdict to the exact content validated.
    """
    path = Path(result_path)
    try:
        result_hash: str | None = file_sha256(path)
    except OSError:
        result_hash = None
    checks: list[ValidationCheck] = []
    original_required = [str(name) for name in required_columns]
    allowlist_folded = frozenset(
        str(name).strip().casefold() for name in duplicate_column_allowlist
    )

    # --- freshness ---------------------------------------------------------
    launch_epoch = parse_timestamp(launch_timestamp)
    if launch_epoch is None:
        checks.append(
            ValidationCheck(
                CHECK_FRESHNESS, "skipped", "No launch timestamp recorded for comparison."
            )
        )
    else:
        try:
            modified_at = path.stat().st_mtime
        except OSError as exc:
            checks.append(
                ValidationCheck(CHECK_FRESHNESS, "fail", f"Cannot stat result file: {exc}")
            )
        else:
            fresh_slack = max(0.0, float(mtime_tolerance_s))
            is_fresh = modified_at > launch_epoch - fresh_slack
            checks.append(
                ValidationCheck(
                    CHECK_FRESHNESS,
                    "pass" if is_fresh else "fail",
                    {
                        "modified_at_utc": datetime.fromtimestamp(
                            modified_at, tz=timezone.utc
                        ).isoformat(timespec="milliseconds"),
                        "launch_epoch_s": launch_epoch,
                        "tolerance_s": fresh_slack,
                    },
                )
            )

    scanner = _Scanner(
        path,
        required_names=original_required,
        time_column_name=time_column,
        allowlist_casefolded=allowlist_folded,
        decrease_tolerance=0.0,
    ).run()

    if scanner.open_error is not None:
        unreadable = f"Result file unreadable ({scanner.open_error})."
        checks.append(ValidationCheck(CHECK_HEADER, "fail", unreadable))
        checks.append(ValidationCheck(CHECK_DATA_ROWS, "fail", unreadable))
        checks.append(ValidationCheck(CHECK_REQUIRED_COLUMNS, "fail", unreadable))
        checks.append(ValidationCheck(CHECK_DUPLICATE_COLUMNS, "skipped", unreadable))
        checks.append(ValidationCheck(CHECK_TIME_MONOTONIC, "skipped", unreadable))
        checks.append(ValidationCheck(CHECK_FINITE, "fail", unreadable))
        checks.append(ValidationCheck(CHECK_ROW_SHAPE, "skipped", unreadable))
        checks.append(ValidationCheck(CHECK_STOP_REACHED, "skipped", unreadable))
        checks.append(ValidationCheck(CHECK_SAMPLE_COUNT, "skipped", unreadable))
        return ValidationReport(result_path=path.resolve(), checks=tuple(checks))

    # --- structure -----------------------------------------------------------
    checks.append(
        ValidationCheck(
            CHECK_HEADER,
            "pass" if scanner.header_cells else "fail",
            {"columns": len(scanner.header_cells)},
        )
    )
    checks.append(
        ValidationCheck(
            CHECK_DATA_ROWS,
            "pass" if scanner.n_data_rows >= 1 else "fail",
            {"n_data_rows": scanner.n_data_rows},
        )
    )
    checks.append(
        ValidationCheck(
            CHECK_REQUIRED_COLUMNS,
            "pass" if not scanner.missing_required else "fail",
            {"required": original_required, "missing": scanner.missing_required},
        )
    )
    checks.append(
        ValidationCheck(
            CHECK_DUPLICATE_COLUMNS,
            "pass" if not scanner.offender_duplicates else "fail",
            {
                "rejected_duplicates": scanner.offender_duplicates,
                "allowlisted_duplicates": scanner.allowlisted_duplicates,
            },
        )
    )

    # --- monotonic time ------------------------------------------------------
    if scanner.n_data_rows < 2:
        checks.append(
            ValidationCheck(
                CHECK_TIME_MONOTONIC,
                "skipped",
                "Fewer than two data rows; monotonicity not evaluated.",
            )
        )
    elif scanner.last_time is None:
        checks.append(
            ValidationCheck(
                CHECK_TIME_MONOTONIC,
                "fail",
                "No parseable finite values found in the time column.",
            )
        )
    elif scanner.monotonic_violations:
        row_number, previous_value, offending_value = (
            scanner.first_monotonic_violation or (0, None, None)
        )
        checks.append(
            ValidationCheck(
                CHECK_TIME_MONOTONIC,
                "fail",
                {
                    "violations": scanner.monotonic_violations,
                    "first_violation": {
                        "data_row": row_number,
                        "previous_time": previous_value,
                        "observed_time": offending_value,
                    },
                },
            )
        )
    else:
        checks.append(
            ValidationCheck(
                CHECK_TIME_MONOTONIC,
                "pass",
                {"n_data_rows": scanner.n_data_rows},
            )
        )

    # --- finiteness ------------------------------------------------------------
    if scanner.finite_problems:
        examples = [
            {"data_row": row, "column": column, "issue": issue}
            for row, column, issue in scanner.finite_problems[:MAX_DETAIL_EXAMPLES]
        ]
        checks.append(
            ValidationCheck(
                CHECK_FINITE,
                "fail",
                {"problem_count": len(scanner.finite_problems), "examples": examples},
            )
        )
    elif scanner.n_data_rows == 0:
        checks.append(
            ValidationCheck(CHECK_FINITE, "skipped", "No data rows to inspect.")
        )
    elif scanner.missing_required:
        absent = ", ".join(scanner.missing_required)
        checks.append(
            ValidationCheck(
                CHECK_FINITE, "skipped", f"Required columns absent: {absent}."
            )
        )
    else:
        checked_columns = [
            name
            for name in original_required
            if name.casefold() in scanner.required_index_by_folded
        ]
        checks.append(
            ValidationCheck(
                CHECK_FINITE, "pass", {"columns_checked": checked_columns}
            )
        )

    # --- row shape -----------------------------------------------------------------
    if not scanner.header_cells:
        checks.append(
            ValidationCheck(CHECK_ROW_SHAPE, "skipped", "No header row detected.")
        )
    else:
        checks.append(
            ValidationCheck(
                CHECK_ROW_SHAPE,
                "fail" if scanner.malformed_rows else "pass",
                {"rows_shorter_than_header": scanner.malformed_rows},
            )
        )

    # --- stop coverage ------------------------------------------------------------------
    if requested_stop_time is None:
        checks.append(
            ValidationCheck(
                CHECK_STOP_REACHED,
                "skipped",
                "No requested stop time supplied.",
            )
        )
    elif scanner.last_time is None:
        checks.append(
            ValidationCheck(
                CHECK_STOP_REACHED,
                "fail",
                {
                    "requested_stop": requested_stop_time,
                    "reason": "no valid time values sampled",
                },
            )
        )
    else:
        stop_slack = max(0.0, float(stop_time_slack_s))
        threshold = float(requested_stop_time) - stop_slack
        reached = scanner.last_time >= threshold
        checks.append(
            ValidationCheck(
                CHECK_STOP_REACHED,
                "pass" if reached else "fail",
                {
                    "requested_stop": float(requested_stop_time),
                    "final_time": scanner.last_time,
                    "slack_s": stop_slack,
                },
            )
        )

    # --- sample count -------------------------------------------------------------------------
    if min_samples is None:
        checks.append(
            ValidationCheck(
                CHECK_SAMPLE_COUNT,
                "skipped",
                "No minimum sample count supplied.",
            )
        )
    else:
        minimum = int(min_samples)
        enough = scanner.n_data_rows >= minimum
        checks.append(
            ValidationCheck(
                CHECK_SAMPLE_COUNT,
                "pass" if enough else "fail",
                {"n_data_rows": scanner.n_data_rows, "min_samples": minimum},
            )
        )

    return ValidationReport(
        result_path=path.resolve(),
        checks=tuple(checks),
        n_data_rows=scanner.n_data_rows,
        first_time=scanner.first_time,
        final_time=scanner.last_time,
        result_sha256=result_hash,
    )


# ---------------------------------------------------------------------------
# Quarantine of stale outputs
# ---------------------------------------------------------------------------


def _lexists(path: str | os.PathLike[str]) -> bool:
    return os.path.lexists(str(path))


def _within(child: Path, ancestor: Path) -> bool:
    try:
        child.relative_to(ancestor)
        return True
    except ValueError:
        return False


def default_quarantine_root() -> Path:
    """Repository-relative quarantine root: ``<repo>/00runs/tmp/quarantine``.

    The repository root is located from the current working directory first,
    falling back to this module's project tree. Discovery prefers a
    ``pyproject.toml`` + ``core/`` + ``helpers/`` layout so unpacked source
    archives (no ``.git``) still land quarantine under
    ``<project>/00runs/tmp/quarantine``. Raises RuntimeError when neither
    walk finds a project root; callers may always pass an explicit
    ``quarantine_root`` instead.
    """
    repo_root = _find_repo_root(Path.cwd()) or _MODULE_REPO_ROOT
    if repo_root is None:
        raise RuntimeError(
            "cannot locate repository root for the default quarantine "
            "directory; pass quarantine_root explicitly"
        )
    return repo_root.joinpath(*_QUARANTINE_PARTS)


def _validate_quarantine_root(root: Path) -> None:
    """Refuse system-temporary locations unless they live inside the repo."""
    resolved = root.expanduser().resolve()
    temp_dir = Path(tempfile.gettempdir()).resolve()
    repo_root = _find_repo_root(Path.cwd()) or _MODULE_REPO_ROOT
    inside_repo = repo_root is not None and _within(resolved, repo_root.resolve())
    if _within(resolved, temp_dir) and not inside_repo:
        raise ValueError(
            f"quarantine root '{root}' resolves under the system temporary "
            f"directory ({temp_dir}); keep run scratch inside the repository "
            "(00runs/tmp/...) or pass an explicit location outside it"
        )


def _new_quarantine_directory(root: Path) -> Path:
    """Create one UTC-stamped quarantine subdirectory for this batch."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target = root / f"{stamp}-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def quarantine_existing_artifacts(
    artifact_paths: Iterable[str | os.PathLike[str]],
    *,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, ...]:
    """Move stale files into ``00runs/tmp/quarantine/<utc-stamp>/``.

    Only paths that currently exist (or dangle as symlinks) participate;
    when nothing exists no directory is created and an empty tuple returns.
    Returns the destination path of every move performed.
    """
    pending = [Path(item) for item in artifact_paths if _lexists(item)]
    if not pending:
        return ()
    root = (
        Path(quarantine_root)
        if quarantine_root is not None
        else default_quarantine_root()
    )
    _validate_quarantine_root(root)
    directory = _new_quarantine_directory(root)
    moved: list[Path] = []
    for source in sorted(pending, key=lambda item: str(item)):
        stem, suffix = os.path.splitext(source.name)
        destination = directory / source.name
        counter = 1
        while destination.exists() or destination.is_symlink():
            destination = directory / f"{stem}_{counter}{suffix}"
            counter += 1
        shutil.move(str(source), str(destination))
        moved.append(destination)
    return tuple(moved)


def _result_artifact_paths(result_path: str | os.PathLike[str]) -> list[Path]:
    """Enumerate every file tied to one result slot (final, tmp, sidecars)."""
    final = Path(result_path)
    return [
        final,
        make_tmp_path(final),
        manifest_sidecar_path(final),
        validation_report_path(final),
    ]


# ---------------------------------------------------------------------------
# Reuse/fresh lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedResult:
    """Outcome of :func:`prepare_result_path` for one result-file slot."""

    action: str  # ACTION_REUSE or ACTION_FRESH
    reason: str
    fingerprint_expected: str | None = None
    fingerprint_recorded: str | None = None
    mismatch_fields: tuple[ManifestDifference, ...] = ()
    quarantined: tuple[Path, ...] = ()
    validation_report: ValidationReport | None = None

    @property
    def can_reuse(self) -> bool:
        return self.action == ACTION_REUSE

    @property
    def reused(self) -> bool:
        return self.can_reuse

    def describe_mismatches(self) -> str:
        return format_manifest_differences(self.mismatch_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "fingerprint_expected": self.fingerprint_expected,
            "fingerprint_recorded": self.fingerprint_recorded,
            "mismatch_fields": [item.to_dict() for item in self.mismatch_fields],
            "quarantined": [str(path) for path in self.quarantined],
            "validation_passed": (
                None
                if self.validation_report is None
                else self.validation_report.passed
            ),
        }


def _fresh_outcome(
    reason: str,
    *,
    fingerprint_expected: str | None,
    fingerprint_recorded: str | None,
    artifacts: Sequence[Path],
    mismatches: tuple[ManifestDifference, ...] = (),
    validation: ValidationReport | None = None,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> PreparedResult:
    moved = quarantine_existing_artifacts(artifacts, quarantine_root=quarantine_root)
    note = f" Moved {len(moved)} prior file(s) into quarantine." if moved else ""
    return PreparedResult(
        action=ACTION_FRESH,
        reason=reason + note,
        fingerprint_expected=fingerprint_expected,
        fingerprint_recorded=fingerprint_recorded,
        mismatch_fields=tuple(mismatches),
        quarantined=moved,
        validation_report=validation,
    )


def _clear_leftover_tmp_after_reuse(
    final: Path,
    *,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, ...]:
    """Housekeeping after a successful reuse decision (REV-bb58e13-02).

    A crashed earlier attempt can leave ``{final}{RESULT_TMP_SUFFIX}`` sitting
    beside a still-valid final CSV.  Remove exactly that documented temporary
    companion -- never arbitrary siblings, and never the reused result or its
    sidecars.  Quarantine is preferred (keeps the half-written bytes for
    inspection); when quarantine cannot proceed (filesystem error, or a
    refused system-temporary root) fall back to an in-place unlink so this
    best-effort cleanup can never spoil an otherwise successful reuse.
    Returns the quarantine destinations of every removed file.
    """
    tmp = make_tmp_path(final)
    if not _lexists(tmp):
        return ()
    try:
        return quarantine_existing_artifacts([tmp], quarantine_root=quarantine_root)
    except (OSError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # keep the validated output authoritative; nothing else to do
        return ()


def prepare_result_path(
    result_path: str | os.PathLike[str],
    *,
    reuse_ok: bool = False,
    expected_fingerprint: str | None = None,
    expected_manifest: Mapping[str, Any] | None = None,
    quarantine_root: str | os.PathLike[str] | None = None,
    required_columns: Sequence[str] = DEFAULT_REQUIRED_COLUMNS,
    duplicate_column_allowlist: Sequence[str] = (),
    time_column: str = "time",
    requested_stop_time: float | None = None,
    stop_time_slack_s: float = 0.0,
    mtime_tolerance_s: float = DEFAULT_MTIME_TOLERANCE_S,
    min_samples: int | None = DEFAULT_MIN_SAMPLES,
) -> PreparedResult:
    """Resolve one result-file slot to *reuse* or *fresh* before launching.

    The existing CSV is reused only when **all** of the following hold:

    - ``reuse_ok`` is true;
    - both the result file and a readable manifest sidecar exist;
    - the sidecar's recorded fingerprint equals its own recomputed manifest
      digest (self-consistency) *and* equals the expected request fingerprint;
    - :func:`validate_result_csv` accepts the stored CSV using the criteria
      supplied here (the launch timestamp comes from the stored manifest).

    Stop-time coverage is enforced whenever either the caller
    (``requested_stop_time``) or the stored manifest supplies a stop value.
    Any other outcome moves prior artifacts -- the result CSV, its leftover
    ``*.tmp``, and stale sidecars -- under the quarantine area (default
    repository-relative ``00runs/tmp/quarantine/<utc-stamp>/``; override via
    ``quarantine_root``) and reports :data:`ACTION_FRESH`, so callers may
    regenerate the output safely through the atomic-publish protocol:

        tmp = make_tmp_path(csv)   # simulate writing here
        atomic_publish(tmp, csv)
        write_result_sidecars(csv, manifest, report)

    When the outcome *is* a successful reuse, any leftover temporary
    companion produced by a crashed earlier attempt -- exactly
    ``make_tmp_path(final)``, never other files -- is removed as well:
    quarantined like stale artifacts when possible, unlinked in place
    otherwise.  The validated CSV and both sidecars always stay untouched;
    removal destinations surface in :attr:`PreparedResult.quarantined`.

    Pass either ``expected_fingerprint`` (hex digest from
    :func:`manifest_fingerprint`) or ``expected_manifest`` (its fingerprint
    is derived).  Supplying both inconsistently raises ``ValueError``.
    When ``expected_manifest`` is available during a fingerprint mismatch,
    the exact differing fields are reported in
    :attr:`PreparedResult.mismatch_fields` -- volatile keys excluded -- ready
    for workflow logging.

    Raises ``ValueError`` when ``reuse_ok=True`` arrives with no expected
    fingerprint at all: unfingerprinted reuse defeats provenance protection.
    """
    if expected_manifest is not None:
        derived = manifest_fingerprint(expected_manifest)
        if (
            expected_fingerprint is not None
            and expected_fingerprint.strip().lower() != derived
        ):
            raise ValueError(
                "expected_fingerprint does not match expected_manifest; "
                "derive one from the other to avoid contradictory reuse requests"
            )
        expected_fingerprint = derived
    elif expected_fingerprint is not None:
        expected_fingerprint = expected_fingerprint.strip().lower()

    if reuse_ok and expected_fingerprint is None:
        raise ValueError(
            "reuse_ok=True requires either expected_fingerprint or "
            "expected_manifest; refusing unfingerprinted reuse"
        )

    final = Path(result_path)
    artifacts = _result_artifact_paths(final)

    if not reuse_ok:
        return _fresh_outcome(
            "Reuse was not requested.",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=None,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    if not _lexists(final):
        return _fresh_outcome(
            "No existing result file to reuse.",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=None,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    payload = read_manifest_sidecar(final)
    if payload is None:
        return _fresh_outcome(
            "No readable manifest sidecar accompanies the existing result.",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=None,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    stored_manifest = payload.get("manifest")
    if not isinstance(stored_manifest, dict):
        return _fresh_outcome(
            "Manifest sidecar lacks a usable 'manifest' section.",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=None,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    recorded_fp = str(payload.get("fingerprint") or "").strip().lower()
    recomputed_fp = manifest_fingerprint(stored_manifest)
    if recorded_fp != recomputed_fp:
        return _fresh_outcome(
            "Recorded sidecar fingerprint does not match its own manifest "
            "(sidecar edited or corrupted).",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=recomputed_fp,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    # Card A16 (plan §6.8, §8.4): a result directory is not reusable
    # across radial / circulation / flow-distribution / heat-exchanger
    # fingerprints. Checked BEFORE the general fingerprint comparison so
    # the refusal names the radial cause; the ordinary fingerprint check
    # remains the authority for every other field.
    radial_conflict = radial_reuse_conflict(expected_manifest, stored_manifest)
    if radial_conflict is not None:
        mismatches: tuple[ManifestDifference, ...] = ()
        if expected_manifest is not None:
            mismatches = diff_manifests(expected_manifest, stored_manifest)
        return _fresh_outcome(
            f"Existing result cannot be reused: {radial_conflict}. "
            "Differing fields:\n"
            + format_manifest_differences(mismatches),
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=recomputed_fp,
            mismatches=mismatches,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    if recomputed_fp != expected_fingerprint:
        mismatches: tuple[ManifestDifference, ...] = ()
        if expected_manifest is not None:
            mismatches = diff_manifests(expected_manifest, stored_manifest)
        summary = format_manifest_differences(mismatches)
        return _fresh_outcome(
            "Existing result belongs to a different request "
            f"(recorded {recomputed_fp[:12]}... != expected "
            f"{str(expected_fingerprint)[:12]}...). Differing fields:\n{summary}",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=recomputed_fp,
            mismatches=mismatches,
            artifacts=artifacts,
            quarantine_root=quarantine_root,
        )

    effective_stop = requested_stop_time
    if effective_stop is None:
        effective_stop = _optional_float(
            stored_manifest.get("stop_time"), "stored manifest stop_time"
        )

    report = validate_result_csv(
        final,
        required_columns=required_columns,
        duplicate_column_allowlist=duplicate_column_allowlist,
        time_column=time_column,
        launch_timestamp=stored_manifest.get("launch_timestamp_utc"),
        mtime_tolerance_s=mtime_tolerance_s,
        requested_stop_time=effective_stop,
        stop_time_slack_s=stop_time_slack_s,
        min_samples=min_samples,
    )
    if not report.passed:
        failures = ", ".join(report.failed_checks) or "unknown"
        return _fresh_outcome(
            f"Fingerprint matched but validation failed ({failures}).",
            fingerprint_expected=expected_fingerprint,
            fingerprint_recorded=recomputed_fp,
            artifacts=artifacts,
            validation=report,
            quarantine_root=quarantine_root,
        )

    try:
        write_validation_sidecar(final, report)
    except OSError:
        pass  # approval refresh must never break the reuse decision itself

    removed_tmps = _clear_leftover_tmp_after_reuse(
        final, quarantine_root=quarantine_root
    )
    cleanup_note = (
        f" Removed {len(removed_tmps)} leftover .tmp file(s)."
        if removed_tmps
        else ""
    )
    return PreparedResult(
        action=ACTION_REUSE,
        reason=(
            "Existing result matches the request fingerprint and passed "
            f"validation ({report.n_data_rows} data rows).{cleanup_note}"
        ),
        fingerprint_expected=expected_fingerprint,
        fingerprint_recorded=recomputed_fp,
        quarantined=removed_tmps,
        validation_report=report,
    )