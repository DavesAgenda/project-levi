"""Unit tests for balance sheet change analysis service.

Covers:
- Snapshot discovery and loading
- Material change computation
- Materiality thresholds (dollar and percentage)
- Net assets extraction
- Empty/missing snapshot handling
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.balance_sheet import (
    BalanceSheetData,
    BalanceSheetRow,
    BalanceSheetSection,
    compute_balance_sheet_changes,
    find_balance_sheet_snapshots,
    load_balance_sheet_snapshot,
)


# ---------------------------------------------------------------------------
# Sample balance sheet responses — two periods for comparison
# ---------------------------------------------------------------------------

def _make_bs_response(date_label: str, bank_value: str, land_value: str,
                       building_value: str, liabilities_value: str,
                       net_assets_value: str) -> dict:
    """Build a minimal but valid Xero balance sheet response."""
    return {
        "Reports": [
            {
                "ReportID": "BalanceSheet",
                "ReportName": "Balance Sheet",
                "ReportTitles": ["Balance Sheet", "Test Church", f"As at {date_label}"],
                "ReportDate": date_label,
                "UpdatedDateUTC": "/Date(1743321600000+0000)/",
                "Rows": [
                    {
                        "RowType": "Header",
                        "Cells": [{"Value": ""}, {"Value": date_label}],
                    },
                    {
                        "RowType": "Section",
                        "Title": "Bank",
                        "Rows": [
                            {
                                "RowType": "Row",
                                "Cells": [
                                    {"Value": "ANZ Cheque Account",
                                     "Attributes": [{"Value": "uuid-anz", "Id": "account"}]},
                                    {"Value": bank_value},
                                ],
                            },
                            {
                                "RowType": "SummaryRow",
                                "Cells": [{"Value": "Total Bank"}, {"Value": bank_value}],
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
                                    {"Value": "Land - 6 Example Street",
                                     "Attributes": [{"Value": "uuid-land", "Id": "account"}]},
                                    {"Value": land_value},
                                ],
                            },
                            {
                                "RowType": "Row",
                                "Cells": [
                                    {"Value": "Buildings - 6 Example Street",
                                     "Attributes": [{"Value": "uuid-bldg", "Id": "account"}]},
                                    {"Value": building_value},
                                ],
                            },
                            {
                                "RowType": "SummaryRow",
                                "Cells": [
                                    {"Value": "Total Fixed Assets"},
                                    {"Value": str(float(land_value) + float(building_value))},
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
                                    {"Value": "Accounts Payable",
                                     "Attributes": [{"Value": "uuid-ap", "Id": "account"}]},
                                    {"Value": liabilities_value},
                                ],
                            },
                            {
                                "RowType": "SummaryRow",
                                "Cells": [
                                    {"Value": "Total Liabilities"},
                                    {"Value": liabilities_value},
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
                                    {"Value": net_assets_value},
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
    }


CURRENT_BS = _make_bs_response(
    date_label="31 Mar 2026",
    bank_value="48000.00",
    land_value="550000.00",
    building_value="320000.00",
    liabilities_value="5000.00",
    net_assets_value="913000.00",
)

PRIOR_BS = _make_bs_response(
    date_label="31 Dec 2025",
    bank_value="45000.00",
    land_value="550000.00",
    building_value="320000.00",
    liabilities_value="4800.00",
    net_assets_value="910200.00",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    """Create a temp directory with two balance sheet snapshot files."""
    # Current period
    current_snapshot = {
        "snapshot_metadata": {
            "saved_at": "2026-03-31T12:00:00Z",
            "report_type": "balance_sheet",
            "from_date": None,
            "to_date": "2026-03-31",
        },
        "response": CURRENT_BS,
    }
    (tmp_path / "balance_sheet_2026-03-31.json").write_text(
        json.dumps(current_snapshot, indent=2), encoding="utf-8",
    )

    # Prior period
    prior_snapshot = {
        "snapshot_metadata": {
            "saved_at": "2025-12-31T12:00:00Z",
            "report_type": "balance_sheet",
            "from_date": None,
            "to_date": "2025-12-31",
        },
        "response": PRIOR_BS,
    }
    (tmp_path / "balance_sheet_2025-12-31.json").write_text(
        json.dumps(prior_snapshot, indent=2), encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """Return an empty temp directory."""
    return tmp_path


# ---------------------------------------------------------------------------
# Test: Snapshot discovery
# ---------------------------------------------------------------------------

class TestFindBalanceSheetSnapshots:
    def test_finds_two_snapshots(self, snapshot_dir: Path):
        results = find_balance_sheet_snapshots(snapshot_dir)
        assert len(results) == 2
        # Newest first
        assert results[0][0] == "2026-03-31"
        assert results[1][0] == "2025-12-31"

    def test_empty_directory(self, empty_dir: Path):
        results = find_balance_sheet_snapshots(empty_dir)
        assert results == []

    def test_ignores_non_bs_files(self, snapshot_dir: Path):
        # Add a non-balance-sheet file
        (snapshot_dir / "pl_2026-01-01_2026-03-31.json").write_text("{}", encoding="utf-8")
        results = find_balance_sheet_snapshots(snapshot_dir)
        assert len(results) == 2  # Still only the BS files

    def test_nonexistent_directory(self, tmp_path: Path):
        results = find_balance_sheet_snapshots(tmp_path / "nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# Test: Snapshot loading
# ---------------------------------------------------------------------------

class TestLoadBalanceSheetSnapshot:
    def test_load_exact_date(self, snapshot_dir: Path):
        parsed = load_balance_sheet_snapshot("2026-03-31", snapshot_dir)
        assert parsed is not None
        assert parsed.report_id == "BalanceSheet"
        assert "31 Mar 2026" in parsed.column_headers

    def test_load_prefix_match(self, snapshot_dir: Path):
        parsed = load_balance_sheet_snapshot("2026-03", snapshot_dir)
        assert parsed is not None
        assert parsed.report_id == "BalanceSheet"

    def test_load_missing_date(self, snapshot_dir: Path):
        parsed = load_balance_sheet_snapshot("2024-01-01", snapshot_dir)
        assert parsed is None

    def test_load_from_empty_dir(self, empty_dir: Path):
        parsed = load_balance_sheet_snapshot("2026-03-31", empty_dir)
        assert parsed is None


# ---------------------------------------------------------------------------
# Test: Change computation
# ---------------------------------------------------------------------------

class TestComputeBalanceSheetChanges:
    def test_basic_changes(self, snapshot_dir: Path):
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31", directory=snapshot_dir,
        )
        assert data.has_data is True
        assert data.current_date == "2026-03-31"
        assert data.prior_date == "2025-12-31"

    def test_bank_change_is_material(self, snapshot_dir: Path):
        """Bank went from 45k to 48k — $3000 change exceeds $500 threshold."""
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31", directory=snapshot_dir,
        )
        bank_section = next(
            (s for s in data.sections if s.title == "Bank"), None,
        )
        assert bank_section is not None
        anz_row = next(
            (r for r in bank_section.rows if "ANZ" in r.account_name), None,
        )
        assert anz_row is not None
        assert anz_row.change_dollar == 3000.0
        assert anz_row.is_material is True

    def test_fixed_assets_no_change(self, snapshot_dir: Path):
        """Land and buildings are the same in both periods — not material."""
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31", directory=snapshot_dir,
        )
        # Fixed Assets section should not appear (no material rows)
        fixed_section = next(
            (s for s in data.sections if s.title == "Fixed Assets"), None,
        )
        assert fixed_section is None

    def test_net_assets(self, snapshot_dir: Path):
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31", directory=snapshot_dir,
        )
        assert data.net_assets_current == 913000.0
        assert data.net_assets_prior == 910200.0
        assert data.net_assets_change == 2800.0

    def test_missing_current_snapshot(self, snapshot_dir: Path):
        data = compute_balance_sheet_changes(
            "2099-01-01", "2025-12-31", directory=snapshot_dir,
        )
        assert data.has_data is False

    def test_missing_prior_snapshot(self, snapshot_dir: Path):
        data = compute_balance_sheet_changes(
            "2026-03-31", "2099-01-01", directory=snapshot_dir,
        )
        assert data.has_data is False

    def test_empty_directory(self, empty_dir: Path):
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31", directory=empty_dir,
        )
        assert data.has_data is False
        assert data.sections == []


# ---------------------------------------------------------------------------
# Test: Materiality thresholds
# ---------------------------------------------------------------------------

class TestMaterialityThresholds:
    def test_high_dollar_threshold_filters_more(self, snapshot_dir: Path):
        """With $5000 threshold, the $3000 bank change should be filtered out."""
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31",
            materiality_dollar=5000,
            materiality_pct=50.0,
            directory=snapshot_dir,
        )
        bank_section = next(
            (s for s in data.sections if s.title == "Bank"), None,
        )
        # $3000 change is below $5000 threshold and ~6.7% is below 50%
        assert bank_section is None

    def test_low_dollar_threshold_includes_more(self, snapshot_dir: Path):
        """With $100 threshold, the $200 liability change becomes material."""
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31",
            materiality_dollar=100,
            materiality_pct=1.0,
            directory=snapshot_dir,
        )
        liab_section = next(
            (s for s in data.sections if s.title == "Liabilities"), None,
        )
        assert liab_section is not None
        ap_row = next(
            (r for r in liab_section.rows if "Payable" in r.account_name), None,
        )
        assert ap_row is not None
        assert ap_row.change_dollar == 200.0

    def test_pct_threshold_catches_small_dollar_change(self, snapshot_dir: Path):
        """Liability change is $200 (below $500) but 4.2% — caught by 4% pct threshold."""
        data = compute_balance_sheet_changes(
            "2026-03-31", "2025-12-31",
            materiality_dollar=10000,  # very high dollar threshold
            materiality_pct=4.0,       # but 4% catches the liability change
            directory=snapshot_dir,
        )
        liab_section = next(
            (s for s in data.sections if s.title == "Liabilities"), None,
        )
        assert liab_section is not None


# ---------------------------------------------------------------------------
# Test: Data class structure
# ---------------------------------------------------------------------------

class TestDataClasses:
    def test_balance_sheet_row(self):
        row = BalanceSheetRow(
            account_name="Test Account",
            section="Bank",
            current_value=10000.0,
            prior_value=9000.0,
            change_dollar=1000.0,
            change_pct=11.1,
            is_material=True,
        )
        assert row.account_name == "Test Account"
        assert row.is_material is True

    def test_balance_sheet_section(self):
        section = BalanceSheetSection(
            title="Bank",
            rows=[],
            current_total=10000.0,
            prior_total=9000.0,
            change_dollar=1000.0,
        )
        assert section.title == "Bank"
        assert section.change_dollar == 1000.0

    def test_balance_sheet_data_defaults(self):
        data = BalanceSheetData()
        assert data.has_data is False
        assert data.sections == []
        assert data.net_assets_current == 0.0
        assert data.net_assets_prior == 0.0
        assert data.net_assets_change == 0.0
