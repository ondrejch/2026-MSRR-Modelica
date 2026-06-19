# Helpers

Legacy/auxiliary scripts, including depletion/sensitivity tooling and older depletion-coupled frequency analysis scripts.

## Scripts in this directory

- `runDepl.py`: depletion-oriented simulation batch generation/execution.
- `runDeplNfreq.py`: older depletion-coupled frequency sweep driver.
- `collectFreq.py`: older depletion-coupled frequency response post-processing.
- `runSensitivity.py`: randomized parameter sensitivity run generator/executor.
- `collectSen.py`: post-processing for sensitivity runs.
- `runSIm2time.py`: timing/throughput helper for repeated simulations.
- `kin_dyn_edit.txt`: legacy depletion/frequency parameter table used by older scripts.
- `omc_gw/`: SSH-native remote execution helpers for dispatching Modelica workflows to
  shared worker nodes with per-node task-capacity tracking and user-level
  site configuration outside the repository.

## Note

Active nominal-trim frequency workflows are in `../freq/`.
Shared active Modelica sources are in `../core/`.
