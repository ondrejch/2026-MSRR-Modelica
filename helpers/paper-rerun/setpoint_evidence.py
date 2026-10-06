#!/usr/bin/env python3
"""Compact, committable evidence for a promoted setpoint table.

rev033 review (Medium): the promoted ``core/init`` tables carried their
sidecars and completion records, but not the measurements behind each
``qualified=1`` verdict. The raw per-step result CSVs are hundreds of MB and
stay on the cluster. This tool condenses the small per-row evidence files the
campaign keeps (``*_res.convergence.json``, ``*_res.validation.json``,
``*_res.manifest.json``) into one JSON per core. For every promoted row it
records:

- the row's origin (first pass or continuation) and its campaign-relative
  directory;
- the result CSV SHA-256 and row count (validation sidecar) and the
  convergence report's SHA-256;
- the late-window tail (start, duration, samples) and the qualification
  profile and tolerances;
- per check family, the worst gated margin (measured value over its
  effective limit), for the window-mean difference, the projected trend
  drift and the detrended residual, naming the signal;
- the OpenModelica and Python versions from the result manifest.

The record also carries the table's and sidecar's SHA-256 and the model
version. All paths are relative to the campaign ``setpoints/`` directory; no
host-specific path is written.

Usage::

    python3.12 helpers/paper-rerun/setpoint_evidence.py \\
        --setpoints_dir 00runs/paper-rerun-<id>/setpoints --core_model 1r \\
        --output core/init/setpoints_1r_evidence.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers import setpoint_model_version as smv  # noqa: E402

SCHEMA = 1
REL_TOL = 1e-9
#: Metric -> (measured field, effective-limit field) in a convergence check.
MARGIN_METRICS = {
    "window_mean_difference": ("window_mean_difference", "window_mean_difference_effective_limit"),
    "projected_trend_drift": ("projected_trend_drift", "projected_trend_drift_effective_limit"),
    "detrended_residual": ("detrended_residual_p99_minus_p01", "detrended_residual_effective_limit"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tag_power(directory: Path) -> float:
    """``power_0p0012589...`` -> 0.0012589..."""
    return float(directory.name.split("power_", 1)[1].replace("p", "."))


def _step_dirs(root: Path) -> dict[float, Path]:
    return {_tag_power(d): d for d in sorted(root.glob("power_*")) if d.is_dir()}


def _find(dirs: dict[float, Path], power: float) -> Path | None:
    hits = [d for p, d in dirs.items() if math.isclose(p, power, rel_tol=REL_TOL, abs_tol=0.0)]
    return hits[0] if len(hits) == 1 else None


def _one(directory: Path, pattern: str) -> Path:
    hits = sorted(directory.glob(pattern))
    if len(hits) != 1:
        raise RuntimeError(f"{directory}: expected exactly one {pattern}, found {len(hits)}")
    return hits[0]


def worst_margins(report: dict) -> dict[str, dict[str, Any]]:
    """Worst gated measured/limit ratio per metric family."""
    out: dict[str, dict[str, Any]] = {}
    for check in report.get("checks") or []:
        detail = check.get("detail") or {}
        if not detail.get("gated", True):
            continue
        for metric, (value_key, limit_key) in MARGIN_METRICS.items():
            value, limit = detail.get(value_key), detail.get(limit_key)
            if not isinstance(value, (int, float)) or not isinstance(limit, (int, float)) or limit <= 0:
                continue
            ratio = abs(float(value)) / float(limit)
            if metric not in out or ratio > out[metric]["ratio_to_limit"]:
                out[metric] = {
                    "ratio_to_limit": ratio,
                    "signal": check.get("column"),
                    "value": float(value),
                    "effective_limit": float(limit),
                    "family": detail.get("family"),
                }
    return out


def row_evidence(step_dir: Path, setpoints_dir: Path, origin: str) -> dict[str, Any]:
    convergence = _one(step_dir, "*_res.convergence.json")
    validation = _one(step_dir, "*_res.validation.json")
    manifest_path = _one(step_dir, "*_res.manifest.json")
    report = json.loads(convergence.read_text(encoding="utf-8"))
    valid = json.loads(validation.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = manifest.get("manifest", manifest)
    tail = report.get("tail") or {}
    return {
        "origin": origin,
        "step_dir": step_dir.relative_to(setpoints_dir).as_posix(),
        "result_csv": Path(str(valid.get("result_file") or report.get("result_csv") or "")).name,
        "result_sha256": valid.get("result_sha256"),
        "result_rows": valid.get("n_data_rows"),
        "validation_passed": bool(valid.get("passed")),
        "convergence_report_sha256": _sha256(convergence),
        "convergence_passed": bool(report.get("passed")),
        "qualification_profile": report.get("qualification_profile"),
        "tail": {k: tail.get(k) for k in ("start_index", "duration_s", "samples", "minimum_duration_s")},
        "worst_margins": worst_margins(report),
        "omc_version": manifest.get("omc_version"),
        "python_version": manifest.get("python_version"),
    }


def build_evidence(setpoints_dir: Path, core_model: str) -> dict[str, Any]:
    setpoints_dir = Path(setpoints_dir)
    table = setpoints_dir / f"setpoints_{core_model}.csv"
    sidecar = smv.read_sidecar(table)
    if sidecar is None:
        raise RuntimeError(f"{table}: no model-version sidecar")
    completion_path = setpoints_dir / f"setpoints_{core_model}_completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8")) if completion_path.is_file() else {}
    targets = [float(p) for p in completion.get("continuation_targets") or []]
    first = _step_dirs(setpoints_dir / f"work_{core_model}")
    cont = _step_dirs(setpoints_dir / f"continuation_{core_model}")
    with table.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    records = []
    for row in rows:
        power = float(row["power"])
        is_cont = any(math.isclose(power, t, rel_tol=REL_TOL, abs_tol=0.0) for t in targets)
        step = _find(cont if is_cont else first, power)
        if step is None:
            raise RuntimeError(f"no {'continuation' if is_cont else 'first-pass'} step directory for power {power:g}")
        entry = {"power": power, "qualified": row.get("qualified")}
        entry.update(row_evidence(step, setpoints_dir, "continuation" if is_cont else "first_pass"))
        if entry["qualified"] != "1" or not (entry["convergence_passed"] and entry["validation_passed"]):
            raise RuntimeError(f"power {power:g}: row or its evidence is not passing: {entry}")
        records.append(entry)
    worst = {}
    for entry in records:
        for metric, margin in entry["worst_margins"].items():
            if metric not in worst or margin["ratio_to_limit"] > worst[metric]["ratio_to_limit"]:
                worst[metric] = dict(margin, power=entry["power"])
    return {
        "schema": SCHEMA,
        "kind": "setpoint-qualification-evidence",
        "core_model": core_model,
        "table": table.name,
        "table_sha256": smv.table_sha256(table),
        "sidecar_sha256": _sha256(smv.sidecar_path(table)),
        "model_version": sidecar.get("model_version"),
        "model_sources_sha256": sidecar.get("model_sources_sha256"),
        "generation": sidecar.get("generation"),
        "continuation_targets": targets,
        "completion_record_sha256": _sha256(completion_path) if completion_path.is_file() else None,
        "worst_margin_over_table": worst,
        "rows": records,
        "note": (
            "Paths are relative to the campaign setpoints/ directory. The raw "
            "per-step result CSVs stay on the cluster; result_sha256 identifies "
            "each. worst_margins: measured value / effective limit of each gated "
            "convergence check (1.0 = at the limit)."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--setpoints_dir", type=Path, required=True)
    parser.add_argument("--core_model", choices=("1r", "9r"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = build_evidence(args.setpoints_dir, args.core_model)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"setpoint_evidence: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({m: {"ratio": round(v["ratio_to_limit"], 4), "power": v["power"], "signal": v["signal"]}
                      for m, v in evidence["worst_margin_over_table"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
