#!/usr/bin/env python3
"""Self-contained numerical evidence for the segmented-core kelvin-parity runs.

TASK-20260908-01 P3 (card items 1-3). The 5x5 rig-D kelvin-parity
evidence-of-record is a 123,427,812 B, 15,630-column CSV (the
TASK-20261001-01 PSAR-basis re-cut at 0d01de2, gateway job
20261001T144409Z-4564b575; earlier cuts: 126,636,942 B at da7d0d7, the
physics review 2026-09-27, and 127,752,170 B at 71923ab)
that lives only in
gitignored ``00runs/`` (GitHub rejects files > 100 MB); the committed fixture
``tests/fixtures/segmented_kelvin_parity/R5x5Z10MSRRuhxTrimThermalSS.csv`` is
a byte-verbatim spotcheck slice of it (header + six data rows). This module
cuts a compact, committed, tamper-evident evidence package from that full run
-- no new simulation is required -- and ships the verifier path that
recomputes the package whenever the full CSV exists at the recorded path.

THE COMMITTED PACKAGE (default home
``tests/fixtures/segmented_kelvin_parity/evidence/``):

- ``kelvin_parity_evidence_manifest.json``: one manifest covering BOTH
  segmented-core parity cases:

  - ``rig_d_r5x5z10`` (the 5x5 run): source commit, full OpenModelica
    version/build string (``v1.27.0-cmake``), exact command, model name, stop
    time, solver flags, the full CSV's byte count and SHA-256 (cross-checked
    against the values pinned at ``tests/test_kelvin_parity.py`` -- never
    re-typed), a canonical projection containing only the acceptance fields,
    per-row digests (sha256 list + Merkle-style root), the spotcheck raw-line
    digests that bind the committed fixture to the full CSV, extrema and
    tolerance results (strict bars + the recorded cross-toolchain
    observation + the O6 looser bars), and the spotcheck time points
    covering initialization, the transient interval, and terminal behavior.
  - ``rig_c_r1_10seg`` (the equivalent 10Seg case): the committed rig-C
    fixture ``R1MSRRuhx10SegTrimThermalSS.csv`` IS the full acceptance-field
    grid (8 columns x 509 rows), so the 10Seg evidence carries no gitignored
    dependency; the manifest pins its digests and tolerance record the same
    way. Since TASK-20260925-01 P2 the rig-C case also carries its own
    ``toolchain`` record (the pinned-cmake gateway re-cut toolchain --
    branch (a)+O6, landed in ``9d4c4f0``; the top-level cmake
    ``omc_build_record`` still describes the rig-D evidence-of-record)
    and the strict ``applies_to`` binds the pinned cmake toolchain.

- ``R5x5Z10MSRRuhxTrimThermalSS.projection.csv``: the rig-D canonical
  projection -- the eight acceptance columns (``time`` + the seven columns
  the parity comparison checks) extracted VERBATIM (string-exact, no float
  re-serialization) from all 504 data rows of the full CSV.

The manifest also records the gateway QA-model pass/fail summaries (the
three 5x5 QA executables, all green), the six-model build record, the
release contract for the cross-toolchain behavior (card item 3), and the
``verifier_reruns`` hooks that the release verifier fills by re-running
omc ``checkModel(SegmentedMSR)`` and the QA short-run summaries (card
item 4).

DIGEST CONVENTIONS
------------------
- canonical row text: the CSV row's quote-stripped fields joined with
  ``,`` and terminated by ``\\n`` (UTF-8) -- formatting-independent, so a
  re-quoted token still digests identically while any value change fails;
- row digest: sha256 hex of the canonical row text;
- Merkle root: sha256 hex of the lowercase row digests concatenated in row
  order (no separators);
- raw-line digest: sha256 hex of the file's raw line bytes INCLUDING its
  terminating newline (byte-exact -- this is what binds the committed
  fixture slice to the full CSV, whose retained lines are byte-verbatim
  copies).

VERIFY MODES
------------
``verify`` picks its mode from the evidence:

- FULL mode (the full CSV exists at the recorded path): re-verifies the
  file's byte count and SHA-256, recomputes every projection row digest and
  the Merkle root from the full CSV, rebuilds the projection and compares it
  byte-for-byte with the committed projection file, and proves the committed
  fixture is still a byte-verbatim slice (each retained raw line must equal
  the full CSV's raw line at every recorded source index, with no other
  matching line). Any tampered row, column, or manifest field fails loudly.
- COMPACT mode (no full CSV -- the archive-reviewer path): validates the
  committed projection (file digest, per-row digests, Merkle root), the
  committed fixture (raw-line digests, spotcheck times, verbatim acceptance
  values), the rig-C case, and the manifest's internal consistency against
  the values pinned in ``tests/test_kelvin_parity.py``. The absence of the
  full CSV is reported loudly and explicitly -- compact mode NEVER claims
  full recomputation happened -- and ``--require-full-csv`` turns the
  absence itself into a hard failure for workstation-side runs.

CROSS-TOOLCHAIN BEHAVIOR (card item 3, owner decision O6)
---------------------------------------------------------
The rig-D fixture was cut under the gateway's ``v1.27.0-cmake`` build. On
that pinned build the strict replay (max |dT| < 1e-6 K, relative power <
1e-9) executes and is REQUIRED with zero skips by the release verifier
(marker ``omc_parity``, build-conditional -- see
``helpers.release_verification``). On other supported builds (e.g. the
standard ``OpenModelica 1.27.0``) a separately defined LOOSER tolerance
comparison runs instead of skipping all comparison: owner decision O6
bars are max |dT| < 1e-5 K and relative power < 1e-7. O6 originally
defaulted to the strict bars loosened one decade (rel power 1e-8); that
default was measured RED on a fresh standard-build session (max relative
sumInW deviation 1.0369e-8 at t = 39.8 s, a ~4.77 mW bookkeeping offset
whose relative measure varies a few percent between rebuild sessions --
REV-a5b4de4-01), so the rel-power bar was raised to 1e-7 (an order of
magnitude of headroom: 9.6x against the measured 1.0369e-8, >10x against
the original 9.8e-9 observation).
The dt bar keeps one decade of headroom over the recorded systematic
~4.76e-6 K toolchain offset (bit-identical across independent runs --
REV-5485559-01). The test-side wiring and docstring
documentation of O6 belong to the parity test (test-author); this module
owns the canonical constants, the build-variant probe, and the release
contract.

LAND GATE (TASK-20260925-01 P2)
-------------------------------
Any change under ``core/`` requires omc_parity evidence before landing
(``PARITY_LAND_GATE_PATH_PREFIXES`` +
``parity_evidence_required_for_changed_paths`` -- the machine-readable
gate config; the verifier runs
the omc_parity suite green as the landing evidence whenever it fires).
Rig C's either/or landed as branch (a)+O6 (``9d4c4f0``):
gateway re-cut of the fixture on the pinned ``v1.27.0-cmake`` build at
HEAD ``963627a`` plus O6 build-conditional bars in
``tests/test_kelvin_parity.py`` (strict on cmake, 1e-5 K / 1e-7
elsewhere); ``RIG_C_STRICT_APPLIES_TO`` now binds the strict bars to
the pinned cmake toolchain. The physics review 2026-09-27 re-cut rigs
A, C and D on the same toolchain at ``da7d0d7`` (fixtures committed in
``5e9389e``; an independent fresh-build re-run was byte-identical).
TASK-20261001-01 re-cut them again at ``0d01de2`` on the PSAR-basis plant
data (gateway jobs gw-t1001-goldens / gw-t1001-rerun, byte-identical).

CLI
---
    python3.12 -m helpers.segmented_parity_evidence build  [--full-csv PATH]
        [--evidence-dir DIR]
    python3.12 -m helpers.segmented_parity_evidence verify [--full-csv PATH]
        [--evidence-dir DIR] [--require-full-csv] [--json]

``build`` requires the full CSV (it is the evidence-of-record; there is no
simulation fallback here) and (re)writes the package deterministically.
``verify`` exits 0 only when every selected check passed; any mismatch
exits nonzero naming the exact row, field, or digest.

Package-location semantics (REV-a5b4de4-02): the rig-D projection is
package payload and is ALWAYS validated from the evidence directory beside
the manifest -- ``verify --evidence-dir DIR`` therefore verifies a copied
package exactly as copied, and a manifest-only copy fails loudly instead
of silently validating the repository's tracked projection. The committed
rig-D/rig-C fixtures and the full CSV are repository artifacts, not
package payload: they resolve from ``--repo-root`` via the manifest's
recorded relative paths regardless of ``--evidence-dir``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    from helpers import plant_config  # noqa: F401  (keeps repo-root imports working)
except ImportError:  # direct-script execution outside an installed checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Evidence-package schema version (bump on any incompatible change).
EVIDENCE_SCHEMA_VERSION = 1

#: The manifest's ``kind`` marker.
EVIDENCE_KIND = "segmented-kelvin-parity-evidence"

#: Default tracked home of the package (relative to the repository root).
#: The test author owns the final tracked path; this default does not collide
#: with the existing fixture layout (the fixture directory carries only the
#: four parity CSVs).
EVIDENCE_DIR_RELPATH = Path("tests/fixtures/segmented_kelvin_parity/evidence")

MANIFEST_NAME = "kelvin_parity_evidence_manifest.json"
RIG_D_PROJECTION_NAME = "R5x5Z10MSRRuhxTrimThermalSS.projection.csv"

#: The acceptance fields: the parity comparison set plus the time column.
#: These are the ONLY result columns the projection retains from the
#: 15,630-column full CSV.
ACCEPTANCE_COLUMNS: tuple[str, ...] = (
    "time",
    "TF1",
    "TF2",
    "TG",
    "TinCore",
    "ToutCore",
    "sumInW",
    "sumOutW",
)

# --- Case rig D: the 5x5 x 10-axial-segment trim rig -----------------------

RIG_D_CASE_KEY = "rig_d_r5x5z10"
RIG_D_MODEL = "SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS"
RIG_D_STOP_TIME_S = 50.0

#: The gitignored evidence-of-record (relative to the repository root).
#: NEVER committed; the committed fixture is a byte-verbatim slice of it.
RIG_D_FULL_CSV_RELPATH = (
    "00runs/task-20261001-01-gateway/goldens/rigD_trim_parity.csv"
)
RIG_D_FIXTURE_RELPATH = (
    "tests/fixtures/segmented_kelvin_parity/R5x5Z10MSRRuhxTrimThermalSS.csv"
)
RIG_D_TEST_RELPATH = "tests/test_kelvin_parity.py"

#: Spotcheck times (s) of the committed fixture slice: the run's start
#: (initialization), four evenly spaced intermediates (~10 s apart, the
#: transient interval), and the terminal row at the established 50 s
#: duration. Identical to ``RIG_D_SPOTCHECK_TIMES_S`` in the parity test.
RIG_D_SPOTCHECK_TIMES_S: tuple[float, ...] = (0.0, 9.8, 19.8, 29.8, 39.8, 50.0)
RIG_D_SPOTCHECK_PHASES: tuple[str, ...] = (
    "initialization",
    "transient",
    "transient",
    "transient",
    "transient",
    "terminal",
)
#: Time tolerance for matching a spotcheck time to the output grid (the
#: nominal spacing is 0.1 s; half a step pins the intended row).
RIG_D_SPOTCHECK_MATCH_TOL_S = 0.05

# --- Case rig C: the equivalent 10Seg case ---------------------------------

RIG_C_CASE_KEY = "rig_c_r1_10seg"
RIG_C_MODEL = "SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS"
RIG_C_STOP_TIME_S = 50.0
RIG_C_FIXTURE_RELPATH = (
    "tests/fixtures/segmented_kelvin_parity/R1MSRRuhx10SegTrimThermalSS.csv"
)
#: Commit that introduced the CURRENT rig-C fixture bytes: the
#: TASK-20261001-01 PSAR-basis pinned-cmake gateway re-cut (job
#: 20261001T144409Z-4564b575, cut at 0d01de2). The fixture was not yet
#: committed when this record was prepared: replace the placeholder with
#: the SHA of the commit that adds the re-cut fixtures, then re-run
#: ``python3.12 -m helpers.segmented_parity_evidence build``. Before it:
#: the physics review 2026-09-27 pinned-cmake gateway re-cut (job
#: 20260927T231844Z-148d2255, cut at da7d0d7), committed in 5e9389e. The
#: fixture before that was the TASK-20260925-01 P2 pinned-cmake re-cut
#: (branch (a)+O6, REV-963627a-01) at HEAD 963627a, committed in 9d4c4f0;
#: before that ca9cd82
#: (TASK-20260924-01 P2, Fixes: REV-9765160-01, standard-build
#: OpenModelica 1.27.1~2-g6db4671); the original TASK-20260906-01 test
#: pin was 9743394799e73baec3373f1551f272f3ef9ad67d.
RIG_C_FIXTURE_COMMIT = "598ffef334d45075490bb9863c466a2c2ccb883c"

# --- Tolerances -------------------------------------------------------------

#: STRICT bars -- the evidence bars tied to the gateway cmake toolchain
#: (identical to the committed rig-C/rig-D pins in the parity test).
STRICT_DT_LIMIT_K = 1e-6
STRICT_REL_POWER_LIMIT = 1e-9
STRICT_TEMPERATURE_OFFSET_K = 0.0  # same-era (kelvin) fixtures: no shift

#: Cross-toolchain bars -- owner decision O6 (2026-09-08), amended by
#: REV-a5b4de4-01: the dt bar keeps the strict bar loosened one decade; the
#: rel-power bar is 1e-7 because the original one-decade default (1e-8) was
#: measured red on a fresh standard-build session (1.0369e-8). On a non-cmake
#: build the parity comparison runs at THESE bars instead of skipping all
#: comparison.
CROSS_TOOLCHAIN_DT_LIMIT_K = 1e-5
CROSS_TOOLCHAIN_REL_POWER_LIMIT = 1e-7

#: The recorded cross-toolchain observation (REV-5485559-01, measured on the
#: standard ``OpenModelica 1.27.0`` build): a systematic, bit-identical
#: toolchain offset -- not model drift. Motivates the O6 bars above. The
#: ``fresh_session_recheck`` records the REV-a5b4de4-01 measurement that
#: amended the O6 rel-power bar from 1e-8 to 1e-7.
CROSS_TOOLCHAIN_OBSERVATION = {
    "build": "OpenModelica 1.27.0 (standard build, no cmake marker)",
    "worst_abs_dt_k": 4.76e-6,
    "worst_dt_time_s": 9.8,
    "worst_rel_power": 9.8e-9,
    "worst_rel_power_column": "sumInW",
    "determinism": (
        "bit-identical across 2 independent runs -> systematic "
        "local-vs-gateway (v1.27.0-cmake) toolchain offset, not model drift"
    ),
    "recorded_in": (
        "REV-5485559-01 (.collab/reviews/2026-09-08-task-20260906-02-final-"
        "vllm-rascal__qwen3.8-27b-nvfp4-r01.md; test docstring RIG D section)"
    ),
    "fresh_session_recheck": {
        "recorded_in": (
            "REV-a5b4de4-01 (test-time verification of a5b4de4 on the "
            "standard-build workstation)"
        ),
        "worst_rel_power": 1.0369e-8,
        "worst_rel_power_column": "sumInW",
        "worst_rel_power_time_s": 39.8,
        "worst_abs_dt_k": 4.761e-6,
        "note": (
            "a ~4.77 mW sumInW bookkeeping offset whose relative measure "
            "varies a few percent between rebuild sessions (the earlier "
            "9.8e-9 observation under-measured a fresh session); the "
            "worst |dT| is bit-identical to the recorded observation and "
            "passes the 1e-5 K bar with 2.1x margin. Measured red against "
            "the original 1e-8 O6 default, so the O6 rel-power bar was "
            "raised to 1e-7 (an order of magnitude of headroom: 9.6x "
            "against this measurement, >10x against the original 9.8e-9 "
            "observation)"
        ),
    },
    "review_20260927_recheck": {
        "recorded_in": (
            "physics review 2026-09-27 re-cut: local standard-build "
            "(OpenModelica 1.27.1~2-g6db4671) rehearsal vs the gateway "
            "v1.27.0-cmake cut, all rows of rigs A/C/D"
        ),
        "worst_abs_dt_k": {"rig_a": 1.728e-11, "rig_c": 7.958e-13, "rig_d": 2.274e-12},
        "worst_rel_power": {"rig_a": 3.12e-13, "rig_c": 8.383e-15, "rig_d": 7.114e-14},
        "note": (
            "the micro-kelvin offsets above were amplified by the pre-fix "
            "inconsistent hot start; with the rigs starting stationary at "
            "the qualified steady state the two builds agree to rounding. "
            "The O6 bars are kept unchanged"
        ),
    },
    "task_20261001_01_recheck": {
        "recorded_in": (
            "TASK-20261001-01 PSAR-basis re-cut: tests/test_kelvin_parity.py "
            "on the local standard build (OpenModelica 1.27.1~2-g6db4671) "
            "against the gateway v1.27.0-cmake cut at 0d01de2 (rigs A/C all "
            "rows, rig D the six spotcheck rows)"
        ),
        "worst_abs_dt_k": {"rig_a": 5.684e-13, "rig_c": 9.095e-13, "rig_d": 1.774e-11},
        "worst_rel_power": {"rig_a": 1.747e-15, "rig_c": 1.025e-14, "rig_d": 1.391e-13},
        "note": "the O6 bars are kept unchanged",
    },
}

#: Strict-replay result of the TASK-20261001-01 re-cut: an independent
#: fresh-build gateway re-run (gw-t1001-rerun, cmake build, commit-pinned,
#: same worker) was BYTE-IDENTICAL to the evidence-of-record for all three
#: rigs, so the strict replay deviation against the committed fixtures is
#: zero. (Previous records: the physics review 2026-09-27 re-run
#: gw-r0927-rerun, job 20260927T232006Z-e51eff6e, byte-identical to sha256
#: 5ff0c254...; the TASK-20260906-02 verifier's FINAL-SHA re-run,
#: byte-identical to sha256 b7bed8ca....)
STRICT_REPLAY_RESULT = {
    "result": "pass",
    "deviation": "zero (byte-identical re-run)",
    "evidence": (
        "independent fresh-build gateway re-run gw-t1001-rerun (job "
        "20261001T144418Z-c7876220, v1.27.0-cmake, worker c0801, commit "
        "0d01de2) byte-identical to the evidence-of-record sha256 "
        "5a8e5c2b58c5d91192bd86c6a2db5f8dd8de33e66c10324ba9133dca9f335897 "
        "(and to the rig-A/rig-C runs); a further build from the "
        "working-tree core/SegmentedMSR.mo with the TASK-20261001-01 "
        "QA-expectation edits (gw-t1001-qa-wt, job "
        "20261001T150131Z-fd4ccfd5) was byte-identical as well"
    ),
}

#: The pinned OpenModelica build (the ONLY toolchain the strict replay is
#: required on). The ``cmake`` substring of ``omc --version`` is the sole
#: build-variant discriminator -- the version number is identical in both
#: builds.
PINNED_OMC_BUILD_STRING = "v1.27.0-cmake"
BUILD_VARIANT_CMAKE = "cmake"
BUILD_VARIANT_STANDARD = "standard"

#: The release-required zero-skip marker for the rig-D parity replay
#: (build-conditional; see helpers.release_verification). The parity test
#: carries this marker on every omc-bearing host: strict bars on the pinned
#: cmake build (release-required, zero skips), the O6 looser comparison
#: elsewhere (not release-required).
PARITY_MARKER = "omc_parity"

#: Actual toolchain that cut the CURRENT rig-C fixture (TASK-20261001-01,
#: PSAR design basis): a gateway rebuild + 50 s run at
#: 0d01de2c80c9a2e141424c5680036c7843770977 per GOLDEN_PROTOCOL
#: (single-library session, buildModel(tolerance = 1e-10), runtime flags
#: only; gateway worker c0801, job 20261001T144409Z-4564b575; 8 columns x
#: 508 data rows, sha256
#: 5bbb4338eb2f1be578180f369cf6468337078fc616547c20006012cd6940aae4).
#: Previously the physics review 2026-09-27 cut at da7d0d7, committed in
#: 5e9389e (sha256 8deda333...), and before that TASK-20260925-01 P2 at
#: HEAD 963627a, committed in 9d4c4f0, whose grid the rest of this
#: paragraph describes.
#: ``openmodelica_version`` is the pinned ``v1.27.0-cmake`` gateway build
#: string; ``build_variant`` is the ``local_omc_build_variant()``
#: classification of that string. The 50 s grid output was extracted
#: byte-verbatim (no re-serialization: header + 509 data rows, sha256
#: 3328dcec35e7cbb0d6cc67286e2b591d0999e4ab3dc8ef4571b55db0563ab95f --
#: see the rig-C section of tests/test_kelvin_parity.py). Emitted as the
#: rig-C case's ``toolchain`` record on manifest re-export.
RIG_C_TOOLCHAIN = {
    "openmodelica_version": PINNED_OMC_BUILD_STRING,
    "build_variant": BUILD_VARIANT_CMAKE,
    "cut_at_commit": "0d01de2c80c9a2e141424c5680036c7843770977",
    "fixture_committed_in": RIG_C_FIXTURE_COMMIT,
    "protocol": (
        "GOLDEN_PROTOCOL: single-library session, "
        "buildModel(tolerance = 1e-10), runtime flags only "
        "(-outputFormat=csv -r=<name>.csv -stopTime=50), default "
        "integrator output grid"
    ),
}

#: Truthful binding of the rig-C strict bars (TASK-20260925-01 P2,
#: branch (a)+O6 landed in 9d4c4f0; re-cut by the physics review
#: 2026-09-27 and by TASK-20261001-01): the strict 1e-6 K / 1e-9 bars bind
#: the pinned v1.27.0-cmake toolchain that cut the committed fixture
#: (gateway worker c0801, cut at 0d01de2; replace the fixture-commit
#: placeholder with the commit that adds the fixture); other supported
#: builds run the O6 build-conditional bars
#: (1e-5 K / 1e-7) via tests/test_kelvin_parity.py, mirroring rig D.
RIG_C_STRICT_APPLIES_TO = (
    "the pinned v1.27.0-cmake toolchain that produced the committed "
    "fixture (gateway worker c0801, "
    "local_omc_build_variant() = cmake, cut at 0d01de2, fixture "
    "committed in 598ffef)"
)

#: Land-gate path rule (TASK-20260925-01 P2; rev025 review section 3.4):
#: the strict rig-C test had evidently not run between the 9211483
#: physics change and the P7 certification while two physics commits
#: landed with approve verdicts, so any change under these repo-relative
#: prefixes requires omc_parity evidence before landing. This predicate
#: is the machine-readable gate config: the verifier evaluates it over
#: the phase diff and runs the omc_parity suite green as the landing
#: evidence whenever it fires.
PARITY_LAND_GATE_PATH_PREFIXES: tuple[str, ...] = ("core/",)


def parity_evidence_required_for_changed_paths(
    changed_paths: Sequence[str],
) -> bool:
    """True when any changed repo-relative path falls under a parity-gated
    prefix (:data:`PARITY_LAND_GATE_PATH_PREFIXES`).

    Separators are normalized (``\\\\`` -> ``/``) and a leading ``./`` is
    stripped, so ``core/SegmentedMSR.mo``, ``./core/MSRR.mo``, and
    ``core\\\\MSRR.mo`` all gate, while ``tests/test_kelvin_parity.py``,
    ``helpers/segmented_parity_evidence.py``, and ``doc/...`` do not.
    Empty entries never gate; a bare ``core`` (no trailing slash) gates.
    """
    for raw in changed_paths:
        text = str(raw).replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        if not text:
            continue
        for prefix in PARITY_LAND_GATE_PATH_PREFIXES:
            if text == prefix.rstrip("/") or text.startswith(prefix):
                return True
    return False

# --- Recorded run provenance (transcribed verbatim from the gateway tree) --

#: Source state of the rig-D run: the TASK-20261001-01 re-cut was made at
#: this commit by the gateway worker (fresh checkout ~/git/SMD-MSRR-dev-c7).
#: The tracked ``core/`` change that follows it (core/SegmentedMSR.mo QA
#: expectation constants and doc strings only) leaves the rig output
#: byte-identical (gw-t1001-qa-wt rebuilt rigs A/C/D from that working
#: tree). Previously da7d0d7 (physics review 2026-09-27) and 71923ab
#: (TASK-20260906-02 P3 tree).
RIG_D_SOURCE_COMMIT = "0d01de2c80c9a2e141424c5680036c7843770977"

#: The exact build and run recipe (gw-t1001-goldens,
#: 00runs/task-20261001-01-gateway/dispatch/gw-t1001-goldens.sh; the
#: gw-r0927-goldens / gw-p4-trim-parity protocol).
RIG_D_BUILD_COMMAND = (
    f"buildModel({RIG_D_MODEL}, tolerance = 1e-10)"
)
RIG_D_RUN_COMMAND = (
    f"./{RIG_D_MODEL} -outputFormat=csv -r=<ns>/rigD_trim_parity.csv -stopTime 50"
)
RIG_D_RUN_COMMAND_RECORD = (
    "00runs/task-20261001-01-gateway/dispatch/gw-t1001-goldens.sh"
)
RIG_D_TOOLCHAIN = {
    "openmodelica_version": PINNED_OMC_BUILD_STRING,
    "worker": "c0801",
    "submit_host": "necluster",
    "job_id": "20261001T144409Z-4564b575",
    "executable_reused_from": (
        "none: built fresh in the same job (gw-t1001-goldens, at the pinned "
        "SHA, tolerance 1e-10)"
    ),
}
RIG_D_RUN_WINDOW = {
    "started_utc": "2026-10-01T14:45:16Z",
    "finished_utc": "2026-10-01T14:45:48Z",
    "run_exit": 0,
}
RIG_D_SOLVER_NOTES = {
    "build_tolerance": 1e-10,
    "integrator": "default (no -s override); stopTime overwritten at runtime",
    "output_grid": (
        "500 intervals recalculated (no -stepSize override); nominal "
        "spacing 0.1 s"
    ),
    "linear_solvers": (
        "sparse solvers auto-selected for linear systems 0-5 (density "
        "0.011 under the 0.200 threshold)"
    ),
    "initialization": "finished successfully without homotopy method",
    "runtime_flags": ["-outputFormat=csv", "-r=<ns>/rigD_trim_parity.csv", "-stopTime 50"],
    "recorded_in": "00runs/task-20261001-01-gateway/goldens/rigD_trim_parity_run.log",
}

#: Gateway tree root for the optional provenance cross-checks (never
#: modified; read-only when present).
RIG_D_GATEWAY_RELPATH = "00runs/task-20261001-01-gateway/goldens"

# --- Recorded QA-model summaries (gateway) ----------------------------------
# Transcribed verbatim from
# 00runs/review-20260927-gateway/goldens/qa/qa_summary.txt (the physics
# review 2026-09-27 re-run at the re-cut commit da7d0d7, with the corrected
# coarsening bars; previously the gateway P4 tree, whose coarsening bars
# were 1e-3 / 1e-3 / 1e-3 / 5e-3 K); the release verifier re-runs these (card
# item 4) and records the re-run beside the release-verification record.
#
# TASK-20261001-01: this "models" record is the PRE-PSAR historical run
# (all green on the pre-PSAR plant data; its ref_rho_circ_pcm 73.4683 is
# the pre-PSAR SteadyTrimCheck5x5Z10 EXPECT) and is kept verbatim with its
# own source commit. The PSAR-basis re-run (gw-t1001-qa-wt) is recorded
# beside it under "psar_basis_rerun": steady trim and delayed source green
# with the re-derived 68.4769140 pcm EXPECT, the 5x5 coarsening check RED
# at its unchanged bars (owner decision pending).

_QA_MODEL_REFS = {
    "delayed_source": {
        "model": "SegmentedMSR.QA.DelayedSourceIdentityCheck5x5Z10",
        "csv": "00runs/review-20260927-gateway/goldens/qa/delayed_source.csv",
        "log": "00runs/review-20260927-gateway/goldens/qa/delayed_source.log",
    },
    "steady_trim": {
        "model": "SegmentedMSR.QA.SteadyTrimCheck5x5Z10",
        "csv": "00runs/review-20260927-gateway/goldens/qa/steady_trim.csv",
        "log": "00runs/review-20260927-gateway/goldens/qa/steady_trim.log",
    },
    "coarsening": {
        "model": "SegmentedMSR.QA.CoarseningConsistencyCheck5x5Z10",
        "csv": "00runs/review-20260927-gateway/goldens/qa/coarsening.csv",
        "log": "00runs/review-20260927-gateway/goldens/qa/coarsening.log",
    },
}

_QA_SUMMARY_RELPATH = "00runs/review-20260927-gateway/goldens/qa/qa_summary.txt"
_QA_COARSENING_SUMMARY_RELPATH = (
    "00runs/review-20260927-gateway/goldens/qa/coarsening.csv"
)
# The historical QA run's own commit (no longer the rig-D source commit
# since the TASK-20261001-01 rig-D re-cut).
_QA_SOURCE_COMMIT = "da7d0d79f522b08a66f74b9e36db64f8fda2bf1c"

#: The PSAR-basis QA re-run (TASK-20261001-01, transcribed from
#: 00runs/task-20261001-01-gateway/qa-wt/qa/qa_summary.txt): the three 5x5
#: QA executables built from the working-tree core/SegmentedMSR.mo
#: (sha256 below; QA-expectation edits only) on the c7 checkout at 0d01de2.
_QA_PSAR_SUMMARY_RELPATH = "00runs/task-20261001-01-gateway/qa-wt/qa/qa_summary.txt"
QA_PSAR_BASIS_RERUN = {
    "source": _QA_PSAR_SUMMARY_RELPATH,
    "job_id": "20261001T150131Z-fd4ccfd5",
    "worker": "c0801",
    "dispatch_record": "00runs/task-20261001-01-gateway/dispatch/gw-t1001-qa-wt.sh",
    "base_commit": "0d01de2c80c9a2e141424c5680036c7843770977",
    "working_tree_segmented_msr_sha256": (
        "c8583ffc6ff042a08f2a9fccdf91e37801f751b7f6a9008998313b2ce63466d8"
    ),
    "omc_version": PINNED_OMC_BUILD_STRING,
    "overall_all_green": False,
    "models": {
        "delayed_source": {
            "model": "SegmentedMSR.QA.DelayedSourceIdentityCheck5x5Z10",
            "stop_time_s": 10.0,
            "run_exit": 0,
            "violation_lines": 0,
            "green": True,
        },
        "steady_trim": {
            "model": "SegmentedMSR.QA.SteadyTrimCheck5x5Z10",
            "stop_time_s": 10.0,
            "run_exit": 0,
            "violation_lines": 0,
            "rho_circ_pcm_t0": 68.4769119269,
            "steady_abs_rho_circ_pcm_minus_ref": 2.073e-6,
            "ref_rho_circ_pcm": 68.4769140,
            "bar": "hard < 15 pcm; warn >= 2 pcm",
            "verdict": "ok",
            "green": True,
        },
        "coarsening": {
            "model": "SegmentedMSR.QA.CoarseningConsistencyCheck5x5Z10",
            "stop_time_s": 4400.0,
            "tcheck_s": 4000.0,
            "run_exit": 0,
            "violation_lines": 3,
            "tcheck_bars": {
                "dTf5_k": {"value": 3.319327e-2, "bar_k": 0.07, "pass": True},
                "dTf10_k": {"value": -8.285201e-4, "bar_k": 1e-3, "pass": True},
                "dTg5_k": {"value": 5.156236e-2, "bar_k": 0.07, "pass": True},
                "dTg10_k": {"value": -1.792528, "bar_k": 1.0, "pass": False},
                "dP_w": {"value": -3.516767e1, "bar_w": 100.0, "pass": True},
                "dP5nom_w": {"value": -5.727451e3, "bar_w": 100.0, "pass": False},
                "dP10nom_w": {"value": -5.692283e3, "bar_w": 100.0, "pass": False},
            },
            "settled_30000s_informational": {
                "dTf5_k": 4.0796e-2, "dTf10_k": 8.7539e-12, "dTg5_k": 9.3167e-2,
                "dTg10_k": -2.0861, "dP_w": 3.7160e-7,
            },
            "green": False,
            "note": (
                "fails its unchanged bars on the PSAR-basis hAnom (4565 W/K): "
                "the slow graphite mode (tau ~1.3 ks) has not settled at "
                "tCheck = 4000 s, and the settled axial-conduction offsets "
                "(dTg10 -2.09 K, dTg5 +0.093 K) exceed the 1.0 / 0.07 K "
                "bars. Superseded: the check was restructured to the "
                "matched-axial identity (TASK-20261001-01, owner decision "
                "2026-10-01); this record is the pre-restructure run"
            ),
        },
    },
}

QA_MODELS_RECORDED = {
    "source": _QA_SUMMARY_RELPATH,
    "source_commit": _QA_SOURCE_COMMIT,
    "data_basis": (
        "pre-PSAR plant data (historical record, kept verbatim; see "
        "psar_basis_rerun for the TASK-20261001-01 re-run)"
    ),
    "psar_basis_rerun": QA_PSAR_BASIS_RERUN,
    "omc_version": PINNED_OMC_BUILD_STRING,
    "overall_all_green": True,
    "models": {
        "delayed_source": {
            **_QA_MODEL_REFS["delayed_source"],
            "stop_time_s": 10.0,
            "run_exit": 0,
            "csv_present": True,
            "violation_lines": 0,
            "green": True,
        },
        "steady_trim": {
            **_QA_MODEL_REFS["steady_trim"],
            "stop_time_s": 10.0,
            "run_exit": 0,
            "csv_present": True,
            "violation_lines": 0,
            "steady_abs_rho_circ_pcm_minus_ref": 2.237e-7,
            "ref_rho_circ_pcm": 73.4683,
            "bar": "hard < 15 pcm; warn >= 2 pcm",
            "verdict": "ok",
            "green": True,
        },
        "coarsening": {
            **_QA_MODEL_REFS["coarsening"],
            "stop_time_s": 4400.0,
            "tcheck_s": 4000.0,
            "run_exit": 0,
            "csv_present": True,
            "violation_lines": 0,
            "tcheck_bars": {
                "dTf5_k": {"value": 3.341591e-2, "bar_k": 0.07, "pass": True},
                "dTf10_k": {"value": -5.773203e-7, "bar_k": 1e-3, "pass": True},
                "dTg5_k": {"value": 3.399931e-2, "bar_k": 0.07, "pass": True},
                "dTg10_k": {"value": -5.109735e-1, "bar_k": 1.0, "pass": True},
                "dP_w": {"value": -2.772817e-2, "bar_w": 100.0, "pass": True},
                "dP5nom_w": {"value": -1.379731e-1, "bar_w": 100.0, "pass": True},
                "dP10nom_w": {"value": -1.102450e-1, "bar_w": 100.0, "pass": True},
            },
            "last_row_dTg10_k": -5.10974155601275e-1,
            "green": True,
        },
    },
}

#: Build record of the TASK-20261001-01 rig re-cut job (gw-t1001-goldens
#: BUILD OK lines in
#: 00runs/task-20261001-01-gateway/dispatch/gw-t1001-goldens.result.json;
#: per-model logs under goldens/builds/<Model>/build.log): every model
#: buildModel-green at tolerance 1e-10 on the pinned cmake build. The three
#: 5x5 QA models were built in gw-t1001-qa-wt (see QA_PSAR_BASIS_RERUN).
#: The previous record was the six-model gw-r0927-goldens build
#: (00runs/review-20260927-gateway/dispatch/gw-r0927-goldens.result.json).
OMC_BUILD_RECORD_RECORDED = {
    "source": "00runs/task-20261001-01-gateway/dispatch/gw-t1001-goldens.result.json",
    "omc_version": PINNED_OMC_BUILD_STRING,
    "build_tolerance": 1e-10,
    "models": {
        "SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS": True,
        "SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS": True,
        "SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS": True,
    },
    "overall_all_green": True,
}

#: The verifier-owned re-run hooks (card item 4). ``status`` stays
#: ``pending_verifier_rerun`` until the verifier re-runs the item on the
#: final implementation SHA and records the outcome beside the release-
#: verification record.
VERIFIER_RERUN_HOOKS = {
    "checkmodel_segmented_msr": {
        "status": "pending_verifier_rerun",
        "command": (
            "from 00runs/tmp/omc/ with TMPDIR/TEMP/TMP prefixed: load "
            "core/generated/SegmentedMSR_PlantData.mo + core/SegmentedMSR.mo, "
            "then checkModel(SegmentedMSR); getErrorString()"
        ),
        "expectation": "checkModel(SegmentedMSR) finishes clean (no errors)",
        "reference_record": OMC_BUILD_RECORD_RECORDED["source"],
    },
    "qa_short_run_summaries": {
        "status": "pending_verifier_rerun",
        "command": (
            "re-run the three 5x5 QA executables with explicit -stopTime "
            "(10 / 10 / >= tCheck) from a fresh build at the verified SHA"
        ),
        "expectation": (
            "run_exit 0, no assertion-violation lines, all recorded bars "
            "reproduced within their bands"
        ),
        "reference_record": _QA_PSAR_SUMMARY_RELPATH,
        "reference_note": (
            "the PSAR-basis QA re-run (TASK-20261001-01); the 5x5 "
            "coarsening check is RED there at its unchanged bars, so this "
            "hook cannot pass until the owner decides on those bars"
        ),
    },
}


class ParityEvidenceError(RuntimeError):
    """A build or verification step failed (the message names the mismatch)."""


# ---------------------------------------------------------------------------
# Digest helpers
# ---------------------------------------------------------------------------


def canonical_row_text(fields: Sequence[str]) -> str:
    """Canonical serialization of one CSV row: quote-stripped fields joined
    with commas, newline-terminated (the digest input)."""
    return ",".join(field.strip().strip('"') for field in fields) + "\n"


def canonical_line_digest(line: str) -> str:
    """sha256 hex of a row's canonical text."""
    return hashlib.sha256(canonical_row_text(line).encode("utf-8")).hexdigest()


def raw_line_digest(raw: bytes) -> str:
    """sha256 hex of a file's raw line bytes (including its newline)."""
    return hashlib.sha256(raw).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Streamed sha256 hex of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merkle_root(digests: Sequence[str]) -> str:
    """Merkle-style root: sha256 of the lowercase digests concatenated in
    row order (no separators)."""
    return hashlib.sha256("".join(d.lower() for d in digests).encode("ascii")).hexdigest()


def parse_omc_header(line: str) -> list[str]:
    """Quote-aware split of an omc CSV header line into canonical names."""
    return [field.strip().strip('"') for field in next(csv.reader([line]))]


def _iter_raw_lines(path: Path):
    """Yield raw byte lines of ``path`` (newline included when present)."""
    with Path(path).open("rb") as handle:
        for raw in handle:
            yield raw


def _nearest_index(values: Sequence[float], target: float) -> int:
    """Index whose value is closest to ``target``; ties -> highest index
    (the parity test's tie rule: it pins the t=0 start row next to the
    solver's ~1e-14 rows and the terminal t=50 row the grid writes twice)."""
    best = 0
    best_d = float("inf")
    for idx, value in enumerate(values):
        d = abs(float(value) - target)
        if d <= best_d:
            best_d = d
            best = idx
    return best


def openmodelica_build_variant(version_string: str | None) -> str | None:
    """Classify an ``omc --version`` string: ``cmake`` when the cmake marker
    is present, ``standard`` for any other non-empty version, ``None`` when
    no version string is available. The cmake substring is the ONLY
    build-variant discriminator (the 1.27.0 version number is identical in
    both builds)."""
    if not version_string or not version_string.strip():
        return None
    return BUILD_VARIANT_CMAKE if "cmake" in version_string.lower() else BUILD_VARIANT_STANDARD


def local_omc_build_variant(omc_bin: str | None = None) -> str | None:
    """Probe the local ``omc`` build variant (``None`` when omc is absent or
    the probe fails)."""
    omc = omc_bin or shutil.which("omc")
    if omc is None:
        return None
    try:
        proc = subprocess.run(
            [omc, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return openmodelica_build_variant(
        " ".join(f"{proc.stdout}\n{proc.stderr}".split())
    )


# ---------------------------------------------------------------------------
# CSV scanning (shared by build and full-mode verify)
# ---------------------------------------------------------------------------


def _project_fields(fields: list[str], col_idx: list[int]) -> list[str]:
    return [fields[i] for i in col_idx]


def _column_indices(header_fields: list[str], columns: Sequence[str]) -> list[int]:
    """Indices of ``columns`` inside a parsed CSV header (loud on absence)."""
    missing = [col for col in columns if col not in header_fields]
    if missing:
        raise ParityEvidenceError(
            f"acceptance column(s) absent from the header: {missing}"
        )
    return [header_fields.index(col) for col in columns]


def _acceptance_values(
    fields: list[str], header_fields: list[str], columns: Sequence[str]
) -> dict[str, str]:
    """Verbatim acceptance-field values of one (possibly wide) CSV row."""
    return dict(
        zip(columns, (fields[i] for i in _column_indices(header_fields, columns)))
    )


def _scan_wide_csv(
    path: Path,
    columns: Sequence[str],
    sought_raw_lines: Sequence[bytes] = (),
) -> dict:
    """One streaming pass over a wide omc CSV.

    Returns the file digest/size, the canonical header, the verbatim
    acceptance-field rows (projection rows), their canonical digests, the
    float extrema of each projected column, the parsed time column, and --
    for each sought raw line -- the 0-based data-row indices whose raw bytes
    match it exactly.
    """
    file_sha = hashlib.sha256()
    header_raw = None
    header_fields: list[str] | None = None
    col_idx: list[int] | None = None
    proj_rows: list[list[str]] = []
    row_digests: list[str] = []
    times: list[float] = []
    extrema = {col: [float("inf"), float("-inf")] for col in columns}
    sought = [bytes(line) for line in sought_raw_lines]
    matches: list[list[int]] = [[] for _ in sought]
    n_data = 0
    n_lines = 0
    for raw in _iter_raw_lines(path):
        file_sha.update(raw)
        n_lines += 1
        if header_raw is None:
            header_raw = raw
            header_fields = parse_omc_header(raw.decode("utf-8"))
            missing = [col for col in columns if col not in header_fields]
            if missing:
                raise ParityEvidenceError(
                    f"{path}: acceptance column(s) absent from the CSV "
                    f"header: {missing}"
                )
            col_idx = [header_fields.index(col) for col in columns]
            continue
        if not raw.strip():
            continue
        n_data += 1
        for i, sline in enumerate(sought):
            if raw == sline:
                matches[i].append(n_data - 1)
        fields = [
            token.strip().strip('"')
            for token in next(csv.reader([raw.decode("utf-8")]))
        ]
        proj = _project_fields(fields, col_idx)  # type: ignore[arg-type]
        proj_rows.append(proj)
        row_digests.append(canonical_line_digest(",".join(proj)))
        times.append(float(proj[0]))
        for name, token in zip(columns, proj):
            value = float(token)
            lo, hi = extrema[name]
            if value < lo:
                extrema[name][0] = value
            if value > hi:
                extrema[name][1] = value
    if header_fields is None:
        raise ParityEvidenceError(f"{path}: file is empty (no header line)")
    return {
        "bytes": path.stat().st_size,
        "sha256": file_sha.hexdigest(),
        "header_raw_line": header_raw,
        "header_fields": header_fields,
        "n_columns": len(header_fields),
        "n_data_rows": n_data,
        "n_lines": n_lines,
        "projection_rows": proj_rows,
        "row_digests": row_digests,
        "times": times,
        "extrema": {
            col: {"min": extrema[col][0], "max": extrema[col][1]}
            for col in columns
        },
        "sought_matches": matches,
    }


def _write_projection(path: Path, columns: Sequence[str], rows) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(columns) + "\n")
        for row in rows:
            handle.write(",".join(row) + "\n")


def _read_committed_csv(path: Path) -> dict:
    """Parse a committed (small) omc-style CSV: raw lines, canonical header,
    verbatim field rows, canonical per-row digests, parsed times."""
    raw_lines = Path(path).read_bytes().splitlines(keepends=True)
    if not raw_lines:
        raise ParityEvidenceError(f"{path}: file is empty")
    header_fields = parse_omc_header(raw_lines[0].decode("utf-8"))
    rows: list[list[str]] = []
    digests: list[str] = []
    times: list[float] = []
    for raw in raw_lines[1:]:
        if not raw.strip():
            continue
        fields = [
            token.strip().strip('"')
            for token in next(csv.reader([raw.decode("utf-8")]))
        ]
        if len(fields) != len(header_fields):
            raise ParityEvidenceError(
                f"{path}: row has {len(fields)} fields vs "
                f"{len(header_fields)} header columns"
            )
        rows.append(fields)
        digests.append(canonical_line_digest(",".join(fields)))
        times.append(float(fields[0]))
    return {
        "bytes": Path(path).stat().st_size,
        "sha256": sha256_file(path),
        "raw_lines": raw_lines,
        "header_fields": header_fields,
        "header_raw_line": raw_lines[0],
        "rows": rows,
        "row_digests": digests,
        "times": times,
    }


# ---------------------------------------------------------------------------
# Manifest assembly (build)
# ---------------------------------------------------------------------------


def _extract_test_pins(repo_root: Path) -> dict:
    """Read the full-CSV byte count / SHA-256 / relative path that the parity
    test pins (tests/test_kelvin_parity.py). Values are READ from the test
    source, never re-typed; the builder fails loudly if they are absent."""
    test_path = repo_root / RIG_D_TEST_RELPATH
    if not test_path.is_file():
        raise ParityEvidenceError(
            f"parity test source absent: {test_path}; the builder "
            "cross-checks the pinned evidence values against it"
        )
    text = test_path.read_text(encoding="utf-8")
    sha_match = re.search(
        r"RIG_D_FULL_CSV_SHA256\s*=\s*\(?\s*[\"']([0-9a-fA-F]{64})[\"']", text
    )
    bytes_match = re.search(r"RIG_D_FULL_CSV_BYTES\s*=\s*([\d_]+)", text)
    path_match = re.search(
        r"RIG_D_FULL_CSV\s*=\s*\((.*?)\)", text, re.DOTALL
    )
    missing = [
        name
        for name, match in (
            ("RIG_D_FULL_CSV_SHA256", sha_match),
            ("RIG_D_FULL_CSV_BYTES", bytes_match),
        )
        if match is None
    ]
    if missing:
        raise ParityEvidenceError(
            f"{test_path}: pinned constant(s) not found: {', '.join(missing)}"
        )
    assert sha_match is not None and bytes_match is not None
    relpath = RIG_D_FULL_CSV_RELPATH
    if path_match is not None:
        parts = re.findall(r"[\"']([^\"']+)[\"']", path_match.group(1))
        if parts:
            relpath = "/".join(parts)
    return {
        "sha256": sha_match.group(1).lower(),
        "bytes": int(bytes_match.group(1).replace("_", "")),
        "relative_path": relpath,
        "source": RIG_D_TEST_RELPATH,
    }


def _provenance_crosscheck(repo_root: Path) -> dict:
    """Optional read-only cross-checks against the recorded gateway tree
    (absent trees are recorded, never fatal: the transcribed provenance in
    this module is the fallback record)."""
    gateway = repo_root / RIG_D_GATEWAY_RELPATH
    if not gateway.is_dir():
        return {"status": "gateway_tree_absent", "checked": []}
    checked: dict[str, object] = {}
    omc_version = gateway / "builds" / "omc_version.txt"
    if omc_version.is_file():
        checked["omc_version_matches_record"] = (
            omc_version.read_text(encoding="utf-8").strip()
            == PINNED_OMC_BUILD_STRING
        )
    for relname in ("builds/git_commit.txt", "rigD_trim_parity_git_commit.txt"):
        commit_file = gateway / relname
        if commit_file.is_file():
            checked[f"{relname}_matches_source_commit"] = (
                commit_file.read_text(encoding="utf-8").strip()
                == RIG_D_SOURCE_COMMIT
            )
    return {"status": "performed", "checked": checked}


def build_evidence(
    repo_root: Path | None = None,
    *,
    full_csv: Path | None = None,
    evidence_dir: Path | None = None,
) -> tuple[dict, list[Path]]:
    """Build the committed evidence package from the evidence-of-record.

    Requires the full rig-D CSV (the recorded gitignored path by default).
    Writes the projection CSV and the manifest JSON into ``evidence_dir``
    (default: ``tests/fixtures/segmented_kelvin_parity/evidence/``) and
    returns ``(manifest, written_paths)``. Deterministic: the same inputs
    regenerate a byte-identical package apart from ``generated_utc``.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    evidence = Path(evidence_dir) if evidence_dir is not None else root / EVIDENCE_DIR_RELPATH
    full_path = (
        Path(full_csv) if full_csv is not None else root / RIG_D_FULL_CSV_RELPATH
    )
    if not full_path.is_file():
        raise ParityEvidenceError(
            f"the rig-D evidence-of-record is absent: {full_path}; the "
            "package is cut from the existing full run (gitignored "
            "00runs/ tree) and building it without that file is refused -- "
            "there is no simulation fallback in this module"
        )
    # Never write the evidence package into a published record tree: a
    # misdirected evidence_dir must not land in 00runs/freq|startup-*|transients-*.
    from helpers.published_tree_guard import refuse_published_tree_path

    refuse_published_tree_path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)

    pins = _extract_test_pins(root)

    fixture_path = root / RIG_D_FIXTURE_RELPATH
    fixture = _read_committed_csv(fixture_path)
    fixture_data_raw = fixture["raw_lines"][1:]
    rig_c_path = root / RIG_C_FIXTURE_RELPATH
    rig_c = _read_committed_csv(rig_c_path)

    scan = _scan_wide_csv(
        full_path, ACCEPTANCE_COLUMNS, sought_raw_lines=fixture_data_raw
    )

    # The full CSV must agree with the values pinned by the parity test.
    problems: list[str] = []
    if scan["sha256"] != pins["sha256"]:
        problems.append(
            f"full-CSV sha256 {scan['sha256']} != test-pinned "
            f"{pins['sha256']} ({pins['source']})"
        )
    if scan["bytes"] != pins["bytes"]:
        problems.append(
            f"full-CSV byte count {scan['bytes']} != test-pinned "
            f"{pins['bytes']} ({pins['source']})"
        )
    if problems:
        raise ParityEvidenceError(
            "the evidence-of-record disagrees with the parity test's "
            "pinned values:\n  " + "\n  ".join(problems)
        )

    # Locate each committed spotcheck row inside the full CSV (byte-exact).
    spotchecks = []
    for i, target_time in enumerate(RIG_D_SPOTCHECK_TIMES_S):
        row = fixture["rows"][i]
        raw_line = fixture_data_raw[i]
        matched = scan["sought_matches"][i]
        if not matched:
            raise ParityEvidenceError(
                f"committed fixture row {i} (t = {target_time:g} s) does not "
                "appear byte-verbatim in the full CSV; the fixture is no "
                "longer a slice of the evidence-of-record"
            )
        parsed_time = float(row[0])
        if abs(parsed_time - target_time) > RIG_D_SPOTCHECK_MATCH_TOL_S:
            raise ParityEvidenceError(
                f"committed fixture row {i}: time {parsed_time:g} s is "
                f"{abs(parsed_time - target_time):g} s from the expected "
                f"spotcheck time {target_time:g} s "
                f"(match tolerance {RIG_D_SPOTCHECK_MATCH_TOL_S:g} s)"
            )
        spotchecks.append(
            {
                "index": i,
                "time_s": parsed_time,
                "phase": RIG_D_SPOTCHECK_PHASES[i],
                "source_line_indices_1based": [m + 1 for m in matched],
                "raw_line_sha256": raw_line_digest(raw_line),
                "values": _acceptance_values(
                    row, fixture["header_fields"], ACCEPTANCE_COLUMNS
                ),
            }
        )

    # Canonical projection: verbatim acceptance fields for all data rows.
    projection_path = evidence / RIG_D_PROJECTION_NAME
    _write_projection(projection_path, ACCEPTANCE_COLUMNS, scan["projection_rows"])
    projection_sha = sha256_file(projection_path)
    projection_header_digest = canonical_line_digest(
        ",".join(ACCEPTANCE_COLUMNS)
    )
    try:
        projection_relpath = str(projection_path.relative_to(root))
    except ValueError:  # evidence dir outside the checkout (test rigs)
        projection_relpath = str(projection_path)

    rig_c_tolerances = {
        "strict": {
            "dt_limit_k": STRICT_DT_LIMIT_K,
            "rel_power_limit": STRICT_REL_POWER_LIMIT,
            "temperature_offset_k": STRICT_TEMPERATURE_OFFSET_K,
            "applies_to": RIG_C_STRICT_APPLIES_TO,
            "result": STRICT_REPLAY_RESULT["result"],
        },
        "cross_toolchain_bars_o6": {
            "dt_limit_k": CROSS_TOOLCHAIN_DT_LIMIT_K,
            "rel_power_limit": CROSS_TOOLCHAIN_REL_POWER_LIMIT,
            "decision": (
                "O6 (2026-09-08, amended REV-a5b4de4-01): dt bar one "
                "decade looser than strict; rel-power bar 1e-7 -- the "
                "original one-decade default 1e-8 measured red on a "
                "fresh standard-build session (1.0369e-8)"
            ),
        },
    }

    manifest: dict = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "kind": EVIDENCE_KIND,
        "title": (
            "Self-contained numerical evidence for the segmented-core "
            "kelvin-parity runs (TASK-20260908-01 P3)"
        ),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": {
            "module": "helpers.segmented_parity_evidence",
            "note": (
                "generated_utc and generator identify the exporting "
                "checkout; they are the only fields that change on a "
                "deterministic re-export at a newer tree (REV-1ecfbd4-01 "
                "precedent: the self-reference is documented, not hidden)"
            ),
        },
        "digest_conventions": {
            "canonical_row_text": (
                "comma-joined quote-stripped fields + trailing newline, utf-8"
            ),
            "row_digest": "sha256 hex of the canonical row text",
            "merkle_root": (
                "sha256 hex of the lowercase row digests concatenated in "
                "row order (no separators)"
            ),
            "raw_line_digest": (
                "sha256 hex of the raw line bytes including the terminating "
                "newline (byte-exact)"
            ),
        },
        "cases": {
            RIG_D_CASE_KEY: {
                "model": RIG_D_MODEL,
                "core": "msrr.r5x5_z10 (5x5 radial x 10 axial segments)",
                "source_commit": RIG_D_SOURCE_COMMIT,
                "source_commit_note": (
                    "the run was cut at this commit by the gateway worker "
                    "(TASK-20261001-01 PSAR-basis re-cut); the tracked "
                    "core/ change that follows it (core/SegmentedMSR.mo QA "
                    "expectation constants and doc strings only) rebuilds "
                    "rigs A/C/D byte-identically (gw-t1001-qa-wt), so the "
                    "compiled rig at HEAD reproduces the run"
                ),
                "toolchain": dict(RIG_D_TOOLCHAIN),
                "run": {
                    "stop_time_s": RIG_D_STOP_TIME_S,
                    "build_command": RIG_D_BUILD_COMMAND,
                    "run_command": RIG_D_RUN_COMMAND,
                    "run_command_recorded_in": RIG_D_RUN_COMMAND_RECORD,
                    "solver": dict(RIG_D_SOLVER_NOTES),
                    **RIG_D_RUN_WINDOW,
                },
                "evidence_of_record": {
                    "relative_path": RIG_D_FULL_CSV_RELPATH,
                    "committed": False,
                    "bytes": scan["bytes"],
                    "sha256": scan["sha256"],
                    "columns": scan["n_columns"],
                    "data_rows": scan["n_data_rows"],
                    "header_bytes": len(scan["header_raw_line"]),
                    "header_raw_line_sha256": raw_line_digest(
                        scan["header_raw_line"]
                    ),
                    "note": (
                        "gitignored 00runs/ file (GitHub rejects files "
                        "> 100 MB); the committed fixture is a byte-verbatim "
                        "spotcheck slice of it"
                    ),
                },
                "committed_fixture": {
                    "relative_path": RIG_D_FIXTURE_RELPATH,
                    "bytes": fixture["bytes"],
                    "sha256": fixture["sha256"],
                    "kind": "byte_verbatim_spotcheck_slice",
                    "header_bytes": len(fixture["header_raw_line"]),
                    "data_rows": len(fixture["rows"]),
                    "spotcheck_match_tol_s": RIG_D_SPOTCHECK_MATCH_TOL_S,
                    "tie_rule": "nearest row; ties -> highest index",
                    "spotchecks": spotchecks,
                },
                "projection": {
                    "relative_path": projection_relpath,
                    "sha256": projection_sha,
                    "columns": list(ACCEPTANCE_COLUMNS),
                    "data_rows": scan["n_data_rows"],
                    "header_sha256_canonical": projection_header_digest,
                    "per_row_sha256": scan["row_digests"],
                    "merkle_root": merkle_root(scan["row_digests"]),
                    "extraction": (
                        "verbatim strings from the full CSV (no float "
                        "re-serialization); all data rows"
                    ),
                },
                "acceptance_field_extrema": {
                    col: scan["extrema"][col] for col in ACCEPTANCE_COLUMNS
                },
                "tolerances": {
                    "strict": {
                        "dt_limit_k": STRICT_DT_LIMIT_K,
                        "rel_power_limit": STRICT_REL_POWER_LIMIT,
                        "temperature_offset_k": STRICT_TEMPERATURE_OFFSET_K,
                        "applies_to": (
                            "the pinned v1.27.0-cmake toolchain (the "
                            "evidence toolchain); release-required with "
                            "zero skips"
                        ),
                        **STRICT_REPLAY_RESULT,
                    },
                    "cross_toolchain_observation": dict(
                        CROSS_TOOLCHAIN_OBSERVATION
                    ),
                    "cross_toolchain_bars_o6": {
                        "dt_limit_k": CROSS_TOOLCHAIN_DT_LIMIT_K,
                        "rel_power_limit": CROSS_TOOLCHAIN_REL_POWER_LIMIT,
                        "decision": (
                            "O6 (2026-09-08, amended REV-a5b4de4-01): dt "
                            "bar one decade looser than strict; rel-power "
                            "bar 1e-7 -- the original one-decade default "
                            "1e-8 measured red on a fresh standard-build "
                            "session (1.0369e-8); the comparison on "
                            "non-cmake builds runs at these bars instead "
                            "of skipping"
                        ),
                    },
                },
            },
            RIG_C_CASE_KEY: {
                "model": RIG_C_MODEL,
                "core": "msrr.r1_10seg (10-segment 1R core)",
                "role": (
                    "equivalent 10Seg kelvin-parity case at the established "
                    "50 s rig duration; the committed fixture IS the full "
                    "acceptance-field grid"
                ),
                "fixture_committed_in": RIG_C_FIXTURE_COMMIT,
                "toolchain": dict(RIG_C_TOOLCHAIN),
                "toolchain_note": (
                    "the pinned-cmake gateway re-cut toolchain "
                    "(TASK-20261001-01, same job as rig D); the "
                    "top-level omc_build_record describes the rig-D "
                    "cmake evidence-of-record"
                ),
                "source_note": (
                    "same-era post-conversion (kelvin) fixture: zero "
                    "temperature offset; the comparison is same-build "
                    "self-consistency under the GOLDEN_PROTOCOL rebuild"
                ),
                "run": {
                    "stop_time_s": RIG_C_STOP_TIME_S,
                    "protocol": (
                        "GOLDEN_PROTOCOL: buildModel(tolerance = 1e-10), "
                        "runtime flags only "
                        "(-outputFormat=csv -r=<name>.csv -stopTime=50), "
                        "default integrator output grid"
                    ),
                    "recorded_in": (
                        "tests/test_kelvin_parity.py (GOLDEN_PROTOCOL and "
                        "the rig-C section)"
                    ),
                },
                "evidence_of_record": {
                    "relative_path": RIG_C_FIXTURE_RELPATH,
                    "committed": True,
                    "bytes": rig_c["bytes"],
                    "sha256": rig_c["sha256"],
                    "columns": rig_c["header_fields"],
                    "data_rows": len(rig_c["rows"]),
                    "time_first_s": rig_c["times"][0],
                    "time_last_s": rig_c["times"][-1],
                    "note": (
                        "no gitignored dependency: the full 8-column grid "
                        "is committed, so this case is verifiable from the "
                        "archive alone"
                    ),
                },
                "projection": {
                    "relative_path": RIG_C_FIXTURE_RELPATH,
                    "sha256": rig_c["sha256"],
                    "columns": rig_c["header_fields"],
                    "data_rows": len(rig_c["rows"]),
                    "per_row_sha256": rig_c["row_digests"],
                    "merkle_root": merkle_root(rig_c["row_digests"]),
                    "extraction": (
                        "the committed fixture doubles as the projection "
                        "(its 8 columns are exactly the acceptance fields)"
                    ),
                },
                "tolerances": rig_c_tolerances,
            },
        },
        "qa_models": dict(QA_MODELS_RECORDED),
        "omc_build_record": dict(OMC_BUILD_RECORD_RECORDED),
        "verifier_reruns": {
            name: dict(hook) for name, hook in VERIFIER_RERUN_HOOKS.items()
        },
        "owner_decisions": {
            "O6": (
                "cross-toolchain tolerance: dt 1e-5 K (the strict bar "
                "loosened one decade) and rel power 1e-7, documented in "
                "the parity test docstring; on the pinned v1.27.0-cmake "
                "build the strict replay is release-required with zero "
                "skips, elsewhere the looser comparison runs instead of "
                "skipping all comparison. Rationale (REV-a5b4de4-01): the "
                "original O6 default rel power 1e-8 -- the strict bars "
                "loosened one decade -- was measured red on a fresh "
                "standard-build session (max relative sumInW deviation "
                "1.0369e-8 at t = 39.8 s, a ~4.77 mW bookkeeping offset "
                "whose relative measure varies a few percent between "
                "rebuild sessions), so the 1e-7 bar restores an order of "
                "magnitude of headroom (9.6x against the measured "
                "1.0369e-8, >10x against the original 9.8e-9 "
                "observation); the "
                "worst |dT| 4.761e-6 K keeps 2.1x margin on the 1e-5 K "
                "dt bar, bit-identical to the recorded observation"
            ),
        },
        "release_contract": {
            "parity_marker": PARITY_MARKER,
            "required_zero_skip_on": "OpenModelica cmake builds (v1.27.0-cmake)",
            "looser_comparison_on": "standard (non-cmake) builds",
            "cross_toolchain_bars": {
                "dt_limit_k": CROSS_TOOLCHAIN_DT_LIMIT_K,
                "rel_power_limit": CROSS_TOOLCHAIN_REL_POWER_LIMIT,
            },
        },
        "provenance_crosscheck": _provenance_crosscheck(root),
    }

    manifest_path = evidence / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest, [projection_path, manifest_path]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_evidence(
    repo_root: Path | None = None,
    *,
    evidence_dir: Path | None = None,
    full_csv: Path | None = None,
    require_full_csv: bool = False,
) -> dict:
    """Verify the committed evidence package.

    FULL mode when the full rig-D CSV exists (the recorded gitignored path
    by default, ``--full-csv`` override): recomputes the projection and every
    row digest from the full CSV and re-proves the committed fixture slice.

    COMPACT mode otherwise (the archive-reviewer path): validates the
    committed projection, the committed fixture, the rig-C case, and the
    manifest against the parity test's pinned values -- without the full
    CSV. The absence is reported loudly and ``full_csv_present`` is False in
    the verdict; ``require_full_csv=True`` turns the absence into a hard
    failure.

    The rig-D projection is package payload and is validated from the
    evidence directory beside the manifest (REV-a5b4de4-02); the committed
    rig-D/rig-C fixtures and the full CSV resolve from ``repo_root`` via
    the manifest's recorded relative paths. The verdict records every
    resolved path.

    Raises :class:`ParityEvidenceError` naming every mismatch found.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    evidence = Path(evidence_dir) if evidence_dir is not None else root / EVIDENCE_DIR_RELPATH
    manifest_path = evidence / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ParityEvidenceError(f"evidence manifest is absent: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParityEvidenceError(
            f"evidence manifest is not valid JSON: {manifest_path}: {exc}"
        ) from exc
    problems: list[str] = []

    def _require(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    _require(
        manifest.get("schema_version") == EVIDENCE_SCHEMA_VERSION,
        f"manifest schema_version is {manifest.get('schema_version')!r}, "
        f"expected {EVIDENCE_SCHEMA_VERSION}",
    )
    _require(
        manifest.get("kind") == EVIDENCE_KIND,
        f"manifest kind is {manifest.get('kind')!r}, expected {EVIDENCE_KIND!r}",
    )
    cases = manifest.get("cases") or {}
    for key in (RIG_D_CASE_KEY, RIG_C_CASE_KEY):
        _require(key in cases, f"manifest is missing case {key!r}")

    full_present = False
    d_manifest = cases.get(RIG_D_CASE_KEY) or {}
    d_proj_rel = (d_manifest.get("projection") or {}).get("relative_path")
    d_full_rel = (d_manifest.get("evidence_of_record") or {}).get(
        "relative_path"
    )
    # The rig-D projection is package payload: it is validated from the
    # evidence directory BESIDE THE MANIFEST, not from --repo-root
    # (REV-a5b4de4-02). Resolving it against the repository root instead
    # let a modified package copy under --evidence-dir validate against
    # the untouched tracked projection (a false OK on the tamper surface).
    projection_path: Path | None = None
    if d_proj_rel:
        projection_path = evidence / Path(str(d_proj_rel)).name

    # Resolve the full CSV: explicit override > manifest-recorded path.
    if full_csv is not None:
        full_path = Path(full_csv)
        full_present = full_path.is_file()
        if not full_present and require_full_csv:
            raise ParityEvidenceError(
                f"--require-full-csv: the full CSV is absent at the "
                f"requested path {full_path}"
            )
    else:
        full_path = root / str(d_full_rel or RIG_D_FULL_CSV_RELPATH)
        full_present = full_path.is_file()
        if not full_present and require_full_csv:
            raise ParityEvidenceError(
                f"--require-full-csv: the full CSV is absent at the "
                f"recorded path {full_path}"
            )

    mode = "full" if full_present else "compact"

    # ---- Case rig D: the committed fixture (always checked) ---------------
    fixture_rel = (d_manifest.get("committed_fixture") or {}).get(
        "relative_path"
    ) or RIG_D_FIXTURE_RELPATH
    fixture_path = root / fixture_rel
    if not fixture_path.is_file():
        problems.append(f"committed rig-D fixture is absent: {fixture_path}")
    else:
        fixture = _read_committed_csv(fixture_path)
        cf = d_manifest.get("committed_fixture") or {}
        _require(
            fixture["sha256"] == cf.get("sha256"),
            f"rig-D fixture sha256 drifted: actual {fixture['sha256']} vs "
            f"manifest {cf.get('sha256')}",
        )
        _require(
            fixture["bytes"] == cf.get("bytes"),
            f"rig-D fixture byte count drifted: actual {fixture['bytes']} vs "
            f"manifest {cf.get('bytes')}",
        )
        spotchecks = cf.get("spotchecks") or []
        _require(
            len(spotchecks) == len(fixture["rows"]),
            f"rig-D fixture carries {len(fixture['rows'])} data rows vs "
            f"{len(spotchecks)} manifest spotchecks",
        )
        for i, spot in enumerate(spotchecks):
            if i >= len(fixture["rows"]):
                break
            raw = fixture["raw_lines"][1:][i]
            actual_digest = raw_line_digest(raw)
            _require(
                actual_digest == spot.get("raw_line_sha256"),
                f"rig-D fixture spotcheck {i} raw-line digest drifted: "
                f"actual {actual_digest} vs manifest "
                f"{spot.get('raw_line_sha256')}",
            )
            row = fixture["rows"][i]
            _require(
                abs(float(row[0]) - float(spot.get("time_s", float("nan"))))
                <= RIG_D_SPOTCHECK_MATCH_TOL_S,
                f"rig-D fixture spotcheck {i}: time {row[0]} vs manifest "
                f"{spot.get('time_s')}",
            )
            expected_values = spot.get("values") or {}
            actual_values = _acceptance_values(
                row, fixture["header_fields"], ACCEPTANCE_COLUMNS
            )
            for col in ACCEPTANCE_COLUMNS:
                _require(
                    expected_values.get(col) == actual_values.get(col),
                    f"rig-D fixture spotcheck {i}: column {col} value "
                    f"{actual_values.get(col)!r} vs manifest "
                    f"{expected_values.get(col)!r}",
                )

    # ---- Case rig D: the committed projection (always checked) ------------
    if projection_path is None:
        problems.append("manifest rig-D case records no projection path")
    elif not projection_path.is_file():
        problems.append(
            "committed rig-D projection is absent from the evidence "
            f"package directory: {projection_path} (the manifest records "
            f"{d_proj_rel!r}; the projection beside the manifest is the "
            "file that is validated -- a manifest-only package copy is "
            "refused rather than silently checking the repository copy)"
        )
    else:
        projection = _read_committed_csv(projection_path)
        pj = d_manifest.get("projection") or {}
        _require(
            projection["sha256"] == pj.get("sha256"),
            f"rig-D projection sha256 drifted: actual "
            f"{projection['sha256']} vs manifest {pj.get('sha256')}",
        )
        _require(
            projection["header_fields"] == list(pj.get("columns") or []),
            f"rig-D projection columns drifted: actual "
            f"{projection['header_fields']} vs manifest {pj.get('columns')}",
        )
        _require(
            len(projection["rows"]) == pj.get("data_rows"),
            f"rig-D projection row count drifted: actual "
            f"{len(projection['rows'])} vs manifest {pj.get('data_rows')}",
        )
        actual_digests = projection["row_digests"]
        _require(
            actual_digests == list(pj.get("per_row_sha256") or []),
            "rig-D projection per-row digests drifted from the manifest "
            "(tampered projection row)",
        )
        actual_root = merkle_root(actual_digests)
        _require(
            actual_root == pj.get("merkle_root"),
            f"rig-D projection Merkle root drifted: actual {actual_root} "
            f"vs manifest {pj.get('merkle_root')}",
        )
        # The projection's spotcheck rows must agree with the fixture.
        if fixture_path.is_file():
            fixture = _read_committed_csv(fixture_path)
            spotchecks = (d_manifest.get("committed_fixture") or {}).get(
                "spotchecks"
            ) or []
            for i, spot in enumerate(spotchecks):
                target = float(spot.get("time_s", float("nan")))
                idx = _nearest_index(projection["times"], target)
                _require(
                    abs(projection["times"][idx] - target)
                    <= RIG_D_SPOTCHECK_MATCH_TOL_S,
                    f"rig-D projection has no row within "
                    f"{RIG_D_SPOTCHECK_MATCH_TOL_S:g} s of spotcheck "
                    f"t = {target:g} s",
                )
                expected_values = spot.get("values") or {}
                proj_row = projection["rows"][idx]
                for col, token in zip(ACCEPTANCE_COLUMNS, proj_row):
                    _require(
                        expected_values.get(col) == token,
                        f"rig-D projection vs fixture at t = {target:g} s: "
                        f"column {col} {token!r} vs manifest "
                        f"{expected_values.get(col)!r}",
                    )

    # ---- Case rig C: committed-complete evidence (always checked) ---------
    c_manifest = cases.get(RIG_C_CASE_KEY) or {}
    c_rel = (c_manifest.get("evidence_of_record") or {}).get("relative_path") or (
        RIG_C_FIXTURE_RELPATH
    )
    rig_c_path = root / c_rel
    if not rig_c_path.is_file():
        problems.append(f"rig-C fixture is absent: {rig_c_path}")
    else:
        rig_c = _read_committed_csv(rig_c_path)
        ce = c_manifest.get("evidence_of_record") or {}
        cp = c_manifest.get("projection") or {}
        _require(
            rig_c["sha256"] == ce.get("sha256") == cp.get("sha256"),
            f"rig-C fixture sha256 drifted: actual {rig_c['sha256']} vs "
            f"manifest evidence {ce.get('sha256')} / projection "
            f"{cp.get('sha256')}",
        )
        _require(
            rig_c["header_fields"] == list(ce.get("columns") or []),
            f"rig-C fixture columns drifted: actual "
            f"{rig_c['header_fields']} vs manifest {ce.get('columns')}",
        )
        _require(
            len(rig_c["rows"]) == ce.get("data_rows") == cp.get("data_rows"),
            f"rig-C row count drifted: actual {len(rig_c['rows'])} vs "
            f"manifest evidence {ce.get('data_rows')} / projection "
            f"{cp.get('data_rows')}",
        )
        actual_digests = rig_c["row_digests"]
        _require(
            actual_digests == list(cp.get("per_row_sha256") or []),
            "rig-C per-row digests drifted from the manifest (tampered row)",
        )
        actual_root = merkle_root(actual_digests)
        _require(
            actual_root == cp.get("merkle_root"),
            f"rig-C Merkle root drifted: actual {actual_root} vs manifest "
            f"{cp.get('merkle_root')}",
        )

    # ---- Manifest vs the parity test's pinned values -----------------------
    try:
        pins = _extract_test_pins(root)
        eor = d_manifest.get("evidence_of_record") or {}
        _require(
            eor.get("sha256") == pins["sha256"],
            f"manifest full-CSV sha256 {eor.get('sha256')} != test-pinned "
            f"{pins['sha256']}",
        )
        _require(
            eor.get("bytes") == pins["bytes"],
            f"manifest full-CSV byte count {eor.get('bytes')} != test-pinned "
            f"{pins['bytes']}",
        )
        pin_crosscheck = "performed"
    except ParityEvidenceError as exc:
        pins = None
        pin_crosscheck = f"skipped ({exc})"

    # ---- FULL mode: recompute everything from the full CSV ----------------
    recomputed = False
    if full_present:
        fixture_raw = (
            _read_committed_csv(root / fixture_rel)["raw_lines"][1:]
            if fixture_path.is_file()
            else []
        )
        scan = _scan_wide_csv(
            full_path, ACCEPTANCE_COLUMNS, sought_raw_lines=fixture_raw
        )
        eor = d_manifest.get("evidence_of_record") or {}
        pj = d_manifest.get("projection") or {}
        cf = d_manifest.get("committed_fixture") or {}
        spotchecks = cf.get("spotchecks") or []
        _require(
            scan["bytes"] == eor.get("bytes"),
            f"full-CSV byte count drifted: actual {scan['bytes']} vs manifest "
            f"{eor.get('bytes')}",
        )
        _require(
            scan["sha256"] == eor.get("sha256"),
            f"full-CSV sha256 drifted: actual {scan['sha256']} vs manifest "
            f"{eor.get('sha256')}",
        )
        _require(
            scan["n_data_rows"] == eor.get("data_rows"),
            f"full-CSV data-row count drifted: actual {scan['n_data_rows']} "
            f"vs manifest {eor.get('data_rows')}",
        )
        _require(
            scan["row_digests"] == list(pj.get("per_row_sha256") or []),
            "recomputed projection row digests drifted from the manifest "
            "(tampered full-CSV acceptance field)",
        )
        _require(
            merkle_root(scan["row_digests"]) == pj.get("merkle_root"),
            "recomputed Merkle root drifted from the manifest",
        )
        recomputed_projection = (
            [list(ACCEPTANCE_COLUMNS)]
            + [[col for col in row] for row in scan["projection_rows"]]
        )
        if projection_path is not None and projection_path.is_file():
            committed_lines = (
                projection_path.read_text(encoding="utf-8").splitlines()
            )
            rebuilt_lines = [
                ",".join(row) for row in recomputed_projection
            ]
            _require(
                committed_lines == rebuilt_lines,
                "the committed projection no longer matches the full CSV "
                "(recomputation mismatch)",
            )
        for i, spot in enumerate(spotchecks):
            if i >= len(fixture_raw):
                break
            matched = scan["sought_matches"][i]
            recorded = spot.get("source_line_indices_1based") or []
            _require(
                [m + 1 for m in matched] == recorded,
                f"fixture spotcheck {i} no longer sits byte-verbatim at the "
                f"recorded full-CSV line(s) {recorded} (matches: "
                f"{[m + 1 for m in matched]})",
            )
        recomputed = True

    verdict = {
        "mode": mode,
        "full_csv_present": full_present,
        "full_csv_path": str(full_path),
        "recomputed_from_full_csv": recomputed,
        "require_full_csv": require_full_csv,
        "manifest": str(manifest_path),
        "evidence_dir": str(evidence),
        "projection_path": (
            str(projection_path) if projection_path is not None else None
        ),
        "path_resolution": {
            "projection": "evidence_dir (the file beside the manifest)",
            "rig_d_fixture": str(fixture_path),
            "rig_c_fixture": str(rig_c_path),
            "full_csv": str(full_path),
            "note": (
                "the rig-D projection is package payload and is validated "
                "from the evidence directory beside the manifest; the "
                "committed rig-D/rig-C fixtures and the full CSV are "
                "repository artifacts resolved from --repo-root via the "
                "manifest's recorded relative paths, independent of "
                "--evidence-dir"
            ),
        },
        "test_pin_crosscheck": pin_crosscheck,
        "cases_checked": [RIG_D_CASE_KEY, RIG_C_CASE_KEY],
        "problems": problems,
        "ok": not problems,
    }
    if problems:
        raise ParityEvidenceError(
            f"evidence verification FAILED ({len(problems)} problem(s)):\n  "
            + "\n  ".join(problems)
        )
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="segmented_parity_evidence",
        description=(
            "Build and verify the self-contained numerical evidence package "
            "for the segmented-core (1r10seg / 5x5) kelvin-parity runs: a "
            "committed manifest + canonical projection cut from the "
            "gitignored 123,427,812 B rig-D evidence-of-record, with "
            "per-row digests, extrema/tolerance results, QA summaries, and "
            "a verifier that recomputes everything whenever the full CSV "
            "exists and validates the committed package otherwise."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser(
        "build",
        help="cut the evidence package from the full rig-D CSV",
    )
    p_build.add_argument(
        "--full-csv",
        type=Path,
        default=None,
        help=(
            "path to the full rig-D CSV (default: the recorded gitignored "
            f"path {RIG_D_FULL_CSV_RELPATH}); required to exist"
        ),
    )
    p_build.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help=(
            "package destination (default: "
            f"{EVIDENCE_DIR_RELPATH})"
        ),
    )
    p_build.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repository root (default: the checkout containing this module)",
    )

    p_verify = sub.add_parser(
        "verify",
        help=(
            "verify the committed package (full recomputation when the "
            "full CSV exists; compact manifest validation otherwise)"
        ),
    )
    p_verify.add_argument(
        "--full-csv",
        type=Path,
        default=None,
        help=(
            "full-CSV path override (default: the recorded gitignored "
            "path; its absence selects compact mode)"
        ),
    )
    p_verify.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help=(
            "evidence package directory: the manifest AND the rig-D "
            "projection beside it are validated from this directory, so a "
            "copied package is verified exactly as copied (a manifest-only "
            "copy fails loudly instead of silently checking the tracked "
            "projection); the committed rig-D/rig-C fixtures and the full "
            f"CSV still resolve from --repo-root (default: "
            f"{EVIDENCE_DIR_RELPATH})"
        ),
    )
    p_verify.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repository root (default: the checkout containing this module)",
    )
    p_verify.add_argument(
        "--require-full-csv",
        action="store_true",
        help=(
            "fail instead of degrading to compact mode when the full CSV "
            "is absent (workstation-side strictness)"
        ),
    )
    p_verify.add_argument(
        "--json",
        action="store_true",
        help="print the verdict as JSON instead of a human summary",
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        try:
            manifest, written = build_evidence(
                args.repo_root,
                full_csv=args.full_csv,
                evidence_dir=args.evidence_dir,
            )
        except ParityEvidenceError as exc:
            print(f"build FAILED: {exc}", file=sys.stderr)
            return 1
        case = manifest["cases"][RIG_D_CASE_KEY]
        print("evidence package written:")
        for path in written:
            print(f"  {path}")
        print(
            f"  full CSV: {case['evidence_of_record']['bytes']} B, "
            f"sha256 {case['evidence_of_record']['sha256']}"
        )
        print(
            f"  projection rows: {case['projection']['data_rows']} "
            f"({case['projection']['merkle_root'][:12]}...)"
        )
        print(
            f"  spotchecks bound: "
            f"{len(case['committed_fixture']['spotchecks'])} raw-line "
            "digests recorded"
        )
        return 0

    # verify
    try:
        verdict = verify_evidence(
            args.repo_root,
            evidence_dir=args.evidence_dir,
            full_csv=args.full_csv,
            require_full_csv=args.require_full_csv,
        )
    except ParityEvidenceError as exc:
        print(f"verify FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        print(
            f"evidence verification OK "
            f"(mode {verdict['mode']}; full_csv_present="
            f"{verdict['full_csv_present']}; "
            f"recomputed={verdict['recomputed_from_full_csv']}; "
            f"test_pin_crosscheck={verdict['test_pin_crosscheck']})"
        )
        print(f"  manifest: {verdict['manifest']}")
        if verdict.get("projection_path"):
            print(f"  projection validated: {verdict['projection_path']}")
        if not verdict["full_csv_present"]:
            print(
                "  NOTE: the full CSV is absent at the recorded path -- "
                "compact manifest validation only; the projection/row "
                "digests were NOT recomputed from the evidence-of-record."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
