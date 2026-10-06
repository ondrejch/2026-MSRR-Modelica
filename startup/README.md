# MSRR Startup simulations

Startup simulation runners and plotting tools.

## Key files

- `runMSRR.py`: Python runner for startup scenarios; supports `--core_model 1r|9r|1r10seg|r5x5_z10`
  (`1r10seg`, the 1-channel x 10-axial-segment core, and `r5x5_z10`, the
  5x5-radial x 10-axial-segment core, are segmented-package only and are
  refused by `--package legacy` before any OpenModelica invocation)
  and the `--package {legacy,segmented}` switch (see
  [SegmentedMSR package mode](#segmentedmsr-package-mode)).
- Scenarios in `data/scenarios/startup/` (`startup`, `startup_to_100kw`,
  `startup_to_1mw`); `runMSRR.py --scenario` selects the YAML id. Optional
  `--scenario_file` overlays a deck. Dedicated Modelica vehicles in `MSRR.mo`
  still run the default named cases.
- `plotStartUp.py`, `plotStartUpWithSource.py`, `plotStartUpTo100kW.py`: startup plotting utilities.
- `plotStartUpTo1MW.py`: startup-to-1MW plotting utility (includes Phase 5 markers).
- `plotApproachToCriticalityPhase4.py`: Phases 1-4 approach-to-criticality plotting utility.

The four to-power/source plotters (`plotStartUpWithSource.py`,
`plotStartUpTo100kW.py`, `plotStartUpTo1MW.py`,
`plotApproachToCriticalityPhase4.py`) accept
`--core_model 1r|9r|1r10seg|r5x5_z10`. An explicit `--run_dir` replaces
the default run directory for both the default CSV and the output file
name, and segmented-package runs are probed automatically (see the Output
tree paragraph below): without `--run_dir` the plotters look at the
segmented default `00runs/segmented/startup-<scenario>-<core_model>/`
(then at the pre-move `00runs/startup-<scenario>-<core_model>/segmented/`),
and with one at its `segmented/` subdirectory, with the legacy location
winning for `1r`/`9r` when it exists. Pass `--package segmented` (as given to
`startup.runMSRR`) to plot a segmented `1r`/`9r` run: the plotters then skip
the legacy location entirely. With the default `--package legacy`, a
segmented result newer than the legacy CSV selected is named on stderr, since
the legacy location can hold an older record. A CSV found in the segmented default
directory keeps the default plot outputs beside it, outside the published
`00runs/startup-*` record. `plotStartUp.py` stays legacy-only
(`--core_model 1r|9r`, no `segmented/` probe).

`plotStartUp.py` interprets a result with the source-normalization
constants its own run manifest records: LAMBDA, nu, P, E_f, eta_S and N0,
written by `runMSRR` as `source_normalization` on both routes. It also
requires the model's time-dependent `sourceRate` column. A result without
that manifest record is refused, because a pre-2026-09-27 run used the
retired 1.58e20 divisor. `--assume_current_normalization` interprets such a
result with the current deck, with a warning. Startup manifests also carry
both maturity axes (`core_maturity`, `core_physical_data_maturity`) on the
legacy route, as the transient manifests do.
- `plotStartUp.m`: legacy MATLAB/Octave plotting script.

## Startup Sequence (Default Paths)

Run (example: startup-to-100kW, 1r):

```bash
python3.12 -m startup.runMSRR --core_model 1r --scenario startup_to_100kw
```

Default run path pattern:

```bash
00runs/startup-<scenario>-<core_model>
```

Example run directory for the command above:

```bash
00runs/startup-startup_to_100kw-1r
```

Plot from the same default run definition (no explicit CSV path needed):

```bash
python3.12 -m startup.plotStartUpTo100kW --core_model 1r
```

## SegmentedMSR package mode

`runMSRR.py` drives one of two model families, selected with
`--package {legacy,segmented}`:

- `--package legacy` (default): loads `SMD_MSR_Modelica.mo` + `MSRR.mo`
  startup models through the route the runner used before the switch
  existed, with the same command lines and the default run directories
  above. The lumped models themselves changed with the physics review
  2026-09-27 (frozen circulating-fuel compensation, flow-referenced
  staircase, physical source normalization) and with the PSAR-basis plant
  data, so legacy results no longer reproduce the pre-review trees byte for
  byte.
- `--package segmented`: drives the standalone SegmentedMSR full-loop trim
  rigs instead:
  - `1r`: `SegmentedMSR.Reactors.R1MSRRuhxTrimThermalSS`
  - `9r`: `SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS`
  - `1r10seg`: `SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS`
    (1-channel x 10-axial-segment first-cut core; segmented-package only)
  - `r5x5_z10`: `SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS`
    (5x5-radial x 10-axial-segment first-cut core; segmented-package only)

  The optional outer-core fuel annulus changes only the `1r10seg`
  selection, and it is disabled by default: the shipped
  `outer_fuel_annulus` deck is `enabled: false` with production
  enablement blocked, so no committed run enables it. When the loaded
  plant's `outer_fuel_annulus` dataset is enabled, the segmented
  `1r10seg` startup runs the CoreVesselAssembly wrapper vehicle --
  by default the dedicated coupled-steady-state production vehicle
  `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusCoupledSS`
  (both core fuel/moderator init modes `SteadyState`, zero
  perturbation fixed in the class; the UHX-ramp wrapper for the
  to-power scenarios extends it) -- instead of the
  bare-core rig above, and its trip counterpart
  `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTripThermalSS` is the
  matching transient vehicle. `--outer-annulus-init-policy bounded_startup`
  selects the shipped-defaults `TrimThermalSS` twin
  (`SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS`,
  `FixedStart` core cells, 1 pcm sine) instead; the flag is refused
  with `--package legacy` and is meaningful only for an enabled
  `1r10seg` dataset (omitted otherwise). An enabled dataset on any other
  core — `9r`
  is refused by name — is refused before any simulation starts, naming
  the configuration path; the `--package` help states the same.

  Rig names come from `helpers/segmented_runs.py`; `--model_name` is rejected
  in segmented mode (select the rig with `--core_model`). The `"nominaltrim"`
  refusal stays effective on both paths — the rig names contain
  `trimthermalss`, not `nominaltrim`, and pass.

Segmented startup route (review 2026-10-01 H2/H3): every segmented startup
scenario, the base `startup` included, emits a structural scenario wrapper
(`helpers/emit_scenario_wrapper.py`) into the run directory. The wrapper
extends the trim rig and carries the scenario YAML schedule plus the startup
semantics of the lumped `MSRRstartUpCriticality` models:

- the YAML schedule: the 29-step reactivity staircase, the pulsed 1e8 n/s
  source windows, the three-stage primary-pump ramp from free convection
  (`freeConvFF = 0.01`), `powerLevel = 0`, and the UHX demand (zero for
  `startup`, the 9-step ramp for the to-power scenarios); the lumped `-1 s`
  first step is emitted as `0 s`, because the SegmentedMSR stepper already
  holds a `t = 0` step during initialization;
- an isothermal start: the core cells (and the 9R upper plenum) at the
  scenario's `init.per_core` temperature, which must equal the deck's
  `ssReferenceTemperature` (570 degC; the qualified feedback setpoints give
  zero feedback only there, asserted at initialization). The SteadyState
  loop then initializes isothermal as well, because nothing removes heat;
- `forcing.heat_loss: true` enables the core heat loss with the
  surroundings at the startup temperature (the lumped `heatLossTinf`
  binding);
- the flow-referenced staircase (`forcing.reactivity_pcm.reference`).
  PKE_T freezes its trim at the free-convection flow, `loss(FF=0.01)`
  (2.03 pcm 1R, 1.49 pcm 9R). The wrapper adds `loss(FF_ref) - loss(FF=0.01)`
  to every step, so trim plus offset equals `loss(FF_ref)`, the same net
  compensation as the lumped `rho_0nom = loss(1)` plus the offset
  `-(loss(1) - loss(FF_stage))`. `FF_ref` is the pump stage in effect at the
  step (`flow_dependent_critical`, the default) or `FF = 1`
  (`nominal_flow_critical`); `historical_live_compensation` has no segmented
  counterpart and is refused. The loss is the closed-form steady state of
  the rig's own precursor network (`SegmentedMSR.Nuclear.
  networkCirculationLossPcm`, `SegmentedMSR.Reactors.
  zoneChainCirculationLossPcm9R`; 66.33 pcm 1R and 72.72 pcm 9R at
  `FF = 1`), and the wrapper asserts at initialization that it reproduces
  the frozen trim. "0 pcm" is therefore just critical on every pump plateau,
  exact on the plateaus and approximate during the short ramps.

There is no runtime `-override=` payload. The former simplified schedule
(Ambiguity A': `powerLevel = 1` on the bare trim rig) kept the rig's 1 MW
UHX demand on during the `-3500 pcm` hold; the inlet salt cooled to 470 K
and the return to 0 pcm went prompt-supercritical (n = 11.2).
`data/scenarios/startup/segmented_simplified.yaml` is no longer read by the
runner. With these semantics the segmented `startup_to_100kw` run settles
where the lumped one does: 105.2 kW and an 840.6 K (1R) / 840.1 K (9R) core
inlet (lumped: 567.47 / 566.96 degC).

Homogeneous poison tracking is off by default. Enable it only through a
scenario `poisons:` block on `--package segmented` (five global
inventories for the mixed fuel salt, not a spatial poison network). See
the top-level README section Homogeneous poison tracking. The legacy
package refuses poison tracking. A poison-on run (tracking or feedback)
whose authored dataset maturity is not approved for production or
publication use — the committed dataset is `reduced_order_pending_review`
— is refused before build unless `--allow-unreviewed-poison-data` is
passed; the override is recorded in the run manifest
(`allow_unreviewed_poison_data`) instead of being silent. Poison-off runs
do not need the flag.

The structural wrapper is needed because runtime `-override=` cannot resize
the `numUhxSteps`, source, and reactivity arrays. Non-zero `--start_time` is
refused for every package before any simulation starts: the plant's
closed-form pump flow-fraction expressions are identical to the time-shifted
`delay()` form only at `startTime = 0`, so any other start would silently
shift every trip and ramp reference. Two further checks apply to every
package: `--number_of_intervals < 1` is refused, and `--extra_simflags`
must not carry a runner-owned flag (`-r`, `-override`, `-stepSize`,
`-stopTime`, `-outputFormat`) — the runner appends its own value after the
extras on both execution routes, so a duplicate would silently shadow the
fingerprinted request the run manifest attests; `-variableFilter` stays
refused for `--extra_simflags` in contract mode (see the result-variable
contract note below). Legacy invocations otherwise pass validation
trivially; segmented mode additionally rejects the other legacy-only knobs
(`--library_file`/`--model_file` overrides, `--method` other than `dassl`,
non-positive `--tolerance`, non-positive `--stop_time`, `--output_format`
other than `csv`).

Execution route note (omc 1.27 environment): `simulate()` scripting is broken
system-wide, so segmented runs copy `SegmentedMSR_PlantData.mo` then
`core/SegmentedMSR.mo` into the run directory — each generated `.mos` loads
PlantData first, then `SegmentedMSR.mo` (no SMD load) — then call
`buildModel(<rig>, tolerance = <tolerance>)` with the tolerance baked at build
time (`-rtol`/`-atol` are not runtime flags), followed by the generated
executable (named after the dotted class name) invoked directly with
`-stopTime=<stop_time>`, `-stepSize=<stop_time/number_of_intervals>` (omc 1.27
rejects `-numberOfIntervals=` at runtime), `-outputFormat=csv`,
`-maxStepSize=<...>`, any remaining runtime `-override=` payload (the
startup schedule itself is structural in the wrapper; see the run guard
below), and — in contract
mode (below) — a probed `-variableFilter=<regex>` selecting the contract
columns at the result writer. A run fails loudly
on nonzero exits, a missing executable/result CSV, or a trajectory whose final
row does not reach ~ `stop_time` (>= 0.995 x stop time; whole rows are
tail-read because 9R result rows are tens of KB wide).

Run guard (review 2026-10-01 M1, the lumped route's check): after the
executable exits 0 the runner reads its log with
`helpers.omc_log.check_overrides_applied` and refuses the run (exit 4,
nothing published) when a runtime override is reported as "not found" or
"not possible to override", or when the log records a fatal assertion
violation (an error-level record, or the runtime's "assert was triggered.
Throwing now!" verdict). The pinned OpenModelica build prints "The
simulation finished successfully." and exits 0 after all three. The poison
switches `enablePoisonTracking`/`enablePoisonFeedback` are `Evaluate=true`
and bound as `buildModel` modifiers, so the executable never receives them:
`helpers.segmented_runs.runtime_override_payload` drops a requested value
equal to the compiled one and refuses a differing one before the build. The
run manifest still records the requested values.

**Result-variable contract output (default).** Segmented startup runs no
longer write the full simulation state: by default the result CSV carries
exactly the contract columns from `helpers/segmented_runs.py`
(`RESULT_CONTRACT`) — `time`, the fixed overlay columns, the reactor-power
taps, and the temperature-feedback channel (14 columns on
`1r`/`1r10seg`/`r5x5_z10`, 24 on `9r`). An enabled outer-annulus run
(see the vehicle note above) appends the 26 compact columns of
`helpers/segmented_runs.OUTER_ANNULUS_COMPACT_COLUMNS` — the annulus/vessel
temperature, heat-flow, and inventory columns from `meanTOuterAnnulus`
through `fuelVolumeClosureResidualM3`, the annulus delayed-neutron
contribution `DSOuterAnnulus`, the total energy-balance residual
including cavity heat `eResidTotal`, and the fuel-volume and energy-ledger
columns from `VActiveCoreM3` through `dUStoresDtW` — on top of this set; disabled runs
keep the 14/24 columns exactly. When the generated executable's
runtime supports `-variableFilter` (probed through its `-help` listing),
the compact CSV is written directly at the result writer — the rows and
time grid are unchanged. Otherwise the wide file is written and immediately
projected onto the contract columns in place (every data row kept; the raw
wide bytes replaced once the projection succeeds). The written header is
verified to be exactly contract-shaped after every run: extra columns from
a silently failed filter or an alias-companion emission are repaired by the
same projection, and a missing required column fails the run. In contract
mode `--extra_simflags` must not carry `-variableFilter` (and, on every
package, it cannot carry the runner-owned flags `-r`, `-override`,
`-stepSize`, `-stopTime`, or `-outputFormat`; see the package-validation
note above).

**`--full-result-output` (diagnostic).** Restores the full wide CSV (every
state/derivative/parameter column, 15,000+ columns on the 5x5 core); it is
rejected with `--package legacy`, which keeps its historical wide output.
The selected set and mode are recorded in the run manifest
(`result_variables` / `result_output_mode`) and enter the request
fingerprint; the realized mechanism (runtime filter vs post-run projection)
is printed in the run logs. Recorded for a representative short 5x5
verification run (on one machine and OpenModelica toolchain): the
result CSV went from 15,629 columns / ~129 MB to
14 columns / ~119 KB, with the contract
columns bit-identical to the corresponding columns of the same run's
full wide output (root README, "Segmented result-variable contract").

Output tree (review 2026-10-01 M6): `00runs/startup-*` is the published
pre-fix record, so segmented results never land there. File names keep the
legacy prefixes:

- default: `00runs/segmented/startup-<scenario>-<core>/`, holding the
  outputs directly (for example `00runs/segmented/startup-startup-1r/`
  holding `startUp_res.csv`);
- explicit `--run_dir <dir>`: `<dir>/segmented/`, so a directory shared with
  legacy runs never collides. A `--run_dir` inside `00runs/startup-*`,
  `00runs/freq/` or `00runs/transients-*` is refused before anything runs
  (`helpers.published_tree_guard`).

Segmented result CSVs report temperatures in kelvin (the SegmentedMSR library
is fully kelvin); setpoint tables stay in degrees Celsius, and no segmented
runner loads them.

Legacy default paths (`00runs/startup-<scenario>-<core_model>`) are
byte-unchanged.

Smoke-scale example (card AC-5 shape), one per core:

```bash
python3.12 -m startup.runMSRR --package segmented --core_model 1r \
  --scenario startup --stop_time 5000 --number_of_intervals 5000

python3.12 -m startup.runMSRR --package segmented --core_model 9r \
  --scenario startup --stop_time 5000 --number_of_intervals 5000
```

Evidence runner (phase-R3 gate):

```bash
python3.12 -m pytest helpers/check_r3_startup_gates.py -q
```

The gate runs local-mirror units and segmented preflight checks without omc,
plus AC-5 end-to-end smokes for BOTH cores at the same 5000 s horizon when
`omc` is available (they skip automatically otherwise). Gates are
anti-divergence ONLY (exit 0, result CSV reaching stop time, finite neutron
population everywhere) plus the mos-text contract (PlantData then
`SegmentedMSR.mo`, buildModel route, never the legacy three-loadFile
`simulate()` emission); trajectory values print as diagnostics,
never gated — no reachable-power-band claims. Gate scratch lives under
`00runs/tmp/r3_startup_gates/`. Fast non-omc unit coverage for segmented mode
lives in `tests/test_startup_segmented_mode.py`.

### Plant selection (`--plant`)

`--plant <id>` (default `msrr`) selects the plant deck of a `--package segmented` run: the plant's generated `core/generated/<id>/SegmentedMSR_PlantData.mo` is copied into the run directory (same file name), scenarios resolve in `data/plants/<id>/scenarios/` with no fallback to `data/scenarios/`, the run manifest records `data/plants/<id>`, and default outputs move under `00runs/segmented/<id>/`. A plant other than msrr requires `--package segmented`. The shipped second plant is the 1 GW scaled analog `msrr_1gw` (see `data/plants/msrr_1gw/README.md`). Its startup ids name the scaled powers:

```bash
python3.12 -m startup.runMSRR --package segmented --plant msrr_1gw --core_model 1r --scenario startup_to_100mw
python3.12 -m startup.runMSRR --package segmented --plant msrr_1gw --core_model 9r --scenario startup_to_1gw
```

The base `startup` id exists for both plants (the 1 GW deck scales the
source strength with the nominal power); `startup_to_100kw` and
`startup_to_1mw` are msrr-only, and `startup_to_100mw` and `startup_to_1gw`
are msrr_1gw-only. Outputs land in
`00runs/segmented/msrr_1gw/startup-<scenario>-<core>/`.

## Run provenance for result CSVs

Every startup result CSV carries two provenance sidecars written beside it:
`<prefix>_res.manifest.json` (SHA-256 digests of the loaded Modelica sources,
package/model identity, scenario/core selection, the complete normalized
override set including `--max_step_size` and any `--extra_simflags`
overrides, solver/tolerance/time span/output grid, revision-control state,
and interpreter/OpenModelica versions) and `<prefix>_res.validation.json`
(outcome of the output checks below). Segmented manifests additionally
record the selected result-variable contract (`result_variables`,
`result_output_mode`), and the variable set is part of the fingerprint. A
segmented run whose plant `outer_fuel_annulus` dataset is enabled
additionally records the top-level `outer_fuel_annulus` manifest field
(dataset id, fingerprint, maturity, topology/direction codes, cavity
temperature, inventory policy) in the fingerprint, plus the
fingerprint-active `outerAnnulusInitPolicy` manifest override naming
the executed initialization policy (`coupled_steady_state` default,
or `bounded_startup` when explicitly selected) — the effective
vehicle rides the manifest `model_name`, so results are never reused
across initialization policies; both fields are absent on
disabled runs, which keep their historical manifest shape. A
single SHA-256 fingerprint of that
canonical request description — hashed source files and request fields,
launch timestamp excluded — is stored in each manifest sidecar, so results
carry their own exact provenance.

Before anything launches, prior artifacts tied to the result path (the CSV,
any leftover temporary file, stale sidecars) are **quarantined** to
`00runs/tmp/quarantine/<utc-stamp>/` instead of being silently deleted, so a
stale file can never be mistaken for the current run's output. Startup does
not reuse prior results; every invocation reruns from scratch. A successful
rerun must pass validation — modification time no earlier than this run's
launch instant, nonempty header with data rows, monotonically nondecreasing
`time`, finite values in required columns, rows as wide as the header, and a
final sample at or above 99.5 % of the requested stop time — before both
sidecars are written; failed outputs exit nonzero with the failing check
names printed. Concurrent launches of the same result slot are serialized
by an exclusive file claim, and `--claim_timeout_s` bounds the wait for a
busy slot (default: wait indefinitely; expiry raises
`ResultSlotClaimTimeout` naming the current holder).

## Notes

- Core Modelica files now live in `../core/` (`SMD_MSR_Modelica.mo`, `MSRR.mo`).
- Startup models use startup-specific initialization (not nominal-trim defaults):
  - `powerLevel = 0`
  - explicit startup equilibrium temperatures defined in `core/MSRR.mo`
    (`570 C` for both `1r` and `9r`; the 9R startup used `552.835... C`
    before the physics review 2026-09-27, a value that matched no setpoint
    table)
  - startup external source strength default: `1e8 n/s`
  - pulsed neutron-source schedule:
    - on: `[0, 40200)`, `[43200, 65400)`, `[68400, 83400)`, `[86400, 101400)` s
      (`startupSourceStepTime` in `MSRRstartUpCriticality`, the `source`
      block of `data/scenarios/startup/approach_to_criticality.yaml`)
- External startup reactivity schedule is applied from `t = -1 s` so startup begins subcritical at `t = 0`.
- Flow-referenced staircase (physics review 2026-09-27, legacy models): the
  staircase amplitudes are measured from the critical position at the pump
  flow in effect at each step. Because the legacy kinetics now freeze the
  circulating-fuel compensation at nominal flow, the core is more reactive at
  reduced flow by `loss(FF=1) - loss(FF)`. `MSRRstartUpCriticality` /
  `MSRRstartUpCriticality9R` (and the `*To100kW*` / `*To1MW*` models that
  extend them) therefore add the offset `-(loss(FF=1) - loss(FF_stage))` per
  pump stage (`flowReferencedStaircase = true`), computed from the core's own
  kinetics data and transit times via `MSRR.Functions.circulationLossPcm`:
  1R -65.10 / -33.51 / -18.04 / 0 pcm and 9R -75.19 / -39.79 / -21.55 / 0 pcm
  at FF = 0.01 / 0.25 / 0.5 / 1.0 on the PSAR-basis kinetics and flow
  (-69.56 / -34.37 / -18.27 and -80.78 / -41.07 / -22.03 pcm before). The
  offset follows the target stage while the pump reaches it along a finite
  ramp, so the re-referencing is exact on the flow plateaus and approximate
  during the short ramps. Each phase is then a source-driven approach to
  criticality at its own flow (on the pre-PSAR data, 1R phases 1-4 peaked at
  about 32 W, 9R at about 28 W). With `flowReferencedStaircase = false` the
  amplitudes are referenced to the nominal-flow critical position, and the
  low-flow phases turn supercritical early: the free-convection phase near
  -65 pcm (1R) / -75 pcm (9R), the offsets above (on the pre-PSAR data,
  near -70 pcm with a feedback-limited 33 kW / 15 kW burst in phase 1). The
  review-2026-09 campaign and the v2 article predate both changes.
- Startup reactivity terminology in plots:
  - plotted external reactivity = commanded external reactivity + the
    circulating-fuel compensation the model applies (`mpke.rho_0applied`:
    since the physics review 2026-09-27 the constant nominal-flow
    `rho_0nom`; runs made before it lack that column, and the plotters then
    reconstruct the former flow-gated `rho_0dyn`)
  - model reactivity = `pke.reactivity`
  - total reactivity = `rho_total = pke.reactivity - rho_0dyn` (external +
    feedback + compensation minus the steady-state precursor loss at the
    current flow; with the frozen compensation it carries the physical flow
    reactivity, e.g. about +65 pcm (1R) / +75 pcm (9R) at the 1 %
    free-convection flow on the PSAR-basis data)
- New startup plots (`plot_startup_phase1to4.png`, `plot_startup_to1MW.png`) use the same neutron-population variable (`mpke.n_population.n`) for consistency.
- `runMSRR.py` writes run artifacts/logs under `00runs/startup-<scenario>-<core_model>` by default.
- Startup plotting scripts default to the same run-definition directory pattern and write outputs there unless overridden.
- `runMSRR.py` is startup-only and rejects nominal-trim frequency models
  (`*NominalTrim*`) to keep startup and frequency initialization paths separate.
- OpenModelica build artifacts, logs, and result CSVs now land in the selected
  `00runs/startup-<scenario>-<core_model>` directory instead of the tracked `startup/` source folder.
