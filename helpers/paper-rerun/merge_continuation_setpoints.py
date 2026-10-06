#!/usr/bin/env python3
"""Merge continuation setpoint rows into the main campaign's tables.

One-shot, idempotent merge for the documented continuation campaign
``paper-rerun-review-2026-09-continuation`` (rerun guide section 4.2).
For each core, inserts the qualified continuation rows into the main
campaign setpoint table at their power. Fails if a main-table row for
the same power already exists, if the schemas differ, or if any
continuation row is not ``qualified=1``. Writes ``MERGE_NOTES.json``
into the continuation setpoints directory as the merge record.

Run on the cluster login node (the campaign trees live under
``~/git/SMD-MSRR-dev/00runs`` on the shared home storage).
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import subprocess
from typing import Any

REPO = os.path.expanduser("~/git/SMD-MSRR-dev")
RUNS = os.path.join(REPO, "00runs")
MAIN = os.path.join(RUNS, "paper-rerun-review-2026-09", "setpoints")
CONT = os.path.join(RUNS, "paper-rerun-review-2026-09-continuation", "setpoints")
CORES = ("1r", "9r")


def read_table(path: str) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fields, rows


def main() -> int:
    notes: dict[str, Any] = {
        "merged_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "commit": subprocess.check_output(
            ["git", "-C", REPO, "rev-parse", "HEAD"], text=True
        ).strip(),
        "continuation_campaign": "paper-rerun-review-2026-09-continuation",
        "continuation_jobs": {
            "1r": "20260901T012943Z-6679770d",
            "9r": "20260901T012944Z-b1344544",
        },
        "cores": {},
    }
    for core in CORES:
        main_path = os.path.join(MAIN, f"setpoints_{core}.csv")
        cont_path = os.path.join(CONT, f"setpoints_{core}_continuation.csv")
        fields_main, rows_main = read_table(main_path)
        fields_cont, rows_cont = read_table(cont_path)
        if fields_main != fields_cont:
            raise SystemExit(f"{core}: schema mismatch between main and continuation tables")
        main_powers = {float(row["power"]) for row in rows_main}
        added: list[float] = []
        for row in rows_cont:
            power = float(row["power"])
            if row.get("qualified") != "1":
                raise SystemExit(f"{core}: continuation row power={power} is not qualified=1")
            if power in main_powers:
                raise SystemExit(f"{core}: main table already has a row for power={power}")
            rows_main.append(row)
            added.append(power)
        rows_main.sort(key=lambda row: float(row["power"]))
        tmp_path = main_path + ".merge.tmp"
        with open(tmp_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields_main)
            writer.writeheader()
            writer.writerows(rows_main)
        os.replace(tmp_path, main_path)
        check_fields, check_rows = read_table(main_path)
        if len(check_rows) != len(rows_main) or any(
            row.get("qualified") != "1" for row in check_rows
        ):
            raise SystemExit(f"{core}: merged table verification failed")
        notes["cores"][core] = {
            "rows_before": len(rows_main) - len(added),
            "rows_added": sorted(added),
            "rows_after": len(check_rows),
            "all_qualified": True,
        }
        print(f"{core}: {len(rows_main) - len(added)} + {len(added)} rows -> {len(check_rows)} "
              f"(added powers: {sorted(added)})")
    notes_path = os.path.join(CONT, "MERGE_NOTES.json")
    with open(notes_path, "w") as handle:
        json.dump(notes, handle, indent=2)
        handle.write("\n")
    print(f"merge record: {notes_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
