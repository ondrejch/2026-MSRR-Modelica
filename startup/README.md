# MSRR Startup simulations

Startup simulation runners and plotting tools.

## Key files

- `runMSRR.py`: Python runner for startup scenarios; supports `--core_model 1r|9r`.
- Scenarios in `runMSRR.py`: `startup`, `startup_to_100kw`, `startup_to_1mw`.
- `runMSRR.mos`: simple OpenModelica startup example loading files from `../core/`.
- `plotStartUp.py`, `plotStartUpWithSource.py`, `plotStartUpTo100kW.py`: startup plotting utilities.
- `plotStartUpTo1MW.py`: startup-to-1MW plotting utility (includes Phase 5 markers).
- `plotApproachToCriticalityPhase4.py`: Phases 1-4 approach-to-criticality plotting utility.
- `plotStartUp.m`: legacy MATLAB/Octave plotting script.

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
