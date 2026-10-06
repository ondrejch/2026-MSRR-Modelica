"""Shared helpers for the startup plotting scripts.

Import-safe on unsupported interpreters: this module imports neither
matplotlib nor numpy/pandas, so each plotter can call
:func:`ensure_supported_python` (the python3.12 re-exec preamble) before
its own plotting imports.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:
    from .paths import (
        default_segmented_startup_run_dir,
        default_startup_csv_path,
        default_startup_run_dir,
    )
except ImportError:  # script-style execution from startup/
    from paths import (
        default_segmented_startup_run_dir,
        default_startup_csv_path,
        default_startup_run_dir,
    )

try:
    from helpers.scenario_config import CORE_CHOICES, SEGMENTED_ONLY_CORES
except ImportError:  # script-style execution from startup/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.scenario_config import CORE_CHOICES, SEGMENTED_ONLY_CORES


def ensure_supported_python() -> None:
    """Re-exec under python3.12 when launched from an unsupported interpreter."""
    if sys.version_info < (3, 13):
        return
    if os.environ.get("MSRR_PLOT_REEXEC") == "1":
        return
    py312 = shutil.which("python3.12")
    if py312 is None:
        raise SystemExit(
            "Python 3.13 detected, but this environment's NumPy/Matplotlib build is not "
            "compatible. Run with python3.12 (or install matching 3.13 wheels)."
        )
    os.environ["MSRR_PLOT_REEXEC"] = "1"
    try:
        os.execv(py312, [py312, *sys.argv])
    except OSError as exc:
        # A broken python3.12 (missing, non-executable, bad loader) would
        # otherwise surface as a raw OSError traceback; fail closed with a
        # clean message naming the interpreter (kept seam-testable: the
        # interpreter still comes from shutil.which, the handoff from
        # os.execv).
        raise SystemExit(
            f"Cannot re-exec under {py312}: {exc}. Run with python3.12 "
            "(or install matching 3.13 wheels)."
        ) from exc


#: Extra path component nested below the resolved run directory for
#: segmented-package runs. Mirrors ``runMSRR.STARTUP_SEGMENTED_DIR_COMPONENT``
#: (the runner owns the write side; plotters only probe the read side).
STARTUP_SEGMENTED_DIR_COMPONENT = "segmented"

#: ``SEGMENTED_ONLY_CORES`` (imported from ``helpers.scenario_config`` above,
#: the module that publishes it for every runner): cores the legacy package
#: ships no vehicle for, whose result CSVs can only exist in a segmented
#: location (the segmented default run directory, or the ``segmented/``
#: subdirectory of a run directory).


#: ``--package`` choices of the startup plotters, mirroring the runner's
#: ``startup.runMSRR --package`` (review 2026-10-02 follow-up).
PACKAGE_CHOICES = ("legacy", "segmented")


def add_package_argument(parser) -> None:
    """Add the plotters' ``--package`` option (default: legacy)."""

    parser.add_argument(
        "--package",
        choices=PACKAGE_CHOICES,
        default="legacy",
        help=(
            "Package of the run to plot, as passed to startup.runMSRR "
            "(default: legacy). With 'segmented', the default CSV is the "
            "segmented output (00runs/segmented/startup-<scenario>-<core>/, "
            "or <run_dir>/segmented/ with --run_dir), so a segmented run is "
            "never plotted from the legacy record by accident."
        ),
    )


def resolve_startup_csv_path(
    repo_root: Path,
    *,
    scenario: str,
    core_model: str,
    run_dir: "Path | str | None" = None,
    package: str = "legacy",
) -> Path:
    """Resolve the default startup result CSV path for a run definition.

    Shared rule for every startup plotter: an explicit ``--run_dir``
    replaces the run directory for the default CSV (and output) file
    names; without it the repo-root default
    ``00runs/startup-<scenario>-<core_model>`` applies (a ``run_dir`` equal
    to that default counts as no ``--run_dir``).

    With an explicit ``--run_dir``, segmented-package runs nest the CSV under
    ``<run_dir>/segmented/``: segmented-only cores (``1r10seg``,
    ``r5x5_z10`` -- the legacy package has no vehicle for them) always
    resolve there, while ``1r``/``9r`` keep the legacy location and probe
    ``segmented/`` as a fallback.

    Without one (review 2026-10-01 M6), segmented runs live at their own
    default ``00runs/segmented/startup-<scenario>-<core_model>/``. ``1r``/
    ``9r`` resolve the legacy CSV first, then that segmented default, then
    the pre-move nested ``00runs/startup-<scenario>-<core_model>/segmented/``
    location (read-only fallback for older outputs), and name the legacy
    CSV when none exists. Segmented-only cores resolve the segmented
    default, then the pre-move nested location, and name the segmented
    default when neither exists.

    ``package="segmented"`` (the plotters' ``--package segmented``) skips the
    legacy CSV for ``1r``/``9r`` as well: the segmented default (or
    ``<run_dir>/segmented/``) first, then the pre-move nested location.
    With the legacy default, a segmented result that is newer than the
    legacy CSV selected for ``1r``/``9r`` is reported on stderr, because
    the legacy location may hold an older record (review 2026-10-02
    follow-up).
    """
    if package not in PACKAGE_CHOICES:
        raise ValueError(f"package must be one of {PACKAGE_CHOICES}, got {package!r}")
    default_csv = default_startup_csv_path(
        repo_root,
        scenario=scenario,
        core_model=core_model,
    )
    legacy_default_dir = default_startup_run_dir(
        repo_root,
        scenario=scenario,
        core_model=core_model,
    )
    explicit = run_dir is not None and Path(run_dir) != legacy_default_dir
    base_dir = Path(run_dir) if explicit else legacy_default_dir
    legacy_path = base_dir / default_csv.name
    nested_path = base_dir / STARTUP_SEGMENTED_DIR_COMPONENT / default_csv.name
    segmented_only = core_model in SEGMENTED_ONLY_CORES or package == "segmented"
    if explicit:
        if segmented_only:
            return nested_path
        if legacy_path.exists():
            _note_newer_segmented(legacy_path, (nested_path,))
            return legacy_path
        if nested_path.exists():
            return nested_path
        return legacy_path
    segmented_default = (
        default_segmented_startup_run_dir(
            repo_root,
            scenario=scenario,
            core_model=core_model,
        )
        / default_csv.name
    )
    if segmented_only:
        candidates = (segmented_default, nested_path)
        fallback = segmented_default
    else:
        candidates = (legacy_path, segmented_default, nested_path)
        fallback = legacy_path
    for candidate in candidates:
        if candidate.exists():
            if candidate == legacy_path:
                _note_newer_segmented(legacy_path, (segmented_default, nested_path))
            return candidate
    return fallback


def _note_newer_segmented(chosen: Path, segmented: "tuple[Path, ...]") -> None:
    """Report a segmented result newer than the legacy CSV chosen by default."""

    try:
        chosen_mtime = chosen.stat().st_mtime
    except OSError:
        return
    for path in segmented:
        try:
            newer = path.stat().st_mtime > chosen_mtime
        except OSError:
            continue
        if newer:
            print(
                f"note: plotting the legacy result {chosen}; a newer segmented "
                f"result exists at {path} (pass --package segmented, or --csv, "
                "to plot it)",
                file=sys.stderr,
            )
            return


def startup_plot_run_dir(
    repo_root: Path,
    *,
    scenario: str,
    core_model: str,
    run_dir: "Path | str",
    csv: "Path | str",
) -> Path:
    """Directory a startup plotter writes its default outputs into.

    ``run_dir`` (the resolved ``--run_dir``), except for a CSV that lies in
    the segmented default run directory
    ``00runs/segmented/startup-<scenario>-<core_model>/`` (review 2026-10-01
    M6): its plots go beside it, never into the published
    ``00runs/startup-*`` record.
    """
    segmented_dir = default_segmented_startup_run_dir(
        repo_root,
        scenario=scenario,
        core_model=core_model,
    )
    if Path(csv).resolve().parent == segmented_dir.resolve():
        return segmented_dir
    return Path(run_dir)
