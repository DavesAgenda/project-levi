"""Tests for the Trend Explorer service layer."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from app.csv_import import load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot, SnapshotRow
from app.services.trend_explorer import (
    CategoryInfo,
    TrendData,
    aggregate_category_by_month,
    aggregate_category_by_year,
    compute_trend_data,
    get_all_categories,
    load_all_snapshots_all_years,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def chart(tmp_path: Path) -> ChartOfAccounts:
    """Minimal chart of accounts for trend testing."""
    yaml_content = dedent("""\
        income:
          offertory:
            budget_label: "1 - Offertory"
            accounts:
              - { code: "10001", name: "Offering EFT" }
              - { code: "10010", name: "Offertory Cash" }
            legacy_accounts:
              - { code: "10005", name: "Offering Family 8AM" }
          property_income:
            budget_label: "2 - Housing Income"
            accounts:
              - { code: "20060", name: "Example Street 6 Rent" }
        expenses:
          administration:
            budget_label: "Administration"
            accounts:
              - { code: "41510", name: "Administrative Expenses" }
              - { code: "41517", name: "Bank Fees" }
    """)
    yaml_path = tmp_path / "chart_of_accounts.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return load_chart_of_accounts(yaml_path)


def _make_snapshot(
    from_date: str,
    to_date: str,
    rows: list[tuple[str, str, float]],
) -> FinancialSnapshot:
    """Create a FinancialSnapshot from (code, name, amount) tuples."""
    return FinancialSnapshot(
        report_date=to_date,
        from_date=from_date,
        to_date=to_date,
        source="test",
        rows=[
            SnapshotRow(account_code=code, account_name=name, amount=amount)
            for code, name, amount in rows
        ],
    )


@pytest.fixture()
def multi_year_snapshots() -> list[FinancialSnapshot]:
    """Three years of annual snapshots with offertory and admin data."""
    return [
        _make_snapshot("2022-01-01", "2022-12-31", [
            ("10001", "Offering EFT", 200000.0),
            ("10010", "Offertory Cash", 10000.0),
            ("41510", "Administrative Expenses", 5000.0),
        ]),
        _make_snapshot("2023-01-01", "2023-12-31", [
            ("10001", "Offering EFT", 220000.0),
            ("10010", "Offertory Cash", 12000.0),
            ("10005", "Offering Family 8AM", 3000.0),  # legacy account
            ("41510", "Administrative Expenses", 5500.0),
        ]),
        _make_snapshot("2024-01-01", "2024-12-31", [
            ("10001", "Offering EFT", 245000.0),
            ("10010", "Offertory Cash", 8200.0),
            ("41510", "Administrative Expenses", 6000.0),
            ("41517", "Bank Fees", 1200.0),
        ]),
    ]


@pytest.fixture()
def monthly_snapshots() -> list[FinancialSnapshot]:
    """Single-month snapshots for monthly granularity testing."""
    return [
        _make_snapshot("2024-01-01", "2024-01-31", [
            ("10001", "Offering EFT", 20000.0),
        ]),
        _make_snapshot("2024-02-01", "2024-02-29", [
            ("10001", "Offering EFT", 19500.0),
        ]),
        _make_snapshot("2024-03-01", "2024-03-31", [
            ("10001", "Offering EFT", 21000.0),
        ]),
    ]


# ---------------------------------------------------------------------------
# Tests: get_all_categories
# ---------------------------------------------------------------------------


class TestGetAllCategories:
    def test_returns_all_categories(self, chart: ChartOfAccounts):
        categories = get_all_categories(chart)
        assert len(categories) == 3
        keys = [c.key for c in categories]
        assert "offertory" in keys
        assert "property_income" in keys
        assert "administration" in keys

    def test_income_categories_come_first(self, chart: ChartOfAccounts):
        categories = get_all_categories(chart)
        sections = [c.section for c in categories]
        # All income should precede all expenses
        income_idx = [i for i, s in enumerate(sections) if s == "income"]
        expense_idx = [i for i, s in enumerate(sections) if s == "expenses"]
        assert max(income_idx) < min(expense_idx)

    def test_returns_empty_for_missing_chart(self):
        categories = get_all_categories(
            ChartOfAccounts(income={}, expenses={})
        )
        assert categories == []


# ---------------------------------------------------------------------------
# Tests: aggregate_category_by_year
# ---------------------------------------------------------------------------


class TestAggregateCategoryByYear:
    def test_aggregates_across_years(
        self,
        chart: ChartOfAccounts,
        multi_year_snapshots: list[FinancialSnapshot],
    ):
        result = aggregate_category_by_year(
            multi_year_snapshots, "offertory", chart,
        )
        assert len(result) == 3
        assert result[0].year == 2022
        assert result[0].total == 210000.0  # 200000 + 10000
        assert result[1].year == 2023
        assert result[1].total == 235000.0  # 220000 + 12000 + 3000 (legacy)
        assert result[2].year == 2024
        assert result[2].total == 253200.0  # 245000 + 8200

    def test_legacy_accounts_mapped_to_parent(
        self,
        chart: ChartOfAccounts,
        multi_year_snapshots: list[FinancialSnapshot],
    ):
        """Legacy account 10005 should be included in offertory totals."""
        result = aggregate_category_by_year(
            multi_year_snapshots, "offertory", chart,
        )
        # 2023 includes legacy account 10005
        year_2023 = next(yt for yt in result if yt.year == 2023)
        assert year_2023.total == 235000.0

    def test_empty_snapshots_returns_empty(self, chart: ChartOfAccounts):
        result = aggregate_category_by_year([], "offertory", chart)
        assert result == []

    def test_nonexistent_category_returns_empty(
        self,
        chart: ChartOfAccounts,
        multi_year_snapshots: list[FinancialSnapshot],
    ):
        result = aggregate_category_by_year(
            multi_year_snapshots, "nonexistent", chart,
        )
        assert result == []

    def test_multiple_accounts_in_same_category(
        self,
        chart: ChartOfAccounts,
        multi_year_snapshots: list[FinancialSnapshot],
    ):
        """Admin category has two accounts — both should aggregate."""
        result = aggregate_category_by_year(
            multi_year_snapshots, "administration", chart,
        )
        year_2024 = next(yt for yt in result if yt.year == 2024)
        assert year_2024.total == 7200.0  # 6000 + 1200


# ---------------------------------------------------------------------------
# Tests: aggregate_category_by_month
# ---------------------------------------------------------------------------


class TestAggregateCategoryByMonth:
    def test_monthly_aggregation(
        self,
        chart: ChartOfAccounts,
        monthly_snapshots: list[FinancialSnapshot],
    ):
        result = aggregate_category_by_month(
            monthly_snapshots, "offertory", chart,
        )
        assert len(result) == 3
        assert result[0].month == 1
        assert result[0].month_label == "Jan"
        assert result[0].total == 20000.0
        assert result[1].month == 2
        assert result[2].month == 3

    def test_skips_multi_month_snapshots(
        self,
        chart: ChartOfAccounts,
        multi_year_snapshots: list[FinancialSnapshot],
    ):
        """Annual snapshots should NOT appear in monthly aggregation."""
        result = aggregate_category_by_month(
            multi_year_snapshots, "offertory", chart,
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests: compute_trend_data
# ---------------------------------------------------------------------------


class TestComputeTrendData:
    def test_basic_trend_data(self, chart, tmp_path):
        """Test with JSON snapshots in a temp directory."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()

        snapshot = _make_snapshot("2023-01-01", "2023-12-31", [
            ("10001", "Offering EFT", 220000.0),
            ("20060", "Example Street 6 Rent", 32000.0),
        ])
        (snap_dir / "2023.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8",
        )

        # Empty historical dir so no CSVs are loaded
        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        data = compute_trend_data(
            "offertory",
            chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=hist_dir,
        )

        assert data.has_data is True
        assert data.primary_category.key == "offertory"
        assert data.primary_category.label == "1 - Offertory"
        assert len(data.primary_yearly) == 1
        assert data.primary_yearly[0].total == 220000.0

    def test_compare_mode(self, chart, tmp_path):
        """Test overlaying two categories."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()

        snapshot = _make_snapshot("2023-01-01", "2023-12-31", [
            ("10001", "Offering EFT", 220000.0),
            ("20060", "Example Street 6 Rent", 32000.0),
        ])
        (snap_dir / "2023.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8",
        )

        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        data = compute_trend_data(
            "offertory",
            compare_key="property_income",
            chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=hist_dir,
        )

        assert data.compare_category is not None
        assert data.compare_category.key == "property_income"
        assert len(data.compare_yearly) == 1
        assert data.compare_yearly[0].total == 32000.0

    def test_missing_years_produce_gaps(self, chart, tmp_path):
        """Years with no data for a compare category should be absent."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()

        snap_2022 = _make_snapshot("2022-01-01", "2022-12-31", [
            ("10001", "Offering EFT", 200000.0),
        ])
        snap_2024 = _make_snapshot("2024-01-01", "2024-12-31", [
            ("10001", "Offering EFT", 250000.0),
            ("20060", "Example Street 6 Rent", 35000.0),
        ])
        (snap_dir / "2022.json").write_text(
            snap_2022.model_dump_json(), encoding="utf-8",
        )
        (snap_dir / "2024.json").write_text(
            snap_2024.model_dump_json(), encoding="utf-8",
        )

        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        data = compute_trend_data(
            "offertory",
            compare_key="property_income",
            chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=hist_dir,
        )

        # Offertory has 2022 and 2024, property_income only 2024
        assert len(data.primary_yearly) == 2
        assert len(data.compare_yearly) == 1
        assert data.available_years == [2022, 2024]

    def test_no_snapshots_returns_no_data(self, chart, tmp_path):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        data = compute_trend_data(
            "offertory",
            chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=hist_dir,
        )
        assert data.has_data is False

    def test_csv_historical_import(self, chart, tmp_path):
        """Historical CSV files should be loaded and mapped."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        csv_content = dedent("""\
            Account,2022
            10001 - Offering EFT,"$200,000.00"
            10010 - Offertory Cash,"$10,000.00"
            41510 - Administrative Expenses,"$5,000.00"
        """)
        (hist_dir / "pl_2022.csv").write_text(csv_content, encoding="utf-8")

        data = compute_trend_data(
            "offertory",
            chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=hist_dir,
        )

        assert data.has_data is True
        assert len(data.primary_yearly) == 1
        assert data.primary_yearly[0].year == 2022
        assert data.primary_yearly[0].total == 210000.0


# ---------------------------------------------------------------------------
# Tests: load_all_snapshots_all_years
# ---------------------------------------------------------------------------


class TestLoadAllSnapshotsAllYears:
    def test_json_preferred_over_csv_for_same_year(self, chart, tmp_path):
        """If both JSON and CSV exist for the same year, JSON wins."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        hist_dir = tmp_path / "historical"
        hist_dir.mkdir()

        # JSON snapshot for 2023
        snapshot = _make_snapshot("2023-01-01", "2023-12-31", [
            ("10001", "Offering EFT", 999999.0),  # distinctive value
        ])
        (snap_dir / "2023.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8",
        )

        # CSV for 2023 with different value
        csv_content = dedent("""\
            Account,2023
            10001 - Offering EFT,"$100,000.00"
        """)
        (hist_dir / "pl_2023.csv").write_text(csv_content, encoding="utf-8")

        all_snaps = load_all_snapshots_all_years(snap_dir, hist_dir, chart)

        # Should have only one snapshot for 2023 (the JSON one)
        assert len(all_snaps) == 1
        assert all_snaps[0].rows[0].amount == 999999.0
