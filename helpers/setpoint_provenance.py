"""Shared provenance gating for steady-state setpoint tables.

TASK-20260910-01 P3 (review-2026-09 residual R3): the two steady-state
setpoint consumers read the generator's ``qualified`` convergence verdict
differently. ``transients/run_nonlinear_steps.py:load_setpoints`` refused
unqualified rows, while ``freq/_common.py:load_steady_state_overrides``
selected its row by power and silently dropped every column outside its
allowed override key set -- ``qualified`` included -- so a
``qualified=0`` row could feed the freq steady-state initialization. This
module is the single home for the verdict contract; both consumers
delegate here.

Contract (documented once, applied by both call sites):

- (a) ``qualified`` column ABSENT: the table predates the generator's
  late-window convergence checks. Under the default ``strict`` policy the
  absent column is a refusal (``ValueError`` naming the CSV): no verdict
  means no qualification evidence, so strict runs refuse to initialize
  from such a table instead of inheriting one. Under the
  ``legacy-compatible`` policy (available by explicit selection) a loud
  warning is written to stderr naming the file, and the load proceeds --
  legacy tables keep working (warn-and-proceed). The warning never goes
  to stdout, and the load never fails because of it. Policy selection is
  an explicit ``policy`` parameter on the two consumer loaders -- there
  is no CLI flag; the shipped default is ``strict``.
- (b) column present and the row's value parses to exactly 1: pass.
  The verdict is read with the generator's ``int(float(...))``
  convention after stripping surrounding whitespace, so "1", "1.0",
  " 1 " and "01" all pass.
- (c) column present and the value is missing, blank, unparseable, 0, or
  anything else: raise ``ValueError`` naming the power and the CSV
  path. The meaning contract is the one already set by the transients
  refusal (before this module existed) and by
  ``helpers/paper-rerun/merge_continuation_setpoints.py``:
  ``qualified=0`` marks a row that FAILED the generator's late-window
  convergence checks and was kept only via ``--accept_unconverged``; a
  blank or unparseable value counts as "no verdict"; any parseable
  verdict other than exactly 0 or 1 is out-of-range (the generator
  writes only 0/1). The refusal wording is shared verbatim with that
  convention -- do not fork it.

Branches (b) and (c) are policy-invariant: the policy mode changes only
how branch (a) behaves. Both modes are recorded in run metadata by the
consumers (``setpoint_policy`` plus ``setpoint_policy_exception`` next to
``setpoint_table_path``; see :func:`table_policy_record`), so a run that
consumed a legacy table under an explicitly selected
``legacy-compatible`` policy is visible in its manifests, not only on
stderr (under the shipped ``strict`` default a legacy table refuses
before any record is written).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

#: The generator's convergence-verdict column
#: (``core/init/generateSetpointTable.py:QUALIFIED_COLUMN``).
QUALIFIED_COLUMN = "qualified"

#: Policy modes (TASK-20260911-01 P4, owner decision 2). The shipped
#: default was ``legacy-compatible`` until 2026-09-11, when the owner
#: decision on TASK-20260911-04 flipped the default to ``strict`` and
#: promoted the qualified review-2026-09 campaign tables into
#: ``core/init/``. ``legacy-compatible`` remains fully functional via
#: explicit selection.
POLICY_LEGACY_COMPATIBLE = "legacy-compatible"
POLICY_STRICT = "strict"
DEFAULT_POLICY = POLICY_STRICT
SETPOINT_POLICIES = (POLICY_LEGACY_COMPATIBLE, POLICY_STRICT)


def normalize_policy(policy: "str | None") -> str:
    """Validate a setpoint policy name; ``None`` selects the default mode.

    Returns the canonical mode string. Raises ``ValueError`` naming the
    valid modes for anything else, so a typo cannot silently degrade a
    strict request to warn-and-proceed.
    """
    mode = DEFAULT_POLICY if policy is None else str(policy).strip().lower()
    if mode not in SETPOINT_POLICIES:
        raise ValueError(
            f"Unknown setpoint policy {policy!r}: expected one of "
            f"{list(SETPOINT_POLICIES)}. '{POLICY_STRICT}' (the default) "
            f"refuses tables without a '{QUALIFIED_COLUMN}' column; "
            f"'{POLICY_LEGACY_COMPATIBLE}' warns and proceeds."
        )
    return mode


def handle_missing_verdict_column(
    csv_path: "str | Path",
    *,
    policy: str = DEFAULT_POLICY,
) -> None:
    """Apply branch (a) of the contract -- the only policy-dependent branch.

    ``strict`` (the default): raise ``ValueError`` naming the CSV,
    refusing to load a table that carries no convergence verdicts.
    ``legacy-compatible``: emit the loud stderr warning via
    :func:`warn_unqualified_table` and proceed, byte-identical to the
    pre-policy behavior. Branches (b)/(c) are handled by
    :func:`require_qualified_row` and do not depend on the mode.
    """
    mode = normalize_policy(policy)
    if mode == POLICY_STRICT:
        raise ValueError(
            f"Setpoints table {csv_path} has no '{QUALIFIED_COLUMN}' column "
            "and the strict setpoint policy is active: the table predates "
            "the generator's late-window convergence checks, so none of its "
            "rows carries a convergence verdict and the load is refused. "
            "Point the run at a qualified table or select the "
            f"'{POLICY_LEGACY_COMPATIBLE}' policy explicitly."
        )
    warn_unqualified_table(csv_path)


def table_policy_record(
    csv_path: "str | Path",
    *,
    policy: str = DEFAULT_POLICY,
) -> dict:
    """Run-metadata record for a CONSUMED setpoint table under *policy*.

    Reads only the CSV header. Returns ``{"policy": <mode>,
    "qualified_column_present": <bool>, "legacy_exception": <bool>}``
    where ``legacy_exception`` is True exactly when the consumed table
    carries no ``qualified`` column and the ``legacy-compatible`` policy
    let the load proceed. Under ``strict`` (the shipped default) an
    absent column refuses the load before any record is written, so that
    state never reaches run metadata. Consumers write this next to
    ``setpoint_table_path`` in their manifests / run-params records so a
    legacy-table load under explicitly selected ``legacy-compatible`` is
    visible in run metadata, not only on stderr.
    """
    mode = normalize_policy(policy)
    with open(csv_path, newline="", encoding="utf-8") as handle:
        fieldnames = next(csv.reader(handle), [])
    has_column = QUALIFIED_COLUMN in fieldnames
    return {
        "policy": mode,
        "qualified_column_present": has_column,
        "legacy_exception": (not has_column) and mode == POLICY_LEGACY_COMPATIBLE,
    }


def warn_unqualified_table(csv_path: "str | Path") -> None:
    """Loud stderr notice that *csv_path* predates the verdict column.

    Emitted by both setpoint consumers (``freq._common.
    load_steady_state_overrides`` and
    ``transients.run_nonlinear_steps.load_setpoints``) when the table
    carries no ``qualified`` column: none of its rows carries a
    convergence verdict, so qualified-row gating is skipped for that
    load and the load proceeds.  Routed to stderr (never stdout) so
    batch pipelines keep stdout clean; the caller decides whether to
    continue.
    """
    print(
        "WARNING: setpoints table "
        f"{csv_path} has no 'qualified' column (written before the "
        "generator's late-window convergence checks existed): none of "
        "its rows carries a convergence verdict, so qualified-row "
        "gating is skipped for this load.",
        file=sys.stderr,
        flush=True,
    )


def require_qualified_row(
    row: dict[str, str],
    csv_path: "str | Path",
    power_level: float,
) -> None:
    """Fail closed unless *row* carries a passing convergence verdict.

    The generator (core/init/generateSetpointTable.py) writes ``qualified``
    as an int rendered 0/1 and counts a row as qualified only when the
    verdict parses to exactly 1.  This gate mirrors that convention and is
    stricter than integer truncation: the verdict must read as a number
    exactly equal to 1, so truncated non-integers (e.g. "1.5") are
    rejected instead of being folded to 1, and any parseable verdict
    other than 0 or 1 is rejected as a value the generator cannot have
    written.  ``qualified=0`` keeps its dedicated rejection.  A blank or
    unparseable value (including nan/inf, which have no integer reading)
    counts as "no verdict" and is rejected too, matching the generator's
    own convention that rows without a verdict count as unqualified
    rather than silently inheriting one.
    """
    raw = (row.get(QUALIFIED_COLUMN) or "").strip()
    if raw == "":
        raise ValueError(
            f"Setpoints row for power={power_level} in {csv_path} has a blank "
            "'qualified' value: the table carries the convergence-verdict "
            "column but this row has no verdict, so it cannot be told apart "
            "from a row that failed the late-window checks (qualified=0, "
            "kept only via --accept_unconverged). Refusing to initialize a "
            "production run from it."
        )
    try:
        numeric = float(raw)
        verdict = int(numeric)
    except (ValueError, OverflowError):
        raise ValueError(
            f"Setpoints row for power={power_level} in {csv_path} has an "
            f"unparseable 'qualified' value {raw!r}; refusing to initialize "
            "a production run from a row without a readable convergence "
            "verdict."
        ) from None
    if verdict == 0:
        raise ValueError(
            f"Setpoints row for power={power_level} in {csv_path} is marked "
            "qualified=0: it FAILED the generator's late-window convergence "
            "checks and was kept only via --accept_unconverged, i.e. its "
            "setpoints were averaged from a non-converged steady state. It "
            "cannot initialize a production run; regenerate the table "
            "without --accept_unconverged or point the run at a qualified "
            "table."
        )
    if numeric != 1:
        raise ValueError(
            f"Setpoints row for power={power_level} in {csv_path} has an "
            f"out-of-range 'qualified' value {raw!r}: the generator writes "
            "only qualified=0 (late-window checks FAILED, kept via "
            "--accept_unconverged) or qualified=1 (passed), so this verdict "
            "cannot have come from the generator. Refusing to initialize a "
            "production run from it."
        )
