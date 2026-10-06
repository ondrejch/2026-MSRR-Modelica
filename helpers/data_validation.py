"""Shared, typed validation for the MSRR YAML decks under ``data/``.

The JSON Schemas in ``data/schema/`` are the reviewable contract; this
module is the executable form of the same requirements. The loaders
(:mod:`helpers.plant_config`, :mod:`helpers.scenario_config`) call these
helpers so every rejected deck fails with a repo-relative file label and
a dotted field path.

Error convention
----------------

Every rejection raises :class:`DataValidationError` (a :class:`ValueError`
subclass, so existing ``except ValueError`` call sites keep working) with a
message of the form::

    <repo-relative file>: <dotted.path>[<index>]: <problem>

for example::

    data/plants/msrr/cores/r9.yaml: zone_end.value[1]: region index 12 exceeds n_regions (9)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, NoReturn

# Units that appear in the committed decks. A new unit means a deliberate
# contract extension: extend this set AND data/schema/quantity.schema.json
# together.
SUPPORTED_UNITS = frozenset(
    {
        "1",
        "1/s",
        "1/K",
        "1/m",
        "1/(m2.s)",
        "1/(cm2.s)",
        "b",
        "J",
        "J/(kg.K)",
        "W",
        "W/(m.K)",
        "W/(m2.K)",
        "W/K",
        "degC",
        "kg/m3",
        "kg/s",
        "m",
        "m2",
        "m3",
        "m3/s",
        "pcm",
        "rad/s",
        "s",
    }
)

# Units whose values must be non-negative in every leaf. Deliberately NOT
# here: "1" (dimensionless shares may be signed in principle), "1/K"
# (feedback coefficients are negative), "pcm" and "rad/s" (schedule
# amplitudes may be signed).
NONNEGATIVE_UNITS = frozenset(
    {
        "1/s",
        "1/m",
        "1/(m2.s)",
        "1/(cm2.s)",
        "J",
        "J/(kg.K)",
        "W",
        "W/(m.K)",
        "W/(m2.K)",
        "W/K",
        "b",
        "kg/m3",
        "kg/s",
        "m",
        "m2",
        "m3",
        "m3/s",
        "s",
    }
)

# Quantity leaf keys that carry a temperature. Plant decks store
# temperatures in degC (lumped native); the segmented emitter converts
# to K. Any quantity under one of these keys must use unit "degC".
TEMPERATURE_LEAF_KEYS = frozenset(
    {
        "TF1",
        "TF1_regions",
        "TF2",
        "TF2_regions",
        "TG",
        "TG_regions",
        "T_0",
        "Tinf",
        "Tmix_0",
        "Tp_0",
        "TpIn_0",
        "TpOut_0",
        "TsIn_0",
        "TsOut_0",
        "heat_loss_tinf",
    }
)

ABSOLUTE_ZERO_DEGC = -273.15

_QUANTITY_KEYS = frozenset({"value", "unit", "doc", "source", "provenance"})
_RULE_KEYS = frozenset({"rule", "doc", "source"})


class DataValidationError(ValueError):
    """A deck failed validation; the message names the file and field."""


def fail(label: str, path: str, message: str) -> NoReturn:
    """Raise a path-specific, actionable deck error."""

    raise DataValidationError(f"{label}: {path}: {message}")


def coerce_number(value: Any) -> Any:
    """YAML 1.1 can load ``2.4916e4`` as a string; coerce numeric leaves."""

    if isinstance(value, str):
        try:
            if any(ch in value for ch in ".eE"):
                return float(value)
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    if isinstance(value, list):
        return [coerce_number(item) for item in value]
    return value


def _number(value: Any, label: str, path: str) -> float:
    """Return ``value`` as a finite float or fail with a precise message."""

    if isinstance(value, bool):
        fail(label, path, f"boolean {value!r} is not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        coerced = coerce_number(value)
        if isinstance(coerced, bool) or not isinstance(coerced, (int, float)):
            fail(label, path, f"not a number: {value!r}")
        number = float(coerced)
    else:
        fail(label, path, f"not a number: {value!r} ({type(value).__name__})")
    if not math.isfinite(number):
        fail(label, path, f"value {number} is not finite (NaN/inf are not physical)")
    return number


def _numeric_leaves(value: Any, label: str, path: str):
    """Yield ``(leaf_path, number)`` for a scalar or homogeneous nested list."""

    if isinstance(value, list):
        if not value:
            fail(label, path, "array must contain at least one number")
        nested = any(isinstance(item, list) for item in value)
        scalars = any(not isinstance(item, list) for item in value)
        if nested and scalars:
            fail(label, path, "array mixes numbers and nested arrays (homogeneous nesting required)")
        for index, item in enumerate(value):
            yield from _numeric_leaves(item, label, f"{path}[{index}]")
        return
    yield path, _number(value, label, path)


def _check_text_field(node: Mapping[str, Any], label: str, path: str, field: str) -> None:
    if field in node:
        text = node[field]
        if not isinstance(text, str) or not text.strip():
            fail(label, f"{path}.{field}", f"must be a non-empty string, got {text!r}")


def check_quantity(
    node: Any,
    label: str,
    path: str,
    *,
    require_provenance: bool = False,
) -> list[tuple[str, float]]:
    """Validate a quantity mapping ``{value, unit[, doc][, source]}``.

    Returns the finite numeric leaves as ``(path, number)`` pairs so
    callers can apply further coupled checks. ``require_provenance``
    additionally demands non-empty ``doc`` and ``source`` strings. A
    present key that plausibly misspells a missing vocabulary key (or an
    unknown key that plausibly misspells any vocabulary key) is named as
    a suspected misspelling in the rejection message.
    """

    if not isinstance(node, Mapping):
        fail(label, path, f"must be a quantity mapping {{value, unit}}, got {type(node).__name__}")
    keys = set(node)
    missing = sorted({"value", "unit"} - keys)
    if missing:
        hints = [
            f"'{key}' looks like a misspelling of '{_typoed_quantity_key(key)}'"
            for key in sorted(keys)
            if _typoed_quantity_key(key) in missing
        ]
        fail(
            label,
            path,
            f"must be a quantity {{value, unit}}; missing {missing}"
            f" (keys present: {sorted(keys)})"
            + (f"; {'; '.join(hints)}" if hints else ""),
        )
    unknown = sorted(keys - _QUANTITY_KEYS)
    if unknown:
        hints = [
            f"'{key}' looks like a misspelling of '{_typoed_quantity_key(key)}'"
            for key in unknown
            if _typoed_quantity_key(key) is not None
        ]
        fail(
            label,
            path,
            f"unknown quantity key(s) {unknown}; allowed keys: {sorted(_QUANTITY_KEYS)}"
            + (f"; {'; '.join(hints)}" if hints else ""),
        )
    for field in ("doc", "source"):
        _check_text_field(node, label, path, field)
    if require_provenance:
        for field in ("doc", "source"):
            if field not in node:
                fail(
                    label,
                    f"{path}.{field}",
                    f"missing required provenance field (quantity '{path}' must"
                    " carry both 'doc' and 'source')",
                )
    if "provenance" in node and not isinstance(node["provenance"], Mapping):
        fail(
            label,
            f"{path}.provenance",
            f"must be a mapping of structured provenance fields, got"
            f" {type(node['provenance']).__name__}",
        )
    value = node["value"]
    if isinstance(value, bool):
        fail(label, f"{path}.value", "boolean is not a number")
    leaves = list(_numeric_leaves(value, label, f"{path}.value"))
    unit = node["unit"]
    if not isinstance(unit, str):
        fail(label, f"{path}.unit", f"must be a string, got {unit!r}")
    local_key = path.rsplit(".", 1)[-1] if path else ""
    if local_key in TEMPERATURE_LEAF_KEYS and unit != "degC":
        fail(
            label,
            f"{path}.unit",
            f"temperature field '{local_key}' must use unit 'degC' (plant convention), got {unit!r}",
        )
    if unit not in SUPPORTED_UNITS:
        fail(
            label,
            f"{path}.unit",
            f"unsupported unit {unit!r}; supported units: {sorted(SUPPORTED_UNITS)}",
        )
    if unit in NONNEGATIVE_UNITS:
        for leaf_path, number in leaves:
            if number < 0.0:
                fail(
                    label,
                    leaf_path,
                    f"negative value {number!r} is not physical for unit {unit!r}",
                )
    if unit == "degC":
        for leaf_path, number in leaves:
            if number <= ABSOLUTE_ZERO_DEGC:
                fail(
                    label,
                    leaf_path,
                    f"temperature {number!r} degC is at or below absolute zero",
                )
    return leaves


def check_rule_node(node: Any, label: str, path: str) -> None:
    """Validate a symbolic rule node (``{rule, doc?, source?}``)."""

    if not isinstance(node, Mapping):
        fail(label, path, f"must be a rule mapping {{rule}}, got {type(node).__name__}")
    unknown = sorted(set(node) - _RULE_KEYS)
    if unknown:
        fail(label, path, f"unknown rule key(s) {unknown}; allowed keys: {sorted(_RULE_KEYS)}")
    if "value" in node or "unit" in node:
        fail(label, path, "a rule node must not carry 'value'/'unit' (it is symbolic)")
    rule = node.get("rule")
    if not isinstance(rule, str) or not rule.strip():
        fail(label, f"{path}.rule", f"must be a non-empty symbolic rule string, got {rule!r}")
    for field in ("doc", "source"):
        _check_text_field(node, label, path, field)


#: Maximum edit distance at which an unknown mapping key still counts as a
#: plausible typo of a quantity-vocabulary key (catches single-character
#: substitution/insertion/deletion and transpositions, e.g. ``valu`` for
#: ``value`` or ``uint`` for ``unit``). Kept tight so ordinary section field
#: names (``n``, ``omega_min``, ``spacing``) never match.
_QUANTITY_TYPO_DISTANCE = 2


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein distance between two short strings (plain DP, no cutoffs)."""

    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _typoed_quantity_key(key: Any) -> str | None:
    """Return the quantity-vocabulary key ``key`` plausibly misspells.

    ``None`` when ``key`` is itself a vocabulary key, is not a string, or
    is farther than :data:`_QUANTITY_TYPO_DISTANCE` edits from every
    vocabulary key (an ordinary section field name, not a typo).
    """

    if not isinstance(key, str) or key in _QUANTITY_KEYS:
        return None
    longest = max(len(vocabulary_key) for vocabulary_key in _QUANTITY_KEYS)
    if len(key) > longest + _QUANTITY_TYPO_DISTANCE:
        return None
    for vocabulary_key in sorted(_QUANTITY_KEYS):
        if _edit_distance(key, vocabulary_key) <= _QUANTITY_TYPO_DISTANCE:
            return vocabulary_key
    return None


def _quantity_shaped_keys(node: Mapping[str, Any]) -> bool:
    """True when every key is a quantity key or a plausible typo of one."""

    return all(
        key in _QUANTITY_KEYS or _typoed_quantity_key(key) is not None for key in node
    )


def check_quantity_tree(
    node: Any,
    label: str,
    path: str = "",
    *,
    require_provenance: bool = False,
) -> None:
    """Walk a deck and validate every quantity and rule node in it.

    Keys starting with ``_`` (loader bookkeeping such as ``_path``) are
    skipped. Non-quantity scalars (policy strings, flags) are ignored.
    A mapping that declares ``unit`` but carries no ``value`` key, and
    whose keys are all quantity-vocabulary keys or plausible typos of
    them, is rejected as an incomplete quantity instead of being walked
    silently (a dropped or misspelled ``value`` key used to slip through
    with no unit/finiteness/temperature-convention/provenance check at
    all). The same holds — naming every suspected misspelling — for a
    mapping with neither a ``value`` nor a ``unit`` key whose keys are
    all quantity-vocabulary keys or plausible typos of them and where at
    least one key is an actual misspelling: without this arm a doubly
    mistyped ``{valu, uint}`` mapping falls through to child recursion
    and its scalar children are ignored, escaping every check. Mappings
    that keep child recursion: all-vocabulary metadata-only nodes with
    no misspelling (``{doc, source}``, ``{provenance}``), mixed key sets
    containing ordinary section field names (a quantity envelope next to
    ``omega_min``), and section mappings that merely label their bare
    numeric fields with a unit (scenario ``grid: {omega..., unit: rad/s}``).
    """

    if isinstance(node, Mapping):
        if "value" in node:
            check_quantity(node, label, path or "<root>", require_provenance=require_provenance)
            return
        if isinstance(node.get("rule"), str):
            check_rule_node(node, label, path or "<root>")
            return
        if "unit" in node and _quantity_shaped_keys(node):
            typos = []
            for key in node:
                typo = _typoed_quantity_key(key)
                if typo is not None:
                    typos.append(f"{key!r} looks like a misspelling of {typo!r}")
            fail(
                label,
                path or "<root>",
                "looks like a quantity mapping but has no 'value' key"
                + (f"; {'; '.join(typos)}" if typos else "")
                + f" (keys present: {sorted(node)})",
            )
        elif _quantity_shaped_keys(node) and any(
            _typoed_quantity_key(k) is not None for k in node
        ):
            # Reaching this arm: no literal 'value' (routed to check_quantity
            # above), no literal 'unit' (the arm above owns that case), every
            # key a vocabulary key or a plausible typo of one, and at least
            # one actual misspelling. Without it the node falls through to
            # child recursion and its scalar children are silently ignored.
            typos = []
            for key in node:
                typo = _typoed_quantity_key(key)
                if typo is not None:
                    typos.append(f"{key!r} looks like a misspelling of {typo!r}")
            fail(
                label,
                path or "<root>",
                "looks like a mistyped quantity mapping"
                + f"; {'; '.join(typos)}"
                + f" (keys present: {sorted(node)})",
            )
        for key, child in node.items():
            if str(key).startswith("_"):
                continue
            child_path = f"{path}.{key}" if path else str(key)
            check_quantity_tree(child, label, child_path, require_provenance=require_provenance)
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            check_quantity_tree(item, label, f"{path}[{index}]", require_provenance=require_provenance)


def require_mapping(node: Any, label: str, path: str, what: str) -> None:
    if not isinstance(node, Mapping):
        fail(label, path, f"{what} must be a mapping, got {type(node).__name__}")


def require_list(
    node: Any,
    label: str,
    path: str,
    *,
    what: str = "list",
    minimum_length: int | None = None,
) -> list[Any]:
    if not isinstance(node, list):
        fail(label, path, f"{what} must be a list, got {type(node).__name__}")
    if minimum_length is not None and len(node) < minimum_length:
        fail(
            label,
            path,
            f"{what} must have at least {minimum_length} entries, got {len(node)}",
        )
    return node


def require_number(
    node: Any,
    label: str,
    path: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> float:
    """Validate a bare (non-quantity) YAML number and return it as float."""

    number = _number(node, label, path)
    if integer and not isinstance(node, int):
        if isinstance(node, float) and node.is_integer():
            pass
        else:
            fail(label, path, f"must be an integer, got {node!r}")
    if minimum is not None and number < minimum:
        fail(label, path, f"must be >= {minimum}, got {number}")
    if exclusive_minimum is not None and number <= exclusive_minimum:
        fail(label, path, f"must be > {exclusive_minimum}, got {number}")
    if maximum is not None and number > maximum:
        fail(label, path, f"must be <= {maximum}, got {number}")
    return number


def require_numbers(values: Any, label: str, path: str, *, what: str = "entries") -> list[float]:
    """Validate every entry of a bare numeric list; returns the floats."""

    require_list(values, label, path, what=what)
    return [_number(item, label, f"{path}[{index}]") for index, item in enumerate(values)]


def require_strictly_increasing(values: list[float], label: str, path: str, *, what: str = "times") -> None:
    for index in range(1, len(values)):
        if not values[index] > values[index - 1]:
            fail(
                label,
                f"{path}[{index}]",
                f"{what} must be strictly increasing:"
                f" {values[index]} is not greater than {values[index - 1]}",
            )


__all__ = [
    "ABSOLUTE_ZERO_DEGC",
    "DataValidationError",
    "NONNEGATIVE_UNITS",
    "SUPPORTED_UNITS",
    "TEMPERATURE_LEAF_KEYS",
    "check_quantity",
    "check_quantity_tree",
    "check_rule_node",
    "coerce_number",
    "fail",
    "require_list",
    "require_mapping",
    "require_number",
    "require_numbers",
    "require_strictly_increasing",
]