#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Collect and analyze frequency response results from the nominal MSRR model.

Usage:
    python collectFreqNominal.py [--results_dir RESULTS_DIR] [--plot]
"""

import argparse
import re
import numpy as np
import pandas as pd
import os
from pathlib import Path
from scipy.optimize import curve_fit

try:
    from .paths import default_freq_case_dir
except ImportError:
    from paths import default_freq_case_dir


def parse_args():
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Collect MSRR frequency response results and compute Bode plot data."
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


def fit_sine_fixed_freq(time_data, power_data, freq_point, sin_mag):
    """
    Fit a sine wave to the power data with the frequency fixed at freq_point.
    Only amplitude, phase, and offset are free parameters.

    Returns (amplitude, phase_rad, offset, r_squared).
    """
    mean_power = np.mean(power_data)

    # Model with frequency fixed via closure
    def model(x, amplitude, phase_shift, offset):
        return amplitude * np.sin(freq_point * x + phase_shift) + offset

    # Initial guess
    p0 = [sin_mag * 1e-5, 0.0, mean_power]

    # Bounds: amplitude > 0, phase in [-pi, pi], offset near mean
    bounds_lower = [0.0, -np.pi, mean_power * 0.5]
    bounds_upper = [np.inf, np.pi, mean_power * 1.5]

    # Ensure bounds are valid even for edge cases
    if bounds_lower[2] >= bounds_upper[2]:
        bounds_lower[2] = mean_power - abs(mean_power) - 1e-10
        bounds_upper[2] = mean_power + abs(mean_power) + 1e-10

    popt, pcov = curve_fit(
        model, time_data, power_data, p0=p0,
        bounds=(bounds_lower, bounds_upper),
        maxfev=10000
    )

    amplitude = popt[0]
    phase_rad = popt[1]
    offset = popt[2]

    # R-squared
    fitted = model(time_data, *popt)
    residuals = power_data - fitted
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((power_data - mean_power)**2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return amplitude, phase_rad, offset, r_squared


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


def main():
    args = parse_args()
    results_dir = args.results_dir

    # Read run parameters
    params = read_run_params(results_dir)
    freq_min = params.get("freq_min", 1e-2)
    freq_max = params.get("freq_max", 1e1)
    num_freq = int(params.get("num_freq", 100))
    sin_mag = params.get("sin_mag", 1.0)
    ss_time = params.get("ss_time", 2000.0)
    stop_time = params.get("stop_time", None)
    fit_start = ss_time if args.fit_start is None else args.fit_start
    fit_end = args.fit_end if args.fit_end is not None else stop_time

    # Reconstruct frequency space
    freq_space = np.logspace(np.log10(freq_min), np.log10(freq_max),
                             num=num_freq)

    # Storage for results
    freq_list = []
    gain_list = []
    phase_list = []
    gain_dB_list = []
    fit_quality = []

    print(f"Collecting results from: {os.path.abspath(results_dir)}")
    print(f"Frequency range: {freq_min:.4f} to {freq_max:.4f} rad/s "
          f"({num_freq} points)")
    print(f"Perturbation amplitude: {sin_mag} pcm")
    print(f"Steady-state time: {ss_time} s")
    print(f"Phase reference: perturbation start at {ss_time} s")
    print(f"Fit window: {fit_start:g} to {fit_end if fit_end is not None else 'end'} s")
    print("-" * 70)

    for freq_point in freq_space:
        work_path = os.path.join(results_dir, f"freq{freq_point:08.5f}")
        file_prefix = f"MSRR_freq{freq_point:08.5f}"
        data_file = os.path.join(work_path, f"{file_prefix}_res.csv")

        if not os.path.exists(data_file):
            print(f"  WARNING: Missing results for freq = {freq_point:8.5f}, "
                  f"skipping.")
            continue

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
                print(f"  WARNING: Cannot find power column for "
                      f"freq = {freq_point:8.5f}")
                print(f"  Available columns: "
                      f"{list(sim_data.columns)[:15]}...")
                continue

            power = sim_data[power_col]

            # Select data within fit window
            fit_end_current = fit_end if fit_end is not None else float(time.iloc[-1])
            if fit_end_current <= fit_start:
                raise ValueError("fit_end must be greater than fit_start")

            mask = (time >= fit_start) & (time <= fit_end_current)
            time_fit = time.loc[mask].values - fit_start
            power_fit = power.loc[mask].values

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

            freq_list.append(freq_point)
            gain_list.append(gain)
            gain_dB_list.append(gain_dB)
            phase_list.append(phase_deg)
            fit_quality.append(r_squared)

            print(f"  freq = {freq_point:8.5f} rad/s | "
                  f"gain = {gain:.4f} | "
                  f"gain_dB = {gain_dB:.2f} dB | "
                  f"phase = {phase_deg:.2f} deg | "
                  f"R² = {r_squared:.6f}")

        except Exception as e:
            print(f"  ERROR processing freq = {freq_point:8.5f}: {e}")
            continue

    print("-" * 70)
    print(f"Successfully processed {len(freq_list)} / {num_freq} "
          f"frequency points.")

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
        mf.write(f"% Fit window: {fit_start} to {fit_end}\n\n")
        mf.write(f"freq = {freq_list};\n")
        mf.write(f"gain = {gain_list};\n")
        mf.write(f"gain_dB = {gain_dB_list};\n")
        mf.write(f"phase = {phase_list};\n")
        mf.write(f"R2 = {fit_quality};\n")
    print(f"Results saved to: {m_file}")

    # Save results as CSV
    csv_file = os.path.join(results_dir, "FreqResponseResults.csv")
    results_df = pd.DataFrame({
        'frequency_rad_s': freq_list,
        'gain': gain_list,
        'gain_dB': gain_dB_list,
        'phase_deg': phase_list,
        'R_squared': fit_quality
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
