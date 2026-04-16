# Frequency response

Frequency-response analysis workflow and related utilities for nominal-trim MSRR models.

## Main scripts

- `runFreqNominal.py`: serial frequency sweep driver.
- `runFreqNominalParallel.py`: parallel frequency sweep driver (thread pool).
- `runFreqNominalParallelAllPowers.py`: table-driven multi-power wrapper around parallel sweep + collection workflow.
- `collectFreqNominal.py`: serial post-processing (gain/phase fitting + Bode exports).
- `collectFreqNominalParallel.py`: parallel post-processing.
- `plotFreqRun.py`: plot a single frequency run CSV.
- `plotFreqTimeCompareCoreModels.py`: create an annotated time-domain comparison for one `1r`/`9r` frequency point.
- `plotBodeCompareCoreModels.py`: overlay Bode plots for `1r` vs `9r` at selected powers.
- `runFreqNominal_script.sh`: example command sequence.

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
  --submissions_jsonl freq/results_lowpower_hifreq_fixed_v3/omc_gw_submissions_batch1.jsonl \
  --submissions_jsonl freq/results_lowpower_hifreq_fixed_v3/omc_gw_submission_batch2.jsonl \
  --log_path freq/results_lowpower_hifreq_fixed_v3/local_collect_watch.log
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
- the temporary `8`-task tail reruns used during the March 26, 2026 recovery
  were only a conservative patch for the already-running low-power repair
  campaign, not the preferred steady-state policy

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

By default, `runFreqNominalParallel.py` **reuses existing** `*_res.csv` files
if they already reach the requested `stop_time`. This means changing inputs
(e.g., `--sin_mag`, `--ss_time`, or different steady-state tables) will **not**
trigger new runs unless you force it.

To force a full rerun, use:

- `--no-reuse` (new flag) — always rerun each frequency point.

Alternative: set a new `--base_dir` or delete the old `freq*/` subfolders.

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
  --base_dir freq/results_run4/1r/power_0p01 \
  --n_jobs 8 \
  --stop_time 10000 \
  --stop_time_mode min_cycles_after_ss \
  --min_cycles_after_ss 12 \
  --reduced_csv_for_collect \
  --cleanup_omc_artifacts

python3.12 freq/collectFreqNominalParallel.py \
  --results_dir freq/results_run4/1r/power_0p01 \
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
  --results_root 02rerun/freq \
  --power 1.0 \
  --freq 0.1 \
  --out_path latex/MSRR_journal_article/figs/MSRR_freq_nominal_time_example.png
```

## Zero-power runs

Frequency response at `power = 0` is nonphysical (the signal is dominated by
numerical noise). Table-driven sweep scripts therefore **skip zero power**
entries by default. If you really need a zero-power run, execute
`runFreqNominalParallel.py` directly with `--power 0`.

## Legacy note

Older depletion-coupled frequency scripts and inputs are kept in `../helpers/`.
