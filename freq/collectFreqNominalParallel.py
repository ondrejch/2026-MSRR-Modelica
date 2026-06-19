#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Collect and analyze frequency response results from the nominal MSRR model.
Parallel version using thread pools for curve fitting across frequency points.

Usage:
    python collectFreqNominalParallel.py [--results_dir RESULTS_DIR] [--plot]
                                        [--n_jobs N_JOBS]
"""

import argparse
import re
import numpy as np
import pandas as pd
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from .paths import default_freq_case_dir
except ImportError:
    from paths import default_freq_case_dir


def default_cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def format_frequency_key(freq: float) -> str:
    """Format a frequency key exactly as persisted by the runner."""
    return f"{float(freq):.12g}"


def parse_args():
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Collect MSRR frequency response results and compute Bode plot data (parallel)."
    )
    parser.add_argument(
        "--results_dir",
        type=str,
        default=None,
        help=(
            "Path to the frequency response results directory "
            "(default: 00runs/freq/<core_model>/power_<tag>)"
        ),
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Core model used to derive default results_dir",
    )
    parser.add_argument(
        "--power",
        type=float,
        default=1.0,
        help="Power level used to derive default results_dir",
    )
    parser.add_argument("--plot", action="store_true",
                        help="Generate a Bode plot using matplotlib")
    parser.add_argument("--n_jobs", type=int, default=default_cpu_count(),
                        help=f"Number of parallel fitting jobs (default: {default_cpu_count()})")
    parser.add_argument(
        "--max_fit_points",
        type=int,
        default=0,
        help="Unused (downsampling disabled).",
    )
    parser.add_argument(
        "--print_each",
        action="store_true",
        help="Print one output line per frequency point",
    )
    parser.add_argument(
        "--fit_start",
        type=float,
        default=None,
        help="Start time for fit window in seconds (default: ss_time).",
    )
    parser.add_argument(
        "--fit_end",
        type=float,
        default=None,
        help="End time for fit window in seconds (default: stop_time if set).",
    )
    parser.add_argument(
        "--fit_window_mode",
        type=str,
        choices=("fixed", "inverse_omega"),
        default="fixed",
        help=(
            "Fit-window mode: 'fixed' uses one window for all frequencies; "
            "'inverse_omega' scales window ~ 1/omega."
        ),
    )
    parser.add_argument(
        "--fit_window_ref_freq",
        type=float,
        default=1e-1,
        help="Reference angular frequency (rad/s) for inverse_omega mode.",
    )
    parser.add_argument(
        "--fit_window_ref_duration",
        type=float,
        default=500.0,
        help="Fit duration (s) at fit_window_ref_freq in inverse_omega mode.",
    )
    args = parser.parse_args()
    if args.results_dir is None:
        args.results_dir = str(
            default_freq_case_dir(
                repo_root,
                core_model=args.core_model,
                power=args.power,
            )
        )
    return args


def resolve_fit_window(
    freq_point: float,
    fit_start: float,
    fit_end_limit: float | None,
    mode: str,
    ref_freq: float,
    ref_duration: float,
) -> tuple[float | None, float | None]:
    if mode == "inverse_omega":
        if freq_point <= 0:
            raise ValueError("frequency must be positive for inverse_omega fit window")
        if ref_freq <= 0 or ref_duration <= 0:
            raise ValueError("fit_window_ref_freq and fit_window_ref_duration must be positive")
        fit_window = ref_duration * (ref_freq / freq_point)
        fit_end = fit_start + fit_window
        if fit_end_limit is not None:
            fit_end = min(fit_end, fit_end_limit)
        return fit_end, fit_end - fit_start

    fit_end = fit_end_limit
    if fit_end is None:
        return None, None
    return fit_end, fit_end - fit_start


def clean_column_headers(columns):
    """Remove conflicting characters from OpenModelica CSV column headers."""
    header_str = str(list(columns))
    chars_to_remove = ['[', ']', '.', '(', ')', '_', "'"]
    rx = '[' + re.escape(''.join(chars_to_remove)) + ']'
    cleaned = re.sub(rx, '', header_str)
    return cleaned.replace(" ", "").split(',')


def read_run_params(results_dir):
    """Read the run parameters file saved by runFreqNominal.py."""
    params = {}
    params_file = os.path.join(results_dir, "run_params.txt")
    if os.path.exists(params_file):
        with open(params_file, "r") as pf:
            for line in pf:
                parts = line.strip().split('\t')
                if len(parts) == 2:
                    try:
                        params[parts[0]] = float(parts[1])
                    except ValueError:
                        params[parts[0]] = parts[1]
    return params


def read_sin_mag_mapping(results_dir: str) -> dict[str, float] | None:
    mapping_path = os.path.join(results_dir, "sin_mag_by_freq.csv")
    if not os.path.exists(mapping_path):
        return None
    df = pd.read_csv(mapping_path)
    if "frequency_rad_s" not in df.columns or "sin_mag" not in df.columns:
        return None
    mapping: dict[str, float] = {}
    for row in df.itertuples():
        mapping[format_frequency_key(row.frequency_rad_s)] = float(row.sin_mag)
    return mapping


def read_stop_time_mapping(results_dir: str) -> dict[str, float] | None:
    mapping_path = os.path.join(results_dir, "stop_time_by_freq.csv")
    if not os.path.exists(mapping_path):
        return None
    df = pd.read_csv(mapping_path)
    if "frequency_rad_s" not in df.columns or "stop_time_s" not in df.columns:
        return None
    mapping: dict[str, float] = {}
    for row in df.itertuples():
        mapping[format_frequency_key(row.frequency_rad_s)] = float(row.stop_time_s)
    return mapping


def fit_sine_fixed_freq(time_data, power_data, freq_point, sin_mag):
    """
    Fit y = offset + A*sin(w*t + phase) with frequency fixed at freq_point.
    Returns (amplitude, phase_rad, offset, r_squared).
    """
    if len(time_data) < 3:
        raise ValueError("not enough samples for sine fit")

    offset = float(power_data.mean())
    y = power_data - offset

    s = np.sin(freq_point * time_data)
    c = np.cos(freq_point * time_data)

    a = (2.0 / len(y)) * (y @ s)
    b = (2.0 / len(y)) * (y @ c)
    amplitude = float(np.hypot(a, b))
    phase_rad = float(np.arctan2(b, a))

    fitted = offset + amplitude * np.sin(freq_point * time_data + phase_rad)
    residuals = power_data - fitted
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((power_data - power_data.mean())**2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return float(amplitude), float(phase_rad), float(offset), float(r_squared)


def wrap_phase_rad(phase_rad: float) -> float:
    """Wrap phase into [-pi, pi) for stable reporting."""
    return float((phase_rad + np.pi) % (2.0 * np.pi) - np.pi)


def phase_relative_to_perturbation_start_deg(
    phase_rad: float,
    freq_point: float,
    fit_start: float,
    perturbation_start: float,
) -> float:
    """
    Convert a fitted phase referenced to fit_start into a phase referenced to
    the forcing activation time (perturbationStartTime / ss_time).
    """
    phase_relative = phase_rad - freq_point * (fit_start - perturbation_start)
    return float(np.degrees(wrap_phase_rad(phase_relative)))


def process_single_freq(freq_point: float, results_dir: str,
                        sin_mag: float, ss_time: float,
                        fit_start: float, fit_end: float | None,
                        max_fit_points: int) -> dict:
    """
    Read simulation CSV for a single frequency point, fit a sine wave,
    and return gain/phase results. Designed to be called by worker threads.

    Returns
    -------
    dict
        Result dictionary with keys: 'freq', 'success', 'gain', 'gain_dB',
        'phase_deg', 'r_squared', 'error'.
    """
    result = {
        'freq': freq_point, 'success': False, 'error': None,
        'gain': None, 'gain_dB': None, 'phase_deg': None, 'r_squared': None,
        'fit_start': fit_start, 'fit_end': fit_end, 'fit_window': None
    }

    work_path = os.path.join(results_dir, f"freq{freq_point:08.5f}")
    file_prefix = f"MSRR_freq{freq_point:08.5f}"
    data_file = os.path.join(work_path, f"{file_prefix}_res.csv")

    if not os.path.exists(data_file):
        result['error'] = "missing CSV"
        return result

    try:
        # Read simulation results
        sim_data = pd.read_csv(data_file)

        # Clean column headers
        sim_data.columns = clean_column_headers(sim_data.columns)

        time = sim_data['time']

        # Find the power column — after header cleaning, try candidates
        power_col = None
        for candidate in ['pkenpopulationn',
                          'pkenpopulation',
                          'powerBlockfissionPowerP',
                          'fuelChannelNomPower',
                          'FuelChannelNomPower',
                          'npopulationn']:
            if candidate in sim_data.columns:
                power_col = candidate
                break

        if power_col is None:
            # Try partial match
            for col in sim_data.columns:
                col_lower = col.lower()
                if 'npopulation' in col_lower or 'nompower' in col_lower:
                    power_col = col
                    break

        if power_col is None:
            result['error'] = (
                f"cannot find power column; "
                f"available: {list(sim_data.columns)[:10]}")
            return result

        power = sim_data[power_col]

        # Select data within fit window
        actual_end = float(time.iloc[-1])
        if fit_end is None:
            effective_fit_end = actual_end
        else:
            effective_fit_end = min(float(fit_end), actual_end)
        result['fit_end'] = effective_fit_end
        result['fit_window'] = effective_fit_end - fit_start
        if effective_fit_end <= fit_start:
            result['error'] = "fit_end must be greater than fit_start"
            return result

        mask = (time >= fit_start) & (time <= effective_fit_end)
        time_fit = time.loc[mask].values - fit_start
        power_fit = power.loc[mask].values

        finite_mask = np.isfinite(time_fit) & np.isfinite(power_fit)
        time_fit = time_fit[finite_mask]
        power_fit = power_fit[finite_mask]

        if len(time_fit) < 10:
            # Fallback for runs that develop NaN/Inf soon after ss_time:
            # include a short pre-perturbation window to preserve enough
            # finite samples for fitting.
            retry_start = max(0.0, fit_start - 500.0)
            retry_mask = (time >= retry_start) & (time <= fit_end)
            time_retry = time.loc[retry_mask].values - fit_start
            power_retry = power.loc[retry_mask].values
            retry_mask = np.isfinite(time_retry) & np.isfinite(power_retry)
            time_fit = time_retry[retry_mask]
            power_fit = power_retry[retry_mask]

        if len(time_fit) < 10:
            result['error'] = "insufficient finite samples for fitting"
            return result

        # Fit sine wave with frequency fixed
        amplitude, phase_rad, offset, r_squared = fit_sine_fixed_freq(
            time_fit, power_fit, freq_point, sin_mag
        )

        # Gain = output amplitude / input amplitude
        # Input is sin_mag pcm = sin_mag * 1E-5 in dk/k
        gain = amplitude / (sin_mag * 1e-5)
        gain_dB = 20.0 * np.log10(gain) if gain > 0 else -np.inf
        phase_deg = phase_relative_to_perturbation_start_deg(
            phase_rad=phase_rad,
            freq_point=freq_point,
            fit_start=fit_start,
            perturbation_start=ss_time,
        )

        result['gain'] = gain
        result['gain_dB'] = gain_dB
        result['phase_deg'] = phase_deg
        result['r_squared'] = r_squared
        result['success'] = True

    except Exception as e:
        result['error'] = str(e)

    return result


def main():
    args = parse_args()
    results_dir = args.results_dir

    # Read run parameters
    params = read_run_params(results_dir)
    freq_min = params.get("freq_min", 1e-2)
    freq_max = params.get("freq_max", 1e1)
    num_freq = int(params.get("num_freq", 100))
    sin_mag = params.get("sin_mag", 1.0)
    sin_mag_map = read_sin_mag_mapping(results_dir)
    stop_time_map = read_stop_time_mapping(results_dir)
    ss_time = params.get("ss_time", 2000.0)
    stop_time = params.get("stop_time", None)
    fit_start = ss_time if args.fit_start is None else args.fit_start
    fit_end_limit = args.fit_end if args.fit_end is not None else stop_time

    # Reconstruct frequency space
    freq_space = np.logspace(np.log10(freq_min), np.log10(freq_max),
                             num=num_freq)

    n_jobs = min(args.n_jobs, num_freq)

    print(f"Collecting results from: {os.path.abspath(results_dir)}")
    print(f"Frequency range: {freq_min:.4f} to {freq_max:.4f} rad/s "
          f"({num_freq} points)")
    if sin_mag_map:
        print("Perturbation amplitude: per-frequency mapping")
    else:
        print(f"Perturbation amplitude: {sin_mag} pcm")
    print(f"Steady-state time: {ss_time} s")
    print(f"Phase reference: perturbation start at {ss_time} s")
    if stop_time_map and args.fit_end is None:
        print("Stop time limit: per-frequency mapping")
    elif stop_time is not None:
        print(f"Stop time limit: {stop_time:g} s")
    if args.fit_window_mode == "fixed":
        if stop_time_map and args.fit_end is None:
            print(f"Fit window mode: fixed ({fit_start:g} to per-frequency end)")
        else:
            print(f"Fit window mode: fixed ({fit_start:g} to {fit_end_limit if fit_end_limit is not None else 'end'} s)")
    else:
        print(
            "Fit window mode: inverse_omega "
            f"({args.fit_window_ref_duration:g}s at {args.fit_window_ref_freq:g} rad/s)"
        )
        print(f"Fit window lower bound: {fit_start:g} s")
    print(f"Parallel jobs: {n_jobs}")
    print("-" * 70)

    all_results: list[dict] = [None] * len(freq_space)  # type: ignore[assignment]
    completed = 0
    progress_every = max(1, len(freq_space) // 16)

    with ThreadPoolExecutor(max_workers=n_jobs) as executor:
        future_map = {}
        for idx, fp in enumerate(freq_space):
            fp_fit_end_limit = fit_end_limit
            if fp_fit_end_limit is None and stop_time_map:
                fp_fit_end_limit = stop_time_map.get(format_frequency_key(fp))
            fp_fit_end, _ = resolve_fit_window(
                freq_point=float(fp),
                fit_start=fit_start,
                fit_end_limit=fp_fit_end_limit,
                mode=args.fit_window_mode,
                ref_freq=args.fit_window_ref_freq,
                ref_duration=args.fit_window_ref_duration,
            )
            future = executor.submit(
                process_single_freq,
                freq_point=float(fp),
                results_dir=os.path.abspath(results_dir),
                sin_mag=(
                    sin_mag_map.get(format_frequency_key(fp), sin_mag)
                    if sin_mag_map
                    else sin_mag
                ),
                ss_time=ss_time,
                fit_start=fit_start,
                fit_end=fp_fit_end,
                max_fit_points=args.max_fit_points,
            )
            future_map[future] = idx
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                all_results[idx] = future.result()
            except Exception as exc:
                all_results[idx] = {
                    "freq": float(freq_space[idx]),
                    "success": False,
                    "error": str(exc),
                    "gain": None,
                    "gain_dB": None,
                    "phase_deg": None,
                    "r_squared": None,
                }
            completed += 1
            if completed % progress_every == 0 or completed == len(freq_space):
                print(
                    f"  Progress: {completed}/{len(freq_space)} "
                    f"frequency fits completed"
                )

    # Collect successful results (preserve frequency ordering)
    freq_list = []
    gain_list = []
    phase_list = []
    gain_dB_list = []
    fit_quality = []
    fit_start_list = []
    fit_end_list = []
    fit_window_list = []

    for r in all_results:
        if r['success']:
            freq_list.append(r['freq'])
            gain_list.append(r['gain'])
            gain_dB_list.append(r['gain_dB'])
            phase_list.append(r['phase_deg'])
            fit_quality.append(r['r_squared'])
            fit_start_list.append(r['fit_start'])
            fit_end_list.append(r['fit_end'])
            fit_window_list.append(r['fit_window'])

            if args.print_each:
                print(f"  freq = {r['freq']:8.5f} rad/s | "
                      f"gain = {r['gain']:.4f} | "
                      f"gain_dB = {r['gain_dB']:.2f} dB | "
                      f"phase = {r['phase_deg']:.2f} deg | "
                      f"R² = {r['r_squared']:.6f} | "
                      f"fit_window = {r['fit_window']:.3f} s")
        else:
            print(f"  freq = {r['freq']:8.5f} rad/s | "
                  f"SKIPPED — {r['error']}")

    print("-" * 70)
    succeeded = sum(1 for r in all_results if r['success'])
    failed = sum(1 for r in all_results if not r['success'])
    print(f"Successfully processed {succeeded} / {num_freq} "
          f"frequency points ({failed} failed).")

    if len(freq_list) == 0:
        print("No results to save. Exiting.")
        return

    # Save results as MATLAB .m file
    m_file = os.path.join(results_dir, "FreqResponseResults.m")
    with open(m_file, "w") as mf:
        mf.write("% MSRR Frequency Response - Nominal Configuration\n")
        mf.write(f"% Power level: {params.get('power', 1.0)}\n")
        mf.write(f"% Perturbation: {sin_mag} pcm\n")
        mf.write(f"% Steady-state time: {ss_time} s\n\n")
        mf.write(f"% Phase reference: perturbation start at {ss_time} s\n\n")
        if args.fit_window_mode == "fixed":
            mf.write("% Fit window mode: fixed\n")
            mf.write(f"% Fit window: {fit_start} to {fit_end_limit}\n\n")
        else:
            mf.write("% Fit window mode: inverse_omega\n")
            mf.write(
                f"% Reference: {args.fit_window_ref_duration} s at "
                f"{args.fit_window_ref_freq} rad/s\n\n"
            )
        mf.write(f"freq = {freq_list};\n")
        mf.write(f"gain = {gain_list};\n")
        mf.write(f"gain_dB = {gain_dB_list};\n")
        mf.write(f"phase = {phase_list};\n")
        mf.write(f"R2 = {fit_quality};\n")
        mf.write(f"fit_start_s = {fit_start_list};\n")
        mf.write(f"fit_end_s = {fit_end_list};\n")
        mf.write(f"fit_window_s = {fit_window_list};\n")
    print(f"Results saved to: {m_file}")

    # Save results as CSV
    csv_file = os.path.join(results_dir, "FreqResponseResults.csv")
    results_df = pd.DataFrame({
        'frequency_rad_s': freq_list,
        'gain': gain_list,
        'gain_dB': gain_dB_list,
        'phase_deg': phase_list,
        'R_squared': fit_quality,
        'fit_start_s': fit_start_list,
        'fit_end_s': fit_end_list,
        'fit_window_s': fit_window_list,
    })
    results_df.to_csv(csv_file, index=False)
    print(f"Results saved to: {csv_file}")

    # Optional: Generate Bode plot
    if args.plot:
        try:
            import matplotlib.pyplot as plt

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8),
                                           sharex=True)

            ax1.semilogx(freq_list, gain_dB_list, 'b-o',
                         markersize=3, linewidth=1.2)
            ax1.set_ylabel('Gain (dB)')
            ax1.set_title(
                f'MSRR Frequency Response — Nominal Power = '
                f'{params.get("power", 1.0)}, '
                f'Perturbation = {sin_mag} pcm'
            )
            ax1.grid(True, which='both', linestyle='--', alpha=0.7)

            ax2.semilogx(freq_list, phase_list, 'r-o',
                         markersize=3, linewidth=1.2)
            ax2.set_ylabel('Phase (degrees)')
            ax2.set_xlabel('Frequency (rad/s)')
            ax2.grid(True, which='both', linestyle='--', alpha=0.7)

            plt.tight_layout()

            plot_file = os.path.join(results_dir, "BodePlot.png")
            plt.savefig(plot_file, dpi=150)
            print(f"Bode plot saved to: {plot_file}")
            plt.show()

        except ImportError:
            print("matplotlib not available, skipping plot generation.")


if __name__ == "__main__":
    main()
