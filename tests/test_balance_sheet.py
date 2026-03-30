"""Unit tests for balance sheet parsing, asset extraction, and property mapping.

Covers:
- Balance sheet parsing via the generic parser
- Fixed asset extraction by account code
- Property asset service mapping from properties.yaml
- Manual fallback path
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from app.xero.parser import (
    ParsedReport,
    FixedAssetEntry,
    extract_fixed_assets,
    extract_fixed_assets_by_code,
    parse_report,
    report_to_flat_rows,
)
from app.services.property_assets import (
    PropertyAssetSummary,
    PropertyAssetValue,
    get_asset_account_codes,
    get_manual_property_values,
    load_properties_config,
    map_balance_sheet_to_properties,
)


# ---------------------------------------------------------------------------
# Sample balance sheet response (realistic Xero structure)
# ---------------------------------------------------------------------------

SAMPLE_BS_RESPONSE = {
    "Reports": [
        {
            "ReportID": "BalanceSheet",
            "ReportName": "Balance Sheet",
            "ReportType": "BalanceSheet",
            "ReportTitles": [
                "Balance Sheet",
                "New Light Anglican Church",
                "As at 31 March 2026",
            ],
            "ReportDate": "31 March 2026",
            "UpdatedDateUTC": "/Date(1743321600000+0000)/",
            "Rows": [
                {
                    "RowType": "Header",
                    "Cells": [
                        {"Value": ""},
                        {"Value": "31 Mar 2026"},
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Assets",
                    "Rows": [],
                },
                {
                    "RowType": "Section",
                    "Title": "Bank",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "ANZ Cheque Account",
                                    "Attributes": [{"Value": "uuid-anz", "Id": "account"}],
                                },
                                {"Value": "45000.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Bank"},
                                {"Value": "45000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Fixed Assets",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Land - 6 Example Street (65010)",
                                    "Attributes": [{"Value": "uuid-land-goodhew", "Id": "account"}],
                                },
                                {"Value": "550000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Buildings - 6 Example Street (66010)",
                                    "Attributes": [{"Value": "uuid-bldg-goodhew", "Id": "account"}],
                                },
                                {"Value": "320000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Land - 33 Example Avenue (65003)",
                                    "Attributes": [{"Value": "uuid-land-hamilton", "Id": "account"}],
                                },
                                {"Value": "480000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Land - 33 Example Road (65007)",
                                    "Attributes": [{"Value": "uuid-land-loane33", "Id": "account"}],
                                },
                                {"Value": "520000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Buildings - 33 Example Road (66007)",
                                    "Attributes": [{"Value": "uuid-bldg-loane33", "Id": "account"}],
                                },
                                {"Value": "280000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Land - 35 Example Road (65008)",
                                    "Attributes": [{"Value": "uuid-land-loane35", "Id": "account"}],
                                },
                                {"Value": "530000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Buildings - 35 Example Road (66008)",
                                    "Attributes": [{"Value": "uuid-bldg-loane35", "Id": "account"}],
                                },
                                {"Value": "290000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Land - 39 Example Road (65009)",
                                    "Attributes": [{"Value": "uuid-land-loane39", "Id": "account"}],
                                },
                                {"Value": "500000.00"},
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Buildings - 39 Example Road (66009)",
                                    "Attributes": [{"Value": "uuid-bldg-loane39", "Id": "account"}],
                                },
                                {"Value": "270000.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Fixed Assets"},
                                {"Value": "3740000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Assets"},
                                {"Value": "3785000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Liabilities",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Accounts Payable",
                                    "Attributes": [{"Value": "uuid-ap", "Id": "account"}],
                                },
                                {"Value": "5000.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Liabilities"},
                                {"Value": "5000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Net Assets"},
                                {"Value": "3780000.00"},
                            ],
                        },
                    ],
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Minimal properties config for testing
# ---------------------------------------------------------------------------

SAMPLE_PROPERTIES_YAML = dedent("""\
    properties:
      goodhew_6:
        address: "6 Example Street"
        land_asset: "65010"
        building_asset: "66010"
      hamilton_33:
        address: "33 Example Avenue"
        land_asset: "65003"
      loane_33:
        address: "33 Example Road"
        land_asset: "65007"
        building_asset: "66007"
      loane_35:
        address: "35 Example Road"
        land_asset: "65008"
        building_asset: "66008"
      loane_39:
        address: "39 Example Road"
        land_asset: "65009"
        building_asset: "66009"
""")


SAMPLE_PROPERTIES_MANUAL = dedent("""\
    properties:
      goodhew_6:
        address: "6 Example Street"
        land_asset: "65010"
        building_asset: "66010"
        manual_land_value: 550000
        manual_building_value: 320000
      hamilton_33:
        address: "33 Example Avenue"
        land_asset: "65003"
        manual_land_value: 480000
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parsed_bs() -> ParsedReport:
    return parse_report(SAMPLE_BS_RESPONSE)


@pytest.fixture
def properties_path(tmp_path: Path) -> Path:
    path = tmp_path / "properties.yaml"
    path.write_text(SAMPLE_PROPERTIES_YAML, encoding="utf-8")
    return path


@pytest.fixture
def manual_properties_path(tmp_path: Path) -> Path:
    path = tmp_path / "properties.yaml"
    path.write_text(SAMPLE_PROPERTIES_MANUAL, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Test: Balance sheet parsing
# ---------------------------------------------------------------------------

class TestBalanceSheetParsing:
    def test_report_metadata(self, parsed_bs: ParsedReport):
        assert parsed_bs.report_id == "BalanceSheet"
        assert parsed_bs.report_name == "Balance Sheet"
        assert parsed_bs.report_date == "31 March 2026"

    def test_column_headers(self, parsed_bs: ParsedReport):
        assert parsed_bs.column_headers == ["31 Mar 2026"]

    def test_section_count(self, parsed_bs: ParsedReport):
        # Assets (empty rows), Bank, Fixed Assets, Liabilities = 4 named sections
        # Two unnamed sections become summaries
        named = [s for s in parsed_bs.sections if s.title]
        assert len(named) >= 3  # Bank, Fixed Assets, Liabilities

    def test_fixed_assets_section(self, parsed_bs: ParsedReport):
        fixed = next(s for s in parsed_bs.sections if s.title == "Fixed Assets")
        assert len(fixed.rows) == 9  # 5 land + 4 building rows
        assert fixed.summary is not None
        assert fixed.summary.label == "Total Fixed Assets"
        assert fixed.summary.values["31 Mar 2026"] == Decimal("3740000.00")

    def test_land_row_values(self, parsed_bs: ParsedReport):
        fixed = next(s for s in parsed_bs.sections if s.title == "Fixed Assets")
        goodhew_land = fixed.rows[0]
        assert "65010" in goodhew_land.account_name
        assert goodhew_land.account_id == "uuid-land-goodhew"
        assert goodhew_land.values["31 Mar 2026"] == Decimal("550000.00")

    def test_building_row_values(self, parsed_bs: ParsedReport):
        fixed = next(s for s in parsed_bs.sections if s.title == "Fixed Assets")
        goodhew_bldg = fixed.rows[1]
        assert "66010" in goodhew_bldg.account_name
        assert goodhew_bldg.account_id == "uuid-bldg-goodhew"
        assert goodhew_bldg.values["31 Mar 2026"] == Decimal("320000.00")

    def test_net_assets_summary(self, parsed_bs: ParsedReport):
        net = [s for s in parsed_bs.summaries if s.label == "Net Assets"]
        assert len(net) == 1
        assert net[0].values["31 Mar 2026"] == Decimal("3780000.00")

    def test_flat_rows(self, parsed_bs: ParsedReport):
        flat = report_to_flat_rows(parsed_bs)
        # Should include all account rows from all sections
        land_rows = [r for r in flat if "65010" in r["account_name"]]
        assert len(land_rows) == 1
        assert land_rows[0]["31 Mar 2026"] == 550000.00


# ---------------------------------------------------------------------------
# Test: extract_fixed_assets_by_code
# ---------------------------------------------------------------------------

class TestExtractFixedAssetsByCode:
    def test_extract_with_uuid_map(self, parsed_bs: ParsedReport):
        code_map = {
            "65010": "uuid-land-goodhew",
            "66010": "uuid-bldg-goodhew",
            "65003": "uuid-land-hamilton",
        }
        results = extract_fixed_assets_by_code(parsed_bs, code_map)
        assert len(results) == 3

        by_code = {r.account_code: r for r in results}
        assert by_code["65010"].value == Decimal("550000.00")
        assert by_code["66010"].value == Decimal("320000.00")
        assert by_code["65003"].value == Decimal("480000.00")

    def test_empty_code_map(self, parsed_bs: ParsedReport):
        results = extract_fixed_assets_by_code(parsed_bs, {})
        assert results == []

    def test_unmatched_uuids(self, parsed_bs: ParsedReport):
        code_map = {"99999": "uuid-nonexistent"}
        results = extract_fixed_assets_by_code(parsed_bs, code_map)
        assert results == []


# ---------------------------------------------------------------------------
# Test: load_properties_config
# ---------------------------------------------------------------------------

class TestLoadPropertiesConfig:
    def test_load_from_file(self, properties_path: Path):
        props = load_properties_config(properties_path)
        assert "goodhew_6" in props
        assert "hamilton_33" in props
        assert props["goodhew_6"]["land_asset"] == "65010"
        assert props["goodhew_6"]["building_asset"] == "66010"

    def test_missing_file(self, tmp_path: Path):
        props = load_properties_config(tmp_path / "nonexistent.yaml")
        assert props == {}


# ---------------------------------------------------------------------------
# Test: get_asset_account_codes
# ---------------------------------------------------------------------------

class TestGetAssetAccountCodes:
    def test_extracts_codes(self, properties_path: Path):
        props = load_properties_config(properties_path)
        codes = get_asset_account_codes(props)
        assert "65010" in codes["land"]
        assert "65003" in codes["land"]
        assert "66010" in codes["buildings"]
        assert "66007" in codes["buildings"]

    def test_hamilton_no_building(self, properties_path: Path):
        props = load_properties_config(properties_path)
        codes = get_asset_account_codes(props)
        # Hamilton has no building_asset
        assert len(codes["land"]) == 5
        assert len(codes["buildings"]) == 4  # goodhew + 3 loane properties


# ---------------------------------------------------------------------------
# Test: map_balance_sheet_to_properties
# ---------------------------------------------------------------------------

class TestMapBalanceSheetToProperties:
    def test_maps_all_properties(self, parsed_bs: ParsedReport, properties_path: Path):
        props = load_properties_config(properties_path)
        summary = map_balance_sheet_to_properties(parsed_bs, properties=props)

        assert len(summary.properties) == 5
        by_key = {p.property_key: p for p in summary.properties}

        # Goodhew: land 550k + building 320k
        assert by_key["goodhew_6"].land_value == 550000.00
        assert by_key["goodhew_6"].building_value == 320000.00
        assert by_key["goodhew_6"].total_value == 870000.00

        # Hamilton: land 480k, no building
        assert by_key["hamilton_33"].land_value == 480000.00
        assert by_key["hamilton_33"].building_value == 0.0

        # Loane 33: land 520k + building 280k
        assert by_key["loane_33"].land_value == 520000.00
        assert by_key["loane_33"].building_value == 280000.00

    def test_totals(self, parsed_bs: ParsedReport, properties_path: Path):
        props = load_properties_config(properties_path)
        summary = map_balance_sheet_to_properties(parsed_bs, properties=props)

        expected_land = 550000 + 480000 + 520000 + 530000 + 500000
        expected_buildings = 320000 + 280000 + 290000 + 270000
        assert summary.total_land == expected_land
        assert summary.total_buildings == expected_buildings
        assert summary.total_assets == expected_land + expected_buildings

    def test_no_properties_config(self, parsed_bs: ParsedReport, tmp_path: Path):
        summary = map_balance_sheet_to_properties(
            parsed_bs,
            config_path=tmp_path / "nonexistent.yaml",
        )
        assert len(summary.properties) == 0
        assert summary.total_assets == 0.0

    def test_unmatched_codes_reported(self, parsed_bs: ParsedReport):
        """If a property has a code not found in BS, it appears in unmatched."""
        props = {
            "test_prop": {
                "address": "Test",
                "land_asset": "99999",  # Not in BS
            }
        }
        summary = map_balance_sheet_to_properties(parsed_bs, properties=props)
        assert "99999" in summary.unmatched_codes


# ---------------------------------------------------------------------------
# Test: manual fallback
# ---------------------------------------------------------------------------

class TestManualFallback:
    def test_manual_values(self, manual_properties_path: Path):
        props = load_properties_config(manual_properties_path)
        summary = get_manual_property_values(properties=props)

        by_key = {p.property_key: p for p in summary.properties}
        assert by_key["goodhew_6"].land_value == 550000.0
        assert by_key["goodhew_6"].building_value == 320000.0
        assert by_key["hamilton_33"].land_value == 480000.0
        assert by_key["hamilton_33"].building_value == 0.0

        assert summary.total_land == 1030000.0
        assert summary.total_buildings == 320000.0

    def test_no_manual_values(self, properties_path: Path):
        """Properties without manual values should return zeros."""
        props = load_properties_config(properties_path)
        summary = get_manual_property_values(properties=props)

        for p in summary.properties:
            assert p.land_value == 0.0
            assert p.building_value == 0.0


# ---------------------------------------------------------------------------
# Test: snapshot save (balance sheet specific filename)
# ---------------------------------------------------------------------------

class TestBalanceSheetSnapshot:
    def test_filename(self):
        from app.xero.snapshots import _build_filename
        result = _build_filename("balance_sheet", to_date="2026-03-31")
        assert result == "balance_sheet_2026-03-31.json"

    def test_save_snapshot(self, tmp_path: Path):
        from app.xero.snapshots import save_balance_sheet_snapshot
        data = {"Reports": [{"ReportID": "BalanceSheet"}]}
        path = save_balance_sheet_snapshot(data, "2026-03-31")

        # Verify file was created (using default dir or override)
        # For unit tests, just verify the function doesn't crash
        # and returns a Path
        assert isinstance(path, Path)

    def test_save_to_custom_dir(self, tmp_path: Path):
        import json
        from app.xero.snapshots import save_snapshot
        data = {"Reports": [{"ReportID": "BalanceSheet"}]}
        path = save_snapshot(data, "balance_sheet", to_date="2026-03-31", directory=tmp_path)

        assert path.exists()
        assert path.name == "balance_sheet_2026-03-31.json"
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["snapshot_metadata"]["report_type"] == "balance_sheet"
        assert content["response"]["Reports"][0]["ReportID"] == "BalanceSheet"
