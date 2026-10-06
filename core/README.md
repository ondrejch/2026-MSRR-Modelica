# Core

Shared Modelica code for the repository.

## Files

- `SMD_MSR_Modelica.mo`: base component library (units, ports, heat transport, kinetics, signals).
- `MSRR.mo`: integrated system-level MSRR models for both 1-region and 9-region cores.
- `generated/`: Modelica `PlantData` packages emitted from `data/plants/msrr/` YAML. Do not edit; regenerate with `python3.12 -m helpers.emit_modelica_plant`. Lumped `R1MSRRuhx` / `R9MSRRuhx` bind `MSRR_PlantData` (load `core/generated/MSRR_PlantData.mo` before `MSRR.mo`). SegmentedMSR binds kelvin `SegmentedMSR_PlantData` (load it before `SegmentedMSR.mo`). Scenario tables live in `data/scenarios/`; structural wrappers (`numUhxSteps`) are emitted into the run directory by `python3.12 -m helpers.emit_scenario_wrapper`.
- `init/`: power-indexed setpoint tables and generator script used for model initialization.

## Primary entry models

- 1-region: `MSRR.R1MSRRuhx`, `MSRR.MSRRuhxNominalTrim`, `MSRR.MSRRstartUpTo100kW`
- 9-region: `MSRR.R9MSRRuhx`, `MSRR.MSRRuhxNominalTrim9R`, `MSRR.MSRRstartUpTo100kW9R`
- 1-channel x 10-axial-segment 1R (`--core_model 1r10seg`; segmented package
  only, no lumped vehicle): `SegmentedMSR.Reactors.MSRR1R_10Seg`,
  `SegmentedMSR.Reactors.R1MSRRuhx10SegTrimThermalSS`,
  `SegmentedMSR.Reactors.R1MSRRuhx10SegTripThermalSS` (see the SegmentedMSR
  section below)
- 5x5-radial x 10-axial-segment 1R (`--core_model r5x5_z10`; segmented
  package only, no lumped vehicle): `SegmentedMSR.Reactors.MSRR5x5_Z10`,
  `SegmentedMSR.Reactors.R5x5Z10MSRRuhxTrimThermalSS`,
  `SegmentedMSR.Reactors.R5x5Z10MSRRuhxTripThermalSS` (see the SegmentedMSR
  section below)

Xenon/samarium poisoning is not integrated in the lumped assemblies: the
`SMD_MSR_Modelica.Nuclear.Poisons` library model exists (`enableFeedback =
false` hard-wires `poisonReactivity.rho = 0`), but neither `MSRR1R` nor
`MSRR9R` instantiates it, so Xe/Sm feedback cannot be enabled in 1R/9R
runs; the segmented cores carry their own opt-in `HomogeneousPoisons`
(default off). Wiring the lumped model in is an owner decision.

All startup/frequency tooling in `startup/` and `freq/` loads from this directory by default.

## Initialization setpoints

- `init/setpoints_1r.csv`: default initialization table for the 1-region model.
- `init/setpoints_9r.csv`: optional initialization table for the 9-region model.
- `init/generateSetpointTable.py`: parallel setpoint generator for nominal steady-state initialization.
- `init/generateSetpointTableContinuation.py`: continuation-mode generator for difficult low-power points.

Current setpoint policy:

- Power-dependent tables are generated with radiative heat loss disabled (`heatLossEnabled=0` rows).
- Startup scenarios handle heat-loss behavior separately in startup models/workflows.
- Zero-power entries are skipped during generation as nonphysical steady-state targets.
- Steady-state generation is solved at nominal forced circulation (`freeConvFF=1` for both pumps).
- Low-power stop-time defaults are extended for convergence:
  - `1r`: `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`
  - `9r`: `1e-3 -> 1e6 s`, `1e-4 -> 1e7 s`, `1e-5 -> 1e8 s`
- A numerical kinetics floor (`n >= 1e-9`) is enforced during low-power setpoint solves to avoid nonphysical extinction.

## Flow fraction (FF) shaping and heat-transfer correlations

Single reference for how the forced-flow fraction signal `FF` is shaped over
time and which empirical heat-transfer correlations consume it. The shaping
equations live in `SMD_MSR_Modelica.HeatTransport.Pump` and `.FlowDistributor`;
lumped assemblies bind the constants recorded here (`Pump` directly, and
`FlowDistributor` in the nine-region assembly). `Pump` is copied verbatim into
`SegmentedMSR.HeatTransport`, whose assemblies bind the same `tripK`/`freeConvFF`
values and feed the pump FF straight to the zones (no per-region distribution);
`FlowDistributor` itself exists only in `SMD_MSR_Modelica`. The distributor's
pre-trip pass-through identity — each output equals the pump FF before any
region trip — is what preserves 1R/9R flow parity.

### FF shaping

- **Ramp-up** (`HeatTransport.Pump`): additive sigmoid stages toward
  `rampUpTo[i]`, activating at `rampUpTime[i]` with time constant `rampUpK[i]`;
  `epsilon = 1e-4` sets the sigmoid edge steepness.
- **Pump-trip coast-down** (`HeatTransport.Pump`): for `t >= tripTime`,
  `FF(t) = sum(rampUp)(t)*exp(-(t - tripTime)/tripK) + freeConvFF`; `tripK` is
  a decay time constant, and all reactor assemblies bind `tripK = 50 s`.
- **Per-region coast-down** (`HeatTransport.FlowDistributor`): before its own
  `regionTripTime[i]` the output equals the inlet pump FF exactly
  (pre-trip pass-through identity, `FF_i(t) = FF_in`); after the trip the
  excess above the floor coasts down as
  `FF_i(t) = (FF_in - freeConvectionFF)*exp(-coastDownK*(t - t_trip_i)) + freeConvectionFF`:
  the excess above `freeConvectionFF` decays at the fixed rate `coastDownK`
  rather than following the pump, so the output relaxes to
  `freeConvectionFF` and stays between `freeConvectionFF` and the inlet FF.
  The nine-region assembly binds
  `coastDownK = 0.02 s^-1` through `regionCoastDownK`.
- **Floor and single-direction flow**: both forms stay bounded below by their
  `freeConvFF`/`freeConvectionFF` parameter (nonnegative sigmoid increments;
  exponential factors in `(0, 1)`). Every reactor assembly binds the floor to
  `freeConvFF = 0.01`, so `FF >= 0.01 > 0` at all times. With strictly positive
  nominal flows, `mdot = Vdot_nom*rho*FF*regionFlowFrac` is therefore always
  positive: reverse flow is impossible by construction.

### Heat-transfer correlations driven by FF

All laws anchor at nominal flow — `hAnom`/`hApNom` reproduce the tabulated
nominal UA at `FF = 1` — and are empirical fits, not validated extrapolations:

- Sixth-order polynomial (`SMD_MSR_Modelica.Nuclear.FuelChannel` hA and the
  `SMD_MSR_Modelica.HeatTransport.Radiator` hA as `hAnom*poly6(FF)`; the
  `SMD_MSR_Modelica.HeatTransport.HeatExchanger` and `.HeatExchangerwithHeat`
  primary sides as `(hApNom/4)*poly6(FF)`, coefficients
  `0.8215, -4.108, 7.848, -7.165, 3.004, 0.5903, 0.008537`): fit near nominal
  operation (`poly6(1) = 0.999337`, so `hA(1) ~ 0.999*hAnom`). It stays
  positive down to `FF = 0` but is NOT intended for far extrapolation outside
  the fitted band around nominal flow.
- `FF^hAExp` power law (`MSRR.Components.FuelChannel` hA and the
  `MSRR.Components.HeatExchanger` primary hApn): power-law fit to laminar
  modified Seider–Tate recalculations (Cooke & Cox ORNL-TM-4079; Pathirana
  2023 dissertation). At the PSAR design basis the fuel-channel Re is about
  700 (`helpers/core_ha_derivation.py`: laminar friction split, 78.8 % of the
  flow in the 87 fuel channels, at 568 degC; the dissertation's property set
  gave ~776), so Dittus–Boelter does not apply. The law gives exactly
  `hA(1) = hAnom` for any
  exponent. The exponent is the component parameter `hAExp` (default `0.33`,
  asserted to lie in `[0, 1]`) on both components; defaults reproduce the
  historical `FF^0.33` behavior exactly. The reviewer sensitivity studies in
  `transients/sensitivity/` and `freq/sensitivity/` vary it via `-override=`.
- `FF^hAExp` in the segmented library (review 2026-10-01 M5): the
  `SegmentedMSR.HeatTransport.HeatExchanger` primary hApn and the core
  salt-to-graphite film conductance of `SegmentedMSR.Core.SegmentedCore`.
  The core-cell law has one source, the function
  `SegmentedMSR.Core.filmFlowFactor(FF, hAExp)` (`FF^hAExp` on the
  nonnegative proxy `max(FF, 0)`), which the `SegmentedCore` conductance
  equation calls once; the HX keeps its own `hApn` statement. Both
  components carry an `hAExp` parameter (asserted in `[0, 1]`) whose
  library default is the cube root `1/3`, so the self-contained QA rigs keep
  their historical law. Every plant-bound rig binds the generated exponent:
  the cores bind `Core1R.hAExp` / `Core1R_10Seg.hAExp` /
  `CoreR5x5_Z10.hAExp` / `Core9R.hAExp` (0.33; `Core9R.hAExp` is emitted
  for the segmented package only, from `cores/r9.yaml`) and the exchangers
  bind `SecondaryLoop.hAExp` (0.33). At `FF = 1` the two laws coincide
  exactly; on the flow_33pct transient (`FF -> 1/3`, then +1 $) the core
  and HX conductances rise by 0.37 %, the temperatures move by at most
  0.031 K (1R) / 0.105 K (9R), and the post-step power peak by less than
  0.001 %. The `hAExp` parameters stay runtime-overridable
  (`-override=core.hAExp=...`, `heatExchanger.hAExp=...`). Before M5 both
  laws were the hard-coded `FF^(1/3)`. The segmented copy applies the
  deck's side-total UAs as a quarter per node (`hApn = (hApNom/4)*FF^hAExp`,
  `hAsn = (0.99*FF + 0.01)*(hAsNom/4)`), so its four primary and four
  secondary nodes sum to `hApNom`/`hAsNom` (physics review 2026-09-27). The
  lumped `MSRR.Components.HeatExchanger` now applies the same quarter split
  (`hApn = (hApNom/4)*FF^hAExp`, `hAsn = (0.99*FF + 0.01)*(hAsNom/4)`); it
  formerly applied the full side UA at every node, a 4x total that put the
  1 MW secondary at 537/545 °C instead of 487/495 °C on the pre-PSAR data
  (the 500/508 °C design within the primary-side trim). The review-2026-09
  campaign and the v2 article figures were computed with the former 4x
  lumped UA.
  `SegmentedMSR.HeatTransport.UHX`
  is a demand node with no film law; its advective mass flow is
  `mDot = vDot*rho*flowFrac.FF` (`vDot` is the FF = 1 nominal), matching
  the secondary pipes. `powDemand` is an ideal extraction independent of
  flow: at FF = 0 with nonzero demand the finite node inventory can still
  be depleted. Shipped plant pumps floor FF at `freeConvFF > 0`, so that
  case is not a production operating point.

Valid ranges: the shipped startup, frequency-response, and transient scenarios
operate over `freeConvFF <= FF <= 1` (0.01 to 1), which is the band in which
these correlations are applied; none of them has been validated above FF = 1,
and below the floor they only keep film convection defined while flow coasts
toward the free-convection level.

Upper range: every law above anchors at nominal flow (`poly6(1) = 0.999337`;
the power laws give exactly `hA(1) = hAnom`), so all of them are fits near
`FF = 1` and none carries validation data for `FF >> 1`. No clamp enforces
this: a scenario that drives FF above 1 extrapolates the fit silently, and no
numerical singularity within `FF >= 0` motivates one. On the low side,
reverse flow is refused rather than fitted: each component that consumes FF
through one of these laws (`SMD_MSR_Modelica.Nuclear.FuelChannel`,
`MSRR.Components.FuelChannel`, `MSRR.Components.HeatExchanger`,
`SegmentedMSR.HeatTransport.HeatExchanger`, `SegmentedMSR.Core.SegmentedCore`)
aborts initialization on `FF < 0` with a "flow fraction must be >= 0; reverse
flow unsupported" message. Zero remains legal — the free-convection pump rig
`TestPump.testPump` binds `freeConvFF = 0` — and the plant pumps never reach
it anyway (`freeConvFF = 0.01` in every assembly).

## Circulating-fuel kinetics conventions (lumped vs segmented)

Since the physics review 2026-09-27 both families FREEZE the circulating-fuel
compensation. `SMD_MSR_Modelica.Nuclear.mPKE` applies `rho_0nom = beta -
sum(CG_0(nomTauCore, nomTauLoop))`, the precursor loss at the NOMINAL transit
times, as a constant, so a flow change inserts the physical reactivity of the
changed precursor loss (about +65 pcm for a 1R pump stop to the 1 %
free-convection flow on the PSAR-basis kinetics and flow, +70 pcm before;
the PSAR states +69 pcm). `SegmentedMSR.Nuclear.PKE_T` freezes its trim
residual `rhoTrim` once at initialization (at nominal flow, since the rigs
start ramped) and keeps the live worth as the `rhoCirc` diagnostic. The former lumped convention -- a live
bias correction `rho_0dyn` re-evaluated at every instant from the steady-state
precursor distribution at the current transit times, which cancels the
steady-state flow reactivity -- remains selectable as
`mpke.liveFlowCompensation = true` (it is what the review-2026-09 campaign and
the v2 article used). Pump-flow responses of the two conventions differ by
construction; differences between them measure the convention, not model
error. Two rig sets record that behavior: `SegmentedMSR.QA.FlowStepLiveCheck`
/ `QA.FlowStepFrozenCheck` (TASK-20260824-02 P5, trim policy mixed with
transport discretization) and the controlled same-transport pair
`SegmentedMSR.QA.SameTransportLiveCheck` / `QA.SameTransportFrozenCheck`,
built on the frozen-trim twin `SegmentedMSR.QA.RefMPKEFrozenTrim`
(TASK-20260824-03 P2), whose single executable delta is the compensation
equation. On identical delay-form transport the live convention steps the
applied compensation down by about 18 pcm across the rigs' 1.0 -> 0.5 flow
step while the frozen convention holds it exactly constant; these runs are
characterization evidence, not a requirement that one family reproduce the
other.

## Legacy (lumped) physics fixes (physics review 2026-09-27, second pass)

The owner extended the review fixes to the whole chain: the legacy
`SMD_MSR_Modelica.mo` / `MSRR.mo` models behind the article figures now carry
the same corrections as SegmentedMSR (next section), plus the paper-facing
method fixes. The review-2026-09 campaign (`00runs/paper-rerun-review-2026-09/`)
and the promoted `core/init/` setpoint tables predate them; a new campaign
regenerates both.

- **Heat exchanger UA** (`MSRR.Components.HeatExchanger`): side totals split
  a quarter per node (formerly 4x); inlet-face axial conduction zeroed (the
  one-sided terms created energy for Kp/Ks > 0; inert at the shipped K = 0).
  At 1 MW the qualified secondary moved from 537.0/544.8 °C to
  487.1/495.0 °C (1R, pre-PSAR data) with the primary side unchanged to
  1 mK; the PSAR-basis values await the next setpoint campaign.
- **Step signals from t = 0** (`Signals.TimeDependent.Stepper`): active from
  the step time (`>=`), so a UHX demand stepped at t = 0 is live in the
  initial equations (formerly the SteadyState loop initialized at zero
  removal and the core inlet started at the core outlet temperature).
- **Pump start** (`HeatTransport.Pump.startRamped`, default false,
  runtime-overridable): completes the ramp stages scheduled at or before
  t = 0. The flow-case transients select it, so they start at FF = 1.
- **Absolute source normalization** (`Nuclear.PKE`, `Nuclear.mPKE`): the
  source divides by N0 = LAMBDA*nu*P/E_f = 1.97e13 (1.82e13 before the
  TASK-20261001-01 PSAR-basis LAMBDA; `MSRR_PlantData.Kinetics.nu`,
  `.energyPerFission`); the former 1.58e20 made 1e8 n/s act like ~12 n/s.
  `MSRR_PlantData.Kinetics.sourceScale` stays emitted as a legacy value.
  The source term is `sourceEffectiveness*S/N0`. The source effectiveness
  eta_S (`kinetics.source_effectiveness`, `MSRR_PlantData.Kinetics.sourceEffectiveness`)
  defaults to 1, which is an unsourced unit-importance assumption, so
  absolute source-range power is a sensitivity result. `PKE`, `mPKE` and
  SegmentedMSR `PKE_T` assert `nu`, `nominalPower`, `energyPerFission` > 0 and
  `sourceEffectiveness` >= 0 at initialization, so a direct model override
  cannot bypass the deck validation.
- **Frozen circulating-fuel compensation** (`Nuclear.mPKE.rho_0nom`, see the
  section above; `liveFlowCompensation = true` restores the former rule).
- **9R transit times** (`MSRR.Components.MSRR9R`): `nomTauCore` / `nomTauLoop`
  follow the 9R mesh, (sum(volF1) + sum(volF2))/vdot = 41.01 s and the ex-core
  remainder of `totalFuelVol` = 13.38 s on the TASK-20261001-01 PSAR-basis
  flow (32.80 / 10.70 s on the pre-PSAR flow). They formerly followed the
  1R 0.4 m3 / 0.1 m3 convention (34.80 / 8.70 s on the pre-PSAR flow,
  43.51 / 10.88 s on the PSAR basis); the nominal circulation loss rises
  accordingly, from 67.11 to 77.33 pcm on the PSAR-basis data (72.0 to
  83.4 pcm before).
- **9R initialization** (`MSRR.R9MSRRuhx`): `TF1_0_regions`, `TF2_0_regions`,
  `TG_0_regions` bind the PlantData region profile directly, so all nine
  elements (region 1 included) are overridable from a setpoint table (the
  former `cat(1, {scalar}, ...)` binding made OMC refuse the overrides and
  region 1 took the shell scalars, i.e. the core average); a top-level
  `Tmix_0` parameter receives the tables' plenum column (formerly "not
  found", so the plenum always started at 580.41 °C).
- **9R startup reference** (`MSRR.MSRRstartUpCriticality9R`): 570 °C, as for
  1R (formerly 552.835 °C, which matched no setpoint table).
- **Flow-referenced startup staircase** (owner decision): with the frozen
  compensation, the startup models reference each staircase step to the
  critical position at the pump flow in effect (`flowReferencedStaircase =
  true`; offsets `-(loss(1) - loss(FF_stage))` from
  `MSRR.Functions.circulationLossPcm` with the core's own kinetics data and
  transit times at FF = 0.01 / 0.25 / 0.5 / 1: 1R -65.10 / -33.51 / -18.04 /
  0 pcm, 9R -75.19 / -39.79 / -21.55 / 0 pcm on the PSAR-basis data;
  -69.56 / -34.37 / -18.27 and -80.78 / -41.07 / -22.03 pcm before), so
  "0 pcm" stays just critical on every pump-stage
  plateau (the offset switches to the target stage while the pump ramps, so
  it is approximate during the short ramps); the pump schedule is bound from
  the same `startupPump*` parameters.
- **Runners and plots** (see `transients/README.md`, `freq/README.md`): the
  flow-case pump route runs at full flow until the t = 2000 s trip; 1R steps
  use the same setpoint-override route as 9R; the 9R "Core Avg."
  temperatures are volume-weighted; the frequency-response fits discard the
  switch-on transient and size the perturbation for a ~1% power swing.

## SegmentedMSR physics fixes (physics review 2026-09-27)

These were first applied to the segmented package only; the second pass above
brought the legacy models to the same state, and the copy-diff tests declare
the few remaining deltas against their lumped twins.

- **Heat exchanger UA.** `HeatTransport.HeatExchanger` applies `hApNom` and
  `hAsNom` as side totals, a quarter per node. The copy previously applied
  the full side UA at every node, a 4x total.
- **Step signals from t = 0.** `Signals.TimeDependent.Stepper` is active from
  its step time (`time >= stepTime[i]`). With the former strict `>`, a
  `{P at t = 0}` UHX demand read 0 in the initial equations, and every
  SteadyState loop initialized at zero heat removal.
- **Pump start.** `HeatTransport.Pump.startRamped` (default false) completes
  every ramp stage scheduled at or before t = 0. All trim rigs bind it true,
  so they initialize at their commanded flow instead of
  FF(0) = freeConvFF + 1e-4. The frozen `PKE_T` trim is therefore taken at
  nominal flow: `rhoTrim` = 66.33 pcm (1R) / 72.72 pcm (9R), not 2.5 pcm
  (TASK-20261001-01 PSAR-basis kinetics and flow; 71.16 / 79.04 pcm before).
- **9R core.**
  - Salt-to-graphite UA uses `u_j = hA_r*kHT_node/(kHT1_r + kHT2_r)`
    (the r1 channel total, 24,916 W/K at the time; it was 207 W/K.
    The total is 4565 W/K since the TASK-20261001-01 recomputation).
  - Graphite heat capacity follows each region's own `volG9R`
    (`SegmentedCore.useModVolShare`/`modVolShare`).
  - Within a region, the two graphite half-nodes split the region's
    graphite mass, its direct graphite heat and its salt contact by the
    same share `a_j = kHT_node/(kHT1_r + kHT2_r)`
    (`Reactors.htShareChain9R`; review 2026-10-01 M4). Each half then
    obeys the legacy region equation `m_G*cP*dT/dt = hA*(T_salt - T) +
    kG*P`, and the feedback tap is the `a_j`-weighted (mass-weighted) mean,
    which reproduces the legacy single graphite node exactly. Before M4
    the mass and direct heat split equally while the contact split by
    `a_j`: the halves had time constants `tau/(2 a_j)` (x1.41 / x0.78 in
    R2 and R5) and the arithmetic tap put the region graphite up to 1.0 K
    off the legacy 1 MW value. With the fix the 1 MW region graphite
    matches the legacy `MSRR.R9MSRRuhx` within 0.025 K, and the region
    graphite response to a thermal-only UHX step (1.0 -> 0.8 MW) within
    2e-3 K of a ~70 K change (1.3 K, 1.8 %, before).
- **Plant-bound material properties** (review 2026-10-01 M5). Every
  production rig and assembly (the `Reactors` 1R / 10-seg / 5x5 / 9R
  assemblies and trim rigs, the outer-annulus rig's `CoreVesselAssembly`,
  the two cavity demos and the `Verify` twins) binds its core's `rho_fuel`,
  `cP_fuel`, `rho_grap` and `cP_mod` to `SegmentedMSR_PlantData.Materials`,
  its graphite volume to the core's generated `volGN` (the 9R zones to
  `volGNZone9R`), and `hAExp` as described under the film laws above. The
  literal defaults of `Core.SegmentedCore` and `Vessel.CoreVesselAssembly`
  (2600 / 1776 / 1800 / 1773, 1.758 m3) now serve only the self-contained QA
  rigs, so a deck edit plus `python3.12 -m helpers.emit_modelica_plant`
  reaches every plant-bound rig. The values are unchanged, so every
  trajectory at `FF = 1` and every `checkModel` count is unchanged.
- **9R power scaling** (review 2026-10-01 H1). `R9MSRRuhxTrimThermalSS`,
  `MSRR9RThermalAdapter` and `Verify.R9RefMPKETrimSS` bind the power-block
  nominal power `P = SegmentedMSR_PlantData.nominalPower` (1 MW), as the
  1R, 10-segment and 5x5 vehicles do; `powerLevel` enters through
  `pke.n_0` and the UHX demand (`uhxDemandAmplitude =
  {powerLevel*nominalPower}`) only. They formerly bound `P =
  powerLevel*nominalPower`, so the initial fission power was `powerLevel^2`
  x 1 MW: none at the startup wrappers' `powerLevel = 0`, and 0.25 MW
  against a 0.5 MW sink at `powerLevel = 0.5`. Below 1 MW the 9R trim rig
  still drifts (n 0.50 -> 0.52 over 600 s at `powerLevel = 0.5`), because
  its feedback reference temperatures are the 1 MW profile.
- **Absolute source normalization.** `PKE_T` divides an absolute source
  S [n/s] by the full-power population N0 = LAMBDA*nu*P/E_f = 1.97e13
  (1.82e13 before the TASK-20261001-01 PSAR-basis LAMBDA), with
  `nu` from `kinetics.yaml`, and scales it by `sourceEffectiveness`
  (eta_S = 1 by default, an unsourced assumption). The legacy `Constants.nomSourceScale = 1.58e20`
  made 1e8 n/s act like ~12 n/s; it is kept only for the byte-frozen
  `Verify.RefMPKE` copy.
- **Precursor floors.** The precursor-network floors (`nFloor`,
  `nFloorDuringForcing`, `nFloorSwitchTime`) bind to `pke.*` in every
  assembly, so a runner override of the `pke.nFloor*` family also moves the
  production clamp.
- **Poison worth.** The `HomogeneousPoisons` worth denominator is derived as
  `Sigma_a = nu*Sigma_f`, with `Sigma_f = F0/(phi0*V_avg)`. Equilibrium Xe is
  -1287 pcm and Sm -441 pcm, instead of the ~1.9x values of the former
  1.0/m placeholder. The deck key `sigma_a_fuel` remains only as a
  documented legacy value for history-replay tests.
- **Qualified steady state.**
  - Every runner rig starts from the deck's `segmented_steady_state` block:
    per-cell fuel and graphite temperatures and the 9R plenum, with feedback
    referenced to the 570 °C isothermal zero-power state. Its feedback
    setpoints are the taps of the same arrays, so the rig starts stationary
    with zero initial feedback.
  - Regenerate the block with `python3.12 -m helpers.segmented_trim_levels
    --write`, then run `python3.12 -m helpers.emit_modelica_plant`. Each
    core first runs to 150000 s; while any temperature still moves more than
    1e-5 K over the last 10 % of the run, the run repeats with the horizon
    doubled, up to 600000 s (`--stop_time`, `--max_stop_time`,
    `--max_drift_K`). A deck is written only from a run that met the bar
    (review 2026-10-01 M9: the former single 40000 s horizon refused
    r1_10seg, r5x5_z10 and r9 on the PSAR-basis data). On the current data
    all four cores qualify at the first 150000 s horizon (drift 1.1e-9 K
    r1, 9.0e-9 K r1_10seg, 1.2e-8 K r5x5_z10, 1.2e-8 K r9; about 3 min for
    all four on one workstation). The r1 block was regenerated at that
    horizon (it was written at 40000 s and sat 1.3e-5 K from the
    stationary state).
  - `python3.12 -m helpers.segmented_trim_levels --check` recomputes every
    block with the same procedure and exits 1 when a committed block differs
    from the recomputed state by more than the 1e-5 K bar, or when the
    recomputation does not qualify (review 2026-10-01 M8). Recomputing also
    catches model changes that leave every deck input untouched (the M4
    graphite split moved the r9 half-nodes by up to 6.8 K). It reproduces
    all four committed blocks within 5e-11 K;
    `tests/test_segmented_trim_levels.py` runs it on r1 and r9
    (`omc_short`).
  - On the pre-PSAR data the 1R result reproduced the lumped 1 MW setpoint
    table within 1e-4 K. The promoted `core/init` tables predate the
    PSAR-basis data (see `core/init/README.md`), so that comparison waits
    for the next setpoint campaign.
  - Bleed-on start (TASK-20261001-01 item 3): the block is computed with the
    UHX demand alone, so each rig scales it to its own t = 0 removal.
    `initialRemovalRatio = (uhxDemandAmplitude[1] + dhrs.DHRS_P_Bleed) /
    uhxDemandAmplitude[1]` multiplies `pke.n_0` and every FixedStart core
    (and 9R plenum) temperature's deviation from `ssReferenceTemperature`
    (the core balances are linear at fixed flow with heat loss off). The
    trip vehicles, whose 0.005 P DHRS bleed runs from t = 0, therefore start
    from the bleed-on steady state (ratio 1.005): the 1R pre-trip energy
    residual is 8.8e-9 of P on [3000, 3990) s instead of 3.6e-6. With no
    bleed the ratio is exactly 1 and the initialization is bit-identical.
  - The outer-annulus rig keeps its legacy setpoints: its
    `Vessel.CoreVesselAssembly` has no per-cell initial-temperature
    pass-through.
- **Opt-in core heat loss.** Radiation from fuel cells to `heatLossTinf` is
  available through `heatLossEnabled` on the four runner rigs (default
  false; FixedStart core only) — the lumped `heatLossEnabled` counterpart.
  - The 1R, 10-seg and 5x5 rigs radiate the lumped 1R area 2 x 3.5172 m2.
    The 10-seg rig splits it over its segments; the 5x5 rig splits it over
    segments and channels.
  - The 9R rig radiates from the R8/R9 cells and the upper plenum, like the
    lumped `MSRR9R`.
- **Conduction guards.** `Pipe`, `MixingPot` and `DHRS` refuse K > 0, and the
  HX inlet faces are adiabatic. The signal connectors carry no heat flow, so
  inter-component axial conduction would create or destroy energy. All
  shipped bindings are K = 0.
- **5x5 lattice.** The deck carries the inventory-consistent effective
  lattice (see the r5x5_z10 section below).

## SegmentedMSR standalone package (`SegmentedMSR.mo`)

Clean-break segmented-core MSR library (plan: `todo/SegmentedMSR-implementation-plan.md`,
task card `.collab/tasks/TASK-20260822-02.md`). Loads side-by-side with the lumped
`SMD_MSR_Modelica.mo` / `MSRR.mo`, which remain editable production sources. The
pre-SegmentedMSR snapshot is git branch `legacy-freeze` (commit `9924090`, parent
of the P1 skeleton).

- `SegmentedMSR.Core` (P2): geometry-only `ChannelMap` backed by pure `Geometry`
  services, `FuelSaltCell`, `ModeratorSegment`, and `SegmentedCore` with central
  per-contact conductance signals (Newton's third law by construction) and
  `Lumping.PerCell | Single` moderator modes.
- `SegmentedMSR.QA` (P2): phase-gate models. `QA.GeometryCheck` asserts lattice
  identities at initialization; `QA.NeighborMapCheck` sweeps neighbor-table
  properties exhaustively over four lattice layouts — no self-entries, no
  duplicate slot-row entries, pairwise symmetry with return entries — anchored
  by exact expected entries so the sweeps cannot pass vacuously on empty
  tables; `QA.SymmetryCheck` enforces 3-channel symmetry (<1e-6 K spread) and
  3-vs-1-channel equivalence (<1e-8 rel) via runtime asserts — run each to at
  least its documented stopTime.
- Radial moderator conduction (2026-08-25, task card
  `.collab/tasks/TASK-20260825-03.md`): implemented in `Core.SegmentedCore`
  for both pitch types. Square lattices expose four live neighbor slots
  (offsets at 90 deg), triangular lattices six (60 deg); every neighbor
  array keeps the fixed width `nbrSlots = 6` with unused slots padded `-1`.
  Missing neighbors are adiabatic: `Geometry.faceAreas` returns 0 wherever
  the neighbor entry is -1, so `G_ck = 0` on those faces, and the safe route
  table `nbrRoute` maps each missing slot back to the owning channel so no
  routing equation ever indexes with -1. Routing is active iff the
  `ChannelMap` flag `enableRadialMod` — a structural parameter bound before
  translation, not a runtime signal. Flag false pins every neighbor port to
  the owning node's own temperature, so every `qNbr` is identically zero and
  pre-radial results stay numerically unchanged. On `ModeratorSegment`,
  `K_mod = 0` is valid (ports stay wired but adiabatic), while a negative
  value aborts initialization with "ModeratorSegment: K_mod must be >= 0";
  initial graphite temperatures enter through the `SegmentedCore` parameter
  `T_mod_0[nChan, nSeg]` (default `fill(T_0, ...)`; Single mode consumes
  `T_mod_0[c, 1]`). Face-area aggregation follows the mass: PerCell hands
  every `M_cell[c, j]` its own one-segment face area from `map.faceA`
  (square `p*dz`, triangular `(p/sqrt(3))*dz`; uniform `dz` keeps the
  per-slice areas equal), while Single owns the full channel graphite mass
  (`m_mod = rho_grap*vol_GN`) and binds the SUM of all `nSeg` axial faces,
  `M_chan.faceANbr = nSeg*map.faceA`, giving a channel-scale conductance
  `G_chan = nSeg*K_mod*A_seg/L` instead of the cell-scale
  `G_seg = K_mod*A_seg/L`. Under axially uniform conditions both lumpings
  exchange the same total radial power and equalize at the same rate: two
  equal capacities `C` linked by conductance `G` close their spread as
  `DeltaT(t) = DeltaT(0)*exp(-(2*G/C)*t)`. Production stays off:
  `Reactors.MSRR1R` builds its map
  with `nChan = 1`, `xy = [0, 0]`, leaves `enableRadialMod` at its false
  default, and binds no positive `K_mod` (default 0); the four 9R zone cores
  are likewise single-channel with the same defaults. Conservation evidence:
  `QA.RadialConductionCheck` (listed in `CHECKABLE_CLASSES`,
  `tests/test_segmented_msr.py`) runs six isolated PerCell lattices — a
  five-channel square cross and a three-channel triangular trio, each
  carrying `K_mod = 0` and flag-off controls — plus a Single-lumping
  two-channel square pair; zero flow, zero power, and `nSeg = 1` make radial
  graphite conduction the only moderator energy path. Active lattices
  conserve `E_mod` to 1e-7 relative and equalize to under 1% of their
  initial temperature spread; every control holds at least 99% of it with
  all `qNbr` identically zero. Run past the documented assert time
  (`tEnd = 100 s`; suggested stopTime 105 s). That Single smoke runs
  `nSeg = 1`, where one face and the sum of faces coincide, so it cannot
  expose an aggregation error; `QA.SingleRadialAreaCheck` (same
  `CHECKABLE_CLASSES` registry) is the multi-segment evidence: two
  isolated twins on the same two-channel square pair at `nSeg = 4`,
  differing only in lumping, must keep PerCell channel means on the Single
  nodes within abs 0.05 K / rel 1e-3 while both follow the analytical rate
  `DeltaT(t) = 30*exp(-lambda*t)` K with
  `lambda = 2*G_chan/C_chan = 2*G_seg/C_cell = 0.526414740 1/s`
  (10.468 K at t = 2 s, 2.158 K at t = 5 s; carrying one segment area
  instead decays four times slower and reads ~23.06 K at t = 2 s),
  conserve `E_mod` to 1e-7 relative, and hold an independently
  reconstructed Python sum over every live directed `qNbr` slot within
  1e-6 W at each sampled row (run stopTime >= 20 s).
- `SegmentedMSR.Nuclear` (P3): inventory-form `PrecursorNetwork` over the shared
  `ChannelMap` (production only in salt cells via `fSalt`; precursor decay in
  every compartment incl. the fixed-order loop chain) driving external-source
  point kinetics `PKE_T`, which freezes its trim residual `rhoTrim` at init and
  exposes the live `rhoCirc` diagnostic.
- `SegmentedMSR.QA` (P3): exit-gate models `QA.PKELimitCheck` (stationary-fuel
  source identity at zero flow), `QA.DelayedSourceIdentityCheck` (per-group
  production integral vs `beta/LAMBDA*n`), and `QA.SteadyTrimCheck`
  (steady-trim consistency) — run via `buildModel(tolerance=...)` + the
  generated executable per the omc 1.27 notes below.
- `SegmentedMSR.Reactors` (P4): named `*1R` / `*9R` aliases bind
  `SegmentedMSR_PlantData` (kelvin, generated from `data/plants/msrr`);
  derived 9R chains stay Modelica expressions over those arrays. Product
  first-cut assembly `MSRR1R` (PerCell moderator lumping, `qModDirect`
  from plant YAML); the QA-Collapse harness `R1MSRRuhxTrimThermalSS`
  keeps collapse `qModDirect [0.07, 0]` and the 1 pcm sine @2000 s as
  harness-only literals. Load `SegmentedMSR_PlantData.mo` before
  `SegmentedMSR.mo`.
- `SegmentedMSR.Verify.R1RefMPKETrimSS` (P4): QA-Collapse stage-A vehicle —
  the identical harness loop with kinetics swapped to the verbatim legacy
  mPKE copy `Verify.RefMPKE` at parity taus `nomTauCore` / `nomTauLoop`
  = `Kinetics.nomTauCore` / `nomTauLoop` (43.5146 s / 10.8787 s on the
  TASK-20261001-01 PSAR-basis deck; 34.8025 s / 8.7006 s before);
  harness-only, never a product path.
- `SegmentedMSR.QA` (P4): exit-gate models `QA.EnergyBalanceCheck`
  (energy-accounting closure of the full harness — reactor power vs UHX
  outflow, storage-rate corrected, |resid| < 1e-6 of P at two post-settle
  checkpoints) and `QA.StagnationDNPCheck` / `QA.StagnationThermalCheck`
  (stage 1/2 stagnant-flow well-posedness on dedicated prescribed-FF=0 QA
  rigs — never a pump trip of the plant assembly; stage 2 sweeps
  `G_stag = eps*hAnom` for eps in {0.01, 0.1}).
- Long-overlay evidence lives under `00runs/segmented-msr-validation/p4/`
  (driver scripts `p4_collapseA.mos` ... `p4_stag2.mos` plus result CSVs);
  long overlays stay OUT of pytest and are post-processed by
  `helpers/analyze_p4_overlays.py` (run from repo root:
  `python3.12 -m pytest helpers/analyze_p4_overlays.py -q -s`).
- `SegmentedMSR.QA` (P5): circulating-fuel flow-step characterization rigs
  `QA.FlowStepLiveCheck` / `QA.FlowStepFrozenCheck` — two variants of one
  isolated protocol (pump `FF` steps 1.0 -> 0.5 at t = 50 s after a settle
  window; temperature feedback, external reactivity, and external source all
  pinned at zero; identical `Reactors` kinetics data, `n_0 = 1`) exercising
  the two circulating-fuel reactivity conventions side by side:
  `FlowStepLiveCheck` runs the LIVE lumped `rho_0dyn` convention through the
  verbatim mPKE twin `Verify.RefMPKE` at the same parity taus as
  `R1RefMPKETrimSS`, while `FlowStepFrozenCheck` runs the segmented FROZEN
  `rhoTrim` convention through production `PKE_T` + `PrecursorNetwork` on the
  collapse topology. Characterization only: no production equation changed,
  and both conventions remain as shipped; measured trajectories live under
  `00runs/segmented-msr-validation/p5/`.
- `SegmentedMSR.Reactors` (P6a): verbatim 9R reference data transcribed from
  the lumped `msre9r` instantiation (`core/MSRR.mo:981`) — region
  volumes `volF19R`/`volF29R`/`volG9R`, per-region UA `hA9R`
  (94.04..1316.33 W/K on the PSAR-basis data, 513.29..7184.60 W/K before),
  splits `kFN19R`/`kFN29R`/`kHT19R`/`kHT29R`, zone
  table Z1={R1}, Z2={R2->R3->R4}, Z3={R5->R6->R7}, Z4={R8->R9} with
  `nSegZone9R {2,6,6,4}` and zone flow fractions
  `{0.061410, 0.138550, 0.234231, 0.565809}`, plus region trim setpoints
  `TF1Regions9R`/`TF2Regions9R`/`TGRegions9R`. Derived constants remain
  expressions over these raw arrays: the 18-cell `cellVolChain9R` /
  `fSaltGlobal9R` / `qModChain9R` chains (global fSalt sum = 1 via the
  data-derived normalizer `fSaltNormalizer9R`, renormalized salt array sum
  0.9392704135017; the direct moderator shares stay un-normalized,
  `qModTotal9R` = 0.060729586498348 ~ 6.07%, the Sigma=1-renormalized
  graphite family, never forced to a thesis 7%) and the historical
  predicted-offset table `predOffsetFW9R`
  = `hA_i*kHT2_i/(kHT1_i+kHT2_i)*(TF2_i - TF1_i)` [W] (pre-fix FuelChannel
  on `legacy-freeze`, using the production convection split; total ~9.73 kW
  on the TASK-20261001-01 PSAR-basis hA, ~53.1 kW before. The un-normalized
  product `kHT2_i*hA_i*(TF2_i-TF1_i)` totals ~66 W, ~362 W before, and
  is not this table).
- `SegmentedMSR.Reactors.MSRR9RThermalAdapter` (P6a): 9R zone-chain thermal
  adapter composing FOUR single-zone product `SegmentedCore` instances
  (nChan=1, uniform-nSeg maps 2/6/6/4; the heterogeneous zone shape never
  enters the product `ChannelMap` contract) through ONE shared STATEFUL top
  plenum (`MixingPot` numStreams=4; `volUP` is the fixed
  `Core9R.volUpperPlenum` = 0.022986 m3 since TASK-20261001-01, formerly
  `2*Vdot`) and then the standard
  primary+secondary loop. Pot Ac/L/Ar/e geometry binds the HARNESS ONLY (plan
  principle P4); since the physics review 2026-09-27 the plenum starts at
  the qualified steady state (`Core9R.ssTPlenum`), not the legacy
  `Tmix_0 = 580.41` °C literal. Initial-equation
  gates: sum of all zone cellVol = 0.377012772187 m3 (+-1e-9), global
  sum(fSalt) = 1, the Sigma=1 identity qModTotal9R + fSaltNormalizer9R = 1
  (+-1e-9), sum(qModDirect) = 0.060729586498348 (+-1e-9), exactly four zone
  outlets into the shared plenum, and the `|TotalFuelVol - 0.500| < 1e-6`
  cross-check WARNING (core + loop + volUP inventory).
- `SegmentedMSR.QA.SymmetryCheckZoneChain` (P6a): "QA-Symmetry extended"
  exit gate — four identical uniform-power zone cores under proportionally
  distributed flow, wired through the same shared-plenum composition
  pattern, stay symmetric across zones (max|dT| < 1e-6 K) AND match an
  equivalent single-chain run to < 1e-8 relative on the outlet rise above
  inlet (SELF-03 tolerances reused verbatim; also pins FF_hA to pump FF
  only). Original `QA.SymmetryCheck` untouched.
- Product-component delta (P6a): `Core.SegmentedCore.s_G` generalized from
  per-channel `s_G[nChan]` to PER-CONTACT `s_G[nChan,nSeg]`, default
  `ones(nChan, nSeg)` — numerically identical for every pre-P6a
  instantiation; the adapter binds `nSeg_z*u_j/Uz` per contact so the
  conductance law reproduces each legacy per-node UA while `hAnom[z]`
  carries the zone-total UA and `htFrac` keeps its genuinely-shared-UA
  meaning (left default here). Per-cell UA is the legacy convection split
  `u_j = hA_r*kHT_node/(kHT1_r + kHT2_r)` (18 cells sum to 4564.99 W/K on
  the PSAR-basis data, 24,915.96 W/K before).
  Before the physics review 2026-09-27 the `1/(kHT1 + kHT2)` factor was
  missing (a 207 W/K core with effectively adiabatic graphite), so segmented
  9R results and P6/P6b thermal evidence from before that fix are invalid.
  The zone cores also bind `useModVolShare = true` with
  `modVolShare = volG9R[r]*a_j/volGNZone9R[z]` per cell
  (`a_j = htShareChain9R[j]`; `volG9R[r]/2` before review 2026-10-01 M4),
  so each region keeps its own lumped graphite heat capacity instead of the
  zone average, and `qModChain9R` splits each region's direct graphite
  share `kHT1 + kHT2` by the same `a_j`.
- P6a gate evidence lives under `00runs/segmented-msr-validation/p6/`
  (extraction identities, zone-chain symmetry, adapter smoke — result CSVs
  + logs); produced by the runner `helpers/check_p6_gates.py` (from repo
  root: `python3.12 -m pytest helpers/check_p6_gates.py -q -s`; resolves
  the repo root from `__file__`, no hardcoded absolute paths). Long
  overlays stay OUT of pytest.
- `SegmentedMSR.Reactors.PrecursorZoneChain9R` (P6b): adapter-level kinetics
  composite over the 18-cell zone chain (Ambiguity-1 route-(ii); product
  `ChannelMap`/`PrecursorNetwork` untouched). ONE shared primary-loop chain
  in fixed named order {pipeCoreToDHRS, DHRS, pipeDHRStoHX, HXprimary,
  pipeHXtoCore} with the PLAN-10 elementwise init assert against the actual
  thermal-loop component volumes (`volLoopRef` bound at the wiring site);
  guarded multi-inlet top-plenum mixing (CHPT-2 form, denominator floored at
  `Core.vdot_eps = 1e-12` m3/s, finite volume-weighted fallback, inert at
  exactly FF=0); STATEFUL upper-plenum storage compartment (inventory over
  `volUP = volUP9R`, decay active); bottom distribution by zone fractions
  (single loop return, intensive-concentration pass-through, SELF-05).
  Production ONLY via `fSaltGlobal9R` (global sum = 1 EXACTLY, asserted);
  decay in EVERY compartment; no `delay()` lines.
- Feedback aggregation (P6b, QWEN-08 disposition landed here): NINE product
  `ReactivityFeedback` instances rf1..rf9 — one per region r with its OWN
  importance weights `IF19R[r]`/`IF29R[r]`/`IG9R[r]` and trim setpoints
  `TF1Regions9R[r]`/`TF2Regions9R[r]`/`TGRegions9R[r]` — summed via product
  `SumReactivity(numInput = 9)`, each tapped to the OWNING region's two
  salt-cell temperatures plus the `htShareChain9R`-weighted (mass-weighted)
  mean of its two PerCell moderator segments (the arithmetic mean before
  review 2026-10-01 M4); legacy-faithful to `RF1..RF9 + sumFB`
  (`core/MSRR.mo:453-472`). The 9->4 zone-aggregated variant was rejected
  (no legacy anchor for intra-zone temperature/IF/IG redistribution).
- `SegmentedMSR.Reactors.R9MSRRuhxTrimThermalSS` (P6b): QA-Collapse-B
  STAGE-B rig — the zone-chain thermal composition with kinetics swapped to
  `PKE_T` + `PrecursorZoneChain9R`. Harness parity literals bind THIS
  HARNESS ONLY (plan principle P4): 1 pcm sine @2000 s omega=0.01, pumps
  `tripTime=1e7 s`, `DHRS_time=1e6`, SteadyState init on ALL loop
  components incl. the secondary side, external source amplitude 0;
  `PrecursorZoneChain9R` owns its own initMode=SteadyState (PLAN24-03).
- `SegmentedMSR.Verify.R9RefMPKETrimSS` (P6b): QA-Collapse STAGE-A twin on
  the verbatim legacy mPKE copy `Verify.RefMPKE` at parity taus
  `nomTauCore` / `nomTauLoop` = `Kinetics.nomTauCore` / `nomTauLoop`
  (43.5146 s / 10.8787 s on the TASK-20261001-01 PSAR-basis deck;
  34.8025 s / 8.7006 s before). Only delta vs the
  stage-B rig is the kinetics stack, so the Collapse-B rel-RMS gate
  isolates kinetics; harness-only, never a product path.
- QA-Collapse-B exit gate (P6b, protocol carried verbatim): HARD n(t) rel
  RMS(stage B vs stage A) < 1% over [2000,4000] s; rhoCirc informational
  with |dev| > 15 pcm a HARD FAIL measured against the DOCUMENTED 9R-topology
  EXPECT `72.7240 pcm`, derived and validated (same solver must
  reproduce the analytic 66.3294 pcm 1R-collapse value; recorded in
  `00runs/segmented-msr-validation/p6/p6b_rho_circ_expect.txt`). The
  66.33 pcm EXPECT is 1R-only and does NOT carry over. TASK-20261001-01
  re-derived both on the PSAR-basis kinetics, flow and fixed plenum (the
  9R rig's own t0 rhoCirc is 72.72396 pcm); the P6b-phase values were
  79.0370 pcm (9R) and 71.1602 / 71.16 pcm (1R).
- P6b gate evidence lives under `00runs/segmented-msr-validation/p6/`
  (stage-A/stage-B overlay CSVs + logs, rhoCirc derivation record, gate
  summary); produced by the runner `helpers/check_p6b_gates.py` (from repo
  root: `python3.12 -m pytest helpers/check_p6b_gates.py -q -s`; resolves
  the repo root from `__file__`, no hardcoded absolute paths). Long
  overlays stay OUT of pytest.
- `SegmentedMSR.Reactors` scenario vehicles (2026-08-23, S1, task card
  `.collab/tasks/TASK-20260823-02.md`): `R1MSRRuhxTripThermalSS` /
  `R9MSRRuhxTripThermalSS` — thin extends-wrappers over the trim rigs
  carrying a STRUCTURAL two-step UHX demand (`numUhxSteps = 2`,
  `{1e6 -> 0}` W at `t = {0, 4000}` s) plus DHRS engagement
  (`DHRS_time = 4000 s`), mirroring the legacy MSRR.mo trip pair
  (`core/MSRR.mo:1131-1152`); `-override=` cannot resize arrays, hence
  dedicated wrapper models. No startup-schedule wrappers ship in S1
  (Ambiguity A stays open for S4). The segmented startup scenarios now run
  through generated scenario wrappers (`helpers/emit_scenario_wrapper.py`,
  review 2026-10-01 H2/H3; see `startup/README.md`), which replace the
  qualified 1 MW start with an isothermal core at the zero-power reference.
- Shared runner mapping (S1): `helpers/segmented_runs.py` is the single
  source of truth for driving the standalone package — core_model ->
  segmented vehicle names (trim rigs + trip pair), the one-file library
  list (`("SegmentedMSR.mo",)`), standalone check/smoke mos builders, and
  segmented CSV column candidates for the downstream plot-side integration.
  On the models themselves, the one-region overlay temperature columns
  `TF1`/`TF2`/`TG`/`TinCore`/`ToutCore`
  (`Reactors.R1MSRRuhxTrimThermalSS`, `Verify.R1RefMPKETrimSS`) are typed
  `SegmentedMSR.Units.Temperature` (kelvin values; CSV column names
  unchanged), while the nine-region overlay temperatures (`TZout[4]`,
  `TPot`, `ToutPlenum`, `TinCore`) remain `output Real ...(unit="K")`; the
  ten-segment rig carries the one-region set (the plot-side column
  candidates in `helpers/segmented_runs.py` mirror the 1R entries for
  `1r10seg`, and `transients.plot_nonlinear_steps` accepts it on
  `--package segmented`).
- S1 gate evidence lives under
  `00runs/segmented-msr-validation/scenarios/s1/` (check-sweep output +
  balance counts, wrapper smoke build/run outputs + result CSVs); produced
  by the runner `helpers/check_s1_gates.py` (from repo root:
  `python3.12 -m pytest helpers/check_s1_gates.py -q -s`; omc scratch
  under `00runs/tmp/omc/s1/`). Long overlays stay OUT of pytest.
- `SegmentedMSR.QA.KelvinRadiationCheck` (2026-08-26): isolated
  active-radiation identity rig under the kelvin contract — three
  fully-bound `MixingPot` instances assert `powRad` equals
  `SigSBK*(900^4 - 800^4)` with the ambient hotter, minus that value
  when the component is hotter, and identically zero under the
  `EnableRad = false` control; production assemblies still bind
  `EnableRad = false`. When `EnableRad` is true, Pipe, MixingPot, DHRS,
  and the heat-exchanger primary nodes abort if ambient or radiating-node
   temperatures go negative (fourth powers would otherwise hide a failed
   trajectory).
- 1-channel x 10-axial-segment 1R core (2026-09-06, task card
  `.collab/tasks/TASK-20260906-01.md`): a third core on the same framework
  -- CLI `--core_model 1r10seg`, YAML core key `r1_10seg`
  (`data/plants/msrr/cores/r1_10seg.yaml`), emitted plant record
  `SegmentedMSR_PlantData.Core1R_10Seg` (kelvin). FIRST CUT: uniform
  partition of the r1 2-segment totals -- no measured axial
  power/temperature profile exists in the repository, so each of the ten
  segments carries 1/10 of each total (cell_vol 0.04 m3 x 10, sum 0.4 m3;
  q_fiss 0.093 x 10, sum 0.93; q_mod 0.007 x 10, sum 0.07 = kG;
  kHT 0.1 x 10, sum 1.0; dz 0.14 m per segment, 10 x 0.14 = 1.4 m = 2 x the
  r1 0.7 m); `vol_graphite` (1.758 m3), `hAnom` (4565 W/K since the
  TASK-20261001-01 recomputation, 2.4916e4 W/K before),
  `IF [0.5, 0.5]`, `IG` (1.0), `hAExp` (0.33), and the lattice metadata
  (pitch 0.02 m, `chanR = [0.008]`, single channel at `[0, 0]`) are
  inherited unchanged from r1. Coarsening anchor: grouping segments 1-5 /
  6-10 reproduces the existing 2-segment values exactly (0.2/0.2 m3,
  0.465/0.465, 0.035/0.035, 0.5/0.5), and the fission split normalizes to
  `fSalt [0.1] x 10`. Axial moderator conduction is the first cut
  `K_mod = 0` (and `G_stag = 0`) until a sourced physical value exists. The
  product feedback API stays 2-node: TF1 applies to segments 1-5, TF2 to
  segments 6-10, TG to all ten graphite segments; the taps read the 5+5
  group means (uniform cellVol, so plain means are the volume-weighted
  aggregation). New vehicles: `Reactors.MSRR1R_10Seg` (product assembly,
  PerCell, the `MSRR1R` pattern with the same |TotalFuelVol - 0.500| < 1e-6
  cross-check WARNING), `Reactors.R1MSRRuhx10SegTrimThermalSS` (full-loop
  runner vehicle, the `R1MSRRuhxTrimThermalSS` pattern -- 1 pcm sine @
  2000 s with omega 0.01 rad/s, pump tripTime 1e7 s, DHRS_time 1e6 s,
  SteadyState init on all loop components including the secondary side;
  core thermal nodes stay FixedStart at the uniform 843.15 K; the literals
  are harness-only parity and do not enter any product assembly), and
  `Reactors.R1MSRRuhx10SegTripThermalSS` (thin extends wrapper, structural
  `numUhxSteps = 2`, the S1 trip literals). Three dedicated QA check models
  under `SegmentedMSR.QA`: `QA.DelayedSourceIdentityCheck10Seg` (the
  production-identity assert re-run on the 10-seg product topology: fission
  split sums to 1, per-group production integral equals beta_i n / LAMBDA
  with residual < 1e-12 per group), `QA.SteadyTrimCheck10Seg` (steady-trim
  consistency/well-posedness on the 10-seg topology; t0-consistent
  `rhoCirc` 68.4769140 pcm, re-measured for TASK-20261001-01 on the
  PSAR-basis data (73.4682658 pcm before), hard fail beyond 15 pcm,
  warning band +/-2 pcm),
  and `QA.CoarseningConsistencyCheck10Seg` (10-seg vs 2-seg product cores
  under identical pinned inputs: group-outlet fuel/graphite nodes within
  1e-3 K and total advected power within 1e-4 relative at steady state,
  checked at tCheck = 16000 s (see the status note under the 5x5 core),
  plus a static t0 anchor that the 1-5/6-10 grouping of the 10-seg data
  reproduces the 2-seg data exactly). No setpoint table ships: the
  segmented path carries its own trimmed SteadyState init. Runner wiring:
  `helpers/segmented_runs.py` (`CORE_KEYS`, `MODEL_BY_CORE`,
  `TRIP_MODEL_BY_CORE`) and the startup/freq/transients runners accept
  `1r10seg` on `--package segmented`; `--package legacy` refuses it with a
  named error before any omc invocation (no legacy 10-segment vehicle
  exists).
- 5x5-radial x 10-axial-segment 1R core (2026-09-06, task card
  `.collab/tasks/TASK-20260906-02.md`): a fourth core on the same framework
  -- CLI `--core_model r5x5_z10`, YAML core key `r5x5_z10`
  (`data/plants/msrr/cores/r5x5_z10.yaml`), emitted plant record
  `SegmentedMSR_PlantData.CoreR5x5_Z10` (kelvin). FIRST CUT: uniform
  25-channel x 10-segment partition of the r1 totals (9R zone-partition
  precedent) -- no measured radial power/flow profile exists in the
  repository, so each of the 25 channels on the staggered 5x5 triangular
  lattice carries 1/25 of each channel-level total (cell_vol 0.0016 m3 x 250
  = 0.4 m3; q_fiss 0.00372 x 250 = 0.93; q_mod 0.00028 x 250 = 0.07 = kG;
  kHT 0.1 x 10 per segment; flow_frac 0.04 x 25 = 1.0; hAnom 182.6 W/K x 25
  = 4565 W/K since TASK-20261001-01, formerly 996.64 W/K x 25 = 2.4916e4
  W/K; dz 0.14 m per segment, 10 x 0.14 = 1.4 m), and the
  per-channel column sums reproduce the `r1_10seg` data exactly (cellVol
  0.04 m3, qFiss 0.093, qMod 0.007, kHT 0.1 per segment; flow 1.0; hAnom
  4565 W/K). Lattice: staggered 5x5 triangular, the inventory-consistent
  effective lattice described below (pitch 0.266825 m, `chanR = 0.060314` m
  x 25; the first cut inherited the r1 placeholders pitch 0.02 m and
  `chanR = 0.008` m, under which G_ax used an annulus area of 1.4535e-4 m2).
  G_ck = K_mod*dz/sqrt(3) is pitch-independent by construction; the `xy`
  channel centers are stored translated by
  +2p in x so every coordinate is non-negative (the shared data envelope
  rejects negative meters; the translation is physics-inert because the
  Modelica neighbor matching is distance-based). Neighbor table: 56
  undirected edges = 112 directed entries of the 150 slots, per-degree
  channel counts {2:2, 3:5, 4:6, 5:3, 6:9} (static t0 pins). K_mod = 86.0
  W/(m.K) is the first non-zero product binding (decision 6, owner-approved
  2026-09-06): first cut from the INL G-348 measured k(T) correlation
  (k = 136.0 - 0.1092*T + 3.772e-5*T^2 W/(m.K), T in degC, ~3% uncertainty,
  valid 20-1000 degC) evaluated at the 570 degC graphite representative
  temperature (TG setpoint; paper linearization band 550-650 degC); MSR-1
  graphite-grade confirmation is an open follow-up. `enableRadialMod = true`
  is the first product map with the radial path enabled; the product
  assembly binds vol_GN/nChan = 1.758/25 on SegmentedCore, so the 250 PerCell
  moderator nodes sum to rho_grap*1.758 exactly. The feedback API stays
  2-node: TF1 applies to segments 1-5, TF2 to 6-10, TG to all 250 graphite
  cells; the taps read the 5+5 x 25-channel group means (125-term sums;
  uniform cellVol, so plain means are the volume-weighted aggregation). New
  vehicles: `Reactors.MSRR5x5_Z10` (product assembly, PerCell, qModDirect
  0.00028 x 250, the first product map with enableRadialMod = true and a
  non-zero K_mod), `Reactors.R5x5Z10MSRRuhxTrimThermalSS` (full-loop runner
  vehicle, the trim-rig pattern with the same harness-only parity literals),
  and `Reactors.R5x5Z10MSRRuhxTripThermalSS` (thin extends wrapper,
  structural `numUhxSteps = 2`, the S1 trip literals). Three dedicated QA
  check models under `SegmentedMSR.QA`: `QA.DelayedSourceIdentityCheck5x5Z10`
  (the production-identity assert re-run on the 250-cell triangular
  topology: fission split sums to 1 over the 250 salt cells, per-group
  production integral equals beta_i n / LAMBDA with residual < 1e-12 per
  group), `QA.SteadyTrimCheck5x5Z10` (steady-trim consistency/well-posedness
  on the 25-channel topology; t0-consistent `rhoCirc` 68.4769140 pcm
  (73.4682658 pcm before TASK-20261001-01)
  inherited from the 10Seg check through the 1/25-replica identity, hard
  fail beyond 15 pcm, warning band +/-2 pcm), and
  `QA.CoarseningConsistencyCheck5x5Z10` (5x5 core, K_mod = 86.0, vs a
  1r10seg reference core that carries the MATCHED summed axial conductance
  K_mod = K10ax, G_ax10 = 25*G_ax5 = 771.37 W/K, under identical pinned
  inputs: since TASK-20261001-01 an exact identity, segment-5 and
  segment-10 fuel and graphite taps within 1e-6 K, total advected power and
  each core's advected power within 1e-4 relative at tCheck = 16000 s;
  measured <= 2.2e-8 K over the run, 0.55 W power residual at tCheck; static t0 anchors pin the neighbor table
  (degree histogram, 112 of 150 directed slots, no self-neighbor, no
  duplicate slot, enableRadialMod true, K_mod = 86.0) and the 25x column
  sums reproducing the 1r10seg data exactly). The wider segment-10 graphite
  bar documents a real model difference, not a partition defect: at the
  uniform partition the radial zero-flux symmetry is exact for the 25-fold
  symmetric state, but the axial graphite links act on the steady-state
  graphite profile, while the product 1r10seg core omits axial graphite
  conduction. Since the physics review 2026-09-27 the 5x5 deck carries the
  inventory-consistent effective lattice (pitch 0.2668 m, chanR 0.0603 m, so
  `modAnnulusArea*nSeg*dz = vol_GN/nChan`). This gives G_ax ~ 30.85 W/K per
  axial link; the former 0.02 m placeholder pitch gave ~0.09 W/K, ~346x too
  small for the node heat capacity. Offsets measured at the check's steady
  state (K_mod = 86.0, nominal 1 MWt, tolerance 1e-10, flat to 4400 s):
  segment-10 outlet graphite -0.511 K, segment-5 graphite +0.034 K,
  segment-5 fuel +0.033 K, segment-10 fuel -1.5e-7 K (pre-PSAR data).
  TASK-20261001-01 status: on the PSAR-basis data (hAnom 4565 W/K) both
  coarsening checks failed their bars at the former tCheck = 4000 s. The
  10Seg identity still holds but its slow mode had not settled (residuals
  up to 0.084 K and 7.1 kW). Review 2026-10-01 H4 moved both checks to
  tCheck = 16000 s (measured on the PSAR-basis data, OpenModelica 1.27.1,
  tolerance 1e-10): the seven 10Seg residuals decay at tau = 1.29-1.54 ks,
  the slowest (group-2 outlet graphite) falls below its 1e-3 K bar at
  11.4 ks, and at 16000 s every residual is at least 23x inside its bar,
  so the 10Seg check passes. The 5x5 check at 16000 s measures its settled
  state (within 0.03% of the 40000 s values): segment-10 outlet graphite
  -2.086 K and segment-5 graphite +0.0931 K, outside the 1.0 / 0.07 K bars;
  segment-5 fuel +0.0408 K, segment-10 fuel -8e-7 K and all three power
  residuals below 0.6 W pass. That comparison failed its run (at error
  level since H4); the investigation (00runs/tmp/coarsen5x5/REPORT.md)
  traced the offsets to the adiabatic-end AXIAL graphite conduction, which
  the K_mod = 0 reference omitted (radial conduction and the partition are
  exact), so the check was restructured to the matched-axial identity
  above (owner decision 2026-10-01).
  QA runs need stopTime >= 16000 s for both checks. No setpoint table ships: the segmented path carries
  its own trimmed SteadyState init. Runner wiring: `helpers/segmented_runs.py`
  (`CORE_KEYS`, `MODEL_BY_CORE`, `TRIP_MODEL_BY_CORE`, the 1R overlay column
  set and the single `fb.TotalTempFeedback` feedback column) and the
  startup/freq/transients runners and `helpers.emit_scenario_wrapper` accept
  `r5x5_z10` on `--package segmented`; `--package legacy` refuses it with a
  named error before any omc invocation (no legacy 5x5 vehicle exists). The
  transients plotter overlays it as the fourth grayscale channel labeled
  `5x5-10Seg` (dash-dot, lightness 0.85, width 0.9).
- Optional intra-channel radial stack + closed annular cooling loop
  (2026-09-14, task card `.collab/tasks/TASK-20260914-01.md`, plan
  `.collab/optional_intra_channel_radial_segmentation_plan.md`): per-core
  opt-in via the plant YAML `intra_channel_radial` block (`data/schema/
  plant.schema.json` `$defs.intraChannelRadial`). Three structural
  decisions (stack present / annular fluid static vs. circulating /
  dedicated loop heat exchanger) admit exactly four valid combinations;
  the heat exchanger is refused unless the stack is enabled AND the
  annular fluid circulates; absent block or `enabled: false` keeps the
   direct fuel-to-moderator `Gsig` path structurally and numerically
   unchanged and the disabled generated records byte-stable. Production
   plant-chain binding (rev019 Phase 5 remediation,
   `.collab/tasks/TASK-20260915-01.md` P5): the six rank-matching
   production `SegmentedCore` instances in `SegmentedMSR.Reactors` --
   `MSRR1R`, `R1MSRRuhxTrimThermalSS` (1R), `MSRR1R_10Seg`,
   `R1MSRRuhx10SegTrimThermalSS` (1r10seg), `MSRR5x5_Z10`,
   `R5x5Z10MSRRuhxTrimThermalSS` (5x5) -- bind all 42 radial record
   fields field-by-field from the generated
   `SegmentedMSR_PlantData.IntraChannelRadial1R` /
   `IntraChannelRadial1R_10Seg` / `IntraChannelRadialR5x5_Z10` packages,
   so plant YAML -> generated Modelica -> executed parameters -> run
   manifest is one traceable chain for those topologies (the run
   manifest's fingerprint-active `intra_channel_radial` field is built
   from the loaded+validated plant deck, `radial_manifest_fields` in
   `helpers/segmented_runs.py`). The normal production runners
   (`startup/runMSRR.py`, `freq/runFreqNominalParallel.py`,
   `transients/run_nonlinear_steps.py`; the trip wrappers share the
   transients path) derive that record and the matching compact-column
   run shape through the single P5 helper
   `helpers.segmented_runs.radial_run_contract` -- plant-loaded, per
   selected core, refusing a runner/core combination that cannot execute
   the requested radial mode (an enabled `cores.r9` block, circulating
   without a production annular-flow command path, a heat exchanger on a
   non-circulating loop) before any manifest can be published -- and
   circulating manifests additionally record the effective
   `annularFlowCommand` (P1 constant-only parameter, default 1) in the
   overrides provenance plus the exact `channelFlowFractions` in the
   radial record. Disabled cores return the all-None contract, so
   disabled manifests and compact CSV column sets keep their historical
   shape byte-identically. The 9R production zone cores (`Z1..Z4`
   in `Reactors.MSRR9RThermalAdapter` and
   `Reactors.R9MSRRuhxTrimThermalSS`) are not plant-driven for radial
   fields, permanently: the rev019 Phase 6 remediation took the fallback
   path, so `helpers/plant_config.py` (`_validate_one_radial_block`, the
   permanent P6 rank guard) refuses any enabled
   `cores.r9.intra_channel_radial` block fail-closed, naming
   `cores.r9.intra_channel_radial.enabled` -- the generated four-entry
   `channelFlowFractions` record (sized by `cores.r9.n_zones = 4`) is
   PREPARATORY-ONLY DATA THAT BINDS NOWHERE: the executable 9R topology
   is four independent ONE-channel zone cores, so the record cannot bind
   any of them, and circulating radial 9R is never plant-driven (only the
   1R-family cores accept an enabled radial block). The only executable
   circulating-9R radial configuration is the hard-coded
   `SegmentedMSR.Demos` records: the 9R demo vehicle
   `R9MSRRuhxRadialCirculating` is four independent synthetic loops, one
   single-channel `ClosedAnnularLoop` per zone core with separate
   supply/return plena, per-zone flow command, and per-zone HX
   selection -- no shared annular return, shared plenum, or shared HX
   exists in the package. The zone-indexed plant path (per-zone
   sub-blocks, generated `IntraChannelRadial9R_Z1..Z4` records) is
   recorded in the guard comment for a future owner card; it is an open
   design, not pending work. All committed
   production decks are radial-disabled, so the binding is live and
   inert today; enabling it is a plant-deck edit plus regeneration. When
   enabled,
   the block states the physical-channel mapping (`channel_meaning:
   literal_tube` -- exactly one physical fuel tube per modeled coarse
   thermal channel, `channel_multiplicity` N_c = 1 -- or
   `channel_meaning: aggregate_bundle` -- a bundle of N_c identical
   parallel fuel tubes, N_c >= 2; the pairing is enforced by the plant
   validator and `SegmentedCore` alike), the geometry (strictly ordered
   radii plus the explicit per-segment physical radial length
   `geometry.physical_length`, one positive entry per core axial segment;
   the stack binds these lengths in place of the moderator center spacing
   `dz`), the interface modes, the optional direct-deposition shares
   (whole-fission-power shares: each is a share of the TOTAL fission
   power, distributed over the cells by the normalized fission split
   `fSalt` (sum = 1) -- together with the core's direct fuel and
   moderator shares they sum to 1, enforced at plant load and
   re-asserted in-model on the enabled path, so the total deposited
   power equals the fission power; not N_c-scaled), and the
   circulating-mode
   `annular_loop` section (the loop mass flow is a whole-bundle total with
   the per-tube flow `nominal_mass_flow/N_c` implicitly, so residence
   times stay correct; plenum and connecting-pipe volumes and the
   heat-exchanger UA/loop-side volume are whole-bundle totals, not
   scaled). Multiplicity scales the radial chain through the
   bundle-equivalent length N_c*L_j: per-cell pipe/annular volumes and
   capacities C_equiv = N_c*C_single, radial resistances R_equiv =
    R_single/N_c (stack and annular-loop channel cells alike). Under the
    default `geometry_policy: strict_physical` the plant validator checks
    the record against the core: the stated per-segment lengths equal
    the core's authored fuel lengths (the `channel_geom` LF chain) within
    `volume_tolerance` (default 1e-6; the stack length is never `dz`
    unless proven equal -- 1r10seg's LF = 0.1575 m vs dz = 0.14 m fails
    that proof), the per-cell fuel volume N_c*pi*fuel_radius^2*L_j
    reproduces the declared salt inventory, the annulus outer diameter
    does not exceed the lattice pitch nor any pairwise channel-center
    distance, and the one-tube envelope pi*annulus_outer_radius^2 does not
    exceed the lattice cell area (per tube, not scaled by N_c).
    `strict_physical` is not complete cell closure, though: it closes
    the fuel volume and the one-tube packing against the lattice, but
    the validator cannot close the aggregate moderator inventory or the
    N_c-scaled axial conduction sections against the N_c lattice cells
    (no physical lattice dataset exists), so it is refused outright for
    `channel_meaning: aggregate_bundle` (named at
    `cores.<core>.intra_channel_radial.channel_meaning`) and unless the
    core's `channel_map` declares `lattice_geometry: physical` (an absent
    declaration canonicalizes to `placeholder`, so the shipped cores'
    documented placeholder `pitch` cannot back a strict claim); a real
    geometry dataset with a `lattice_geometry: physical` declaration
    remains a future owner option; until then `effective_thermal` is the
    honest policy for both the bundle and the placeholder lattice. The
    generated `SegmentedCore` re-asserts the same combination rules, the
    literal_tube/N_c pairing, the per-segment length domain, and -- under
    `strict_physical` -- the packing/material-volume/fuel-volume closures
    in-model (the length-provenance equality against the authored LF chain
    is plant-side only: the authored lengths are not core parameters).
    `effective_thermal` permits a reported discrepancy (the closure checks
    are inapplicable and `volume_tolerance` is refused on that policy).
   New packages:
  `Core.Radial` (`Geometry` cylindrical area/volume, shell/annulus
  conduction, and film resistances; `SolidShell` mean-temperature pipe
  cell with the half-resistance partition; `StaticAnnularFluidCell` /
  `FlowingAnnularFluidCell` well-mixed annular cells -- zero-flow
  regular, no balance divides by mass flow; stateless `Interfaces`,
  perfect contact an explicit mode, never an arbitrarily large
  coefficient; `TemperatureTap`; `IntraChannelStack` fuel -> pipe ->
  annulus -> moderator chain exposing
  `G_fuelSide = 1/(R_fuelPipe + Rsh/2)` and
  `G_modSide = 1/(R_fluidMod + Ra/2)`) and the standalone `AnnularLoop`
   (`AxialAnnularChannel` nCell cells wired in flow order with
   `flowDirectionCode` 1 = bottom-to-top / 2 = top-to-bottom folded at
   translation, each cell spanning its own per-tube physical length
   `Lcell[i]` -- `Lcell[nCell]` is bound from the radial record's
   per-segment `physicalLength`, each flowing cell takes
   `L = N_c*Lcell[i]`, and the uniform `Lcell = L/nCell` mean law is
   gone --; parallel distributor; fixed-volume supply/return plena;
  prescribed total-flow driver; `AnnularLoopHeatExchanger`, finite
  conductance onto a prescribed sink, `Q_HX = UA_HX*(T_HX - T_sink)`;
  `ClosedAnnularLoop` with fixed `M_loop` and the algebraic
  `U_loop`/`balanceRHS` diagnostics). `SegmentedCore` enabled path: one
  `IntraChannelStack` per (channel, segment) contact -- fuel-cell
  conductance = `G_fuelSide` with the fuel cell's modT tap reading the
  pipe mean node, moderator contact = `G_modSide` reading the annular
  bulk; `Gsig` retained as an unused diagnostic; circulating mode
   connects the stack annulus ports to the loop's per-cell ports, wires
   the live fission-power deposition splitters, and exposes the bounded
   `annFlowCmd` input. Moderator displacement
   (rev019 Phase 3 remediation, `.collab/tasks/TASK-20260915-01.md`
   P3): `vol_GN` is the GROSS per-channel graphite volume (the
   established moderator inventory before any intra-channel structure is
   removed); with the stack enabled the moderator masses represent the
   NET volume, losing `N_c*pi*(annulusOuterRadius^2 - fuelRadius^2)*L_j`
   per coarse cell -- computed from the same radii, multiplicity, and
   per-segment physical length the stack consumes, equal to the stack's
   own `V_pipe + V_ann` by construction (asserted per cell and
   whole-core; a stack claiming more graphite than a cell's gross share
   is refused at initialization) -- under BOTH geometry policies (the
   committed effective_thermal demos displace their synthetic claimed
   volume, about 1.3e-3 m3 per 1r/1r10seg channel, about 0.07% of the
   gross 1.758 m3 graphite; same displacement on the 5x5 and 9R zone
   rigs). The axial moderator conduction section becomes
   `cellA - pi*annulusOuterRadius^2` (net inner boundary at the annulus
   outer radius) wherever representable (always under `strict_physical`
   via the packing closure); the legacy gross section
   `cellA - pi*chanR^2` (`map.modAnnulusArea`) is kept for overfilling
   `effective_thermal` envelopes where the net section is negative; the
   inter-channel neighbor faces (`map.faceA`) are unchanged in every
   configuration (they carry no channel footprint), and the direct
   fuel-channel radial contact does not exist on the enabled path.
   Enabled-path result variables: `VmodGross`, `VmodNet`,
   `VmodDisplaced`, `VpipeAdded`, `VannAdded`, `CmodRemoved`,
    `CpipeAnnAdded`. QA: 14 new checks under `SegmentedMSR.QA`
   (`RadialGeometryCheck`, `RadialSolidShellCheck`,
   `RadialStaticCellCheck`, `RadialStaticStackCheck`,
   `RadialFlowingCellCheck`, `RadialAxialChannelCheck`,
   `RadialParallelMixCheck`, `RadialClosedLoopAdiabaticCheck`,
   `RadialHXCheck`, `RadialLoopHXSteadyCheck`,
   `RadialWholeSystemEnergyCheck`,
   `RadialModeratorClosureCheck` -- gross/net graphite convention:
   disabled-core gross preservation, per-cell displacement identity,
   per-cell/whole-core volume and heat-capacity closure, and the
   net-vs-legacy conduction-section rule,
   `RadialDepositionClosureCheck` -- whole-power deposition closure on
   seven enabled rigs (1R, 1r10seg with sum(qFissFrac) = 0.93, 5x5, and
   the four 9R independent zone loops), each carrying nonzero
   depFracPipe/depFracAnnularFluid: Q_depFuel_tot + Q_depMod_tot +
   Q_depPipe_tot + Q_depAnn_tot == P_fission_tot at the horizon within
    1e-9 relative, against the per-term fSalt data identities,
    `Radial9RZoneIsolationCheck` -- the 9R multi-zone isolation proof
    (two rigs, REFERENCE a1..a4 vs PERTURBED b1..b4, over the same four
    plant-based zone maps; tCheck = 100 s; b1 carries the dedicated
    per-zone HX with a cold 700 K sink plus half the flow command:
     inter-zone isolation isoTann2..4/isoUloop2..4 within 1e-9 of the
     twin value (relative-with-floor: 1e-6 K/J/kg absolute floor),
     zone-1 perturbation live > 1e-3 K, per-zone HX selection
     QhxPert1 > 0 vs QhxPert2..4 = 0; per-zone Tann/Uloop/Qhx/iso
     result columns)), each
    fail-closed at the run horizon.
   Exploratory demo vehicles under `SegmentedMSR.Demos` (synthetic
   geometry, `exploratory_geometry` maturity, no plant-prediction
   claims): the deck-driven 1r10seg trio
   `R1MSRRuhx10SegRadialStatic` / `R1MSRRuhx10SegRadialCirculating` /
   `R1MSRRuhx10SegRadialCirculatingHX`, plus the 1r static
   `R1MSRRuhxRadialStatic`, the 9R four independent single-channel zone
   loops `R9MSRRuhxRadialCirculating`, and the 25-channel 5x5
   `R5x5Z10MSRRuhxRadialCirculating` (the last three are Modelica-only
   vehicles, not deck-driven); the three decks run through
   `python3.12 -m helpers.run_radial_demo --scenario <id>` / `--list`
   (decks under `data/scenarios/radial_demos/`, scratch
   `00runs/tmp/radial_demos/<id>/`). Before any build the runner
   validates the deck's claimed radial identity (annular mode,
   heat-exchanger selection, geometry policy, flow direction, dataset id,
   fingerprints, and channel flow distribution) field by field against
   the code-owned contract of the named `SegmentedMSR.Demos`
   vehicle and refuses mismatches fail-closed -- a deck cannot claim
   `geometry_policy: strict_physical` for a committed vehicle -- and the
   manifest identity is
   built from that contract rather than the deck's claims. Every committed
   demo vehicle and deck binds `geometry_policy: effective_thermal`
   (`geometryPolicyCode = 2`): the synthetic dimensions cannot satisfy the
   `strict_physical` closure, so no committed demo claims
   `strict_physical`, and the disclosed non-closure (vehicle docstrings
   and deck `doc:` fields) is a factor of about 561 between the single-tube
   geometric fuel volume and the declared per-segment salt inventory on
   the 1r/1r10seg/5x5 rigs (1r10seg: 7.1e-5 m3 vs 0.04 m3 per segment;
    2-segment 1r: 3.6e-4 m3 vs 0.2 m3) with the annulus outer diameter
    2*annulus_outer_radius = 0.040 m exceeding the 0.020 m lattice pitch
    (the cell inradius is pitch/2 = 0.010 m, so the 0.040 m diameter
    cannot fit; envelope area pi*annulus_outer_radius^2 = 1.26e-3 m2 about
    3.1x the 4.0e-4 m2 square cell), and factors of about 11 (zone 1) to
   about 296 (zone 4) against the declared per-zone inventories on the 9R
   vehicle. The deck's
  validated `numerics.tolerance` is baked into the omc build and its
  whitelisted `numerics.method` passed to the executable
  (`-s=<method>`), so the manifest's solver/tolerance fields record what
  actually built and ran. The compact result contract gains
  `RADIAL_COMPACT_COLUMNS`, `LOOP_COMPACT_COLUMNS`, and
  `LOOP_HX_COMPACT_COLUMNS` (`helpers/segmented_runs.py`) and the run
  manifest records the fingerprint-active `intra_channel_radial` field
  (absent on disabled runs). Tests:
   `tests/test_radial_p1_contracts.py` through
   `tests/test_radial_p7_models.py`.
- Optional outer fuel annulus, reactor vessel, and thermostated cavity
  (2026-09-17, task card `.collab/tasks/TASK-20260917-01.md`, plan
  `.collab/optional_outer_core_fuel_annulus_and_cavity_plan.md`; the
  annulus fission increment is 2026-09-18, task card
  `.collab/tasks/TASK-20260918-01.md`, plan
  `.collab/rev021_outer_annulus_review_and_fission_source_plan.md`):
  per-core opt-in via the plant YAML `outer_fuel_annulus` block
  (`data/plants/msrr/shared/core_vessel.yaml`, shared across MSRR plants),
  shipped `enabled: false` (production enablement BLOCKED in the P1
  decision record `core_vessel_decisions.md`: inventory subtraction, flow
  topology, cavity/vessel surface data, and vessel material properties are
  all unsourced), with the `fission:` sub-block shipped `enabled: false`
  and zero fraction vectors (the split of the whole-reactor fission
  source, never an additive source; enabling requires source-backed or
  explicitly synthetic distributions with complete provenance).
  `Core.OuterFuelAnnulusConfig` carries the structural
  options (series location codes 1--3, code 1 = disabled; flow direction
  codes 1--2; geometry policy; authored `G_modAnn` matrix -- film
  interface modes refused; vessel material properties; cavity mode
  1/2/3 = UA only / radiation only / both; cavity temperature and
  per-segment conductance/area/emissivity; external-loop
  cavity-exposure list; the annulus-fission face `fissionEnabled`,
  `fissionCouplingPolicyCode` = 1 local_same_fraction only, the
  `sourceFraction`/`heatDepositionFraction` vectors (strict
  retained-share rule `1-fsum(f) >= 1e-9` on both, hence
  0 <= sum < 1; nonzero entries illegal while off, elementwise
  agreement within 1e-9 under the coupling policy), and the
  `annulusTemperatureFeedbackPolicyCode`/`poisonFluxExposurePolicyCode`
  = 1 zero_credit only with `poisonExposureWeight` all-zero -- weighted
  codes refuse fail-closed; both zero_credit policies are exploratory
  approximations, not quantitative MSRR annulus-fission predictions); `Vessel.OuterFuelAnnulusCell` /
   `Vessel.OuterFuelAnnulusChannel` model the annular primary-fuel cells
   (the shipped deck and the zero-fraction case are decay heat only --
   no fission term; a fission-active deck -- enabled with a nonzero
   heat-deposition distribution (TASK-20260922-03 P2) -- adds explicit
   per-cell fission-power inputs -- a connected split of the total
   fission power, never an additive top-up -- with separate decay and
   fission diagnostics; nonnegative primary pump flow shared with the
   core, reverse flow refused; zero-flow regular);
  `Vessel.AxialVesselShell` the segmented steel vessel (half-resistance
  partition at both faces; deposition structurally zero; no axial
  conduction in this release); `ThermalBoundary.SurfaceToFixedCavity` /
  `FixedTemperatureCavity` the signed fixed-temperature cavity (duty
  never clamped; disabled = adiabatic exterior, structural fold).
  `Vessel.CoreVesselAssembly` wraps the established `Core.SegmentedCore`
  (member name `core` preserved; external interface unchanged; series
  code 1 folds to the bare core with every outer element structurally
   absent); on a fission-active deck it splits the total fission power
  with connected `ScaledPower` splitters (core `F_c^Q = 1 -
  sum(heatDepositionFraction)`, annulus cell `f^Q_a[j]`), asserts the
  whole-power closure from the DELIVERED powers (two-sided 1e-6
  relative + 1 W floor, fail-closed), and publishes the policy-visible
  `VIrradiatedEffective` (zero_credit: `= sum(map.cellVol)`) and
  `annulusFeedbackContribution` (identically 0).
  `Nuclear.PrecursorNetworkOuterAnnulus` attaches the annulus
  precursor chain at the same physical series location and direction
  (discrete-compartment transport; P1 zero-credit importance default)
  and splits the per-group fission-event production the same way
  (`ProdAnn[i, j] = beta_i/LAMBDA * f^S_a[j] * n`, core `ProdCell`
  scaled by `F_c^S`), asserting the per-group production closure
  fail-closed.
  Fuel-inventory decomposition (`split_existing`): `V_active_core =
  sum(map.cellVol)` of the carved executable active-core volume map (the
  annulus volume is not subtracted from a diagnostic; the physical
  states were carved), `V_total_fuel = V_active_core +
  V_outer_annulus + V_external_primary_loop` (retained 0.5 m3 total).
  Production vehicles `Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS`
  / `Reactors.R1MSRRuhx10SegOuterAnnulusTripThermalSS` (1r10seg only;
  every other core key refused explicitly); the demo
  `Demos.R1_10SegOuterFuelAnnulusCavity` is synthetic
  (`datasetId = 'SYNTHETIC-P4-DEMO'`, `maturity = 'synthetic'`,
  nonuniform ten-length mesh, counterflow series code 2 + direction
  code 2, fixed 700 K cavity mode 3, 3600 s protocol), and its P7
  sibling `Demos.R1_10SegOuterFuelAnnulusFissionCavity` is the same
  deck with the annulus fission split enabled
  (`datasetId = 'SYNTHETIC-P7-FISSION-DEMO'`, synthetic nonuniform
  ten-entry fraction profile summing to exactly 0.05 of the
  whole-reactor fission source, both zero_credit policies, zero-credit
  precursor importance, fixed 1 MW schedule with a 2000 s
  runtime-overridable power step via `-override=powerStepPostW=<W>`,
  4000 s protocol). Run contract: the
  26 `OUTER_ANNULUS_COMPACT_COLUMNS` appended to the compact CSV only
  when enabled (disabled runs keep their historical column set
  byte-identically; the 8 P5 additions -- `VActiveCoreM3`,
  `VExternalLoopM3`, `VFuelConfiguredM3`, `QDepositedCoreW`,
  `QDecayExternalLoopW`, `QDHRSRemovedW`, `UStoresTotalJ`,
  `dUStoresDtW` -- complete the inventory decomposition and the
  whole-power balance set), and a fission-active deck (enabled with a nonzero heat-deposition distribution, TASK-20260922-03 P2) appends the
  6 `OUTER_ANNULUS_FISSION_COMPACT_COLUMNS` (`QFissionActiveCoreW`,
  `QFissionOuterAnnulusW`, `QDecayOuterAnnulusW`,
  `fissionPowerClosureResidualW`, `VIrrEffM3`,
   `annulusFeedbackPcm`) on top -- 32 total; an enabled deck with
   all-zero vectors keeps the base 26-column shape, matching the folded
   Modelica outputs. The fingerprint-active `outer_fuel_annulus`
  manifest field (absent when disabled) carries the fission record
  (`fissionEnabled`, coupling policy, both fraction vectors, both
  no-credit policies -- explicit and manifest-visible in every
  enabled state, fission-disabled included), via
  `helpers.segmented_runs.outer_fuel_annulus_run_contract` (fail-closed
  per core: 9R activation refused by name, a dataset whose
  `first_production_target` differs from the selected core refused,
  and an enabled dataset on a core without the wrapper vehicle
  refused). `eResidTotal` is the true transient residual (net outflows
  -- UHX removal, DHRS removal, and the cavity-facing vessel and
  exposed-loop losses -- minus the derivative of the total modeled
  stored energy, scaled by P), not a steady-state-only net-power
  fraction.
  QA: 7 new checks under `SegmentedMSR.QA`
  (`OuterFuelAnnulusCellCheck`, `CavityHeatFlowReversalCheck`,
  `ModeratorOuterBoundaryActionReactionCheck`, `CoreVesselAdiabaticCheck`,
  `CoreVesselLoopCavityOracleCheck`,
  `OuterAnnulusPrecursorTransitCheck`, `OuterAnnulusPoisonSteadyStateCheck`;
  all QA literals synthetic; the package check count moves 40 -> 47),
  plus the fail-closed in-model fission closures (whole-power fission
  closure from the delivered powers, per-group precursor production
  closure, per-elevation inventory closure, delivered
  decay-deposition closure) and the thirteen-category fission QA suite
  (`tests/test_outer_fuel_annulus_fission_p6.py`) with independent
  closed-form and CSV-recomputed oracles.
  Tests: `tests/test_outer_fuel_annulus_p2.py` through
  `tests/test_outer_fuel_annulus_p8.py`, plus
  `tests/test_outer_fuel_annulus_fission_p1.py` through
  `tests/test_outer_fuel_annulus_fission_p7.py`.
- omc 1.27 notes for this install: scripting `simulate()` is broken (use
  `buildModel(tolerance=...)` + run the generated executable, e.g.
  `-outputFormat=csv -r=out.csv -stopTime=N`); builtin `abs()` mis-resolves
  (use `Core.Geometry.nearlyEqual`); enum-to-integer folds crash when feeding
  array dimensions (Core uses integer-coded switches and fixed 6-slot neighbor
  tables). An `assert` inside `when time >= t then ... end when;` is
  evaluated only during event iteration: omc 1.27 logs a violation at
  `LOG_ASSERT | info`, prints "Found event, previous asserts are ignored."
  and exits 0, so such a check can never fail a run. Since review
  2026-10-01 H4 every deferred QA check latches its verdict into a discrete
  Boolean `qaFail_<t>_<k>` inside the when-clause and asserts it outside
  (396 latches in 87 checkpoint blocks); a failed check then ends the run
  at error level ("No event found, but assert was triggered. Throwing
  now!", or a direct error-level record at the final time).
  `helpers.omc_log.fatal_assert_violations` counts that record as fatal even
  on a build that exits 0. The `when terminal()` horizon guards already fail
  at error level and keep their plain assert. Tests:
  `tests/test_segmented_msr.py`, `tests/test_qa_latched_guards.py`.
