"""Tests for the AGM report service layer."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from app.models import FinancialSnapshot, SnapshotRow
from app.services.agm_report import (
    AGMCategoryRow,
    AGMReportData,
    SectionSummary,
    TrendYear,
    _is_significant_variance,
    _load_csv_as_snapshot,
    compute_agm_report,
    load_year_actuals,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def full_year_snapshot() -> FinancialSnapshot:
    """A full-year snapshot for 2023."""
    return FinancialSnapshot(
        report_date="2023-12-31",
        from_date="2023-01-01",
        to_date="2023-12-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=245000.0),
            SnapshotRow(account_code="10010", account_name="Offertory Cash", amount=12500.0),
            SnapshotRow(account_code="20060", account_name="Goodhew Street 6 Rent", amount=32832.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=4800.0),
            SnapshotRow(account_code="41517", account_name="Bank Fees", amount=1200.0),
            SnapshotRow(account_code="44601", account_name="Repairs & Maintenance", amount=15000.0),
            SnapshotRow(account_code="89010", account_name="Hamilton Street 33 Costs", amount=3500.0),
        ],
    )


@pytest.fixture()
def year_2022_snapshot() -> FinancialSnapshot:
    """A full-year snapshot for 2022."""
    return FinancialSnapshot(
        report_date="2022-12-31",
        from_date="2022-01-01",
        to_date="2022-12-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=230000.0),
            SnapshotRow(account_code="10010", account_name="Offertory Cash", amount=14000.0),
            SnapshotRow(account_code="20060", account_name="Goodhew Street 6 Rent", amount=30000.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=5200.0),
            SnapshotRow(account_code="41517", account_name="Bank Fees", amount=1100.0),
            SnapshotRow(account_code="44601", account_name="Repairs & Maintenance", amount=12000.0),
        ],
    )


@pytest.fixture()
def budget_data() -> dict[str, float]:
    """Annual budget dict keyed by category_key."""
    return {
        "offertory": 275000.0,
        "property_income": 120000.0,
        "administration": 12000.0,
        "property_maintenance": 24000.0,
    }


@pytest.fixture()
def historical_csv_dir(tmp_path: Path) -> Path:
    """Create a tmp dir with a sample historical CSV."""
    csv_content = dedent("""\
        Account,2021
        10001 - Offering EFT,"$220,000.00"
        10010 - Offertory Cash,"$15,000.00"
        20060 - Goodhew Street 6 Rent,"$28,000.00"
        41510 - Administrative Expenses,"$4,500.00"
        44601 - Repairs & Maintenance,"$10,000.00"
    """)
    csv_path = tmp_path / "sample_2021.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Significance test
# ---------------------------------------------------------------------------

class TestIsSignificantVariance:
    def test_large_dollar_variance(self):
        assert _is_significant_variance(1500.0, 5.0) is True

    def test_large_pct_variance(self):
        assert _is_significant_variance(500.0, 15.0) is True

    def test_not_significant(self):
        assert _is_significant_variance(500.0, 5.0) is False

    def test_none_pct(self):
        assert _is_significant_variance(500.0, None) is False

    def test_boundary_dollar(self):
        assert _is_significant_variance(1000.0, 5.0) is False
        assert _is_significant_variance(1001.0, 5.0) is True

    def test_boundary_pct(self):
        assert _is_significant_variance(500.0, 10.0) is False
        assert _is_significant_variance(500.0, 10.1) is True

    def test_negative_values(self):
        assert _is_significant_variance(-1500.0, -15.0) is True
        assert _is_significant_variance(-500.0, -5.0) is False


# ---------------------------------------------------------------------------
# CSV loading test
# ---------------------------------------------------------------------------

class TestLoadCsvAsSnapshot:
    def test_load_valid_csv(self, historical_csv_dir):
        csv_path = historical_csv_dir / "sample_2021.csv"
        snap = _load_csv_as_snapshot(csv_path, 2021)
        assert snap is not None
        assert snap.from_date == "2021-01-01"
        assert snap.to_date == "2021-12-31"
        assert len(snap.rows) == 5
        # Check one row
        offering = next(r for r in snap.rows if r.account_code == "10001")
        assert offering.amount == 220000.0

    def test_skip_totals(self, tmp_path):
        csv_content = dedent("""\
            Account,2021
            10001 - Offering EFT,"$220,000.00"
            Total Income,"$220,000.00"
            Net Profit,"$100,000.00"
        """)
        (tmp_path / "test_2021.csv").write_text(csv_content)
        snap = _load_csv_as_snapshot(tmp_path / "test_2021.csv", 2021)
        assert snap is not None
        assert len(snap.rows) == 1

    def test_empty_csv(self, tmp_path):
        (tmp_path / "empty_2021.csv").write_text("Account,2021\n")
        snap = _load_csv_as_snapshot(tmp_path / "empty_2021.csv", 2021)
        assert snap is None


# ---------------------------------------------------------------------------
# load_year_actuals tests
# ---------------------------------------------------------------------------

class TestLoadYearActuals:
    def test_json_snapshot_preferred(self, chart, tmp_path, full_year_snapshot):
        """JSON snapshots should be used when available."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        actuals = load_year_actuals(2023, lookup, snapshots_dir=snap_dir)

        assert "offertory" in actuals
        assert actuals["offertory"] == 257500.0  # 245000 + 12500

    def test_csv_fallback(self, chart, historical_csv_dir):
        """If no JSON snapshot, fall back to historical CSV."""
        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        actuals = load_year_actuals(
            2021, lookup,
            snapshots_dir=Path("/nonexistent"),
            historical_dir=historical_csv_dir,
        )

        assert "offertory" in actuals
        assert actuals["offertory"] == 235000.0  # 220000 + 15000

    def test_no_data_returns_empty(self, chart, tmp_path):
        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        actuals = load_year_actuals(
            2019, lookup,
            snapshots_dir=tmp_path,
            historical_dir=tmp_path,
        )
        assert actuals == {}


# ---------------------------------------------------------------------------
# compute_agm_report tests
# ---------------------------------------------------------------------------

class TestComputeAgmReport:
    def test_no_data_returns_empty(self, chart, tmp_path):
        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=tmp_path,
            historical_dir=tmp_path,
            budget={},
        )
        assert data.has_data is False
        assert data.year == 2023
        assert data.income_rows == []
        assert data.expense_rows == []

    def test_with_snapshot_data(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        assert data.has_data is True
        assert data.year == 2023
        assert len(data.income_rows) > 0
        assert len(data.expense_rows) > 0

    def test_income_rows_present(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        assert all(r.section == "income" for r in data.income_rows)
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        assert offertory.actual == 257500.0  # 245000 + 12500

    def test_variance_calculations(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        expected_variance = 257500.0 - 275000.0
        assert offertory.variance_dollar == expected_variance
        assert offertory.variance_pct is not None

    def test_significant_variances_flagged(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        # -17500 variance is > $1000, so it should be significant
        assert offertory.is_significant is True

    def test_section_summaries(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        assert data.income_summary is not None
        assert data.income_summary.label == "Total Income"
        assert data.income_summary.actual > 0

        assert data.expense_summary is not None
        assert data.expense_summary.label == "Total Expenses"
        assert data.expense_summary.actual > 0

    def test_net_position(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2023,
        )
        expected_net = data.income_summary.actual - data.expense_summary.actual
        assert data.net_actual == expected_net

    def test_multi_year_trend(
        self, chart, tmp_path, full_year_snapshot, year_2022_snapshot, budget_data,
    ):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())
        (snap_dir / "pl_2022.json").write_text(year_2022_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2022,
        )
        assert data.trend_years == [2022, 2023]
        assert len(data.trend_data) == 2

        # 2022 trend data
        assert data.trend_data[0].year == 2022
        assert data.trend_data[0].total_income > 0

        # 2023 trend data
        assert data.trend_data[1].year == 2023
        assert data.trend_data[1].total_income > 0

    def test_trend_values_in_rows(
        self, chart, tmp_path, full_year_snapshot, year_2022_snapshot, budget_data,
    ):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())
        (snap_dir / "pl_2022.json").write_text(year_2022_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
            trend_start_year=2022,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        assert len(offertory.trend_values) == 2
        assert offertory.trend_values[0] == 244000.0  # 2022: 230000 + 14000
        assert offertory.trend_values[1] == 257500.0  # 2023: 245000 + 12500

    def test_csv_historical_in_trend(
        self, chart, tmp_path, full_year_snapshot, budget_data, historical_csv_dir,
    ):
        """CSV data should be picked up for years without JSON snapshots."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=historical_csv_dir,
            budget=budget_data,
            trend_start_year=2021,
        )
        assert data.trend_years == [2021, 2022, 2023]

        # 2021 should have data from CSV
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        assert offertory.trend_values[0] == 235000.0  # from CSV: 220000 + 15000

    def test_default_trend_range(self, chart, tmp_path, full_year_snapshot, budget_data):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "pl_2023.json").write_text(full_year_snapshot.model_dump_json())

        data = compute_agm_report(
            year=2023, chart=chart,
            snapshots_dir=snap_dir,
            historical_dir=tmp_path,
            budget=budget_data,
        )
        # Default: year - 4 to year
        assert data.trend_years == [2019, 2020, 2021, 2022, 2023]


# ---------------------------------------------------------------------------
# AGMCategoryRow status tests
# ---------------------------------------------------------------------------

class TestAGMCategoryRowStatus:
    def test_income_above_budget_success(self):
        row = AGMCategoryRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            actual=300000,
            budget=275000,
            variance_dollar=25000,
            variance_pct=9.1,
            is_significant=True,
            trend_values=[],
        )
        assert row.status == "success"

    def test_income_below_budget_significant_danger(self):
        row = AGMCategoryRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            actual=240000,
            budget=275000,
            variance_dollar=-35000,
            variance_pct=-12.7,
            is_significant=True,
            trend_values=[],
        )
        assert row.status == "danger"

    def test_income_below_budget_small_warning(self):
        row = AGMCategoryRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            actual=270000,
            budget=275000,
            variance_dollar=-5000,
            variance_pct=-1.8,
            is_significant=False,
            trend_values=[],
        )
        assert row.status == "warning"

    def test_expense_over_budget_significant_danger(self):
        row = AGMCategoryRow(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            actual=15000,
            budget=12000,
            variance_dollar=3000,
            variance_pct=25.0,
            is_significant=True,
            trend_values=[],
        )
        assert row.status == "danger"

    def test_expense_under_budget_success(self):
        row = AGMCategoryRow(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            actual=10000,
            budget=12000,
            variance_dollar=-2000,
            variance_pct=-16.7,
            is_significant=True,
            trend_values=[],
        )
        assert row.status == "success"

    def test_zero_budget_neutral(self):
        row = AGMCategoryRow(
            category_key="misc",
            budget_label="Misc",
            section="expenses",
            actual=500,
            budget=0,
            variance_dollar=500,
            variance_pct=None,
            is_significant=False,
            trend_values=[],
        )
        assert row.status == "neutral"
