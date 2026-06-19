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
