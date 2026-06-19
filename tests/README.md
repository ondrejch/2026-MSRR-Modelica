# tests

Automated integration tests for Modelica simulations.

## Contents

- `test_msrrv2.py`: Runs short OpenModelica simulations against `core/MSRR.mo` for both `1r` and `9r` variants, validates startup/nominal behavior and key output signals, and asserts initialization states:
  - startup: initial neutron population at the configured numerical floor (`nFloor`), thermal equilibrium, negative initial external reactivity.
  - nominal/frequency: steady-state nonzero neutron level, setpoint-based initial temperatures, and near-equilibrium startup for frequency-ready overrides (including detailed HX-state initialization checks at `power=0.1` and `power=1.0`).
- `test_freq_scripts.py`: Unit tests for frequency-workflow helper logic and CLI argument validation.
- `test_modelica_ssh_gateway.py`: Unit tests for the remote Modelica SSH gateway scheduler, including per-worker task-capacity accounting.

## Running tests

From repository root:

```bash
python3.12 -m pytest tests/test_msrrv2.py -q
```

Tests are skipped automatically when `omc` is not available on `PATH`.
