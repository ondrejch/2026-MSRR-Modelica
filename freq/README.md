# Frequency response

Frequency-response analysis workflow and related utilities for nominal-trim MSRR models.

## Main scripts

- `runFreqNominal.py`: serial frequency sweep driver (single core model, one power).
- `runFreqNominalParallel.py`: parallel frequency sweep driver (thread pool).
- `runFreqNominalParallelAllPowers.py`: table-driven multi-power wrapper around parallel sweep + collection workflow.
- `collectFreqNominalParallel.py`: parallel post-processing (gain/phase fitting + Bode
  exports). Verifies each case's provenance sidecars before fitting and refuses
  to publish an incomplete aggregate by default (see
  [Collection provenance and completeness](#collection-provenance-and-completeness)).
- `plotFreqTimeCompareCoreModels.py`: create an annotated time-domain comparison for one `1r`/`9r` frequency point.
- `plotBodeCompareCoreModels.py`: overlay Bode plots for `1r` vs `9r` at selected powers.
- `sensitivity/`: reviewer-response hA-exponent frequency sensitivity at 1.0 MW, 1R (see `sensitivity/README.md`).

## Sine fit method (collector)

`collectFreqNominalParallel.py` uses the shared fitter
(`freq/_common.fit_sine_least_squares`)
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
data. Integer-cycle,
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
points the fitter rejected before producing coefficients. Historical Bode results produced with the old projector are pre-fix
evidence rather than a new baseline.

## Collection provenance and completeness

By default, collection is fail-closed: every requested frequency point is
verified against its provenance sidecars before its sine fit enters the
aggregate. For each case CSV (`<prefix>_res.csv`), `collectFreqNominalParallel.py`
requires:

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
  OpenModelica versions. Heterogeneous tool versions inside one sweep (for
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
fit-window lower bound and the phase reference are the perturbation start
recorded in the accepted manifests (falling back to `run_params.txt` when no
provenanced case exists). An explicit `--fit_start` is validated: a start
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

Historical result trees that predate per-case sidecars and
content hashing require collecting with
`--allow_legacy_unprovenanced`. The workflow wrappers propagate the
collector's nonzero exit: `watchOmcGwCollect.py` marks the case blocked
instead of reporting a successful collection (and exits nonzero under
`--once`), and `runFreqNominalParallelAllPowers.py` records the failed power
and exits nonzero.

## Default artifact locations

- Single run default directory:
  - `00runs/freq/<core_model>/power_<power_tag>`
- Multi-power wrapper default base:
  - `00runs/freq/<core_model>/`
- Default compare-plot output directory:
  - `00runs/freq/plots/`
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

- `--core_model 1r|9r`
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
- `--heat_loss 0|1` remains available for compatibility with legacy/custom
  tables, but the standard tables use `0` rows only.
- When heat-exchanger detailed state columns (`heatExchanger.T_*_0`) are present,
  `runFreqNominalParallel.py` enables explicit HX state initialization via
  `heatExchanger.detailedStateInitWeight=1` to start frequency cases from a
  tighter thermal steady state.

## Storage controls

To reduce disk usage during large sweeps:

- `--reduced_csv_for_collect`: write only `time` plus the power-response columns
  needed by `collectFreqNominal*`, then keep the final CSV trimmed to the
  single collected signal.
- `--stop_time_mode min_cycles_after_ss --min_cycles_after_ss 12`: extend only
  the slowest frequencies so each run sees at least 12 post-forcing cycles,
  while faster frequencies still reuse the original shorter trajectories.
- `--cleanup_omc_artifacts`: remove OpenModelica temporary build/runtime files after successful runs.

Both options are available in:

- `runFreqNominal.py`
- `runFreqNominalParallel.py`
- `runFreqNominalParallelAllPowers.py` (pass-through)

## Remote omc_gw Watcher

For remote `omc_gw` campaigns that are synced and collected locally, use:

```bash
python3.12 freq/watchOmcGwCollect.py \
  --submissions_jsonl <run_dir>/omc_gw_submissions.jsonl \
  --log_path <run_dir>/local_collect_watch.log
```

This watcher does **not** trust gateway job `state` alone. It combines:

- latest submission per `(core, power_tag, base_dir)` so rerun entries replace older failed jobs
- `omc_gw result` terminal metadata (`state`, `finished_at`, `exit_code`)
- remote per-frequency stop-time completeness from reduced `*_res.csv` files

Remote scheduling convention:

- do not pack multiple sweep jobs onto one worker unless there is a specific reason
- preferred future layout: `1` `omc_gw` job per worker, reserving `16` task slots
  with `--tasks 16`, and run the sweep itself with `--n_jobs 16`
- rationale: earlier multi-job layouts saturated remote I/O bandwidth; isolating
  one sweep per worker gave more predictable progress and simpler recovery when
  tails needed reruns

Behavior:

- if every frequency reached its requested `stop_time`, it syncs reduced CSVs and runs local `collectFreqNominalParallel.py`
- if a job is terminal but some frequencies are still incomplete, it marks the case `blocked` instead of waiting forever
- if a rerun is later appended to the submission JSONL for that case, restarting the watcher will follow the newer job automatically

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
- perturbation amplitude with `--sin_mag_auto`:
  - `power <= 1e-2` and `omega <= 1e-2 rad/s`: `sin_mag=1 pcm`
  - `power <= 1e-3`: `sin_mag=1 pcm` for all bins
- collection:
  - use the full post-forcing window implied by each run's `stop_time`

For reproduction, leave the low-power defaults enabled.

Post-processing note:

- `collectFreqNominalParallel.py --fit_window_mode inverse_omega` is still
  useful for diagnostics, but it is not the accepted default for these
  low-power plots.

## Rerun control (important)

By default, `runFreqNominalParallel.py` **reuses** an existing `*_res.csv`
only when all of the following hold:

- a provenance sidecar exists beside it
  (`<prefix>_res.manifest.json`), its stored fingerprint is self-consistent,
  and it matches a fingerprint rebuilt from the **current request**: SHA-256
  digests of the loaded model sources, the fully qualified model name, the
  complete override set (including per-frequency forcing cadence and low-power
  floors), solver/tolerance/time span/output grid, forcing frequency and
  amplitude, setpoint-table provenance, and interpreter/tool versions;
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

```bash
python3.12 freq/runFreqNominalParallel.py \
  --core_model 1r \
  --power 0.01 \
  --freq_min 1e-3 \
  --freq_max 1e1 \
  --num_freq 80 \
  --base_dir freq/results_reduced/1r/power_0p01 \
  --n_jobs 8 \
  --stop_time 10000 \
  --stop_time_mode min_cycles_after_ss \
  --min_cycles_after_ss 12 \
  --reduced_csv_for_collect \
  --cleanup_omc_artifacts

python3.12 freq/collectFreqNominalParallel.py \
  --results_dir freq/results_reduced/1r/power_0p01 \
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
  --results_root freq/results_reduced \
  --power 1.0 \
  --freq 0.1 \
  --out_path <out_dir>/MSRR_freq_nominal_time_example.png
```

## Zero-power runs

Frequency response at `power = 0` is nonphysical (the signal is dominated by
numerical noise). Table-driven sweep scripts therefore **skip zero power**
entries by default. If you really need a zero-power run, execute
`runFreqNominalParallel.py` directly with `--power 0`.



### Immutable campaign request and transactional plot

`runFreqNominalParallel.py` writes `sweep_request.manifest.json` before it
submits any worker. Normal collection refuses a missing or altered request
manifest, requires every expected case, rejects case sidecars from another
campaign, and reports unexpected result directories. Historical directories
may be collected only with `--allow_legacy_request_manifest`; the resulting
aggregate is labeled as legacy request authority.

With `--plot`, `BodePlot.png` is rendered to a unique staged file before the
aggregate claim modifies current outputs. The PNG is then quarantined,
published, hashed, and listed in `FreqResponseResults.manifest.json` with the
CSV and MATLAB products. Interactive display is separate (`--show_plot`) and
occurs only after successful publication.
