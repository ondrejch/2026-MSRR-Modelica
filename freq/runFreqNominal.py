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


def main() -> None:
    print(
        "NOTE: freq.runFreqNominal is deprecated and delegates to "
        "freq.runFreqNominalParallel with --n_jobs 1.",
        file=sys.stderr,
    )
    _parallel_main(["--n_jobs", "1"] + sys.argv[1:])


if __name__ == "__main__":
    main()
