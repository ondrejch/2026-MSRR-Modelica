# AGENTS.md

Guidance for coding agents working in the `latex/` subtree.

## Scope

- This file applies to everything under `latex/`.
- Follow the repository-root `AGENTS.md` as baseline guidance.

## Purpose

- `latex/` contains paper, presentation, and dissertation sources documenting
  MSRR modeling, startup behavior, and frequency-response studies.

## Editing Expectations

- Keep edits minimal and focused on the requested section or file.
- Preserve technical meaning, symbols, and units.
- Keep terminology consistent with repository scripts and model names
  (for example, `1r`, `9r`, startup, nominal frequency response).
- Keep figure filenames and `\includegraphics` paths synchronized with the
  scripts that generate paper-facing plots.
- Do not rewrite large sections for style unless explicitly requested.

## LaTeX Hygiene

- Preserve existing document structure (`\\section`, `\\subsection`, includes,
  labels, citations, and bibliography keys).
- Do not change citation keys or label names unless required by the task.
- Avoid introducing new packages unless necessary; prefer existing macros and
  conventions used in each document.
- If adding equations, ensure variable names and notation match nearby text.

## Validation Guidance

- If practical, run a local build for the touched document and check for errors.
- When a figure or caption changes, rebuild the affected document and confirm
  the new figure is the one actually included.
- If full compile is heavy or unavailable, at least verify syntax consistency in
  edited blocks and note what was not validated.

## Safety

- Do not delete figures, generated PDFs, or auxiliary folders unless explicitly
  requested.
- Keep binary artifacts and large generated outputs out of commits unless the
  task explicitly requires them.
