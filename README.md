# 2026-MSRR-Modelica

Modelica models and Python tooling for MSRR startup, frequency-response, and transient studies.

**New here?** Start with [GETTING_STARTED.md](GETTING_STARTED.md): install, check, and three first runs in about 15 minutes.

> **Design basis.** The models and the committed initialization tables use
> the MSRR PSAR design basis (model version `psar-basis-2026-10-01`):
> `core/init/setpoints_*.csv` are the PSAR-basis tables of campaign
> `psar-2026-10-01` (see `core/init/README.md`), which also reran startup,
> transients and the frequency response on that basis.

## Repository Structure

- `core/`: shared Modelica sources used by all workflows.
  - `core/SMD_MSR_Modelica.mo`: component library (nuclear kinetics, heat transport, pumps, signals).
  - `core/MSRR.mo`: system models with both 1-region and 9-region core variants.
  - `core/generated/`: plant-data Modelica packages emitted from `data/plants/` (do not edit by hand; regenerate with `python3.12 -m helpers.emit_modelica_plant --catalog`).
  - `core/init/`: steady-state setpoint table generator, the qualified CSV setpoint tables, and their model-version sidecars.
- `data/`: plant parameter decks (`plants/`), scenario tables and the frequency-response gain prior (`scenarios/`), and their schemas.
- `startup/`: startup-focused runners and plotting scripts.
- `freq/`: nominal frequency sweep, collection, and plotting workflows; `sensitivity/` holds the hA-exponent frequency sensitivity study.
- `transients/`: nonlinear transient runners, plotting, and the `sensitivity/` property study.
- `helpers/`: plant and scenario loaders, provenance and setpoint-binding libraries, remote OpenModelica dispatch helpers (`omc_gw/`), and the paper reproduction campaign driver (`paper-rerun/`).
- `tests/`: OpenModelica integration tests and pure-Python unit tests.

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
- perturbation amplitude with `--sin_mag_auto`: per-frequency amplitude for
  a 1 % relative power swing from the committed gain prior
  (`data/scenarios/freq/fr_gain_prior.json`, clamped to 0.01–10 pcm); the
  historical 1 pcm low-power caps apply only under
  `--sin_mag_auto_rule inverse_power`
- settling discard (rule `settle_prior_v2`, per frequency, no cap): the fit
  window opens `T_d = ceil(3 / min(zeta*omega_n, 5e-4 1/s))` after the
  perturbation start (about 2.2e7 s / 1.6e7 s of forcing at 1e-5 MW for
  1R / 9R), and `min_cycles_after_ss` counts cycles after it; at the low
  powers where the resonance decays slower than the 5e-4 1/s floor, points
  with `omega >= 120 omega_n` discard only `ceil(3 / 5e-4 1/s)` = 6000 s and
  fit a shorter window with a linear trend term (the drift regime)
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
- Pure-Python suites (no OpenModelica needed):

```bash
python3.12 -m pytest tests/test_freq_scripts.py tests/test_freq_estimator.py -q
```
