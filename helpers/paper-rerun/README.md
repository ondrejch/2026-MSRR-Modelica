# paper-rerun: legacy MSRR paper reproduction over the OpenModelica gateway

Scripts that re-ran the legacy MSRR paper results (startup, transients,
frequency response) for the revised paper, on the OpenModelica 1.27.0 SSH
cluster, through the gateway in `helpers/omc_gw/`. Everything here ran the
`review-2026-09` campaign; the same tooling can run a new campaign against a
different commit.

The full protocol, acceptance rules, and reviewer-response mapping live in the
reviewer guide,
[paperv2/legacy_msrr_paper_rerun_and_reviewer_guide.md](../../paperv2/legacy_msrr_paper_rerun_and_reviewer_guide.md)
(tracked and present in this checkout; a passing release-verification
record also verifies that the target is in the packed release archive).
This README covers the tooling.

## How a campaign runs

`run_paper_rerun_gateway.py` builds a job plan (stages: preflight, setpoints,
startup, transients, frequency, freq-time, plots, inventory), dispatches each
job to a cluster worker, and records every job in a state file under
`00runs/tmp/paper_rerun_<campaign-id>.json`. The state file is the source of
truth; the cluster-side job directories under `~/.modelica_ssh/jobs/` are the
evidence. Results land on the cluster under `00runs/paper-rerun-<campaign-id>/`
and sync back with rsync.

The digest guard makes stored job commands immutable: to change what a stage
does, run a dispatcher from this directory and record its jobs in the report,
never edit the plan in place.

## Preflight and setpoint jobs

The preflight records the checkout's source digests. It also runs
`python3.12 -m helpers.setpoint_model_version status`, so a checkout whose
lumped model sources do not match `core/init/model_version.json` fails
before any simulation job starts.

Each `setpoints-<core>` job runs the documented two-step procedure in one
script:

1. `core.init.generateSetpointTable` for every campaign power. It writes the
   rows that pass its late-window checks, plus the table's model-version
   sidecar. It exits 3 when some rows do not pass, and the job continues
   only on 0 or 3. Any other status (1: no table, hard failure) fails the
   job.
2. `test -f` on the table.
3. `complete_setpoint_table.py`. It runs the warm-started continuation for
   exactly the missing or unqualified powers and merges them into the table.
   It keeps `<table>.first_pass.csv` and its sidecar, and publishes the
   merged table, its rebound sidecar, and `<table stem>_completion.json` as
   one recoverable transaction. The record holds the digests of every input
   and output; a crash is finished or restarted on the next run.

   It fails the job when:
   - the first-pass table is not bound to the current model (checked even
     when nothing is missing);
   - the continuation exits nonzero or its output is not bound;
   - a target is missing or unqualified;
   - powers are duplicated or nonfinite;
   - artifacts of another attempt are present;
   - a power is still missing.
4. `python3.12 -m helpers.setpoint_model_version verify` on the finished
   table: binding, finite unique qualified rows, and every campaign power
   present.

Publication thresholds are never relaxed (`--accept_unconverged` is not
passed). This replaces the review-2026-09 hand-configured
`submit_continuation_setpoints.py` / `merge_continuation_setpoints.py`
dispatch for new campaigns.

## Frequency jobs

Each `freq-<core>-<tag>` job runs five steps in one shell script
(`set -euo pipefail`):

1. `freq.runFreqNominalParallel` with the campaign arguments (per-frequency
   settling discard, rule `settle_prior_v2`; power-scaled solver tolerance;
   see `freq/README.md`, "Measurement protocol");
2. `freq.collectFreqNominalParallel --plot`;
3. `freq.refine_sweep` with the same runner arguments: points that fail the
   convergence check or the realized-swing band are rerun (longer discard,
   or amplitude rescaled by the measured gain) in `<dir>/refine/round_NN/`
   and re-collected, up to `--refine-max-rounds` (default 3) and
   `--refine-max-wall-seconds` (default 0, no limit); its exit status is
   kept in `refine_rc`;
4. when `refine_rc` is 0, `freq.linearity_check campaign` with the same
   runner arguments: half-amplitude reruns of the band edges and the
   measured resonance point into `<dir>/checks/linearity_half_amplitude/`
   (exit status kept in `linearity_rc`);
5. `freq.verify_campaign --exit_policy publication_approved`, written to
   `logs/freq_<core>_<tag>_verify.json`: an unconverged point, a swing out
   of band, or a failed or missing approval check fails the job instead of
   being published; the job also fails when `refine_rc` or `linearity_rc`
   is nonzero.

Logs: `logs/freq_<core>_<tag>_{run,collect,refine,linearity}.log`; the
refinement report is `<dir>/refine/refine_report.json`, the check report
`<dir>/checks/linearity_half_amplitude/linearity_report.json`. Expected cost
per job (cost model from the `review-2026-09` logs, 16 slots): minutes at
P >= 0.01 MW, 0.8 h (1R) / 2.5 h (9R) at 1e-4 MW, and 3.2 h (1R) / 10.4 h
(9R) at 1e-5 MW per round, plus the approval checks (at 1e-5 MW another
3.2 h / 10.4 h for the low-edge point); the 9R 1e-5 MW job is the critical
path. `--fr-prior <remote path>` runs the frequency jobs with a gain prior
rebuilt by `freq.build_gain_prior` from an earlier corrected-model campaign
(`freq/README.md`, "Rebuilding the gain prior"). The preflight also hashes
`freq/fr_protocol.py`, `freq/refinement.py`, `freq/refine_sweep.py`, and the
committed gain prior into `metadata/source_sha256.txt`.

## Quick start

```bash
# smoke first: 15 jobs, minutes, proves the pipeline end to end
python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py run \
  --campaign-id review-smoke2 --remote-workdir ~/git/SMD-MSRR-dev \
  --full-power-grid-smoke

# full campaign (33 jobs, hours), then sync results locally
python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py run \
  --campaign-id review-2026-09 --remote-workdir ~/git/SMD-MSRR-dev --sync

# after a crash or a completed side job: pick up where the state file says
python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py resume \
  --campaign-id review-2026-09 --expected-commit <sha>
```

Run from the repo root. Commit pinning is mandatory: every job re-checks
`git rev-parse HEAD` on the worker before it runs.

## Scripts

Driver:

- `run_paper_rerun_gateway.py` — plan / run / resume / sync. Build the plan,
  dispatch through the gateway, record state, rsync results back.

Campaign stage dispatchers (run jobs the driver's digest guard cannot change):

- `submit_continuation_setpoints.py` — fill missing setpoint-table rows by
  continuation from existing rows.
- `merge_continuation_setpoints.py` — merge those rows into the campaign
  setpoint tables, with `MERGE_NOTES.json`.
- `submit_partial_plots_startup_transients.py` — plot startup and transient
  figures before the frequency stage closes.
- `submit_final_plots_11powers.py` — regenerate the Bode set at 11 powers and
  write the resolution note into campaign metadata.

Frequency-protocol qualification (rev032 review, run before the corrected
campaign is accepted):

- `submit_drift_validation.py` — drift-regime validation matrix: 1R and
  9R, 1e-5 / 1e-4 / 1e-3 MW, the regime boundary, two interior points, and
  10 rad/s; per point the drift regime, the doubled drift window
  (`--settle_drift_window_fraction 0.2`), and the forced full discard
  (`--settle_force_full`, plus one refinement round), each a single-point
  sweep under `00runs/drift-validation-<id>/<core>/power_<tag>/<label>_<variant>/`,
  then a comparison job. `plan` prints the matrix, every job script
  (`--show-commands`), and the CPU estimate without contacting the gateway
  (base runs: 11.3 CPU h 1R, 37.3 CPU h 9R, longest cell 10.4 h; one
  refinement round of every full reference adds at most 21.6 / 70.8 CPU h,
  critical path about 31 h).
  - `run` writes the immutable matrix manifest (every expected cell,
    frequency, variant, commit, setpoint hash and model version, infeasible
    cells), dispatches, compares, and returns the remote comparison's
    verdict. State lives in `00runs/tmp/drift_validation_<id>.json`, and the
    run is resumable.
  - `reconstruct` rebuilds the matrix manifest for a run dispatched before
    manifests existed (`drift-validation-2026-09-28`, commit `38c8c95`).
  - `compare-remote` runs the comparison on the gateway. With
    `--compare-workdir` it runs from a second checkout at the fix commit
    while the validation root stays under `--remote-workdir`.
  - `sync` / `compare` pull the tree and compare locally (large: the full
    case CSVs are needed).

  `--setpoints-1r` / `--setpoints-9r` name the corrected-model setpoint
  tables on the cluster; `--top-full` adds the 10 rad/s full references
  where the row budget allows. The full references' refinement runs with
  `--no_amplitude_halving --skip_approval_checks`, so every variant of a
  point compares estimators at one amplitude.
- `compare_drift_validation.py` — the comparison report
  (`drift_validation_report.json` / `.md`). It is fail-closed (rev033
  review):
  - it judges a tree only against its matrix manifest, requiring exact
    coverage and reporting missing whole points, duplicates and unknown
    cells;
  - every variant of a point must share one identity: commit, source and
    workflow digests, setpoint hash and version, tools, numerics;
  - it verifies every aggregate through its collection manifest and output
    hashes, and requires each cell's `freq.verify_campaign` result.

  Per variant it reports gain, phase, `|H2|/|H1|`, mean operating-point
  shift, realized swing and convergence. Drift vs full (same normalization)
  is the gating comparison, "as published" is informational, and the
  window-doubling sensitivity is checked too, each within 0.5 % / 0.5 deg.
  It recommends a drift operating-point bound (`ceil(1.5 × max shift)`)
  when every point passes. Exit 0 only when every point is conclusive and
  passes; the dispatcher no longer masks per-cell or final failures.

1e-5 MW recovery (the 15 high-frequency points per core crashed inside OMC;
see "The 1e-5 MW fix" below):

- `submit_freq_1e5_rerun.py` — serial two-worker rerun attempt (superseded).
- `submit_freq_1e5_wide_rerun.py` — one-point-per-job rerun attempt
  (superseded; it also overwrote the sweep-level metadata, which forced the
  full-resweep remedy).
- `submit_two_phase_diag.py` — settle-only diagnostics that ruled out the
  restart design.
- `submit_freq_1e5_coarse_rerun.py` — the working remedy: fresh full sweeps
  on the coarse settle grid.
- `collect_1e5_coarse.py` — collection for the coarse sweeps, including the
  mapping-file rebuild.

Analysis:

- `compare_coarse_grid.py` — protocol-grid vs coarse-grid comparison at the
  boundary frequency; the basis for the 0.06% gain / 0.002 deg figure.
- `apply_excluded_powers_note.py` — write the campaign metadata note and
  re-run the inventory (kept for the record; superseded by the resolution
  note).

## The 1e-5 MW fix

The 15 highest-frequency 1e-5 MW points per core died inside OMC v1.27.0:
`LOG_ASSERT | out of memory` followed by a segfault in the delay machinery.
Cause: the models' pump/region trip delays (`delay(time, tripTime=1e12, ...)`)
append one 16-byte entry per output point to delay ring buffers that are never
trimmed. At 2^26 = 67,108,864 entries the next doubling asks for 2^27 * 16 =
2^31 bytes, which overflows the 32-bit size computation in `expandRingBuffer`
(ringbuffer.c:114). The protocol's frequency-scaled grid gave these points
74.8M-382M output intervals; the highest surviving point sat at 66.5M, a 1%
margin.

(Superseded for new campaigns: the trip times are closed-form since the
physics review 2026-09-27, so the remaining delay histories are trimmed, and
the runner caps the output grid of event-sampled cases at 2e6 intervals; see
`freq/README.md`, "Feasibility".)

The remedy changes the settle output grid and nothing else:
`--disable_low_power_auto_output_grid --output_interval_mode fixed_rate
--output_intervals_per_second 0.00005` (~2003 intervals over the full span).
The forcing window stays dense because every forcing event (0.05 s cadence) is
written to the CSV, and the transfer-function fit only reads
`t >= perturbation_start`. Measured at the boundary frequency on both cores:
the collector's fit agrees between grids to 0.06% in gain and 0.002 deg in
phase.

Evidence in the campaign tree: `metadata/excluded_powers.json` (the resolution
note), `freq/1r/power_0p00001_{proto,coarse}_check/` (the comparison pair),
and `freq/<core>/power_0p00001_protocol_archive/` (the protocol-grid attempt,
preserved by rename).

## Rules that will bite you

- Never pass `--accept_unconverged` (guide sec. 4.2).
- Keep pytest and omc scratch in `00runs/tmp/`, never `/tmp`, never the repo
  root.
- Do not delete `00runs/startup-*`, `00runs/freq/`, `00runs/transients-*`;
  they are the published record.
- Per-point runner invocations into a shared sweep directory overwrite the
  sweep-level request and mapping files. The collector enforces sweep-common
  provenance, so a mixed sweep can never aggregate. Run a full sweep or
  archive and re-sweep; do not patch a sweep point by point.
- `git push` is denied for agents in this repo. Commit, then push from a
  shell.
