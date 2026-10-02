# Getting started

This repository holds reduced-order dynamic models of the Molten Salt
Research Reactor (MSRR) in OpenModelica, and the Python workflows that run
them. There are two core models: **1R**, one lumped fuel and graphite region,
and **9R**, nine regions in four parallel flow zones. The workflows cover the
three analyses of the accompanying paper: the staged startup to criticality
and power, nonlinear step transients, and the power-dependent frequency
response.

This guide takes you from a fresh clone to your first results in about
15 minutes. Run every command from the repository root.

## 1. Install

You need OpenModelica 1.27 (`omc` on PATH) and Python 3.12.

```bash
omc --version
python3.12 --version
python3.12 -m pip install -e .[test]
```

## 2. Check the installation

```bash
python3.12 -m pytest tests -q
python3.12 -m helpers.setpoint_model_version status --table core/init/setpoints_1r.csv
```

The tests (184, including OpenModelica integration tests) take about 3 min
and should all pass. The second command should end with
`'psar-basis-2026-10-01' (current)`: the shipped steady-state setpoint table
belongs to these model sources (`source digests: match`).

## 3. Find your way around

| Directory | What it holds |
|---|---|
| `core/` | Modelica sources: `SMD_MSR_Modelica.mo` (component library) and `MSRR.mo` (1R and 9R plant models) |
| `core/generated/` | Plant-data Modelica packages generated from `data/plants/`; never edit them by hand |
| `core/init/` | Qualified steady-state setpoint tables for 11 powers (1e-5 to 1.2 MW) and their generator |
| `data/` | Plant parameters (`plants/`), scenario schedules and the frequency-response gain prior (`scenarios/`), schemas |
| `startup/` | Startup runner and plots |
| `transients/` | Nonlinear step and heat-sink-loss transients, plots, and a property sensitivity study |
| `freq/` | Frequency-response sweeps, collection (gain and phase fits), verification, and Bode plots |
| `helpers/` | Shared libraries, the remote OpenModelica gateway (`omc_gw/`), and the paper's campaign driver (`paper-rerun/`) |
| `tests/` | Unit and OpenModelica integration tests |

Every run writes under `00runs/` (ignored by git).

## 4. Three first runs

### a. Startup to 1 MW (1R): about 3.5 min, 0.75 GB

```bash
python3.12 -m startup.runMSRR --core_model 1r --scenario startup_to_1mw
python3.12 -m startup.plotStartUpTo1MW --core_model 1r
python3.12 -m startup.plotApproachToCriticalityPhase4 --core_model 1r
```

This simulates 42 h of reactor time: a source-assisted approach to
criticality at four pump-flow stages, then an hourly ramp of the heat-removal
demand to 1 MW. Results and figures go to
`00runs/startup-startup_to_1mw-1r/` (`plot_startup_to1MW.png`,
`plot_startup_phase1to4.png`). The first plot command prints
`Final total power: 999.517 kW`.

For 9R, replace `1r` with `9r` (about 7 min, 1.6 GB; final power 999.660 kW).

### b. A small frequency sweep: about 1 min

```bash
python3.12 -m freq.runFreqNominalParallel --core_model 1r --power 1.0 \
    --freq_min 0.01 --freq_max 1 --num_freq 5 --n_jobs 5
python3.12 -m freq.collectFreqNominalParallel --core_model 1r --power 1.0 --plot
```

This forces the 1R model at 1 MW with a small sinusoidal reactivity at five
frequencies and fits the power response. Results go to
`00runs/freq/1r/power_1/`: `FreqResponseResults.csv` (gain in dB, phase in
degrees, and the convergence checks) and `BodePlot.png`. The gains are about
56.6 dB at 0.01 rad/s and 52.1 dB at 0.1 rad/s, and every point should report
`converged=True`.

The paper's sweeps use 80 points from 1e-3 to 10 rad/s. At 1 MW that is a
modest run, but at the lowest powers the settling times reach years of
simulated time, so full low-power sweeps are cluster jobs
(`freq/README.md`).

### c. Nonlinear transients (1R): about 5 min, 2 GB

```bash
python3.12 -m transients.run_nonlinear_steps --core_models 1r
python3.12 -m transients.plot_nonlinear_steps --core_models 1r
```

This runs reactivity steps at nominal and reduced flow and a loss of the
ultimate heat sink. The figures (`MSRRstep_nominal.png`, `MSRRstep_flow.png`,
`MSRR_uhx_trip.png`) go to `00runs/transients-1r/`. Use `--core_models 1r 9r`
to run and overlay both core models. The step and flow cases are written every
0.01 s (only the plotted columns) so the sub-second prompt bursts of the
large steps are resolved; that is most of the 2 GB.

## 5. How the pieces fit

- **Plant parameters** live in `data/plants/msrr/` (YAML). After editing
  them, regenerate the Modelica packages and check them:
  `python3.12 -m helpers.emit_modelica_plant --catalog` and
  `python3.12 -m helpers.emit_modelica_plant --check`.
- **Scenarios** (startup schedules, transient cases, frequency grids) live
  in `data/scenarios/`.
- **Setpoint tables** in `core/init/` are the steady states that transients
  and frequency sweeps start from. They are bound to the model sources: if
  you change the models, the runners refuse the tables until they are
  regenerated (`core/init/README.md`; low powers take hours).
- **Every run records itself**: a `*.manifest.json` next to each result names
  the model, solver settings, source digests, and the OpenModelica and Python
  versions.

## 6. Heavier runs

The paper's full campaign (setpoints, startup, transients, and 22
frequency sweeps) was run on a cluster through the SSH gateway in
`helpers/omc_gw/` (copy `config.example.json` to `config.json` and edit it
for your site) with `helpers/paper-rerun/run_paper_rerun_gateway.py`.

## 7. Where to read next

Each directory has a `README.md` with the full options: start with
`startup/README.md`, `transients/README.md`, and `freq/README.md`. Every
runner also prints its options with `--help`.

## 8. Common problems

- **`omc: command not found`**: install OpenModelica and put `omc` on PATH,
  or pass `--omc /path/to/omc` to the startup and transient runners (the
  frequency runner uses the `omc` on PATH).
- **A Python version error**: use `python3.12`.
- **A run waits before starting**: another run of the same case holds the
  result slot; wait for it, or give the new run its own output directory
  (`--run_dir`, `--base_dir`, or `--out_dir`).
- **A plot cannot find its CSV**: pass the plot the same `--core_model` (and
  output directory, if you set one) that you gave the run.
- **The disk fills up**: runs are 0.5–2 GB each; delete what you no longer
  need under `00runs/`.
- **A simulation fails**: read the `*stderr.log` file in the run directory first.
