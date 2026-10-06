"""Locate the MSRR plant/scenario data tree for every supported install mode.

The authored data (YAML plant decks, scenario decks, JSON schemas) lives in
the repository's ``data/`` directory. Two install modes are supported and
both resolve through this module:

1. **Source checkout or editable install.** The repository root is
   discovered by walking upward from this file's location for the markers
   ``pyproject.toml`` + ``core/`` + ``helpers/`` (historical behavior,
   unchanged), and ``data/`` is read in place.
2. **Wheel install.** The data tree ships inside the distribution as the
   importable package ``msrr_data`` (mapped from ``data/`` by
   ``[tool.setuptools.package-dir]`` in ``pyproject.toml``) and is located
   through :func:`importlib.resources.files`.

The checkout branch only applies when the *running* ``helpers`` copy
belongs to the discovered checkout (an installed copy whose site-packages
happens to sit inside a checkout tree must not silently read that
checkout's data). Anything unsupported fails with
:class:`DataUnavailableError` -- a :class:`FileNotFoundError` subclass
whose message names what is absent and how to fix it, instead of a bare
missing-file traceback.
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
from pathlib import Path

#: Importable anchor package that carries the data tree inside the wheel.
#: ``pyproject.toml`` maps it onto the repository's ``data/`` directory
#: (``[tool.setuptools.package-dir] msrr_data = "data"``).
DATA_PACKAGE = "msrr_data"

#: Markers identifying a source checkout (directory that contains them).
REPO_MARKERS = ("pyproject.toml", "core", "helpers")

#: Paths that must exist inside a healthy ``msrr_data`` package.
_DATA_CONTENT_MARKERS = ("plants", "schema/plant.schema.json")

_THIS_DIR = Path(__file__).resolve().parent


class DataUnavailableError(FileNotFoundError):
    """The plant/scenario data tree is not available in this install mode."""


def _packaged_anchor_path(package: str = DATA_PACKAGE) -> Path:
    """Filesystem path of an installed resource package.

    Wheels are unpacked by ``pip`` into site-packages, so the anchor
    resolves to a real directory. The packaged ``msrr_data`` payload is
    a regular package (it ships an ``__init__.py``), so
    ``importlib.resources.files`` normally reports a single directory;
    the resolver also tolerates a backing without ``__init__.py``
    (namespace packages), where ``files`` wraps the locations in a
    ``MultiplexedPath`` and the first filesystem candidate holding the
    expected content is used. Any other backing (zip import, missing
    content) is rejected with the install-fix message.
    """

    try:
        anchor = importlib.resources.files(package)
    except ModuleNotFoundError as exc:
        raise DataUnavailableError(
            f"the packaged data package {package!r} is not importable; the "
            "smd-msrr distribution does not appear to be installed. Fix: "
            "install the wheel (`pip install smd-msrr`) or run from a "
            "complete source checkout."
        ) from exc
    candidates = [Path(str(item)) for item in getattr(anchor, "paths", (anchor,))]
    filesystem_backed = [item for item in candidates if item.is_dir()]
    if not filesystem_backed:
        raise DataUnavailableError(
            f"the packaged data package {package!r} is not filesystem-backed "
            f"(anchor: {anchor!r}). Zip-imported installations are "
            "unsupported. Fix: install the wheel normally so files unpack "
            "into site-packages (`pip install smd-msrr`)."
        )
    for item in filesystem_backed:
        if all((item / name).exists() for name in _DATA_CONTENT_MARKERS):
            return item
    raise DataUnavailableError(
        f"the installed {package!r} package is missing its data content "
        f"(no {', '.join(_DATA_CONTENT_MARKERS)} under {filesystem_backed[0]}); "
        "the wheel that provided it is incomplete. Fix: reinstall smd-msrr "
        "from a clean wheel (`pip install --force-reinstall smd-msrr`)."
    )


def checkout_root(start: Path | None = None) -> Path | None:
    """Return the source-checkout root, or ``None`` when this is not one.

    Walks upward from ``start`` (default: this file's directory) until a
    directory containing every :data:`REPO_MARKERS` entry is found.
    """

    here = (start or Path(__file__).resolve()).parent
    for candidate in (here, *here.parents):
        if all((candidate / marker).exists() for marker in REPO_MARKERS):
            return candidate
    return None


def _running_copy_owns_checkout(checkout: Path) -> bool:
    """True when this module is the checkout's own ``helpers`` copy.

    Guards the nested-install case: a wheel installed into a virtualenv
    that happens to live *inside* a checkout would otherwise have its
    marker walk terminate on the enclosing repository and read that
    checkout's data instead of its own packaged payload.
    """

    return _THIS_DIR == (checkout / "helpers").resolve()


def resolve_repo_root(start: Path | None = None) -> Path:
    """Return the directory that contains the ``core/`` and ``helpers/`` trees.

    Mode 1 (checkout/editable install): the marker walk from
    :func:`checkout_root`, exactly the historical behavior. An explicit
    ``start`` pins the search; when it finds no checkout the lookup fails
    rather than silently consulting the installed package.

    Mode 2 (wheel install): the parent of the installed ``core`` package
    (site-packages), which also contains the ``msrr_data`` package.
    """

    if start is not None:
        root = checkout_root(start)
        if root is not None:
            return root
        here = Path(start).resolve().parent
        raise DataUnavailableError(
            "could not locate a repository root (markers "
            f"{list(REPO_MARKERS)}) from {here} upward"
        )
    root = checkout_root()
    if root is not None and _running_copy_owns_checkout(root):
        return root
    try:
        anchor = importlib.resources.files("core")
    except ModuleNotFoundError as exc:
        raise DataUnavailableError(
            "could not locate a repository root (markers "
            f"{list(REPO_MARKERS)}) from {_THIS_DIR} upward, and smd-msrr "
            "is not installed as a package. Fix: run from a source checkout "
            "of the repository or install the wheel "
            "(`pip install smd-msrr`)."
        ) from exc
    installed_root = Path(str(anchor)).parent
    if not (installed_root / "helpers").is_dir():
        raise DataUnavailableError(
            f"the installed smd-msrr layout under {installed_root} is "
            "incomplete (no 'helpers' package next to 'core'); reinstall "
            "the wheel (`pip install --force-reinstall smd-msrr`)."
        )
    return installed_root


def resolve_data_root(root: Path | None = None) -> Path:
    """Return the directory holding ``plants/``, ``scenarios/``, ``schema/``.

    ``root`` (explicit override, used by runners and tests with synthetic
    trees) maps onto ``<root>/data`` exactly as before, without touching
    the filesystem.

    Otherwise: in a checkout whose running ``helpers`` copy owns it, the
    in-tree ``data/`` directory is used (unchanged behavior); when the
    checkout markers are present but ``data/`` itself is missing, the
    error names the absent directory and both remedies. Outside a
    checkout (wheel install), the packaged ``msrr_data`` package is used.
    """

    if root is not None:
        return Path(root) / "data"
    checkout = checkout_root()
    if checkout is not None and _running_copy_owns_checkout(checkout):
        data_dir = checkout / "data"
        if data_dir.is_dir():
            return data_dir
        raise DataUnavailableError(
            f"the source checkout at {checkout} has no data/ directory "
            "(plant YAML decks, scenario decks, and JSON schemas are "
            "absent). Fix: restore data/ from the repository or release "
            "archive, or install the wheel that bundles it "
            "(`pip install smd-msrr`)."
        )
    return _packaged_anchor_path()


def installed_distribution_version() -> str | None:
    """Version of the installed ``smd-msrr`` distribution, if any."""

    try:
        return importlib.metadata.version("smd-msrr")
    except importlib.metadata.PackageNotFoundError:
        return None


__all__ = [
    "DATA_PACKAGE",
    "DataUnavailableError",
    "REPO_MARKERS",
    "checkout_root",
    "installed_distribution_version",
    "resolve_data_root",
    "resolve_repo_root",
]
