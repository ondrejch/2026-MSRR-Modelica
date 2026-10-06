"""Shared power-directory tags and finite numeric validation.

One legacy-preserving, round-trip-exact formatter is used by every workflow
that constructs or interprets a ``power_<tag>`` path: the setpoint
generator, frequency runners, and campaign-evidence hashing. Powers whose
historical ``%.5f`` rendering parses back to the identical float keep that
rendering (byte-identical campaign tags). Powers that do not round-trip
(for example ``1.4e-5`` vs ``1e-5``) use the shortest exact decimal, so two
distinct accepted powers can never share a result tree.

``POWER_TAG_FORMAT_VERSION`` is recorded in run metadata so a future
formatter change is diagnosable without guessing which rendering produced
an on-disk tag.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

#: Version of :func:`sanitize_power_tag`. Increment when the rendering
#: rule changes; historical campaign tags for the established power grid
#: must remain byte-identical across version 1.
POWER_TAG_FORMAT_VERSION = 1


def require_finite(value: Any, name: str) -> float:
    """Return ``value`` as a finite float or raise ``ValueError``."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return number


def require_finite_positive(value: Any, name: str) -> float:
    """Return a finite float that is strictly greater than zero."""

    number = require_finite(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return number


def require_finite_nonnegative(value: Any, name: str) -> float:
    """Return a finite float that is greater than or equal to zero."""

    number = require_finite(value, name)
    if number < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return number


def parse_finite_number(token: str, *, name: str = "value") -> float:
    """Parse one numeric token, refusing nonfinite spellings.

    ``nan``/``inf``/``-inf`` (any case, and any numeric spelling that
    overflows to an infinity) parse in Python but are nonphysical; they
    are rejected here before dedup, filtering, or tag construction.
    """

    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Nonfinite {name} is nonphysical: {token!r} "
            "(nan/inf spellings are rejected)"
        ) from exc
    if not math.isfinite(value):
        raise ValueError(
            f"Nonfinite {name} is nonphysical: {token!r} "
            "(nan/inf spellings are rejected)"
        )
    return value


def _strip_trailing_tag_zeros(tag: str) -> str:
    """Drop trailing fractional zeros from a ``p``-encoded power tag.

    Frequency campaign trees historically used this compact form
    (``1p00000`` -> ``1``, ``0p10000`` -> ``0p1``) while setpoint tables
    keep the unstripped ``%.5f`` spelling. Off-grid exact decimals such as
    ``0p000014`` are unchanged because they have no trailing zeros.
    """

    sign = ""
    body = tag
    if body.startswith("m"):
        sign = "m"
        body = body[1:]
    if "p" not in body:
        return tag
    integer, fraction = body.split("p", 1)
    fraction = fraction.rstrip("0")
    if fraction:
        return f"{sign}{integer}p{fraction}"
    return f"{sign}{integer}"


def sanitize_power_tag(power: float, *, strip_trailing_zeros: bool = False) -> str:
    """Filename-safe tag for a power level, injective over distinct floats.

    Powers whose historical ``%.5f`` rendering parses back to the identical
    float keep that rendering (byte-identical legacy tag), so existing
    on-disk ``power_<tag>/`` trees and the tag-keyed override tables of
    both setpoint scripts stay valid. Powers whose ``%.5f`` rendering does
    not round-trip (anything between adjacent 5-decimal grid points, e.g.
    1.4e-5 between 1e-5 and 2e-5) get the shortest decimal that ``repr``
    guarantees to parse back to the identical float, expanded from
    scientific notation, so the tag is injective: two distinct powers can
    never share one ``power_<tag>/`` directory. ``-`` maps to ``m`` and
    ``.`` to ``p`` to keep the tag filesystem-safe.

    ``strip_trailing_zeros`` selects the frequency-campaign spelling
    (``power_1``, ``power_0p1``). Setpoint tables keep the default
    unstripped form (``1p00000``, ``0p10000``). Both spellings remain
    injective over distinct floats.
    """

    value = float(power)
    if not math.isfinite(value):
        raise ValueError(
            f"Nonfinite power is nonphysical: {power!r} "
            "(nan/inf spellings are rejected)"
        )
    legacy = f"{value:.5f}"
    if float(legacy) == value:
        tag = legacy.replace("-", "m").replace(".", "p")
    else:
        tag = format(Decimal(repr(value)), "f").replace("-", "m").replace(".", "p")
    if strip_trailing_zeros:
        return _strip_trailing_tag_zeros(tag)
    return tag


def freq_power_tag(power: float) -> str:
    """Frequency-campaign power tag (historical stripped spelling)."""

    return sanitize_power_tag(power, strip_trailing_zeros=True)


def check_power_tag_collisions(
    powers: Sequence[float],
    *,
    tagger: Any = None,
    what: str = "power_<tag>/ result tree",
) -> None:
    """Raise if two distinct requested powers render the same power tag.

    Belt-and-braces invariant: the round-trip-exact rendering of
    :func:`sanitize_power_tag` keeps tags injective, so every power owns
    its own result tree. If that invariant is ever violated, the run
    fails loudly naming both colliding powers instead of silently writing
    two results into one directory.

    ``tagger`` defaults to :func:`sanitize_power_tag`. Callers that re-export
    the formatter (so tests can monkeypatch the local name) pass their
    module-level function. ``what`` names the shared artifact in the error.
    """

    render = sanitize_power_tag if tagger is None else tagger
    by_tag: dict[str, float] = {}
    for power in powers:
        value = require_finite(power, "power")
        tag = render(value)
        previous = by_tag.get(tag)
        if previous is not None and previous != value:
            raise ValueError(
                f"Power tag collision: powers {previous!r} and {value!r} "
                f"both render to tag {tag!r}. They would share one {what}; "
                "distinct powers require distinct tags."
            )
        by_tag[tag] = value


def validate_power_list(powers: Iterable[float]) -> list[float]:
    """Validate a sequence of powers before path construction or launch.

    Requires every entry to be finite and strictly positive, rejects
    duplicate numeric values after parsing, and rejects tag collisions.
    Returns the values in the input order (not sorted).

    Duplicate detection uses exact parsed-float identity (TASK-20260912-01
    P1 ride-along, review rev017 Low): a ``set[float]`` instead of a
    ``%.16g`` string key, which cannot round-trip every float64 and could
    therefore refuse a valid list of two distinct powers that collapse to
    one 16-significant-digit rendering. Equal floats always compare equal,
    so genuine duplicates are still refused by the same named error.
    """

    validated: list[float] = []
    seen: set[float] = set()
    for power in powers:
        value = require_finite_positive(power, "power")
        if value in seen:
            raise ValueError(
                f"Duplicate power {value!r} after parsing; each accepted "
                "power must appear once before tag construction."
            )
        seen.add(value)
        validated.append(value)
    check_power_tag_collisions(validated, tagger=sanitize_power_tag)
    return validated
