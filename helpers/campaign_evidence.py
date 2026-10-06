#!/usr/bin/env python3.12
"""Compact evidence package of a results campaign (rev035 review phase D).

The papers cite results of campaigns whose raw trees live on the cluster;
what the repository keeps is a pruned copy (sidecars, reductions, figures,
logs). This module condenses one campaign -- one or several result trees
under ``00runs/`` -- into a tracked, self-contained package that lets a
reader of the public repository determine what ran and why each result is
accepted, without the cluster filesystem:

- ``<id>-manifest.json``: machine-readable. Per tree: the campaign
  metadata (commit, toolchain, source digests, normalized to repository-
  relative paths), the campaign ``SHA256SUMS`` statistics where present and
  the digests of every local file. Per run (every ``*_res.manifest.json``):
  family, plant, core, case, vehicle, commit, manifest fingerprint
  (recomputed), stop time, the validation record's verdict and digest, the
  reduction certificate, the setpoint convergence report, the blockage
  summary's status fields and the peak of a peak-convergence run. The
  composition (which commit and vehicle produced which cases of which
  family) is stated explicitly. Frequency verification reports, figures
  (campaign and article copies, matched by digest), the setpoint tables
  with their compact evidence rebuilt by the current builder, named
  records (a drift-validation report, a metadata-correction record) with a
  summary of their verdicts, and the directories set aside from the
  accepted set (superseded or reference copies) with the reason their
  README gives.
- ``<id>.md``: the narrative with the composition, verification, figure,
  setpoint and record tables.
- ``SHA256SUMS.normalized.txt``: the campaign sums of the trees that carry
  one, every path normalized (``<tree>/<campaign-relative path>``).
- ``files.local.sha256``: the digest of every file of the local copies.

The package carries no result payloads and never writes into a result tree.
A re-export of unchanged inputs is byte-identical (no timestamps; the
exporter records the digest of this module and the checkout commit).

    python3.12 -m helpers.campaign_evidence --id psar-2026-10 \\
        --tree 00runs/paper-rerun-psar-2026-10-01 --tree 00runs/paper-rerun-psar-2026-10-02 \\
        --article-figs latex/MSRR_journal_article_v3/figs \\
        --setpoints 00runs/paper-rerun-psar-2026-10-01/setpoints \\
        --record 00runs/drift-validation-2026-09-28/drift_validation_report_strict.json \\
        --disposition rebaseline-evidence/psar-2026-10/drift-validation-2026-09-28-disposition.md \\
        --output rebaseline-evidence/psar-2026-10
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

from helpers import rebaseline_evidence as rb
from helpers import run_results as rr

SCHEMA_VERSION = 1
KIND = "msrr-campaign-evidence"
#: Directory names excluded from the accepted set and listed as set aside.
SET_ASIDE_PATTERNS = (re.compile(r".*\.superseded-[0-9a-f]+$"), re.compile(r"^reference-.*"), re.compile(r"^lumped-.*"))
SKIP_DIRS = ("build", "__pycache__", ".mplconfig")
FAMILY_BY_PREFIX = (("transients", "transients"), ("startup", "startup"), ("freq_time", "frequency_time_example"),
                    ("freq", "frequency"), ("setpoints", "setpoints"), ("blockage", "blockage"),
                    ("peakconv", "peak_convergence"), ("sensitivity", "sensitivity"))
CORE_KEYS = ("1r10seg", "r5x5_z10", "rings_z10", "1r", "9r")
SWEEP_MARKERS = ("sweep_request.manifest.json", "FreqResponseResults.manifest.json")


class CampaignEvidenceError(ValueError):
    """The campaign trees do not support an evidence package."""


def _sha256(path: Path) -> str:
    return rr.file_sha256(path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        raise CampaignEvidenceError(f"{path} lies outside the repository; the package records repository-relative paths only")


def _is_set_aside(name: str) -> bool:
    return any(p.fullmatch(name) for p in SET_ASIDE_PATTERNS)


def _walk(tree: Path):
    """Every file of ``tree`` (sorted), skipping build scratch."""

    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


# --------------------------------------------------------------------------
# Trees
# --------------------------------------------------------------------------
def _parse_metadata_txt(path: Path, tree_name: str, repo_name: str) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if path.name == "source_sha256.txt":
        out = []
        for ln in lines:
            parts = ln.split(None, 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                out.append({"path": rb._campaign_relative(parts[1], tree_name, repo_name), "sha256": parts[0]})
        return out
    if lines and all(re.match(r"^[A-Za-z_]+: ", ln) for ln in lines):
        record = {}
        for ln in lines:
            key, value = ln.split(": ", 1)
            record[key] = re.sub(r"/home/[^/\s]+/", "/home/<user>/", value.strip())
        return record
    return "\n".join(lines).strip() if len(lines) == 1 else lines


def tree_section(tree: Path, repo_name: str) -> dict[str, Any]:
    if not tree.is_dir():
        raise CampaignEvidenceError(f"campaign tree {tree} is not a directory")
    tree_rel = _rel(tree)  # inside the repository, before anything is read
    metadata: dict[str, Any] = {}
    meta_dir = tree / "metadata"
    if meta_dir.is_dir():
        for path in sorted(meta_dir.glob("*.txt")):
            metadata[path.stem] = _parse_metadata_txt(path, tree.name, repo_name)
        for path in sorted(meta_dir.glob("*.json")):
            metadata[path.name] = {"sha256": _sha256(path), "kind": (_read_json(path) or {}).get("kind")}
    commits = set()
    for key, value in metadata.items():
        if key == "git_commit" and isinstance(value, str):
            commits.add(value)
        if isinstance(value, dict) and isinstance(value.get("git_commit"), str):
            commits.add(value["git_commit"])
    sums = None
    if (tree / "SHA256SUMS").is_file():
        hashes, stats = rb._load_sha256sums(tree, repo_name)
        sums = {"stats": stats, "entries": hashes}
    local = {p.relative_to(tree).as_posix(): {"sha256": _sha256(p), "bytes": p.stat().st_size}
             for p in _walk(tree) if p.is_file()}
    summary = _read_json(tree / "campaign_summary.json")
    return {
        "name": tree.name,
        "path": tree_rel,
        "metadata": metadata,
        "toolchain": _toolchain(metadata),
        "git_commits": sorted(commits),
        "campaign_summary": summary,
        "sha256sums": {"present": sums is not None, **(sums["stats"] if sums else {})},
        "_sums_entries": sums["entries"] if sums else {},
        "local_files": {"count": len(local), "bytes": sum(v["bytes"] for v in local.values())},
        "_local": local,
    }


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def _family(parts: tuple[str, ...]) -> str:
    for part in parts:
        for prefix, family in FAMILY_BY_PREFIX:
            if part.startswith(prefix):
                return family
    return "other"


def _plant(parts: tuple[str, ...]) -> str:
    return parts[0] if parts and parts[0] in ("msrr", "msrr_1gw") else "msrr"


def _core_from_path(parts: tuple[str, ...]) -> str | None:
    for part in parts:
        if part in CORE_KEYS:
            return part
        for prefix in ("blockage-", "transients-", "startup-", "peakconv-"):
            if part.startswith(prefix) and part[len(prefix):] in CORE_KEYS:
                return part[len(prefix):]
    return None


def _toolchain(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """OpenModelica and Python versions named by the tree's metadata (single
    values or per-job records)."""

    omc, py = set(), set()
    for key, value in metadata.items():
        if key == "openmodelica_version" and isinstance(value, str):
            omc.add(value)
        if key == "python_version" and isinstance(value, str):
            py.add(value)
        if isinstance(value, dict):
            if isinstance(value.get("omc_version"), str):
                omc.add(value["omc_version"])
            if isinstance(value.get("python"), str):
                py.add(value["python"])
    return {"openmodelica": sorted(omc), "python": sorted(py)}


def run_record(manifest_path: Path, tree: Path) -> dict[str, Any]:
    rel_parts = manifest_path.relative_to(tree).parts
    base = manifest_path.name[: -len(".manifest.json")]
    case_stem = base[:-4] if base.endswith("_res") else base
    where = manifest_path.parent
    payload = _read_json(manifest_path) or {}
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    try:
        recomputed = rr.manifest_fingerprint(manifest) if manifest else None
    except (TypeError, ValueError):
        recomputed = None
    overrides = manifest.get("overrides") if isinstance(manifest.get("overrides"), dict) else {}
    validation = _read_json(where / f"{base}.validation.json")
    certificate = _read_json(where / f"{case_stem}_reduction.json")
    convergence = _read_json(where / f"{base}.convergence.json")
    summary = _read_json(where / f"{case_stem}_summary.json")
    extremes = _read_json(where / f"{case_stem}_extremes.json")
    record: dict[str, Any] = {
        "manifest": manifest_path.relative_to(tree).as_posix(),
        "family": _family(rel_parts),
        "plant": _plant(rel_parts),
        "core_model": overrides.get("core_model") or manifest.get("core_model") or _core_from_path(rel_parts),
        "case": overrides.get("case") or case_stem,
        "model_name": manifest.get("model_name"),
        "git_commit": manifest.get("git_commit"),
        "git_dirty": manifest.get("git_dirty"),
        "omc_version": manifest.get("omc_version"),
        "stop_time_s": manifest.get("stop_time"),
        "fingerprint": payload.get("fingerprint"),
        "fingerprint_ok": recomputed is not None and payload.get("fingerprint") == recomputed,
        "hashed_plant_deck": {k.split("/")[-1]: v for k, v in (manifest.get("source_files") or {}).items()
                              if str(k).startswith("data/plants/")},
        "validation": None if validation is None else {
            "passed": validation.get("passed"), "failed_checks": validation.get("failed_checks"),
            "result_sha256": validation.get("result_sha256"), "n_data_rows": validation.get("n_data_rows")},
        "reduction_certificate": None if certificate is None else {
            "raw_sha256": certificate.get("raw_sha256"),
            "matches_validation": bool(validation) and certificate.get("raw_sha256") == validation.get("result_sha256"),
            "manifest_fingerprint_matches": certificate.get("manifest_fingerprint") == payload.get("fingerprint"),
            "extremes_sha256": certificate.get("extremes_sha256")},
        "sweep_request": manifest.get("sweep_request"),
    }
    if convergence is not None:
        record["convergence"] = {"passed": convergence.get("passed"), "kind": convergence.get("kind"),
                                 "result_sha256": convergence.get("result_sha256")}
    if summary is not None and "physical_domain" in summary:
        record["blockage_summary"] = {
            k: summary.get(k) for k in ("execution_valid", "event_verified", "numerical_checks_passed")}
        record["blockage_summary"]["physical_domain"] = (summary.get("physical_domain") or {}).get("status")
        record["blockage_summary"]["cell_salt_max_c"] = ((summary.get("after_onset") or {}).get("cell_salt_max") or {}).get("value_c")
    if record["family"] in ("peak_convergence", "transients") and extremes and "nOut" in (extremes.get("columns") or {}):
        record["peak"] = {"nOut_max": extremes["columns"]["nOut"]["max"],
                          "t_peak_s": extremes["columns"]["nOut"]["t_max"],
                          "raw_rows": extremes.get("raw_rows")}
    return record


def _sweep_dir(manifest_path: Path, tree: Path) -> Path | None:
    """The sweep directory (holding the request or collection manifest) a
    frequency case belongs to, up to three levels above the case."""

    here = manifest_path.parent
    for _ in range(4):
        if here == tree or here == here.parent:
            return None
        if any((here / m).is_file() for m in SWEEP_MARKERS):
            return here
        here = here.parent
    return None


def summarize_sweeps(runs: list[dict[str, Any]], tree: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fold the per-point frequency runs into one record per sweep directory
    (the request fingerprint, the collection manifest's status and counts,
    the advertised outputs) and return (sweeps, remaining runs)."""

    sweeps: dict[str, dict[str, Any]] = {}
    remaining = []
    for run in runs:
        if run["family"] != "frequency":
            remaining.append(run)
            continue
        sweep_dir = _sweep_dir(tree / run["manifest"], tree)
        if sweep_dir is None:
            remaining.append(run)
            continue
        key = sweep_dir.relative_to(tree).as_posix()
        if key not in sweeps:
            request = _read_json(sweep_dir / "sweep_request.manifest.json") or {}
            collection = _read_json(sweep_dir / "FreqResponseResults.manifest.json") or {}
            req = request.get("request") if isinstance(request.get("request"), dict) else {}
            sweeps[key] = {
                "sweep": key, "family": "frequency", "plant": run["plant"], "core_model": run["core_model"],
                "model_name": run["model_name"], "git_commits": [], "points": 0, "validation_passed": 0,
                "fingerprint_ok": 0,
                "request": {"campaign_id": request.get("campaign_id"), "fingerprint": request.get("fingerprint"),
                            "cases": len(req.get("cases") or []), "power": req.get("power"),
                            "plant_normalization": req.get("plant_normalization")} if request else None,
                "collection": {"generation_id": collection.get("generation_id"),
                               "collection_status": collection.get("collection_status"),
                               "counts": collection.get("counts"),
                               "plant_normalization": collection.get("plant_normalization"),
                               "outputs": collection.get("outputs")} if collection else None,
            }
        sw = sweeps[key]
        sw["points"] += 1
        sw["validation_passed"] += bool((run.get("validation") or {}).get("passed"))
        sw["fingerprint_ok"] += bool(run.get("fingerprint_ok"))
        if run["git_commit"] not in sw["git_commits"]:
            sw["git_commits"].append(run["git_commit"])
    for sw in sweeps.values():
        sw["git_commits"] = sorted(str(c) for c in sw["git_commits"])
    return [sweeps[k] for k in sorted(sweeps)], remaining


def discover_runs(tree: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(accepted-set runs, set-aside directories) of one tree."""

    runs = []
    aside = []
    for dirpath, dirnames, filenames in os.walk(tree):
        here = Path(dirpath)
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS:
                continue
            if _is_set_aside(d):
                readme = here / d / "README.md"
                reason = None
                if readme.is_file():
                    paragraphs = [x.strip().replace("\n", " ") for x in readme.read_text(encoding="utf-8").split("\n\n")]
                    reason = " ".join(x for x in paragraphs[1:] if x) or None
                aside.append({
                    "path": (here / d).relative_to(tree).as_posix(),
                    "files": sum(1 for p in _walk(here / d) if p.is_file()),
                    "reason": reason,
                    "readme_sha256": _sha256(readme) if readme.is_file() else None,
                    "manifests": sum(1 for p in _walk(here / d) if p.name.endswith(".manifest.json")),
                })
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name.endswith("_res.manifest.json") or (name.endswith(".manifest.json") and "setpoints" in here.parts):
                if name == "sweep_request.manifest.json" or name.startswith("FreqResponseResults"):
                    continue
                runs.append(run_record(here / name, tree))
    return runs, aside


def composition(runs: list[dict[str, Any]], tree_name: str) -> list[dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = {}
    for run in runs:
        key = (run["family"], run["plant"], str(run["core_model"]), str(run["model_name"]), str(run["git_commit"]))
        g = groups.setdefault(key, {"tree": tree_name, "family": key[0], "plant": key[1], "core_model": key[2],
                                    "model_name": key[3], "git_commit": key[4], "cases": [], "runs": 0,
                                    "validation_passed": 0, "certified": 0, "fingerprint_ok": 0})
        g["runs"] += 1
        if run["case"] not in g["cases"]:
            g["cases"].append(run["case"])
        g["validation_passed"] += bool((run.get("validation") or {}).get("passed"))
        g["certified"] += bool((run.get("reduction_certificate") or {}).get("matches_validation"))
        g["fingerprint_ok"] += bool(run.get("fingerprint_ok"))
    for g in groups.values():
        g["cases"] = sorted(g["cases"])
    return [groups[k] for k in sorted(groups)]


# --------------------------------------------------------------------------
# Verification reports, figures, setpoints, records
# --------------------------------------------------------------------------
def verify_reports(tree: Path, repo_name: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted((tree / "logs").glob("*_verify.json")) if (tree / "logs").is_dir() else []:
        d = _read_json(path) or {}
        results_dir = d.get("results_dir")
        out.append({
            "report": path.relative_to(tree).as_posix(),
            "sha256": _sha256(path),
            "results_dir": rb._campaign_relative(str(results_dir), tree.name, repo_name) if results_dir else None,
            "accepted": d.get("accepted"), "expected": d.get("expected"),
            "campaign_complete": d.get("campaign_complete"),
            "publication_eligible": d.get("publication_eligible"),
            "publication_approved": d.get("publication_approved"),
            "fit_convergence_status": d.get("fit_convergence_status"),
            "numerical_quality_status": d.get("numerical_quality_status"),
            "swing_check_pass": d.get("swing_check_pass"),
            "core_maturity": d.get("core_maturity"),
        })
    return out


def figures_section(trees: list[Path], article_figs: Path | None) -> dict[str, Any]:
    campaign = []
    by_sha: dict[str, str] = {}
    for tree in trees:
        figs = tree / "figures"
        if not figs.is_dir():
            continue
        for path in _walk(figs):
            if path.suffix.lower() in (".png", ".pdf"):
                sha = _sha256(path)
                rel = f"{tree.name}/{path.relative_to(tree).as_posix()}"
                campaign.append({"figure": rel, "sha256": sha})
                by_sha.setdefault(sha, rel)
    article = []
    if article_figs is not None:
        if not article_figs.is_dir():
            raise CampaignEvidenceError(f"article figure directory {article_figs} is not a directory")
        for path in sorted(article_figs.iterdir()):
            if path.suffix.lower() in (".png", ".pdf"):
                sha = _sha256(path)
                article.append({"figure": path.name, "sha256": sha, "campaign_copy": by_sha.get(sha)})
    scripts = []
    if article_figs is not None and (article_figs.parent / "scripts").is_dir():
        scripts = [{"script": p.name, "sha256": _sha256(p)} for p in sorted((article_figs.parent / "scripts").glob("*.py"))]
    return {"article_dir": _rel(article_figs) if article_figs else None, "campaign": campaign, "article": article,
            "article_matched": sum(1 for a in article if a["campaign_copy"]), "article_total": len(article),
            "article_scripts": scripts}


def _load_setpoint_builder():
    path = repo_root() / "helpers" / "paper-rerun" / "setpoint_evidence.py"
    spec = importlib.util.spec_from_file_location("setpoint_evidence", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def setpoints_section(setpoints_dir: Path) -> dict[str, Any]:
    from helpers import setpoint_model_version as smv

    builder = _load_setpoint_builder()
    init = repo_root() / "core" / "init"
    out: dict[str, Any] = {"setpoints_dir": _rel(setpoints_dir), "cores": {}}
    for core in ("1r", "9r"):
        table = setpoints_dir / f"setpoints_{core}.csv"
        if not table.is_file():
            continue
        entry: dict[str, Any] = {"table_sha256": smv.table_sha256(table)}
        promoted = init / f"setpoints_{core}.csv"
        entry["promoted_table_identical"] = promoted.is_file() and smv.table_sha256(promoted) == entry["table_sha256"]
        sidecar = smv.read_sidecar(table) or {}
        entry["model_version"] = sidecar.get("model_version")
        shipped = init / f"setpoints_{core}_evidence.json"
        if shipped.is_file():
            d = _read_json(shipped) or {}
            entry["promoted_evidence"] = {"file": _rel(shipped), "sha256": _sha256(shipped), "schema": d.get("schema"),
                                          "evidence_basis": d.get("evidence_basis")}
        try:
            rebuilt = builder.build_evidence(setpoints_dir, core)
            entry["rebuilt_evidence"] = {
                "schema": rebuilt.get("schema"), "evidence_basis": rebuilt.get("evidence_basis"),
                "rows": len(rebuilt.get("rows") or []), "raw_digests_verified": rebuilt.get("raw_digests_verified"),
                "model_sources_bound": rebuilt.get("model_sources_bound"),
                "model_sources": (rebuilt.get("rows") or [{}])[0].get("model_sources"),
                "worst_margin_over_table": rebuilt.get("worst_margin_over_table"),
                "sha256": _sha256_bytes(json.dumps(rebuilt, sort_keys=True).encode("utf-8")),
            }
        except Exception as exc:  # the builder names the refused row
            entry["rebuilt_evidence"] = {"error": str(exc)[:500]}
        out["cores"][core] = entry
    return out


def record_section(path: Path) -> dict[str, Any]:
    d = _read_json(path) or {}
    kind = d.get("kind")
    entry: dict[str, Any] = {"file": _rel(path), "sha256": _sha256(path), "kind": kind}
    if kind == "drift-regime-validation-report":
        entry["summary"] = {
            "status": d.get("status"), "pass": d.get("pass"), "counts": d.get("counts"),
            "tolerances": d.get("tolerances"), "created_utc": d.get("created_utc"),
            "matrix_commit": (d.get("matrix") or {}).get("commit") or (d.get("matrix") or {}).get("git_commit"),
            "matrix_campaign_id": (d.get("matrix") or {}).get("campaign_id"),
            "max_drift_operating_point_shift_overall": d.get("max_drift_operating_point_shift_overall"),
            "points": [{"point": p.get("point"), "status": p.get("status"),
                        "drift_vs_full": (p.get("drift_vs_full_same_normalization") or {}).get("status"),
                        "window_doubling": (p.get("window_doubling") or {}).get("status")}
                       for p in d.get("points") or []],
        }
    elif kind == "derived-metadata-correction":
        entry["summary"] = {"finding": d.get("finding"), "corrected_manifests": len(d.get("corrected_manifests") or []),
                            "unproved_manifests": len(d.get("unproved_manifests") or []),
                            "campaign_source_commit": d.get("campaign_source_commit")}
    return entry


# --------------------------------------------------------------------------
# Build and render
# --------------------------------------------------------------------------
def build(campaign_id: str, trees: list[Path], *, article_figs: Path | None = None, setpoints: Path | None = None,
          records: list[Path] = (), disposition: Path | None = None) -> dict[str, Any]:
    root = repo_root()
    repo_name = root.name
    tree_sections = [tree_section(t, repo_name) for t in trees]
    runs_all, aside_all, groups = [], [], []
    sweeps_all = []
    for tree in trees:
        runs, aside = discover_runs(tree)
        sweeps, runs = summarize_sweeps(runs, tree)
        for r in runs:
            r["tree"] = tree.name
        for sw in sweeps:
            sw["tree"] = tree.name
        for a in aside:
            a["tree"] = tree.name
        runs_all += runs
        sweeps_all += sweeps
        aside_all += aside
        groups += composition(runs, tree.name)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": campaign_id,
        "trees": [{k: v for k, v in t.items() if not k.startswith("_")} for t in tree_sections],
        "composition": groups,
        "sweeps": sweeps_all,
        "runs": sorted(runs_all, key=lambda r: (r["tree"], r["manifest"])),
        "run_totals": {
            "runs": len(runs_all),
            "fingerprint_ok": sum(1 for r in runs_all if r["fingerprint_ok"]),
            "validation_passed": sum(1 for r in runs_all if (r.get("validation") or {}).get("passed")),
            "validation_missing": sum(1 for r in runs_all if r.get("validation") is None),
            "validation_missing_by_family": sorted(
                {f"{r['tree']}/{r['family']}" for r in runs_all if r.get("validation") is None}),
            "sweep_points": sum(sw["points"] for sw in sweeps_all),
            "sweep_points_validated": sum(sw["validation_passed"] for sw in sweeps_all),
            "certified": sum(1 for r in runs_all if (r.get("reduction_certificate") or {}).get("matches_validation")),
            "commits": sorted({str(r["git_commit"]) for r in runs_all}),
        },
        "verification": [v for t in trees for v in verify_reports(t, repo_name)],
        "figures": figures_section(trees, article_figs),
        "setpoints": setpoints_section(setpoints) if setpoints else None,
        "records": [record_section(p) for p in records],
        "disposition": None if disposition is None else {
            "file": _rel(disposition), "sha256": _sha256(disposition),
            "text": disposition.read_text(encoding="utf-8").strip()},
        "set_aside": aside_all,
        "exporter": {
            "module": "helpers.campaign_evidence",
            "module_sha256": _sha256(Path(__file__).resolve()),
            "git_commit": rb._run_git_soft(["rev-parse", "HEAD"], root),
            "python": platform.python_version(),
            "read_only": True,
        },
    }
    manifest["_sums"] = {t["name"]: t["_sums_entries"] for t in tree_sections}
    manifest["_local"] = {t["name"]: t["_local"] for t in tree_sections}
    return manifest


def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def render_markdown(m: dict[str, Any]) -> str:
    L: list[str] = [f"# Campaign evidence: `{m['campaign_id']}`", ""]
    L.append("This package condenses the campaign's result trees into provenance, verdicts and digests: what ran, "
             "from which commit and vehicle, which outputs passed validation, and which figures the article carries. "
             "It carries no result payloads; the raw CSVs stay on the cluster, the local copies hold the sidecars, "
             "reductions, figures and logs. Paths are repository-relative; host prefixes are normalized away.")
    L.append("")
    L.append("## Trees and toolchain")
    L.append("")
    for t in m["trees"]:
        md = t["metadata"]
        tc = t.get("toolchain") or {}
        L.append(f"- `{t['path']}`: commits {', '.join(c[:7] for c in t['git_commits']) or 'n/a'}; "
                 f"OpenModelica {', '.join(tc.get('openmodelica') or []) or 'n/a'}; "
                 f"Python {', '.join(tc.get('python') or []) or 'n/a'}; "
                 f"campaign SHA256SUMS {'present (' + str(t['sha256sums'].get('entries')) + ' entries)' if t['sha256sums']['present'] else 'absent'}; "
                 f"local copy {t['local_files']['count']} files, {t['local_files']['bytes'] / 1e6:.1f} MB.")
        if isinstance(md.get("source_sha256"), list):
            L.append("  - model sources at the campaign commit: " + "; ".join(
                f"`{e['path']}` `{e['sha256'][:12]}`" for e in md["source_sha256"]))
    L.append("")
    L.append("## Composition")
    L.append("")
    L.append("One row per (tree, family, plant, core, vehicle, commit): the cases it produced and how many of its "
             "runs carry a passing validation record and a reduction certificate bound to it.")
    L.append("")
    rows = [[g["tree"], g["family"], g["plant"], g["core_model"], f"`{g['model_name']}`", f"`{str(g['git_commit'])[:7]}`",
             g["runs"], g["validation_passed"], g["certified"], ", ".join(g["cases"])[:160]] for g in m["composition"]]
    L += _md_table(["Tree", "Family", "Plant", "Core", "Vehicle", "Commit", "Runs", "Validated", "Certified", "Cases"], rows)
    t = m["run_totals"]
    L.append("")
    L.append(f"Totals: {t['runs']} runs, {t['fingerprint_ok']} manifest fingerprints recomputed, "
             f"{t['validation_passed']} validation records passed ({t['validation_missing']} missing), "
             f"{t['certified']} reduction certificates bound; commits {', '.join(c[:7] for c in t['commits'])}.")
    if t["validation_missing"]:
        L.append("")
        L.append(f"Runs without a validation record ({', '.join(t['validation_missing_by_family'])}): their runners at "
                 "that commit wrote run manifests only. They carry no result-validation verdict in this package; their "
                 "acceptance rests on the campaign's own log checks and the article's independent number check, and "
                 "they are listed here as such, not as validated results.")
    if m.get("sweeps"):
        L.append("")
        L.append("## Frequency sweeps")
        L.append("")
        L.append("One row per sweep directory; the per-point run manifests are folded into the counts "
                 f"({t['sweep_points']} points, {t['sweep_points_validated']} with a passing validation record).")
        L.append("")
        rows = []
        for sw in m["sweeps"]:
            col = sw.get("collection") or {}
            req = sw.get("request") or {}
            rows.append([sw["tree"], f"`{sw['sweep']}`", sw["plant"], sw["core_model"], f"`{str(sw['model_name'])}`",
                         ", ".join(c[:7] for c in sw["git_commits"]), f"{sw['validation_passed']}/{sw['points']}",
                         f"`{str(req.get('fingerprint'))[:12]}`" if req else "none",
                         col.get("collection_status") or "none",
                         (col.get("counts") or {}).get("accepted") if col else ""])
        L += _md_table(["Tree", "Sweep", "Plant", "Core", "Vehicle", "Commits", "Validated/points", "Request",
                        "Collection", "Accepted"], rows)
    if m["verification"]:
        L.append("")
        L.append("## Frequency verification reports")
        L.append("")
        rows = [[f"`{v['report']}`", f"{v['accepted']}/{v['expected']}", v["fit_convergence_status"],
                 v["numerical_quality_status"], v["swing_check_pass"], v["publication_approved"]] for v in m["verification"]]
        L += _md_table(["Report", "Accepted", "Fit convergence", "Numerical quality", "Swing", "Publication approved"], rows)
    blockage = [r for r in m["runs"] if r.get("blockage_summary")]
    if blockage:
        L.append("")
        L.append("## Blockage cases")
        L.append("")
        rows = [[r["case"], f"`{str(r['git_commit'])[:7]}`", r["blockage_summary"]["execution_valid"],
                 r["blockage_summary"]["event_verified"], r["blockage_summary"]["numerical_checks_passed"],
                 f"{r['blockage_summary']['cell_salt_max_c']:.1f}" if r["blockage_summary"]["cell_salt_max_c"] is not None else "",
                 r["blockage_summary"]["physical_domain"]] for r in blockage]
        L += _md_table(["Case", "Commit", "Execution valid", "Event verified", "Checks passed", "Cell salt max [degC]",
                        "Physical domain"], rows)
    peaks = [r for r in m["runs"] if r.get("peak") and r["family"] in ("peak_convergence", "transients")
             and r["case"] == "step_2dol"]
    if peaks:
        L.append("")
        L.append("## 2 $ step peaks (output-grid convergence)")
        L.append("")
        rows = [[r["tree"], r["family"], r["plant"], r["core_model"], f"{r['peak']['nOut_max']:.4f}",
                 f"{r['peak']['t_peak_s']:.3f}", r["peak"]["raw_rows"]] for r in peaks]
        L += _md_table(["Tree", "Family", "Plant", "Core", "n max", "t peak [s]", "Rows"], rows)
    f = m["figures"]
    L.append("")
    L.append("## Figures")
    L.append("")
    if f["article_dir"]:
        L.append(f"Article figures in `{f['article_dir']}`: {f['article_matched']} of {f['article_total']} are "
                 "byte-identical to a campaign figure (the rest are drawn locally from the pulled-back reductions by "
                 "the article's scripts).")
        L.append("")
        rows = [[f"`{a['figure']}`", f"`{a['sha256'][:12]}`", f"`{a['campaign_copy']}`" if a["campaign_copy"] else "local"]
                for a in f["article"]]
        L += _md_table(["Article figure", "SHA-256 (12)", "Campaign copy"], rows)
    L.append("")
    L.append(f"Campaign figures: {len(f['campaign'])} files (digests in the manifest).")
    if f.get("article_scripts"):
        L.append(f"Article figure scripts ({len(f['article_scripts'])}, digests in the manifest): "
                 + ", ".join(f"`{x['script']}`" for x in f["article_scripts"]) + ".")
    if m.get("setpoints"):
        L.append("")
        L.append("## Setpoint tables")
        L.append("")
        for core, e in m["setpoints"]["cores"].items():
            rebuilt = e.get("rebuilt_evidence") or {}
            L.append(f"- `{core}`: table `{e['table_sha256'][:12]}` (promoted copy identical: {e['promoted_table_identical']}), "
                     f"model version `{e.get('model_version')}`; shipped evidence schema {(e.get('promoted_evidence') or {}).get('schema')} "
                     f"({(e.get('promoted_evidence') or {}).get('evidence_basis')}); rebuilt by the current builder: "
                     + (f"schema {rebuilt.get('schema')}, {rebuilt.get('rows')} rows, basis '{rebuilt.get('evidence_basis')}', "
                        f"model sources {rebuilt.get('model_sources')}" if "error" not in rebuilt else f"refused: {rebuilt['error']}"))
    if m["records"]:
        L.append("")
        L.append("## Records")
        L.append("")
        for r in m["records"]:
            s = r.get("summary") or {}
            if r["kind"] == "drift-regime-validation-report":
                c = s.get("counts") or {}
                L.append(f"- `{r['file']}` (`{r['sha256'][:12]}`): drift-regime validation, status **{s.get('status')}** "
                         f"(pass {c.get('pass')}, inconclusive {c.get('inconclusive')}, fail {c.get('fail')}, missing {c.get('missing')} "
                         f"of {c.get('points')} points), tolerances {s.get('tolerances')}, matrix commit `{str(s.get('matrix_commit'))[:7]}`, "
                         f"largest drift operating-point shift {s.get('max_drift_operating_point_shift_overall')}.")
                inconclusive = [p for p in s.get("points") or [] if p.get("status") != "pass"]
                if inconclusive:
                    L.append("  - points not passing: " + ", ".join(
                        f"`{p['point']}` ({p['status']}: drift vs full {p['drift_vs_full']}, window doubling {p['window_doubling']})"
                        for p in inconclusive))
            elif r["kind"] == "derived-metadata-correction":
                L.append(f"- `{r['file']}` (`{r['sha256'][:12]}`): {s.get('finding')}; {s.get('corrected_manifests')} manifests "
                         f"corrected, {s.get('unproved_manifests')} unproved (campaign source `{str(s.get('campaign_source_commit'))[:7]}`).")
            else:
                L.append(f"- `{r['file']}` (`{r['sha256'][:12]}`): {r['kind']}")
    if m.get("disposition"):
        L.append("")
        L.append("## Disposition")
        L.append("")
        L.append(f"From `{m['disposition']['file']}` (`{m['disposition']['sha256'][:12]}`):")
        L.append("")
        L += ["> " + ln if ln else ">" for ln in m["disposition"]["text"].splitlines()]
    if m["set_aside"]:
        L.append("")
        L.append("## Set aside")
        L.append("")
        L.append("Directories excluded from the accepted set above (their files are digested in `files.local.sha256`):")
        L.append("")
        for a in m["set_aside"]:
            reason = a["reason"] or "no README reason recorded"
            L.append(f"- `{a['tree']}/{a['path']}` ({a['files']} files, {a['manifests']} run manifests): "
                     f"{reason[:400]}{'...' if len(reason) > 400 else ''}")
    L.append("")
    L.append("## Exporter")
    L.append("")
    e = m["exporter"]
    L.append(f"`{e['module']}` (`{e['module_sha256'][:12]}`), checkout `{str(e['git_commit'])[:7]}`, Python {e['python']}; "
             "the trees were read only. A re-export of unchanged inputs is byte-identical apart from this line's checkout.")
    return "\n".join(L) + "\n"


def export(campaign_id: str, trees: list[Path], output: Path, **kwargs) -> dict[str, Any]:
    output = output.resolve()
    rel = _rel(output)
    if rel.startswith("00runs/") and not rel.startswith("00runs/tmp/"):
        raise CampaignEvidenceError(f"refusing to write the package into a result tree ({rel})")
    manifest = build(campaign_id, trees, **kwargs)
    sums = manifest.pop("_sums")
    local = manifest.pop("_local")
    output.mkdir(parents=True, exist_ok=True)
    rb._atomic_write_bytes(output / f"{campaign_id}-manifest.json",
                           (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    rb._atomic_write_bytes(output / f"{campaign_id}.md", render_markdown(manifest).encode("utf-8"))
    lines = [f"{sha}  {tree}/{rel}" for tree, entries in sorted(sums.items()) for rel, sha in sorted(entries.items())]
    sums_path = output / "SHA256SUMS.normalized.txt"
    if lines:
        rb._atomic_write_bytes(sums_path, ("\n".join(lines) + "\n").encode("utf-8"))
    elif sums_path.exists():
        sums_path.unlink()
    lines = [f"{v['sha256']}  {tree}/{rel}" for tree, entries in sorted(local.items()) for rel, v in sorted(entries.items())]
    rb._atomic_write_bytes(output / "files.local.sha256", ("\n".join(lines) + "\n").encode("utf-8"))
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--id", required=True, help="Campaign id of the package (file names and heading)")
    ap.add_argument("--tree", type=Path, action="append", required=True, help="Result tree under 00runs/ (repeatable)")
    ap.add_argument("--article-figs", type=Path, default=None, help="Article figure directory to match by digest")
    ap.add_argument("--setpoints", type=Path, default=None, help="Campaign setpoints/ directory to rebuild the compact evidence from")
    ap.add_argument("--record", type=Path, action="append", default=[], help="Record file to digest and summarize (repeatable)")
    ap.add_argument("--disposition", type=Path, default=None, help="Markdown disposition text to embed")
    ap.add_argument("--output", type=Path, required=True, help="Package directory (not inside a result tree)")
    args = ap.parse_args(argv)
    try:
        manifest = export(args.id, args.tree, args.output, article_figs=args.article_figs, setpoints=args.setpoints,
                          records=args.record, disposition=args.disposition)
    except (CampaignEvidenceError, rb.EvidenceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    t = manifest["run_totals"]
    print(f"{args.output}: {t['runs']} runs ({t['validation_passed']} validated, {t['certified']} certified), "
          f"{len(manifest['verification'])} verification reports, {len(manifest['figures']['campaign'])} campaign figures, "
          f"{len(manifest['set_aside'])} set aside")
    return 0


if __name__ == "__main__":
    sys.exit(main())
