#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plot sine-wave fits for MSRR frequency response runs.

Usage examples:
  python plotFreqFits.py --results_dir freq/results/1r/power_0p1 --freq 0.1
  python plotFreqFits.py --results_dir freq/results/9r/power_1 --out_dir /tmp/fit_plots
  python plotFreqFits.py --results_dir freq/results/1r/power_0p01 --all
"""

import argparse
import os
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
try:
    from scipy.optimize import curve_fit
except Exception:
    curve_fit = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot power signal and fitted sine wave for frequency response runs."
    )
    parser.add_argument(
        "--results_dir",
        type=str,
        required=True,
        help="Results directory (e.g., freq/results/1r/power_0p1)",
    )
    parser.add_argument(
        "--out_dir",
        type=str,
        default=None,
        help="Output directory for plots (default: <results_dir>/fit_plots)",
    )
    parser.add_argument(
        "--freq",
        type=float,
        default=None,
        help="Single frequency (rad/s) to plot",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Plot all frequencies found in results_dir",
    )
    parser.add_argument(
        "--ss_time",
        type=float,
        default=None,
        help="Steady-state time override (seconds)",
    )
    parser.add_argument(
        "--fit_end",
        type=float,
        default=None,
        help=(
            "End time for fit window in absolute seconds "
            "(default: use all data after ss_time)"
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=600,
        help="DPI for saved plots (default: 150)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show plots instead of saving (default saves)",
    )
    return parser.parse_args()


def clean_column_headers(columns):
    header_str = str(list(columns))
    chars_to_remove = ['[', ']', '.', '(', ')', '_', "'"]
    rx = '[' + re.escape(''.join(chars_to_remove)) + ']'
    cleaned = re.sub(rx, '', header_str)
    return cleaned.replace(" ", "").split(',')


def read_run_params(results_dir):
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


def fit_sine_fixed_freq(time_data, power_data, freq_point):
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

    return amplitude, phase_rad, offset, r_squared, fitted


def sine_model(t, amplitude, phase_rad, offset, freq_point):
    return offset + amplitude * np.sin(freq_point * t + phase_rad)


def find_power_column(sim_data):
    candidates = [
        'pkenpopulationn',
        'pkenpopulation',
        'powerBlockfissionPowerP',
        'fuelChannelNomPower',
        'FuelChannelNomPower',
        'npopulationn',
    ]
    for candidate in candidates:
        if candidate in sim_data.columns:
            return candidate

    for col in sim_data.columns:
        col_lower = col.lower()
        if 'npopulation' in col_lower or 'nompower' in col_lower:
            return col

    return None


def list_frequencies(results_dir):
    freqs = []
    for name in os.listdir(results_dir):
        if not name.startswith("freq"):
            continue
        try:
            freq_val = float(name.replace("freq", ""))
        except ValueError:
            continue
        freqs.append(freq_val)
    return sorted(freqs)


def plot_single_freq(results_dir, out_dir, freq_point, ss_time, fit_end, show, dpi):
    work_path = os.path.join(results_dir, f"freq{freq_point:08.5f}")
    file_prefix = f"MSRR_freq{freq_point:08.5f}"
    data_file = os.path.join(work_path, f"{file_prefix}_res.csv")

    if not os.path.exists(data_file):
        print(f"Missing CSV for freq {freq_point:.5f}: {data_file}")
        return False

    sim_data = pd.read_csv(data_file)
    sim_data.columns = clean_column_headers(sim_data.columns)

    if 'time' not in sim_data.columns:
        print(f"Missing time column for freq {freq_point:.5f}")
        return False

    power_col = find_power_column(sim_data)
    if power_col is None:
        print(f"Missing power column for freq {freq_point:.5f}")
        return False

    time = sim_data['time'].values
    power = sim_data[power_col].values

    if fit_end is not None and fit_end <= ss_time:
        print(f"Invalid fit window for freq {freq_point:.5f}: fit_end <= ss_time")
        return False

    if fit_end is None:
        mask = time >= ss_time
    else:
        mask = (time >= ss_time) & (time <= fit_end)

    time_fit = time[mask] - ss_time
    power_fit = power[mask]

    finite_mask = np.isfinite(time_fit) & np.isfinite(power_fit)
    time_fit = time_fit[finite_mask]
    power_fit = power_fit[finite_mask]

    if len(time_fit) < 10:
        print(f"Insufficient samples after ss_time for freq {freq_point:.5f}")
        return False

    amp, phase, offset, r2, fitted = fit_sine_fixed_freq(time_fit, power_fit, freq_point)

    scipy_amp = scipy_phase = scipy_offset = None
    scipy_r2 = None
    scipy_err_amp = scipy_err_phase = scipy_err_offset = None
    scipy_fitted = None
    if curve_fit is not None:
        try:
            p0 = [amp, phase, offset]
            popt, pcov = curve_fit(
                lambda t, a, ph, off: sine_model(t, a, ph, off, freq_point),
                time_fit,
                power_fit,
                p0=p0,
                maxfev=20000,
            )
            scipy_amp, scipy_phase, scipy_offset = popt.tolist()
            scipy_fitted = sine_model(time_fit, scipy_amp, scipy_phase, scipy_offset, freq_point)
            residuals = power_fit - scipy_fitted
            ss_res = np.sum(residuals**2)
            ss_tot = np.sum((power_fit - power_fit.mean())**2)
            scipy_r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
            if pcov is not None and np.all(np.isfinite(pcov)):
                perr = np.sqrt(np.diag(pcov))
                if len(perr) == 3:
                    scipy_err_amp, scipy_err_phase, scipy_err_offset = perr.tolist()
        except Exception as exc:
            print(f"SciPy fit failed for freq {freq_point:.5f}: {exc}")

    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    ax.plot(time_fit, power_fit, 'b-', linewidth=0.5, alpha=0.8, label='Simulated')
    ax.plot(time_fit, fitted, 'r-', linewidth=0.4, alpha=0.8, label='Manual fit')
    if scipy_fitted is not None:
        ax.plot(time_fit, scipy_fitted, color='#1f77b4', linestyle=':', linewidth=0.8, label='SciPy fit')
    ax.set_xlabel('Time after SS (s)')
    ax.set_ylabel('Normalized Power')
    if fit_end is None:
        window_label = f"[{ss_time:g}, end]"
    else:
        window_label = f"[{ss_time:g}, {fit_end:g}]"
    ax.set_title(
        f"Freq {freq_point:.5f} rad/s | A={amp:.4e}, phase={phase:.3f} rad, "
        f"R^2={r2:.3f} | fit={window_label}"
    )
    ax.grid(True, linestyle='--', alpha=0.6)
    ax.legend(loc='best')
    plt.tight_layout()

    print("\nFit summary:")
    print(
        "run: results_dir="
        f"{results_dir} | freq={freq_point:.5f} rad/s | "
        f"ss_time={ss_time} | fit_end={fit_end if fit_end is not None else 'end'}"
    )
    print("method   | amplitude        | phase(rad)       | offset           | R^2      | amp_err         | phase_err       | offset_err")
    print("---------|------------------|------------------|------------------|----------|-----------------|-----------------|-----------------")
    print(
        f"manual   | {amp: .8e} | {phase: .8e} | {offset: .8e} | {r2: .6f} | {'-':>15} | {'-':>15} | {'-':>15}"
    )
    if scipy_fitted is not None:
        amp_err = f"{scipy_err_amp: .8e}" if scipy_err_amp is not None else "n/a"
        phase_err = f"{scipy_err_phase: .8e}" if scipy_err_phase is not None else "n/a"
        offset_err = f"{scipy_err_offset: .8e}" if scipy_err_offset is not None else "n/a"
        print(
            f"scipy    | {scipy_amp: .8e} | {scipy_phase: .8e} | {scipy_offset: .8e} | {scipy_r2: .6f} | {amp_err:>15} | {phase_err:>15} | {offset_err:>15}"
        )
    else:
        print("scipy    | (scipy not available)")

    if show:
        plt.show()
        return True

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"fit_freq{freq_point:08.5f}.png")
    plt.savefig(out_path, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_path}")
    return True


def main():
    args = parse_args()

    if not os.path.isdir(args.results_dir):
        raise SystemExit(f"Results dir not found: {args.results_dir}")

    params = read_run_params(args.results_dir)
    ss_time = args.ss_time if args.ss_time is not None else params.get("ss_time", 2000.0)

    out_dir = args.out_dir
    if out_dir is None:
        out_dir = os.path.join(args.results_dir, "fit_plots")

    if args.freq is None and not args.all:
        raise SystemExit("Specify --freq or --all")

    if args.freq is not None:
        plot_single_freq(
            args.results_dir,
            out_dir,
            args.freq,
            ss_time,
            args.fit_end,
            args.show,
            args.dpi,
        )
        return

    freqs = list_frequencies(args.results_dir)
    if not freqs:
        raise SystemExit("No frequency folders found.")

    for fp in freqs:
        plot_single_freq(
            args.results_dir,
            out_dir,
            fp,
            ss_time,
            args.fit_end,
            args.show,
            args.dpi,
        )


if __name__ == "__main__":
    main()
