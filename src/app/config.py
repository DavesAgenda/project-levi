"""Config loader — parse YAML config files into dicts."""

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load a YAML file from the config directory and return as dict."""
    filepath = CONFIG_DIR / filename
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_chart_of_accounts() -> dict[str, Any]:
    """Load chart_of_accounts.yaml."""
    return _load_yaml("chart_of_accounts.yaml")


def load_properties() -> dict[str, Any]:
    """Load properties.yaml."""
    return _load_yaml("properties.yaml")


def load_payroll() -> dict[str, Any]:
    """Load payroll.yaml."""
    return _load_yaml("payroll.yaml")


def load_mission_giving() -> dict[str, Any]:
    """Load mission_giving.yaml."""
    return _load_yaml("mission_giving.yaml")
