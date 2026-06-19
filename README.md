# SMD-MSRR-dev

Modelica models and Python tooling for MSRR startup and nominal frequency-response studies.

## Repository Structure

- `core/`: shared Modelica sources used by all workflows.
  - `core/SMD_MSR_Modelica.mo`: component library (nuclear kinetics, heat transport, pumps, signals).
  - `core/MSRR.mo`: system models with both 1-region and 9-region core variants.
  - `core/init/`: steady-state setpoint table generator and CSV setpoint tables.
- `startup/`: startup-focused runners and plotting scripts.
- `freq/`: nominal frequency sweep, collection, and plotting workflows.
- `helpers/`: legacy depletion/sensitivity scripts and older workflows.
- `tests/`: OpenModelica integration tests.

## Main Model Variants (Core)

- 1-region:
  - `MSRR.R1MSRRuhx`
  - `MSRR.MSRRuhxNominalTrim`
  - `MSRR.MSRRstartUpTo100kW`
- 9-region:
  - `MSRR.R9MSRRuhx`
  - `MSRR.MSRRuhxNominalTrim9R`
  - `MSRR.MSRRstartUpTo100kW9R`

## Previous Work

Relevant previous MSR dynamic-modeling papers of  Dr. Chvala's research group are listed below. 
BibTeX entries are in `previous_work.bib`.

1. Singh, V., Wheeler, A. M., Lish, M. R., Chvala, O., and Upadhyaya, B. R. Nonlinear Dynamic Model of Molten-Salt Reactor Experiment: Validation and Operational Analysis. *Annals of Nuclear Energy*, 113, 177-193 (2018). https://doi.org/10.1016/j.anucene.2017.10.047
2. Pathirana, V., Chvala, O., and Wheeler, A. M. Scalable Modular Dynamic Molten Salt Reactor System Model with Decay Heat. *Annals of Nuclear Energy*, 154, 108060 (2021). https://doi.org/10.1016/j.anucene.2020.108060
3. Wheeler, A. M., Chvala, O., and Skutnik, S. E. Signatures of Plutonium Diversion in Molten Salt Reactor Dynamics. *Annals of Nuclear Energy*, 160, 108370 (2021). https://doi.org/10.1016/j.anucene.2021.108370
4. Pathirana, V., Chvala, O., and Skutnik, S. E. Depletion Dependency of Molten Salt Reactor Dynamics. *Annals of Nuclear Energy*, 168, 108852 (2022). https://doi.org/10.1016/j.anucene.2021.108852
5. Creasman, T. D., Pathirana, V., and Chvala, O. Sensitivity Study of Parameters Important to Molten Salt Reactor Safety. *Nuclear Engineering and Technology*, 55(5), 1687-1707 (2023). https://doi.org/10.1016/j.net.2023.02.002
6. Pathirana, V., Creasman, T., Chvala, O., and Skutnik, S. E. Molten Salt Reactor System Dynamics in Simulink and Modelica: A Code-to-Code Comparison. *Nuclear Engineering and Design*, 413, 112484 (2023). https://doi.org/10.1016/j.nucengdes.2023.112484
7. Dunkle, N., Richardson, J., Pathirana, V., Wheeler, A., Chvala, O., and Skutnik, S. E. NERTHUS Thermal Spectrum Molten Salt Reactor Neutronics and Dynamic Model. *Nuclear Engineering and Design*, 411, 112390 (2023). https://doi.org/10.1016/j.nucengdes.2023.112390
8. Dunkle, N., Chvala, O., Effect of xenon removal rate on load following in high power thermal spectrum Molten-Salt Reactors. *Nuclear Engineering and Design*, 409, 112329 (2023). https://doi.org/10.1016/j.nucengdes.2023.112329

## Requirements

- OpenModelica (`omc`) on `PATH`.
- Python 3.12 recommended.
- Install options:

```bash
# Recommended: editable install with test extras.
python3.12 -m pip install -e .[test]

# Alternative for requirements-file workflows.
python3.12 -m pip install -r requirements-dev.txt
python3.12 -m pip install -e .
```

The project now ships `pyproject.toml`, `setup.py`, `requirements.txt`, and
`requirements-dev.txt`.
Installing in editable mode makes the workflow modules importable as regular packages such as
`startup.runMSRR`, `freq.runFreqNominalParallel`, and `core.init.generateSetpointTable`.
It also installs console scripts such as `msrr-startup-run`,
`msrr-freq-run-parallel`, and `msrr-setpoints-generate`.

## Quick Start

### 1) Run startup simulation

From repository root:

```bash
python3.12 -m startup.runMSRR --core_model 1r --scenario startup_to_100kw
python3.12 -m startup.runMSRR --core_model 9r --scenario startup_to_100kw
```

Default startup run path convention:

```bash
00runs/startup-<scenario>-<core_model>
```

Examples:

- `00runs/startup-startup_to_100kw-1r`
- `00runs/startup-startup_to_100kw-9r`

Useful options:

- `--run_dir`
- `--core_dir`
- `--model_name`
- `--stop_time`
- `--number_of_intervals`
- `--max_step_size`

### 2) Plot startup results

From repository root:

```bash
python3.12 -m startup.plotStartUpTo100kW --core_model 1r
python3.12 -m startup.plotStartUpWithSource --core_model 1r
python3.12 -m startup.plotStartUp --core_model 1r
```

By default, each plotting script reads the CSV from the matching
`00runs/startup-<scenario>-<core_model>` run directory and writes plots back
into that same directory.

## Frequency Response Workflow (Nominal Trim)

### 1) Sweep frequencies

Serial:

```bash
python3.12 -m freq.runFreqNominal --core_model 1r --power 1.0
python3.12 -m freq.runFreqNominal --core_model 9r --power 1.0
```

Parallel:

```bash
python3.12 -m freq.runFreqNominalParallel --core_model 1r --power 1.0 --n_jobs 8
python3.12 -m freq.runFreqNominalParallel --core_model 9r --power 1.0 --n_jobs 8
```

Default frequency run path convention:

```bash
00runs/freq/<core_model>/power_<power_tag>
```

Examples:

- `00runs/freq/1r/power_1`
- `00runs/freq/9r/power_1`

For large sweeps, add:

- `--reduced_csv_for_collect` to keep only the columns used by collection.
- `--stop_time_mode min_cycles_after_ss --min_cycles_after_ss 12` to rerun only
  the slowest frequencies with longer trajectories while reusing the existing
  faster ones in place.
- `--cleanup_omc_artifacts` to remove OpenModelica temporary build/runtime files after each successful run.

### 2) Collect Bode data

```bash
python3.12 -m freq.collectFreqNominal --core_model 1r --power 1.0 --plot
```

or

```bash
python3.12 -m freq.collectFreqNominalParallel --core_model 1r --power 1.0 --plot --n_jobs 8
```

### 2b) Low-Power / High-Frequency Methodology

For `power <= 1e-2`, reproduce the accepted low-power results with the default
runner settings in `runFreqNominalParallel.py`:

- long-horizon forcing:
  - `ss_time >= 400/power`
    - `0.01 MW`: `4e4 s`
    - `0.001 MW`: `4e5 s`
    - `1e-4 MW`: `4e6 s`
    - `1e-5 MW`: `4e7 s`
  - `stop_time >= ss_time + min(5e4, ss_time/4)`
  - `--stop_time_mode min_cycles_after_ss --min_cycles_after_ss 12`
- steady-state pump conditions:
  - `primaryPump.freeConvFF=1`
  - `secondaryPump.freeConvFF=1`
- neutron floor during forcing:
  - `nFloor=1e-9`
  - `nFloorDuringForcing=1e-9`
- forcing cadence:
  - `omega <= 1 rad/s`: `forcingTimeStep=1.0 s`
  - `omega > 1 rad/s`: `forcingTimeStep=0.05 s`
- output grid:
  - `output_interval_mode=frequency_scaled`
  - minimum `10` intervals/s
  - target `6` samples per forcing period
  - `output_step_max=50 s`
- perturbation amplitude with `--sin_mag_auto`:
  - `power <= 1e-2` and `omega <= 1e-2 rad/s`: `sin_mag=1 pcm`
  - `power <= 1e-3`: `sin_mag=1 pcm` for all bins
- collection:
  - use the full post-forcing window implied by each run's `stop_time`

These settings are applied automatically unless you disable the corresponding
low-power defaults. For reproduction, leave those defaults enabled.

### 3) Run all powers from table

```bash
python3.12 -m freq.runFreqNominalParallelAllPowers --core_model 1r --reduced_csv_for_collect --cleanup_omc_artifacts
python3.12 -m freq.runFreqNominalParallelAllPowers --core_model 9r --reduced_csv_for_collect --cleanup_omc_artifacts
```

This wrapper now defaults to a `1e-3` to `1e1 rad/s` sweep with `80`
log-spaced frequency points. Use `--power_min 1e-5` if you want to include the
lowest-power table rows in the full campaign.

Accepted low-power plots are now summarized in
`freq/results_lowpower_default_protocol`:

- `0.01 MW`: inherited from the earlier accepted `1 pcm` slow-band merge
- `0.001 MW`, `1e-4 MW`, `1e-5 MW`: merged from the reduced-forcing slow-band
  reruns plus the original `run4` higher-frequency bins
- updated plots:
  - `freq/BodePlots_run4/BodePlot_1r_power_0p01.png`
  - `freq/BodePlots_run4/BodePlot_1r_power_0p001.png`
  - `freq/BodePlots_run4/BodePlot_1r_power_0p0001.png`
  - `freq/BodePlots_run4/BodePlot_1r_power_0p00001.png`
  - matching `9r` plots and compare plots in `freq/BodePlots_run4_compare`

The raw `freq/results_run4` tree was not overwritten.

Final accepted high-frequency low-power merge:

- `freq/results_lowpower_default_protocol_hifreqfix_final`
- This extends the earlier partial high-frequency repair with the final
  accepted `1e-5 MW` correction.
- The last `1e-5 MW` `9r` upper-tail fix uses:
  - `1 pcm`
  - `ss_time = 1e7 s`
  - `stop_time = 1.005e7 s`
  - `output_samples_per_period = 2`
  - `output_step_max = 100 s`
  - late-time collection window `fit_start = 10049900 s`
- The corrected `1e-5 MW` high-frequency tail is stitched from:
  - `freq/results_lowpower_hifreq_fixed_v6_1em05_lowmid_fullcollect/9r`
  - `freq/results_lowpower_hifreq_fixed_v8_1em05_9r_tail_ss1e7_spp2_split/9r`
- The final accepted `1e-5 MW` compare plot is:
  - `freq/BodePlots_run4_compare/BodePlot_compare_1r_9r_power_0p00001.png`
- A later phase-only issue in the accepted `1e-5 MW` upper tail was traced to
  collection, not simulation:
  - `collectFreqNominal.py` and `collectFreqNominalParallel.py` had been
    reporting phase relative to `fit_start`
  - the accepted high-frequency `1e-5 MW` tail used late fit windows
    (`4049900 s` for `1r`, `10049900 s` for `9r`), so the reported phase
    wrapped nonphysically at high frequency
  - the collector now reports phase relative to the forcing activation time
    (`perturbationStartTime`, i.e. `ss_time`)
- `freq/results_lowpower_default_protocol_hifreqfix_final` and the updated
  `BodePlots_run4*` `0p00001` plots include that corrected phase reference

Presentation-only nominal high-frequency extension:

- The nominal-power extension used only in the presentation now uses the
  corrected merged tree:
  - `freq/results_nominal_hifreq_extension_merged_corrected`
- The original `10` to `1000 rad/s` extension became visibly unstable above
  about `100 rad/s` because DASSL internal steps were too large in the top
  decade.
- The accepted repair is to rerun the high-frequency suffix from
  `50.118723 rad/s` to `1000 rad/s` with:
  - `simflags_extra=-maxStepSize=5e-5`
  - the same nominal extension settings otherwise (`1 pcm`, `20 s` settle,
    `100 s` stop, fixed-rate `3000` intervals/s, `95` to `100 s` fit window)
- Updated artifacts:
  - `freq/results_nominal_hifreq_maxstep5e-5`
  - `freq/results_nominal_hifreq_maxstep5e-5_from50`
  - `freq/results_nominal_hifreq_extension_corrected`
  - `freq/results_nominal_hifreq_extension_merged_corrected`
  - `latex/MSRR_journal_article/figs/BodePlot_compare_1r_9r_power_1_to1e3.png`

For remote `omc_gw` batches that are synced and collected locally, use
`freq/watchOmcGwCollect.py` instead of an ad hoc polling loop. The watcher
tracks the latest submission for each case, checks `finished_at/exit_code`
through `omc_gw result`, and verifies that every reduced CSV actually reached
its requested `stop_time` before it runs local collection. If a terminal job is
still incomplete, it reports the case as blocked instead of waiting forever.

Remote scheduling convention for future `omc_gw` frequency campaigns:

- prefer `1` sweep job per worker, reserving `16` task slots with `--tasks 16`
- run the sweep itself with `--n_jobs 16` so remote reservation and local
  sweep concurrency match
- do not pack multiple sweep jobs onto one worker unless there is a specific
  reason to trade throughput for higher I/O pressure
- rationale: earlier multi-job layouts saturated remote I/O bandwidth; one
  isolated sweep per worker gave more predictable progress and simpler rerun
  recovery.

## Nonlinear Transients Workflow

Run:

```bash
python3.12 -m transients.run_nonlinear_steps --core_models 1r 9r
```

Plot (matching defaults):

```bash
python3.12 -m transients.plot_nonlinear_steps --core_models 1r 9r
```

Default transient run path convention:

```bash
00runs/transients-<core_models>
```

Default table path used by frequency scripts:

```bash
core/init/setpoints_<core_model>.csv
```

## Generate Setpoint Table vs Power

```bash
python3.12 -m core.init.generateSetpointTable \
  --core_model 1r \
  --powers 1e-5,1e-4,1e-3,1e-2,0.1,0.2,0.4,0.6,0.8,1.0,1.2 \
  --n_jobs 18 \
  --work_dir 00setpoints/work_1r \
  --output core/init/setpoints_1r.csv
```

For 9-region nominal initialization (writes `core/init/setpoints_9r.csv` by default):

```bash
python3.12 -m core.init.generateSetpointTable \
  --core_model 9r \
  --n_jobs 18 \
  --work_dir 00setpoints/work_9r \
  --output core/init/setpoints_9r.csv
```

Notes:

- Power `0` entries are skipped automatically as nonphysical.
- Power-dependent setpoint generation is heat-loss disabled by design.
  `--heat_loss` is reserved for startup studies and rejected in setpoint scripts.
- Low-power runs use built-in long-horizon overrides:
  - `1r`: `1e-3 -> 1e6 s`, `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`
  - `9r`: `1e-3 -> 1e6 s`, `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`

For difficult low-power convergence, use the continuation workflow:

```bash
python3.12 -m core.init.generateSetpointTableContinuation \
  --core_model 1r \
  --enable_steady_state \
  --steady_state_tol 1e-6 \
  --work_dir 00setpoints/work_1r_cont \
  --output core/init/setpoints_1r.csv
```

## Tests

```bash
python3.12 -m pytest tests/test_msrrv2.py -q
```

Notes:

- Tests are skipped automatically if `omc` is not available.
- Tests exercise both `1r` and `9r` startup/nominal models.
