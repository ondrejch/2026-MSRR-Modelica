#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serial entry point for MSRR nominal-trim frequency response sweeps.

Deprecated shim: delegates to ``freq.runFreqNominalParallel`` with
``--n_jobs 1`` so both entry points share a single simulation engine.

All legacy flags are accepted unchanged. Behavior now matches the parallel
runner, including full steady-state-table override application, result reuse,
the low-power protocol, per-frequency stop times, and frequency-scaled output
grids.
"""

from __future__ import annotations

import sys

try:
    from .runFreqNominalParallel import main as _parallel_main
except ImportError:  # script-style execution from freq/
    from runFreqNominalParallel import main as _parallel_main


def _strip_n_jobs(argv: list[str]) -> list[str]:
    """Drop any user-supplied ``--n_jobs`` AND every argparse abbreviation
    that resolves to it (``--n_jobs N``, ``--n_jobs=N``, ``--n_j 4``, ...).

    argparse (``allow_abbrev``, the default) resolves an unambiguous
    option-string prefix to the option, so ``--n_``, ``--n_j``, ``--n_jo``,
    ``--n_job`` -- with or without ``=VALUE`` -- all select ``--n_jobs``
    (the only other ``--n``-prefixed option is ``--num_freq``, which none of
    those prefix). The shim strips every prefix of ``--n_jobs`` plus the
    bare value that follows it: the serial shim always forces ``--n_jobs 1``
    itself, and a stray surviving user value would either collide (argparse
    ``last-one-wins`` ambiguity) or, worse, silently re-parallelize a run
    that is documented to be serial (rev022 M-3). A bare ``--n`` is stripped
    too, in the conservative direction for a documented-serial entry point
    (argparse would refuse it as ambiguous between ``--n_jobs`` and
    ``--num_freq``).
    """
    out: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        option = arg.split("=", 1)[0]
        if (
            option.startswith("--")
            and len(option) > 2
            and "--n_jobs".startswith(option)
        ):
            if "=" not in arg:
                skip_next = True
            continue
        out.append(arg)
    return out


def main() -> None:
    print(
        "DEPRECATED: freq.runFreqNominal is a deprecated shim that delegates to "
        "freq.runFreqNominalParallel with --n_jobs 1.",
        file=sys.stderr,
        flush=True,
    )
    _parallel_main(["--n_jobs", "1"] + _strip_n_jobs(sys.argv[1:]))


if __name__ == "__main__":
    main()
