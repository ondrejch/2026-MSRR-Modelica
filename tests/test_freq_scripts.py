#!/usr/bin/env python3
"""Unit tests for frequency workflow helper logic.

TASK-20260908-01 P1: split-field semantics of
``freq.verify_campaign`` -- the summary carries the four explicit fields
(``provenance_complete`` / ``campaign_complete`` /
``numerical_quality(_status/_pass)`` / ``publication_approved``) beside the
LEGACY ``publication_eligible`` label, which keeps meaning provenance +
campaign completeness only and still keys the CLI exit status. Numerical
quality is evaluated from the aggregate's recorded fit statistics against
the owner threshold table (``helpers.numerical_quality``, owner decision
O2), with no waivers (waivers apply at the evidence/approval level).
Everything runs on tiny synthetic sweeps built through
``helpers.run_results`` + ``freq.sweep_manifest`` (hermetic: no omc, no
network, nothing written outside pytest scratch).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
from pathlib import Path
import sys

import numpy as np
import pytest

try:
    from freq import sweep_manifest
    from freq import verify_campaign
    from freq._common import (
        format_frequency_key,
        frequency_case_dir_name,
        frequency_file_prefix,
    )
    from helpers import numerical_quality as numerical_quality
    from helpers import plant_config as plant_config
    from helpers import run_results as run_results
except ImportError:  # direct-script execution outside an installed checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from freq import sweep_manifest
    from freq import verify_campaign
    from freq._common import (
        format_frequency_key,
        frequency_case_dir_name,
        frequency_file_prefix,
    )
    from helpers import numerical_quality as numerical_quality
    from helpers import plant_config as plant_config
    from helpers import run_results as run_results


def _load_module(_module_name: str, relative_path: str):
    qualified_name = relative_path.removesuffix(".py").replace("/", ".")
    return importlib.reload(importlib.import_module(qualified_name))


def test_read_sin_mag_mapping_uses_formatted_frequency_keys(tmp_path: Path) -> None:
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_test",
        "freq/collectFreqNominalParallel.py",
    )

    freqs = np.logspace(-2, 1, num=5)
    mapping_path = tmp_path / "sin_mag_by_freq.csv"
    with mapping_path.open("w", encoding="utf-8") as handle:
        handle.write("frequency_rad_s,sin_mag\n")
        for idx, freq in enumerate(freqs):
            handle.write(f"{freq:.12g},{(idx + 1):.12g}\n")

    mapping = collect_parallel.read_sin_mag_mapping(str(tmp_path))
    assert mapping is not None
    assert all(isinstance(key, str) for key in mapping)
    assert len(mapping) == len(freqs)
    for idx, freq in enumerate(freqs):
        key = collect_parallel.format_frequency_key(freq)
        assert mapping[key] == pytest.approx(float(idx + 1))


def test_read_sin_mag_mapping_returns_none_when_missing(tmp_path: Path) -> None:
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_test_missing",
        "freq/collectFreqNominalParallel.py",
    )
    assert collect_parallel.read_sin_mag_mapping(str(tmp_path)) is None


def test_read_stop_time_mapping_uses_formatted_frequency_keys(tmp_path: Path) -> None:
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_stop_time_test",
        "freq/collectFreqNominalParallel.py",
    )

    freqs = np.logspace(-3, -2, num=4)
    mapping_path = tmp_path / "stop_time_by_freq.csv"
    with mapping_path.open("w", encoding="utf-8") as handle:
        handle.write("frequency_rad_s,stop_time_s\n")
        for idx, freq in enumerate(freqs):
            handle.write(f"{freq:.12g},{(idx + 1) * 1000:.12g}\n")

    mapping = collect_parallel.read_stop_time_mapping(str(tmp_path))
    assert mapping is not None
    for idx, freq in enumerate(freqs):
        key = collect_parallel.format_frequency_key(freq)
        assert mapping[key] == pytest.approx(float((idx + 1) * 1000))


def test_format_matlab_assignment_is_numpy2_safe() -> None:
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_matlab_test",
        "freq/collectFreqNominalParallel.py",
    )
    line = collect_parallel.format_matlab_assignment(
        "freq",
        [np.float64(0.01), np.float64(1000.0)],
    )
    assert line == "freq = [0.01 1000];\n"
    assert "np.float64" not in line

    edge = collect_parallel.format_matlab_assignment(
        "gain_dB",
        [float("-inf"), float("nan"), 3.5],
    )
    assert edge == "gain_dB = [-Inf NaN 3.5];\n"


def test_process_single_freq_phase_is_referenced_to_perturbation_start(
    tmp_path: Path,
) -> None:
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_phase_reference_test",
        "freq/collectFreqNominalParallel.py",
    )

    freq_point = 2.5
    ss_time = 100.0
    sin_mag = 1.0
    gain = 2.0
    phase_reference_deg = -20.0
    phase_reference_rad = np.deg2rad(phase_reference_deg)
    amplitude = gain * sin_mag * 1e-5

    work_path = tmp_path / frequency_case_dir_name(freq_point)
    work_path.mkdir()
    csv_path = work_path / f"{frequency_file_prefix(freq_point)}_res.csv"

    times = np.arange(95.0, 220.0, 0.05)
    power = 1.0 + amplitude * np.sin(freq_point * (times - ss_time) + phase_reference_rad)
    with csv_path.open("w", encoding="utf-8") as handle:
        handle.write("time,npopulationn\n")
        for time_value, power_value in zip(times, power):
            handle.write(f"{time_value:.8f},{power_value:.16e}\n")

    early = collect_parallel.process_single_freq(
        freq_point=freq_point,
        results_dir=str(tmp_path),
        sin_mag=sin_mag,
        ss_time=ss_time,
        fit_start=ss_time,
        fit_end=200.0,
    )
    late = collect_parallel.process_single_freq(
        freq_point=freq_point,
        results_dir=str(tmp_path),
        sin_mag=sin_mag,
        ss_time=ss_time,
        fit_start=137.6,
        fit_end=200.0,
    )

    assert early["success"] is True
    assert late["success"] is True
    assert early["phase_deg"] == pytest.approx(phase_reference_deg, abs=0.2)
    assert late["phase_deg"] == pytest.approx(phase_reference_deg, abs=0.3)
    assert late["phase_deg"] == pytest.approx(early["phase_deg"], abs=0.3)


def test_process_single_freq_does_not_extend_before_perturbation(
    tmp_path: Path,
) -> None:
    """Too few finite post-start samples reject the fit; pre-forcing data
    are never pulled in. ``fit_end=None`` must not TypeError."""
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_no_preforce_fallback",
        "freq/collectFreqNominalParallel.py",
    )
    freq_point = 0.1
    ss_time = 100.0
    work_path = tmp_path / frequency_case_dir_name(freq_point)
    work_path.mkdir()
    csv_path = work_path / f"{frequency_file_prefix(freq_point)}_res.csv"
    with csv_path.open("w", encoding="utf-8") as handle:
        handle.write("time,npopulationn\n")
        for time_value in np.linspace(0.0, 99.9, 400):
            handle.write(f"{time_value:.8f},1.0\n")
        for time_value in np.linspace(ss_time, ss_time + 0.3, 4):
            handle.write(f"{time_value:.8f},1.0\n")

    result = collect_parallel.process_single_freq(
        freq_point=freq_point,
        results_dir=str(tmp_path),
        sin_mag=1.0,
        ss_time=ss_time,
        fit_start=ss_time,
        fit_end=None,
        fit_min_samples=10,
    )
    assert result["success"] is False
    assert result["error"] is not None
    assert "sine fit rejected" in result["error"]
    assert result.get("n_samples", 0) < 10


def test_process_single_freq_fit_end_none_uses_csv_end(tmp_path: Path) -> None:
    """``fit_end=None`` takes the last CSV time as the window end."""
    collect_parallel = _load_module(
        "collect_freq_nominal_parallel_fit_end_none",
        "freq/collectFreqNominalParallel.py",
    )
    freq_point = 2.5
    ss_time = 100.0
    sin_mag = 1.0
    amplitude = 2.0 * sin_mag * 1e-5
    work_path = tmp_path / frequency_case_dir_name(freq_point)
    work_path.mkdir()
    csv_path = work_path / f"{frequency_file_prefix(freq_point)}_res.csv"
    times = np.arange(ss_time, 220.0, 0.05)
    power = 1.0 + amplitude * np.sin(freq_point * (times - ss_time))
    with csv_path.open("w", encoding="utf-8") as handle:
        handle.write("time,npopulationn\n")
        for time_value, power_value in zip(times, power):
            handle.write(f"{time_value:.8f},{power_value:.16e}\n")

    result = collect_parallel.process_single_freq(
        freq_point=freq_point,
        results_dir=str(tmp_path),
        sin_mag=sin_mag,
        ss_time=ss_time,
        fit_start=ss_time,
        fit_end=None,
    )
    assert result["success"] is True
    assert result["fit_end"] == pytest.approx(float(times[-1]))


def test_validate_args_parallel_accepts_nominal_case() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_test",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1.0,
        freq_min=1e-2,
        freq_max=1e1,
        num_freq=64,
        stop_time=1000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        output_intervals_per_second=10.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
        ss_time=100.0,
        forcing_time_step=0.0,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
        n_jobs=4,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
    )
    run_parallel.validate_args(args)


def test_run_all_powers_defaults_use_extended_low_frequency_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_all = _load_module(
        "run_freq_nominal_parallel_all_powers_test",
        "freq/runFreqNominalParallelAllPowers.py",
    )
    monkeypatch.setattr(sys, "argv", ["runFreqNominalParallelAllPowers.py"])
    args = run_all.parse_args()
    assert args.freq_min == pytest.approx(1e-3)
    assert args.freq_max == pytest.approx(1e1)
    assert args.num_freq == 80


def test_parallel_reduced_csv_builds_variable_filter_simflag() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_filter_test",
        "freq/runFreqNominalParallel.py",
    )
    simflags = run_parallel.build_simflags(
        "powerLevel=1",
        simflags_extra="",
        reduced_csv_for_collect=True,
    )
    assert "-override=powerLevel=1" in simflags
    assert "-variableFilter=" in simflags
    assert "n_population" in simflags
    assert "-variableFilter='^(" in simflags


def test_compute_effective_stop_time_extends_only_slow_points() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_stop_time_test",
        "freq/runFreqNominalParallel.py",
    )
    assert run_parallel.compute_effective_stop_time(
        0.1,
        base_stop_time=10000.0,
        ss_time=2000.0,
        stop_time_mode="min_cycles_after_ss",
        min_cycles_after_ss=12.0,
    ) == pytest.approx(10000.0)
    assert run_parallel.compute_effective_stop_time(
        1e-3,
        base_stop_time=10000.0,
        ss_time=2000.0,
        stop_time_mode="min_cycles_after_ss",
        min_cycles_after_ss=12.0,
    ) == pytest.approx(77399.0)


def test_build_sin_mag_profile_caps_low_power_slow_bins() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_sin_mag_profile_test",
        "freq/runFreqNominalParallel.py",
    )
    freq_space = np.array([1e-3, 5e-3, 2e-2])
    mapping = run_parallel.build_sin_mag_profile(
        freq_space,
        base_sin_mag=10.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        apply_low_power_slowfreq_cap=True,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
    )
    assert mapping is not None
    assert mapping[1e-3] == pytest.approx(1.0)
    assert mapping[5e-3] == pytest.approx(1.0)
    assert mapping[2e-2] == pytest.approx(10.0)


def test_compute_number_of_intervals_frequency_scaled_caps_long_low_frequency_runs() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_output_grid_test",
        "freq/runFreqNominalParallel.py",
    )
    assert run_parallel.compute_number_of_intervals(
        1e-3,
        stop_time=4.0e7,
        output_interval_mode="frequency_scaled",
        output_intervals_per_second=10.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
    ) == 800000


def test_resolve_low_power_auto_protocol_matches_working_0p0001_case() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_auto_protocol_test",
        "freq/runFreqNominalParallel.py",
    )
    resolved = run_parallel.resolve_low_power_auto_protocol(
        power=1e-4,
        low_power_threshold=1e-2,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        ss_time=2000.0,
        stop_time=10000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
    )
    assert resolved["ss_time"] == pytest.approx(4.0e6)
    assert resolved["stop_time"] == pytest.approx(4.05e6)
    assert resolved["stop_time_mode"] == "min_cycles_after_ss"
    assert resolved["min_cycles_after_ss"] == pytest.approx(12.0)
    assert resolved["output_interval_mode"] == "frequency_scaled"
    assert resolved["auto_time_horizon_applied"] is True
    assert resolved["auto_output_grid_applied"] is True


def test_resolve_low_power_auto_protocol_can_be_disabled() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_auto_protocol_disable_test",
        "freq/runFreqNominalParallel.py",
    )
    resolved = run_parallel.resolve_low_power_auto_protocol(
        power=1e-4,
        low_power_threshold=1e-2,
        disable_low_power_auto_time_horizon=True,
        disable_low_power_auto_output_grid=True,
        ss_time=2000.0,
        stop_time=10000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
    )
    assert resolved["ss_time"] == pytest.approx(2000.0)
    assert resolved["stop_time"] == pytest.approx(10000.0)
    assert resolved["stop_time_mode"] == "fixed"
    assert resolved["output_interval_mode"] == "fixed_rate"
    assert resolved["auto_time_horizon_applied"] is False
    assert resolved["auto_output_grid_applied"] is False


def test_should_apply_low_power_slowfreq_sin_mag_cap_matches_default_0p01_case() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_sin_mag_cap_test",
        "freq/runFreqNominalParallel.py",
    )
    assert run_parallel.should_apply_low_power_slowfreq_sin_mag_cap(
        power=1e-2,
        low_power_threshold=1e-2,
        sin_mag_auto=True,
        sin_mag_logscale=False,
        disable_low_power_slowfreq_sin_mag_cap=False,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
    )
    assert not run_parallel.should_apply_low_power_slowfreq_sin_mag_cap(
        power=0.1,
        low_power_threshold=1e-2,
        sin_mag_auto=True,
        sin_mag_logscale=False,
        disable_low_power_slowfreq_sin_mag_cap=False,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
    )


def test_should_apply_low_power_allfreq_sin_mag_cap_matches_default_0p001_case() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_allfreq_sin_mag_cap_test",
        "freq/runFreqNominalParallel.py",
    )
    assert run_parallel.should_apply_low_power_allfreq_sin_mag_cap(
        power=1e-3,
        sin_mag_auto=True,
        sin_mag_logscale=False,
        disable_low_power_allfreq_sin_mag_cap=False,
        low_power_allfreq_sin_mag_cap=1.0,
        low_power_allfreq_sin_mag_cap_power=1e-3,
    )
    assert not run_parallel.should_apply_low_power_allfreq_sin_mag_cap(
        power=1e-2,
        sin_mag_auto=True,
        sin_mag_logscale=False,
        disable_low_power_allfreq_sin_mag_cap=False,
        low_power_allfreq_sin_mag_cap=1.0,
        low_power_allfreq_sin_mag_cap_power=1e-3,
    )


def test_parallel_reuse_path_trims_existing_csv_when_reduced_output_requested(
    tmp_path: Path,
) -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_reuse_test",
        "freq/runFreqNominalParallel.py",
    )
    work_path = tmp_path / frequency_case_dir_name(0.1)
    work_path.mkdir()
    csv_path = work_path / f"{frequency_file_prefix(0.1)}_res.csv"
    csv_path.write_text(
        "time,core1R.mpke.n_population.n,other\n0,1,9\n1,2,8\n",
        encoding="utf-8",
    )
    run_parallel.csv_reaches_stop_time = lambda **_: True

    result = run_parallel.run_single_freq(
        freq_point=0.1,
        base_dir=str(tmp_path),
        model_name="MSRR.Dummy",
        power=1.0,
        sin_mag=10.0,
        ss_time=100.0,
        stop_time=1.0,
        number_of_intervals=10,
        steady_state_overrides=None,
        forcing_time_step=0.0,
        low_power_mixed_forcing_step=False,
        low_power_forcing_step=0.0,
        low_power_forcing_step_hifreq=0.0,
        low_power_hifreq_split=0.0,
        smd_library="SMD_MSR_Modelica.mo",
        msrr_model="MSRR.mo",
        smd_library_src=str(tmp_path / "unused_SMD_MSR_Modelica.mo"),
        msrr_model_src=str(tmp_path / "unused_MSRR.mo"),
        allow_reuse=True,
        cleanup_omc_artifacts=False,
        reduced_csv_for_collect=True,
        simflags_extra="",
    )

    assert result["success"] is True
    assert result["warning"] == "existing result reused"
    assert csv_path.read_text(encoding="utf-8").splitlines()[0] == (
        "time,core1R.mpke.n_population.n"
    )


def test_parallel_run_single_freq_applies_explicit_forcing_time_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_forcing_step_test",
        "freq/runFreqNominalParallel.py",
    )

    from helpers.plant_config import write_stub_plant_data

    smd_src = tmp_path / "SMD_MSR_Modelica.mo"
    msrr_src = tmp_path / "MSRR.mo"
    smd_src.write_text("// dummy\n", encoding="utf-8")
    msrr_src.write_text("// dummy\n", encoding="utf-8")
    write_stub_plant_data(tmp_path)

    class DummyCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text, timeout=None):
        csv_path = Path(cwd) / f"{frequency_file_prefix(0.1)}_res.csv"
        csv_path.write_text(
            "time,npopulationn\n0.0,1.0\n1.0,1.0\n",
            encoding="utf-8",
        )
        return DummyCompleted()

    monkeypatch.setattr(run_parallel.subprocess, "run", fake_run)
    monkeypatch.setattr(run_parallel, "csv_reaches_stop_time", lambda **_: True)

    result = run_parallel.run_single_freq(
        freq_point=0.1,
        base_dir=str(tmp_path),
        model_name="MSRR.Dummy",
        power=1.0,
        sin_mag=1.0,
        ss_time=20.0,
        stop_time=40.0,
        number_of_intervals=100,
        steady_state_overrides=None,
        forcing_time_step=0.001,
        low_power_mixed_forcing_step=False,
        low_power_forcing_step=0.0,
        low_power_forcing_step_hifreq=0.0,
        low_power_hifreq_split=0.0,
        smd_library="SMD_MSR_Modelica.mo",
        msrr_model="MSRR.mo",
        smd_library_src=str(smd_src),
        msrr_model_src=str(msrr_src),
        allow_reuse=False,
        cleanup_omc_artifacts=False,
        reduced_csv_for_collect=False,
        simflags_extra="",
    )

    assert result["success"] is True
    assert result["forcing_step"] == pytest.approx(0.001)
    run_script = (
        tmp_path / frequency_case_dir_name(0.1) / "runModelica.mos"
    ).read_text(encoding="utf-8")
    assert "forcingTimeStep=0.001" in run_script


def test_validate_args_parallel_rejects_invalid_frequency_range() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_test_bad",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1.0,
        freq_min=1.0,
        freq_max=1.0,
        num_freq=64,
        stop_time=1000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        output_intervals_per_second=10.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
        ss_time=100.0,
        forcing_time_step=0.0,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
        n_jobs=4,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
    )
    with pytest.raises(ValueError, match="--freq_max must be greater than --freq_min"):
        run_parallel.validate_args(args)


def test_validate_args_parallel_allows_single_frequency_exact_bounds() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_test_exact_single",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1e-3,
        freq_min=1e-1,
        freq_max=1e-1,
        num_freq=1,
        stop_time=10000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_intervals_per_second=10.0,
        output_interval_mode="fixed_rate",
        output_samples_per_period=6.0,
        output_step_max=50.0,
        n_jobs=4,
        forcing_time_step=0.0,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
        ss_time=2000.0,
        disable_low_power_tweaks=False,
        low_power_threshold=0.01,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=50000.0,
        low_power_auto_min_cycles_after_ss=12.0,
        low_power_allfreq_sin_mag_cap=1.0,
        low_power_allfreq_sin_mag_cap_power=1e-3,
        disable_low_power_allfreq_sin_mag_cap=False,
        disable_low_power_slowfreq_sin_mag_cap=False,
        heat_loss=0,
    )

    run_parallel.validate_args(args)


def test_validate_args_parallel_rejects_nonpositive_dynamic_cycles() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_test_dynamic_bad",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1e-3,
        freq_min=1e-3,
        freq_max=1e1,
        num_freq=80,
        stop_time=10000.0,
        stop_time_mode="min_cycles_after_ss",
        min_cycles_after_ss=0.0,
        output_interval_mode="fixed_rate",
        output_intervals_per_second=10.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
        ss_time=2000.0,
        forcing_time_step=0.0,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
        n_jobs=4,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
    )
    with pytest.raises(
        ValueError,
        match="--min_cycles_after_ss must be > 0 when dynamic stop time is used",
    ):
        run_parallel.validate_args(args)


def test_validate_args_parallel_rejects_nonpositive_output_interval_rate() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_output_rate_bad",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1e-3,
        freq_min=1e-3,
        freq_max=1e1,
        num_freq=80,
        stop_time=10000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        output_intervals_per_second=0.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
        ss_time=2000.0,
        forcing_time_step=0.0,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=400.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
        n_jobs=4,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
    )
    with pytest.raises(
        ValueError,
        match="--output_intervals_per_second must be > 0",
    ):
        run_parallel.validate_args(args)


def test_validate_args_parallel_rejects_invalid_low_power_auto_ss_factor() -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_auto_ss_factor_bad",
        "freq/runFreqNominalParallel.py",
    )
    args = argparse.Namespace(
        power=1e-3,
        freq_min=1e-3,
        freq_max=1e1,
        num_freq=80,
        stop_time=10000.0,
        stop_time_mode="fixed",
        min_cycles_after_ss=12.0,
        output_interval_mode="fixed_rate",
        output_intervals_per_second=10.0,
        output_samples_per_period=6.0,
        output_step_max=50.0,
        ss_time=2000.0,
        forcing_time_step=0.0,
        disable_low_power_auto_time_horizon=False,
        disable_low_power_auto_output_grid=False,
        low_power_auto_ss_time_factor=0.0,
        low_power_auto_stop_tail_max=5.0e4,
        low_power_auto_min_cycles_after_ss=12.0,
        n_jobs=4,
        sin_mag=1.0,
        sin_mag_auto=False,
        sin_mag_ref=0.1,
        sin_mag_min=1.0,
        sin_mag_max=20.0,
        sin_mag_logscale=False,
        sin_mag_low=10.0,
        sin_mag_high=1.0,
        low_power_slowfreq_sin_mag_cap=1.0,
        low_power_slowfreq_sin_mag_cap_freq=1e-2,
        low_power_hifreq_split=1.0,
        low_power_nfloor_pre=1e-8,
        low_power_nfloor_forcing=1e-9,
    )
    with pytest.raises(
        ValueError,
        match="--low_power_auto_ss_time_factor must be > 0",
    ):
        run_parallel.validate_args(args)


def test_serial_main_forwards_validation_to_shared_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deprecated serial entry point must route through the shared engine."""

    run_serial = _load_module(
        "run_freq_nominal_test_bad",
        "freq/runFreqNominal.py",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runFreqNominal.py",
            "--ss_time",
            "100",
            "--stop_time",
            "50",
        ],
    )
    with pytest.raises(ValueError, match="--ss_time must be smaller than --stop_time"):
        run_serial.main()


def test_setpoint_generators_include_long_horizon_override_for_1r_0p001() -> None:
    setpoint_gen = _load_module(
        "generate_setpoint_table_test",
        "core/init/generateSetpointTable.py",
    )
    setpoint_gen_cont = _load_module(
        "generate_setpoint_table_continuation_test",
        "core/init/generateSetpointTableContinuation.py",
    )

    assert setpoint_gen.STOP_TIME_OVERRIDES["1r"]["0p00100"] == pytest.approx(1.0e6)
    assert setpoint_gen.NUMBER_OF_INTERVALS_OVERRIDES["1r"]["0p00100"] == 50_000
    assert setpoint_gen_cont.STOP_TIME_OVERRIDES["1r"]["0p00100"] == pytest.approx(1.0e6)


def test_watch_omc_gw_collect_uses_latest_submission_for_duplicate_case(
    tmp_path: Path,
) -> None:
    """Job re-submissions dedupe, replacement submissions do not.

    The watch key is ``(core, power_tag, base_dir, job_id)``: an appended
    re-submission with the same ``job_id`` keeps deduping to the latest
    entry, while a retried/replacement submission (same ``core`` /
    ``power_tag`` / ``base_dir``, new ``job_id``) forms a fresh watch key
    beside the earlier job.
    """
    watcher = _load_module(
        "watch_omc_gw_collect_duplicate_case_test",
        "freq/watchOmcGwCollect.py",
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "job_id": "job-old",
                        "core": "1r",
                        "power": 1e-5,
                        "power_tag": "0p00001",
                        "base_dir": "freq/results/foo",
                    }
                ),
                json.dumps(
                    {
                        "job_id": "job-keep",
                        "core": "9r",
                        "power": 1e-5,
                        "power_tag": "0p00001",
                        "base_dir": "freq/results/bar",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    second.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        # Replacement submission: same (core, power_tag,
                        # base_dir) as job-old but a fresh job_id, so it
                        # must form its own watch key instead of being
                        # merged with the earlier job.
                        "job_id": "job-new",
                        "core": "1r",
                        "power": 1e-5,
                        "power_tag": "0p00001",
                        "base_dir": "freq/results/foo",
                    }
                ),
                json.dumps(
                    {
                        # Re-append of the same job_id: still deduplicated,
                        # latest entry wins.
                        "job_id": "job-keep",
                        "core": "9r",
                        "power": 2e-5,
                        "power_tag": "0p00001",
                        "base_dir": "freq/results/bar",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cases = watcher.load_watch_cases([str(first), str(second)])
    assert len(cases) == 3
    assert cases[("1r", "0p00001", "freq/results/foo", "job-old")].job_id == "job-old"
    assert cases[("1r", "0p00001", "freq/results/foo", "job-new")].job_id == "job-new"
    kept = cases[("9r", "0p00001", "freq/results/bar", "job-keep")]
    assert kept.job_id == "job-keep"
    assert kept.power == pytest.approx(2e-5)


def test_watch_omc_gw_collect_classifies_terminal_incomplete_case_as_blocked() -> None:
    watcher = _load_module(
        "watch_omc_gw_collect_blocked_case_test",
        "freq/watchOmcGwCollect.py",
    )
    assert (
        watcher.classify_case(
            {"state": "FAILED"},
            {"complete_count": 44, "expected_count": 60},
        )
        == "blocked"
    )


def test_watch_omc_gw_collect_prefers_complete_data_over_failed_job_state() -> None:
    watcher = _load_module(
        "watch_omc_gw_collect_ready_case_test",
        "freq/watchOmcGwCollect.py",
    )
    assert (
        watcher.classify_case(
            {"state": "FAILED"},
            {"complete_count": 60, "expected_count": 60},
        )
        == "ready"
    )


def test_watch_omc_gw_collect_extracts_stop_time_failure_hint() -> None:
    watcher = _load_module(
        "watch_omc_gw_collect_failure_hint_test",
        "freq/watchOmcGwCollect.py",
    )
    hint = watcher.extract_failure_hint(
        {
            "stdout_tail": [
                "Results are in: /tmp/out\n",
                "freq = 10.00000 rad/s - simulation output did not reach requested stop_time\n",
            ]
        }
    )
    assert hint == "freq = 10.00000 rad/s - simulation output did not reach requested stop_time"


def test_serial_main_delegates_to_parallel_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial main must compute intervals via the shared parallel engine."""

    run_serial = _load_module(
        "run_freq_nominal_serial_main_test",
        "freq/runFreqNominal.py",
    )
    run_parallel = _load_module(
        "run_freq_nominal_serial_main_engine_test",
        "freq/runFreqNominalParallel.py",
    )

    from helpers.plant_config import write_stub_plant_data

    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "SMD_MSR_Modelica.mo").write_text("// dummy\n", encoding="utf-8")
    (core_dir / "MSRR.mo").write_text("// dummy\n", encoding="utf-8")
    write_stub_plant_data(core_dir)
    base_dir = tmp_path / "results"

    captured: dict[str, float | int] = {}

    def fake_run_single_freq(**kwargs):
        captured["freq_point"] = float(kwargs["freq_point"])
        captured["number_of_intervals"] = int(kwargs["number_of_intervals"])
        return {"success": True, "warning": None, "error": None}

    monkeypatch.setattr(run_parallel, "run_single_freq", fake_run_single_freq)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runFreqNominal.py",
            "--core_model",
            "1r",
            "--core_dir",
            str(core_dir),
            "--freq_min",
            "0.1",
            "--freq_max",
            "0.2",
            "--num_freq",
            "1",
            "--stop_time",
            "100",
            "--ss_time",
            "10",
            "--output_interval_mode",
            "frequency_scaled",
            "--disable_steady_state_table",
            # 90 s of forcing cannot host the default settling discard
            # (FR protocol A1); this test covers the interval plumbing only.
            "--settle_rule",
            "none",
            "--base_dir",
            str(base_dir),
        ],
    )

    run_serial.main()

    assert captured["freq_point"] == pytest.approx(0.1)
    assert captured["number_of_intervals"] == 10


def test_run_single_freq_timeout_writes_omc_logs(tmp_path: Path, monkeypatch) -> None:
    run_parallel = _load_module(
        "run_freq_nominal_parallel_timeout_logs_test",
        "freq/runFreqNominalParallel.py",
    )

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd="omc",
            timeout=2.0,
            output="omc stdout",
            stderr="omc stderr",
        )

    monkeypatch.setattr(run_parallel.subprocess, "run", fake_run)
    monkeypatch.setattr(run_parallel, "copyfile", lambda *_args, **_kwargs: None)

    result = run_parallel.run_single_freq(
        freq_point=0.1,
        base_dir=str(tmp_path),
        model_name="MSRR.MSRRuhxNominalTrim",
        power=1.0,
        sin_mag=1.0,
        ss_time=10.0,
        stop_time=20.0,
        number_of_intervals=10,
        steady_state_overrides=None,
        forcing_time_step=0.0,
        low_power_mixed_forcing_step=False,
        low_power_forcing_step=0.0,
        low_power_forcing_step_hifreq=0.0,
        low_power_hifreq_split=1.0,
        smd_library="SMD_MSR_Modelica.mo",
        msrr_model="MSRR.mo",
        smd_library_src="unused.mo",
        msrr_model_src="unused.mo",
        allow_reuse=False,
        cleanup_omc_artifacts=False,
        reduced_csv_for_collect=False,
        omc_timeout_seconds=2.0,
    )

    work = tmp_path / frequency_case_dir_name(0.1)
    assert result["success"] is False
    assert "timed out" in (result["error"] or "")
    assert (work / "omc_stdout.log").read_text(encoding="utf-8") == "omc stdout"
    assert (work / "omc_stderr.log").read_text(encoding="utf-8") == "omc stderr"


# ---------------------------------------------------------------------------
# freq.verify_campaign split-field semantics (TASK-20260908-01 P1)
# ---------------------------------------------------------------------------

#: Two-point hermetic sweep definition. The case CSVs are provenance-valid
#: but carry no fitted signal: ``audit_case`` revalidates shape/provenance
#: only, and the numerical-quality decision statistic is read from the
#: published aggregate (fully controlled by the tests).
_VC_FREQS = (0.1, 0.2)
_VC_STOP_TIME = 200.0
_VC_SS_TIME = 100.0
_VC_SIN_MAG = 1.0
_VC_POWER = 1.0
_VC_INTERVALS = 20
_VC_ROWS = 21
_VC_LAUNCH = "2026-01-01T00:00:00+00:00"
_VC_OMC = "OpenModelica v1.24.0-vc-verify-stub"
_VC_PY = "3.12.x-vc-verify-stub"
_VC_COMMIT = "0123456789abcdeffedcba987654321001234567"
_VC_GIT_STUB = {"commit": _VC_COMMIT, "dirty": False, "detached": True}
# Collector revalidation criteria, restated (time column only, 0.5% slack).
_VC_REQUIRED_COLUMNS = ("time",)
_VC_STOP_SLACK_RATIO = 0.005


def _vc_write_sources(results_dir: Path) -> tuple[Path, Path]:
    smd = results_dir / "SMD_MSR_Modelica.mo"
    msrr = results_dir / "MSRR.mo"
    smd.write_text("// tiny fake SMD library\nmodel SMD end SMD;\n", encoding="utf-8")
    msrr.write_text("// tiny fake MSRR model\nmodel MSRR end MSRR;\n", encoding="utf-8")
    return smd, msrr


def _vc_case_csv() -> str:
    lines = ["time,npopulationn"]
    for i in range(_VC_ROWS):
        t = _VC_STOP_TIME * i / (_VC_ROWS - 1)
        lines.append(f"{t:.8f},{1.0:.16e}")
    return "\n".join(lines) + "\n"


def _vc_case_manifest(
    sources: tuple[Path, Path], freq_point: float, request_reference: dict
) -> dict:
    manifest = run_results.build_run_manifest(
        package_name="MSRR",
        model_name="MSRR.MSRRuhxNominalTrimNoTrips",
        source_files=list(sources),
        overrides={
            "powerLevel": _VC_POWER,
            "perturbationOmega": freq_point,
            "perturbationStartTime": _VC_SS_TIME,
        },
        solver="dassl",
        tolerance=1.0e-6,
        start_time=0.0,
        stop_time=_VC_STOP_TIME,
        number_of_intervals=_VC_INTERVALS,
        output_grid="equidistant",
        perturbation_amplitude=_VC_SIN_MAG,
        backend="local",
        omc_version=_VC_OMC,
        python_version=_VC_PY,
        git_info=_VC_GIT_STUB,
        workflow_version="freq-verify-campaign/test-fixtures",
        launch_timestamp=_VC_LAUNCH,
    )
    manifest["sweep_request"] = dict(request_reference)
    return manifest


def _vc_seed_verified_sweep(results_dir: Path) -> dict:
    """A fully provenanced two-point sweep: request manifest plus per-case
    CSVs and self-consistent manifest/validation sidecars, exactly the way
    the sweep runner writes them (validate first, then persist sidecars so
    ``result_sha256`` binds the verdict to the CSV bytes)."""
    results_dir.mkdir(parents=True, exist_ok=True)
    sources = _vc_write_sources(results_dir)
    cases = [
        {
            "frequency_key": format_frequency_key(float(fp)),
            "frequency_rad_s": float(fp),
            "perturbation_amplitude_pcm": _VC_SIN_MAG,
            "stop_time_s": _VC_STOP_TIME,
            "number_of_intervals": _VC_INTERVALS,
            "output_step_s": _VC_STOP_TIME / _VC_INTERVALS,
        }
        for fp in _VC_FREQS
    ]
    request = {
        "coordinate": {"name": "perturbationOmega", "unit": "rad/s"},
        "core_model": "1r",
        "package": "legacy",
        "package_name": "MSRR",
        "model_name": "MSRR.MSRRuhxNominalTrimNoTrips",
        "power": _VC_POWER,
        "perturbation_start_time_s": _VC_SS_TIME,
        "cases": cases,
    }
    payload = sweep_manifest.publish_sweep_request_manifest(results_dir, request)
    reference = sweep_manifest.case_reference(payload)
    for fp in _VC_FREQS:
        manifest = _vc_case_manifest(sources, float(fp), reference)
        work = results_dir / frequency_case_dir_name(float(fp))
        work.mkdir(parents=True, exist_ok=True)
        csv_path = work / f"{frequency_file_prefix(float(fp))}_res.csv"
        csv_path.write_text(_vc_case_csv(), encoding="utf-8")
        report = run_results.validate_result_csv(
            str(csv_path),
            required_columns=_VC_REQUIRED_COLUMNS,
            time_column="time",
            launch_timestamp=_VC_LAUNCH,
            requested_stop_time=_VC_STOP_TIME,
            stop_time_slack_s=_VC_STOP_SLACK_RATIO * _VC_STOP_TIME,
        )
        assert report.passed, report.failed_checks
        run_results.write_result_sidecars(str(csv_path), manifest, report)
    return payload


def _vc_publish_aggregate(results_dir: Path, r2_by_freq: dict[float, float]) -> None:
    """Publish a self-consistent aggregate (CSV + .m + manifest with
    matching output digests) carrying the given ``R_squared`` rows, so the
    numerical-quality decision statistic is fully controlled."""
    rows = "".join(
        f"{float(fp):g},{2.0:g},{-30.0:g},{r2_by_freq[float(fp)]:g}\n"
        for fp in _VC_FREQS
    )
    texts = {
        "FreqResponseResults.csv": "frequency_rad_s,gain,phase_deg,R_squared\n" + rows,
        "FreqResponseResults.m": "% synthetic aggregate (self-consistent)\n",
    }
    outputs = {}
    for name, text in texts.items():
        data = text.encode("utf-8")
        (results_dir / name).write_bytes(data)
        outputs[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    manifest = {
        "kind": "freq-collection",
        "schema_version": 1,
        "collection_status": "complete",
        "outputs": outputs,
    }
    (results_dir / "FreqResponseResults.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_verify_campaign_pass_band_split_fields(tmp_path: Path) -> None:
    results = tmp_path / "sweep"
    request = _vc_seed_verified_sweep(results)
    _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
    summary = verify_campaign.verify_campaign(str(results))
    assert summary["publication_eligible"] is True
    assert summary["provenance_complete"] is True
    assert summary["campaign_complete"] is True
    assert summary["numerical_quality_status"] == "pass"
    assert summary["numerical_quality_pass"] is True
    assert summary["publication_approved"] is True
    quality = summary["numerical_quality"]
    assert quality["min_r_squared"] == pytest.approx(0.98)
    assert quality["points"] == 2
    assert quality["thresholds"] == {"pass_at": 0.95, "waiver_floor": 0.80}
    assert quality["waiver"] is None
    assert quality["regime"] == "frequency"
    assert summary["expected"] == 2 and summary["accepted"] == 2
    assert summary["missing"] == []
    assert summary["rejected"] == []
    assert summary["unexpected"] == []
    assert summary["aggregate_problems"] == []
    # Provenance is bound to the sweep-request identity.
    reference = sweep_manifest.case_reference(request)
    assert summary["campaign"]["campaign_id"] == reference["campaign_id"]
    assert summary["campaign"]["fingerprint"] == reference["fingerprint"]


def test_verify_campaign_waiver_band_blocks_approval_not_legacy_eligibility(
    tmp_path: Path,
) -> None:
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    _vc_publish_aggregate(results, {0.1: 0.92, 0.2: 0.99})
    summary = verify_campaign.verify_campaign(str(results))
    # The legacy derived/provenance label is unchanged: the sweep ran
    # completely with complete provenance, so it stays publication_eligible.
    assert summary["publication_eligible"] is True
    assert summary["campaign_complete"] is True
    # Numerical quality is explicit: waiver band, and verify_campaign
    # passes no waiver records, so the point is traceable but not approved.
    assert summary["numerical_quality_status"] == "waiver"
    assert summary["numerical_quality_pass"] is False
    assert summary["publication_approved"] is False
    assert summary["numerical_quality"]["min_r_squared"] == pytest.approx(0.92)
    assert summary["numerical_quality"]["waiver"] is None


def test_verify_campaign_fail_band_reproduces_the_reviewed_0p4396_point(
    tmp_path: Path,
) -> None:
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    _vc_publish_aggregate(results, {0.1: 0.4396, 0.2: 0.99})
    summary = verify_campaign.verify_campaign(str(results))
    assert summary["publication_eligible"] is True
    assert summary["numerical_quality_status"] == "fail"
    assert summary["numerical_quality_pass"] is False
    assert summary["publication_approved"] is False
    assert summary["numerical_quality"]["min_r_squared"] == pytest.approx(0.4396)


def test_verify_campaign_missing_aggregate_is_not_applicable_and_incomplete(
    tmp_path: Path,
) -> None:
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    summary = verify_campaign.verify_campaign(str(results))
    assert summary["numerical_quality_status"] == "not_applicable"
    assert summary["numerical_quality_pass"] is None
    # A missing aggregate manifest is itself an aggregate problem: the
    # campaign is incomplete and NOT eligible (fail closed), so the
    # not-applicable quality can never mask a missing publication.
    assert summary["aggregate_problems"]
    assert summary["campaign_complete"] is False
    assert summary["publication_eligible"] is False
    assert summary["publication_approved"] is False


def test_verify_campaign_corrupt_aggregate_fails_loudly(tmp_path: Path) -> None:
    """TASK-20260920-01 P12: the QualityError raised by the aggregate fit
    read is CATCHED by verify_campaign and recorded as an explicit
    numerical-quality failure report (status ``fail``, ``pass`` False, the
    offending error text preserved) -- a verifier crash is not the contract,
    and the quality read must never evaluate as quality-less. Approval
    fails closed; campaign completeness is unaffected by the quality read.
    """
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    # A coherent publication whose aggregate lacks the fit-statistic
    # column: the quality read must fail loudly, never evaluate as
    # quality-less.
    csv_text = "frequency_rad_s,gain,phase_deg\n0.1,2.0,-30.0\n0.2,2.0,-30.0\n"
    m_text = "% synthetic aggregate\n"
    outputs = {}
    for name, text in (
        ("FreqResponseResults.csv", csv_text),
        ("FreqResponseResults.m", m_text),
    ):
        data = text.encode("utf-8")
        (results / name).write_bytes(data)
        outputs[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    (results / "FreqResponseResults.manifest.json").write_text(
        json.dumps(
            {
                "kind": "freq-collection",
                "schema_version": 1,
                "collection_status": "complete",
                "outputs": outputs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = verify_campaign.verify_campaign(str(results))
    # The QualityError is recorded, not propagated: the quality read
    # fails loudly with the offending aggregate named in the report.
    assert summary["numerical_quality_status"] == "fail"
    assert summary["numerical_quality_pass"] is False
    assert summary["numerical_quality"]["pass"] is False
    assert "R_squared" in summary["numerical_quality"]["error"]
    assert summary["publication_approved"] is False
    # Campaign completeness is unaffected by the aggregate quality read.
    assert summary["campaign_complete"] is True


def test_verify_campaign_exit_status_keys_on_legacy_eligibility(
    tmp_path: Path,
) -> None:
    """The CLI exit contract is unchanged: exit 0 keys on the legacy
    ``publication_eligible`` (provenance + campaign completeness), even
    when the explicit quality verdict is a fail and approval is False."""
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    _vc_publish_aggregate(results, {0.1: 0.4396, 0.2: 0.99})
    out = tmp_path / "verify.json"
    code = verify_campaign.main([str(results), "--output", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["publication_eligible"] is True
    assert payload["publication_approved"] is False


def test_verify_campaign_strict_exit_policy_keys_on_publication_approved(
    tmp_path: Path,
) -> None:
    """--exit_policy publication_approved does not overload publication_eligible."""
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    _vc_publish_aggregate(results, {0.1: 0.4396, 0.2: 0.99})
    out = tmp_path / "verify.json"
    code = verify_campaign.main(
        [str(results), "--output", str(out), "--exit_policy", "publication_approved"]
    )
    assert code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["publication_eligible"] is True
    assert payload["publication_approved"] is False
    code_ok = verify_campaign.main(
        [str(results), "--exit_policy", "legacy_eligible"]
    )
    assert code_ok == 0


def test_verify_campaign_incomplete_campaign_is_not_complete_or_eligible(
    tmp_path: Path,
) -> None:
    results = tmp_path / "sweep"
    _vc_seed_verified_sweep(results)
    # One verified case CSV vanishes after publication.
    (
        results
        / frequency_case_dir_name(0.1)
        / f"{frequency_file_prefix(0.1)}_res.csv"
    ).unlink()
    _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
    summary = verify_campaign.verify_campaign(str(results))
    assert summary["expected"] == 2 and summary["accepted"] == 1
    assert summary["missing"] == [format_frequency_key(0.1)]
    assert summary["campaign_complete"] is False
    assert summary["publication_eligible"] is False
    assert summary["publication_approved"] is False


def test_verify_campaign_requires_the_sweep_request_manifest(tmp_path: Path) -> None:
    with pytest.raises(sweep_manifest.SweepManifestError):
        verify_campaign.verify_campaign(str(tmp_path))


# ---------------------------------------------------------------------------
# freq.verify_campaign poison-data gate (TASK-20260912-01 P5)
# ---------------------------------------------------------------------------
#
# Publication approval must refuse poison-coupled results: per-case
# manifests that enable poison FEEDBACK from a dataset whose recorded
# ``poisonMaturity`` is outside the approved subset
# (``plant_config.POISON_MATURITY_APPROVED``), and must fail closed when a
# feedback manifest records no maturity label at all. Track-only manifests
# are development data, not poison-coupled results, and keep passing. The
# legacy ``publication_eligible`` field (and the CLI exit keyed on it) is
# untouched. These tests rewrite the per-case manifest sidecars through
# the canonical sidecar writer so the stored manifest fingerprint stays
# self-consistent (audit_case step 3 recomputes it from the manifest).

_VC_PLANT_RECORD = plant_config.poison_dataset_record("msrr")
_VC_PLANT_DATASET_ID = _VC_PLANT_RECORD["poisonDatasetId"]
_VC_UNAPPROVED_MATURITY = _VC_PLANT_RECORD["poisonMaturity"]
_VC_APPROVED_MATURITY = "reference"
assert _VC_UNAPPROVED_MATURITY not in plant_config.POISON_MATURITY_APPROVED
assert _VC_APPROVED_MATURITY in plant_config.POISON_MATURITY_APPROVED


def _vc_apply_poison_overrides(results_dir: Path, poison_overrides: dict) -> None:
    """Rewrite every per-case manifest sidecar with the given poison
    overrides, through the canonical sidecar writer (the stored manifest
    fingerprint is recomputed from the mutated manifest, so audit_case's
    fingerprint recheck stays consistent)."""
    for directory in sorted(results_dir.glob("freq*")):
        if not directory.is_dir():
            continue
        for csv_path in sorted(directory.glob("*_res.csv")):
            payload = run_results.read_manifest_sidecar(str(csv_path))
            assert payload is not None, csv_path
            manifest = payload["manifest"]
            manifest["overrides"].update(poison_overrides)
            run_results.write_manifest_sidecar(str(csv_path), manifest)


def _vc_feedback_overrides(maturity: object = _VC_UNAPPROVED_MATURITY) -> dict:
    """The poison provenance overrides exactly the freq runner records on a
    feedback-on full-power run (P5 development override included); pass
    ``maturity=None`` to record a feedback manifest with no label at all."""
    overrides = {
        "enablePoisonTracking": True,
        "enablePoisonFeedback": True,
        "poisonInitialization": "steady_state",
        "poisonDatasetId": _VC_PLANT_DATASET_ID,
        "poisonSourceDigest": _VC_PLANT_RECORD["poisonSourceDigest"],
        "allow_unreviewed_poison_data": True,
    }
    if maturity is not None:
        overrides["poisonMaturity"] = maturity
    return overrides


class TestVerifyCampaignPoisonDataGate:
    def test_feedback_on_unapproved_maturity_blocks_approval_only(
        self, tmp_path: Path
    ) -> None:
        """Green quality + complete provenance still refuse approval: the
        poison-data gate is the independent blocker, and the legacy
        ``publication_eligible`` label (and its exit key) is untouched."""
        results = tmp_path / "sweep"
        _vc_seed_verified_sweep(results)
        _vc_apply_poison_overrides(results, _vc_feedback_overrides())
        _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
        summary = verify_campaign.verify_campaign(str(results))
        assert summary["provenance_complete"] is True
        assert summary["campaign_complete"] is True
        assert summary["numerical_quality_status"] == "pass"
        assert summary["numerical_quality_pass"] is True
        assert summary["publication_approved"] is False
        # Legacy label unchanged: pending-review poison data is not a
        # provenance or completeness defect.
        assert summary["publication_eligible"] is True
        problems = summary["poison_data_problems"]
        assert len(problems) == len(_VC_FREQS)
        for problem in problems:
            assert problem["poison_dataset_id"] == _VC_PLANT_DATASET_ID
            assert problem["poison_maturity"] == _VC_UNAPPROVED_MATURITY
            assert "not in the approved set" in problem["reason"]
            assert problem["source_csv"].endswith("_res.csv")

    def test_feedback_on_missing_maturity_fails_closed(self, tmp_path: Path) -> None:
        """A feedback manifest recording no ``poisonMaturity`` label cannot
        be assumed benign: approval fails closed and names the gap."""
        results = tmp_path / "sweep"
        _vc_seed_verified_sweep(results)
        _vc_apply_poison_overrides(results, _vc_feedback_overrides(maturity=None))
        _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
        summary = verify_campaign.verify_campaign(str(results))
        assert summary["publication_approved"] is False
        assert summary["publication_eligible"] is True
        problems = summary["poison_data_problems"]
        assert len(problems) == len(_VC_FREQS)
        for problem in problems:
            assert problem["poison_maturity"] is None
            assert "records no poisonMaturity label" in problem["reason"]

    def test_feedback_on_reference_maturity_stays_approved(self, tmp_path: Path) -> None:
        """Positive control: the approved maturity label passes the gate,
        so the gate discriminates on the label, not on poison feedback
        itself."""
        results = tmp_path / "sweep"
        _vc_seed_verified_sweep(results)
        _vc_apply_poison_overrides(
            results, _vc_feedback_overrides(maturity=_VC_APPROVED_MATURITY)
        )
        _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
        summary = verify_campaign.verify_campaign(str(results))
        assert summary["poison_data_problems"] == []
        assert summary["publication_approved"] is True
        assert summary["publication_eligible"] is True

    def test_track_only_poison_manifests_keep_passing(self, tmp_path: Path) -> None:
        """Track-only (no feedback) manifests are development data, not
        poison-coupled results: unapproved maturity must not block them."""
        results = tmp_path / "sweep"
        _vc_seed_verified_sweep(results)
        _vc_apply_poison_overrides(
            results,
            {
                "enablePoisonTracking": True,
                "enablePoisonFeedback": False,
                "poisonInitialization": "steady_state",
                "poisonDatasetId": _VC_PLANT_DATASET_ID,
                "poisonMaturity": _VC_UNAPPROVED_MATURITY,
                "poisonSourceDigest": _VC_PLANT_RECORD["poisonSourceDigest"],
            },
        )
        _vc_publish_aggregate(results, {0.1: 0.99, 0.2: 0.98})
        summary = verify_campaign.verify_campaign(str(results))
        assert summary["poison_data_problems"] == []
        assert summary["publication_approved"] is True
        assert summary["publication_eligible"] is True

    def test_exit_policy_publication_approved_keys_on_the_poison_gate(
        self, tmp_path: Path
    ) -> None:
        """CLI: ``--exit_policy publication_approved`` exits 1 for a
        poison-blocked campaign (quality green, provenance complete) and 0
        for the clean campaign; the legacy policy still exits 0 on the
        blocked campaign (``publication_eligible`` is untouched)."""
        blocked = tmp_path / "blocked"
        _vc_seed_verified_sweep(blocked)
        _vc_apply_poison_overrides(blocked, _vc_feedback_overrides())
        _vc_publish_aggregate(blocked, {0.1: 0.99, 0.2: 0.98})
        assert (
            verify_campaign.main(
                [str(blocked), "--exit_policy", "publication_approved"]
            )
            == 1
        )
        assert verify_campaign.main([str(blocked)]) == 0

        clean = tmp_path / "clean"
        _vc_seed_verified_sweep(clean)
        _vc_publish_aggregate(clean, {0.1: 0.99, 0.2: 0.98})
        assert (
            verify_campaign.main(
                [str(clean), "--exit_policy", "publication_approved"]
            )
            == 0
        )


# ---------------------------------------------------------------------------
# TASK-20260920-01 P21 (rev021): freq workflow hardening pins.
#
# - freq.runFreqNominal strips the user --n_jobs in every form before the
#   delegated call; the delegated argv carries --n_jobs 1 exactly once.
# - freq.plotFreqFits: --dpi default AND --help text agree (default: 600);
#   with --fit_trend the SciPy comparison model carries the same centered
#   linear trend term as the primary fit; a missing/rejected fit exits
#   main() nonzero.
# - freq.plotFreqTimeCompareCoreModels: read_time_trace resolves time/power
#   by HEADER name (not column 1); read_fit_summary refuses a nearest
#   aggregate beyond FIT_FREQ_REL_TOL = 1e-3 relative.
# ---------------------------------------------------------------------------

os.environ.setdefault("MPLBACKEND", "Agg")  # headless plotter imports below


def _load_run_freq_nominal(tag: str):
    return _load_module(f"runFreqNominal_{tag}", "freq/runFreqNominal.py")


def _load_plot_freq_fits(tag: str):
    return _load_module(f"plotFreqFits_{tag}", "freq/plotFreqFits.py")


def _load_time_compare(tag: str):
    return _load_module(
        f"plotFreqTimeCompareCoreModels_{tag}",
        "freq/plotFreqTimeCompareCoreModels.py",
    )


def test_run_freq_nominal_strip_n_jobs_drops_every_user_form() -> None:
    """The serial shim must never forward a user-supplied n_jobs value
    (argparse last-one-wins ambiguity, or a silent re-parallelization)."""
    mod = _load_run_freq_nominal("p21_strip")
    assert mod._strip_n_jobs(["--n_jobs", "8", "--power", "1.0"]) == [
        "--power",
        "1.0",
    ]
    assert mod._strip_n_jobs(["--power", "1.0", "--n_jobs=8"]) == [
        "--power",
        "1.0",
    ]
    # Both forms in one argv: every user value is gone.
    assert mod._strip_n_jobs(
        ["--n_jobs", "4", "--n_jobs=2", "--freq_min", "0.1"]
    ) == ["--freq_min", "0.1"]
    # A trailing bare flag with no value still drops cleanly.
    assert mod._strip_n_jobs(["--n_jobs"]) == []
    # An unrelated flag that merely CONTAINS the text survives untouched.
    assert mod._strip_n_jobs(["--n_jobs_max", "8"]) == ["--n_jobs_max", "8"]
    # rev022 M-3: argparse (allow_abbrev, the default) resolves every
    # unambiguous prefix of --n_jobs to it, so EVERY prefix form -- with or
    # without "=VALUE" -- is a user n_jobs form the serial shim must strip
    # (a survivor would re-parallelize a documented-serial run).
    for prefix in ("--n", "--n_", "--n_j", "--n_jo", "--n_job"):
        assert mod._strip_n_jobs([prefix, "8", "--power", "1.0"]) == [
            "--power",
            "1.0",
        ], prefix
        assert mod._strip_n_jobs([f"{prefix}=8", "--power", "1.0"]) == [
            "--power",
            "1.0",
        ], prefix
    # The other --n-prefixed option is unrelated and survives.
    assert mod._strip_n_jobs(["--num_freq", "0.1"]) == ["--num_freq", "0.1"]


def test_run_freq_nominal_main_delegates_serial_with_exactly_one_n_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_run_freq_nominal("p21_delegate")
    captured: dict[str, list[str]] = {}

    def fake_parallel_main(argv: list[str]) -> None:
        captured["argv"] = list(argv)

    monkeypatch.setattr(mod, "_parallel_main", fake_parallel_main)
    monkeypatch.setattr(
        sys, "argv", ["runFreqNominal.py", "--power", "1.0", "--n_jobs", "8"]
    )
    mod.main()
    argv = captured["argv"]
    assert argv.count("--n_jobs") == 1
    assert argv[argv.index("--n_jobs") + 1] == "1"
    assert "--n_jobs=8" not in argv


def test_plot_freq_fits_dpi_default_and_help_text_agree(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The original bug was help/default drift (default 600 vs
    "default: 150" text); both sides are pinned in one test."""
    mod = _load_plot_freq_fits("p21_dpi")
    monkeypatch.setattr(sys, "argv", ["plotFreqFits.py", "--results_dir", "r"])
    assert mod.parse_args().dpi == 600
    monkeypatch.setattr(sys, "argv", ["plotFreqFits.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        mod.parse_args()
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "default: 600" in help_text
    assert "default: 150" not in help_text


def _seed_single_freq_case(
    tmp_path: Path, freq: float, *, trend: float
) -> tuple[Path, np.ndarray, np.ndarray]:
    results = tmp_path / "results"
    case = results / frequency_case_dir_name(freq)
    case.mkdir(parents=True)
    time = np.arange(0.0, 100.0 + 1e-9, 0.5)
    power = 1.0 + trend * time + 0.2 * np.sin(freq * time + 0.3)
    csv_path = case / f"MSRR_{case.name}_res.csv"
    with csv_path.open("w", encoding="utf-8") as handle:
        handle.write("time,pk.enPopulationN\n")
        for t, p in zip(time, power):
            handle.write(f"{t:.12g},{p:.12g}\n")
    return results, time, power


def test_plot_freq_fits_scipy_comparison_carries_the_trend_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --fit_trend the SciPy comparison fits the IDENTICAL model as the
    primary solver: sine + a linear trend centered on the same t_c (the
    window midpoint of the primary fit)."""
    mod = _load_plot_freq_fits("p21_trend")
    freq = 0.1
    results, time, power = _seed_single_freq_case(tmp_path, freq, trend=0.001)
    calls: dict = {}

    def fake_curve_fit(model, t, y, *, p0, maxfev):
        calls["model"] = model
        calls["p0"] = list(p0)
        popt = np.array([0.2, 0.3, 1.0, 0.001])
        pcov = np.zeros((4, 4))
        return popt, pcov

    monkeypatch.setattr(mod, "curve_fit", fake_curve_fit)
    ok = mod.plot_single_freq(
        str(results),
        str(tmp_path / "plots"),
        freq,
        0.0,
        None,
        False,
        600,
        fit_trend=True,
    )
    assert ok is True
    model = calls["model"]
    assert model.__code__.co_argcount == 5, (
        "the comparison model lost the trend parameter "
        "(expected t + amplitude, phase, offset, c1)"
    )
    primary = mod.fit_sine_least_squares(time, power, freq, fit_trend=True)
    t_c = 0.5 * (primary.fit_start + primary.fit_end)
    a, ph, off, c1 = (0.2, 0.3, 1.0, 0.001)
    sample = np.array([0.0, 10.0, 50.0, 90.0])
    expected = off + c1 * (sample - t_c) + a * np.sin(freq * sample + ph)
    np.testing.assert_allclose(model(sample, a, ph, off, c1), expected)
    assert len(calls["p0"]) == 4, "the trend fit must be seeded with four params"


def test_plot_freq_fits_scipy_comparison_trend_free_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --fit_trend the comparison stays the bare sine (three
    parameters); the trend term is not silently added."""
    mod = _load_plot_freq_fits("p21_no_trend")
    freq = 0.1
    results, time, power = _seed_single_freq_case(tmp_path, freq, trend=0.0)
    calls: dict = {}

    def fake_curve_fit(model, t, y, *, p0, maxfev):
        calls["model"] = model
        calls["p0"] = list(p0)
        popt = np.array([0.2, 0.3, 1.0])
        pcov = np.zeros((3, 3))
        return popt, pcov

    monkeypatch.setattr(mod, "curve_fit", fake_curve_fit)
    ok = mod.plot_single_freq(
        str(results), str(tmp_path / "plots"), freq, 0.0, None, False, 600
    )
    assert ok is True
    model = calls["model"]
    assert model.__code__.co_argcount == 4
    a, ph, off = (0.2, 0.3, 1.0)
    sample = np.array([0.0, 10.0, 50.0])
    np.testing.assert_allclose(
        model(sample, a, ph, off), off + a * np.sin(freq * sample + ph)
    )
    assert len(calls["p0"]) == 3


def test_plot_freq_fits_main_exits_nonzero_when_the_fit_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing/rejected fit is a FAILURE of the plotting run, not a
    printed note: main() exits with status 1 (both entry shapes)."""
    mod = _load_plot_freq_fits("p21_exit")
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        ["plotFreqFits.py", "--results_dir", str(results), "--freq", "0.1"],
    )
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    monkeypatch.setattr(
        sys, "argv", ["plotFreqFits.py", "--results_dir", str(results), "--all"]
    )
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert "No frequency folders" in str(exc.value)


def test_time_compare_fit_freq_tolerance_constant_pinned() -> None:
    mod = _load_time_compare("p21_tol_const")
    assert mod.FIT_FREQ_REL_TOL == 1e-3


def _fit_summary_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "FreqResponseResults.csv").write_text(
        "frequency_rad_s,sin_mag,cos_mag\n"
        "0.1,2.0,0.0\n"
        "0.2,2.1,0.1\n"
        "0.3,1.9,0.2\n",
        encoding="utf-8",
    )
    return case_dir


def test_time_compare_read_fit_summary_accepts_within_tolerance(
    tmp_path: Path,
) -> None:
    mod = _load_time_compare("p21_tol_accept")
    case_dir = _fit_summary_case(tmp_path)
    row = mod.read_fit_summary(case_dir, 0.10005)
    assert float(row["frequency_rad_s"]) == pytest.approx(0.1)


def test_time_compare_read_fit_summary_refuses_out_of_tolerance(
    tmp_path: Path,
) -> None:
    """A requested aggregate that is NOT within the relative tolerance of
    any available row is an error -- no silent nearest-match substitution of
    a different forcing frequency's fit."""
    mod = _load_time_compare("p21_tol_refuse")
    case_dir = _fit_summary_case(tmp_path)
    with pytest.raises(ValueError, match="relative deviation"):
        mod.read_fit_summary(case_dir, 0.15)


def test_time_compare_read_time_trace_resolves_columns_by_header(
    tmp_path: Path,
) -> None:
    """Header-name resolution, not column 1: the power column sits FIRST
    and the time column second."""
    mod = _load_time_compare("p21_cols")
    csv_path = tmp_path / "trace.csv"
    csv_path.write_text(
        "pk.enPopulationN,time,other\n"
        "1.0,0.0,9.0\n"
        "1.1,0.5,9.1\n"
        "1.2,1.0,9.2\n",
        encoding="utf-8",
    )
    time, power = mod.read_time_trace(csv_path)
    np.testing.assert_allclose(time, [0.0, 0.5, 1.0])
    np.testing.assert_allclose(power, [1.0, 1.1, 1.2])


def test_time_compare_read_time_trace_missing_columns_refused(
    tmp_path: Path,
) -> None:
    mod = _load_time_compare("p21_cols_missing")
    no_time = tmp_path / "no_time.csv"
    no_time.write_text(
        "pk.enPopulationN,other\n1.0,2.0\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Missing time column"):
        mod.read_time_trace(no_time)
    no_power = tmp_path / "no_power.csv"
    no_power.write_text("time,other\n0.0,2.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing power column"):
        mod.read_time_trace(no_power)

