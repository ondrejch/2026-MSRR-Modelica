#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for core/MSRR.mo via OpenModelica."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
SMD_LIBRARY = CORE_DIR / "SMD_MSR_Modelica.mo"
MSRR_MODEL = CORE_DIR / "MSRR.mo"
OMC_BIN = shutil.which("omc")


CORE_MODELS = {
    "1r": {
        "startup": "MSRR.MSRRstartUpCriticality",
        "startup_to_100kw": "MSRR.MSRRstartUpTo100kW",
        "nominal": "MSRR.MSRRuhxNominalTrim",
        "n_population_col": "core1R.mpke.n_population.n",
        "freq_temp_cols": {
            "fuel1": "core1R.fuelchannel.fuelNode1.T",
            "fuel2": "core1R.fuelchannel.fuelNode2.T",
            "graphite": "core1R.fuelchannel.grapNode.T",
        },
    },
    "9r": {
        "startup": "MSRR.MSRRstartUpCriticality9R",
        "startup_to_100kw": "MSRR.MSRRstartUpTo100kW9R",
        "nominal": "MSRR.MSRRuhxNominalTrim9R",
        "n_population_col": "msre9r.mpke.n_population.n",
        "freq_temp_cols": {
            "fuel1": "msre9r.R1.fuelNode1.T",
            "fuel2": "msre9r.R1.fuelNode2.T",
            "graphite": "msre9r.R1.grapNode.T",
        },
    },
}

STARTUP_EQ_T0 = {
    "1r": 570.0000000000202,
    "9r": 552.8354270952742,
}
STARTUP_N_FLOOR = 1e-9
NOMINAL_INIT_REL_DELTA_N_TOL = {
    "1r": 1e-3,
    # 9R at nominal full power shows a small initialization transient in some
    # OpenModelica builds while remaining near equilibrium.
    "9r": 5e-3,
}


pytestmark = pytest.mark.skipif(OMC_BIN is None, reason="OpenModelica (omc) not found")


def _prepare_workspace(tmp_path: Path) -> Path:
    workdir = tmp_path / "omc_case"
    workdir.mkdir()
    shutil.copy2(SMD_LIBRARY, workdir / SMD_LIBRARY.name)
    shutil.copy2(MSRR_MODEL, workdir / MSRR_MODEL.name)
    return workdir


def _simulate(
    workdir: Path,
    model_name: str,
    file_prefix: str,
    *,
    start_time: float = 0.0,
    stop_time: float = 10.0,
    number_of_intervals: int = 100,
    tolerance: float = 1e-6,
    method: str = "dassl",
    simflags: str = "",
) -> tuple[Path, str]:
    mos_path = workdir / "run_test.mos"
    csv_path = workdir / f"{file_prefix}_res.csv"
    if csv_path.exists():
        csv_path.unlink()

    flags_text = f',simflags="{simflags}"' if simflags else ""
    mos_text = (
        'loadFile("SMD_MSR_Modelica.mo");\n'
        'loadFile("MSRR.mo");\n'
        f"simulate({model_name},"
        f"startTime={start_time:.10g},"
        f"stopTime={stop_time:.10g},"
        f"numberOfIntervals={int(number_of_intervals)},"
        f"tolerance={tolerance:.10g},"
        f"method={method},"
        'outputFormat="csv",'
        f'fileNamePrefix="{file_prefix}"'
        f"{flags_text});\n"
    )
    mos_path.write_text(mos_text)

    proc = subprocess.run(
        [OMC_BIN, "--showErrorMessages", mos_path.name],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{proc.stdout}\n{proc.stderr}"

    assert proc.returncode == 0, output
    assert "Simulation execution failed for model" not in output, output
    assert 'resultFile = ""' not in output, output
    assert csv_path.exists(), output

    return csv_path, output


def _tail_mean(csv_path: Path, column_name: str, tail_fraction: float = 0.1) -> float:
    data = pd.read_csv(csv_path)
    assert column_name in data.columns, f"Missing column '{column_name}' in {csv_path}"
    tail_len = max(5, int(len(data) * tail_fraction))
    return float(data[column_name].tail(tail_len).mean())


def _setpoint_row_for_power(core_model: str, power: float) -> pd.Series:
    table_path = CORE_DIR / "init" / f"setpoints_{core_model}.csv"
    table = pd.read_csv(table_path)
    idx = (table["power"] - power).abs().idxmin()
    return table.loc[idx]


def _simflags_from_setpoint_row(power_level: float, row: pd.Series) -> str:
    hx_state_keys = {
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
    }
    non_overridable_keys = {
        "TF1_0_regions[1]",
        "TF2_0_regions[1]",
        "TG_0_regions[1]",
    }
    parts = [
        f"powerLevel={power_level:.16g}",
        "perturbationAmplitudePcm=0",
        "perturbationOmega=0.01",
        "perturbationStartTime=1000",
        "primaryPump.freeConvFF=1",
        "secondaryPump.freeConvFF=1",
    ]
    if any(key in row.index for key in hx_state_keys):
        parts.append("heatExchanger.detailedStateInitWeight=1")
    for key, value in row.items():
        if key == "power" or key in non_overridable_keys:
            continue
        parts.append(f"{key}={float(value):.16g}")
    return ",".join(parts)


def _external_reactivity_column(columns: list[str]) -> str:
    for name in columns:
        if not name.startswith("der(") and name.endswith("externalReactivityIn"):
            return name
    raise AssertionError("Missing *externalReactivityIn column")


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_startup_initialization_state(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)

    csv_path, _ = _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["startup"],
        file_prefix=f"startup_init_{core_model}",
        stop_time=5.0,
        number_of_intervals=50,
        simflags="-maxStepSize=0.01",
    )

    data = pd.read_csv(csv_path)
    row0 = data.iloc[0]

    n_col = CORE_MODELS[core_model]["n_population_col"]
    # Depending on OpenModelica codegen/runtime, startup n(t0) may be reported
    # as either the configured floor or exactly zero.
    assert abs(float(row0[n_col])) <= STARTUP_N_FLOOR

    rho_col = _external_reactivity_column(list(data.columns))
    assert float(row0[rho_col]) < 0.0

    if core_model == "1r":
        startup_temp_cols = [
            "core1R.fuelchannel.fuelNode1.T",
            "core1R.fuelchannel.fuelNode2.T",
            "core1R.fuelchannel.grapNode.T",
        ]
    else:
        startup_temp_cols = [
            name for name in data.columns
            if name.startswith("msre9r.R")
            and (
                name.endswith(".fuelNode1.T")
                or name.endswith(".fuelNode2.T")
                or name.endswith(".grapNode.T")
            )
        ]
    assert startup_temp_cols, "Missing startup core temperature columns"

    startup_temps = row0[startup_temp_cols].astype(float)
    assert float(startup_temps.max() - startup_temps.min()) < 1e-6

    expected_t0 = STARTUP_EQ_T0[core_model]
    assert float(startup_temps.mean()) == pytest.approx(expected_t0, abs=1e-6)


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_nominal_initialization_state_from_setpoints(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)
    power_level = 0.1
    setpoint_row = _setpoint_row_for_power(core_model, power_level)

    csv_path, _ = _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["nominal"],
        file_prefix=f"nominal_init_{core_model}",
        stop_time=5.0,
        number_of_intervals=50,
        simflags=(
            f"-override=powerLevel={power_level:.16g},"
            "perturbationAmplitudePcm=0,"
            "perturbationOmega=0.01,"
            "perturbationStartTime=1000,"
            "primaryPump.freeConvFF=1,"
            "secondaryPump.freeConvFF=1,"
            f"fuelTempSetPointNode1={float(setpoint_row['fuelTempSetPointNode1']):.16g},"
            f"fuelTempSetPointNode2={float(setpoint_row['fuelTempSetPointNode2']):.16g},"
            f"graphiteTempSetPoint={float(setpoint_row['graphiteTempSetPoint']):.16g}"
        ),
    )

    data = pd.read_csv(csv_path)
    row0 = data.iloc[0]

    n_col = CORE_MODELS[core_model]["n_population_col"]
    assert float(row0[n_col]) == pytest.approx(power_level, abs=1e-8)

    rho_col = _external_reactivity_column(list(data.columns))
    assert abs(float(row0[rho_col])) < 1e-12

    fission_power_col = next(
        (name for name in data.columns if not name.startswith("der(") and name.endswith("fissionPower.P")),
        None,
    )
    assert fission_power_col is not None, "Missing fission power column"
    assert float(row0[fission_power_col]) > 0.0

    freq_temp_cols = CORE_MODELS[core_model]["freq_temp_cols"]
    assert float(row0[freq_temp_cols["fuel1"]]) == pytest.approx(
        float(setpoint_row["fuelTempSetPointNode1"]), abs=1e-6
    )
    assert float(row0[freq_temp_cols["fuel2"]]) == pytest.approx(
        float(setpoint_row["fuelTempSetPointNode2"]), abs=1e-6
    )
    assert float(row0[freq_temp_cols["graphite"]]) == pytest.approx(
        float(setpoint_row["graphiteTempSetPoint"]), abs=1e-6
    )


@pytest.mark.parametrize("core_model", ["1r", "9r"])
@pytest.mark.parametrize("power_level", [0.1, 1.0])
def test_nominal_frequency_initialization_near_equilibrium(
    tmp_path: Path,
    core_model: str,
    power_level: float,
) -> None:
    workdir = _prepare_workspace(tmp_path)
    setpoint_row = _setpoint_row_for_power(core_model, power_level)

    csv_path, _ = _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["nominal"],
        file_prefix=f"nominal_eq_init_{core_model}",
        stop_time=20.0,
        number_of_intervals=2000,
        simflags=f"-override={_simflags_from_setpoint_row(power_level, setpoint_row)}",
    )

    data = pd.read_csv(csv_path)
    row0 = data.iloc[0]
    idx_t10 = (data["time"] - 10.0).abs().idxmin()
    row10 = data.iloc[idx_t10]

    n_col = CORE_MODELS[core_model]["n_population_col"]
    dn_col = f"der({n_col})"
    assert dn_col in data.columns

    assert float(row0["primaryPump.flowFrac.FF"]) == pytest.approx(1.0, abs=1e-12)
    assert float(row0["secondaryPump.flowFrac.FF"]) == pytest.approx(1.0, abs=1e-12)
    assert abs(float(row0[dn_col])) < 1e-9

    hx_dn_col = "der(heatExchanger.T_TN1)"
    assert hx_dn_col in data.columns
    assert abs(float(row0[hx_dn_col])) < 1e-3

    rel_delta_n = abs(float(row10[n_col]) - float(row0[n_col])) / max(abs(float(row0[n_col])), 1e-12)
    assert rel_delta_n < NOMINAL_INIT_REL_DELTA_N_TOL[core_model]


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_startup_model_initializes_and_runs_short(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)

    _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["startup"],
        file_prefix=f"startup_short_{core_model}",
        stop_time=10.0,
        number_of_intervals=100,
        simflags="-maxStepSize=0.01",
    )


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_startup_to_100kw_model_initializes_and_runs_short(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)

    _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["startup_to_100kw"],
        file_prefix=f"startup_to_100kw_short_{core_model}",
        stop_time=10.0,
        number_of_intervals=100,
        simflags="-maxStepSize=0.01",
    )


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_nominal_trim_power_override_changes_operating_point(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)
    nominal_model = CORE_MODELS[core_model]["nominal"]
    n_col = CORE_MODELS[core_model]["n_population_col"]

    csv_p1, out_p1 = _simulate(
        workdir=workdir,
        model_name=nominal_model,
        file_prefix=f"nominal_p1_{core_model}",
        stop_time=400.0,
        number_of_intervals=4000,
        simflags=(
            "-override=powerLevel=1.0,"
            "perturbationAmplitudePcm=0,"
            "perturbationOmega=0.01,"
            "perturbationStartTime=1000,"
            "primaryPump.freeConvFF=1,"
            "secondaryPump.freeConvFF=1"
        ),
    )
    csv_p08, out_p08 = _simulate(
        workdir=workdir,
        model_name=nominal_model,
        file_prefix=f"nominal_p08_{core_model}",
        stop_time=400.0,
        number_of_intervals=4000,
        simflags=(
            "-override=powerLevel=0.8,"
            "perturbationAmplitudePcm=0,"
            "perturbationOmega=0.01,"
            "perturbationStartTime=1000,"
            "primaryPump.freeConvFF=1,"
            "secondaryPump.freeConvFF=1"
        ),
    )

    assert "not possible to override the following quantity: powerLevel" not in out_p1
    assert "not possible to override the following quantity: powerLevel" not in out_p08

    mean_n_p1 = _tail_mean(csv_p1, n_col)
    mean_n_p08 = _tail_mean(csv_p08, n_col)
    assert mean_n_p1 > mean_n_p08 + 0.05, (mean_n_p1, mean_n_p08)


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_nominal_trim_emits_feedback_signals(tmp_path: Path, core_model: str) -> None:
    workdir = _prepare_workspace(tmp_path)

    csv_path, _ = _simulate(
        workdir=workdir,
        model_name=CORE_MODELS[core_model]["nominal"],
        file_prefix=f"nominal_feedback_{core_model}",
        stop_time=200.0,
        number_of_intervals=2000,
        simflags=(
            "-override=powerLevel=1.0,"
            "perturbationAmplitudePcm=1.0,"
            "perturbationOmega=0.005,"
            "perturbationStartTime=50"
        ),
    )

    data = pd.read_csv(csv_path)
    columns = list(data.columns)

    has_total_feedback = any(name.endswith("TotalTempFeedback") for name in columns)
    has_external_reactivity = any(name.endswith("externalReactivityIn") for name in columns)

    assert has_total_feedback, "Missing any *TotalTempFeedback column"
    assert has_external_reactivity, "Missing any *externalReactivityIn column"
