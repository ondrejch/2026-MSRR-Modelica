"""Shared path conventions for transient workflow artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def _normalize_core_models(core_models: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for core_model in core_models:
        if core_model not in ordered:
            ordered.append(core_model)
    return tuple(ordered)


def transients_core_models_tag(core_models: Iterable[str]) -> str:
    """Return a stable directory tag for a selected core-model set."""

    models = _normalize_core_models(core_models)
    if not models:
        return "none"
    return "-".join(models)


def default_transients_run_dir(repo_root: Path, *, core_models: Iterable[str]) -> Path:
    """Return default run directory under 00runs for transient studies."""

    return repo_root / "00runs" / f"transients-{transients_core_models_tag(core_models)}"


def default_segmented_transients_run_dir(
    repo_root: Path, *, core_models: Iterable[str], plant: str = "msrr"
) -> Path:
    """Default run root of ``--package segmented`` transient studies.

    Review 2026-10-01 M6: ``00runs/transients-*`` is the published pre-fix
    record, so segmented runs default to the same run name under
    ``00runs/segmented/`` and write ``<root>/<core>/`` directly. An explicit
    ``--out_dir`` keeps the ``<out_dir>/segmented/<core>/`` nesting. A plant
    other than msrr nests one level deeper, ``00runs/segmented/<plant>/``.
    """

    base = repo_root / "00runs" / "segmented"
    if plant != "msrr":
        base = base / str(plant)
    return base / f"transients-{transients_core_models_tag(core_models)}"
