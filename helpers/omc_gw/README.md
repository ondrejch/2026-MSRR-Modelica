# Remote Modelica Gateway

SSH-native helpers for dispatching OpenModelica and Python workflow commands to
remote worker nodes without opening network ports or running a persistent
service.

System-specific settings are intentionally kept out of the repository. The
gateway reads user configuration from:

- `$MSRR_OMC_GW_CONFIG`, if set
- otherwise `~/.config/msrr_omc_gw/config.json`

A template is provided in [config.example.json](config.example.json).

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
- `agent_command` (also `remote_agent_command`): optional explicit override for the remote agent command
- `ssh_options`: optional extra SSH arguments

`agent_command` and `remote_repo_relpath` are alternatives. If `agent_command`
is omitted, the client uses `remote_repo_relpath`. If neither is set, the
client raises an error rather than guessing a remote path from the local
checkout.

Value validation. `host` and `workers` entries must not be empty, must not
start with `-`, and must not contain whitespace or control characters;
invalid values are rejected before any SSH command is built.
`remote_repo_relpath` must be a path relative to the remote home directory
containing only letters, digits, `.`, `_`, `/`, `-`, and `~`; absolute paths
and `..` segments are rejected (use `agent_command` for absolute remote
paths). `agent_command` is a trusted shell fragment: the remote shell
executes it verbatim, so quotes and `$HOME`/`~` expansion are honored and the
`python3 "$HOME/..."` form works. Control characters, including newlines,
are rejected, and every argument the client appends after the fragment is
shell-quoted automatically.

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

## Job records

Each submitted job gets a directory under `jobs_root` named by its `job_id`,
which must conform to the generator format `YYYYMMDDTHHMMSSZ-XXXXXXXX`
(eight digits, `T`, six digits, `Z`, a dash, and eight lowercase hex
digits). Other values are rejected: the agent answers `ok: false` and the
client exits with status 2.

On the shared jobs filesystem, `meta.json` (which embeds the submitted
command) and `command.sh` are created owner-only (mode 0600); the worker
`run.sh` wrapper remains mode 0700.

## Orphan-job reaping

The worker wrapper touches a `heartbeat` file in the job directory every 60 s
while the command runs. A job that still looks `RUNNING` but has no heartbeat
activity for `1800` seconds (default) is classified as `FAILED` and records an
`orphaned_at` marker, which releases its reserved task slots. This covers
worker reboots and remote launches that never actually started. The threshold
also applies to jobs with no heartbeat at all, measured from their
`submitted_at` timestamp, so wedged submissions cannot block capacity forever.

Tune or disable with:

```bash
export MODELICA_SSH_STALE_HEARTBEAT_SECONDS=3600   # default: 1800
export MODELICA_SSH_STALE_HEARTBEAT_SECONDS=0      # disable reaping entirely
```

The comparison uses the shared filesystem mtime against the login node clock,
so keep NTP sane across the cluster. Terminal states (`exit_code` present)
always take precedence over heartbeat staleness.

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
