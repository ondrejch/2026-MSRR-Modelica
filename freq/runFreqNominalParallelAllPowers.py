#!/usr/bin/env python3
"""Run nominal frequency sweeps for all powers in a steady-state table."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from .paths import (
        POWER_TAG_FORMAT_VERSION,
        check_power_tag_collisions,
        default_freq_core_dir,
        default_freq_plot_dir,
        make_power_tag,
    )
except ImportError:
    from paths import (
        POWER_TAG_FORMAT_VERSION,
        check_power_tag_collisions,
        default_freq_core_dir,
        default_freq_plot_dir,
        make_power_tag,
    )

try:
    from helpers.power_tags import parse_finite_number, require_finite
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.power_tags import parse_finite_number, require_finite


def default_steady_state_table(core_model: str) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "core" / "init" / f"setpoints_{core_model}.csv"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description=(
            "Run runFreqNominalParallel.py for each power in a steady-state table, "
            "then run collectFreqNominalParallel.py --plot and copy plots."
        )
    )
    parser.add_argument(
        "--steady_state_table",
        type=Path,
        default=None,
        help=(
            "Path to steady-state table CSV "
            "(default: core/init/setpoints_<core_model>.csv)"
        ),
    )
    parser.add_argument(
        "--base_dir",
        type=Path,
        default=None,
        help=(
            "Base output directory; each power gets a subdirectory under this path "
            "(default: 00runs/freq/<core_model>)"
        ),
    )
    parser.add_argument(
        "--plot_copy_dir",
        type=Path,
        default=None,
        help=(
            "Directory where collected PNG plots are copied "
            "(default: 00runs/freq/plots/<core_model>)"
        ),
    )
    parser.add_argument(
        "--power_min",
        type=float,
        default=1e-3,
        help="Minimum table power to include (default: 1e-3)",
    )
    parser.add_argument(
        "--power_max",
        type=float,
        default=10.0,
        help="Maximum table power to include (default: 10.0)",
    )
    parser.add_argument(
        "--freq_min",
        type=float,
        default=1e-3,
        help="Minimum frequency in rad/s passed to runFreqNominalParallel.py (default: 1e-3)",
    )
    parser.add_argument(
        "--freq_max",
        type=float,
        default=10.0,
        help="Maximum frequency in rad/s passed to runFreqNominalParallel.py (default: 10)",
    )
    parser.add_argument(
        "--num_freq",
        type=int,
        default=80,
        help="Number of frequency points passed to runFreqNominalParallel.py (default: 80)",
    )
    parser.add_argument(
        "--sin_mag",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--sin_mag_auto",
        action="store_true",
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--sin_mag_ref",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--sin_mag_min",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--sin_mag_max",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--stop_time",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--ss_time",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--stop_time_mode",
        type=str,
        choices=("fixed", "min_cycles_after_ss"),
        default=None,
        help="Pass-through to runFreqNominalParallel.py (omitted when unset)",
    )
    parser.add_argument(
        "--min_cycles_after_ss",
        type=float,
        default=None,
        help="Pass-through to runFreqNominalParallel.py (omitted when unset)",
    )
    parser.add_argument(
        "--settle_rule",
        type=str,
        choices=("prior", "none"),
        default=None,
        help=(
            "Pass-through to runFreqNominalParallel.py (settling discard; "
            "omitted when unset, i.e. the runner default 'prior')"
        ),
    )
    parser.add_argument(
        "--sin_mag_auto_rule",
        type=str,
        choices=("target_swing", "inverse_power"),
        default=None,
        help=(
            "Pass-through to runFreqNominalParallel.py (--sin_mag_auto "
            "amplitude rule; omitted when unset, i.e. 'target_swing')"
        ),
    )
    parser.add_argument(
        "--n_jobs",
        type=int,
        default=None,
        help="Pass-through to runFreqNominalParallel.py and collectFreqNominalParallel.py",
    )
    parser.add_argument(
        "--collect_n_jobs",
        type=int,
        default=None,
        help="Override n_jobs only for collectFreqNominalParallel.py",
    )
    parser.add_argument(
        "--core_model",
        type=str,
        choices=("1r", "9r"),
        default="1r",
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--core_dir",
        type=Path,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default=None,
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--disable_steady_state_table",
        action="store_true",
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--cleanup_omc_artifacts",
        action="store_true",
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--reduced_csv_for_collect",
        action="store_true",
        help="Pass-through to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="Pass-through to runFreqNominalParallel.py (force rerun even if CSVs exist)",
    )
    parser.add_argument(
        "--heat_loss",
        type=int,
        choices=(0, 1),
        default=0,
        help="Select heat-loss-enabled rows from the steady-state table (0=no loss, 1=loss)",
    )
    parser.add_argument(
        "--python",
        type=str,
        default=sys.executable,
        help="Python executable used for subprocess calls (default: current interpreter)",
    )
    parser.add_argument(
        "--run_script",
        type=Path,
        default=script_dir / "runFreqNominalParallel.py",
        help="Path to runFreqNominalParallel.py",
    )
    parser.add_argument(
        "--collect_script",
        type=Path,
        default=script_dir / "collectFreqNominalParallel.py",
        help="Path to collectFreqNominalParallel.py",
    )
    args = parser.parse_args()
    if args.base_dir is None:
        args.base_dir = default_freq_core_dir(repo_root, core_model=args.core_model)
    if args.plot_copy_dir is None:
        args.plot_copy_dir = default_freq_plot_dir(repo_root) / args.core_model
    return args


def load_powers(table_path: Path, power_min: float, power_max: float, heat_loss: int) -> list[float]:
    if not table_path.exists():
        raise FileNotFoundError(f"Steady-state table not found: {table_path}")

    lo = require_finite(power_min, "power_min")
    hi = require_finite(power_max, "power_max")

    with table_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "power" not in reader.fieldnames:
            raise ValueError(
                f"Steady-state table {table_path} must contain a 'power' column."
            )

        has_heat_loss = "heatLossEnabled" in reader.fieldnames
        seen: set[str] = set()
        powers: list[float] = []
        for row in reader:
            raw = (row.get("power") or "").strip()
            if not raw:
                continue
            if has_heat_loss:
                raw_hl = (row.get("heatLossEnabled") or "").strip()
                if raw_hl == "":
                    continue
                try:
                    row_hl = int(float(raw_hl))
                except ValueError:
                    row_hl = 1 if raw_hl.lower() in ("true", "yes") else 0
                if row_hl != int(heat_loss):
                    continue
            try:
                power = parse_finite_number(raw, name="power")
            except ValueError as exc:
                raise ValueError(f"Steady-state table {table_path}: {exc}") from exc
            # Skip zero-power runs; frequency response is nonphysical at power=0.
            if abs(power) < 1e-12:
                continue
            if power < 0:
                raise ValueError(
                    f"Steady-state table {table_path}: negative power is "
                    f"nonphysical: {power}"
                )
            if power < lo or power > hi:
                continue
            key = f"{power:.12g}"
            if key in seen:
                continue
            seen.add(key)
            powers.append(power)

    powers.sort()
    check_power_tag_collisions(
        powers,
        tagger=make_power_tag,
        what="power_<tag>/ frequency result tree",
    )
    return powers


def run_command(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode


def copy_plots(
    results_dir: Path,
    power_tag: str,
    out_dir: Path,
    core_model: str,
) -> list[Path]:
    copied: list[Path] = []
    for plot_path in sorted(results_dir.glob("*.png")):
        target_name = (
            f"{plot_path.stem}_{core_model}_power_{power_tag}{plot_path.suffix}"
        )
        target = out_dir / target_name
        shutil.copy2(plot_path, target)
        copied.append(target)
    return copied


def main() -> int:
    args = parse_args()

    table_path = (
        args.steady_state_table.resolve()
        if args.steady_state_table is not None
        else default_steady_state_table(args.core_model).resolve()
    )
    run_script = args.run_script.resolve()
    collect_script = args.collect_script.resolve()
    base_dir = args.base_dir.resolve()
    out_dir = args.plot_copy_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base_dir.mkdir(parents=True, exist_ok=True)

    powers = load_powers(
        table_path=table_path,
        power_min=args.power_min,
        power_max=args.power_max,
        heat_loss=args.heat_loss,
    )
    if not powers:
        raise RuntimeError(
            "No powers found in steady-state table within range "
            f"[{args.power_min}, {args.power_max}]."
        )

    print(f"Using table: {table_path}")
    print(f"Core model: {args.core_model}")
    print(f"Heat loss: {args.heat_loss}")
    print(f"Power-tag format version: {POWER_TAG_FORMAT_VERSION}")
    print(f"Powers ({len(powers)}): {', '.join(f'{p:.6g}' for p in powers)}")
    print(f"Base output dir: {base_dir}")
    print(f"Plot copy target: {out_dir}")
    print("-" * 70)
    metadata_path = base_dir / "power_tags.json"
    metadata_path.write_text(
        json.dumps(
            {
                "power_tag_format_version": POWER_TAG_FORMAT_VERSION,
                "powers": [
                    {
                        "power": power,
                        "tag": make_power_tag(power),
                    }
                    for power in powers
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    failures: list[tuple[float, str, int]] = []
    total_plots = 0

    for power in powers:
        tag = make_power_tag(power)
        power_dir = base_dir / f"power_{tag}"
        power_dir.mkdir(parents=True, exist_ok=True)

        run_cmd = [
            args.python,
            str(run_script),
            "--core_model",
            args.core_model,
            "--power",
            f"{power:.12g}",
            "--freq_min",
            f"{args.freq_min:.12g}",
            "--freq_max",
            f"{args.freq_max:.12g}",
            "--num_freq",
            str(args.num_freq),
            "--base_dir",
            str(power_dir),
        ]
        run_cmd.extend(["--heat_loss", str(args.heat_loss)])
        if not args.disable_steady_state_table:
            run_cmd.extend(["--steady_state_table", str(table_path)])
        if args.disable_steady_state_table:
            run_cmd.append("--disable_steady_state_table")
        if args.sin_mag is not None:
            run_cmd.extend(["--sin_mag", f"{args.sin_mag:.12g}"])
        if args.sin_mag_auto:
            run_cmd.append("--sin_mag_auto")
        if args.sin_mag_ref is not None:
            run_cmd.extend(["--sin_mag_ref", f"{args.sin_mag_ref:.12g}"])
        if args.sin_mag_min is not None:
            run_cmd.extend(["--sin_mag_min", f"{args.sin_mag_min:.12g}"])
        if args.sin_mag_max is not None:
            run_cmd.extend(["--sin_mag_max", f"{args.sin_mag_max:.12g}"])
        if args.stop_time is not None:
            run_cmd.extend(["--stop_time", f"{args.stop_time:.12g}"])
        if args.ss_time is not None:
            run_cmd.extend(["--ss_time", f"{args.ss_time:.12g}"])
        if args.stop_time_mode is not None:
            run_cmd.extend(["--stop_time_mode", args.stop_time_mode])
        if args.min_cycles_after_ss is not None:
            run_cmd.extend(
                ["--min_cycles_after_ss", f"{args.min_cycles_after_ss:.12g}"]
            )
        if args.settle_rule is not None:
            run_cmd.extend(["--settle_rule", args.settle_rule])
        if args.sin_mag_auto_rule is not None:
            run_cmd.extend(["--sin_mag_auto_rule", args.sin_mag_auto_rule])
        if args.n_jobs is not None:
            run_cmd.extend(["--n_jobs", str(args.n_jobs)])
        if args.core_dir is not None:
            run_cmd.extend(["--core_dir", str(args.core_dir)])
        if args.model_name is not None:
            run_cmd.extend(["--model_name", args.model_name])
        if args.cleanup_omc_artifacts:
            run_cmd.append("--cleanup_omc_artifacts")
        if args.reduced_csv_for_collect:
            run_cmd.append("--reduced_csv_for_collect")
        if args.no_reuse:
            run_cmd.append("--no-reuse")

        print(f"[power={power:.6g}] Running frequency sweep")
        rc = run_command(run_cmd)
        if rc != 0:
            failures.append((power, "runFreqNominalParallel.py", rc))
            print(f"[power={power:.6g}] sweep failed (exit {rc})")
            print("-" * 70)
            continue

        collect_cmd = [
            args.python,
            str(collect_script),
            "--results_dir",
            str(power_dir),
            "--plot",
        ]
        collect_jobs = args.collect_n_jobs if args.collect_n_jobs is not None else args.n_jobs
        if collect_jobs is not None:
            collect_cmd.extend(["--n_jobs", str(collect_jobs)])

        print(f"[power={power:.6g}] Collecting response and generating plots")
        rc = run_command(collect_cmd)
        if rc != 0:
            failures.append((power, "collectFreqNominalParallel.py", rc))
            print(f"[power={power:.6g}] collect failed (exit {rc})")
            print("-" * 70)
            continue

        copied = copy_plots(
            results_dir=power_dir,
            power_tag=tag,
            out_dir=out_dir,
            core_model=args.core_model,
        )
        total_plots += len(copied)
        if copied:
            for path in copied:
                print(f"[power={power:.6g}] copied plot -> {path}")
        else:
            print(f"[power={power:.6g}] no PNG plots found in {power_dir}")

        print("-" * 70)

    if failures:
        print("Completed with failures:")
        for power, stage, code in failures:
            print(f"  power={power:.6g}: {stage} failed with exit code {code}")
        print(f"Total copied plots: {total_plots}")
        return 1

    print("Completed successfully for all powers.")
    print(f"Total copied plots: {total_plots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
