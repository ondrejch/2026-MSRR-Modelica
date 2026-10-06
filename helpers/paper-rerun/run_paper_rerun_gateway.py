#!/usr/bin/env python3
"""Submit and retrieve a publication rerun of the legacy MSRR paper workflows.

The script deliberately uses only the lumped legacy model family:
 ``core/SMD_MSR_Modelica.mo`` plus ``core/MSRR.mo``.  It never selects
 ``SegmentedMSR``.  Long calculations are dispatched through
 ``helpers.omc_gw`` and written to a new campaign directory under ``00runs/``
 of the remote checkout. Each job runs with a per-job scratch working
 directory (``~/tmp/SMD2/<campaign-id>-<job>`` by default, configurable via
 ``--remote-scratch-dir``) so parallel jobs keep isolated cwd scratch; all
 campaign paths in the dispatched commands are anchored to the checkout.

Stages are dependency ordered:

1. preflight and reproducibility metadata;
2. publication-qualified 1R and 9R setpoint tables;
3. startup, nonlinear-transient, and per-power frequency jobs;
4. paper-facing plots;
5. campaign inventory and SHA-256 list.

Examples
--------
Preview the complete command plan without contacting the gateway:

    python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py plan \
      --campaign-id review-2026-09

Submit, wait, and sync the completed campaign:

    python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py run \
      --campaign-id review-2026-09 \
      --remote-workdir ~/src/SMD-MSRR-dev \
      --sync

Resume after a local interruption:

    python3.12 helpers/paper-rerun/run_paper_rerun_gateway.py resume \
      --campaign-id review-2026-09 --sync

The gateway configuration is read from ``$MSRR_OMC_GW_CONFIG`` or
``~/.config/msrr_omc_gw/config.json``. Site-specific hosts and worker names
remain outside the repository.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]  # repository root (helpers/paper-rerun/ -> repo)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.omc_gw.modelica_ssh_client import (
    DEFAULT_MAX_TASKS_PER_WORKER,
    TERMINAL_STATES,
    ModelicaSshClient,
    _default_agent_command,
)
from freq.paths import make_power_tag
from helpers.omc_gw.omc_gw_config import (
    get_config_int,
    get_config_list,
    get_config_str,
    load_user_config,
)


DEFAULT_POWERS = (
    1e-5,
    1e-4,
    1e-3,
    1e-2,
    0.1,
    0.2,
    0.4,
    0.6,
    0.8,
    1.0,
    1.2,
)
STAGE_ORDER = ("preflight", "setpoints", "simulations", "plots", "inventory")


@dataclass(frozen=True)
class JobSpec:
    key: str
    stage: str
    name: str
    command: str
    tasks: int
    workdir: str


@dataclass
class JobRecord:
    key: str
    stage: str
    name: str
    command: str
    command_sha256: str
    tasks: int
    job_id: str | None = None
    state: str = "PLANNED"
    exit_code: int | None = None
    submitted_at: str | None = None
    finished_at: str | None = None
    worker: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quote_token(part: str) -> str:
    """Quote a shell token, keeping bare '~/...' paths tilde-expandable.

    Bash expands '~' only at the start of an unquoted word, or after an
    unquoted '=' in an assignment; shlex.quote would suppress that for any
    token containing '~'.
    """
    if any(ch in part for ch in "$`\"'\\<>|&;(){}*?[]!# \t\n"):
        return shlex.quote(part)
    for index, ch in enumerate(part):
        if ch == "~" and index != 0 and part[index - 1] != "=":
            return shlex.quote(part)
    return part


def shell_join(parts: Iterable[str | Path]) -> str:
    return " ".join(_quote_token(str(part)) for part in parts)


def shell_script(commands: Iterable[str]) -> str:
    body = "\n".join(commands)
    return f"set -euo pipefail\n{body}\n"


def parse_powers(text: str) -> tuple[float, ...]:
    values: list[float] = []
    seen: set[str] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        value = float(token)
        if not math.isfinite(value):
            raise argparse.ArgumentTypeError(
                "frequency-response powers must be finite; got nonfinite "
                f"token {token!r} (nan/inf spellings are rejected)"
            )
        if value <= 0:
            raise argparse.ArgumentTypeError("frequency-response powers must be > 0")
        key = f"{value:.12g}"
        if key not in seen:
            seen.add(key)
            values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one power is required")
    return tuple(values)


def sanitize_campaign_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise argparse.ArgumentTypeError(
            "campaign id must contain only letters, digits, '.', '_' and '-'"
        )
    return value


def local_git_commit() -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def normalize_remote_workdir(value: str | None, config: dict[str, Any]) -> str:
    if value:
        return value.rstrip("/")
    configured = get_config_str(config, "remote_repo_relpath")
    if not configured:
        raise ValueError(
            "--remote-workdir is required when remote_repo_relpath is not configured"
        )
    configured = configured.strip()
    if configured in ("~", "$HOME"):
        return "~"
    if configured.startswith(("~/", "$HOME/", "/")):
        return configured.rstrip("/")
    return f"~/{configured.rstrip('/')}"


def remote_path(workdir: str, relative: str) -> str:
    if workdir == "~":
        return f"~/{relative}"
    return f"{workdir.rstrip('/')}/{relative}"


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def build_client(
    args: argparse.Namespace, config: dict[str, Any]
) -> tuple[ModelicaSshClient, list[str], int, str | None]:
    host = args.host or get_config_str(config, "host")
    client = ModelicaSshClient(
        host=host,
        agent_command=args.agent_command or _default_agent_command(config),
        ssh_bin=args.ssh_bin,
        ssh_options=tuple(
            args.ssh_option or get_config_list(config, "ssh_options") or ()
        ),
        jobs_root=args.jobs_root or get_config_str(config, "jobs_root"),
    )
    workers = (
        [item.strip() for item in args.workers.split(",") if item.strip()]
        if args.workers
        else get_config_list(config, "workers")
    ) or []
    max_tasks = (
        args.max_tasks_per_worker
        if args.max_tasks_per_worker is not None
        else get_config_int(
            config,
            "max_tasks_per_worker",
            DEFAULT_MAX_TASKS_PER_WORKER,
        )
    )
    modelica_bin_dir = get_config_str(config, "modelica_bin_dir")
    return client, workers, max_tasks, modelica_bin_dir


def make_plan(args: argparse.Namespace, remote_workdir: str) -> list[JobSpec]:
    # Each gateway job runs with cwd = <remote-scratch>/<campaign>-<job>, a
    # per-job directory the agent creates on the worker, so parallel omc runs
    # never share cwd artifacts. The cwd is not the checkout, therefore every
    # workflow path below is anchored to the checkout and PYTHONPATH makes the
    # repository importable from the scratch cwd. Bare '~/...' path words stay
    # unquoted by shlex, so the worker shell still expands '~'.
    campaign = remote_path(remote_workdir, f"00runs/paper-rerun-{args.campaign_id}")
    powers_text = ",".join(f"{value:.12g}" for value in args.powers)
    setpoints_1r = f"{campaign}/setpoints/setpoints_1r.csv"
    setpoints_9r = f"{campaign}/setpoints/setpoints_9r.csv"
    python = args.python
    py = [f"PYTHONPATH={remote_workdir}", python]
    scratch = args.remote_scratch_dir.rstrip("/")

    expected_commit = args.expected_commit or local_git_commit()
    commit_check = ""
    if expected_commit:
        commit_check = (
            f'test "$(git -C {remote_workdir} rev-parse HEAD)" = {shlex.quote(expected_commit)} || '
            f'{{ echo "remote commit does not match {expected_commit}" >&2; exit 3; }}'
        )
    dirty_check = ""
    if not args.allow_dirty:
        dirty_check = (
            f'test -z "$(git -C {remote_workdir} status --porcelain --untracked-files=no)" || '
            f'{{ echo "tracked remote worktree is dirty" >&2; git -C {remote_workdir} status --short; exit 3; }}'
        )

    preflight_commands = [
        f"mkdir -p {campaign}/metadata {campaign}/logs",
        f'printf "%s\\n" {shlex.quote(args.campaign_id)} > {campaign}/metadata/campaign_id.txt',
        f"date -u +%Y-%m-%dT%H:%M:%SZ > {campaign}/metadata/started_utc.txt",
        f"git -C {remote_workdir} rev-parse HEAD > {campaign}/metadata/git_commit.txt",
        f"git -C {remote_workdir} status --short > {campaign}/metadata/git_status.txt",
        f"omc --version > {campaign}/metadata/openmodelica_version.txt",
        f"{shell_join([*py, '--version'])} > {campaign}/metadata/python_version.txt 2>&1",
        # Fails the preflight unless the checkout's lumped model sources match
        # core/init/model_version.json (the version every table of this
        # campaign will be stamped with; helpers.setpoint_model_version).
        f"{shell_join([*py, '-m', 'helpers.setpoint_model_version', 'status'])} "
        f"> {campaign}/metadata/setpoint_model_version.txt",
        (
            f"sha256sum {remote_workdir}/core/generated/MSRR_PlantData.mo "
            f"{remote_workdir}/core/SMD_MSR_Modelica.mo "
            f"{remote_workdir}/core/MSRR.mo "
            f"{remote_workdir}/startup/runMSRR.py "
            f"{remote_workdir}/transients/run_nonlinear_steps.py "
            f"{remote_workdir}/freq/runFreqNominalParallel.py "
            f"{remote_workdir}/freq/collectFreqNominalParallel.py "
            f"{remote_workdir}/freq/fr_protocol.py "
            f"{remote_workdir}/freq/refinement.py "
            f"{remote_workdir}/freq/refine_sweep.py "
            f"{remote_workdir}/freq/linearity_check.py "
            f"{remote_workdir}/data/scenarios/freq/fr_gain_prior.json "
            f"{remote_workdir}/core/init/model_version.json "
            f"{remote_workdir}/helpers/setpoint_model_version.py "
            f"{remote_workdir}/helpers/paper-rerun/complete_setpoint_table.py "
            f"> {campaign}/metadata/source_sha256.txt"
        ),
    ]
    if commit_check:
        preflight_commands.append(commit_check)
    if dirty_check:
        preflight_commands.append(dirty_check)
    legacy_check_code = (
        "import os; from pathlib import Path; "
        f"r=os.path.expanduser({remote_workdir!r}); "
        "s=(Path(r+'/core/generated/MSRR_PlantData.mo').read_text()+"
        "Path(r+'/core/SMD_MSR_Modelica.mo').read_text()+"
        "Path(r+'/core/MSRR.mo').read_text()); "
        "assert 'package MSRR' in s; "
        "print('legacy MSRR sources present')"
    )
    preflight_commands.append(
        f"{shell_join([*py, '-c', legacy_check_code])} "
        f"> {campaign}/metadata/model_family_check.txt"
    )
    if not args.skip_tests:
        preflight_commands.append(
            f"{shell_join([*py, '-m', 'pytest', f'{remote_workdir}/tests/', '--rootdir', remote_workdir, '-m', 'unit or static_modelica or ssh or omc_short', '-q'])} "
            f"| tee {campaign}/logs/preflight_tests.log"
        )

    plan: list[JobSpec] = [
        JobSpec(
            key="preflight",
            stage="preflight",
            name=f"paper-{args.campaign_id}-preflight",
            command=shell_script(preflight_commands),
            tasks=1,
            workdir=f"{scratch}/{args.campaign_id}-preflight",
        )
    ]

    for core in ("1r", "9r"):
        output = setpoints_1r if core == "1r" else setpoints_9r
        setpoint_log = f"{campaign}/logs/setpoints_{core}.log"
        # Two-step setpoint procedure (physics review 2026-09-27): the plain
        # generator writes every row that passes its late-window checks and
        # exits 3 (EXIT_TABLE_INCOMPLETE) when some do not; any other nonzero
        # status is a hard failure and fails the job (rev032 review). The
        # completion helper then runs the warm-started continuation for
        # exactly the missing/unqualified powers, merges them, and fails
        # closed if any requested power is still missing; a final strict
        # verification checks the binding, rows and coverage.
        complete = shell_join(
            [
                *py,
                f"{remote_workdir}/helpers/paper-rerun/complete_setpoint_table.py",
                "--core_model",
                core,
                "--powers",
                powers_text,
                "--table",
                output,
                "--work_dir",
                f"{campaign}/setpoints/continuation_{core}",
                "--python",
                python,
                *(
                    ["--omc_timeout_seconds", f"{args.omc_timeout_seconds:.12g}"]
                    if args.omc_timeout_seconds > 0
                    else []
                ),
            ]
        )
        cmd = shell_script(
            [
                f"mkdir -p {campaign}/setpoints/work_{core} {campaign}/logs",
                "gen_rc=0",
                shell_join(
                    [
                        *py,
                        "-m",
                        "core.init.generateSetpointTable",
                        "--core_model",
                        core,
                        "--powers",
                        powers_text,
                        "--qualification_profile",
                        "publication",
                        "--n_jobs",
                        str(args.setpoint_jobs),
                        "--work_dir",
                        f"{campaign}/setpoints/work_{core}",
                        "--output",
                        output,
                        "--omc_timeout_seconds",
                        str(args.omc_timeout_seconds),
                    ]
                )
                + f" 2>&1 | tee {setpoint_log} || gen_rc=$?",
                f'echo "first-pass generator exit status: $gen_rc" | tee -a {setpoint_log}',
                'case "$gen_rc" in 0|3) ;; *) exit "$gen_rc" ;; esac',
                f"test -f {output}",
                f"{complete} 2>&1 | tee -a {setpoint_log}",
                shell_join(
                    [
                        *py,
                        "-m",
                        "helpers.setpoint_model_version",
                        "verify",
                        "--table",
                        output,
                        "--powers",
                        powers_text,
                    ]
                )
                + f" 2>&1 | tee -a {setpoint_log}",
            ]
        )
        plan.append(
            JobSpec(
                key=f"setpoints-{core}",
                stage="setpoints",
                name=f"paper-{args.campaign_id}-setpoints-{core}",
                command=cmd,
                tasks=args.setpoint_jobs,
                workdir=f"{scratch}/{args.campaign_id}-setpoints-{core}",
            )
        )

    for core in ("1r", "9r"):
        run_dir = f"{campaign}/startup/{core}"
        cmd = shell_script(
            [
                f"mkdir -p {run_dir} {campaign}/logs",
                shell_join(
                    [
                        *py,
                        "-m",
                        "startup.runMSRR",
                        "--package",
                        "legacy",
                        "--core_model",
                        core,
                        "--scenario",
                        "startup_to_1mw",
                        "--run_dir",
                        run_dir,
                        "--keep_mos",
                        "--omc_timeout_seconds",
                        str(args.omc_timeout_seconds),
                    ]
                )
                + f" 2>&1 | tee {campaign}/logs/startup_{core}.log",
            ]
        )
        plan.append(
            JobSpec(
                key=f"startup-{core}",
                stage="simulations",
                name=f"paper-{args.campaign_id}-startup-{core}",
                command=cmd,
                tasks=1,
                workdir=f"{scratch}/{args.campaign_id}-startup-{core}",
            )
        )

    for core in ("1r", "9r"):
        cmd = shell_script(
            [
                f"mkdir -p {campaign}/transients {campaign}/logs",
                shell_join(
                    [
                        *py,
                        "-m",
                        "transients.run_nonlinear_steps",
                        "--package",
                        "legacy",
                        "--core_models",
                        core,
                        "--out_dir",
                        f"{campaign}/transients",
                        "--setpoints_csv_1r",
                        setpoints_1r,
                        "--setpoints_csv_9r",
                        setpoints_9r,
                        "--omc_timeout_seconds",
                        str(args.omc_timeout_seconds),
                    ]
                )
                + f" 2>&1 | tee {campaign}/logs/transients_{core}.log",
            ]
        )
        plan.append(
            JobSpec(
                key=f"transients-{core}",
                stage="simulations",
                name=f"paper-{args.campaign_id}-transients-{core}",
                command=cmd,
                tasks=1,
                workdir=f"{scratch}/{args.campaign_id}-transients-{core}",
            )
        )

    for core in ("1r", "9r"):
        table = setpoints_1r if core == "1r" else setpoints_9r
        for power in args.powers:
            tag = make_power_tag(power)
            results_dir = f"{campaign}/freq/{core}/power_{tag}"
            # One runner argument list serves the base sweep and every
            # refinement round (freq.refine_sweep reuses it verbatim with
            # --base_dir <results_dir>/refine/round_NN --refine_plan <plan>).
            runner_args = [
                "--package",
                "legacy",
                "--core_model",
                core,
                "--power",
                f"{power:.12g}",
                "--freq_min",
                f"{args.freq_min:.12g}",
                "--freq_max",
                f"{args.freq_max:.12g}",
                "--num_freq",
                str(args.num_freq),
                "--sin_mag_auto",
                "--stop_time_mode",
                "min_cycles_after_ss",
                "--min_cycles_after_ss",
                "12",
                "--steady_state_table",
                table,
                "--base_dir",
                results_dir,
                "--n_jobs",
                str(args.freq_jobs),
                "--reduced_csv_for_collect",
                "--cleanup_omc_artifacts",
                "--no-reuse",
                "--omc_timeout_seconds",
                str(args.omc_timeout_seconds),
                *(["--fr_prior", args.fr_prior] if args.fr_prior else []),
            ]
            collect_args = ["--plot", "--n_jobs", str(args.freq_jobs)]
            run = shell_join([*py, "-m", "freq.runFreqNominalParallel", *runner_args])
            collect = shell_join(
                [
                    *py,
                    "-m",
                    "freq.collectFreqNominalParallel",
                    "--results_dir",
                    results_dir,
                    *collect_args,
                ]
            )
            refine = shell_join(
                [
                    *py,
                    "-m",
                    "freq.refine_sweep",
                    "--results_dir",
                    results_dir,
                    "--max_rounds",
                    str(args.refine_max_rounds),
                    "--max_wall_s",
                    f"{args.refine_max_wall_seconds:.12g}",
                    "--collect_args=" + " ".join(collect_args),
                    "--",
                    *runner_args,
                ]
            )
            # Half-amplitude approval checks (band edges + measured
            # resonance, effective discards) once every point converged.
            linearity = shell_join(
                [
                    *py,
                    "-m",
                    "freq.linearity_check",
                    "campaign",
                    "--results_dir",
                    results_dir,
                    "--",
                    *runner_args,
                ]
            )
            # A point that still fails the convergence check or the realized
            # swing band after the refinement loop, or a failed linearity
            # check, fails the job (--exit_policy publication_approved)
            # instead of being published; the verification JSON is written
            # first.
            verify = shell_join(
                [
                    *py,
                    "-m",
                    "freq.verify_campaign",
                    results_dir,
                    "--exit_policy",
                    "publication_approved",
                ]
            )
            log = f"{campaign}/logs/freq_{core}_{tag}"
            cmd = shell_script(
                [
                    f"mkdir -p {results_dir} {campaign}/logs",
                    f"{run} 2>&1 | tee {log}_run.log",
                    f"{collect} 2>&1 | tee {log}_collect.log",
                    "refine_rc=0",
                    f"{refine} 2>&1 | tee {log}_refine.log || refine_rc=$?",
                    "linearity_rc=0",
                    'if [ "$refine_rc" -eq 0 ]; then '
                    f"{linearity} 2>&1 | tee {log}_linearity.log || linearity_rc=$?; fi",
                    f"{verify} > {log}_verify.json",
                    'test "$refine_rc" -eq 0',
                    'test "$linearity_rc" -eq 0',
                ]
            )
            plan.append(
                JobSpec(
                    key=f"freq-{core}-{tag}",
                    stage="simulations",
                    name=f"paper-{args.campaign_id}-freq-{core}-{tag}",
                    command=cmd,
                    tasks=args.freq_jobs,
                    workdir=f"{scratch}/{args.campaign_id}-freq-{core}-{tag}",
                )
            )

    # The paper's annotated time-domain example uses exactly 0.1 rad/s.  The
    # default 80-point 1e-3..1e1 logarithmic grid does not contain that value,
    # so run it as a separate one-point, provenance-complete campaign rather
    # than inserting a foreign case into the main immutable sweep.
    if any(abs(power - 1.0) <= 1e-12 for power in args.powers):
        for core in ("1r", "9r"):
            table = setpoints_1r if core == "1r" else setpoints_9r
            results_dir = f"{campaign}/freq_time_example/{core}/power_1"
            run = shell_join(
                [
                    *py,
                    "-m",
                    "freq.runFreqNominalParallel",
                    "--package",
                    "legacy",
                    "--core_model",
                    core,
                    "--power",
                    "1.0",
                    "--freq_min",
                    f"{args.time_example_frequency:.12g}",
                    "--freq_max",
                    f"{args.time_example_frequency:.12g}",
                    "--num_freq",
                    "1",
                    "--sin_mag",
                    "1.0",
                    "--steady_state_table",
                    table,
                    "--base_dir",
                    results_dir,
                    "--n_jobs",
                    "1",
                    "--reduced_csv_for_collect",
                    "--cleanup_omc_artifacts",
                    "--no-reuse",
                    "--omc_timeout_seconds",
                    str(args.omc_timeout_seconds),
                ]
            )
            collect = shell_join(
                [
                    *py,
                    "-m",
                    "freq.collectFreqNominalParallel",
                    "--results_dir",
                    results_dir,
                    "--n_jobs",
                    "1",
                ]
            )
            verify = shell_join(
                [*py, "-m", "freq.verify_campaign", results_dir]
            )
            cmd = shell_script(
                [
                    f"mkdir -p {results_dir} {campaign}/logs",
                    f"{run} 2>&1 | tee {campaign}/logs/freq_time_{core}_run.log",
                    f"{collect} 2>&1 | tee {campaign}/logs/freq_time_{core}_collect.log",
                    f"{verify} > {campaign}/logs/freq_time_{core}_verify.json",
                ]
            )
            plan.append(
                JobSpec(
                    key=f"freq-time-{core}",
                    stage="simulations",
                    name=f"paper-{args.campaign_id}-freq-time-{core}",
                    command=cmd,
                    tasks=1,
                    workdir=f"{scratch}/{args.campaign_id}-freq-time-{core}",
                )
            )

    plot_commands = [
        f"mkdir -p {campaign}/figures/startup {campaign}/figures/transients "
        f"{campaign}/figures/frequency {campaign}/.mplconfig {campaign}/logs",
        f"export MPLBACKEND=Agg MPLCONFIGDIR={campaign}/.mplconfig",
    ]
    for core in ("1r", "9r"):
        run_dir = f"{campaign}/startup/{core}"
        plot_commands.extend(
            [
                shell_join(
                    [
                        *py,
                        "-m",
                        "startup.plotApproachToCriticalityPhase4",
                        "--core_model",
                        core,
                        "--run_dir",
                        run_dir,
                        "--out",
                        f"{campaign}/figures/startup/startup_phase1to4_{core}.png",
                        "--signed_log_out",
                        f"{campaign}/figures/startup/startup_phase1to4_signedlog_{core}.png",
                    ]
                ),
                shell_join(
                    [
                        *py,
                        "-m",
                        "startup.plotStartUpTo1MW",
                        "--core_model",
                        core,
                        "--run_dir",
                        run_dir,
                        "--out",
                        f"{campaign}/figures/startup/startup_to1MW_{core}.png",
                    ]
                ),
            ]
        )
    plot_commands.extend(
        [
            shell_join(
                [
                    *py,
                    "-m",
                    "transients.plot_nonlinear_steps",
                    "--package",
                    "legacy",
                    "--outputs_dir",
                    f"{campaign}/transients",
                    "--fig_dir",
                    f"{campaign}/figures/transients",
                    "--core_models",
                    "1r",
                    "9r",
                ]
            ),
            shell_join(
                [
                    *py,
                    "-m",
                    "freq.plotBodeCompareCoreModels",
                    "--results_root",
                    f"{campaign}/freq",
                    "--out_dir",
                    f"{campaign}/figures/frequency",
                    "--powers",
                    *[f"{power:.12g}" for power in args.powers],
                ]
            ),
        ]
    )
    if any(abs(power - 1.0) <= 1e-12 for power in args.powers):
        plot_commands.append(
            shell_join(
                [
                    *py,
                    "-m",
                    "freq.plotFreqTimeCompareCoreModels",
                    "--results_root",
                    f"{campaign}/freq_time_example",
                    "--power",
                    "1.0",
                    "--freq",
                    f"{args.time_example_frequency:.12g}",
                    "--out_path",
                    f"{campaign}/figures/frequency/MSRR_freq_nominal_time_example.png",
                ]
            )
        )
    plan.append(
        JobSpec(
            key="plots",
            stage="plots",
            name=f"paper-{args.campaign_id}-plots",
            command=shell_script(plot_commands),
            tasks=1,
            workdir=f"{scratch}/{args.campaign_id}-plots",
        )
    )

    inventory_python = (
        "import os; from pathlib import Path; import json; "
        f"r=Path(os.path.expanduser({campaign!r})); "
        "files=[p for p in r.rglob('*') if p.is_file()]; "
        "payload={'file_count':len(files),"
        "'csv_count':sum(p.suffix=='.csv' for p in files),"
        "'png_count':sum(p.suffix=='.png' for p in files),"
        "'json_count':sum(p.suffix=='.json' for p in files),"
        "'total_bytes':sum(p.stat().st_size for p in files)}; "
        "(r/'campaign_summary.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\\n'); "
        "print(json.dumps(payload,sort_keys=True))"
    )
    inventory_cmd = shell_script(
        [
            f"date -u +%Y-%m-%dT%H:%M:%SZ > {campaign}/metadata/completed_utc.txt",
            shell_join([*py, "-c", inventory_python]),
            f"find {campaign} -type f ! -name SHA256SUMS -print0 | sort -z | "
            f"xargs -0 sha256sum > {campaign}/SHA256SUMS",
        ]
    )
    plan.append(
        JobSpec(
            key="inventory",
            stage="inventory",
            name=f"paper-{args.campaign_id}-inventory",
            command=inventory_cmd,
            tasks=1,
            workdir=f"{scratch}/{args.campaign_id}-inventory",
        )
    )

    forbidden = [
        spec.key
        for spec in plan
        if "SegmentedMSR" in spec.command or "--package segmented" in spec.command
    ]
    if forbidden:
        raise RuntimeError(f"legacy-only invariant violated by jobs: {forbidden}")
    return plan


class StateStore:
    def __init__(self, path: Path, campaign_id: str, remote_workdir: str):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if self.data["campaign_id"] != campaign_id:
                raise ValueError("state file belongs to another campaign")
            if self.data["remote_workdir"] != remote_workdir:
                raise ValueError("state file remote_workdir differs from this invocation")
        else:
            self.data = {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "remote_workdir": remote_workdir,
                "created_utc": utc_now(),
                "updated_utc": utc_now(),
                "jobs": {},
            }
            self.save()

    def save(self) -> None:
        self.data["updated_utc"] = utc_now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def get(self, spec: JobSpec) -> JobRecord:
        stored = self.data["jobs"].get(spec.key)
        digest = command_hash(spec.command)
        if stored:
            if stored["command_sha256"] != digest:
                raise ValueError(
                    f"command for {spec.key} changed since the state file was created"
                )
            return JobRecord(**stored)
        record = JobRecord(
            key=spec.key,
            stage=spec.stage,
            name=spec.name,
            command=spec.command,
            command_sha256=digest,
            tasks=spec.tasks,
        )
        self.put(record)
        return record

    def put(self, record: JobRecord) -> None:
        self.data["jobs"][record.key] = asdict(record)
        self.save()


def terminal_success(job: dict[str, Any]) -> bool:
    state = str(job.get("state", ""))
    code = job.get("exit_code")
    return state == "COMPLETED" and (code is None or int(code) == 0)


def process_stage(
    *,
    stage: str,
    specs: list[JobSpec],
    store: StateStore,
    client: ModelicaSshClient,
    remote_workdir: str,
    workers: list[str],
    max_tasks: int,
    modelica_bin_dir: str | None,
    args: argparse.Namespace,
) -> None:
    active: list[tuple[JobSpec, JobRecord]] = []
    for spec in specs:
        record = store.get(spec)
        if record.state == "COMPLETED" and record.exit_code in (None, 0):
            print(f"[{stage}] already complete: {spec.key}")
            continue
        if record.job_id and record.state not in TERMINAL_STATES:
            print(f"[{stage}] following existing job {record.job_id}: {spec.key}")
            active.append((spec, record))
            continue
        if record.state in {"FAILED", "CANCELED", "SUBMIT_FAILED"} and not args.retry_failed:
            raise RuntimeError(
                f"{spec.key} previously failed; rerun with --retry-failed after diagnosis"
            )
        print(f"[{stage}] submitting {spec.key} ({spec.tasks} task slots)")
        job = client.submit(
            spec.command,
            workdir=spec.workdir,
            workers=workers,
            name=spec.name,
            tasks=spec.tasks,
            max_tasks_per_worker=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            wait_for_slot=True,
            poll_seconds=args.poll_seconds,
            wait_timeout_seconds=(
                args.slot_timeout_seconds if args.slot_timeout_seconds > 0 else None
            ),
        )
        record.job_id = str(job["job_id"])
        record.state = str(job.get("state", "SUBMITTED"))
        record.worker = job.get("worker")
        record.submitted_at = utc_now()
        record.exit_code = None
        record.finished_at = None
        store.put(record)
        active.append((spec, record))

    failures: list[str] = []
    for spec, record in active:
        assert record.job_id is not None
        print(f"[{stage}] waiting for {spec.key}: {record.job_id}")
        final = client.wait(
            record.job_id,
            poll_seconds=args.poll_seconds,
            timeout_seconds=(
                args.job_timeout_seconds if args.job_timeout_seconds > 0 else None
            ),
        )
        detail = client.result(record.job_id, tail_lines=80)
        record.state = str(final.get("state", detail.get("state", "UNKNOWN")))
        code = detail.get("exit_code", final.get("exit_code"))
        record.exit_code = int(code) if code is not None else None
        record.worker = detail.get("worker", record.worker)
        record.finished_at = utc_now()
        store.put(record)
        if terminal_success(detail):
            print(f"[{stage}] complete: {spec.key}")
        else:
            failures.append(spec.key)
            print(f"[{stage}] FAILED: {spec.key} ({record.job_id})", file=sys.stderr)
            for line in detail.get("stderr_tail", []) or []:
                print(f"  {line}", file=sys.stderr)
    if failures:
        raise RuntimeError(f"stage {stage} failed: {', '.join(failures)}")


#: rsync filter for ``sync`` (owner practice: bulky raw data stays on the
#: cluster). Setpoint step directories (first-pass ``work_*`` and
#: ``continuation_*`` power directories) keep only their evidence files --
#: manifests, validation and convergence JSON, logs, ``runModelica.mos`` --
#: not the per-step result CSVs (hundreds of MB each) or build products;
#: OpenModelica build artifacts are excluded everywhere. Tables, sidecars,
#: completion records, frequency aggregates and reduced result CSVs, figures,
#: logs and metadata all come back.
SYNC_FILTER_ARGS: tuple[str, ...] = (
    "--include", "setpoints/*/*/*.json",
    "--include", "setpoints/*/*/*.log",
    "--include", "setpoints/*/*/runModelica.mos",
    "--exclude", "setpoints/*/*/*",
    "--exclude", "*.c",
    "--exclude", "*.o",
    "--exclude", "*.h",
    "--exclude", "*.libs",
    "--exclude", "*.makefile",
    "--exclude", "*_JacA.bin",
    "--exclude", "*_JacLSJac*.bin",
    "--exclude", "*.claim",
)


def sync_campaign(
    *,
    client: ModelicaSshClient,
    remote_workdir: str,
    campaign_id: str,
    local_parent: Path,
) -> Path:
    if not client.host:
        raise ValueError("gateway host is not configured")
    remote_campaign = remote_path(
        remote_workdir, f"00runs/paper-rerun-{campaign_id}"
    )
    local_parent.mkdir(parents=True, exist_ok=True)
    ssh_command = shell_join([client.ssh_bin, *client.ssh_options])
    destination = local_parent / f"paper-rerun-{campaign_id}"
    cmd = [
        "rsync",
        "-az",
        "--partial",
        "--info=progress2",
        *SYNC_FILTER_ARGS,
        "-e",
        ssh_command,
        f"{client.host}:{remote_campaign.rstrip('/')}/",
        str(destination) + "/",
    ]
    print("$", shell_join(cmd))
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"rsync failed with exit code {proc.returncode}")
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete legacy-MSRR paper reproduction campaign through "
            "the SSH OpenModelica gateway."
        )
    )
    parser.add_argument("action", choices=("plan", "run", "resume", "sync"))
    parser.add_argument(
        "--campaign-id",
        type=sanitize_campaign_id,
        default=datetime.now(timezone.utc).strftime("%Y%m%d"),
    )
    parser.add_argument("--remote-workdir", default=None)
    parser.add_argument(
        "--remote-scratch-dir",
        default="~/tmp/SMD2",
        help=(
            "Per-job remote working directory root. Each job runs with cwd "
            "<dir>/<campaign-id>-<job> so parallel jobs keep isolated cwd "
            "scratch; the gateway agent creates the directory on the worker. "
            "(default: ~/tmp/SMD2)"
        ),
    )
    parser.add_argument("--local-results-parent", type=Path, default=ROOT / "00runs")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Local gateway state JSON (default: 00runs/tmp/paper_rerun_<id>.json)",
    )
    parser.add_argument(
        "--powers",
        type=parse_powers,
        default=DEFAULT_POWERS,
        help="Comma-separated MW powers for setpoints and frequency sweeps",
    )
    parser.add_argument("--freq-min", type=float, default=1e-3)
    parser.add_argument("--freq-max", type=float, default=1e1)
    parser.add_argument("--num-freq", type=int, default=80)
    parser.add_argument("--time-example-frequency", type=float, default=0.1)
    parser.add_argument("--setpoint-jobs", type=int, default=16)
    parser.add_argument("--freq-jobs", type=int, default=16)
    parser.add_argument(
        "--refine-max-rounds",
        type=int,
        default=8,
        help=(
            "Refinement rounds per frequency job (freq.refine_sweep "
            "--max_rounds; default: 8, room for discard escalation, amplitude "
            "halving and linearity settle/probe/restore rounds). Points still unconverged after them "
            "fail the job through verify_campaign --exit_policy "
            "publication_approved."
        ),
    )
    parser.add_argument(
        "--fr-prior",
        default="",
        help=(
            "Gain prior for the frequency jobs (runner --fr_prior; remote "
            "path). Default: the committed data/scenarios/freq/"
            "fr_gain_prior.json. Use a prior rebuilt with freq.build_gain_prior "
            "from a corrected-model campaign for a second pass (see "
            "freq/README.md, 'Rebuilding the gain prior')."
        ),
    )
    parser.add_argument(
        "--refine-max-wall-seconds",
        type=float,
        default=0.0,
        help=(
            "freq.refine_sweep --max_wall_s per frequency job: no new round "
            "starts after this many seconds (default: 0, no limit)."
        ),
    )
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--omc-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--expected-commit", default=None)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--slot-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=0.0)
    parser.add_argument(
        "--only-stages",
        default=",".join(STAGE_ORDER),
        help=f"Comma-separated subset of: {', '.join(STAGE_ORDER)}",
    )

    parser.add_argument("--host", default=None)
    parser.add_argument("--agent-command", default=None)
    parser.add_argument("--jobs-root", default=None)
    parser.add_argument("--workers", default=None)
    parser.add_argument("--max-tasks-per-worker", type=int, default=None)
    parser.add_argument("--ssh-bin", default="ssh")
    parser.add_argument("--ssh-option", action="append", default=[])
    return parser


def selected_stages(text: str) -> tuple[str, ...]:
    stages = tuple(item.strip() for item in text.split(",") if item.strip())
    unknown = sorted(set(stages) - set(STAGE_ORDER))
    if unknown:
        raise ValueError(f"unknown stages: {', '.join(unknown)}")
    return tuple(stage for stage in STAGE_ORDER if stage in stages)


def print_plan(plan: list[JobSpec], stages: tuple[str, ...]) -> None:
    for stage in stages:
        print(f"\n## {stage}")
        for spec in plan:
            if spec.stage != stage:
                continue
            print(f"\n[{spec.key}] tasks={spec.tasks}\n{spec.command.rstrip()}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.freq_min <= 0 or args.freq_max <= args.freq_min:
        raise ValueError("require 0 < --freq-min < --freq-max")
    if args.num_freq < 2:
        raise ValueError("--num-freq must be at least 2")
    if args.setpoint_jobs < 1 or args.freq_jobs < 1:
        raise ValueError("job counts must be positive")
    if args.refine_max_rounds < 0:
        raise ValueError("--refine-max-rounds must be >= 0")
    if not math.isfinite(args.refine_max_wall_seconds) or args.refine_max_wall_seconds < 0:
        raise ValueError("--refine-max-wall-seconds must be finite and >= 0")

    config = load_user_config()
    remote_workdir = normalize_remote_workdir(args.remote_workdir, config)
    plan = make_plan(args, remote_workdir)
    stages = selected_stages(args.only_stages)

    if args.action == "plan":
        print_plan(plan, stages)
        return 0

    client, workers, max_tasks, modelica_bin_dir = build_client(args, config)

    if args.action == "sync":
        destination = sync_campaign(
            client=client,
            remote_workdir=remote_workdir,
            campaign_id=args.campaign_id,
            local_parent=args.local_results_parent.resolve(),
        )
        print(f"Synced campaign to {destination}")
        return 0

    state_file = (
        args.state_file
        if args.state_file is not None
        else ROOT / "00runs" / "tmp" / f"paper_rerun_{args.campaign_id}.json"
    )
    store = StateStore(state_file.resolve(), args.campaign_id, remote_workdir)

    for stage in stages:
        stage_specs = [spec for spec in plan if spec.stage == stage]
        process_stage(
            stage=stage,
            specs=stage_specs,
            store=store,
            client=client,
            remote_workdir=remote_workdir,
            workers=workers,
            max_tasks=max_tasks,
            modelica_bin_dir=modelica_bin_dir,
            args=args,
        )

    if args.sync:
        destination = sync_campaign(
            client=client,
            remote_workdir=remote_workdir,
            campaign_id=args.campaign_id,
            local_parent=args.local_results_parent.resolve(),
        )
        print(f"Synced campaign to {destination}")

    print(f"Campaign complete: paper-rerun-{args.campaign_id}")
    print(f"Gateway state: {state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
