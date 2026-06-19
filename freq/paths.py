"""Shared path conventions for frequency workflow artifacts."""

from __future__ import annotations

from pathlib import Path


def make_power_tag(power: float) -> str:
    """Return the repository power tag used in directory names."""

    text = f"{float(power):.5f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return text.replace(".", "p")


def make_freq_slug(freq: float) -> str:
    """Return a filename-safe frequency tag for plot artifacts."""

    text = f"{float(freq):.5g}".lower().replace("+", "")
    text = text.replace("-", "m").replace(".", "p")
    return text


def default_freq_results_root(repo_root: Path) -> Path:
    """Return the root directory for frequency workflow artifacts."""

    return repo_root / "00runs" / "freq"


def default_freq_core_dir(repo_root: Path, *, core_model: str) -> Path:
    """Return the core-specific root directory under 00runs/freq."""

    return default_freq_results_root(repo_root) / core_model


def default_freq_case_dir(repo_root: Path, *, core_model: str, power: float) -> Path:
    """Return the default run directory for one core/power definition."""

    return default_freq_core_dir(repo_root, core_model=core_model) / f"power_{make_power_tag(power)}"


def default_freq_plot_dir(repo_root: Path) -> Path:
    """Return the default directory for frequency-derived plots."""

    return default_freq_results_root(repo_root) / "plots"


def default_freq_time_compare_out_path(
    repo_root: Path,
    *,
    power: float,
    freq: float,
) -> Path:
    """Return default output path for a time-domain comparison plot."""

    return default_freq_plot_dir(repo_root) / (
        f"freq_time_compare_power_{make_power_tag(power)}"
        f"_omega_{make_freq_slug(freq)}.png"
    )
