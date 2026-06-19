"""Shared path conventions for startup run artifacts."""

from __future__ import annotations

from pathlib import Path


STARTUP_SCENARIO_FILE_PREFIX = {
    "startup": {
        "1r": "startUp",
        "9r": "startUp_9r",
    },
    "startup_to_100kw": {
        "1r": "startUp_to100kW",
        "9r": "startUp_to100kW_9r",
    },
    "startup_to_1mw": {
        "1r": "startUp_to1MW",
        "9r": "startUp_to1MW_9r",
    },
}


def default_startup_run_dir(repo_root: Path, *, scenario: str, core_model: str) -> Path:
    """Return default run directory under 00runs for a startup run definition."""

    return repo_root / "00runs" / f"startup-{scenario}-{core_model}"


def startup_result_prefix(*, scenario: str, core_model: str) -> str:
    """Return the default result file prefix for a startup scenario/core pair."""

    return STARTUP_SCENARIO_FILE_PREFIX[scenario][core_model]


def default_startup_csv_path(repo_root: Path, *, scenario: str, core_model: str) -> Path:
    """Return the default startup result CSV path for a run definition."""

    run_dir = default_startup_run_dir(
        repo_root,
        scenario=scenario,
        core_model=core_model,
    )
    return run_dir / f"{startup_result_prefix(scenario=scenario, core_model=core_model)}_res.csv"
