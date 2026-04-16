# Low-Power Frequency-Run Instability: Current Findings

This note summarizes what we currently know about the low-power instability
seen in frequency-response runs (especially at power <= 0.001).

## Summary
- The low-power frequency runs show **non-stationary drift** in neutron
  population that is **not well fit by a sine**, especially for the 9r core.
- The drift looks **quadratic over the analysis window**, consistent with a
  slow thermal/kinetic bias rather than random noise.
- The issue is **power-dependent**: higher powers (>= 0.01) look stable and
  1r vs 9r Bode curves are close; at low power the 1r/9r gains diverge.
- A likely contributor was **radiative heat loss being enabled during frequency
  runs for 1r**, while 9r did not include the same loss. This has since been
  corrected by adding a `heatLossEnabled` setpoint flag and running new sweeps
  with heat loss disabled.

## Key Observations
- **Divergent low-power gain**: Bode plots show large differences between 1r
  and 9r at power 0.001 and below, while higher powers remain nearly identical.
  Example: `freq/BodePlots_reduced/BodePlot_compare_1r_9r_power_0p001.png`.
- **Poor sine fit at low power**: The 9r fits have very low R^2 for sine-only
  fits, indicating a strong non-sinusoidal component.
- **Quadratic drift component**: Fitting a parabola to the late-time response
  reveals a strong quadratic trend in 9r runs, weaker but present in 1r.
  This implies a slow bias/drift rather than pure noise.
- **Heat loss mismatch (fixed)**: The 1r model had `EnableRad` active in
  frequency runs, while 9r did not. This changes long-term thermal balance and
  can produce drift at low power even when the sine input is small. The mismatch
  is now fixed via `heatLossEnabled=0` in run3.

## Evidence and Artifacts
### Fit comparisons (power = 0.001)
Sine fits from `freq/plotFreqFits.py`:
- `freq/fit_plots_compare/power_0p001/1r/fit_freq00.10000.png`
- `freq/fit_plots_compare/power_0p001/1r/fit_freq01.00000.png`
- `freq/fit_plots_compare/power_0p001/9r/fit_freq00.10000.png`
- `freq/fit_plots_compare/power_0p001/9r/fit_freq01.00000.png`

Parabola fits (same windows):
- `freq/fit_plots_compare/power_0p001/1r/parabola_freq00.10000.png`
- `freq/fit_plots_compare/power_0p001/1r/parabola_freq01.00000.png`
- `freq/fit_plots_compare/power_0p001/9r/parabola_freq00.10000.png`
- `freq/fit_plots_compare/power_0p001/9r/parabola_freq01.00000.png`

Quadratic fit results (t >= 2000 s, t' = time - 2000):
- 1r, 0.1 rad/s: a = -1.5118e-11, b = 1.1359e-07, c = 1.0256e-03, R^2 = 0.5683
- 9r, 0.1 rad/s: a = 1.9957e-11,  b = -1.2297e-07, c = 4.6531e-04, R^2 = 0.9565
- 1r, 1.0 rad/s: a = -8.7619e-12, b = 7.1721e-08, c = 9.9620e-04, R^2 = 0.6652
- 9r, 1.0 rad/s: a = 1.8342e-11,  b = -1.2088e-07, c = 4.4525e-04, R^2 = 0.9788

These R^2 values show the quadratic component dominates the 9r signal at low
power.

## What We Tried (and what happened)
- **Changed sin_mag (5, 10, 20 pcm)**:
  - Using fixed magnitudes did **not remove the drift**.
  - Autoscaling (`--sin_mag_auto`) produced more consistent plots; no evidence
    that sin_mag alone was the root cause.
- **Forced fresh reruns**:
  - `--no-reuse` was added and used to guarantee fresh results.
  - Fresh runs in new base dirs (`freq/results_run2`, `freq/results_run2_10pcm`)
    still show low-power divergence, so reuse was not the culprit.
- **Dropped power=0**:
  - Zero-power runs are nonphysical; the signal is numerical noise and very
    low magnitude.
  - Power=0 is now skipped in `runFreqNominalParallelAllPowers.py`.
- **Heat-loss toggle and new setpoints**:
  - Added `heatLossEnabled` to setpoint tables and ability to append heat-loss
    on/off in the same CSV.
  - Frequency runs now filter on `heatLossEnabled=0` and add overrides so
    radiative heat loss is disabled during frequency analysis.
  - New full sweep produced `freq/results_run3` and overlay plots in
    `freq/BodePlots_run3` (both 1r and 9r complete). These need review to
    confirm low-power behavior is improved.

## Likely Root Causes (not yet proven)
1. **Setpoint generation disables feedback**:
   - `core/init/generateSetpointTable.py` forces `a_F = 0` and `a_G = 0` during
     setpoint generation.
   - That means setpoints are derived from a **different physics regime** than
     frequency runs (feedback on).
   - When feedback is later enabled, the “steady state” can be offset, causing
     long-term drift.
2. **Circulating-fuel bias term `rho_0sta`**:
   - In `mPKE` (`core/SMD_MSR_Modelica.mo`), reactivity includes:
     `+ rho_0sta` whenever flow > MinTau.
   - This is independent of temperature feedback and can introduce a constant
     bias if the setpoints were generated with feedback disabled.
3. **Low power amplifies drift vs signal**:
   - Thermal/feedback signals scale with power; drift terms do not.
   - At very low power, the drift becomes comparable to the oscillatory signal,
     producing the quadratic component and degrading fit quality.
4. **Higher-order dynamics in 9r**:
   - 9r has more thermal states and time constants, so long transient tails
     are more visible at low power.
5. **Heat-loss mismatch (1r vs 9r, now fixed)**:
   - 1r frequency runs previously had radiative heat loss enabled; 9r did not.
   - At low power this can create a net thermal bias even when feedback is near
     zero, leading to quadratic drift and lower sine-fit quality.
   - Run3 disables heat loss for frequency analysis; remaining divergence (if
     any) is likely from other causes.

## Open Questions / Next Checks
- Compute and plot **net reactivity terms** during low-power runs:
  - `feedback.rho`, `rho_0sta`, `externalReactivityIn`, and total `reactivity`.
  - Verify if net reactivity bias is non-zero even when temperatures match
    setpoints.
- Validate that the steady-state setpoints actually correspond to **closed-loop
  equilibrium** (feedback enabled), especially at low power.
- Confirm whether the drift is tied to the **flow-dependent `rho_0sta` term**
  by running a low-power case with it explicitly disabled for comparison.
- Inspect `freq/BodePlots_run3` to confirm whether disabling heat loss resolves
  the 1r/9r divergence at 0.001 and 0.01.

## Related Files
- Setpoint generation: `core/init/generateSetpointTable.py`
- Feedback math: `core/SMD_MSR_Modelica.mo` (ReactivityFeedback, mPKE)
- Frequency workflows: `freq/runFreqNominalParallel.py`,
  `freq/runFreqNominalParallelAllPowers.py`,
  `freq/plotFreqFits.py`
