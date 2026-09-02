#!/usr/bin/env python3
"""Run nonlinear step transients for Results II plots (1R and 9R).

With ``--package segmented`` (default: ``legacy``, which keeps the historical
behavior byte-identical) the runner instead drives the standalone SegmentedMSR
vehicles from ``helpers/segmented_runs.py``:

    1r: SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS   (steps + flows)
        SegmentedMSR.Reactors.R1MSRRuhxTripThermalSS   (uhx_trip)
    9r: SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS   (steps + flows)
        SegmentedMSR.Reactors.R9MSRRuhxTripThermalSS   (uhx_trip)

loading ONLY ``core/SegmentedMSR.mo``. Because ``omc`` 1.27 ``simulate()``
scripting is broken system-wide, segmented runs use ``buildModel(...)`` plus
the generated executable directly, translating the requested interval count
into an equidistant output grid (``-stepSize``; omc 1.27 rejects
``-numberOfIntervals=`` at runtime). Legacy-only machinery (setpoints CSV
inputs, init-mode overrides, the 9R loop-setpoint harmonization) is rejected
loudly in segmented mode: the rigs carry their own trimmed SteadyState init.
The ``uhx_trip`` case rides the S1 trip wrappers (structural
``numUhxSteps = 2``, unreachable via runtime overrides) with a >=4500 s
horizon floor so every gate run integrates through the t=4000 s demand drop;
segmented flow cases carry the analogous >=4400 s floor because their
overrides insert the 1-$ reactivity step at t=4000 s (mirrored from legacy)
and the flow plot window spans (t - 4000) in [-50, 400] s.

Every transient result CSV carries provenance sidecars
(helpers/run_results.py): prior artifacts are quarantined under
00runs/tmp/quarantine/ before launch -- an executable exiting 0 without
producing output can no longer let a pre-existing CSV pass silently --
and each fresh CSV is validated before its manifest and validation
sidecars are written beside it. Programmatic callers of run_case /
run_case_segmented without a provenance context keep the historical
behavior; both CLI drivers always supply one.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from shutil import copyfile
import subprocess

try:
    from .paths import default_transients_run_dir
except ImportError:
    from paths import default_transients_run_dir


STEP_PCM = {
    "step_2dol": 1178.138,
    "step_1dol": 589.069,
    "step_0p5dol": 294.535,
    "step_0p1dol": 58.907,
    "step_100pcm": 100.0,
    "step_10pcm": 10.0,
}

FLOW_CASES = {
    "flow_100pct": 1.0,
    "flow_66pct": 2.0 / 3.0,
    "flow_33pct": 1.0 / 3.0,
}

UHX_TRIP_CASE = "uhx_trip"

# ---------------------------------------------------------------------------
# Model-package selection (TASK-20260823-04 phase R2; mirrors the landed freq
# precedent, commit 408e08a): the CLI exposes --package {legacy,segmented}
# with DEFAULT LEGACY so every pre-existing invocation keeps byte-identical
# behavior. 'segmented' drives the standalone SegmentedMSR package through
# helpers/segmented_runs.py (single source of truth for vehicle names and
# library files; imported ONLY on the segmented code path).
# ---------------------------------------------------------------------------
LEGACY_PACKAGE = "legacy"
SEGMENTED_PACKAGE = "segmented"
PACKAGE_CHOICES = (LEGACY_PACKAGE, SEGMENTED_PACKAGE)
DEFAULT_PACKAGE = LEGACY_PACKAGE

#: Segmented-mode per-core configuration. The vehicles themselves resolve at
#: run time through helpers.segmented_runs.MODEL_BY_CORE (steps + flows) and
#: TRIP_MODEL_BY_CORE (uhx_trip) -- both rigs expose the matched override
#: surface this module emits (powerLevel, perturbationAmplitudePcm,
#: externalReactivityAmplitude[2]/externalReactivityStepTime[2], pump
#: freeConvFF/rampUpTo[1]/rampUpTime[1]/tripTime), so ONE route per case
#: family serves both cores.
SEGMENTED_CORE_CONFIG = {
    "1r": {"label": "1R"},
    "9r": {"label": "9R"},
}

#: UHX-trip SHORT-horizon floor for segmented runs. The S1 wrapper pair drops
#: the UHX demand at t=4000 s (structural numUhxSteps=2), so a meaningful gate
#: must integrate past it. Legacy mode keeps its full multi-hour floor
#: max(stop_time, 4000 + 4*3600 s); segmented mode raises to this floor
#: instead (with a printed notice). Full-window trip overlays remain explicit
#: scripts under 00runs/segmented-msr-validation/scenarios/, never pytest.
SEGMENTED_TRIP_MIN_STOP_TIME_S = 4500.0

#: External-reactivity insertion times mirrored from the legacy routes:
#: nominal-flow steps fire at t=2000 s, variable-flow steps at t=4000 s.
SEGMENTED_STEP_INSERT_TIME_S = 2000.0
SEGMENTED_FLOW_INSERT_TIME_S = 4000.0

#: Variable-flow SHORT-horizon floor for segmented runs (REV-2fdd01d-02,
#: TASK-20260823-04). The flow overrides insert the 1-$ step at
#: t=4000 s -- kept byte-identical to the legacy route so segmented and
#: legacy flow cases stay physically comparable -- and ``plot_flow`` draws
#: the window (t - 4000) in [-50, 400] s. A gate run stopped before
#: t=4400 s therefore produces an empty flow panel (observed with the
#: AC-4 smoke's --stop_time 2500), so segmented mode raises short flow
#: horizons to this floor (with a printed notice) exactly like the trip
#: floor above; the overrides themselves are NOT rescheduled.
SEGMENTED_FLOW_MIN_STOP_TIME_S = SEGMENTED_FLOW_INSERT_TIME_S + 400.0


def _segmented_helpers():
    """Lazily import the shared segmented-run mapping module.

    helpers/segmented_runs.py is imported ONLY on the segmented code path
    (its legacy-compat contract); the fallback keeps script-style execution
    from transients/ working by putting the repo root on sys.path.
    """
    try:
        from helpers import segmented_runs as seg
    except ImportError:  # script-style execution from transients/
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import segmented_runs as seg
    return seg


def build_segmented_step_overrides(step_pcm: float) -> str:
    """Nominal-flow reactivity-step overrides for the segmented trim rigs.

    Mirrors the legacy-9R override route (same parameter names and insertion
    time): pumps pinned at forced circulation, sine perturbation disabled,
    and a single reactivity step of ``step_pcm`` at t=2000 s via EXISTING
    array element [2] (runtime overrides cannot resize arrays).
    """
    return (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        f"externalReactivityAmplitude[2]={step_pcm:.10g},"
        f"externalReactivityStepTime[2]={SEGMENTED_STEP_INSERT_TIME_S:.10g}"
    )


def build_segmented_flow_overrides(flow_frac: float) -> str:
    """Variable-flow overrides for the segmented trim rigs.

    Mirrors the legacy flow-case pump route (freeConvFF / rampUpTo[1] /
    rampUpTime[1] / tripTime) plus the 1-$ reactivity step at t=4000 s.
    """
    return (
        f"primaryPump.freeConvFF={flow_frac},"
        "secondaryPump.freeConvFF=1,"
        f"primaryPump.rampUpTo[1]={1.0 - flow_frac:.10g},"
        "primaryPump.rampUpTime[1]=0,"
        "primaryPump.tripTime=2000,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0,"
        f"externalReactivityAmplitude[2]={STEP_PCM['step_1dol']:.10g},"
        f"externalReactivityStepTime[2]={SEGMENTED_FLOW_INSERT_TIME_S:.10g}"
    )


def build_segmented_uhx_trip_overrides() -> str:
    """UHX-trip overrides for the S1 trip wrappers.

    The demand-drop schedule ({1e6 W -> 0} at {0, 4000} s) and DHRS
    engagement are STRUCTURAL on ``TRIP_MODEL_BY_CORE`` vehicles
    (numUhxSteps=2 cannot be reached via -override); only the pump/power
    pins ride runtime overrides.
    """
    return (
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1,"
        "primaryPump.rampUpTo[1]=1.0,"
        "primaryPump.rampUpTime[1]=0,"
        "powerLevel=1,"
        "perturbationAmplitudePcm=0"
    )


def build_segmented_run_plan(
    seg,
    core_model: str,
    *,
    stop_time: float,
    trip_stop_time: float,
    flow_stop_time: float | None = None,
) -> list[dict]:
    """Ordered per-core segmented case map (unit-testable selection gate).

    Cases follow the legacy order (STEP_PCM, then FLOW_CASES, then
    uhx_trip). Steps and flows ride ``MODEL_BY_CORE``; uhx_trip rides
    ``TRIP_MODEL_BY_CORE``. ``flow_stop_time=None`` falls back to
    ``stop_time``; the CLI driver passes the REV-2fdd01d-02 floor
    (``max(stop_time, SEGMENTED_FLOW_MIN_STOP_TIME_S)``) explicitly, just
    as it floors ``trip_stop_time`` for the t=4000 s demand drop.
    """
    step_model = seg.model_for(core_model)
    trip_model = seg.trip_model_for(core_model)
    effective_flow_stop = (
        float(stop_time) if flow_stop_time is None else float(flow_stop_time)
    )
    plan: list[dict] = []
    for case, pcm in STEP_PCM.items():
        plan.append(
            {
                "case": case,
                "family": "step",
                "model": step_model,
                "overrides": build_segmented_step_overrides(pcm),
                "stop_time": float(stop_time),
            }
        )
    for case, flow_frac in FLOW_CASES.items():
        plan.append(
            {
                "case": case,
                "family": "flow",
                "model": step_model,
                "overrides": build_segmented_flow_overrides(flow_frac),
                "stop_time": effective_flow_stop,
            }
        )
    plan.append(
        {
            "case": UHX_TRIP_CASE,
            "family": "uhx_trip",
            "model": trip_model,
            "overrides": build_segmented_uhx_trip_overrides(),
            "stop_time": float(trip_stop_time),
        }
    )
    return plan


def csv_reaches_stop_time(csv_path: str | Path, stop_time: float) -> bool:
    """Return True when the CSV's final data row reaches ~stop_time.

    Local mirror of freq/_common.csv_reaches_stop_time (Ambiguity I,
    TASK-20260823-04): backward tail read that brackets the newline STARTING
    the final NON-EMPTY row and parses that row WHOLE, because SegmentedMSR
    9R result rows are tens of KB wide and a fixed window would slice
    mid-row. Returns False for empty/header-only/single-line files, malformed
    times, or newline-free tails beyond the 1 MB scan cap.
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


# ---------------------------------------------------------------------------
# Run-provenance wiring (TASK-20260826-02 P4, sharing helpers/run_results.py
# with the startup and frequency workflows): per-case manifests are recorded
# beside each <case>_res.csv, any prior result artifacts are quarantined
# BEFORE launch -- closing the stale-acceptance hole where an executable or
# omc exited 0 without producing output and a pre-existing CSV silently
# passed -- and every fresh CSV must pass validation (freshness against this
# run's launch timestamp included) before both sidecars are written.
#
# Callers of run_case / run_case_segmented that omit ``provenance_context``
# keep the historical byte-behavior (no lifecycle, no sidecars); the CLI
# drivers always supply one. One documented exception rides the legacy
# simulate() context: stop-time coverage stays unenforced there because the
# route never had a tail check (preserved byte-for-byte by its layout pins).
# ---------------------------------------------------------------------------
TRANSIENTS_RESULT_REQUIRED_COLUMNS: tuple[str, ...] = ("time",)

#: Mirrors the historical >= 99.5 % stop-reach rule used by the segmented
#: tail check so finished trajectories are not rejected over rounding.
TRANSIENTS_VALIDATION_STOP_SLACK_RATIO = 0.005


def _run_results():
    """Lazily import the shared run-result provenance library."""
    try:
        from helpers import run_results as rr
    except ImportError:  # script-style execution from transients/
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from helpers import run_results as rr
    return rr


def _provenance_workflow_version() -> str:
    """Workflow implementation version recorded in every manifest."""
    return f"transients-run_nonlinear_steps/run_results-{_run_results().__version__}"


def _workflow_python_sources(package: str) -> list[str]:
    """Workflow Python modules whose bytes enter the run fingerprint.

    Complete by construction for this call site: the active runner module,
    ``transients/paths.py`` (shared result-path module), and
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
    return TRANSIENTS_VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _parse_override_payload(payload: str) -> dict[str, str]:
    """Split an ``a=1,b=2`` override payload into a plain string-keyed dict.

    Works for array keys too (``externalReactivityAmplitude[2]=589.069``);
    values stay as text because only the canonical JSON form is hashed.
    """
    return dict(part.split("=", 1) for part in payload.split(",") if "=" in part)


def _static_provenance(
    *,
    package: str,
    solver: str,
    tolerance: float,
    number_of_intervals: int,
    max_step_size: float,
    source_files: list[str],
    label: str,
    enforce_stop_coverage: bool,
    min_samples: int | None,
) -> dict:
    """Static per-invocation fields shared by every case manifest.

    Tool/revision probes degrade to their documented unknown/unavailable
    states instead of aborting a sweep over a hiccup.
    """
    rr = _run_results()
    return {
        "package": package,
        "solver": str(solver),
        "tolerance": float(tolerance),
        "number_of_intervals": int(number_of_intervals),
        "max_step_size": float(max_step_size),
        "source_files": [str(path) for path in source_files],
        "label": str(label),
        "enforce_stop_coverage": bool(enforce_stop_coverage),
        "min_samples": min_samples,
        "omc_version": _probe(rr.probe_omc_version, "unavailable"),
        "git_info": _probe(
            rr.collect_git_info,
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        "setpoint_table_path": None,
    }


def build_case_manifest(provenance_context: dict, *, case: str, model_name: str, overrides_payload: str, stop_time: float) -> dict:
    """Build the canonical manifest for ONE transient case.

    ``provenance_context`` carries the invocation-static fields; the case
    name, vehicle, override payload, and horizon enter through keyword
    arguments. Every override key/value that reaches the model is part of
    the fingerprint; the launch timestamp is recorded but volatile.
    """
    rr = _run_results()
    overrides: dict[str, object] = {
        "case": str(case),
        "package": str(provenance_context["package"]),
        "core_model": str(provenance_context.get("core_model", "")),
        "max_step_size": float(provenance_context["max_step_size"]),
        "sync_loop_setpoints": bool(
            provenance_context.get("sync_loop_setpoints", False)
        ),
        **_parse_override_payload(overrides_payload),
    }
    setpoint_path = provenance_context.get("setpoint_table_path")
    return rr.build_run_manifest(
        package_name=model_name.rsplit(".", 1)[0] if "." in model_name else model_name,
        model_name=str(model_name),
        source_files=list(provenance_context["source_files"]),
        workflow_python_files=_workflow_python_sources(
            provenance_context["package"]
        ),
        overrides=overrides,
        solver=str(provenance_context["solver"]),
        tolerance=float(provenance_context["tolerance"]),
        start_time=0.0,
        stop_time=float(stop_time),
        number_of_intervals=int(provenance_context["number_of_intervals"]),
        output_grid=(
            "segmented_executable_fixed_stepSize_from_intervals"
            if provenance_context["package"] == SEGMENTED_PACKAGE
            else "legacy_simulate_numberOfIntervals"
        ),
        setpoint_table_path=setpoint_path,
        backend="local",
        omc_version=str(provenance_context.get("omc_version", "unavailable")),
        git_info=provenance_context.get(
            "git_info",
            {"commit": "unknown", "dirty": None, "detected": False},
        ),
        workflow_version=_provenance_workflow_version(),
    )


def _quarantine_prior_result(
    expected_csv: Path,
    *,
    manifest: dict,
    provenance_context: dict,
    quarantine_root: str | Path | None = None,
) -> None:
    """Move any prior artifacts for this slot into the quarantine area."""
    rr = _run_results()
    prepared = rr.prepare_result_path(
        expected_csv,
        reuse_ok=False,
        expected_manifest=manifest,
        required_columns=TRANSIENTS_RESULT_REQUIRED_COLUMNS,
        quarantine_root=quarantine_root,
    )
    if prepared.quarantined:
        destination = prepared.quarantined[0].parent
        print(
            f"  [{provenance_context.get('label', '')}] quarantined "
            f"{len(prepared.quarantined)} prior result artifact(s) to "
            f"{destination}"
        )


def _record_validated_result(
    expected_csv: Path,
    *,
    manifest: dict,
    provenance_context: dict,
    quarantine_root: str | Path | None = None,
) -> None:
    """Validate a fresh CSV; on success publish it and write both sidecars.

    The raw output is expected at the ``{expected_csv}.tmp`` companion the
    simulation was directed at; a passing report publishes those bytes onto
    ``expected_csv`` atomically before the sidecars are written.  A rejected
    temporary output is parked under the quarantine area instead, so a
    failed or interrupted run never leaves a reusable final result.  Raises
    RuntimeError listing the failed check names otherwise.
    """
    rr = _run_results()
    try:
        rr.publish_validated_result(
            rr.make_tmp_path(expected_csv),
            expected_csv,
            manifest=manifest,
            required_columns=TRANSIENTS_RESULT_REQUIRED_COLUMNS,
            time_column="time",
            requested_stop_time=(
                float(manifest.get("stop_time") or 0.0)
                if provenance_context.get("enforce_stop_coverage", True)
                else None
            ),
            stop_time_slack_s=_validation_stop_slack(float(manifest.get("stop_time") or 0.0)),
            min_samples=provenance_context.get("min_samples", rr.DEFAULT_MIN_SAMPLES),
            quarantine_root=quarantine_root,
        )
    except rr.ResultRejectedError as exc:
        failed_checks = ", ".join(exc.report.failed_checks)
        raise RuntimeError(
            f"simulation output rejected by result validation for "
            f"{expected_csv.name}; failed checks: {failed_checks}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"simulation completed but the validated result or its "
            f"provenance sidecars could not be published beside "
            f"{expected_csv.name}: {exc}"
        ) from exc


def run_case_segmented(
    omc: str,
    model_name: str,
    mos_text: str,
    workdir: Path,
    *,
    case_prefix: str,
    stop_time: float,
    number_of_intervals: int,
    max_step_size: float,
    overrides: str,
    omc_timeout_seconds: float | None = None,
    skip_build: bool = False,
    provenance_context: dict | None = None,
    quarantine_root: str | Path | None = None,
    claim_timeout_s: float | None = None,
) -> None:
    """Two-stage segmented execution for one case (omc 1.27 constraints).

    Stage 1 runs ``omc <case_prefix>.mos`` (single loadFile +
    ``buildModel(..., tolerance=...)``; tolerance is baked at BUILD time --
    ``-rtol/-atol`` are NOT runtime flags). Stage 2 drives the generated
    executable directly with ``-outputFormat=csv -stopTime=N
    -stepSize=(stop/intervals)`` (omc 1.27 rejects
    ``-numberOfIntervals=`` at runtime) plus the ``-override=`` payload and
    the ``-maxStepSize=`` cap. On the provenanced route the executable is
    pointed at the temporary companion ``-r=<case>_res.csv.tmp``, which is
    validated and then published atomically as ``<case>_res.csv``; without
    provenance the historical final name ``-r=<case>_res.csv`` is used
    directly. Fails loudly on nonzero exits, a missing
    executable/result CSV, or a trajectory that does not reach stop_time.

    With ``provenance_context`` the result slot is claimed exclusively, then
    cleared through quarantine BEFORE anything launches; the fresh output is
    validated and published atomically before both provenance sidecars are
    written beside it.  Omitting the context keeps the historical behavior
    for direct programmatic calls.

    REV-2fdd01d-03: ``skip_build=True`` still writes the case .mos (the
    per-case buildModel record stays complete) but SKIPS the omc stage so
    the caller can reuse an executable built earlier in the same workdir;
    the executable-existence check remains as the safety net.
    """
    timeout = (
        omc_timeout_seconds
        if omc_timeout_seconds and omc_timeout_seconds > 0
        else None
    )
    expected_csv = workdir / f"{case_prefix}_res.csv"
    result_tmp_csv = _run_results().make_tmp_path(expected_csv)
    manifest: dict | None = None
    claim = None
    if provenance_context is not None:
        manifest = build_case_manifest(
            provenance_context,
            case=case_prefix,
            model_name=model_name,
            overrides_payload=overrides,
            stop_time=float(stop_time),
        )
        # Same-slot claim (P5): a second concurrent launch of this slot
        # blocks here instead of quarantining this run's in-flight *.tmp.
        # --claim_timeout_s bounds the wait (M2 / REV008-10).
        claim = _run_results().acquire_result_claim(
            expected_csv, timeout_s=claim_timeout_s
        )
    try:
        if manifest is not None and provenance_context is not None:
            _quarantine_prior_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )

        mos_path = workdir / f"{case_prefix}.mos"
        mos_path.write_text(mos_text)

        omc_stdout_path = workdir / f"{case_prefix}_omc_stdout.log"
        if not skip_build:
            omc_stderr_path = workdir / f"{case_prefix}_omc_stderr.log"
            try:
                with omc_stdout_path.open("w") as stdout, omc_stderr_path.open("w") as stderr:
                    result = subprocess.run(
                        [omc, mos_path.name],
                        stdout=stdout,
                        stderr=stderr,
                        cwd=workdir,
                        timeout=timeout,
                    )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"OMC timed out after {exc.timeout:g} s for {mos_path.name}"
                ) from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"OMC failed for {mos_path.name} (exit {result.returncode})"
                )

        exe_path = workdir / model_name
        if not exe_path.exists():
            if skip_build:
                raise RuntimeError(
                    f"no executable {model_name} to reuse for {case_prefix} "
                    f"(build was skipped)"
                )
            stdout_tail = omc_stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"buildModel produced no executable {model_name} for "
                f"{case_prefix}\n{stdout_tail}"
            )

        exe_cmd = [
            f"./{model_name}",
            f"-stopTime={stop_time:.10g}",
            # Translate the requested interval count into an equidistant output
            # grid (startTime is 0 for every transient case).
            f"-stepSize={stop_time / number_of_intervals:.17g}",
            "-outputFormat=csv",
            # Provenanced runs publish through the temporary companion;
            # direct programmatic calls keep the historical final name.
            (
                f"-r={result_tmp_csv.name}"
                if manifest is not None
                else f"-r={case_prefix}_res.csv"
            ),
            f"-maxStepSize={max_step_size:.10g}",
            f"-override={overrides}",
        ]
        exe_stdout_path = workdir / f"{case_prefix}_exe_stdout.log"
        exe_stderr_path = workdir / f"{case_prefix}_exe_stderr.log"
        try:
            with exe_stdout_path.open("w") as stdout, exe_stderr_path.open("w") as stderr:
                exe_result = subprocess.run(
                    exe_cmd,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=workdir,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"simulation executable timed out after {exc.timeout:g} s "
                f"for {case_prefix}"
            ) from exc
        if exe_result.returncode != 0:
            stderr_tail = exe_stderr_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"simulation executable failed for {case_prefix} "
                f"(exit {exe_result.returncode})\n{stderr_tail}"
            )

        raw_csv = result_tmp_csv if manifest is not None else expected_csv
        if manifest is not None and not raw_csv.exists():
            # An engine that ignored the -r= redirect leaves the result at
            # the final name (historical layout); validate it in place.
            raw_csv = expected_csv
        if not raw_csv.exists():
            stdout_tail = exe_stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"executable exited 0 but produced no result CSV for "
                f"{case_prefix}: {expected_csv.name}\n{stdout_tail}"
            )
        if not csv_reaches_stop_time(raw_csv, stop_time):
            raise RuntimeError(
                f"simulation output did not reach requested stop_time for "
                f"{case_prefix}; see {exe_stderr_path.name}"
            )
        if manifest is not None and provenance_context is not None:
            _record_validated_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
    finally:
        if claim is not None:
            claim.release()


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsupported argument combinations before any simulation starts.

    Legacy invocations pass trivially (zero behavior change). Segmented mode
    hard-errors every legacy-only knob (DECISION, card R2: hard-error over
    loud-skip, matching the landed freq precedent of Ambiguity B): the
    setpoints CSV inputs, the init-mode overrides they feed, and the 9R
    loop-setpoint harmonization do not exist on the trimmed SegmentedMSR
    rigs, which carry their own SteadyState init.
    """
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    if package != SEGMENTED_PACKAGE:
        return
    if args.stop_time <= 0:
        raise ValueError("--stop_time must be > 0 with --package segmented.")
    if args.number_of_intervals < 1:
        raise ValueError(
            "--number_of_intervals must be >= 1 with --package segmented."
        )
    if args.tolerance <= 0:
        raise ValueError(
            "--tolerance must be > 0 with --package segmented (it is baked "
            "into buildModel at BUILD time; -rtol/-atol are not runtime "
            "flags)."
        )
    if args.method.strip().lower() != "dassl":
        raise ValueError(
            "--method is unsupported with --package segmented: only the "
            "built-in dassl route of the generated executables is exercised."
        )
    if args.setpoints_csv is not None:
        raise ValueError(
            "--setpoints_csv is unsupported with --package segmented: no "
            "segmented setpoint tables exist; the rigs carry their own "
            "trimmed SteadyState init."
        )
    repo_root = Path(__file__).resolve().parents[1]
    for core_key in ("1r", "9r"):
        default_csv = repo_root / "core" / "init" / f"setpoints_{core_key}.csv"
        provided = getattr(args, f"setpoints_csv_{core_key}").resolve()
        if provided != default_csv.resolve():
            raise ValueError(
                f"--setpoints_csv_{core_key} is unsupported with --package "
                "segmented: no segmented setpoint tables exist; the rigs "
                "carry their own trimmed SteadyState init."
            )
    if args.no_sync_loop_setpoints:
        raise ValueError(
            "--no_sync_loop_setpoints is unsupported with --package "
            "segmented: the 9R loop-setpoint harmonization path does not "
            "exist in segmented mode."
        )


# Shared primary/secondary-loop initialization fields. These should represent the
# same plant hardware between 1R and 9R runs at a given nominal operating point.
SHARED_LOOP_SETPOINT_KEYS = (
    "heatExchanger.TpIn_0",
    "heatExchanger.TpOut_0",
    "heatExchanger.TsIn_0",
    "heatExchanger.TsOut_0",
    "heatExchanger.T_PN1_0",
    "heatExchanger.T_PN2_0",
    "heatExchanger.T_PN3_0",
    "heatExchanger.T_PN4_0",
    "heatExchanger.T_TN1_0",
    "heatExchanger.T_TN2_0",
    "heatExchanger.T_SN1_0",
    "heatExchanger.T_SN2_0",
    "heatExchanger.T_SN3_0",
    "heatExchanger.T_SN4_0",
    "pipeHXtoUHX.T_0",
    "pipeUHXtoHX.T_0",
    "dhrs.T_0",
    "pipeDHRStoHX.T_0",
    "pipeHXtoCore.T_0",
    "pipeCoreToDHRS.T_0",
    "uhx.Tp_0",
)

CORE_CONFIG = {
    "1r": {
        "label": "1R",
        "setpoints_file": "setpoints_1r.csv",
        "init_mode_override": "core1R.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState",
        "step_models": {
            "step_2dol": "MSRR.Transients.R1fullSteps.R1MSRR2dol",
            "step_1dol": "MSRR.Transients.R1fullSteps.R1MSRR1dol",
            "step_0p5dol": "MSRR.Transients.R1fullSteps.R1MSRRhalfDol",
            "step_0p1dol": "MSRR.Transients.R1fullSteps.R1MSRRpOneDol",
            "step_100pcm": "MSRR.Transients.R1fullSteps.R1MSRR100pcm",
            "step_10pcm": "MSRR.Transients.R1fullSteps.R1MSRR10pcm",
        },
        "nominal_thermal_model": "MSRR.MSRRuhxNominalTrimThermalSS",
        "uhx_model": "MSRR.MSRRuhxTripThermalSS",
        "uhx_extra_overrides": "",
    },
    "9r": {
        "label": "9R",
        "setpoints_file": "setpoints_9r.csv",
        "init_mode_override": "msre9r.mpke.initMode=SMD_MSR_Modelica.Units.InitMode.SteadyState",
        # Dedicated R9fullSteps models fail to build in current branch.
        # Use nominal thermal-SS model with explicit external step overrides instead.
        "step_models": {},
        "step_via_overrides": True,
        "nominal_thermal_model": "MSRR.MSRRuhxNominalTrim9RThermalSS",
        "uhx_model": "MSRR.MSRRuhxTrip9RThermalSS",
        "uhx_extra_overrides": "",
    },
}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run nonlinear step dynamics simulations.")
    parser.add_argument(
        "--core_dir",
        type=Path,
        default=repo_root / "core",
        help="Path to core Modelica directory",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=None,
        help=(
            "Output directory for CSV/logs "
            "(default: 00runs/transients-<core_models>; segmented runs "
            "nest under an additional segmented/ component)"
        ),
    )
    parser.add_argument(
        "--core_models",
        type=str,
        nargs="+",
        choices=tuple(CORE_CONFIG.keys()),
        default=["1r", "9r"],
        help="Core models to run (default: 1r 9r).",
    )
    parser.add_argument(
        "--package",
        type=str,
        choices=PACKAGE_CHOICES,
        default=DEFAULT_PACKAGE,
        help=(
            "Model family to drive (default: legacy). 'legacy' loads "
            "SMD_MSR_Modelica.mo + MSRR.mo with unchanged behavior; "
            "'segmented' drives the standalone SegmentedMSR trim/trip rigs "
            "(loads ONLY SegmentedMSR.mo; buildModel + direct executable "
            "because omc 1.27 simulate() scripting is broken), ignores the "
            "legacy setpoints CSVs and the 9R loop-setpoint harmonization "
            "(the rigs carry their own trimmed SteadyState init), and rides "
            "the TRIP_MODEL_BY_CORE wrappers for uhx_trip with a >=4500 s "
            "horizon floor plus >=4400 s flow-case floors covering the "
            "t=4000 s reactivity insertion. Segmented result CSVs report "
            "temperatures in kelvin."
        ),
    )
    parser.add_argument(
        "--setpoints_csv",
        type=Path,
        default=None,
        help="Deprecated alias for --setpoints_csv_1r (legacy mode only).",
    )
    parser.add_argument(
        "--setpoints_csv_1r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_1r.csv",
        help="Setpoints CSV for 1R 1 MW initialization (legacy mode only)",
    )
    parser.add_argument(
        "--setpoints_csv_9r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_9r.csv",
        help="Setpoints CSV for 9R 1 MW initialization (legacy mode only)",
    )
    parser.add_argument(
        "--no_sync_loop_setpoints",
        action="store_true",
        help=(
            "Do not harmonize 9R shared loop initialization fields to the 1R "
            "nominal setpoint row (legacy mode only)."
        ),
    )
    parser.add_argument("--omc", type=str, default="omc", help="OpenModelica compiler")
    parser.add_argument("--stop_time", type=float, default=10000.0, help="Stop time [s]")
    parser.add_argument(
        "--number_of_intervals",
        type=int,
        default=10000,
        help="Number of output intervals",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-6,
        help=(
            "Solver tolerance (segmented mode bakes it into buildModel at "
            "BUILD time; -rtol/-atol are not runtime flags)"
        ),
    )
    parser.add_argument(
        "--method",
        type=str,
        default="dassl",
        help=(
            "Solver method (legacy mode only; segmented mode exercises the "
            "built-in dassl route of the generated executables)"
        ),
    )
    parser.add_argument(
        "--max_step_size",
        type=float,
        default=0.02,
        help="Maximum solver step size",
    )
    parser.add_argument(
        "--omc_timeout_seconds",
        type=float,
        default=0.0,
        help=(
            "Maximum wall-clock seconds per omc simulation before it is "
            "treated as failed (0 disables the timeout, default: 0)."
        ),
    )
    parser.add_argument(
        "--claim_timeout_s",
        type=float,
        default=None,
        help=(
            "Maximum seconds to wait for a busy result-slot claim when "
            "another launch holds the same transient case; expiry raises "
            "ResultSlotClaimTimeout naming the holder. Default: wait "
            "indefinitely (previous behavior)."
        ),
    )
    args = parser.parse_args()
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    if args.out_dir is None:
        args.out_dir = default_transients_run_dir(
            repo_root,
            core_models=args.core_models,
        )
    return args


def load_setpoints(csv_path: Path, power_level: float = 1.0) -> dict[str, float]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing setpoints CSV: {csv_path}")
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"Setpoints CSV is empty: {csv_path}")
    match = None
    for row in rows:
        try:
            if abs(float(row.get("power", "nan")) - power_level) < 1e-9:
                match = row
                break
        except ValueError:
            continue
    if match is None:
        raise ValueError(f"No setpoints row found for power={power_level} in {csv_path}")

    def get(key: str) -> float:
        val = match.get(key)
        if val is None:
            raise KeyError(f"Missing column '{key}' in {csv_path}")
        return float(val)

    overrides = {
        "fuelTempSetPointNode1": get("fuelTempSetPointNode1"),
        "fuelTempSetPointNode2": get("fuelTempSetPointNode2"),
        "graphiteTempSetPoint": get("graphiteTempSetPoint"),
        "heatExchanger.TpIn_0": get("heatExchanger.TpIn_0"),
        "heatExchanger.TpOut_0": get("heatExchanger.TpOut_0"),
        "heatExchanger.TsIn_0": get("heatExchanger.TsIn_0"),
        "heatExchanger.TsOut_0": get("heatExchanger.TsOut_0"),
        "heatExchanger.T_PN1_0": get("heatExchanger.T_PN1_0"),
        "heatExchanger.T_PN2_0": get("heatExchanger.T_PN2_0"),
        "heatExchanger.T_PN3_0": get("heatExchanger.T_PN3_0"),
        "heatExchanger.T_PN4_0": get("heatExchanger.T_PN4_0"),
        "heatExchanger.T_TN1_0": get("heatExchanger.T_TN1_0"),
        "heatExchanger.T_TN2_0": get("heatExchanger.T_TN2_0"),
        "heatExchanger.T_SN1_0": get("heatExchanger.T_SN1_0"),
        "heatExchanger.T_SN2_0": get("heatExchanger.T_SN2_0"),
        "heatExchanger.T_SN3_0": get("heatExchanger.T_SN3_0"),
        "heatExchanger.T_SN4_0": get("heatExchanger.T_SN4_0"),
        "pipeHXtoUHX.T_0": get("pipeHXtoUHX.T_0"),
        "pipeUHXtoHX.T_0": get("pipeUHXtoHX.T_0"),
        "dhrs.T_0": get("dhrs.T_0"),
        "pipeDHRStoHX.T_0": get("pipeDHRStoHX.T_0"),
        "pipeHXtoCore.T_0": get("pipeHXtoCore.T_0"),
        "pipeCoreToDHRS.T_0": get("pipeCoreToDHRS.T_0"),
        "uhx.Tp_0": get("uhx.Tp_0"),
    }
    # 9R setpoint tables include regional initial temperatures; apply them when present.
    for prefix in ("TF1_0_regions", "TF2_0_regions", "TG_0_regions"):
        for idx in range(1, 10):
            key = f"{prefix}[{idx}]"
            raw = (match.get(key) or "").strip()
            if raw:
                overrides[key] = float(raw)
    tmix_raw = (match.get("Tmix_0") or "").strip()
    if tmix_raw:
        overrides["Tmix_0"] = float(tmix_raw)
    return overrides


def build_setpoint_override(setpoints: dict[str, float], init_mode_override: str) -> str:
    setpoint_overrides = ",".join(f"{k}={v:.10g}" for k, v in setpoints.items())
    return (
        f"{setpoint_overrides},"
        "heatExchanger.detailedStateInitWeight=1,"
        f"{init_mode_override}"
    )


def build_mos(
    library_path: Path,
    model_path: Path,
    model_name: str,
    start_time: float,
    stop_time: float,
    number_of_intervals: int,
    tolerance: float,
    method: str,
    file_prefix: str,
    simflags: str,
) -> str:
    return (
        "// Generated by transients/run_nonlinear_steps.py\n"
        f'loadFile("{library_path}");\n'
        f'loadFile("{model_path}");\n'
        f"simulate({model_name},"
        f"startTime={start_time:.10g},"
        f"stopTime={stop_time:.10g},"
        f"numberOfIntervals={number_of_intervals},"
        f"tolerance={tolerance:.10g},"
        f"method={method},"
        f'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}",'
        f'simflags="{simflags}");\n'
    )


def run_case(
    omc: str,
    mos_text: str,
    mos_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    workdir: Path,
    expected_csv: Path,
    omc_timeout_seconds: float | None = None,
    provenance_context: dict | None = None,
    quarantine_root: str | Path | None = None,
    claim_timeout_s: float | None = None,
) -> None:
    """Run one ``simulate()`` case via omc scripting.

    With ``provenance_context`` the result slot is claimed exclusively, then
    cleared through quarantine BEFORE anything launches; the fresh output is
    validated and published atomically before both provenance sidecars are
    written beside it.  Omitting the context keeps the historical behavior
    for direct programmatic calls.
    """
    rr = _run_results()
    manifest: dict | None = None
    claim = None
    if provenance_context is not None:
        manifest = build_case_manifest(
            provenance_context,
            case=mos_path.stem,
            model_name=str(provenance_context["model_name"]),
            overrides_payload=str(provenance_context["overrides_payload"]),
            stop_time=float(provenance_context["stop_time"]),
        )
        # Same-slot claim (P5): a second concurrent launch of this slot
        # blocks here instead of quarantining this run's in-flight *.tmp.
        # --claim_timeout_s bounds the wait (M2 / REV008-10).
        claim = rr.acquire_result_claim(expected_csv, timeout_s=claim_timeout_s)
    try:
        if manifest is not None and provenance_context is not None:
            _quarantine_prior_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
        raw_csv = rr.make_tmp_path(expected_csv) if manifest is not None else expected_csv
        mos_path.write_text(mos_text)
        try:
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                result = subprocess.run(
                    [omc, str(mos_path)],
                    stdout=stdout,
                    stderr=stderr,
                    cwd=workdir,
                    timeout=(
                        omc_timeout_seconds
                        if omc_timeout_seconds and omc_timeout_seconds > 0
                        else None
                    ),
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"OMC timed out after {exc.timeout:g} s for {mos_path.name}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(f"OMC failed for {mos_path.name} (exit {result.returncode})")
        produced_csv = raw_csv
        if manifest is not None and not produced_csv.exists():
            # An engine that ignored the -r= redirect leaves the result at
            # the final name (historical layout); validate it in place.
            produced_csv = expected_csv
        if not produced_csv.exists():
            stdout_tail = stdout_path.read_text(errors="ignore")[-1000:]
            raise RuntimeError(
                f"OMC did not produce expected CSV: {expected_csv.name}\n"
                f"{stdout_tail}"
            )
        if manifest is not None and provenance_context is not None:
            _record_validated_result(
                expected_csv,
                manifest=manifest,
                provenance_context=provenance_context,
                quarantine_root=quarantine_root,
            )
    finally:
        if claim is not None:
            claim.release()


def _main_legacy(args: argparse.Namespace) -> int:
    core_dir = args.core_dir.resolve()
    library_path = core_dir / "SMD_MSR_Modelica.mo"
    model_path = core_dir / "MSRR.mo"

    if not library_path.exists():
        raise FileNotFoundError(f"Missing library file: {library_path}")
    if not model_path.exists():
        raise FileNotFoundError(f"Missing model file: {model_path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    setpoint_paths = {
        "1r": args.setpoints_csv_1r.resolve(),
        "9r": args.setpoints_csv_9r.resolve(),
    }
    if args.setpoints_csv is not None:
        setpoint_paths["1r"] = args.setpoints_csv.resolve()

    # Production CLI runs always wire per-case provenance: prior result
    # artifacts are quarantined before launch, outputs are validated, and
    # manifest/validation sidecars record everything beside each CSV. The
    # legacy simulate() route never had a tail check, so stop-time coverage
    # stays unenforced there to preserve its byte-pinned acceptance
    # behavior; freshness against this run's launch stamp still applies.
    legacy_static = _static_provenance(
        package=LEGACY_PACKAGE,
        solver=str(args.method),
        tolerance=float(args.tolerance),
        number_of_intervals=int(args.number_of_intervals),
        max_step_size=float(args.max_step_size),
        source_files=[str(library_path), str(model_path)],
        label="",
        enforce_stop_coverage=False,
        min_samples=None,
    )

    simflags_base = f"-maxStepSize={args.max_step_size:.10g}"
    core_models = list(dict.fromkeys(args.core_models))
    shared_loop_setpoints: dict[str, float] | None = None
    if not args.no_sync_loop_setpoints:
        shared_loop_setpoints = load_setpoints(setpoint_paths["1r"], power_level=1.0)

    for core_model in core_models:
        config = CORE_CONFIG[core_model]
        core_out_dir = out_dir / core_model
        core_out_dir.mkdir(parents=True, exist_ok=True)

        core_provenance = {
            **legacy_static,
            "label": config["label"],
            "core_model": core_model,
            "setpoint_table_path": str(setpoint_paths[core_model]),
            "sync_loop_setpoints": not args.no_sync_loop_setpoints,
        }

        setpoints = load_setpoints(setpoint_paths[core_model], power_level=1.0)
        if core_model == "9r" and shared_loop_setpoints is not None:
            for key in SHARED_LOOP_SETPOINT_KEYS:
                if key in shared_loop_setpoints:
                    setpoints[key] = shared_loop_setpoints[key]
            print("[9R] Harmonized shared loop setpoints to 1R nominal row.")
        setpoint_overrides = build_setpoint_override(
            setpoints=setpoints,
            init_mode_override=config["init_mode_override"],
        )

        print(f"[{config['label']}] Running nominal-flow step insertions...")
        for case in STEP_PCM:
            model_name = config["step_models"].get(case, config["nominal_thermal_model"])
            mos_path = core_out_dir / f"{case}.mos"
            stdout_path = core_out_dir / f"{case}_omc_stdout.log"
            stderr_path = core_out_dir / f"{case}_omc_stderr.log"
            if config.get("step_via_overrides", False):
                overrides = (
                    f"{setpoint_overrides},"
                    "primaryPump.freeConvFF=1,"
                    "secondaryPump.freeConvFF=1,"
                    "powerLevel=1,"
                    "perturbationAmplitudePcm=0,"
                    f"externalReactivityAmplitude[2]={STEP_PCM[case]:.10g},"
                    "externalReactivityStepTime[2]=2000"
                )
                simflags = (
                    f"{simflags_base} -override={overrides}"
                    # Production route: result lands on the *.tmp companion
                    # for validated atomic publication (P5).
                    f" -r={case}_res.csv.tmp"
                )
                overrides_payload = overrides
            else:
                simflags = (
                    f"{simflags_base} "
                    "-override=externalReact.stepTime[2]=2000"
                    f" -r={case}_res.csv.tmp"
                )
                overrides_payload = "externalReact.stepTime[2]=2000"
            mos_text = build_mos(
                library_path,
                model_path,
                model_name,
                start_time=0.0,
                stop_time=args.stop_time,
                number_of_intervals=args.number_of_intervals,
                tolerance=args.tolerance,
                method=args.method,
                file_prefix=case,
                simflags=simflags,
            )
            run_case(
                args.omc,
                mos_text,
                mos_path,
                stdout_path,
                stderr_path,
                core_out_dir,
                core_out_dir / f"{case}_res.csv",
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                provenance_context={
                    **core_provenance,
                    "model_name": model_name,
                    "overrides_payload": overrides_payload,
                    "stop_time": float(args.stop_time),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )

        print(f"[{config['label']}] Running variable-flow step insertions...")
        for case, flow_frac in FLOW_CASES.items():
            mos_path = core_out_dir / f"{case}.mos"
            stdout_path = core_out_dir / f"{case}_omc_stdout.log"
            stderr_path = core_out_dir / f"{case}_omc_stderr.log"
            overrides = (
                f"{setpoint_overrides},"
                f"primaryPump.freeConvFF={flow_frac},"
                "secondaryPump.freeConvFF=1,"
                f"primaryPump.rampUpTo[1]={1.0 - flow_frac:.10g},"
                "primaryPump.rampUpTime[1]=0,"
                "primaryPump.tripTime=2000,"
                "powerLevel=1,"
                "perturbationAmplitudePcm=0,"
                f"externalReactivityAmplitude[2]={STEP_PCM['step_1dol']:.10g},"
                "externalReactivityStepTime[2]=4000"
            )
            mos_text = build_mos(
                library_path,
                model_path,
                config["nominal_thermal_model"],
                start_time=0.0,
                stop_time=args.stop_time,
                number_of_intervals=args.number_of_intervals,
                tolerance=args.tolerance,
                method=args.method,
                file_prefix=case,
                simflags=(
                    f"{simflags_base} -override={overrides}"
                    f" -r={case}_res.csv.tmp"
                ),
            )
            run_case(
                args.omc,
                mos_text,
                mos_path,
                stdout_path,
                stderr_path,
                core_out_dir,
                core_out_dir / f"{case}_res.csv",
                omc_timeout_seconds=(
                    args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
                ),
                provenance_context={
                    **core_provenance,
                    "model_name": config["nominal_thermal_model"],
                    "overrides_payload": overrides,
                    "stop_time": float(args.stop_time),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )

        print(f"[{config['label']}] Running UHX-trip scenario...")
        uhx_prefix = UHX_TRIP_CASE
        mos_path = core_out_dir / f"{UHX_TRIP_CASE}.mos"
        stdout_path = core_out_dir / f"{UHX_TRIP_CASE}_omc_stdout.log"
        stderr_path = core_out_dir / f"{UHX_TRIP_CASE}_omc_stderr.log"
        uhx_overrides = (
            f"{setpoint_overrides},"
            "primaryPump.freeConvFF=1,"
            "secondaryPump.freeConvFF=1,"
            "primaryPump.rampUpTo[1]=1.0,"
            "primaryPump.rampUpTime[1]=0,"
            "powerLevel=1,"
            "perturbationAmplitudePcm=0"
            f"{config['uhx_extra_overrides']}"
        )
        uhx_stop_time = max(args.stop_time, 4000.0 + 4.0 * 3600.0)
        mos_text = build_mos(
            library_path,
            model_path,
            config["uhx_model"],
            start_time=0.0,
            stop_time=uhx_stop_time,
            number_of_intervals=args.number_of_intervals,
            tolerance=args.tolerance,
            method=args.method,
            file_prefix=uhx_prefix,
            simflags=(
                f"{simflags_base} -override={uhx_overrides}"
                f" -r={uhx_prefix}_res.csv.tmp"
            ),
        )
        run_case(
            args.omc,
            mos_text,
            mos_path,
            stdout_path,
            stderr_path,
            core_out_dir,
            core_out_dir / f"{uhx_prefix}_res.csv",
            omc_timeout_seconds=(
                args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
            ),
            provenance_context={
                **core_provenance,
                "model_name": config["uhx_model"],
                "overrides_payload": uhx_overrides,
                "stop_time": float(uhx_stop_time),
            },
            claim_timeout_s=getattr(args, "claim_timeout_s", None),
        )

        print(f"[{config['label']}] Completed. Outputs: {core_out_dir}")

    print(f"All requested runs completed. Outputs in: {out_dir}")
    return 0


def _main_segmented(args: argparse.Namespace) -> int:
    """Segmented-mode driver (standalone SegmentedMSR package).

    Loads ONLY ``core/SegmentedMSR.mo`` per case dir and follows the landed
    freq execution route: ``buildModel(..., tolerance=args.tolerance)`` via
    omc scripting, then the generated executable directly with
    ``-stepSize`` translating the requested interval count. Output
    convention (Ambiguity J, TASK-20260823-04): results nest under an extra
    ``segmented/<core_model>/`` component below ``--out_dir`` so they can
    never collide with legacy transients outputs; file names inside each
    core dir stay identical to legacy (``<case>_res.csv``).

    REV-2fdd01d-03: every case still gets its .mos written (complete
    per-case buildModel record), but each DISTINCT ``item['model']`` is
    built via omc only ONCE per core dir; the later cases sharing that
    model reuse the already-built executable.
    """
    seg = _segmented_helpers()
    core_dir = args.core_dir.resolve()
    library_src = core_dir / seg.SEGMENTED_PACKAGE_FILE
    if not library_src.exists():
        raise FileNotFoundError(f"Missing segmented library file: {library_src}")

    out_dir = args.out_dir.resolve() / "segmented"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeout = args.omc_timeout_seconds if args.omc_timeout_seconds > 0 else None
    core_models = list(dict.fromkeys(args.core_models))

    # Production CLI runs always wire per-case provenance (quarantine before
    # launch, validated outputs, sidecars beside every CSV). Unlike the
    # documented legacy simulate() hatch (_main_legacy), segmented keeps the
    # library's default sample floor via _record_validated_result.
    segmented_static = _static_provenance(
        package=SEGMENTED_PACKAGE,
        solver="dassl",
        tolerance=float(args.tolerance),
        number_of_intervals=int(args.number_of_intervals),
        max_step_size=float(args.max_step_size),
        source_files=[str(library_src)],
        label="",
        enforce_stop_coverage=True,
        min_samples=_run_results().DEFAULT_MIN_SAMPLES,
    )

    for core_model in core_models:
        config = SEGMENTED_CORE_CONFIG[core_model]
        core_provenance = {
            **segmented_static,
            "label": config["label"],
            "core_model": core_model,
            "sync_loop_setpoints": False,
        }
        trip_stop_time = max(float(args.stop_time), SEGMENTED_TRIP_MIN_STOP_TIME_S)
        if trip_stop_time > float(args.stop_time):
            print(
                f"[{config['label']}] Segmented uhx_trip horizon raised to "
                f"{trip_stop_time:g} s to cover the t=4000 s UHX demand drop."
            )
        # REV-2fdd01d-02: same short-horizon floor pattern for the flow
        # cases, whose 1-$ insertion fires at t=4000 s (legacy-mirrored
        # overrides) while the plot window needs data through t=4400 s.
        flow_stop_time = max(float(args.stop_time), SEGMENTED_FLOW_MIN_STOP_TIME_S)
        if flow_stop_time > float(args.stop_time):
            print(
                f"[{config['label']}] Segmented flow-case horizon raised to "
                f"{flow_stop_time:g} s to cover the t="
                f"{SEGMENTED_FLOW_INSERT_TIME_S:g} s reactivity insertion."
            )
        plan = build_segmented_run_plan(
            seg,
            core_model,
            stop_time=float(args.stop_time),
            trip_stop_time=trip_stop_time,
            flow_stop_time=flow_stop_time,
        )
        core_out_dir = out_dir / core_model
        core_out_dir.mkdir(parents=True, exist_ok=True)
        # Standalone SegmentedMSR package: copy ONLY SegmentedMSR.mo into the
        # case dir (single loadFile; no SMD load, no double-load).
        copyfile(library_src, core_out_dir / seg.SEGMENTED_PACKAGE_FILE)

        print(
            f"[{config['label']}] Running segmented nonlinear steps "
            f"({len(plan)} cases, package=segmented)..."
        )
        # REV-2fdd01d-03: the six steps + three flows share the core's trim
        # rig and uhx_trip rides its own wrapper, so omc runs once per
        # DISTINCT model per core dir; later cases reuse the executable.
        built_models: set[str] = set()
        for item in plan:
            mos_text = seg.build_smoke_mos(
                item["model"], tolerance=float(args.tolerance)
            )
            first_build = item["model"] not in built_models
            print(
                f"  [{config['label']}] {item['case']}: {item['model']} "
                f"(stop {item['stop_time']:g} s"
                + (")" if first_build else ", reusing built executable)")
            )
            run_case_segmented(
                args.omc,
                item["model"],
                mos_text,
                core_out_dir,
                case_prefix=item["case"],
                stop_time=item["stop_time"],
                number_of_intervals=args.number_of_intervals,
                max_step_size=args.max_step_size,
                overrides=item["overrides"],
                omc_timeout_seconds=timeout,
                skip_build=not first_build,
                provenance_context={
                    **core_provenance,
                    "model_name": item["model"],
                    "overrides_payload": item["overrides"],
                    "stop_time": float(item["stop_time"]),
                },
                claim_timeout_s=getattr(args, "claim_timeout_s", None),
            )
            built_models.add(item["model"])

        print(f"[{config['label']}] Completed. Outputs: {core_out_dir}")

    print(f"All requested runs completed. Outputs in: {out_dir}")
    return 0


def main() -> int:
    args = parse_args()
    validate_args(args)
    if args.package == SEGMENTED_PACKAGE:
        return _main_segmented(args)
    return _main_legacy(args)


if __name__ == "__main__":
    raise SystemExit(main())
