"""Route pytest/omc scratch into 00runs/tmp (never /tmp or the repo root)."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
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


def _session_basetemp() -> Path:
    """A basetemp directory unique to this pytest session.

    ``<pid>-<timestamp_ns>`` under ``00runs/tmp/pytest/``: concurrent
    green sessions in one tree never share a directory, so one
    session's green-exit cleanup cannot destroy another's scratch.
    """

    return PYTEST_BASETEMP / f"{os.getpid()}-{time.time_ns()}"


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
        session_dir = _session_basetemp()
        counter = 0
        while session_dir.exists():
            counter += 1
            session_dir = (
                PYTEST_BASETEMP / f"{os.getpid()}-{time.time_ns()}-{counter}"
            )
        session_dir.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = session_dir


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop this session's own pytest scratch after a green run.

    Failed/interrupted runs keep their ``00runs/tmp/pytest/<pid>-*``
    session directory for inspection. Exit 0 (ok) and 5 (no tests
    collected) clean up only the directory in ``session.config.option``,
    which is session-unique per :func:`pytest_configure` — a concurrent
    session's directory is never touched. Never touch
    00runs/startup-*, 00runs/freq/, or 00runs/transients-*.
    """
    if session.testsfailed or exitstatus not in (0, 5):
        return
    basetemp = getattr(session.config.option, "basetemp", None)
    if basetemp is None:
        return
    basetemp_path = Path(basetemp)
    # Own directory only: it must be a session-unique child of
    # 00runs/tmp/pytest, never the parent itself. This bounds cleanup to
    # exactly the directory this session created.
    if (
        basetemp_path.resolve() == PYTEST_BASETEMP.resolve()
        or not _is_under(basetemp_path, PYTEST_BASETEMP)
        or not _is_under(basetemp_path, SCRATCH_ROOT)
    ):
        return
    shutil.rmtree(basetemp_path, ignore_errors=True)
