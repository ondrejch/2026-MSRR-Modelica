#!/usr/bin/env python3
"""Drift-regime validation matrix for the frequency-response protocol.

The ``settle_prior_v2`` drift regime (freq/README.md, "Drift regime")
replaces the full resonance discard by the slow-mode discard, a window of a
tenth of the natural period, a linear trend, and a gain referenced to the
window-mean power, for points far above the resonance.  The rev032 review
asks for a numerical qualification before the corrected campaign is
accepted: compare drift-regime and full-discard estimates at the regime
boundary and at interior high-frequency points, at the lowest three powers,
for both cores, and measure the sensitivity to doubling the drift window.

This dispatcher builds that matrix as gateway jobs.  Each matrix cell is a
single-point sweep (``--freq_min W --freq_max W --num_freq 1``) in its own
directory ``<root>/<core>/power_<tag>/<label>_<variant>/``, with the
campaign runner arguments plus

- ``drift_phi0p1``: nothing (the protocol default, window 0.1 T_n);
- ``drift_phi0p2``: ``--settle_drift_window_fraction 0.2`` (doubled window);
- ``full``: ``--settle_force_full`` (every point gets the full discard T_full,
  no trend, nominal-power gain reference); followed by ``freq.refine_sweep``
  (``--full-refine-rounds``, default 1) so the reference itself converges.

Fail-closed evidence chain (rev033 review):

- ``run`` first writes an immutable **matrix manifest**
  (``00runs/tmp/drift_validation_<id>.matrix.json``; every expected cell
  with its frequency, variant, directory, rule regime and discard, and
  feasibility; every point with its planned comparisons; the pinned
  commit; the setpoint tables' SHA-256 and model version, read at the
  pinned commit or from ``--setpoints-local-<core>``), then dispatches.
  A first job installs the same bytes at ``<root>/drift_validation_matrix.json``
  (SHA-256-checked, never replaced by a different matrix).
- Each cell job runs the runner and the collector (fatal on failure), the
  reference's refinement, and ``freq.verify_campaign``; the refinement and
  verification exit codes are recorded in ``logs/<cell>_status.json`` and the
  job exits with the verifier's status (a cell job never masks a failure).
- The final job runs ``compare_drift_validation.py`` against the matrix
  (exact coverage, per-cell provenance, aggregate hashes, per-cell
  verification, canonical identity across variants); its REMOTE exit
  status is retrieved and returned: ``run`` / ``compare-remote`` exit 0 only
  when the comparison passes.  Failed cells stay recorded as evidence and
  never turn into overall success.
- ``reconstruct`` rebuilds the matrix manifest of a run dispatched before
  the manifest existed, from the same ``plan`` arguments and the local
  state file (``00runs/tmp/drift_validation_<id>.json``); it refuses unless
  the plan reproduces every dispatched cell exactly.
- ``sync`` copies the remote root to ``00runs/drift-validation-<id>/`` and
  ``compare`` evaluates that tree locally against the matrix.

Matrix points per core and power (80-point campaign grid, 1e-3..10 rad/s):

- ``boundary``: the lowest grid frequency in the drift regime
  (omega >= 120 omega_n with the committed prior);
- ``interior1`` / ``interior2``: the grid points nearest 10x and 100x the
  boundary, capped at the 1 rad/s forcing-cadence split so the full
  reference stays at the 1 s cadence;
- ``top``: 10 rad/s (window doubling only; its full reference is
  opt-in with ``--top-full`` -- 0.05 s forcing events over the full discard,
  1-25 CPU h per cell where feasible at all).

Usage::

    # print the matrix, the job scripts, and the CPU estimate (no gateway)
    python3.12 helpers/paper-rerun/submit_drift_validation.py plan \\
        --campaign-id dv1 --remote-workdir ~/git/SMD-MSRR-dev \\
        --setpoints-1r <remote table> --setpoints-9r <remote table>
    # write the matrix manifest, submit, wait, compare (resumable; state and
    # matrix in 00runs/tmp/); exit status = the comparison's verdict
    python3.12 helpers/paper-rerun/submit_drift_validation.py run ...same flags...
    # a run dispatched before the matrix manifest existed:
    python3.12 helpers/paper-rerun/submit_drift_validation.py reconstruct ...its flags...
    python3.12 helpers/paper-rerun/submit_drift_validation.py compare-remote ...its flags... \\
        --compare-commit <commit> --compare-workdir <remote checkout at that commit>
    #   (the root stays under --remote-workdir; the pinned checkout the cells
    #   run from is never moved while cells are running)
    #   or: sync, then compare (local)

The CPU estimate uses the cost model fitted to the 1760 ``review-2026-09``
case logs (simulation time = c + a * forcing events + b * output intervals,
plus ~3 s build; median error 14-17 %).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from freq import fr_protocol  # noqa: E402
from freq._common import compute_number_of_intervals, format_frequency_key  # noqa: E402
from freq.paths import make_power_tag  # noqa: E402

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import compare_drift_validation as matrix_io  # noqa: E402

DEFAULT_POWERS = (1e-5, 1e-4, 1e-3)
DEFAULT_CORES = ("1r", "9r")
GRID = (1e-3, 10.0, 80)
MIN_CYCLES = 12.0
VARIANTS = ("drift_phi0p1", "drift_phi0p2", "full")

#: Campaign runner arguments the matrix shares with the paper campaign
#: (helpers/paper-rerun/run_paper_rerun_gateway.py frequency jobs).
CAMPAIGN_RUNNER_FLAGS = (
    "--package", "legacy", "--sin_mag_auto",
    "--stop_time_mode", "min_cycles_after_ss", "--min_cycles_after_ss", "12",
    "--reduced_csv_for_collect", "--cleanup_omc_artifacts", "--no-reuse",
    "--omc_timeout_seconds", "0",
)

#: Cost model per core: (c [s], a [s per forcing event], b [s per output
#: interval]); least-squares fit of the review-2026-09 case logs.
COST_MODEL = {"1r": (0.95, 5.23e-4, 1.34e-5), "9r": (1.25, 2.38e-3, 2.68e-5)}
BUILD_OVERHEAD_S = 3.0


@dataclass
class Cell:
    core: str
    power: float
    label: str
    variant: str
    frequency_rad_s: float
    regime: str
    discard_s: float
    stop_time_s: float
    fit_window_s: float
    forcing_step_s: float
    output_intervals: int
    predicted_rows: int
    cpu_h: float
    feasible: bool
    omega_over_omega_n: float
    runner_extra: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.core}-{make_power_tag(self.power)}-{self.label}-{self.variant}"


def campaign_grid() -> np.ndarray:
    return np.logspace(math.log10(GRID[0]), math.log10(GRID[1]), GRID[2])


def matrix_points(core: str, power: float, prior: fr_protocol.GainPrior) -> list[tuple[str, float]]:
    """(label, grid frequency) of the boundary, two interior, and the top point."""
    decision = fr_protocol.settle_decision(prior, core, power)
    kappa = decision.drift_ratio(MIN_CYCLES)
    grid = campaign_grid()
    if kappa is None:
        return []
    threshold = kappa * decision.omega_n_rad_s
    drift = [float(w) for w in grid if 2 * math.pi * MIN_CYCLES / w <= decision.drift_window_s * (1 + 1e-12)]
    if not drift:
        return []
    boundary = drift[0]
    split = 1.0

    def nearest(target: float) -> float:
        candidates = [w for w in drift if w <= split] or drift
        return min(candidates, key=lambda w: abs(math.log(w / target)))

    points = [("boundary", boundary)]
    for label, factor in (("interior1", 10.0), ("interior2", 100.0)):
        w = nearest(min(boundary * factor, split))
        if all(abs(w - existing) > 0 for _, existing in points):
            points.append((label, w))
    top = float(grid[-1])
    if all(abs(top - existing) > 0 for _, existing in points):
        points.append(("top", top))
    assert boundary >= threshold * (1 - 1e-9)
    return points


def _low_power_horizon(power: float) -> tuple[float, float]:
    """(perturbation start, base stop) of the runner's low-power protocol."""
    from freq import runFreqNominalParallel as runner

    protocol = runner.resolve_low_power_auto_protocol(
        power=power,
        low_power_threshold=1e-2,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        ss_time=2000.0,
        stop_time=10000.0,
        stop_time_mode="min_cycles_after_ss",
        min_cycles_after_ss=MIN_CYCLES,
        output_interval_mode="fixed_rate",
        low_power_auto_ss_time_factor=runner.LOW_POWER_AUTO_SS_TIME_FACTOR_DEFAULT,
        low_power_auto_stop_tail_max=runner.LOW_POWER_AUTO_STOP_TAIL_MAX_DEFAULT,
        low_power_auto_min_cycles_after_ss=runner.LOW_POWER_AUTO_MIN_CYCLES_AFTER_SS_DEFAULT,
    )
    return float(protocol["ss_time"]), float(protocol["stop_time"])


def plan_cell(core: str, power: float, label: str, frequency: float, variant: str,
              prior: fr_protocol.GainPrior, *, max_case_rows: float) -> Cell:
    """Discard, window, rows, and CPU estimate of one matrix cell (runner rules)."""
    phi = 0.2 if variant == "drift_phi0p2" else fr_protocol.DEFAULT_DRIFT_WINDOW_FRACTION
    decision = fr_protocol.settle_decision(
        prior, core, power, drift_window_fraction=phi, allow_drift=variant != "full"
    )
    ss_time, base_stop = _low_power_horizon(power)
    point = fr_protocol.point_settle(
        decision, frequency, ss_time=ss_time, base_stop_time=base_stop,
        stop_time_mode="min_cycles_after_ss", min_cycles_after_ss=MIN_CYCLES,
    )
    step = 0.05 if frequency > 1.0 else 1.0  # low-power mixed forcing cadence
    intervals = compute_number_of_intervals(
        frequency, stop_time=point.stop_time_s, output_interval_mode="frequency_scaled",
        output_intervals_per_second=10.0, output_samples_per_period=6.0, output_step_max=50.0,
    )
    intervals = min(intervals, fr_protocol.DEFAULT_MAX_OUTPUT_INTERVALS)
    rows = fr_protocol.predicted_case_rows(
        stop_time_s=point.stop_time_s, ss_time=ss_time,
        number_of_intervals=intervals, forcing_step_s=step,
    )
    events = fr_protocol.forcing_event_count(point.stop_time_s, ss_time, step)
    c, a, b = COST_MODEL[core]
    cpu_s = BUILD_OVERHEAD_S + c + a * events + b * intervals
    extra: list[str] = ["--max_case_rows", f"{max_case_rows:g}"]
    if variant == "drift_phi0p2":
        extra += ["--settle_drift_window_fraction", "0.2"]
    elif variant == "full":
        extra += ["--settle_force_full"]
    return Cell(
        core=core, power=power, label=label, variant=variant, frequency_rad_s=frequency,
        regime=point.regime, discard_s=point.discard_s, stop_time_s=point.stop_time_s,
        fit_window_s=point.fit_window_s, forcing_step_s=step, output_intervals=int(intervals),
        predicted_rows=int(rows), cpu_h=cpu_s / 3600.0, feasible=rows <= max_case_rows,
        omega_over_omega_n=frequency / decision.omega_n_rad_s, runner_extra=extra,
    )


def build_matrix(cores, powers, *, max_case_rows: float, prior_path: str | None = None,
                 top_full: bool = False) -> list[Cell]:
    prior = fr_protocol.load_gain_prior(prior_path)
    cells = []
    for core in cores:
        for power in powers:
            for label, frequency in matrix_points(core, power, prior):
                for variant in VARIANTS:
                    if label == "top" and variant == "full" and not top_full:
                        continue
                    cells.append(plan_cell(core, power, label, frequency, variant, prior,
                                           max_case_rows=max_case_rows))
    return cells


def _guards(remote: str, commit: str | None, allow_dirty: bool) -> list[str]:
    lines = []
    if commit:
        lines.append(
            f'test "$(git -C {remote} rev-parse HEAD)" = {shlex.quote(commit)}'
            f' || {{ echo "remote commit does not match {commit}" >&2; exit 3; }}'
        )
    if not allow_dirty:
        lines.append(
            f'test -z "$(git -C {remote} status --porcelain --untracked-files=no)"'
            f' || {{ echo "tracked remote worktree is dirty" >&2; exit 3; }}'
        )
    return lines


def cell_relative_dir(cell: Cell) -> str:
    """Case directory of a cell relative to the validation root."""
    return f"{cell.core}/power_{make_power_tag(cell.power)}/{cell.label}_{cell.variant}"


def cell_dir(root: str, cell: Cell) -> str:
    return f"{root}/{cell_relative_dir(cell)}"


def _quote(part: str) -> str:
    """Shell-quote a token, keeping bare ``~/...`` paths tilde-expandable."""
    text = str(part)
    if any(ch in text for ch in "$`\"'\\<>|&;(){}*?[]!# \t\n"):
        return shlex.quote(text)
    if "~" in text[1:]:
        return shlex.quote(text)
    return text


def cell_runner_args(cell: Cell, *, table: str | None, directory: str) -> list[str]:
    """Runner arguments of one cell (``table=None``: no setpoint table)."""
    w = repr(float(cell.frequency_rad_s))
    table_args = ["--steady_state_table", table] if table else ["--disable_steady_state_table"]
    return [
        *CAMPAIGN_RUNNER_FLAGS,
        "--core_model", cell.core, "--power", f"{cell.power:.12g}",
        "--freq_min", w, "--freq_max", w, "--num_freq", "1",
        *table_args, "--base_dir", directory, "--n_jobs", "1",
        *cell.runner_extra,
    ]


def cell_command(cell: Cell, *, remote: str, root: str, table: str, python: str,
                 commit: str | None, allow_dirty: bool, full_refine_rounds: int,
                 max_case_rows: float = 1e8) -> str:
    """Gateway script of one cell: run, collect, [refine], verify.

    The runner and collector are fatal (``set -e``).  The reference's
    refinement status and the per-cell verification status are recorded in
    ``logs/<cell>_status.json`` as evidence; the job exits with the
    verifier's status, so an ineligible cell is a failed job -- nothing is
    masked into success (the comparator re-verifies every cell itself).
    """
    directory = cell_dir(root, cell)
    py = f"PYTHONPATH={remote} {python}"
    joined = " ".join(_quote(part) for part in cell_runner_args(cell, table=table, directory=directory))
    log = f"{root}/logs/{cell.key}"
    lines = [
        f"mkdir -p {directory} {root}/logs",
        *_guards(remote, commit, allow_dirty),
        f"{py} -m freq.runFreqNominalParallel {joined} 2>&1 | tee {log}_run.log",
        f"{py} -m freq.collectFreqNominalParallel --results_dir {directory} --n_jobs 1"
        f" 2>&1 | tee {log}_collect.log",
        "refine_rc=0",
    ]
    if cell.variant == "full" and full_refine_rounds > 0:
        # The reference must itself converge; a refinement beyond the row
        # budget is reported infeasible by refine_sweep, not launched.  The
        # reference keeps the variants' amplitude (no amplitude halving) and
        # runs no approval checks: the matrix compares estimators at one
        # amplitude.
        lines.append(
            f"{py} -m freq.refine_sweep --results_dir {directory} --max_rounds {full_refine_rounds}"
            f" '--collect_args=--n_jobs 1' --max_case_rows {max_case_rows:g}"
            f" --no_amplitude_halving --skip_approval_checks"
            f" -- {joined} 2>&1 | tee {log}_refine.log || refine_rc=$?"
        )
    lines += [
        "verify_rc=0",
        f"{py} -m freq.verify_campaign {directory} > {log}_verify.json || verify_rc=$?",
        f"printf '{{\"cell\": \"%s\", \"refine_exit_code\": %s, \"verify_exit_code\": %s}}\\n'"
        f" {cell.key} \"$refine_rc\" \"$verify_rc\" > {log}_status.json",
        'exit "$verify_rc"',
    ]
    return "set -euo pipefail\n" + "\n".join(lines) + "\n"


def matrix_job_command(*, root: str, matrix_text: str, matrix_sha256: str) -> str:
    """First job: install the immutable matrix manifest at the remote root."""
    target = f"{root}/{matrix_io.MATRIX_FILENAME}"
    marker = "DRIFT_VALIDATION_MATRIX_EOF"
    assert marker not in matrix_text
    lines = [
        f"mkdir -p {root}",
        f"tmp={root}/.{matrix_io.MATRIX_FILENAME}.$$",
        f"cat > \"$tmp\" <<'{marker}'\n{matrix_text.rstrip()}\n{marker}",
        f'echo "{matrix_sha256}  $tmp" | sha256sum -c --quiet'
        ' || { echo "matrix manifest bytes corrupted in transfer" >&2; exit 3; }',
        f'if [ -e {target} ]; then cmp -s "$tmp" {target}'
        ' || { echo "a different matrix manifest already exists (immutable)" >&2; exit 3; };'
        f' rm -f "$tmp"; else mv "$tmp" {target}; fi',
    ]
    return "set -euo pipefail\n" + "\n".join(lines) + "\n"


def compare_command(*, remote: str, root: str, python: str, commit: str | None,
                    allow_dirty: bool, matrix_sha256: str | None = None) -> str:
    """Final job: the matrix-checked comparison; its exit status is the verdict."""
    py = f"PYTHONPATH={remote} {python}"
    sha = f" --matrix-sha256 {matrix_sha256}" if matrix_sha256 else ""
    report = f"{root}/{matrix_io.REPORT_BASENAME}"
    lines = [
        *_guards(remote, commit, allow_dirty),
        f"{py} {remote}/helpers/paper-rerun/compare_drift_validation.py --root {root}"
        f" --matrix {root}/{matrix_io.MATRIX_FILENAME}{sha}"
        f" --output {report}.json --markdown {report}.md",
    ]
    return "set -euo pipefail\n" + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Matrix manifest
# ---------------------------------------------------------------------------


def resolve_commit(commit: str | None) -> str:
    """Full 40-character commit of ``commit`` (default: local HEAD)."""
    target = commit or "HEAD"
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", f"{target}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    full = proc.stdout.strip()
    if proc.returncode != 0 or len(full) != 40:
        raise ValueError(f"cannot resolve commit {target!r} in {ROOT}")
    return full


def resolve_setpoint_identity(remote_path: str, *, remote_workdir: str, commit: str,
                              local_path: str | None = None) -> dict[str, Any]:
    """SHA-256 and model version of the table a cell job will consume.

    ``local_path`` hashes a local copy explicitly.  Otherwise a table under
    the remote checkout (``<remote-workdir>/<relpath>``) is read at the
    pinned commit (``git show <commit>:<relpath>``, with its model-version
    sidecar, through ``helpers.setpoint_model_version``).  Anything else
    cannot be resolved locally and is refused (pass ``--setpoints-local-*``).
    """
    import hashlib

    from helpers.setpoint_model_version import sidecar_path, table_model_version

    if local_path:
        table = Path(local_path).expanduser().resolve()
        source = f"local:{table}"
    else:
        prefix = remote_workdir.rstrip("/") + "/"
        if not remote_path.startswith(prefix):
            raise ValueError(
                f"setpoint table {remote_path} is not under the remote checkout "
                f"{remote_workdir}; pass --setpoints-local-<core> to hash a local copy"
            )
        relative = remote_path[len(prefix):]
        scratch = ROOT / "00runs" / "tmp" / "drift_validation_setpoints" / commit
        table = scratch / relative
        table.parent.mkdir(parents=True, exist_ok=True)
        for rel in (relative, str(Path(relative).with_name(sidecar_path(relative).name))):
            proc = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{commit}:{rel}"],
                capture_output=True, check=False,
            )
            if proc.returncode != 0:
                if rel == relative:
                    raise ValueError(f"{relative} is not tracked at {commit}")
                continue
            (scratch / rel).write_bytes(proc.stdout)
        source = f"git:{commit}:{relative}"
    if not table.is_file():
        raise ValueError(f"setpoint table {table} not found")
    return {
        "remote_path": remote_path,
        # Exact bytes, as the run manifests record setpoint_table_sha256.
        "sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
        "model_version": table_model_version(table),
        "source": source,
    }


def build_matrix_manifest(cells: list[Cell], *, campaign_id: str, root: str, commit: str,
                          remote_workdir: str, setpoints: dict[str, dict], allow_dirty: bool,
                          max_case_rows: float, full_refine_rounds: int, top_full: bool,
                          prior_path: str | None = None,
                          reconstruction: dict | None = None) -> dict:
    """Immutable expected-matrix record (sealed by the comparator module)."""
    prior = fr_protocol.load_gain_prior(prior_path)
    points: dict[tuple, dict] = {}
    for cell in cells:
        point_key = (cell.core, make_power_tag(cell.power), cell.label)
        entry = points.setdefault(point_key, {
            "point": f"{cell.core}-{make_power_tag(cell.power)}-{cell.label}",
            "core": cell.core, "power": cell.power, "label": cell.label,
            "frequency_rad_s": cell.frequency_rad_s,
            "frequency_key": format_frequency_key(cell.frequency_rad_s),
            "cells": [], "_variants": {},
        })
        entry["cells"].append(cell.key)
        entry["_variants"][cell.variant] = cell.feasible
    for entry in points.values():
        variants = entry.pop("_variants")
        comparisons = []
        if variants.get("drift_phi0p1") and variants.get("full"):
            comparisons.append(matrix_io.COMPARISON_DRIFT_VS_FULL)
        if variants.get("drift_phi0p1") and variants.get("drift_phi0p2"):
            comparisons.append(matrix_io.COMPARISON_WINDOW_DOUBLING)
        entry["comparisons"] = comparisons
    manifest = {
        "kind": matrix_io.MATRIX_KIND,
        "schema_version": matrix_io.MATRIX_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "root": root,
        "remote_workdir": remote_workdir,
        "commit": commit,
        "allow_dirty": bool(allow_dirty),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "setpoints": setpoints,
        "prior": prior.reference(),
        "grid": {"omega_min_rad_s": GRID[0], "omega_max_rad_s": GRID[1], "points": GRID[2]},
        "max_case_rows": float(max_case_rows),
        "full_refine_rounds": int(full_refine_rounds),
        "top_full": bool(top_full),
        "cells": [
            {
                "key": cell.key,
                "core": cell.core,
                "power": cell.power,
                "label": cell.label,
                "variant": cell.variant,
                "frequency_rad_s": cell.frequency_rad_s,
                "frequency_key": format_frequency_key(cell.frequency_rad_s),
                "directory": cell_relative_dir(cell),
                "regime": cell.regime,
                "discard_s": cell.discard_s,
                "fit_window_s": cell.fit_window_s,
                "forcing_step_s": cell.forcing_step_s,
                "predicted_rows": cell.predicted_rows,
                "cpu_h": cell.cpu_h,
                "feasible": bool(cell.feasible),
                "runner_extra": list(cell.runner_extra),
            }
            for cell in cells
        ],
        "infeasible": [cell.key for cell in cells if not cell.feasible],
        "points": sorted(points.values(), key=lambda p: (p["core"], p["power"], p["frequency_rad_s"])),
        "reconstructed": reconstruction is not None,
    }
    if reconstruction is not None:
        manifest["reconstruction"] = reconstruction
    return matrix_io.seal_matrix(manifest)


def _matrix_path(args: argparse.Namespace) -> Path:
    if getattr(args, "matrix", ""):
        return Path(args.matrix).expanduser()
    return ROOT / "00runs" / "tmp" / f"drift_validation_{args.campaign_id}.matrix.json"


def _setpoint_identities(args: argparse.Namespace) -> dict[str, dict]:
    identities = {}
    for core in args.cores:
        remote = args.setpoints_1r if core == "1r" else args.setpoints_9r
        local = args.setpoints_local_1r if core == "1r" else args.setpoints_local_9r
        if not remote:
            raise ValueError(f"--setpoints-{core} is required")
        identities[core] = resolve_setpoint_identity(
            remote, remote_workdir=args.remote_workdir, commit=args.commit, local_path=local or None
        )
    return identities


def reconstruct(cells: list[Cell], args: argparse.Namespace) -> int:
    """Rebuild the matrix manifest of an already-dispatched run.

    The plan (same arguments as the original ``run``) must reproduce the
    state file exactly: the same feasible cell keys, and for each the same
    frequency, variant, regime, discard, window, and runner arguments.  The
    manifest records the reconstruction (state file SHA-256, job ids and
    states) and is written like a dispatch-time manifest.
    """
    state_path = Path(args.state_file).expanduser() if args.state_file else _state_path(args.campaign_id)
    if not state_path.is_file():
        print(f"ERROR: no state file {state_path}", file=sys.stderr)
        return 2
    state = json.loads(state_path.read_text(encoding="utf-8"))
    jobs = state.get("jobs") or {}
    feasible = {cell.key: cell for cell in cells if cell.feasible}
    problems = []
    for key in sorted(set(jobs) - set(feasible)):
        problems.append(f"state lists {key}, the plan does not")
    for key in sorted(set(feasible) - set(jobs)):
        problems.append(f"plan lists {key}, the state has no job for it")
    compared = ("core", "power", "label", "variant", "frequency_rad_s", "regime",
                "discard_s", "fit_window_s", "runner_extra")
    for key in sorted(set(jobs) & set(feasible)):
        recorded = jobs[key].get("cell") or {}
        planned = asdict(feasible[key])
        for name in compared:
            if recorded.get(name) != planned.get(name):
                problems.append(f"{key}: state {name} {recorded.get(name)!r} != plan {planned.get(name)!r}")
    if problems:
        print("ERROR: the plan does not reproduce the dispatched run:\n  " + "\n  ".join(problems),
              file=sys.stderr)
        return 2
    manifest = build_matrix_manifest(
        cells, campaign_id=args.campaign_id, root=_root(args), commit=args.commit,
        remote_workdir=args.remote_workdir, setpoints=_setpoint_identities(args),
        allow_dirty=args.allow_dirty, max_case_rows=args.max_case_rows,
        full_refine_rounds=args.full_refine_rounds, top_full=args.top_full,
        prior_path=args.fr_prior or None,
        reconstruction={
            "state_file": str(state_path),
            "state_sha256": matrix_io.matrix_file_sha256(state_path),
            "jobs": {key: {"job_id": record.get("job_id"), "state": record.get("state"),
                           "exit_code": record.get("exit_code")}
                     for key, record in sorted(jobs.items())},
        },
    )
    path = _matrix_path(args)
    published = matrix_io.publish_matrix(path, manifest)
    print(f"matrix manifest: {path} (fingerprint {published['fingerprint']}, "
          f"sha256 {matrix_io.matrix_file_sha256(path)})")
    return 0


def summarize(cells: list[Cell]) -> dict[str, Any]:
    feasible = [cell for cell in cells if cell.feasible]
    by_core: dict[str, float] = {}
    refine_by_core: dict[str, float] = {}
    for cell in feasible:
        by_core[cell.core] = by_core.get(cell.core, 0.0) + cell.cpu_h
        if cell.variant == "full":
            # A refinement round reruns the reference with twice the
            # discard: about twice its forcing events.
            refine_by_core[cell.core] = refine_by_core.get(cell.core, 0.0) + 2.0 * cell.cpu_h
    return {
        "cells": len(cells),
        "feasible_cells": len(feasible),
        "infeasible": [cell.key for cell in cells if not cell.feasible],
        "cpu_h_by_core": {core: round(value, 2) for core, value in sorted(by_core.items())},
        "cpu_h_total": round(sum(by_core.values()), 2),
        "longest_cell_h": round(max((cell.cpu_h for cell in feasible), default=0.0), 2),
        "refine_round_worst_case_cpu_h_by_core": {
            core: round(value, 2) for core, value in sorted(refine_by_core.items())
        },
        "note": (
            "Base runs; the worst case adds one refinement round of every full "
            "reference (twice the discard, about twice its base cost)."
        ),
    }


def print_plan(cells: list[Cell], args: argparse.Namespace) -> None:
    print(f"{'cell':44} {'omega':>9} {'w/wn':>6} {'regime':>6} {'T_d (s)':>10} "
          f"{'window (s)':>10} {'rows':>9} {'CPU h':>7} feasible")
    for cell in cells:
        print(f"{cell.key:44} {cell.frequency_rad_s:9.4g} {cell.omega_over_omega_n:6.0f} "
              f"{cell.regime:>6} {cell.discard_s:10.4g} {cell.fit_window_s:10.4g} "
              f"{cell.predicted_rows:9.3g} {cell.cpu_h:7.2f} {cell.feasible}")
    print(json.dumps(summarize(cells), indent=2, sort_keys=True))
    if args.show_commands:
        root = _root(args)
        for cell in cells:
            if cell.feasible:
                print(f"\n[{cell.key}]\n" + cell_command(
                    cell, remote=args.remote_workdir, root=root, table=_table(args, cell.core),
                    python=args.python, commit=args.commit, allow_dirty=args.allow_dirty,
                    full_refine_rounds=args.full_refine_rounds, max_case_rows=args.max_case_rows,
                ).rstrip())
        print("\n[compare]\n" + compare_command(
            remote=args.remote_workdir, root=root, python=args.python, commit=args.commit,
            allow_dirty=args.allow_dirty).rstrip())


def _root(args: argparse.Namespace) -> str:
    return f"{args.remote_workdir.rstrip('/')}/00runs/drift-validation-{args.campaign_id}"


def _local_root(args: argparse.Namespace) -> Path:
    if getattr(args, "local_root", ""):
        return Path(args.local_root).expanduser()
    return ROOT / "00runs" / f"drift-validation-{args.campaign_id}"


def _table(args: argparse.Namespace, core: str) -> str:
    table = args.setpoints_1r if core == "1r" else args.setpoints_9r
    return table or f"<{core} corrected-model setpoint table>"


def _state_path(campaign_id: str) -> Path:
    return ROOT / "00runs" / "tmp" / f"drift_validation_{campaign_id}.json"


def _load_state(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema_version": 1, "jobs": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _client():  # pragma: no cover - gateway I/O
    from helpers.omc_gw.modelica_ssh_client import ModelicaSshClient, _default_agent_command
    from helpers.omc_gw.omc_gw_config import (
        get_config_int, get_config_list, get_config_str, load_user_config,
    )

    config = load_user_config()
    client = ModelicaSshClient(
        host=get_config_str(config, "host"),
        agent_command=_default_agent_command(config),
        ssh_bin=get_config_str(config, "ssh_bin") or "ssh",
        ssh_options=tuple(get_config_list(config, "ssh_options") or ()),
        jobs_root=get_config_str(config, "jobs_root"),
    )
    return client, {
        "workers": get_config_list(config, "workers") or [],
        "max_tasks_per_worker": get_config_int(config, "max_tasks_per_worker", 32) or 32,
        "modelica_bin_dir": get_config_str(config, "modelica_bin_dir"),
    }


def _submit_and_wait(client, options: dict, command: str, *, name: str, workdir: str,
                     poll_seconds: float) -> dict:  # pragma: no cover - gateway I/O
    submitted = client.submit(
        command, workdir=workdir, workers=options["workers"], name=name, tasks=1,
        max_tasks_per_worker=options["max_tasks_per_worker"],
        modelica_bin_dir=options["modelica_bin_dir"], wait_for_slot=True,
        poll_seconds=poll_seconds,
    )
    job_id = str(submitted.get("job_id"))
    final = client.wait(job_id, poll_seconds=poll_seconds)
    detail = client.result(job_id, tail_lines=80)
    return {
        "job_id": job_id,
        "state": str(final.get("state", detail.get("state", "UNKNOWN"))),
        "exit_code": detail.get("exit_code", final.get("exit_code")),
        "stdout_tail": detail.get("stdout_tail"),
        "stderr_tail": detail.get("stderr_tail"),
    }


def _job_succeeded(record: dict) -> bool:
    return record.get("state") == "COMPLETED" and record.get("exit_code") in (0, "0")


def run_compare_job(args: argparse.Namespace, matrix_path: Path, state: dict,
                    state_path: Path) -> int:  # pragma: no cover - gateway I/O
    """Install the matrix remotely (idempotent), run the comparison, and
    return its REMOTE exit status (0 only for a passing report)."""
    client, options = _client()
    root = _root(args)
    matrix_text = matrix_path.read_text(encoding="utf-8")
    matrix_sha = matrix_io.matrix_file_sha256(matrix_path)
    install = _submit_and_wait(
        client, options, matrix_job_command(root=root, matrix_text=matrix_text, matrix_sha256=matrix_sha),
        name=f"drift-validation-{args.campaign_id}-matrix-{matrix_sha[:8]}",
        workdir=f"{args.remote_scratch_dir}/drift-validation-{args.campaign_id}-matrix",
        poll_seconds=args.poll_seconds,
    )
    state.setdefault("matrix_install", []).append(install)
    _save_state(state_path, state)
    if not _job_succeeded(install):
        print(f"ERROR: matrix manifest install failed: {install}", file=sys.stderr)
        return 1
    record = _submit_and_wait(
        client, options,
        compare_command(remote=getattr(args, "compare_workdir", "") or args.remote_workdir,
                        root=root, python=args.python,
                        commit=args.compare_commit or args.commit, allow_dirty=args.allow_dirty,
                        matrix_sha256=matrix_sha),
        name=f"drift-validation-{args.campaign_id}-compare",
        workdir=f"{args.remote_scratch_dir}/drift-validation-{args.campaign_id}-compare",
        poll_seconds=args.poll_seconds,
    )
    state.setdefault("compare", []).append(record)
    _save_state(state_path, state)
    for line in record.get("stderr_tail") or []:
        print(f"  {line}", file=sys.stderr)
    if not _job_succeeded(record):
        print(
            f"drift validation comparison FAILED or INCONCLUSIVE (job {record['job_id']}, "
            f"state {record['state']}, exit {record['exit_code']}); report: "
            f"{root}/{matrix_io.REPORT_BASENAME}.md",
            file=sys.stderr,
        )
        return 1
    print(f"drift validation PASSED; report: {root}/{matrix_io.REPORT_BASENAME}.md")
    return 0


def run(cells: list[Cell], args: argparse.Namespace) -> int:  # pragma: no cover - gateway I/O
    if not all((args.setpoints_1r if core == "1r" else args.setpoints_9r) for core in args.cores):
        print("ERROR: --setpoints-<core> is required for every matrix core", file=sys.stderr)
        return 2
    # The immutable matrix manifest is written BEFORE anything is dispatched.
    manifest = build_matrix_manifest(
        cells, campaign_id=args.campaign_id, root=_root(args), commit=args.commit,
        remote_workdir=args.remote_workdir, setpoints=_setpoint_identities(args),
        allow_dirty=args.allow_dirty, max_case_rows=args.max_case_rows,
        full_refine_rounds=args.full_refine_rounds, top_full=args.top_full,
        prior_path=args.fr_prior or None,
    )
    matrix_path = _matrix_path(args)
    matrix_io.publish_matrix(matrix_path, manifest)
    state_path = _state_path(args.campaign_id)
    state = _load_state(state_path)
    state["matrix"] = {"path": str(matrix_path), "sha256": matrix_io.matrix_file_sha256(matrix_path)}
    _save_state(state_path, state)
    client, options = _client()
    root = _root(args)
    jobs = [cell for cell in cells if cell.feasible]
    for cell in jobs:
        record = state["jobs"].get(cell.key) or {}
        if record.get("state") == "COMPLETED" or record.get("job_id"):
            continue
        command = cell_command(
            cell, remote=args.remote_workdir, root=root, table=_table(args, cell.core),
            python=args.python, commit=args.commit, allow_dirty=args.allow_dirty,
            full_refine_rounds=args.full_refine_rounds, max_case_rows=args.max_case_rows,
        )
        submitted = client.submit(
            command, workdir=f"{args.remote_scratch_dir}/drift-validation-{args.campaign_id}-{cell.key}",
            workers=options["workers"], name=f"drift-validation-{args.campaign_id}-{cell.key}", tasks=1,
            max_tasks_per_worker=options["max_tasks_per_worker"],
            modelica_bin_dir=options["modelica_bin_dir"],
            wait_for_slot=True, poll_seconds=args.poll_seconds,
        )
        state["jobs"][cell.key] = {"job_id": str(submitted.get("job_id")), "state": "SUBMITTED",
                                   "cell": asdict(cell)}
        _save_state(state_path, state)
        print(f"[{cell.key}] submitted {submitted.get('job_id')}", flush=True)
    failures = []
    for cell in jobs:
        record = state["jobs"][cell.key]
        if record.get("state") != "COMPLETED":
            final = client.wait(record["job_id"], poll_seconds=args.poll_seconds)
            detail = client.result(record["job_id"], tail_lines=40)
            record["state"] = str(final.get("state", detail.get("state", "UNKNOWN")))
            record["exit_code"] = detail.get("exit_code", final.get("exit_code"))
            _save_state(state_path, state)
        print(f"[{cell.key}] {record['state']} exit={record['exit_code']}", flush=True)
        if not _job_succeeded(record):
            failures.append(cell.key)
    if failures:
        # Kept as evidence: the comparison still runs and records them.
        print("failed cells: " + ", ".join(failures), file=sys.stderr)
    verdict = run_compare_job(args, matrix_path, state, state_path)
    return 1 if failures or verdict != 0 else 0


def sync(args: argparse.Namespace) -> int:  # pragma: no cover - gateway I/O
    """rsync the remote validation root to ``00runs/drift-validation-<id>/``."""
    from helpers.omc_gw.omc_gw_config import get_config_list, get_config_str, load_user_config

    config = load_user_config()
    host = get_config_str(config, "host")
    ssh = " ".join(shlex.quote(part) for part in
                   [get_config_str(config, "ssh_bin") or "ssh", *(get_config_list(config, "ssh_options") or [])])
    destination = _local_root(args)
    destination.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-az", "--partial", "--info=progress2", "-e", ssh,
           "--exclude", "*.c", "--exclude", "*.o", "--exclude", "*.h", "--exclude", "*.libs",
           "--exclude", "*.makefile", "--exclude", "*_JacA.bin", "--exclude", "*_JacLSJac*.bin",
           f"{host}:{_root(args).rstrip('/')}/", str(destination) + "/"]
    print("$ " + " ".join(shlex.quote(part) for part in cmd))
    return subprocess.run(cmd, check=False).returncode


def compare_local(args: argparse.Namespace) -> int:
    """Run the matrix-checked comparison on a synced local tree."""
    root = _local_root(args)
    report = root / matrix_io.REPORT_BASENAME
    return matrix_io.main([
        "--root", str(root), "--matrix", str(_matrix_path(args)),
        "--output", f"{report}.json", "--markdown", f"{report}.md",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "action", choices=("plan", "run", "reconstruct", "sync", "compare", "compare-remote"),
        help=(
            "plan: print the matrix (no gateway); run: write the matrix manifest, "
            "dispatch, compare, and return the remote comparison's verdict; "
            "reconstruct: rebuild the matrix manifest of an already-dispatched run "
            "from the plan and the local state file; sync: rsync the remote root "
            "locally; compare: matrix-checked comparison of the synced tree; "
            "compare-remote: install the matrix and run the comparison on the gateway"
        ),
    )
    parser.add_argument("--campaign-id", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    parser.add_argument("--remote-workdir", default="~/git/SMD-MSRR-dev")
    parser.add_argument("--remote-scratch-dir", default="~/tmp/SMD2")
    parser.add_argument("--setpoints-1r", default="", help="remote corrected-model 1R table")
    parser.add_argument("--setpoints-9r", default="", help="remote corrected-model 9R table")
    parser.add_argument("--setpoints-local-1r", default="",
                        help="local copy of the 1R table to hash (default: git show at --commit)")
    parser.add_argument("--setpoints-local-9r", default="",
                        help="local copy of the 9R table to hash (default: git show at --commit)")
    parser.add_argument("--cores", default=",".join(DEFAULT_CORES))
    parser.add_argument("--powers", default=",".join(f"{p:g}" for p in DEFAULT_POWERS))
    parser.add_argument("--fr-prior", default="", help="local gain prior for the plan (default: committed)")
    parser.add_argument("--max-case-rows", type=float, default=1e8,
                        help="per-cell row budget; above it a cell is infeasible (default 1e8)")
    parser.add_argument("--full-refine-rounds", type=int, default=1,
                        help="freq.refine_sweep rounds for the full reference (default 1)")
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--commit", default=None, help="remote commit pin (default: local HEAD)")
    parser.add_argument("--compare-commit", default=None,
                        help="compare-remote: commit of the remote checkout that runs the "
                             "comparator (default: --commit)")
    parser.add_argument("--compare-workdir", default="",
                        help="compare-remote: remote checkout that runs the comparator "
                             "(default: --remote-workdir); the validation root stays under "
                             "--remote-workdir, so the pinned checkout need not move")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--top-full", action="store_true",
                        help="also run the full reference at 10 rad/s where feasible (1-25 CPU h per cell)")
    parser.add_argument("--matrix", default="",
                        help="matrix manifest path (default: 00runs/tmp/drift_validation_<id>.matrix.json)")
    parser.add_argument("--state-file", default="",
                        help="reconstruct: state file (default: 00runs/tmp/drift_validation_<id>.json)")
    parser.add_argument("--local-root", default="",
                        help="sync/compare: local tree (default: 00runs/drift-validation-<id>)")
    parser.add_argument("--show-commands", action="store_true", help="plan: print every job script")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.cores = [item.strip() for item in args.cores.split(",") if item.strip()]
    powers = [float(item) for item in args.powers.split(",") if item.strip()]
    if args.action in ("sync",):
        return sync(args)
    if args.action == "compare":
        return compare_local(args)
    try:
        args.commit = resolve_commit(args.commit)
        if args.compare_commit:
            args.compare_commit = resolve_commit(args.compare_commit)
    except ValueError as exc:
        if args.action != "plan":
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    cells = build_matrix(args.cores, powers, max_case_rows=args.max_case_rows,
                         prior_path=args.fr_prior or None, top_full=args.top_full)
    if args.action == "plan":
        print_plan(cells, args)
        return 0
    if args.action == "reconstruct":
        try:
            return reconstruct(cells, args)
        except (ValueError, matrix_io.MatrixError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    if args.action == "compare-remote":
        matrix_path = _matrix_path(args)
        try:
            matrix_io.load_matrix(matrix_path)
        except matrix_io.MatrixError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        state_path = _state_path(args.campaign_id)
        return run_compare_job(args, matrix_path, _load_state(state_path), state_path)
    try:
        return run(cells, args)
    except (ValueError, matrix_io.MatrixError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
