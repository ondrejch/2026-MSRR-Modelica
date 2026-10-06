"""Model-version binding of steady-state setpoint tables.

A setpoint table stores steady states of one specific lumped model. The
physics review of 2026-09-27 changed that model (per-node HX UA, physical
source normalization, frozen circulating-fuel compensation, t = 0 Stepper,
9R transit times, ...). The tables promoted into ``core/init/`` still had
``qualified=1`` rows computed with the former model, so the per-row verdict
alone could not tell a stale table from a current one (rev031 external
review, High finding). This module binds each table to the model state it
was generated from:

- ``core/init/model_version.json`` names the CURRENT lumped-model version.
  It holds an owner-chosen identifier and the SHA-256 digests (LF-normalized,
  emitter revision stamp ignored) of the three lumped sources
  ``SMD_MSR_Modelica.mo``, ``MSRR.mo`` and ``generated/MSRR_PlantData.mo``,
  relative to the ``core`` package directory.
  ``tests/test_setpoint_model_version.py`` pins those digests to the working
  tree. Any change to the three files therefore fails that test (and every
  strict load, see below) until someone decides, with this module's CLI:

  - ``record --version NAME --note TEXT`` when the change moves steady
    states (bump the version; the tables must then be regenerated), or
  - ``record [--note TEXT]`` alone when it does not: the new digest set is
    appended to the version's ``accepted_sources``, so tables generated from
    an earlier set of the same version stay bound (the live checkout must
    match the latest set).

- ``<table stem>.model_version.json`` is the sidecar the generators write
  next to every table (``core.init.generateSetpointTable.write_table``). It
  records the table's own name and SHA-256, the digests of the sources the
  generator actually compiled, the model version they correspond to
  (``null`` when they match no recorded version), and the generation
  settings (generator digest, qualification profile, OpenModelica version,
  numerics).

- The loaders (``freq._common.load_steady_state_overrides`` and
  ``transients.run_nonlinear_steps.load_setpoints``) call
  :func:`check_table_model_version`. Under the ``strict`` setpoint policy
  (the default) the load is refused unless the sidecar exists and is
  readable, names this CSV, matches the CSV's bytes, names the current
  version, records exactly the version file's source digests, and the live
  lumped sources in this checkout still match those digests (rev032 review:
  a version label alone is not a binding). Under ``legacy-compatible`` the
  same problem is written to stderr and the load proceeds. Any other policy
  string is refused.

CLI::

    python3.12 -m helpers.setpoint_model_version status [--table T.csv ...]
    python3.12 -m helpers.setpoint_model_version verify --table T.csv [--powers P1,P2,...]
    python3.12 -m helpers.setpoint_model_version record [--version NAME --note TEXT]
    python3.12 -m helpers.setpoint_model_version stamp --table T.csv --version NAME --note TEXT
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = 1

#: Lumped-model sources a setpoint table depends on, relative to the ``core``
#: package directory (the same layout in a source checkout and in the wheel).
MODEL_SOURCES: tuple[str, ...] = (
    "SMD_MSR_Modelica.mo",
    "MSRR.mo",
    "generated/MSRR_PlantData.mo",
)

SIDECAR_SUFFIX = ".model_version.json"

POLICY_STRICT = "strict"
POLICY_LEGACY_COMPATIBLE = "legacy-compatible"
#: The setpoint policies this module accepts (the same two modes as
#: ``helpers.setpoint_provenance``); anything else is refused, never treated
#: as permissive.
KNOWN_POLICIES = (POLICY_STRICT, POLICY_LEGACY_COMPATIBLE)

REL_POWER_TOL = 1e-9


def core_dir() -> Path:
    """The ``core`` package directory (source checkout or installed wheel)."""
    return Path(__file__).resolve().parents[1] / "core"


def version_file_path() -> Path:
    return core_dir() / "init" / "model_version.json"


def sidecar_path(table: "str | Path") -> Path:
    """``setpoints_1r.csv`` -> ``setpoints_1r.model_version.json``."""
    path = Path(table)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


#: The generated PlantData packages open with an informational emitter
#: revision stamp that ``helpers.emit_modelica_plant --check`` also ignores;
#: re-emitting at a new commit must not count as a model change.
_EMITTER_STAMP_PREFIX = b"// Emitter git revision:"


def file_sha256(path: "str | Path") -> str:
    """SHA-256 of a model source (CRLF normalized to LF, emitter stamp line dropped)."""
    lines = Path(path).read_bytes().replace(b"\r\n", b"\n").split(b"\n")
    kept = [line for line in lines if not line.startswith(_EMITTER_STAMP_PREFIX)]
    return hashlib.sha256(b"\n".join(kept)).hexdigest()


def table_sha256(path: "str | Path") -> str:
    """SHA-256 of a table's exact bytes (no normalization)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def _cached_source_sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _DIGEST_CACHE:
        _DIGEST_CACHE[key] = file_sha256(path)
    return _DIGEST_CACHE[key]


def source_digests(sources: "Mapping[str, str | Path] | None" = None) -> "dict[str, str | None]":
    """Digests keyed by the :data:`MODEL_SOURCES` names.

    *sources* maps each name to the file actually used (a generator may
    compile a model or library outside ``core/``); omitted names resolve
    under :func:`core_dir`. A source that does not exist digests to
    ``None``, so it can never match the version file.
    """
    sources = dict(sources or {})
    out: "dict[str, str | None]" = {}
    for name in MODEL_SOURCES:
        path = Path(sources.get(name, core_dir() / name))
        out[name] = _cached_source_sha256(path) if path.is_file() else None
    return out


def load_version_file(path: "Path | None" = None) -> dict:
    path = path or version_file_path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or not isinstance(data.get("model_version"), str):
        raise ValueError(f"{path}: not a schema-{SCHEMA} model-version file")
    return data


def current_model_version() -> str:
    return str(load_version_file()["model_version"])


def accepted_source_sets(data: "Mapping[str, Any] | None" = None) -> list[dict]:
    """Source-digest sets accepted for the CURRENT version.

    ``record`` without a version bump declares that a source change moves no
    steady state, so tables generated from any earlier digest set of the same
    version stay bound; the list is ``accepted_sources`` (the latest set last).
    A version bump starts a new list. Files without the key accept only
    ``sources``.
    """
    data = data if data is not None else load_version_file()
    sets = [dict(s) for s in data.get("accepted_sources") or [] if isinstance(s, dict)]
    latest = dict(data.get("sources") or {})
    if latest and latest not in sets:
        sets.append(latest)
    return sets


def _canonical(digests: "Mapping[str, Any] | None") -> dict:
    return {name: (digests or {}).get(name) for name in MODEL_SOURCES}


def version_for_digests(digests: Mapping[str, "str | None"]) -> "str | None":
    """The current model version when *digests* are an accepted set of it."""
    data = load_version_file()
    if _canonical(digests) in [_canonical(s) for s in accepted_source_sets(data)]:
        return str(data["model_version"])
    return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def build_sidecar(
    table: "str | Path",
    *,
    sources: "Mapping[str, str | Path] | None" = None,
    generator: str,
    generation: "Mapping[str, Any] | None" = None,
) -> dict:
    """The sidecar payload for *table* as it is on disk now (not written)."""
    digests = source_digests(sources)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "table": Path(table).name,
        "table_sha256": table_sha256(table),
        "model_version": version_for_digests(digests),
        "model_sources_sha256": digests,
        "generator": generator,
        "written_utc": _utc_now(),
    }
    if generation:
        payload["generation"] = dict(generation)
    return payload


def write_sidecar(
    table: "str | Path",
    *,
    sources: "Mapping[str, str | Path] | None" = None,
    generator: str,
    generation: "Mapping[str, Any] | None" = None,
) -> dict:
    """Write the sidecar for a freshly generated *table*; returns it."""
    payload = build_sidecar(table, sources=sources, generator=generator, generation=generation)
    _atomic_write_json(sidecar_path(table), payload)
    return payload


def rebind_sidecar(table: "str | Path", base: Mapping[str, Any], *, derived: Mapping[str, Any]) -> dict:
    """Sidecar for a table derived from *base*'s table (e.g. a merge).

    Keeps *base*'s model identity and generation settings, rebinds the table
    name and digest to *table* as it is on disk now, and records *derived*
    (what the table was derived from). Returns the payload (not written).
    """
    payload = {k: v for k, v in base.items() if k not in ("table", "table_sha256", "written_utc")}
    payload["table"] = Path(table).name
    payload["table_sha256"] = table_sha256(table)
    payload["derived"] = dict(derived)
    payload["written_utc"] = _utc_now()
    return payload


def read_sidecar(table: "str | Path") -> "dict | None":
    path = sidecar_path(table)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a schema-{SCHEMA} setpoint model-version sidecar")
    return data


def table_model_version(table: "str | Path") -> "str | None":
    """The version label a table's sidecar names (``None``: absent or unversioned).

    A label only; :func:`binding_problem` decides whether the binding holds.
    """
    try:
        data = read_sidecar(table)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if data is None:
        return None
    version = data.get("model_version")
    return str(version) if isinstance(version, str) and version else None


def _live_source_drift() -> list[str]:
    """Lumped sources in this checkout that differ from the version file."""
    recorded = load_version_file().get("sources") or {}
    live = source_digests()
    return [name for name in MODEL_SOURCES if live[name] is not None and live[name] != recorded.get(name)]


def binding_problem(table: "str | Path") -> "tuple[str | None, str | None]":
    """``(problem, found_version)`` for *table*; ``problem`` is ``None`` when bound."""
    table = Path(table)
    try:
        data = read_sidecar(table)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"its model-version sidecar is unreadable ({exc})", None
    if data is None:
        return f"it has no model-version sidecar ({sidecar_path(table).name})", None
    version_file = load_version_file()
    current = str(version_file["model_version"])
    found = data.get("model_version")
    found = str(found) if isinstance(found, str) and found else None
    if data.get("table") != table.name:
        return (
            f"its sidecar belongs to {data.get('table')!r}, not to this CSV "
            "(a sidecar copied or renamed next to another table)"
        ), found
    if found is None:
        return (
            "its sidecar records no model version (the generator compiled "
            "sources that do not match core/init/model_version.json)"
        ), None
    if found != current:
        return f"it was generated for model version {found!r}", found
    accepted = [_canonical(s) for s in accepted_source_sets(version_file)]
    if not isinstance(data.get("model_sources_sha256"), dict) or _canonical(data["model_sources_sha256"]) not in accepted:
        return (
            "its sidecar's source digests differ from every digest set "
            f"core/init/model_version.json accepts for {current!r}"
        ), found
    digest = data.get("table_sha256")
    if not isinstance(digest, str) or not os.path.isfile(table) or table_sha256(table) != digest:
        return "its bytes no longer match the SHA-256 its sidecar records (edited or replaced)", found
    drift = _live_source_drift()
    if drift:
        return (
            f"the lumped model sources in this checkout ({', '.join(drift)}) differ from the "
            f"digests core/init/model_version.json records for {current!r} (re-record them with "
            "`python3.12 -m helpers.setpoint_model_version record`, or bump the version and "
            "regenerate the tables)"
        ), found
    return None, found


def check_table_model_version(table: "str | Path", *, policy: str = POLICY_STRICT) -> dict:
    """Fail closed (strict) unless *table* is bound to the current model.

    Returns ``{"table_model_version", "current_model_version",
    "model_version_match"}`` for run metadata. Under ``strict`` a broken
    binding raises ``ValueError`` naming the problem, both versions and the
    regeneration command; under ``legacy-compatible`` the same text goes to
    stderr as a warning; any other policy string raises ``ValueError``.
    """
    if policy not in KNOWN_POLICIES:
        raise ValueError(
            f"Unknown setpoint policy {policy!r}: expected one of {list(KNOWN_POLICIES)}."
        )
    current = current_model_version()
    problem, found = binding_problem(table)
    record = {
        "table_model_version": found,
        "current_model_version": current,
        "model_version_match": problem is None,
    }
    if problem is None:
        return record
    message = (
        f"Setpoints table {table}: {problem}, but the current lumped model is "
        f"{current!r} (core/init/model_version.json). Its steady states are not "
        "bound to that model, so it cannot initialize a production run. "
        "Regenerate it (python3.12 -m core.init.generateSetpointTable, then "
        "helpers/paper-rerun/complete_setpoint_table.py) or select the "
        "'legacy-compatible' setpoint policy explicitly."
    )
    if policy == POLICY_STRICT:
        raise ValueError(message)
    print(f"WARNING: {message}", file=sys.stderr, flush=True)
    return record


def _finite_power(text: str, where: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: unparseable power {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{where}: power must be finite and positive, got {text!r}")
    return value


def verify_table(table: "str | Path", powers: "Sequence[float] | None" = None) -> dict:
    """Strict campaign check of a finished table; raises ``ValueError``.

    The binding must hold (:func:`binding_problem`); every row power must be
    finite, positive and unique per ``heatLossEnabled``; every row must be
    ``qualified=1``; and every requested power in *powers* must have a row.
    """
    table = Path(table)
    problem, found = binding_problem(table)
    if problem is not None:
        raise ValueError(f"{table}: {problem}")
    with table.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{table}: no rows")
    seen: list[tuple[float, str]] = []
    for index, row in enumerate(rows, start=2):
        power = _finite_power(row.get("power", ""), f"{table}:{index}")
        heat = str(row.get("heatLossEnabled", "0")).strip()
        if any(h == heat and math.isclose(p, power, rel_tol=REL_POWER_TOL, abs_tol=0.0) for p, h in seen):
            raise ValueError(f"{table}:{index}: duplicate power {power:g}")
        seen.append((power, heat))
        if str(row.get("qualified", "")).strip() != "1":
            raise ValueError(f"{table}:{index}: power {power:g} is not qualified=1")
    requested = list(powers or [])
    missing = [
        p for p in requested
        if not any(math.isclose(p, q, rel_tol=REL_POWER_TOL, abs_tol=0.0) for q, _ in seen)
    ]
    if missing:
        raise ValueError(f"{table}: requested powers without a row: {missing}")
    return {"table": str(table), "model_version": found, "rows": len(rows), "requested": requested}


def require_appendable(table: "str | Path", *, sources: "Mapping[str, str | Path] | None" = None) -> None:
    """Refuse ``--append`` into a table generated for another model state."""
    existing = read_sidecar(table)
    digests = source_digests(sources)
    if (
        existing is None
        or existing.get("model_sources_sha256") != digests
        or existing.get("table") != Path(table).name
        or existing.get("table_sha256") != table_sha256(table)
    ):
        raise ValueError(
            f"--append refused: {table} is not bound to the model sources being compiled "
            "now (model-version sidecar missing, for another table, out of date with the "
            "CSV, or with different source digests); merging rows of two model states "
            "would mislabel the table."
        )


def record_version(version: "str | None" = None, note: "str | None" = None) -> dict:
    """Re-record the source digests; with *version*, bump and log it."""
    path = version_file_path()
    data = load_version_file(path) if path.is_file() else {"schema": SCHEMA, "history": []}
    if version is not None:
        if not note:
            raise ValueError("a version bump needs --note describing the model change")
        data["model_version"] = version
        data.setdefault("history", []).append(
            {"model_version": version, "recorded_utc": datetime.date.today().isoformat(), "note": note}
        )
    elif "model_version" not in data:
        raise ValueError("no model version recorded yet; pass --version and --note")
    data["schema"] = SCHEMA
    _DIGEST_CACHE.clear()
    previous = accepted_source_sets(data) if version is None else []
    data["sources"] = source_digests()
    if data["sources"] not in previous:
        previous.append(dict(data["sources"]))
    data["accepted_sources"] = previous
    if version is None and note:
        data.setdefault("history", []).append(
            {"model_version": data["model_version"], "recorded_utc": datetime.date.today().isoformat(),
             "note": f"digests re-recorded (steady states unchanged): {note}"}
        )
    ordered = {
        k: data[k]
        for k in ("schema", "model_version", "doc", "sources", "accepted_sources", "history")
        if k in data
    }
    _atomic_write_json(path, ordered)
    return ordered


def stamp_legacy(table: "str | Path", version: str, note: str) -> dict:
    """Write a sidecar naming the (historical) version of an existing table.

    For tables generated before sidecars existed; the source digests are
    unknown and recorded as ``null``, so such a table can never be bound to
    a current model version.
    """
    payload = {
        "schema": SCHEMA,
        "table": Path(table).name,
        "table_sha256": table_sha256(table),
        "model_version": version,
        "model_sources_sha256": None,
        "generator": "stamped after the fact (helpers.setpoint_model_version stamp)",
        "note": note,
    }
    _atomic_write_json(sidecar_path(table), payload)
    return payload


def _status(tables: Sequence[Path]) -> int:
    data = load_version_file()
    drift = _live_source_drift()
    print(f"current model version: {data['model_version']}")
    print("source digests: " + ("match" if not drift else f"DRIFT in {', '.join(drift)}"))
    rc = 1 if drift else 0
    for table in tables:
        problem, found = binding_problem(table)
        rc |= 0 if problem is None else 1
        print(f"{table}: {found!r} ({'current' if problem is None else 'STALE: ' + problem})")
    return rc


def _parse_powers(text: str) -> list[float]:
    values = [_finite_power(item.strip(), "--powers") for item in text.split(",") if item.strip()]
    for i, a in enumerate(values):
        if any(math.isclose(a, b, rel_tol=REL_POWER_TOL, abs_tol=0.0) for b in values[:i]):
            raise ValueError(f"--powers: duplicate power {a:g}")
    return values


def main(argv: "Sequence[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="Compare the working tree and tables with the version file")
    st.add_argument("--table", type=Path, action="append", default=[])
    ver = sub.add_parser("verify", help="Strict check of a finished table (binding, rows, coverage)")
    ver.add_argument("--table", type=Path, required=True)
    ver.add_argument("--powers", default="", help="Comma-separated powers the table must cover")
    rec = sub.add_parser("record", help="Re-record the source digests (optionally bump the version)")
    rec.add_argument("--version")
    rec.add_argument("--note")
    stp = sub.add_parser("stamp", help="Name the version of a table generated before sidecars existed")
    stp.add_argument("--table", type=Path, required=True)
    stp.add_argument("--version", required=True)
    stp.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "status":
            return _status(args.table)
        if args.cmd == "verify":
            result = verify_table(args.table, _parse_powers(args.powers) if args.powers else None)
            print(json.dumps(result))
            return 0
        if args.cmd == "record":
            data = record_version(args.version, args.note)
            print(f"recorded {data['model_version']} -> {version_file_path()}")
            return 0
        stamp_legacy(args.table, args.version, args.note)
        print(f"stamped {sidecar_path(args.table)}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"setpoint_model_version: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
