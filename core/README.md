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
