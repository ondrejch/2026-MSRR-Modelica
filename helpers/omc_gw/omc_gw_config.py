"""User-level configuration helpers for the OMC SSH gateway.

System-specific settings are intentionally kept out of the repository and read
from a per-user JSON file. The config file path is resolved from
``MSRR_OMC_GW_CONFIG`` when set, otherwise from
``~/.config/msrr_omc_gw/config.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_ENV_VAR = "MSRR_OMC_GW_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "msrr_omc_gw" / "config.json"


def config_path_hint() -> str:
    """Return a human-readable hint for where configuration is loaded from."""

    return f"{CONFIG_ENV_VAR} or {DEFAULT_CONFIG_PATH}"


def candidate_config_paths() -> list[Path]:
    """Return the ordered list of candidate config paths."""

    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return [Path(env_path).expanduser()]
    return [DEFAULT_CONFIG_PATH]


def load_user_config() -> dict[str, Any]:
    """Load the first existing JSON config file, or return an empty config."""

    for path in candidate_config_paths():
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"OMC gateway config must contain a JSON object: {path}")
        return payload
    return {}


def get_config_str(config: dict[str, Any], key: str) -> str | None:
    """Return a string value from *config* if present and non-empty."""

    raw = config.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def get_config_int(config: dict[str, Any], key: str, default: int | None = None) -> int | None:
    """Return an integer config value with optional default."""

    raw = config.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for config key {key!r}: {raw!r}") from exc


def get_config_list(config: dict[str, Any], key: str) -> list[str] | None:
    """Return a list-of-strings config value, accepting CSV strings."""

    raw = config.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or None
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
        return values or None
    raise ValueError(f"Invalid list value for config key {key!r}: {raw!r}")
