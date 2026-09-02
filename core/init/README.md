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
- `--heat_loss` is deprecated and rejected by all setpoint scripts
  (both generators and the long-low-power harness).
- If some power cases fail, generators write the successful rows, list the
  failures, and exit nonzero. Rerun `generateSetpointTable.py` with
  `--reuse_csv` to reuse existing per-power CSVs and simulate missing cases.
  Reuse is provenance-checked: a stored CSV is accepted only when its
  manifest sidecar fingerprint matches the current request (model sources,
  power, overrides, integrator, stop time, output intervals) and its contents
  pass validation; anything else is moved to `00runs/tmp/quarantine/` and
  resimulated.
- Rerun continuation with the failed `--powers` and `--append` to merge those
  rows, replacing any matching `(power, heatLossEnabled)` keys.
- `--omc_timeout_seconds` optionally bounds each omc simulation (0 disables).
- Heat loss is treated in startup scenarios only.
- CSV tables retain a `heatLossEnabled` column for compatibility; standard generated rows are `0`.
- Power `0` is skipped automatically as nonphysical for steady-state setpoint generation.
- Steady-state setpoint solves are performed at nominal forced circulation (`freeConvFF=1` for both pumps).
- A numerical neutron-population floor of `n >= 1e-9` is used by the model in low-power steady-state solves (solver aid, not external source strength).

### Convergence qualification

A late-time average alone does not prove that the trajectory settled, so
every generated row must pass late-window checks before it enters an output
table. The last `--tail_fraction` fraction of each per-power simulation,
requested to hold at least `--tail_min_samples` samples, is split into two
equal windows, and each requested state plus each present plant signal must
satisfy its family's bars:

- the two half-window means differ by at most the effective combined
  limit `max(--conv_window_tol * S_x, absolute bar)` of that quantity,
  where `S_x = max(|mean|, floor)` is its engineering scale;
- the centered-time least-squares slope multiplied by the window duration
  is at most the corresponding combined limit
  `max(--conv_slope_tol * S_x, absolute bar)`; and
- all samples in the window are finite.

Acceptance denominators are these engineering scales, not the observed
motion. The peak-to-peak span within the window is still computed and
reported per check as a `*_span_fraction` diagnostic, but it no longer forms
the acceptance denominator; a perfectly constant window passes with both
relative fractions reported as 0.

Classification runs on the mapped Modelica result-variable names — the
columns the checks actually score — not on the setpoint-table column
labels. An exchanger table key such as `heatExchanger.TpIn_0` or `TsIn_0`
is a label in the output table only; its mapped result variable
(`heatExchanger.T_in_pFluid.T`, `heatExchanger.T_in_sFluid.T`) is what
carries the temperature name pattern and is scored. Per-family floors and
absolute bars; flag names below are their CLI overrides:

| Family | Matching result-variable names | Scale floor | Absolute bar |
|---|---|---|---|
| Temperature | names ending in `.T` (e.g. `heatExchanger.T_in_pFluid.T`, `msre9r.R1.fuelNode1.T`), names containing `.T_` (e.g. `heatExchanger.T_PN1`), or names containing `temp` (e.g. `pipeHXtoUHX.tempPi`), matched case-insensitively | `--conv_temperature_floor` (default 1.0) | `--conv_temperature_abs_tol` (default 1.0) |
| Power | trailing `.P` channels such as `core1R.reactorPower.P`, `msre9r.reactorPower.P`, `powerBlock.fissionPower.P` | `--conv_power_floor` (default 1.0 W) | `--conv_power_abs_tol` (default 1.0 W) |
| Normalized population | `*.n_population.n`, e.g. `core1R.mpke.n_population.n` | `--conv_population_floor` (default 1e-9) | none |
| Generic (unmapped columns) | anything else | fixed safety floor of 1e-9 | none |

The relative bars (`--conv_window_tol`, `--conv_slope_tol`) apply to every
family. Temperature floors and bars are stated in the setpoint-table
temperature unit, degrees Celsius; kelvin shares the numeric width, so the
same numbers transfer unchanged between the two scales. Power floors and
bars are watts; population quantities are dimensionless and judged by the
relative bars only. Each scored metric follows one combined
relative-or-absolute rule, `d <= max(d_abs, eps * S_x)`: the effective
limit is the larger of the relative bar (`eps * S_x`) and the family's
absolute bar, and a deviation is rejected only when it exceeds both. The
relative bar keeps large-amplitude signals honest against their own level;
the absolute bar is a near-zero rescue alone — it never rejects a deviation
the relative bar already accepts, and it decides the verdict only at scales
so small that the floor-scaled relative bar would be tighter than the
signal's physical unit.

A late window holding fewer than `--tail_min_samples` usable samples fails
every scored check, with the shortfall recorded in the report as
`samples` versus `required`; undersampling is never silently absorbed by
shrinking the window down to whatever rows happen to exist.

Plant-level checks are added whenever their columns exist in the reduced
CSV (lumped and segmented models name some differently, so a missing column
is skipped with an explicit note rather than treated as failure):
neutron-population drift (`core1R.mpke.n_population.n` or
`msre9r.mpke.n_population.n`), mapped fuel-node and graphite core
temperatures, and each reactor/fission power channel checked individually
under the power family.

`reactorPower.P - fissionPower.P` is NOT a plant energy balance. That pair
difference is dominated by decay heat and nonprompt-power bookkeeping
between plant channels; no energy conservation is claimed or tested
anywhere in these checks. It appears only in the diagnostic group
`decay_or_nonprompt_power_stationarity` (status `noted`; formerly labeled
`net_energy_residual`), which never affects qualification however large the
difference is. A thermally stationary row therefore cannot be disqualified
by a constant nonzero power offset unless one of the individual power
channels fails its own power-family bars.

Each per-power report is written beside the case CSV as
`MSRR_ss_<tag>_res.convergence.json`, carrying `schema_version` 3 and
recording the tail-window statistics, every tolerance/floor/bar in force,
each check with its measured engineering-relative fractions (and the span
fractions), per-metric relative limits, absolute bars, the effective
combined limit actually applied, and the accepting or rejecting decision
branch (`relative`, `absolute_rescue`, `rejected`), its family, the
derivative estimate (least-squares slope over the late window times its
duration), and pass/fail/skipped/noted status with reasons. Reports with
`schema_version` 2 or older were produced under the retired
both-bars-must-clear rule; their verdicts must not be compared with v3
verdicts on the same data. Result CSVs also receive `.manifest.json` /
`.validation.json` provenance sidecars (freshness against launch time,
requested stop time, finiteness).

Concurrent generation of the same power slot is serialized by an exclusive
file claim: each provenanced case acquires the slot claim (a `flock` on
`MSRR_ss_<tag>_res.csv.claim`) before its first prepare/quarantine decision
and holds it through the reuse decision, simulation, validation,
publication, and sidecar writes, releasing it only after the complete
published pair is visible. A second launch of the same slot therefore
blocks instead of quarantining the first writer's in-flight output, and
after the claim is released its own reuse check finds the finished pair. A
crashed writer never leaves a stale claim behind (the kernel drops the
lock when the process exits). The wait is unbounded by default;
`--claim_timeout_s` (both generators) bounds it to a positive number of
seconds, failing the case with `ResultSlotClaimTimeout` — naming the
current holder — instead of waiting.

Rows failing any check are excluded from the output table and listed (exit
code 1). Pass `--accept_unconverged` to keep such rows in the table marked
`qualified=0`; qualifying rows carry `qualified=1`. Tables written before
this feature have no `qualified` column; appending treats those historical
rows as `qualified=0`. Downstream frequency workflows ignore the extra
column (they read only known setpoint keys). In either mode the final table
is published atomically: rows land under a unique temporary name in the
destination directory, are flushed and fsynced, and are moved onto the
destination path with `os.replace`, so an interrupted run can never leave
a truncated or partially written table at the final path.

### Threshold calibration basis

The family defaults (`1.0` absolute temperature bar and floor in the table
unit, `1.0` W absolute power bar and floor, `1e-9` population floor, `0.05`
relative window/slope bars) are calibrated against three independent
scales, not tuned to make particular cases pass. Under the implemented
combined rule each scored deviation is judged against the effective limit
`max(d_abs, eps * S_x)`, so at ordinary signal levels the RELATIVE bar is
the binding limit and the absolute bar never fires: on a ~570 °C
temperature channel the effective limit is ≈28.5 K (5% relative), and on a
~1e6 W power channel it is ≈50 kW. The absolute bars matter only as a
near-zero rescue — they accept (never reject) a deviation at scales where
the floor-scaled relative bar would be tighter than the physical unit of
the signal.

- **Solver noise.** Steady-state solves run with DASSL at relative
  tolerance `1e-6` (baked into each generated `runModelica.mos`). On a
  ~570 °C temperature channel that is ~1e-3 K of representable jitter, and
  on a ~1e6 W power channel ~1 W — a factor of ≈5e4 below the binding
  relative bars (≈28.5 K and ≈50 kW at those scales), so
  integrator noise alone cannot move a verdict. The `1.0` K and `1.0` W
  absolute bars sit far below the relative bars at these ordinary scales
  and therefore never bind there; they exist for the near-zero rescue. The
  committed `setpoints_1r.csv` holds its lowest-power row within ~4e-5 K of
  the 570 °C setpoint, well under that jitter scale.
- **Initialization accuracy.** Runs start from a uniform 570 °C core
  (`--init_temp`); the checks score only the late window, so the approach
  transient is excluded by construction and the bars calibrate the settled
  tail alone. The converged fuel and graphite temperatures stay within
  roughly 565–578 °C across the tabulated powers; at that level the
  effective limit is the ≈28.5 K relative bar (about 5% of 570 °C), not the
  1.0 K absolute bar.
- **Reactivity sensitivity.** The temperature-feedback coefficients are
  `a_F = -6.26e-5` and `a_G = -5.16e-5` Δk/k per unit temperature
  (`core/MSRR.mo`; applied per region for 9R). One kelvin of unsettled fuel- or
  graphite-temperature drift in an exported row is therefore worth
  ≈6.3 pcm (fuel) or ≈5.2 pcm (graphite) of reactivity error in any
  downstream frequency or transient run initialized from the table. The
  reactivity mis-initialization a qualified row can carry is set by the
  RELATIVE tolerance, not the 1.0 K absolute bar: at the ≈28.5 K effective
  limit of a ~570 °C channel the current defaults bound it to roughly
  1.8e3 pcm (fuel) or 1.5e3 pcm (graphite) per channel.

Power normalization follows the result variables the checks score: `.P`
channels are in watts on the ~1e6 W full-power scale while the table's
`power` column is the normalized level (1.0 = nominal), and population
columns are dimensionless with the model's `n >= 1e-9` numerical floor as
their engineering scale (relative bars only, no absolute bar). The
synthetic fixtures in `tests/test_setpoint_convergence.py` pin the combined
rule at these scales: a 0.1 W ramp on a 0.2 W near-zero channel is accepted
through `absolute_rescue` (relative bar 0.05 W, absolute bar 1 W), a 10 W
drift on a ~1 MW channel is accepted through the relative branch (~50 kW
bar), a 200 W ramp on a 1000 W channel is rejected against both bars, and
a 2 K persistent sinusoid on a 900 °C channel passes while a 100 K sinusoid
at the same frequency fails. These bar values predate the combined
relative-or-absolute rule and were not retuned when the rule changed;
widening bars per run through the `--conv_*` overrides is a documented
recalibration and should be justified in the run record.

## Usage

Scratch for generator runs defaults to `00runs/tmp/setpoints/` (continuation:
`00runs/tmp/setpoints_continuation/`). Override with `--work_dir`. Do not
use `/tmp`.

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
- These tables are in degrees Celsius.

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


## Qualification profiles

Setpoint generation defaults to `--qualification_profile diagnostic`, which
preserves the historical relative-or-absolute mean-shift and projected-trend
checks. Candidate rows intended for a scientific baseline must use
`--qualification_profile publication`. Publication mode additionally:

- gates the detrended 99th-minus-1st percentile residual amplitude, so a
  persistent oscillation cannot pass merely because its mean and linear trend
  are small;
- requires population, core-temperature, and plant-power signal families to
  be present;
- requires at least 1000 seconds of physical tail duration as well as the
  configured sample count; and
- records the profile, all thresholds, required-signal policy, and every metric
  in convergence schema version 4.

The bundled publication thresholds are conservative candidate values, not a
substitute for owner review against acceptable initialization and reactivity
error. They may be tightened with the existing `--conv_*` options and
`--conv_residual_amplitude_tol`; publication mode will not silently relax its
built-in upper bounds. `--accept_unconverged` remains exceptional and writes
`qualified=0`.
