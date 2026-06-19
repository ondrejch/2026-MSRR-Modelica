# Model initialization

Initialization setpoint table utilities and data.

## Files

- `generateSetpointTable.py`: parallel, per-power nominal-trim steady-state generator. Writes core temperatures plus loop/region thermal states used by frequency runs, including detailed heat-exchanger state initial values (`heatExchanger.T_*_0`).
- `generateSetpointTableContinuation.py`: descending-power continuation workflow with optional intermediate steps and `-steadyState` stop flags for difficult low-power points.
- `setpoints_1r.csv`: default 1-region steady-state initialization table.
- `setpoints_9r.csv`: optional 9-region steady-state initialization table (generate when needed).

9R scalar-setpoint convention:

- `fuelTempSetPointNode1`, `fuelTempSetPointNode2`, and `graphiteTempSetPoint` in
  `setpoints_9r.csv` are written as core-representative volume-weighted averages
  of `TF*_0_regions` / `TG_0_regions`, not region-1 values.
- This keeps 1R/9R scalar setpoints comparable at the same reactor power while
  retaining full regional initialization fields for 9R.

Setpoint-generation policy:

- Power-dependent setpoints are generated with heat loss disabled.
- `--heat_loss` is deprecated and rejected by both generators for setpoint runs.
- Heat loss is treated in startup scenarios only.
- CSV tables retain a `heatLossEnabled` column for compatibility; standard generated rows are `0`.
- Power `0` is skipped automatically as nonphysical for steady-state setpoint generation.
- Steady-state setpoint solves are performed at nominal forced circulation (`freeConvFF=1` for both pumps).
- A numerical neutron-population floor of `n >= 1e-9` is used by the model in low-power steady-state solves (solver aid, not external source strength).

## Usage

From repository root:

```bash
python3.12 core/init/generateSetpointTable.py \
  --core_model 1r \
  --n_jobs 18 \
  --work_dir 00setpoints/work_1r \
  --output core/init/setpoints_1r.csv

python3.12 core/init/generateSetpointTable.py \
  --core_model 9r \
  --n_jobs 18 \
  --work_dir 00setpoints/work_9r \
  --output core/init/setpoints_9r.csv
```

Run serially:

```bash
python3.12 core/init/generateSetpointTable.py --core_model 1r --n_jobs 1
```

Default output paths:

- `core/init/setpoints_1r.csv` for `--core_model 1r`
- `core/init/setpoints_9r.csv` for `--core_model 9r`

Default low-power stop-time overrides in `generateSetpointTable.py`:

- `1r`: `1e-3 -> 1e6 s`, `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`
- `9r`: `1e-3 -> 1e6 s`, `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`

Initialization temperature:

- Use `--init_temp 570` (default) to start steady-state runs from a 570 C
  uniform core setpoint. This aligns with the thesis furnace/heater hold
  assumption during pre‑critical heating.

Reactivity feedback:

- By default, reactivity feedback is enabled during steady‑state generation.
- Use `--no_feedback` to disable reactivity feedback (`a_F=a_G=0`) when generating
  setpoints.

## Continuation workflow (low power)

Use continuation mode when direct per-power solves are slow to converge:

```bash
python3.12 core/init/generateSetpointTableContinuation.py \
  --core_model 1r \
  --enable_steady_state \
  --steady_state_tol 1e-6 \
  --max_ratio 2 \
  --work_dir 00setpoints/work_1r_cont \
  --output core/init/setpoints_1r.csv
```

This script writes a metadata CSV (`continuation_meta_*`) in the work directory
with per-step stop time, attempt mode, and neutron-population diagnostics.

## Startup usage

These tables are used by nominal/frequency workflows.
Startup models (`MSRRstartUpCriticality*`, `MSRRstartUpTo100kW*`,
`MSRRstartUpTo1MW*`) currently use dedicated startup setpoint parameters defined
in `core/MSRR.mo` (not automatic table ingestion at runtime).

For frequency runs, `freq/runFreqNominalParallel.py` consumes table values and
automatically switches the heat exchanger to explicit state initialization
(`heatExchanger.detailedStateInitWeight=1`) when detailed HX columns are
available.
