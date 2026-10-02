#!/usr/bin/env python3
"""Complete a campaign setpoint table: continuation for the rows the plain
generator could not qualify, then an in-place merge.

The documented setpoint procedure (rerun guide section 4.2) is two-step:
``core.init.generateSetpointTable`` qualifies most powers; low-power points
that fail its late-window checks are regenerated with
``core.init.generateSetpointTableContinuation`` warm-started from the
qualified rows. In the review-2026-09 campaign the second step was a
separate, hand-configured dispatch (``submit_continuation_setpoints.py``,
fixed targets) plus a one-shot merge (``merge_continuation_setpoints.py``).
This helper makes it part of each campaign's setpoint job (physics review
2026-09-27): it derives the targets from the table itself -- every
requested power that is absent or not ``qualified=1`` -- runs the
continuation chain for exactly those, and merges the qualified rows into
the main table.

Publication thresholds are never relaxed (``--accept_unconverged`` is not
passed). The helper fails closed (rev032 review hardening) when:

- a requested or table power is nonfinite, nonpositive or duplicated;
- the first-pass table is not bound to the current model
  (``helpers.setpoint_model_version.binding_problem``: sidecar present,
  naming this CSV, matching its bytes, current version and source digests)
  -- checked on the no-op path too;
- the continuation exits nonzero, its output is not bound to the current
  model, a target is missing or unqualified in it, or the two tables'
  headers or source digests differ;
- stale artifacts of another attempt are present (a ``.first_pass.csv`` or
  a completion record that does not belong to the current table);
- the merged table fails the final strict check
  (``setpoint_model_version.verify_table``: binding, finite unique powers,
  every row ``qualified=1``, every requested power present).

Publication is one recoverable transaction: the merged table is staged next
to the target, the completion record ``<stem>_completion.json`` is written
in state ``committing`` with the SHA-256 of the first-pass table and
sidecar, the continuation table and sidecar, the merged table, and the
merged sidecar payload; then the table and its rebound sidecar are replaced
and the record is finalized. A rerun after a crash recognizes the recorded
state and either finishes the publication or restarts it. The first-pass
table and sidecar are kept as ``<stem>.first_pass.csv`` /
``<stem>.first_pass.model_version.json``. A completed table is idempotent:
rerunning verifies it against its record and returns.

Usage (inside the campaign setpoint job, after the plain generator)::

    python3.12 helpers/paper-rerun/complete_setpoint_table.py \\
        --core_model 1r --powers 1e-5,1e-4,...,1.2 \\
        --table <campaign>/setpoints/setpoints_1r.csv \\
        --work_dir <campaign>/setpoints/continuation_1r
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers import setpoint_model_version as smv  # noqa: E402

REL_TOL = 1e-9

RESULT_NO_CONTINUATION = "complete (no continuation needed)"
RESULT_MERGED = "complete (continuation merged)"
STATE_COMMITTING = "committing"
STATE_COMPLETE = "complete"


def _same_power(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=0.0)


def _finite_power(text: str, where: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: unparseable power {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{where}: power must be finite and positive, got {text!r}")
    return value


def parse_powers(text: str) -> list[float]:
    values = [_finite_power(item.strip(), "--powers") for item in text.split(",") if item.strip()]
    if not values:
        raise ValueError(f"--powers must name at least one power; got {text!r}")
    for i, value in enumerate(values):
        if any(_same_power(value, other) for other in values[:i]):
            raise ValueError(f"--powers: duplicate power {value:g}")
    return values


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def validate_rows(rows: Sequence[dict[str, str]], where: str) -> None:
    """Every row power finite and positive; no duplicate qualified power."""
    qualified: list[float] = []
    for index, row in enumerate(rows, start=2):
        power = _finite_power(row.get("power", ""), f"{where}:{index}")
        if str(row.get("qualified", "")).strip() == "1":
            if any(_same_power(power, other) for other in qualified):
                raise RuntimeError(f"{where}: duplicate qualified row for power {power:g}")
            qualified.append(power)


def qualified_powers(rows: Sequence[dict[str, str]]) -> list[float]:
    return [float(row["power"]) for row in rows if str(row.get("qualified", "")).strip() == "1"]


def missing_powers(requested: Sequence[float], rows: Sequence[dict[str, str]]) -> list[float]:
    have = qualified_powers(rows)
    return [p for p in requested if not any(_same_power(p, q) for q in have)]


def continuation_command(
    python: str,
    core_model: str,
    targets: Sequence[float],
    table: Path,
    work_dir: Path,
    output: Path,
    omc_timeout_seconds: float | None = None,
) -> list[str]:
    cmd = [
        python, "-m", "core.init.generateSetpointTableContinuation",
        "--core_model", core_model,
        "--powers", ",".join(f"{p:.12g}" for p in targets),
        "--init_from", str(table),
        "--qualification_profile", "publication",
        "--work_dir", str(work_dir),
        "--output", str(output),
    ]
    if omc_timeout_seconds is not None:
        cmd += ["--omc_timeout_seconds", f"{omc_timeout_seconds:g}"]
    return cmd


def merge_rows(
    fields_main: list[str],
    rows_main: list[dict[str, str]],
    fields_cont: list[str],
    rows_cont: list[dict[str, str]],
    targets: Sequence[float],
) -> list[dict[str, str]]:
    if fields_main != fields_cont:
        raise RuntimeError(
            "continuation table header differs from the main table header; "
            "refusing to merge"
        )
    merged = [row for row in rows_main if str(row.get("qualified", "")).strip() == "1"]
    for target in targets:
        hits = [row for row in rows_cont if _same_power(float(row["power"]), target)]
        if len(hits) != 1:
            raise RuntimeError(f"continuation output has {len(hits)} rows for power {target:g} (need 1)")
        if str(hits[0].get("qualified", "")).strip() != "1":
            raise RuntimeError(f"continuation row for power {target:g} is not qualified=1")
        if any(_same_power(float(row["power"]), target) for row in merged):
            raise RuntimeError(f"main table already has a qualified row for power {target:g}")
        merged.append(hits[0])
    merged.sort(key=lambda row: float(row["power"]))
    return merged


def _write_rows(path: Path, fields: list[str], rows: Sequence[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _json_sha256(payload: dict) -> str:
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _git_commit(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _portable(value: str) -> str:
    """Repository-relative spelling of paths under the checkout (rev033 review:
    promoted records must not carry host-specific absolute paths)."""
    return str(value).replace(str(REPO_ROOT) + os.sep, "")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _require_bound(table: Path, what: str) -> dict:
    problem, _ = smv.binding_problem(table)
    if problem is not None:
        raise RuntimeError(f"{what} {table} is not bound to the current model: {problem}")
    sidecar = smv.read_sidecar(table)
    assert sidecar is not None
    return sidecar


def _publish(table: Path, staged: Path, record: dict, notes_path: Path, requested: Sequence[float]) -> dict:
    """Replace the table and its sidecar from *record*, verify, finalize."""
    if staged.is_file():
        os.replace(staged, table)
    if _sha256(table) != record["merged_table_sha256"]:
        raise RuntimeError(f"{table} does not hold the merged table the completion record names")
    _write_json(smv.sidecar_path(table), record["merged_sidecar"])
    smv.verify_table(table, requested)
    record["state"] = STATE_COMPLETE
    record["result"] = RESULT_MERGED
    record["finished_utc"] = _now()
    _write_json(notes_path, record)
    return record


def complete(
    *,
    core_model: str,
    requested: Sequence[float],
    table: Path,
    work_dir: Path,
    python: str = "python3.12",
    omc_timeout_seconds: float | None = None,
    runner=subprocess.run,
) -> dict:
    """Run the completion; returns the recorded notes (raises on failure)."""
    requested = parse_powers(",".join(f"{p!r}" for p in requested))
    if not table.is_file():
        raise RuntimeError(f"first-pass setpoint table missing: {table}")
    notes_path = table.with_name(f"{table.stem}_completion.json")
    staged = table.with_name(f".{table.name}.merged.partial")
    first_pass = table.with_name(f"{table.stem}.first_pass.csv")
    record = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.is_file() else None

    # Recovery and idempotence: a record belongs to this table or refuses.
    if record is not None:
        state = record.get("state")
        current = _sha256(table)
        if state == STATE_COMPLETE:
            expected = record.get("merged_table_sha256") or record.get("table_sha256")
            if current != expected:
                raise RuntimeError(
                    f"{table} changed after its completion record {notes_path.name} was written"
                )
            smv.verify_table(table, requested)
            return record
        if state == STATE_COMMITTING:
            if current == record.get("merged_table_sha256") or (
                staged.is_file() and _sha256(staged) == record.get("merged_table_sha256")
            ):
                return _publish(table, staged, record, notes_path, requested)
            if current != record.get("first_pass_table_sha256"):
                raise RuntimeError(
                    f"{table} matches neither the first-pass nor the merged table of the "
                    f"interrupted completion recorded in {notes_path.name}"
                )
            staged.unlink(missing_ok=True)  # restart from the recorded first pass
        else:
            raise RuntimeError(f"{notes_path.name}: unknown completion state {state!r}")
    if first_pass.exists() and (
        record is None or _sha256(first_pass) != record.get("first_pass_table_sha256")
    ):
        raise RuntimeError(
            f"stale {first_pass.name} from another attempt next to {table.name}; move it away"
        )

    main_sidecar = _require_bound(table, "first-pass table")
    fields_main, rows_main = read_table(table)
    validate_rows(rows_main, str(table))
    targets = missing_powers(requested, rows_main)
    notes: dict = {
        "helper": "helpers/paper-rerun/complete_setpoint_table.py",
        "core_model": core_model,
        "table": _portable(str(table)),
        "requested_powers": list(requested),
        "first_pass_qualified_powers": qualified_powers(rows_main),
        "continuation_targets": targets,
        "model_version": main_sidecar.get("model_version"),
        "first_pass_table_sha256": _sha256(table),
        "first_pass_sidecar_sha256": _sha256(smv.sidecar_path(table)),
        "commit": _git_commit(REPO_ROOT),
        "started_utc": _now(),
    }
    if not targets:
        smv.verify_table(table, requested)
        notes.update(state=STATE_COMPLETE, result=RESULT_NO_CONTINUATION,
                     table_sha256=notes["first_pass_table_sha256"], finished_utc=_now())
        _write_json(notes_path, notes)
        return notes

    work_dir.mkdir(parents=True, exist_ok=True)
    cont_output = work_dir / f"{table.stem}_continuation.csv"
    cmd = continuation_command(
        python, core_model, targets, table, work_dir, cont_output, omc_timeout_seconds
    )
    notes["continuation_command"] = _portable(shlex.join(cmd))
    proc = runner(cmd, check=False)
    notes["continuation_exit_code"] = int(proc.returncode)
    if proc.returncode != 0:
        raise RuntimeError(f"continuation exited {proc.returncode}: {notes['continuation_command']}")
    cont_sidecar = _require_bound(cont_output, "continuation output")
    if main_sidecar.get("model_sources_sha256") != cont_sidecar.get("model_sources_sha256"):
        raise RuntimeError(
            "the first pass and the continuation compiled different model "
            "sources (model-version sidecar digests differ); refusing to merge"
        )
    fields_cont, rows_cont = read_table(cont_output)
    validate_rows(rows_cont, str(cont_output))
    merged = merge_rows(fields_main, rows_main, fields_cont, rows_cont, targets)
    still = missing_powers(requested, merged)
    if still:
        raise RuntimeError(f"powers still missing after the merge: {still}")

    _write_rows(staged, fields_main, merged)
    merged_sidecar = smv.rebind_sidecar(
        staged,
        main_sidecar,
        derived={
            "helper": notes["helper"],
            "first_pass_table_sha256": notes["first_pass_table_sha256"],
            "continuation_table": cont_output.name,
            "continuation_table_sha256": _sha256(cont_output),
            "continuation_generation": cont_sidecar.get("generation"),
            "continuation_targets": targets,
        },
    )
    merged_sidecar["table"] = table.name
    notes.update(
        state=STATE_COMMITTING,
        continuation_output=_portable(str(cont_output)),
        continuation_table_sha256=_sha256(cont_output),
        continuation_sidecar_sha256=_sha256(smv.sidecar_path(cont_output)),
        merged_table_sha256=_sha256(staged),
        merged_rows=len(merged),
        merged_sidecar=merged_sidecar,
        merged_sidecar_sha256=_json_sha256(merged_sidecar),
        first_pass_copy=_portable(str(first_pass)),
    )
    if not first_pass.exists():
        first_pass.write_bytes(table.read_bytes())
        smv.sidecar_path(first_pass).write_bytes(smv.sidecar_path(table).read_bytes())
    _write_json(notes_path, notes)
    return _publish(table, staged, notes, notes_path, requested)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--core_model", choices=("1r", "9r"), required=True)
    parser.add_argument("--powers", type=parse_powers, required=True,
                        help="Comma-separated campaign powers [MW] the table must qualify")
    parser.add_argument("--table", type=Path, required=True,
                        help="First-pass campaign table (merged in place)")
    parser.add_argument("--work_dir", type=Path, required=True,
                        help="Continuation work directory")
    parser.add_argument("--python", default=sys.executable or "python3.12")
    parser.add_argument("--omc_timeout_seconds", type=float, default=None,
                        help="Passed through to the continuation generator")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        notes = complete(core_model=args.core_model, requested=args.powers, table=args.table,
                         work_dir=args.work_dir, python=args.python,
                         omc_timeout_seconds=args.omc_timeout_seconds)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"complete_setpoint_table: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: notes.get(k) for k in ("core_model", "continuation_targets", "result")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
