# Remote Modelica Gateway

SSH-native helpers for dispatching OpenModelica and Python workflow commands to
remote worker nodes without opening network ports or running a persistent
service.

System-specific settings are intentionally kept out of the repository. The
gateway reads user configuration from:

- `$MSRR_OMC_GW_CONFIG`, if set
- otherwise `~/.config/msrr_omc_gw/config.json`

A template is provided in [config.example.json](/home/o/git/SMD-MSRR-dev/helpers/omc_gw/config.example.json).

## Files

- `modelica_ssh_agent.py`: runs on the login node and launches jobs on workers
- `modelica_ssh_client.py`: local/client-side CLI and Python API
- `omc_gw_config.py`: shared user-config loader
- `config.example.json`: non-site-specific config template

## Configuration

Typical config keys:

- `host`: SSH login host used by the client
- `workers`: worker-node hostnames used by the agent
- `max_tasks_per_worker`: task-slot capacity per worker
- `modelica_bin_dir`: optional OpenModelica bin directory prepended to worker `PATH`
- `jobs_root`: shared directory for job metadata and logs
- `remote_repo_relpath`: path to this repo under the remote user's home directory
- `agent_command`: optional explicit override for the remote agent command
- `ssh_options`: optional extra SSH arguments

`agent_command` and `remote_repo_relpath` are alternatives. If `agent_command`
is omitted, the client uses `remote_repo_relpath` when configured, otherwise it
falls back to a path derived from the local repo location relative to the local
home directory.

## Example setup

Copy the template outside the repo:

```bash
mkdir -p ~/.config/msrr_omc_gw
cp helpers/omc_gw/config.example.json ~/.config/msrr_omc_gw/config.json
```

Then edit `~/.config/msrr_omc_gw/config.json` with site-specific values.

## Task accounting

Each submitted job reserves `--tasks` slots on a worker. Placement only occurs
when:

```text
active_reserved_tasks + requested_tasks <= max_tasks_per_worker
```

Use `--tasks` to match the actual parallelism of the submitted command. For
example, if the remote command runs `freq/runFreqNominalParallel.py --n_jobs 8`,
submit it with `--tasks 8`.

The worker wrapper exports `MODELICA_SSH_TASKS` and defaults common math-library
thread counts to `1` so many independent OpenModelica jobs do not silently
oversubscribe a node.

## Typical usage

Check current worker capacity:

```bash
python3 -m helpers.omc_gw.modelica_ssh_client capacity
```

Submit a single OpenModelica run:

```bash
python3 -m helpers.omc_gw.modelica_ssh_client submit \
  --workdir ~/path/to/SMD-MSRR-dev/core \
  --name setpoint_case \
  --tasks 1 \
  --cmd "omc --showErrorMessages runModelica.mos"
```

Submit a frequency workflow that internally uses eight parallel jobs:

```bash
python3 -m helpers.omc_gw.modelica_ssh_client submit \
  --workdir ~/path/to/SMD-MSRR-dev \
  --name freq_case \
  --tasks 8 \
  --cmd "python3.12 freq/runFreqNominalParallel.py --core_model 1r --power 1.0 --n_jobs 8"
```

Inspect job state:

```bash
python3 -m helpers.omc_gw.modelica_ssh_client status <job_id>
python3 -m helpers.omc_gw.modelica_ssh_client result <job_id>
python3 -m helpers.omc_gw.modelica_ssh_client logs <job_id> --follow
```

All CLI flags override config-file values.

## Python API

```python
from helpers.omc_gw.modelica_ssh_client import ModelicaSshClient

client = ModelicaSshClient(host="your-login-host")
job = client.submit(
    "python3.12 freq/runFreqNominalParallel.py --core_model 9r --power 1.0 --n_jobs 16",
    workdir="~/path/to/SMD-MSRR-dev",
    tasks=16,
)
final = client.wait(job["job_id"])
```

## Agent vs client config

The same config schema can be used on both sides, but the relevant keys differ:

- client side: `host`, `remote_repo_relpath` or `agent_command`, `jobs_root`, `ssh_options`
- login-node agent side: `workers`, `max_tasks_per_worker`, `modelica_bin_dir`, `jobs_root`, `ssh_options`

If a key is not configured, pass the corresponding CLI flag explicitly.
