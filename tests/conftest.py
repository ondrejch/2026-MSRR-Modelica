"""Route pytest/omc scratch into 00runs/tmp (never /tmp or the repo root)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = REPO_ROOT / "00runs" / "tmp"
PYTEST_BASETEMP = SCRATCH_ROOT / "pytest"


def _ensure_scratch() -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(SCRATCH_ROOT)
    os.environ["TEMP"] = str(SCRATCH_ROOT)
    os.environ["TMP"] = str(SCRATCH_ROOT)
    tempfile.tempdir = None
    return SCRATCH_ROOT


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


_ensure_scratch()


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    _ensure_scratch()
    current = getattr(config.option, "basetemp", None)
    if current is None or not _is_under(Path(current), SCRATCH_ROOT):
        PYTEST_BASETEMP.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = PYTEST_BASETEMP


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop this session's pytest scratch after a green run.

    Failed/interrupted runs keep 00runs/tmp/pytest for inspection.
    Exit 0 (ok) and 5 (no tests collected) are cleaned. Never touch
    00runs/startup-*, 00runs/freq/, or 00runs/transients-*.
    """
    if session.testsfailed or exitstatus not in (0, 5):
        return
    basetemp = getattr(session.config.option, "basetemp", None)
    if basetemp is None:
        return
    basetemp_path = Path(basetemp)
    if _is_under(basetemp_path, SCRATCH_ROOT):
        shutil.rmtree(basetemp_path, ignore_errors=True)
