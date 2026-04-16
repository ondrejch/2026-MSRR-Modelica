"""Core Modelica assets and setpoint-table tooling for MSRR workflows."""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def core_dir() -> Path:
    """Return the package path that contains the shared Modelica sources."""

    return PACKAGE_ROOT


def modelica_library_path() -> Path:
    """Return the packaged ``SMD_MSR_Modelica.mo`` path."""

    return PACKAGE_ROOT / "SMD_MSR_Modelica.mo"


def modelica_model_path() -> Path:
    """Return the packaged ``MSRR.mo`` path."""

    return PACKAGE_ROOT / "MSRR.mo"


def setpoints_path(core_model: str) -> Path:
    """Return the packaged setpoint table for the requested core model."""

    core_key = core_model.lower()
    if core_key not in {"1r", "9r"}:
        raise ValueError(f"Unsupported core model: {core_model}")
    return PACKAGE_ROOT / "init" / f"setpoints_{core_key}.csv"


__all__ = [
    "PACKAGE_ROOT",
    "core_dir",
    "modelica_library_path",
    "modelica_model_path",
    "setpoints_path",
]
