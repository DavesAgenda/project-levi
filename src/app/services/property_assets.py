"""Property asset service — maps balance sheet values to property config.

Loads properties.yaml, extracts land_asset / building_asset account codes,
and matches them against parsed balance sheet data to produce per-property
asset valuations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.xero.parser import (
    FixedAssetEntry,
    ParsedReport,
    extract_fixed_assets_by_code,
)

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PROPERTIES_PATH = CONFIG_DIR / "properties.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PropertyAssetValue:
    """Asset values for a single property."""

    property_key: str
    address: str
    land_code: str | None
    land_value: float
    building_code: str | None
    building_value: float

    @property
    def total_value(self) -> float:
        return self.land_value + self.building_value


@dataclass
class PropertyAssetSummary:
    """Summary of all property asset values from a balance sheet."""

    properties: list[PropertyAssetValue] = field(default_factory=list)
    total_land: float = 0.0
    total_buildings: float = 0.0
    total_assets: float = 0.0
    unmatched_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_properties_config(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load properties from properties.yaml.

    Returns:
        Dict of property_key -> property config dict.
    """
    config_path = path or PROPERTIES_PATH
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return raw.get("properties", {})


def get_asset_account_codes(
    properties: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Extract all land_asset and building_asset codes from properties config.

    Returns:
        {"land": ["65010", "65003", ...], "buildings": ["66010", "66007", ...]}
    """
    land_codes: list[str] = []
    building_codes: list[str] = []

    for _key, prop in properties.items():
        if "land_asset" in prop:
            land_codes.append(str(prop["land_asset"]))
        if "building_asset" in prop:
            building_codes.append(str(prop["building_asset"]))

    return {"land": land_codes, "buildings": building_codes}


# ---------------------------------------------------------------------------
# Balance sheet -> property asset mapping
# ---------------------------------------------------------------------------

def map_balance_sheet_to_properties(
    parsed_bs: ParsedReport,
    properties: dict[str, dict[str, Any]] | None = None,
    config_path: Path | None = None,
) -> PropertyAssetSummary:
    """Map balance sheet fixed asset values to property config entries.

    Uses a flat-row scan approach: walks all balance sheet rows and
    matches account names / codes against the property config's
    land_asset and building_asset codes.

    Because Xero balance sheet rows include the account code in the
    account name (e.g. "Land - 6 Example Street (65010)"), we match
    by checking if the configured code appears in the account name
    or if it matches a known UUID.

    Args:
        parsed_bs: ParsedReport from a balance sheet.
        properties: Property config dict (loaded from YAML). If None, loads from disk.
        config_path: Override path for properties.yaml.

    Returns:
        PropertyAssetSummary with per-property values.
    """
    if properties is None:
        properties = load_properties_config(config_path)

    if not properties:
        return PropertyAssetSummary()

    # Build code -> (property_key, asset_type) mapping
    code_to_property: dict[str, tuple[str, str]] = {}  # code -> (prop_key, "land"|"building")
    for prop_key, prop in properties.items():
        if "land_asset" in prop:
            code_to_property[str(prop["land_asset"])] = (prop_key, "land")
        if "building_asset" in prop:
            code_to_property[str(prop["building_asset"])] = (prop_key, "building")

    # Collect all values by scanning flat rows
    first_col = parsed_bs.column_headers[0] if parsed_bs.column_headers else None

    # Track per-property values
    prop_land: dict[str, float] = {}
    prop_building: dict[str, float] = {}
    matched_codes: set[str] = set()

    for section in parsed_bs.sections:
        for row in section.rows:
            if first_col:
                amount = float(row.values.get(first_col, 0))
            elif row.values:
                amount = float(next(iter(row.values.values())))
            else:
                continue

            # Strategy 1: Check if any configured code appears in the account name
            for code, (prop_key, asset_type) in code_to_property.items():
                if code in row.account_name:
                    matched_codes.add(code)
                    if asset_type == "land":
                        prop_land[prop_key] = prop_land.get(prop_key, 0) + amount
                    else:
                        prop_building[prop_key] = prop_building.get(prop_key, 0) + amount
                    break

    # Build per-property results
    results: list[PropertyAssetValue] = []
    total_land = 0.0
    total_buildings = 0.0

    for prop_key, prop in properties.items():
        land_code = str(prop["land_asset"]) if "land_asset" in prop else None
        building_code = str(prop["building_asset"]) if "building_asset" in prop else None
        land_val = prop_land.get(prop_key, 0.0)
        building_val = prop_building.get(prop_key, 0.0)

        results.append(PropertyAssetValue(
            property_key=prop_key,
            address=prop.get("address", ""),
            land_code=land_code,
            land_value=round(land_val, 2),
            building_code=building_code,
            building_value=round(building_val, 2),
        ))

        total_land += land_val
        total_buildings += building_val

    # Find unmatched codes
    all_codes = set(code_to_property.keys())
    unmatched = sorted(all_codes - matched_codes)

    return PropertyAssetSummary(
        properties=results,
        total_land=round(total_land, 2),
        total_buildings=round(total_buildings, 2),
        total_assets=round(total_land + total_buildings, 2),
        unmatched_codes=unmatched,
    )


def get_manual_property_values(
    properties: dict[str, dict[str, Any]] | None = None,
    config_path: Path | None = None,
) -> PropertyAssetSummary:
    """Fallback: return property asset values from manual config entries.

    When balance sheet data is not available, properties.yaml may contain
    manual_land_value and manual_building_value fields as a fallback.

    Args:
        properties: Property config dict. If None, loads from disk.
        config_path: Override path for properties.yaml.

    Returns:
        PropertyAssetSummary from manual values (all zeros if not configured).
    """
    if properties is None:
        properties = load_properties_config(config_path)

    results: list[PropertyAssetValue] = []
    total_land = 0.0
    total_buildings = 0.0

    for prop_key, prop in properties.items():
        land_val = float(prop.get("manual_land_value", 0))
        building_val = float(prop.get("manual_building_value", 0))

        results.append(PropertyAssetValue(
            property_key=prop_key,
            address=prop.get("address", ""),
            land_code=str(prop["land_asset"]) if "land_asset" in prop else None,
            land_value=round(land_val, 2),
            building_code=str(prop["building_asset"]) if "building_asset" in prop else None,
            building_value=round(building_val, 2),
        ))

        total_land += land_val
        total_buildings += building_val

    return PropertyAssetSummary(
        properties=results,
        total_land=round(total_land, 2),
        total_buildings=round(total_buildings, 2),
        total_assets=round(total_land + total_buildings, 2),
    )
