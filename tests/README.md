# tests

Automated integration and unit tests for the Modelica model sources and the
Python workflows.

## Contents

- `test_msrrv2.py`: Runs short OpenModelica simulations against `core/MSRR.mo` for both `1r` and `9r` variants, validates startup/nominal behavior and key output signals, and asserts initialization states:
  - startup: initial neutron population at the configured numerical floor (`nFloor`), thermal equilibrium, negative initial external reactivity.
  - nominal/frequency: steady-state nonzero neutron level, setpoint-based initial temperatures, and near-equilibrium startup for frequency-ready overrides (including detailed HX-state initialization checks at `power=0.1` and `power=1.0`).
- `test_freq_scripts.py`: Unit tests for frequency-workflow helper logic and CLI argument validation.
- `test_freq_estimator.py`: Synthetic accuracy tests for the shared least-squares frequency estimator (`freq/_common.fit_sine_least_squares`), with known amplitudes, phases, offsets, and drift signals. No OpenModelica needed.
- `conftest.py`: Routes pytest/omc scratch into `00runs/tmp` (never `/tmp` or the repo root).

## Running tests

From repository root:

```bash
python3.12 -m pytest tests/test_msrrv2.py -q
```

Tests are skipped automatically when `omc` is not available on `PATH`.

The pure-Python suites run without OpenModelica:

```bash
python3.12 -m pytest tests/test_freq_scripts.py tests/test_freq_estimator.py -q
```
