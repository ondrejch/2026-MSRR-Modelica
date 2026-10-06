# Helpers

Auxiliary tooling. The older depletion/sensitivity scripts have been moved to
`legacy/` and are no longer maintained (see `legacy/README.md`).

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
- `campaign_evidence.py`: compact evidence package of a results campaign.
  `python3.12 -m helpers.campaign_evidence --id <id> --tree 00runs/<tree>
  [...] [--article-figs DIR] [--setpoints DIR] [--record FILE ...]
  [--disposition FILE] --output rebaseline-evidence/<id>` writes
  `<id>-manifest.json`, `<id>.md`, the campaign `SHA256SUMS` normalized to
  `<tree>/<path>` and `files.local.sha256`: per tree the metadata, commits
  and toolchain; per run (every `*_res.manifest.json`) the family, core,
  case, vehicle, commit, recomputed fingerprint and validation verdict;
  frequency sweeps folded per sweep directory; frequency verification
  reports; campaign and article figures matched by digest; the setpoint
  tables with their compact evidence; named records (the drift-validation
  report) with their verdicts; and an embedded disposition text. No
  payloads and no timestamps (a re-export is byte-identical); never written
  into a result tree. Uses `rebaseline_evidence.py` (path normalization and
  the setpoint evidence builder). Committed package:
  `rebaseline-evidence/psar-2026-10/` (campaign `psar-2026-10-01` and its
  `psar-2026-10-02` delta, the paper's results).
- `published_tree_guard.py`: one classification of the published pre-fix
  record trees (`00runs/freq/`, `00runs/startup-*`, `00runs/transients-*`).
  The fake-run test helpers refuse those working directories
  (`refuse_published_tree_cwd`), and the `--package segmented` routes of
  the startup, transients and frequency runners refuse an explicit
  `--run_dir` / `--out_dir` / `--base_dir` inside them
  (`refuse_published_tree_output`, review 2026-10-01 M6). The segmented
  defaults live under `00runs/segmented/`; the legacy routes keep their
  historical defaults.
- `make_source_archive.py`: packs repository sources into a ZIP with
  `MANIFEST.json` (SHA-256 per file). Two artifact kinds exist with
  separate builders (owner decision O7, 2026-09-08), so the pack rules
  cannot blur:
  - `create_archive` builds the distributable release archive. The
    canonical manifest kind is `msrr_release_source_archive`
    (`RELEASE_ARCHIVE_KIND`, the `create_archive` default). The pre-split
    literal `msrr_source_archive` (`LEGACY_RELEASE_ARCHIVE_KIND`) remains
    constructible with an explicit `kind=` argument; the release gate
    hard-rejects it. The full release denylist is the release contract:
    `create_archive` hard-fails on any `forbidden_path_violations` match
    and on user-local content-denylist hook hits. The forbidden directory
    segments are `.collab/` (internal collaboration records; owner decision
    P0.3) and `.opencode/` (the local-only agent-routing framework; owner
    decision 2026-09-13), plus the tool-cache segments (`.mypy_cache/`,
    `.ruff_cache/`, `.ipynb_checkpoints/`): a tracked path in any of them
    refuses the pack with the offender named, while the documented junk
    classes (the `00runs/`, `__pycache__`, and `.pytest_cache/` segments,
    `*.o`, `*_res.csv`) keep their silent exclusion.
  - `create_review_bundle` builds the NON-distributable internal review
    bundle (manifest kind `msrr_internal_review_bundle`,
    `REVIEW_BUNDLE_KIND`): the tracked tree plus the untracked `.collab/`
    collaboration records by owner scope decision (the rev014
    external-review precedent). The release denylist still runs over the
    bundle's documented narrower subset — the candidate set minus
    `REVIEW_BUNDLE_DENYLIST_EXEMPT_PREFIXES` (`.collab/`) — so the
    `.opencode/` agent-routing framework (local-only by owner decision
    2026-09-13; a forbidden directory segment, not a silent junk class,
    and outside the `.collab/`-only exemption), credentials, site
    configuration, logs, caches, and generated outputs refuse the bundle
    exactly as they refuse a release archive; nothing is silently bypassed.
    The manifest names the added untracked sources
    under `includes_untracked` and carries `audience`/`redistribution`
    per kind from `KIND_MANIFEST_METADATA` (the bundle: internal-review
    audience, `redistribution: "not-permitted"`); the release gate
    rejects this kind outright. The internal-review cut also performs
    the extract-and-verify gate mechanically (owner directive
    2026-09-26): after the member set is packed, the packed content
    (tracked tree plus `.collab/` -- exactly what ships) is extracted to
    a fresh directory outside any Git worktree, where
    `python3.12 -m pytest tests/ --collect-only -q -p no:cacheprovider`
    and
    `python3.12 -m pytest tests/ -q -p no:cacheprovider -m "not omc_check
    and not omc_short and not omc_long and not omc_parity"` (cwd = the
    extract root) must both pass; a failing gate refuses the cut
    (`SourceArchiveError`; no ZIP written), and a passing gate records
    its result in the embedded `MANIFEST.json` under `extract_gate`
    (the packed `git_commit`, the extract location, the failing
    `git -C <extract> rev-parse --show-toplevel` outcome, and the two
    command summaries). The extract directory is created per run and
    removed on exit, success and refusal alike. The release path is
    unchanged.
  The manifest always travels inside the archive (the builders never
  write `MANIFEST.json` into the repository tree): a root-level
  `MANIFEST.json` (gitignored) is builder output, never a tracked release
  record -- delete a stale one rather than committing or rebuilding it.
  Any bundle carrying `.collab/` records must use
  `kind=msrr_internal_review_bundle`.
  The CLI selects the builder with
  `python3.12 -m helpers.make_source_archive --kind {release,internal-review}`
  (default `release`); default output filenames distinguish the kinds
  (`msrr-source-<commit>-<stamp>.zip` vs
  `msrr-internal-review-<commit>-<stamp>.zip`, both under
  `00runs/tmp/source_archives/`), and the command prints the manifest
  `kind` and `audience`.
  Packing itself requires git. The
  manifest `created_utc` stamp is derived from repository state, not the
  wall clock: `SOURCE_DATE_EPOCH` (seconds) wins when set to a valid
  nonnegative integer, otherwise the HEAD commit timestamp is used, and
  only a tree without any commit falls back to the live clock. Packing the
  same commit twice therefore yields byte-identical ZIPs (identical
  SHA-256), and a differing `SOURCE_DATE_EPOCH` changes only the manifest
  timestamp. `--repo-root` must be a git toplevel (a subdirectory is
  refused), and unpacked archives refuse to be repacked; both cases fail
  instead of silently packing an enclosing checkout. Packing refuses when
  any packaging file the README promises to archive consumers is absent
  from the tree (release contract).
- `release_gate.py`: one-command release gate
  (`python3.12 -m helpers.release_gate`). Refuses a working tree with
  modified tracked files, checks the packaging files promised by the
  top-level README, packs through `make_source_archive` to a unique
  STAGING path under the release scratch (default
  `00runs/tmp/release_gate/`; `--scratch-dir` overrides; never the
  requested final path), unpacks into the same scratch, verifies
  `MANIFEST.json` (schema and artifact kind: only
  `msrr_release_source_archive` — the internal review bundle and the
  pre-split `msrr_source_archive` literal are refused) and every recorded
  SHA-256 against the unpacked files, re-checks the manifest's provenance
  attestation against the checkout cleared up front (a public-release
  manifest reporting a dirty tree or a commit other than the cleared one
  is refused, as is a member set missing any required packaging file),
  and runs the Python-only test subset from the extract (see
  `../tests/README.md`).
  Only after every mandatory check passes is the staged archive published
  to the requested final path (atomic rename; a cross-filesystem
  `--output` is served by copy + fsync + rename), so a failed gate never
  leaves a distributable-looking ZIP behind: on any failure the staged
  archive is removed while the verification extract is kept for
  inspection. `--skip-consumer-subset` produces a clearly labeled
  STRUCTURAL CHECK ONLY result: the archive is published under a
  `<name>.structural-check<ext>` path instead of the requested final path
  and is reported as `structural_check_only`, never as a full release
  pass; its embedded `MANIFEST.json` is stamped `consumer_subset:
  "skipped"`, a stamp `verify_archive_manifest` refuses, so a renamed
  structural-check archive can never verify as a full release under any
  filename. The stamp is an unsigned plain JSON field: anyone able to unzip/edit/re-zip the archive can strip it, consistent with the unsigned-manifest trust model — it guards against accidental renames, not against a deliberate forger. The verification extract is deleted after a successful run
  unless
  `--keep-extract` retains it for inspection, and
  `--consumer-timeout-s SECONDS` sets the wall-clock limit for the
  consumer-subset pytest run (default 1800 s).
  `--report-json PATH` writes a machine-readable report (outcome,
  archive SHA-256 and final path, git commit, file count, consumer-subset
  status and command, the content-denylist hook state, and the Python
  version); the embedded `MANIFEST.json` records the same
  `content_denylist_hook` state (the resolved hook path, `"disabled"` when
  `MSRR_RELEASE_DENYLIST` is explicitly off, `"absent"` when unset). The
  release gate is the only supported way to produce a distributable
  source ZIP: it packs through `make_source_archive.create_archive()`
  (a library API the gate drives), so a direct `create_archive()` call
  that skips the gate's checks, like a manually copied or re-zipped
  tree, is not a distributable artifact.
- `wheel_check.py`: controlled-host distribution check
  (`python3.12 -m helpers.wheel_check`), run from a source checkout.
  Builds the wheel and the sdist, installs each into its own clean
  virtual environment, and from an unrelated directory executes
  representative console commands (the `--help` forms of the smoke
  scripts plus `python3.12 -m helpers.emit_modelica_plant --check` on the
  packaged data). Verifies that both artifacts carry the Modelica
  sources, YAML decks, JSON schemas, setpoint CSVs, and the gateway
  configuration template, that runtime data access resolves through the
  packaged `msrr_data` payload (`importlib.resources`) rather than the
  checkout, and that an unsupported install mode (checkout markers
  without a `data/` directory) fails with the actionable message.
  Retains a JSON record (versions, artifact SHA-256s, and every executed
  command with its exit code) under `00runs/tmp/distribution_check/` by
  default; `--work-dir` overrides the scratch root, `--deps`
  selects the clean-env dependency mode (`auto`, `pypi`, `host`), and
  `--keep-venvs` keeps the smoke virtualenvs for inspection. (The
  former manual-dispatch CI form,
  `../.github/workflows/release-distribution.yml`, was removed with the
  rest of the GitHub CI -- owner decision 2026-09-16; this tool is now
  the sole performer of the distribution check.)
- `check_r1_gates.py`, `check_r3_startup_gates.py`: self-check suites for the
  segmented frequency and startup runners; their OpenModelica smoke runs write
  under `00runs/tmp/` by default, and `R1_GATES_AC1_BASE_DIR` /
  `R3_GATES_STARTUP_RUN_DIR` redirect campaign output elsewhere deliberately —
  never into the published `00runs/freq/` / `00runs/startup-*` records.
- `draw_msrr_schematics.py`: schematic drawing utility with two modes:
  the default journal mode regenerates the six 1R/9R block/loop/nodal
  SVGs of the frozen journal article
  (`../latex/MSRR_journal_article/figures/`; historical behavior — do not
  run this mode as part of ordinary tasks, that tree is frozen since
  2026-09-08 by owner decision O10, see the root `AGENTS.md`), and
  `python3.12 -m helpers.draw_msrr_schematics --what outer-annulus`
  renders the user-manual topology schematic
  `../doc/figures/outer_fuel_annulus_vessel_schematic.png` (300 dpi;
  topology only, no MSRR-geometry claim).
- `draw_manual_schematics.py`: regenerates the three schematic PNGs used by
  the user manual (`../doc/figures/`); `make -C doc figures` runs it.
- `poison_oracle.py`: independent five-state homogeneous poison equilibrium
  (Te-135/I-135/Xe-135 and Pm-149/Sm-149) assembled from the governing
  equations, not by parsing Modelica. Not a spatial network: five global
  inventories and no per-cell states. Evaluates absorption-rate constants
  under `core_volume` or `total_fuel` flux averaging, closed-form
  full-power inventories, homogeneous concentrations, reduced-order Xe/Sm
  worth with the derived denominator `worth_denominator(...)`
  (`Sigma_a = nu*F0/(phi0*V_avg)`; physics review 2026-09-27, the deck's
  `sigma_a_fuel` is a legacy value no longer read), and equation residuals. `from_plant(...)` binds a loaded plant
  deck plus aggregate `v_fuel` / `v_core`. Used to check
  `SegmentedMSR.Nuclear.HomogeneousPoisons` initialization against
  `data/plants/msrr/shared/poisons.yaml` (`msrr_reference_v1`, independent
  yields, pending scientific review). Microscopic cross-sections are
  authored in barns and flux in n/(cm2.s); `microscopic_xs_m2` /
  `neutron_flux_per_m2_s` convert to SI. Tracking remains off unless a
  scenario `poisons:` block enables it on `--package segmented`.
- `segmented_trim_levels.py`: regenerates the segmented-only
  `segmented_steady_state` block of each core deck (physics review
  2026-09-27). It builds a scratch wrapper of each segmented runner rig with
  its feedback setpoints and initial core temperatures at the deck's 570 °C
  isothermal reference, and runs it to steady state at 1 MW and nominal flow.
  It prints (or, with `--write`, stores) the per-cell fuel and graphite
  temperatures, plus the 9R plenum. Each core first runs to `--stop_time`
  (default 150000 s); while any temperature still moves more than
  `--max_drift_K` (default 1e-5 K) over the last 10 % of the run, the run
  repeats with the horizon doubled, up to `--max_stop_time` (default
  600000 s). A deck is written only from a run that met that bar, so the
  default command qualifies all four cores. `--check` recomputes the
  selected cores with the same procedure and exits 1 when a committed block
  differs from the recomputed state by more than `--max_drift_K`, or when
  the recomputation does not qualify; `--write` and `--check` are mutually
  exclusive. Scratch goes to `00runs/tmp/segmented_trim/`. Run `python3.12
  -m helpers.emit_modelica_plant` after `--write`
  (`python3.12 -m helpers.segmented_trim_levels [--cores ...] [--stop_time S]
  [--max_stop_time S] [--max_drift_K K] [--write | --check]`).
  `--plant <id>` (default `msrr`) trims another plant deck: the run loads
  `core/generated/<id>/SegmentedMSR_PlantData.mo`, `--write` updates
  `data/plants/<id>/cores/`, and scratch goes to
  `00runs/tmp/segmented_trim/<id>/`. For a generated scaled plant, rerun
  `python3.12 -m helpers.scale_plant --plant <id>` afterwards (it keeps the
  trimmed values and records their provenance), then
  `python3.12 -m helpers.emit_modelica_plant --plant <id> --catalog`.
- `scale_plant.py`: generates a geometrically scaled plant deck
  `data/plants/<id>/` from its authored `scaling.yaml` and a base deck
  (`data/plants/msrr_1gw/` from `data/plants/msrr/`): nominal power, core
  power density and core temperature rise in the spec; every base quantity
  scaled by an explicit rule (volumes, lengths, areas, powers, flows, design
  temperatures, kept neutronics), and an unlisted quantity is an error. It
  also writes the plant's scaled scenario decks under
  `data/plants/<id>/scenarios/` and a README with the derived design.
  Optional spec keys: `reference_temperature` (the isothermal reference;
  a core deck whose steady-state block was trimmed at another reference is
  rewritten as the estimate, never kept), `hx_ua_factor`, and
  `results_ii_uhx_demand_follows_flow`.
  `--check` exits 1 when the committed deck is out of date
  (`python3.12 -m helpers.scale_plant --plant msrr_1gw [--check]`).
- `core_ha_scaled.py`: derives the core salt-graphite conductance hA of a
  scaled core with the same graphite lattice cell (companion of
  `core_ha_derivation.py`, which it reproduces on the PSAR geometry): a
  friction-regime flow split and the Gnielinski laminar / transitional /
  turbulent channel correlation, plus the minimax fit of the two-branch
  flow law `hA/hAnom = max(FF^hAExp, hA_lam_frac*FF^hA_lam_exp)` that
  `SegmentedMSR.Core.filmFlowFactor` evaluates
  (`python3.12 -m helpers.core_ha_scaled --length-scale S --mdot M --power P`).
- `omc_log.py`: classifies OpenModelica logs for every runner, lumped and
  segmented. `check_overrides_applied` refuses a run whose log reports a
  runtime override as "not found" or "not possible to override", or records
  a fatal assertion violation (`fatal_assert_violations`: an error-level
  record, or the runtime's "assert was triggered. Throwing now!" verdict);
  the pinned OpenModelica build can exit 0 after either. Info-level
  violation records that the runtime later drops ("previous asserts are
  ignored") stay non-fatal; `run_rejected` combines the exit code and the
  fatal-assert scan.
- `segmented_runs.py`: the single source of truth for driving the
  SegmentedMSR package from the runners: core-to-vehicle maps, the
  result-variable contract (`RESULT_CONTRACT`), and
  `runtime_override_payload`, which removes a build-bound switch (the
  `Evaluate=true` poison switches) from the runtime `-override=` payload
  when the requested value equals the compiled one and refuses a differing
  value before the build.
- `plant_config.py`, `scenario_config.py`, `emit_modelica_plant.py`,
  `emit_scenario_wrapper.py`: load YAML plant/scenario decks under `../data/`
  and emit Modelica `PlantData` packages to `../core/generated/`. YAML is the
  authored source of truth; generated `.mo` files must not be edited by hand.
  Lumped 1R/9R models bind `MSRR_PlantData` (load it before `MSRR.mo`).
  SegmentedMSR binds kelvin `SegmentedMSR_PlantData` (load it before
  `SegmentedMSR.mo`). Runners take `SCENARIOS` / `STEP_PCM` / frequency-grid
   defaults from `data/scenarios/`. Structural wrappers (`numUhxSteps` and
   friends) are emitted into the run directory with
   `python3.12 -m helpers.emit_scenario_wrapper`. The wrapper model name is
   a sanitized single-token stem: after mapping `.`/`/`/`\` in the
   scenario/case id to `_`, the stem must match `[A-Za-z0-9_]+`; ids
   carrying spaces, quotes, newlines, or other non-identifier characters
   are refused with `ValueError`. The mapping deliberately collapses ids
   differing only in those characters — `a.b`, `a/b`, and `a_b` all yield
   the stem `a_b` — so callers emitting several wrappers into one
   directory must key artifacts by wrapper name, not by raw scenario id.
   Segmented startup wrappers on the four trim rigs (every segmented
   startup scenario, the base `startup` included) also carry the lumped
   startup semantics: an isothermal start at `init.per_core` (asserted equal
   to the deck's `ssReferenceTemperature`), `heatLossEnabled` from
   `forcing.heat_loss`, and the staircase re-referenced to the critical
   position at the pump flow in effect via the closed-form circulation loss
   of the rig's precursor network (see `startup/README.md`); the
   outer-annulus wrapper bases keep their own initialization policies.
   Regenerate plant packages
  with `python3.12 -m helpers.emit_modelica_plant` (add `--catalog` to refresh
  `../data/CATALOG.md`, `--check` locally). `--plant <id>` emits another plant
  deck to `../core/generated/<id>/` and `../data/plants/<id>/CATALOG.md`
  (same file and package names, so a run directory loads whichever plant was
  copied into it); `--check` compares one plant at a time. The segmented
  runners select the plant with `--plant` (see `../data/plants/msrr_1gw/README.md`).
- `legacy/`: archived scripts for retired MSRE-based models; reference only.

## Note

Active nominal-trim frequency workflows are in `../freq/`.
Shared active Modelica sources are in `../core/`.
