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
  - `(rhoFuel, scpFuel)` × {0.9, 1.0, 1.1};
  - `kFuel` × {0.9, 1.1} — exactly inert because the production 1R model
    sets `kFuel = 0`; the rows are run and reported anyway so the zero
    sensitivity is on the record;
  - an extra `hAnom` linked set (`core1R.fuelchannel.hAnom` +
    `heatExchanger.hApNom`, same factors) as the informative
    film-conductance surrogate for that inert axis; drop with
    `--skip_hanom_set`.

## Normalization caveat (FF = 1 rows)

The hA law anchors at nominal flow (`hA = hAnom*FF^hAExp`), so at FF = 1
every exponent gives `1^hAExp = 1`: the FF = 1 rows of grid A are
exponent-invariant by construction. That exact invariance is itself a
reportable answer; the FF = 0.66 rows (the production `flow_66pct` operating
point) carry the observable sensitivity.

## Outputs

Default `00runs/sensitivity-review-2026-09/transients/`:

- `sensitivity_metrics.csv`: per-case peak power, time of peak, post-step
  steady power, overshoot fraction, decay ratio, pre/post steady fuel outlet
  ΔT, and the run's hA.
- `summary.md` (table) and `summary.png` (power traces + peak-power bars).
- `metadata.json`: git commit, omc version, solver, tolerance, resolved
  grids, per-case override payloads.
- `runs/<case_id>/`: the `.mos`, omc logs, and result CSV per case.

See `python3.12 -m transients.sensitivity.run_review_sensitivity --help`
for the remaining knobs (`--step_pcm`, `--step_time`, `--stop_time`,
solver settings, averaging windows).
