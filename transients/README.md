# transients

Nonlinear step-dynamics runs and plots for the journal article Results II.
The workflow now runs and overlays both core models (`1R` and `9R`).

## Key files

- `run_nonlinear_steps.py`: OpenModelica runner for step-reactivity, flow-fraction, and UHX-trip cases (`1R` and `9R`; `1R10Seg` and `R5x5Z10` additionally on `--package segmented` — the 1-channel x 10-axial-segment core and the 5x5-radial x 10-axial-segment core have no legacy vehicles, and `--package legacy` refuses them before any OpenModelica invocation); supports the `--package {legacy,segmented}` switch (see [SegmentedMSR package mode](#segmentedmsr-package-mode)). Case tables (`STEP_PCM`, flow fractions, UHX-trip horizon) come from `data/scenarios/transients/results_ii.yaml` (`--scenario results_ii`, optional `--scenario_file`).
- `plot_nonlinear_steps.py`: plotting utility for the three Results II figures with `1R`/`9R` overlays (`1R10Seg` and `R5x5Z10` additionally on `--package segmented` — the 1-channel x 10-axial-segment core and the 5x5-radial x 10-axial-segment core plot through the 1R column set; the 5x5 overlay is labeled `5x5-10Seg` on a dedicated dash-dot grayscale channel); supports the same `--package {legacy,segmented}` switch.
- `sensitivity/`: reviewer-response hA-exponent + linked-property sensitivity study on the 1R reactivity step (see `sensitivity/README.md`).
- Default run directory: `00runs/transients-<core_models>/` (for example `00runs/transients-1r-9r/`).
- Per-core simulation outputs live in `<run_dir>/<core_model>/` (for example `00runs/transients-1r-9r/1r/`).
- Default plot output directory is the same run directory unless overridden (`--fig_dir`).

## Quick start

Run transient cases:

```bash
python3.12 -m transients.run_nonlinear_steps --core_models 1r 9r
```

Plot using matching defaults:

```bash
python3.12 -m transients.plot_nonlinear_steps --core_models 1r 9r
```

Both commands default to `00runs/transients-1r-9r/`.

## Scenarios

- Reactivity steps at nominal flow:
  - `1R`: `MSRR.MSRRuhxNominalTrimThermalSS`
  - `9R`: `MSRR.MSRRuhxNominalTrim9RThermalSS`
  - both with the setpoint-table trim and explicit
    `externalReactivityAmplitude[2]`/`externalReactivityStepTime[2]` overrides
    (physics review 2026-09-27: 1R formerly used the dedicated
    `MSRR.Transients.R1fullSteps.*` models, which carried the legacy PlantData
    trims and pumps ramping from ~1 % flow at t = 0; those models remain in
    `MSRR.mo` but no runner selects them).
  - all with step insertion at `t=2000 s`.
- Reactivity steps at reduced flow:
  - `1R`: `MSRR.MSRRuhxNominalTrimThermalSS`
  - `9R`: `MSRR.MSRRuhxNominalTrim9RThermalSS`
  - with pump trip at `t=2000 s` and reactivity step at `t=4000 s`.
  - Flow fractions: 1.0, 2/3, 1/3.
- UHX trip:
  - `1R`: `MSRR.MSRRuhxTripThermalSS`.
  - `9R`: `MSRR.MSRRuhxTrip9RThermalSS`.
  - UHX demand steps from `1 MW` to `0` at `t=4000 s`.
  - Extended runtime to 4 hours after the trip (`stop_time = 18400 s`).

Dollar-labelled steps (`step_2dol` … `step_0p1dol`, and the insertion of each
flow case, sized by its own `dollars` label; an unlabelled flow case inserts
the `step_1dol` amplitude) are applied at each core's own 1 $: the static β minus that
model's own circulation loss at nominal flow, computed by
`helpers.circulation_dollar` from the plant deck
(`python3.12 -m helpers.circulation_dollar` prints the table). On the msrr deck:

| Package | 1R | 9R | 10-seg | 5x5 |
|---|---|---|---|---|
| legacy (lumped mPKE, plug flow) | 604.887 pcm | 594.672 pcm | | |
| segmented (PrecursorNetwork) | 605.671 pcm | 599.276 pcm | 603.523 pcm | 603.523 pcm |

The 1R lumped value is the reference `dollar_pcm` (604.887 pcm): the six-group
kinetics β (sum 6.720e-3, the MSRR PSAR static 672 pcm) evaluated through the
mPKE `CG_0dyn` steady-state precursor formula at the lumped nominal
in-core/ex-core transit times (43.5146 s / 10.8787 s), i.e. the fixed-fuel
672 pcm reduced by a 67.1 pcm circulation loss (the PSAR quotes 69 pcm). The
case `pcm` entries in `results_ii.yaml` record that reference amplitude; the
`dollars` field is what the runners apply. Until the physics review of
2026-10-02 (M3, TASK-20261002-01) every core used 604.887 pcm, which is
1.017 $ on the lumped 9R (its plenum counts as ex-core: 77.3 pcm loss) and
1.0094 $ on the segmented 9R. Reduced-flow cases use their core's
nominal-flow dollar, so the 1/3-flow "1 $" is 0.957 $ of the 1R at that flow.
The reference was 589.069 pcm before the PSAR data update (TASK-20261001-01).

## SegmentedMSR package mode

Both scripts support `--package {legacy,segmented}`:

- `--package legacy` (default): the lumped route — loads
  `SMD_MSR_Modelica.mo` + `MSRR.mo` through `simulate()` scripting, so every
  pre-existing invocation (including all examples above) keeps its command
  line. The lumped models and runner routes changed with the physics review
  2026-09-27 (full pump flow until the t = 2000 s trip, the setpoint-override
  route for 1R steps, volume-weighted 9R core averages) and with the
  PSAR-basis plant data, so legacy results no longer reproduce the
  pre-review trees (including `00runs/paper-rerun-review-2026-09/`) byte for
  byte.
- `--package segmented`: drives the standalone SegmentedMSR vehicles instead:
  - steps and variable-flow cases ride `SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS` (1R) / `SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS` (9R) via runtime `-override=` payloads;
  - `uhx_trip` rides the S1 trip-wrapper pair `SegmentedMSR.Reactors.R{1,9}MSRRuhxTripThermalSS` (structural `numUhxSteps = 2`, unreachable via runtime overrides);
  - the 1-channel x 10-axial-segment core (`1r10seg`, segmented-package only) rides `SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS` for steps/flow and `SegmentedMSR.Reactors.R1MSRRuhx10SegTripThermalSS` for `uhx_trip`;
  - the 5x5-radial x 10-axial-segment core (`r5x5_z10`, segmented-package only) rides `SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS` for steps/flow and `SegmentedMSR.Reactors.R5x5Z10MSRRuhxTripThermalSS` for `uhx_trip`.

  The optional outer-core fuel annulus changes only the `1r10seg`
  selection, and it is disabled by default: the shipped
  `outer_fuel_annulus` deck is `enabled: false` with production
  enablement blocked, so no committed run enables it. When the loaded
  plant's `outer_fuel_annulus` dataset is enabled, the segmented
  `1r10seg` cases run the CoreVesselAssembly wrapper vehicles — by
  default the dedicated coupled-steady-state production vehicle
  `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusCoupledSS`
  (both core fuel/moderator init modes `SteadyState`, zero
  perturbation fixed in the class) for steps/flow, and the trip
  counterpart for `uhx_trip` when shipped — instead of the bare-core
  rigs above. `--outer-annulus-init-policy bounded_startup` selects
  the shipped-defaults `TrimThermalSS` twin
   (`SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS`,
   `FixedStart` core cells, 1 pcm sine) for steps/flow instead; the
   flag is refused with `--package legacy` and is meaningful only for
   an enabled `1r10seg` dataset. Step/flow per-case manifests record
   the requested policy as the fingerprint-active
   `outerAnnulusInitPolicy` override, while `uhx_trip` always records
   the effective `bounded_startup` policy (TASK-20260925-01 P3 branch
   (ii): the single
   `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTripThermalSS`
   trip counterpart initializes `FixedStart`, so the requested
   step/flow policy does not apply to it); the effective vehicle rides
   the per-case manifest `model_name`, so step/flow results are never
   reused across initialization policies while trip results are
    reusable across requested policies; disabled runs omit the override
    and keep their historical vehicles, manifests, and columns.

    An enabled dataset on any other core
   — `9r` is refused by name — is refused before any simulation
   starts, naming the configuration path.

Vehicle names resolve through `helpers/segmented_runs.py`
(`MODEL_BY_CORE` / `TRIP_MODEL_BY_CORE`). The rigs carry their own trimmed
SteadyState init, so the legacy-only machinery — setpoints CSV inputs, the
init-mode overrides they feed, and the 9R loop-setpoint harmonization — has no
segmented counterpart. Homogeneous poison tracking is off by default;
enable it only through a scenario `poisons:` block on `--package segmented`
(five global inventories, not a spatial poison network). The legacy
package refuses poison tracking. A poison-on run (tracking or feedback)
whose authored dataset maturity is not approved for production or
publication use — the committed dataset is `reduced_order_pending_review`
— is refused before build unless `--allow-unreviewed-poison-data` is
passed; the override is recorded in the per-case run manifests
(`allow_unreviewed_poison_data`) instead of being silent. Poison-off runs
do not need the flag.

Segmented override surface (mirrors the legacy routes; existing array element
`[2]` is reused because runtime overrides cannot resize arrays):

- Nominal-flow steps: pumps pinned at forced circulation
  (`primaryPump.freeConvFF=1`, `secondaryPump.freeConvFF=1`),
  sine perturbation disabled (`perturbationAmplitudePcm=0`), and a reactivity
  step via `externalReactivityAmplitude[2]=<case pcm>` at `t=2000 s`.
- Variable-flow cases: full flow until the pump trip at `t=2000 s`, then an
  exponential coast-down to the case fraction —
  `primaryPump.freeConvFF=<fraction>`, `primaryPump.rampUpTo[1]=1.0` (the
  pre-trip *target* flow fraction), `rampUpTime[1]=0` and
  `primaryPump.tripTime=2000` — plus the core's 1 $ step (segmented 1R 605.671 pcm,
  9R 599.276 pcm) at `t=4000 s`. The segmented rigs start the ramp complete
  (`Pump.startRamped`), so FF(0) = 1 and the frozen PKE_T trim is taken at
  nominal flow.
  The legacy (lumped) route uses the same pump schedule and selects
  `primaryPump.startRamped=true` at runtime, so its FF(0) = 1 as well
  (physics review 2026-09-27). It formerly bound `rampUpTo[1] = 1 − fraction`;
  because `rampUpTo` is the target flow, those runs held `1 − fraction`
  before the trip and coasted to `fraction` after it, and the "100 %" case
  stood still from t ≈ 5 s to 2000 s and then restarted. The review-2026-09
  campaign and the v2 article flow figures were computed with that former
  route.
- Salt-freezing territory (physics review 2026-10-02, M2; owner decision:
  kept as is). The msrr flow cases hold the 1 MW UHX demand after the trip.
  At 1/3 flow the secondary cold leg settles at about 452 degC (1R) and
  442 degC (9R), below the FLiBe freezing point (about 459 degC), and the
  segmented 9R fuel inlet reaches about 500 degC, the fuel-salt liquidus.
  The model has no phase change, so these states are reported, not
  physical. The 9R is still settling at the 4000 s insertion (n = 0.972).
  This is getting into salt-freezing territory: if that behavior is within
  the expected safety envelope, it would need design work. The 1 GW analog
  lets the demand follow the flow (`uhx_demand_follows_flow`, package A).
- Override guard (legacy route, physics review 2026-09-27): after each omc run
  the runner scans the log with `helpers.omc_log.check_overrides_applied` and
  refuses the case if OMC reported any override key as "not found" or "not
  possible to override" (before the review the 9R `Tmix_0` and
  `TF*_0_regions[1]` table overrides were dropped this way in every run). The
  structural kinetics `*.initMode` request is tolerated: OMC evaluates it at
  translation, so the trims initialize the kinetics FixedStart at n = n_0 with
  equilibrium precursors, which is the same steady state. The same check
  refuses a case whose log records a fatal assertion violation (the pinned
  build exits 0 after one).
- Override guard (segmented route, review 2026-10-01 M1): the simulation runs
  in the generated executable, so `run_case_segmented` applies the same check
  to the executable's stdout/stderr after it exits 0 and raises
  `RuntimeError` before anything is published. The poison switches
  (`enablePoisonTracking`/`enablePoisonFeedback`, `Evaluate=true`) are bound
  as `buildModel` modifiers and kept out of the executable's `-override=`:
  `helpers.segmented_runs.runtime_override_payload` drops a requested value
  equal to the compiled one and refuses a differing one before the build. The
  per-case manifests still record the requested values.
- `uhx_trip`: only pump/power pins ride overrides — the demand-drop schedule
  ({1e6 -> 0} W at t={0, 4000} s) and DHRS engagement are structural on the
  trip wrappers. Their 0.005 P DHRS bleed runs from t = 0, so the trip
  vehicles start from the bleed-on steady state (the trim rigs scale the
  qualified profile and `n_0` by `initialRemovalRatio` = 1.005;
  TASK-20261001-01): the pre-trip window is stationary instead of drifting
  for ~1 h toward the 1.005 P operating point.

Segmented `uhx_trip` horizon: legacy mode extends the trip run to
`max(--stop_time, 18400 s)`; segmented mode raises it to
`max(--stop_time, 4500 s)` instead (with a printed notice), so short-horizon
gate runs still integrate through the t=4000 s demand drop. Full multi-hour
overlays stay explicit scripts under
`00runs/segmented-msr-validation/scenarios/`, never pytest.

Segmented argument validation hard-errors unsupported combinations before any
simulation starts (legacy invocations pass trivially):

- non-positive `--stop_time`, `--number_of_intervals < 1`, or
  `--tolerance <= 0`;
- any `--method` other than `dassl` (only the generated executables' built-in
  dassl route is exercised);
- setpoints CSV inputs: `--setpoints_csv`, or `--setpoints_csv_1r` /
  `--setpoints_csv_9r` pointing anywhere other than their built-in defaults;
- `--no_sync_loop_setpoints` (the 9R harmonization path does not exist in
  segmented mode).

Execution route (omc 1.27 environment; `simulate()` scripting is broken
system-wide): `SegmentedMSR_PlantData.mo` then `SegmentedMSR.mo` are loaded
(no SMD load) — then `buildModel(<vehicle>, tolerance = <tolerance>)` with the tolerance baked
at build time (`-rtol`/`-atol` are not runtime flags), followed by the
generated executable (named after the dotted class name) invoked directly with
`-outputFormat=csv`, `-stopTime=<stop_time>`,
`-stepSize=<stop_time/number_of_intervals>` (omc 1.27 rejects
`-numberOfIntervals=` at runtime; the step and flow cases use the
`--step_output_interval` grid instead, see Notes), `-maxStepSize=<...>`,
the usual `-override=` payload, and — in contract mode (below) — a probed
`-variableFilter=<regex>` selecting the contract columns at the result
writer (required, not probed, on the fine step/flow grid). A case fails loudly on nonzero exits, a missing
executable/result CSV, or a trajectory whose final row does not reach ~
`stop_time` (>= 0.995 x stop time; the checker tail-reads whole rows because
9R result rows are tens of KB wide).

**Result-variable contract output (default).** Segmented transient cases no
longer write the full simulation state: by default each `<case>_res.csv`
carries exactly the contract columns from `helpers/segmented_runs.py`
(`RESULT_CONTRACT`) — `time`, the fixed overlay columns, the reactor-power
taps, the required decay-power tap `pb.decayPower` (consumed by the
segmented UHX-trip plot panel), and the temperature-feedback channels
(15 columns on `1r`/`1r10seg`/`r5x5_z10`, 25 on `9r`). An enabled
outer-annulus run appends the annulus compact columns on top of this
set (see the vehicle note above); the CoupledSS vehicle inherits every
plan §10.6 output from its TrimThermalSS base, so the column contract
holds on both initialization policies. When the generated
executable's runtime supports `-variableFilter` (probed through its
`-help` listing), the compact CSV is written directly at the result
writer — the rows and time grid are unchanged. Otherwise the wide file is
written and immediately projected onto the contract columns in place
(every data row kept; the raw
wide bytes replaced once the projection succeeds). The written header is
verified to be exactly contract-shaped before the case is accepted: extra
columns from a silently failed filter or an alias-companion emission are
repaired by the same projection, and a missing required column fails the
case.

**`--full-result-output` (diagnostic).** Restores the full wide CSV for
every case (every state/derivative/parameter column, 15,000+ columns on the
5x5 core); it is rejected with `--package legacy`, which keeps its
historical wide output. The selected set and mode are recorded in every
per-case manifest (`result_variables` / `result_output_mode`) and enter the
request fingerprint; the realized mechanism (runtime filter vs post-run
projection) is printed in the run logs. Recorded for a representative short
5x5 verification run (startup contract mode, on one machine and
OpenModelica toolchain): the result CSV went from
15,629 columns / ~129 MB to 14 columns / ~119 KB, with the contract
columns bit-identical to the corresponding columns of the same run's
full wide output; a transients contract-mode run of
the same rig projects to 15 columns, since the transients contract
additionally carries `pb.decayPower` (root README, "Segmented
result-variable contract").

Output tree (review 2026-10-01 M6): `00runs/transients-*` is the published
pre-fix record, so segmented results never land there. File names inside
each core directory stay identical to legacy (`<case>_res.csv`,
`<case>.mos`, run logs):

- default run root: `00runs/segmented/transients-<core_models>/`, holding
  `<core>/` directly (for example `00runs/segmented/transients-1r-9r/1r/`);
- explicit `--out_dir <dir>`: `<dir>/segmented/<core>/`, so a directory
  shared with legacy runs never collides. A `--out_dir` inside
  `00runs/transients-*`, `00runs/startup-*` or `00runs/freq/` is refused
  before anything runs (`helpers.published_tree_guard`); the legacy route
  keeps its historical default `00runs/transients-<core_models>/<core>/`.

Results written before this change under
`00runs/transients-<core_models>/segmented/` stay readable with an explicit
`--outputs_dir`.

Smoke-scale example (card AC-4 shape), runner then plotter against the same
default tree:

```bash
python3.12 -m transients.run_nonlinear_steps --package segmented \
  --core_models 1r 9r --stop_time 2500 --number_of_intervals 5000

python3.12 -m transients.plot_nonlinear_steps --package segmented --core_models 1r 9r
```

Segmented plotting reads the same three `<case>_res.csv` families from
`<outputs_dir>/segmented/<core>/` or `<outputs_dir>/<core>/` (passing the
explicit-run root, its `segmented/` directory, or the default root all
resolve; with `--package segmented` the default `--outputs_dir` is
`00runs/segmented/transients-<core_models>`) and writes the same three
figures to `--fig_dir` (default: the `--outputs_dir` root).
Segmented result CSVs report temperatures in kelvin (the SegmentedMSR library
is fully kelvin); this plotter converts every segmented temperature array
K->degC at the render boundary, so panel axes stay in degrees Celsius exactly
as legacy.
Columns resolve through the declared candidates in
`helpers/segmented_runs.py`; nothing is re-derived per plot:

- power: `pb.reactorPower` (fallback candidate `pb.fissionPower.P`);
- temperature feedback: `fb.TotalTempFeedback` (1R) / the sum of
  `rf1..rf9.TotalTempFeedback` (9R), scaled by `1e5` to pcm;
- fuel/slow-node panels: `TF1`+`TF2` and `TG` (1R); for 9R the Ambiguity-H
  decision maps to the rig's fixed overlay columns `TZout[1..4]` (averaged)
  for the fuel panel and `TPot` as the slow-node analogue (`ToutPlenum`
  remains available in the CSVs but is unused on the panels);
- `uhx_trip` panel: total/fission/decay power from `pb.reactorPower` /
  `pb.fissionPower.P` / `pb.decayPower`; temperatures from `TinCore`,
  `ToutCore` (1R) or `ToutPlenum` (9R), and `TG` (1R) or `TPot` (9R); feedback
  summed to pcm as above.

The 10-segment and 5x5-radial cores resolve the 1R column set: the 10Seg
and 5x5Z10 rigs expose the same five temperature columns
(`TF1`/`TF2`/`TG`/`TinCore`/`ToutCore`) and the single
`fb.TotalTempFeedback` channel, so `1r10seg` and `r5x5_z10` overlays plot
through the 1R panel mapping rather than the 9R zone-outlet columns (the
5x5 overlay is labeled `5x5-10Seg`).

### Plant selection (`--plant`)

`--plant <id>` (default `msrr`) selects the plant deck of a `--package segmented` run: the plant's generated `core/generated/<id>/SegmentedMSR_PlantData.mo` is copied into the run directory (same file name), scenarios resolve in `data/plants/<id>/scenarios/` with no fallback to `data/scenarios/`, the run manifest records `data/plants/<id>`, and default outputs move under `00runs/segmented/<id>/`. A plant other than msrr requires `--package segmented`. The shipped second plant is the 1 GW scaled analog `msrr_1gw` (see `data/plants/msrr_1gw/README.md`). The 1 GW Results II deck keeps the case ids and restates the
dollar-labelled amplitudes in its own dollar (reference 1 $ = 606.501 pcm; the
runner again applies each core's own value, e.g. segmented 9R 601.253 pcm) and the UHX
trip demand at 1 GW:

```bash
python3.12 -m transients.run_nonlinear_steps --package segmented --plant msrr_1gw --core_models 1r 9r
python3.12 -m transients.plot_nonlinear_steps --package segmented --plant msrr_1gw --core_models 1r 9r
```

The plotter's `--plant` only sets its default `--outputs_dir`
(`00runs/segmented/msrr_1gw/transients-<core_models>/`).

The 1 GW deck marks its three flow cases `uhx_demand_follows_flow: true`:
the segmented route then runs the core's follow vehicle
(`SegmentedMSR.Reactors.*FlowFollowThermalSS`, `helpers.segmented_runs.
FLOW_FOLLOW_MODEL_BY_CORE`) with `uhxFollowFraction = flow` and
`uhxFollowTime = 2000`, so the heat-sink demand drops to the flow fraction
times nominal power at the pump trip instead of staying at nominal power
through the coast-down. The flag is optional (absent means false); the
msrr deck leaves it out, so its flow cases keep the trim rig and the
constant demand.

## Run provenance for result CSVs

Every transient case result (`<case>_res.csv`, legacy and segmented) carries
two provenance sidecars written beside it: `<case>_res.manifest.json` —
SHA-256 digests of the loaded Modelica sources, package/model identity, the
case name plus the complete normalized `-override=` payload, `--max_step_size`,
solver/tolerance/time span/output grid, setpoint-table provenance (legacy
mode: which table was loaded; segmented mode: none), revision-control state,
and interpreter/OpenModelica versions — and `<case>_res.validation.json`
(outcome of the output checks below). Segmented case manifests additionally
record the selected result-variable contract (`result_variables`,
`result_output_mode`), and the variable set is part of the fingerprint.
An enabled outer-annulus run additionally records the
fingerprint-active `outerAnnulusInitPolicy` manifest override naming
the executed initialization policy (step/flow: `coupled_steady_state`
 default, or `bounded_startup` when explicitly selected; `uhx_trip`
 always records the effective `bounded_startup`) — the effective vehicle
 rides the per-case manifest `model_name`, so step/flow results are never
 reused across initialization policies, while trip results are reusable
 across requested policies; disabled cases keep their
historical manifest shape. Setpoint tables follow the `strict`
qualification policy by default: a table
without the generator's `qualified` convergence-verdict column is refused
with an error naming the CSV, so no case initializes from a table that
predates the generator's late-window convergence checks. The
`legacy-compatible` mode (warn on stderr naming the CSV and proceed)
remains available only by explicit selection; policy choice is a library
parameter, not a CLI flag. Each per-case manifest records the active mode
(`setpoint_policy`) beside `setpoint_table_path`, with
`setpoint_policy_exception` recorded true only when a table without the
`qualified` column was consumed under an explicitly selected
`legacy-compatible` policy. The shipped `core/init/` tables carry
`qualified=1` on every row: they pass the strict default with no warning.

Before any case launches, prior artifacts tied to its result path (the CSV,
leftover temporary files, stale sidecars) are **quarantined** to
`00runs/tmp/quarantine/<utc-stamp>/`. This closes an old acceptance hole:
previously a pre-existing CSV passed silently when `omc` or the executable
exited 0 without producing new output. After exit 0 with a fresh CSV, the
case must pass validation — modification time no earlier than this run's
launch instant, nonempty header with data rows, monotonically nondecreasing
`time`, finite values in required columns, rows as wide as the header, and
(segmented mode additionally) a final sample at or above 99.5 % of the
requested stop time — before both sidecars are written. A failed validation
aborts the runner with the failing check names. Concurrent launches of the
same result slot are serialized by an exclusive file claim, and
`--claim_timeout_s` bounds the wait for a busy slot (default: wait
indefinitely; expiry raises `ResultSlotClaimTimeout` naming the current
holder).

Two deliberate behavior notes: prior results are never *reused* (every
invocation reruns all requested cases), and the legacy `simulate()` route
keeps its historical no-tail-check acceptance for fresh outputs (freshness
and structure still validated), so its stored byte-level layout pins stay
meaningful.

Fast, omc-free unit coverage for segmented mode lives in
`tests/test_transients_segmented_mode.py`.

## Notes

- Core Modelica files are in `core/` (`SMD_MSR_Modelica.mo`, `MSRR.mo`).
- `run_nonlinear_steps.py` uses initialization values from:
  - `core/init/setpoints_1r.csv`
  - `core/init/setpoints_9r.csv`
- For 9R, regional initialization fields (`TF1_0_regions[*]`, `TF2_0_regions[*]`,
  `TG_0_regions[*]`, and `Tmix_0` when present) are also injected from the setpoint table.
- By default, 9R shared loop-state fields (HX/pipe/UHX/DHRS temperatures) are
  harmonized to the 1R nominal setpoint row so both cores start from comparable
  plant-loop temperatures; disable with `--no_sync_loop_setpoints` if needed.
- Legacy step and flow cases write a 0.01 s output grid
  (`--step_output_interval`, default from `numerics.step_output_interval_s` in
  `data/scenarios/transients/results_ii.yaml`) restricted by `-variableFilter`
  to the columns the step figures read (`LEGACY_STEP_RESULT_FILTER`; about 14
  columns for 1R and 48 for 9R, about 0.2/0.8 GB per 10000 s case). A 1 s grid
  under-draws the sub-second prompt bursts: the 2 $ peak by about 30x.
  `--step_output_interval 0` restores the uniform `--number_of_intervals` grid
  with full output. The UHX-trip case always uses `--number_of_intervals`.
- Segmented step and flow cases (`--package segmented`, review 2026-10-01 M2)
  write the same `--step_output_interval` grid, `-stepSize = 0.01 s` by
  default, through their result-variable contract (15 columns on the 1R-shaped
  cores, 25 on 9R). The runtime `-variableFilter` is required on that grid
  (no post-run projection fallback, which would first write the full wide
  output), and `--full-result-output` is refused unless
  `--step_output_interval 0` is passed. The grid is recorded per case in the
  manifest (`number_of_intervals`); `uhx_trip` keeps the
  `--number_of_intervals` grid. Measured on the 1R 2 $ step (2100 s horizon,
  OpenModelica 1.27.1): peak n 2477.5 at t = 2000.36 s on the 0.01 s grid
  against 87.5 on a 1 s grid (28x under-drawn); 210,010 rows, 54 MB, so
  about 0.26 GB per 10000 s case on 1R (about 0.4 GB on 9R).
- `plot_nonlinear_steps.py` plots the step and flow power panels in MW on a log
  axis by default (`--power_scale {log,linear}`), so the prompt bursts and the
  ~1 MW tails share one panel. It writes:
  - `<run_dir>/MSRRstep_nominal.png`
  - `<run_dir>/MSRRstep_flow.png`
  - `<run_dir>/MSRR_uhx_trip.png`
- In `MSRRstep_nominal.png` and `MSRRstep_flow.png`, fuel and graphite panels are
  shown as `T - T_prestep_mean` (baseline window `-50 s <= t < 0 s`) so cross-core
  transient shape can be compared without static offset bias. The 9R "Core
  Avg." fuel and graphite temperatures are VOLUME-weighted over the nine
  regions (fuel over both fuel nodes, graphite over the graphite nodes; node
  volumes from `data/plants/msrr/cores/r9.yaml` `vol_F1`/`vol_F2`/`vol_G`),
  i.e. the mean temperature of each inventory (physics review 2026-09-27; the
  former unweighted region mean gave a 1 $ peak fuel dT of 83.0 K vs 67.4 K
  volume-weighted on the review-2026-09 data). The 1R core has one channel with
  equal fuel-node volumes, so its mean is unchanged.
- In `MSRR_uhx_trip.png`, the temperature panel is plotted as
  `T - T_pretrip_mean` (baseline window `-0.5 h <= t < 0 h`) to compare
  transient response independent of small core-model nominal offsets.
- With synchronized loop setpoints, the 9R overlay generally shows slightly
  faster post-peak damping than 1R in the nonlinear step/UHX cases, consistent
  with finer in-core flow and feedback resolution.
- Plot styling convention:
  - `1R`: base colors with dashed lines.
  - `9R`: slight hue-shift variants of the same-intensity colors with solid lines.
- OpenModelica generates many build artifacts inside the selected run directory.
