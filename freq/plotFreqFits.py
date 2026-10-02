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
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
try:
    from scipy.optimize import curve_fit
except Exception:
    curve_fit = None

try:
    from ._common import (
        clean_column_headers,
        fit_sine_least_squares,
        format_frequency_key,
        read_run_params,
        resolve_case_dir,
    )
except ImportError:  # direct-script execution (python plotFreqFits.py ...)
    from _common import (
        clean_column_headers,
        fit_sine_least_squares,
        format_frequency_key,
        read_run_params,
        resolve_case_dir,
    )


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
        help=(
            "Fit-window start override in seconds (default: the settling fit "
            "start fit_start_time recorded in run_params.txt by the runner, "
            "else its ss_time, i.e. the collector's fit window)"
        ),
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
        help="DPI for saved plots (default: 600)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show plots instead of saving (default saves)",
    )
    parser.add_argument(
        "--fit_trend",
        action="store_true",
        help=(
            "Include a linear trend term in the sine fit "
            "(default: off). Same option as collectFreqNominalParallel."
        ),
    )
    return parser.parse_args()


def sine_model(t, amplitude, phase_rad, offset, freq_point):
    return offset + amplitude * np.sin(freq_point * t + phase_rad)


def sine_model_trend(t, amplitude, phase_rad, offset, trend, freq_point, t_c):
    """Sine + linear trend, matching the primary fit's centered design.

    ``t_c`` is the midpoint of the fit window (same centering as
    ``fit_sine_least_squares``) so the SciPy comparison fits the identical
    model as the primary solver when ``--fit_trend`` is active.
    """
    return (
        offset
        + trend * (t - t_c)
        + amplitude * np.sin(freq_point * t + phase_rad)
    )


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


def plot_single_freq(results_dir, out_dir, freq_point, ss_time, fit_end,
                     show, dpi, fit_trend=False):
    # Canonical case-directory name (falls back to the frozen pre-fix
    # zero-padded name of published records when the canonical one is
    # absent); the CSV prefix always matches the resolved directory name.
    work_path = resolve_case_dir(results_dir, freq_point)
    file_prefix = f"MSRR_{os.path.basename(work_path)}"
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

    fit = fit_sine_least_squares(
        time_fit, power_fit, freq_point, fit_trend=fit_trend
    )
    if not fit.ok or fit.amplitude is None or fit.phase_rad is None:
        print(
            f"Sine fit rejected for freq {freq_point:.5f}: "
            f"{fit.rejection_reason or 'unknown sine-fit failure'}"
        )
        return False

    amp = float(fit.amplitude)
    phase = float(fit.phase_rad)
    offset = fit.c0
    r2 = fit.r_squared
    fitted = fit.predict(time_fit)

    scipy_amp = scipy_phase = scipy_offset = None
    scipy_r2 = None
    scipy_err_amp = scipy_err_phase = scipy_err_offset = None
    scipy_fitted = None
    if curve_fit is not None:
        try:
            # Match the primary fit's model: with --fit_trend the comparison
            # also carries the linear trend term (same window centering t_c).
            if fit_trend:
                t_c = 0.5 * (fit.fit_start + fit.fit_end)
                model = lambda t, a, ph, off, c1: sine_model_trend(
                    t, a, ph, off, c1, freq_point, t_c
                )
                p0 = [amp, phase, offset, fit.c1]
            else:
                model = lambda t, a, ph, off: sine_model(t, a, ph, off, freq_point)
                p0 = [amp, phase, offset]
            popt, pcov = curve_fit(model, time_fit, power_fit, p0=p0, maxfev=20000)
            scipy_amp, scipy_phase, scipy_offset = popt[:3].tolist()
            if fit_trend:
                scipy_fitted = model(
                    time_fit, scipy_amp, scipy_phase, scipy_offset, popt[3]
                )
            else:
                scipy_fitted = sine_model(
                    time_fit, scipy_amp, scipy_phase, scipy_offset, freq_point
                )
            residuals = power_fit - scipy_fitted
            ss_res = np.sum(residuals**2)
            ss_tot = np.sum((power_fit - power_fit.mean())**2)
            scipy_r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
            if pcov is not None and np.all(np.isfinite(pcov)):
                perr = np.sqrt(np.diag(pcov))
                if len(perr) >= 3:
                    scipy_err_amp, scipy_err_phase, scipy_err_offset = perr[:3].tolist()
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
    out_path = os.path.join(
        out_dir, f"fit_freq{format_frequency_key(freq_point)}.png"
    )
    plt.savefig(out_path, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_path}")
    return True


def main():
    args = parse_args()

    if not os.path.isdir(args.results_dir):
        raise SystemExit(f"Results dir not found: {args.results_dir}")

    params = read_run_params(args.results_dir)
    # Default to the collector's fit window: the settling fit start the
    # runner records (perturbation start + discard), else the perturbation
    # start of pre-protocol runs.
    ss_time = (
        args.ss_time
        if args.ss_time is not None
        else params.get("fit_start_time", params.get("ss_time", 2000.0))
    )

    out_dir = args.out_dir
    if out_dir is None:
        out_dir = os.path.join(args.results_dir, "fit_plots")

    if args.freq is None and not args.all:
        raise SystemExit("Specify --freq or --all")

    if args.freq is not None:
        ok = plot_single_freq(
            args.results_dir,
            out_dir,
            args.freq,
            ss_time,
            args.fit_end,
            args.show,
            args.dpi,
            fit_trend=args.fit_trend,
        )
        if not ok:
            raise SystemExit(1)
        return

    freqs = list_frequencies(args.results_dir)
    if not freqs:
        raise SystemExit("No frequency folders found.")

    failures = 0
    for fp in freqs:
        if not plot_single_freq(
            args.results_dir,
            out_dir,
            fp,
            ss_time,
            args.fit_end,
            args.show,
            args.dpi,
            fit_trend=args.fit_trend,
        ):
            failures += 1
    if failures:
        raise SystemExit(f"{failures} of {len(freqs)} frequencies failed to plot/fit")


if __name__ == "__main__":
    main()
