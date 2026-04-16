#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a power-indexed steady-state initialization table.

For each requested power level, this script simulates nominal-trim operation with
zero sinusoidal perturbation and averages late-time temperatures used as
feedback setpoint overrides in downstream analyses.
"""

import argparse
import csv
import os
import subprocess
from shutil import copyfile
from concurrent.futures import ThreadPoolExecutor, as_completed


MODEL_NAME_BY_CORE = {
    "1r": "MSRR.MSRRuhxNominalTrimNoTrips",
    "9r": "MSRR.MSRRuhxNominalTrim9RNoTrips",
}

# Keep setpoint generation behavior explicit and predictable.
# If a special model/stop-time is needed for a particular power, pass it via CLI.
STOP_TIME_OVERRIDES: dict[str, dict[str, float]] = {
    # Low-power 1R points also require long horizons for n/setpoint convergence.
    "1r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    },
    # Low-power 9R points require long horizons to settle near n/setpoint ~= 1.
    "9r": {
        "0p00001": 1.0e8,  # 1e-5
        "0p00010": 1.0e7,  # 1e-4
        "0p00100": 1.0e6,  # 1e-3
    }
}
NUMBER_OF_INTERVALS_OVERRIDES: dict[str, dict[str, int]] = {
    # Keep long-horizon runs tractable while retaining tail-mean fidelity.
    "1r": {
        "0p00001": 20_000,  # 1e-5 @ 1e8 s
        "0p00010": 50_000,  # 1e-4 @ 1e7 s
        "0p00100": 50_000,  # 1e-3 @ 1e6 s
    },
    "9r": {
        "0p00001": 20_000,  # 1e-5 @ 1e8 s
        "0p00010": 50_000,  # 1e-4 @ 1e7 s
        "0p00100": 50_000,  # 1e-3 @ 1e6 s
    },
}
MODEL_NAME_OVERRIDES: dict[str, dict[str, str]] = {}

# Region-volume weights used to summarize 9R region temperatures into
# core-representative scalar setpoints (comparable to 1R scalar fields).
R9_VOL_F1 = (
    0.003795391373,
    0.012869720861,
    0.007038081469,
    0.008797601837,
    0.021767608195,
    0.011889109660,
    0.014880331986,
    0.059823315476,
    0.040594513827,
)
R9_VOL_F2 = (
    0.003971456514,
    0.008772341956,
    0.007038081469,
    0.017142787894,
    0.014880331986,
    0.011889109660,
    0.028956494924,
    0.034788134316,
    0.068118358784,
)
R9_VOL_G = (
    0.03488047800,
    0.10533760200,
    0.08002416000,
    0.10244745000,
    0.17818736400,
    0.13543456200,
    0.17330364000,
    0.47895127800,
    0.46943522400,
)

def table_to_result_variable(core_model: str) -> dict[str, str]:
    if core_model == "1r":
        mapping = {
            "fuelTempSetPointNode1": "core1R.fuelchannel.fuelNode1.T",
            "fuelTempSetPointNode2": "core1R.fuelchannel.fuelNode2.T",
            "graphiteTempSetPoint": "core1R.fuelchannel.grapNode.T",
            "heatExchanger.TpIn_0": "heatExchanger.T_in_pFluid.T",
            "heatExchanger.TpOut_0": "heatExchanger.T_out_pFluid.T",
            "heatExchanger.TsIn_0": "heatExchanger.T_in_sFluid.T",
            "heatExchanger.TsOut_0": "heatExchanger.T_out_sFluid.T",
            "heatExchanger.T_PN1_0": "heatExchanger.T_PN1",
            "heatExchanger.T_PN2_0": "heatExchanger.T_PN2",
            "heatExchanger.T_PN3_0": "heatExchanger.T_PN3",
            "heatExchanger.T_PN4_0": "heatExchanger.T_out_pFluid.T",
            "heatExchanger.T_TN1_0": "heatExchanger.T_TN1",
            "heatExchanger.T_TN2_0": "heatExchanger.T_TN2",
            "heatExchanger.T_SN1_0": "heatExchanger.T_SN1",
            "heatExchanger.T_SN2_0": "heatExchanger.T_SN2",
            "heatExchanger.T_SN3_0": "heatExchanger.T_SN3",
            "heatExchanger.T_SN4_0": "heatExchanger.T_out_sFluid.T",
            "pipeHXtoUHX.T_0": "pipeHXtoUHX.tempPi",
            "pipeUHXtoHX.T_0": "pipeUHXtoHX.tempPi",
            "dhrs.T_0": "dhrs.tempOut.T",
            "pipeDHRStoHX.T_0": "pipeDHRStoHX.tempPi",
            "pipeHXtoCore.T_0": "pipeHXtoCore.tempPi",
            "pipeCoreToDHRS.T_0": "pipeCoreToDHRS.tempPi",
            "uhx.Tp_0": "uhx.tempOut.T",
        }
        return mapping

    mapping = {
        "fuelTempSetPointNode1": "msre9r.R1.fuelNode1.T",
        "fuelTempSetPointNode2": "msre9r.R1.fuelNode2.T",
        "graphiteTempSetPoint": "msre9r.R1.grapNode.T",
        "Tmix_0": "msre9r.upperPlenum.T",
        "heatExchanger.TpIn_0": "heatExchanger.T_in_pFluid.T",
        "heatExchanger.TpOut_0": "heatExchanger.T_out_pFluid.T",
        "heatExchanger.TsIn_0": "heatExchanger.T_in_sFluid.T",
        "heatExchanger.TsOut_0": "heatExchanger.T_out_sFluid.T",
        "heatExchanger.T_PN1_0": "heatExchanger.T_PN1",
        "heatExchanger.T_PN2_0": "heatExchanger.T_PN2",
        "heatExchanger.T_PN3_0": "heatExchanger.T_PN3",
        "heatExchanger.T_PN4_0": "heatExchanger.T_out_pFluid.T",
        "heatExchanger.T_TN1_0": "heatExchanger.T_TN1",
        "heatExchanger.T_TN2_0": "heatExchanger.T_TN2",
        "heatExchanger.T_SN1_0": "heatExchanger.T_SN1",
        "heatExchanger.T_SN2_0": "heatExchanger.T_SN2",
        "heatExchanger.T_SN3_0": "heatExchanger.T_SN3",
        "heatExchanger.T_SN4_0": "heatExchanger.T_out_sFluid.T",
        "pipeHXtoUHX.T_0": "pipeHXtoUHX.tempPi",
        "pipeUHXtoHX.T_0": "pipeUHXtoHX.tempPi",
        "dhrs.T_0": "dhrs.tempOut.T",
        "pipeDHRStoHX.T_0": "pipeDHRStoHX.tempPi",
        "pipeHXtoCore.T_0": "pipeHXtoCore.tempPi",
        "pipeCoreToDHRS.T_0": "pipeCoreToDHRS.tempPi",
        "uhx.Tp_0": "uhx.tempOut.T",
    }
    for idx in range(1, 10):
        mapping[f"TF1_0_regions[{idx}]"] = f"msre9r.R{idx}.fuelNode1.T"
    for idx in range(1, 10):
        mapping[f"TF2_0_regions[{idx}]"] = f"msre9r.R{idx}.fuelNode2.T"
    for idx in range(1, 10):
        mapping[f"TG_0_regions[{idx}]"] = f"msre9r.R{idx}.grapNode.T"
    return mapping


def parse_args() -> argparse.Namespace:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
    default_core_dir = os.path.join(repo_root, "core")

    parser = argparse.ArgumentParser(
        description="Generate steady-state setpoint table vs power for nominal initialization."
    )
    parser.add_argument(
        "--powers",
        type=str,
        default="1e-5, 1e-4, 1e-3, 1e-2, 0.1, 0.2,0.4,0.6,0.8,1.0,1.2",
        help="Comma-separated power values, e.g. 1e-5,1e-4,1e-3,1e-2,0.1,0.2,0.4,0.6,0.8,1.0,1.2",
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core segmentation to simulate (default: 1r)",
    )
    parser.add_argument(
        "--core_dir",
        type=str,
        default=default_core_dir,
        help="Directory containing core Modelica files (default: ../core)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Path to MSRR model file (default: <core_dir>/MSRR.mo)",
    )
    parser.add_argument(
        "--library",
        type=str,
        default=None,
        help="Path to SMD_MSR_Modelica.mo (default: <core_dir>/SMD_MSR_Modelica.mo)",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default=None,
        help="Modelica model to simulate (default depends on --core_model)",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=30000.0,
        help="Simulation stop time in seconds (default: 30000)",
    )
    parser.add_argument(
        "--tail_fraction",
        type=float,
        default=0.2,
        help="Fraction of final time points to average for steady state (default: 0.2)",
    )
    parser.add_argument(
        "--tail_min_samples",
        type=int,
        default=1000,
        help="Minimum number of samples from simulation tail to average (default: 1000)",
    )
    parser.add_argument(
        "--work_dir",
        type=str,
        default="/tmp/msrr_setpoint_table",
        help="Working directory for generated simulation cases (default: /tmp/msrr_setpoint_table)",
    )
    parser.add_argument(
        "--reuse_csv",
        action="store_true",
        help="Reuse existing CSVs in work_dir and skip re-running omc",
    )
    parser.add_argument(
        "--heat_loss",
        action="store_true",
        help=(
            "Deprecated for power-dependent setpoints. "
            "Heat loss should only be used in startup scenarios."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows to an existing output CSV (requires matching columns)",
    )
    feedback_group = parser.add_mutually_exclusive_group()
    feedback_group.add_argument(
        "--no_feedback",
        action="store_true",
        help="Disable reactivity feedback during steady-state generation",
    )
    feedback_group.add_argument(
        "--feedback_on",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--init_temp",
        type=float,
        default=570.0,
        help="Initial setpoint temperature for steady-state runs (degC, default: 570)",
    )
    parser.add_argument(
        "--init_from",
        type=str,
        default="",
        help="CSV path to use for initialization overrides (matches by power and heatLossEnabled if present)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output CSV path (default: core/init/setpoints_<core_model>.csv)",
    )
    parser.add_argument(
        "--n_jobs",
        type=int,
        default=max(1, os.cpu_count() or 1),
        help="Number of parallel omc jobs (default: CPU count; use 1 for serial)",
    )
    return parser.parse_args()


def parse_powers(powers_text: str) -> list[float]:
    parts = [item.strip() for item in powers_text.split(",") if item.strip()]
    if not parts:
        raise ValueError("No power values provided.")
    values = sorted({float(item) for item in parts})

    filtered: list[float] = []
    skipped_zero = 0
    for value in values:
        if abs(value) < 1e-12:
            skipped_zero += 1
            continue
        if value < 0:
            raise ValueError(f"Negative power is nonphysical: {value}")
        filtered.append(value)

    if skipped_zero > 0:
        print(f"Skipping {skipped_zero} zero-power entries (nonphysical for setpoint generation).")
    if not filtered:
        raise ValueError("No non-zero power values provided after filtering.")
    return filtered


def sanitize_power_tag(power: float) -> str:
    text = f"{power:.5f}"
    return text.replace("-", "m").replace(".", "p")


def power_key(power: float) -> str:
    return f"{power:.12g}"


def load_init_table(
    init_path: str,
    steady_state_columns: list[str],
) -> dict[tuple[str, int | None], dict[str, float]]:
    init_table: dict[tuple[str, int | None], dict[str, float]] = {}
    if not init_path:
        return init_table
    if not os.path.exists(init_path):
        raise FileNotFoundError(f"Init table not found: {init_path}")
    with open(init_path, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Init table has no header: {init_path}")
        has_heat_loss = "heatLossEnabled" in reader.fieldnames
        for raw in reader:
            if not raw or (raw.get("power") or "").strip() == "":
                continue
            power = float(raw["power"])
            key = power_key(power)
            heat_flag = None
            if has_heat_loss:
                heat_flag = int(float(raw.get("heatLossEnabled", "0")))
            overrides: dict[str, float] = {}
            for column in steady_state_columns:
                val = (raw.get(column) or "").strip()
                if val == "":
                    continue
                overrides[column] = float(val)
            init_table[(key, heat_flag)] = overrides
    return init_table


def select_init_overrides(
    init_table: dict[tuple[str, int | None], dict[str, float]],
    power: float,
    heat_loss: bool,
) -> dict[str, float] | None:
    if not init_table:
        return None
    key = power_key(power)
    heat_key = int(bool(heat_loss))
    match = init_table.get((key, heat_key))
    if match is not None:
        return match
    return init_table.get((key, None))


def default_init_overrides(core_model: str, init_temp: float) -> dict[str, float]:
    overrides = {
        "fuelTempSetPointNode1": init_temp,
        "fuelTempSetPointNode2": init_temp,
        "graphiteTempSetPoint": init_temp,
    }
    if core_model == "9r":
        for idx in range(1, 10):
            overrides[f"TF1_0_regions[{idx}]"] = init_temp
            overrides[f"TF2_0_regions[{idx}]"] = init_temp
            overrides[f"TG_0_regions[{idx}]"] = init_temp
        overrides["Tmix_0"] = init_temp
    return overrides


def resolve_init_overrides(
    core_model: str,
    init_table: dict[tuple[str, int | None], dict[str, float]],
    power: float,
    heat_loss: bool,
    init_temp: float,
) -> dict[str, float] | None:
    init_row = select_init_overrides(init_table, power, heat_loss)
    if init_row is not None:
        return init_row
    return default_init_overrides(core_model, init_temp)


def run_steady_state_case(
    power: float,
    work_dir: str,
    model_name: str,
    model_src: str,
    library_src: str,
    stop_time: float,
    variable_filter: str,
    heat_loss: bool,
    init_overrides: dict[str, float] | None = None,
    feedback_on: bool = True,
    simflags_extra: str = "",
    method: str = "dassl",
    number_of_intervals: int | None = None,
) -> str:
    power_tag = sanitize_power_tag(power)
    case_dir = os.path.join(work_dir, f"power_{power_tag}")
    os.makedirs(case_dir, exist_ok=True)

    model_name_file = "MSRR.mo"
    library_name_file = "SMD_MSR_Modelica.mo"

    copyfile(model_src, os.path.join(case_dir, model_name_file))
    copyfile(library_src, os.path.join(case_dir, library_name_file))

    file_prefix = f"MSRR_ss_{power_tag}"
    override = (
        f"powerLevel={power:.10g},"
        f"perturbationAmplitudePcm=0,"
        f"perturbationOmega=0.01,"
        f"perturbationStartTime={stop_time + 1:.10g}"
    )

    # Join tuple into a string if it was created as a tuple due to commas
    if isinstance(override, tuple):
        override = "".join(override)

    if not feedback_on:
        if "9R" in model_name or "9r" in model_name:
            # Disable reactivity feedback for 9R core
            override += ",msre9r.aF=0,msre9r.aG=0"
        else:
            # Disable reactivity feedback for 1R core (default)
            override += ",core1R.a_F=0,core1R.a_G=0"

    if init_overrides:
        for key in sorted(init_overrides):
            override += f",{key}={init_overrides[key]:.16g}"

    variable_filter = variable_filter.strip()
    variable_filter = variable_filter.replace('"', '\\"')
    simflags_extra = simflags_extra.strip()
    if number_of_intervals is None:
        intervals = int(stop_time * 10)
    else:
        intervals = int(number_of_intervals)
    if intervals <= 0:
        raise ValueError(f"number_of_intervals must be > 0, got {intervals}")

    mos_text = (
        f'loadFile("{library_name_file}");\n'
        f'loadFile("{model_name_file}");\n'
        f'simulate({model_name},'
        f'startTime=0,'
        f'stopTime={stop_time:.0f},'
        f'numberOfIntervals={intervals},'
        f'tolerance=1E-6,'
        f'method="{method}",'
        f'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}",'
        f'simflags="-override={override} -variableFilter=\\"{variable_filter}\\"'
        f'{f" {simflags_extra}" if simflags_extra else ""}");\n'
    )

    mos_path = os.path.join(case_dir, "runModelica.mos")
    with open(mos_path, "w") as handle:
        handle.write(mos_text)

    result = subprocess.run(
        ["omc", "--showErrorMessages", "runModelica.mos"],
        cwd=case_dir,
        capture_output=True,
        text=True,
    )

    with open(os.path.join(case_dir, "omc_stdout.log"), "w") as handle:
        handle.write(result.stdout)
    with open(os.path.join(case_dir, "omc_stderr.log"), "w") as handle:
        handle.write(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"omc failed for power={power} with exit code {result.returncode}. "
            f"See {case_dir}/omc_stderr.log."
        )

    csv_path = os.path.join(case_dir, f"{file_prefix}_res.csv")
    if not os.path.exists(csv_path):
        raise RuntimeError(
            f"omc succeeded but output CSV missing for power={power}. "
            f"Expected: {csv_path}"
        )

    return csv_path


def read_tail_means(
    csv_path: str,
    variable_names: list[str],
    tail_fraction: float,
    tail_min_samples: int,
) -> dict[str, float]:
    def count_data_rows(path: str) -> int:
        with open(path, "rb") as handle:
            header = handle.readline()
            if not header:
                raise ValueError(f"Empty CSV: {path}")
            count = 0
            last_byte = b"\n"
            while True:
                buf = handle.read(1024 * 1024)
                if not buf:
                    break
                count += buf.count(b"\n")
                last_byte = buf[-1:]
            if count == 0:
                return 0
            if last_byte != b"\n":
                count += 1
            return count

    sample_count = count_data_rows(csv_path)
    if sample_count == 0:
        raise ValueError(f"No data rows in CSV: {csv_path}")

    tail_size = max(int(sample_count * tail_fraction), tail_min_samples)
    tail_size = min(tail_size, sample_count)
    start_index = sample_count - tail_size

    with open(csv_path, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Empty CSV: {csv_path}")

        names = [item.strip().strip('"') for item in header]
        name_to_index = {name: idx for idx, name in enumerate(names)}
        missing = [name for name in variable_names if name not in name_to_index]
        if missing:
            raise ValueError(
                f"Missing result variables in {csv_path}: {', '.join(missing)}"
            )

        sums = {name: 0.0 for name in variable_names}
        count = 0
        for idx, row in enumerate(reader):
            if idx < start_index:
                continue
            if not row:
                continue
            for name in variable_names:
                sums[name] += float(row[name_to_index[name]])
            count += 1

    if count == 0:
        raise ValueError(f"No tail data rows in CSV: {csv_path}")

    return {name: sums[name] / float(count) for name in variable_names}


def _weighted_region_average(
    row: dict[str, float],
    *,
    prefix: str,
    weights: tuple[float, ...],
) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for idx, weight in enumerate(weights, start=1):
        key = f"{prefix}[{idx}]"
        if key not in row:
            raise KeyError(f"Missing 9R regional setpoint '{key}' while computing weighted average.")
        weighted_sum += float(row[key]) * weight
        total_weight += weight
    if total_weight <= 0.0:
        raise ValueError("Invalid 9R weighting with non-positive total volume.")
    return weighted_sum / total_weight


def _apply_9r_scalar_harmonization(row: dict[str, float]) -> None:
    # Keep 9R scalar columns physically representative of the whole core,
    # not just region 1, so they are comparable to 1R scalar setpoints.
    row["fuelTempSetPointNode1"] = _weighted_region_average(
        row,
        prefix="TF1_0_regions",
        weights=R9_VOL_F1,
    )
    row["fuelTempSetPointNode2"] = _weighted_region_average(
        row,
        prefix="TF2_0_regions",
        weights=R9_VOL_F2,
    )
    row["graphiteTempSetPoint"] = _weighted_region_average(
        row,
        prefix="TG_0_regions",
        weights=R9_VOL_G,
    )


def build_table_row(
    core_model: str,
    power: float,
    tail_means: dict[str, float],
    table_mapping: dict[str, str],
    heat_loss: bool,
) -> dict[str, float]:
    row = {"power": power, "heatLossEnabled": 1 if heat_loss else 0}
    for column, result_variable in table_mapping.items():
        row[column] = tail_means[result_variable]
    if core_model == "9r":
        _apply_9r_scalar_harmonization(row)
    return row


def write_table(
    rows: list[dict[str, float]],
    output_path: str,
    steady_state_columns: list[str],
) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    header = ["power", "heatLossEnabled"] + steady_state_columns
    with open(output_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            formatted = {"power": f"{row['power']:.10g}"}
            formatted["heatLossEnabled"] = str(int(row.get("heatLossEnabled", 0)))
            for column in steady_state_columns:
                formatted[column] = f"{row[column]:.16g}"
            writer.writerow(formatted)


def run_power_case(
    power: float,
    work_dir: str,
    core_model: str,
    model_name: str,
    model_src: str,
    library_src: str,
    stop_time: float,
    variable_names: list[str],
    tail_fraction: float,
    tail_min_samples: int,
    table_mapping: dict[str, str],
    reuse_csv: bool,
    heat_loss: bool,
    init_overrides: dict[str, float] | None,
    feedback_on: bool,
) -> dict[str, float]:
    output_variables = ["time"] + sorted(set(variable_names))
    power_tag = sanitize_power_tag(power)
    interval_override = NUMBER_OF_INTERVALS_OVERRIDES.get(core_model, {}).get(power_tag)
    if core_model == "9r":
        variable_filter = (
            r"^(time|msre9r\\.upperPlenum\\.T|msre9r\\.R[1-9]\\.(fuelNode1|fuelNode2|grapNode)\\.T|"
            r"heatExchanger\\.T_(in|out)_(p|s)Fluid\\.T|heatExchanger\\.T_[PST]N[1-4]|"
            r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\\.tempPi|"
            r"dhrs\\.tempOut\\.T|uhx\\.tempOut\\.T|"
            r"msre9r\\.mpke\\.n_population\\.n|msre9r\\.reactorPower\\.P|powerBlock\\.fissionPower\\.P)$"
        )
    else:
        variable_filter = (
            r"^(time|core1R\\.fuelchannel\\.(fuelNode1|fuelNode2|grapNode)\\.T|"
            r"heatExchanger\\.T_(in|out)_(p|s)Fluid\\.T|heatExchanger\\.T_[PST]N[1-4]|"
            r"pipe(HXtoUHX|UHXtoHX|DHRStoHX|HXtoCore|CoreToDHRS)\\.tempPi|"
            r"dhrs\\.tempOut\\.T|uhx\\.tempOut\\.T|"
            r"core1R\\.mpke\\.n_population\\.n|core1R\\.reactorPower\\.P|powerBlock\\.fissionPower\\.P)$"
        )
    if reuse_csv:
        csv_path = os.path.join(
            work_dir,
            f"power_{power_tag}",
            f"MSRR_ss_{power_tag}_res.csv",
        )
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Missing CSV for power={power}: {csv_path}")
    else:
        csv_path = run_steady_state_case(
            power=power,
            work_dir=work_dir,
            model_name=model_name,
            model_src=model_src,
            library_src=library_src,
            stop_time=stop_time,
            variable_filter=variable_filter,
            heat_loss=heat_loss,
            init_overrides=init_overrides,
            feedback_on=feedback_on,
            number_of_intervals=interval_override,
        )
    tail_means = read_tail_means(
        csv_path=csv_path,
        variable_names=variable_names,
        tail_fraction=tail_fraction,
        tail_min_samples=tail_min_samples,
    )
    return build_table_row(
        core_model=core_model,
        power=power,
        tail_means=tail_means,
        table_mapping=table_mapping,
        heat_loss=heat_loss,
    )


def main() -> None:
    args = parse_args()

    if not (0.0 < args.tail_fraction <= 1.0):
        raise ValueError("--tail_fraction must be in (0, 1].")
    if args.tail_min_samples <= 0:
        raise ValueError("--tail_min_samples must be > 0.")
    if args.n_jobs <= 0:
        raise ValueError("--n_jobs must be > 0.")
    if args.heat_loss:
        raise ValueError(
            "Heat-loss setpoint generation is disabled. "
            "Use heat loss only in startup scenarios."
        )

    powers = parse_powers(args.powers)
    core_dir = os.path.abspath(args.core_dir)
    model_src = os.path.abspath(args.model) if args.model else os.path.join(core_dir, "MSRR.mo")
    library_src = (
        os.path.abspath(args.library)
        if args.library
        else os.path.join(core_dir, "SMD_MSR_Modelica.mo")
    )
    base_model_name = args.model_name or MODEL_NAME_BY_CORE[args.core_model]
    work_dir = os.path.abspath(args.work_dir)
    if args.output.strip():
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), f"setpoints_{args.core_model}.csv")
        )

    if not os.path.exists(model_src):
        raise FileNotFoundError(f"Cannot find model file: {model_src}")
    if not os.path.exists(library_src):
        raise FileNotFoundError(f"Cannot find library file: {library_src}")

    os.makedirs(work_dir, exist_ok=True)

    table_mapping = table_to_result_variable(args.core_model)
    steady_state_columns = list(table_mapping.keys())
    requested_result_vars = sorted(set(table_mapping.values()))
    ncpu = min(args.n_jobs, len(powers))
    init_table = {}
    if args.init_from.strip():
        init_table = load_init_table(args.init_from, steady_state_columns)

    print("Generating MSRR steady-state table")
    print(f"  Core model:  {args.core_model}")
    print(f"  Model file:  {model_src}")
    print(f"  Library:     {library_src}")
    print(f"  Model name:  {base_model_name}")
    print(f"  Powers:      {powers}")
    print(f"  Stop time:   {args.stop_time}")
    print(f"  Tail window: {args.tail_fraction * 100:.1f}% (min {args.tail_min_samples} samples)")
    print(f"  Work dir:    {work_dir}")
    print(f"  Reuse CSVs:  {args.reuse_csv}")
    print("  Heat loss:   False (startup-only, disabled for power setpoints)")
    print(f"  Append:      {args.append}")
    print(f"  Init temp:   {args.init_temp}")
    feedback_on = not args.no_feedback
    print(f"  Feedback:   {'on' if feedback_on else 'off'}")
    print(f"  Init from:   {args.init_from or '(none)'}")
    print(f"  Output:      {output_path}")
    print(f"  Parallel:    {ncpu} jobs (threaded)")
    print("=" * 72)
    rows = []
    total = len(powers)
    with ThreadPoolExecutor(max_workers=ncpu) as executor:
        future_map = {
            executor.submit(
                run_power_case,
                power=power,
                work_dir=work_dir,
                core_model=args.core_model,
                model_name=MODEL_NAME_OVERRIDES.get(args.core_model, {}).get(
                    sanitize_power_tag(power),
                    base_model_name,
                ),
                model_src=model_src,
                library_src=library_src,
                stop_time=STOP_TIME_OVERRIDES.get(args.core_model, {}).get(
                    sanitize_power_tag(power),
                    args.stop_time,
                ),
                variable_names=requested_result_vars,
                tail_fraction=args.tail_fraction,
                tail_min_samples=args.tail_min_samples,
                table_mapping=table_mapping,
                reuse_csv=args.reuse_csv,
                heat_loss=args.heat_loss,
                init_overrides=resolve_init_overrides(
                    args.core_model,
                    init_table,
                    power,
                    args.heat_loss,
                    args.init_temp,
                ),
                feedback_on=feedback_on,
            ): power
            for power in powers
        }
        done = 0
        for future in as_completed(future_map):
            rows.append(future.result())
            done += 1
            print(f"  Progress: {done}/{total} cases complete")

    if args.append and os.path.exists(output_path):
        with open(output_path, newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"Existing output CSV has no header: {output_path}")
            if "heatLossEnabled" not in reader.fieldnames:
                raise ValueError(
                    "Existing output CSV missing 'heatLossEnabled' column. "
                    "Regenerate it before appending."
                )
            existing = []
            for raw in reader:
                if not raw or (raw.get("power") or "").strip() == "":
                    continue
                row = {"power": float(raw["power"])}
                row["heatLossEnabled"] = int(float(raw.get("heatLossEnabled", "0")))
                for column in steady_state_columns:
                    val = (raw.get(column) or "").strip()
                    if val == "":
                        continue
                    row[column] = float(val)
                existing.append(row)
        rows = existing + rows

    rows.sort(key=lambda item: (item["power"], item.get("heatLossEnabled", 0)))
    write_table(
        rows=rows,
        output_path=output_path,
        steady_state_columns=steady_state_columns,
    )

    print("=" * 72)
    print(f"Wrote steady-state table: {output_path}")


if __name__ == "__main__":
    main()
