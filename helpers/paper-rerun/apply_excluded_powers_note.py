#!/usr/bin/env python3
"""Write metadata/excluded_powers.json into the synced campaign tree and
regenerate the inventory (campaign_summary.json + SHA256SUMS +
completed_utc.txt), then incrementally re-sync the changed files locally.

Follow-up to submit_final_tail_excl_1e5.py: its inline note-write used a
literal ``~`` in ``Path(...)`` (no shell expansion inside python -c), so
the note landed in the job's scratch cwd instead of the campaign's
``metadata/``.  This fix writes the note with ``os.path.expanduser``,
re-runs the driver's inventory command verbatim, and re-rsyncs.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from helpers.omc_gw.modelica_ssh_client import (
    _default_agent_command,
    ModelicaSshClient,
)
from helpers.omc_gw.omc_gw_config import get_config_str, load_user_config

sys.path.insert(0, "helpers/paper-rerun")
from submit_final_tail_excl_1e5 import (
    CAMPAIGN,
    CAMPAIGN_DIR,
    EXCLUDED_POWERS_NOTE,
    LOCAL_DEST,
    REMOTE,
    inventory_command,
)

import json


def note_write_command() -> str:
    note_json = json.dumps(EXCLUDED_POWERS_NOTE, indent=2, sort_keys=True)
    note_path = f"{CAMPAIGN_DIR}/metadata/excluded_powers.json"
    py = f"PYTHONPATH={REMOTE} python3.12"
    write_note = (
        "import os; from pathlib import Path; "
        f"note = {json.dumps(note_json)}; "
        f"p = Path(os.path.expanduser({note_path!r})); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(note); print('wrote', p, len(note), 'bytes')"
    )
    return f"{py} -c {shlex.quote(write_note)}"


def main() -> int:
    config = load_user_config()
    client = ModelicaSshClient(
        host=get_config_str(config, "host"),
        agent_command=_default_agent_command(config),
        ssh_bin=get_config_str(config, "ssh_bin") or "ssh",
        ssh_options=tuple(get_config_str_list(config)),
        jobs_root=get_config_str(config, "jobs_root"),
    )

    remote_script = "set -euo pipefail\n" + "\n".join(
        [note_write_command(), *inventory_command().splitlines()[1:]]
    ) + "\n"
    cmd = [client.ssh_bin, *client.ssh_options, client.host, "bash -s"]
    print("$", " ".join(cmd[:5]), "... (note + inventory, ~10 min)", flush=True)
    proc = subprocess.run(cmd, input=remote_script, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"remote note+inventory failed rc={proc.returncode}")

    ssh_command = f"{client.ssh_bin} {' '.join(client.ssh_options)}"
    rsync = [
        "rsync", "-az", "--partial", "--info=stats1", "-e", ssh_command,
        f"{client.host}:{CAMPAIGN_DIR.rstrip('/')}/", f"{LOCAL_DEST}/",
    ]
    print("$", " ".join(rsync), flush=True)
    proc = subprocess.run(rsync, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"rsync failed rc={proc.returncode}")
    print("note + inventory + re-sync complete")
    return 0


def get_config_str_list(config):
    from helpers.omc_gw.omc_gw_config import get_config_list
    return get_config_list(config, "ssh_options") or ()


if __name__ == "__main__":
    raise SystemExit(main())
