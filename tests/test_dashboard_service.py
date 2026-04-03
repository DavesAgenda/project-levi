"""Tests for the dashboard data service layer."""

from __future__ import annotations

import json

import pytest

from app.models import FinancialSnapshot, SnapshotRow
from app.services.dashboard import (
    CategoryVariance,
    DashboardData,
    compute_dashboard_data,
    find_latest_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_snapshot() -> FinancialSnapshot:
    """A minimal snapshot for testing."""
    return FinancialSnapshot(
        report_date="2026-03-31",
        from_date="2026-01-01",
        to_date="2026-03-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=62500.0),
            SnapshotRow(account_code="10010", account_name="Offertory Cash", amount=1200.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=680.0),
            SnapshotRow(account_code="41517", account_name="Bank Fees", amount=145.0),
            SnapshotRow(account_code="44601", account_name="Repairs & Maintenance", amount=2800.0),
        ],
    )


@pytest.fixture()
def budget_data() -> dict[str, float]:
    """Simple budget dict keyed by category_key."""
    return {
        "offertory": 100000.0,
        "administration": 5000.0,
        "property_maintenance": 8000.0,
    }


# ---------------------------------------------------------------------------
# CategoryVariance tests
# ---------------------------------------------------------------------------

class TestCategoryVariance:
    def test_expense_over_budget(self):
        cv = CategoryVariance(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            actual=6000,
            budget=5000,
            variance_dollar=1000,
            variance_pct=20.0,
        )
        assert cv.is_over_budget is True
        assert cv.status == "danger"

    def test_expense_under_budget(self):
        cv = CategoryVariance(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            actual=3000,
            budget=5000,
            variance_dollar=-2000,
            variance_pct=-40.0,
        )
        assert cv.is_over_budget is False
        assert cv.status == "success"

    def test_expense_near_budget(self):
        cv = CategoryVariance(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            actual=4600,
            budget=5000,
            variance_dollar=-400,
            variance_pct=-8.0,
        )
        assert cv.status == "warning"

    def test_income_above_target(self):
        cv = CategoryVariance(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            actual=70000,
            budget=60000,
            variance_dollar=10000,
            variance_pct=16.7,
        )
        assert cv.is_over_budget is False
        assert cv.status == "success"

    def test_income_below_target(self):
        cv = CategoryVariance(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            actual=40000,
            budget=60000,
            variance_dollar=-20000,
            variance_pct=-33.3,
        )
        assert cv.is_over_budget is True
        assert cv.status == "danger"

    def test_zero_budget(self):
        cv = CategoryVariance(
            category_key="misc",
            budget_label="Misc",
            section="expenses",
            actual=500,
            budget=0,
            variance_dollar=500,
            variance_pct=None,
        )
        assert cv.is_over_budget is False
        assert cv.status == "success"


# ---------------------------------------------------------------------------
# compute_dashboard_data tests
# ---------------------------------------------------------------------------

class TestComputeDashboardData:
    def test_no_snapshot_returns_empty(self, chart, tmp_path):
        data = compute_dashboard_data(snapshot=None, budget={}, chart=chart, snapshots_dir=tmp_path)
        assert data.has_data is False
        assert data.total_income == 0.0
        assert data.categories == []

    def test_with_snapshot_and_budget(self, chart, sample_snapshot, budget_data):
        data = compute_dashboard_data(
            snapshot=sample_snapshot,
            budget=budget_data,
            chart=chart,
        )
        assert data.has_data is True
        assert data.total_income == 63700.0  # 62500 + 1200
        assert data.total_expenses == 3625.0  # 680 + 145 + 2800
        assert data.net_position == 63700.0 - 3625.0

    def test_variance_calculations(self, chart, sample_snapshot, budget_data):
        data = compute_dashboard_data(
            snapshot=sample_snapshot,
            budget=budget_data,
            chart=chart,
        )
        # Find offertory category
        offertory = next((c for c in data.categories if c.category_key == "offertory"), None)
        assert offertory is not None
        assert offertory.actual == 63700.0
        assert offertory.budget == 100000.0
        assert offertory.variance_dollar == 63700.0 - 100000.0
        assert offertory.variance_pct is not None

    def test_budget_consumed_pct(self, chart, sample_snapshot, budget_data):
        data = compute_dashboard_data(
            snapshot=sample_snapshot,
            budget=budget_data,
            chart=chart,
        )
        # budget_total_expenses = 5000 + 8000 = 13000
        # total_expenses = 3625
        expected_pct = round(3625.0 / 13000.0 * 100, 1)
        assert data.budget_consumed_pct == expected_pct

    def test_snapshot_metadata(self, chart, sample_snapshot):
        data = compute_dashboard_data(snapshot=sample_snapshot, budget={}, chart=chart)
        assert data.snapshot_date == "2026-03-31"
        assert data.snapshot_period == "2026-01-01 to 2026-03-31"

    def test_income_and_expense_category_filters(self, chart, sample_snapshot, budget_data):
        data = compute_dashboard_data(
            snapshot=sample_snapshot,
            budget=budget_data,
            chart=chart,
        )
        assert len(data.income_categories) > 0
        assert all(c.section == "income" for c in data.income_categories)
        assert len(data.expense_categories) > 0
        assert all(c.section == "expenses" for c in data.expense_categories)


# ---------------------------------------------------------------------------
# find_latest_snapshot tests
# ---------------------------------------------------------------------------

class TestFindLatestSnapshot:
    def test_returns_none_for_empty_dir(self, tmp_path):
        result = find_latest_snapshot(tmp_path)
        assert result is None

    def test_returns_none_for_missing_dir(self, tmp_path):
        result = find_latest_snapshot(tmp_path / "nonexistent")
        assert result is None

    def test_loads_raw_snapshot(self, tmp_path):
        snapshot_data = {
            "report_date": "2026-03-31",
            "from_date": "2026-01-01",
            "to_date": "2026-03-31",
            "source": "csv_import",
            "rows": [
                {"account_code": "10001", "account_name": "Offering EFT", "amount": 1000.0},
            ],
        }
        (tmp_path / "pl_test.json").write_text(json.dumps(snapshot_data))
        result = find_latest_snapshot(tmp_path)
        assert result is not None
        assert result.report_date == "2026-03-31"
        assert len(result.rows) == 1

    def test_loads_wrapped_snapshot(self, tmp_path):
        """Snapshot writer wraps data in {snapshot_metadata, response}."""
        wrapped = {
            "snapshot_metadata": {"saved_at": "2026-03-31T00:00:00Z"},
            "response": {
                "report_date": "2026-03-31",
                "from_date": "2026-01-01",
                "to_date": "2026-03-31",
                "source": "xero_api",
                "rows": [],
            },
        }
        (tmp_path / "pl_wrapped.json").write_text(json.dumps(wrapped))
        result = find_latest_snapshot(tmp_path)
        assert result is not None
        assert result.source == "xero_api"

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("not json at all")
        (tmp_path / "also_bad.json").write_text('{"foo": "bar"}')
        result = find_latest_snapshot(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# DashboardData properties
# ---------------------------------------------------------------------------

class TestDashboardData:
    def test_empty_dashboard(self):
        d = DashboardData()
        assert d.has_data is False
        assert d.income_categories == []
        assert d.expense_categories == []
        assert d.net_position == 0.0
