# Frequency response

Frequency-response analysis workflow and related utilities for nominal-trim MSRR models.

## Main scripts

- `runFreqNominal.py`: serial frequency sweep driver. The shim strips any
  user-supplied `--n_jobs` value — including every argparse prefix
  abbreviation of `--n_jobs` (`--n_j 4`, `--n_j=4`, even bare `--n`) and
  the bare value that follows — so it always runs serial; only prefixes of
  `--n_jobs` are stripped, so options that merely start with `--n` (such as
  `--num_freq`) pass through unchanged.
- `runFreqNominalParallel.py`: parallel frequency sweep driver (thread pool);
  supports the `--package {legacy,segmented}` switch (see
  [SegmentedMSR package mode](#segmentedmsr-package-mode)). `--scenario`
  default `nominal_sweep` is the developer 100-point grid; `paper_nominal`
  is the 80-point campaign protocol. Explicit `--freq_min` / `--num_freq`
  flags win over YAML.
- `runFreqNominalParallelAllPowers.py`: table-driven multi-power wrapper around parallel sweep + collection workflow.
- `collectFreqNominal.py`: serial post-processing (gain/phase fitting + Bode exports);
  a deprecated shim that delegates to `collectFreqNominalParallel.py` with `--n_jobs 1`.
  Like the sweep shim, it strips any user-supplied `--n_jobs` value first, so it
  always runs serial.
- `collectFreqNominalParallel.py`: parallel post-processing. Both collectors verify each
  case's provenance sidecars before fitting and refuse to publish an incomplete aggregate
  by default (see
  [Collection provenance and completeness](#collection-provenance-and-completeness)).
- `plotFreqRun.py`: plot a single frequency run CSV.
- `plotFreqTimeCompareCoreModels.py`: create an annotated time-domain comparison for one `1r`/`9r` frequency point.
- `plotBodeCompareCoreModels.py`: overlay Bode plots for `1r` vs `9r` at selected powers.
- `fr_protocol.py`: gain prior, target-swing amplitude rule, and the
  per-frequency settling discard used by the sweep runner (see
  [Measurement protocol](#measurement-protocol-settling-discard-amplitude-convergence)).
- `refine_sweep.py`: refinement loop -- reruns non-converged points with
  longer discards or halved amplitudes, re-collects, and runs the
  half-amplitude approval checks, until every point converges and passes
  or a reported limit is reached (see
  [Refinement loop](#refinement-loop-freqrefine_sweep)).
- `refinement.py`: refinement-round plans and the authority rules the
  runner, collector, and verifier apply to `<dir>/refine/round_NN/`.
- `sweep_manifest.py`: the immutable sweep request and the canonical
  parent identity shared by refinement rounds, approval checks, the
  collector, and the verifier (see
  [Parent identity](#parent-identity-rounds-approval-checks-every-case)).
- `build_gain_prior.py`: regenerates the committed gain prior
  `data/scenarios/freq/fr_gain_prior.json` from a finished campaign.
- `linearity_check.py`: half-amplitude linearity self-check for selected
  points (`suggest` / `compare`).
- `sensitivity/`: reviewer-response hA-exponent frequency sensitivity at 1.0 MW, 1R (see `sensitivity/README.md`).

## Sine fit method (collectors and fit plots)

`collectFreqNominalParallel.py`, `collectFreqNominal.py`, and
`plotFreqFits.py` share one fitter (`freq/_common.fit_sine_least_squares`)
that estimates `y(t) = c0 + c1*(t - t_c) + a*sin(ω t) + b*cos(ω t)` by linear
least squares, with the offset `c0` always estimated together with the sine
coefficients (the previous mean-subtract + `(2/N)` projections were biased for
windows that do not span an integer number of periods). The optional linear
trend `c1` is off by default; enable it with `--fit_trend`
(`collectFreqNominalParallel.py` also exposes `--fit_min_samples` (10),
`--fit_min_cycles` (0.25), and `--fit_cond_max` (1e8) to adjust the floors
that reject short or ill-conditioned windows explicitly). Here `t_c` is the
midpoint of the samples used and only centers the trend term — amplitude
`A = hypot(a, b)` and phase follow the historical convention
(`arctan2(b, a)`, reported relative to the perturbation start), which is
unchanged. Timestamp grids with `max(|Δt / median(Δt) − 1|) > 1e-6` are
fitted with trapezoidal interval-length weights (half-interval endpoints,
half of each neighbor in the interior) instead of uniform weighting; a
grid that is only clustered toward small steps is treated as nonuniform
too. Duplicate or nonpositive intervals are rejected. The collector never
extends a fit window before the perturbation start: too few finite samples
after forcing begins is a rejected point, not a reason to mix unforced
data. The window opens at the settling fit start recorded by the runner
(see [Measurement protocol](#measurement-protocol-settling-discard-amplitude-convergence)).
Integer-cycle,
uniformly sampled windows reproduce the old estimator — to rounding error when
the grid does not repeat the boundary phase sample, and to better than
`1e-3` relative amplitude / `0.1` degrees otherwise (the residual decays like
`1/samples-per-period` because the closed window duplicates one phase point,
which biased only the old projection). Every fit records
sample count, cycle count, window bounds, residual RMS, R², condition number,
and the fitted coefficients in `FreqResponseResults.csv`. The recorded
residual RMS and R² refer to the objective the fit actually minimized:
interval-weighted residuals on a nonuniform grid, raw residuals on a uniform
grid. The aggregate `FreqResponseResults.csv` carries both views: the primary
pair (`residual_rms`), the raw-sample pair (`residual_rms_unweighted`,
`r_squared_unweighted`), the interval-weighting flag (`weighted_intervals`,
true when the fitted grid was judged nonuniform), and the grid uniformity
metric (`uniformity_metric`, `max(|Δt / median(Δt) − 1|)` over the fitted
samples). The two pairs coincide on uniform grids, and both stay NaN for
points the fitter rejected before producing coefficients.

**Gain normalization.** `gain` is `|δn/δρ|`: the fitted amplitude of the
neutron population `n`, normalized to 1 at the 1 MW nominal power, per unit
reactivity amplitude in dk/k (`δρ = sin_mag × 1e-5`). `gain_dB` is
`20 log10(gain)`, and the Bode axes are labeled with this normalization. At
an operating power `P` the population is `n_op = P / 1 MW`, so `gain`
shrinks with power even when the relative response does not. The aggregate
therefore also carries `gain_fractional = gain / n_op`, the fractional gain
`(δn/n_op)/δρ` about the operating point. Use it when comparing powers.
Points in the drift regime reference their gain to the window-mean power
(`gain_reference`), and `gain_fractional` applies the same `n_op`
division. Historical Bode
results under `00runs/freq/` were produced with the old projector, so they
are pre-fix evidence rather than a new baseline.

## Measurement protocol: settling discard, amplitude, convergence

The legacy (lumped 1R/9R) sweep applies three protocol rules before any
simulation starts (target-swing amplitude, per-frequency settling discard,
power-scaled solver tolerance), the collector checks every point for
settling and for its realized swing, the refinement loop reruns every point
that fails either check (longer discard, or rescaled amplitude), and
half-amplitude checks at the band edges and the measured resonance are part
of publication approval. The rules are defaults of the campaign invocation
(`--sin_mag_auto --stop_time_mode min_cycles_after_ss`); the historical
behavior stays selectable with `--settle_rule none` and
`--sin_mag_auto_rule inverse_power`. Segmented sweeps (`--package
segmented`) always resolve to `none` / `inverse_power`, because the gain
prior below was measured on the lumped vehicles; the run log and the sweep
request record that resolution as a note. The rule code lives in
`freq/fr_protocol.py`.

### Why

Two defects biased the `review-2026-09` Bode data:

1. **Switch-on transient in the fit window.** The fit window used to open at
   the perturbation start. Switching the sine on excites the lightly damped
   power/temperature resonance (`omega_n` of about 0.0116 rad/s at 1 MW,
   0.0036 rad/s at 0.1 MW, and 0.0011 rad/s at 0.01 MW, roughly
   `omega_n ~ 0.0116 sqrt(P / 1 MW)`), so near-resonance points averaged a
   still-building envelope. The published peak gains are low by 1.5–2.7 %
   at 1 MW, 4–6 % at 0.2 MW, 9.3 %/6.5 % (1R/9R) at 0.1 MW, and 21–28 % at
   0.01 MW (phase error 4–15°).
2. **Perturbation amplitude.** `sin_mag_ref / P` clamped to [1, 20] pcm
   gives a relative power swing `dn/n0 = |G| drho / P` of 1.3 % at the
   1 MW resonance, 12.9 % at 0.1 MW, and 107 % at 0.01 MW with 1 pcm
   (10 pcm above 0.01 rad/s). At that size the fitted gain is a describing
   function rather than a transfer function (`|H2|/|H1|` = 36 % at
   0.01 MW), and the 1-to-10 pcm switch near 0.0103 rad/s steps the
   0.01 MW gain by −2.1 to −2.8 %.

### Target-swing amplitude (`--sin_mag_auto_rule target_swing`, default)

With `--sin_mag_auto`, every frequency point gets the amplitude that makes
the predicted relative swing equal the target `s` (`--target_swing`,
default 0.01):

```text
drho(omega, P)  = s * P / |G_prior(omega, P)|              (dk/k)
sin_mag_pcm     = clamp(1e5 * drho, [0.01, 10])  pcm
```

`|G_prior|` is in normalized power (1 = 1 MW) per unit reactivity, the unit
of the aggregate `gain` column. The clamp bounds are
`--target_swing_min_pcm` (0.01) and `--target_swing_max_pcm` (10); a clamped
point keeps a swing below target (for example 0.87–0.89 % at 1.2 MW,
1e-3 rad/s, where 11.3 pcm would be needed). The low-power 1 pcm caps of the
inverse-power rule do not apply under this rule; the swing target bounds the
nonlinearity directly. `--sin_mag_scale` multiplies every resolved amplitude
(the linearity check below uses 0.5).

The prior is the committed table `data/scenarios/freq/fr_gain_prior.json`
(`--fr_prior` selects another file; its path and SHA-256 are recorded in the
sweep request `policies.fr_protocol.prior` and in every case manifest). It
holds the `review-2026-09` published gains of both cores (11 powers × 80
frequencies, 1e-3 to 10 rad/s) and per-power resonance rows, generated
read-only from the collected aggregates (source commit `f1eeafb2`,
OpenModelica v1.27.0-cmake; per-aggregate SHA-256 digests are recorded in
`provenance.aggregates`):

```bash
python3.12 -m freq.build_gain_prior \
  --source_root 00runs/paper-rerun-review-2026-09 \
  --output data/scenarios/freq/fr_gain_prior.json
python3.12 -m freq.build_gain_prior --source_root 00runs/paper-rerun-review-2026-09 --check
```

Interpolation: `log |G|` is linear in `log omega` within a table power and
linear in `log P` between the two bracketing powers. Two interpolants are
formed, one along constant `omega` and one along constant `omega / sqrt(P)`
(the measured resonance scaling, so the peak moves between table powers
instead of splitting into two humps), and the larger gain is used; it never
predicts a gain below either interpolant and so errs toward a smaller swing.
Table nodes are reproduced exactly. Frequencies outside the table use the
edge gain; powers outside it scale the edge curve as `|G| ~ P`. Both cases
are flagged `prior_extrapolated` in the case manifest.

The prior gains carry the bias of defects 1 and 2 (near-resonance gains are
low). A gain error `e` changes the realized swing by a factor `1/(1 − e)`,
for example 1.3 % instead of 1 % at the 0.01 MW resonance; the prior is
never reported as a result.

Numerics at tiny amplitudes. The amplitudes range from 0.012 pcm (0.01 MW
resonance) to 10 pcm. The absolute swing is `0.01 P` in normalized units:
1e-4 at 0.01 MW, 1e-5 at 1e-3 MW, 1e-6 at 1e-4 MW, and 1e-7 at 1e-5 MW.
OpenModelica's DASSL sets each state's absolute tolerance to
`tolerance × |nominal|`; the population state `n_population.n` keeps the
default nominal 1, so its absolute tolerance equals the sweep tolerance. At
the historical tolerance 1e-6 the signal at P ≤ 1e-4 MW sat at or below it,
and the local error control alone did not resolve it; accuracy then rested
on the step limits imposed by the forcing-event cadence (`forcingTimeStep`
1 s / 0.05 s) and by the other states. The historical protocol operated
there: at 1e-5 MW and 8.9 rad/s its 1 pcm points had an absolute swing of
about 1.6e-8 and still matched the 1 MW gain per unit power (158.2 vs
158.1). The power-scaled tolerance of the next section removes that
reliance (v2: absolute error 1e-4 of the target swing at every power); the
half-amplitude check below confirms linearity and resolution per sweep.

### Solver tolerance (`--tolerance_rule power_scaled_v2`, default)

OpenModelica's DASSL uses `atol = tolerance × nominal` per state. The
neutron-population state keeps nominal 1 (normalized power, 1 = 1 MW), so its
absolute error scale is the tolerance itself, independent of the operating
power and of the swing. The default rule `power_scaled_v2` (record
`tolerance_power_scaled_v2`) keeps that error scale at 1e-4 of the target
swing `0.01 P`:

```text
tolerance = max(1e-11, min(--solver_tolerance [1e-8], 1e-4 * target_swing * P))
          = min(1e-8, 1e-6 * P)   at the 1 % target, P in MW (= normalized)
```

That is 1e-8 for P ≥ 0.01 MW, 1e-9 at 1e-3 MW, 1e-10 at 1e-4 MW, and 1e-11
(the floor) at 1e-5 MW. The applied tolerance is the case manifests'
`tolerance` field, the request's `numerics.tolerance`, and
`policies.fr_protocol.solver_tolerance` (rule, base, floor, ratio of the
absolute error scale to the swing, and the per-case rule and floor of halved
and check cases, see "Amplitude halving"); `run_params.txt` repeats it.
`--tolerance_rule power_scaled` selects the v1 rule `min(1e-6, 0.01 *
target_swing * P)` (1e-6 for P ≥ 0.01 MW, 1e-7 / 1e-8 / 1e-9 at 1e-3 /
1e-4 / 1e-5 MW; record `tolerance_power_scaled_v1`, per-case rule
`tolerance_swing_scaled_v2`), `--tolerance_rule fixed` the fixed
`--solver_tolerance` (default 1e-6) for diagnostics.

Why v2. The v1 rule left the error scale at 1e-4 to 1e-3 of the target swing
for P ≥ 0.01 MW (1e-6 absolute). Two measurements showed that this is not
converged:

1. The H2 diagnostic of 2026-09-28 (commit `3e9ad6a`,
   `fr-diag-h2-2026-09-28`) reran three failed check pairs of the
   2026-09-28b campaign at a fixed 1e-8. The `|H2|/|H1|` half/reference
   ratio moved from 1.188 to 0.602 (1R 0.4 MW, 0.001 rad/s), from 0.903 to
   0.512 (1R 0.8 MW, 0.001 rad/s), and from 0.201 to 0.486 (9R 0.1 MW,
   resonance, half/quarter amplitude). The two-halves differences fell from
   4e-4 to 1e-3 to 8e-6 to 3e-5 (1R); the 9R quarter-amplitude run went
   from unconverged (dG = +1.2 %) to dG = −3.1e-4, and its gain from 604.5 to
   627.9. The 1R 0.001 rad/s gains rose by 0.6 % (105.44 → 106.10 at
   0.4 MW, 104.59 → 105.14 at 0.8 MW).
2. Short single-point runs (switch-on at 2000 s; the fitted amplitude over
   the last half of a 6000 s window at 0.004 / 0.01 rad/s, 600 s at 10 rad/s;
   local OpenModelica 1.27.1; `00runs/tmp/frfix7` of the 2026-09-28 task)
   give the amplitude change against the tightest tolerance run:

   | Core, power, ω | 1e-6 | 1e-8 | 1e-9 | 1e-10 | 1e-11 |
   |---|---|---|---|---|---|
   | 1R 0.1 MW, 0.004 rad/s | −3.7 % | −0.17 % | −0.033 % | ref. | |
   | 9R 0.1 MW, 0.004 rad/s | −3.4 % | −0.19 % | −0.029 % | ref. | |
   | 1R 1 MW, 0.004 rad/s | −0.88 % | −0.03 % | 0.001 % | ref. | |
   | 9R 1 MW, 0.004 rad/s | −0.81 % | −0.05 % | 0.000 % | ref. | |
   | 1R/9R 0.1-1 MW, 10 rad/s | −1.6e-4 … −5e-5 | ≤ 9e-6 | ref. | | |
   | 1R/9R 1e-4-1e-5 MW, 0.01 rad/s | | ≤ 4e-6 | ≤ 4e-6 | ≤ 4e-6 | ≤ 4e-6 (ref. 1e-12) |
   | 1R/9R 1e-4-1e-5 MW, 10 rad/s | | −1.4e-3 | −6.3e-4 … −1.4e-3 | −1.3e-4 … −6.3e-4 | ≤ 3.0e-4 (ref. 1e-12) |

   The v1 tolerance biased the gain near the resonance low by 3.4-3.7 % at
   0.1 MW and 0.8-0.9 % at 1 MW; at 1e-8 the bias is 0.17-0.19 % at 0.1 MW
   and at most 0.05 % at 1 MW. The low-frequency low-power points were
   already tolerance-converged; the 10 rad/s low-power points (0.05 s forcing
   events) converge to 3e-4 at the v2 values.
3. The check pairs and convergence failures of the failed 2026-09-28b sweeps,
   rerun as single-point sweeps at the v2 tolerance (full and half amplitude,
   rule discard, local OpenModelica 1.27.1; the 1R 0.8 MW, 0.001 rad/s pair
   reproduces the cluster diagnostic to all printed digits): all 15 pairs
   pass the gain, phase, and H2 tests -- the band edge (0.001 rad/s) and
   measured resonance of 1R 0.1/0.2/0.4/0.8 MW and 9R 0.1/0.2 MW, 1R 0.2 MW
   at twice and 9R 0.2 MW at four times the discard, and the 1R 1e-3 MW band
   edge -- with gain changes of −0.35 % to +0.001 % and H2 ratios of
   0.40-0.60; the six points that needed one to three rounds and a halving to
   converge in that campaign converge at the rule discard (E ≤ 0.05). The
   9R 0.2 MW full-amplitude resonance run, which got worse with a longer
   discard at 1e-6 (dG −0.80 % at 24000 s), converges at 24000 s (E = 0.03).
   Exception: the 1R 1e-3 MW band edge keeps its H2 ratio of 0.132 at the
   100× tighter tolerance (its half run's second harmonic, 9.7e-7 of the
   mean power, sits at 0.97× the separate half run's error scale and passes
   as `not_applicable_below_resolution`; an in-loop check at half the
   tolerance would apply the test); that ratio is not solver noise and is
   open.

Cost. Simulation time relative to the v1 tolerance of the same power, same
runs: 0.55x-1.28x at 1e-5 and 1e-4 MW (v2 = 1e-11 / 1e-10 against 1e-9 /
1e-8), 0.85x-1.30x at 0.1 and 1 MW (1e-8 against 1e-6). The spread of
repeated runs on the shared host is of the same size, and the cost model
of the campaign (14-17 % median error, below) is kept. The long pre-perturbation
period of the low-power sweeps was not part of the timed runs.

Floor. All 76 runs (both cores, 1e-5 and 1e-4 MW, 0.01 and 10 rad/s,
1e-8 to 1e-12; 0.1 and 1 MW, 1e-6 to 1e-10) completed without solver
failures or warnings; 1e-12 costs 0.6x-1.9x the 1e-8 time. The floor
1e-11 is the rule's own value at 1e-5 MW and keeps one decade of that
verified margin; it binds only below 1e-5 MW.

Judgment call: the 1e-8 base is the owner's working value. At 0.1 MW near the
resonance it leaves a 0.17-0.19 % gain bias (1e-9: 0.03 %, at 0.9x-1.55x
the 1e-8 simulation time in the table's runs); `--solver_tolerance 1e-9`
lowers the base.

### Settling discard (`--settle_rule prior`, default; rule `settle_prior_v2`)

The fit window of every point opens a discard `T_d` after the perturbation
start. `T_d` is chosen per frequency, from two prior-derived discards, and
is never capped:

```text
T_full  = ceil(k / min(zeta * omega_n, sigma_floor))    full regime
T_floor = ceil(k / sigma_floor)                         drift regime
```

- `k` = `--settle_e_folds` (default 3; values below 3 are refused, so
  `T_full >= 3 / (zeta * omega_n)` always holds).
- `zeta * omega_n` comes from the prior's resonance rows: `omega_n` is the
  gain peak (log-parabolic interpolation), and
  `zeta = (omega_hi − omega_lo) / (2 omega_n)` from the −3 dB (half-power)
  points, which is exact for a second-order band-pass. The width is in
  band for 0.01–1.2 MW. Below 0.01 MW, `omega_n` follows `sqrt(P)` from the
  0.01 MW row and `zeta * omega_n` follows the power law through the
  0.01 and 0.1 MW rows (exponent 0.91 for 1R, 0.92 for 9R). The half-power
  decay agrees with the time-domain envelope decay where both are
  measurable: 7.5e-5 vs 7.7e-5 1/s at 0.01 MW and 6.1e-4 vs 5.3e-4 1/s at
  0.1 MW (1R).
- `sigma_floor` = 5e-4 1/s (`settle_model.sigma_floor_s_inv` in the prior,
  tau = 2000 s). At P ≥ 0.4 MW the half-power width predicts
  `zeta * omega_n` = 2.5e-3 to 1.4e-2 1/s, yet near-resonance 2-cycle
  sliding gains keep drifting by 0.5–1.3 % for about 5000–6000 s after
  forcing onset (a slow mode the peak width does not see); the prior's
  `settle_model.evidence` block records the sliding-window settle times per
  power. Refitting the `review-2026-09` P ≥ 0.1 MW cases with the fit
  window opened 6000 s after forcing, 1097 of the 1120 points (2 cores ×
  7 powers × 80) pass the convergence check below even though their
  windows are shorter than the new protocol's (the old stop times); 12 of
  the 23 failures are 1R 0.1 MW points near the resonance, driven at the
  legacy swing of up to 12.9 %.

Three regimes:

1. **Full regime** (default): `T_d = T_full`, no trend term, gain referenced
   to the nominal power. The stop time keeps the required cycles AFTER the
   discard:

   ```text
   stop(omega) = max(stop_time, ceil(ss_time + T_d + N * 2 pi / omega))   (min_cycles_after_ss)
   stop(omega) = stop_time                                                (fixed)
   ```

   with `N = --min_cycles_after_ss`. In fixed mode the window is
   `[ss_time + T_d, stop_time]`; a discard that leaves no window is refused
   before anything is published (exit 2).
2. **Drift regime**: when `T_full > T_floor` (the resonance decays slower
   than the slow-mode floor: P ≤ 0.01 MW with the committed prior) AND the
   N-cycle window spans at most a fraction `phi` of the natural period
   `T_n = 2 pi / omega_n`,

   ```text
   2 pi N / omega <= phi * T_n   <=>   omega >= kappa * omega_n,   kappa = N / phi
   ```

   (`phi` = `--settle_drift_window_fraction`, default 0.1, so `kappa` = 120
   for the campaign's `N` = 12; min_cycles_after_ss mode only), only
   `T_d = T_floor` is discarded, the window is `phi * T_n` long (at least
   `N` cycles; `stop = ceil(ss_time + T_floor + phi * T_n)`), the collector
   fits a linear trend term, and the gain is referenced to the window-mean
   power (below).
3. **Lock-in regime**: at the same powers (`T_full > T_floor`), a point
   outside the drift window with `omega >= 10 omega_n` keeps the full
   regime's discard and stop time but is measured relative to the
   one-period local mean power, which rejects the slow free mode (see
   "Lock-in regime" below).

At P ≥ 0.1 MW `sigma_res >= sigma_floor`, so `T_full = T_floor` = 6000 s and
the drift regime cannot shorten anything: the rule reproduces the previous
uniform discard, stop times, output grids, and estimator exactly.

#### Drift regime: why the free mode is a removable drift

1. **Free-mode amplitude.** Near the resonance the closed loop behaves like
   `G(s) ≈ K s / (s² + 2 sigma s + omega_n²)` (the kinetics integrate the
   reactivity, the temperature feedback closes a second integrator, hence
   `omega_n ∝ sqrt(P)`). A sine switched on from rest leaves, besides the
   forced response, a free mode `F exp(−sigma t) cos(omega_n t + psi)`; for
   `omega >> omega_n` the residues at `s = j omega` and at the poles have
   equal magnitude to O(zeta), so `F` ≈ the forced amplitude (smaller on the
   prompt-jump plateau). The `review-2026-09` 1R 1e-4 MW cases show it
   directly: the per-period mean power oscillates at `omega_n` with 0.93×
   (0.0103 rad/s) and 0.58× (0.106–10 rad/s) the forced amplitude, and it
   does not decay over their 5e4 s windows (`sigma t` ≈ 0.06). Under the
   1 % target swing the mean power therefore wanders by up to ~1 % at
   P ≤ 1e-4 MW, and waiting it out needs `T_full` (2.2e7 s at 1e-5 MW), which
   at 10 rad/s would be 4e8 forcing events.
2. **Bilinear kinetics.** For `omega >= kappa * omega_n` the feedback loop
   gain is `|L| ≈ (omega_n / omega)² <= kappa⁻²` = 7e-5, so the response is
   the zero-power kinetics `dn = n_mean(t) Z(j omega) drho`: the forced
   amplitude follows the instantaneous mean power. In the same cases the
   relative forced amplitude tracks the mean-power excursion with a slope
   of 0.998–1.06 (1 = bilinear, 0 = additive; 0.0103–10 rad/s).
   Referencing the gain to the window-mean power,
   `G = (A / drho) * P / y_mean` (`y_mean` = the fitted trend's window mean),
   removes the excursion to first order; the nominal reference would carry
   it as a gain bias of up to ±1 % and fail the 0.5 % operating-point
   criterion.
3. **Trend removal.** Over `phi * T_n` the free mode advances by
   `x = 2 pi phi` = 0.63 rad. A polynomial trend of order `p` leaves a
   remainder of order `F (x/2)^(p+1) / (p+1)!`, which projects onto the
   forcing sine with a further factor ~`1 / (pi M)` (`M >= N` cycles).
   Through the production fitter, worst case over the free-mode and forcing
   phases, `F` = forced swing = 1 %, window 0.1 `T_n` at the regime boundary
   (`omega = 120 omega_n`, 12 cycles):

   | Trend | Gain error | Phase error | Halves `|dG|`, additive worst case |
   |---|---|---|---|
   | none | 1.6 % | 0.88° | 0.60 % |
   | linear | 0.039 % | 0.024° | 0.32 % |
   | quadratic | 0.011 % | 0.006° | 0.31 % |

   At `omega = 1000 omega_n` the linear-trend errors fall to 0.003 % and
   0.002°. A linear trend is therefore sufficient (bias below a tenth of
   the 0.5 % / 0.5° tolerances); the quadratic term (`trend_order=2` in
   `freq._common.fit_sine_least_squares`) is available but not needed. The
   halves column is the purely additive model (forced response not
   following the mean power), where the mean-normalized halves differ by
   `F x / 2` = 0.31 %; under the measured bilinear kinetics it vanishes.
   With `phi` = 0.2 the additive worst case would reach 0.6 % and fail, so
   `phi` = 0.1 keeps even that bound inside the tolerance.
   `test_drift_regime_bias_bound_linear_vs_quadratic_trend` pins these
   numbers.
4. **Operating point.** For drift-regime points `|y_mean / P − 1|` is the
   mean-power excursion, and it has two parts: the switch-on free mode
   (≤ ~1 %), and the second-order rectification of the forcing itself. The
   kinetics term `rho * n` averages over a forcing period to a DC
   reactivity `rho_eff = drho * s * cos(phi) / 2` (`s` the relative swing,
   `phi` the phase of the power response), which at low power drives the
   slow closed-loop mode like a reactivity step, with a peak excursion of
   about `K rho_eff / (omega_n P)` (`K ≈ omega |G|` on the low-frequency
   side of the resonance). It is largest where `drho` is largest (the
   prompt-jump plateau) and grows as `1 / sqrt(P)`: measured on the
   corrected 1R model at the end of the drift window, 1.2 % (0.1 rad/s) and
   3.4 % (10 rad/s) at 1e-4 MW, and 2.2 %, 5.1 %, and 5.9 % (0.1, 1, 10
   rad/s) at 1e-5 MW, against 0.18–0.19 % at 0.01 MW. Neither part biases
   the window-mean referenced gain: at `omega >= 120 omega_n` the relative
   transfer function depends on the power only through the loop gain
   (≤ 7e-5), so an excursion `eps` changes the gain by at most `7e-5 eps`.
   (The same 1e-5 MW points reproduce the 1e-4 MW gain per unit power to
   0.01 % at 10 rad/s.) The collector therefore bounds the excursion at
   20 % (`--convergence_drift_operating_point_tol`, a linearization guard
   with a factor ~1.7 over the estimated 1e-5 MW peak) instead of 0.5 %.

The prior's `omega_n` below 0.01 MW is an extrapolation, and the physics
review changed the model, so the true boundary may shift. If `omega_n` were
twice the prior value the effective window fraction would be 0.2: the gain
error stays below 0.07 %, and the halves check still arbitrates. Any point
that fails it goes to the refinement loop.

#### Drift-regime validation (required before the corrected campaign is accepted)

The drift regime estimates a local response about a slowly drifting state,
not a fully settled transfer function about the nominal state. The owner
approved it for points far above the resonance; the rev032 review requires
its numerical qualification first. `--settle_force_full` (runner) disables
the drift regime (and the lock-in regime) for one sweep: every point gets
`T_full`, no trend, the sine-fit estimator, and the nominal-power gain
reference, recorded as
`policies.fr_protocol.settle.drift_regime_disabled = true` (part of the
parent identity; never a campaign setting).
`helpers/paper-rerun/submit_drift_validation.py` builds the gateway job
matrix and `helpers/paper-rerun/compare_drift_validation.py` evaluates it:

- cores 1R and 9R; powers 1e-5, 1e-4, and 1e-3 MW;
- points: the regime boundary (lowest drift-regime grid point, about
  127 `omega_n`), two interior points (the grid points nearest 10× and 100×
  the boundary, at most 1 rad/s so the full reference keeps the 1 s forcing
  cadence), and 10 rad/s (window doubling only; `--top-full` adds its full
  reference where the row budget allows -- 1-25 CPU h per cell);
- variants per point: the drift regime (`phi` = 0.1), the drift regime
  with the doubled window (`--settle_drift_window_fraction 0.2`), and the
  forced full discard, followed by one refinement round so the reference
  itself converges.

The validation is fail-closed (rev033 review). Before dispatch, `run`
writes an immutable **matrix manifest**
(`00runs/tmp/drift_validation_<id>.matrix.json`, installed unchanged at
`<root>/drift_validation_matrix.json` by the first job; sealed with a
fingerprint, never replaced by a different matrix): every expected cell
(core, power, point, variant, frequency, directory, rule regime and
discard, feasibility), every point with its planned comparisons, the
pinned commit, and the setpoint tables' SHA-256 and model version. The
comparator judges the tree against that manifest only:

1. **coverage**: every feasible cell exactly once, infeasible cells absent,
   no unknown directory, no two directories holding the same (core, power,
   frequency, variant); a point without any cell is reported missing;
2. **cell provenance**: each request names its matrix cell (core, power,
   frequency key, `drift_regime_disabled` / `drift_window_fraction`, the
   rule's regime), the matrix commit on a clean tree, and the matrix
   setpoint SHA-256 and model version; each aggregate verifies through its
   collection manifest (`verify_aggregate_outputs`: every advertised output
   hash) and belongs to the cell's request (its generation ID and CSV
   SHA-256 are recorded); `freq.verify_campaign` is called for every cell
   and must report `publication_eligible`;
3. **identity**: the variants of a point share the canonical request
   identity (`freq.sweep_manifest.request_identity`) apart from the
   drift/full and window-fraction settle fields; all cells share the tool
   identity (sources, workflow Python, Git, interpreter, OpenModelica,
   workflow version, solver, output grid), all cells of a core the model
   and setpoint identity;
4. **comparison**: drift against full with both gains per unit window-mean
   power (gating; at low power `G/P` does not depend on the operating
   point) and as published (informational; the full run's nominal gain
   carries its own operating-point shift), and the doubled window against
   the default window, each within 0.5 % in gain and 0.5° in phase with
   every variant converged.

The report (`drift_validation_report_strict.{json,md}`) passes only when all
of this holds for every point; a missing, invalid, or unconverged cell makes
its point `fail` or `inconclusive`, and either fails the report (exit 1;
exit 2 for a missing or tampered matrix). Each cell job records its
refinement and verification exit codes (`logs/<cell>_status.json`) and exits
with the verifier's status, and the dispatcher returns the REMOTE exit
status of the comparison job, so a dispatch can no longer succeed while the
validation fails. A run dispatched before the matrix manifest existed is
covered by `reconstruct`: it rebuilds the manifest from the same `plan`
arguments and the local state file (`00runs/tmp/drift_validation_<id>.json`)
and refuses unless the plan reproduces every dispatched cell exactly; its
cells are then judged by `compare-remote` (on the gateway, with the
comparator from a checkout at `--compare-commit`; `--compare-workdir` names
a separate checkout, so the pinned one the cells run from never moves while
cells are running) or `sync` + `compare` (locally).
Request fingerprints cannot be known before the remote runners publish
them; the report records the observed ones. Cost estimate (cost model
below): 11.3 CPU h (1R) and 37.3 CPU h (9R) for the 66 base cells, the
longest a 9R 1e-5 MW full reference at 10.4 h; if every full reference
needs its refinement round (twice the discard) the worst case adds 21.6
(1R) and 70.8 (9R) CPU h, and the critical path is about 31 h (9R,
1e-5 MW: 10.4 h plus a 20.8 h round).

The 20 % operating-point bound can only be tightened on this evidence. The
largest shift measured so far is 5.9 % (1R, 1e-5 MW, 10 rad/s, `phi` = 0.1,
end of window); the 9R core and the doubled window are unmeasured, so a
bound of about 10 % would already be supportable for 1R at `phi` = 0.1 but
not in general. The comparison report therefore proposes
`ceil(1.5 × max shift)` over all drift variants, and only when every point
passes; until then the bound stays at 20 %. If equivalence is not
demonstrated, the affected points must either be published as local
drifting-state responses or be run with the full discard
(`--settle_force_full`), at the cost in the table below.

#### Lock-in regime (rule `lockin_local_mean_v1`)

**Why.** The lumped plants carry a slow power-temperature oscillation (the
resonance above, `omega_n ∝ sqrt(P)`), and at low power its period reaches
tens of hours. The plants have no temperature-dependent heat sink: the
ultimate heat exchanger removes a fixed demanded power (`powRm = powDemand.R` in `core/SMD_MSR_Modelica.mo`,
with `uhxDemand` set to `powerLevel * nominalPower` in `core/MSRR.mo`), so
the salt temperature integrates the power imbalance and feeds back through
the temperature coefficient. The result is a feedback oscillator whose
damping comes only from internal lags (delayed neutrons, loop transport,
decay heat). The sine switch-on excites it, and at the lowest power it
barely decays or grows:

1. At 1e-5 MW the records of the PSAR-basis campaign
   (`paper-rerun-psar-2026-10-01`) at 1e-3 rad/s show the mode at
   `omega` ≈ 3.53e-5 rad/s in 1R and 3.62e-5 rad/s in 9R (period ≈ 49.4 h
   and 48.2 h). In 1R it grows with an e-folding time of ≈ 24 Ms, from
   0.95 % to 2.36 % of the mean power over the 21.7 Ms analyzed record; in
   9R it decays with an e-folding time of ≈ 126 Ms, from 1.00 % to 0.89 %
   over 15.7 Ms. The mode is measured on the one-forcing-period centered
   moving average of the population, which removes the forced component,
   fitted with an exponentially growing or decaying sinusoid (fit
   R² ≥ 0.9987). On the earlier parameter set (corrected campaign) the
   its 1e-3 rad/s records showed the mode at ≈ 3.7e-5 rad/s (period
   ≈ 47 h) growing in both cores: in 1R by a factor 1.335 per 5.42 Ms
   (e-folding time ≈ 19 Ms), from 0.55 % to 4.1 % of the mean power over
   the 40 Ms forced record, and in 9R with an e-folding time of ≈ 57 Ms.
   The forced component at 1e-3 rad/s stays constant throughout. The
   negative damping at 10 W (1R) is measured (these growth rates); which
   lag produces it has not been identified.
2. In the corrected campaign at 1e-4 MW (1R) the same mode
   (`omega` ≈ 1.17e-4 rad/s) decays, with an e-folding time of ≈ 3 Ms,
   but it is still 0.4–0.8 % of the mean power in the fit window,
   comparable to the 0.5–1 % forced swing. It is the cause of the R² values
   of 0.93 and 0.94 at 0.00455 and 0.00512 rad/s.
3. A longer discard therefore makes the 1e-5 MW points worse. In the
   `corrected-2026-09-28b` campaign one doubling of the 1R discard raised the
   operating-point offsets of the 13 lowest points from 0.5–2.9 % to
   2.4–9.7 % and their two-halves gain differences to up to 44 %; halving
   the amplitude did not help, and the refinement ended at the row budget.

**Rule.** At powers where the drift regime is available (`T_full >
T_floor`), a point outside the drift window with `omega >= 10 omega_n`
(`fr_protocol.LOCKIN_MIN_OMEGA_RATIO`) is a lock-in point:
`settle_regime = lockin`, `fit_estimator = lockin_local_mean_v1`,
`gain_reference = local_mean_power`, `fit_trend_order = 1`. Its discard,
stop time, output grid, forcing cadence, amplitude, solver tolerance, and
refinement escalation are exactly those of the full regime; only the
estimator differs, and the runner writes the same simulation call for it
(`test_lockin_case_simulates_exactly_what_the_full_regime_requests`).
`--settle_force_full` switches the lock-in regime off together with the
drift regime.

**Estimator** (`freq/lockin.py`):

1. Local mean `m(t)`: the centered average of the raw population over
   exactly one forcing period `T = 2 pi / omega`, from the exact integral of
   the piecewise-linear interpolant (nonuniform grids). A stationary forced
   component averages to zero; a slow mode at `omega_s` passes with gain
   `1 − (pi omega_s / omega)² / 6` (0.998 at `omega = 27 omega_s`).
2. Relative fluctuation `r = n / m − 1`, fitted with the collector's sine
   fitter (sine, cosine, constant, linear trend) over an even number of
   whole forcing periods between the fit start and the record end minus
   `T/2` (the average never reaches before the forcing start); the two
   halves have equal numbers of periods. The linear term removes the slow
   ramp that the average leaves, which would otherwise bias the forced
   amplitude by up to `2 (pi² / 6) (omega_s / omega)³` times the slow
   amplitude.
3. Leakage correction: the one-period average of the forced product `m f`
   is not zero when `m` varies. To first order it equals `m' f' / omega²`
   (0.15 % of the forced amplitude at `omega = 27 omega_s` with the slow
   mode at 8× the forced swing). Three fixed-point iterations
   `m ← (MA(n) − m' f' / omega²) / (1 + MA_d(f))` remove it, together with
   the discrete average `MA_d(f)` of the fitted forced component on coarse
   grids. On a stationary signal every correction is zero.
4. Gain `A_r * P / drho` (`A_r` the amplitude of `r`, `P` the manifest
   power): the local-mean reference, since the low-power kinetics are
   bilinear. The phase, R², H2/H1, relative swing (`A_r`), and the two-halves
   metrics (`A_2 / A_1 − 1`, phase difference) are those of the fit of `r`;
   `c0` and the operating-point offset use the window mean of `m`.

**Operating point.** The slow mode moves the local mean by several
percent (−3.0 % to +5.0 % of `P` inside the 1R 1e-5 MW window below, window
mean +1.8 %), so lock-in points use the relaxed bound
`--convergence_drift_operating_point_tol` (0.2). The argument of the drift
regime extends to `omega >= 10 omega_n`: the feedback loop gain is
`|L| ≈ (omega_n / omega)² <= 1e-2`, so an excursion `eps` of the mean power
changes the relative gain by at most `1e-2 eps` (≤ 0.2 % at `eps` = 0.2).

**Accuracy** (synthetic records through the production code, forced swing
0.5 %, 12-cycle window, `tests/test_freq_lockin.py`; slow-mode amplitude
given as a multiple of the forced swing at the window end):

| Case | Gain error | Phase error | Halves `|dG|` / `|dphi|` |
|---|---|---|---|
| Settled signal | < 1e-11 (equals the sine fit within 1e-6) | < 1e-4° | < 1e-9 |
| Slow mode at `omega/27`, 8×, growing or decaying | ≤ 0.008 % | ≤ 0.006° | ≤ 0.008 % / 0.002° |
| Same, 47 samples per period with jitter | ≤ 0.009 % | ≤ 0.003° | ≤ 0.005 % / 0.002° |
| Same record, raw-signal sine fit | up to 19 % | up to 23° | 3.8–43 % / 15–33° |
| Slow mode at `omega/10`, 1×, decaying | ≤ 0.05 % | ≤ 0.03° | ≤ 0.07 % / 0.04° |
| Slow mode at `omega/10`, 2× | ≤ 0.1 % | ≤ 0.07° | ≤ 0.13 % / 0.08° |
| Slow mode at `omega/10`, 8× | ≤ 0.5 % | ≤ 0.35° | up to 0.55 % / 0.31° |

The 1e-5 MW lock-in points lie at 28–113 `omega_n` with the slow mode up
to 8× the forced swing; the 1e-4 MW points at 11–115 `omega_n` with the
slow mode at about 1×. A point that still fails the halves check goes to
the refinement loop like a full-regime point.

**Evidence on campaign records.** A prototype of the estimator (without
the linear term and the leakage correction) applied to the 1R 1e-5 MW
record at 1e-3 rad/s (`corrected-2026-09-28b` refinement round 2, amplitude
0.03437 pcm) gives 0.1418 per pcm on the protocol's own 11-cycle window,
with the halves agreeing to 0.017 % and 0.09° and R² = 0.9996; the whole
30-cycle tail gives 0.1418 as well. The same fit normalized by the nominal
power instead of the local mean has halves differing by 5 %, and the
raw-signal sine fit gives 0.168 and −62.86° against the lock-in −87.80°
(+18 % and 25°). The production estimator on the same record gives
0.1418 per pcm and −87.95°, R² = 0.99999, halves agreeing to 0.023 % and
0.0001° over 10 periods; without the linear term the phase halves differ
by 0.04°. At 1R 1e-4 MW and 0.00455 rad/s six consecutive
segments of the forced record agree to four digits (phase −81.03°); there
the raw-signal fit is within 0.16 % and 0.94° of it.

**Points per core** at the committed prior (80-point grid, 1e-3–10 rad/s):

| P (MW) | 1R lock-in points (rad/s) | 9R lock-in points (rad/s) | Full points left |
|---|---|---|---|
| 1e-5 | 13 (0.00100–0.00405) | 13 (0.00100–0.00405) | 0 |
| 1e-4 | 21 (0.00126–0.0130) | 21 (0.00126–0.0130) | 2 |
| 1e-3 | 22 (0.00361–0.0417) | 21 (0.00405–0.0417) | 11 (1R), 12 (9R) |
| 0.01 | 22 (0.0116–0.134) | 22 (0.0116–0.134) | 21 |
| ≥ 0.1 | 0 | 0 | 80 |

**Record.** Each request case carries `fit_estimator` (`sine_ls_v1` or
`lockin_local_mean_v1`), bound to the case manifest's
`fr_protocol.settle.fit_estimator`; `policies.fr_protocol.settle` adds
`lockin_enabled`, `lockin_min_omega_ratio`, `lockin_estimator`, and
`lockin_trend_order` (parent identity). The aggregate CSV adds the
`fit_estimator` column; the collection manifest adds
`fit_settings.fit_estimator_counts` and
`convergence.tolerances.operating_point_offset_local_mean_reference`;
`run_params.txt` adds `settle_lockin_points`. Sweeps written before the
rule carry no `fit_estimator`: their points keep the sine fit, and a
refinement round of such a sweep cannot switch estimators (the case
identity includes the regime and the gain reference). Their 1e-5 MW
low-frequency points, including those of `review-2026-09`, carry the
slow-mode contamination described above.

#### Record and implied settings

The per-point decision is the collector's fit-start and estimator
authority: each sweep request case carries `fit_start_s`,
`settle_discard_s`, `settle_rule_discard_s`, `settle_regime` (`full` /
`drift` / `lockin` / `none`), `settle_refine_round`, `fit_trend_order`,
`gain_reference` (`nominal_power` / `window_mean_power` /
`local_mean_power`), `fit_estimator` (`sine_ls_v1` /
`lockin_local_mean_v1`), `fit_window_s`,
`natural_period_fraction` (`omega_n * window / 2 pi`), `fit_cycles`,
`forcing_time_step_s`, `predicted_result_rows`, `output_intervals_capped`,
`amplitude_clamped` (`null` / `min` / `max`), `effective_target_swing`, and
`amplitude_halvings` (see "Amplitude halving"); `policies.fr_protocol`
carries the sweep-level
constants (`T_full`, `T_floor`, `sigma_res`, `sigma_floor`, `omega_n`,
`zeta`, `phi`, the drift window, the lock-in settings, the refinement
factor, the row budget, and the regime counts); each legacy case manifest carries the same record under
`fr_protocol` (fingerprint-active, so results are never reused across
protocol changes). The request also records the parent identity fields
`setpoint_model_version`, `core_maturity`, `core_physical_data_maturity`,
and `case_common_overrides` (see "Parent identity"). Legacy 1R/9R case
manifests carry both maturity labels too (`reference_regression`,
`legacy_inherited`), read from the core deck through
`helpers.plant_config.core_maturity_labels`; the collection manifest
(`sweep.core_maturity`, `sweep.core_physical_data_maturity`) and the
verification summary repeat them. `run_params.txt` repeats the constants
for human readers.

Implied settings of the campaign invocation (80 points, 1e-3–10 rad/s; CPU
from the cost model below, wall time for 16 slots per job; the "full"
points include the lock-in points, which run the same simulations):

| Core | P (MW) | T_full (s) | Drift for omega ≥ (rad/s) | Full / drift points | Drift window (s) | Max stop (s) | Max rows per case | CPU h | Wall h |
|---|---|---|---|---|---|---|---|---|---|
| 1R | 1e-5 | 2.163e7 | 0.0043 | 13 / 67 | 1.752e4 | 6.17e7 | 4.46e7 | 43.0 | 3.16 |
| 1R | 1e-4 | 2.661e6 | 0.0136 | 23 / 57 | 5541 | 6.74e6 | 5.61e6 | 10.1 | 0.78 |
| 1R | 1e-3 | 3.274e5 | 0.043 | 33 / 47 | 1752 | 8.03e5 | 2.31e6 | 2.35 | 0.16 |
| 1R | 0.01 | 4.029e4 | 0.136 | 43 / 37 | 554 | 1.56e5 | 7.07e5 | 0.85 | 0.06 |
| 1R | 0.1–1.2 | 6000 | -- | 80 / 0 | -- | 8.34e4 | 8.34e5 | 0.14 | 0.01 |
| 9R | 1e-5 | 1.568e7 | 0.0043 | 13 / 67 | 1.741e4 | 5.58e7 | 3.26e7 | 143 | 10.4 |
| 9R | 1e-4 | 1.896e6 | 0.0137 | 23 / 57 | 5504 | 5.97e6 | 4.06e6 | 33.2 | 2.53 |
| 9R | 1e-3 | 2.293e5 | 0.0433 | 33 / 47 | 1741 | 7.05e5 | 2.31e6 | 7.95 | 0.55 |
| 9R | 0.01 | 2.773e4 | 0.137 | 43 / 37 | 550 | 1.43e5 | 7.07e5 | 3.17 | 0.20 |
| 9R | 0.1–1.2 | 6000 | -- | 80 / 0 | -- | 8.34e4 | 8.34e5 | 0.20 | 0.01 |

Amplitudes are unchanged by the settle rule (see the target-swing section);
at 1e-3 rad/s, `omega_n(P)`, and 1 rad/s they are 0.069, 0.069, 5.25 pcm
(1R, 1e-5 MW), 0.061, 0.061, 5.26 (1e-3 MW), 0.024, 0.012, 5.24 (0.01 MW),
0.88, 0.086, 5.23 (0.1 MW), and 9.41, 0.77, 5.22 pcm (1 MW).

#### Feasibility: delay history, output grid, row budget

- **Delay ring buffer.** The `review-2026-09` 1e-5 MW crash (2^26-entry
  delay ring-buffer overflow, `00runs/paper-rerun-review-2026-09/metadata/
  excluded_powers.json`) came from `delay(time, tripTime = 1e12, ...)`,
  whose history is never trimmed. Since the physics review 2026-09-27 the
  trip times are closed-form (`core/SMD_MSR_Modelica.mo`, `PrimaryPump`);
  the remaining `delay()` histories (pipe transport, precursor loop) are
  trimmed to their delay time. Measured with OpenModelica 1.27.1: the 1R
  `MSRRuhxNominalTrimNoTrips` vehicle with 4e6 communication points over
  2e6 s peaks at 24.5 MB RSS against 23.6 MB with 4e5 points (an untrimmed
  history would add ~1 GB), and a probe model with 50 delays stays at 22 MB
  with 5e6 output points or 5e6 time events. The largest case of the table
  has 2.3e7 communication points (output grid plus forcing events), 34 % of
  2^26, so no case could overflow even with an untrimmed history.
- **Output grid.** Cases whose fit window is sampled by forcing events (the
  low-power cadence, 1 s for `omega <= 1` rad/s and 0.05 s above) get at
  most `--max_output_intervals` (default 2e6) output intervals: the grid
  then only resolves the pre-forcing settle. Without the cap the 1e-5 MW
  drift points would ask for up to 3.8e8 intervals (a 4e7 s settle at a
  period/6 step), the grid that crashed the `review-2026-09` protocol runs.
- **Row budget.** OpenModelica writes the pre- and post-event values of
  every forcing event plus the output grid, so a case has
  `intervals + 1 + 2 * events` rows. The runner refuses (exit 2, before any
  simulation) a sweep with a case above `--max_case_rows` (default 1e8,
  about 4.4 GB of reduced CSV); the refinement loop reports such a case
  `infeasible` instead of launching it. The largest campaign case (1R,
  1e-5 MW, 4.1e-3 rad/s) has 4.5e7 rows (~2 GB).
- **Cost model.** Least-squares fit of the 1760 `review-2026-09` case logs
  (median error 14–17 %): simulation time ≈ 0.95 s + 5.2e-4 s per forcing
  event + 1.3e-5 s per output interval (1R), 1.25 s + 2.4e-3 s per event +
  2.7e-5 s per interval (9R), plus about 3 s to translate and compile. At
  low power the forcing events dominate; the longest points are the 13
  full-regime 1e-5 MW points (3.2 h 1R, 10.4 h 9R each). Campaign totals:
  57 CPU h (1R) and 189 CPU h (9R) for the base rounds, against 47 / 140 CPU h
  for the capped rule it replaces and 13 / 54 CPU h logged by
  `review-2026-09` (no discard). One refinement round of every full-regime
  point would add at most 105 (1R) and 342 (9R) CPU h; the 9R 1e-5 MW job is
  the critical path (10.4 h per round). The model was fitted at the v1
  tolerances; the short v2-tolerance runs of "Solver tolerance" (1e-10 at
  1e-4 MW: 6000 1 s events at 0.01 rad/s, 12 000 0.05 s events at 10 rad/s)
  took 3.5 / 7.2 s (1R) and 15.7 / 23.9 s (9R) against 4.2 / 7.9 s and
  15.9 / 31.5 s predicted, so the table stands for `power_scaled_v2`.

### Refinement loop (`freq.refine_sweep`)

A point that fails the convergence check below is rerun with a longer
discard until it converges or an explicit limit is reached:

```bash
python3.12 -m freq.refine_sweep --results_dir <dir> [--max_rounds 8] \
  [--max_wall_s 0] [--max_case_rows 1e8] "--collect_args=--plot --n_jobs 16" \
  [--no_amplitude_halving] [--halving_min_improvement 2] [--skip_approval_checks] \
  -- <the runner arguments of the base sweep>
```

Each round `r`:

1. reads the current aggregate; the points with `converged == False` or
   `swing_check == fail` are the candidates (a swing-only failure keeps its
   discard and gets the amplitude rescaled by the measured gain, see
   "Realized swing check");
2. sets each unconverged candidate's next discard to `max(2 T_d, T_full)`
   (`fr_protocol.refine_discard`): a drift-regime point first jumps to the
   full resonance discard, a full-regime point doubles its e-folds
   (3 → 6 → 12 → 24); the point keeps its regime, estimator, and fit-window
   length (the window moves later by the discard increase) -- unless the
   amplitude-halving rule below halves its amplitude instead;
3. reports a candidate whose rerun would exceed `--max_case_rows` as
   `infeasible` and does not rerun it; a drift-regime point whose full
   discard is beyond the budget (the high-frequency points at P ≤ 1e-4 MW:
   `T_full` of 0.05 s forcing events) doubles within the drift regime
   instead (`escalation = double_drift_fallback` in the plan; the other
   values are `full` and `double`);
4. runs the runner with the base sweep's arguments plus `--base_dir
   <dir>/refine/round_NN --refine_plan <dir>/refine/round_NN/refine_plan.json`;
   the round directory is a complete sweep with its own immutable request,
   whose `refinement` block names the parent request fingerprint, the round,
   and per point the new discard, the discard it replaces, the round it
   replaces, and the failed criteria. The runner refuses (exit 2) a plan
   whose parent request is not the one in `<dir>`, a `--base_dir` other than
   the canonical round directory, a discard shorter than the rule's, and
   any argument change that alters the parent's model, power, perturbation
   start, or rule amplitudes (the plan's per-point amplitude -- kept,
   rescaled with the measured swing recorded, halved, or restored -- and the
   per-case solver tolerance are applied only after that check);
5. re-collects `<dir>`;
6. once every point converged and passes its swing check, runs the
   half-amplitude approval checks the base request declares (see "Approval
   checks"; only points without a current check are simulated), and steers
   every point whose check fails with the linearity branch of "Amplitude
   halving" (settle, halve, restore, or report) in the next round.
   `--skip_approval_checks` leaves the checks to `freq.linearity_check
   campaign`.

The loop exits 0 when every point converged and, with in-loop checks, the
approval checks pass; 3 when a limit (`round_limit`, `wall_limit`,
`infeasible`, `approval_checks_failed`) left points unconverged
(`non_converged`) or unapproved (`unapproved`); 1 on a runner or collector
failure; 2 on invalid input.

#### Amplitude halving (rule `amplitude_halving_v2`)

Near the 9R resonance at 0.1 and 0.2 MW, discard doubling alone does not
converge the corrected campaign's sweeps. A diagnostic at commit `38c8c95`
(`00runs/fr-diag-2026-09-28/remote/*/FreqResponseResults.csv`) showed:

1. 9R, 0.2 MW, measured resonance 0.005747694 rad/s: at the 1 % target
   swing the gain was 663.39 with a two-halves difference dG = +0.18 % at
   the rule discard and 666.95 with dG = −0.80 % at 4× the discard (not
   converged); at half the amplitude and 4× the discard it converged
   (dG = −1.5e-6, gain 657.06). The half-amplitude check had reported a
   −0.73 % gain change, with `|H2|/|H1|` scaling by 0.51.
2. 9R, 0.1 MW, 0.0036054711542 rad/s (unconverged after three
   discard-doubling rounds): dG = −0.69 % at 4× and −1.13 % at 8× the
   discard, at full amplitude.
3. The operating-point offset stayed at +0.0003 in every run.

A settling transient decays with the discard: each doubling of a discard of
k ≥ 3 e-folds removes a further factor of at least e^3 ≈ 20. A two-halves
disagreement that a doubling does not reduce, or increases, is therefore not
a settling transient; at these points it depends on the amplitude, and a
smaller amplitude removes it.

**Convergence branch** (`fr_protocol.refinement_action`, target-swing
sweeps only; unchanged from v1). It uses the convergence excess
`E = max(|dG| / 0.5 %, |dphi| / 0.5°)` of the collector's two-halves
metrics (`fr_protocol.convergence_excess`; E < 1 passes):

1. a base case, a rescaled case, or a restored case (below) escalates its
   discard first;
2. after a discard escalation the point escalates again when E fell by at
   least `--halving_min_improvement` (default 2) relative to the case it
   replaced; otherwise the next round halves its amplitude and effective
   target swing at the same discard (`halving_trigger = discard_stall`);
3. after a halving it halves again when E fell by at least that factor
   (`halving_progress`), and escalates the discard when it did not;
4. only a two-halves failure (`halves_gain` / `halves_phase`) can trigger a
   halving; an operating-point or not-evaluable failure, a concurrent swing
   failure (the rescale comes first), or a round recorded without its
   trigger metrics escalates the discard.

**Linearity branch** (`fr_protocol.linearity_action`, v2). The v1 rule halved
every point whose half-amplitude check failed the gain or phase test. The
2026-09-28b campaign (commit `3e9ad6a`) showed three different causes behind
such failures, which need different levers (numbers from its refine reports
and aggregates):

1. **An unsettled run of the pair.** 1R 0.1 MW, resonance 0.00405 rad/s:
   the check failed by −0.52 % gain while the half run had E = 0.28 (dG
   = +0.14 %); after one halving the discrepancy grew to −0.60 %, and the
   quarter-amplitude case failed its own convergence test (E = 3.7). 9R
   0.2 MW: both runs of the first pair were unsettled (E = 0.36 and 0.64),
   and the discrepancy changed sign after the halving (−0.73 % → +0.79 %).
2. **An amplitude-driven discrepancy on a settled pair.** 1R 0.4 MW,
   resonance 0.00815 rad/s: both runs settled (E = 0.003 and 0.001),
   −0.89 % at the base amplitude, +0.26 % after one halving (a factor 3.4;
   a gain term in A² predicts 4); 1R 0.2 MW: −1.19 % → −0.27 % (4.4×).
3. **An H2-only failure** (gain and phase within 0.5 % / 0.5°): see "H2-only
   failures" below.

The branch therefore judges a failed check only on a *settled* pair and
decides from how the discrepancy responds to each lever. Every check now
records both runs' two-halves metrics (`fits.<run>.convergence`), the pair
excess (the larger E), `settled` (pair excess ≤ 0.25, i.e. halves agreeing
within 0.125 % / 0.125°, `fr_protocol.LINEARITY_SETTLE_EXCESS`), and the
linearity excess `L = max(|ΔG| / 0.5 %, |Δφ| / 0.5°)`. Per failed point, from
its earlier linearity decisions (the plan points' `trigger.linearity`):

1. an unsettled pair escalates the point's discard (`linearity_action =
   settle`), amplitude kept -- the check reruns at the new discard, so both
   runs are escalated; when the previous decision was already a settle
   escalation that did not reduce the pair excess by 2×, the non-stationarity
   is amplitude-driven (the 9R 0.2 MW full-amplitude run gets worse with a
   longer discard) and the point is halved (`linearity_unsettled_amplitude`);
2. a settled gain/phase failure without an earlier settled failure is halved
   once (`linearity_probe`);
3. after a halving, a discrepancy reduced by at least 2× with the same sign
   is amplitude-driven: halve again (`linearity_nonlinear`), until the check
   passes or a floor is reached; a flat, grown, or sign-flipped one is
   settling-limited: restore the previous amplitude (`amplitude_rule =
   amplitude_restore_v2`, exactly 2×, one halving undone) and escalate the
   discard (`linearity_settling_limited`);
4. after a discard lever (settle, restore, or a discard continuation), any
   reduction of L against the last settled failure at the same amplitude
   continues the discard (`linearity_discard_progress`; a transient of time
   constant τ shrinks by `exp(ΔT / τ)` per escalation, less than 2× while the
   discard is short of τ); no reduction probes the amplitude again;
5. a settled H2-only failure is reported (`linearity_h2_only`), not
   remedied; an unsettled one is escalated as in step 1.

Each decision is re-checked in a new check attempt. `--no_amplitude_halving`
keeps only the discard levers (settle and discard continuation); halving and
restore decisions are then reported.

**Per-case solver tolerance** (`tolerance_swing_scaled_v3` under the default
`power_scaled_v2` sweep rule; `tolerance_swing_scaled_v2` under v1). The
population state's absolute error scale is `tolerance × 1` (nominal 1 MW),
independent of the swing, so each halving doubles the error relative to the
swing, and the half-amplitude check already runs at 2× the base ratio. At
0.1 MW under the v1 tolerance (1e-6, resonance swing 0.79 %) the base ratio
was 1.3e-3; a once-halved point's check reached 5e-3, the size of the 0.5 %
gain tolerance, consistent with the quarter-amplitude failures above (E =
3.7, sign flips). A case whose effective target swing is below the policy
target (a halved case, every check run) therefore runs with the request
tolerance × `effective / policy` target swing, which keeps the base sweep's
ratio through halvings and checks (1e-8 → 5e-9 for the check of a base case
at P ≥ 0.01 MW, 2.5e-9 for a once-halved case's check). Base cases keep the
request tolerance exactly.

- `tolerance_swing_scaled_v3` clamps the case tolerance at the sweep floor
  (1e-11, `fr_protocol.MIN_CASE_SOLVER_TOLERANCE_V3`, recorded with
  `clamped`) instead of refusing it: under the v2 sweep rule the error scale
  is 1e-4 of the swing, so a clamped check at 1e-5 MW runs at 2e-4. A halving
  is refused (`infeasible`) only when its check's error scale would exceed
  1 % of the check's swing (the v1 resolution criterion), about six halvings
  below the floor-bound powers.
- `tolerance_swing_scaled_v2` (sweeps of the v1 rule) refuses a case
  tolerance below `1e-10` (`fr_protocol.MIN_CASE_SOLVER_TOLERANCE`): a
  halving whose case or check would need it is reported `infeasible`
  ("below the floor").

The request case records `solver_tolerance`, the case manifest records it as
`tolerance` with `fr_protocol.solver_tolerance_case` (rule, request
tolerance, scale, and under v3 the floor and `clamped`), and the simulate
call uses it. `numerics.tolerance` stays the sweep identity:
`manifest_request_differences` binds each manifest's tolerance to it through
the recorded scale, which must equal the case's effective / policy target
swing (and the recorded floor must be the request's
`policies.fr_protocol.solver_tolerance.case_floor`); a round case or check
case whose `solver_tolerance` is not that product (clamped under v3) is
refused (collection exit 2). Requests written before the field existed ran
every case at `numerics.tolerance` and verify unchanged.

Limits: a halving never takes the amplitude below `target_swing_min_pcm`
or its check beyond the solver's resolution (above); every lever counts
against `--max_rounds` (default 8, raised from 3 because the settle-first
step adds rounds); a halving keeps the discard, hence the row count; a
discard lever reports a point beyond the row budget as `infeasible`.

Records: the plan point (`amplitude_rule`, `halving_trigger`,
`linearity_action`, `linearity_trigger`, and `trigger` -- the replaced case's
dG, dphi, operating-point offset, relative swing, E, the improvement factor,
the last action, and for a linearity decision the failed tests, ΔG, Δφ, L,
the H2 status, both runs' convergence, the pair excess, `settled`, and the
compared entries in `linearity_detail`); the request case
(`effective_target_swing` = the policy target divided by 2 per halving,
`amplitude_halvings`, `solver_tolerance`); the case manifest
(`fr_protocol.amplitude.effective_target_swing`, `.halvings`, `rule_id`,
`plan_halving_trigger`, `plan_linearity_trigger`, `tolerance`,
`fr_protocol.solver_tolerance_case`, bound to the request case by
`audit_case`); the aggregate (`target_swing` is the case's effective target,
`amplitude_halvings`); the collection manifest (`swing_check.halved_points`,
and `final_effective_target_swing`, `final_perturbation_amplitude_pcm`,
`amplitude_halvings` per refined point); `freq.verify_campaign`
(`swing_check.halved_points`, `amplitude_history_pcm` and
`final_effective_target_swing` per refined point); and the refine report
(`approval_checks[].decisions`). A round case is accepted only as an exact
halving (0.5× amplitude and target, the same discard, one more halving) or
an exact restore (2× amplitude and target, a longer discard, one halving
fewer) of the case it replaces; a target swing, halving count, or case
tolerance that changes otherwise is refused (collection exit 2,
`publication_eligible = false`). Requests written before these fields
existed never halved: their cases target the policy swing. The v1 rounds of
the 2026-09-28b campaign (`amplitude_halving_v1`) remain readable.

**H2-only failures.** After the v1 halvings, seven of the 2026-09-28b
sweeps were blocked by the `|H2|/|H1|` halving test alone (gain and phase
within tolerance): the 0.001 rad/s band edge at 1R 1e-3, 0.2, 0.4, and
0.8 MW, and the halved resonance points at 1R 0.4 MW and 9R 0.1 and
0.2 MW. In every one the second harmonic of the half run, relative to the
mean power, was below the solver's absolute error scale (tolerance / P):
0.01 to 0.71 of it. All 21 checked points whose half-run second harmonic
was at least that scale passed the test; 13 points below it passed as well
and 10 were below the `1e-4` floor. The H2 diagnostic at 1e-8 (see "Solver
tolerance") confirmed the cause: three of these pairs passed with ratios of
0.49-0.60. Two changes follow: the v2 solver tolerance (100× below v1 at
P ≥ 0.01 MW) and the resolution-aware H2 applicability of the approval checks
(`not_applicable_below_resolution`, see "Approval checks"). An H2-only band
deviation above the resolution is recorded but, since 2026-09-29, no longer
blocks approval (see "Approval checks").

Judgment calls: the factor 2 separates "the lever is working" from "it is
not" with margin on both sides (a settling transient gives about 20 per
discard doubling of a converging point; the v1 diagnostic points gave 0.23
and 0.61); the settle bar of a quarter of the convergence tolerance keeps
each run's settling error well inside the 0.5 % linearity tolerance; the
sign test in step 3 rejects discrepancies that change sign under a halving,
which an amplitude term of fixed sign cannot do. All three are exposed or
named in `freq/fr_protocol.py`. The rule has been exercised on the
synthetic plants of `tests/test_freq_refine_sweep.py` and replayed on the
2026-09-28b records; it has not run on the reactor models yet.

The collector (and `freq.verify_campaign`) take the case of a key from the
highest round that lists it, audit it against THAT round's request, and
enforce the sweep-common manifest fields across base and round cases. A
round must refine keys of the base request only, with the same frequency,
the plan's amplitude (equal to the replaced one unless a measured-gain
rescale or an amplitude halving of that source is recorded), the base
request's parent identity
(below), a discard not shorter than the one it replaces, and rounds numbered
1..N without gaps; anything else fails collection closed (exit 2) and
verification (`refinement.error`, `publication_eligible = false`).

#### Parent identity (rounds, approval checks, every case)

One canonical projection in `freq/sweep_manifest.py` decides whether a
refinement round or an approval check run may stand in for, or certify,
the base sweep:

- **Request identity** (`request_identity`): the whole request except the
  case list, the `refinement` / `check` block, the setpoint table's display
  path, and the subset summaries `policies.fr_protocol.{fit_start_s,
  fit_start_range_s, settle_regime_counts, amplitude_clamped_points,
  approval_checks}` and the prior's file path. It therefore covers the
  source-file and workflow-Python digests, Git commit and dirty state,
  Python and OpenModelica versions, workflow version, model, package, core,
  power, perturbation start, the setpoint table's SHA-256 and model version
  (`setpoint_model_version`), both maturity labels, the solver, tolerance,
  and output-grid numerics, every sampling, forcing, amplitude, settle, and
  tolerance policy (including the prior's SHA-256, `drift_trend_order`, and
  `drift_regime_disabled`), and `case_common_overrides` (the override set
  every case records: setpoint values, neutron floors, pump and heat-loss
  switches, output filter).
- **Case identity** (`case_identity`): each request case except the fields
  a round or check may change -- `settle_discard_s`, `fit_start_s`,
  `stop_time_s`, `number_of_intervals`, `output_step_s`,
  `predicted_result_rows`, `output_intervals_capped`,
  `settle_refine_round`, and `perturbation_amplitude_pcm`. The regime,
  `fit_trend_order`, `gain_reference`, `fit_window_s`, rule discard, and
  forcing cadence must equal those of the case it replaces.
- **Manifest anchoring** (`manifest_request_differences`): every accepted
  case manifest must carry the identity the BASE request records (sources,
  workflow Python, Git, tool versions, model, numerics, setpoint digest and
  model version, maturity labels, power and perturbation start, the
  sweep-level settle constants, the solver-tolerance record, and the
  case-common overrides apart from `perturbationOmega` / `forcingTimeStep`).
  A case that departs is rejected as `parent_identity_mismatch`. This also
  covers a sweep whose every base point was superseded by rounds: the
  effective cases may agree with each other, but not with the base request.

- **Per-case binding** (`case_manifest_binding_problems`, applied by
  `audit_case` before any fit): each request case's decisions must be the
  ones its case manifest records -- `settle_regime`,
  `settle_rule_discard_s`, `settle_discard_s`, `fit_start_s`,
  `stop_time_s`, `settle_refine_round`, `fit_trend_order`,
  `gain_reference`, `fit_window_s`, `natural_period_fraction`,
  `fit_cycles`, `forcing_time_step_s` (also against the manifest override
  `forcingTimeStep`), `predicted_result_rows`, `output_intervals_capped`,
  `perturbation_amplitude_pcm` (`fr_protocol.amplitude.applied_pcm`),
  `amplitude_clamped` (`fr_protocol.amplitude.clamped`),
  `effective_target_swing` and `amplitude_halvings`
  (`fr_protocol.amplitude.effective_target_swing` / `.halvings`), and, for
  target-swing requests, the amplitude rule and policy (prior SHA-256 and
  id, target swing, clamp bounds, `sin_mag_scale`). A disagreement is a
  `request_mismatch`. New request cases record `amplitude_clamped`
  explicitly; for older requests it is derived from the request
  (`sweep_manifest.case_amplitude_clamp`), so earlier campaigns can be
  re-verified without re-simulation.

The runner applies the request and case checks before anything is
published or simulated (`--refine_plan`, `refinement.plan_identity_problems`;
exit 2 naming every differing path). The collector and `freq.verify_campaign`
apply all three again (`refinement.discover_rounds`,
`refinement.resolve_case_sources`, `collectFreqNominalParallel.enforce_parent_identity`),
and the approval checks compare the check request with the base request, each
check case with its effective reference case, and the two case manifests
with each other and with the base request. A long campaign resumed from a
different checkout, setpoint table, tolerance, OpenModelica build, or
estimator policy is therefore refused instead of mixed in. The aggregate labels every point
(`refined`, `refine_round`, `case_source` -- the case directory relative to
`<dir>` -- and `settle_discard_history_s`, the semicolon-joined discards it
went through); the collection manifest lists the rounds (request
fingerprint and SHA-256, points, reasons) under `refinement` and the round
requests under `sweep.mapping_files`.

Limits, all reported: `--max_rounds` (default 8 total rounds, shared by the
convergence and linearity levers), `--max_wall_s` (no new round
after this many seconds; default 0 = none), and the row budget. The report
`<dir>/refine/refine_report.json` records every round and the final status
(`converged`, `round_limit`, `wall_limit`, `infeasible`, `runner_failed`,
`collect_failed`, `no_aggregate`, `approval_checks_failed`) with the points
still not converged or approved. Exit
status: 0 all converged, 3 limit reached with unconverged points, 1 runner
or collector failure, 2 invalid input. Rerunning the command resumes: a
round directory with a plan but no request is rerun with its plan, and a
round missing from the aggregate is re-collected (rerun if incomplete).

### Convergence check (collector) and verification

`collectFreqNominalParallel.py` opens each fit window at the recorded fit
start: the sweep request case's `fit_start_s` (normal mode), the case
manifest's `fr_protocol.settle.fit_start_s` (legacy-request mode), or the
perturbation start when neither records one (requests and sidecars that
predate the discard). The audit rejects a case whose manifest fit start
disagrees with its request case (`request_mismatch`). A recorded fit start
before the perturbation start is a malformed request (exit 2). An explicit
`--fit_start` earlier than a recorded fit start is refused (exit 2) unless
`--fit_start_allow_pre_settle` is passed; that diagnostic override labels
the affected rows `unsettled_fit_window=True`. The phase reference stays
the perturbation start.

Every fitted point then gets a two-halves check. The fit window is split at
its midpoint time, both halves are fitted with the same options, and the
point is `converged` only when all three criteria hold:

1. `|conv_gain_rel_diff| <= --convergence_gain_tol` (default 0.005), where
   the gain of each half is its amplitude divided by its own fitted mean
   power `c0`. At low power the kinetics are bilinear (the response
   amplitude follows the current mean population), so a slow drift of the
   operating point after switch-on would otherwise read as an amplitude
   drift; the raw ratio is recorded as `conv_gain_rel_diff_raw`.
2. `|conv_phase_diff_deg| <= --convergence_phase_tol_deg` (default 0.5).
3. `|operating_point_offset| <= --convergence_operating_point_tol`
   (default 0.005), with `operating_point_offset = y_mean / P − 1` over the
   full window (`y_mean` the fitted trend's window mean, `c0` without a
   quadratic term) and `P` the manifest power. A constant offset biases the
   absolute gain without making the halves disagree. Points whose gain is
   referenced to the window-mean power (drift regime) or to the local mean
   power (lock-in regime) are not biased by it; they use
   `--convergence_drift_operating_point_tol` (default 0.2) as a
   linearization bound (see "Drift regime", item 4, and "Lock-in regime").

A window whose halves cannot be fitted (under `--fit_min_cycles` each) is
not converged (`not_evaluable`). The aggregate CSV adds
`perturbation_amplitude_pcm`, `settle_discard_s`, `settle_capped`,
`unsettled_fit_window`, `fit_trend`, `conv_gain_rel_diff`,
`conv_gain_rel_diff_raw`, `conv_phase_diff_deg`, `operating_point_offset`,
`h2_h1_ratio` (full-window second-to-first harmonic ratio, a linearity
diagnostic), `relative_swing` (`A / c0`), `converged`,
`convergence_reason`, and (settle_prior_v2) `settle_regime`,
`fit_trend_order`, `c2`, `gain_reference`, `fit_estimator`, `refined`,
`refine_round`, `case_source`, and `settle_discard_history_s`. For lock-in
points the fit diagnostics (`R_squared`, residuals, `n_samples`,
`n_cycles` = the whole periods used) are those of the relative-fluctuation
fit. The collection manifest gains
a `convergence` section (tolerances, counts, the non-converged list with
regime, discard, and source), records the fit-start source and the
trend-order, gain-reference, and regime counts in `fit_settings`, and lists
the refinement rounds under `refinement`. By default a non-converged point
stays in the aggregate, labeled; `--require_convergence` turns it into a
failed fit (strict abort unless `--allow_partial`).

The estimator of each point follows its request case: the recorded
`fit_estimator` (the lock-in estimator for lock-in points; requests without
it use the sine fit, or the lock-in estimator when the gain reference is
`local_mean_power`), `fit_trend_order` (1 for drift-regime and lock-in
points), and `gain_reference`; an estimator/reference combination other
than lock-in with `local_mean_power` fails the point.
`--no_auto_fit_trend` ignores the recorded order (diagnostic only);
`--fit_trend` forces at least a linear trend everywhere. Requests older
than `settle_prior_v2` carry no trend order; their settle-capped cases keep
the automatic linear trend. Refitting the 1e-5 MW `review-2026-09` case at
1e-3 rad/s shows why a trend matters whenever the free mode is undecayed:
the switch-on offset makes the mean level drift across the window, which
leaks about 1.2° into the phase difference of the halves and shifts the
fitted phase by 3.6°; with the trend term the halves agree to 0.01°.

`freq.verify_campaign` re-evaluates the recorded metrics against the
default tolerances (not the collector's own verdict) and reports
`fit_convergence` (status `pass`, `fail`, or `not_evaluated`; the
non-converged points with their failed criteria, regime, discard, and
refinement round; points whose recorded collector verdict disagrees; the
settle-capped cases of older requests) plus `fit_convergence_pass`. The
relaxed operating-point bound applies only to points whose REQUEST case is
window-mean or local-mean referenced (a row relabeled in the aggregate is a
`gain_reference_mismatch`). When the request cases record `fit_estimator`,
the aggregate must carry the `fit_estimator` column and every row must
match its request case (`fit_estimator_mismatch` otherwise). It audits every refined point against its
round's request and the base request's parent identity and reports the
rounds and the refined points (final round, discard history) under
`refinement`; a malformed round makes `publication_eligible` false.

`publication_approved` is request-aware and requires exactly `True` from
every check the request's protocol makes mandatory
(`approval_requirements`, `verify_campaign.protocol_requirements`):

1. settle rule `prior` (any rule id): the convergence check and the
   numerical-quality statistic; under `settle_prior_v2` the aggregate must
   also carry `gain_reference`;
2. `target_swing_rule_active`: the realized-swing check;
3. every check in `policies.fr_protocol.approval_checks` (a declared check
   the verifier does not know fails).

For these, a missing aggregate, a missing required column
(`conv_gain_rel_diff`, `conv_phase_diff_deg`, `operating_point_offset`,
`converged`, `gain_reference`, `relative_swing`), an unknown recorded
verdict, a nonfinite swing, or rows that do not cover every requested point
exactly once fail the check -- "not evaluated" no longer passes. Checks
that do not apply to the request (historical requests without an FR
protocol) may be not evaluated but never failed. `approval_gates` records
each gate and `approval_blockers` the failing ones. The legacy
`publication_eligible` field keeps its provenance-plus-completeness meaning
(and the default exit status). `--exit_policy converged` exits 0 only when
`publication_eligible` holds and the convergence check evaluated to pass
(`not_evaluated` fails); the campaign driver gates on
`--exit_policy publication_approved` after the refinement loop and the
approval checks.

### Realized swing check (prior gains are not trusted)

The gain prior was measured on the `review-2026-09` model, before the
physics review (4× heat-exchanger UA among others), so the amplitude it
implies is a prediction, not a result. Every aggregate row therefore carries
the realized swing `relative_swing = A / y_mean`, the `target_swing` (the
case's effective target: the policy target, halved once per amplitude
halving, as its effective request case records), their
`swing_ratio`, and `swing_check` (`pass` / `fail` / `not_applicable`): a
point passes when `0.5 <= swing_ratio <= 1.5` (`fr_protocol.SWING_BAND`),
or when its amplitude was clamped -- as its EFFECTIVE REQUEST case records
(`amplitude_clamped`; derived from the request's amplitude against its
bounds for requests written before the field existed), never the case
manifest alone -- and the swing falls short on the clamp
side (for example 0.87 % at 1.2 MW, 1e-3 rad/s, at the 10 pcm clamp). The
collection manifest lists the failures under `swing_check`;
`freq.verify_campaign` re-evaluates them from the aggregate, the base
request, and the audited case manifests (`swing_check`,
`swing_check_pass`), and a failure blocks `publication_approved`. The
refinement loop reruns a failing point with the amplitude rescaled by the
measured gain toward its effective target, `drho' = drho * target / measured` (clamped; plan
`amplitude_rule = measured_gain_rescale_v1`, recorded with the measured
swing and the replaced amplitude in the round request and the case
manifest's `fr_protocol.amplitude`), keeping its discard unless it also
failed convergence. On the corrected 1R model the prior-derived amplitudes
gave swings of 0.995–1.003 % at the nine smoke points checked (0.01, 1e-4,
and 1e-5 MW; 0.01–10 rad/s); the resonance neighborhood, where the prior
gains carry the old settling bias, was not among them.

### Approval checks (half amplitude, campaign)

A base sweep of the prior protocol declares its approval checks in the
immutable request (`policies.fr_protocol.approval_checks =
["linearity_half_amplitude"]`); `publication_approved` then requires them:

```bash
python3.12 -m freq.linearity_check campaign --results_dir <dir> -- <runner args>
```

selects the two band edges and the measured resonance point (the
aggregate's gain maximum -- the corrected model's peak, not the prior's; at
1e-5 MW it coincides with the low edge), writes a check plan with each
point's EFFECTIVE case (base or refined round: its discard, its window, and
half its amplitude and effective target swing, `amplitude_rule =
half_amplitude_check`; the check case runs at half its reference's solver
tolerance, see "Per-case solver tolerance"), runs the
runner into `<dir>/checks/linearity_half_amplitude/` (its own immutable
request with a `check` block naming the parent fingerprint and the attempt;
not a case source of the sweep), and compares every point with the linearity
criteria below (`linearity_report.json` in that directory, exit 1 on any
failure). Each compared point also records both runs' two-halves
convergence (`fits.reference.convergence`, `fits.half.convergence`), the
pair excess, `settled`, and the linearity excess (see "Amplitude halving").
`freq.refine_sweep` runs the same stage inside its loop and steers a failed
point with the linearity branch (settle, halve, restore, or report); a
point whose effective case changed, and a point that newly becomes the
measured resonance, are checked in a new attempt
`<dir>/checks/linearity_half_amplitude_attempt_NN/` (numbered 2..N without
gaps). Only the points without a current check are simulated, so `campaign`
after an in-loop run only re-evaluates. `freq.verify_campaign` re-runs the
comparison itself (`approval_checks`, `approval_checks_pass`): every attempt
must belong to the base request and carry its parent identity (the same
sources, setpoint table and model version, tolerance, OpenModelica build,
and estimator settings; see "Parent identity"); each selected point (the band
edges and the CURRENT measured resonance) is judged by the latest attempt
that covers it, which must have been planned against its current effective
case, and must pass; a missing, stale, or foreign check fails approval, and
the record lists the attempts and, per point, the attempt, reference round,
reference amplitude, and reference effective target swing. Cost: one extra
run per selected point and attempt; at 1e-5 MW the low-edge full-regime
point doubles the critical path (3.2 h 1R, 10.4 h 9R).

### Rebuilding the gain prior from the corrected model

The prior (`data/scenarios/freq/fr_gain_prior.json`) predates the physics
review. A reduced pilot (band edges plus resonance per power) cannot rebuild
it: `freq.build_gain_prior` needs the full gain curve per core and power and
a resolved resonance peak for the half-power width. The documented path is
therefore a full corrected-model campaign followed by a rebuild:

```bash
# 1. campaign with the committed prior (per-point swing verification and
#    refinement make it publication-grade on its own)
python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py run --campaign-id <A> ...
# 2. rebuild the prior from its converged, swing-verified aggregates
python3.12 -m freq.build_gain_prior --source_root 00runs/paper-rerun-<A> \
  --source_label paper-rerun-<A> --output <prior_A.json>
python3.12 -m freq.build_gain_prior --source_root 00runs/paper-rerun-<A> --check  # after committing
# 3. optional second pass (or the next campaign) with the rebuilt prior
python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py run --campaign-id <B> \
  --fr-prior <remote path of prior_A.json> ...
```

The driver's `--fr-prior` forwards the runner's `--fr_prior` (path and
SHA-256 are recorded in every request and case manifest). The second pass
is not wired in by default because it doubles the campaign cost (about
250 → 500 CPU h) without being needed for correctness: prior errors can
only shift the realized swing, the regime boundary, and the discard, and
all three are verified or corrected per point (swing band plus rescale,
the convergence check plus refinement, which promotes a failing drift point
to the full discard, and the approval checks). A prior error that moves
`omega_n` by a factor 2 changes the drift-regime bias by less than 0.07 %
(see the table above).

### Linearity self-check (half amplitude)

A point is linear when halving its amplitude leaves gain and phase
unchanged and halves `|H2|/|H1|`. To check selected points of a finished
campaign:

```bash
# 1. Suggested points (grid point nearest omega_n(P), band edges) and the
#    flags to append to the ORIGINAL campaign runner command:
python3.12 -m freq.linearity_check suggest --reference <campaign_dir>
#    -> --freq_min W --freq_max W --num_freq 1 --sin_mag_scale 0.5 --base_dir <half_root>/freq<key>
# 2. Run each suggested command (runner only; no collection needed), then:
python3.12 -m freq.linearity_check compare --reference <campaign_dir> \
  --half <half_root>/freq<key> [<half_root>/freq<key2> ...]
```

`compare` audits both cases through the collector's provenance checks,
requires the half run to carry the reference's parent identity (request,
case, and case-manifest identity, see "Parent identity"; the `--sin_mag_scale`
record is the one additional authorized difference of this mode) and exactly
0.5× the reference amplitude with the same fit start and stop time, refits
both over the recorded window, and passes a point when
`|G_half/G_ref − 1| <= 0.005`, `|phase change| <= 0.5°`, and the harmonic
test passes (tolerances are flags). The harmonic test distinguishes
(`h2_status`):

- reference `|H2|/|H1|` below 1e-4 (numerical noise):
  `not_applicable_below_floor` (passes);
- reference at or above the floor, half result finite, and the half run's
  second harmonic resolvable -- `|H2|/|H1| × relative swing` of the half run
  (its second harmonic relative to the mean power) at least 1× the solver's
  absolute error scale relative to the mean power, the half case's
  tolerance / P (`linearity_check.H2_RESOLUTION_MULTIPLE`):
  `(H2/H1)_half / (H2/H1)_ref` must lie in [0.35, 0.65] (`pass` / `fail`);
- the same, but the half run's second harmonic below that scale:
  `not_applicable_below_resolution` (passes; `h2_half_component`,
  `h2_half_error_scale`, and the multiple are recorded with the ratio). The
  multiple 1× is the boundary the 2026-09-28b campaign supports: every
  checked point at or above it passed (21 of 21), every H2 failure lay at
  0.01-0.71×, and 13 points below it passed as well (a pass below the
  resolution is not evidence either way). Under the v2 solver tolerance and
  the per-case check tolerance, the seven H2 failures of that campaign would
  sit at 2-280× the scale (same harmonic amplitudes), so the rule is a safety
  net;
- reference at or above the floor, half result nonfinite:
  `fail_half_nonfinite`;
- reference nonfinite: `fail_reference_nonfinite`, unless the reference
  window is explicitly below the measurable-harmonic threshold (under one
  forcing period or 10 samples): `not_applicable_not_measurable`.

**The band verdict is informational** (owner decision 2026-09-29). Approval
rests on the gain and phase linearity tests, which are what the transfer
function needs. The models are accurate to a few percent, and an
out-of-band ratio of a second harmonic that is a small fraction of the
fundamental does not threaten that. The check's gating `h2` entry is
therefore true unless the harmonic measurement is nonfinite
(`fail_half_nonfinite` / `fail_reference_nonfinite`, which still block). The
band result is recorded as `h2_status`, `h2_band_pass`, and
`h2_band_gating: false`. This case arose in campaign `corrected-2026-09-29`
at the 1R 1e-4 MW 0.001 rad/s band edge: gain change 6e-6, phase change
0.003 deg, both runs settled, H2 ratio 0.675. Each case is refitted with its recorded estimator (drift-regime
points: linear trend, window-mean gain reference); `--fit_trend` forces at
least a linear trend (older settle-capped requests). Points taken from a
refinement round are not covered (the half-amplitude case would need the
refined discard). A local 1R, 1 MW,
0.1 rad/s check gave a gain change of −3.4e-5, a phase change of −0.009°,
and an `|H2|/|H1|` ratio of 0.498 (0.38 % to 0.19 %).

### Campaign command lines

Per core and power (`helpers/paper-rerun/run_paper_rerun_gateway.py`
builds exactly this job): run, collect, refine until converged and approved
(the loop runs the approval checks and the amplitude halving), then verify.
The new rules are the runner and refinement defaults.

```bash
RUN_ARGS="--package legacy --core_model <core> --power <P> --freq_min 1e-3 \
  --freq_max 10 --num_freq 80 --sin_mag_auto --stop_time_mode min_cycles_after_ss \
  --min_cycles_after_ss 12 --steady_state_table <table> --base_dir <dir> \
  --n_jobs 16 --reduced_csv_for_collect --cleanup_omc_artifacts --no-reuse"
python3.12 -m freq.runFreqNominalParallel $RUN_ARGS
python3.12 -m freq.collectFreqNominalParallel --results_dir <dir> --plot --n_jobs 16
python3.12 -m freq.refine_sweep --results_dir <dir> --max_rounds 8 --max_wall_s 0 \
  "--collect_args=--plot --n_jobs 16" -- $RUN_ARGS          # exit 3: limit reached
python3.12 -m freq.linearity_check campaign --results_dir <dir> -- $RUN_ARGS   # re-evaluates
python3.12 -m freq.verify_campaign <dir> --exit_policy publication_approved    # nonzero: not published
```

`publication_approved` requires provenance and completeness, numerical
quality, convergence of every point, every realized swing in band, and the
passing approval checks; `--exit_policy converged` remains available as the
narrower settling gate.

The one-point time-domain example (`--freq_min 0.1 --freq_max 0.1
--num_freq 1 --sin_mag 1.0`, fixed stop mode, 1 MW) keeps its 1 pcm
amplitude and now fits `[8000, 10000]` s (31.8 cycles after the 6000 s
discard); it is not part of the refinement gate.

`runFreqNominalParallelAllPowers.py` forwards `--stop_time_mode`,
`--min_cycles_after_ss`, `--settle_rule`, and `--sin_mag_auto_rule` when
given (unset flags leave the runner defaults). Without `--stop_time_mode
min_cycles_after_ss`, its sweeps above the low-power threshold run in fixed
mode, where the default discard leaves the window `[8000, 10000]` s; slow
points then cannot pass the convergence check, so pass
`--stop_time_mode min_cycles_after_ss --min_cycles_after_ss 12` for
publication-grade wrapper runs (the drift regime requires that mode).

## Collection provenance and completeness

By default, collection is fail-closed: every requested frequency point is
verified against its provenance sidecars before its sine fit enters the
aggregate. For each case CSV (`<prefix>_res.csv`), `collectFreqNominalParallel.py`
(and `collectFreqNominal.py` through it) requires:

- both sidecars to exist and be readable beside the CSV:
  `<prefix>_res.manifest.json` and `<prefix>_res.validation.json`;
- the fingerprint recorded in the manifest sidecar to match a fingerprint
  recomputed from the manifest itself — an edited or corrupted sidecar is
  rejected;
- the CSV bytes to hash to the `result_sha256` recorded in the validation
  sidecar, tying the verdict to the exact content that was validated;
- the CSV to pass full output revalidation with the same acceptance criteria
  the sweep runner enforced when it wrote the sidecars (see
  [Rerun control](#rerun-control-important));
- the manifest's forcing frequency and perturbation amplitude to agree with
  the sweep request (the per-frequency `sin_mag_by_freq.csv` mapping when
  present, otherwise the uniform amplitude);
- the sweep-common manifest fields to agree across all accepted cases: model
  name and package, source hashes, workflow Python source hashes, solver,
  tolerance, output-grid policy, setpoint table path and hash, power,
  perturbation start time, backend, workflow version, and the interpreter and
  OpenModelica versions. The initialization-policy selection changes the
  per-case `model_name` (CoupledSS vs the bounded-startup twin), which
  this check compares field-for-field, so the two policies can never
  silently mix in one collection. Heterogeneous tool versions inside one
  sweep (for
  example a differing `omc_version` or `workflow_python` hash) are rejected as
  `sweep_field_mismatch`; per-frequency quantities (stop time, interval count,
  forcing frequency, perturbation amplitude) stay recorded per accepted case
  instead.

If any requested point is missing or rejected, the collection exits nonzero
and writes no aggregate — no `FreqResponseResults.csv`, no
`FreqResponseResults.m`, and no collection manifest. Before aborting, the
collector also quarantines any prior aggregate still sitting at those names
(together with a stale `BodePlot.png`) under
`00runs/tmp/quarantine/<utc-stamp>/`, so a failed collection never leaves an
outdated complete-looking aggregate behind — only the new failure record.
The console names every quarantined destination, and the failure record
lists each source→destination move under
`provenance.quarantined_prior_artifacts` — the same field name the
successful-collection manifest uses.
For diagnosis it writes
`FreqResponseResults.failures.json`, a machine-readable table listing every
requested frequency with its status, reason, source CSV, and the expected
fingerprint, so the file doubles as a rerun worklist. The failure table
records the `generation_id` of the collection that produced it. A sine fit
rejected after provenance verification (for example, a too-short or
ill-conditioned window) counts as a failed point under the same rule, so the
aggregate can never quietly publish fewer points than the sweep requested.

After the audit, the verified per-case manifests are authoritative. The
phase reference is the perturbation start recorded in the accepted manifests
(falling back to `run_params.txt` when no provenanced case exists); the
fit-window lower bound is the per-case settling fit start recorded in the
sweep request and case manifests, or that perturbation start when no
settling discard is recorded (see
[Measurement protocol](#measurement-protocol-settling-discard-amplitude-convergence)).
An explicit `--fit_start` is validated: a start
earlier than the perturbation start minus a documented tolerance of `1e-6` s
(absolute slack, absorbing float round-off when the manifest value is
repeated) is rejected with a request-validation error — exit code 2, before
any fit or worker dispatch — because samples recorded before forcing begins
carry no transfer-function information. A per-frequency stop-time mapping
entry (or an explicit `--fit_end`) that does not clear the fit start fails
the same way, before any worker dispatch.
`--fit_start_allow_pre_forcing` is the separately named, explicitly unsafe
diagnostic override: it admits a pre-forcing fit window, labels every
aggregate row `non_transfer_function=True` (with the same marker and a note
in the collection manifest and the `.m` header), and prints a console
warning — such rows are not transfer-function measurements. The
aggregate's power, model, and package labels come from the same accepted
manifests. `run_params.txt` still defines the requested
frequency set, and its `power` and `ss_time` entries must agree with the
manifest values within a relative tolerance of `1e-9` (absolute `1e-12`);
`model_name` and `package` must match exactly when present. On disagreement
the provenanced points are rejected with status `run_params_mismatch` before
any fit, the collection exits nonzero, and no aggregate is written; the
failure table names the disagreeing entries. `--allow_partial` labels this
outcome rather than excusing it — the rejected points still do not enter the
aggregate, and `provenance.manifest_authority` in the collection manifest
records the per-field comparison and the applied tolerances. A sweep in
which no point survives still exits 1 without an aggregate. A collection
with no provenanced cases at all (legacy-only under
`--allow_legacy_unprovenanced`) falls back to `run_params.txt` for the
labels and the phase reference, recorded as
`authority_source: run_params_fallback` in the collection manifest.

The per-frequency sweep-mapping files `sin_mag_by_freq.csv` and
`stop_time_by_freq.csv` are validated strictly whenever present: each must be
a readable CSV with the required columns (`frequency_rad_s` plus `sin_mag` or
`stop_time_s`), hold only parseable finite values, and list no normalized
frequency key twice — the previous last-row-wins handling of duplicate rows
is removed. The amplitude mapping must carry exactly one entry per requested
frequency, and stop-time coverage is required whenever the per-frequency
stop-time policy is active (no `--fit_end`). Entries for frequencies outside
the requested sweep grid are rejected unless `--allow_unknown_mapping_freqs`
is passed, in which case they are ignored. Any violation exits with code 2
before any provenance audit or fit. The collection manifest's
`sweep.mapping_files` section records each mapping file's SHA-256, row count,
and whether it steered the collection.

Three flags relax parts of the default; the first two label their output:

- `--allow_partial` — instead of aborting, write a clearly labeled PARTIAL
  aggregate: a `Collection status:` comment in `FreqResponseResults.m`, a
  `collection_status` column (values `complete`, `partial`,
  `legacy_unprovenanced`) in `FreqResponseResults.csv`, a PARTIAL annotation
  in the plot title, and the per-point counts in
  `FreqResponseResults.manifest.json`. The exit code is then 0, but the flag
  never excuses a rejected point — it only labels the omission, and the
  omitted points remain listed in `FreqResponseResults.failures.json`. Even
  with `--allow_partial`, a collection in which nothing verifies still exits
  1 without writing an aggregate.
- `--allow_legacy_unprovenanced` — the only path that accepts case CSVs
  without usable provenance sidecars (including validation sidecars that
  predate content hashing). Such points are accepted with status
  `accepted_legacy_unprovenanced` and flagged `legacy_unprovenanced: true`
  in the audit table and the collection manifest; the collection status
  becomes `legacy_unprovenanced` when nothing else failed, and the `.m`
  header and plot title state that the data were collected without
  provenance sidecars. The flag covers only missing provenance: a missing
  result CSV, a tampered or self-inconsistent sidecar, failed revalidation,
  sweep disagreement, or a failed fit remains a rejection under either flag.
- `--allow_unknown_mapping_freqs` — accept sweep-mapping-file entries for
  frequencies outside the requested grid instead of rejecting them; the extra
  entries are then ignored. The flag does not excuse duplicate frequency keys
  or missing coverage of the requested grid: those remain failures with or
  without it.

## Aggregate publication protocol

A complete aggregate is published as one claimed, self-verifying generation
in four steps:

1. **Aggregate claim.** The collector first takes an aggregate-level claim
   on the `FreqResponseResults` basename (the companion file
   `FreqResponseResults.claim`), which serializes concurrent collections of
   the same directory: a mixed set such as one collector's `.m` beside
   another's `.csv` cannot be published. The wait is unbounded by default;
   pass `--claim_timeout_s` (a positive number of seconds) to fail the
   collection with `ResultSlotClaimTimeout` — naming the current holder —
   instead of waiting.
2. **Quarantine of the prior generation.** While the claim is held, the
   prior current-name set (`FreqResponseResults.m`, `FreqResponseResults.csv`,
   `FreqResponseResults.failures.json`, `FreqResponseResults.manifest.json`),
   hidden staging leftovers of interrupted collections
   (`.FreqResponseResults.*.staged` and `.partial`), and stale optional
   files the new generation does not regenerate (for example a stale
   `BodePlot.png` or an old failure table) are moved under
   `00runs/tmp/quarantine/<utc-stamp>/`; the destination of every move is
   recorded in `provenance.quarantined_prior_artifacts` in the collection
   manifest.
3. **Staged, hashed publication.** Every output is written under a unique
   hidden staging name in the results directory and hashed there (SHA-256
   plus byte length), renamed into place, and only then is the collection
   manifest written — last. An interruption can therefore leave data
   products without a manifest, but never a manifest advertising products
   the collection did not finish writing.
4. **Post-publication self-check.** After the manifest is published, the
   collector re-verifies every published file against the digests the
   manifest advertises and exits nonzero on any mismatch.

Readers should verify the advertised digests before consuming an aggregate:
`freq.collectFreqNominalParallel.verify_aggregate_outputs(results_dir)`
returns the list of problems found and reports a missing, unreadable, or
hash-less manifest (an aggregate predating this protocol) as a problem
rather than passing it unverified.

When an aggregate is written, the collection manifest
`FreqResponseResults.manifest.json` records the collection status and
per-point counts, the sweep and fit settings, the sweep-common fields of the
reference case, the fingerprint of every accepted case CSV, the collector
environment (Python version, git commit, dirty flag), the aggregate claim
file name (`collector.claim_file`), and a `collector_python` digest section —
SHA-256 of `freq/collectFreqNominalParallel.py`, `freq/_common.py`,
`freq/paths.py`, and `helpers/run_results.py`, the collector-side sources
whose behavior shapes the published values, kept separate from the per-case
`workflow_python` hashes the sweep runner records. Each aggregate also
carries a top-level `generation_id`, echoed as a
`% Collection generation:` comment in the `.m` header, and an `outputs`
section mapping each published file name to its `sha256` and byte length
(the manifest itself is excluded, since it cannot hash itself). The `sweep`
labels (`power`, `ss_time`) and the model/package identity are
manifest-derived, with `authority_source` recording where they came from and
`sweep.mapping_files` carrying the mapping-file hashes;
`provenance.manifest_authority` records the `run_params.txt` comparison with
its per-field checks and tolerances. It records
`workflow_python_hashed: true` when at least one accepted case carries a
provenance sidecar and every such sidecar hashes the workflow Python
sources that produced it (a non-empty manifest `workflow_python` section).
Sidecar-less legacy points are excluded from that decision: a mixed
collection of hash-carrying sidecars and accepted legacy CSVs can therefore
report `workflow_python_hashed: true` while `collection_status` is
`legacy_unprovenanced`, because the legacy points are labeled separately.
Otherwise the flag is `false` with an explicit note — when every accepted
case is legacy-unprovenanced (no sidecar to inspect), when the provenanced
sidecars predate workflow-Python hashing or carry an empty
`workflow_python` section, or when hash-carrying and hash-less sidecars
are mixed.

Historical result trees under `00runs/freq/` predate per-case sidecars,
content hashing, and the canonical case-directory names, so collecting from
them requires `--allow_legacy_unprovenanced` and a resweep into the
canonical names. The workflow wrappers propagate the
collector's nonzero exit: `watchOmcGwCollect.py` marks the case blocked
instead of reporting a successful collection (and exits nonzero under
`--once`), and `runFreqNominalParallelAllPowers.py` records the failed power
and exits nonzero.

## Default artifact locations

- Single run default directory:
  - `00runs/freq/<core_model>/power_<power_tag>`
- Segmented sweep runs (`--package segmented`) default to the same layout
  under `00runs/segmented/freq/` (review 2026-10-01 M6; `00runs/freq/` is the
  published pre-fix record, and a `--base_dir` inside it, `00runs/startup-*`
  or `00runs/transients-*` is refused):
  - `00runs/segmented/freq/<core_model>/power_<power_tag>`
- Multi-power wrapper default base:
  - `00runs/freq/<core_model>/`
- Default compare-plot output directory:
  - `00runs/freq/plots/`
- Frequency case directories are named from the canonical frequency key,
  the `%.12g` form of the rad/s value: a 0.1 rad/s point lands in
  `freq0.1`, with its result CSV and sidecars prefixed `MSRR_freq0.1`.
  The frozen pre-fix published records under `00runs/freq/` keep their
  old zero-padded five-decimal names (`freq00.10000`); plotters and the
  campaign verifier resolve those through a legacy fallback, and the
  names are never migrated.
- Override any default with explicit CLI paths (`--base_dir`, `--results_dir`, `--out_dir`, etc.).

Quick defaults-aligned commands:

```bash
python3.12 -m freq.runFreqNominalParallel --core_model 1r --power 1.0 --n_jobs 8
python3.12 -m freq.collectFreqNominalParallel --core_model 1r --power 1.0 --plot --n_jobs 8
python3.12 -m freq.plotBodeCompareCoreModels --powers 1.0
python3.12 -m freq.plotFreqTimeCompareCoreModels --power 1.0 --freq 0.1
```

## Setpoint tables

- Default table path for sweep scripts: `core/init/setpoints_<core_model>.csv`.
- `1r` default: `core/init/setpoints_1r.csv`.
- `9r` default: `core/init/setpoints_9r.csv` (generate if missing).
- Table generator: `python core/init/generateSetpointTable.py --core_model 1r|9r`.
- Setpoint tables retain `heatLossEnabled` for compatibility, but current
  power-dependent generation writes `heatLossEnabled=0` rows only.
- Heat-loss physics is handled in startup workflows, not in power-dependent
  setpoint generation.

## Core model selection

The active sweep runners support:

- `--core_model 1r|9r` (on `--package legacy`; `1r10seg` and `r5x5_z10` are
  accepted by the flag but only on `--package segmented` — the 1-channel x
  10-axial-segment core and the 5x5-radial x 10-axial-segment core have no
  legacy nominal-trim vehicles, and `--package legacy` refuses them before any
  OpenModelica invocation)
- `--core_dir <path>` (defaults to `core`)

Default nominal models:

- `1r`: `MSRR.MSRRuhxNominalTrim`
- `9r`: `MSRR.MSRRuhxNominalTrim9R`

Initialization policy:

- Frequency runs are steady-state analyses and must use nominal-trim models.
- `runFreqNominalParallel.py` rejects startup models (`*startUp*`) to avoid
  startup/frequency initialization cross-contamination.
- `runFreqNominalParallel.py` initializes pumps at full flow for steady-state
  frequency runs (`primaryPump.freeConvFF=1`, `secondaryPump.freeConvFF=1`).
- Steady-state initialization overrides are loaded from `core/init/setpoints_*.csv`
  at the requested power. Current tables include core setpoints plus additional
  loop/region thermal initialization parameters used by nominal frequency runs.
- Setpoint qualification follows the `strict` policy by default: a table
  without the generator's `qualified` convergence-verdict column is refused
  with an error naming the CSV, so no run initializes from a table that
  predates the generator's late-window convergence checks. The
  `legacy-compatible` mode (warn on stderr naming the CSV and proceed)
  remains available only by explicit selection; policy choice is a library
  parameter, not a CLI flag. The run metadata records the active mode
  (`setpoint_policy`) beside `setpoint_table_path` in `run_params.txt` and
  the per-case manifests, with `setpoint_policy_exception` recorded true
  only when a table without the `qualified` column was consumed under an
  explicitly selected `legacy-compatible` policy. The shipped `core/init/`
  tables carry `qualified=1` on every row: they pass the strict default
  with no warning, recording `setpoint_policy=strict` with no exception.
- `--heat_loss 0|1` remains available for compatibility with legacy/custom
  tables, but the standard tables use `0` rows only.
- When heat-exchanger detailed state columns (`heatExchanger.T_*_0`) are present,
  `runFreqNominalParallel.py` enables explicit HX state initialization via
  `heatExchanger.detailedStateInitWeight=1` to start frequency cases from a
  tighter thermal steady state.

## SegmentedMSR package mode

`runFreqNominalParallel.py` drives one of two model families, selected with
`--package {legacy,segmented}`:

- `--package legacy` (default): loads `SMD_MSR_Modelica.mo` + `MSRR.mo`
  nominal-trim models through the route the runner used before the switch
  existed. Default invocations emit the same run payloads (`.mos` text,
  library copy set, subprocess argv, argparse defaults), which
  `helpers/check_r1_gates.py` byte-compares against a pinned commit. The
  lumped models and data themselves changed with the physics review
  2026-09-27 and the PSAR-basis plant data, so legacy results no longer
  reproduce the pre-review trees (including
  `00runs/paper-rerun-review-2026-09/`) byte for byte.
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
  enablement blocked, so no committed sweep enables it. When the loaded
  plant's `outer_fuel_annulus` dataset is enabled, the segmented
  `1r10seg` sweep runs the CoreVesselAssembly wrapper vehicle — by
  default the dedicated coupled-steady-state production vehicle
  `SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusCoupledSS`
  (both core fuel/moderator init modes `SteadyState`, zero
  perturbation fixed in the class) — instead of the bare-core rig
  above. `--outer-annulus-init-policy bounded_startup` selects the
  shipped-defaults `TrimThermalSS` twin
  (`SegmentedMSR.Reactors.R1MSRRuhx10SegOuterAnnulusTrimThermalSS`,
  `FixedStart` core cells, 1 pcm sine) instead; the flag is refused
  with `--package legacy` and is meaningful only for an enabled
  `1r10seg` dataset. An enabled dataset on any other core — `9r`
  is refused by name — is refused before any simulation starts, naming
  the configuration path. Enabled sweeps additionally record the
  fingerprint-active `outerAnnulusInitPolicy` manifest override naming
  the executed policy, and the effective vehicle rides the per-case
  manifest `model_name`, so results are never reused across
  initialization policies; disabled sweeps keep their historical
  vehicles, manifests, and columns.

  Rig names come from `helpers/segmented_runs.py`; `--model_name` is rejected
  in segmented mode (select the rig with `--core_model`). Homogeneous
  poison tracking is off by default; enable it only through a scenario
  `poisons:` block on `--package segmented` (five global inventories, not
  a spatial poison network). The legacy package refuses poison tracking.
  A poison-on sweep (tracking or feedback) whose authored dataset maturity
  is not approved for production or publication use — the committed
  dataset is `reduced_order_pending_review` — is refused before build
  unless `--allow-unreviewed-poison-data` is passed; the override is
  recorded in the per-case manifests (`allow_unreviewed_poison_data`)
  instead of being silent. Poison-off sweeps do not need the flag.
  Publication approval applies the same bar (top-level README, Homogeneous
  poison tracking): `verify_campaign.py --exit_policy publication_approved`
  refuses a campaign whose per-case manifests enable poison feedback from a
  non-approved dataset, even when numerical quality is green (the offending
  manifests are named in `poison_data_problems`).

Segmented-mode argument validation hard-errors away from the supported
shape:

- requires `--power 1.0` (the rigs are trimmed full-power vehicles; no
  segmented setpoint tables exist yet),
- rejects `--forcing_time_step > 0` and `--heat_loss 1` (the rigs do not
  expose those legacy knobs),
- rejects `--steady_state_table`: steady-state table application is
  suppressed entirely and the suppression is recorded in `run_params.txt`,
- rejects `--reduced_csv_for_collect`: the result-variable contract is
  already compact (14/24 columns), and the legacy reduction would keep the
  PowerBlock `pb.fissionPower.P` column [W] instead of the `nOut` population
  the collector reads,
- routes low-power `nFloor*` overrides to the rig PKE instance
  (`pke.nFloor`, `pke.nFloorDuringForcing`, `pke.nFloorSwitchTime`) instead
  of the legacy `core1R.mpke` / `msre9r.mpke` paths; low-power forcing-step
  overrides are skipped.

`SegmentedMSR_PlantData.mo` then `SegmentedMSR.mo` are loaded (no SMD load)
and both files are copied into the run work directory. Segmented results land
in their own tree,

- `00runs/segmented/freq/<core_model>/power_<power_tag>/`

so they can never collide with legacy sweeps or write into the published
`00runs/freq/` record (override with an explicit `--base_dir` as usual; one
inside `00runs/freq/`, `00runs/startup-*` or `00runs/transients-*` is
refused before anything runs, review 2026-10-01 M6). `run_params.txt` records `package=segmented` plus the
rig model name. Segmented result CSVs report temperatures in kelvin (the
SegmentedMSR library is fully kelvin); setpoint tables stay in degrees
Celsius and are not loaded in this mode.

Collecting a segmented sweep (review 2026-10-01 M3):
`collectFreqNominalParallel.py` accepts every runner core
(`--core_model 1r|9r|1r10seg|r5x5_z10`) and `--package {legacy,segmented}`,
which only selects the default `--results_dir` (the segmented-only cores
require `--package segmented`). The power signal resolves from the CSV
header: the legacy candidates and the legacy `npopulation`/`nompower`
substring fallback come first, so legacy collection is unchanged, and the
segmented contract column `nOut` (`= pke.n_population.n`, 1 at 1 MW) is
the last candidate. The PowerBlock taps `pb.reactorPower` and
`pb.fissionPower.P` are never used: they are in W, and the gain and
operating-point offset assume the normalized population.

```bash
python3.12 -m freq.collectFreqNominalParallel --package segmented \
  --core_model 1r --power 1.0 --n_jobs 8
```

Execution route note (omc 1.27 environment): `simulate()` scripting is broken
system-wide, so segmented `.mos` files use `buildModel(<rig>, tolerance =
1e-6)` — tolerance baked at build time; `-rtol`/`-atol` are not runtime flags
— and then invoke the generated executable directly with
`-stopTime=<stop_time>` and `-stepSize=<stop_time/numberOfIntervals>` (omc
1.27 rejects `-numberOfIntervals=` at runtime), plus `-outputFormat=csv`, the
result CSV name, and the usual `-override=` parameter list — and, in
contract mode (below), a probed `-variableFilter=<regex>` selecting the
contract columns at the result writer.

Run guard (review 2026-10-01 M1): the legacy route checks the omc
`simulate()` log with `helpers.omc_log.check_overrides_applied`; the
segmented route applies the same check to the generated executable's
output. A frequency whose log reports a runtime override as "not found" or
"not possible to override", or records a fatal assertion violation, is
rejected (nothing published; the sweep exits 1) even though the pinned
OpenModelica build exits 0 after all three. The poison switches
(`enablePoisonTracking`/`enablePoisonFeedback`, `Evaluate=true`) ride the
`buildModel` instance only: `helpers.segmented_runs.runtime_override_payload`
drops a requested value equal to the compiled one from the executable's
`-override=` and refuses a differing one before the build. The per-case
manifests still record the requested values.

**Result-variable contract output (default).** Segmented sweeps no longer
write the full simulation state: by default every per-frequency result CSV
carries exactly the contract columns from `helpers/segmented_runs.py`
(`RESULT_CONTRACT`) — `time`, the fixed overlay columns, the reactor-power
taps, and the `rf1..rf9` feedback channels (14 columns on
`1r`/`1r10seg`/`r5x5_z10`, 24 on `9r`). An enabled outer-annulus run
appends the annulus compact columns on top of this set (see the
vehicle note above); the CoupledSS vehicle inherits every plan
§10.6 output from its TrimThermalSS base, so the column contract
holds on both initialization policies. When the generated executable's
runtime supports `-variableFilter` (probed through its `-help` listing),
the compact CSV is written directly at the result writer — the rows and
time grid are unchanged. Otherwise the wide file is written and immediately
projected onto the contract columns in place (every data row kept; the raw
wide bytes replaced once the projection succeeds). The written header is
verified to be exactly contract-shaped before the case is accepted: extra
columns from a silently failed filter or an alias-companion emission are
repaired by the same projection, and a missing required column rejects the
frequency. In contract mode `--simflags_extra` must not carry
`-variableFilter`.

**`--full-result-output` (diagnostic).** Restores the full wide CSV for
every frequency (every state/derivative/parameter column, 15,000+ columns
on the 5x5 core); it is rejected with `--package legacy`, which keeps its
historical wide output (`--reduced_csv_for_collect` remains the legacy
reduction control). The selected set and mode are recorded in every
per-case manifest (`result_variables` / `result_output_mode`) and enter the
request fingerprint, so contract-mode and full-mode results never
cross-reuse; the realized mechanism (runtime filter vs post-run projection)
is printed in the run logs. Recorded for a representative short 5x5
verification run (on one machine and OpenModelica toolchain): the
result CSV went from 15,629 columns / ~129 MB to
14 columns / ~119 KB, with the contract
columns bit-identical to the corresponding columns of the same run's
full wide output (root README, "Segmented result-variable contract").

Smoke-scale example (card AC-1 shape), one per core:

```bash
python3.12 -m freq.runFreqNominalParallel --package segmented --core_model 1r \
  --power 1.0 --num_freq 2 --freq_min 0.1 --freq_max 1.0 \
  --ss_time 50 --stop_time 120 --n_jobs 2

python3.12 -m freq.runFreqNominalParallel --package segmented --core_model 9r \
  --power 1.0 --num_freq 2 --freq_min 0.1 --freq_max 1.0 \
  --ss_time 50 --stop_time 120 --n_jobs 2
```

Add `--no-reuse` to force fresh runs instead of reusing reachable result CSVs
(see [Rerun control](#rerun-control-important)).

Legacy byte-compat gate:

```bash
python3.12 -m pytest helpers/check_r1_gates.py -q
```

The gate regenerates reference payloads on every run from provenance SHA
`1cf8619c00069ef8eb312327e558a22f16a055a4` (the canonical-frequency-slot
commit) via `git show <SHA>:freq/<file>` — never live HEAD — and
byte-compares the working tree's legacy code path (`runModelica.mos` text,
library copy set, subprocess invocation, argparse defaults). The argparse
namespace must match the pin exactly: the allowance for added flags is
empty at this pin, and a flag may join it only when it provably cannot
touch the legacy payloads (`--package` and `--claim_timeout_s` played that
role over the original `48658ed` pin, which the gate records in its own
comments). The side tree materializes the runner, its shared helpers, and
`sweep_manifest.py` (the runner's request-manifest dependency) from the
pinned SHA. Gate scratch lives under `00runs/tmp/r1_gates/`; its end-to-end
smokes skip automatically when `omc` is unavailable. Fast non-omc unit
coverage for segmented mode lives in `tests/test_freq_segmented_mode.py`.

### Plant selection (`--plant`)

`--plant <id>` (default `msrr`) selects the plant deck of a `--package segmented` run: the plant's generated `core/generated/<id>/SegmentedMSR_PlantData.mo` is copied into the run directory (same file name), scenarios resolve in `data/plants/<id>/scenarios/` with no fallback to `data/scenarios/`, the run manifest records `data/plants/<id>`, and default outputs move under `00runs/segmented/<id>/`. A plant other than msrr requires `--package segmented`. The shipped second plant is the 1 GW scaled analog `msrr_1gw` (see `data/plants/msrr_1gw/README.md`). `--power` stays a fraction of the selected plant's nominal power
(`--power 1.0` is 1 GW on msrr_1gw), and the collector takes the same flag
for its default `--results_dir`:

```bash
python3.12 -m freq.runFreqNominalParallel --package segmented --plant msrr_1gw --core_model 1r --power 1.0
python3.12 -m freq.collectFreqNominalParallel --package segmented --plant msrr_1gw --core_model 1r --power 1.0
```

Outputs land in `00runs/segmented/msrr_1gw/freq/<core>/power_<tag>/`.

## Storage controls

To reduce disk usage during large sweeps:

- `--reduced_csv_for_collect`: write only `time` plus the power-response columns
  needed by `collectFreqNominal*`, then keep the final CSV trimmed to the
  single collected signal.
- `--stop_time_mode min_cycles_after_ss --min_cycles_after_ss 12`: extend only
  the slowest frequencies so each fit window holds at least 12 cycles after
  the settling discard (`stop = max(stop_time, ss_time + T_d + 12*2*pi/omega)`),
  while faster frequencies keep the base stop time.
- `--cleanup_omc_artifacts`: remove OpenModelica temporary build/runtime files after successful runs.

Both options are available in:

- `runFreqNominal.py`
- `runFreqNominalParallel.py`
- `runFreqNominalParallelAllPowers.py` (pass-through)

Segmented sweeps (`--package segmented`) need no reduction flag: they write
the result-variable contract columns by default (see
[SegmentedMSR package mode](#segmentedmsr-package-mode));
`--reduced_csv_for_collect` remains a legacy-path control.

## Remote omc_gw Watcher

For remote `omc_gw` campaigns that are synced and collected locally, use:

```bash
python3.12 freq/watchOmcGwCollect.py \
  --submissions_jsonl freq/results_lowpower_hifreq_fixed_v3/omc_gw_submissions_batch1.jsonl \
  --submissions_jsonl freq/results_lowpower_hifreq_fixed_v3/omc_gw_submission_batch2.jsonl \
  --log_path freq/results_lowpower_hifreq_fixed_v3/local_collect_watch.log
```

This watcher does **not** trust gateway job `state` alone. It combines:

- latest submission per `(core, power_tag, base_dir, job_id)` so a retried submission is watched as a fresh case instead of being skipped forever after the earlier job was collected
- `omc_gw result` terminal metadata (`state`, `finished_at`, `exit_code`)
- remote per-frequency stop-time completeness from reduced `*_res.csv` files

Remote scheduling convention:

- do not pack multiple sweep jobs onto one worker unless there is a specific reason
- preferred future layout: `1` `omc_gw` job per worker, reserving `16` task slots
  with `--tasks 16`, and run the sweep itself with `--n_jobs 16`
- rationale: earlier multi-job layouts saturated remote I/O bandwidth; isolating
  one sweep per worker gave more predictable progress and simpler recovery when
  tails needed reruns
- the temporary `8`-task tail reruns used during the March 26, 2026 recovery
  were only a conservative patch for the already-running low-power repair
  campaign, not the preferred steady-state policy

Behavior:

- if every frequency reached its requested `stop_time`, it syncs the reduced `*_res.csv` files, the campaign files (`run_params.txt`, `stop_time_by_freq.csv`, `sin_mag_by_freq.csv`, `output_grid_by_freq.csv`), and the provenance sidecars (`sweep_request.manifest.json`, per-case `*.manifest.json` and `*.validation.json`), then runs local `collectFreqNominalParallel.py`
- if a job is terminal but some frequencies are still incomplete, it marks the case `blocked` instead of waiting forever
- if a rerun is later appended to the submission JSONL, the watcher follows the newer job automatically on the next pass: the watch key includes `job_id`, and the submission logs are re-read on every pass
- case resolution follows the newest submission per `(core, power_tag, base_dir)` triple: once a retried job collects, an older blocked job of the same case no longer holds the watcher unresolved, while a new failed submission after collection still counts as unresolved
- with `--once`, the watcher inspects the current state once and exits instead of polling; exit 0 only if every watched case was collected in that single pass, nonzero while the newest submission of any case is still waiting or blocked

## Low-Power / High-Frequency Protocol (Current)

For `power <= 1e-2`, reproduce the accepted low-power results with the default
runner settings in `runFreqNominalParallel.py`:

- long-horizon forcing:
  - `ss_time >= 400/power`
    - `0.01 MW`: `4e4 s`
    - `0.001 MW`: `4e5 s`
    - `1e-4 MW`: `4e6 s`
    - `1e-5 MW`: `4e7 s`
  - `stop_time >= ss_time + min(5e4, ss_time/4)`
  - `stop_time_mode=min_cycles_after_ss`
  - at least `12` post-forcing cycles
- neutron floor during forcing:
  - `nFloor=1e-9`
  - `nFloorDuringForcing=1e-9`
  - `nFloorSwitchTime=ss_time`
- forcing cadence:
  - `omega <= 1 rad/s`: `forcingTimeStep=1.0 s`
  - `omega > 1 rad/s`: `forcingTimeStep=0.05 s`
  - controls:
    - `--low_power_hifreq_split`
    - `--low_power_forcing_step`
    - `--low_power_forcing_step_hifreq`
- output grid:
  - `output_interval_mode=frequency_scaled`
  - minimum `10` intervals/s
  - target `6` samples per forcing period
  - `output_step_max=50 s`
- perturbation amplitude with `--sin_mag_auto`: the default target-swing
  rule sets a per-frequency amplitude for a 1 % relative swing (0.012–10
  pcm; see [Measurement protocol](#measurement-protocol-settling-discard-amplitude-convergence)).
  The historical caps below apply only under
  `--sin_mag_auto_rule inverse_power`:
  - `power <= 1e-2` and `omega <= 1e-2 rad/s`: `sin_mag=1 pcm`
  - `power <= 1e-3`: `sin_mag=1 pcm` for all bins
- output grid cap: cases sampled by forcing events get at most
  `--max_output_intervals` (2e6) output intervals
- settling discard (per frequency, no cap): the full `T_full` (4.0e4 s /
  2.8e4 s at 0.01 MW for 1R / 9R, 3.3e5 / 2.3e5 s at 1e-3 MW, 2.7e6 /
  1.9e6 s at 1e-4 MW, 2.2e7 / 1.6e7 s at 1e-5 MW) below `120 omega_n`, the
  drift regime (6000 s, window `0.1 * 2 pi / omega_n`, linear trend,
  window-mean gain reference) above it; see
  [Measurement protocol](#measurement-protocol-settling-discard-amplitude-convergence)
- neutron floor overrides target the core assembly (`core1R.nFloor*`,
  `msre9r.nFloor*`); the former `<core>.mpke.nFloor*` keys are bound inside
  the assembly and were never applied (OpenModelica "not possible to
  override"), which the override guard now refuses
- collection:
  - fit window from the recorded settling fit start to each run's `stop_time`

For reproduction of the historical (pre-protocol) records, pass
`--settle_rule none --sin_mag_auto_rule inverse_power`; otherwise leave the
low-power defaults enabled.

Accepted low-power plot set:

- The accepted low-power plots are summarized in
  `freq/results_lowpower_default_protocol`.
- That tree contains:
  - `0.01 MW`: the earlier accepted `1 pcm` slow-band merge
  - `0.001 MW`, `1e-4 MW`, `1e-5 MW`: reduced-forcing slow-band reruns merged
    with the original `run4` higher-frequency bins
- This remains a summary-only plotting tree so the original `freq/results_run4`
  raw data stay untouched.

Final `1e-5 MW` high-frequency resolution:

- The earlier slow-band fix was not sufficient for `1e-5 MW` above `1e-2 rad/s`.
- The accepted final `1e-5 MW` plot set is summarized in
  `freq/results_lowpower_default_protocol_hifreqfix_final`.
- For the `9r` upper tail, the accepted rerun protocol is:
  - `sin_mag = 1 pcm`
  - `ss_time = 1e7 s`
  - `stop_time = 1.005e7 s`
  - `output_samples_per_period = 2`
  - `output_step_max = 100 s`
  - collect with `fit_start = 10049900 s`
- The final `9r` tail summary is stitched from:
  - `freq/results_lowpower_hifreq_fixed_v8_1em05_9r_tail_ss1e7_spp2_split/9r/power_0p00001/FreqResponseResults.csv`
- The accepted final compare plot is:
  - `freq/BodePlots_run4_compare/BodePlot_compare_1r_9r_power_0p00001.png`
- A follow-up phase issue in that accepted `1e-5 MW` upper tail was caused by
  the collector reference frame, not by the model:
  - the late-window high-frequency tail collections fit on `time - fit_start`
  - before March 26, 2026, the collector wrote `phase_deg` directly from that
    fit, so phase was implicitly referenced to `fit_start`
  - that is only equivalent to the physical forcing phase when
    `fit_start == ss_time`
  - `collectFreqNominal.py` and `collectFreqNominalParallel.py` now convert the
    fitted phase back to the forcing activation time (`perturbationStartTime` /
    `ss_time`) before writing `phase_deg`
  - the updated `freq/results_lowpower_default_protocol_hifreqfix_final` and
    `freq/BodePlots_run4*` `0p00001` plots already include this correction

Post-processing note:

- The accepted low-power reruns above use the full post-forcing window implied
  by the per-frequency `stop_time` map.
- `collectFreqNominalParallel.py --fit_window_mode inverse_omega` is still
  useful for diagnostics, but it is not the accepted default for these
  low-power plots.

Nominal-power presentation-only extension to `1e3 rad/s`:

- The separate nominal-power extension used only in the presentation is
  summarized in:
  - `freq/results_nominal_hifreq_extension_merged_corrected`
- Diagnosis:
  - the original `10` to `1000 rad/s` extension looked numerically unstable
    above about `100 rad/s`
  - increasing CSV output density alone did not fix it
  - targeted `omc_gw` probes showed the real issue was under-resolved DASSL
    internal stepping in the top decade
- Accepted repair for the `50` to `1000 rad/s` band:
  - rerun the same `21` log-spaced bins with
    `simflags_extra=-maxStepSize=5e-5`
  - after confirming that the `100` to `1000 rad/s` fix was real, extend the
    same repair down to the original extension suffix starting at
    `50.118723 rad/s` so the presentation-only high-frequency segment uses one
    consistent methodology
  - keep the original presentation extension settings otherwise:
    - `power = 1.0 MW`
    - `sin_mag = 1 pcm`
    - `ss_time = 20 s`
    - `stop_time = 100 s`
    - `output_interval_mode = fixed_rate`
    - `output_intervals_per_second = 3000`
    - collector fit window `95` to `100 s`
- Accepted artifacts:
  - corrected high-band reruns:
    - `freq/results_nominal_hifreq_maxstep5e-5`
    - `freq/results_nominal_hifreq_maxstep5e-5_from50`
  - corrected merged summary:
    - `freq/results_nominal_hifreq_extension_merged_corrected`
  - updated presentation figure:
    - `latex/MSRR_journal_article/figs/BodePlot_compare_1r_9r_power_1_to1e3.png`
- Optional probe flag:
  - `--forcing_time_step` exposes the model `forcingTimeStep` override for
    targeted event-cadence experiments
  - default is `0` (disabled)
  - it was useful for this investigation, but it is not part of the accepted
    nominal high-frequency repair

## Rerun control (important)

By default, `runFreqNominalParallel.py` **reuses** an existing `*_res.csv`
only when all of the following hold:

- a provenance sidecar exists beside it
  (`<prefix>_res.manifest.json`), its stored fingerprint is self-consistent,
  and it matches a fingerprint rebuilt from the **current request**: SHA-256
  digests of the loaded model sources, the fully qualified model name (on
  enabled outer-annulus runs this is the CoupledSS vs bounded-startup
  vehicle selected by `--outer-annulus-init-policy`), the
  complete override set (including per-frequency forcing cadence, low-power
  floors, and — on enabled outer-annulus runs — the
  `outerAnnulusInitPolicy` initialization-policy selection),
  solver/tolerance/time span/output grid, forcing frequency and
  amplitude, setpoint-table provenance, the selected result-variable set
  (segmented sweeps), and interpreter/tool versions;
- the stored CSV still passes output validation: modification time no earlier
  than the launch instant recorded in its manifest, nonempty header with at
  least one data row, monotonically nondecreasing `time`, finite required
  columns, a final sample at or above 99.5 % of the requested stop time, and
  at least two rows.

Anything else — a changed input, a result from an older workflow revision, or
a plain CSV without sidecars — is **not** reused: the prior CSV, any leftover
temporary file, and stale sidecars are moved to
`00runs/tmp/quarantine/<utc-stamp>/`, and the frequency case reruns. The
rerun itself is gated by the same validation before success is reported, and
the finished output gains both sidecars
(`*_res.manifest.json` and `*_res.validation.json`). The simulation writes
its raw output to a temporary companion (`<case>_res.csv.tmp`), and only
output that passes validation is published atomically onto the final name
before the sidecars are written, so a failed or interrupted run never leaves
a reusable final result; a per-result claim (`<case>_res.csv.claim`)
serializes concurrent same-slot launches. The claim wait is unbounded by
default; the sweep runner's `--claim_timeout_s` bounds it (a positive number
of seconds; expiry raises `ResultSlotClaimTimeout` naming the holder instead
of waiting forever). A failed validation is
reported per frequency in the end-of-run summary.

Collection enforces this provenance independently: `collectFreqNominalParallel.py`
re-verifies each case's sidecars against the CSV bytes and the sweep request
before fitting (see
[Collection provenance and completeness](#collection-provenance-and-completeness)).

Practical consequences:

- Historical trees produced before this change carry no sidecars, so the
  first resweep with default settings regenerates each case and quarantines
  the old CSVs. Pass `--no-reuse` for the same effect deliberately, or point
  `--base_dir` at a fresh tree.
- Published results under `00runs/freq/` remain the pre-fix record and are
  not regenerated in place.

To force a full rerun regardless:

- `--no-reuse` — always rerun each frequency point.

Alternative: set a new `--base_dir`.

Note on `run_params.txt`: it records the sweep-level request (power grid,
time horizons, flags) purely as information, after all cases have been
scheduled. It is never consulted as a reuse key — the per-case sidecars are
authoritative. The collector additionally requires its `power`, `ss_time`,
`model_name`, and `package` entries to agree with the verified case
manifests (see
[Collection provenance and completeness](#collection-provenance-and-completeness)),
so a stale request file is a rejected collection, not a silently relabeled
one.

## Full frequency workflow (recommended)

Use this sequence to regenerate steady-state tables, run the full sweep for all
setpoint powers, and generate overlay plots. This keeps CSVs small by retaining
only the columns needed for fitting.

```bash
python3 core/init/generateSetpointTable.py --core_model 1r --n_jobs 4
python3 core/init/generateSetpointTable.py --core_model 9r --n_jobs 4
```

```bash
set -euo pipefail
BASE="freq/results_reduced"
POWERS=(1e-05 1e-04 0.001 0.01 0.1 0.2 0.4 0.6 0.8 1.0 1.2)

tag_for() {
  python3 - "$1" <<'PY'
import sys
p=float(sys.argv[1])
text=f"{p:.5f}".rstrip("0").rstrip(".")
if not text:
    text="0"
print(text.replace(".", "p"))
PY
}

run_one() {
  local core="$1" power="$2"
  local tag
  tag=$(tag_for "$power")
  local base_dir="${BASE}/${core}/power_${tag}"
  echo "=== Running ${core} power=${power} (tag ${tag}) ==="
  python3 freq/runFreqNominalParallel.py \
    --core_model "${core}" \
    --power "${power}" \
    --freq_min 1e-3 \
    --freq_max 1e1 \
    --num_freq 80 \
    --n_jobs 18 \
    --base_dir "${base_dir}" \
    --sin_mag_auto \
    --reduced_csv_for_collect \
    --cleanup_omc_artifacts
  python3 freq/collectFreqNominalParallel.py \
    --results_dir "${base_dir}" \
    --plot \
    --n_jobs 18
}

for core in 1r 9r; do
  for power in "${POWERS[@]}"; do
    run_one "$core" "$power"
  done
done
```

To rerun only the slow-frequency tail in an existing result tree with longer
trajectories and then recollect against the per-frequency stop-time map:

> Do not run this against a published historical tree (`freq/results_run4*`,
> `00runs/freq/`): copy the tree to scratch first (e.g. under `00runs/tmp/`)
> and point `--base_dir` / `--results_dir` at the copy, so the published
> record stays untouched.

```bash
python3.12 freq/runFreqNominalParallel.py \
  --core_model 1r \
  --power 0.01 \
  --freq_min 1e-3 \
  --freq_max 1e1 \
  --num_freq 80 \
  --base_dir 00runs/tmp/rerun_1r_power_0p01 \
  --n_jobs 8 \
  --stop_time 10000 \
  --stop_time_mode min_cycles_after_ss \
  --min_cycles_after_ss 12 \
  --reduced_csv_for_collect \
  --cleanup_omc_artifacts

python3.12 freq/collectFreqNominalParallel.py \
  --results_dir 00runs/tmp/rerun_1r_power_0p01 \
  --fit_window_mode fixed \
  --n_jobs 8
```

For the intermediate-power comparison panel used in the paper, use
`\{0.2, 0.4, 0.6, 0.8\} MW` (not `0.02–0.08 MW`).

```bash
python3 freq/plotBodeCompareCoreModels.py \
  --powers "${POWERS[@]}" \
  --results_root freq/results_reduced \
  --out_dir freq/BodePlots_reduced
```

To export the annotated nominal-power time-domain example used in the paper and
presentation:

```bash
python3 freq/plotFreqTimeCompareCoreModels.py \
  --results_root 00runs/paper-rerun-review-2026-09/freq_time_example \
  --power 1.0 \
  --freq 0.1 \
  --out_path 00runs/tmp/MSRR_freq_nominal_time_example.png
```

(Write the plot to scratch: the `latex/MSRR_journal_article*/figs/` trees
are frozen, so never pass one as `--out_path` in ordinary tasks.)

## Zero-power runs

Frequency response at `power = 0` is nonphysical (the signal is dominated by
numerical noise). Table-driven sweep scripts therefore **skip zero power**
entries by default. If you really need a zero-power run, execute
`runFreqNominalParallel.py` directly with `--power 0`.

## Legacy note

Older depletion-coupled frequency scripts and inputs are kept in `../helpers/`.


### Immutable campaign request and transactional plot

`runFreqNominalParallel.py` writes `sweep_request.manifest.json` before it
submits any worker. The published manifest is immutable campaign authority:
an existing manifest recording a different request (a differing
fingerprint), or one that cannot be read and validated, is refused and the
run exits 2, while a rerun with the identical request stays idempotent. To
change a campaign request, delete the existing manifest — a deliberate
human step — or point the new request at a fresh campaign directory.
Normal collection refuses a missing or altered request manifest, requires
every expected case, rejects case sidecars from another campaign, and
reports unexpected result directories. Historical directories may be
collected only with `--allow_legacy_request_manifest`; the resulting
aggregate is labeled as legacy request authority.

With `--plot`, `BodePlot.png` is rendered to a unique staged file before the
aggregate claim modifies current outputs. The PNG is then quarantined,
published, hashed, and listed in `FreqResponseResults.manifest.json` with the
CSV and MATLAB products. Interactive display is separate (`--show_plot`) and
occurs only after successful publication.
