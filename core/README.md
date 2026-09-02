# Core

Shared Modelica code for the repository.

## Files

- `SMD_MSR_Modelica.mo`: base component library (units, ports, heat transport, kinetics, signals).
- `MSRR.mo`: integrated system-level MSRR models for both 1-region and 9-region cores.
- `init/`: power-indexed setpoint tables and generator script used for model initialization.

## Primary entry models

- 1-region: `MSRR.R1MSRRuhx`, `MSRR.MSRRuhxNominalTrim`, `MSRR.MSRRstartUpTo100kW`
- 9-region: `MSRR.R9MSRRuhx`, `MSRR.MSRRuhxNominalTrim9R`, `MSRR.MSRRstartUpTo100kW9R`

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
`FlowDistributor` in the nine-region assembly); `FlowDistributor` itself
exists only in `SMD_MSR_Modelica`.

### FF shaping

- **Ramp-up** (`HeatTransport.Pump`): additive sigmoid stages toward
  `rampUpTo[i]`, activating at `rampUpTime[i]` with time constant `rampUpK[i]`;
  `epsilon = 1e-4` sets the sigmoid edge steepness.
- **Pump-trip coast-down** (`HeatTransport.Pump`): for `t >= tripTime`,
  `FF(t) = sum(rampUp)(t)*exp(-(t - tripTime)/tripK) + freeConvFF`; `tripK` is
  a decay time constant, and all reactor assemblies bind `tripK = 50 s`.
- **Per-region coast-down** (`HeatTransport.FlowDistributor`): each output
  passes the pump FF through unchanged until its own `regionTripTime[i]`, then
  coasts down as
  `FF_i(t) = FF_in*(1 - freeConvectionFF)*exp(-coastDownK*(t - t_trip_i)) + freeConvectionFF`:
  the pumped component survives only under the decaying envelope, so the output
  relaxes to `freeConvectionFF` at the fixed rate `coastDownK` rather than
  following the pump. The nine-region assembly binds
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
  2023 dissertation — the channel Re is ~776, so Dittus–Boelter does not
  apply), exactly `hA(1) = hAnom` for any
  exponent. The exponent is the component parameter `hAExp` (default `0.33`,
  asserted to lie in `[0, 1]`) on both components; defaults reproduce the
  historical `FF^0.33` behavior exactly. The reviewer sensitivity studies in
     `transients/sensitivity/` and `freq/sensitivity/` vary it via `-override=`.

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
`MSRR.Components.FuelChannel`, `MSRR.Components.HeatExchanger`)
aborts initialization on `FF < 0` with a "flow fraction must be >= 0; reverse
flow unsupported" message. Zero remains legal — the free-convection pump rig
`TestPump.testPump` binds `freeConvFF = 0` — and the plant pumps never reach
it anyway (`freeConvFF = 0.01` in every assembly).

## Circulating-fuel kinetics conventions

`SMD_MSR_Modelica.Nuclear.mPKE` applies a live bias correction: `rho_0dyn` is
re-evaluated at every instant from the steady-state precursor distribution at
the current core/loop transit times, so the applied compensation tracks
instantaneous pump flow.
