"""Shared path conventions for frequency workflow artifacts."""

from __future__ import annotations

from pathlib import Path

try:
    from helpers.power_tags import (
        POWER_TAG_FORMAT_VERSION,
        check_power_tag_collisions,
        freq_power_tag,
    )
except ImportError:  # script-style execution from freq/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helpers.power_tags import (
        POWER_TAG_FORMAT_VERSION,
        check_power_tag_collisions,
        freq_power_tag,
    )

__all__ = [
    "POWER_TAG_FORMAT_VERSION",
    "check_power_tag_collisions",
    "default_freq_case_dir",
    "default_freq_core_dir",
    "default_freq_plot_dir",
    "default_freq_results_root",
    "default_freq_time_compare_out_path",
    "default_segmented_freq_results_root",
    "make_freq_slug",
    "make_power_tag",
]


def make_power_tag(power: float) -> str:
    """Return the frequency-campaign power tag used in directory names.

    Uses the stripped historical spelling (``1``, ``0p1``, ``0p00001``)
    so committed ``00runs/freq/<core>/power_<tag>/`` trees stay
    byte-identical. Setpoint tables keep the unstripped ``%.5f`` form
    via :func:`helpers.power_tags.sanitize_power_tag`. Both are injective.
    """

    return freq_power_tag(power)


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


def default_freq_case_dir(
    repo_root: Path,
    *,
    core_model: str,
    power: float,
    package: str = "legacy",
    plant: str = "msrr",
) -> Path:
    """Return the default run directory for one core/power definition.

    Segmented runs (``package="segmented"``) default to the same layout under
    ``00runs/segmented/freq/`` (``00runs/segmented/freq/<core>/power_<tag>``;
    review 2026-10-01 M6): ``00runs/freq/`` is the published pre-fix record,
    and legacy collectors glob ``00runs/freq/<core>/power_<tag>`` directly.
    """
    if str(package) == "segmented":
        case_root = default_segmented_freq_results_root(repo_root, plant=plant) / core_model
    else:
        case_root = default_freq_core_dir(repo_root, core_model=core_model)
    return case_root / f"power_{make_power_tag(power)}"


def default_segmented_freq_results_root(repo_root: Path, *, plant: str = "msrr") -> Path:
    """Root of the ``--package segmented`` sweep defaults (review M6); a plant
    other than msrr nests under ``00runs/segmented/<plant>/freq``."""

    base = repo_root / "00runs" / "segmented"
    return (base if plant == "msrr" else base / str(plant)) / "freq"


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
