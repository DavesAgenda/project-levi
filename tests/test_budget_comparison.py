"""Tests for the budget comparison service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.models import FinancialSnapshot, SnapshotRow
from app.services.budget_comparison import (
    ComparisonData,
    ComparisonRow,
    DatasetSummary,
    _load_actuals_by_category,
    compute_budget_comparison,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHART_YAML = """
income:
  offertory:
    budget_label: "1 - Offertory"
    accounts:
      - { code: "10001", name: "Offering EFT" }
      - { code: "10010", name: "Offertory Cash" }
  property_income:
    budget_label: "2 - Housing Income"
    accounts:
      - { code: "20060", name: "Example Street 6 Rent" }

expenses:
  administration:
    budget_label: "4 - Administration"
    accounts:
      - { code: "41510", name: "Administrative Expenses" }
  property_maintenance:
    budget_label: "5 - Property Maintenance"
    accounts:
      - { code: "44601", name: "Repairs & Maintenance" }
"""


def _make_budget_yaml(year: int, income: dict, expenses: dict) -> str:
    data = {
        "year": year,
        "status": "draft" if year == 2027 else "approved",
        "income": income,
        "expenses": expenses,
    }
    return yaml.dump(data, default_flow_style=False)


@pytest.fixture()
def comparison_dirs(tmp_path: Path):
    """Set up chart, budgets, and snapshots for testing."""
    # Chart of accounts
    chart_path = tmp_path / "config" / "chart_of_accounts.yaml"
    chart_path.parent.mkdir(parents=True)
    chart_path.write_text(CHART_YAML, encoding="utf-8")

    # Budgets directory
    budgets_dir = tmp_path / "budgets"
    budgets_dir.mkdir()

    # Draft budget for 2027
    draft = _make_budget_yaml(
        2027,
        income={"offertory": {"10001_offering_eft": 280000}, "property_income": {"20060_goodhew": 50000}},
        expenses={"administration": {"41510_admin": 6000}, "property_maintenance": {"44601_repairs": 10000}},
    )
    (budgets_dir / "2027.yaml").write_text(draft, encoding="utf-8")

    # Current year budget for 2026
    current = _make_budget_yaml(
        2026,
        income={"offertory": {"10001_offering_eft": 260000}, "property_income": {"20060_goodhew": 45000}},
        expenses={"administration": {"41510_admin": 5500}, "property_maintenance": {"44601_repairs": 8000}},
    )
    (budgets_dir / "2026.yaml").write_text(current, encoding="utf-8")

    # Snapshots directory
    snapshots_dir = tmp_path / "data" / "snapshots"
    snapshots_dir.mkdir(parents=True)

    # 2026 snapshot (current year actuals)
    snap_2026 = FinancialSnapshot(
        report_date="2026-12-31",
        from_date="2026-01-01",
        to_date="2026-12-31",
        source="test",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=250000.0),
            SnapshotRow(account_code="10010", account_name="Offertory Cash", amount=5000.0),
            SnapshotRow(account_code="20060", account_name="Goodhew Rent", amount=48000.0),
            SnapshotRow(account_code="41510", account_name="Admin", amount=5200.0),
            SnapshotRow(account_code="44601", account_name="Repairs", amount=7500.0),
        ],
    )
    (snapshots_dir / "2026_annual.json").write_text(
        json.dumps(snap_2026.model_dump(), default=str), encoding="utf-8"
    )

    # 2025 snapshot (prior year actuals)
    snap_2025 = FinancialSnapshot(
        report_date="2025-12-31",
        from_date="2025-01-01",
        to_date="2025-12-31",
        source="test",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=240000.0),
            SnapshotRow(account_code="20060", account_name="Goodhew Rent", amount=44000.0),
            SnapshotRow(account_code="41510", account_name="Admin", amount=4800.0),
            SnapshotRow(account_code="44601", account_name="Repairs", amount=9000.0),
        ],
    )
    (snapshots_dir / "2025_annual.json").write_text(
        json.dumps(snap_2025.model_dump(), default=str), encoding="utf-8"
    )

    return {
        "chart_path": chart_path,
        "budgets_dir": budgets_dir,
        "snapshots_dir": snapshots_dir,
    }


# ---------------------------------------------------------------------------
# ComparisonRow unit tests
# ---------------------------------------------------------------------------

class TestComparisonRow:
    def test_is_significant_over_20_pct(self):
        row = ComparisonRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            draft_budget=300000,
            current_actual=200000,
            current_budget=250000,
            prior_actual=180000,
            variance_dollar=100000,
            variance_pct=50.0,
        )
        assert row.is_significant is True

    def test_is_significant_under_20_pct(self):
        row = ComparisonRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            draft_budget=210000,
            current_actual=200000,
            current_budget=200000,
            prior_actual=190000,
            variance_dollar=10000,
            variance_pct=5.0,
        )
        assert row.is_significant is False

    def test_is_significant_zero_actual(self):
        row = ComparisonRow(
            category_key="test",
            budget_label="Test",
            section="income",
            draft_budget=1000,
            current_actual=0,
            current_budget=0,
            prior_actual=0,
            variance_dollar=1000,
            variance_pct=None,
        )
        assert row.is_significant is True

    def test_is_significant_both_zero(self):
        row = ComparisonRow(
            category_key="test",
            budget_label="Test",
            section="income",
            draft_budget=0,
            current_actual=0,
            current_budget=0,
            prior_actual=0,
            variance_dollar=0,
            variance_pct=None,
        )
        assert row.is_significant is False

    def test_variance_status_income_positive(self):
        row = ComparisonRow(
            category_key="x", budget_label="X", section="income",
            draft_budget=100, current_actual=80, current_budget=80,
            prior_actual=70, variance_dollar=20, variance_pct=25.0,
        )
        assert row.variance_status == "positive"

    def test_variance_status_expense_positive_is_negative(self):
        """For expenses, higher draft = bad = negative."""
        row = ComparisonRow(
            category_key="x", budget_label="X", section="expenses",
            draft_budget=100, current_actual=80, current_budget=80,
            prior_actual=70, variance_dollar=20, variance_pct=25.0,
        )
        assert row.variance_status == "negative"

    def test_variance_status_neutral(self):
        row = ComparisonRow(
            category_key="x", budget_label="X", section="income",
            draft_budget=100, current_actual=100, current_budget=100,
            prior_actual=100, variance_dollar=0, variance_pct=0.0,
        )
        assert row.variance_status == "neutral"


# ---------------------------------------------------------------------------
# DatasetSummary tests
# ---------------------------------------------------------------------------

class TestDatasetSummary:
    def test_net_position(self):
        s = DatasetSummary(total_income=100000, total_expenses=80000)
        assert s.net_position == 20000.0

    def test_net_position_deficit(self):
        s = DatasetSummary(total_income=50000, total_expenses=80000)
        assert s.net_position == -30000.0


# ---------------------------------------------------------------------------
# Integration: compute_budget_comparison
# ---------------------------------------------------------------------------

class TestComputeBudgetComparison:
    def test_basic_comparison(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        assert data.has_data is True
        assert data.target_year == 2027
        assert data.current_year == 2026
        assert data.prior_year == 2025

    def test_income_rows_present(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        assert len(data.income_rows) > 0
        offertory = [r for r in data.income_rows if r.category_key == "offertory"]
        assert len(offertory) == 1
        assert offertory[0].draft_budget == 280000.0
        assert offertory[0].current_actual == 255000.0  # 250000 + 5000
        assert offertory[0].prior_actual == 240000.0

    def test_expense_rows_present(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        assert len(data.expense_rows) > 0
        admin = [r for r in data.expense_rows if r.category_key == "administration"]
        assert len(admin) == 1
        assert admin[0].draft_budget == 6000.0
        assert admin[0].current_actual == 5200.0

    def test_variance_calculation(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        offertory = [r for r in data.income_rows if r.category_key == "offertory"][0]
        # draft 280000 - current actual 255000 = 25000
        assert offertory.variance_dollar == 25000.0
        # 25000 / 255000 * 100 ≈ 9.8%
        assert offertory.variance_pct == pytest.approx(9.8, abs=0.1)

    def test_summary_totals(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        # Draft income: 280000 + 50000 = 330000
        assert data.draft_summary.total_income == 330000.0
        # Draft expenses: 6000 + 10000 = 16000
        assert data.draft_summary.total_expenses == 16000.0
        # Draft net: 330000 - 16000 = 314000
        assert data.draft_summary.net_position == 314000.0

    def test_current_summary(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        # Current actuals income: 255000 + 48000 = 303000
        assert data.current_summary.total_income == 303000.0
        # Current actuals expenses: 5200 + 7500 = 12700
        assert data.current_summary.total_expenses == 12700.0

    def test_prior_summary(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        # Prior income: 240000 + 44000 = 284000
        assert data.prior_summary.total_income == 284000.0
        # Prior expenses: 4800 + 9000 = 13800
        assert data.prior_summary.total_expenses == 13800.0

    def test_no_chart_returns_empty(self, tmp_path):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=tmp_path / "nonexistent.yaml",
        )
        assert data.has_data is False
        assert data.target_year == 2027

    def test_no_budget_returns_empty_rows(self, comparison_dirs):
        """If the target year budget doesn't exist, rows should still work."""
        data = compute_budget_comparison(
            target_year=2030,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        # No budget for 2030, no snapshots for 2028/2029 — no data
        assert data.has_data is False

    def test_significant_rows_flagged(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        # Check that rows where diff > 20% are flagged
        for row in data.income_rows + data.expense_rows:
            if row.current_actual != 0:
                pct = abs(row.draft_budget - row.current_actual) / abs(row.current_actual) * 100
                assert row.is_significant == (pct > 20)

    def test_sorted_by_label(self, comparison_dirs):
        data = compute_budget_comparison(
            target_year=2027,
            chart_path=comparison_dirs["chart_path"],
            budgets_dir=comparison_dirs["budgets_dir"],
            snapshots_dir=comparison_dirs["snapshots_dir"],
        )
        income_labels = [r.budget_label for r in data.income_rows]
        assert income_labels == sorted(income_labels)
        expense_labels = [r.budget_label for r in data.expense_rows]
        assert expense_labels == sorted(expense_labels)


# ---------------------------------------------------------------------------
# _load_actuals_by_category tests
# ---------------------------------------------------------------------------

class TestLoadActualsByCategory:
    def test_loads_correct_year(self, comparison_dirs):
        from app.csv_import import load_chart_of_accounts
        chart = load_chart_of_accounts(comparison_dirs["chart_path"])
        actuals = _load_actuals_by_category(
            2026, chart, comparison_dirs["snapshots_dir"]
        )
        assert "offertory" in actuals
        assert actuals["offertory"] == 255000.0  # 250000 + 5000

    def test_empty_year(self, comparison_dirs):
        from app.csv_import import load_chart_of_accounts
        chart = load_chart_of_accounts(comparison_dirs["chart_path"])
        actuals = _load_actuals_by_category(
            2020, chart, comparison_dirs["snapshots_dir"]
        )
        assert actuals == {}
