# SKILLS.md

Project-specific working skills for `SMD-MSRR-dev`.

## Global Artifact Location Rule

- Keep all simulation artifacts in `00runs/` (or clearly named subdirectories under `00runs/`) so generated outputs do not sprawl across tracked source areas.

## 1) Core / Setpoint Skill

Use when editing Modelica core models or setpoint-generation tooling in `core/`.

- Preserve published workflow behavior unless the user explicitly requests a model change.
- Keep `1r` and `9r` handling aligned when the same feature exists in both.
- If setpoint tables or initialization columns change, update the generator, downstream readers, and nearby docs together.
- Validate with a targeted command or test rather than a full campaign unless the user asks for the full rerun.

## 2) Startup Workflow Skill

Use when editing startup schedules, startup plotting, or startup analysis in `startup/`.

- Keep reactivity terminology consistent with the paper: distinguish model/external/total reactivity carefully.
- Do not assume startup and steady-state workflows share the same numerical safeguards; confirm floors, source handling, and flow logic explicitly.
- Replot the affected startup figure when behavior changes.
- Rebuild the paper or presentation if startup figures or captions are touched.

## 3) Frequency Analysis Skill

Use when editing `freq/` sweep, collection, or plotting scripts.

- Preserve the run/collect/plot contract across `runFreqNominal*`, `collectFreqNominal*`, and the plotting scripts.
- Keep default run/plot artifacts rooted under `00runs/freq/` unless the user explicitly overrides paths.
- Avoid overwriting large result trees unless the user explicitly asks for reruns.
- When adding a paper-facing plot, keep the script output path, figure filename, and LaTeX include synchronized.
- For low-power or fit-window changes, document the numerical rationale in `freq/README.md`.
- Validate with one representative frequency case before assuming the whole sweep still works.

## 4) Transient Analysis Skill

Use when editing `transients/` run or plotting scripts.

- Keep run and plotting defaults aligned so `plot_nonlinear_steps.py` works against `run_nonlinear_steps.py` outputs without extra flags.
- Keep transient artifacts in `00runs/` (for example `00runs/transients-1r-9r/`) rather than tracked source subdirectories.
- Preserve 1R/9R scenario parity unless the requested change is intentionally one-sided.

## 5) Documentation / LaTeX Skill

Use when editing `latex/`, paper-facing figure scripts, or presentation sources.

- Keep terminology, units, figure names, and labels consistent with the repository scripts.
- Prefer small text edits over large rewrites unless the user asks for broader revision.
- If a figure changes, make sure the paper and presentation both reference the intended file.
- Build the touched document when practical:
  - `latexmk -pdf -interaction=nonstopmode -file-line-error latex/MSRR_journal_article/msrr_system_dynamics.tex`
  - `latexmk -pdf -interaction=nonstopmode -file-line-error latex/MSRR_journal_article/presentation/msrr_presentation.tex`

## 6) Remote OpenModelica Gateway Skill

Use when editing `helpers/omc_gw/`.

- Keep site-specific details out of tracked files; use per-user config under `~/.config/msrr_omc_gw/`.
- Preserve the local testability of the gateway without requiring a live cluster.
- Update the gateway README and tests when CLI/config behavior changes.
- Validate with:
  - `python3.12 -m pytest tests/test_modelica_ssh_gateway.py -q`
