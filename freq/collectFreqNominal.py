#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serial entry point for collecting MSRR nominal-trim frequency response results.

Deprecated shim: delegates to ``freq.collectFreqNominalParallel`` with
``--n_jobs 1`` so both entry points share a single collection engine.
Any user-supplied ``--n_jobs`` value — including every argparse prefix
abbreviation — is stripped before delegation, so the shim always runs
serial (same rule as ``freq.runFreqNominal``).

All legacy flags are accepted unchanged. Results now match the parallel
collector exactly, including the closed-form sine fit, per-frequency
perturbation amplitudes from ``sin_mag_by_freq.csv``, and per-frequency fit
windows from ``stop_time_by_freq.csv``.
"""

from __future__ import annotations

import sys

try:
    from .collectFreqNominalParallel import main as _parallel_main
    from .runFreqNominal import _strip_n_jobs
except ImportError:  # script-style execution from freq/
    from collectFreqNominalParallel import main as _parallel_main
    from runFreqNominal import _strip_n_jobs


def main() -> int:
    print(
        "NOTE: freq.collectFreqNominal is deprecated and delegates to "
        "freq.collectFreqNominalParallel with --n_jobs 1.",
        file=sys.stderr,
    )
    return int(
        _parallel_main(["--n_jobs", "1"] + _strip_n_jobs(sys.argv[1:]))
    )


if __name__ == "__main__":
    sys.exit(main())
