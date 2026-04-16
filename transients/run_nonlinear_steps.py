#!/usr/bin/env python3
"""Run nonlinear step transients for Results II plots (1R and 9R)."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
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
            "(default: 00runs/transients-<core_models>)"
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
        "--setpoints_csv",
        type=Path,
        default=None,
        help="Deprecated alias for --setpoints_csv_1r.",
    )
    parser.add_argument(
        "--setpoints_csv_1r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_1r.csv",
        help="Setpoints CSV for 1R 1 MW initialization",
    )
    parser.add_argument(
        "--setpoints_csv_9r",
        type=Path,
        default=repo_root / "core" / "init" / "setpoints_9r.csv",
        help="Setpoints CSV for 9R 1 MW initialization",
    )
    parser.add_argument(
        "--no_sync_loop_setpoints",
        action="store_true",
        help=(
            "Do not harmonize 9R shared loop initialization fields to the 1R "
            "nominal setpoint row."
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
    parser.add_argument("--tolerance", type=float, default=1e-6, help="Solver tolerance")
    parser.add_argument("--method", type=str, default="dassl", help="Solver method")
    parser.add_argument(
        "--max_step_size",
        type=float,
        default=0.02,
        help="Maximum solver step size",
    )
    args = parser.parse_args()
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
) -> None:
    mos_path.write_text(mos_text)
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        result = subprocess.run([omc, str(mos_path)], stdout=stdout, stderr=stderr, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"OMC failed for {mos_path.name} (exit {result.returncode})")
    if not expected_csv.exists():
        stdout_tail = stdout_path.read_text(errors="ignore")[-1000:]
        raise RuntimeError(
            f"OMC did not produce expected CSV: {expected_csv.name}\n"
            f"{stdout_tail}"
        )


def main() -> int:
    args = parse_args()

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

    simflags_base = f"-maxStepSize={args.max_step_size:.10g}"
    core_models = list(dict.fromkeys(args.core_models))
    shared_loop_setpoints: dict[str, float] | None = None
    if not args.no_sync_loop_setpoints:
        shared_loop_setpoints = load_setpoints(setpoint_paths["1r"], power_level=1.0)

    for core_model in core_models:
        config = CORE_CONFIG[core_model]
        core_out_dir = out_dir / core_model
        core_out_dir.mkdir(parents=True, exist_ok=True)

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
                simflags = f"{simflags_base} -override={overrides}"
            else:
                simflags = (
                    f"{simflags_base} "
                    "-override=externalReact.stepTime[2]=2000"
                )
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
                simflags=f"{simflags_base} -override={overrides}",
            )
            run_case(
                args.omc,
                mos_text,
                mos_path,
                stdout_path,
                stderr_path,
                core_out_dir,
                core_out_dir / f"{case}_res.csv",
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
            simflags=f"{simflags_base} -override={uhx_overrides}",
        )
        run_case(
            args.omc,
            mos_text,
            mos_path,
            stdout_path,
            stderr_path,
            core_out_dir,
            core_out_dir / f"{uhx_prefix}_res.csv",
        )

        print(f"[{config['label']}] Completed. Outputs: {core_out_dir}")

    print(f"All requested runs completed. Outputs in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
