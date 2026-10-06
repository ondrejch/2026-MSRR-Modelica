# freq/sensitivity

Reviewer-response frequency sensitivity (ANUCENE-D-26-00942 item R1.3a,
TASK-20260901-01): effect of the `FF^hAExp` film-convection exponent on the
1R nominal-power (1.0 MW) frequency response at 0.005, 0.02, and 0.1 rad/s —
three points bracketing the nominal-power roll-off. Each case is one
`simulate()` run of the production frequency vehicle
`MSRR.MSRRuhxNominalTrimNoTrips` with a 1 pcm sine, driven purely by
`-override=` payloads; amplitude and phase come from the shared production
sine fitter (`freq/_common.py`).

## Invocation

```bash
python3.12 -m freq.sensitivity.run_review_sensitivity           # full grid
python3.12 -m freq.sensitivity.run_review_sensitivity --smoke   # reduced test grid
```

## Grid and key flags

`hAExp` ∈ {0.0, 0.25, 0.33, 0.5, 0.8} × frequency {0.005, 0.02, 0.1} rad/s
× flow fraction {1.0, 0.66}; both power-law twins
(`core1R.fuelchannel.hAExp`, `heatExchanger.hAExp`) are varied together.
Metrics are gain (dB) and phase (deg) per case plus the shift vs the
`hAExp = 0.33` baseline at the same frequency and flow.

- `--exponents`, `--frequencies`, `--flow_fractions`: override the grids.
- `--sin_mag`: perturbation amplitude in pcm (default 1.0).
- `--ss_time`: perturbation start / settle time in s (default 2000).
- `--settle_discard`: settling discard after the perturbation start in s
  (default 0, the historical fit from the perturbation start). The fit
  window opens at `ss_time + settle_discard` and the stop time is extended
  so it still spans `--min_cycles_after_ss` periods. The corrected
  frequency-response protocol uses 6000 s at 1 MW (`freq/README.md`).
  The metrics also carry the two-halves agreement of the fit window
  (`conv_gain_rel_diff`, `conv_phase_diff_deg`).
- `--smoke`: exponents {0.33, 0.8} at 0.1 rad/s, FF 0.66, short settle.

## Normalization caveat (FF = 1 rows)

`hA = hAnom*FF^hAExp` anchors at nominal flow, so at FF = 1 the exponent
cancels exactly (`1^hAExp = 1`): FF = 1 gain/phase shifts are zero to solver
noise by construction — a reportable invariance, not a measured sensitivity.
The FF = 0.66 rows carry the observable exponent dependence.

## Outputs

Default `00runs/sensitivity-review-2026-09/freq/`:

- `sensitivity_metrics.csv`: gain, gain_dB, phase_deg, R², and the
  delta-gain/delta-phase columns vs the 0.33 baseline.
- `summary.md`: the same as a markdown table.
- `metadata.json`: git commit, omc version, solver, tolerance, grids,
  per-case stop times.
- `runs/<case_id>/`: the `.mos`, omc logs, and result CSV per case.

## Published tables

`data/sensitivity_metrics.csv` and `data/summary.md` are the tables the
paper quotes. They were produced with the corrected frequency-response
settings:

```bash
python3.12 -m freq.sensitivity.run_review_sensitivity \
    --settle_discard 6000 --min_cycles_after_ss 12 --tolerance 1e-8
```

See `python3.12 -m freq.sensitivity.run_review_sensitivity --help` for the
remaining knobs (`--min_cycles_after_ss`, `--base_stop_time`, output
cadence, solver tolerance).
