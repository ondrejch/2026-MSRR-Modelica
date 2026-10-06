#!/usr/bin/env python3
"""Numerical-quality evaluation of recorded fit statistics (owner decision O2).

The review of ``rev014`` split the single ``publication_eligible`` label into
four explicit fields: ``provenance_complete``, ``campaign_complete``,
``numerical_quality_pass``, and ``publication_approved``. This module owns the
numerical-quality half: it reads the fit statistics recorded in a frequency
aggregate table (``FreqResponseResults.csv``) and evaluates them against one
named threshold table, with an explicit waiver-record mechanism.

Owner decision O2 (2026-09-08, default until the owner confirms or replaces
it before the next external release):

- ``R^2 >= 0.95``          -> ``pass``   (numerical_quality_pass = True)
- ``0.80 <= R^2 < 0.95``   -> ``waiver`` (an explicit waiver record covering
  the evaluated scope is required for ``numerical_quality_pass`` = True;
  without one the point stays traceable as ``waiver`` with pass = False)
- ``R^2 < 0.80``           -> ``fail``   (numerical_quality_pass = False; a
  waiver cannot rescue a fail under O2 -- the owner would have to replace
  the threshold table)

Low-fit points are never deleted: the evaluation record carries the observed
minimum, the applied thresholds, and any matching waiver verbatim, so the
0.4396-class points stay available and traceable rather than implicitly
passing.

The decision statistic is the minimum weighted ``R_squared`` over the
aggregate rows (the quantity quoted as ``min_r_squared`` in the rebaseline
evidence); the minimum unweighted ``r_squared_unweighted`` is recorded
alongside when the column exists. No simulation is ever run here: the module
only reads already-recorded aggregates.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

#: Owner decision O2 (2026-09-08, default until the owner confirms or
#: replaces it before the next external release): numerical-quality
#: thresholds for recorded fit statistics, keyed by evidence regime so a
#: future regime can carry its own row without changing the contract.
#: ``pass_at``: statistic >= pass_at evaluates to ``pass``.
#: ``waiver_floor``: waiver_floor <= statistic < pass_at evaluates to
#: ``waiver`` (an explicit waiver record is required for approval).
#: statistic < waiver_floor evaluates to ``fail``.
QUALITY_THRESHOLD_TABLE: dict[str, dict[str, float]] = {
    "frequency": {"pass_at": 0.95, "waiver_floor": 0.80},
}

#: Default evidence regime (frequency-response sweeps and nominal-power
#: time-domain frequency cases all use the frequency fit statistics).
DEFAULT_QUALITY_REGIME = "frequency"

#: Aggregate column the thresholds apply to (the decision statistic).
QUALITY_STATISTIC = "R_squared"

#: Optional context column recorded alongside the decision statistic.
UNWEIGHTED_STATISTIC = "r_squared_unweighted"

QUALITY_STATUS_PASS = "pass"
QUALITY_STATUS_WAIVER = "waiver"
QUALITY_STATUS_FAIL = "fail"
QUALITY_STATUS_NOT_APPLICABLE = "not_applicable"

#: Quality statuses that represent an evaluated fit statistic (a binding or
#: report with ``not_applicable`` carries no fit statistic at all).
EVALUATED_QUALITY_STATUSES = (
    QUALITY_STATUS_PASS,
    QUALITY_STATUS_WAIVER,
    QUALITY_STATUS_FAIL,
)


class QualityError(RuntimeError):
    """Raised when recorded fit statistics cannot be read or evaluated."""


def read_aggregate_fit_statistics(path: str | Path) -> dict | None:
    """Read the fit statistics of one ``FreqResponseResults.csv`` aggregate.

    Returns ``None`` when ``path`` does not exist (the caller decides
    whether that is applicable). Raises :class:`QualityError` when the file
    exists but does not carry a readable ``R_squared`` column, when a row
    value is unreadable, or when a weighted/unweighted value is not finite
    (rev021: ``inf`` is not a usable fit statistic -- the threshold
    comparison ``inf >= pass_at`` would evaluate a degenerate fit as
    ``pass``). A corrupt aggregate must fail loudly, never evaluate as
    quality-less.
    """

    aggregate = Path(path)
    if not aggregate.is_file():
        return None
    with aggregate.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if QUALITY_STATISTIC not in fieldnames:
            raise QualityError(
                f"{aggregate}: fit-statistic column {QUALITY_STATISTIC!r} "
                f"not found in aggregate header {fieldnames!r}"
            )
        weighted: list[float] = []
        unweighted: list[float] = []
        for lineno, row in enumerate(reader, start=2):
            raw = (row.get(QUALITY_STATISTIC) or "").strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise QualityError(
                    f"{aggregate}:{lineno}: unreadable {QUALITY_STATISTIC!r} "
                    f"value {raw!r}"
                ) from exc
            if not math.isfinite(value):
                raise QualityError(
                    f"{aggregate}:{lineno}: non-finite {QUALITY_STATISTIC!r} "
                    f"value {raw!r} (nan/inf cannot pass a quality gate)"
                )
            weighted.append(value)
            raw_unweighted = (row.get(UNWEIGHTED_STATISTIC) or "").strip()
            if raw_unweighted:
                try:
                    value_unweighted = float(raw_unweighted)
                except ValueError as exc:
                    raise QualityError(
                        f"{aggregate}:{lineno}: unreadable "
                        f"{UNWEIGHTED_STATISTIC!r} value {raw_unweighted!r}"
                    ) from exc
                if not math.isfinite(value_unweighted):
                    raise QualityError(
                        f"{aggregate}:{lineno}: non-finite "
                        f"{UNWEIGHTED_STATISTIC!r} value {raw_unweighted!r} "
                        "(nan/inf cannot pass a quality gate)"
                    )
                unweighted.append(value_unweighted)
    if not weighted:
        raise QualityError(f"{aggregate}: no fit-statistic rows recorded")
    statistics: dict = {
        "statistic": QUALITY_STATISTIC,
        "min_r_squared": min(weighted),
        "points": len(weighted),
        "aggregate": str(aggregate),
    }
    if unweighted:
        statistics["min_r_squared_unweighted"] = min(unweighted)
    return statistics


def _matching_waiver(
    waivers, scope: str | None
) -> dict | None:
    """First waiver record covering ``scope`` for the decision statistic.

    A waiver record must carry ``scope`` (the evaluated scope string, e.g.
    the campaign-relative results directory of one sweep) and ``statistic``
    equal to :data:`QUALITY_STATISTIC`. Any further keys (approver, date,
    reason, observed value) ride along verbatim for traceability.
    """

    for waiver in waivers or ():
        if not isinstance(waiver, dict):
            continue
        if waiver.get("scope") != scope:
            continue
        if waiver.get("statistic") != QUALITY_STATISTIC:
            continue
        return waiver
    return None


def evaluate_quality(
    statistics: dict | None,
    *,
    regime: str = DEFAULT_QUALITY_REGIME,
    scope: str | None = None,
    waivers=None,
) -> dict:
    """Evaluate one fit-statistics record against the O2 threshold table.

    ``statistics`` is the record returned by
    :func:`read_aggregate_fit_statistics` (or ``None`` when no aggregate
    exists, which evaluates to :data:`QUALITY_STATUS_NOT_APPLICABLE`).
    ``scope`` names the evaluated unit (e.g. the campaign-relative sweep
    directory) for waiver matching. Returns a deterministic record:

    - ``status``: ``pass`` | ``waiver`` | ``fail`` | ``not_applicable``;
    - ``pass``: True only for ``pass`` and for ``waiver`` covered by a
      matching waiver record; ``None`` when not applicable;
    - ``min_r_squared`` / ``points``: verbatim from the record;
    - ``thresholds``: the applied regime row of the named table;
    - ``waiver``: the matching waiver record or ``None``.

    A waiver only applies inside the ``waiver`` band; a ``fail`` stays a
    fail under O2 regardless of any recorded waiver.
    """

    if statistics is None:
        return {
            "regime": regime,
            "status": QUALITY_STATUS_NOT_APPLICABLE,
            "pass": None,
            "min_r_squared": None,
            "points": None,
            "thresholds": None,
            "waiver": None,
        }
    thresholds = QUALITY_THRESHOLD_TABLE.get(regime)
    if thresholds is None:
        raise QualityError(
            f"no quality thresholds registered for regime {regime!r}; "
            f"known regimes: {sorted(QUALITY_THRESHOLD_TABLE)}"
        )
    min_r2 = float(statistics["min_r_squared"])
    if min_r2 >= thresholds["pass_at"]:
        status = QUALITY_STATUS_PASS
        passed = True
        waiver = None
    elif min_r2 >= thresholds["waiver_floor"]:
        status = QUALITY_STATUS_WAIVER
        waiver = _matching_waiver(waivers, scope)
        passed = waiver is not None
    else:
        status = QUALITY_STATUS_FAIL
        passed = False
        waiver = None
    return {
        "regime": regime,
        "status": status,
        "pass": passed,
        "min_r_squared": min_r2,
        "points": statistics.get("points"),
        "thresholds": {
            "pass_at": thresholds["pass_at"],
            "waiver_floor": thresholds["waiver_floor"],
        },
        "waiver": waiver,
    }


def aggregate_quality_pass(reports: list[dict], key: str = "numerical_quality_pass") -> bool | None:
    """Aggregate per-report quality booleans into one verdict.

    Reports whose quality is not applicable carry ``None`` and are excluded;
    the aggregate is ``True`` only when at least one report is applicable and
    every applicable report passed, ``False`` when any applicable report
    failed, and ``None`` when no report carries a fit statistic at all.
    """

    applicable = [report.get(key) for report in reports if report.get(key) is not None]
    if not applicable:
        return None
    return all(applicable)
