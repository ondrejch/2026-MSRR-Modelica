#!/usr/bin/env python3
"""Compare the coarse-grid rerun against the protocol-grid run at the
marginal-survivor point omega=1.73983 rad/s (both cores).

The protocol-grid CSV (66.5M rows, 0.602 s settle/window step) is read
from the tail only (the forcing window); the coarse-grid CSV (event-dense
0.05 s window rows) is read fully.  The coarse rows are interpolated onto
the protocol window times and compared per signal; the transfer-function
amplitude and phase at omega are estimated by single-frequency DFT over
the forcing window for both grids.

Prints, per core and signal: max/rms relative trace deviation (relative
to the window oscillation amplitude) and the DFT amplitude ratio +
phase difference (coarse minus protocol).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LOCAL = Path("00runs/paper-rerun-review-2026-09/freq")
OMEGA = 1.73983
T_FORCING = 4.0e7
T_STOP = 4.005e7
SIGNALS = ["n_population.n"]


def find_col(df: pd.DataFrame, want: str) -> str:
    for c in df.columns:
        name = str(c).strip().strip('"').casefold()
        if name == want.casefold() or name.endswith("." + want.casefold()):
            return c
    raise KeyError(want)


def read_protocol_window(core: str) -> pd.DataFrame:
    """Read the forcing-window tail of the protocol-grid result CSV."""
    path = LOCAL / core / "power_0p00001" / f"freq01.73983" / "MSRR_freq01.73983_res.csv"
    # Rows are uniform at ~0.602 s; the window starts at row T_FORCING/step.
    approx_step = 0.602
    skip = int(T_FORCING / approx_step) - 2000
    df = pd.read_csv(path, skiprows=range(1, skip))
    t = find_col(df, "time")
    return df[df[t] >= T_FORCING - 100.0].reset_index(drop=True)


def read_coarse(core: str, tmp: bool = True) -> pd.DataFrame:
    base = LOCAL / core / "power_0p00001_coarse_check" / "freq01.73983"
    path = base / ("MSRR_freq01.73983_res.csv.tmp" if tmp else "MSRR_freq01.73983_res.csv")
    if not path.exists():
        path = base / "MSRR_freq01.73983_res.csv"
    return pd.read_csv(path)


def dft_amp_phase(t: np.ndarray, y: np.ndarray, omega: float) -> tuple[float, float]:
    """Amplitude/phase at omega from a drift-aware LSQ fit
    y ~ c0 + c1*t + A*cos(wt) + B*sin(wt)."""
    X = np.column_stack(
        [np.ones_like(t), t - t[0], np.cos(omega * t), np.sin(omega * t)]
    )
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = coef[2], coef[3]
    return math.hypot(a, b), math.degrees(math.atan2(b, a))


def main() -> int:
    rc = 0
    for core in ("1r", "9r"):
        proto = read_protocol_window(core)
        coarse = read_coarse(core)
        t_p = proto[find_col(proto, "time")].to_numpy(float)
        t_c = coarse[find_col(coarse, "time")].to_numpy(float)
        proto = proto[(t_p >= T_FORCING) & (t_p <= T_STOP + 1.0)].reset_index(drop=True)
        mask_c = (t_c >= T_FORCING) & (t_c <= T_STOP + 1.0)
        t_c, coarse = t_c[mask_c], coarse[mask_c].reset_index(drop=True)
        t_p = proto[find_col(proto, "time")].to_numpy(float)
        t_c = coarse[find_col(coarse, "time")].to_numpy(float)
        print(f"== {core}: protocol window rows {len(proto)}, coarse window rows {len(coarse)}")
        for sig in SIGNALS:
            try:
                col_p, col_c = find_col(proto, sig), find_col(coarse, sig)
            except KeyError:
                print(f"   signal {sig}: not present, skipped")
                continue
            y_p = proto[col_p].to_numpy(float)
            y_c = coarse[col_c].to_numpy(float)
            y_ci = np.interp(t_p, t_c, y_c)
            amp = float(np.max(y_p) - np.min(y_p))
            dev = y_ci - y_p
            rel_max = float(np.max(np.abs(dev))) / amp
            rel_rms = float(np.sqrt(np.mean(dev**2))) / amp
            a_p, ph_p = dft_amp_phase(t_p, y_p, OMEGA)
            a_c, ph_c = dft_amp_phase(t_c, y_c, OMEGA)
            print(
                f"   {sig}: rel_dev max {rel_max:.3e} rms {rel_rms:.3e} | "
                f"DFT amp proto {a_p:.6e} coarse {a_c:.6e} "
                f"ratio {a_c / a_p:.6f} | dphase {ph_c - ph_p:+.4f} deg"
            )
            if rel_max > 0.02 or abs(a_c / a_p - 1.0) > 0.01:
                rc = 1
    print("PASS" if rc == 0 else "FAIL: deviation beyond tolerance")
    return rc


if __name__ == "__main__":
    sys.exit(main())
