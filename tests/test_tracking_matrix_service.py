"""Tests for the tracking matrix service.

Uses mocked Xero API responses to test matrix computation without
requiring a live Xero connection.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ChartOfAccounts
from app.services.tracking_matrix import (
    TrackingCategory,
    TrackingMatrixData,
    TrackingOption,
    _build_matrix,
    _parse_tracking_categories,
    compute_tracking_matrix,
    discover_tracking_categories,
)
from app.xero.parser import parse_report


# ---------------------------------------------------------------------------
# Fixtures: sample Xero responses
# ---------------------------------------------------------------------------

SAMPLE_TRACKING_CATEGORIES_RESPONSE = {
    "TrackingCategories": [
        {
            "TrackingCategoryID": "cat-uuid-001",
            "Name": "Congregations",
            "Status": "ACTIVE",
            "Options": [
                {"TrackingOptionID": "opt-001", "Name": "8am Traditional", "Status": "ACTIVE"},
                {"TrackingOptionID": "opt-002", "Name": "10am Family", "Status": "ACTIVE"},
                {"TrackingOptionID": "opt-003", "Name": "6pm Youth", "Status": "ACTIVE"},
            ],
        },
        {
            "TrackingCategoryID": "cat-uuid-002",
            "Name": "Ministry & Funds",
            "Status": "ACTIVE",
            "Options": [
                {"TrackingOptionID": "opt-004", "Name": "General Fund", "Status": "ACTIVE"},
                {"TrackingOptionID": "opt-005", "Name": "Missions", "Status": "ACTIVE"},
            ],
        },
    ]
}


def _make_pl_response(columns: list[str], sections: list[dict]) -> dict:
    """Build a minimal Xero P&L JSON response with tracking columns."""
    header_cells = [{"Value": ""}]  # first cell is blank (account name col)
    for col in columns:
        header_cells.append({"Value": col})

    rows = [{"RowType": "Header", "Cells": header_cells}]

    for section in sections:
        section_rows = []
        for row in section.get("rows", []):
            cells = [{"Value": row["name"], "Attributes": [{"Id": "account", "Value": row.get("uuid", "")}]}]
            for val in row["values"]:
                cells.append({"Value": str(val)})
            section_rows.append({"RowType": "Row", "Cells": cells})

        # Add summary row
        if "summary" in section:
            summary_cells = [{"Value": section["summary"]["label"]}]
            for val in section["summary"]["values"]:
                summary_cells.append({"Value": str(val)})
            section_rows.append({"RowType": "SummaryRow", "Cells": summary_cells})

        rows.append({
            "RowType": "Section",
            "Title": section["title"],
            "Rows": section_rows,
        })

    return {
        "Reports": [{
            "ReportID": "ProfitAndLoss",
            "ReportName": "Profit and Loss",
            "ReportDate": "30 March 2026",
            "UpdatedDateUTC": "/Date(1743321600000+0000)/",
            "ReportTitles": ["Profit and Loss", "Church", "1 Jan 2026 to 30 Mar 2026"],
            "Rows": rows,
        }]
    }


SAMPLE_PL_WITH_TRACKING = _make_pl_response(
    columns=["8am Traditional", "10am Family", "6pm Youth"],
    sections=[
        {
            "title": "Income",
            "rows": [
                {"name": "10001 - Offering EFT", "uuid": "uuid-10001", "values": [5000, 8000, 3000]},
                {"name": "10010 - Offering Cash", "uuid": "uuid-10010", "values": [1000, 2000, 500]},
            ],
            "summary": {"label": "Total Income", "values": [6000, 10000, 3500]},
        },
        {
            "title": "Less Operating Expenses",
            "rows": [
                {"name": "40001 - Stipend", "uuid": "uuid-40001", "values": [3000, 3000, 1500]},
                {"name": "42000 - Office Supplies", "uuid": "uuid-42000", "values": [200, 300, 100]},
            ],
            "summary": {"label": "Total Operating Expenses", "values": [3200, 3300, 1600]},
        },
    ],
)


SAMPLE_CHART = ChartOfAccounts(
    income={
        "offerings_eft": {
            "budget_label": "Offering (EFT)",
            "accounts": [{"code": "10001", "name": "Offering EFT"}],
        },
        "offerings_cash": {
            "budget_label": "Offering (Cash)",
            "accounts": [{"code": "10010", "name": "Offering Cash"}],
        },
    },
    expenses={
        "stipend": {
            "budget_label": "Stipend",
            "accounts": [{"code": "40001", "name": "Stipend"}],
        },
        "office_supplies": {
            "budget_label": "Office Supplies",
            "accounts": [{"code": "42000", "name": "Office Supplies"}],
        },
    },
)


# ---------------------------------------------------------------------------
# Tests: _parse_tracking_categories
# ---------------------------------------------------------------------------

class TestParseTrackingCategories:
    """Test parsing of raw Xero tracking categories response."""

    def test_parse_basic(self):
        result = _parse_tracking_categories(SAMPLE_TRACKING_CATEGORIES_RESPONSE)
        assert len(result) == 2
        assert result[0].name == "Congregations"
        assert result[0].category_id == "cat-uuid-001"
        assert len(result[0].options) == 3
        assert result[0].options[0].name == "8am Traditional"

    def test_parse_empty(self):
        result = _parse_tracking_categories({"TrackingCategories": []})
        assert result == []

    def test_parse_missing_key(self):
        result = _parse_tracking_categories({})
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _build_matrix
# ---------------------------------------------------------------------------

class TestBuildMatrix:
    """Test matrix building from parsed P&L report."""

    def test_basic_matrix(self):
        parsed = parse_report(SAMPLE_PL_WITH_TRACKING)
        selected_cat = TrackingCategory(
            category_id="cat-uuid-001",
            name="Congregations",
            options=[
                TrackingOption(option_id="opt-001", name="8am Traditional"),
                TrackingOption(option_id="opt-002", name="10am Family"),
                TrackingOption(option_id="opt-003", name="6pm Youth"),
            ],
        )

        result = _build_matrix(
            parsed=parsed,
            chart=SAMPLE_CHART,
            selected_cat=selected_cat,
            from_date="2026-01-01",
            to_date="2026-03-30",
        )

        assert result.has_data is True
        assert result.column_headers == ["8am Traditional", "10am Family", "6pm Youth"]

        # Check income rows
        assert len(result.income_rows) == 2
        labels = {r.budget_label for r in result.income_rows}
        assert "Offering (EFT)" in labels
        assert "Offering (Cash)" in labels

        # Check expense rows
        assert len(result.expense_rows) == 2

        # Check income totals
        assert result.income_totals["8am Traditional"] == Decimal("6000")
        assert result.income_totals["10am Family"] == Decimal("10000")
        assert result.income_grand_total == Decimal("19500")

        # Check expense totals
        assert result.expense_totals["8am Traditional"] == Decimal("3200")
        assert result.expense_grand_total == Decimal("8100")

        # Check net position
        assert result.net_grand_total == Decimal("11400")
        assert result.net_position["8am Traditional"] == Decimal("2800")

    def test_matrix_sort_order(self):
        """Income rows should come before expense rows, both sorted by label."""
        parsed = parse_report(SAMPLE_PL_WITH_TRACKING)
        result = _build_matrix(
            parsed=parsed,
            chart=SAMPLE_CHART,
            selected_cat=None,
            from_date="2026-01-01",
            to_date="2026-03-30",
        )

        # Income rows sorted by label
        income_labels = [r.budget_label for r in result.income_rows]
        assert income_labels == sorted(income_labels)

        # Expense rows sorted by label
        expense_labels = [r.budget_label for r in result.expense_rows]
        assert expense_labels == sorted(expense_labels)

    def test_empty_report(self):
        """Matrix with no matching accounts should have has_data=False."""
        empty_response = _make_pl_response(
            columns=["Option A"],
            sections=[{"title": "Income", "rows": []}],
        )
        parsed = parse_report(empty_response)
        result = _build_matrix(
            parsed=parsed,
            chart=SAMPLE_CHART,
            selected_cat=None,
            from_date="2026-01-01",
            to_date="2026-03-30",
        )
        assert result.has_data is False


# ---------------------------------------------------------------------------
# Tests: discover_tracking_categories (async)
# ---------------------------------------------------------------------------

class TestDiscoverTrackingCategories:
    """Test tracking category discovery with mocked API."""

    @pytest.mark.asyncio
    async def test_discover_from_api(self):
        with patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            return_value=SAMPLE_TRACKING_CATEGORIES_RESPONSE,
        ):
            cats = await discover_tracking_categories()
            assert len(cats) == 2
            assert cats[0].name == "Congregations"
            assert cats[1].name == "Ministry & Funds"

    @pytest.mark.asyncio
    async def test_discover_fallback_to_snapshot(self, tmp_path):
        """When API fails, falls back to snapshot file."""
        # Write a snapshot
        snapshot = {
            "snapshot_metadata": {"report_type": "tracking_categories"},
            "response": SAMPLE_TRACKING_CATEGORIES_RESPONSE,
        }
        snap_file = tmp_path / "tracking_categories.json"
        snap_file.write_text(json.dumps(snapshot))

        with patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API unavailable"),
        ):
            cats = await discover_tracking_categories(snapshot_dir=tmp_path)
            assert len(cats) == 2

    @pytest.mark.asyncio
    async def test_discover_no_data(self, tmp_path):
        """When API fails and no snapshot exists, returns empty list."""
        with patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API unavailable"),
        ):
            cats = await discover_tracking_categories(snapshot_dir=tmp_path)
            assert cats == []


# ---------------------------------------------------------------------------
# Tests: compute_tracking_matrix (async, integration)
# ---------------------------------------------------------------------------

class TestComputeTrackingMatrix:
    """Test the full compute pipeline with mocked API."""

    @pytest.mark.asyncio
    async def test_full_compute(self):
        with patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            return_value=SAMPLE_TRACKING_CATEGORIES_RESPONSE,
        ), patch(
            "app.services.tracking_matrix.fetch_profit_and_loss",
            new_callable=AsyncMock,
            return_value=SAMPLE_PL_WITH_TRACKING,
        ):
            result = await compute_tracking_matrix(
                tracking_category_id="cat-uuid-001",
                from_date="2026-01-01",
                to_date="2026-03-30",
                chart=SAMPLE_CHART,
            )

            assert result.has_data is True
            assert result.tracking_category is not None
            assert result.tracking_category.name == "Congregations"
            assert len(result.column_headers) == 3
            assert len(result.income_rows) == 2
            assert len(result.expense_rows) == 2

    @pytest.mark.asyncio
    async def test_compute_no_chart(self, tmp_path):
        """Returns error when chart of accounts is missing."""
        with patch(
            "app.services.tracking_matrix.CHART_PATH",
            tmp_path / "nonexistent.yaml",
        ), patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            return_value=SAMPLE_TRACKING_CATEGORIES_RESPONSE,
        ):
            result = await compute_tracking_matrix(
                tracking_category_id="cat-uuid-001",
                from_date="2026-01-01",
                to_date="2026-03-30",
            )
            assert result.error is not None
            assert "Chart of accounts" in result.error

    @pytest.mark.asyncio
    async def test_compute_api_unavailable_no_snapshot(self, tmp_path):
        """Returns error with helpful message when no data source available."""
        with patch(
            "app.services.tracking_matrix.fetch_tracking_categories",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API unavailable"),
        ), patch(
            "app.services.tracking_matrix.fetch_profit_and_loss",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API unavailable"),
        ):
            result = await compute_tracking_matrix(
                tracking_category_id="cat-uuid-001",
                from_date="2026-01-01",
                to_date="2026-03-30",
                chart=SAMPLE_CHART,
                snapshot_dir=tmp_path,
            )
            assert result.error is not None
            assert "No data available" in result.error
