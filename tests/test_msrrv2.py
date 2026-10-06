#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for core/MSRR.mo via OpenModelica."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest


from helpers.plant_config import (
    LUMPED_PLANT_DATA_FILE,
    copy_lumped_sources,
    load_plant,
    lumped_load_file_text,
    quantity_value,
)

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
    # Physics review 2026-09-27 (B1.4): the 9R startup references the same
    # 570 degC isothermal zero-power critical reference as 1R (formerly
    # 552.835 degC, which matched no setpoint table).
    "9r": 570.0,
}
STARTUP_N_FLOOR = 1e-9
# Restart from the promoted corrected-model rows (campaign
# corrected-2026-09-28), bounds about 5-10x the measured values at 0.1 and
# 1 MW. Measured relative n movement at 10 s / 50 s: 1R <= 2.7e-5 / 1.7e-4,
# 9R <= 4.4e-5 / 2.0e-4; rate at 50 s <= 3.4e-6. (A 9R 1.9e-2 drift seen
# first came from this test skipping the region-1 ICs, a pre-B1.3
# exclusion, not from the table.) The former 3e-2 / 0.20 bounds absorbed the
# stale-HX-IC kick of the review-2026-09 tables.
NOMINAL_INIT_REL_DELTA_N_TOL = {
    "1r": 3e-4,
    "9r": 3e-4,
}
NOMINAL_INIT_REL_DELTA_N_T50_TOL = {"1r": 2e-3, "9r": 2e-3}
NOMINAL_INIT_REL_DN_RATE_TOL = 5e-5


pytestmark = pytest.mark.skipif(OMC_BIN is None, reason="OpenModelica (omc) not found")


def _prepare_workspace(tmp_path: Path) -> Path:
    workdir = tmp_path / "omc_case"
    workdir.mkdir()
    copy_lumped_sources(CORE_DIR, workdir)
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
        lumped_load_file_text(
            [LUMPED_PLANT_DATA_FILE, "SMD_MSR_Modelica.mo", "MSRR.mo"]
        )
        + f"simulate({model_name},"
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
    # Region 1 of the 9R arrays is overridable since the physics review
    # 2026-09-27 (B1.3: R9MSRRuhx binds all nine region elements directly).
    # The former exclusion started region 1 from the deck trim instead of the
    # table row, which showed up as a ~2 % 9R neutron restart transient.
    non_overridable_keys: set[str] = set()
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
    # TASK-20260911-04 P1: the promoted core/init/ setpoint tables carry the
    # generator's 'qualified' convergence-verdict column. It is provenance
    # metadata, not a Modelica parameter -- forwarding it as
    # 'qualified=1' would corrupt the -override= payload (omc 1.27.0-cmake
    # only warns on unknown override names, so the leak must be excluded
    # here; pinned by test_simflags_from_setpoint_row_skips_non_modelica_columns).
    for key, value in row.items():
        if key == "power" or key == "qualified" or key in non_overridable_keys:
            continue
        parts.append(f"{key}={float(value):.16g}")
    return ",".join(parts)


@pytest.mark.parametrize("core_model", ["1r", "9r"])
def test_simflags_from_setpoint_row_skips_non_modelica_columns(core_model: str) -> None:
    """TASK-20260911-04 P1 pin: the -override= payload built from a promoted
    ``core/init/`` setpoint row must NOT contain the generator's ``qualified``
    verdict column.

    Non-vacuous in both directions: the row read from the promoted table
    genuinely carries ``qualified=1`` (the premise of the leak -- the
    pre-fix helper emitted a trailing 'qualified=1' into every payload,
    which omc 1.27.0-cmake only warns about, non-fatally), and the payload
    still forwards the real setpoint surface."""
    row = _setpoint_row_for_power(core_model, 1.0)
    assert "qualified" in row.index, (
        "premise: the promoted table row carries the verdict column"
    )
    simflags = _simflags_from_setpoint_row(1.0, row)
    assert "qualified=" not in simflags, (
        "the convergence verdict is not a Modelica parameter; it must be "
        "excluded from the override payload"
    )
    # The genuine setpoint surface is still forwarded.
    assert simflags.startswith("powerLevel=1")
    assert "fuelTempSetPointNode1=" in simflags


def _external_reactivity_column(columns: list[str]) -> str:
    for name in columns:
        if not name.startswith("der(") and name.endswith("externalReactivityIn"):
            return name
    raise AssertionError("Missing *externalReactivityIn column")


def _plant_data_core9r_trim_arrays(path: Path) -> dict[str, list[float]]:
    """TF1_0/TF2_0/TG_0 region ICs from the generated ``MSRR_PlantData.mo``.

    Reads the Core9R package of the PlantData file the simulated workdir
    actually loaded (copy_lumped_sources flattens core/generated/
    MSRR_PlantData.mo beside MSRR.mo), so the expected values come from the
    same generated constants R9MSRRuhx binds -- not from hard-coded digits.
    """
    raw = path.read_text()
    pkg_match = re.search(r"package\s+Core9R\b(.*?)\bend\s+Core9R\s*;", raw, re.S)
    assert pkg_match is not None, "Core9R package not found in MSRR_PlantData.mo"
    body = pkg_match.group(1)
    arrays: dict[str, list[float]] = {}
    for name in ("TF1_0_regions", "TF2_0_regions", "TG_0_regions"):
        arr_match = re.search(
            rf"final\s+constant\s+Real\s+{name}\s*\[[^\]]*\]\s*=\s*\{{([^}}]+)\}}",
            body,
            re.S,
        )
        assert arr_match is not None, f"{name} not found in PlantData Core9R"
        arrays[name] = [
            float(part.strip())
            for part in arr_match.group(1).split(",")
            if part.strip()
        ]
        assert len(arrays[name]) == 9, f"{name}: expected 9 regions"
    return arrays


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
    # Physics review 2026-09-27 (B1.3): the 9R core is trimmed by the
    # table's region columns (all nine elements overridable); the shell
    # scalars no longer feed it. The 1R core keeps the scalar route.
    region_overrides = ""
    if core_model == "9r":
        region_overrides = "".join(
            f",{prefix}[{i}]={float(setpoint_row[f'{prefix}[{i}]']):.16g}"
            for prefix in _REGION_PREFIXES
            for i in range(1, 10)
        )

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
            f"{region_overrides}"
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
    if core_model == "9r":
        expected = (
            float(setpoint_row["TF1_0_regions[1]"]),
            float(setpoint_row["TF2_0_regions[1]"]),
            float(setpoint_row["TG_0_regions[1]"]),
        )
    else:
        expected = (
            float(setpoint_row["fuelTempSetPointNode1"]),
            float(setpoint_row["fuelTempSetPointNode2"]),
            float(setpoint_row["graphiteTempSetPoint"]),
        )
    assert float(row0[freq_temp_cols["fuel1"]]) == pytest.approx(expected[0], abs=1e-6)
    assert float(row0[freq_temp_cols["fuel2"]]) == pytest.approx(expected[1], abs=1e-6)
    assert float(row0[freq_temp_cols["graphite"]]) == pytest.approx(expected[2], abs=1e-6)


_REGION_PREFIXES = ("TF1_0_regions", "TF2_0_regions", "TG_0_regions")


def test_nominal_trim_9r_region_trims_and_plenum_are_overridable(tmp_path: Path) -> None:
    """9R initialization contract (physics review 2026-09-27, B1.2/B1.3).

    Every element of TF1_0_regions / TF2_0_regions / TG_0_regions -- region 1
    included -- and the top-level Tmix_0 accept a setpoint-table override
    (the former cat(1, {scalar}, ...) binding made OMC refuse the region
    overrides, and Tmix_0 was 'not found' because the plenum bound a
    PlantData constant inside msre9r). The shell scalars
    fuelTempSetPointNode1/fuelTempSetPointNode2/graphiteTempSetPoint no
    longer feed the 9R core: overriding them alone leaves region 1 at the
    PlantData profile. Row-0 readbacks prove both directions."""
    workdir = _prepare_workspace(tmp_path)
    power_level = 0.1
    setpoint_row = _setpoint_row_for_power("9r", power_level)
    common = (
        f"powerLevel={power_level:.16g},"
        "perturbationAmplitudePcm=0,"
        "perturbationOmega=0.01,"
        "perturbationStartTime=1000,"
        "primaryPump.freeConvFF=1,"
        "secondaryPump.freeConvFF=1"
    )
    region_keys = [f"{prefix}[{i}]" for prefix in _REGION_PREFIXES for i in range(1, 10)]
    table = ",".join(
        f"{key}={float(setpoint_row[key]):.16g}" for key in region_keys + ["Tmix_0"]
    )
    csv_path, output = _simulate(
        workdir=workdir,
        model_name="MSRR.MSRRuhxNominalTrim9RNoTrips",
        file_prefix="nominal_init_9r_regions",
        stop_time=5.0,
        number_of_intervals=50,
        simflags=f"-override={common},{table}",
    )
    for key in region_keys + ["Tmix_0"]:
        assert f"not possible to override the following quantity: {key}" not in output, key
        assert f"override variable name not found in model: {key}" not in output, key
    row0 = pd.read_csv(csv_path).iloc[0]
    assert float(row0["msre9r.mpke.n_population.n"]) == pytest.approx(power_level, abs=1e-8)
    for region in (1, 2, 9):
        for node, prefix in (("fuelNode1", "TF1_0_regions"), ("fuelNode2", "TF2_0_regions"),
                             ("grapNode", "TG_0_regions")):
            assert float(row0[f"msre9r.R{region}.{node}.T"]) == pytest.approx(
                float(setpoint_row[f"{prefix}[{region}]"]), abs=1e-6
            ), (region, node)
    assert float(row0["msre9r.upperPlenum.T"]) == pytest.approx(
        float(setpoint_row["Tmix_0"]), abs=1e-6
    )

    # Shell scalars alone: accepted, but dead for the 9R core.
    csv_scalar, output_scalar = _simulate(
        workdir=workdir,
        model_name="MSRR.MSRRuhxNominalTrim9RNoTrips",
        file_prefix="nominal_init_9r_scalars",
        stop_time=5.0,
        number_of_intervals=50,
        simflags=(
            f"-override={common},"
            f"fuelTempSetPointNode1={float(setpoint_row['fuelTempSetPointNode1']):.16g},"
            f"fuelTempSetPointNode2={float(setpoint_row['fuelTempSetPointNode2']):.16g},"
            f"graphiteTempSetPoint={float(setpoint_row['graphiteTempSetPoint']):.16g}"
        ),
    )
    row0s = pd.read_csv(csv_scalar).iloc[0]
    plant = _plant_data_core9r_trim_arrays(workdir / LUMPED_PLANT_DATA_FILE)
    assert float(row0s["msre9r.R1.fuelNode1.T"]) == pytest.approx(plant["TF1_0_regions"][0], abs=1e-6)
    assert float(row0s["msre9r.R1.grapNode.T"]) == pytest.approx(plant["TG_0_regions"][0], abs=1e-6)


@pytest.mark.parametrize("core_model", ["1r", "9r"])
@pytest.mark.parametrize(
    # The 1 MW cases were strict xfails while core/init held the
    # review-2026-09 tables (former 4x HX UA); the corrected campaign
    # corrected-2026-09-28 regenerated and promoted them.
    "power_level", [0.1, 1.0]
)
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
        stop_time=60.0,
        number_of_intervals=3000,
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
    # The promoted corrected-model tables carry the finite-UA HX node states,
    # so the tube starts at rest: measured |der(T_TN1)(0)| <= 3.3e-8 K/s over
    # 1R/9R at 0.1 and 1 MW (the former review-2026-09 tables, with
    # T_TN≈T_SN, kicked it by up to ~20 K/s).
    assert abs(float(row0[hx_dn_col])) < 1e-4
    # Fast tube-node kick plus slower secondary-loop tail; require strong
    # decay of that IC imbalance by t=50 s.
    idx_t50 = (data["time"] - 50.0).abs().idxmin()
    row50 = data.iloc[idx_t50]
    assert abs(float(row50[hx_dn_col])) < max(
        0.05 * abs(float(row0[hx_dn_col])), 0.15
    )

    n0 = float(row0[n_col])
    n_scale = max(abs(n0), 1e-12)
    rel_delta_n_10 = abs(float(row10[n_col]) - n0) / n_scale
    rel_delta_n_50 = abs(float(row50[n_col]) - n0) / n_scale
    assert rel_delta_n_10 < NOMINAL_INIT_REL_DELTA_N_TOL[core_model]
    assert rel_delta_n_50 < NOMINAL_INIT_REL_DELTA_N_T50_TOL[core_model]
    assert abs(float(row50[dn_col])) / n_scale < NOMINAL_INIT_REL_DN_RATE_TOL


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


def test_uhx_trip_wrapper_demand_scales_with_power_level(tmp_path: Path) -> None:
    """TASK-20260909-01 P6 runtime evidence (lumped wrapper): the UHX-trip
    vehicle run at ``-override=powerLevel=0.1`` must demand 100 kW (1e5 W)
    at the UHX before the t = 4000 s demand drop.

    The trip wrapper's two-step demand is
    ``uhxDemandAmplitude = {powerLevel * MSRR_PlantData.nominalPower, 0}``
    (fix SHA 28f1193); the Stepper holds amplitude[1] over (0, 4000) s and
    ``uhx.powRm = powDemand.R * realToPow`` is an algebraic parameter-driven
    signal, so every sampled point in (1, 60] s must read exactly
    0.1 x 1e6 = 1e5 W. Fail-closed in both directions: the pre-fix literal
    ``{MSRR_PlantData.nominalPower, 0}`` reads 1e6 W here (10x
    over-extraction at a 100 kW operating point), and a silently rejected
    override (default powerLevel = 1) reads 1e6 W too. The default
    powerLevel = 1 makes the fixed expression identical to the pre-fix
    literal (algebraic identity 1*x == x), so the qualified review-2026-09
    baseline is unaffected; the expected number derives from the plant data
    (nominalPower = 1e6 W), never from simulation output. OpenModelica's
    initialization snapshot pair (t = 0 and the t ~ 1e-10 pre/post-init
    evaluation) legitimately reads zero -- the Stepper emits amplitude[1]
    only for time > stepTime[1] = 0 -- so the pin starts at t = 1 s.
    """
    workdir = _prepare_workspace(tmp_path)
    power_level = 0.1

    csv_path, output = _simulate(
        workdir=workdir,
        model_name="MSRR.MSRRuhxTripThermalSS",
        file_prefix="uhx_trip_p010",
        stop_time=60.0,
        number_of_intervals=60,
        simflags=f"-override=powerLevel={power_level:.16g}",
    )

    assert (
        "not possible to override the following quantity: powerLevel" not in output
    ), (
        "the powerLevel runtime override was rejected; the pin below would "
        "then only re-pin the powerLevel = 1 default"
    )

    data = pd.read_csv(csv_path)
    assert "uhx.powRm" in data.columns, (
        f"Missing 'uhx.powRm' column; available: {list(data.columns)[:20]}..."
    )
    nominal_power_w = float(quantity_value(load_plant("msrr")["nominal_power"]))
    expected_demand_w = power_level * nominal_power_w
    assert expected_demand_w == pytest.approx(1e5), (
        f"shared plant data moved: 0.1 x nominalPower = {expected_demand_w} W "
        "(evidence target is 100 kW)"
    )

    # Every sampled point at t >= 1 s (well inside the pre-step plateau, far
    # from the t = 4000 s drop) carries the scaled demand. The rows below
    # 1 s are OpenModelica's initialization snapshot pair (t = 0 and the
    # t ~ 1e-10 pre/post-init evaluation): the Stepper emits amplitude[1]
    # only for time > stepTime[1] = 0, so those rows legitimately read zero.
    window = data.loc[data["time"] >= 1.0, "uhx.powRm"].astype(float)
    assert not window.empty, "no sampled points in the pre-step demand window"
    assert (window - expected_demand_w).abs().max() <= 1e-6 * expected_demand_w, (
        f"UHX demand in the pre-step window deviates from "
        f"{expected_demand_w:.6e} W "
        f"(max deviation {(window - expected_demand_w).abs().max():.6e} W): "
        "the trip wrapper dropped the powerLevel factor from "
        "uhxDemandAmplitude or the override was ignored"
    )
    assert float(data.iloc[-1]["time"]) >= 60.0 - 1e-3
    print(
        f"\n[uhx-trip-p010] {len(window)} sampled pre-step points, "
        f"uhx.powRm = {window.iloc[-1]:.6e} W at t="
        f"{float(data.iloc[-1]['time']):.1f} s == {power_level:g} x "
        f"nominalPower ({nominal_power_w:.4e} W)"
    )
