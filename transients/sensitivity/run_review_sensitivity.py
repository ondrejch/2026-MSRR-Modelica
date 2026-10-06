#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewer-response sensitivity study: hA exponent + linked property sets.

Answers ANUCENE-D-26-00942 reviewer items R1.3a/R1.3b (TASK-20260901-01
Phase 1): how does the FF^hAExp film-convection power law and the adopted
constant-property set change the 1R nominal-power reactivity-step response?

Modeling approach (documented per the task card)
------------------------------------------------
Every case is one legacy ``simulate()`` run of
``MSRR.MSRRuhxNominalTrimThermalSS`` (the same vehicle the production
transient step cases use), driven through OpenModelica ``-override=``
payloads only -- no source-file patching and no generated model variants.
The 1 MW setpoint row from ``core/init/setpoints_1r.csv`` is applied through
the shared ``transients.run_nonlinear_steps.load_setpoints`` /
``build_setpoint_override`` helpers (identical to the production step
route), the reactivity step of ``--step_pcm`` (default 100 pcm) fires at
``--step_time`` via ``externalReactivityAmplitude[2]`` /
``externalReactivityStepTime[2]``, and the new component parameters land as
``core1R.fuelchannel.hAExp=<e>,heatExchanger.hAExp=<e>`` (both power-law
twins are varied together). Each case runs in its own directory under the
output tree; OpenModelica build artifacts are removed after a successful
run, keeping the ``.mos``, the omc logs, and the result CSV. Every case's
result slot is held under an exclusive flock claim
(``helpers.run_results.acquire_result_claim``) for the whole
launch-validate window, and a result CSV only counts as finished when its
tail reaches the requested stop time.

Physics notes that shape the default grids
------------------------------------------
1. hA is normalized at nominal flow: ``hA = hAnom*FF^hAExp`` with FF = 1
   during the whole nominal-flow step, so ``FF^hAExp == 1`` for EVERY
   exponent and grid A at ``--flow_fractions 1.0`` is exactly degenerate by
   construction. That invariance is itself a reportable result, so the
   nominal-flow rows stay in the default grid; the default additionally
   runs the same exponent grid at a reduced prescribed flow (0.66, matching
   the production ``flow_66pct`` operating point) where the exponent is
   observable. Reduced-flow cases pin ``primaryPump.freeConvFF =
   primaryPump.rampUpTo[1] = FF`` so the flow fraction is exactly constant
   from t = 0.
2. The production 1R model sets ``kFuel = 0`` (axial conduction neglected),
   so the carded ``kFuel x {0.9, 1.1}`` rows are exactly inert
   (0.9*0 = 1.1*0 = 0). They are still run and reported -- the zero
   sensitivity is the honest answer -- and an explicitly labeled extra
   linked set scales the salt-side film conductances
   (``core1R.hAnom`` + ``heatExchanger.hApNom``) by the same
   factors as the informative film-coefficient counterpart
   (disable with ``--skip_hanom_set``).

3. The ``rho_cp_fuel`` linked set scales EACH of ``rhoFuel`` and
   ``scpFuel`` by the listed factor, so the volumetric heat capacity
   rho_f*c_p,f moves by the factor squared (x0.81 / x1.21 for the default
   0.9 / 1.1 scales).

Matched no-step twins (review 2026-10-02 M8)
--------------------------------------------
Every case starts FixedStart from the FF = 1, nominal-hA 1 MW setpoint row
and steps at ``--step_time`` (2000 s by default, about 1.2-1.6 graphite time
constants in), so the reduced-flow and rescaled cases are still drifting
when the step fires. Each case is therefore paired with a no-step twin
(``<case_id>_nostep``: identical overrides, ``externalReactivityAmplitude[2]
= 0``), and the reported outlet rise is the step response net of that drift:
``delta_tout_C = [Tout_step(tail) - Tout_step(pre)] - [Tout_nostep(tail) -
Tout_nostep(pre)]``. The uncorrected difference and the twin's drift are
kept as ``delta_tout_raw_C`` and ``nostep_delta_tout_C``. The step runs are
unchanged by the pairing (same step time, same overrides), so every other
column is the same as without it.

Metrics per run (written to ``sensitivity_metrics.csv`` + ``summary.md``,
one compact figure in ``summary.png``): peak power after the step, time of
peak, post-step steady power (tail mean), pre/post steady fuel outlet
temperature and the drift-corrected outlet rise above, and two damping
proxies -- the first-overshoot fraction ``(peak - post_ss)/post_ss`` and the
decay ratio: the height above the post-step steady level of the first local
maximum that follows the first local minimum after the peak, relative to the
first overshoot (``decay_ratio_from_trace``; turning points must clear a
hysteresis band of ``DECAY_RATIO_HYSTERESIS`` times the first overshoot;
NaN when no such second peak lies above the post-step level). Its time is
``time_of_second_peak_s``. ``metadata.json`` records git commit, omc
version, solver, tolerance, and the resolved grids. A failing case (or
twin) is recorded (metadata ``failures`` + stderr) without losing the other
cases' results: the outputs are always written at the end (``summary.png``
only when at least one case produced metrics), and the script exits nonzero
when any case failed.

Usage
-----
    python3.12 -m transients.sensitivity.run_review_sensitivity
    python3.12 -m transients.sensitivity.run_review_sensitivity --smoke
    python3.12 -m transients.sensitivity.run_review_sensitivity \
        --exponents 0.33 0.5 --flow_fractions 0.66 --stop_time 6000

Outputs default to ``00runs/sensitivity-review-2026-09/transients/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from ..run_nonlinear_steps import (
        build_mos,
        build_setpoint_override,
        csv_reaches_stop_time,
        load_setpoints,
    )
except ImportError:  # script-style execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from transients.run_nonlinear_steps import (
        build_mos,
        build_setpoint_override,
        csv_reaches_stop_time,
        load_setpoints,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]

MODEL_NAME = "MSRR.MSRRuhxNominalTrimThermalSS"
INIT_MODE_OVERRIDE = (
    "core1R.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState"
)

DEFAULT_OUT_DIR = REPO_ROOT / "00runs" / "sensitivity-review-2026-09" / "transients"

#: Grid A exponents (task card TASK-20260901-01 Phase 1).
DEFAULT_EXPONENTS = (0.0, 0.25, 0.33, 0.5, 0.8)
#: hAExp = 0.33 is the production default; used as the comparison baseline.
BASELINE_EXPONENT = 0.33
#: Grid A flow fractions: 1.0 documents the exact nominal-flow invariance
#: (FF^hAExp == 1), 0.66 is the reduced-flow point where hAExp is observable.
DEFAULT_FLOW_FRACTIONS = (1.0, 0.66)
#: Grid B linked-set scale factors (nominal 1.0 shared across sets).
DEFAULT_PROPERTY_SCALES = (0.9, 1.1)

#: Production 1R defaults the linked property sets scale (core/MSRR.mo
#: R1MSRRuhx instantiation + MSRR1R fuelchannel/heatExchanger bindings).
NOMINAL_RHO_FUEL = 2600.0
NOMINAL_CP_FUEL = 1776.0
NOMINAL_K_FUEL = 0.0
NOMINAL_HA_NOM_CORE = 4565.0
NOMINAL_HA_NOM_HX_PRIMARY = 7.7966e04

#: Result columns kept by the omc -variableFilter (regex; '.' matches '.').
VARIABLE_FILTER = (
    "time|core1R.powerblock.reactorPower|core1R.tempOut.T"
    "|core1R.fuelchannel.fuelNode2.T|core1R.fuelchannel.hA"
    "|heatExchanger.hApn|primaryPump.flowFrac.FF"
)

#: omc echoes this prefix (one line per quantity) when an ``-override=``
#: quantity could not be set. Rejections of the study-varied parameters below
#: invalidate the case; the echo may use the full override path
#: (``heatExchanger.hAExp``) or a bare name (``hAnom``), so both spellings are
#: matched. Rejections of payload entries already at their model default
#: (e.g. ``heatLossEnabled``) are benign and tolerated exactly as the
#: production route tolerates them.
OVERRIDE_REJECT_MARKER = "not possible to override the following quantity:"

#: The physics parameters every case varies (grid B adds the per-case
#: ``extra_overrides`` keys at run time); omc rejection of any of them
#: (echoed as a full path or a bare name) means the case would run with
#: different physics than planned.
VARIED_OVERRIDE_QUANTITIES = ("core1R.fuelchannel.hAExp", "heatExchanger.hAExp")

METRICS_FIELDS = (
    "case_id",
    "grid",
    "hAExp",
    "flow_fraction",
    "property_set",
    "property_scale",
    "step_pcm",
    "pre_steady_power_W",
    "peak_power_W",
    "time_of_peak_s",
    "post_steady_power_W",
    "overshoot_fraction",
    "decay_ratio",
    "pre_steady_tout_C",
    "post_steady_tout_C",
    "delta_tout_C",
    "hA_W_per_K",
    # Appended by the review 2026-10-02 M8 fix (existing columns keep their
    # names and order).
    "delta_tout_raw_C",
    "nostep_delta_tout_C",
    "time_of_second_peak_s",
)

#: Suffix of the matched no-step twin run of each case (module docstring).
NOSTEP_SUFFIX = "_nostep"

#: Hysteresis band, as a fraction of the first overshoot (peak - post_ss),
#: that a turning point of the post-peak power trace must clear before it
#: counts as a local extremum. It rejects flat sample runs and solver /
#: output-interpolation noise (relative noise ~1e-6 of the power at the
#: default tolerance) while staying two orders of magnitude below the
#: physical second overshoot (~10 % of the first).
DECAY_RATIO_HYSTERESIS = 1e-3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "hA-exponent + linked-property sensitivity study on the 1R "
            "nominal-power reactivity step (reviewer response, "
            "TASK-20260901-01 Phase 1)."
        )
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--core_dir",
        type=Path,
        default=REPO_ROOT / "core",
        help="Directory containing SMD_MSR_Modelica.mo and MSRR.mo",
    )
    parser.add_argument("--omc", type=str, default="omc", help="OpenModelica compiler")
    parser.add_argument(
        "--exponents",
        type=float,
        nargs="+",
        default=list(DEFAULT_EXPONENTS),
        help="Grid A hAExp values (default: 0.0 0.25 0.33 0.5 0.8)",
    )
    parser.add_argument(
        "--flow_fractions",
        type=float,
        nargs="+",
        default=list(DEFAULT_FLOW_FRACTIONS),
        help=(
            "Prescribed primary flow fractions for grid A (default: 1.0 0.66; "
            "at 1.0 the exponent is invisible by construction -- see module "
            "docstring)"
        ),
    )
    parser.add_argument(
        "--property_scales",
        type=float,
        nargs="+",
        default=list(DEFAULT_PROPERTY_SCALES),
        help="Grid B linked-set scale factors besides the shared nominal 1.0",
    )
    parser.add_argument(
        "--skip_gridB",
        action="store_true",
        help="Run only the hA-exponent grid (grid A).",
    )
    parser.add_argument(
        "--skip_hanom_set",
        action="store_true",
        help=(
            "Drop the extra hAnom linked set (the film-conductance surrogate "
            "for the inert kFuel=0 axis)."
        ),
    )
    parser.add_argument(
        "--step_pcm",
        type=float,
        default=100.0,
        help="External reactivity step amplitude in pcm (default: 100)",
    )
    parser.add_argument(
        "--step_time",
        type=float,
        default=2000.0,
        help="Reactivity step insertion time [s] (default: 2000)",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=10000.0,
        help="Simulation stop time [s] (default: 10000)",
    )
    parser.add_argument(
        "--number_of_intervals",
        type=int,
        default=10000,
        help="Output intervals (default: 10000)",
    )
    parser.add_argument(
        "--tolerance", type=float, default=1e-6, help="Solver tolerance (default: 1e-6)"
    )
    parser.add_argument(
        "--method", type=str, default="dassl", help="Solver method (default: dassl)"
    )
    parser.add_argument(
        "--max_step_size",
        type=float,
        default=0.02,
        help="Maximum solver step size (default: 0.02, production value)",
    )
    parser.add_argument(
        "--pre_window",
        type=float,
        default=500.0,
        help="Averaging window [s] before the step for the pre-step baseline",
    )
    parser.add_argument(
        "--tail_window",
        type=float,
        default=1000.0,
        help="Averaging window [s] at end of run for post-step steady values",
    )
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help="Per-run omc wall-clock timeout (0 disables, default: 0)",
    )
    parser.add_argument(
        "--keep_build_artifacts",
        action="store_true",
        help="Keep OpenModelica build artifacts in each run directory.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Reduced end-to-end grid for tests: exponents {0.33, 0.8} at "
            "flow 0.66, one rho/cP property run, short horizon."
        ),
    )
    args = parser.parse_args(argv)

    if args.smoke:
        args.exponents = [0.33, 0.8]
        args.flow_fractions = [0.66]
        args.property_scales = [1.1]
        args.skip_hanom_set = True
        args.step_time = 800.0
        args.stop_time = 2000.0
        args.number_of_intervals = 2000
        args.pre_window = 300.0
        args.tail_window = 300.0

    for exponent in args.exponents:
        if not (0.0 <= exponent <= 1.0):
            parser.error(f"--exponents must lie in [0, 1]; got {exponent}")
    for flow in args.flow_fractions:
        if not (0.0 < flow <= 1.0):
            parser.error(f"--flow_fractions must lie in (0, 1]; got {flow}")
    for scale in args.property_scales:
        if scale <= 0:
            parser.error(f"--property_scales must be > 0; got {scale}")
    if args.step_time <= args.pre_window:
        parser.error("--step_time must exceed --pre_window")
    if args.stop_time <= args.step_time + args.tail_window:
        parser.error("--stop_time must exceed --step_time + --tail_window")
    return args


def _guard_out_dir(out_dir: Path) -> None:
    """Refuse the published record trees (00runs/freq, startup-*, transients-*)."""
    try:
        from helpers.published_tree_guard import refuse_published_tree_path
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from helpers.published_tree_guard import refuse_published_tree_path
    refuse_published_tree_path(out_dir)


def _run_results():
    try:
        from helpers import run_results as rr
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from helpers import run_results as rr
    return rr


def _fmt(value: float) -> str:
    return f"{float(value):.10g}"


def _case_tag(value: float) -> str:
    return _fmt(value).replace(".", "p").replace("-", "m")


def build_case_plan(args: argparse.Namespace) -> list[dict]:
    """Ordered case list; the fully nominal run is shared between grids."""
    plan: list[dict] = []
    seen: set[tuple] = set()

    def add(grid: str, exponent: float, flow: float, prop_set: str, scale: float,
            overrides: dict[str, float]) -> None:
        key = (round(exponent, 12), round(flow, 12), prop_set, round(scale, 12))
        if key in seen:
            return
        seen.add(key)
        case_id = (
            f"{grid}_exp{_case_tag(exponent)}_ff{_case_tag(flow)}"
            if prop_set == "nominal"
            else f"{grid}_{prop_set}_x{_case_tag(scale)}"
        )
        plan.append(
            {
                "case_id": case_id,
                "grid": grid,
                "hAExp": float(exponent),
                "flow_fraction": float(flow),
                "property_set": prop_set,
                "property_scale": float(scale),
                "extra_overrides": dict(overrides),
            }
        )

    for flow in args.flow_fractions:
        for exponent in args.exponents:
            add("gridA", exponent, flow, "nominal", 1.0, {})

    if not args.skip_gridB:
        # Shared nominal (may already exist through grid A).
        add("gridB", BASELINE_EXPONENT, 1.0, "nominal", 1.0, {})
        for scale in args.property_scales:
            add(
                "gridB", BASELINE_EXPONENT, 1.0, "rho_cp_fuel", scale,
                {
                    "rhoFuel": NOMINAL_RHO_FUEL * scale,
                    "scpFuel": NOMINAL_CP_FUEL * scale,
                },
            )
        for scale in args.property_scales:
            # kFuel = 0 in production: scaling is exactly inert; run it anyway
            # so the zero sensitivity is on the record (module docstring).
            add(
                "gridB", BASELINE_EXPONENT, 1.0, "kFuel", scale,
                {"kFuel": NOMINAL_K_FUEL * scale},
            )
        if not args.skip_hanom_set:
            for scale in args.property_scales:
                add(
                    "gridB", BASELINE_EXPONENT, 1.0, "hAnom", scale,
                    {
                        # core1R.hAnom, not core1R.fuelchannel.hAnom: the
                        # channel value is a calculated parameter bound to
                        # it since the plant-data packages (MSRR_PlantData),
                        # so only the component-level parameter is
                        # changeable at run time.
                        "core1R.hAnom": NOMINAL_HA_NOM_CORE * scale,
                        "heatExchanger.hApNom": NOMINAL_HA_NOM_HX_PRIMARY * scale,
                    },
                )
    return plan


def nostep_twin(case: dict) -> dict:
    """The matched no-step twin of *case*: identical settings, zero step.

    Only the case id (so the twin gets its own run directory) and the step
    amplitude differ; the step time stays, so the solver meets the same time
    event and the pre-step history is the same as the step run's.
    """
    twin = dict(case)
    twin["case_id"] = f"{case['case_id']}{NOSTEP_SUFFIX}"
    twin["step_pcm"] = 0.0
    twin["extra_overrides"] = dict(case.get("extra_overrides", {}))
    return twin


def build_case_overrides(
    case: dict, args: argparse.Namespace, setpoint_overrides: str
) -> str:
    flow = case["flow_fraction"]
    # A case may carry its own amplitude (the no-step twin sets 0).
    step_pcm = case.get("step_pcm", args.step_pcm)
    parts = [
        setpoint_overrides,
        f"primaryPump.freeConvFF={_fmt(flow)}",
        f"primaryPump.rampUpTo[1]={_fmt(flow)}",
        "secondaryPump.freeConvFF=1",
        "powerLevel=1",
        "perturbationAmplitudePcm=0",
        f"externalReactivityAmplitude[2]={_fmt(step_pcm)}",
        f"externalReactivityStepTime[2]={_fmt(args.step_time)}",
        f"core1R.fuelchannel.hAExp={_fmt(case['hAExp'])}",
        f"heatExchanger.hAExp={_fmt(case['hAExp'])}",
    ]
    for key in sorted(case["extra_overrides"]):
        parts.append(f"{key}={_fmt(case['extra_overrides'][key])}")
    return ",".join(parts)


def _override_rejections(stdout_text: str, watched: set[str]) -> list[str]:
    """Study-varied quantities omc refused to override, in order.

    omc echoes the quantity as a full override path (``heatExchanger.hAExp``)
    or a bare name (``hAnom``); both spellings count as a match.
    """
    watched_bare = {key.rsplit(".", 1)[-1] for key in watched}
    rejected: list[str] = []
    for line in stdout_text.splitlines():
        if OVERRIDE_REJECT_MARKER not in line:
            continue
        name = line.split(OVERRIDE_REJECT_MARKER, 1)[1].strip()
        if name in watched or name.rsplit(".", 1)[-1] in watched_bare:
            rejected.append(name)
    return rejected


def run_case(
    case: dict,
    args: argparse.Namespace,
    setpoint_overrides: str,
    library_path: Path,
    model_path: Path,
    runs_dir: Path,
) -> Path:
    """Simulate one case; return the result CSV path (raises on failure).

    The case's result slot is held under an exclusive flock claim
    (``helpers.run_results.acquire_result_claim``) for the whole
    launch-validate window, so a concurrent invocation of the same case
    serializes behind this one instead of quarantining its output.
    """
    work_dir = runs_dir / case["case_id"]
    work_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = case["case_id"]
    csv_path = work_dir / f"{file_prefix}_res.csv"

    rr = _run_results()
    claim = rr.acquire_result_claim(csv_path)
    try:
        if csv_path.exists():
            csv_path.unlink()

        overrides = build_case_overrides(case, args, setpoint_overrides)
        # NOTE: unlike run_case_segmented (which invokes the executable
        # directly in list form), the legacy simulate() route hands the
        # simflags string to /bin/sh -- omc's launcher shells it -- so the
        # '|' alternators in the -variableFilter regex MUST stay shell-quoted
        # here. The quotes are consumed by that shell; the executable still
        # receives the plain regex.
        simflags = (
            f"-maxStepSize={_fmt(args.max_step_size)}"
            f" -override={overrides}"
            f" -variableFilter={shlex.quote(VARIABLE_FILTER)}"
        )
        from helpers.plant_config import lumped_plant_data_path

        mos_text = build_mos(
            library_path,
            model_path,
            MODEL_NAME,
            start_time=0.0,
            stop_time=args.stop_time,
            number_of_intervals=args.number_of_intervals,
            tolerance=args.tolerance,
            method=args.method,
            file_prefix=file_prefix,
            simflags=simflags,
            plant_data_path=lumped_plant_data_path(library_path.parent),
        )
        mos_path = work_dir / f"{file_prefix}.mos"
        mos_path.write_text(mos_text)

        stdout_path = work_dir / "omc_stdout.log"
        stderr_path = work_dir / "omc_stderr.log"
        timeout = args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
        try:
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                proc = subprocess.run(
                    [args.omc, mos_path.name],
                    stdout=stdout,
                    stderr=stderr,
                    cwd=work_dir,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"omc timed out after {exc.timeout:g} s for {case['case_id']}"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"omc failed for {case['case_id']} (exit {proc.returncode}); "
                f"see {stderr_path}"
            )
        if not csv_path.exists():
            tail = stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"omc exited 0 but produced no result CSV for {case['case_id']}\n{tail}"
            )

        # Truncated runs (omc exit 0 with early termination) must not flow
        # into compute_metrics: the tail must reach the requested stop time.
        if not csv_reaches_stop_time(csv_path, args.stop_time):
            raise RuntimeError(
                f"{case['case_id']}: result CSV does not reach stopTime="
                f"{args.stop_time:g} s (truncated run); refusing to compute metrics"
            )

        stdout_text = stdout_path.read_text(errors="ignore")
        watched: set[str] = set(VARIED_OVERRIDE_QUANTITIES) | set(
            case["extra_overrides"]
        )
        rejected = _override_rejections(stdout_text, watched)
        if rejected:
            raise RuntimeError(
                f"{case['case_id']}: override rejected by omc: {', '.join(rejected)}"
            )

        if not args.keep_build_artifacts:
            keep = {
                csv_path.name,
                mos_path.name,
                stdout_path.name,
                stderr_path.name,
                # The flock claim file stays on disk by design (deleting it
                # would let a third writer slip past a blocked waiter).
                rr.make_claim_path(csv_path).name,
            }
            for entry in work_dir.iterdir():
                if entry.name in keep:
                    continue
                # rev022 M-4: best-effort cleanup -- a read-only file or a
                # symlink-to-dir inside an omc build tree must not turn a
                # SUCCESSFUL simulation into a case failure (pre-fix, and
                # via N-3, into a whole-study abort; the work_dir is
                # per-case scratch).
                try:
                    if entry.is_symlink():  # link-to-dir: unlink, never rmtree
                        entry.unlink()
                    elif entry.is_dir():  # omc build dirs, not just loose files
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink()
                except OSError:
                    pass
        return csv_path
    finally:
        claim.release()


def _window_mean(time: np.ndarray, values: np.ndarray, lo: float, hi: float) -> float:
    mask = (time >= lo) & (time <= hi)
    if not mask.any():
        return math.nan
    return float(np.nanmean(values[mask]))


def decay_ratio_from_trace(
    power_from_peak: np.ndarray,
    steady_level: float,
    hysteresis: float = DECAY_RATIO_HYSTERESIS,
) -> tuple[float, int | None]:
    """Decay ratio of a post-step power trace that starts at its first peak.

    ``power_from_peak[0]`` is the first (global) peak. The trough is the
    FIRST local minimum after it and the second peak is the FIRST local
    maximum after that trough. A running extremum only counts as a turning
    point once the trace has moved back by more than ``hysteresis *
    (peak - steady_level)``, so flat sample runs and sub-band noise neither
    end the descent early nor fake a second peak, and a maximum still rising
    at the end of the record is not a peak. Non-finite samples are skipped.

    Returns ``(ratio, index_of_second_peak)``, where ``ratio = (second_peak -
    steady_level) / (peak - steady_level)``; ``(nan, None)`` when the trace
    is monotone / overdamped, has no confirmed second maximum, or that
    maximum does not lie above ``steady_level``.
    """
    values = np.asarray(power_from_peak, dtype=float)
    nothing: tuple[float, int | None] = (math.nan, None)
    if values.size < 3 or not math.isfinite(steady_level):
        return nothing
    peak = float(values[0])
    first_amp = peak - steady_level
    if not math.isfinite(peak) or not first_amp > 0:
        return nothing
    band = hysteresis * first_amp

    # 1. First local minimum: follow the descent until the trace rises by
    #    more than the band above its running minimum.
    run_min, idx_min, trough = peak, 0, None
    for i in range(1, values.size):
        x = values[i]
        if not math.isfinite(x):
            continue
        if x < run_min:
            run_min, idx_min = x, i
        elif x > run_min + band:
            trough = idx_min
            break
    if trough is None:
        return nothing

    # 2. First local maximum after the trough: follow the rise until the
    #    trace falls by more than the band below its running maximum.
    run_max, idx_max, second = float(values[trough]), trough, None
    for j in range(trough + 1, values.size):
        x = values[j]
        if not math.isfinite(x):
            continue
        if x > run_max:
            run_max, idx_max = x, j
        elif x < run_max - band:
            second = idx_max
            break
    if second is None:
        return nothing
    second_amp = float(values[second]) - steady_level
    if not second_amp > 0:
        return nothing
    return second_amp / first_amp, second


def _read_tout(df: pd.DataFrame) -> np.ndarray:
    tout_col = (
        "core1R.tempOut.T"
        if "core1R.tempOut.T" in df.columns
        else "core1R.fuelchannel.fuelNode2.T"
    )
    return df[tout_col].to_numpy(dtype=float)


def steady_tout_window_means(
    time: np.ndarray, tout: np.ndarray, args: argparse.Namespace
) -> tuple[float, float]:
    """(pre-step mean, tail mean) of the outlet temperature.

    Pre window ``[step_time - pre_window, step_time - 1]``, tail window
    ``[stop_time - tail_window, stop_time]`` -- the same windows for the step
    run and its no-step twin.
    """
    step_t = args.step_time
    pre = _window_mean(time, tout, step_t - args.pre_window, step_t - 1.0)
    post = _window_mean(time, tout, args.stop_time - args.tail_window, args.stop_time)
    return pre, post


def compute_metrics(
    csv_path: Path,
    case: dict,
    args: argparse.Namespace,
    baseline_csv_path: Path | None = None,
) -> dict:
    """Metrics of one step run; ``baseline_csv_path`` is its no-step twin.

    ``delta_tout_C`` is the drift-corrected outlet rise (step-run rise minus
    the twin's rise over the same windows) and is NaN without a twin;
    ``delta_tout_raw_C`` is always the uncorrected step-run difference.
    """
    df = pd.read_csv(csv_path)
    time = df["time"].to_numpy(dtype=float)
    power = df["core1R.powerblock.reactorPower"].to_numpy(dtype=float)
    tout = _read_tout(df)
    ha_col = "core1R.fuelchannel.hA"
    ha_value = float(df[ha_col].iloc[-1]) if ha_col in df.columns else math.nan

    step_t = args.step_time
    pre_lo = step_t - args.pre_window
    pre_power = _window_mean(time, power, pre_lo, step_t - 1.0)
    tail_lo = args.stop_time - args.tail_window
    post_power = _window_mean(time, power, tail_lo, args.stop_time)
    pre_tout, post_tout = steady_tout_window_means(time, tout, args)
    delta_tout_raw = post_tout - pre_tout

    nostep_delta = math.nan
    if baseline_csv_path is not None:
        base = pd.read_csv(baseline_csv_path)
        base_pre, base_post = steady_tout_window_means(
            base["time"].to_numpy(dtype=float), _read_tout(base), args
        )
        nostep_delta = base_post - base_pre
    delta_tout = delta_tout_raw - nostep_delta  # NaN without a twin

    post_mask = time > step_t
    peak_power = math.nan
    time_of_peak = math.nan
    overshoot_fraction = math.nan
    decay_ratio = math.nan
    time_of_second_peak = math.nan
    if post_mask.any() and np.isfinite(power[post_mask]).any():
        t_post = time[post_mask]
        p_post = power[post_mask]
        idx_peak = int(np.nanargmax(p_post))
        peak_power = float(p_post[idx_peak])
        time_of_peak = float(t_post[idx_peak])
        if post_power and math.isfinite(post_power) and post_power > 0:
            overshoot_fraction = (peak_power - post_power) / post_power
        # Decay ratio: first local maximum after the first local minimum
        # that follows the peak (decay_ratio_from_trace; NaN when overdamped
        # or no second peak lies above the post-step steady level).
        decay_ratio, idx_second = decay_ratio_from_trace(
            p_post[idx_peak:], post_power
        )
        if idx_second is not None:
            time_of_second_peak = float(t_post[idx_peak + idx_second])

    return {
        "case_id": case["case_id"],
        "grid": case["grid"],
        "hAExp": case["hAExp"],
        "flow_fraction": case["flow_fraction"],
        "property_set": case["property_set"],
        "property_scale": case["property_scale"],
        "step_pcm": args.step_pcm,
        "pre_steady_power_W": pre_power,
        "peak_power_W": peak_power,
        "time_of_peak_s": time_of_peak,
        "post_steady_power_W": post_power,
        "overshoot_fraction": overshoot_fraction,
        "decay_ratio": decay_ratio,
        "pre_steady_tout_C": pre_tout,
        "post_steady_tout_C": post_tout,
        "delta_tout_C": delta_tout,
        "hA_W_per_K": ha_value,
        "delta_tout_raw_C": delta_tout_raw,
        "nostep_delta_tout_C": nostep_delta,
        "time_of_second_peak_s": time_of_second_peak,
    }


def write_metrics_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(METRICS_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in METRICS_FIELDS})


def write_summary_markdown(rows: list[dict], out_path: Path, args: argparse.Namespace) -> None:
    lines = [
        "# 1R reactivity-step sensitivity (hA exponent + linked properties)",
        "",
        f"Step: +{args.step_pcm:g} pcm at t = {args.step_time:g} s; "
        f"model `{MODEL_NAME}`; horizon {args.stop_time:g} s.",
        "",
        "At nominal prescribed flow (FF = 1) the hA power law is normalized "
        "(`FF^hAExp = 1`), so the exponent has no effect by construction; "
        "the reduced-flow rows quantify the actual sensitivity. `kFuel = 0` "
        "in the production model, so its scaled rows are exactly inert. "
        "The `rho_cp_fuel` rows scale each of rho_f and c_p,f by the listed "
        "factor, so rho_f*c_p,f moves by its square (scale 0.9 / 1.1: "
        "rho*c_p x0.81 / x1.21).",
        "",
        "dT_out is the outlet-temperature rise (tail mean minus pre-step mean) "
        "net of a matched no-step twin run over the same windows: every case "
        "starts from the FF = 1 nominal-hA setpoint row and is still drifting "
        "when the step fires. dT_out raw is the uncorrected step-run "
        "difference and no-step dT the twin's drift (dT_out = raw - no-step). "
        "In sensitivity_metrics.csv, pre_steady_tout_C and post_steady_tout_C "
        "are the step run's uncorrected window means, so their difference is "
        "dT_out raw, not dT_out. "
        "The decay ratio is the overshoot above the post-step steady power of "
        "the first local maximum after the first local minimum that follows "
        "the peak (at t_peak2), relative to the first overshoot.",
        "",
        "| case | hAExp | FF | property set | scale | peak [MW] | t_peak [s] "
        "| steady [MW] | overshoot | decay ratio | dT_out [C] | hA [W/K] "
        "| dT_out raw [C] | no-step dT [C] | t_peak2 [s] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        def num(key: str, scale: float = 1.0, fmt: str = ".4g") -> str:
            value = row.get(key)
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                return "-"
            return format(float(value) * scale, fmt)

        lines.append(
            f"| {row['case_id']} | {row['hAExp']:g} | {row['flow_fraction']:g} "
            f"| {row['property_set']} | {row['property_scale']:g} "
            f"| {num('peak_power_W', 1e-6)} | {num('time_of_peak_s')} "
            f"| {num('post_steady_power_W', 1e-6)} | {num('overshoot_fraction')} "
            f"| {num('decay_ratio')} | {num('delta_tout_C')} "
            f"| {num('hA_W_per_K')} | {num('delta_tout_raw_C')} "
            f"| {num('nostep_delta_tout_C')} | {num('time_of_second_peak_s')} |"
        )
    out_path.write_text("\n".join(lines) + "\n")


def write_summary_figure(
    rows: list[dict],
    traces: dict[str, tuple[np.ndarray, np.ndarray]],
    out_path: Path,
    args: argparse.Namespace,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_traces, ax_bars) = plt.subplots(1, 2, figsize=(11, 4.2))

    # Left panel: grid A power traces at the lowest requested flow fraction
    # (falls back to FF=1 when only nominal flow was requested).
    grid_a = [r for r in rows if r["grid"] == "gridA"]
    flow_for_traces = min((r["flow_fraction"] for r in grid_a), default=1.0)
    for row in sorted(grid_a, key=lambda r: r["hAExp"]):
        if row["flow_fraction"] != flow_for_traces:
            continue
        trace = traces.get(row["case_id"])
        if trace is None:
            continue
        time, power = trace
        ax_traces.plot(
            time - args.step_time,
            power * 1e-6,
            label=f"hAExp = {row['hAExp']:g}",
            linewidth=1.0,
        )
    ax_traces.set_xlim(-50.0, min(400.0, args.stop_time - args.step_time))
    ax_traces.set_xlabel("Time after step [s]")
    ax_traces.set_ylabel("Reactor power [MW]")
    ax_traces.set_title(
        f"+{args.step_pcm:g} pcm step at FF = {flow_for_traces:g}"
    )
    ax_traces.grid(True, linestyle="--", alpha=0.5)
    ax_traces.legend(fontsize=8)

    # Right panel: peak power per case. Cases with a non-finite peak (failed
    # or rejected runs) are skipped rather than drawn as zero-height bars,
    # which would misrepresent a failed run as zero power.
    finite_rows = [r for r in rows if math.isfinite(r["peak_power_W"])]
    skipped_ids = [
        r["case_id"] for r in rows if not math.isfinite(r["peak_power_W"])
    ]
    labels = [r["case_id"] for r in finite_rows]
    peaks = [r["peak_power_W"] * 1e-6 for r in finite_rows]
    positions = np.arange(len(labels))
    ax_bars.bar(positions, peaks, color="tab:gray")
    ax_bars.set_xticks(positions)
    ax_bars.set_xticklabels(labels, rotation=75, fontsize=6, ha="right")
    ax_bars.set_ylabel("Peak power after step [MW]")
    if skipped_ids:
        ax_bars.set_title(
            "Peak power by case "
            f"({len(skipped_ids)} case(s) with non-finite peak not shown)"
        )
    else:
        ax_bars.set_title("Peak power by case")
    ax_bars.grid(True, axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def write_metadata(
    out_dir: Path,
    args: argparse.Namespace,
    plan: list[dict],
    setpoint_table: Path,
    failures: list[dict] | None = None,
) -> None:
    rr = _run_results()
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script": "transients.sensitivity.run_review_sensitivity",
        "task": "TASK-20260901-01 Phase 1 (reviewer items R1.3a/R1.3b)",
        "argv": sys.argv[1:],
        "git_info": rr.collect_git_info(REPO_ROOT),
        # Probe the exact --omc binary the cases ran with, not PATH omc.
        "omc_version": rr.probe_omc_version(omc_executable=str(args.omc)),
        "model_name": MODEL_NAME,
        "solver": args.method,
        "tolerance": args.tolerance,
        "max_step_size": args.max_step_size,
        "number_of_intervals": args.number_of_intervals,
        "stop_time_s": args.stop_time,
        "step_pcm": args.step_pcm,
        "step_time_s": args.step_time,
        "setpoint_table": str(setpoint_table),
        "exponents": list(args.exponents),
        "flow_fractions": list(args.flow_fractions),
        "property_scales": list(args.property_scales),
        "smoke": bool(args.smoke),
        "cases": [
            {
                **{key: case[key] for key in
                   ("case_id", "grid", "hAExp", "flow_fraction", "property_set",
                    "property_scale", "extra_overrides")},
                "nostep_case_id": nostep_twin(case)["case_id"],
            }
            for case in plan
        ],
        "failures": list(failures or []),
        "notes": [
            "FF=1 rows are exactly invariant in hAExp (power law normalized "
            "at nominal flow).",
            "kFuel=0 in the production 1R model; its scaled rows are inert "
            "by construction.",
            "rho_cp_fuel rows scale each of rhoFuel and scpFuel by the listed "
            "factor, so rho*cp moves by its square (0.9 / 1.1 -> x0.81 / x1.21).",
            "delta_tout_C = [Tout_step(tail) - Tout_step(pre)] - "
            "[Tout_nostep(tail) - Tout_nostep(pre)]: each case is paired with "
            "a no-step twin (<case_id>_nostep, externalReactivityAmplitude[2]"
            "=0, otherwise identical) because the FixedStart initial state is "
            "still drifting at the step; delta_tout_raw_C is the uncorrected "
            "step-run difference, nostep_delta_tout_C the twin's drift.",
            "decay_ratio = (second peak - post_ss)/(peak - post_ss), second "
            "peak = first local maximum after the first local minimum after "
            "the peak (hysteresis band "
            f"{DECAY_RATIO_HYSTERESIS:g} x first overshoot); NaN when "
            "no such maximum lies above post_ss.",
        ],
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out_dir.resolve()
    _guard_out_dir(out_dir)
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    core_dir = args.core_dir.resolve()
    library_path = core_dir / "SMD_MSR_Modelica.mo"
    model_path = core_dir / "MSRR.mo"
    for path in (library_path, model_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing Modelica source: {path}")

    setpoint_table = core_dir / "init" / "setpoints_1r.csv"
    setpoints = load_setpoints(setpoint_table, power_level=1.0)
    setpoint_overrides = build_setpoint_override(setpoints, INIT_MODE_OVERRIDE)

    plan = build_case_plan(args)
    print(f"Sensitivity plan: {len(plan)} case(s) -> {out_dir}")

    rows: list[dict] = []
    traces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    failures: list[dict] = []
    for index, case in enumerate(plan, start=1):
        print(f"[{index}/{len(plan)}] {case['case_id']} (+ no-step twin) ...", flush=True)
        # One failing case must not lose the other cases' completed work:
        # record the failure and keep going; outputs are always written.
        try:
            csv_path = run_case(
                case, args, setpoint_overrides, library_path, model_path, runs_dir
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {case['case_id']}: {message}", file=sys.stderr, flush=True)
            failures.append({"case_id": case["case_id"], "error": message})
            continue
        # The matched no-step twin; if it fails, the step metrics are still
        # reported, delta_tout_C stays NaN, and the twin failure is recorded.
        twin = nostep_twin(case)
        baseline_csv: Path | None = None
        try:
            baseline_csv = run_case(
                twin, args, setpoint_overrides, library_path, model_path, runs_dir
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {twin['case_id']}: {message}", file=sys.stderr, flush=True)
            failures.append({"case_id": twin["case_id"], "error": message})
        try:
            rows.append(
                compute_metrics(csv_path, case, args, baseline_csv_path=baseline_csv)
            )
            df = pd.read_csv(
                csv_path, usecols=["time", "core1R.powerblock.reactorPower"]
            )
            traces[case["case_id"]] = (
                df["time"].to_numpy(dtype=float),
                df["core1R.powerblock.reactorPower"].to_numpy(dtype=float),
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {case['case_id']}: {message}", file=sys.stderr, flush=True)
            failures.append({"case_id": case["case_id"], "error": message})

    write_metrics_csv(rows, out_dir / "sensitivity_metrics.csv")
    write_summary_markdown(rows, out_dir / "summary.md", args)
    if rows:
        write_summary_figure(rows, traces, out_dir / "summary.png", args)
    write_metadata(out_dir, args, plan, setpoint_table, failures=failures)
    print(f"Wrote {len(rows)} metric rows to {out_dir / 'sensitivity_metrics.csv'}")
    if rows:
        print(f"Summary figure: {out_dir / 'summary.png'}")
    if failures:
        print(
            f"{len(failures)} of {len(plan)} case(s) failed; outputs written "
            "anyway (failures recorded in metadata.json).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
