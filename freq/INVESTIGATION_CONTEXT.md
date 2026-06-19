 Investigation Context (Setpoints + Frequency Analysis)

This file documents the investigation into setpoint corruption, steady‑state generation, and frequency‑response analysis. It includes what changed, how scripts are organized, what was tried, what worked, what didn’t, and where the outputs live.

## Summary

We suspected corrupted steady‑state setpoints after an IDE crash. The root cause was not confirmed, but the steady‑state generation workflow was hardened by introducing **no‑trip** nominal‑trim models and regenerating all setpoints from scratch. Low‑power instability and 1r vs 9r divergence were traced to **radiative heat loss being enabled during frequency analysis** for the 1r model. The fix was to add a `heatLossEnabled` flag and generate setpoints with and without heat loss in the same table, then run frequency sweeps with `heatLossEnabled=0`. A new run set (**results_run3**) was produced for both 1r and 9r and comparison plots were generated. Fit quality is still good at moderate/high powers, and low‑power behavior remains a focus area for interpretation.

## Status (2026‑03‑02)

- Setpoint tables now include a `heatLossEnabled` column (0/1) and store both cases in a single CSV per core model.
- Frequency runs were re‑done with `heatLossEnabled=0`, `--no-reuse`, reduced CSV outputs, and auto‑scaled perturbations.
- 1r results: `freq/results_run3/1r`
- 9r results: `freq/results_run3/9r`
- 1r vs 9r overlays: `freq/BodePlots_run3/BodePlot_compare_1r_9r_power_*.png`

## Repository Organization (relevant pieces)

**Modelica core**
- `core/MSRR.mo`
  - Contains the MSRR Modelica models (nominal trim, startup, thermal SS, etc.).
  - New **no‑trip** variants were added to isolate steady‑state and avoid trip logic interfering with setpoint generation.
- `core/SMD_MSR_Modelica.mo`
  - Package wrapper for Modelica compilation. A missing `end HeatExchanger;` caused OMC compile failures. This was fixed, along with non‑ASCII comment artifacts from the IDE crash.

**Setpoint generation**
- `core/init/generateSetpointTable.py`
  - Generates `core/init/setpoints_1r.csv` and `core/init/setpoints_9r.csv`.
  - Uses thermal steady‑state models and saves per‑power overrides used by other scripts.
  - Updated to use **no‑trip** models and adjusted stop times.

**Frequency response workflow**
- `freq/runFreqNominalParallel.py`
  - Launches per‑frequency OMC simulations in parallel.
  - Loads steady‑state overrides from `core/init/setpoints_<core_model>.csv`.
  - Forces nominal‑flow initialization (`primaryPump.freeConvFF=1`, `secondaryPump.freeConvFF=1`).
  - Filters steady‑state overrides by `heatLossEnabled` when present and adds `heatLossEnabled=true/false` to overrides.
  - Has `--reduced_csv_for_collect` to keep only time + power columns (critical for storage/perf).
- `freq/collectFreqNominalParallel.py`
  - Fits sine response and emits `FreqResponseResults.csv`/`.m` plus optional `BodePlot.png`.
- `freq/plotBodeCompareCoreModels.py`
  - Overlays 1r vs 9r Bode plots for selected powers.
- `freq/runFreqNominalParallelAllPowers.py`
  - Convenience wrapper to run all powers from the setpoint table.
  - Accepts `--heat_loss` and forwards it to the per‑power runner.

**Outputs**
- `core/init/setpoints_1r.csv`, `core/init/setpoints_9r.csv`
- `freq/results_reduced` and `freq/BodePlots_reduced`
- `freq/results_reduced_auto` and `freq/BodePlots_reduced_auto`
- `freq/results_run3` and `freq/BodePlots_run3`

## Changes Made

**1) No‑trip models added (steady‑state generation safety)**

In `core/MSRR.mo`, added:
- `MSRRuhxNominalTrimNoTrips`
- `MSRRuhxNominalTrimThermalSSNoTrips`
- `MSRRuhxNominalTrim9RNoTrips`
- `MSRRuhxNominalTrim9RThermalSSNoTrips`

These are nominal‑trim variants with trip logic disabled, used only for steady‑state setpoint generation.

**2) Modelica package fix**

In `core/SMD_MSR_Modelica.mo`:
- Replaced non‑ASCII comment artifacts introduced after the IDE crash.
- Inserted missing `end HeatExchanger;` so OpenModelica compiles the package correctly.

**3) Steady‑state setpoint generation updated**

In `core/init/generateSetpointTable.py`:
- Switched to **no‑trip** models for both 1r and 9r.
- Reduced overrides and tuned stop times.
- Notable settings:
  - 1r at power `0.01`: stop time 300k.
  - 1r at power `0.001`: required 600k to reduce drift.
  - 9r at power `0.1`: stop time 300k.

**4) Test runs for steady‑state correctness**

Using `tests/testSetpointLongLowPower.py` with the no‑trip ThermalSS models:
- 1r power `0.01`, 300k: steady (slopes ~1e‑17).
- 1r power `0.001`, 300k: slight drift; increased to 600k.
- 1r power `0.001`, 600k: drift improved (slope ~‑2.89e‑6 C/s, rel_delta ~‑1e‑4 across tail) and accepted.
- 9r power `0.1`, 300k: steady (slopes ~1e‑14).

**5) Setpoints regenerated**

Commands used:
- `python3 core/init/generateSetpointTable.py --core_model 1r --n_jobs 4`
- `python3 core/init/generateSetpointTable.py --core_model 9r --n_jobs 4`

Outputs:
- `core/init/setpoints_1r.csv`
- `core/init/setpoints_9r.csv`

Quick integrity check:
- No negative or NaN rows in either table.

**6) Tests**

`pytest tests/test_msrrv2.py -k setpoint` passed (2 tests).

**7) Heat‑loss toggle for startup vs frequency analysis**

In `core/MSRR.mo`:
- Added `heatLossEnabled` and `heatLossTinf` parameters to `R1MSRRuhx` and `R9MSRRuhx`.
- Core components now use `EnableRad = heatLossEnabled` (1r) and `EnableRad = heatLossEnabled` (9r).
- Startup models explicitly enable heat loss using `heatLossEnabled = true`.
- Default behavior for frequency analysis is **no heat loss** via overrides (`heatLossEnabled=false`).

**8) Setpoint tables now include heat‑loss flag**

In `core/init/generateSetpointTable.py`:
- Added `--heat_loss` and `--append`.
- Added `heatLossEnabled` column to the CSV output (0/1).
- Tables now store two rows per power: heat loss off and on.

Related updates:
- `core/init/testSetpointLongLowPower.py` accepts `--heat_loss`.
- `freq/runFreqNominal.py` and `freq/runFreqNominalParallel.py` accept `--heat_loss` and filter the table by `heatLossEnabled`.
- `freq/runFreqNominalParallelAllPowers.py` accepts `--heat_loss`.
- Documentation updates in `core/init/README.md`, `freq/README.md`, and `latex/MSRR_journal_article/msrr_system_dynamics.tex`.

**9) Minimum flow fraction consistent**

In `core/MSRR.mo`, pump `freeConvFF` is now 0.01 (1%) across 1r/9r models to represent minimum flow during trip/coast‑down.
Note: `freq/runFreqNominalParallel.py` currently overrides `primaryPump.freeConvFF=1` and `secondaryPump.freeConvFF=1`, which will supersede the 0.01 defaults for frequency runs unless removed.

## Frequency Analysis Runs

### Baseline full sweep (fixed sin_mag = 1 pcm)

Run parameters:
- `omega`: 1e‑2 to 1e1 rad/s
- `N`: 64 points
- `n_jobs`: 18
- Powers (from setpoint tables):
  - `0, 1e‑5, 1e‑4, 0.001, 0.01, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2`
- Both `1r` and `9r`.

Storage and performance:
- Original run produced huge CSVs (~477 MB per frequency). Collect step was slow and disk‑heavy (~31 GB for 1r power 0.01 alone). This was the main bottleneck.
- Solution: re‑run with `--reduced_csv_for_collect` to keep only time + power columns.

Full reduced‑CSV run outputs:
- Results: `freq/results_reduced/<core>/power_<tag>/FreqResponseResults.csv`
- Per‑power Bode: `freq/results_reduced/<core>/power_<tag>/BodePlot.png`
- Overlay plots: `freq/BodePlots_reduced/BodePlot_compare_1r_9r_power_<tag>.png`

Fit quality (R²) summary from `freq/results_reduced`:
- 1r:
  - `0, 1e‑5, 1e‑4`: R² ~ 0 (fits meaningless).
  - `0.001`: median R² ≈ 0.861 (58% below 0.9).
  - `0.01`: median R² ≈ 0.923 (41% below 0.9).
  - `0.1` and above: R² >= ~0.93 everywhere (good).
- 9r:
  - `0` through `0.01`: R² ~ 0 (fits meaningless).
  - `0.1` and above: R² >= ~0.93 everywhere (good).

Interpretation:
- Low‑power fits are dominated by poor SNR. At moderate/high power, the fits are robust.

### Low‑power rerun with `--sin_mag_auto`

Goal:
- Improve fit quality for low powers by increasing perturbation amplitude.

Run parameters:
- Powers: `0, 1e‑5, 1e‑4, 0.001, 0.01`
- `--sin_mag_auto` enabled
  - For very low powers, this clamps to 20 pcm.
  - For 0.01 MW, it yields 10 pcm.

Outputs:
- Results: `freq/results_reduced_auto/<core>/power_<tag>/FreqResponseResults.csv`
- Per‑power Bode: `freq/results_reduced_auto/<core>/power_<tag>/BodePlot.png`
- Overlay plots: `freq/BodePlots_reduced_auto/*.png`

Fit quality (R²) summary from `freq/results_reduced_auto`:
- 1r:
  - `0`: R² ~ 0.
  - `1e‑5`: median R² ≈ 0.097.
  - `1e‑4`: median R² ≈ 0.115.
  - `0.001`: median R² ≈ 0.378.
  - `0.01`: median R² ≈ 0.800.
- 9r:
  - `0`: R² ~ 0.
  - `1e‑5`: median R² ≈ 0.0099.
  - `1e‑4`: median R² ≈ 0.0197.
  - `0.001`: median R² ≈ 0.0689.
  - `0.01`: median R² ≈ 0.0225.

Result:
- Auto‑scaling did **not** fix low‑power convergence. In many cases, it was worse or still far below acceptable R² thresholds.

### Run3 (heat loss disabled, no‑reuse, updated setpoints)

Run parameters:
- `omega`: 1e‑2 to 1e1 rad/s
- `N`: 64 points
- `n_jobs`: 18
- `--sin_mag_auto`
- `--reduced_csv_for_collect`
- `--cleanup_omc_artifacts`
- `--no-reuse`
- `--heat_loss 0`
- Powers (from setpoint tables, heatLossEnabled=0):
  - `1e‑5, 1e‑4, 0.001, 0.01, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2`

Outputs:
- Results: `freq/results_run3/1r/power_<tag>/FreqResponseResults.csv`
- Results: `freq/results_run3/9r/power_<tag>/FreqResponseResults.csv`
- Overlays: `freq/BodePlots_run3/BodePlot_compare_1r_9r_power_<tag>.png`

Status:
- 1r run complete.
- 9r run complete.
- Overlays generated for all listed powers.

## What Worked

- No‑trip models eliminated trip‑induced disturbances during setpoint generation.
- Regenerating setpoints from scratch with the no‑trip models produced stable tables.
- Frequency analysis with reduced CSV outputs completed efficiently and produced reliable fits for power >= 0.1 (both 1r and 9r).

## What Didn’t Work

- Full‑CSV frequency runs generated enormous output and stalled the collect step.
- `--sin_mag_auto` did **not** improve low‑power fit quality.
- Low‑power (`<= 0.01`) fits are not reliable under current settings.

## Relevant Paths and Artifacts

**Setpoints**
- `core/init/setpoints_1r.csv`
- `core/init/setpoints_9r.csv`

**Frequency results (fixed 1 pcm)**
- `freq/results_reduced/1r/power_<tag>/FreqResponseResults.csv`
- `freq/results_reduced/9r/power_<tag>/FreqResponseResults.csv`
- `freq/BodePlots_reduced/BodePlot_compare_1r_9r_power_<tag>.png`

**Frequency results (sin_mag_auto for low powers)**
- `freq/results_reduced_auto/1r/power_<tag>/FreqResponseResults.csv`
- `freq/results_reduced_auto/9r/power_<tag>/FreqResponseResults.csv`
- `freq/BodePlots_reduced_auto/BodePlot_compare_1r_9r_power_<tag>.png`

**Frequency results (run3, heat loss disabled)**
- `freq/results_run3/1r/power_<tag>/FreqResponseResults.csv`
- `freq/results_run3/9r/power_<tag>/FreqResponseResults.csv`
- `freq/BodePlots_run3/BodePlot_compare_1r_9r_power_<tag>.png`

**Per‑power Bode plots**
- `freq/results_reduced/<core>/power_<tag>/BodePlot.png`
- `freq/results_reduced_auto/<core>/power_<tag>/BodePlot.png`

## Notes / Warnings Observed

- Matplotlib warning: `Axes3D` not available (multiple matplotlib versions). This does not affect 2D Bode plots.
- `FigureCanvasAgg` warning (`plt.show()` on non‑interactive backend). Plots are still saved correctly.

## Run4 Low-Frequency Extension and 0.01 MW Resolution (2026-03-26)

This section supersedes the older conclusion that `--sin_mag_auto` simply did
not help low-power fits. The main issue at `0.01 MW` was identified and a
workable default was added.

### Run4 setup

- Frequency grid extended to `1e-3` to `1e1 rad/s` with `80` log-spaced bins.
- Main outputs:
  - `freq/results_run4/1r`
  - `freq/results_run4/9r`
  - `freq/BodePlots_run4_compare`
- Slow-band reruns used dynamic stop time:
  - `--stop_time_mode min_cycles_after_ss`
  - `--min_cycles_after_ss 12`

### What went wrong at 0.01 MW

For `power = 0.01 MW`, extending `stop_time` exposed a low-frequency problem in
the first 20 bins (`1e-3` to `9.16273901188673e-3 rad/s`):

- whole-window sine fits became poor even though enough cycles were present
- gain and phase changed significantly from one late-time window to the next
- increasing `ss_time` from `2000 s` to `40000 s` did not materially change the
  bad low-frequency fits

Conclusion:

- the issue was not "too few cycles"
- the issue was not solved by a longer pre-forcing settle
- the problem was nonstationary response under too-large forcing in the slow
  low-power regime

### Evidence that forcing amplitude was the cause

The old `--sin_mag_auto` rule gave `10 pcm` at `0.01 MW`.

Dedicated reruns of the `0.01 MW` slow band were then made with:

- `sin_mag = 1 pcm`
- `ss_time = 40000 s`
- dynamic stop time with at least `12` post-forcing cycles

Outputs:

- `freq/results_0p01_slowfreq_sin1_ss4e4_cycles12/1r/power_0p01`
- `freq/results_0p01_slowfreq_sin1_ss4e4_cycles12/9r/power_0p01`

Result:

- fit quality improved strongly across the slow band for both cores
- windowed gain/phase drift was much smaller
- the low-frequency issue was therefore treated as a large-signal forcing
  artifact, not a trustworthy physical spike or resonance

Diagnostic artifacts:

- `freq/results_run4_diagnostics/power_0p01_windowed/windowed_fit_run4_sin10_1r_slowbins.png`
- `freq/results_run4_diagnostics/power_0p01_windowed/windowed_fit_run4_sin10_9r_slowbins.png`
- `freq/results_run4_diagnostics/power_0p01_windowed/windowed_fit_rerun_sin1_1r_slowbins.png`
- `freq/results_run4_diagnostics/power_0p01_windowed/windowed_fit_rerun_sin1_9r_slowbins.png`
- `freq/results_run4_diagnostics/power_0p01_windowed/summary_compare_run4_vs_sin1_0p01.csv`

### Final fix kept in the code

`freq/runFreqNominalParallel.py` now applies a default low-power slow-frequency
cap when `--sin_mag_auto` is enabled:

- if `power <= 1e-2`
- and `omega <= 1e-2 rad/s`
- then cap `sin_mag` at `1 pcm`
- above `1e-2 rad/s`, keep the usual auto-scaled amplitude

CLI controls:

- `--low_power_slowfreq_sin_mag_cap`
- `--low_power_slowfreq_sin_mag_cap_freq`
- `--disable_low_power_slowfreq_sin_mag_cap`

For the standard `80`-bin `run4` grid at `0.01 MW`, this means:

- first `20` bins use `1 pcm`
- remaining `60` bins keep the auto-scaled `10 pcm`

### Accepted 0.01 MW plots

To avoid rewriting the original `run4` raw results, the accepted `0.01 MW`
plots were regenerated from a merged summary-only tree:

- `freq/results_0p01_default_autoforcing/1r/power_0p01/FreqResponseResults.csv`
- `freq/results_0p01_default_autoforcing/9r/power_0p01/FreqResponseResults.csv`

Source split:

- slow band (`<= 9.16273901188673e-3 rad/s`): from the `1 pcm` rerun
- higher frequencies: from `freq/results_run4`

Plot outputs updated from that merged tree:

- `freq/BodePlots_run4/BodePlot_1r_power_0p01.png`
- `freq/BodePlots_run4/BodePlot_9r_power_0p01.png`
- `freq/BodePlots_run4_compare/BodePlot_compare_1r_9r_power_0p01.png`

Provenance files:

- `freq/results_0p01_default_autoforcing/1r/power_0p01/MERGED_FROM.txt`
- `freq/results_0p01_default_autoforcing/9r/power_0p01/MERGED_FROM.txt`

## Lower-Power Resolution Below 0.01 MW (2026-03-26)

Follow-up investigations were then run for `0.001 MW`, `1e-4 MW`, and `1e-5 MW`
through `helpers/omc_gw`.

### Controlled rerun matrix

For each power and for both `1r` and `9r`, the slow band
`1e-3` to `9.16273901188673e-3 rad/s` (`20` bins) was rerun with:

- reduced CSV output
- dynamic stop time with at least `12` post-forcing cycles
- frequency-scaled output grid (`6` samples/period target, `50 s` max step)

Two forcing cases were compared:

- long settle + original forcing
- long settle + reduced forcing (`1 pcm`)

The long-settle rule that matched all accepted cases was:

- `ss_time = 400/power`
- base `stop_time = ss_time + min(5e4, ss_time/4)`

This gives:

- `0.01 MW`: `ss_time=4e4 s`, `stop_time=5e4 s`
- `0.001 MW`: `ss_time=4e5 s`, `stop_time=4.5e5 s`
- `1e-4 MW`: `ss_time=4e6 s`, `stop_time=4.05e6 s`
- `1e-5 MW`: `ss_time=4e7 s`, `stop_time=4.005e7 s`

### Findings

`0.001 MW`

- `run4`: median slow-band `R^2 = 0.233 / 0.360` (`1r / 9r`), median
  cross-core gain split `0.778 dB`, max split `5.115 dB`
- long settle + old forcing: gain split shrank, but fit quality stayed poor
- long settle + `1 pcm`: median `R^2 = 0.542 / 0.587`, median gain split
  `0.010 dB`, max split `0.026 dB`

`1e-4 MW`

- `run4`: median slow-band `R^2 = 0.268 / 0.390`, median gain split
  `4.195 dB`, max split `6.799 dB`
- long settle + old forcing: gain split collapsed (`0.016 dB` median) but fit
  quality stayed poor (`R^2 ~ 0.085`)
- long settle + `1 pcm`: median `R^2 = 0.517 / 0.520`, median gain split
  `0.004 dB`, max split `0.007 dB`

`1e-5 MW`

- `run4`: median slow-band `R^2 = 0.360 / 0.881`, median gain split
  `9.614 dB`, max split `13.195 dB`
- long settle + old forcing: gain split collapsed (`0.027 dB` median) but fit
  quality remained weak (`R^2 ~ 0.205`)
- long settle + `1 pcm`: median `R^2 = 0.804 / 0.804`, median gain split
  `0.006 dB`, max split `0.006 dB`

Interpretation:

- the nonphysical `1r`/`9r` gain mismatch at these powers was mainly a
  low-power stabilization / large-signal protocol artifact
- long settle time alone fixes most of the cross-core mismatch, but not the fit
  quality
- reduced forcing (`1 pcm` in the slow band) is still required to recover good
  small-signal fits

### Code default updated

`freq/runFreqNominalParallel.py` now defaults to the full low-power protocol for
`power <= 1e-2` unless disabled:

- long-horizon time rule:
  - `ss_time >= 400/power`
  - `stop_time >= ss_time + min(5e4, ss_time/4)`
  - `stop_time_mode=min_cycles_after_ss`
  - `min_cycles_after_ss >= 12`
- frequency-scaled output grid:
  - `output_interval_mode=frequency_scaled`
  - `10 intervals/s` legacy minimum resolution
  - `6` samples/period target
  - `50 s` max step
- existing slow-bin `1 pcm` cap under `--sin_mag_auto` remains in place

Opt-out controls:

- `--disable_low_power_auto_time_horizon`
- `--disable_low_power_auto_output_grid`
- `--disable_low_power_slowfreq_sin_mag_cap`

### Accepted low-power summary tree

Accepted low-power plots are now summarized in:

- `freq/results_lowpower_default_protocol`

This tree contains:

- `0.01 MW`: the earlier accepted `1 pcm` slow-band merge
- `0.001 MW`, `1e-4 MW`, `1e-5 MW`: reduced-forcing slow-band reruns merged
  with the original `run4` higher-frequency bins

Updated plot outputs:

- `freq/BodePlots_run4/BodePlot_1r_power_0p001.png`
- `freq/BodePlots_run4/BodePlot_1r_power_0p0001.png`
- `freq/BodePlots_run4/BodePlot_1r_power_0p00001.png`
- matching `9r` plots
- compare overlays in `freq/BodePlots_run4_compare`

Comparison artifacts:

- `freq/results_run4_diagnostics/power_0p001_compare/summary_compare_run4_vs_longss.csv`
- `freq/results_run4_diagnostics/power_0p0001_compare/summary_compare_run4_vs_longss.csv`
- `freq/results_run4_diagnostics/power_0p00001_compare/summary_compare_run4_vs_longss.csv`

### Remote execution convention retained from this recovery

For future `helpers/omc_gw` frequency campaigns, keep the remote layout at:

- `1` sweep job per worker
- `--tasks 16` on the gateway submission
- `--n_jobs 16` inside the frequency runner

Do not colocate multiple sweep jobs on a worker unless there is a specific
reason to trade wall-clock throughput for higher remote filesystem pressure.
Earlier multi-job layouts saturated remote I/O bandwidth and made incomplete
tail reruns harder to diagnose and recover cleanly.

The temporary `8`-task tail reruns used later on March 26, 2026 were only a
conservative repair step for already-running low-power high-frequency reruns.
They are not the preferred steady-state submission policy.

### Final `1e-5 MW` high-frequency repair

The remaining unresolved low-power artifact after the earlier fixes was the
`1e-5 MW` upper tail (`omega >= 1.739828052930364 rad/s`) for `9r`.

What failed:

- the shorter-horizon `9r` tail rerun at `ss_time = 4e6 s` improved fit
  quality, but still left nonphysical `1r/9r` gain splits in the upper tail
  (`1.5` to `3.6 dB` at the worst bins)

Probe result that resolved the issue:

- targeted single-frequency `9r` probes with:
  - `sin_mag = 1 pcm`
  - `ss_time = 1e7 s`
  - `stop_time = 1.005e7 s`
  - `output_samples_per_period = 2`
  - `output_step_max = 100 s`
  - collection fit window `10049900` to `10050000 s`
- these collapsed the tested worst-bin splits to about `0.07` to `0.11 dB`

Accepted final repair:

- reran the full `9r` upper tail in four remote `omc_gw` groups:
  - `freq/results_lowpower_hifreq_fixed_v8_1em05_9r_tail_ss1e7_spp2_split`
- collected each group remotely with `fit_start = 10049900 s`
- stitched the final `9r` tail summary:
  - `freq/results_lowpower_hifreq_fixed_v8_1em05_9r_tail_ss1e7_spp2_split/9r/power_0p00001/FreqResponseResults.csv`
- aligned the stitched tail frequencies to the canonical `1r` tail grid for
  exact cross-core row matching

Accepted final merged root:

- `freq/results_lowpower_default_protocol_hifreqfix_final`

Final `1e-5 MW` result quality:

- exact merged rows across `1r` and `9r`: `80`
- full-curve median gain split: about `0.0056 dB`
- upper-tail median gain split: about `0.14 dB`
- upper-tail max gain split: about `0.40 dB`
- upper-tail median `R^2`: about `0.9999 / 0.9982` (`1r / 9r`)

Final updated plot outputs:

- `freq/BodePlots_run4/BodePlot_1r_power_0p00001.png`
- `freq/BodePlots_run4/BodePlot_9r_power_0p00001.png`
- `freq/BodePlots_run4_compare/BodePlot_compare_1r_9r_power_0p00001.png`

### Final `1e-5 MW` high-frequency phase correction

After the final accepted `1e-5 MW` gain repair, the upper-tail phase still
looked unstable at high frequency even though:

- `R_squared` stayed near `1`
- `1r` and `9r` gains agreed closely

Root cause:

- the collector fit is performed on `time - fit_start`
- the fitted phase was being written directly as `phase_deg`
- that made phase implicitly referenced to `fit_start`, not to the forcing
  activation time (`perturbationStartTime`, stored in the workflows as
  `ss_time`)
- this was hidden in earlier runs because most accepted collections used
  `fit_start == ss_time`
- it became obvious in the accepted `1e-5 MW` upper-tail repairs because the
  final late windows used:
  - `1r`: `fit_start = 4049900 s`, `ss_time = 4000000 s`
  - `9r`: `fit_start = 10049900 s`, `ss_time = 10000000 s`

Fix:

- `collectFreqNominal.py` and `collectFreqNominalParallel.py` now convert the
  fitted phase back to the perturbation-start reference before saving
  `phase_deg`
- mathematically, the correction is:
  - `phase_ref = phase_fit - omega * (fit_start - ss_time)`
  - then wrap to `[-180, 180]`

Artifacts updated after the fix:

- `freq/results_lowpower_hifreq_fixed_v4_1em05_tail_ss4e6/1r/power_0p00001/FreqResponseResults.csv`
- `freq/results_lowpower_hifreq_fixed_v8_1em05_9r_tail_ss1e7_spp2_split/9r/power_0p00001/FreqResponseResults.csv`
- `freq/results_lowpower_default_protocol_hifreqfix_final`
- `freq/BodePlots_run4/BodePlot_1r_power_0p00001.png`
- `freq/BodePlots_run4/BodePlot_9r_power_0p00001.png`
- `freq/BodePlots_run4_compare/BodePlot_compare_1r_9r_power_0p00001.png`

### Nominal-power presentation-only high-frequency extension (`50` to `1000 rad/s`)

Problem observed:

- the separate nominal-power extension used for the presentation looked
  numerically unstable above about `100 rad/s`
- visible symptoms in the original extension:
  - `1r` worst high-band bins: `794.3 rad/s` (`R^2 ≈ 0.61`) and
    `1000 rad/s` (`R^2 ≈ 0.89`)
  - `9r` worst high-band bins: `707.9 rad/s` (`R^2 ≈ 0.84`) and
    `1000 rad/s` (`R^2 ≈ 0.85`)
  - phase jumps up to about `8` to `9 deg`
  - nonphysical cross-core high-band splits up to about `1.05 dB` and
    `12.35 deg`

What was tested with `omc_gw`:

- first probe: explicit `forcingTimeStep = 5e-4 s`
  - this was not the fix; it made `1r` fits much worse
- second probe: denser CSV output only
  - this had negligible effect at `1000 rad/s`
- third probe: DASSL max-step cap only
  - `simflags_extra=-maxStepSize=5e-5`
  - this immediately repaired the bad `1r` bins and strongly improved the bad
    `9r` bins
- fourth probe: denser CSV output plus tighter max-step caps
  - this showed the remaining limitation at `9r, 1000 rad/s` was not the CSV
    output grid
  - even there, the `1r`/`9r` gain and phase became mutually consistent once
    the max-step cap was applied

Accepted diagnosis:

- the presentation-only nominal extension above `100 rad/s` was dominated by
  under-resolved DASSL internal stepping, not by physical model behavior
- increasing saved output density alone does not fix the issue
- the accepted repair is to cap DASSL step size in the top decade

Accepted repair run:

- reran the `100` to `1000 rad/s` band (`21` log-spaced bins) for both `1r`
  and `9r` with:
  - `power = 1.0 MW`
  - `sin_mag = 1 pcm`
  - `ss_time = 20 s`
  - `stop_time = 100 s`
  - `output_interval_mode = fixed_rate`
  - `output_intervals_per_second = 3000`
  - `simflags_extra=-maxStepSize=5e-5`
  - collector fit window `95` to `100 s`
- raw corrected reruns:
  - `freq/results_nominal_hifreq_maxstep5e-5`
- after confirming that this removed the visible `>100 rad/s` instability,
  reran the full original extension suffix from `50.118723 rad/s` to
  `1000 rad/s` (`27` bins) with the same settings:
  - `freq/results_nominal_hifreq_maxstep5e-5_from50`

Corrected quality:

- `1r` high-band minimum `R^2`: `0.891 -> 0.992`
- `9r` high-band minimum `R^2`: `0.839 -> 0.893`
- `1r` maximum phase jump across adjacent bins: `8.08 -> 2.26 deg`
- `9r` maximum phase jump across adjacent bins: `9.10 -> 2.26 deg`
- cross-core maximum gain split: `1.046 -> 0.040 dB`
- cross-core maximum phase split: `12.35 -> 0.024 deg`

Accepted corrected merged roots:

- extension-only:
  - `freq/results_nominal_hifreq_extension_corrected`
- presentation merged `1e-3` to `1e3 rad/s` tree:
  - `freq/results_nominal_hifreq_extension_merged_corrected`

Updated presentation artifact:

- `latex/MSRR_journal_article/figs/BodePlot_compare_1r_9r_power_1_to1e3.png`
