#!/usr/bin/env python3
"""Refinement rounds of a frequency sweep: plans, discovery, case sources.

A point whose fit fails the collector's two-halves convergence check is
rerun by ``freq.refine_sweep`` with a longer settling discard.  Each rerun
batch is a *refinement round*: a complete sweep directory

    <results_dir>/refine/round_NN/

written by ``freq.runFreqNominalParallel --refine_plan`` with its own
immutable ``sweep_request.manifest.json``.  That request carries a
``refinement`` block naming the parent (base) request fingerprint, the round
number, and, per refined frequency, the new discard, the discard it
replaces, and the failed criteria that triggered it.

Authority chain (no silent overrides):

- the base request defines the case set (frequency keys) and the amplitudes;
- a round (or check) request must carry exactly the base request's parent
  identity (``freq.sweep_manifest.request_identity``: sources, workflow
  Python, Git state, interpreter and OpenModelica versions, model and
  setpoint identity incl. its model version, numerics, every sampling /
  forcing / estimator policy, the case-common overrides); it may only list
  keys of the base request, each with the same per-case estimator settings
  as the case it replaces (``case_identity``: regime, trend order, gain
  reference, window length, forcing cadence, rule discard) and a discard
  that is not shorter than the one it replaces.  The runner applies these
  checks before any simulation (:func:`plan_identity_problems`) and the
  collector and verifier apply them again (:func:`discover_rounds`,
  :func:`resolve_case_sources`);
- the case of a key is taken from the highest round that lists it, else
  from the base directory, and it is audited against THAT round's request;
- every substitution is recorded in the aggregate (``refined``,
  ``refine_round``, ``case_source``, ``settle_discard_history_s``) and in
  the collection manifest's ``refinement`` section.

A plan point may carry the amplitude to apply: ``keep`` (the replaced
case's amplitude), ``measured_gain_rescale_v1`` (a swing outside the band,
rescaled by the measured gain; the measured swing and replaced amplitude are
recorded), ``amplitude_halving_v2`` (exactly half the replaced amplitude and
effective target swing with the replaced discard kept; the trigger --
a stalled discard escalation, a productive previous halving, or a decision
of the linearity branch -- and its metrics are recorded;
``amplitude_halving_v1`` rounds are the same operation),
``amplitude_restore_v2`` (exactly twice the replaced amplitude and target,
undoing one halving, with an escalated discard), or
``half_amplitude_check``.  The last belongs to the approval check runs
(``purpose = linearity_half_amplitude``) written to
``<results_dir>/checks/<purpose>/`` (attempt 1) and
``<results_dir>/checks/<purpose>_attempt_NN/`` (later attempts, after an
amplitude halving) with a ``check`` request block; those are never case
sources of the sweep.  Every plan point also records the effective target
swing and halving count of the case it produces
(``effective_target_swing``, ``amplitude_halvings``) and the metrics of
the case it replaces (``trigger``).  A case below the policy target swing
runs with a proportionally tighter solver tolerance (``solver_tolerance``,
``freq.fr_protocol.case_solver_tolerance``); rounds and checks are refused
when a case records another value.

A malformed round (unreadable request, wrong parent, foreign key, gap in the
round numbering, an amplitude change without a recorded rescale) is a
:class:`RefinementError`: collection and verification fail closed instead of
ignoring it.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    from . import sweep_manifest
except ImportError:  # script-style execution from freq/
    import sweep_manifest

REFINE_DIRNAME = "refine"
CHECKS_DIRNAME = "checks"
PLAN_FILENAME = "refine_plan.json"
REPORT_FILENAME = "refine_report.json"
PLAN_KIND = "frequency-refine-plan"
PLAN_SCHEMA_VERSION = 1
ROUND_DIR_PATTERN = re.compile(r"^round_(\d{2,})$")
#: Suffix of check attempts after the first (``checks/<purpose>`` is
#: attempt 1, the historical location).
CHECK_ATTEMPT_SUFFIX = "_attempt_"

#: Plan purposes: a refinement round (``refine/round_NN``, request block
#: ``refinement``) or an approval check run (``checks/<purpose>``, request
#: block ``check``; not a case source of the sweep).
PURPOSE_REFINE = "refine"
PURPOSE_LINEARITY = "linearity_half_amplitude"
PLAN_PURPOSES = (PURPOSE_REFINE, PURPOSE_LINEARITY)

#: Per-point amplitude rules a plan may carry.
AMPLITUDE_RULE_KEEP = "keep"
AMPLITUDE_RULE_RESCALE = "measured_gain_rescale_v1"
#: Exact halving at the replaced discard (rule ``amplitude_halving_v2``;
#: ``amplitude_halving_v1`` rounds of the 2026-09-28b campaign are the same
#: operation and stay readable).
AMPLITUDE_RULE_HALVE = "amplitude_halving_v2"
AMPLITUDE_RULE_HALVE_V1 = "amplitude_halving_v1"
HALVING_RULES = (AMPLITUDE_RULE_HALVE_V1, AMPLITUDE_RULE_HALVE)
#: Exact doubling that undoes one earlier halving, with an escalated
#: discard (v2 linearity branch: a settling-limited discrepancy).
AMPLITUDE_RULE_RESTORE = "amplitude_restore_v2"
AMPLITUDE_RULE_HALF = "half_amplitude_check"
AMPLITUDE_RULES = (
    AMPLITUDE_RULE_KEEP, AMPLITUDE_RULE_RESCALE, *HALVING_RULES, AMPLITUDE_RULE_RESTORE,
    AMPLITUDE_RULE_HALF,
)



class RefinementError(ValueError):
    """A refinement plan or round is malformed or inconsistent with its parent."""


def round_dir(results_dir: str | os.PathLike[str], round_index: int) -> Path:
    return Path(results_dir) / REFINE_DIRNAME / f"round_{int(round_index):02d}"


def check_dir(results_dir: str | os.PathLike[str], purpose: str, attempt: int = 1) -> Path:
    """Directory of check attempt ``attempt`` (1: ``checks/<purpose>``)."""
    name = str(purpose) if int(attempt) <= 1 else f"{purpose}{CHECK_ATTEMPT_SUFFIX}{int(attempt):02d}"
    return Path(results_dir) / CHECKS_DIRNAME / name


def check_attempt_dirs(results_dir: str | os.PathLike[str], purpose: str) -> list[tuple[int, Path]]:
    """Existing check attempt directories ``[(attempt, dir), ...]`` (sorted).

    Attempts must be numbered 1..N without gaps (:class:`RefinementError`).
    """
    root = Path(results_dir) / CHECKS_DIRNAME
    if not root.is_dir():
        return []
    pattern = re.compile(rf"^{re.escape(str(purpose))}(?:{CHECK_ATTEMPT_SUFFIX}(\d{{2,}}))?$")
    found: list[tuple[int, Path]] = []
    for entry in root.iterdir():
        match = pattern.match(entry.name)
        if entry.is_dir() and match:
            found.append((int(match.group(1)) if match.group(1) else 1, entry))
    found.sort()
    indexes = [index for index, _ in found]
    if indexes and indexes != list(range(1, len(indexes) + 1)):
        raise RefinementError(
            f"check attempts of {purpose} under {root} are not numbered 1..N without gaps "
            f"(found {indexes})"
        )
    return found


def plan_purpose(plan: Mapping[str, Any]) -> str:
    return str(plan.get("purpose") or PURPOSE_REFINE)


def plan_dir(plan: Mapping[str, Any]) -> Path:
    """Canonical directory of a plan's runs (refinement round or check)."""
    parent = plan["parent_results_dir"]
    if plan_purpose(plan) == PURPOSE_REFINE:
        return round_dir(parent, int(plan["round"]))
    return check_dir(parent, plan_purpose(plan), int(plan.get("round") or 1))


def plan_request_key(plan: Mapping[str, Any]) -> str:
    """Sweep-request key that records the plan (``refinement`` / ``check``)."""
    return "refinement" if plan_purpose(plan) == PURPOSE_REFINE else "check"


def parse_round_dir_name(name: str) -> int | None:
    match = ROUND_DIR_PATTERN.match(str(name))
    return int(match.group(1)) if match else None


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RefinementError(f"{label}: not a number ({value!r})") from exc
    if not math.isfinite(number):
        raise RefinementError(f"{label}: must be finite ({value!r})")
    return number


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def build_plan(
    parent_results_dir: str | os.PathLike[str],
    parent_payload: Mapping[str, Any],
    round_index: int,
    points: list[Mapping[str, Any]],
    *,
    purpose: str = PURPOSE_REFINE,
) -> dict[str, Any]:
    """Plan record for one round or check run.

    Points: key, frequency, the discard to apply and the one it replaces,
    the source round, the trigger reason, and optionally the amplitude to
    apply (``perturbation_amplitude_pcm``, with ``previous_amplitude_pcm``
    and ``amplitude_rule``; without it the base amplitude applies).
    """
    reference = sweep_manifest.case_reference(parent_payload)
    records = []
    for point in sorted(points, key=lambda item: float(item["frequency_rad_s"])):
        record = {
            "frequency_key": str(point["frequency_key"]),
            "frequency_rad_s": float(point["frequency_rad_s"]),
            "settle_discard_s": float(point["settle_discard_s"]),
            "previous_settle_discard_s": float(point["previous_settle_discard_s"]),
            "previous_round": int(point["previous_round"]),
            "reason": str(point.get("reason") or ""),
            "escalation": str(point.get("escalation") or ""),
        }
        if point.get("perturbation_amplitude_pcm") is not None:
            record["perturbation_amplitude_pcm"] = float(point["perturbation_amplitude_pcm"])
            record["previous_amplitude_pcm"] = float(point["previous_amplitude_pcm"])
            record["amplitude_rule"] = str(point.get("amplitude_rule") or AMPLITUDE_RULE_KEEP)
            if "amplitude_clamped" in point:
                clamp = point.get("amplitude_clamped")
                record["amplitude_clamped"] = None if clamp is None else str(clamp)
            if point.get("measured_swing") is not None:
                record["measured_swing"] = float(point["measured_swing"])
        if "effective_target_swing" in point:
            target = point.get("effective_target_swing")
            record["effective_target_swing"] = None if target is None else float(target)
        if "amplitude_halvings" in point:
            record["amplitude_halvings"] = int(point.get("amplitude_halvings") or 0)
        for name in ("halving_trigger", "linearity_action", "linearity_trigger"):
            if point.get(name):
                record[name] = str(point[name])
        if point.get("trigger") is not None:
            record["trigger"] = json_safe(point["trigger"])
        records.append(record)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "purpose": str(purpose),
        "parent_results_dir": str(Path(parent_results_dir).resolve()),
        "parent_request_fingerprint": reference["fingerprint"],
        "parent_campaign_id": reference["campaign_id"],
        "round": int(round_index),
        "points": records,
    }


def json_safe(value: Any) -> Any:
    """Plan/request-safe copy: nonfinite floats become ``None``."""
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise RefinementError("refinement plan is not a JSON object")
    if plan.get("kind") != PLAN_KIND or plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise RefinementError(
            f"not a {PLAN_KIND} (schema {PLAN_SCHEMA_VERSION}) document"
        )
    try:
        round_index = int(plan.get("round"))
    except (TypeError, ValueError) as exc:
        raise RefinementError("refinement plan lacks an integer round") from exc
    if round_index < 1:
        raise RefinementError("refinement rounds are numbered from 1")
    if not str(plan.get("parent_request_fingerprint") or "").strip():
        raise RefinementError("refinement plan lacks parent_request_fingerprint")
    if not str(plan.get("parent_results_dir") or "").strip():
        raise RefinementError("refinement plan lacks parent_results_dir")
    if plan_purpose(plan) not in PLAN_PURPOSES:
        raise RefinementError(
            f"unknown plan purpose {plan.get('purpose')!r} (known: {PLAN_PURPOSES})"
        )
    points = plan.get("points")
    if not isinstance(points, list) or not points:
        raise RefinementError("refinement plan lists no points")
    seen: set[str] = set()
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise RefinementError(f"plan point {index} is not an object")
        key = str(point.get("frequency_key") or "").strip()
        if not key or key in seen:
            raise RefinementError(f"plan point {index}: missing or duplicate frequency_key")
        seen.add(key)
        discard = _finite(point.get("settle_discard_s"), f"plan point {key} settle_discard_s")
        previous = _finite(
            point.get("previous_settle_discard_s"),
            f"plan point {key} previous_settle_discard_s",
        )
        _finite(point.get("frequency_rad_s"), f"plan point {key} frequency_rad_s")
        if discard <= 0 or discard < previous:
            raise RefinementError(
                f"plan point {key}: discard {discard:g} s must be > 0 and not "
                f"shorter than the discard it replaces ({previous:g} s)"
            )
        try:
            previous_round = int(point.get("previous_round"))
        except (TypeError, ValueError) as exc:
            raise RefinementError(f"plan point {key}: previous_round missing") from exc
        if previous_round < 0 or (
            plan_purpose(plan) == PURPOSE_REFINE and previous_round >= round_index
        ):
            raise RefinementError(
                f"plan point {key}: previous_round {previous_round} must precede "
                f"round {round_index}"
            )
        if point.get("perturbation_amplitude_pcm") is not None:
            amplitude = _finite(
                point.get("perturbation_amplitude_pcm"),
                f"plan point {key} perturbation_amplitude_pcm",
            )
            previous_amplitude = _finite(
                point.get("previous_amplitude_pcm"),
                f"plan point {key} previous_amplitude_pcm",
            )
            rule = str(point.get("amplitude_rule") or "")
            if amplitude <= 0 or previous_amplitude <= 0:
                raise RefinementError(f"plan point {key}: amplitudes must be > 0")
            if rule == AMPLITUDE_RULE_KEEP and not math.isclose(
                amplitude, previous_amplitude, rel_tol=1e-9, abs_tol=0.0
            ):
                raise RefinementError(
                    f"plan point {key}: amplitude rule 'keep' changes the amplitude"
                )
            if rule == AMPLITUDE_RULE_HALF and not math.isclose(
                amplitude, 0.5 * previous_amplitude, rel_tol=1e-9, abs_tol=0.0
            ):
                raise RefinementError(
                    f"plan point {key}: half-amplitude check must halve the amplitude"
                )
            if rule not in AMPLITUDE_RULES:
                raise RefinementError(f"plan point {key}: unknown amplitude rule {rule!r}")
            if rule == AMPLITUDE_RULE_HALF and plan_purpose(plan) != PURPOSE_LINEARITY:
                raise RefinementError(
                    f"plan point {key}: half amplitude is a linearity check, not a refinement"
                )
            if rule in HALVING_RULES:
                if plan_purpose(plan) != PURPOSE_REFINE:
                    raise RefinementError(
                        f"plan point {key}: amplitude halving is a refinement, not a check"
                    )
                if not math.isclose(amplitude, 0.5 * previous_amplitude, rel_tol=1e-9, abs_tol=0.0):
                    raise RefinementError(
                        f"plan point {key}: amplitude halving must halve the amplitude"
                    )
                if not math.isclose(discard, previous, rel_tol=0.0, abs_tol=1e-6):
                    raise RefinementError(
                        f"plan point {key}: amplitude halving keeps the discard it replaces"
                    )
            if rule == AMPLITUDE_RULE_RESTORE:
                if plan_purpose(plan) != PURPOSE_REFINE:
                    raise RefinementError(
                        f"plan point {key}: amplitude restore is a refinement, not a check"
                    )
                if not math.isclose(amplitude, 2.0 * previous_amplitude, rel_tol=1e-9, abs_tol=0.0):
                    raise RefinementError(
                        f"plan point {key}: amplitude restore must double the amplitude"
                    )
                if not discard > previous + 1e-6:
                    raise RefinementError(
                        f"plan point {key}: amplitude restore escalates the discard"
                    )
        if point.get("effective_target_swing") is not None:
            target = _finite(
                point.get("effective_target_swing"),
                f"plan point {key} effective_target_swing",
            )
            if target <= 0:
                raise RefinementError(f"plan point {key}: effective_target_swing must be > 0")
    return dict(plan)


def write_plan(path: str | os.PathLike[str], plan: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_plan(plan)
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return target


def load_plan(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefinementError(f"cannot read refinement plan {path}: {exc}") from exc
    return validate_plan(payload)


def load_plan_parent(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Load and fingerprint-check the parent (base) request of a plan."""
    parent_dir = Path(str(plan["parent_results_dir"]))
    try:
        payload = sweep_manifest.load_sweep_request_manifest(parent_dir)
    except sweep_manifest.SweepManifestError as exc:
        raise RefinementError(
            f"parent sweep request of the refinement plan is not valid: {exc}"
        ) from exc
    if payload["fingerprint"] != plan["parent_request_fingerprint"]:
        raise RefinementError(
            "refinement plan names a different parent request "
            f"({str(plan['parent_request_fingerprint'])[:12]}...) than "
            f"{parent_dir} holds ({str(payload['fingerprint'])[:12]}...)"
        )
    base_cases = sweep_manifest.case_map(payload)
    for point in plan["points"]:
        key = str(point["frequency_key"])
        case = base_cases.get(key)
        if case is None:
            raise RefinementError(f"plan point {key} is not a case of the parent request")
        if not math.isclose(
            float(case["frequency_rad_s"]), float(point["frequency_rad_s"]),
            rel_tol=1e-12, abs_tol=0.0,
        ):
            raise RefinementError(f"plan point {key}: frequency differs from the parent case")
    return payload


def plan_record(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The ``refinement`` / ``check`` block the run's sweep request carries."""
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "purpose": plan_purpose(plan),
        "parent_request_fingerprint": str(plan["parent_request_fingerprint"]),
        "parent_campaign_id": str(plan.get("parent_campaign_id") or ""),
        "round": int(plan["round"]),
        "points": [dict(point) for point in plan["points"]],
    }


def plan_amplitude(point: Mapping[str, Any]) -> float | None:
    value = point.get("perturbation_amplitude_pcm")
    return float(value) if value is not None else None


# ---------------------------------------------------------------------------
# Discovery (collector / verifier)
# ---------------------------------------------------------------------------


@dataclass
class RoundInfo:
    index: int
    directory: Path
    payload: dict[str, Any] = field(repr=False)
    keys: tuple[str, ...]
    refinement: dict[str, Any] = field(repr=False)

    def summary(self, results_dir: str | os.PathLike[str]) -> dict[str, Any]:
        request_path = self.directory / sweep_manifest.SWEEP_REQUEST_FILENAME
        return {
            "round": self.index,
            "directory": os.path.relpath(self.directory, Path(results_dir)),
            "request_fingerprint": self.payload["fingerprint"],
            "request_campaign_id": self.payload["campaign_id"],
            "request_sha256": _file_sha256(request_path),
            "frequency_keys": list(self.keys),
            "points": [dict(point) for point in self.refinement.get("points") or []],
        }


@dataclass
class CaseSource:
    key: str
    round_index: int
    results_dir: str
    payload: dict[str, Any] = field(repr=False)
    case: dict[str, Any] = field(repr=False)
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def refined(self) -> bool:
        return self.round_index > 0

    def relative_case_dir(self, base_results_dir: str | os.PathLike[str], case_dir_name: str) -> str:
        return os.path.relpath(
            Path(self.results_dir) / case_dir_name, Path(base_results_dir)
        )


def _file_sha256(path: Path) -> str | None:
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _case_discard(case: Mapping[str, Any], payload: Mapping[str, Any]) -> float:
    value = case.get("settle_discard_s")
    if value is not None:
        return float(value)
    start = case.get("fit_start_s")
    if start is not None:
        return float(start) - float(payload["request"]["perturbation_start_time_s"])
    return 0.0


def _history_entry(round_index: int, case: Mapping[str, Any], payload: Mapping[str, Any]) -> dict:
    return {
        "round": int(round_index),
        "settle_discard_s": _case_discard(case, payload),
        "fit_start_s": (
            float(case["fit_start_s"]) if case.get("fit_start_s") is not None else None
        ),
        "stop_time_s": float(case["stop_time_s"]),
        "settle_regime": case.get("settle_regime"),
        "perturbation_amplitude_pcm": float(case["perturbation_amplitude_pcm"]),
        "effective_target_swing": sweep_manifest.case_target_swing(payload["request"], case),
        "amplitude_halvings": sweep_manifest.case_amplitude_halvings(case),
    }


def discover_rounds(
    results_dir: str | os.PathLike[str],
    base_payload: Mapping[str, Any],
    *,
    max_round: int | None = None,
) -> list[RoundInfo]:
    """Validated refinement rounds under ``<results_dir>/refine/`` (sorted).

    ``max_round`` restricts discovery to rounds ``1..max_round`` (the
    runner's pre-simulation check of round ``max_round + 1``, whose own
    directory already holds its plan).
    """
    root = Path(results_dir) / REFINE_DIRNAME
    if not root.is_dir():
        return []
    base_request = base_payload["request"]
    base_cases = sweep_manifest.case_map(base_payload)
    found: list[tuple[int, Path]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        index = parse_round_dir_name(entry.name)
        if index is None:
            continue
        if max_round is not None and index > int(max_round):
            continue
        found.append((index, entry))
    found.sort()
    indexes = [index for index, _ in found]
    if indexes and indexes != list(range(1, len(indexes) + 1)):
        raise RefinementError(
            f"refinement rounds under {root} are not numbered 1..N without gaps "
            f"(found {indexes})"
        )
    rounds: list[RoundInfo] = []
    for index, directory in found:
        try:
            payload = sweep_manifest.load_sweep_request_manifest(directory)
        except sweep_manifest.SweepManifestError as exc:
            raise RefinementError(
                f"refinement round {directory} has no valid sweep request: {exc}"
            ) from exc
        request = payload["request"]
        block = request.get("refinement")
        if not isinstance(block, dict) or str(block.get("purpose") or PURPOSE_REFINE) != PURPOSE_REFINE:
            raise RefinementError(
                f"refinement round {directory}: sweep request carries no refinement block"
            )
        if block.get("parent_request_fingerprint") != base_payload["fingerprint"]:
            raise RefinementError(
                f"refinement round {directory} refines a different parent request"
            )
        if int(block.get("round", -1)) != index:
            raise RefinementError(
                f"refinement round {directory}: request round {block.get('round')!r} "
                f"does not match the directory name"
            )
        differing = sweep_manifest.request_identity_differences(base_request, request)
        if differing:
            raise RefinementError(
                f"refinement round {directory}: request departs from the base "
                "request's parent identity in " + ", ".join(differing)
            )
        cases = sweep_manifest.case_map(payload)
        planned = {str(point.get("frequency_key")) for point in block.get("points") or []}
        if planned != set(cases):
            raise RefinementError(
                f"refinement round {directory}: request cases do not match its plan"
            )
        for key, case in cases.items():
            base_case = base_cases.get(key)
            if base_case is None:
                raise RefinementError(
                    f"refinement round {directory}: case {key} is not in the base request"
                )
            if not math.isclose(
                float(case["frequency_rad_s"]), float(base_case["frequency_rad_s"]),
                rel_tol=1e-12, abs_tol=0.0,
            ):
                raise RefinementError(
                    f"refinement round {directory}: case {key} frequency differs from base"
                )
            point = next(
                item for item in block.get("points") or []
                if str(item.get("frequency_key")) == key
            )
            expected_amplitude = plan_amplitude(point)
            if expected_amplitude is None:
                expected_amplitude = float(base_case["perturbation_amplitude_pcm"])
            if not math.isclose(
                float(case["perturbation_amplitude_pcm"]), expected_amplitude,
                rel_tol=1e-9, abs_tol=1e-15,
            ):
                raise RefinementError(
                    f"refinement round {directory}: case {key} amplitude differs "
                    "from its plan (or the base amplitude)"
                )
        rounds.append(
            RoundInfo(
                index=index,
                directory=directory.resolve(),
                payload=payload,
                keys=tuple(sorted(cases, key=lambda k: float(cases[k]["frequency_rad_s"]))),
                refinement=dict(block),
            )
        )
    return rounds


def resolve_case_sources(
    results_dir: str | os.PathLike[str],
    base_payload: Mapping[str, Any],
    *,
    max_round: int | None = None,
) -> tuple[dict[str, CaseSource], list[RoundInfo]]:
    """Effective source (base or latest round) of every base key.

    Each round case must keep the per-case estimator identity of the case it
    replaces (``sweep_manifest.case_identity``); ``max_round`` as in
    :func:`discover_rounds`.
    """
    base_dir = str(Path(results_dir).resolve())
    base_cases = sweep_manifest.case_map(base_payload)
    sources = {
        key: CaseSource(
            key=key,
            round_index=0,
            results_dir=base_dir,
            payload=dict(base_payload),
            case=dict(case),
            history=[_history_entry(0, case, base_payload)],
        )
        for key, case in base_cases.items()
    }
    rounds = discover_rounds(results_dir, base_payload, max_round=max_round)
    for info in rounds:
        cases = sweep_manifest.case_map(info.payload)
        points = {str(point["frequency_key"]): point for point in info.refinement["points"]}
        for key, case in cases.items():
            previous = sources[key]
            previous_discard = _case_discard(previous.case, previous.payload)
            discard = _case_discard(case, info.payload)
            point = points[key]
            if int(point.get("previous_round", -1)) != previous.round_index:
                raise RefinementError(
                    f"refinement round {info.index}: case {key} claims to replace round "
                    f"{point.get('previous_round')!r}, but its latest source is round "
                    f"{previous.round_index}"
                )
            if not math.isclose(
                float(point.get("previous_settle_discard_s", math.nan)),
                previous_discard, rel_tol=1e-12, abs_tol=1e-6,
            ):
                raise RefinementError(
                    f"refinement round {info.index}: case {key} records a replaced "
                    f"discard of {point.get('previous_settle_discard_s')!r} s, but its "
                    f"previous source discarded {previous_discard:g} s"
                )
            if discard < previous_discard - 1e-6:
                raise RefinementError(
                    f"refinement round {info.index}: case {key} shortens the discard "
                    f"({discard:g} s < {previous_discard:g} s)"
                )
            estimator = sweep_manifest.case_identity_differences(previous.case, case)
            if estimator:
                raise RefinementError(
                    f"refinement round {info.index}: case {key} changes fields "
                    "outside the authorized discard/amplitude set: "
                    + ", ".join(estimator)
                )
            previous_amplitude = float(previous.case["perturbation_amplitude_pcm"])
            amplitude = float(case["perturbation_amplitude_pcm"])
            rule = str(point.get("amplitude_rule") or "")
            if not math.isclose(amplitude, previous_amplitude, rel_tol=1e-9, abs_tol=0.0):
                recorded = point.get("previous_amplitude_pcm")
                if (
                    rule not in (AMPLITUDE_RULE_RESCALE, *HALVING_RULES, AMPLITUDE_RULE_RESTORE)
                    or recorded is None
                    or not math.isclose(float(recorded), previous_amplitude, rel_tol=1e-9)
                ):
                    raise RefinementError(
                        f"refinement round {info.index}: case {key} changes the "
                        "amplitude without a recorded measured-gain rescale, amplitude "
                        "halving, or amplitude restore of its previous source"
                    )
                if rule in HALVING_RULES and (
                    not math.isclose(amplitude, 0.5 * previous_amplitude, rel_tol=1e-9, abs_tol=0.0)
                    or not math.isclose(discard, previous_discard, rel_tol=0.0, abs_tol=1e-6)
                ):
                    raise RefinementError(
                        f"refinement round {info.index}: case {key} is not an exact "
                        "halving of its previous source at the same discard"
                    )
                if rule == AMPLITUDE_RULE_RESTORE and (
                    not math.isclose(amplitude, 2.0 * previous_amplitude, rel_tol=1e-9, abs_tol=0.0)
                    or not discard > previous_discard + 1e-6
                    or sweep_manifest.case_amplitude_halvings(previous.case) < 1
                ):
                    raise RefinementError(
                        f"refinement round {info.index}: case {key} is not an exact "
                        "restore of a halved previous source at a longer discard"
                    )
            elif rule in (*HALVING_RULES, AMPLITUDE_RULE_RESTORE):
                raise RefinementError(
                    f"refinement round {info.index}: case {key} records an amplitude "
                    f"{'halving' if rule in HALVING_RULES else 'restore'} but keeps the "
                    "amplitude of its previous source"
                )
            # Effective target swing: halved by a halving, doubled by a
            # restore, kept otherwise (requests that predate the field fall
            # back to the policy target).
            target_problem = _target_swing_problem(
                previous.payload["request"], previous.case, info.payload["request"], case,
                halved=rule in HALVING_RULES,
                restored=rule == AMPLITUDE_RULE_RESTORE,
            )
            if target_problem:
                raise RefinementError(
                    f"refinement round {info.index}: case {key} {target_problem}"
                )
            tolerance_problem = sweep_manifest.case_tolerance_problem(info.payload["request"], case)
            if tolerance_problem:
                raise RefinementError(
                    f"refinement round {info.index}: case {key} {tolerance_problem}"
                )
            sources[key] = CaseSource(
                key=key,
                round_index=info.index,
                results_dir=str(info.directory),
                payload=info.payload,
                case=dict(case),
                history=[*previous.history, _history_entry(info.index, case, info.payload)],
            )
    return sources, rounds


def _target_swing_problem(
    previous_request: Mapping[str, Any],
    previous_case: Mapping[str, Any],
    request: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    halved: bool,
    restored: bool = False,
) -> str | None:
    """Why a round case's effective target swing / halving count is not the
    one its previous source authorizes (``None``: consistent)."""
    before = sweep_manifest.case_target_swing(previous_request, previous_case)
    after = sweep_manifest.case_target_swing(request, case)
    halvings_before = sweep_manifest.case_amplitude_halvings(previous_case)
    halvings_after = sweep_manifest.case_amplitude_halvings(case)
    if restored:
        if before is None or after is None or not math.isclose(
            float(after), 2.0 * float(before), rel_tol=1e-9, abs_tol=0.0
        ):
            return (
                f"restores the amplitude but records effective target swing {after!r} "
                f"(expected twice {before!r})"
            )
        if halvings_after != halvings_before - 1:
            return (
                f"restores the amplitude but records {halvings_after} halvings "
                f"(previous source: {halvings_before})"
            )
        return None
    if halved:
        if before is None or after is None or not math.isclose(
            float(after), 0.5 * float(before), rel_tol=1e-9, abs_tol=0.0
        ):
            return (
                f"halves the amplitude but records effective target swing {after!r} "
                f"(expected half of {before!r})"
            )
        if halvings_after != halvings_before + 1:
            return (
                f"halves the amplitude but records {halvings_after} halvings "
                f"(previous source: {halvings_before})"
            )
        return None
    if (before is None) != (after is None) or (
        before is not None and not math.isclose(float(after), float(before), rel_tol=1e-9, abs_tol=0.0)
    ):
        return (
            f"changes the effective target swing ({before!r} -> {after!r}) "
            "without an amplitude halving"
        )
    if halvings_after != halvings_before:
        return (
            f"changes the halving count ({halvings_before} -> {halvings_after}) "
            "without an amplitude halving"
        )
    return None


def source_plan_point(source: CaseSource) -> dict[str, Any] | None:
    """Plan point of the round that produced ``source`` (``None``: base case)."""
    if not source.refined:
        return None
    block = source.payload["request"].get("refinement") or {}
    for point in block.get("points") or []:
        if str(point.get("frequency_key")) == source.key:
            return dict(point)
    return None


def source_last_action(source: CaseSource) -> str:
    """What produced ``source``: ``base``, ``discard`` (a longer discard),
    ``halve`` (an amplitude halving), or ``amplitude`` (a measured-gain
    rescale at the same discard); values of ``freq.fr_protocol``
    ``LAST_ACTION_*``."""
    point = source_plan_point(source)
    if point is None:
        return "base"
    if str(point.get("amplitude_rule") or "") in HALVING_RULES:
        return "halve"
    if str(point.get("amplitude_rule") or "") == AMPLITUDE_RULE_RESTORE:
        # A restored amplitude is a new amplitude: its convergence steering
        # starts over with the discard (as after a rescale).
        return "amplitude"
    if float(point.get("settle_discard_s", 0.0)) > float(
        point.get("previous_settle_discard_s", 0.0)
    ) + 1e-6:
        return "discard"
    return "amplitude"


def plan_identity_problems(
    plan: Mapping[str, Any],
    parent_payload: Mapping[str, Any],
    child_request: Mapping[str, Any],
) -> list[str]:
    """Pre-simulation check of a round or check run against its parent.

    ``child_request`` is the request the runner is about to publish.  It
    must carry the parent's request identity, and every case must keep the
    estimator identity of its effective reference case (the latest earlier
    round for a refinement round, the latest round for a check) and name
    that case's round, discard, and amplitude as the ones it replaces.
    Returns the problems found (empty: the run may start).
    """
    problems = [
        f"request {path}"
        for path in sweep_manifest.request_identity_differences(
            parent_payload["request"], child_request
        )
    ]
    purpose = plan_purpose(plan)
    max_round = int(plan["round"]) - 1 if purpose == PURPOSE_REFINE else None
    try:
        sources, _rounds = resolve_case_sources(
            plan["parent_results_dir"], parent_payload, max_round=max_round
        )
    except RefinementError as exc:
        return [*problems, f"parent refinement rounds: {exc}"]
    points = {str(point["frequency_key"]): point for point in plan["points"]}
    for case in child_request.get("cases") or []:
        key = str(case.get("frequency_key"))
        source = sources.get(key)
        point = points.get(key)
        if source is None or point is None:
            problems.append(f"case {key}: not a planned key of the parent request")
            continue
        for path in sweep_manifest.case_identity_differences(source.case, case):
            problems.append(f"case {key}: {path}")
        if int(point.get("previous_round", -1)) != int(source.round_index):
            problems.append(
                f"case {key}: plan replaces round {point.get('previous_round')!r}, "
                f"the effective source is round {source.round_index}"
            )
        if not math.isclose(
            float(point.get("previous_settle_discard_s", math.nan)),
            _case_discard(source.case, source.payload),
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            problems.append(f"case {key}: plan names a different replaced discard")
        previous_amplitude = point.get("previous_amplitude_pcm")
        if previous_amplitude is not None and not math.isclose(
            float(previous_amplitude),
            float(source.case["perturbation_amplitude_pcm"]),
            rel_tol=1e-9,
        ):
            problems.append(f"case {key}: plan names a different replaced amplitude")
    return problems
