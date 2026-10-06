"""Shared path conventions for startup run artifacts."""

from __future__ import annotations

from pathlib import Path


STARTUP_SCENARIO_FILE_PREFIX = {
    "startup": {
        "1r": "startUp",
        "9r": "startUp_9r",
        # 1-channel x 10-axial-segment 1R core (TASK-20260906-01; segmented
        # package only -- the legacy package ships no 10-segment vehicle).
        "1r10seg": "startUp_1r10seg",
        # 5x5-radial x 10-axial-segment 1R core (TASK-20260906-02; segmented
        # package only -- the legacy package ships no 5x5 vehicle).
        "r5x5_z10": "startUp_r5x5_z10",
    },
    "startup_to_100kw": {
        "1r": "startUp_to100kW",
        "9r": "startUp_to100kW_9r",
        "1r10seg": "startUp_to100kW_1r10seg",
        "r5x5_z10": "startUp_to100kW_r5x5_z10",
    },
    "startup_to_1mw": {
        "1r": "startUp_to1MW",
        "9r": "startUp_to1MW_9r",
        "1r10seg": "startUp_to1MW_1r10seg",
        "r5x5_z10": "startUp_to1MW_r5x5_z10",
    },
}

#: Result prefixes of the scaled-plant startup ids (data/plants/msrr_1gw/
#: scenarios/startup/; segmented package only). Kept beside, not inside,
#: STARTUP_SCENARIO_FILE_PREFIX so the msrr table stays the historical one.
SCALED_PLANT_SCENARIO_FILE_PREFIX = {
    "startup_to_100mw": {
        "1r": "startUp_to100MW",
        "9r": "startUp_to100MW_9r",
        "1r10seg": "startUp_to100MW_1r10seg",
        "r5x5_z10": "startUp_to100MW_r5x5_z10",
    },
    "startup_to_1gw": {
        "1r": "startUp_to1GW",
        "9r": "startUp_to1GW_9r",
        "1r10seg": "startUp_to1GW_1r10seg",
        "r5x5_z10": "startUp_to1GW_r5x5_z10",
    },
}

#: The plant whose segmented defaults sit directly under 00runs/segmented/.
DEFAULT_PLANT_ID = "msrr"


def segmented_results_base(repo_root: Path, plant: str = DEFAULT_PLANT_ID) -> Path:
    """``00runs/segmented/`` for the default plant, ``00runs/segmented/<plant>/`` else."""

    base = repo_root / "00runs" / "segmented"
    return base if plant == DEFAULT_PLANT_ID else base / str(plant)


def default_startup_run_dir(repo_root: Path, *, scenario: str, core_model: str) -> Path:
    """Return default run directory under 00runs for a startup run definition."""

    return repo_root / "00runs" / f"startup-{scenario}-{core_model}"


def default_segmented_startup_run_dir(
    repo_root: Path, *, scenario: str, core_model: str, plant: str = DEFAULT_PLANT_ID
) -> Path:
    """Default run directory of a ``--package segmented`` startup run.

    Review 2026-10-01 M6: ``00runs/startup-*`` is the published pre-fix
    record, so segmented startups default to the same run name under
    ``00runs/segmented/`` (``00runs/segmented/startup-<scenario>-<core>/``),
    with no further ``segmented/`` component. An explicit ``--run_dir``
    keeps the ``<run_dir>/segmented/`` nesting. A plant other than the
    default nests one level deeper, ``00runs/segmented/<plant>/``.
    """

    return segmented_results_base(repo_root, plant) / f"startup-{scenario}-{core_model}"


def startup_result_prefix(*, scenario: str, core_model: str) -> str:
    """Return the default result file prefix for a startup scenario/core pair."""

    table = STARTUP_SCENARIO_FILE_PREFIX.get(scenario) or SCALED_PLANT_SCENARIO_FILE_PREFIX[scenario]
    return table[core_model]


def default_startup_csv_path(repo_root: Path, *, scenario: str, core_model: str) -> Path:
    """Return the default startup result CSV path for a run definition."""

    run_dir = default_startup_run_dir(
        repo_root,
        scenario=scenario,
        core_model=core_model,
    )
    return run_dir / f"{startup_result_prefix(scenario=scenario, core_model=core_model)}_res.csv"
