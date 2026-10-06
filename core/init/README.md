# Model initialization

Initialization setpoint table utilities and data.

## Files

- `generateSetpointTable.py`: parallel, per-power nominal-trim steady-state generator. Writes core temperatures plus loop/region thermal states used by frequency runs, including detailed heat-exchanger state initial values (`heatExchanger.T_*_0`).
- `generateSetpointTableContinuation.py`: descending-power continuation workflow with optional intermediate steps and `-steadyState` stop flags for difficult low-power points.
- `testSetpointLongLowPower.py`: single-power extended-horizon steady-state
  check for one core or both. It prints the per-variable tail-drift summary
  and then ENFORCES it: any key variable whose relative tail drift exceeds
  `TAIL_DRIFT_MAX_REL_DELTA = 2e-2` raises and exits nonzero, naming the
  variable and the threshold (a non-finite metric also fails). The bar sits
  about 3x above the worst metric measured on the shipped converged
  review-2026-09 cases (6.8e-3 at 1r/1e-2 p.u.) and 2.5x below the
  generator's 5% relative qualification bar. On 9r the gate scores 35 key
  variables, including the exported regional arrays
  `TF1_0_regions[1..9]`, `TF2_0_regions[1..9]`, `TG_0_regions[1..9]`.
- `setpoints_1r.csv`: 1-region steady-state initialization table (27
  columns, `qualified` last, all 11 rows `qualified=1`), byte-identical to
  `00runs/paper-rerun-psar-2026-10-01/setpoints/setpoints_1r.csv`.
- `setpoints_9r.csv`: 9-region steady-state initialization table (55
  columns, `qualified` last, all 11 rows `qualified=1`), byte-identical to
  `00runs/paper-rerun-psar-2026-10-01/setpoints/setpoints_9r.csv`.
- `setpoints_<core>.model_version.json`: the tables' model-version sidecars
  (see below). `setpoints_<core>_completion.json` is the completion record
  (a promoted copy with host-specific absolute paths rewritten
  repository-relative). `setpoints_<core>_evidence.json` is the compact
  per-row qualification evidence (see
  [Qualification profiles](#qualification-profiles)).
  Continuation filled 1e-5 and 0.01 MW after the first pass qualified the
  other nine powers, and the record holds the digests of every table in the
  merge.

These tables are the production initialization baseline since 2026-10-02.
They were generated for the PSAR-basis lumped model
(`psar-basis-2026-10-01`: static beta 672 pcm, Lambda 259.3 us, fuel-salt
rho/cp 2600 kg/m3 / 1776 J/(kg K), FLiBe coolant 2038 kg/m3, core UA
4565 W/K) by the setpoint stage of campaign `psar-2026-10-01`, on the pinned
`v1.27.0-cmake` gateway build at commit `fb804dc`, and promoted with their
sidecars, completion records and per-row evidence. At 1 MW the 1R core
inlet/outlet is 546.0/569.2 degC (9R 541.2/564.4 degC), the graphite sits
about 14 K above the salt, and the secondary spans 483.0-489.0 degC (1R) and
478.2-484.2 degC (9R) across the four HX nodes.

Temperature level (physics review 2026-10-02, M1; owner decision: keep the
reference). The tables are trimmed at the 570 degC isothermal zero-power
reference, so the full-power states sit below the PSAR operating band (600 degC
nominal average). The 1 MW core inlet (546.0 / 541.2 degC) is under the PSAR
550 degC drain set point (the RPS drains the reactor when any point in the
system falls below 550 degC, PSAR Sec. 4.2.2.1). At 1.2 MW the 9R coolant cold
leg (457.6 degC) is under the FLiBe freezing point (about 459 degC). This is
getting into salt-freezing territory: if such states are within the expected
safety envelope, that would need design work. The model has no phase change,
drain logic or freeze protection; with constant properties the dynamics do not
depend on the absolute level.

`tests/test_msrrv2.py::test_nominal_frequency_initialization_near_equilibrium`
restarts from the 0.1 and 1 MW rows of both tables and checks that they
hold. The physics-review-2026-09-27 tables of campaign `corrected-2026-09-28`
remain recoverable from git history (commit `dc0024e`), as do the
review-2026-09 tables (commit `26740a2`) and the pre-2026-09-11 tables (blobs
`f538df6` / `f684ae3`).

**Model version.** Every table is bound to the lumped model it was generated
from (`helpers/setpoint_model_version.py`), because the per-row `qualified`
verdict cannot tell a stale table from a current one:

- `model_version.json` names the current lumped-model version
  (`psar-basis-2026-10-01`) and pins the SHA-256 digests of
  `SMD_MSR_Modelica.mo`, `MSRR.mo` and `generated/MSRR_PlantData.mo`. The
  digests are LF-normalized, and the emitter's revision-stamp line is
  ignored. `accepted_sources` lists every digest set accepted for the
  current version, latest last. A table generated from any of them stays
  bound, but the live checkout must match the latest. `tests/test_setpoint_model_version.py` fails when the three files
  drift from the recorded digests. The fix is then one of two commands:
  - `python3.12 -m helpers.setpoint_model_version record --version NAME
    --note TEXT` when the change moves steady states (then regenerate the
    tables);
  - `python3.12 -m helpers.setpoint_model_version record --note TEXT` when
    it does not. The new set is appended to `accepted_sources`, so existing
    tables stay bound. This was used for the Stepper `numSteps >= 1` assert.
- `setpoints_<core>.model_version.json` is the sidecar next to each table.
  Both generators write it through `write_table`. It records:
  - the table's own name and SHA-256;
  - the digests of the sources actually compiled, and the version they
    match (`null` when they match no recorded version);
  - the generation settings: generator file and digest, OpenModelica and
    Python versions, qualification profile, convergence bars and numerics,
    and the digest of the `--init_from` table.

  `--append` refuses a target table whose sidecar does not describe the CSV
  and the sources being compiled now.
- Both loaders (`freq._common.load_steady_state_overrides` and
  `transients.run_nonlinear_steps.load_setpoints`) enforce the binding under
  the strict setpoint policy. A table is refused unless all of the
  following hold:
  - its sidecar exists and is readable;
  - the sidecar names this CSV and matches its bytes;
  - the sidecar names the current version and records exactly the version
    file's source digests;
  - the lumped sources in this checkout still match those digests.

  A version label alone is not a binding (rev032 review).
  `legacy-compatible` warns and proceeds; any other policy string is
  refused. Run manifests record the consumed table's version as
  `setpoint_model_version`.
- `python3.12 -m helpers.setpoint_model_version verify --table T.csv
  --powers ...` is the strict check of a finished table. It checks the
  binding, requires finite, positive, unique powers and `qualified=1` on
  every row, and requires every requested power to have a row. Campaign
  setpoint jobs end with it.
- Tables generated before sidecars existed can be stamped with their
  historical version (`stamp`, digests `null`). Strict runs always refuse
  them. `python3.12 -m helpers.setpoint_model_version status --table
  core/init/setpoints_1r.csv` reports the state.

Promotion copies each campaign table together with its sidecar, byte for
byte.

Override guard (physics review 2026-09-27): the generator refuses a case
whose runtime overrides OMC reported as "not found" or "not possible to
override" (`helpers.omc_log.check_overrides_applied`; the structural
kinetics `*.initMode` and `heatLossEnabled` keys are tolerated). Before the
R9MSRRuhx binding fix the 9R initial-state overrides `Tmix_0` and
`TF*_0_regions[1]` were dropped this way. The same post-simulation check
(used by every runner) also refuses a run whose log records a fatal,
error-level assertion violation (`helpers.omc_log.fatal_assert_violations`,
`run_rejected`). The pinned gateway build `v1.27.0-cmake` exits 0 after
such an assertion and prints "The simulation finished successfully", so
the exit status alone cannot decide (rev033 review).

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
- If some power cases fail, `generateSetpointTable.py` writes the successful
  rows, lists the failures, and exits with status 3
  (`EXIT_TABLE_INCOMPLETE`: table written, incomplete). Status 1 means no
  table was written (or a hard failure). Rerun `generateSetpointTable.py` with
  `--reuse_csv` to reuse existing per-power CSVs and simulate missing cases.
  Reuse is provenance-checked: a stored CSV is accepted only when its
  manifest sidecar fingerprint matches the current request (model sources,
  power, overrides, integrator, stop time, output intervals, and the
  `simflags` string passed to omc) and its contents
  pass validation; anything else is moved to `00runs/tmp/quarantine/` and
  resimulated.
- Rerun continuation with the failed `--powers` and `--append` to merge those
  rows, replacing any matching `(power, heatLossEnabled)` keys. Campaigns do
  this automatically with `helpers/paper-rerun/complete_setpoint_table.py`.
  It derives the missing or unqualified powers from the table, runs the
  continuation for exactly those, merges them, and fails closed. It also
  refuses when the two passes' model-version sidecars record different
  sources.
- `--omc_timeout_seconds` optionally bounds each omc simulation (0 disables).
- Heat loss is treated in startup scenarios only.
- CSV tables retain a `heatLossEnabled` column for compatibility; standard generated rows are `0`.
- Power `0` is skipped automatically as nonphysical for steady-state setpoint generation.
- Requested `--powers` values are p.u. powers (1.0 = nominal) and must lie
  inside the validated setpoint envelope `[1e-5, 1.2]`: an out-of-envelope
  power is refused (fail closed, no CLI opt-out) because no qualified
  setpoint row exists outside it and late-window convergence alone proves
  settling, not physical validity.
- Low-power long horizons generalize beyond the exact tabulated points:
  the `1e-5 -> 1e8 s`, `1e-4 -> 1e7 s`, and `1e-3 -> 1e6 s` overrides below
  are unchanged, and any other power inside the low band `[1e-5, 1e-3]`
  p.u. inherits the `1e-3` horizons (1e6 s, 50000 intervals) for both cores
  instead of silently falling back to the base horizon.
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
| Power | trailing power channels such as `core1R.powerblock.reactorPower`, `msre9r.powerblock.reactorPower`, `powerBlock.fissionPower.P` | `--conv_power_floor` (default 1.0 W) | `--conv_power_abs_tol` (default 1.0 W) |
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

A missing requested-state column is the opposite: the setpoint signals that
are actually exported must be present, so an absent requested-state column
fails that column's check (status `fail`, never `skipped`) and disqualifies
the row. Plant-level checks, by contrast, are added whenever their columns
exist in the reduced CSV (lumped and segmented models name some differently,
so a missing column is skipped with an explicit note rather than treated as
failure):
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
rows as `qualified=0`. Downstream frequency and transient runners read the
column rather than applying it as a model override: every consumed row must
carry `qualified=1` (the run refuses otherwise), and a table without the
column is refused under the default `strict` policy with an error naming
the CSV; `legacy-compatible` remains available by explicit selection and
proceeds with a warning. In either mode the final table
is published atomically: rows land under a unique temporary name in the
destination directory, are flushed and fsynced, and are moved onto the
destination path with `os.replace`, so an interrupted run can never leave
a truncated or partially written table at the final path. Loading the table
at analysis time is fail-closed: `load_setpoints` in
`transients/run_nonlinear_steps.py` raises a `ValueError` naming the missing
power key when no row matches, so an out-of-range setpoint request aborts the
run rather than substituting a default.

### Threshold calibration basis

This section describes the generic CLI defaults of the `diagnostic` profile.
The promoted tables used the stricter `publication` profile; see
[Qualification profiles](#qualification-profiles) for its values and for the
margins the promoted rows actually achieved.

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
  committed `setpoints_1r.csv` holds its lowest-power row within 3.814e-3 K
  of the 570 °C setpoint, a factor of 262 below the 1.0 K absolute
  qualification bar.
- **Initialization accuracy.** Runs start from a uniform 570 °C core
  (`--init_temp`); the checks score only the late window, so the approach
  transient is excluded by construction and the bars calibrate the settled
  tail alone. The converged fuel and graphite temperatures stay within
  roughly 565–578 °C across the tabulated powers; at that level the
  effective limit is the ≈28.5 K relative bar (about 5% of 570 °C), not the
  1.0 K absolute bar.
- **Reactivity sensitivity.** The temperature-feedback coefficients are
  `a_F = -6.26e-5` and `a_G = -5.16e-5` Δk/k per unit temperature
  (`core/MSRR.mo`; `SegmentedMSR.Reactors.aF1R`/`aG1R` on the segmented
  side, applied per region for 9R). One kelvin of unsettled fuel- or
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
  --work_dir 00runs/tmp/work_1r \
  --output core/init/setpoints_1r.csv

python3.12 core/init/generateSetpointTable.py \
  --core_model 9r \
  --n_jobs 18 \
  --work_dir 00runs/tmp/work_9r \
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

Any other power inside the `[1e-5, 1e-3]` p.u. band resolves to the `1e-3`
horizons through `resolve_long_horizon` (both generators and the
long-low-power check); powers above the band use the base horizons.

Initialization temperature:

- Use `--init_temp 570` (default) to start steady-state runs from a 570 C
  uniform core setpoint. This aligns with the thesis furnace/heater hold
  assumption during pre‑critical heating.
- These tables remain in degrees Celsius. The standalone SegmentedMSR package
  consumes kelvin internally, and no segmented runner loads these tables today;
  any future loader must convert degC to K at that boundary.

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
  --work_dir 00runs/tmp/work_1r_cont \
  --output core/init/setpoints_1r.csv
```

This script writes a metadata CSV (`continuation_meta_*`) in the work directory
with per-step stop time, attempt mode, and neutron-population diagnostics.
The fallback attempt (enabled by a non-empty `--retry_nls`) runs when the
primary attempt raises **or** fails its late-window convergence checks —
non-convergence, not just solver robustness, triggers the retry; an empty
`--retry_nls` disables the fallback attempt entirely.

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

The promoted tables were qualified under the `publication` profile, as
recorded in each sidecar's `generation.settings` and each convergence report:
- window-mean relative tolerance 0.2 %;
- projected-trend relative tolerance 0.2 %;
- detrended-residual relative tolerance 0.5 %;
- absolute temperature bar 0.5 K and absolute power bar 1 W;
- minimum tail duration 1000 s.

On a ~570 °C channel that makes the binding relative limit about 1.1 K,
not the ≈28.5 K of the diagnostic default above.
`setpoints_<core>_evidence.json` (written by
`helpers/paper-rerun/setpoint_evidence.py` from the campaign's per-row
evidence files) records, for every promoted row:
- its origin (first pass or continuation);
- the result and convergence-report SHA-256;
- the tail window;
- the worst gated margin in each check family, as measured value over
  effective limit.

In the corrected tables no row exceeds 0.13 of its limit; the worst is the
projected trend at 0.1 MW. The raw per-step result CSVs stay on the cluster
and are identified by their hashes.

The bundled publication thresholds are conservative candidate values, not a
substitute for owner review against acceptable initialization and reactivity
error. They may be tightened with the existing `--conv_*` options and
`--conv_residual_amplitude_tol`; publication mode will not silently relax its
built-in upper bounds. `--accept_unconverged` remains exceptional and writes
`qualified=0`.
