#!/usr/bin/env python3
"""Automated refinement of non-converged frequency-response points.

After a sweep has been run and collected, every point whose fit fails the
collector's two-halves convergence check is rerun with a longer settling
discard, and the sweep is re-collected; this repeats until every point
converges or an explicit limit is reached::

    python3.12 -m freq.refine_sweep --results_dir <dir> \\
        [--max_rounds 3] [--max_wall_s 0] [--collect_args "--plot --n_jobs 16"] \\
        [--no_amplitude_halving] [--halving_min_improvement 2] \\
        [--skip_approval_checks] \\
        -- <the runner arguments of the base sweep>

Per round ``r`` (``freq/refinement.py`` holds the authority rules):

1. read the current aggregate (``FreqResponseResults.csv``); the points with
   ``converged == False`` or ``swing_check == fail`` (realized swing outside
   ``fr_protocol.SWING_BAND`` of the target: the gain prior is not trusted)
   are the round's candidates; a swing failure keeps the discard and
   rescales the amplitude by the measured gain
   (:func:`freq.fr_protocol.rescale_amplitude`);
2. each candidate's next discard is ``max(2 T_d, T_full)``
   (:func:`freq.fr_protocol.refine_discard`: a drift-regime point first
   jumps to the full resonance discard, a full-regime point doubles its
   e-folds), with the fit-window length kept; a drift-regime point whose
   full discard is beyond the row budget doubles within the drift regime
   instead (``escalation = double_drift_fallback``).  Amplitude halving
   (target-swing sweeps; ``--no_amplitude_halving`` disables it): when a
   discard escalation left a point unconverged on the two-halves test
   without reducing its convergence excess ``max(|dG|/tol, |dphi|/tol)``
   by ``--halving_min_improvement`` (default 2), the next round halves its
   amplitude and effective target swing at the same discard
   (``amplitude_halving_v2``); a halving is repeated while it reduces the
   excess by that factor and the discard escalates when it does not
   (:func:`freq.fr_protocol.refinement_action`); never below
   ``target_swing_min_pcm``.  Every plan point records the metrics of the
   case it replaces (``trigger``);
3. a candidate whose rerun would exceed the result-row budget
   (``--max_case_rows``, output grid plus two rows per forcing event) is
   reported ``infeasible`` and not rerun;
4. the runner is invoked with the base sweep's arguments plus
   ``--base_dir <dir>/refine/round_NN --refine_plan <plan>``; it publishes
   the round's immutable sweep request (parent fingerprint, per-point new
   and replaced discards, trigger reasons);
5. the collector re-collects ``<dir>``: each refined key now takes its case
   from the latest round, audited against that round's request, and the
   aggregate labels it (``refined``, ``refine_round``, ``case_source``,
   ``settle_discard_history_s``, ``amplitude_halvings``, the effective
   ``target_swing``);
6. once every point converged, the half-amplitude approval checks the base
   request declares run inside the loop
   (:func:`freq.linearity_check.run_check_stage`; only points without a
   current check are simulated, ``--skip_approval_checks`` leaves them to
   ``freq.linearity_check campaign``); a point whose check fails is steered
   by the v2 linearity branch (:func:`plan_linearity_round`,
   :func:`freq.fr_protocol.linearity_action`): an unsettled pair (either
   run's two-halves excess above 0.25) escalates the discard of both runs;
   a settled gain/phase failure is halved once, then halved again when the
   discrepancy fell by ``--halving_min_improvement`` with its sign kept, or
   restored to the previous amplitude with an escalated discard when it did
   not; a settled H2-only failure is reported.  Every change is re-checked
   in a new check attempt.

Limits (all reported, none silent): ``--max_rounds`` total rounds (default
8: the convergence and linearity levers share the budget),
``--max_wall_s`` elapsed seconds before a new round may start (0: none),
and the per-case row budget.  The report ``<dir>/refine/refine_report.json``
records every round, the final status (``converged``, ``round_limit``,
``wall_limit``, ``infeasible``, ``runner_failed``, ``collect_failed``,
``no_aggregate``, ``approval_checks_failed``), the points still not
converged (``non_converged``) or not approved (``unapproved``), and every
in-loop check stage (``approval_checks``).

Exit status: 0 when every point converged (and, with in-loop checks, the
approval checks pass), 3 when a limit left points unconverged or
unapproved (the report says which), 1 on a runner or collector failure, 2
on invalid input.  The campaign driver runs ``freq.verify_campaign
--exit_policy publication_approved`` afterwards, so an unconverged or
unapproved point fails the job instead of being published.

Rerunning the command resumes: existing rounds are kept, a round whose
results are not yet in the aggregate is re-collected (and, if its cases are
incomplete, rerun with its recorded plan) before a new round starts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import fr_protocol
    from . import linearity_check
    from . import refinement
    from . import sweep_manifest
    from ._common import compute_number_of_intervals, format_frequency_key
except ImportError:  # script-style execution from freq/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from freq import fr_protocol
    from freq import linearity_check
    from freq import refinement
    from freq import sweep_manifest
    from freq._common import compute_number_of_intervals, format_frequency_key

AGGREGATE_CSV = "FreqResponseResults.csv"
AGGREGATE_MANIFEST = "FreqResponseResults.manifest.json"
DEFAULT_MAX_ROUNDS = 8

STATUS_CONVERGED = "converged"
STATUS_ROUND_LIMIT = "round_limit"
STATUS_WALL_LIMIT = "wall_limit"
STATUS_INFEASIBLE = "infeasible"
STATUS_RUNNER_FAILED = "runner_failed"
STATUS_COLLECT_FAILED = "collect_failed"
STATUS_NO_AGGREGATE = "no_aggregate"
STATUS_CHECKS_FAILED = "approval_checks_failed"

EXIT_CONVERGED = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_UNCONVERGED = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_command(cmd: list[str]) -> int:
    """Run one child command (runner or collector); returns its exit code."""
    print("$ " + " ".join(shlex.quote(part) for part in cmd), flush=True)
    return subprocess.run(cmd, check=False).returncode


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        split = argv.index("--")
        own, runner_args = argv[:split], argv[split + 1:]
    else:
        own, runner_args = argv, []
    parser = argparse.ArgumentParser(
        description=(
            "Rerun non-converged frequency-response points with longer "
            "settling discards and re-collect, until every point converges "
            "or a reported limit is reached. Arguments after '--' are the "
            "runner arguments of the base sweep (--base_dir may be omitted)."
        )
    )
    parser.add_argument("--results_dir", required=True,
                        help="Base sweep directory (holds sweep_request.manifest.json).")
    parser.add_argument("--max_rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                        help=f"Maximum total refinement rounds (default: {DEFAULT_MAX_ROUNDS}).")
    parser.add_argument("--max_wall_s", type=float, default=0.0,
                        help="Do not start a new round after this many elapsed seconds (0: no limit).")
    parser.add_argument("--max_case_rows", type=float, default=fr_protocol.DEFAULT_MAX_CASE_ROWS,
                        help=(
                            "Result-row budget per refined case; candidates above it are "
                            f"reported infeasible (default: {fr_protocol.DEFAULT_MAX_CASE_ROWS:g})."
                        ))
    parser.add_argument("--collect_args", default="",
                        help="Extra collector arguments (one shell-quoted string), e.g. \"--plot --n_jobs 16\".")
    parser.add_argument("--python", default=sys.executable,
                        help="Interpreter for the runner and collector (default: this one).")
    parser.add_argument("--dry_run", action="store_true",
                        help="Print the next round's plan and exit without running anything.")
    parser.add_argument(
        "--no_amplitude_halving", dest="amplitude_halving", action="store_false",
        help=(
            "Disable the amplitude-halving rule (discard escalation only; a failed "
            "linearity check is reported, not remedied)."
        ),
    )
    parser.add_argument(
        "--halving_min_improvement", type=float, default=fr_protocol.HALVING_MIN_IMPROVEMENT,
        help=(
            "Factor by which a discard escalation (or a previous halving) must reduce "
            "the convergence excess max(|dG|/tol, |dphi|/tol) to be repeated; below it "
            "the other lever is used (default: "
            f"{fr_protocol.HALVING_MIN_IMPROVEMENT:g})."
        ),
    )
    parser.add_argument(
        "--skip_approval_checks", action="store_true",
        help=(
            "Do not run the half-amplitude approval checks the base request declares "
            "inside the loop (freq.linearity_check campaign runs them separately)."
        ),
    )
    args = parser.parse_args(own)
    if args.max_rounds < 0:
        parser.error("--max_rounds must be >= 0")
    if not math.isfinite(args.max_wall_s) or args.max_wall_s < 0:
        parser.error("--max_wall_s must be finite and >= 0")
    if not math.isfinite(args.max_case_rows) or args.max_case_rows <= 0:
        parser.error("--max_case_rows must be finite and > 0")
    if not math.isfinite(args.halving_min_improvement) or args.halving_min_improvement <= 1.0:
        parser.error("--halving_min_improvement must be finite and > 1")
    return args, runner_args


def _strip_base_dir(runner_args: list[str], results_dir: Path) -> list[str]:
    """Drop ``--base_dir`` (must name the base sweep) from the runner args."""
    out: list[str] = []
    skip = False
    for index, token in enumerate(runner_args):
        if skip:
            skip = False
            continue
        value = None
        if token == "--base_dir":
            if index + 1 >= len(runner_args):
                raise ValueError("--base_dir in the runner arguments lacks a value")
            value = runner_args[index + 1]
            skip = True
        elif token.startswith("--base_dir="):
            value = token.split("=", 1)[1]
        if value is not None:
            if Path(value).expanduser().resolve() != results_dir:
                raise ValueError(
                    f"runner --base_dir {value} is not the refined sweep {results_dir}"
                )
            continue
        if token == "--refine_plan" or token.startswith("--refine_plan="):
            raise ValueError("the runner arguments must not carry --refine_plan")
        out.append(token)
    return out


def _pending_round(results_dir: Path) -> Path | None:
    """Last round directory that has a plan but no published sweep request."""
    root = results_dir / refinement.REFINE_DIRNAME
    if not root.is_dir():
        return None
    indexed = sorted(
        (index, entry)
        for entry in root.iterdir()
        if entry.is_dir()
        and (index := refinement.parse_round_dir_name(entry.name)) is not None
    )
    if not indexed:
        return None
    _index, directory = indexed[-1]
    if (directory / sweep_manifest.SWEEP_REQUEST_FILENAME).exists():
        return None
    if not (directory / refinement.PLAN_FILENAME).is_file():
        return None
    return directory


def load_aggregate(results_dir: Path) -> tuple[list[dict], dict] | None:
    csv_path = results_dir / AGGREGATE_CSV
    manifest_path = results_dir / AGGREGATE_MANIFEST
    if not csv_path.is_file() or not manifest_path.is_file():
        return None
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, csv.Error):
        return None
    return rows, manifest


def _row_bool(row: dict, name: str) -> bool:
    return str(row.get(name, "")).strip().lower() in ("true", "1", "yes")


def _aggregate_round_indexes(manifest: dict) -> set[int]:
    block = manifest.get("refinement") if isinstance(manifest, dict) else None
    rounds = block.get("rounds") if isinstance(block, dict) else None
    return {int(item.get("round")) for item in rounds or [] if isinstance(item, dict)}


def _row_float(row: dict, name: str) -> float:
    try:
        return float(row.get(name))
    except (TypeError, ValueError):
        return math.nan


def row_metrics(row: dict) -> dict:
    """Convergence metrics of one aggregate row (a plan point's ``trigger``)."""
    gain = _row_float(row, "conv_gain_rel_diff")
    phase = _row_float(row, "conv_phase_diff_deg")
    return {
        "conv_gain_rel_diff": gain,
        "conv_phase_diff_deg": phase,
        "operating_point_offset": _row_float(row, "operating_point_offset"),
        "relative_swing": _row_float(row, "relative_swing"),
        "convergence_reason": str(row.get("convergence_reason") or ""),
        "convergence_excess": fr_protocol.convergence_excess(gain, phase),
    }


def _trigger_excess(point: dict | None) -> float:
    """Convergence excess of the case a round replaced (its plan point's
    ``trigger``; NaN for rounds recorded before the metrics were)."""
    trigger = (point or {}).get("trigger")
    if not isinstance(trigger, dict):
        return math.nan
    value = trigger.get("convergence_excess")
    if value is None:
        value = fr_protocol.convergence_excess(
            trigger.get("conv_gain_rel_diff"), trigger.get("conv_phase_diff_deg")
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


class _Budget:
    """Result-row prediction of a refined case (output grid + forcing events)."""

    def __init__(self, request: dict) -> None:
        self.policies = request.get("policies") or {}
        fr_policy = self.policies.get("fr_protocol") or {}
        self.ss_time = float(request["perturbation_start_time_s"])
        self.max_intervals = int(fr_policy.get("max_output_intervals") or 0)

    def rows(self, case: dict, discard: float) -> int:
        current = float(case.get("settle_discard_s") or 0.0)
        freq = float(case["frequency_rad_s"])
        step = float(case.get("forcing_time_step_s") or 0.0)
        new_stop = float(case["stop_time_s"]) + (discard - current)
        intervals = compute_number_of_intervals(
            freq,
            stop_time=new_stop,
            output_interval_mode=str(
                self.policies.get("effective_output_interval_mode", "fixed_rate")
            ),
            output_intervals_per_second=float(
                self.policies.get("output_intervals_per_second", 10.0)
            ),
            output_samples_per_period=float(
                self.policies.get("output_samples_per_period", 6.0)
            ),
            output_step_max=float(self.policies.get("output_step_max_s", 50.0)),
        )
        if self.max_intervals > 0 and step > 0:
            intervals = min(intervals, self.max_intervals)
        return fr_protocol.predicted_case_rows(
            stop_time_s=new_stop,
            ss_time=self.ss_time,
            number_of_intervals=intervals,
            forcing_step_s=step,
        )


def _tolerance_floor_check(request: dict, effective_target: float) -> None:
    """Refuse (``PriorError``) a halving the solver cannot resolve.

    ``tolerance_swing_scaled_v2`` sweeps: the case's own tolerance, or its
    half-amplitude check's, would fall below ``MIN_CASE_SOLVER_TOLERANCE``.
    ``tolerance_swing_scaled_v3`` sweeps (clamped at the floor): the check's
    absolute error scale (its clamped tolerance) would exceed
    ``TOLERANCE_SWING_RESOLUTION`` of the check's swing.
    """
    policy = (request.get("policies") or {}).get("fr_protocol") or {}
    base = (request.get("numerics") or {}).get("tolerance")
    target = policy.get("target_swing") if policy.get("target_swing_rule_active") else None
    if base is None or not target:
        return
    floor = (policy.get("solver_tolerance") or {}).get("case_floor")
    if floor is None:
        fr_protocol.case_solver_tolerance(float(base), float(target), float(effective_target))
        fr_protocol.case_solver_tolerance(float(base), float(target), 0.5 * float(effective_target))
        return
    check_target = 0.5 * float(effective_target)
    check_tolerance, _scale = fr_protocol.case_solver_tolerance(
        float(base), float(target), check_target, floor=float(floor)
    )
    power = float(request.get("power") or 0.0)
    if power > 0:
        ratio = check_tolerance / (check_target * power)
        if ratio > fr_protocol.TOLERANCE_SWING_RESOLUTION * (1.0 + 1e-12):
            raise fr_protocol.PriorError(
                f"the half-amplitude check would run at tolerance {check_tolerance:.3g}, "
                f"an absolute error scale {ratio:.3g} of its swing (above the "
                f"{fr_protocol.TOLERANCE_SWING_RESOLUTION:g} resolution floor)"
            )


def _halving_point(source, case: dict, *, min_pcm: float, trigger_kind: str) -> dict:
    """Amplitude fields of a halving plan point (raises ``PriorError`` at the
    target-swing floor, at the case-tolerance floor of the halved case or of
    its half-amplitude check, or without an effective target swing)."""
    amplitude = float(case["perturbation_amplitude_pcm"])
    target = sweep_manifest.case_target_swing(source.payload["request"], case)
    if target is None:
        raise fr_protocol.PriorError("the case has no effective target swing")
    new_amplitude, new_target = fr_protocol.halve_amplitude(amplitude, target, min_pcm=min_pcm)
    _tolerance_floor_check(source.payload["request"], new_target)
    clamp = sweep_manifest.case_amplitude_clamp(source.payload["request"], case)
    return {
        "perturbation_amplitude_pcm": new_amplitude,
        "previous_amplitude_pcm": amplitude,
        "amplitude_rule": refinement.AMPLITUDE_RULE_HALVE,
        # A max-clamped amplitude stays short of its (halved) target; any
        # other halved amplitude is unclamped (the floor is refused above).
        "amplitude_clamped": "max" if clamp == "max" else None,
        "effective_target_swing": new_target,
        "amplitude_halvings": sweep_manifest.case_amplitude_halvings(case) + 1,
        "halving_trigger": trigger_kind,
    }


def plan_round(
    results_dir: Path,
    base_payload: dict,
    rows: list[dict],
    *,
    max_case_rows: float,
    skip_keys: set[str],
    amplitude_halving: bool = True,
    halving_min_improvement: float = fr_protocol.HALVING_MIN_IMPROVEMENT,
) -> tuple[list[dict], list[dict], list[dict]]:
    """(plan points, infeasible points, failing points) for one round.

    A point fails when it is not converged or when its realized swing is
    outside the band (``swing_check == fail``: the amplitude is rescaled by
    the measured gain toward the case's effective target swing, keeping the
    discard).  An unconverged point escalates its discard, or -- with
    ``amplitude_halving`` on a target-swing sweep -- halves its amplitude
    and effective target swing at the same discard when
    :func:`freq.fr_protocol.refinement_action` says so (a discard escalation
    that did not reduce the convergence excess by ``halving_min_improvement``,
    or a previous halving that did).  Every point records the metrics of the
    case it replaces (``trigger``).
    """
    sources, _rounds = refinement.resolve_case_sources(results_dir, base_payload)
    request = base_payload["request"]
    policies = request.get("policies") or {}
    fr_policy = policies.get("fr_protocol") or {}
    settle = fr_policy.get("settle") or {}
    full_discard = float(settle.get("full_discard_s") or 0.0)
    budget = _Budget(request)
    policy_target = float(fr_policy.get("target_swing") or fr_protocol.DEFAULT_TARGET_SWING)
    min_pcm = float(fr_policy.get("target_swing_min_pcm") or fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM)
    max_pcm = float(fr_policy.get("target_swing_max_pcm") or fr_protocol.DEFAULT_TARGET_SWING_MAX_PCM)
    halving_enabled = bool(amplitude_halving) and bool(fr_policy.get("target_swing_rule_active"))
    non_converged: list[dict] = []
    points: list[dict] = []
    infeasible: list[dict] = []
    for row in rows:
        converged = _row_bool(row, "converged")
        swing_failed = str(row.get("swing_check") or "") == "fail"
        if converged and not swing_failed:
            continue
        freq = float(row["frequency_rad_s"])
        key = format_frequency_key(freq)
        reasons = []
        if not converged:
            reasons.append(str(row.get("convergence_reason") or "not_converged"))
        if swing_failed:
            reasons.append(f"swing_out_of_band(ratio={row.get('swing_ratio')})")
        entry = {
            "frequency_key": key,
            "frequency_rad_s": freq,
            "reason": ";".join(reasons),
            "converged": converged,
            "swing_check": str(row.get("swing_check") or ""),
            "conv_gain_rel_diff": row.get("conv_gain_rel_diff"),
            "conv_phase_diff_deg": row.get("conv_phase_diff_deg"),
            "operating_point_offset": row.get("operating_point_offset"),
            "relative_swing": row.get("relative_swing"),
        }
        non_converged.append(entry)
        source = sources.get(key)
        if source is None:
            infeasible.append({**entry, "why": "point is not a case of the base request"})
            continue
        if key in skip_keys:
            infeasible.append({**entry, "why": "already refused as infeasible"})
            continue
        case = source.case
        if settle.get("rule") != fr_protocol.SETTLE_RULE_PRIOR or case.get("fit_start_s") is None:
            infeasible.append({**entry, "why": "the sweep does not use the prior settle rule"})
            continue
        current = float(case.get("settle_discard_s") or 0.0)
        if current <= 0:
            infeasible.append({**entry, "why": "no settling discard recorded"})
            continue
        metrics = row_metrics(row)
        trigger = {**metrics, "last_action": refinement.source_last_action(source)}
        # Steering (freq/README.md, "Amplitude halving"): which lever the
        # unconverged point pulls next.
        action = fr_protocol.REFINE_ACTION_DISCARD
        trigger_kind = None
        if not converged and not swing_failed and halving_enabled:
            reason = metrics["convergence_reason"]
            action, trigger_kind, improvement = fr_protocol.refinement_action(
                trigger["last_action"],
                previous_excess=_trigger_excess(refinement.source_plan_point(source)),
                current_excess=metrics["convergence_excess"],
                halves_failed=reason_halves_failed(reason),
                min_improvement=halving_min_improvement,
            )
            trigger["improvement"] = improvement
            trigger["min_improvement"] = float(halving_min_improvement)

        # Escalation: max(2 T_d, T_full).  A drift-regime point whose full
        # resonance discard is beyond the row budget (the high-frequency
        # points at 1e-5 MW) falls back to doubling within the drift regime
        # (removes more of the slow sigma_floor mode); both are recorded.
        if converged:
            # Swing-only failure: same discard, rescaled amplitude.
            new_discard = current
            escalation = "keep"
        elif action == fr_protocol.REFINE_ACTION_HALVE:
            new_discard = current
            escalation = "halve_amplitude"
        else:
            new_discard = fr_protocol.refine_discard(current, full_discard)
            escalation = "full" if new_discard > 2.0 * current else "double"
        rows_predicted = budget.rows(case, new_discard)
        if (
            rows_predicted > max_case_rows
            and case.get("settle_regime") == fr_protocol.SETTLE_REGIME_DRIFT
            and escalation == "full"
        ):
            fallback = float(math.ceil(fr_protocol.REFINE_DISCARD_FACTOR * current))
            fallback_rows = budget.rows(case, fallback)
            if fallback_rows <= max_case_rows:
                new_discard, rows_predicted = fallback, fallback_rows
                escalation = "double_drift_fallback"
        if rows_predicted > max_case_rows:
            infeasible.append(
                {
                    **entry,
                    "why": (
                        f"next discard {new_discard:g} s needs {rows_predicted:.4g} "
                        f"result rows > budget {max_case_rows:g}"
                    ),
                    "next_settle_discard_s": new_discard,
                }
            )
            continue
        amplitude = float(case["perturbation_amplitude_pcm"])
        target = sweep_manifest.case_target_swing(source.payload["request"], case)
        amplitude_point = {
            "perturbation_amplitude_pcm": amplitude,
            "previous_amplitude_pcm": amplitude,
            "amplitude_rule": refinement.AMPLITUDE_RULE_KEEP,
            "effective_target_swing": target,
            "amplitude_halvings": sweep_manifest.case_amplitude_halvings(case),
        }
        kept_clamp = sweep_manifest.case_amplitude_clamp(source.payload["request"], case)
        if kept_clamp != sweep_manifest.CLAMP_UNKNOWN:
            amplitude_point["amplitude_clamped"] = kept_clamp
        if swing_failed:
            try:
                swing = float(row.get("relative_swing"))
                new_amplitude, clamped = fr_protocol.rescale_amplitude(
                    amplitude, swing,
                    target_swing=float(target) if target is not None else policy_target,
                    min_pcm=min_pcm, max_pcm=max_pcm,
                )
            except (TypeError, ValueError, fr_protocol.PriorError) as exc:
                infeasible.append({**entry, "why": f"cannot rescale the amplitude: {exc}"})
                continue
            if math.isclose(new_amplitude, amplitude, rel_tol=1e-9):
                infeasible.append(
                    {**entry, "why": f"amplitude already at the {clamped} clamp"}
                )
                continue
            amplitude_point = {
                "perturbation_amplitude_pcm": new_amplitude,
                "previous_amplitude_pcm": amplitude,
                "amplitude_rule": refinement.AMPLITUDE_RULE_RESCALE,
                "amplitude_clamped": clamped,
                "measured_swing": swing,
                "effective_target_swing": target,
                "amplitude_halvings": sweep_manifest.case_amplitude_halvings(case),
            }
        elif action == fr_protocol.REFINE_ACTION_HALVE:
            try:
                amplitude_point = _halving_point(
                    source, case, min_pcm=min_pcm, trigger_kind=str(trigger_kind)
                )
            except fr_protocol.PriorError as exc:
                infeasible.append({**entry, "why": f"cannot halve the amplitude: {exc}"})
                continue
        points.append(
            {
                "frequency_key": key,
                "frequency_rad_s": float(case["frequency_rad_s"]),
                "settle_discard_s": new_discard,
                "previous_settle_discard_s": current,
                "previous_round": int(source.round_index),
                "reason": entry["reason"],
                "escalation": escalation,
                "predicted_result_rows": int(rows_predicted),
                "trigger": trigger,
                **amplitude_point,
            }
        )
    return points, infeasible, non_converged


def reason_halves_failed(reason: str) -> bool:
    """A two-halves (gain or phase) failure in a collector convergence reason."""
    parts = set(str(reason or "").replace(",", ";").split(";"))
    return bool(parts & {"halves_gain", "halves_phase"})


def _escalated_discard(case: dict, full_discard: float, budget: "_Budget",
                       max_case_rows: float) -> tuple[float, int, str]:
    """``(discard, predicted rows, escalation)`` of a discard escalation
    (``max(2 T_d, T_full)``, with the drift-regime doubling fallback);
    raises ``ValueError`` beyond the row budget."""
    current = float(case.get("settle_discard_s") or 0.0)
    new_discard = fr_protocol.refine_discard(current, full_discard)
    escalation = "full" if new_discard > 2.0 * current else "double"
    rows_predicted = budget.rows(case, new_discard)
    if (
        rows_predicted > max_case_rows
        and case.get("settle_regime") == fr_protocol.SETTLE_REGIME_DRIFT
        and escalation == "full"
    ):
        fallback = float(math.ceil(fr_protocol.REFINE_DISCARD_FACTOR * current))
        fallback_rows = budget.rows(case, fallback)
        if fallback_rows <= max_case_rows:
            new_discard, rows_predicted, escalation = fallback, fallback_rows, "double_drift_fallback"
    if rows_predicted > max_case_rows:
        raise ValueError(
            f"next discard {new_discard:g} s needs {rows_predicted:.4g} result rows > "
            f"budget {max_case_rows:g}"
        )
    return new_discard, int(rows_predicted), escalation


def linearity_history(results_dir: Path, base_payload: dict, key: str) -> list[dict]:
    """Earlier linearity decisions of one point, oldest first: the
    ``trigger.linearity`` record of every round plan point that the
    linearity branch produced, with its ``action``.  v1 rounds (halving on
    every failed check) enter as ``halve`` decisions of unknown
    settledness."""
    history: list[dict] = []
    for info in refinement.discover_rounds(results_dir, base_payload):
        for point in info.refinement.get("points") or []:
            if str(point.get("frequency_key")) != key:
                continue
            trigger = point.get("trigger") or {}
            linearity = trigger.get("linearity")
            if not isinstance(linearity, dict):
                continue
            action = point.get("linearity_action")
            if not action and point.get("halving_trigger") == fr_protocol.HALVING_TRIGGER_LINEARITY:
                action = fr_protocol.REFINE_ACTION_HALVE  # v1
            history.append({**linearity, "action": action, "round": info.index})
    return history


def plan_linearity_round(
    results_dir: Path,
    base_payload: dict,
    rows: list[dict],
    failures: list[dict],
    *,
    max_case_rows: float,
    amplitude_halving: bool = True,
    min_improvement: float = fr_protocol.HALVING_MIN_IMPROVEMENT,
) -> tuple[list[dict], list[dict], list[dict]]:
    """(plan points, infeasible points, reported points) for the points whose
    half-amplitude check failed (v2 linearity branch,
    :func:`freq.fr_protocol.linearity_action`).

    Per point: an unsettled pair escalates the discard (``settle``); a
    settled gain/phase failure is halved once and the change of its
    discrepancy decides the next lever -- halve again (amplitude-driven),
    or restore the previous amplitude and escalate the discard
    (settling-limited); a discard lever that reduced the discrepancy is
    repeated.  A settled H2-only failure is reported, not remedied.
    ``amplitude_halving`` False keeps only the discard levers.
    """
    sources, _rounds = refinement.resolve_case_sources(results_dir, base_payload)
    request = base_payload["request"]
    fr_policy = (request.get("policies") or {}).get("fr_protocol") or {}
    min_pcm = float(fr_policy.get("target_swing_min_pcm") or fr_protocol.DEFAULT_TARGET_SWING_MIN_PCM)
    full_discard = float((fr_policy.get("settle") or {}).get("full_discard_s") or 0.0)
    budget = _Budget(request)
    by_key = {format_frequency_key(float(row["frequency_rad_s"])): row for row in rows}
    points: list[dict] = []
    infeasible: list[dict] = []
    reported: list[dict] = []
    for failure in failures:
        key = str(failure["frequency_key"])
        source = sources.get(key)
        row = by_key.get(key, {})
        description = (
            f"linearity_half_amplitude failed ({'+'.join(failure['failed'])}: gain change "
            f"{failure.get('gain_rel_change')}, phase change {failure.get('phase_change_deg')} deg, "
            f"{'settled' if failure.get('settled') else 'unsettled'} pair)"
        )
        entry = {"frequency_key": key, "reason": description}
        if source is None:
            infeasible.append({**entry, "why": "point is not a case of the base request"})
            continue
        case = source.case
        current_discard = float(case.get("settle_discard_s") or 0.0)
        if current_discard <= 0:
            infeasible.append({**entry, "why": "no settling discard recorded"})
            continue
        history = linearity_history(results_dir, base_payload, key)
        action, linearity_trigger, detail = fr_protocol.linearity_action(
            history, failure, min_improvement=min_improvement
        )
        if not amplitude_halving and action in (
            fr_protocol.REFINE_ACTION_HALVE, fr_protocol.REFINE_ACTION_RESTORE
        ):
            action, detail = fr_protocol.REFINE_ACTION_NONE, {**detail, "disabled": action}
        decision = {**failure, "action": action, "linearity_trigger": linearity_trigger,
                    "detail": detail}
        if action == fr_protocol.REFINE_ACTION_NONE:
            reported.append({**entry, "why": f"no remedy ({linearity_trigger})",
                             "decision": decision})
            continue
        amplitude = float(case["perturbation_amplitude_pcm"])
        target = sweep_manifest.case_target_swing(source.payload["request"], case)
        halvings = sweep_manifest.case_amplitude_halvings(case)
        try:
            if action in (fr_protocol.REFINE_ACTION_SETTLE, fr_protocol.REFINE_ACTION_DISCARD):
                new_discard, rows_predicted, escalation = _escalated_discard(
                    case, full_discard, budget, max_case_rows
                )
                amplitude_point = {
                    "perturbation_amplitude_pcm": amplitude,
                    "previous_amplitude_pcm": amplitude,
                    "amplitude_rule": refinement.AMPLITUDE_RULE_KEEP,
                    "effective_target_swing": target,
                    "amplitude_halvings": halvings,
                }
                clamp = sweep_manifest.case_amplitude_clamp(source.payload["request"], case)
                if clamp != sweep_manifest.CLAMP_UNKNOWN:
                    amplitude_point["amplitude_clamped"] = clamp
            elif action == fr_protocol.REFINE_ACTION_HALVE:
                amplitude_point = _halving_point(
                    source, case, min_pcm=min_pcm,
                    trigger_kind=fr_protocol.HALVING_TRIGGER_LINEARITY,
                )
                new_discard, escalation = current_discard, "halve_amplitude"
                rows_predicted = budget.rows(case, new_discard)
                if rows_predicted > max_case_rows:
                    raise ValueError(f"{rows_predicted:.4g} result rows > budget {max_case_rows:g}")
            else:  # restore
                if halvings < 1 or target is None:
                    raise ValueError("no halving to restore")
                new_discard, rows_predicted, escalation = _escalated_discard(
                    case, full_discard, budget, max_case_rows
                )
                clamp = sweep_manifest.case_amplitude_clamp(source.payload["request"], case)
                amplitude_point = {
                    # Exact in binary floating point: undoes an exact halving.
                    "perturbation_amplitude_pcm": 2.0 * amplitude,
                    "previous_amplitude_pcm": amplitude,
                    "amplitude_rule": refinement.AMPLITUDE_RULE_RESTORE,
                    "amplitude_clamped": "max" if clamp == "max" else None,
                    "effective_target_swing": 2.0 * float(target),
                    "amplitude_halvings": halvings - 1,
                }
                escalation = f"restore_amplitude+{escalation}"
        except (ValueError, fr_protocol.PriorError) as exc:
            infeasible.append({**entry, "why": f"cannot {action}: {exc}", "decision": decision})
            continue
        points.append(
            {
                "frequency_key": key,
                "frequency_rad_s": float(case["frequency_rad_s"]),
                "settle_discard_s": float(new_discard),
                "previous_settle_discard_s": current_discard,
                "previous_round": int(source.round_index),
                "reason": description,
                "escalation": escalation,
                "predicted_result_rows": int(rows_predicted),
                "linearity_action": action,
                "linearity_trigger": linearity_trigger,
                "trigger": {
                    **row_metrics(row),
                    "last_action": refinement.source_last_action(source),
                    "linearity": {
                        name: failure.get(name)
                        for name in (
                            "attempt", "kind", "failed", "gain_rel_change", "phase_change_deg",
                            "linearity_excess", "h2_status", "h2_ratio_half_over_reference",
                            "settled", "pair_excess", "convergence", "reference_amplitude_pcm",
                            "reference_round",
                        )
                    },
                    "linearity_detail": detail,
                },
                **amplitude_point,
            }
        )
    return points, infeasible, reported


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report["updated_utc"] = utc_now()
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
    args, runner_args = parse_args(argv)
    results_dir = Path(args.results_dir).expanduser().resolve()
    try:
        runner_args = _strip_base_dir(runner_args, results_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    try:
        base_payload = sweep_manifest.load_sweep_request_manifest(results_dir)
    except sweep_manifest.SweepManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    collect_args = shlex.split(args.collect_args)
    report_path = results_dir / refinement.REFINE_DIRNAME / refinement.REPORT_FILENAME
    report: dict = {
        "schema_version": 1,
        "kind": "frequency-refine-report",
        "results_dir": str(results_dir),
        "base_request_fingerprint": base_payload["fingerprint"],
        "started_utc": utc_now(),
        "limits": {
            "max_rounds": int(args.max_rounds),
            "max_wall_s": float(args.max_wall_s),
            "max_case_rows": float(args.max_case_rows),
            "refine_discard_factor": fr_protocol.REFINE_DISCARD_FACTOR,
            "amplitude_halving": bool(args.amplitude_halving),
            "halving_rule_id": fr_protocol.AMPLITUDE_HALVING_RULE_ID,
            "halving_min_improvement": float(args.halving_min_improvement),
        },
        "runner_args": list(runner_args),
        "collect_args": list(collect_args),
        "rounds": [],
        "approval_checks": [],
        "status": None,
    }
    policy = (base_payload["request"].get("policies") or {}).get("fr_protocol") or {}
    checks_in_loop = (
        linearity_check.CHECK_PURPOSE in (policy.get("approval_checks") or [])
        and not args.skip_approval_checks
    )
    report["limits"]["approval_checks_in_loop"] = bool(checks_in_loop)
    start = time.monotonic()
    runner_base = [args.python, "-m", "freq.runFreqNominalParallel", *runner_args]
    collect_cmd = [
        args.python, "-m", "freq.collectFreqNominalParallel",
        "--results_dir", str(results_dir), *collect_args,
    ]

    def finish(status: str, code: int, **extra) -> int:
        report["status"] = status
        report["finished_utc"] = utc_now()
        report["elapsed_s"] = round(time.monotonic() - start, 3)
        report.update(extra)
        if not args.dry_run:
            _write_report(report_path, report)
        print(f"Refinement status: {status}")
        for point in report.get("non_converged") or []:
            print(
                f"  not converged: {point['frequency_key']} rad/s -- {point['reason']}"
            )
        for point in report.get("unapproved") or []:
            print(f"  not approved: {point['frequency_key']} rad/s -- {point['reason']}")
        for point in report.get("infeasible") or []:
            print(f"  infeasible: {point['frequency_key']} rad/s -- {point['why']}")
        print(f"Refinement report: {report_path}")
        return code

    # Resume, step 1: a round directory holding a plan but no published
    # request (the runner died before publishing) is rerun with its plan.
    pending = _pending_round(results_dir)
    if pending is not None and not args.dry_run:
        print(f"Resuming: round {pending.name} has a plan but no request; rerunning it")
        code = run_command(
            [*runner_base, "--base_dir", str(pending),
             "--refine_plan", str(pending / refinement.PLAN_FILENAME)]
        )
        if code != 0:
            return finish(STATUS_RUNNER_FAILED, EXIT_FAILED, runner_exit_code=code)
    try:
        rounds = refinement.discover_rounds(results_dir, base_payload)
    except refinement.RefinementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    # Resume, step 2: a round whose results are not in the current aggregate
    # is re-collected (and rerun with its recorded plan if incomplete).
    aggregate = load_aggregate(results_dir)
    if rounds and not args.dry_run:
        latest = rounds[-1]
        included = _aggregate_round_indexes(aggregate[1]) if aggregate else set()
        if latest.index not in included:
            print(f"Resuming: round {latest.index} is not in the aggregate; re-collecting")
            if run_command(collect_cmd) != 0:
                plan_path = latest.directory / refinement.PLAN_FILENAME
                if not plan_path.is_file():
                    return finish(STATUS_COLLECT_FAILED, EXIT_FAILED)
                code = run_command(
                    [*runner_base, "--base_dir", str(latest.directory),
                     "--refine_plan", str(plan_path)]
                )
                if code != 0:
                    return finish(STATUS_RUNNER_FAILED, EXIT_FAILED, runner_exit_code=code)
                if run_command(collect_cmd) != 0:
                    return finish(STATUS_COLLECT_FAILED, EXIT_FAILED)
    infeasible_keys: set[str] = set()
    infeasible: list[dict] = []
    round_index = len(rounds) + 1
    while True:
        aggregate = load_aggregate(results_dir)
        if aggregate is None:
            return finish(STATUS_NO_AGGREGATE, EXIT_FAILED)
        rows, _manifest = aggregate
        try:
            points, new_infeasible, non_converged = plan_round(
                results_dir, base_payload, rows,
                max_case_rows=float(args.max_case_rows), skip_keys=infeasible_keys,
                amplitude_halving=bool(args.amplitude_halving),
                halving_min_improvement=float(args.halving_min_improvement),
            )
        except refinement.RefinementError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return finish(STATUS_COLLECT_FAILED, EXIT_INVALID)
        for point in new_infeasible:
            if point["frequency_key"] not in infeasible_keys:
                infeasible_keys.add(point["frequency_key"])
                infeasible.append(point)
        if not non_converged:
            if not checks_in_loop:
                return finish(STATUS_CONVERGED, EXIT_CONVERGED, non_converged=[],
                              infeasible=infeasible)
            # Every point converged: the approval checks the base request
            # declares run inside the loop (only the points without a
            # current check are simulated); a gain or phase failure halves
            # that point's amplitude in the next round.
            try:
                sources, _rounds = refinement.resolve_case_sources(results_dir, base_payload)
                if args.dry_run:
                    _selection, pending = linearity_check.pending_check_keys(
                        results_dir, base_payload, sources, rows
                    )
                    print(json.dumps({"approval_checks_pending": pending}, indent=2,
                                     sort_keys=True))
                    return EXIT_CONVERGED
                if args.max_wall_s > 0 and time.monotonic() - start >= args.max_wall_s:
                    return finish(STATUS_WALL_LIMIT, EXIT_UNCONVERGED, non_converged=[],
                                  infeasible=infeasible)
                record = linearity_check.run_check_stage(
                    results_dir, base_payload, runner_base, runner=run_command,
                    sources=sources, rows=rows,
                )
            except refinement.RefinementError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return finish(STATUS_COLLECT_FAILED, EXIT_INVALID)
            failures = linearity_check.linearity_failures(record)
            report["approval_checks"].append(refinement.json_safe(
                {
                    "finished_utc": utc_now(),
                    "ran": record.get("ran") or [],
                    "status": record.get("status"),
                    "pass": record.get("pass"),
                    "problems": record.get("problems") or [],
                    "points": [
                        {
                            name: point.get(name)
                            for name in ("frequency_key", "attempt", "pass", "gain_rel_change",
                                         "phase_change_deg", "h2_status", "reference_round",
                                         "reference_amplitude_pcm",
                                         "reference_effective_target_swing")
                        }
                        for point in record.get("points") or []
                    ],
                    "gain_or_phase_failures": failures,
                }
            ))
            if record.get("runner_exit_code"):
                return finish(STATUS_RUNNER_FAILED, EXIT_FAILED, non_converged=[],
                              infeasible=infeasible, runner_exit_code=record["runner_exit_code"])
            linearity_check.write_check_report(results_dir, record)
            if record.get("pass"):
                return finish(STATUS_CONVERGED, EXIT_CONVERGED, non_converged=[],
                              infeasible=infeasible)
            points, new_infeasible, reported = [], [], []
            if failures:
                try:
                    points, new_infeasible, reported = plan_linearity_round(
                        results_dir, base_payload, rows, failures,
                        max_case_rows=float(args.max_case_rows),
                        amplitude_halving=bool(args.amplitude_halving),
                        min_improvement=float(args.halving_min_improvement),
                    )
                except refinement.RefinementError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    return finish(STATUS_COLLECT_FAILED, EXIT_INVALID)
            report["approval_checks"][-1]["decisions"] = refinement.json_safe(
                [{"frequency_key": p["frequency_key"], "action": p["linearity_action"],
                  "trigger": p["linearity_trigger"], "escalation": p["escalation"]}
                 for p in points]
                + [{"frequency_key": p["frequency_key"], "action": "none", "why": p["why"]}
                   for p in (*reported, *new_infeasible)]
            )
            infeasible.extend(new_infeasible)
            unapproved = [
                {"frequency_key": point.get("frequency_key"),
                 "reason": "approval check failed" + (
                     f" ({point.get('problems')})" if point.get("problems") else "")}
                for point in record.get("points") or [] if not point.get("pass")
            ] or [{"frequency_key": None, "reason": "; ".join(record.get("problems") or [])}]
            if not points:
                return finish(STATUS_CHECKS_FAILED, EXIT_UNCONVERGED, non_converged=[],
                              unapproved=unapproved, infeasible=infeasible)
            if round_index > args.max_rounds:
                return finish(STATUS_ROUND_LIMIT, EXIT_UNCONVERGED, non_converged=[],
                              unapproved=unapproved, infeasible=infeasible)
        else:
            if args.dry_run:
                print(json.dumps({"round": round_index, "points": points,
                                  "infeasible": new_infeasible}, indent=2, sort_keys=True))
                return EXIT_CONVERGED
            if round_index > args.max_rounds:
                return finish(STATUS_ROUND_LIMIT, EXIT_UNCONVERGED,
                              non_converged=non_converged, infeasible=infeasible)
            if args.max_wall_s > 0 and time.monotonic() - start >= args.max_wall_s:
                return finish(STATUS_WALL_LIMIT, EXIT_UNCONVERGED,
                              non_converged=non_converged, infeasible=infeasible)
            if not points:
                return finish(STATUS_INFEASIBLE, EXIT_UNCONVERGED,
                              non_converged=non_converged, infeasible=infeasible)
        directory = refinement.round_dir(results_dir, round_index)
        plan = refinement.build_plan(results_dir, base_payload, round_index, points)
        plan_path = refinement.write_plan(directory / refinement.PLAN_FILENAME, plan)
        round_record = {
            "round": round_index,
            "directory": os.path.relpath(directory, results_dir),
            "started_utc": utc_now(),
            "points": refinement.json_safe(points),
            "infeasible": refinement.json_safe(new_infeasible),
        }
        report["rounds"].append(round_record)
        _write_report(report_path, report)
        print(
            f"Refinement round {round_index}: {len(points)} point(s) -> "
            f"{directory}"
        )
        round_start = time.monotonic()
        code = run_command(
            [*runner_base, "--base_dir", str(directory), "--refine_plan", str(plan_path)]
        )
        round_record["runner_exit_code"] = code
        if code != 0:
            return finish(STATUS_RUNNER_FAILED, EXIT_FAILED,
                          non_converged=non_converged, infeasible=infeasible)
        code = run_command(collect_cmd)
        round_record["collect_exit_code"] = code
        round_record["wall_s"] = round(time.monotonic() - round_start, 3)
        if code != 0:
            return finish(STATUS_COLLECT_FAILED, EXIT_FAILED,
                          non_converged=non_converged, infeasible=infeasible)
        _write_report(report_path, report)
        round_index += 1


if __name__ == "__main__":
    raise SystemExit(main())
