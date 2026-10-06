#!/usr/bin/env python3
"""Export a compact scientific verification artifact for the review-2026-09
rebaseline campaign (``00runs/paper-rerun-review-2026-09/``).

The exported artifact answers, for a reviewer without the raw CSV tree:
what was run, with which model/configuration/toolchain, which cases were
accepted, and which exact result aggregates fed each paper-facing figure.
It contains manifests, hashes, and comparison tables only -- never raw
result payloads.

Usage:

    python3.12 -m helpers.rebaseline_evidence [--campaign DIR] [--output DIR]
                                              [--latex-figs DIR] [--repo-root DIR]

Defaults (resolved from the checkout containing this module):

- ``--campaign``    ``<repo_root>/00runs/paper-rerun-review-2026-09``
- ``--output``      ``<repo_root>/00runs/tmp/rebaseline-evidence``
- ``--latex-figs``  ``<repo_root>/latex/MSRR_journal_article_v2/figs``

Artifact files (written into ``--output``):

- ``review-2026-09.md``                narrative + comparison tables
- ``review-2026-09-manifest.json``     machine-readable provenance
- ``SHA256SUMS.normalized.txt``        campaign SHA256SUMS with every
                                       absolute generating-host path
                                       normalized to a deterministic
                                       campaign-root-relative path

Read-only guarantee: the campaign tree, the historical pre-fix trees
(``00runs/startup-*``, ``00runs/freq/``, ``00runs/transients-*``), and the
synced LaTeX figures are only ever opened for reading. The output target is
refused if it lies inside any published ``00runs/`` tree (anything under
``00runs/`` other than ``00runs/tmp/``, mirroring
``helpers.make_source_archive._guard_published_trees``) or inside the
campaign tree itself; pass an explicit ``--output`` for other locations.

Path normalization: every ``<sha256>  <path>`` entry of the campaign
``SHA256SUMS`` is rewritten to a path relative to the campaign root. The
recorded entries carry absolute paths from the generating host
(``/home/<user>/.../00runs/paper-rerun-review-2026-09/...``); the normalizer
locates the ``00runs/<campaign>/`` segment and keeps everything after it.
An entry that does not contain that segment fails the export loudly with
the offending line named, so no absolute path can survive silently. An
entry containing a ``..`` path component is likewise refused (with the
line named): it would normalize to a path that escapes the campaign root
and let the artifact hash a file outside the tree. The same rule maps
``metadata/source_sha256.txt`` (repository-root-relative) and
``results_dir`` fields of the campaign verification reports. The mapping
is documented inside the artifact.

Determinism: two consecutive exports of the same campaign tree produce
byte-identical artifact files. Serialization is canonical (sorted keys,
``indent=2``, trailing newline; sorted iteration everywhere), the markdown
is rendered from the same structures with fixed numeric formatting, and no
wall-clock timestamp enters the artifact content -- the only time-like
values are taken verbatim from campaign metadata. The exporting checkout's
``git rev-parse HEAD`` is recorded (a function of the tree, not the clock).

Figure-to-input bindings are derived from the campaign's own recorded
evidence: the recorded plot commands in ``helpers/paper-rerun/
submit_final_plots_11powers.py`` and ``submit_partial_plots_startup_transients.py``
combined with the input-path conventions of the plotting modules
(``freq/plotBodeCompareCoreModels.py``, ``freq/plotFreqTimeCompareCoreModels.py``,
``startup/paths.py``, ``transients/plot_nonlinear_steps.py``). The derivation
method is recorded per figure in the artifact. A SHA-256 mismatch between a
campaign figure and its synced LaTeX copy is REPORTED in the artifact, never
silently resolved.

Journal-article freeze (owner decision O10, 2026-09-08): the six startup
figure bindings whose campaign-vs-LaTeX digests diverge are the recorded,
accepted frozen state. Each carries an explicit ``binding_status`` field --
``match`` when the digests agree, ``frozen_divergence`` for the six recorded
startup divergences -- alongside the computed ``latex_copy_matches``, with
the campaign-figure digest as the authoritative side (``binding_authority``)
and a note naming the freeze decision (``binding_note``). The manifest also
carries the top-level fields ``article_frozen``, ``article_frozen_decision``,
and ``authoritative_figure_source`` so external reviews identify the accepted
frozen state without reading per-binding entries. Figure sync into the
frozen article is deferred to a separate owner decision.

Verification fields: every report carries the legacy ``publication_eligible``
label verbatim (provenance + campaign completeness; kept unchanged so the
24 previously published reports stay comparable) plus the four explicit
split fields ``provenance_complete``, ``campaign_complete``,
``numerical_quality_pass`` (evaluated from the recorded fit statistics of
the sweep aggregate against the owner threshold table,
``helpers.numerical_quality``, owner decision O2, with an explicit waiver
record field), and ``publication_approved``. Low-fit points are reported
with an explicit status (pass/waiver/fail/not_applicable) -- never deleted,
never implicitly passed.

Programmatic use::

    from helpers.rebaseline_evidence import export_evidence
    export_evidence(campaign_dir, output_dir)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    from helpers import numerical_quality as nq
except ImportError:  # direct-script execution outside an installed checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers import numerical_quality as nq

MANIFEST_NAME = "review-2026-09-manifest.json"
NARRATIVE_NAME = "review-2026-09.md"
SUMS_NAME = "SHA256SUMS.normalized.txt"
SCHEMA_VERSION = 1

#: Reference frequency (rad/s) for the per-power 1R/9R comparison column.
REFERENCE_OMEGA = 0.1

#: Number of trailing bytes inspected for end-of-record startup summaries.
_TAIL_BYTES = 1 << 20

#: Explicit per-binding status values for the campaign-vs-LaTeX figure
#: binding. ``match``: both digests agree. ``frozen_divergence``: the
#: digests diverge AND the divergence is the recorded, accepted frozen
#: state of the journal article (owner decision O10, 2026-09-08). Any
#: other combination (an absent LaTeX copy, or a divergence not listed in
#: :data:`FROZEN_DIVERGENCE_FIGURES`) records ``None`` and is reported as
#: an open deviation, never blessed.
BINDING_STATUS_MATCH = "match"
BINDING_STATUS_FROZEN_DIVERGENCE = "frozen_divergence"

#: The campaign figure is the authoritative side of every recorded binding.
BINDING_AUTHORITY_CAMPAIGN = "campaign"

#: Startup figure bindings whose campaign-vs-LaTeX divergence is the
#: recorded, accepted frozen state (owner decision O10, 2026-09-08): the
#: journal article is frozen, nothing is copied into its ``figs/`` tree,
#: and figure sync is deferred to a separate owner decision when the
#: article is unfrozen. The campaign tree is the authoritative figure set.
FROZEN_DIVERGENCE_FIGURES = frozenset(
    {
        "figures/startup/startup_phase1to4_1r.png",
        "figures/startup/startup_phase1to4_9r.png",
        "figures/startup/startup_phase1to4_signedlog_1r.png",
        "figures/startup/startup_phase1to4_signedlog_9r.png",
        "figures/startup/startup_to1MW_1r.png",
        "figures/startup/startup_to1MW_9r.png",
    }
)

#: Note recorded verbatim on every ``frozen_divergence`` binding.
FROZEN_DIVERGENCE_NOTE = (
    "Article frozen by owner decision O10 (2026-09-08): the journal "
    "article is frozen, no figure sync into "
    "latex/MSRR_journal_article_v2/figs/ is performed, and this "
    "campaign-vs-LaTeX divergence is the recorded, accepted frozen "
    "state. The campaign figure is the authoritative side; figure sync "
    "is deferred to a separate owner decision when the article is "
    "unfrozen."
)


class EvidenceError(RuntimeError):
    """Raised when the evidence artifact cannot be exported."""


# ---------------------------------------------------------------------------
# Repository resolution and output guards
# ---------------------------------------------------------------------------


#: Cap on a single soft git probe (rev-parse / status / describe). These are
#: metadata lookups, not simulations; a hung git (e.g. a wedded index lock)
#: must not stall the export indefinitely.
GIT_TIMEOUT_S = 60.0


def _run_git_soft(args: list[str], repo_root: Path) -> str | None:
    """Run ``git -C <repo_root> <args>``; return None on any failure.

    ``TimeoutExpired`` (a hung git) is treated like any other soft failure:
    the provenance metadata simply goes absent rather than hanging the export.
    """

    command = ["git", "-C", str(repo_root), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout


def _project_tree_root(start: Path) -> Path | None:
    """Nearest ancestor (inclusive) with ``pyproject.toml`` + ``core/`` + ``helpers/``."""

    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "core").is_dir()
            and (candidate / "helpers").is_dir()
        ):
            return candidate
    return None


def resolve_repo_root(explicit: str | Path | None = None) -> Path:
    """Return the repository root (the checkout containing this module)."""

    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if (root / "pyproject.toml").is_file() and (root / "helpers").is_dir():
            return root
        raise EvidenceError(
            f"--repo-root {root} does not look like this project's checkout "
            "(expected pyproject.toml next to core/ and helpers/)"
        )
    project_root = _project_tree_root(Path(__file__).resolve().parent)
    if project_root is None:
        raise EvidenceError(
            "could not locate the project checkout from this module's "
            "location; pass --repo-root"
        )
    return project_root


def _guard_output_target(output: Path, repo_root: Path, campaign: Path) -> None:
    """Refuse output targets that would touch the published record.

    Mirrors ``helpers.make_source_archive._guard_published_trees``: inside the
    repository, anything under ``00runs/`` other than ``00runs/tmp/`` is off
    limits. The campaign tree itself is additionally refused even if reached
    by another route, because it is the read-only acceptance baseline.
    """

    resolved = output.expanduser().resolve()
    campaign_resolved = campaign.expanduser().resolve()
    if resolved == campaign_resolved or campaign_resolved in resolved.parents:
        raise EvidenceError(
            f"refusing to write into the read-only campaign tree: {resolved}"
        )
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError:
        return  # outside the repository: caller's responsibility
    parts = relative.parts
    if parts[:1] == ("00runs",) and parts[1:2] != ("tmp",):
        raise EvidenceError(
            f"refusing to write into published result trees: {resolved} "
            "(anything under 00runs/ other than 00runs/tmp/ is off limits)"
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file (streamed)."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 16)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically (temp file + ``os.replace``).

    A plain ``write_bytes`` leaves a window where a concurrent reader (or a
    crash mid-write) sees a truncated artifact; the temp file is fully
    written before the atomic rename publishes it. A pre-existing target
    keeps its mode (matching the previous ``write_bytes``); a new file
    takes the umask-derived mode (``mkstemp`` starts at 0600). The umask is
    only consulted for new files and never mutated process-globally around
    an existing target (mirrors
    ``helpers.emit_modelica_plant._atomic_write_text``).
    """

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            mask = os.umask(0)
            os.umask(mask)
            mode = 0o666 & ~mask
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict:
    """Read a JSON file (read-only)."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid JSON in {path}: {exc}") from exc


def _read_small_text(path: Path) -> str:
    """Read a small text file verbatim (read-only)."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc


def _campaign_relative(recorded: str, campaign_name: str, repo_name: str) -> str:
    """Normalize one recorded absolute path.

    Rule (documented in the artifact): if the path contains
    ``00runs/<campaign_name>/``, everything after that segment is returned
    (campaign-root-relative). Otherwise, if the path contains the checkout
    root directory name, everything after it is returned
    (repository-root-relative). Anything else fails loudly: no absolute
    path may survive normalization. An entry containing a ``..`` path
    component is refused up front: without the refusal, an entry such as
    ``00runs/<campaign_name>/../../etc/passwd`` would normalize to
    ``../etc/passwd`` and let a consumer hash a file outside the campaign
    root.
    """

    posix = recorded.replace("\\", "/").strip()
    if ".." in posix.split("/"):
        raise EvidenceError(
            f"refusing recorded path {recorded!r}: it contains a '..' path "
            "component that would escape the campaign root after "
            "normalization"
        )
    marker = f"00runs/{campaign_name}/"
    idx = posix.find(marker)
    if idx >= 0:
        return posix[idx + len(marker) :]
    # Campaign-root-relative names already: pass through if they look relative.
    if not posix.startswith("/") and not PurePosixPath(posix).is_absolute():
        return posix
    # Repository-root-relative fallback (e.g. metadata/source_sha256.txt).
    # A campaign run from a commit-pinned cluster clone records the clone's
    # directory name, ``<repo_name>-c<N>`` (rev035 phase D): the same rule
    # applies to it.
    root_match = re.search(rf"/{re.escape(repo_name)}(?:-c\d+)?/", posix)
    if root_match:
        return posix[root_match.end() :]
    raise EvidenceError(
        f"cannot normalize recorded path {recorded!r}: neither the campaign "
        f"segment 00runs/{campaign_name}/ nor the checkout root name "
        f"{repo_name!r} appears in it; refusing to keep an absolute path"
    )


def _load_sha256sums(campaign: Path, repo_name: str) -> tuple[dict[str, str], dict]:
    """Parse and normalize the campaign ``SHA256SUMS``.

    Returns ``(hash_by_campaign_relative_path, stats)``. Every entry must
    normalize; duplicates by normalized path fail loudly.
    """

    sums_path = campaign / "SHA256SUMS"
    text = _read_small_text(sums_path)
    hashes: dict[str, str] = {}
    duplicates: list[str] = []
    absolute_entries = 0
    relative_entries = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise EvidenceError(
                f"{sums_path}:{lineno}: malformed SHA256SUMS entry {raw!r}"
            )
        sha, recorded = parts
        try:
            rel = _campaign_relative(recorded, campaign.name, repo_name)
        except EvidenceError as exc:
            # Keep the parse-level style: every normalization failure names
            # its line (the ``..`` refusal and the un-normalizable-path
            # refusal both surface here with the line number).
            raise EvidenceError(f"{sums_path}:{lineno}: {exc}") from exc
        if rel.startswith("/") or "::" in rel:
            raise EvidenceError(
                f"{sums_path}:{lineno}: absolute path survived normalization: {raw!r}"
            )
        if rel in hashes:
            duplicates.append(rel)
        hashes[rel] = sha
        if recorded.replace("\\", "/").strip().startswith("/"):
            absolute_entries += 1
        else:
            relative_entries += 1
    if duplicates:
        raise EvidenceError(
            "duplicate normalized paths in SHA256SUMS: " + ", ".join(sorted(duplicates))
        )
    stats = {
        "entries": len(hashes),
        "absolute_normalized": absolute_entries,
        "already_relative": relative_entries,
        "source_file": "SHA256SUMS",
        "source_sha256": _sha256_file(sums_path),
    }
    return hashes, stats


def _canonical_json(obj: dict) -> bytes:
    """Serialize to canonical deterministic JSON bytes."""

    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Campaign metadata, verification reports, inventories
# ---------------------------------------------------------------------------


def _load_campaign_metadata(campaign: Path, repo_name: str) -> dict:
    """Load the campaign-level metadata files verbatim."""

    meta = campaign / "metadata"
    required = {
        "campaign_id": "campaign_id.txt",
        "started_utc": "started_utc.txt",
        "completed_utc": "completed_utc.txt",
        "git_commit": "git_commit.txt",
        "git_status": "git_status.txt",
        "openmodelica_version": "openmodelica_version.txt",
        "python_version": "python_version.txt",
        "model_family_check": "model_family_check.txt",
    }
    fields: dict[str, object] = {}
    for key, name in required.items():
        path = meta / name
        if not path.is_file():
            raise EvidenceError(f"campaign metadata missing: {path}")
        fields[key] = _read_small_text(path).strip()

    source_sha: dict[str, str] = {}
    for raw in _read_small_text(meta / "source_sha256.txt").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise EvidenceError(
                f"malformed source_sha256.txt entry: {raw!r}"
            )
        source_sha[_campaign_relative(parts[1], campaign.name, repo_name)] = parts[0]

    excluded_path = meta / "excluded_powers.json"
    fields["excluded_powers"] = _read_json(excluded_path) if excluded_path.is_file() else None
    fields["source_sha256"] = source_sha
    fields["campaign_summary"] = _read_json(campaign / "campaign_summary.json")
    return fields


def _report_provenance_complete(data: dict) -> bool:
    """The report's identifying provenance is present and non-empty."""

    campaign_block = data.get("campaign")
    if not isinstance(campaign_block, dict):
        campaign_block = {}
    return bool(
        str(data.get("kind") or "").strip()
        and str(data.get("results_dir") or "").strip()
        and str(campaign_block.get("campaign_id") or "").strip()
        and str(campaign_block.get("fingerprint") or "").strip()
    )


def _report_campaign_complete(data: dict) -> bool:
    """The reported campaign ran completely (nothing missing/unexpected)."""

    expected = data.get("expected")
    accepted = data.get("accepted")
    return bool(
        expected is not None
        and accepted is not None
        and accepted == expected
        and not (data.get("rejected") or [])
        and not (data.get("missing") or [])
        and not (data.get("unexpected") or [])
        and not (data.get("aggregate_problems") or [])
    )


def _load_verify_reports(
    campaign: Path, repo_name: str, quality_waivers=None
) -> list[dict]:
    """Load every ``logs/*_verify.json`` campaign verification report.

    Each report record carries the legacy ``publication_eligible`` field
    verbatim plus the four explicit split fields. ``numerical_quality`` is
    evaluated from the recorded fit statistics of the report's sweep
    aggregate (``<results_dir>/FreqResponseResults.csv``) against the owner
    threshold table; reports whose results directory carries no aggregate
    evaluate to ``not_applicable`` rather than implicitly passing.
    """

    logs = campaign / "logs"
    reports: list[dict] = []
    for path in sorted(logs.glob("*_verify.json")):
        data = _read_json(path)
        results_dir = _campaign_relative(
            str(data.get("results_dir", "")), campaign.name, repo_name
        )
        provenance_complete = _report_provenance_complete(data)
        campaign_complete = _report_campaign_complete(data)
        quality = nq.evaluate_quality(
            nq.read_aggregate_fit_statistics(
                campaign / results_dir / "FreqResponseResults.csv"
            ),
            regime=nq.DEFAULT_QUALITY_REGIME,
            scope=results_dir,
            waivers=quality_waivers,
        )
        # TASK-20260912-01 P5: a verify report that recorded poison-feedback
        # governance problems (poison feedback from a dataset whose maturity
        # is outside the approved set) cannot become publication-approved on
        # replay. Reports predating the field carry no such key -- the
        # recorded rebaseline campaign is poison-off -- and are unaffected.
        publication_approved = bool(
            provenance_complete
            and campaign_complete
            and quality["pass"] is not False
            and not (data.get("poison_data_problems") or [])
        )
        reports.append(
            {
                "report": f"logs/{path.name}",
                "kind": data.get("kind"),
                "results_dir": results_dir,
                "expected": data.get("expected"),
                "accepted": data.get("accepted"),
                "rejected_count": len(data.get("rejected") or []),
                "missing_count": len(data.get("missing") or []),
                "unexpected_count": len(data.get("unexpected") or []),
                "publication_eligible": data.get("publication_eligible"),
                "provenance_complete": provenance_complete,
                "campaign_complete": campaign_complete,
                "numerical_quality": quality,
                "numerical_quality_status": quality["status"],
                "numerical_quality_pass": quality["pass"],
                "publication_approved": publication_approved,
                "sweep_campaign_id": (data.get("campaign") or {}).get("campaign_id"),
                "sweep_fingerprint": (data.get("campaign") or {}).get("fingerprint"),
            }
        )
    if not reports:
        raise EvidenceError(f"no *_verify.json reports under {logs}")
    return reports


def _load_run_params(campaign: Path, relpath: str) -> dict:
    """Parse a tab-separated ``run_params.txt`` into a dict (absent -> {})."""

    path = campaign / relpath
    if not path.is_file():
        return {}
    params: dict[str, str] = {}
    for raw in _read_small_text(path).splitlines():
        if "\t" in raw:
            key, value = raw.split("\t", 1)
            params[key.strip()] = value.strip()
    return params


def _frequency_inventory(
    campaign: Path,
    hashes: dict[str, str],
    repo_name: str,
    verify_by_dir: dict[str, dict],
) -> list[dict]:
    """Inventory every frequency sweep directory with its role and fingerprints."""

    sweeps: list[dict] = []
    freq_root = campaign / "freq"
    if not freq_root.is_dir():
        raise EvidenceError(f"campaign frequency root missing: {freq_root}")
    for core_dir in sorted(p for p in freq_root.iterdir() if p.is_dir()):
        core = core_dir.name
        for case_dir in sorted(p for p in core_dir.iterdir() if p.is_dir()):
            manifest_path = case_dir / "FreqResponseResults.manifest.json"
            if not manifest_path.is_file():
                continue
            rel_dir = f"freq/{core}/{case_dir.name}"
            manifest = _read_json(manifest_path)
            request_rel = f"{rel_dir}/sweep_request.manifest.json"
            request = _read_json(campaign / request_rel) if (campaign / request_rel).is_file() else {}
            request_block = request.get("request") or {}
            verify = verify_by_dir.get(rel_dir)
            role = "production" if verify is not None else "diagnostic"
            counts = manifest.get("counts") or {}
            entry = {
                "dir": rel_dir,
                "core_model": request_block.get("core_model", core),
                "power": request_block.get("power"),
                "model_name": request_block.get("model_name"),
                "package": request_block.get("package"),
                "role": role,
                "verified": verify is not None,
                "publication_eligible": verify["publication_eligible"] if verify else None,
                "provenance_complete": (
                    verify["provenance_complete"] if verify else None
                ),
                "campaign_complete": (
                    verify["campaign_complete"] if verify else None
                ),
                "numerical_quality_status": (
                    verify["numerical_quality_status"] if verify else None
                ),
                "numerical_quality_pass": (
                    verify["numerical_quality_pass"] if verify else None
                ),
                "publication_approved": (
                    verify["publication_approved"] if verify else None
                ),
                "accepted": counts.get("accepted"),
                "expected": counts.get("requested"),
                "collection_status": manifest.get("collection_status"),
                "collection_fingerprint": manifest.get("campaign", {}).get("fingerprint"),
                "sweep_request_fingerprint": request.get("fingerprint"),
                "git_commit": request_block.get("git_commit"),
                "omc_version": request_block.get("omc_version"),
                "python_version": request_block.get("python_version"),
                "numerics": request_block.get("numerics"),
                "setpoint_table": request_block.get("setpoint_table_path"),
                "setpoint_table_sha256": request_block.get("setpoint_table_sha256"),
                "aggregate_hashes": {
                    name: hashes.get(f"{rel_dir}/{name}")
                    for name in ("FreqResponseResults.csv", "FreqResponseResults.m")
                },
                "manifest_sha256": hashes.get(f"{rel_dir}/FreqResponseResults.manifest.json"),
                "run_params_sha256": hashes.get(f"{rel_dir}/run_params.txt"),
            }
            sweeps.append(entry)
    sweeps.sort(key=lambda e: (e["core_model"], str(e["power"]), e["dir"]))
    return sweeps


def _startup_inventory(campaign: Path, hashes: dict[str, str]) -> list[dict]:
    """Inventory the campaign startup runs from their result manifests."""

    runs: list[dict] = []
    startup_root = campaign / "startup"
    if not startup_root.is_dir():
        return runs
    for core_dir in sorted(p for p in startup_root.iterdir() if p.is_dir()):
        for manifest_path in sorted(core_dir.glob("*_res.manifest.json")):
            data = _read_json(manifest_path)
            block = data.get("manifest") or {}
            rel_manifest = f"startup/{core_dir.name}/{manifest_path.name}"
            res_rel = rel_manifest.replace("_res.manifest.json", "_res.csv")
            overrides = block.get("overrides") or {}
            runs.append(
                {
                    "run_dir": f"startup/{core_dir.name}",
                    "result_csv": res_rel,
                    "result_csv_sha256": hashes.get(res_rel),
                    "model_name": block.get("model_name"),
                    "core_model": overrides.get("core_model", core_dir.name),
                    "scenario": overrides.get("scenario"),
                    "package": overrides.get("package"),
                    "fingerprint": data.get("fingerprint"),
                    "git_commit": block.get("git_commit"),
                    "omc_version": block.get("omc_version"),
                    "python_version": block.get("python_version"),
                    "solver": block.get("solver"),
                    "tolerance": block.get("tolerance"),
                    "output_grid": block.get("output_grid"),
                    "stop_time_s": block.get("stop_time"),
                    "source_files": block.get("source_files"),
                    "manifest_sha256": hashes.get(rel_manifest),
                }
            )
    return runs


def _transients_inventory(campaign: Path, hashes: dict[str, str]) -> list[dict]:
    """Inventory the campaign transient runs from their result manifests."""

    runs: list[dict] = []
    transients_root = campaign / "transients"
    if not transients_root.is_dir():
        return runs
    for core_dir in sorted(p for p in transients_root.iterdir() if p.is_dir()):
        for manifest_path in sorted(core_dir.glob("*_res.manifest.json")):
            data = _read_json(manifest_path)
            block = data.get("manifest") or {}
            rel_manifest = f"transients/{core_dir.name}/{manifest_path.name}"
            res_rel = rel_manifest.replace("_res.manifest.json", "_res.csv")
            runs.append(
                {
                    "run_dir": f"transients/{core_dir.name}",
                    "case": manifest_path.name.replace("_res.manifest.json", ""),
                    "result_csv": res_rel,
                    "result_csv_sha256": hashes.get(res_rel),
                    "model_name": block.get("model_name"),
                    "fingerprint": data.get("fingerprint"),
                    "git_commit": block.get("git_commit"),
                    "omc_version": block.get("omc_version"),
                    "solver": block.get("solver"),
                    "tolerance": block.get("tolerance"),
                    "stop_time_s": block.get("stop_time"),
                    "manifest_sha256": hashes.get(rel_manifest),
                }
            )
    return runs


def _setpoints_inventory(campaign: Path, hashes: dict[str, str]) -> list[dict]:
    """Inventory the regenerated steady-state setpoint tables."""

    entries: list[dict] = []
    setpoints_root = campaign / "setpoints"
    if not setpoints_root.is_dir():
        return entries
    for path in sorted(setpoints_root.glob("setpoints_*.csv")):
        rel = f"setpoints/{path.name}"
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        header = rows[0] if rows else []
        entries.append(
            {
                "path": rel,
                "sha256": hashes.get(rel, _sha256_file(path)),
                "data_rows": max(len(rows) - 1, 0),
                "columns": len(header),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Figure-to-input bindings
# ---------------------------------------------------------------------------

_BODE_POWERS_RECORDED = [
    # Verbatim --powers list of the recorded 11-power plot command
    # (helpers/paper-rerun/submit_final_plots_11powers.py, driver formatting
    # f"{p:.12g}"). Kept as recorded evidence, not as behavior.
    "1e-05", "0.0001", "0.001", "0.01", "0.1",
    "0.2", "0.4", "0.6", "0.8", "1", "1.2",
]

_DERIVATION_BODE = (
    "recorded command 'python3.12 -m freq.plotBodeCompareCoreModels "
    "--results_root <campaign>/freq --out_dir <campaign>/figures/frequency "
    "--powers 1e-05 0.0001 0.001 0.01 0.1 0.2 0.4 0.6 0.8 1 1.2' in "
    "helpers/paper-rerun/submit_final_plots_11powers.py; that module reads "
    "<results_root>/<core>/power_<tag>/FreqResponseResults.csv per core "
    "(freq/plotBodeCompareCoreModels.py::build_csv_path)"
)
_DERIVATION_FREQ_TIME = (
    "recorded command 'python3.12 -m freq.plotFreqTimeCompareCoreModels "
    "--results_root <campaign>/freq_time_example --power 1.0 --freq 0.1 "
    "--out_path <campaign>/figures/frequency/MSRR_freq_nominal_time_example.png' "
    "in helpers/paper-rerun/submit_final_plots_11powers.py; that module reads "
    "the fit summary <case_dir>/FreqResponseResults.csv and the trace "
    "<case_dir>/freq00.10000/MSRR_freq00.10000_res.csv per core "
    "(freq/plotFreqTimeCompareCoreModels.py::build_case_dir/build_trace_path)"
)
_DERIVATION_STARTUP = (
    "recorded commands 'python3.12 -m startup.plotApproachToCriticalityPhase4 "
    "--core_model <core> --run_dir <campaign>/startup/<core> ...' and "
    "'python3.12 -m startup.plotStartUpTo1MW --core_model <core> --run_dir "
    "<campaign>/startup/<core> ...' in "
    "helpers/paper-rerun/submit_partial_plots_startup_transients.py; with "
    "--run_dir the modules read <run_dir>/startUp_to1MW_res.csv (1R) or "
    "<run_dir>/startUp_to1MW_9r_res.csv (9R), the startup_to_1mw prefix of "
    "startup/paths.py::STARTUP_SCENARIO_FILE_PREFIX"
)
_DERIVATION_TRANSIENTS = (
    "recorded command 'python3.12 -m transients.plot_nonlinear_steps "
    "--package legacy --outputs_dir <campaign>/transients --fig_dir "
    "<campaign>/figures/transients --core_models 1r 9r' in "
    "helpers/paper-rerun/submit_partial_plots_startup_transients.py; that "
    "module reads <outputs_dir>/<core>/<case>_res.csv for STEP_CASES "
    "(step_2dol, step_1dol, step_0p5dol), FLOW_CASES (flow_100pct, "
    "flow_66pct, flow_33pct), and UHX_CASE (uhx_trip) per "
    "transients/plot_nonlinear_steps.py"
)


def _power_tag(power: float) -> str:
    """Frequency-campaign power tag (historical stripped spelling)."""

    from helpers.power_tags import freq_power_tag

    return freq_power_tag(power)


def _sweep_manifest_output_sha(campaign: Path, rel: str) -> str | None:
    """Recorded output digest of an aggregate from its sweep manifest.

    For ``freq/<core>/power_<tag>/FreqResponseResults.(csv|m)`` inputs, the
    sweep's own ``FreqResponseResults.manifest.json`` records the SHA-256 of
    each output it wrote. That record is independent of the campaign
    ``SHA256SUMS`` snapshot and survives for aggregates added after it.
    """

    parts = rel.split("/")
    if len(parts) != 4 or parts[0] != "freq" or not parts[3].startswith(
        "FreqResponseResults."
    ):
        return None
    manifest_rel = f"{parts[0]}/{parts[1]}/{parts[2]}/FreqResponseResults.manifest.json"
    manifest_path = campaign / manifest_rel
    if not manifest_path.is_file():
        return None
    outputs = (_read_json(manifest_path).get("outputs") or {}).get(parts[3])
    if not isinstance(outputs, dict):
        return None
    sha = outputs.get("sha256")
    return sha if isinstance(sha, str) else None


def _binding_status(figure_rel: str, sha: str, latex_sha: str | None) -> tuple[str | None, str | None, str | None]:
    """Explicit per-binding status (authority, note) for one figure binding.

    Returns ``(binding_status, binding_authority, binding_note)``:

    - ``(match, campaign, None)`` when both digests agree;
    - ``(frozen_divergence, campaign, <freeze note>)`` when the digests
      diverge and the figure is one of the six recorded frozen-state
      startup divergences (:data:`FROZEN_DIVERGENCE_FIGURES`);
    - ``(None, None, None)`` when no LaTeX copy exists, or when a
      divergence is not recorded as the accepted frozen state -- an open
      deviation the artifact reports, never blesses.
    """

    if latex_sha is None:
        return None, None, None
    if latex_sha == sha:
        return BINDING_STATUS_MATCH, BINDING_AUTHORITY_CAMPAIGN, None
    if figure_rel in FROZEN_DIVERGENCE_FIGURES:
        return (
            BINDING_STATUS_FROZEN_DIVERGENCE,
            BINDING_AUTHORITY_CAMPAIGN,
            FROZEN_DIVERGENCE_NOTE,
        )
    return None, None, None


def _figure_binding(
    campaign: Path,
    figure_rel: str,
    latex_figs: Path,
    hashes: dict[str, str],
    inputs: list[str],
    derivation: str,
) -> dict:
    """Bind one campaign figure to its LaTeX copy and input aggregates.

    Each input carries up to three independent digest records: the digest
    computed directly from the file (``sha256``), the entry in the campaign
    ``SHA256SUMS`` snapshot (``sha256sums_entry``; absent for the
    post-snapshot 1e-5 MW rerun artifacts), and, for frequency aggregates,
    the digest recorded by the sweep's own collection manifest
    (``sha256_sweep_manifest``). A missing ``SHA256SUMS`` entry is recorded
    as such, never papered over.

    The binding carries the explicit ``binding_status`` field (``match`` /
    ``frozen_divergence`` / ``None``) beside the computed
    ``latex_copy_matches``, with the campaign-figure digest as the
    authoritative side (``binding_authority``) and, for recorded frozen
    divergences, the freeze note (``binding_note``).
    """

    figure_path = campaign / figure_rel
    if not figure_path.is_file():
        raise EvidenceError(f"campaign figure missing: {figure_path}")
    sha = _sha256_file(figure_path)
    sums_sha = hashes.get(figure_rel)
    latex_path = latex_figs / Path(figure_rel).name
    latex_sha = _sha256_file(latex_path) if latex_path.is_file() else None
    status, authority, note = _binding_status(figure_rel, sha, latex_sha)
    input_entries = []
    for rel in inputs:
        abs_path = campaign / rel
        manifest_sha = _sweep_manifest_output_sha(campaign, rel)
        input_entries.append(
            {
                "path": rel,
                "sha256": _sha256_file(abs_path) if abs_path.is_file() else None,
                "sha256sums_entry": hashes.get(rel),
                "sha256_sweep_manifest": manifest_sha,
                "present": abs_path.is_file(),
            }
        )
    return {
        "figure": figure_rel,
        "sha256": sha,
        "sha256sums_entry": sums_sha,
        "matches_sha256sums": (sums_sha == sha) if sums_sha is not None else None,
        "latex_copy": (
            f"latex/MSRR_journal_article_v2/figs/{Path(figure_rel).name}"
            if latex_sha is not None
            else None
        ),
        "latex_sha256": latex_sha,
        "latex_copy_matches": (latex_sha == sha) if latex_sha is not None else None,
        "binding_status": status,
        "binding_authority": authority,
        "binding_note": note,
        "inputs": input_entries,
        "derivation": derivation,
    }


def _figure_bindings(
    campaign: Path, latex_figs: Path, hashes: dict[str, str]
) -> list[dict]:
    """Derive every paper-facing figure binding from the recorded evidence."""

    bindings: list[dict] = []
    freq_dir = campaign / "figures" / "frequency"
    for png in sorted(freq_dir.glob("BodePlot_compare_1r_9r_power_*.png")):
        rel = f"figures/frequency/{png.name}"
        match = re.fullmatch(
            r"BodePlot_compare_1r_9r_power_(.+)\.png", png.name
        )
        if match is None:
            raise EvidenceError(f"unparseable Bode figure name: {png.name}")
        tag = match.group(1)
        inputs = [
            f"freq/{core}/power_{tag}/FreqResponseResults.csv"
            for core in ("1r", "9r")
        ]
        bindings.append(
            _figure_binding(campaign, rel, latex_figs, hashes, inputs, _DERIVATION_BODE)
        )
    freq_time_rel = "figures/frequency/MSRR_freq_nominal_time_example.png"
    if (campaign / freq_time_rel).is_file():
        inputs = [
            f"freq_time_example/{core}/power_1/FreqResponseResults.csv"
            for core in ("1r", "9r")
        ] + [
            f"freq_time_example/{core}/power_1/freq00.10000/MSRR_freq00.10000_res.csv"
            for core in ("1r", "9r")
        ]
        bindings.append(
            _figure_binding(
                campaign, freq_time_rel, latex_figs, hashes, inputs, _DERIVATION_FREQ_TIME
            )
        )
    startup_prefix = {"1r": "startUp_to1MW", "9r": "startUp_to1MW_9r"}
    for core in ("1r", "9r"):
        res = f"startup/{core}/{startup_prefix[core]}_res.csv"
        for stem in (
            "startup_phase1to4",
            "startup_phase1to4_signedlog",
            "startup_to1MW",
        ):
            rel = f"figures/startup/{stem}_{core}.png"
            if (campaign / rel).is_file():
                bindings.append(
                    _figure_binding(
                        campaign, rel, latex_figs, hashes, [res], _DERIVATION_STARTUP
                    )
                )
    step_cases = ["step_2dol", "step_1dol", "step_0p5dol"]
    flow_cases = ["flow_100pct", "flow_66pct", "flow_33pct"]
    step_inputs = [f"transients/{core}/{case}_res.csv" for core in ("1r", "9r") for case in step_cases]
    flow_inputs = [f"transients/{core}/{case}_res.csv" for core in ("1r", "9r") for case in flow_cases]
    uhx_inputs = [f"transients/{core}/uhx_trip_res.csv" for core in ("1r", "9r")]
    for stem, inputs in (
        ("MSRRstep_nominal", step_inputs),
        ("MSRRstep_flow", flow_inputs),
        ("MSRR_uhx_trip", uhx_inputs),
    ):
        rel = f"figures/transients/{stem}.png"
        if (campaign / rel).is_file():
            bindings.append(
                _figure_binding(
                    campaign, rel, latex_figs, hashes, inputs, _DERIVATION_TRANSIENTS
                )
            )
    bindings.sort(key=lambda b: b["figure"])
    return bindings


# ---------------------------------------------------------------------------
# Comparison-table extraction (campaign CSVs; read-only, compact scalars)
# ---------------------------------------------------------------------------


def _read_aggregate_rows(path: Path) -> list[dict[str, float]]:
    """Read a FreqResponseResults.csv aggregate into typed rows."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            try:
                rows.append(
                    {
                        "frequency_rad_s": float(raw["frequency_rad_s"]),
                        "gain": float(raw["gain"]),
                        "phase_deg": float(raw["phase_deg"]),
                        "r_squared": float(raw["R_squared"]),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceError(f"unexpected aggregate schema in {path}: {exc}") from exc
    return rows


def _column_index(header: list[str], suffix: str) -> int:
    """First header column ending with ``suffix`` (after optional quotes)."""

    for idx, name in enumerate(header):
        if name.strip().strip('"').endswith(suffix):
            return idx
    raise EvidenceError(f"column ending {suffix!r} not found in header")


#: Fuel-temperature column suffix per core model. The 1R model records its
#: physical fuel channel (``core1R.fuelchannel.fuelNode1.T``); the 9R model
#: records nine radial regions, of which R1 is the outermost
#: (``msre9r.R1.fuelNode1.T``). The mapping is recorded in the artifact so
#: the 1R/9R temperature comparison states exactly which node is quoted.
_STARTUP_FUEL_SUFFIX = {"1r": "fuelchannel.fuelNode1.T", "9r": "R1.fuelNode1.T"}


def _startup_end_state(path: Path, core_model: str) -> dict:
    """End-of-record scalars of a startup run CSV via a bounded tail read.

    The model's ``powerblock.reactorPower`` is recorded in W (the nominal
    power of the ``startup_to_1mw`` scenario is 1.0 MW = 1e6 W); the final
    power is reported in MW after dividing by 1e6. The reactivity is the
    dimensionless Delta-k/k variable ``mpke.reactivity``, reported in pcm
    (1 pcm = 1e-5). The fuel temperature is the physical fuel-channel
    node-1 temperature of each core (see :data:`_STARTUP_FUEL_SUFFIX`).
    """

    fuel_suffix = _STARTUP_FUEL_SUFFIX.get(core_model)
    if fuel_suffix is None:
        raise EvidenceError(f"no fuel-temperature column mapping for core {core_model!r}")
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(size - _TAIL_BYTES, 0))
        tail = handle.read(_TAIL_BYTES)
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise EvidenceError(f"startup CSV tail unusable: {path}")
    # The first line of the tail block may be a truncated record; the header
    # is taken from the true start of the file.
    with path.open("rb") as handle:
        header_line = handle.readline()
    header = header_line.decode("utf-8", errors="replace").strip().split(",")
    last = lines[-1].decode("utf-8", errors="replace").split(",")
    if len(last) != len(header):
        raise EvidenceError(f"startup CSV tail row width mismatch: {path}")

    def value(suffix: str) -> float:
        return float(last[_column_index(header, suffix)])

    return {
        "end_time_s": value("time"),
        "final_reactor_power_MW": value("powerblock.reactorPower") / 1.0e6,
        "final_reactivity_pcm": value("mpke.reactivity") * 1.0e5,
        "final_fuel_node1_temperature_K": value(fuel_suffix),
        "fuel_column_suffix": fuel_suffix,
        "csv_bytes": size,
    }


#: Figure-window half-widths (s) around the perturbation event, mirroring
#: ``transients/plot_nonlinear_steps.py`` (mask ``(t_rel >= -50) & (t_rel <= 400)``).
_TRANSIENT_WINDOW_BEFORE_S = 50.0
_TRANSIENT_WINDOW_AFTER_S = 400.0

#: Event-time override keys recorded in the campaign case scripts (.mos).
#: The step cases record ``externalReact.stepTime[2]=2000``; the flow cases
#: record ``externalReactivityStepTime[2]=4000``.
_MOS_EVENT_TIME_RE = re.compile(
    r"externalReac(?:t\.stepTime|tivityStepTime)\[2\]=(\d+(?:\.\d+)?)"
)


def _transient_event_time(campaign: Path, run: dict) -> tuple[float, str]:
    """Perturbation time of one transient case, from campaign records.

    The step and flow case scripts record the insertion time in their
    ``-override`` simulation flags (``externalReact.stepTime[2]=2000`` for
    steps, ``externalReactivityStepTime[2]=4000`` for the flow cases' 1$
    insertion). The UHX-trip case script embeds the demand step in the model
    (``times_s = [0, 4000]``); for it the scenario table
    (``data/scenarios/transients/results_ii.yaml``) supplies the time.
    """

    mos = campaign / run["run_dir"] / f"{run['case']}.mos"
    if mos.is_file():
        match = _MOS_EVENT_TIME_RE.search(_read_small_text(mos))
        if match is not None:
            return float(match.group(1)), "recorded case .mos override"
    if run["case"] == "uhx_trip":
        return 4000.0, "scenario table data/scenarios/transients/results_ii.yaml"
    raise EvidenceError(f"no recorded perturbation time for {run['case']}")


def _transient_window_summary(
    path: Path, event_time_s: float
) -> dict:
    """Figure-window response scalars of one transient run.

    Mirrors the paper-figure conventions of
    ``transients/plot_nonlinear_steps.py``: the window spans
    ``[event-50, event+400]`` s; power is ``powerblock.reactorPower`` (W);
    the fuel panel averages every ``fuelNode1.T`` / ``fuelNode2.T`` column
    (0.5*(mean fuelNode1 + mean fuelNode2)); the feedback is the total
    temperature-feedback variable times 1e5 (pcm). Baselines are the
    pre-event window mean (the figure's reference), so the initial settle
    excursion present in the records never enters these numbers.
    """

    t_lo = event_time_s - _TRANSIENT_WINDOW_BEFORE_S
    t_hi = event_time_s + _TRANSIENT_WINDOW_AFTER_S
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        clean = [name.strip().strip('"') for name in header]
        t_idx = _column_index(clean, "time")
        p_idx = _column_index(clean, "powerblock.reactorPower")
        fb_idx = None
        for idx, name in enumerate(clean):
            low = name.lower()
            if low.endswith("totaltempfeedback") or low.endswith(
                "sumfb.reactivityout.rho"
            ):
                fb_idx = idx
                break
        if fb_idx is None:
            raise EvidenceError(f"feedback column not found in {path}")
        fuel1_idx = [i for i, n in enumerate(clean) if n.endswith("fuelNode1.T")]
        fuel2_idx = [i for i, n in enumerate(clean) if n.endswith("fuelNode2.T")]
        if not fuel1_idx or not fuel2_idx:
            raise EvidenceError(f"fuel-temperature columns not found in {path}")

        pre_power = []
        pre_fuel = []
        peak_p = None
        peak_t = None
        min_p = None
        peak_abs_dt = None
        peak_abs_fb = None
        for row in reader:
            if len(row) <= max(t_idx, p_idx, fb_idx, max(fuel1_idx + fuel2_idx)):
                continue
            try:
                t = float(row[t_idx])
                p = float(row[p_idx])
                fb = float(row[fb_idx]) * 1.0e5
                fuel = 0.5 * (
                    sum(float(row[i]) for i in fuel1_idx) / len(fuel1_idx)
                    + sum(float(row[i]) for i in fuel2_idx) / len(fuel2_idx)
                )
            except ValueError:
                continue  # NaN gaps pass through, mirroring the figure loader
            if t < t_lo:
                continue
            if t < event_time_s:
                pre_power.append(p)
                pre_fuel.append(fuel)
                continue
            if t > t_hi:
                break
            if peak_p is None or p > peak_p:
                peak_p, peak_t = p, t
            if min_p is None or p < min_p:
                min_p = p
            if pre_fuel:
                ref = sum(pre_fuel) / len(pre_fuel)
                dt = abs(fuel - ref)
                if peak_abs_dt is None or dt > peak_abs_dt:
                    peak_abs_dt = dt
            if peak_abs_fb is None or abs(fb) > peak_abs_fb:
                peak_abs_fb = abs(fb)
    if peak_p is None or not pre_power or min_p is None:
        raise EvidenceError(f"no rows inside the figure window of {path}")
    return {
        "event_time_s": event_time_s,
        "window_s": [t_lo, t_hi],
        "pre_event_power_MW": (sum(pre_power) / len(pre_power)) / 1.0e6,
        "peak_power_MW": peak_p / 1.0e6,
        "time_of_peak_power_s": peak_t,
        "min_power_MW": min_p / 1.0e6,
        "peak_fuel_delta_T_C": peak_abs_dt,
        "peak_abs_feedback_pcm": peak_abs_fb,
    }


def _transients_comparison(campaign: Path, runs: list[dict]) -> list[dict]:
    """Figure-window 1R/9R transient comparison per case."""

    rows = []
    for run in runs:
        path = campaign / run["result_csv"]
        if not path.is_file():
            raise EvidenceError(f"transient result CSV missing: {path}")
        event_time, event_source = _transient_event_time(campaign, run)
        summary = _transient_window_summary(path, event_time)
        rows.append(
            {
                "case": run["case"],
                "core_model": run["run_dir"].split("/")[-1],
                "fingerprint": run["fingerprint"],
                "event_time_source": event_source,
                **summary,
            }
        )
    rows.sort(key=lambda r: (r["case"], r["core_model"]))
    return rows


def _classify_header(header: str) -> str:
    """Classify a result CSV by its first non-time column (model variant)."""

    first = header.split(",", 1)
    if len(first) < 2:
        return "unknown"
    col = first[1].strip().strip('"')
    if col == "time":
        return "unknown"
    if "," not in header:
        return "reduced-two-column"
    second = header.split(",")
    if len(second) >= 2 and second[1].strip().strip('"') == "value":
        return "reduced-two-column"
    if col.startswith("core1R."):
        return "legacy-1r"
    if col.startswith("core9R."):
        return "legacy-9r"
    if col.startswith(("core.", "Z1.", "pke.", "prec.")):
        return "segmented"
    return "unknown"


def _prefix_inventory(repo_root: Path, campaign_name: str) -> list[dict]:
    """Inventory the historical pre-fix trees (read-only; headers only).

    Walks ``00runs/startup-*``, ``00runs/freq``, ``00runs/transients-*``
    (never the campaign tree), records every ``*_res.csv`` with its model
    variant, and counts PNG figure outputs per tree.
    """

    runs_root = repo_root / "00runs"
    trees: list[dict] = []
    candidates: list[Path] = sorted(
        {
            p
            for p in (
                *runs_root.glob("startup-*"),
                *runs_root.glob("transients-*"),
                runs_root / "freq",
            )
            if p.is_dir() and p.name != campaign_name
        }
    )
    for tree in candidates:
        res_files: list[dict] = []
        png_count = 0
        for dirpath, _dirnames, filenames in _walk_sorted(tree):
            for name in filenames:
                path = Path(dirpath) / name
                if name.endswith("_res.csv"):
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        header = handle.readline().strip()
                    res_files.append(
                        {
                            "path": path.relative_to(repo_root).as_posix(),
                            "variant": _classify_header(header),
                            "case": name[: -len("_res.csv")],
                        }
                    )
                elif name.endswith(".png"):
                    png_count += 1
        variants: dict[str, int] = {}
        for entry in res_files:
            variants[entry["variant"]] = variants.get(entry["variant"], 0) + 1
        trees.append(
            {
                "tree": f"00runs/{tree.name}",
                "res_csv_count": len(res_files),
                "png_count": png_count,
                "variants": dict(sorted(variants.items())),
                "runs": sorted(res_files, key=lambda e: e["path"]),
            }
        )
    return trees


def _walk_sorted(root: Path):
    """Deterministic os.walk (sorted dirs and files)."""

    for dirpath, dirnames, filenames in root.walk():
        dirnames.sort()
        yield dirpath, sorted(dirnames), sorted(filenames)


# ---------------------------------------------------------------------------
# 1R/9R comparison tables (campaign = corrected model; pre-fix = context only)
# ---------------------------------------------------------------------------


def _frequency_comparison(
    campaign: Path, sweeps: list[dict]
) -> dict:
    """1R/9R frequency-response comparison per production power."""

    rows: list[dict] = []
    grid_ref_used: float | None = None
    production_powers = sorted(
        {
            float(s["power"])
            for s in sweeps
            if s["role"] == "production" and s.get("power") is not None
        }
    )
    for power in production_powers:
        tag = _power_tag(power)
        row: dict[str, object] = {"power_MW": power}
        per_core: dict[str, dict[str, float | int | str]] = {}
        for core in ("1r", "9r"):
            rel = f"freq/{core}/power_{tag}/FreqResponseResults.csv"
            path = campaign / rel
            if not path.is_file():
                raise EvidenceError(f"production aggregate missing: {path}")
            data = _read_aggregate_rows(path)
            ref = min(data, key=lambda r: abs(r["frequency_rad_s"] - REFERENCE_OMEGA))
            if grid_ref_used is None:
                grid_ref_used = ref["frequency_rad_s"]
            per_core[core] = {
                "aggregate": rel,
                "gain_at_ref": ref["gain"],
                "phase_deg_at_ref": ref["phase_deg"],
                "r_squared_at_ref": ref["r_squared"],
                "min_r_squared": min(r["r_squared"] for r in data),
                "points": len(data),
            }
        g1 = float(per_core["1r"]["gain_at_ref"])
        g9 = float(per_core["9r"]["gain_at_ref"])
        p1 = float(per_core["1r"]["phase_deg_at_ref"])
        p9 = float(per_core["9r"]["phase_deg_at_ref"])
        row["reference_omega_rad_s"] = grid_ref_used
        row["gain_1r"] = g1
        row["gain_9r"] = g9
        row["gain_ratio_1r_over_9r"] = g1 / g9 if g9 else None
        row["phase_deg_1r"] = p1
        row["phase_deg_9r"] = p9
        row["phase_difference_deg_1r_minus_9r"] = p1 - p9
        row["details"] = per_core
        rows.append(row)
    return {
        "reference_omega_rad_s": grid_ref_used,
        "reference_selection": (
            "grid point nearest omega = 0.1 rad/s of the shared 80-point "
            "logarithmic sweep grid; both cores share the grid, so the "
            "comparison is point-wise exact"
        ),
        "units": (
            "gain dimensionless (output/input amplitude ratio); phase in "
            "degrees; omega in rad/s; power in MW"
        ),
        "rows": rows,
    }


def _startup_comparison(campaign: Path, runs: list[dict]) -> list[dict]:
    """End-of-record 1R/9R startup comparison from the campaign run CSVs."""

    rows = []
    for run in runs:
        path = campaign / run["result_csv"]
        if not path.is_file():
            raise EvidenceError(f"startup result CSV missing: {path}")
        state = _startup_end_state(path, str(run["core_model"]))
        rows.append(
            {
                "core_model": run["core_model"],
                "scenario": run["scenario"],
                "fingerprint": run["fingerprint"],
                **state,
            }
        )
    rows.sort(key=lambda r: r["core_model"])
    return rows


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt(value, digits: int = 4) -> str:
    """Fixed-format a float for the markdown tables (deterministic)."""

    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _md_freq_table(freq: dict) -> list[str]:
    lines = [
        f"Reference grid point: omega = {_fmt(freq['reference_omega_rad_s'], 6)} rad/s "
        "(nearest grid point to 0.1 rad/s on the shared 80-point logarithmic grid).",
        "",
        "| P (MW) | gain 1R | gain 9R | ratio 1R/9R | phase 1R (deg) | phase 9R (deg) | d-phase (deg) | min R^2 1R | min R^2 9R |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in freq["rows"]:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                _fmt(row["power_MW"], 6),
                _fmt(row["gain_1r"]),
                _fmt(row["gain_9r"]),
                _fmt(row["gain_ratio_1r_over_9r"]),
                _fmt(row["phase_deg_1r"]),
                _fmt(row["phase_deg_9r"]),
                _fmt(row["phase_difference_deg_1r_minus_9r"]),
                _fmt(row["details"]["1r"]["min_r_squared"]),
                _fmt(row["details"]["9r"]["min_r_squared"]),
            )
        )
    return lines


def _md_startup_table(rows: list[dict]) -> list[str]:
    lines = [
        "| Core | Scenario | End of record (s) | Final P (MW) | Final rho (pcm) | Final fuel-node-1 T (K) |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                row["core_model"],
                row["scenario"],
                _fmt(row["end_time_s"], 6),
                _fmt(row["final_reactor_power_MW"], 6),
                _fmt(row["final_reactivity_pcm"], 6),
                _fmt(row["final_fuel_node1_temperature_K"], 6),
            )
        )
    return lines


def _md_transient_table(rows: list[dict]) -> list[str]:
    lines = [
        "| Case | Core | Event (s) | Pre-event P (MW) | Peak P (MW) | Min P (MW) | Peak dT_fuel (C) | Peak abs feedback (pcm) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row["case"],
                row["core_model"],
                _fmt(row["event_time_s"], 6),
                _fmt(row["pre_event_power_MW"], 4),
                _fmt(row["peak_power_MW"], 4),
                _fmt(row["min_power_MW"], 4),
                _fmt(row["peak_fuel_delta_T_C"]),
                _fmt(row["peak_abs_feedback_pcm"]),
            )
        )
    return lines


def _md_figure_table(bindings: list[dict]) -> list[str]:
    lines = [
        "| Figure | SHA-256 (12) | LaTeX copy matches | Binding status | Inputs with digests |",
        "|---|---|---|---|---|",
    ]
    for b in bindings:
        n_inputs = len(b["inputs"])
        bound = sum(
            1
            for i in b["inputs"]
            if i["present"] and i["sha256"] is not None
        )
        lines.append(
            "| `{}` | `{}` | {} | {} | {}/{} |".format(
                b["figure"],
                b["sha256"][:12],
                _fmt(b["latex_copy_matches"]),
                _fmt(b["binding_status"]),
                bound,
                n_inputs,
            )
        )
    return lines


def _md_prefix_table(trees: list[dict]) -> list[str]:
    lines = [
        "| Tree | Result CSVs | Model variants | PNGs |",
        "|---|---|---|---|",
    ]
    for tree in trees:
        variants = (
            ", ".join(f"{k} ({v})" for k, v in tree["variants"].items())
            if tree["variants"]
            else "none"
        )
        lines.append(
            "| `{}` | {} | {} | {} |".format(
                tree["tree"], tree["res_csv_count"], variants, tree["png_count"]
            )
        )
    return lines


def render_markdown(evidence: dict) -> str:
    """Render the narrative + tables artifact in journal register."""

    camp = evidence["campaign"]
    meta = camp["metadata"]
    verify = evidence["verification"]
    totals = verify["totals"]
    freq_cmp = evidence["comparisons"]["frequency_1r_9r"]
    startup_cmp = evidence["comparisons"]["startup_1r_9r"]
    trans_cmp = evidence["comparisons"]["transients_1r_9r"]
    prefix = evidence["prefix_inventory"]
    matched = sum(
        1
        for tree in prefix["trees"]
        for run in tree["runs"]
        if run["campaign_match"]
    )
    unbound = evidence["figures"]["unbound_latex_figures"]

    lines: list[str] = []
    add = lines.append
    add("# Review-2026-09 rebaseline verification evidence")
    add("")
    add(
        "This artifact condenses the `review-2026-09` corrected-model rebaseline "
        f"campaign (`00runs/{camp['root_name']}/`) into manifests, hashes, and "
        "comparison tables. It lets a reviewer verify what was run, with which "
        "model, configuration, and toolchain, and which exact outputs fed each "
        "reported figure, without the raw CSV tree. The campaign tree was opened "
        "read-only; the artifact carries no result payloads."
    )
    add("")
    add("## Campaign identity and toolchain")
    add("")
    add(f"- Campaign id: `{meta['campaign_id']}`")
    add(f"- Window: {meta['started_utc']} to {meta['completed_utc']} (campaign metadata, verbatim)")
    add(f"- Source commit: `{meta['git_commit']}` (campaign metadata; tracked-tree status: {meta['git_status'] or 'clean'})")
    add(f"- OpenModelica: `{meta['openmodelica_version']}`; Python: `{meta['python_version']}`")
    add(f"- Model family check: {meta['model_family_check']}")
    add(
        "- Model source digests (campaign metadata, normalized to repository-relative paths): "
        + "; ".join(
            f"`{path}` `{sha[:12]}`" for path, sha in sorted(meta["source_sha256"].items())
        )
    )
    _recon = evidence["normalization"]["stats"].get("file_count_reconciliation")
    add(
        f"- Tree inventory (campaign_summary.json): {meta['campaign_summary']['file_count']} files, "
        f"{meta['campaign_summary']['csv_count']} CSV result tables, "
        f"{meta['campaign_summary']['png_count']} PNG figures, "
        f"{_fmt(meta['campaign_summary']['total_bytes'] / 1e9, 4)} GB."
        + (f" Reconciliation: {_recon}." if _recon else "")
    )
    add("")
    add("## Verification status and case inventory")
    add("")
    quality_counts: dict[str, int] = {}
    approved_count = 0
    for report in verify["reports"]:
        status = str(report.get("numerical_quality_status"))
        quality_counts[status] = quality_counts.get(status, 0) + 1
        if report.get("publication_approved") is True:
            approved_count += 1
    quality_summary = ", ".join(
        f"{count} {status}" for status, count in sorted(quality_counts.items())
    )
    add(
        f"The campaign's {totals['reports']} verification reports cover "
        f"{totals['accepted']} accepted simulation points out of "
        f"{totals['expected']} expected; every report is publication-eligible "
        "-- the legacy derived/provenance field, kept unchanged so these "
        "reports stay comparable with the previously published ones; it "
        "means provenance plus campaign completeness and carries no "
        "numerical-quality content. Numerical quality is reported "
        "explicitly per report from the recorded fit statistics against "
        "the owner threshold table (owner decision O2, 2026-09-08 default: "
        "R^2 >= 0.95 pass, 0.80-0.95 waiver required, < 0.80 fail): "
        f"{quality_summary}; {approved_count} of {totals['reports']} "
        "reports are publication-approved. Low-fit points are recorded "
        "with their explicit status and stay traceable; none is deleted. "
        "The inventory comprises 22 production frequency sweeps (11 powers "
        "x 2 core models x 80 points = 1760 points), 2 nominal-power "
        "time-domain frequency cases, 2 startup runs, 20 nonlinear "
        "transient runs (10 cases x 2 core models), and 2 regenerated "
        "steady-state setpoint tables."
    )
    add("")
    add(
        "The 1e-5 MW (0.01 kW) sweeps were rerun on a coarse settle-output grid "
        "after an OpenModelica v1.27.0-cmake delay ring-buffer overflow crashed "
        "the original protocol-grid points. The campaign records the deviation "
        "and its remedy in `metadata/excluded_powers.json`; its status field "
        f"reads: \"{meta['excluded_powers']['status']}\". The measured "
        "protocol-vs-coarse equivalence at omega = 1.73983 rad/s is 0.06% gain "
        "and 0.002 deg phase (campaign record, verbatim). The coarse-grid "
        "verification reports (`logs/freq_*_0p00001_coarse_verify.json`) accept "
        "80/80 points on both cores."
    )
    add("")
    add("## Path normalization")
    add("")
    norm = evidence["normalization"]
    add(
        "The campaign `SHA256SUMS` carries absolute paths from the generating "
        "host. Every entry is normalized to a campaign-root-relative path: the "
        "normalizer locates the `00runs/paper-rerun-review-2026-09/` segment and "
        "keeps everything after it. For example,"
    )
    add("")
    add("    before: /home/<user>/git/SMD-MSRR-dev/00runs/paper-rerun-review-2026-09/campaign_summary.json")
    add("    after:  campaign_summary.json")
    add("")
    add(
        f"All {norm['stats']['absolute_normalized']} recorded entries were "
        f"absolute and normalized; {norm['stats']['already_relative']} were "
        "already relative. Any entry that cannot be normalized fails the "
        "export loudly, so no absolute path can survive in "
        "`SHA256SUMS.normalized.txt`. The same rule maps the `results_dir` "
        "fields of the verification reports and the "
        "`metadata/source_sha256.txt` entries (the latter to "
        "repository-relative paths, because they point at model sources "
        "outside the campaign tree)."
        + (
            " The campaign `file_count` exceeds the sums entries by exactly "
            "the self-excluded `SHA256SUMS` manifest (see the tree-inventory "
            "reconciliation above)."
            if _recon is not None
            else ""
        )
    )
    add("")
    add("## Figure provenance")
    add("")
    add(
        "Each paper-facing figure is bound to the aggregate result files it "
        "was plotted from, with SHA-256 digests. Three independent digest "
        "records are kept per input where available: the digest computed "
        "directly from the file, the entry in the campaign `SHA256SUMS` "
        "snapshot, and the digest recorded by the sweep's own collection "
        "manifest. The `SHA256SUMS` snapshot predates the final 1e-5 MW "
        "coarse-grid rerun, so the artifacts it added (the "
        "`power_0p00001` aggregates and the 11-power Bode figure for that "
        "power) carry direct and sweep-manifest digests but no `SHA256SUMS` "
        "entry; this is recorded per binding, never silently repaired. The "
        "bindings are derived from the campaign's own recorded evidence: the "
        "recorded plot commands in `helpers/paper-rerun/"
        "submit_final_plots_11powers.py` (frequency set) and "
        "`helpers/paper-rerun/submit_partial_plots_startup_transients.py` "
        "(startup and transient set), combined with the input-path "
        "conventions of the plotting modules; the per-figure derivation "
        "text is recorded in the machine-readable manifest."
    )
    add("")
    add(
        "The journal article is frozen (owner decision O10, 2026-09-08): "
        f"`article_frozen: {str(evidence['article_frozen']).lower()}`, "
        f"`article_frozen_decision: {evidence['article_frozen_decision']}`, "
        "and the authoritative figure set is "
        f"`{evidence['authoritative_figure_source']}`. Nothing is copied "
        "into the frozen article's `figs/` tree by this artifact; the "
        "campaign-vs-LaTeX binding digests are recorded read-only."
    )
    add("")
    frozen_divergences = [
        b
        for b in evidence["figures"]["bindings"]
        if b["binding_status"] == BINDING_STATUS_FROZEN_DIVERGENCE
    ]
    unrecorded = [
        b
        for b in evidence["figures"]["bindings"]
        if b["latex_copy_matches"] is False
        and b["binding_status"] != BINDING_STATUS_FROZEN_DIVERGENCE
    ]
    if frozen_divergences:
        add(
            "Resolution record (frozen divergence, intentional): the "
            f"following {len(frozen_divergences)} startup figure bindings "
            "diverge from their frozen LaTeX copies, and that divergence is "
            "the recorded, accepted frozen state of the article -- not an "
            "open mismatch. Both sides' SHA-256 digests are recorded per "
            "binding in the machine-readable manifest with "
            "`binding_status: frozen_divergence` and the campaign figure as "
            "the authoritative side (`binding_authority: campaign`); the "
            "campaign tree remains the authoritative figure set. Figure "
            "sync into the article is deferred to a separate owner decision "
            "when the article is unfrozen: "
            + ", ".join(f"`{b['figure']}`" for b in frozen_divergences)
        )
        add("")
    if unrecorded:
        add(
            "Deviation to resolve by the campaign owner: the following figures "
            "do not match their synced LaTeX copies and the divergence is NOT "
            "recorded as the accepted frozen state. Both digests are recorded "
            "in the machine-readable manifest; the mismatch is reported, never "
            "silently resolved: "
            + ", ".join(f"`{b['figure']}`" for b in unrecorded)
        )
        add("")
    lines.extend(_md_figure_table(evidence["figures"]["bindings"]))
    add("")
    if unbound:
        add(
            "One LaTeX figure is a schematic, not a plotted result, and has no "
            f"campaign counterpart: {', '.join(f'`{u}`' for u in unbound)}."
        )
        add("")
    add("## 1R/9R comparison (campaign, corrected model)")
    add("")
    add("### Frequency response")
    add("")
    add(
        "Gains are dimensionless output/input amplitude ratios; phases are in "
        "degrees; the reference baseline is the shared 80-point logarithmic grid "
        "(0.001 to 10 rad/s) at each power; the comparison is point-wise exact "
        "because both core models share the grid. P is the nominal reactor power "
        "in MW."
    )
    add("")
    lines.extend(_md_freq_table(freq_cmp))
    add("")
    add("### Startup (scenario `startup_to_1mw`)")
    add("")
    add(
        "End-of-record values. P is the model's `powerblock.reactorPower` "
        "(recorded in W, reported in MW); the reactivity is the final "
        "`mpke.reactivity` (dimensionless Delta-k/k, reported in pcm, "
        "1 pcm = 1e-5); the fuel temperature is the physical fuel-channel "
        "node-1 temperature of each core: `core1R.fuelchannel.fuelNode1.T` "
        "for the 1R model and `msre9r.R1.fuelNode1.T` (outermost radial "
        "region R1) for the 9R model, in K. The end of record is the "
        "baseline for every column."
    )
    add("")
    lines.extend(_md_startup_table(startup_cmp))
    add("")
    add("### Nonlinear transients (legacy package)")
    add("")
    add(
        "Every quantity is evaluated inside each figure's own display window, "
        "t in [event - 50, event + 400] s, exactly as the paper figures draw "
        "them (event time: 2000 s for the reactivity steps, 4000 s for the "
        "flow cases' 1$ insertion and the UHX-trip demand step, each taken "
        "from the case's recorded simulation script overrides, or from the "
        "scenario table for the UHX trip). P is the model's "
        "`powerblock.reactorPower` (recorded in W, reported in MW); the "
        "pre-event baseline is the mean over the 50 s before the event (the "
        "figure's reference); dT_fuel is the peak absolute change of the "
        "fuel-panel average (mean of the fuelNode1/fuelNode2 columns) "
        "relative to that baseline, in C (K shifted by 273.15); feedback is "
        "the total temperature-feedback variable in pcm. The flow cases "
        "combine a primary-pump trip to the case flow fraction at 2000 s "
        "with the 1$ insertion at 4000 s; the flow figure window covers the "
        "insertion, so the pre-event baseline is the post-trip steady state. "
        "The record-wide maxima of the sub-dollar step cases are set by a "
        "case-independent initial settle excursion (peak n/n(0) of 3.03 for "
        "the 1R model at t = 174 s) that predates every event; the windows "
        "exclude it by construction."
    )
    add("")
    lines.extend(_md_transient_table(trans_cmp))
    add("")
    add("## Historical pre-fix trees (comparison context only)")
    add("")
    add(
        "The trees `00runs/startup-*`, `00runs/freq/`, and `00runs/transients-*` "
        "are the PRE-FIX published record (the v1 submission's trees). They are "
        "comparison context only and are never an acceptance baseline for the "
        "corrected model; nothing in this artifact treats them as such. The "
        "inventory below classifies every result CSV in those trees by its "
        "recorded model variant (from the CSV header) and flags whether a "
        "model-variant- and case-matched campaign run exists."
    )
    add("")
    lines.extend(_md_prefix_table(prefix["trees"]))
    add("")
    if matched == 0:
        add(
            "No result CSV in the pre-fix trees is both model-variant-matched "
            "and case-matched to a campaign run: the pre-fix startup trees hold "
            "the `startup` and `startup_to_100kw` scenarios while the campaign "
            "ran `startup_to_1mw`; the pre-fix transient and frequency trees "
            "hold segmented-model records (or two-column reduced placeholders) "
            "while the campaign ran the legacy 1R and 9R packages. A numerical "
            "pre-fix/campaign comparison is therefore not tabulated here; the "
            "published pre-fix comparison remains the v1 submission's figures."
        )
    else:
        add(
            f"{matched} model-variant- and case-matched pre-fix runs were found; "
            "their run-level records, including the same end-of-record scalars "
            "computed for the campaign runs, are in the machine-readable manifest."
        )
    add("")
    add("## Reproducing this artifact")
    add("")
    add(
        f"Exported from the campaign tree with `python3.12 -m "
        f"helpers.rebaseline_evidence --output <dir>` (exporting checkout "
        f"commit `{evidence['exporter']['git_commit']}`). Two consecutive "
        "exports of the same tree are byte-identical; the artifact carries no "
        "export-time timestamps. Re-running the exporter against the same "
        "campaign and comparing SHA-256 digests re-verifies every binding."
    )
    add("")
    add(
        "Machine-readable provenance: `review-2026-09-manifest.json`; "
        "normalized result hashes: `SHA256SUMS.normalized.txt`."
    )
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Assembly and export
# ---------------------------------------------------------------------------


def _load_quality_waivers(campaign: Path) -> list[dict]:
    """Load recorded quality waivers from the campaign metadata (or ``[]``).

    Waiver records live in ``metadata/quality_waivers.json`` when the owner
    records them; the file is optional. Each record must carry ``scope``
    (the evaluated scope string, e.g. the campaign-relative sweep
    directory) and ``statistic``; every other key rides along verbatim.
    Missing file means no waivers, never implicit approval.
    """

    path = campaign / "metadata" / "quality_waivers.json"
    if not path.is_file():
        return []
    payload = _read_json(path)
    waivers = payload.get("waivers") if isinstance(payload, dict) else None
    if not isinstance(waivers, list):
        raise EvidenceError(
            f"{path}: expected a top-level 'waivers' list, got "
            f"{type(waivers).__name__}"
        )
    for waiver in waivers:
        if not isinstance(waiver, dict):
            raise EvidenceError(f"{path}: waiver records must be objects")
        if not str(waiver.get("scope") or "").strip():
            raise EvidenceError(f"{path}: waiver record missing 'scope': {waiver!r}")
        if not str(waiver.get("statistic") or "").strip():
            raise EvidenceError(
                f"{path}: waiver record missing 'statistic': {waiver!r}"
            )
    return waivers


def _campaign_summary_reconciliation(meta: dict, sums_stats: dict) -> str | None:
    """M-16 (rev022): reconcile ``campaign_summary.json``'s ``file_count``
    against the SHA256SUMS entry count.

    The campaign inventory (``helpers/paper-rerun/run_paper_rerun_gateway.py``
    and ``submit_final_tail_excl_1e5.py``) is a plain ``rglob('*')`` walk over
    every file in the tree -- INCLUDING the ``SHA256SUMS`` manifest itself --
    while the sums generator excludes the sums file from its own digest list
    by construction (``find <campaign> -type f ! -name SHA256SUMS``). A
    difference of exactly one is therefore the self-excluded ``SHA256SUMS``
    manifest; any other difference is recorded verbatim as unexplained. The
    sentence is stored in the sums-stats dicts (manifest) and rendered beside
    the tree-inventory bullet (narrative), so the recorded off-by-one is
    explicit in both artifact views.
    """
    summary = meta.get("campaign_summary") or {}
    file_count = summary.get("file_count")
    if file_count is None:
        return None
    difference = int(file_count) - int(sums_stats["entries"])
    if difference == 1:
        return (
            "file_count includes the SHA256SUMS manifest itself, which is not "
            "a member of SHA256SUMS.normalized.txt: the campaign inventory is "
            "a plain walk over every file in the tree (including SHA256SUMS), "
            "while the sums generator excludes the sums file from its own "
            "digest list by construction (find <campaign> -type f "
            "! -name SHA256SUMS), so file_count - entries = 1 is exactly the "
            "SHA256SUMS file"
        )
    return (
        f"file_count - entries = {difference}; the difference is not the "
        "self-excluded SHA256SUMS manifest and is left unexplained by this "
        "exporter"
    )


def build_evidence(
    campaign: Path, latex_figs: Path, repo_root: Path
) -> tuple[dict, str, str]:
    """Collect, compare, and serialize; return (evidence, markdown, sums_text)."""

    if not campaign.is_dir():
        raise EvidenceError(f"campaign directory not found: {campaign}")
    repo_name = repo_root.name
    meta = _load_campaign_metadata(campaign, repo_name)
    hashes, sums_stats = _load_sha256sums(campaign, repo_name)
    recon_sentence = _campaign_summary_reconciliation(meta, sums_stats)
    if recon_sentence is not None:
        # One shared dict: the sentence lands in BOTH campaign.sha256sums_stats
        # and normalization.stats (the exporter keeps them equal by design).
        sums_stats["file_count_reconciliation"] = recon_sentence
    quality_waivers = _load_quality_waivers(campaign)
    verify_reports = _load_verify_reports(campaign, repo_name, quality_waivers)
    verify_by_dir = {r["results_dir"]: r for r in verify_reports}

    # The verify reports carry repo-relative results_dir; strip the campaign
    # prefix for the role lookup.
    verify_by_campaign_rel = {}
    for r in verify_reports:
        rel = r["results_dir"]
        marker = f"00runs/{campaign.name}/"
        if rel.startswith(marker):
            rel = rel[len(marker):]
        verify_by_campaign_rel[rel] = r

    freq_sweeps = _frequency_inventory(
        campaign, hashes, repo_name, verify_by_campaign_rel
    )
    startup_runs = _startup_inventory(campaign, hashes)
    transient_runs = _transients_inventory(campaign, hashes)
    setpoints = _setpoints_inventory(campaign, hashes)

    totals = {
        "reports": len(verify_reports),
        "expected": sum(r["expected"] or 0 for r in verify_reports),
        "accepted": sum(r["accepted"] or 0 for r in verify_reports),
        "rejected": sum(r["rejected_count"] for r in verify_reports),
        "publication_eligible": all(r["publication_eligible"] for r in verify_reports),
        "provenance_complete": all(r["provenance_complete"] for r in verify_reports),
        "campaign_complete": all(r["campaign_complete"] for r in verify_reports),
        "numerical_quality_pass": nq.aggregate_quality_pass(verify_reports),
        "publication_approved": all(r["publication_approved"] for r in verify_reports),
    }

    bindings = _figure_bindings(campaign, latex_figs, hashes)
    latex_names = {Path(b["figure"]).name for b in bindings}
    campaign_figure_names = {Path(b["figure"]).name for b in bindings}
    unbound = sorted(
        p.name for p in latex_figs.glob("*.png") if p.name not in campaign_figure_names
    )
    del latex_names

    freq_cmp = _frequency_comparison(campaign, freq_sweeps)
    startup_cmp = _startup_comparison(campaign, startup_runs)
    trans_cmp = _transients_comparison(campaign, transient_runs)

    prefix_trees = _prefix_inventory(repo_root, campaign.name)
    campaign_variants = {
        "legacy-1r": {r["case"] for r in transient_runs if r["run_dir"].endswith("1r")}
        | {r["scenario"] for r in startup_runs if r["core_model"] == "1r"}
        | {"freq"},
        "legacy-9r": {r["case"] for r in transient_runs if r["run_dir"].endswith("9r")}
        | {r["scenario"] for r in startup_runs if r["core_model"] == "9r"}
        | {"freq"},
    }
    for tree in prefix_trees:
        for run in tree["runs"]:
            cases = campaign_variants.get(run["variant"], set())
            run["campaign_match"] = run["case"] in cases

    head = (_run_git_soft(["rev-parse", "HEAD"], repo_root) or "").strip() or None

    evidence: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "msrr-rebaseline-evidence",
        # Journal-article freeze (owner decision O10, 2026-09-08): the
        # accepted frozen state is identified at manifest TOP LEVEL so
        # external reviews do not have to read per-binding entries. The
        # campaign figure set is authoritative; figure sync into the
        # frozen article is a separate owner decision.
        "article_frozen": True,
        "article_frozen_decision": "O10 (2026-09-08)",
        "authoritative_figure_source": f"00runs/{campaign.name}/figures/",
        "exporter": {
            "module": "helpers.rebaseline_evidence",
            "git_commit": head,
            "campaign_read_only": True,
        },
        "campaign": {
            "root_name": campaign.name,
            "metadata": meta,
            "sha256sums_stats": sums_stats,
        },
        "normalization": {
            "rule": (
                "campaign SHA256SUMS entries are rewritten relative to the "
                "campaign root: the segment 00runs/<campaign>/ is located in "
                "each recorded path and everything after it is kept; entries "
                "outside the campaign map to repository-relative paths through "
                "the checkout root name. Unnormalizable entries fail the "
                "export; no absolute path survives."
            ),
            "stats": sums_stats,
        },
        "verification": {"reports": verify_reports, "totals": totals},
        "inventory": {
            "frequency_sweeps": freq_sweeps,
            "startup_runs": startup_runs,
            "transient_runs": transient_runs,
            "setpoints": setpoints,
        },
        "configuration_fingerprints": {
            "frequency_sweeps": {
                s["dir"]: {
                    "collection_fingerprint": s["collection_fingerprint"],
                    "sweep_request_fingerprint": s["sweep_request_fingerprint"],
                    "numerics": s["numerics"],
                    "setpoint_table_sha256": s["setpoint_table_sha256"],
                }
                for s in freq_sweeps
            },
            "startup_runs": {
                r["result_csv"]: {"fingerprint": r["fingerprint"], "solver": r["solver"], "tolerance": r["tolerance"], "output_grid": r["output_grid"]}
                for r in startup_runs
            },
            "transient_runs": {
                r["result_csv"]: {"fingerprint": r["fingerprint"], "solver": r["solver"], "tolerance": r["tolerance"]}
                for r in transient_runs
            },
        },
        "figures": {
            "bindings": bindings,
            "unbound_latex_figures": unbound,
            "latex_figs_dir": (
                latex_figs.relative_to(repo_root).as_posix()
                if latex_figs.is_relative_to(repo_root)
                else str(latex_figs)
            ),
        },
        "comparisons": {
            "frequency_1r_9r": freq_cmp,
            "startup_1r_9r": startup_cmp,
            "transients_1r_9r": trans_cmp,
        },
        "prefix_inventory": {"trees": prefix_trees},
    }

    sums_text = "".join(
        f"{hashes[rel]}  {rel}\n" for rel in sorted(hashes)
    )
    markdown = render_markdown(evidence)
    return evidence, markdown, sums_text


def export_evidence(
    campaign: str | Path | None = None,
    output: str | Path | None = None,
    latex_figs: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> list[Path]:
    """Export the artifact; return the written file paths (sorted)."""

    root = resolve_repo_root(repo_root)
    campaign_dir = (
        Path(campaign).expanduser().resolve()
        if campaign is not None
        else root / "00runs" / "paper-rerun-review-2026-09"
    )
    out_dir = (
        Path(output).expanduser().resolve()
        if output is not None
        else root / "00runs" / "tmp" / "rebaseline-evidence"
    )
    figs_dir = (
        Path(latex_figs).expanduser().resolve()
        if latex_figs is not None
        else root / "latex" / "MSRR_journal_article_v2" / "figs"
    )
    _guard_output_target(out_dir, root, campaign_dir)
    if not figs_dir.is_dir():
        raise EvidenceError(f"LaTeX figures directory not found: {figs_dir}")

    evidence, markdown, sums_text = build_evidence(campaign_dir, figs_dir, root)

    out_dir.mkdir(parents=True, exist_ok=True)
    sums_path = out_dir / SUMS_NAME
    md_path = out_dir / NARRATIVE_NAME
    _atomic_write_bytes(sums_path, sums_text.encode("utf-8"))
    _atomic_write_bytes(md_path, markdown.encode("utf-8"))

    evidence["artifact_files"] = {
        SUMS_NAME: _sha256_file(sums_path),
        NARRATIVE_NAME: _sha256_file(md_path),
    }
    manifest_path = out_dir / MANIFEST_NAME
    _atomic_write_bytes(manifest_path, _canonical_json(evidence))
    return sorted([sums_path, md_path, manifest_path])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rebaseline_evidence",
        description=(
            "Export a compact scientific verification artifact (manifests, "
            "hashes, comparison tables) for the review-2026-09 rebaseline "
            "campaign; reads the campaign tree read-only and never writes "
            "into published 00runs/ result trees."
        ),
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        default=None,
        help=(
            "campaign tree to export from (default: "
            "00runs/paper-rerun-review-2026-09; opened read-only)"
        ),
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=(
            "artifact destination directory (default: "
            "00runs/tmp/rebaseline-evidence; never inside a published "
            "00runs/ result tree)"
        ),
    )
    parser.add_argument(
        "--latex-figs",
        type=Path,
        default=None,
        help=(
            "synced paper-figure directory for the binding cross-check "
            "(default: latex/MSRR_journal_article_v2/figs)"
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="project checkout (default: the checkout containing this module)",
    )
    args = parser.parse_args(argv)

    written = export_evidence(
        campaign=args.campaign,
        output=args.output,
        latex_figs=args.latex_figs,
        repo_root=args.repo_root,
    )
    print("rebaseline evidence exported:")
    for path in written:
        print(f"  {path}  {_sha256_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
