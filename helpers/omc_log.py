"""Classify OpenModelica check/translate log segments.

A success phrase such as ``Check of X completed successfully`` must not
mask an error diagnostic in the same segment. Three historical copies of
this predicate accepted the success phrase alone; this module is the
single implementation.
"""

from __future__ import annotations

import re

_SUCCESS = re.compile(
    r'"?Check of (?P<target>.+?) completed successfully'
)
_ERROR_LINE = re.compile(r"(?i)^(?:\[\s*)?error:")


def has_error_diagnostic(segment: str) -> bool:
    """True when *segment* contains an OpenModelica error diagnostic.

    Matches lines whose first token is ``Error:`` (optional leading
    bracket from some omc front-end formats). Warnings are not errors.
    """

    for line in segment.splitlines():
        stripped = line.strip()
        if _ERROR_LINE.match(stripped):
            return True
    return False


def check_completed_successfully(segment: str, target: str) -> bool:
    """True only when the named check succeeded and no error is present.

    ``target`` is the Modelica class name printed in the success phrase
    (quoted or unquoted). A success phrase together with an ``Error:``
    line is a failure: the phrase must not mask a diagnostic.
    """

    if not segment or not target:
        return False
    success = re.search(
        rf'"?Check of {re.escape(target)} completed successfully',
        segment,
    )
    if success is None:
        return False
    return not has_error_diagnostic(segment)


# ---------------------------------------------------------------------------
# Runtime -override acceptance (physics review 2026-09-27, finding B1)
# ---------------------------------------------------------------------------

_OVERRIDE_NOT_FOUND = re.compile(
    r"override variable name not found in model:\s*(?P<key>[^\s\"']+)"
)
_OVERRIDE_NOT_POSSIBLE = re.compile(
    r"not possible to override the following quantity:\s*(?P<key>[^\s\"']+)"
)

#: Keys a runner may pass that OMC legitimately refuses at runtime because
#: they are STRUCTURAL (evaluated at translation): the kinetics ``initMode``
#: enumerations the transient runner requests (the trims then initialize the
#: kinetics FixedStart at n = n_0 with equilibrium precursors, which is the
#: same steady state) and the ``heatLossEnabled`` table column (an
#: if-equation switch; the shipped power tables carry its default, 0).
STRUCTURAL_OVERRIDE_SUFFIXES: tuple[str, ...] = (".initMode", "heatLossEnabled")


def rejected_overrides(output: str) -> dict[str, list[str]]:
    """Runtime ``-override`` keys OMC reported as absent or not overridable.

    Returns ``{"not_found": [...], "not_overridable": [...]}`` (deduplicated,
    in first-seen order)."""

    def _uniq(pattern: re.Pattern[str]) -> list[str]:
        seen: list[str] = []
        for m in pattern.finditer(output or ""):
            key = m.group("key").rstrip(".,;")
            if key not in seen:
                seen.append(key)
        return seen

    return {
        "not_found": _uniq(_OVERRIDE_NOT_FOUND),
        "not_overridable": _uniq(_OVERRIDE_NOT_POSSIBLE),
    }


def check_overrides_applied(
    output: str,
    *,
    label: str,
    structural_suffixes: tuple[str, ...] = STRUCTURAL_OVERRIDE_SUFFIXES,
) -> list[str]:
    """Fail closed when a runtime override was silently dropped.

    Before the physics review 2026-09-27 the 9R ``Tmix_0`` and
    ``TF*_0_regions[1]`` table overrides were rejected by OMC in every run
    (only an informational log line), so the plenum and region 1 started from
    the wrong state. Any "not found" key, and any "not possible to override"
    key that is not a documented structural key, raises ``RuntimeError``
    naming the keys. A fatal assertion violation in the log
    (:func:`fatal_assert_violations`) raises too. Returns the tolerated
    structural keys (for logging)."""

    # rev033 review: the same post-simulation check also refuses a run whose
    # log records a fatal (error-level) assertion violation -- the pinned
    # gateway build exits 0 after one, so the exit status alone would let
    # such a run through every runner that calls this check.
    fatal = fatal_assert_violations(output)
    if fatal:
        raise RuntimeError(
            f"{label}: the simulation logged fatal assertion violation(s): "
            + "; ".join(fatal[:3])
            + ". The run is rejected even when the executable exited 0."
        )
    rejected = rejected_overrides(output)
    tolerated = [
        key for key in rejected["not_overridable"]
        if any(key.endswith(sfx) for sfx in structural_suffixes)
    ]
    fatal_nop = [key for key in rejected["not_overridable"] if key not in tolerated]
    if rejected["not_found"] or fatal_nop:
        raise RuntimeError(
            f"{label}: OMC silently dropped runtime overrides -- not found: "
            f"{rejected['not_found'] or 'none'}; not overridable: "
            f"{fatal_nop or 'none'}. The run would start from a state the "
            "setpoint table did not request; fix the key or the model binding."
        )
    return tolerated


# ---------------------------------------------------------------------------
# Runtime assertion violations (rev033 review)
# ---------------------------------------------------------------------------
# The pinned gateway build (OpenModelica v1.27.0-cmake) logs a violated
# error-level assertion -- e.g. a QA gate's ``when terminal()`` horizon guard
# -- as a multi-line ``LOG_ASSERT | error |`` record but still exits 0 and
# prints "The simulation finished successfully." (the local 1.27.1 build
# exits nonzero). The exit status alone therefore cannot decide whether a
# run was rejected; the log record must be read too.
#
# Info-level violation records (review 2026-10-01, H4). During a solver step
# omc 1.27 logs a violated assert at ``LOG_ASSERT | info`` and holds it. If the
# step turns out to contain an event, the runtime prints "Found event,
# previous asserts are ignored." and drops it, because the assert was then
# evaluated at a point the solver rejects. If the step held no event, it
# prints ``LOG_ASSERT | error | No event found, but assert was triggered.
# Throwing now!`` and aborts the run. An assert written INSIDE ``when time >=
# t then ... end when;`` is evaluated only during event iteration, so it
# always takes the first path: logged at info level, ignored, exit 0. The
# SegmentedMSR QA checks used that form until H4 and could not fail. They now
# latch the condition into a discrete Boolean inside the when-clause and
# assert the Boolean outside it; a failed check then reaches the error path
# on the next event-free step, or at the latest as a direct error-level
# record at the final time.
#
# Info-level violation records stay NON-fatal on their own. omc also uses
# that level on correct runs: for the variable min-constraint diagnostics of
# a precursor inventory decaying through zero (30 such records in
# 00runs/segmented-msr-validation/p4/stag1_dnp.log, exit 0, no verdict line)
# and for every assert it later drops with "previous asserts are ignored".
# Every production runner (startup, freq, transients and the setpoint
# generator, lumped and segmented) reaches this function through
# check_overrides_applied, so treating info-level records as fatal would
# reject correct runs. The fatal signal is the runtime's own error-level
# verdict: a direct error-level violation, or the "assert was triggered.
# Throwing now!" record, which promotes the held info-level violation(s) of
# the latest logged time -- the evaluation it re-raises.
#
# Log shape (review follow-up m1 #8). Each verdict covers every violation
# held since the previous verdict, so "previous asserts are ignored" clears
# the whole held list. Every verdict recorded so far (omc 1.27.0-cmake and
# 1.27.1 run logs under 00runs/) is a single-line LOG_ASSERT header, so only
# header lines are scanned for it. Held violations are grouped by their
# logged time as a number, not as the raw string, so "5" and "5.000000"
# name the same evaluation.

_LOG_ASSERT_LEVEL = re.compile(r"LOG_ASSERT\s*\|\s*(\w+)\s*\|")
_ASSERT_VIOLATED = re.compile(r"(?i)assertion\s+has\s+been\s+violated")
#: The runtime's verdict on held info-level violations (see above).
_ASSERTS_IGNORED = re.compile(r"(?i)previous\s+asserts\s+are\s+ignored")
_ASSERT_THROWN = re.compile(r"(?i)assert\s+was\s+triggered")
_VIOLATION_TIME = re.compile(r"(?i)violated\s+(?:during\s+initialization\s+)?at\s+time\s+(\S+)")
#: Non-fatal LOG_ASSERT levels: warning-level assertions (deliberate
#: warn-only paths) and info-level solver min-constraint diagnostics.
NONFATAL_ASSERT_LEVELS = frozenset({"warning", "info"})


def _violation_time(line: str) -> "float | str | None":
    """Logged time of a violation line, as a float when it parses."""

    when = _VIOLATION_TIME.search(line)
    if not when:
        return None
    raw = when.group(1).rstrip(".,;:")
    try:
        return float(raw)
    except ValueError:
        return raw


def fatal_assert_violations(output: str) -> list[str]:
    """Lines reporting violated assertions above warning/info level.

    omc 1.27 writes multi-line ``LOG_ASSERT`` records whose first line
    carries the level; the violation text follows on continuation lines.
    The legacy single-line format (no level header) counts unless the line
    itself says ``warning``.

    An info-level violation is held until the runtime decides it: "previous
    asserts are ignored" drops it (non-fatal), while an error-level "No
    event found, but assert was triggered. Throwing now!" record re-raises
    the latest evaluation, and the held violation lines of the latest
    logged time are then reported as fatal (the header line itself when
    none is held). The pinned gateway build may exit 0 after that record,
    so the record must count on its own.
    """

    hits: list[str] = []
    held: list[tuple["float | str | None", str]] = []  # (logged time, violation line)
    level: "str | None" = None
    for line in output.splitlines():
        level_match = _LOG_ASSERT_LEVEL.search(line)
        if level_match:
            level = level_match.group(1).lower()
            text = line[level_match.end():]
            if _ASSERTS_IGNORED.search(text):
                held.clear()
            elif level not in NONFATAL_ASSERT_LEVELS and _ASSERT_THROWN.search(text):
                latest = held[-1][0] if held else None
                hits.extend([ln for t, ln in held if t == latest] or [text.strip()])
                held.clear()
            continue
        if not _ASSERT_VIOLATED.search(line):
            continue
        if level is None:
            if "warning" in line.lower():
                continue
        elif level in NONFATAL_ASSERT_LEVELS:
            if level == "info":
                held.append((_violation_time(line), line.strip()))
            continue
        hits.append(line.strip())
    return hits


def run_rejected(returncode: int, output: str) -> bool:
    """True when a simulation run failed: nonzero exit OR a fatal assertion.

    The one definition of "the executable rejected the run" for runners and
    tests alike, so a build that exits 0 after a fatal assertion cannot turn
    a refusal into a pass.
    """

    return returncode != 0 or bool(fatal_assert_violations(output))
