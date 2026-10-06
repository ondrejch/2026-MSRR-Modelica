#!/usr/bin/env python3
"""Classify a project tree as a real Git checkout or a gitless copy.

TASK-20260904-03 P3: the archive-packer tests
(``tests/test_make_source_archive.py``) re-pack the tree through
``git ls-files``/``git rev-parse``, so they can only run from a real Git
checkout. From an unpacked source-archive extract -- the gitless consumer
tree a release ships -- they must SKIP with a precise reason instead of
failing. The registered ``source_checkout`` marker (``pyproject.toml``,
``[tool.pytest.ini_options]``) marks those tests, and this module supplies
the detection and the human-readable skip message the marker wiring uses.

A "real Git checkout" here means BOTH:

- the project root (the directory containing ``helpers/``) carries a
  ``.git`` entry: a directory for an ordinary clone, a plain file for a
  linked worktree or submodule; a SYMLINK ``.git`` is refused outright
  (the foreign-git provenance vector, rev022 N-1: git would report the
  symlinked tree's own toplevel while the commits come from the target
  repository -- a real worktree uses a ``.git`` file, not a symlink);
- ``git`` is resolvable on PATH, accepts the tree as a repository, AND
  resolves that tree itself as the repository toplevel (a ``.git``
  directory or file rooting its repository elsewhere is refused --
  symlinked ``.git`` entries never reach that check: they are refused
  outright above);
- a ``.git`` FILE is accepted only when its resolved git dir is a
  registered worktree admin dir whose ``gitdir`` record points back at
  this tree's ``.git`` file (rev022 P2: a hand-crafted, unregistered
  ``gitdir:`` pointer into a FOREIGN repository's ``.git`` passes the
  toplevel check -- git reports this tree as toplevel while HEAD comes
  from the foreign repo -- so it is refused with a reason naming the
  foreign git dir; REV-4175b5f-01: the same holds when the pointer names
  a NESTED foreign repository's ``.git`` UNDER this tree's root -- the
  under-root location alone accepts nothing, and the nested ``.git``
  carries no back-pointer record, so it is likewise refused);

Everything else -- an unpacked archive extract, a plain file copy, a tree
whose ``git`` is unavailable -- is gitless for the packer tests' purposes.
The check looks for ``.git`` at the project root ONLY and deliberately
never walks upward: an extract nested inside a checkout must not inherit
the enclosing repository's identity (the same silent-provenance case the
archiver refuses; see ``helpers.make_source_archive.resolve_repo_root``).

Standalone by design (standard library only), so the module imports
identically from a checkout and from an extract's own ``helpers/`` copy.

Usage (skip wiring is the test-side half of the P3 ownership split)::

    from helpers.git_checkout import git_checkout_skip_reason

    reason = git_checkout_skip_reason()
    if reason:
        pytest.skip(reason)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["is_real_git_checkout", "git_checkout_skip_reason"]


def _default_root() -> Path:
    """The project tree whose ``helpers/`` directory holds this module."""

    return Path(__file__).resolve().parents[1]


def git_checkout_skip_reason(start: str | Path | None = None) -> str | None:
    """Return why ``start`` is not a real Git checkout, or ``None`` if it is.

    ``start`` defaults to the project tree that contains this module's
    ``helpers/`` directory, which is the repository root in a checkout and
    the extract root in an unpacked source archive. A ``None`` return means
    the tree is a working Git checkout and the git-requiring tests may run;
    a string return is a precise, self-contained reason suitable for a
    ``pytest.skip`` message: it states why the tree is not a usable
    checkout and where the full suite belongs (a Git clone of the
    repository).
    """

    root = (
        Path(start).expanduser().resolve() if start is not None else _default_root()
    )
    git_entry = root / ".git"
    if not git_entry.exists():
        return (
            f"not a real Git checkout: {root} carries no .git (this is an "
            "unpacked source-archive extract or a plain copy), so the "
            "archive-packer tests cannot re-pack the tree through git; run "
            "the full suite from a Git clone of the repository (the "
            "documented gitless consumer subset in tests/README.md still "
            "runs here)"
        )
    if git_entry.is_symlink():
        # A symlinked .git is the foreign-provenance vector (rev022 N-1):
        # `git -C <root> rev-parse --show-toplevel` reports the symlinked
        # tree itself while the commits come from the symlink target's
        # repository, so the toplevel comparison below would pass and this
        # tree would be stamped with a foreign HEAD. Refuse outright: a
        # real git worktree uses a .git FILE (gitfile), not a symlink, so
        # symlinking .git (to anywhere) can only import foreign provenance.
        target = git_entry.resolve()
        return (
            f"not a real Git checkout: {git_entry} is a symlink pointing at "
            f"{target}: git would resolve this tree against the target "
            f"repository and stamp {root} with a foreign commit; refusing "
            "foreign-git provenance (a real worktree uses a .git FILE, not "
            "a symlink); the archive-packer tests cannot re-pack THIS tree "
            "through git; run the full suite from a Git clone of the "
            "repository"
        )
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=60,  # rev022 M-5: bounded like the other git probes
        )
    except FileNotFoundError:
        return (
            "git executable not found on PATH: the archive-packer tests "
            f"cannot query repository state in {root} (a .git entry exists "
            "but git itself is unavailable); run the full suite from a Git "
            "clone of the repository with git on PATH (the documented "
            "gitless consumer subset in tests/README.md still runs here)"
        )
    except subprocess.TimeoutExpired:
        # rev022 M-5: a wedged git (hung index lock, filesystem stall) must
        # fail toward the conservative side: the tree is NOT verified as a
        # real checkout, so the git-requiring tests skip.
        return (
            "not a real Git checkout: git did not answer within 60 s in "
            f"{root} (wedged index or filesystem stall); the archive-packer "
            "tests cannot verify repository state here; run the full suite "
            "from a Git clone of the repository"
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "no detail"
        return (
            f"git cannot verify {root} as a Git repository ({detail}); the "
            "archive-packer tests need a working Git checkout; run the "
            "full suite from a Git clone of the repository"
        )
    # git accepted the tree as A repository -- verify it is THIS one
    # (rev021): a .git FILE (worktree gitfile) or DIRECTORY whose git data
    # roots the repository elsewhere makes rev-parse resolve to a different
    # toplevel, which would misclassify the wrong tree as a real checkout.
    # (Symlinked .git entries are already refused above and never reach
    # this branch.) Both sides are resolved paths.
    toplevel = Path(completed.stdout.strip()).resolve()
    if toplevel != root:
        kind = (
            "directory"
            if git_entry.is_dir()
            else "file" if git_entry.is_file() else "entry"
        )
        return (
            f"git resolves the repository containing {root} to a different "
            f"toplevel ({toplevel}): the .git {kind} in {root} roots its "
            f"repository elsewhere (not THIS tree), so the archive-packer "
            f"tests cannot re-pack THIS tree through git; run the full "
            f"suite from a Git clone of the repository"
        )
    if git_entry.is_file():
        # rev022 P2 (TASK-20260922-01): a hand-crafted, UNREGISTERED .git
        # FILE passes the toplevel check above -- real git reports this
        # tree as toplevel while HEAD comes from the foreign repository
        # the `gitdir:` pointer names -- so the toplevel comparison alone
        # cannot see the mix-up (probed with real git: toplevel == this
        # tree, `--short HEAD` == the foreign commit, `--absolute-git-dir`
        # == the foreign .git). Rejecting every .git FILE outright is
        # wrong (a registered `git worktree` checkout legitimately carries
        # one). REV-4175b5f-01: accepting every git dir under this tree's
        # root alone is likewise insufficient -- a NESTED foreign
        # repository's .git lives under the root too (probed: toplevel ==
        # victim, HEAD == nested repo's commit) yet carries no worktree
        # registration. So a .git FILE is accepted only with git's own
        # registration tying the git dir back to THIS checkout (the
        # worktree-admin `gitdir` record); anything else is refused.
        return _refuse_unregistered_git_file(root)
    return None


def _worktree_admin_points_back_at(root: Path, git_dir: Path) -> bool:
    """True when ``git_dir`` is a registered worktree admin dir of ``root``.

    Git's own registration record ``<gitdir>/gitdir`` names the
    worktree's ``.git`` file; it must point back at this tree's ``.git``
    file. A foreign repository's main ``.git`` -- whether outside this
    tree or nested under its root -- carries no such record.
    """

    try:
        registered = (git_dir / "gitdir").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not registered:
        return False
    back = Path(registered)
    if not back.is_absolute():
        back = git_dir / back
    return back.resolve() == (root / ".git").resolve()


def _refuse_unregistered_git_file(root: Path) -> str | None:
    """Return why the ``.git`` FILE in ``root`` is refused, if it is.

    The caller has already established that git accepts ``root`` as a
    repository rooted at ``root`` itself. Returns ``None`` when the
    file-backed git dir belongs to this tree (accepted); otherwise a
    precise skip reason naming the foreign git dir.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            timeout=60,  # rev022 M-5: bounded like the other git probes
        )
    except FileNotFoundError:
        return (
            "git executable not found on PATH: the archive-packer tests "
            f"cannot query repository state in {root} (a .git entry exists "
            "but git itself is unavailable); run the full suite from a Git "
            "clone of the repository with git on PATH (the documented "
            "gitless consumer subset in tests/README.md still runs here)"
        )
    except subprocess.TimeoutExpired:
        # rev022 M-5: a wedged git (hung index lock, filesystem stall) must
        # fail toward the conservative side: the tree is NOT verified as a
        # real checkout, so the git-requiring tests skip.
        return (
            "not a real Git checkout: git did not answer within 60 s in "
            f"{root} (wedged index or filesystem stall); the archive-packer "
            "tests cannot verify repository state here; run the full suite "
            "from a Git clone of the repository"
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "no detail"
        return (
            f"git cannot resolve the git dir backing {root} ({detail}); the "
            "archive-packer tests need a working Git checkout; run the "
            "full suite from a Git clone of the repository"
        )
    raw = completed.stdout.strip()
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    git_dir = git_dir.resolve()
    if git_dir == root or root in git_dir.parents:
        # REV-4175b5f-01: the under-root location alone accepts nothing.
        # A nested foreign repository's .git lives under this tree's root
        # too, and real git still reports this tree as toplevel while HEAD
        # comes from the nested repo -- so the git dir is accepted only
        # with the same worktree-admin registration a registered worktree
        # carries (a nested foreign .git has no such record).
        if _worktree_admin_points_back_at(root, git_dir):
            return None
        return (
            f"not a real Git checkout: the .git file in {root} points at "
            f"the foreign git dir {git_dir} inside this tree with no "
            f"worktree registration tying it back to this checkout (a "
            f"nested repository's .git carries no such record): git would "
            f"stamp {root} with that repository's commits (foreign-git "
            f"provenance); run the full suite from a Git clone of the "
            f"repository"
        )
    # The git dir lives outside this tree: accept only a REGISTERED
    # worktree of THIS tree. Git's own admin record `<gitdir>/gitdir`
    # names the worktree's .git file; it must point back at this tree.
    # (A foreign repository's main .git carries no such record.)
    if _worktree_admin_points_back_at(root, git_dir):
        return None
    return (
        f"not a real Git checkout: the .git file in {root} points at the "
        f"foreign git dir {git_dir}, which is neither inside this tree nor "
        f"a registered worktree of it: git would stamp {root} with that "
        f"repository's commits (foreign-git provenance); run the full "
        f"suite from a Git clone of the repository"
    )


def is_real_git_checkout(start: str | Path | None = None) -> bool:
    """Return True when ``start`` is a real, git-verifiable Git checkout.

    See :func:`git_checkout_skip_reason` for the exact criteria; this is
    its boolean form for callers that only need the classification.
    """

    return git_checkout_skip_reason(start) is None
