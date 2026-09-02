#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run startup-oriented MSRR simulations using shared core Modelica files.

With ``--package segmented`` (default: ``legacy``, which keeps the historical
behavior byte-identical) the runner instead drives the standalone SegmentedMSR
full-loop trim rigs from ``helpers/segmented_runs.py``:

    1r: SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS
    9r: SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS

loading ONLY ``core/SegmentedMSR.mo``. Because ``omc`` 1.27 ``simulate()``
scripting is broken system-wide, segmented runs use ``buildModel(...)`` plus
the generated executable directly, translating the requested interval count
into an equidistant output grid (``-stepSize``; omc 1.27 rejects
``-numberOfIntervals=`` at runtime).

Segmented startup semantics (Ambiguity A', TASK-20260823-04; candidate (i),
simplified continuous-source approach within current rig defaults):
a CONTINUOUS external neutron source (single source-stepper stage,
``sourceAmplitude[1]`` = 1e8 n/s from t=0) plus a TWO-stage staged-reactivity
schedule riding the rigs' EXISTING ``externalReactivity*`` array elements
(times stay at the rig defaults {0, 4000} s): a -3500 pcm subcritical hold
from t~0 mirroring the legacy scenario's first entry, recovering to 0 pcm at
t=4000 s mirroring the legacy staircase's terminal approach-to-criticality
step. The harness sine perturbation is disabled and the pumps are pinned to
forced circulation; ``powerLevel`` stays at the rig default so the frozen
rhoTrim compensation applies unchanged by construction. Only the base
``startup`` scenario is mapped: the to-100kW/to-1MW variants' defining
multi-step UHX-demand ramp is structural (``numUhxSteps`` resize) and cannot
ride runtime overrides; non-mapped scenarios hard-error under ``--package
segmented``. Outputs nest under an extra ``segmented/`` component below the
resolved run directory so they can never collide with legacy startup runs.

Every startup result CSV carries provenance sidecars (helpers/run_results.py):
prior artifacts are quarantined rather than silently deleted before launch,
and the fresh CSV is validated -- freshness against this run's launch
timestamp included -- before both the manifest and validation sidecars are
written beside it.
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from shutil import copyfile

try:
    from .paths import default_startup_run_dir, startup_result_prefix
except ImportError:
    from paths import default_startup_run_dir, startup_result_prefix


CORE_MODEL_TO_SCENARIO_MODEL = {
    "startup": {
        "1r": "MSRR.MSRRstartUpCriticality",
        "9r": "MSRR.MSRRstartUpCriticality9R",
    },
    "startup_to_100kw": {
        "1r": "MSRR.MSRRstartUpTo100kW",
        "9r": "MSRR.MSRRstartUpTo100kW9R",
    },
    "startup_to_1mw": {
        "1r": "MSRR.MSRRstartUpTo1MW",
        "9r": "MSRR.MSRRstartUpTo1MW9R",
    },
}

SCENARIOS = {
    "startup": {
        "stop_time": 108000.0,
        "number_of_intervals": 1080000,
    },
    "startup_to_100kw": {
        "stop_time": 151200.0,
        "number_of_intervals": 151200,
    },
    "startup_to_1mw": {
        "stop_time": 151200.0,
        "number_of_intervals": 151200,
    },
}

# ---------------------------------------------------------------------------
# Model-package selection (TASK-20260823-04 phase R3; mirrors the landed freq
# (408e08a) and transients (2fdd01d) precedents): the CLI exposes --package
# {legacy,segmented} with DEFAULT LEGACY so every pre-existing invocation
# keeps byte-identical behavior. 'segmented' drives the standalone SegmentedMSR
# package through helpers/segmented_runs.py (single source of truth for vehicle
# names and library files; imported ONLY on the segmented code path).
# ---------------------------------------------------------------------------
LEGACY_PACKAGE = "legacy"
SEGMENTED_PACKAGE = "segmented"
PACKAGE_CHOICES = (LEGACY_PACKAGE, SEGMENTED_PACKAGE)
DEFAULT_PACKAGE = LEGACY_PACKAGE

#: Extra path component nested below the resolved run directory for
#: segmented-package runs (Ambiguity J, TASK-20260823-04; the transients
#: runner-side precedent -- paths.py stays untouched so legacy default paths
#: are byte-unchanged and legacy/segmented artifacts can never collide).
STARTUP_SEGMENTED_DIR_COMPONENT = "segmented"

# ---------------------------------------------------------------------------
# Segmented startup schedule (Ambiguity A' decision, TASK-20260823-04 R3:
# candidate (i), simplified continuous-source startup within current rig
# defaults -- no wrapper models, no array resizes, no Core-level changes).
# The rigs' PKE_T pins n = max(n_0, nFloor) at init with n_0 = powerLevel and
# freezes rhoTrim, so keeping powerLevel at its default means the frozen trim
# compensation applies unchanged BY CONSTRUCTION; the smoke run verifies the
# stable init balance empirically. Stepper amplitudes are ABSOLUTE cumulative
# levels (Signals.TimeDependent.Stepper: output after step i == amplitude[i]).
# ---------------------------------------------------------------------------
#: Only legacy scenario with a segmented entry: the base criticality approach.
SEGMENTED_SCENARIO = "startup"

#: Continuous external neutron source [n/s] from t=0. A SINGLE source-stepper
#: stage (rig default numSourceSteps=1, sourceStepTime={0}) never switches
#: off, so one element IS the continuous-source simplification of the legacy
#: 8-window pulsed schedule (startupSourceStrength = 1E8 n/s, MSRR.mo).
SEGMENTED_SOURCE_AMPLITUDE_N_S = 1.0e8

#: Stage 1: subcritical hold [pcm] via existing element [1] at the rig-default
#: step time t=0 -- mirrors the legacy staircase's first entry (-3500 pcm).
SEGMENTED_HOLD_REACTIVITY_PCM = -3500.0

#: Stage 2: recovery-to-criticality [pcm] via existing element [2], left at
#: the rig-default step time t=4000 s -- mirrors the legacy staircase's
#: terminal 0-pcm approach-to-criticality step.
SEGMENTED_RECOVER_REACTIVITY_PCM = 0.0


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_core_dir = str(repo_root / "core")

    parser = argparse.ArgumentParser(description="Run MSRR startup simulation.")
    parser.add_argument(
        "--run_dir",
        type=str,
        default=None,
        help=(
            "Directory where run artifacts/logs/results are written "
            "(default: 00runs/startup-<scenario>-<core_model>; segmented "
            "runs nest under an additional segmented/ component)"
        ),
    )
    parser.add_argument(
        "--core_dir",
        type=str,
        default=default_core_dir,
        help="Directory containing core Modelica files (default: ../core)",
    )
    parser.add_argument(
        "--library_file",
        type=str,
        default="SMD_MSR_Modelica.mo",
        help="Core Modelica library filename",
    )
    parser.add_argument(
        "--model_file",
        type=str,
        default="MSRR.mo",
        help="Core MSRR model filename",
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core segmentation to use: 1r or 9r",
    )
    parser.add_argument(
        "--package",
        type=str,
        choices=PACKAGE_CHOICES,
        default=DEFAULT_PACKAGE,
        help=(
            "Model family to drive (default: legacy). 'legacy' loads "
            "SMD_MSR_Modelica.mo + MSRR.mo startup models with unchanged "
            "behavior; 'segmented' drives the standalone SegmentedMSR "
            "full-loop trim rigs (loads ONLY SegmentedMSR.mo; buildModel + "
            "direct executable because omc 1.27 simulate() scripting is "
            "broken) with the simplified continuous-source startup schedule: "
            "a 1e8 n/s source from t=0, a -3500 pcm subcritical hold, and "
            "recovery to 0 pcm at the rig-default t=4000 s. Only the base "
            "'startup' scenario is mapped; to-power variants hard-error in "
            "segmented mode (their UHX-demand ramp is structural). Segmented "
            "result CSVs report temperatures in kelvin."
        ),
    )
    parser.add_argument(
        "--scenario",
        type=str,
        choices=sorted(SCENARIOS.keys()),
        default="startup",
        help="Predefined startup run configuration",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default=None,
        help="Modelica model to simulate (overrides --scenario/--core_model mapping)",
    )
    parser.add_argument("--start_time", type=float, default=0.0, help="Simulation start time (s)")
    parser.add_argument(
        "--stop_time",
        type=float,
        default=None,
        help="Simulation stop time (s). Default depends on scenario.",
    )
    parser.add_argument(
        "--number_of_intervals",
        type=int,
        default=None,
        help="Number of output intervals. Default depends on scenario.",
    )
    parser.add_argument("--tolerance", type=float, default=1e-6, help="Solver relative tolerance")
    parser.add_argument("--method", type=str, default="dassl", help="DAE solver method")
    parser.add_argument("--output_format", type=str, default="csv", help="Output format")
    parser.add_argument(
        "--file_prefix",
        type=str,
        default=None,
        help="Result file prefix (defaults by scenario/core_model)",
    )
    parser.add_argument("--max_step_size", type=float, default=0.01, help="Maximum solver step size")
    parser.add_argument("--extra_simflags", type=str, default="", help="Additional simflags")
    parser.add_argument(
        "--mos_name",
        type=str,
        default="runMSRR_generated.mos",
        help="Generated .mos script filename",
    )
    parser.add_argument("--keep_mos", action="store_true", help="Keep generated .mos script after run")
    parser.add_argument("--omc", type=str, default="omc", help="OpenModelica compiler command")
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help=(
            "Maximum wall-clock seconds for the omc simulation before it is "
            "treated as failed (0 disables the timeout, default: 0)."
        ),
    )
    parser.add_argument(
        "--claim_timeout_s",
        type=float,
        default=None,
        help=(
            "Maximum seconds to wait for a busy result-slot claim when "
            "another launch holds the same startup run; expiry raises "
            "ResultSlotClaimTimeout naming the holder. Default: wait "
            "indefinitely (previous behavior)."
        ),
    )
    args = parser.parse_args()
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    return args


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsupported argument combinations before any simulation starts.

    Legacy invocations pass trivially (zero behavior change). Segmented mode
    hard-errors every legacy-only knob (Ambiguity A' scenario-entry decision,
    TASK-20260823-04 R3: hard-error over loud-skip, matching the landed
    freq/transients precedent) instead of silently mapping it to unlike
    segmented semantics.
    """
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    if package != SEGMENTED_PACKAGE:
        return
    if args.scenario != SEGMENTED_SCENARIO:
        raise ValueError(
            f"--scenario {args.scenario} is unsupported with --package "
            "segmented: only the base 'startup' criticality approach is "
            "mapped. The to-power variants' multi-step UHX-demand ramp is "
            "structural (numUhxSteps resize) and cannot ride runtime "
            "overrides."
        )
    if args.model_name:
        raise ValueError(
            "--model_name cannot be combined with --package segmented; "
            "select the vehicle with --core_model {1r,9r}."
        )
    if args.library_file != "SMD_MSR_Modelica.mo":
        raise ValueError(
            "--library_file is a legacy-load knob and is unsupported with "
            "--package segmented: the standalone package loads ONLY "
            "SegmentedMSR.mo."
        )
    if args.model_file != "MSRR.mo":
        raise ValueError(
            "--model_file is a legacy-load knob and is unsupported with "
            "--package segmented: the standalone package loads ONLY "
            "SegmentedMSR.mo."
        )
    if args.method.strip().lower() != "dassl":
        raise ValueError(
            "--method is unsupported with --package segmented: only the "
            "built-in dassl route of the generated executables is exercised."
        )
    if args.tolerance <= 0:
        raise ValueError(
            "--tolerance must be > 0 with --package segmented (it is baked "
            "into buildModel at BUILD time; -rtol/-atol are not runtime "
            "flags)."
        )
    if float(args.start_time) != 0.0:
        raise ValueError(
            "--start_time is unsupported with --package segmented: the "
            "direct-executable route always starts at t=0."
        )
    if args.stop_time is not None and args.stop_time <= 0:
        raise ValueError("--stop_time must be > 0 with --package segmented.")
    if args.number_of_intervals is not None and args.number_of_intervals < 1:
        raise ValueError(
            "--number_of_intervals must be >= 1 with --package segmented."
        )
    if args.output_format.strip().lower() != "csv":
        raise ValueError(
            "--output_format is unsupported with --package segmented: the "
            "direct-executable route writes CSV results."
        )


def build_simflags(max_step_size: float, extra_simflags: str) -> str:
    simflags = f"-maxStepSize={max_step_size:.10g}"
    extra = extra_simflags.strip()
    if extra:
        simflags = f"{simflags} {extra}"
    return simflags


# ---------------------------------------------------------------------------
# Run-provenance wiring (TASK-20260826-02 P4, sharing helpers/run_results.py
# with the frequency-transient workflows): every startup run quarantines any
# prior result CSV (plus leftover temporary/sidecar files) under
# 00runs/tmp/quarantine/<utc-stamp>/ instead of silently deleting it, then
# validates the fresh output -- freshness against this run's launch timestamp
# included -- and records manifest/validation sidecars beside the CSV.
# Startup never reuses a prior result (historical semantics preserved);
# prepare_result_path only formalizes the reset into a visible quarantine.
# ---------------------------------------------------------------------------
STARTUP_RESULT_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)

#: The segmented tail check accepted a final sample at >= 99.5 % of the
#: requested stop; the validator reproduces that margin as slack so finished
#: trajectories are not rejected over last-step rounding.
STARTUP_VALIDATION_STOP_SLACK_RATIO = 0.005


def _run_results():
    """Lazily import the shared run-result provenance library."""
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from startup/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import run_results as rr
    return rr


def _provenance_workflow_version() -> str:
    """Workflow implementation version recorded in every manifest."""
    return f"startup-runMSRR/run_results-{_run_results().__version__}"


def _workflow_python_sources(package: str) -> list[str]:
    """Workflow Python modules whose bytes enter the run fingerprint.

    Complete by construction for this call site: the active runner module,
    ``startup/paths.py`` (shared result-path module), and
    ``helpers/run_results.py`` (the provenance library itself) -- plus
    ``helpers/segmented_runs.py`` (which builds the segmented ``.mos``) only
    when the segmented code path is selected.  A byte change in any of these
    yields a new fingerprint even when the git commit/dirty state is
    unchanged, so dirty workflow Python can no longer share a result slot
    with committed code.
    """
    rr = _run_results()
    here = Path(__file__).resolve()
    helpers_dir = Path(rr.__file__).resolve().parent
    files = [
        here,
        here.parent / "paths.py",
        Path(rr.__file__).resolve(),
    ]
    if str(package) == SEGMENTED_PACKAGE:
        files.append(helpers_dir / "segmented_runs.py")
    return [str(path) for path in files]


def _probe(call, default):
    """Best-effort environment probes; degrade to ``default`` on any hiccup."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - provenance metadata must not abort runs
        return default


def _validation_stop_slack(stop_time: float) -> float:
    """Stop-time slack (seconds) allowed when validating finished output."""
    return STARTUP_VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _extract_override_payload(simflags: str) -> str:
    """Best-effort extraction of the ``-override=a=1,b=2`` simflag payload.

    Legacy startup runs carry no overrides of their own, but users may pass
    one through ``--extra_simflags``; whatever rides the flag becomes part of
    the request fingerprint so unlike override sets cannot share results.
    """
    parts = simflags.split("-override=", 1)
    if len(parts) != 2:
        return ""
    return parts[1].split(" ", 1)[0].strip()


def _parse_override_payload(payload: str) -> dict[str, str]:
    """Split an ``a=1,b=2`` override payload into a plain string-keyed dict."""
    return dict(part.split("=", 1) for part in payload.split(",") if "=" in part)


def build_startup_manifest(
    args: argparse.Namespace,
    *,
    package: str,
    model_name: str,
    source_files: list[str],
    overrides_payload: str,
    start_time: float,
    stop_time: float,
    number_of_intervals: int,
) -> dict:
    """Assemble the canonical provenance manifest for one startup result CSV.

    Package/model identity, hashed Modelica sources, numerics, and the
    normalized override dictionary (runner-specific details live there --
    scenario selection, step-size cap, and every parsed runtime override)
    enter the request fingerprint; the wall-clock launch stamp is recorded
    but excluded from it.
    """
    rr = _run_results()
    segmented = package == SEGMENTED_PACKAGE
    overrides: dict[str, object] = {
        "package": package,
        "core_model": str(args.core_model),
        "scenario": str(args.scenario),
        "output_format": str(args.output_format),
        "max_step_size": float(args.max_step_size),
        "extra_simflags": str(args.extra_simflags or ""),
        **_parse_override_payload(overrides_payload),
    }
    return rr.build_run_manifest(
        package_name="SegmentedMSR" if segmented else "MSRR",
        model_name=str(model_name),
        source_files=list(source_files),
        workflow_python_files=_workflow_python_sources(package),
        overrides=overrides,
        solver=str(args.method),
        tolerance=float(args.tolerance),
        start_time=float(start_time),
        stop_time=float(stop_time),
        number_of_intervals=int(number_of_intervals),
        output_grid=(
            "segmented_executable_fixed_stepSize_from_intervals"
            if segmented
            else "legacy_simulate_numberOfIntervals"
        ),
        backend="local",
        omc_version=_probe(rr.probe_omc_version, "unavailable"),
        git_info=_probe(
            rr.collect_git_info,
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        workflow_version=_provenance_workflow_version(),
    )


def _prepare_fresh_result(
    result_csv: str,
    *,
    manifest: dict,
    quarantine_root: str | None = None,
) -> None:
    """Quarantine any prior artifacts tied to this result path, then return.

    A stale CSV (or leftover ``*.tmp``/sidecar debris) must never survive to
    be mistaken for this run's output; unlike the former silent delete, the
    moved files remain inspectable under the quarantine area.
    """
    rr = _run_results()
    prepared = rr.prepare_result_path(
        result_csv,
        reuse_ok=False,
        expected_manifest=manifest,
        required_columns=STARTUP_RESULT_REQUIRED_COLUMNS,
        quarantine_root=quarantine_root,
    )
    if prepared.quarantined:
        destination = prepared.quarantined[0].parent
        print(f"  Prior result artifacts quarantined to: {destination}")


def _accept_startup_result(
    result_csv: str,
    manifest: dict,
    *,
    tmp_csv: str | None = None,
    log_hint: str | None = None,
    quarantine_root: str | None = None,
) -> bool:
    """Validate the fresh result output; publish it and record both sidecars.

    ``tmp_csv`` is the temporary companion the simulation was directed at
    (``{result_csv}.tmp``); a passing report publishes those bytes onto
    ``result_csv`` atomically before the sidecars are written, so a failed
    or interrupted run never leaves a reusable final result.  When no
    temporary companion exists (an engine that ignored the result-path flag,
    or the historical direct-write layout of direct programmatic callers)
    the file at ``result_csv`` is validated in place and left untouched on
    rejection.  Rejected because checks failed -> prints the failing check
    names and returns False so the caller exits nonzero without sidecars.
    """
    rr = _run_results()
    raw_csv = tmp_csv if tmp_csv is not None else result_csv
    try:
        rr.publish_validated_result(
            raw_csv,
            result_csv,
            manifest=manifest,
            required_columns=STARTUP_RESULT_REQUIRED_COLUMNS,
            requested_stop_time=float(manifest.get("stop_time") or 0.0) or None,
            stop_time_slack_s=_validation_stop_slack(float(manifest.get("stop_time") or 0.0)),
            quarantine_root=quarantine_root,
        )
    except rr.ResultRejectedError as exc:
        print("ERROR: simulation output rejected by result validation.")
        print(f"  Failed checks: {', '.join(exc.report.failed_checks)}")
        print(f"  Result CSV: {result_csv}")
        if log_hint:
            print(f"  See log: {log_hint}")
        return False
    except OSError as exc:
        print(
            "ERROR: simulation completed but the validated result or its "
            f"provenance sidecars could not be published beside {result_csv}: {exc}"
        )
        return False
    return True


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path
    (its legacy-compat contract); the fallback keeps script-style execution
    from startup/ working by putting the repo root on sys.path.
    """
    try:
        from helpers import segmented_runs as seg
    except ImportError:  # script-style execution from startup/
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import segmented_runs as seg
    return seg


def build_segmented_startup_overrides() -> str:
    """Runtime ``-override=`` payload for the segmented startup schedule.

    Ambiguity A' candidate (i): continuous external source (single stepper
    stage from t=0), a -3500 pcm subcritical hold via EXISTING array element
    [1] (rig-default step time t=0), recovery to 0 pcm via existing element
    [2] (rig-default step time t=4000 s; step TIMES stay at the rig defaults,
    only amplitudes are overridden). The harness sine perturbation is disabled
    and both pumps are pinned to forced circulation (parity with the landed
    freq/transients override blocks); powerLevel keeps its rig default so the
    frozen rhoTrim compensation applies unchanged.
    """
    return (
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        f"sourceAmplitude[1]={SEGMENTED_SOURCE_AMPLITUDE_N_S:.10g},"
        f"externalReactivityAmplitude[1]={SEGMENTED_HOLD_REACTIVITY_PCM:.10g},"
        f"externalReactivityAmplitude[2]={SEGMENTED_RECOVER_REACTIVITY_PCM:.10g}"
    )


def csv_reaches_stop_time(csv_path: str | Path, stop_time: float) -> bool:
    """Return True when the CSV's final data row reaches ~stop_time.

    Local mirror of transients/run_nonlinear_steps.csv_reaches_stop_time
    (Ambiguity I pattern, TASK-20260823-04): backward tail read that brackets
    the newline STARTING the final NON-EMPTY row and parses that row WHOLE,
    because SegmentedMSR 9R result rows are tens of KB wide and a fixed window
    would slice mid-row. Returns False for empty/header-only/single-line
    files, malformed times, or newline-free tails beyond the 1 MB scan cap.
    """
    try:
        with open(csv_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return False
            chunk_size = 16384
            max_scan = 1 << 20
            pos = size
            data = b""
            found = False
            while pos > 0:
                if len(data) > max_scan:
                    return False
                step = min(chunk_size, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
                idx = data.rstrip(b"\n").rfind(b"\n")
                if idx != -1:
                    found = True
                    break
            if not found:
                return False
            handle.seek(pos + idx + 1)
            last = handle.read().decode("utf-8", errors="ignore")
        last = last.strip()
        if not last:
            return False
        last_time = float(last.split(",")[0].strip().strip('"'))
        return last_time >= 0.995 * stop_time
    except Exception:
        return False


def _captured_text(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _write_captured_process_output(
    stdout_path: str,
    stderr_path: str,
    stdout: object,
    stderr: object,
) -> None:
    with open(stdout_path, "w") as handle:
        handle.write(_captured_text(stdout))
    with open(stderr_path, "w") as handle:
        handle.write(_captured_text(stderr))


def build_mos_text(
    library_path: str,
    model_path: str,
    resolved_model_name: str,
    start_time: float,
    resolved_stop_time: float,
    resolved_number_of_intervals: int,
    tolerance: float,
    method: str,
    output_format: str,
    resolved_file_prefix: str,
    simflags: str,
) -> str:
    return (
        "// Generated by startup/runMSRR.py\n"
        f'loadFile("{library_path}");\n'
        f'loadFile("{model_path}");\n'
        f"simulate({resolved_model_name},"
        f"startTime={start_time:.10g},"
        f"stopTime={resolved_stop_time:.10g},"
        f"numberOfIntervals={resolved_number_of_intervals},"
        f"tolerance={tolerance:.10g},"
        f"method={method},"
        f'outputFormat="{output_format}",'
        f'fileNamePrefix="{resolved_file_prefix}",'
        f'simflags="{simflags}");\n'
    )


def _main_segmented(args: argparse.Namespace) -> int:
    """Segmented-mode driver (standalone SegmentedMSR package).

    Loads ONLY ``core/SegmentedMSR.mo`` per run dir and follows the landed
    freq/transients execution route: ``buildModel(..., tolerance=...)`` via
    omc scripting (tolerance baked at BUILD time; ``-rtol/-atol`` are NOT
    runtime flags), then the generated executable directly with
    ``-stepSize`` translating the requested interval count (omc 1.27 rejects
    ``-numberOfIntervals=`` at runtime). Output convention (Ambiguity J,
    TASK-20260823-04): results nest under an extra ``segmented/`` component
    below the resolved run directory so they can never collide with legacy
    startup outputs; file names inside keep the legacy prefixes.
    """
    seg = _segmented_helpers()
    resolved_model_name = seg.model_for(args.core_model)
    # Guard interaction (card constraint): the "nominaltrim" refusal stays
    # effective on BOTH paths; segmented vehicle names contain
    # "trimthermalss", not "nominaltrim", and pass.
    if "nominaltrim" in resolved_model_name.lower():
        print(
            "ERROR: startup/runMSRR.py expects startup models. "
            f"Got nominal-trim model: {resolved_model_name}"
        )
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    core_dir = os.path.abspath(args.core_dir)
    library_src = os.path.join(core_dir, seg.SEGMENTED_PACKAGE_FILE)
    if not os.path.exists(library_src):
        print(f"ERROR: missing segmented library file: {library_src}")
        return 2

    if args.run_dir is None:
        run_dir_root = str(
            default_startup_run_dir(
                repo_root,
                scenario=args.scenario,
                core_model=args.core_model,
            )
        )
    else:
        run_dir_root = os.path.abspath(args.run_dir)
    run_dir = os.path.join(run_dir_root, STARTUP_SEGMENTED_DIR_COMPONENT)

    scenario_defaults = SCENARIOS[args.scenario]
    resolved_file_prefix = args.file_prefix
    if resolved_file_prefix is None:
        resolved_file_prefix = startup_result_prefix(
            scenario=args.scenario,
            core_model=args.core_model,
        )
    resolved_stop_time = (
        args.stop_time if args.stop_time is not None else scenario_defaults["stop_time"]
    )
    resolved_number_of_intervals = (
        args.number_of_intervals
        if args.number_of_intervals is not None
        else scenario_defaults["number_of_intervals"]
    )

    try:
        os.makedirs(run_dir, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: unable to create run directory: {run_dir}")
        print(f"  {exc}")
        return 2

    # Standalone SegmentedMSR package: copy ONLY SegmentedMSR.mo into the run
    # dir (single loadFile; no SMD load, no double-load).
    copyfile(library_src, os.path.join(run_dir, seg.SEGMENTED_PACKAGE_FILE))

    mos_path = os.path.join(run_dir, args.mos_name)
    result_csv = os.path.join(run_dir, f"{resolved_file_prefix}_res.csv")
    overrides = build_segmented_startup_overrides()

    # Quarantine any prior result artifacts (formerly a silent delete) and
    # stamp this run's provenance manifest before anything is launched.
    startup_manifest = build_startup_manifest(
        args,
        package=SEGMENTED_PACKAGE,
        model_name=resolved_model_name,
        source_files=[library_src],
        overrides_payload=overrides,
        start_time=0.0,
        stop_time=float(resolved_stop_time),
        number_of_intervals=int(resolved_number_of_intervals),
    )
    # Same-slot claim (P5): a second concurrent launch of this slot blocks
    # here instead of quarantining this run's in-flight *.tmp output, then
    # finds the published pair through its own reuse/lifecycle decision.
    # --claim_timeout_s bounds the wait (M2 / REV008-10); getattr keeps
    # programmatic namespaces that predate the flag working (the same
    # tolerance the --package read uses below/above).
    claim = _run_results().acquire_result_claim(
        result_csv, timeout_s=getattr(args, "claim_timeout_s", None)
    )
    try:
        return _run_segmented_startup(
            args=args,
            seg=seg,
            run_dir=run_dir,
            core_dir=core_dir,
            mos_path=mos_path,
            result_csv=result_csv,
            result_tmp_csv=str(_run_results().make_tmp_path(result_csv)),
            overrides=overrides,
            startup_manifest=startup_manifest,
            resolved_model_name=resolved_model_name,
            resolved_file_prefix=resolved_file_prefix,
            resolved_stop_time=resolved_stop_time,
            resolved_number_of_intervals=resolved_number_of_intervals,
        )
    finally:
        claim.release()


def _run_segmented_startup(
    args,
    seg,
    *,
    run_dir: str,
    core_dir: str,
    mos_path: str,
    result_csv: str,
    result_tmp_csv: str,
    overrides: str,
    startup_manifest: dict,
    resolved_model_name: str,
    resolved_file_prefix: str,
    resolved_stop_time: float,
    resolved_number_of_intervals: int,
) -> int:
    """Simulate one segmented startup run and publish its result atomically.

    The caller holds the slot's exclusive claim for the whole span, so the
    quarantine-before-launch and the atomic publication can never interleave
    with a concurrent same-slot launch.  The simulation is directed at
    ``result_tmp_csv`` (``{result_csv}.tmp``); only output that passes result
    validation is published onto ``result_csv`` before both sidecars are
    written beside it, so a failed or interrupted run never leaves a
    reusable final result.
    """
    _prepare_fresh_result(result_csv, manifest=startup_manifest)

    with open(mos_path, "w") as handle:
        handle.write(
            seg.build_smoke_mos(resolved_model_name, tolerance=float(args.tolerance))
        )

    print("Running MSRR startup simulation (package=segmented)")
    print(f"  Run dir:            {run_dir}")
    print(f"  Core dir:           {core_dir}")
    print(f"  Core model:         {args.core_model}")
    print(f"  Scenario:           {args.scenario}")
    print(f"  Model:              {resolved_model_name}")
    print(f"  Start/stop:         0 -> {resolved_stop_time} s")
    print(f"  Intervals:          {resolved_number_of_intervals}")
    print(f"  Tolerance (baked):  {args.tolerance}")
    print(f"  File prefix:        {resolved_file_prefix}")
    print(f"  Overrides:          {overrides}")
    print(f"  OMC script:         {mos_path}")
    print("=" * 72)

    timeout = args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
    stdout_log = os.path.join(run_dir, "runMSRR_py_omc_stdout.log")
    stderr_log = os.path.join(run_dir, "runMSRR_py_omc_stderr.log")
    try:
        result = subprocess.run(
            # NOTE: no --showErrorMessages here (unlike the legacy route
            # above): this box's mismatched NFModelicaBuiltin.mo makes omc
            # spray known system-wide JSON error records onto stderr even on
            # SUCCESSFUL buildModel sessions; the landed transients/freq
            # segmented route omits the flag for clean comparable logs.
            [args.omc, mos_path],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _write_captured_process_output(stdout_log, stderr_log, exc.stdout, exc.stderr)
        print(f"ERROR: omc timed out after {args.omc_timeout_seconds:g} s")
        print(f"  OMC script: {mos_path}")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return 5

    _write_captured_process_output(stdout_log, stderr_log, result.stdout, result.stderr)

    if result.returncode != 0:
        print(f"ERROR: omc failed with exit code {result.returncode}")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return result.returncode

    exe_path = os.path.join(run_dir, resolved_model_name)
    if not os.path.exists(exe_path):
        print(
            "ERROR: buildModel produced no executable "
            f"{resolved_model_name}; see {stdout_log}"
        )
        return 4

    exe_cmd = [
        os.path.join(".", resolved_model_name),
        f"-stopTime={resolved_stop_time:.10g}",
        # omc 1.27 runtime rejects -numberOfIntervals ("invalid command line
        # option"); translate the requested interval count into its equivalent
        # equidistant output grid, stepSize = stopTime/numberOfIntervals
        # (startTime is 0 for every segmented startup run).
        f"-stepSize={resolved_stop_time / resolved_number_of_intervals:.17g}",
        "-outputFormat=csv",
        # Production route: the result lands on the temporary companion
        # (never the final name) for validated atomic publication.
        f"-r={os.path.basename(result_tmp_csv)}",
        f"-maxStepSize={args.max_step_size:.10g}",
        f"-override={overrides}",
    ]
    extra = args.extra_simflags.strip()
    if extra:
        exe_cmd.extend(shlex.split(extra))

    exe_stdout_log = os.path.join(run_dir, "runMSRR_py_exe_stdout.log")
    exe_stderr_log = os.path.join(run_dir, "runMSRR_py_exe_stderr.log")
    try:
        exe_result = subprocess.run(
            exe_cmd,
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _write_captured_process_output(
            exe_stdout_log, exe_stderr_log, exc.stdout, exc.stderr
        )
        print(f"ERROR: simulation executable timed out after {args.omc_timeout_seconds:g} s")
        print(f"  Stdout log: {exe_stdout_log}")
        print(f"  Stderr log: {exe_stderr_log}")
        return 5

    _write_captured_process_output(
        exe_stdout_log, exe_stderr_log, exe_result.stdout, exe_result.stderr
    )

    if not args.keep_mos:
        try:
            os.remove(mos_path)
        except OSError:
            pass

    if exe_result.returncode != 0:
        print(
            "ERROR: simulation executable failed with exit code "
            f"{exe_result.returncode}"
        )
        print(f"  Stdout log: {exe_stdout_log}")
        print(f"  Stderr log: {exe_stderr_log}")
        return exe_result.returncode

    # The raw output is expected at the temporary companion; an engine that
    # ignored -r= leaves it at the final name (validated in place instead).
    raw_csv = result_tmp_csv if os.path.exists(result_tmp_csv) else result_csv
    if not os.path.exists(raw_csv):
        print("ERROR: simulation executable exited 0 but result CSV is missing.")
        print(f"  Expected: {result_tmp_csv}")
        print(f"  Stdout log: {exe_stdout_log}")
        print(f"  Stderr log: {exe_stderr_log}")
        return 3

    if not csv_reaches_stop_time(csv_path=raw_csv, stop_time=resolved_stop_time):
        print("ERROR: simulation output did not reach requested stop time.")
        print(f"  Result CSV: {raw_csv}")
        print(f"  Stderr log: {exe_stderr_log}")
        return 4

    if not _accept_startup_result(
        result_csv,
        startup_manifest,
        tmp_csv=result_tmp_csv,
        log_hint=exe_stderr_log,
    ):
        return 4

    print("Simulation completed successfully.")
    print(f"  Result CSV:  {result_csv}")
    print(f"  OMC logs:    {stdout_log}")
    print(f"  Exe logs:    {exe_stdout_log}")
    return 0


def main() -> int:
    args = parse_args()
    validate_args(args)
    if str(getattr(args, "package", DEFAULT_PACKAGE)) == SEGMENTED_PACKAGE:
        return _main_segmented(args)

    repo_root = Path(__file__).resolve().parents[1]
    if args.run_dir is None:
        run_dir = str(
            default_startup_run_dir(
                repo_root,
                scenario=args.scenario,
                core_model=args.core_model,
            )
        )
    else:
        run_dir = os.path.abspath(args.run_dir)
    core_dir = os.path.abspath(args.core_dir)
    library_path = os.path.join(core_dir, args.library_file)
    model_path = os.path.join(core_dir, args.model_file)

    try:
        os.makedirs(run_dir, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: unable to create run directory: {run_dir}")
        print(f"  {exc}")
        return 2
    if not os.path.isdir(run_dir):
        print(f"ERROR: run path is not a directory: {run_dir}")
        return 2
    if not os.path.isdir(core_dir):
        print(f"ERROR: core directory does not exist: {core_dir}")
        return 2
    if not os.path.exists(library_path):
        print(f"ERROR: missing library file: {library_path}")
        return 2
    if not os.path.exists(model_path):
        print(f"ERROR: missing model file: {model_path}")
        return 2

    scenario_defaults = SCENARIOS[args.scenario]
    resolved_model_name = args.model_name
    if resolved_model_name is None:
        resolved_model_name = CORE_MODEL_TO_SCENARIO_MODEL[args.scenario][args.core_model]
    if "nominaltrim" in resolved_model_name.lower():
        print(
            "ERROR: startup/runMSRR.py expects startup models. "
            f"Got nominal-trim model: {resolved_model_name}"
        )
        return 2
    resolved_file_prefix = args.file_prefix
    if resolved_file_prefix is None:
        resolved_file_prefix = startup_result_prefix(
            scenario=args.scenario,
            core_model=args.core_model,
        )
    resolved_stop_time = (
        args.stop_time if args.stop_time is not None else scenario_defaults["stop_time"]
    )
    resolved_number_of_intervals = (
        args.number_of_intervals
        if args.number_of_intervals is not None
        else scenario_defaults["number_of_intervals"]
    )

    mos_path = os.path.join(run_dir, args.mos_name)
    result_csv = os.path.join(run_dir, f"{resolved_file_prefix}_res.csv")
    simflags = build_simflags(args.max_step_size, args.extra_simflags)

    # Quarantine any prior result artifacts (formerly a silent delete) and
    # stamp this run's provenance manifest before anything is launched.
    startup_manifest = build_startup_manifest(
        args,
        package=LEGACY_PACKAGE,
        model_name=resolved_model_name,
        source_files=[library_path, model_path],
        overrides_payload=_extract_override_payload(simflags),
        start_time=float(args.start_time),
        stop_time=float(resolved_stop_time),
        number_of_intervals=int(resolved_number_of_intervals),
    )
    # Same-slot claim (P5): a second concurrent launch of this slot blocks
    # here instead of quarantining this run's in-flight *.tmp output, then
    # finds the published pair through its own lifecycle decision.
    # --claim_timeout_s bounds the wait (M2 / REV008-10); getattr keeps
    # programmatic namespaces that predate the flag working.
    claim = _run_results().acquire_result_claim(
        result_csv, timeout_s=getattr(args, "claim_timeout_s", None)
    )
    try:
        return _run_legacy_startup(
            args=args,
            run_dir=run_dir,
            core_dir=core_dir,
            library_path=library_path,
            model_path=model_path,
            mos_path=mos_path,
            result_csv=result_csv,
            result_tmp_csv=str(_run_results().make_tmp_path(result_csv)),
            simflags=simflags,
            startup_manifest=startup_manifest,
            resolved_model_name=resolved_model_name,
            resolved_file_prefix=resolved_file_prefix,
            resolved_stop_time=resolved_stop_time,
            resolved_number_of_intervals=resolved_number_of_intervals,
        )
    finally:
        claim.release()


def _run_legacy_startup(
    args,
    *,
    run_dir: str,
    core_dir: str,
    library_path: str,
    model_path: str,
    mos_path: str,
    result_csv: str,
    result_tmp_csv: str,
    simflags: str,
    startup_manifest: dict,
    resolved_model_name: str,
    resolved_file_prefix: str,
    resolved_stop_time: float,
    resolved_number_of_intervals: int,
) -> int:
    """Simulate one legacy startup run and publish its result atomically.

    The caller holds the slot's exclusive claim for the whole span, so the
    quarantine-before-launch and the atomic publication can never interleave
    with a concurrent same-slot launch.  The omc simulation is directed at
    ``result_tmp_csv`` (``{result_csv}.tmp``) via a ``-r=`` simflag; only
    output that passes result validation is published onto ``result_csv``
    before both sidecars are written beside it.
    """
    _prepare_fresh_result(result_csv, manifest=startup_manifest)

    # Direct the omc result at the temporary companion (never the final
    # name); appended after the override payload was extracted for the
    # manifest, so the request fingerprint wiring is untouched.
    simflags = f"{simflags} -r={os.path.basename(result_tmp_csv)}"

    with open(mos_path, "w") as handle:
        handle.write(
            build_mos_text(
                library_path=library_path,
                model_path=model_path,
                resolved_model_name=resolved_model_name,
                start_time=args.start_time,
                resolved_stop_time=resolved_stop_time,
                resolved_number_of_intervals=resolved_number_of_intervals,
                tolerance=args.tolerance,
                method=args.method,
                output_format=args.output_format,
                resolved_file_prefix=resolved_file_prefix,
                simflags=simflags,
            )
        )

    print("Running MSRR startup simulation")
    print(f"  Run dir:            {run_dir}")
    print(f"  Core dir:           {core_dir}")
    print(f"  Core model:         {args.core_model}")
    print(f"  Scenario:           {args.scenario}")
    print(f"  Model:              {resolved_model_name}")
    print(f"  Start/stop:         {args.start_time} -> {resolved_stop_time} s")
    print(f"  Intervals:          {resolved_number_of_intervals}")
    print(f"  Tolerance:          {args.tolerance}")
    print(f"  Method:             {args.method}")
    print(f"  Output format:      {args.output_format}")
    print(f"  File prefix:        {resolved_file_prefix}")
    print(f"  Simflags:           {simflags}")
    print(f"  OMC script:         {mos_path}")
    print("=" * 72)

    stdout_log = os.path.join(run_dir, "runMSRR_py_omc_stdout.log")
    stderr_log = os.path.join(run_dir, "runMSRR_py_omc_stderr.log")
    try:
        result = subprocess.run(
            [args.omc, "--showErrorMessages", args.mos_name],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=(
                args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
            ),
        )
    except subprocess.TimeoutExpired as exc:
        _write_captured_process_output(stdout_log, stderr_log, exc.stdout, exc.stderr)
        print(f"ERROR: omc timed out after {args.omc_timeout_seconds:g} s")
        print(f"  OMC script: {mos_path}")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return 5

    _write_captured_process_output(stdout_log, stderr_log, result.stdout, result.stderr)

    if not args.keep_mos:
        try:
            os.remove(mos_path)
        except OSError:
            pass

    if result.returncode != 0:
        print(f"ERROR: omc failed with exit code {result.returncode}")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return result.returncode

    combined_output = f"{result.stdout}\n{result.stderr}"
    failed_in_record = "Simulation execution failed for model" in combined_output
    empty_result_file = bool(re.search(r'resultFile\s*=\s*""', combined_output))

    if failed_in_record or empty_result_file:
        print("ERROR: OpenModelica reported simulation failure.")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return 4

    if not os.path.exists(result_tmp_csv) and not os.path.exists(result_csv):
        print("ERROR: omc exited successfully but result CSV is missing.")
        print(f"  Expected: {result_tmp_csv}")
        print(f"  Stdout log: {stdout_log}")
        print(f"  Stderr log: {stderr_log}")
        return 3

    if not _accept_startup_result(
        result_csv,
        startup_manifest,
        tmp_csv=result_tmp_csv,
        log_hint=stdout_log,
    ):
        return 4

    print("Simulation completed successfully.")
    print(f"  Result CSV:  {result_csv}")
    print(f"  Stdout log:  {stdout_log}")
    print(f"  Stderr log:  {stderr_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
