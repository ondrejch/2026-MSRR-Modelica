"""Shared refuse-published-record-tree guard (test fakes and segmented runners).

One implementation of the guard that keeps the fake omc/CSV-writing helpers
used by the segmented-mode test modules from ever writing into the published
record trees under ``00runs/``.  A pre-redirect diagnostic run is how the
published record got contaminated once (the incident hit
``00runs/transients-1r/segmented/1r/``), so every fake executable and fake
result-CSV writer must refuse any working directory that resolves under
``00runs/freq/``, ``00runs/startup-*``, or ``00runs/transients-*``.

History: the guard was introduced per test module (REV-a98c520-01) in
``tests/test_startup_segmented_mode.py`` and
``tests/test_transients_segmented_mode.py``; the freq twin
``tests/test_freq_segmented_mode.py`` was missed (review M4 /
REV008-11 / predecessor nit REV-8d60c0c-01).  This module is the single
source of truth all three helpers call, so the twins carry no local copy and
the freq helpers gain the guard.

Test modules import it like any other helper package member::

    from helpers.published_tree_guard import refuse_published_tree_cwd

``refuse_published_tree_cwd`` is a drop-in replacement for the per-module
``_refuse_published_tree_cwd(cwd)`` helpers it replaces (same signature, same
refusal message); the freq helpers can call either name.

Production runners (review 2026-10-01 M6): the segmented routes of
``startup/runMSRR.py``, ``transients/run_nonlinear_steps.py`` and
``freq/runFreqNominalParallel.py`` write their defaults under
``00runs/segmented/`` and refuse an explicit output path inside a published
record tree through :func:`refuse_published_tree_output` (a ``ValueError``
naming the CLI flag, raised from each runner's ``validate_args`` before
anything is created). The legacy routes keep their historical defaults.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNS_ROOT = _REPO_ROOT / "00runs"

_REFUSAL_MESSAGE = (
    "fake-run helper refuses to write into published record tree "
    "{resolved}: 00runs/freq/, 00runs/startup-* and "
    "00runs/transients-* are the published record trees; point the "
    "runner at 00runs/tmp/ or a pytest tmp_path instead"
)


def is_under_published_record_tree(path: str | Path) -> bool:
    """True when *path* resolves under a published ``00runs/`` record tree."""
    resolved = Path(path).resolve()
    try:
        rel = resolved.relative_to(_RUNS_ROOT)
    except ValueError:  # not under the repository's 00runs/ at all
        return False
    first = rel.parts[0] if rel.parts else ""
    return first == "freq" or first.startswith(("transients-", "startup-"))


def refuse_published_tree_path(path: str | Path) -> None:
    """Raise when *path* resolves under a published record tree.

    ``00runs/tmp/`` pytest scratch and everything outside the repository's
    ``00runs/`` stay allowed.  The refusal is a hard ``RuntimeError`` so a
    misdirected runner aborts the test instead of silently appending to the
    published evidence.
    """
    resolved = Path(path).resolve()
    if is_under_published_record_tree(resolved):
        raise RuntimeError(_REFUSAL_MESSAGE.format(resolved=resolved))


#: Drop-in alias matching the name the ported per-module helpers used
#: (``_refuse_published_tree_cwd(cwd)`` in the startup/transients twins).
refuse_published_tree_cwd = refuse_published_tree_path


def refuse_published_tree_output(path: str | Path, *, flag: str) -> None:
    """Refuse a production output path under a published record tree.

    For the segmented runner routes (review 2026-10-01 M6): ``path`` is the
    explicit ``flag`` value (``--run_dir``, ``--out_dir`` or
    ``--base_dir``). Raises ``ValueError`` naming the flag and the resolved
    location; paths elsewhere, ``00runs/segmented/`` and ``00runs/tmp/``
    included, pass.
    """
    resolved = Path(path).resolve()
    if is_under_published_record_tree(resolved):
        raise ValueError(
            f"{flag} {resolved} resolves under a published pre-fix record "
            "tree (00runs/freq/, 00runs/startup-*, 00runs/transients-*), "
            "which segmented runs never write: omit "
            f"{flag} to use the segmented default under 00runs/segmented/, "
            "or pass a path outside those trees."
        )
