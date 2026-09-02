# MSRR Startup simulations

Startup simulation runners and plotting tools.

## Key files

- `runMSRR.py`: Python runner for startup scenarios; supports `--core_model 1r|9r`.
- Scenarios in `runMSRR.py`: `startup`, `startup_to_100kw`, `startup_to_1mw`.
- `runMSRR.mos`: simple OpenModelica startup example loading files from `../core/`.
- `plotStartUp.py`, `plotStartUpWithSource.py`, `plotStartUpTo100kW.py`: startup plotting utilities.
- `plotStartUpTo1MW.py`: startup-to-1MW plotting utility (includes Phase 5 markers).
- `plotApproachToCriticalityPhase4.py`: Phases 1-4 approach-to-criticality plotting utility.

## Startup Sequence (Default Paths)

Run (example: startup-to-100kW, 1r):

```bash
python3.12 -m startup.runMSRR --core_model 1r --scenario startup_to_100kw
```

Default run path pattern:

```bash
00runs/startup-<scenario>-<core_model>
```

Example run directory for the command above:

```bash
00runs/startup-startup_to_100kw-1r
```

Plot from the same default run definition (no explicit CSV path needed):

```bash
python3.12 -m startup.plotStartUpTo100kW --core_model 1r
```

## Run provenance for result CSVs

Every startup result CSV carries two provenance sidecars written beside it:
`<prefix>_res.manifest.json` (SHA-256 digests of the loaded Modelica sources,
package/model identity, scenario/core selection, the complete normalized
override set including `--max_step_size` and any `--extra_simflags`
overrides, solver/tolerance/time span/output grid, revision-control state,
and interpreter/OpenModelica versions) and `<prefix>_res.validation.json`
(outcome of the output checks below). A single SHA-256 fingerprint of that
canonical request description — hashed source files and request fields,
launch timestamp excluded — is stored in each manifest sidecar, so results
carry their own exact provenance.

Before anything launches, prior artifacts tied to the result path (the CSV,
any leftover temporary file, stale sidecars) are **quarantined** to
`00runs/tmp/quarantine/<utc-stamp>/` instead of being silently deleted, so a
stale file can never be mistaken for the current run's output. Startup does
not reuse prior results; every invocation reruns from scratch. A successful
rerun must pass validation — modification time no earlier than this run's
launch instant, nonempty header with data rows, monotonically nondecreasing
`time`, finite values in required columns, rows as wide as the header, and a
final sample at or above 99.5 % of the requested stop time — before both
sidecars are written; failed outputs exit nonzero with the failing check
names printed. Concurrent launches of the same result slot are serialized
by an exclusive file claim, and `--claim_timeout_s` bounds the wait for a
busy slot (default: wait indefinitely; expiry raises
`ResultSlotClaimTimeout` naming the current holder).

## Notes

- Core Modelica files now live in `../core/` (`SMD_MSR_Modelica.mo`, `MSRR.mo`).
- Startup models use startup-specific initialization (not nominal-trim defaults):
  - `powerLevel = 0`
  - explicit startup equilibrium temperatures defined in `core/MSRR.mo`
    (`570 C` for `1r`; `552.835... C` for `9r`)
  - startup external source strength default: `1e8 n/s`
  - pulsed neutron-source schedule:
    - on: `[3600, 47400)`, `[54000, 69000)`, `[75600, 87000)`, `[93600, 101400)` s
- External startup reactivity schedule is applied from `t = -1 s` so startup begins subcritical at `t = 0`.
- Startup reactivity terminology in plots:
  - plotted external reactivity = commanded external reactivity + `rho_0`
    (with the same flow-gating logic used in `mPKE`)
  - model reactivity = `pke.reactivity`
  - total reactivity = `rho_total = pke.reactivity - rho_0dyn`
- New startup plots (`plot_startup_phase1to4.png`, `plot_startup_to1MW.png`) use the same neutron-population variable (`mpke.n_population.n`) for consistency.
- `runMSRR.py` writes run artifacts/logs under `00runs/startup-<scenario>-<core_model>` by default.
- Startup plotting scripts default to the same run-definition directory pattern and write outputs there unless overridden.
- `runMSRR.py` is startup-only and rejects nominal-trim frequency models
  (`*NominalTrim*`) to keep startup and frequency initialization paths separate.
- OpenModelica build artifacts, logs, and result CSVs now land in the selected
  `00runs/startup-<scenario>-<core_model>` directory instead of the tracked `startup/` source folder.
