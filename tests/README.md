# tests

Automated integration and unit tests for the Modelica model sources and the
Python workflows.

## Contents

- `test_msrrv2.py`: Runs short OpenModelica simulations against `core/MSRR.mo` for both `1r` and `9r` variants, validates startup/nominal behavior and key output signals, and asserts initialization states:
  - startup: initial neutron population at the configured numerical floor (`nFloor`), thermal equilibrium, negative initial external reactivity.
  - nominal/frequency: steady-state nonzero neutron level, setpoint-based initial temperatures, and near-equilibrium startup for frequency-ready overrides (including detailed HX-state initialization checks at `power=0.1` and `power=1.0`).
- `test_freq_scripts.py`: Unit tests for frequency-workflow helper logic and CLI argument validation.
- `test_watch_omc_gw_collect.py`: Offline pins for the omc_gw frequency watcher (`freq/watchOmcGwCollect.py`): rsync provenance-sidecar filter, 4-tuple watch-case key, and `--once`/polling exit semantics, all on stubbed SSH/collect seams (no omc, no network).
- `test_freq_estimator.py`: Synthetic accuracy tests for the shared least-squares frequency estimator (`freq._common.fit_sine_least_squares`), with known amplitudes, phases, offsets, and drift signals.
- `test_run_results.py`: Provenance contract of `helpers.run_results` -- fingerprints, validators, quarantine lifecycle.
- `test_freq_reuse_provenance.py`: Fingerprint-gated reuse and output lifecycle of the frequency runner on tiny fake CSVs (no omc).
- `test_freq_fr_protocol.py`: Frequency-response measurement protocol (no omc): the committed gain prior and its interpolation, the target-swing amplitude rule, the settling discard (cap and fallback) and stop times, the fit-start authority recorded in the sweep request and case manifests, the collector's two-halves convergence check, `freq.verify_campaign` convergence flags, and the half-amplitude linearity helper.
- `test_startup_transient_provenance.py`: Quarantine-before-launch and validated outputs for the startup and transient runners (no omc).
- `test_setpoint_convergence.py`: Late-window convergence qualification and fingerprint-gated reuse of the setpoint generator (no omc).
- `test_make_source_archive.py`: Archive-unpack tests for `helpers/make_source_archive.py`: member set equals tracked files minus junk exclusions, no `00runs/`, `__pycache__`, `.pytest_cache`, `*.o`, or `*_res.csv` members, manifest SHA-256/size entries verified against unpacked bytes, deterministic rebuilds, and refusal to write into published `00runs/` result trees. An unpack smoke check extracts the archive into scratch (no `.git`, `00runs/`, bytecode, `*_res.csv`, or executables in the extract), imports `helpers.run_results`, `freq._common`, and `core.init` from it, confirms the default quarantine lands under the extract's own `00runs/tmp/quarantine/`, and runs the Python-only subset documented under "Running tests"; the embedded `MANIFEST.json` keeps its `git_commit` as packed. Both repack refusals are pinned: `--repo-root` that is not the git toplevel, and running the packer from an unpacked archive.
- `test_modelica_ssh_gateway.py`: Unit tests for the remote Modelica SSH gateway scheduler, including per-worker task-capacity accounting.
- `test_setpoint_scripts.py`: Unit tests for setpoint-table recovery (`--reuse_csv` fallback, row merge, continuation override rollback, timeout log writes).
- `test_startup_scripts.py`: Unit tests for the startup runner, including timeout log writes.
- `test_scratch_layout.py`: Locks pytest `tmp_path` / `TMPDIR` under `00runs/tmp/` (see `conftest.py`).
- `test_plant_data.py`: Digit-parity of `data/plants/msrr/` YAML against generated `PlantData` and `SegmentedMSR.Reactors` PlantData binds, plus lumped 1R/9R bind-string checks and emitter determinism for `core/generated/*.mo`.
- `test_scenario_yaml.py`: Scenario YAML is the source of startup/transients/frequency runner tables; default CLI values match the decks; structural wrappers carry YAML UHX schedules, including segmented to-power wrappers.

## Test markers

Markers are registered in `pyproject.toml` together with
`--strict-markers`, so a misspelled marker fails collection instead of
warning silently (built-in marks such as `parametrize`, `skipif`, and
`xfail` are unaffected).

| Marker | Meaning |
|---|---|
| `unit` | Pure-Python tests needing neither OpenModelica nor network access. |
| `static_modelica` | Checks Modelica sources statically (parsing/text); no simulation. |
| `omc_check` | Executes QA self-check models through `omc`. |
| `omc_short` | Short OpenModelica simulations (minutes scale). |
| `omc_long` | Long or heavy OpenModelica simulation campaigns. |
| `omc_parity` | Segmented-core kelvin-parity replay (strict bars with release-required zero skips on the pinned `v1.27.0-cmake` build; loosened cross-toolchain bars on other builds). |
| `ssh` | Remote-dispatch gateway logic with offline stubs; no cluster contact. |
| `docs` | Documentation-consistency checks. |
| `source_checkout` | Requires a real Git checkout; tests that need the live checkout skip with a precise reason in a gitless source-archive extract. |

The `source_checkout` inventory is `--markers`-derived, not enumerated
here: run `python3.12 -m pytest tests/ --markers` (the marker is
registered in `pyproject.toml`) and grep `tests/test_*.py` for
`pytest.mark.source_checkout` / `pytestmark` to list the currently
marked modules (for example the archive-packer tests in
`test_make_source_archive.py` and
`test_release_denylist.py::TestCheckoutStateInvariants`, which re-pack
the live checkout through git, plus the AFW gate batteries
`test_afw_gate_adversarial.py`, `test_afw_gate_parity_land_gate.py`,
`test_afw_gate_r26_placeholders.py`, the git-history pins in
`test_plant_data.py`, `test_power_tags.py`,
`test_poison_config_refusal.py`, `test_poison_p2_five_state.py`,
`test_poison_p5_maturity_governance.py`, `test_p2_metadata_contract.py`,
`test_rev021_p16_data_schema.py`, `test_rebaseline_evidence.py`,
`test_outer_fuel_annulus_fission_p5_contract.py`,
`test_release_gate_provenance.py`, `test_segmented_msr.py`, and
`test_yaml_validation.py`). Any test needing the live checkout carries
the marker and skips with the precise reason from
`helpers.git_checkout.git_checkout_skip_reason`, so the documented
consumer run below excludes them up front via
`-m '(unit or ssh) and not source_checkout'`.

Currently tagged: `test_plant_data.py`, `test_scenario_yaml.py`, `test_freq_estimator.py`, `test_run_results.py`,
`test_freq_reuse_provenance.py`, `test_freq_collect_provenance.py`,
`test_freq_fr_protocol.py`,
`test_startup_transient_provenance.py`, `test_setpoint_convergence.py`,
and `test_make_source_archive.py` carry `[pytest.mark.unit]`;
`test_modelica_ssh_gateway.py` and `test_watch_omc_gw_collect.py`
carry `[pytest.mark.ssh]`; and `tests/test_kelvin_parity.py` tags its
strict-replay test `[pytest.mark.omc_parity]`.

The omc-executing markers (`omc_check`, `omc_short`, `omc_long`,
`omc_parity`) sit on individual tests in many modules; list them with
`grep -l 'mark.omc_' tests/test_*.py`. In `test_segmented_msr.py`, for
example, 29 QA self-check execution test functions carry `omc_check` and 7
short-simulation test functions carry `omc_short`, while its rig smoke runs
and `checkModel` tests stay untagged behind a per-test omc `skipif`. Other
omc modules (for example `test_msrrv2.py`) skip themselves when omc is
absent through a module-level `skipif` marker, and omc-free modules such
as the segmented-mode runner suites (fake executables, no simulation) carry
no marker; both are intentionally left untagged, and retagging them belongs
to the deferred maintainability refactor of the large test modules.
Untagged does not mean unexercised: a plain
suite run collects everything locally, and the removed
continuous-integration fast job used to run everything except the four
omc-executing markers (untagged omc-gated modules skip themselves when
`omc` is absent); see "Continuous integration" below.

Marker selection notes:

```bash
python3.12 -m pytest tests/ -m unit -q       # pure-Python units currently tagged
python3.12 -m pytest tests/ -m ssh -q        # gateway client-logic tests
python3.12 -m pytest tests/ -q               # complete suite
```

Because `-m` filtering deselects modules that carry no marker at all, use
the plain form for full coverage. Note that `-m unit` alone also selects
`test_make_source_archive.py`, whose packer tests re-pack the live
checkout through git: from a real checkout they run as part of the suite;
from an unpacked source archive they skip with a reason (the
`source_checkout` marker), so the documented consumer run below excludes
them up front.

## Continuous integration

There is no GitHub CI: the `.github/workflows/` jobs (the push/PR fast
`tests.yml` job and the manual-dispatch `release-verification.yml` /
`release-distribution.yml`) were removed by owner decision 2026-09-16,
and local testing is the validation path. The commands the fast job used
to run are exactly the local ones above: the marker-filtered subset

```bash
python -m pytest tests/ -ra -q -m "not omc_check and not omc_short and not omc_long and not omc_parity"
```

which deselects every omc-executing marker (untagged omc-gated modules
skip themselves when `omc` is absent), `python -m helpers.emit_modelica_plant --check`
(generated-file drift), and the `--help` smoke over the console entry
points declared in `pyproject.toml`. The release checks (OpenModelica,
LaTeX, the release gate) run locally via `python3.12 -m
helpers.release_verification` and `python3.12 -m helpers.wheel_check`
when a release is cut, not on every push. Do not re-create CI
configuration (see AGENTS.md, "No GitHub CI").

## Running tests

From repository root:

```bash
python3.12 -m pytest tests/ -q
```

Tests that need OpenModelica run when `omc` is available and skip
automatically otherwise; skips never fail the session.

Scratch (`tmp_path`, OpenModelica compile dumps) is routed to `00runs/tmp/`
by `tests/conftest.py`. Do not use `/tmp`. Each session gets its own
`--basetemp` directory under `00runs/tmp/pytest/`; a green session deletes
only that directory, and a failed run keeps it for inspection, so concurrent
sessions never remove each other's scratch.

### From an unpacked source archive

The archived tree is not a git checkout: no `.git` entry ships in the
archive, so the archive-packer tests in `test_make_source_archive.py`
(which re-pack through `git ls-files` and `git rev-parse`) cannot run from
an extract. They carry the registered `source_checkout` marker and skip
with a precise reason whenever they are selected from a gitless tree; the
subset below excludes them up front, so the documented consumer run stays
green without skips. Every other tagged suite is pure Python, and the
default quarantine and scratch locations resolve inside the extract
(`pyproject.toml` + `core/` + `helpers/` discovery). Run the Python-only
subset from the extract root:

```bash
python3.12 -m pytest tests/ -m '(unit or ssh) and not source_checkout' -p no:cacheprovider -q
```

This is the gitless consumer subset the release gate runs from its own
verification extract (`CONSUMER_SUBSET_ARGS` in `helpers/release_gate.py`);
`tests/test_release_gate.py` (`TestConsumerSubsetWiring`) parses this
command and compares it against the gate's argument tuple, so the two
cannot drift apart.

Limits of this subset:

- The exclusion is by marker, not by path: `source_checkout` is
  registered in `pyproject.toml` and names the property that makes the
  packer tests unrunnable from an extract -- they re-pack the live tree
  through git. Registration is not a `--strict-markers` requirement: an
  unregistered marker name inside a `-m` expression does not fail under
  `--strict-markers`; the marker route is justified by semantic
  selection, not by a strict-markers constraint.
- Plain `-m 'unit or ssh'` from an extract still selects the packer tests;
  there they skip with the gitless reason instead of failing, so a full
  `pytest tests/` from an extract stays green, while the subset above
  keeps the documented consumer run skip-free.
- The full suite belongs to a Git clone of the repository: the
  `source_checkout`-marked packer tests run only from a real checkout.
- One unit-marked static pin self-skips from an extract with a named
  reason that states the lost pin: `test_outer_fuel_annulus_p6.py::
  test_loop_component_energy_equations_unchanged_vs_parent` compares the
  four HeatTransport component energy-equation bodies (Pipe / DHRS /
  MixingPot / HeatExchanger, squashed) between the two fixed historical
  blobs `8b11f52:core/SegmentedMSR.mo` and `9f595ce:core/SegmentedMSR.mo`
  through `git show` — the invariant "the P6 phase never rewrote these
  component bodies". A shipped archive cannot serve either blob, so the
  no-rewrite pin is lost on gitless consumers.
- `-p no:cacheprovider` keeps a `.pytest_cache` directory out of the
  extract.
- Anything needing OpenModelica is out of reach here, and packing a new
  archive from the extract needs a real git clone.
