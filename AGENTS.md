# AGENTS.md

Guidance for coding agents working in this repository.

## Scope

- This file applies to the entire repository tree.

## Project Snapshot

- Repo purpose: Modelica models and Python tooling for MSRR startup and frequency-response studies.
- Primary areas:
  - `core/` for Modelica source and setpoint generation.
  - `startup/` for startup simulation runners and plotting.
  - `freq/` for frequency sweep, collection, and plotting workflows.
  - `transients/` for nonlinear transient runners and plotting.
  - `latex/` for the journal article, presentation, and related figures.
  - `helpers/omc_gw/` for site-configurable remote OpenModelica job dispatch.
  - `tests/` for OpenModelica-backed integration tests.

## Environment Assumptions

- Run commands from repo root unless a script expects a subdirectory.
- Python target is `python3.12` where available.
- OpenModelica (`omc`) should be on `PATH` for simulation and tests.

## Common Commands

- Run tests:

```bash
python3.12 -m pytest tests/test_msrrv2.py -q
```

- Run startup scenarios:

```bash
python3.12 -m startup.runMSRR --core_model 1r --scenario startup_to_100kw
python3.12 -m startup.runMSRR --core_model 9r --scenario startup_to_100kw
```

- Run nominal frequency sweeps (parallel):

```bash
python3.12 -m freq.runFreqNominalParallel --core_model 1r --power 1.0 --n_jobs 8
python3.12 -m freq.runFreqNominalParallel --core_model 9r --power 1.0 --n_jobs 8
```

- Run nonlinear transient scenarios:

```bash
python3.12 -m transients.run_nonlinear_steps --core_models 1r 9r
python3.12 -m transients.plot_nonlinear_steps --core_models 1r 9r
```

- Generate the nominal-power time-domain frequency plot used in the paper:

```bash
python3.12 -m freq.plotFreqTimeCompareCoreModels \
  --results_root 02rerun/freq \
  --power 1.0 \
  --freq 0.1 \
  --out_path latex/MSRR_journal_article/figs/MSRR_freq_nominal_time_example.png
```

- Rebuild the paper or presentation after LaTeX or figure changes:

```bash
latexmk -pdf -interaction=nonstopmode -file-line-error latex/MSRR_journal_article/msrr_system_dynamics.tex
latexmk -pdf -interaction=nonstopmode -file-line-error latex/MSRR_journal_article/presentation/msrr_presentation.tex
```

## Editing Expectations

- Prefer minimal, targeted changes.
- Preserve existing scientific assumptions and parameter defaults unless explicitly asked to alter model behavior.
- Keep both `1r` and `9r` workflows consistent when adding shared features.
- Keep all simulation artifacts under `00runs/` (or a clearly named subdirectory within it) rather than scattering run outputs across the repository.
- Keep figure names, output paths, and LaTeX includes synchronized when a script generates paper-facing plots.
- Keep site-specific cluster details out of tracked source files; use user config for `helpers/omc_gw/`.
- If changing CLI flags or outputs, update nearby docs (`README.md` and script help text) in the same change.

## Validation Guidance

- If `omc` is available, run the targeted test command above after code changes.
- For workflow changes in `startup/`, `freq/`, or `transients/`, run at least one representative command for each affected path.
- For plotting changes used by the paper or presentation, regenerate the touched figure and rebuild the affected LaTeX document when practical.
- For `helpers/omc_gw/` changes, run the gateway unit tests and avoid hard-coding local cluster settings in the repository.
- If runtime-heavy commands are impractical, note what was not run and why.

## Safety and Hygiene

- Do not delete or overwrite large result directories unless explicitly requested.
- Keep simulation outputs and intermediate run artifacts in `00runs/` to maintain a single, predictable location for generated data.
- Avoid destructive git operations (`reset --hard`, forced checkout, etc.).
- Keep generated artifacts out of commits unless the task explicitly requires them.
