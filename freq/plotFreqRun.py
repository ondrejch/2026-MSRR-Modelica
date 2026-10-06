#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: SMD-MSRR-dev
Advisor: Dr. Ondrej Chvala

Plot power and core temperatures from a single MSRR frequency response run.

Usage:
    python plotFreqRun.py --freq 0.01
    python plotFreqRun.py --freq 0.01 --results_dir 00runs/freq/1r/power_1
    python plotFreqRun.py --freq 0.01 --ss_only --save
    python plotFreqRun.py --csv path/to/MSRR_freq0.01_res.csv

The script can locate the CSV either by frequency value (looking up the
standard directory structure) or by direct path to the CSV file.
"""

import argparse
import re
import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

try:
    from ._common import clean_column_headers, resolve_case_dir
    from .paths import default_freq_case_dir
except ImportError:
    from _common import clean_column_headers, resolve_case_dir
    from paths import default_freq_case_dir


def parse_args():
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Plot power and core temperatures from a frequency response run."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freq", type=float,
                       help="Frequency point in rad/s (uses standard directory layout)")
    group.add_argument("--csv", type=str,
                       help="Direct path to a simulation result CSV file")
    parser.add_argument(
        "--results_dir",
        type=str,
        default=None,
        help=(
            "Base results directory "
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
    parser.add_argument("--ss_only", action="store_true",
                        help="Plot only the post-steady-state region (after ss_time)")
    parser.add_argument("--ss_time", type=float, default=None,
                        help="Steady-state time in seconds (auto-read from run_params.txt)")
    parser.add_argument("--save", action="store_true",
                        help="Save plots to PNG files instead of displaying")
    parser.add_argument("--dpi", type=int, default=150,
                        help="DPI for saved figures (default: 150)")
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


def find_column(sim_data, candidates, partial_matches=None):
    """
    Find a column in the dataframe by trying exact matches first,
    then partial (substring) matches.

    Returns the column name or None.
    """
    for candidate in candidates:
        if candidate in sim_data.columns:
            return candidate

    if partial_matches:
        for col in sim_data.columns:
            col_lower = col.lower()
            for pattern in partial_matches:
                if pattern in col_lower:
                    return col

    return None


def main():
    args = parse_args()

    # Locate the CSV file
    if args.csv:
        data_file = args.csv
        results_dir = os.path.dirname(os.path.dirname(data_file))
        freq_point = None
        # Try to extract frequency from filename
        basename = os.path.basename(data_file)
        match = re.search(r'freq([\d.]+)', basename)
        if match:
            freq_point = float(match.group(1))
    else:
        freq_point = args.freq
        results_dir = args.results_dir
        # Canonical case-directory name (falls back to the frozen pre-fix
        # zero-padded name of published records when the canonical one is
        # absent); the CSV prefix always matches the resolved directory name.
        work_path = resolve_case_dir(results_dir, freq_point)
        file_prefix = f"MSRR_{os.path.basename(work_path)}"
        data_file = os.path.join(work_path, f"{file_prefix}_res.csv")

    if not os.path.exists(data_file):
        print(f"ERROR: Cannot find CSV file: {data_file}")
        sys.exit(1)

    # Read run parameters for ss_time
    params = read_run_params(results_dir)
    ss_time = args.ss_time if args.ss_time is not None else params.get("ss_time", 2000.0)
    sin_mag = params.get("sin_mag", 1.0)

    freq_label = f"{freq_point:.5f}" if freq_point is not None else "unknown"

    print(f"Loading: {data_file}")
    print(f"Frequency: {freq_label} rad/s")
    print(f"Steady-state time: {ss_time} s")

    # Read and clean data
    sim_data = pd.read_csv(data_file)
    sim_data.columns = clean_column_headers(sim_data.columns)

    # Print available columns for debugging
    print(f"Available columns ({len(sim_data.columns)}):")
    for i, col in enumerate(sim_data.columns):
        if i < 30:  # show first 30
            print(f"  {col}")
    if len(sim_data.columns) > 30:
        print(f"  ... and {len(sim_data.columns) - 30} more")

    if 'time' not in sim_data.columns:
        print(f"Missing time column in {data_file}")
        sys.exit(1)
    time = sim_data['time'].values

    # --- Find power column ---
    power_col = find_column(
        sim_data,
        candidates=['pkenpopulationn', 'pkenpopulation',
                    'powerBlocknomReactorPower',
                    'powerBlocknomFissionPower',
                    'fuelChannelNomPower', 'FuelChannelNomPower'],
        partial_matches=['npopulation', 'nompower', 'fissionpower']
    )

    # --- Find temperature columns ---
    fuel1_col = find_column(
        sim_data,
        candidates=['fuelChannelfuelNode1T'],
        partial_matches=['fuelnode1t']
    )
    fuel2_col = find_column(
        sim_data,
        candidates=['fuelChannelfuelNode2T'],
        partial_matches=['fuelnode2t']
    )
    grap_col = find_column(
        sim_data,
        candidates=['fuelChannelgrapNodeT'],
        partial_matches=['grapnodet']
    )
    inlet_col = find_column(
        sim_data,
        candidates=['fuelChanneltempInT'],
        partial_matches=['tempint']
    )
    outlet_col = find_column(
        sim_data,
        candidates=['fuelChanneltempOutT'],
        partial_matches=['tempoutt']
    )

    # --- Find reactivity feedback columns ---
    fuel_fb1_col = find_column(
        sim_data,
        candidates=['reactivityFeedbackFuelTempFeedbackNode1'],
        partial_matches=['fueltempfeedbacknode1']
    )
    fuel_fb2_col = find_column(
        sim_data,
        candidates=['reactivityFeedbackFuelTempFeedbackNode2'],
        partial_matches=['fueltempfeedbacknode2']
    )
    grap_fb_col = find_column(
        sim_data,
        candidates=['reactivityFeedbackGrapTempFeedback'],
        partial_matches=['graptempfeedback']
    )
    total_fb_col = find_column(
        sim_data,
        candidates=['reactivityFeedbackTotalTempFeedback'],
        partial_matches=['totaltempfeedback']
    )

    # Report what was found
    found = {}
    for name, col in [('Power', power_col),
                      ('Fuel Node 1', fuel1_col),
                      ('Fuel Node 2', fuel2_col),
                      ('Graphite', grap_col),
                      ('Inlet', inlet_col),
                      ('Outlet', outlet_col),
                      ('Fuel Feedback 1', fuel_fb1_col),
                      ('Fuel Feedback 2', fuel_fb2_col),
                      ('Graphite Feedback', grap_fb_col),
                      ('Total Feedback', total_fb_col)]:
        if col:
            found[name] = col
            print(f"  Found {name}: {col}")
        else:
            print(f"  WARNING: {name} column not found")

    if not power_col and not any([fuel1_col, fuel2_col, grap_col]) and not any([fuel_fb1_col, fuel_fb2_col, grap_fb_col, total_fb_col]):
        print("ERROR: No plottable columns found.")
        sys.exit(1)

    # Select time range
    if args.ss_only:
        mask = time >= ss_time
        time_plot = time[mask] - ss_time
        xlabel = "Time after steady state (s)"
    else:
        mask = np.ones(len(time), dtype=bool)
        time_plot = time
        xlabel = "Time (s)"

    # Count how many subplots we need
    has_power = power_col is not None
    has_temps = any([fuel1_col, fuel2_col, grap_col, inlet_col, outlet_col])
    has_feedback = any([fuel_fb1_col, fuel_fb2_col, grap_fb_col, total_fb_col])
    n_plots = int(has_power) + int(has_temps) + int(has_feedback)

    if n_plots == 0:
        print("Nothing to plot.")
        sys.exit(1)

    # --- Create figure ---
    fig, axes = plt.subplots(n_plots, 1, figsize=(12, 4.5 * n_plots),
                             sharex=True)
    if n_plots == 1:
        axes = [axes]

    plot_idx = 0

    # --- Power plot ---
    if has_power:
        ax = axes[plot_idx]
        power = sim_data[power_col].values[mask]

        ax.plot(time_plot, power, 'b-', linewidth=0.8, label='Neutron population')
        ax.set_ylabel('Normalized Power')
        ax.set_title(
            f'MSRR Power — freq = {freq_label} rad/s, '
            f'perturbation = {sin_mag} pcm'
        )
        ax.legend(loc='best')
        ax.grid(True, linestyle='--', alpha=0.7)

        # Add ss_time marker if showing full time range
        if not args.ss_only:
            ax.axvline(x=ss_time, color='r', linestyle='--', alpha=0.5,
                       label=f'SS time = {ss_time} s')
            ax.legend(loc='best')

        plot_idx += 1

    # --- Temperature plot ---
    if has_temps:
        ax = axes[plot_idx]

        colors = {'Fuel Node 1': '#d62728',   # red
                  'Fuel Node 2': '#ff7f0e',   # orange
                  'Graphite':    '#2ca02c',    # green
                  'Inlet':       '#1f77b4',    # blue
                  'Outlet':      '#9467bd'}    # purple

        temp_cols = [
            ('Fuel Node 1', fuel1_col),
            ('Fuel Node 2', fuel2_col),
            ('Graphite', grap_col),
            ('Inlet', inlet_col),
            ('Outlet', outlet_col),
        ]

        for name, col in temp_cols:
            if col is not None:
                temp_data = sim_data[col].values[mask]
                ax.plot(time_plot, temp_data, color=colors[name],
                        linewidth=0.8, label=name)

        ax.set_ylabel('Temperature (°C)')
        ax.set_title(
            f'MSRR Core Temperatures — freq = {freq_label} rad/s'
        )
        ax.legend(loc='best')
        ax.grid(True, linestyle='--', alpha=0.7)

        # Add ss_time marker if showing full time range
        if not args.ss_only:
            ax.axvline(x=ss_time, color='r', linestyle='--', alpha=0.5,
                       label=f'SS time = {ss_time} s')
            ax.legend(loc='best')

        plot_idx += 1

    # --- Feedback plot ---
    if has_feedback:
        ax = axes[plot_idx]

        fuel_fb1 = None
        fuel_fb2 = None

        if fuel_fb1_col is not None:
            fuel_fb1 = sim_data[fuel_fb1_col].values[mask]
            ax.plot(time_plot, fuel_fb1 * 1e5, color='#d62728',
                    linewidth=0.8, label='Fuel Feedback Node 1')

        if fuel_fb2_col is not None:
            fuel_fb2 = sim_data[fuel_fb2_col].values[mask]
            ax.plot(time_plot, fuel_fb2 * 1e5, color='#ff7f0e',
                    linewidth=0.8, label='Fuel Feedback Node 2')

        if fuel_fb1 is not None and fuel_fb2 is not None:
            ax.plot(time_plot, (fuel_fb1 + fuel_fb2) * 1e5, color='#8c564b',
                    linewidth=1.0, label='Fuel Feedback Total')

        if grap_fb_col is not None:
            grap_fb = sim_data[grap_fb_col].values[mask]
            ax.plot(time_plot, grap_fb * 1e5, color='#2ca02c',
                    linewidth=0.8, label='Graphite Feedback')

        if total_fb_col is not None:
            total_fb = sim_data[total_fb_col].values[mask]
            ax.plot(time_plot, total_fb * 1e5, color='k',
                    linewidth=1.0, linestyle='--', label='Total Feedback')

        ax.set_ylabel('Feedback (pcm)')
        ax.set_title(
            f'MSRR Reactivity Feedback — freq = {freq_label} rad/s'
        )
        ax.legend(loc='best')
        ax.grid(True, linestyle='--', alpha=0.7)

        if not args.ss_only:
            ax.axvline(x=ss_time, color='r', linestyle='--', alpha=0.5,
                       label=f'SS time = {ss_time} s')
            ax.legend(loc='best')

        plot_idx += 1

    axes[-1].set_xlabel(xlabel)
    plt.tight_layout()

    if args.save:
        if args.csv:
            out_dir = os.path.dirname(data_file)
        else:
            # --freq/--csv are mutually exclusive and required, so the
            # non-csv branch always has a frequency point (explicit raise:
            # an assert would be stripped under `python -O`).
            if freq_point is None:
                raise ValueError(
                    "the --save branch without --csv requires a frequency "
                    "point (--freq); --freq/--csv are mutually exclusive "
                    "and required"
                )
            out_dir = resolve_case_dir(results_dir, freq_point)

        suffix = "_ss" if args.ss_only else ""
        plot_file = os.path.join(out_dir,
                                 f"plot_freq{freq_label}{suffix}.png")
        plt.savefig(plot_file, dpi=args.dpi, bbox_inches='tight')
        print(f"Plot saved to: {plot_file}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
