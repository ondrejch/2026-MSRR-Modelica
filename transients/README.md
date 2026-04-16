# transients

Nonlinear step-dynamics runs and plots for the journal article Results II.
The workflow now runs and overlays both core models (`1R` and `9R`).

## Key files

- `run_nonlinear_steps.py`: OpenModelica runner for step-reactivity, flow-fraction, and UHX-trip cases (`1R` and `9R`).
- `plot_nonlinear_steps.py`: plotting utility for the three Results II figures with `1R`/`9R` overlays.
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
  - `1R`: `MSRR.Transients.R1fullSteps.*`
  - `9R`: `MSRR.MSRRuhxNominalTrim9RThermalSS` with explicit
    `externalReactivityAmplitude[2]`/`externalReactivityStepTime[2]` overrides.
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
- `plot_nonlinear_steps.py` writes:
  - `<run_dir>/MSRRstep_nominal.png`
  - `<run_dir>/MSRRstep_flow.png`
  - `<run_dir>/MSRR_uhx_trip.png`
- In `MSRRstep_nominal.png` and `MSRRstep_flow.png`, fuel and graphite panels are
  shown as `T - T_prestep_mean` (baseline window `-50 s <= t < 0 s`) so cross-core
  transient shape can be compared without static offset bias.
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
