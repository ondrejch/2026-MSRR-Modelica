# transients/sensitivity

Reviewer-response sensitivity study (ANUCENE-D-26-00942 items R1.3a/R1.3b,
TASK-20260901-01): effect of the `FF^hAExp` film-convection power law and the
adopted constant-property set on the 1R nominal-power (1.0 MW) reactivity
step (+100 pcm at t = 2000 s, `MSRR.MSRRuhxNominalTrimThermalSS`). Every case
is one `simulate()` run driven purely by `-override=` payloads — no source
patching.

## Invocation

```bash
python3.12 -m transients.sensitivity.run_review_sensitivity           # full grids
python3.12 -m transients.sensitivity.run_review_sensitivity --smoke   # reduced test grid
```

## Grids

- **Grid A (hA exponent)**: `hAExp` ∈ {0.0, 0.25, 0.33, 0.5, 0.8} × flow
  fraction {1.0, 0.66}. Both power-law twins are varied together
  (`core1R.fuelchannel.hAExp` and `heatExchanger.hAExp`). Override with
  `--exponents` / `--flow_fractions`.
- **Grid B (linked properties)**, at the baseline `hAExp = 0.33`
  (skip with `--skip_gridB`; scales via `--property_scales`, default
  {0.9, 1.1} plus the shared nominal):
  - `(rhoFuel, scpFuel)` × {0.9, 1.0, 1.1}: each of ρ_f and c_p,f is
    scaled by ±10 %, so the volumetric heat capacity ρc_p moves by the
    square of the factor (ρc_p ×0.81 / ×1.21; the case ids `x0p9` / `x1p1`
    and the `property_scale` column give the per-property factor);
  - `kFuel` × {0.9, 1.1} — exactly inert because the production 1R model
    sets `kFuel = 0`; the rows are run and reported anyway so the zero
    sensitivity is on the record;
  - an extra `hAnom` linked set (`core1R.hAnom`, which sets the channel's
    calculated `core1R.fuelchannel.hAnom`, +
    `heatExchanger.hApNom`, same factors) as the informative
    film-conductance surrogate for that inert axis; drop with
    `--skip_hanom_set`.

## Normalization caveat (FF = 1 rows)

The hA law anchors at nominal flow (`hA = hAnom*FF^hAExp`), so at FF = 1
every exponent gives `1^hAExp = 1`: the FF = 1 rows of grid A are
exponent-invariant by construction. That exact invariance is itself a
reportable answer; the FF = 0.66 rows (the production `flow_66pct` operating
point) carry the observable sensitivity.

## Metric definitions

- **Decay ratio** (`decay_ratio`): the overshoot above the post-step
  steady power of the first local maximum that follows the first local
  minimum after the peak, divided by the first overshoot
  (`peak − post_ss`). Its time is `time_of_second_peak_s`. A turning point
  counts only once the trace moves back by more than 1e-3 of the first
  overshoot (`DECAY_RATIO_HYSTERESIS`), so flat sample runs and solver
  noise do not create spurious extrema. The ratio is NaN when the response
  is overdamped or the second maximum does not lie above the post-step
  level.
- **Outlet rise** (`delta_tout_C`): every case starts FixedStart from the
  FF = 1 nominal-hA 1 MW setpoint row and steps at 2000 s, which is only
  about 1.2–1.6 graphite time constants in (τ_G ≈ 1229 s at FF = 1). The
  reduced-flow and rescaled cases are therefore still drifting when the
  step fires. Each case is paired with a no-step twin
  (`runs/<case_id>_nostep/`: identical overrides, step time included, with
  `externalReactivityAmplitude[2] = 0`), and

  `delta_tout_C = [Tout_step(tail) − Tout_step(pre)] − [Tout_nostep(tail) − Tout_nostep(pre)]`

  over the pre-step window `[step − 500 s, step − 1 s]` and the tail window
  `[stop − 1000 s, stop]`. The uncorrected step-run difference is
  `delta_tout_raw_C` (the value published as `delta_tout_C` before
  2026-10-02) and the twin's drift is `nostep_delta_tout_C`. The twin
  leaves the step runs unchanged, so all other columns keep their values.

## Outputs

Default `00runs/sensitivity-review-2026-09/transients/`:

- `sensitivity_metrics.csv`: per-case peak power, time of peak, post-step
  steady power, overshoot fraction, decay ratio, pre/post steady fuel outlet
  temperature, the drift-corrected outlet ΔT, and the run's hA, followed by
  the columns added on 2026-10-02 (`delta_tout_raw_C`,
  `nostep_delta_tout_C`, `time_of_second_peak_s`). The original columns
  keep their names and order. `pre_steady_tout_C` and `post_steady_tout_C`
  are the step run's own window means, uncorrected: their difference is
  `delta_tout_raw_C`, not `delta_tout_C`. Use `delta_tout_C` for the outlet
  rise.
- `summary.md` (table) and `summary.png` (power traces + peak-power bars).
- `metadata.json`: git commit, omc version, solver, tolerance, resolved
  grids, per-case override payloads, and per-case failure records
  (`case_id` + `error`) when a case fails. A failing case is recorded
  (metadata `failures` + stderr) without losing the other cases' results:
  the outputs are always written at the end, and the runner exits
  nonzero when any case failed.
- `runs/<case_id>/` and `runs/<case_id>_nostep/`: the `.mos`, omc logs,
  and result CSV per case and per no-step twin.

## Published tables

`data/sensitivity_metrics.csv` and `data/summary.md` are the tables the
paper quotes, produced with the default full grids
(`python3.12 -m transients.sensitivity.run_review_sensitivity`).

The current tables (2026-10-02, review 2026-10-02 item M8) come from
`00runs/sensitivity-psar-2026-10-02b/transients/`. That run used the
default settings (+100 pcm at 2000 s, stop 10000 s, 10000 intervals, dassl,
tolerance 1e-6, `-maxStepSize=0.02`, pre/tail windows 500 / 1000 s,
`--out_dir` only) with the no-step twins. It ran on local OpenModelica
1.27.1 from a clean checkout of `53a5632` (the PSAR-basis model, identical
in `core/` to the `178712a` source of the previous tables) plus the M8
runner fix. Against the previous tables (`00runs/sensitivity-psar-2026-10-02/`,
gateway v1.27.0-cmake), every unchanged column agrees within 2e-12 relative
(not bit-identical: the omc builds differ).
The peak powers, peak times, steady powers, overshoots, outlet
temperatures, and hA quoted in the paper are therefore unchanged. Only
`decay_ratio` and `delta_tout_C` changed:

- **`decay_ratio`**: the old values (5e-7 to 5e-6) used the global minimum
  of the post-peak record as the trough. They are replaced by the
  first local maximum after the first trough (about 300 s after the step).
- **`delta_tout_C`**: the old values were the drift-contaminated raw
  difference, now kept as `delta_tout_raw_C`. The apparent −4.7 % hAExp
  trend at FF = 0.66 (8.732 → 8.323 K) was a baseline artifact.

See `python3.12 -m transients.sensitivity.run_review_sensitivity --help`
for the remaining knobs (`--step_pcm`, `--step_time`, `--stop_time`,
solver settings, averaging windows).
