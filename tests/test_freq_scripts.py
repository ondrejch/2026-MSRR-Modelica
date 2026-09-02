#!/usr/bin/env python3
"""Unit tests for frequency workflow helper logic."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
from pathlib import Path
import sys

import numpy as np
import pytest


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

    work_path = tmp_path / f"freq{freq_point:08.5f}"
    work_path.mkdir()
    csv_path = work_path / f"MSRR_freq{freq_point:08.5f}_res.csv"

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
    work_path = tmp_path / f"freq{freq_point:08.5f}"
    work_path.mkdir()
    csv_path = work_path / f"MSRR_freq{freq_point:08.5f}_res.csv"
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
    work_path = tmp_path / f"freq{freq_point:08.5f}"
    work_path.mkdir()
    csv_path = work_path / f"MSRR_freq{freq_point:08.5f}_res.csv"
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
    work_path = tmp_path / "freq00.10000"
    work_path.mkdir()
    csv_path = work_path / "MSRR_freq00.10000_res.csv"
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

    smd_src = tmp_path / "SMD_MSR_Modelica.mo"
    msrr_src = tmp_path / "MSRR.mo"
    smd_src.write_text("// dummy\n", encoding="utf-8")
    msrr_src.write_text("// dummy\n", encoding="utf-8")

    class DummyCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text, timeout=None):
        csv_path = Path(cwd) / "MSRR_freq00.10000_res.csv"
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
        tmp_path / "freq00.10000" / "runModelica.mos"
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
        json.dumps(
            {
                "job_id": "job-new",
                "core": "1r",
                "power": 1e-5,
                "power_tag": "0p00001",
                "base_dir": "freq/results/foo",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cases = watcher.load_watch_cases([str(first), str(second)])
    assert len(cases) == 2
    assert cases[("1r", "0p00001", "freq/results/foo")].job_id == "job-new"
    assert cases[("9r", "0p00001", "freq/results/bar")].job_id == "job-keep"


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

    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "SMD_MSR_Modelica.mo").write_text("// dummy\n", encoding="utf-8")
    (core_dir / "MSRR.mo").write_text("// dummy\n", encoding="utf-8")
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

    work = tmp_path / "freq00.10000"
    assert result["success"] is False
    assert "timed out" in (result["error"] or "")
    assert (work / "omc_stdout.log").read_text(encoding="utf-8") == "omc stdout"
    assert (work / "omc_stderr.log").read_text(encoding="utf-8") == "omc stderr"

