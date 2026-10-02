#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run startup-oriented MSRR simulations using shared core Modelica files.

With ``--package segmented`` (default: ``legacy``, which keeps the historical
behavior byte-identical) the runner instead drives the standalone SegmentedMSR
full-loop trim rigs from ``helpers/segmented_runs.py``:

    1r: SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS
    9r: SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS
    1r10seg: SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS
    r5x5_z10: SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS

loading ``SegmentedMSR_PlantData.mo`` then ``core/SegmentedMSR.mo``.
``1r10seg`` (1-channel x 10-axial-segment 1R core, TASK-20260906-01) and
``r5x5_z10`` (5x5-radial x 10-axial-segment 1R core, TASK-20260906-02) are
segmented-package only: the legacy package ships no vehicle for either, so
``--package legacy --core_model {1r10seg,r5x5_z10}`` is rejected with a named
error before any omc invocation.

Because ``omc`` 1.27 ``simulate()`` scripting is broken system-wide,
segmented runs
use ``buildModel(...)`` plus the generated executable directly, translating
the requested interval count into an equidistant output grid (``-stepSize``;
omc 1.27 rejects ``-numberOfIntervals=`` at runtime).

Segmented startup semantics (review 2026-10-01 H2/H3): every segmented
startup scenario -- the base ``startup`` approach to criticality and the
to-power YAML (``startup_to_100kw`` / ``startup_to_1mw``) alike -- emits a
structural scenario wrapper (``helpers/emit_scenario_wrapper.py``) on the
trim rig into the run dir, carrying the scenario YAML's schedule: the
29-step reactivity staircase, the pulsed 1e8 n/s source windows, the
three-stage pump ramp, ``powerLevel = 0`` and the UHX demand (zero for
``startup``). Like the lumped ``MSRRstartUpCriticality`` models the wrapper
starts the core isothermal at the scenario's ``init.per_core`` temperature
(the deck's zero-power critical reference), enables the core heat loss
(``forcing.heat_loss``), and measures each staircase amplitude from the
critical position at the pump flow in effect
(``forcing.reactivity_pcm.reference``). The bare trim rigs are 1 MW
steady-state harnesses and are never run as startup vehicles: the former
simplified schedule (TASK-20260823-04 Ambiguity A': 1 MW UHX demand kept on
a -3500 pcm subcritical core) froze the inlet salt and went
prompt-supercritical on the return to 0 pcm. Outputs default to
``00runs/segmented/startup-<scenario>-<core>/`` (review 2026-10-01 M6: the
``00runs/startup-*`` trees are the published pre-fix record and an explicit
``--run_dir`` inside them is refused); an explicit ``--run_dir`` nests them
under an extra ``segmented/`` component so they can never collide with
legacy startup runs.

Every startup result CSV carries provenance sidecars (helpers/run_results.py):
prior artifacts are quarantined rather than silently deleted before launch,
and the fresh CSV is validated -- freshness against this run's launch
timestamp included -- before both the manifest and validation sidecars are
written beside it.
"""

import argparse
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from shutil import copyfile

try:
    from .paths import (
        default_segmented_startup_run_dir,
        default_startup_run_dir,
        startup_result_prefix,
    )
except ImportError:
    from paths import (
        default_segmented_startup_run_dir,
        default_startup_run_dir,
        startup_result_prefix,
    )

try:
    from helpers.scenario_config import (
        CORE_CHOICES,
        add_plant_argument,
        plant_package_refusal,
        legacy_core_refusal,
        legacy_vehicle,
        merge_manifest_sources,
        needs_generated_wrapper,
        resolve_scenario,
        scenario_core_refusal,
        scenario_source_entry,
        startup_cli_table,
        startup_legacy_vehicles,
        startup_numerics,
        wrapper_source_entry,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.scenario_config import (
        CORE_CHOICES,
        add_plant_argument,
        plant_package_refusal,
        legacy_core_refusal,
        legacy_vehicle,
        merge_manifest_sources,
        needs_generated_wrapper,
        resolve_scenario,
        scenario_core_refusal,
        scenario_source_entry,
        startup_cli_table,
        startup_legacy_vehicles,
        startup_numerics,
        wrapper_source_entry,
    )


LOGGER = logging.getLogger(__name__)

CORE_MODEL_TO_SCENARIO_MODEL = startup_legacy_vehicles()
SCENARIOS = startup_cli_table()
#: --scenario choices: every plant's startup ids (an id missing from the
#: selected --plant deck tree is refused when the scenario is resolved).
SCENARIO_CHOICES = sorted(startup_cli_table(all_plants=True))

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
# Segmented startup route (review 2026-10-01 H2/H3). Every segmented startup
# scenario emits a structural scenario wrapper (helpers/emit_scenario_wrapper)
# on the trim rig: the YAML schedule (staircase, source windows, pump ramp,
# powerLevel = 0, UHX demand) plus the lumped startup semantics -- isothermal
# start at init.per_core, heat loss, and the flow-referenced staircase. The
# former runtime-override payload (Ambiguity A': powerLevel = 1, so the rig's
# 1 MW UHX demand stayed on during the -3500 pcm hold) is retired.
# ---------------------------------------------------------------------------
#: The base criticality approach (data/scenarios/startup/
#: approach_to_criticality.yaml). The to-power YAML extends it.
SEGMENTED_SCENARIO = "startup"


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
            "runs default to 00runs/segmented/startup-<scenario>-<core_model> "
            "and nest an explicit --run_dir under an additional segmented/ "
            "component, refusing one inside the published 00runs/startup-*, "
            "00runs/freq/ or 00runs/transients-* trees)"
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
        choices=CORE_CHOICES,
        default="1r",
        help=(
            "Core segmentation to use: 1r, 9r, 1r10seg "
            "(1-channel x 10-axial-segment 1R core; segmented package only -- "
            "the legacy package has no 10-segment vehicle), or r5x5_z10 "
            "(5x5-radial x 10-axial-segment 1R core; segmented package only -- "
            "the legacy package has no 5x5 vehicle)"
        ),
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
            "full-loop trim rigs (loads SegmentedMSR_PlantData.mo then "
            "SegmentedMSR.mo; buildModel + direct executable because omc "
            "1.27 simulate() scripting is broken). Every startup scenario "
            "(the base 'startup' approach to criticality and the to-power "
            "startup_to_100kw / startup_to_1mw) emits a structural scenario "
            "wrapper into the run dir: the YAML schedule at powerLevel = 0, "
            "an isothermal core at the scenario's init.per_core "
            "temperature, the core heat loss, and the reactivity staircase "
            "measured from the critical position at the pump flow in "
            "effect, as in the lumped startup models. Segmented result CSVs "
            "report temperatures in kelvin. 1r10seg and r5x5_z10 are "
            "segmented-package only: --package legacy has no 10-segment or "
            "5x5 vehicle and rejects --core_model 1r10seg / r5x5_z10 before "
            "any omc invocation. Homogeneous poison tracking is off by "
            "default; enable it only through a scenario poisons: block on "
            "--package segmented (not a spatial poison network). When the "
            "loaded plant's outer_fuel_annulus dataset is enabled, the "
            "segmented 1r10seg startup runs the plan 10.6 "
            "CoreVesselAssembly wrapper vehicle -- by default "
            "SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusCoupledSS (both core init modes "
            "SteadyState, zero perturbation), or the shipped-defaults "
            "SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusTrimThermalSS twin under "
            "--outer-annulus-init-policy bounded_startup "
            "(TASK-20260923-01 P1) -- (the ramp wrapper extends "
            "it) and the manifest/compact columns carry the outer-annulus "
            "identity (P8); the requested policy rides the "
            "fingerprint-active outerAnnulusInitPolicy manifest "
            "override and the effective vehicle rides the manifest "
            "model_name; the disabled shipped dataset keeps the "
            "historical vehicle, manifest, and columns."
        ),
    )
    add_plant_argument(parser)
    parser.add_argument(
        "--scenario",
        type=str,
        choices=SCENARIO_CHOICES,
        default="startup",
        help=(
            "Startup YAML id under data/scenarios/startup/, or under "
            "data/plants/<plant>/scenarios/startup/ with --plant "
            "(default: startup). Tables come from that file."
        ),
    )
    parser.add_argument(
        "--scenario_file",
        type=str,
        default=None,
        help="Optional scenario YAML overlay (id still used for the run-dir name)",
    )
    parser.add_argument(
        "--plant_file",
        type=str,
        default=None,
        help=(
            "Optional plant.yaml path recorded in the run manifest. "
            "Modelica still loads the checked-in PlantData.mo; regenerate "
            "that file to change plant numbers."
        ),
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
    parser.add_argument(
        "--allow-unlisted-core",
        "--allow_unlisted_core",
        dest="allow_unlisted_core",
        action="store_true",
        help=(
            "Deliberately run a (scenario, core_model) combination the "
            "scenario YAML does not list in applies_to. The override is "
            "recorded in the run manifest (allow_unlisted_core) instead "
            "of being silent."
        ),
    )
    parser.add_argument(
        "--allow-unreviewed-poison-data",
        "--allow_unreviewed_poison_data",
        dest="allow_unreviewed_poison_data",
        action="store_true",
        help=(
            "Development override: allow a poison-on run (tracking or "
            "feedback) whose authored poison dataset maturity is not "
            "approved for production or publication use (the committed "
            "dataset is reduced_order_pending_review). The override is "
            "recorded in the run manifest (allow_unreviewed_poison_data) "
            "instead of being silent. Poison-off runs do not need it."
        ),
    )
    parser.add_argument(
        "--full-result-output",
        "--full_result_output",
        dest="full_result_output",
        action="store_true",
        help=(
            "Diagnostic only (segmented package): write the FULL wide "
            "result CSV (every state/derivative/parameter column -- "
            "15,000+ columns on the 5x5 core) instead of the default "
            "result-variable contract projection (time + the physical "
            "outputs, acceptance diagnostics, and provenance taps the "
            "workflows consume). The selected set is recorded in the run "
            "manifest (result_variables / result_output_mode). Rejected "
            "with --package legacy, which keeps its historical wide "
            "output."
        ),
    )
    parser.add_argument(
        "--outer-annulus-init-policy",
        "--outer_annulus_init_policy",
        dest="outer_annulus_init_policy",
        type=str,
        default=None,
        choices=("coupled_steady_state", "bounded_startup"),
        help=(
            "Outer-annulus initialization policy (segmented package, "
            "enabled 1r10seg dataset only; TASK-20260923-01 P1): "
            "'coupled_steady_state' (default) executes the dedicated "
            "CoupledSS production vehicle (SegmentedMSR.Reactors."
            "R1MSRRuhx10SegOuterAnnulusCoupledSS: both core init modes "
            "SteadyState, zero perturbation); 'bounded_startup' executes "
            "the shipped-defaults TrimThermalSS twin (SegmentedMSR."
            "Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS: "
            "FixedStart core cells, 1 pcm sine). The requested policy "
            "is recorded in the run manifest as the fingerprint-active "
            "outerAnnulusInitPolicy override and the effective vehicle "
            "rides the manifest model_name."
        ),
    )
    args = parser.parse_args()
    if args.claim_timeout_s is not None and args.claim_timeout_s <= 0:
        parser.error("--claim_timeout_s must be a positive number of seconds")
    if args.omc_timeout_seconds < 0:
        parser.error(
            "--omc_timeout_seconds must be a non-negative number of seconds "
            "(0 disables the timeout)"
        )
    try:
        args._scenario_data = resolve_scenario(
            kind="startup",
            scenario_id=args.scenario,
            scenario_file=args.scenario_file,
            plant=args.plant,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def validate_args(args: argparse.Namespace) -> None:
    """Reject unsupported argument combinations before any simulation starts.

    One contract is enforced for EVERY package (TASK-20260911-01 P4, owner
    decision): ``--start_time`` must stay at its default 0.0 -- the closed-form
    elapsed-time pump terms in both packages are identical to the time-shift
    ``delay()`` form only at ``startTime = 0``. Otherwise legacy invocations
    pass trivially (zero behavior change). Segmented mode hard-errors every
    legacy-only knob (Ambiguity A' scenario-entry decision, TASK-20260823-04
    R3: hard-error over loud-skip, matching the landed freq/transients
    precedent) instead of silently mapping it to unlike segmented semantics.
    """
    # Closed-form-delay contract (TASK-20260911-01 P4, owner decision 1):
    # nonzero --start_time is refused for EVERY package, here in argument
    # validation -- before any omc invocation. The plant's pump
    # flow-fraction transients are written in closed form (PrimaryPump,
    # FlowDistributor, and Pump in the legacy SMD_MSR_Modelica library; the
    # Pump ramp/trip terms in the SegmentedMSR package), and that closed
    # form is identical to the time-shift delay() form only at
    # startTime = 0; any other start would silently shift every trip and
    # ramp reference.
    if float(getattr(args, "start_time", 0.0) or 0.0) != 0.0:
        raise ValueError(
            "--start_time is unsupported: startup accepts only the default "
            "startTime = 0, for both the legacy and the segmented package. "
            "The plant's pump flow-fraction transients are written in closed "
            "form -- PrimaryPump, FlowDistributor, and Pump in the legacy "
            "SMD_MSR_Modelica library and the Pump ramp/trip terms in the "
            "SegmentedMSR package -- and that closed form is identical to "
            "the time-shift delay() form only at startTime = 0; any other "
            "start silently shifts every trip and ramp reference away from "
            "the published runs. Rerun from t = 0 (omit --start_time)."
        )
    package = str(getattr(args, "package", DEFAULT_PACKAGE))
    refusal = plant_package_refusal(getattr(args, "plant", None), package)
    if refusal:
        raise ValueError(refusal)
    if getattr(args, "full_result_output", False) and package != SEGMENTED_PACKAGE:
        # The wide output IS the legacy default; the diagnostic flag exists
        # only to escape the segmented result-variable contract (P4).
        raise ValueError(
            "--full_result_output is only meaningful with --package "
            "segmented: legacy startup runs always write their historical "
            "(wide) result CSV."
        )
    if (
        getattr(args, "outer_annulus_init_policy", None) is not None
        and package != SEGMENTED_PACKAGE
    ):
        # The initialization-policy lever selects an outer-annulus
        # production vehicle; there is no legacy vehicle to select.
        raise ValueError(
            "--outer-annulus-init-policy is only meaningful with --package "
            "segmented: legacy startup runs keep their historical vehicles."
        )
    # Legacy number_of_intervals bound (review rev021 minor): the segmented
    # route rejects < 1 below (its own named guard, kept as defense-in-depth);
    # this package-agnostic copy mirrors it so a CLI-supplied 0 or negative
    # interval count can never reach the legacy
    # simulate(..., numberOfIntervals=0) either.
    if (
        getattr(args, "number_of_intervals", None) is not None
        and args.number_of_intervals < 1
    ):
        raise ValueError("--number_of_intervals must be >= 1.")
    # Runner-owned simflag conflicts (review rev021 M1): --extra_simflags is
    # appended AFTER the runner-owned flags on both execution routes (the
    # segmented executable command line, and the legacy .mos simflags string
    # that already carries the runner's -r=), so a duplicate would silently
    # shadow the atomic-publication result path, the fingerprinted override
    # payload, or the fingerprinted time-grid/output-format request -- the
    # manifest would then attest values that were never simulated.  Hard-error
    # with the same pattern as the segmented -variableFilter refusal below.
    # Matching is token-based (shlex, like the segmented executable route) so
    # unrelated flags such as -rtol still pass.
    extra_simflags = str(getattr(args, "extra_simflags", "") or "")
    if extra_simflags.strip():
        runner_owned_flags = (
            "-r",
            "-override",
            "-stepSize",
            "-stopTime",
            "-outputFormat",
        )
        try:
            simflag_tokens = shlex.split(extra_simflags)
        except ValueError:
            simflag_tokens = extra_simflags.split()
        for token in simflag_tokens:
            for flag in runner_owned_flags:
                if token == flag or token.startswith(f"{flag}="):
                    raise ValueError(
                        f"--extra_simflags must not carry {flag}: the runner "
                        "owns this flag on both execution routes and appends "
                        "its own value after the extras, so a duplicate would "
                        "silently shadow the fingerprinted request and the "
                        "manifest would attest values that were never "
                        "simulated."
                    )
    if package != SEGMENTED_PACKAGE:
        return
    if getattr(args, "run_dir", None) is not None:
        # Review 2026-10-01 M6: 00runs/startup-* is the published pre-fix
        # record; segmented runs never write into it.
        from helpers.published_tree_guard import refuse_published_tree_output

        refuse_published_tree_output(args.run_dir, flag="--run_dir")
    if (
        not getattr(args, "full_result_output", False)
        and "-variableFilter" in str(getattr(args, "extra_simflags", "") or "")
    ):
        # Contract mode owns output filtering (the runner appends its own
        # -variableFilter); a second, user-supplied filter would silently
        # fight it.  The diagnostic --full-result-output path is the
        # explicit escape hatch for hand-picked filtering.
        raise ValueError(
            "--extra_simflags must not carry -variableFilter: the "
            "segmented result-variable contract owns output filtering "
            "(use --full_result_output for the unfiltered wide output)."
        )
    scenario_data = getattr(args, "_scenario_data", None)
    if args.scenario != SEGMENTED_SCENARIO:
        allowed_wrapper = False
        if scenario_data is None:
            try:
                scenario_data = resolve_scenario(
                    kind="startup",
                    scenario_id=str(args.scenario),
                    plant=getattr(args, "plant", None),
                )
            except (OSError, TypeError, ValueError):
                scenario_data = None
        if scenario_data is not None:
            allowed_wrapper = bool(
                needs_generated_wrapper(scenario_data, SEGMENTED_PACKAGE)
            )
        if not allowed_wrapper:
            raise ValueError(
                f"--scenario {args.scenario} is unsupported with --package "
                "segmented: use 'startup' (the approach to criticality) or a "
                "to-power YAML with package_support.segmented.needs_wrapper "
                "(both emit a structural scenario wrapper)."
            )
    if args.model_name:
        raise ValueError(
            "--model_name cannot be combined with --package segmented; "
            "select the vehicle with --core_model "
            "{1r,9r,1r10seg,r5x5_z10}."
        )
    if args.library_file != "SMD_MSR_Modelica.mo":
        raise ValueError(
            "--library_file is a legacy-load knob and is unsupported with "
            "--package segmented: the standalone package loads "
            "SegmentedMSR_PlantData.mo then SegmentedMSR.mo."
        )
    if args.model_file != "MSRR.mo":
        raise ValueError(
            "--model_file is a legacy-load knob and is unsupported with "
            "--package segmented: the standalone package loads "
            "SegmentedMSR_PlantData.mo then SegmentedMSR.mo."
        )
    if str(args.method).strip().lower() != "dassl":
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
    # Retained segmented-mode bound (TASK-20260911-01 P4): the unconditional
    # closed-form-delay refusal at the top of this function now fires first
    # for every package; this narrower copy stays as defense-in-depth so the
    # segmented contract keeps its own named guard.
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


#: Startup reactivity-reference policies (physics review 2026-09-27; scenario
#: field ``forcing.reactivity_pcm.reference``). The legacy kinetics freeze the
#: circulating-fuel compensation at nominal flow, so the staircase meaning
#: must be explicit:
#: - ``flow_dependent_critical`` (default): each step is measured from the
#:   critical position at the pump flow in effect (the startup models add
#:   -(loss(1) - loss(FF_stage)); ``flowReferencedStaircase = true``);
#: - ``nominal_flow_critical``: steps measured from the nominal-flow critical
#:   position (absolute rod program; the low-flow phases turn supercritical);
#: - ``historical_live_compensation``: the pre-review convention, live
#:   circulating-fuel compensation (``liveFlowCompensation = true``) with the
#:   unreferenced staircase -- reproduces the former idealized behavior and
#:   must be labeled as such.
STARTUP_REACTIVITY_REFERENCES: dict[str, tuple[str, ...]] = {
    "flow_dependent_critical": ("flowReferencedStaircase=true",),
    "nominal_flow_critical": ("flowReferencedStaircase=false",),
    "historical_live_compensation": (
        "flowReferencedStaircase=false",
        "{core}.mpke.liveFlowCompensation=true",
    ),
}
DEFAULT_STARTUP_REACTIVITY_REFERENCE = "flow_dependent_critical"
_LEGACY_KINETICS_OWNER = {"1r": "core1R", "9r": "msre9r"}


def startup_reactivity_reference_overrides(scenario: dict | None, core_model: str) -> tuple[str, str]:
    """(policy, override payload) for a legacy startup scenario.

    Reads ``forcing.reactivity_pcm.reference`` (default
    ``flow_dependent_critical``) and returns the runner-owned override that
    selects it on the legacy startup models, so the policy is part of the
    fingerprinted request and the run manifest."""
    policy = DEFAULT_STARTUP_REACTIVITY_REFERENCE
    if scenario:
        rho = ((scenario.get("forcing") or {}).get("reactivity_pcm") or {})
        policy = str(rho.get("reference") or policy)
    if policy not in STARTUP_REACTIVITY_REFERENCES:
        raise ValueError(
            f"unknown startup reactivity reference {policy!r}; expected one of "
            f"{sorted(STARTUP_REACTIVITY_REFERENCES)}"
        )
    owner = _LEGACY_KINETICS_OWNER.get(core_model, "core1R")
    payload = ",".join(
        item.format(core=owner) for item in STARTUP_REACTIVITY_REFERENCES[policy]
    )
    return policy, payload


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
        helpers_dir / "plant_config.py",
        helpers_dir / "scenario_config.py",
    ]
    if str(package) == SEGMENTED_PACKAGE:
        files.append(helpers_dir / "segmented_runs.py")
        files.append(helpers_dir / "emit_scenario_wrapper.py")
    return [str(path) for path in files]


def _probe(call, default):
    """Best-effort environment probes; degrade to ``default`` on any hiccup."""
    try:
        return call()
    except Exception:  # noqa: BLE001 - provenance metadata must not abort runs
        LOGGER.debug("startup probe failed; degrading to %r", default, exc_info=True)
        return default


def _validation_stop_slack(stop_time: float) -> float:
    """Stop-time slack (seconds) allowed when validating finished output."""
    return STARTUP_VALIDATION_STOP_SLACK_RATIO * abs(float(stop_time))


def _extract_override_payload(simflags: str) -> str:
    """Best-effort extraction of the ``-override=a=1,b=2`` simflag payload.

    Legacy startup runs carry no overrides of their own, but users may pass
    one through ``--extra_simflags``; whatever rides the flag becomes part of
    the request fingerprint so unlike override sets cannot share results.
    The tail is tokenized shlex-aware (matching how the segmented executable
    route splits the same string) so a quoted value containing spaces is
    fingerprinted whole instead of being cut at the first blank; a string
    that is not shell-quoting-clean keeps the historical first-blank cut.
    """
    parts = simflags.split("-override=", 1)
    if len(parts) != 2:
        return ""
    tail = parts[1]
    try:
        tokens = shlex.split(tail)
    except ValueError:
        tokens = tail.split()
    return tokens[0].strip() if tokens else ""


def _parse_override_payload(payload: str) -> dict[str, str]:
    """Split an ``a=1,b=2`` override payload into a plain string-keyed dict."""
    return dict(part.split("=", 1) for part in payload.split(",") if "=" in part)


def _resolve_legacy_startup_model(
    args: argparse.Namespace,
    scenario_data,
    core_model: str,
    run_dir: str,
) -> tuple[str, str | None]:
    """Return ``(model_name, wrapper_path_or_none)``.

    Named YAML vehicles (the committed ``MSRR.MSRRstartUp*`` classes) stay
    on the default CLI path. A ``--scenario_file`` without a dedicated
    vehicle emits a structural wrapper into ``run_dir``.
    """

    if args.model_name:
        return str(args.model_name), None
    named = legacy_vehicle(scenario_data, core_model)
    if named:
        return named, None
    mapped = CORE_MODEL_TO_SCENARIO_MODEL.get(str(args.scenario), {})
    named = mapped.get(core_model)
    if named:
        return named, None
    from helpers.emit_scenario_wrapper import emit_scenario_wrapper

    path, model_name = emit_scenario_wrapper(
        scenario_data,
        run_dir,
        core_model=core_model,
        package=LEGACY_PACKAGE,
    )
    return model_name, str(path)


def _plant_id(args: argparse.Namespace) -> str:
    """The run's plant deck (``--plant``; msrr when absent)."""

    return str(getattr(args, "plant", None) or "msrr")


def _plant_kwargs(args: argparse.Namespace) -> dict[str, str]:
    """``{"plant": id}`` for a non-default plant, else empty."""

    plant = _plant_id(args)
    return {} if plant == "msrr" else {"plant": plant}


def _startup_scenario(args: argparse.Namespace):
    """Return the YAML deck attached at parse time, loading it if missing."""

    data = getattr(args, "_scenario_data", None)
    if data is not None:
        return data
    return resolve_scenario(
        kind="startup",
        scenario_id=getattr(args, "scenario", "startup"),
        scenario_file=getattr(args, "scenario_file", None),
        plant=getattr(args, "plant", None),
    )


def _legacy_source_files(
    core_dir: str,
    library_path: str,
    model_path: str,
    plant_data_path: str,
    scenario=None,
    wrapper_path: str | None = None,
    plant_file: str | None = None,
):
    """PlantData.mo + SMD + MSRR, plus plant-YAML and scenario-YAML fingerprints."""

    try:
        import yaml

        from helpers.plant_config import fingerprint, lumped_manifest_source_files

        files = lumped_manifest_source_files(core_dir)
        extra = {}
        if scenario is not None:
            extra.update(scenario_source_entry(scenario))
        if wrapper_path:
            extra.update(wrapper_source_entry(wrapper_path))
        if plant_file:
            plant_path = Path(plant_file)
            extra[str(plant_path)] = fingerprint(
                yaml.safe_load(plant_path.read_text(encoding="utf-8"))
            )
        return merge_manifest_sources(files, extra) if extra else files
    except Exception:  # noqa: BLE001 - hermetic tests may lack a full plant deck
        LOGGER.debug(
            "legacy manifest source enumeration degraded to the base trio",
            exc_info=True,
        )
        return [plant_data_path, library_path, model_path]


def _legacy_maturity_fields(core_model: str) -> dict:
    from helpers.plant_config import core_maturity_labels

    return core_maturity_labels(core_model)


def _source_normalization() -> dict:
    from helpers.plant_config import source_normalization_record

    return source_normalization_record()


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
    core_maturity: str | None = None,
    core_physical_data_maturity: str | None = None,
    source_normalization: "dict | None" = None,
    result_variables: "list[str] | tuple[str, ...] | None" = None,
    result_output_mode: str | None = None,
    radial_config: "dict | None" = None,
    outer_annulus_config: "dict | None" = None,
    radial_annular_flow_command: float | None = None,
    outer_annulus_init_policy: "str | None" = None,
) -> dict:
    """Assemble the canonical provenance manifest for one startup result CSV.

    Package/model identity, hashed Modelica sources, numerics, and the
    normalized override dictionary (runner-specific details live there --
    scenario selection, step-size cap, and every parsed runtime override)
    enter the request fingerprint; the wall-clock launch stamp is recorded
    but excluded from it. ``core_maturity`` (segmented mode only) records
    the core's machine-readable maturity label (and
    ``core_physical_data_maturity`` its physical-data maturity); ``None`` omits the field
    so legacy manifests keep their historical shape. ``result_variables`` /
    ``result_output_mode`` (segmented mode only, TASK-20260908-01 P4) record
    the selected result-variable contract; ``None`` omits both fields.
    ``radial_config`` / ``radial_annular_flow_command`` (segmented mode
    only, review rev020 Phase 2; TASK-20260916-01 P5) record the
    fingerprint-active ``intra_channel_radial`` record and the effective
    top-level ``annularFlowCommand`` (the P1 constant-only parameter:
    helper-derived default 1, or the runner's parsed runtime override) --
    the command entry lands in ``overrides`` only on circulating radial
    runs, and ``radial_config=None`` omits the field entirely so disabled
    manifests keep their historical shape and fingerprint.
    ``outer_annulus_config`` (segmented mode only, TASK-20260917-01 P8;
    plan §11.2) records the fingerprint-active ``outer_fuel_annulus``
    dataset identity (dataset id, fingerprint, maturity, topology,
    direction, cavity, inventory policy); ``None`` omits the field
    entirely so disabled manifests keep their historical shape and
    fingerprint (the radial omission pattern).
    ``outer_annulus_init_policy`` (segmented mode only, enabled
    outer-annulus runs, TASK-20260923-01 P1) records the requested
    initialization policy (``coupled_steady_state`` / ``bounded_startup``)
    as the ``outerAnnulusInitPolicy`` manifest override -- the
    runner-request surface recording which production vehicle executed
    (the effective vehicle rides the manifest ``model_name``); ``None``
    omits the entry so disabled runs keep their historical shape, and
    the policy entry is fingerprint-active so results are never reused
    across initialization policies.
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
    if bool(getattr(args, "allow_unlisted_core", False)):
        # Recorded only when set, so default-path manifests keep their
        # historical shape and fingerprint (absence == no override).
        overrides["allow_unlisted_core"] = True
    poison_fields = getattr(args, "_poison_fingerprint_fields", None)
    if poison_fields:
        overrides.update(poison_fields)
    if radial_annular_flow_command is not None:
        # Effective top-level annularFlowCommand of the circulating run
        # (derived once by helpers.segmented_runs.radial_run_contract from
        # the parsed runtime-override payload or the P1 default; a payload
        # that names the parameter already placed its string value here, so
        # this overwrites it with the parsed number).
        overrides["annularFlowCommand"] = float(radial_annular_flow_command)
    if outer_annulus_init_policy is not None:
        # Requested outer-annulus initialization policy (derived once by
        # helpers.segmented_runs.outer_fuel_annulus_run_contract; the
        # policy selects the CoupledSS vs bounded-startup production
        # vehicle, whose name rides the manifest model_name).
        # Fingerprint-active (recorded only on enabled runs).
        overrides["outerAnnulusInitPolicy"] = str(outer_annulus_init_policy)
    manifest = rr.build_run_manifest(
        package_name="SegmentedMSR" if segmented else "MSRR",
        model_name=str(model_name),
        source_files=source_files,
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
        core_maturity=core_maturity,
        core_physical_data_maturity=core_physical_data_maturity,
        radial_config=radial_config,
        outer_annulus_config=outer_annulus_config,
        result_variables=result_variables,
        result_output_mode=result_output_mode,
        workflow_version=_provenance_workflow_version(),
    )
    if source_normalization is not None:
        # External-source normalization constants the run applies (rev032
        # review): fingerprint-active, and what plotStartUp.py interprets
        # the result with, instead of the current checkout's deck.
        manifest["source_normalization"] = dict(source_normalization)
    return manifest


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
    except (OSError, ValueError, UnicodeDecodeError):
        # Unreadable/unparseable tails still count as "did not reach stop
        # time"; anything else (a programming error) must propagate so it
        # cannot be misreported as a short trajectory (review rev021 M3).
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
    plant_data_path: str | None = None,
    extra_model_files: list[str] | None = None,
) -> str:
    from helpers.plant_config import lumped_load_file_text

    load_files = [library_path, model_path]
    if plant_data_path:
        load_files = [plant_data_path, library_path, model_path]
    if extra_model_files:
        load_files = list(load_files) + [str(path) for path in extra_model_files]
    return (
        "// Generated by startup/runMSRR.py\n"
        + lumped_load_file_text(load_files)
        + f"simulate({resolved_model_name},"
        f"startTime={start_time:.10g},"
        f"stopTime={resolved_stop_time:.10g},"
        f"numberOfIntervals={resolved_number_of_intervals},"
        f"tolerance={tolerance:.10g},"
        f"method={method},"
        f'outputFormat="{output_format}",'
        f'fileNamePrefix="{resolved_file_prefix}",'
        f'simflags="{simflags}");\n'
    )


def _resolve_segmented_startup_model(
    args: argparse.Namespace,
    scenario_data,
    core_model: str,
    run_dir: str,
    seg,
    *,
    outer_annulus: bool = False,
    outer_annulus_init_policy: "str | None" = None,
) -> tuple[str, str | None]:
    """Return ``(model_name, wrapper_path_or_none)`` for segmented startup.

    ``outer_annulus=True`` (P8) selects the plan §10.6 wrapper vehicle from
    :data:`helpers.segmented_runs.OUTER_ANNULUS_MODEL_BY_CORE` under the
    TASK-20260923-01 P1 initialization policy (``outer_annulus_init_policy``:
    ``coupled_steady_state`` default selects the dedicated CoupledSS
    vehicle with SteadyState core modes + zero perturbation fixed in the
    class; ``bounded_startup`` selects the shipped-defaults TrimThermalSS
    twin) and emits the structural UHX-ramp wrapper on the SAME base (the
    wrapper extends the outer-annulus vehicle, so the executed
    configuration and the manifest identity stay the same model -
    plan §11.1).

    Review 2026-10-01 H2: every segmented startup scenario -- the base
    ``startup`` included -- runs through the generated wrapper; the bare
    trim rig is a 1 MW steady-state harness, not a startup vehicle. The
    wrapper base is the same vehicle (:data:`helpers.emit_scenario_wrapper.
    _SEGMENTED_TRIM_BASE` mirrors ``MODEL_BY_CORE``).
    """

    from helpers.emit_scenario_wrapper import emit_scenario_wrapper

    path, model_name = emit_scenario_wrapper(
        scenario_data,
        run_dir,
        core_model=core_model,
        package=SEGMENTED_PACKAGE,
        outer_annulus=outer_annulus,
        outer_annulus_init_policy=outer_annulus_init_policy,
    )
    return model_name, str(path)


def _main_segmented(args: argparse.Namespace) -> int:
    """Segmented-mode driver (standalone SegmentedMSR package).

    Loads ``SegmentedMSR_PlantData.mo`` then ``SegmentedMSR.mo`` per run dir
    and follows the landed freq/transients execution route:
    ``buildModel(..., tolerance=...)`` via omc scripting (tolerance baked at
    BUILD time; ``-rtol/-atol`` are NOT runtime flags), then the generated
    executable directly with ``-stepSize`` translating the requested interval
    count (omc 1.27 rejects ``-numberOfIntervals=`` at runtime). Output
    convention (Ambiguity J, TASK-20260823-04; review 2026-10-01 M6): the
    default run directory is
    ``00runs/segmented/startup-<scenario>-<core>/``; an explicit
    ``--run_dir`` nests results under an extra ``segmented/`` component so
    they can never collide with legacy startup outputs. File names inside
    keep the legacy prefixes.
    """
    from helpers.plant_config import (
        copy_segmented_sources,
        segmented_manifest_source_files,
    )

    seg = _segmented_helpers()
    repo_root = Path(__file__).resolve().parents[1]
    core_dir = os.path.abspath(args.core_dir)
    scenario_data = _startup_scenario(args)
    # The wrapper extends the mapped trim rig: refuse a nominal-trim mapping
    # before anything is created (the legacy nominaltrim guard's twin).
    resolved_early = seg.model_for(args.core_model)
    if "nominaltrim" in resolved_early.lower():
        print(
            "ERROR: startup/runMSRR.py expects startup models. "
            f"Got nominal-trim model: {resolved_early}"
        )
        return 2

    if args.run_dir is None:
        # Review 2026-10-01 M6: the default lives under 00runs/segmented/,
        # outside the published 00runs/startup-* record, and needs no
        # further segmented/ component.
        run_dir = str(
            default_segmented_startup_run_dir(
                repo_root,
                scenario=args.scenario,
                core_model=args.core_model,
                # Only a non-default plant passes the keyword, so default
                # calls keep the historical signature.
                **_plant_kwargs(args),
            )
        )
    else:
        run_dir = os.path.join(
            os.path.abspath(args.run_dir), STARTUP_SEGMENTED_DIR_COMPONENT
        )

    scenario_defaults = startup_numerics(scenario_data)
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

    try:
        copied = copy_segmented_sources(core_dir, run_dir, plant_id=_plant_id(args))
    except FileNotFoundError as exc:
        print(f"ERROR: missing segmented library files: {exc}")
        return 2

    # Outer-annulus provenance + runner/core refusal (TASK-20260917-01 P8;
    # plan §11.2; rev024 §6 Phase 1 / TASK-20260923-01 P1): derived BEFORE
    # the model resolution so the wrapper (and the direct vehicle)
    # selection uses the plan §10.6 CoreVesselAssembly vehicle when the
    # loaded plant's outer_fuel_annulus dataset is enabled. The
    # initialization policy selection (coupled_steady_state default, or
    # the explicit bounded_startup twin) rides the contract: it selects
    # the CoupledSS vehicle vs the shipped-defaults TrimThermalSS twin
    # and is recorded in the manifest record (policy + effective vehicle,
    # fingerprint-active). Fail-closed refusals (9R, an enabled dataset
    # on a vehicle that cannot execute it, an enabled dataset targeting
    # a different core than the runner selected, an unknown policy name)
    # happen inside the contract, BEFORE any manifest can be published.
    # The disabled/absent dataset returns the all-None contract:
    # historical manifest shape, column set, and vehicle mapping.
    from helpers.plant_config import load_plant as _load_outer_plant

    outer_plant = _load_outer_plant(_plant_id(args))
    outer = seg.outer_fuel_annulus_run_contract(
        str(args.core_model),
        plant=outer_plant,
        init_policy=getattr(args, "outer_annulus_init_policy", None),
    )

    resolved_model_name, wrapper_path = _resolve_segmented_startup_model(
        args,
        scenario_data,
        args.core_model,
        run_dir,
        seg,
        outer_annulus=outer.enabled,
        outer_annulus_init_policy=(
            outer.init_policy if outer.enabled else None
        ),
    )

    mos_path = os.path.join(run_dir, args.mos_name)
    result_csv = os.path.join(run_dir, f"{resolved_file_prefix}_res.csv")
    from helpers.scenario_config import join_override_payload

    poison_payload = str(getattr(args, "_poison_payload", "") or "")
    poison_tracking = bool(getattr(args, "_poison_tracking", False))
    overrides = join_override_payload("", poison_payload)

    # Radial provenance + runner/core refusal (review rev020 Phase 2;
    # TASK-20260916-01 P5): one helper derives, from the loaded plant and
    # the selected core, the radial manifest record and the result-contract
    # run shape -- and refuses, fail-closed (named configuration path and
    # offending value), a runner/core combination that cannot execute the
    # requested radial mode, BEFORE any manifest can be published. The
    # effective top-level annularFlowCommand (P1 constant-only interface;
    # the fixed startup payload carries no override of it) rides the
    # manifest overrides on circulating runs. Disabled cores return the
    # all-None contract, so disabled manifests and column sets keep their
    # historical shape.
    radial = seg.radial_run_contract(
        str(args.core_model),
        plant=outer_plant,
        annular_flow_command=_parse_override_payload(overrides).get(
            "annularFlowCommand"
        ),
    )

    extra_sources = dict(scenario_source_entry(scenario_data))
    if wrapper_path:
        extra_sources.update(wrapper_source_entry(wrapper_path))
    try:
        source_files = merge_manifest_sources(
            segmented_manifest_source_files(core_dir, plant_id=_plant_id(args)),
            extra_sources,
        )
    except Exception:  # noqa: BLE001 - hermetic tests may lack a full plant deck
        LOGGER.debug(
            "segmented manifest source enumeration degraded to the copied trio",
            exc_info=True,
        )
        source_files = merge_manifest_sources(
            [str(path) for path in copied],
            extra_sources,
        )

    # Result-output policy (TASK-20260908-01 P4): segmented runs default to
    # the result-variable contract; --full-result-output restores the wide
    # output explicitly for diagnostics.  The selected set is recorded in
    # the manifest either way.
    result_variables: list[str] | None = None
    result_output_mode: str | None = None
    if getattr(args, "full_result_output", False):
        result_output_mode = seg.RESULT_OUTPUT_MODE_FULL
    else:
        result_variables = list(
            seg.result_variables_for(
                "startup",
                str(args.core_model),
                poison_tracking=poison_tracking,
                radial_shape=radial.shape,
                outer_annulus_shape=outer.shape,
            )
        )
        result_output_mode = seg.RESULT_OUTPUT_MODE_CONTRACT

    startup_manifest = build_startup_manifest(
        args,
        package=SEGMENTED_PACKAGE,
        model_name=resolved_model_name,
        source_files=source_files,
        overrides_payload=overrides,
        start_time=0.0,
        stop_time=float(resolved_stop_time),
        number_of_intervals=int(resolved_number_of_intervals),
        core_maturity=seg.core_maturity_for(args.core_model),
        core_physical_data_maturity=seg.core_physical_data_maturity_for(args.core_model),
        source_normalization=_source_normalization(),
        radial_config=radial.manifest_fields,
        outer_annulus_config=outer.manifest_fields,
        radial_annular_flow_command=radial.annular_flow_command,
        outer_annulus_init_policy=(
            outer.init_policy if outer.enabled else None
        ),
        result_variables=result_variables,
        result_output_mode=result_output_mode,
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
            result_variables=result_variables,
            wrapper_path=wrapper_path,
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
    result_variables: "list[str] | tuple[str, ...] | None" = None,
    wrapper_path: str | None = None,
) -> int:
    """Simulate one segmented startup run and publish its result atomically.

    The caller holds the slot's exclusive claim for the whole span, so the
    quarantine-before-launch and the atomic publication can never interleave
    with a concurrent same-slot launch.  The simulation is directed at
    ``result_tmp_csv`` (``{result_csv}.tmp``); only output that passes result
    validation is published onto ``result_csv`` before both sidecars are
    written beside it, so a failed or interrupted run never leaves a
    reusable final result.

    Result-output policy (TASK-20260908-01 P4): when ``result_variables`` is
    given (contract mode), the generated executable is probed for
    ``-variableFilter`` support -- if present the runtime writes the compact
    output directly (result-writer-level only; the trajectory's time grid is
    unchanged).  The written header is then verified to be EXACTLY
    contract-shaped (missing + extra column checks): a runtime filter that
    silently failed (POSIX-ERE pattern pitfall -> full wide output) or that
    emitted alias-companion extras is repaired by the post-run projection,
    and a runtime without filter support goes straight to that projection.
    ``None`` (the diagnostic ``--full-result-output`` path) keeps the
    historical wide output.  The realized mechanism is surfaced in the run
    logs; the selected set itself is in the run manifest.
    """
    _prepare_fresh_result(result_csv, manifest=startup_manifest)

    extra_model_files = ()
    if wrapper_path:
        extra_model_files = (Path(wrapper_path).name,)
    mos_text = seg.build_smoke_mos(
        resolved_model_name,
        tolerance=float(args.tolerance),
        extra_model_files=extra_model_files,
        poison_tracking=bool(getattr(args, "_poison_tracking", False)),
        poison_feedback=bool(getattr(args, "_poison_feedback", False)),
    )
    # Review 2026-10-01 M1: the poison switches are build-bound (Evaluate =
    # true; bound as buildModel modifiers above). The manifest keeps the
    # requested payload; the executable receives only the keys it can apply,
    # and a requested value that differs from the compiled one is refused.
    try:
        runtime_overrides = seg.runtime_override_payload(
            overrides, build_script=mos_text
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    with open(mos_path, "w") as handle:
        handle.write(mos_text)

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
    if runtime_overrides != overrides:
        print(f"  Runtime overrides:  {runtime_overrides or '(none; build-bound)'}")
    if result_variables is not None:
        print(
            "  Result output:      contract "
            f"({len(result_variables)} variables; full via --full-result-output)"
        )
    else:
        print("  Result output:      full (wide, diagnostic --full-result-output)")
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

    # Result-output policy (P4): probe the executable's runtime for
    # -variableFilter support.  Supported -> the runtime writes the compact
    # contract output directly; unsupported -> the wide temporary output is
    # projected post-run.  Both mechanisms publish identical columns.
    contract_mode = result_variables is not None
    runtime_filter_applied = False
    if contract_mode:
        assert result_variables is not None  # narrows the type for mypy
        if seg.executable_supports_variable_filter(exe_path):
            runtime_filter_applied = True
            print(
                f"  Output mechanism:   {seg.MECHANISM_RUNTIME_VARIABLE_FILTER}"
            )
        else:
            print(f"  Output mechanism:   {seg.MECHANISM_POST_RUN_PROJECTION}")

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
    ]
    if runtime_filter_applied:
        assert result_variables is not None
        exe_cmd.append(
            f"-variableFilter={seg.variable_filter_regex(result_variables)}"
        )
    if runtime_overrides:
        exe_cmd.append(f"-override={runtime_overrides}")
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

    # Review 2026-10-01 M1 (the lumped route's physics-review B1 / rev033
    # guards): refuse a run whose runtime overrides the executable dropped
    # ("not found" / not overridable) or whose log records a fatal assertion
    # violation -- the pinned build exits 0 after both.
    from helpers.omc_log import check_overrides_applied

    try:
        check_overrides_applied(
            f"{_captured_text(exe_result.stdout)}\n{_captured_text(exe_result.stderr)}",
            label=resolved_model_name,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        print(f"  Stdout log: {exe_stdout_log}")
        print(f"  Stderr log: {exe_stderr_log}")
        return 4

    # The raw output is expected at the temporary companion; an engine that
    # ignored -r= leaves it at the final name (validated in place instead).
    raw_csv = result_tmp_csv if os.path.exists(result_tmp_csv) else result_csv
    if not os.path.exists(raw_csv):
        print("ERROR: simulation executable exited 0 but result CSV is missing.")
        print(f"  Expected: {result_tmp_csv}")
        print(f"  Stdout log: {exe_stdout_log}")
        return 3

    # Result-output policy (P4): the published file must be EXACTLY
    # contract-shaped.  Without runtime filter support the wide temporary
    # output is projected onto the contract columns in place; a runtime
    # filter that did not produce exactly the contract shape (silent
    # POSIX-ERE failure -> full wide output, or alias-companion extras) is
    # repaired the same way.  The raw wide bytes are replaced --
    # regenerable via --full-result-output -- and contract compliance is
    # proven before anything is published.
    if contract_mode:
        assert result_variables is not None
        missing = seg.missing_contract_columns(raw_csv, result_variables)
        extras = seg.extra_contract_columns(raw_csv, result_variables)
        if extras and not missing:
            if runtime_filter_applied:
                print(
                    "  Runtime filter output not contract-shaped "
                    f"({len(extras)} extra columns); downgrading to "
                    "post-run projection."
                )
            report = seg.project_contract_csv(raw_csv, result_variables)
            print(f"  {report.describe()}")
            missing = seg.missing_contract_columns(raw_csv, result_variables)
            extras = seg.extra_contract_columns(raw_csv, result_variables)
        if missing or extras:
            print(
                "ERROR: contract-mode result CSV is not contract-shaped: "
                f"missing={missing}; extras={extras[:8]}"
                + (f" (+{len(extras) - 8} more)" if len(extras) > 8 else "")
            )
            print(f"  Result CSV: {raw_csv}")
            print(f"  Stderr log: {exe_stderr_log}")
            return 4

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


def _legacy_core_refusal(args: argparse.Namespace) -> str | None:
    """Name a core that has no legacy vehicle, before any omc invocation.

    TASK-20260906-01 P3: ``1r10seg`` exists only in the standalone
    SegmentedMSR package; the legacy SMD_MSR_Modelica.mo/MSRR.mo library
    ships no 10-segment startup vehicle. A legacy-mode request for such a
    core hard-errors at the top of :func:`main` (clean exit 2, named
    message) instead of failing later on a raw ``KeyError`` in the
    scenario-vehicle / wrapper-emit path. TASK-20260906-02 P3 adds the same
    refusal for ``r5x5_z10`` (the legacy package ships no 5x5 startup
    vehicle). Returns the refusal message, or ``None`` when the core has
    legacy support (or legacy support cannot be determined, e.g. a hermetic
    environment without the scenario tree).

    TASK-20260908-01 P6 item 4: the membership decision and the message
    text are delegated to the shared helpers in ``helpers/scenario_config``
    (same strings; the clause table and message shape live there once).
    """
    legacy_cores = {
        core
        for vehicles in CORE_MODEL_TO_SCENARIO_MODEL.values()
        for core in vehicles
    }
    return legacy_core_refusal(
        str(args.core_model),
        legacy_cores,
        flag="--core_model",
        vehicle_noun="vehicle",
        legacy_group_noun="legacy startup vehicles",
    )


def main() -> int:
    args = parse_args()
    validate_args(args)
    try:
        from helpers.scenario_config import poison_run_bindings

        payload, fields, tracking = poison_run_bindings(
            str(getattr(args, "package", DEFAULT_PACKAGE)),
            _startup_scenario(args),
            allow_unreviewed_poison_data=bool(
                getattr(args, "allow_unreviewed_poison_data", False)
            ),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    args._poison_payload = payload
    args._poison_fingerprint_fields = fields
    args._poison_tracking = tracking
    args._poison_feedback = "enablePoisonFeedback=true" in payload
    if str(getattr(args, "package", DEFAULT_PACKAGE)) == LEGACY_PACKAGE:
        refusal = _legacy_core_refusal(args)
        if refusal is not None:
            print(f"ERROR: {refusal}")
            return 2
    # Scenario applies_to enforcement (TASK-20260908-01 P2 item 4): the
    # selected core must be listed in the scenario deck (after the
    # CLI-to-YAML key conversion) BEFORE wrapper generation, run-dir
    # creation, or any external process.
    scenario_refusal = scenario_core_refusal(
        _startup_scenario(args),
        str(args.core_model),
        allow_unlisted=bool(getattr(args, "allow_unlisted_core", False)),
    )
    if scenario_refusal is not None:
        print(f"ERROR: {scenario_refusal}")
        return 2
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
    from helpers.plant_config import lumped_plant_data_path

    plant_data_path = str(lumped_plant_data_path(core_dir))

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
    if not os.path.exists(plant_data_path):
        print(f"ERROR: missing plant-data file: {plant_data_path}")
        return 2

    scenario_data = _startup_scenario(args)
    scenario_defaults = startup_numerics(scenario_data)
    resolved_model_name, wrapper_path = _resolve_legacy_startup_model(
        args, scenario_data, args.core_model, run_dir
    )
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
    # Scenario-deck defense (review rev021 minor): the validate_args bound
    # sees only the CLI value; a deck that resolves to 0 (or a programmatic
    # caller) is refused here, before any manifest or subprocess work.
    if int(resolved_number_of_intervals) < 1:
        print(
            "ERROR: --number_of_intervals must be >= 1 "
            f"(resolved value: {resolved_number_of_intervals})."
        )
        return 2

    mos_path = os.path.join(run_dir, args.mos_name)
    result_csv = os.path.join(run_dir, f"{resolved_file_prefix}_res.csv")
    simflags = build_simflags(args.max_step_size, args.extra_simflags)
    # Startup reactivity-reference policy (physics review 2026-09-27): a
    # runner-owned override so the choice rides the request fingerprint and
    # the manifest overrides (only the legacy startup models carry the
    # switch; a user-selected --model_name outside them keeps its own).
    if resolved_model_name.startswith("MSRR.MSRRstartUp"):
        reactivity_reference, reference_payload = startup_reactivity_reference_overrides(
            scenario_data, str(args.core_model)
        )
        simflags = f"{simflags} -override={reference_payload}"
        print(f"  Reactivity reference: {reactivity_reference} ({reference_payload})")

    # Quarantine any prior result artifacts (formerly a silent delete) and
    # stamp this run's provenance manifest before anything is launched.
    startup_manifest = build_startup_manifest(
        args,
        package=LEGACY_PACKAGE,
        model_name=resolved_model_name,
        source_files=_legacy_source_files(
            core_dir,
            library_path,
            model_path,
            plant_data_path,
            scenario=scenario_data,
            wrapper_path=wrapper_path,
            plant_file=getattr(args, "plant_file", None),
        ),
        overrides_payload=_extract_override_payload(simflags),
        start_time=float(args.start_time),
        stop_time=float(resolved_stop_time),
        number_of_intervals=int(resolved_number_of_intervals),
        # Both maturity axes on legacy runs too (rev032 review), read from
        # the core deck without importing the segmented helpers.
        **_legacy_maturity_fields(str(args.core_model)),
        source_normalization=_source_normalization(),
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
            plant_data_path=plant_data_path,
            mos_path=mos_path,
            result_csv=result_csv,
            result_tmp_csv=str(_run_results().make_tmp_path(result_csv)),
            simflags=simflags,
            startup_manifest=startup_manifest,
            resolved_model_name=resolved_model_name,
            resolved_file_prefix=resolved_file_prefix,
            resolved_stop_time=resolved_stop_time,
            resolved_number_of_intervals=resolved_number_of_intervals,
            extra_model_files=[wrapper_path] if wrapper_path else None,
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
    plant_data_path: str,
    mos_path: str,
    result_csv: str,
    result_tmp_csv: str,
    simflags: str,
    startup_manifest: dict,
    resolved_model_name: str,
    resolved_file_prefix: str,
    resolved_stop_time: float,
    resolved_number_of_intervals: int,
    extra_model_files: list[str] | None = None,
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
                plant_data_path=plant_data_path,
                resolved_model_name=resolved_model_name,
                start_time=args.start_time,
                resolved_stop_time=resolved_stop_time,
                resolved_number_of_intervals=resolved_number_of_intervals,
                tolerance=args.tolerance,
                method=args.method,
                output_format=args.output_format,
                resolved_file_prefix=resolved_file_prefix,
                simflags=simflags,
                extra_model_files=extra_model_files,
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
    # omc resolves its script argument relative to cwd=run_dir (review rev021
    # M2): pass the bare name when the script sits in the run directory and
    # the absolute path otherwise, so an absolute (or nested) --mos_name can
    # neither lose the run directory nor strand the script.
    if os.path.dirname(os.path.abspath(mos_path)) == os.path.abspath(run_dir):
        omc_script_arg = os.path.basename(mos_path)
    else:
        omc_script_arg = mos_path
    try:
        result = subprocess.run(
            [args.omc, "--showErrorMessages", omc_script_arg],
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

    # Physics review 2026-09-27 (B1): refuse a run whose overrides (the
    # runner-owned reactivity-reference switch included) OMC dropped.
    from helpers.omc_log import check_overrides_applied

    try:
        check_overrides_applied(combined_output, label=os.path.basename(mos_path))
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        print(f"  Stdout log: {stdout_log}")
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

    # The generated .mos is removed only after the run is accepted (review
    # rev021 minor): on every failure path above the exact failing script
    # stays beside the logs as part of the diagnostics.
    if not args.keep_mos:
        try:
            os.remove(mos_path)
        except OSError:
            pass

    print("Simulation completed successfully.")
    print(f"  Result CSV:  {result_csv}")
    print(f"  Stdout log:  {stdout_log}")
    print(f"  Stderr log:  {stderr_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
