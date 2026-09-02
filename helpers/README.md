# Helpers

Auxiliary tooling.

## Contents

- `omc_gw/`: SSH-native remote execution helpers for dispatching Modelica workflows to
  shared worker nodes with per-node task-capacity tracking and user-level
  site configuration outside the repository.
- `run_results.py`: provenance library shared by the simulation workflows,
  providing canonical run manifests with SHA-256 fingerprints, result-CSV
  validation, atomic publish helpers, and quarantine of stale outputs under
  `../00runs/tmp/quarantine/`. Default quarantine discovery uses the nearest
  `pyproject.toml` + `core/` + `helpers/` tree (so an unpacked source
  archive without `.git` still stays inside the project) and falls back to
  a parent `.git` directory. Manifests also record SHA-256 digests of the
  workflow Python sources that produced the result (the active runner module
  plus shared helper modules, in a `workflow_python` section), so workflow-code
  edits change the fingerprint even when the git commit and dirty flag are
  unchanged.
## Note

Active nominal-trim frequency workflows are in `../freq/`.
Shared active Modelica sources are in `../core/`.
