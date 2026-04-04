"""Tests for the council report service layer."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from app.models import FinancialSnapshot, SnapshotRow
from app.services.council_report import (
    CouncilReportData,
    CouncilReportRow,
    SectionSummary,
    _month_key,
    _month_label,
    _prorate_budget,
    _snapshot_to_monthly_actuals,
    compute_council_report,
    load_all_snapshots,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def q1_snapshot() -> FinancialSnapshot:
    """A Q1 snapshot covering Jan-Mar 2026."""
    return FinancialSnapshot(
        report_date="2026-03-31",
        from_date="2026-01-01",
        to_date="2026-03-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=62500.0),
            SnapshotRow(account_code="10010", account_name="Offertory Cash", amount=1200.0),
            SnapshotRow(account_code="20060", account_name="Example Street 6 Rent", amount=7800.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=680.0),
            SnapshotRow(account_code="41517", account_name="Bank Fees", amount=145.0),
            SnapshotRow(account_code="44601", account_name="Repairs & Maintenance", amount=2800.0),
        ],
    )


@pytest.fixture()
def jan_snapshot() -> FinancialSnapshot:
    """A single-month January snapshot."""
    return FinancialSnapshot(
        report_date="2026-01-31",
        from_date="2026-01-01",
        to_date="2026-01-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=20000.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=200.0),
        ],
    )


@pytest.fixture()
def feb_snapshot() -> FinancialSnapshot:
    """A single-month February snapshot."""
    return FinancialSnapshot(
        report_date="2026-02-28",
        from_date="2026-02-01",
        to_date="2026-02-28",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=22000.0),
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=250.0),
        ],
    )


@pytest.fixture()
def budget_data() -> dict[str, float]:
    """Annual budget dict keyed by category_key."""
    return {
        "offertory": 100000.0,
        "property_income": 120000.0,
        "administration": 12000.0,
        "property_maintenance": 24000.0,
    }


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestMonthHelpers:
    def test_month_key(self):
        assert _month_key(2026, 1) == "2026-01"
        assert _month_key(2026, 12) == "2026-12"

    def test_month_label(self):
        assert _month_label("2026-01") == "Jan"
        assert _month_label("2026-03") == "Mar"
        assert _month_label("2026-12") == "Dec"


class TestProrateBudget:
    def test_full_year(self):
        budget = {"offertory": 120000.0}
        result = _prorate_budget(budget, 12)
        assert result["offertory"] == 120000.0

    def test_quarter(self):
        budget = {"offertory": 120000.0}
        result = _prorate_budget(budget, 3)
        assert result["offertory"] == 30000.0

    def test_single_month(self):
        budget = {"offertory": 120000.0}
        result = _prorate_budget(budget, 1)
        assert result["offertory"] == 10000.0

    def test_empty_budget(self):
        result = _prorate_budget({}, 6)
        assert result == {}


# ---------------------------------------------------------------------------
# Snapshot to monthly actuals tests
# ---------------------------------------------------------------------------

class TestSnapshotToMonthlyActuals:
    def test_single_month_snapshot(self, jan_snapshot, chart):
        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        result = _snapshot_to_monthly_actuals(jan_snapshot, lookup)

        assert "offertory" in result
        assert "2026-01" in result["offertory"]
        assert result["offertory"]["2026-01"] == 20000.0

    def test_multi_month_snapshot_distributes_evenly(self, q1_snapshot, chart):
        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        result = _snapshot_to_monthly_actuals(q1_snapshot, lookup)

        # Offertory: 62500 + 1200 = 63700, split across 3 months
        expected_monthly = round(63700.0 / 3, 2)
        assert "offertory" in result
        assert result["offertory"]["2026-01"] == expected_monthly
        assert result["offertory"]["2026-02"] == expected_monthly
        assert result["offertory"]["2026-03"] == expected_monthly

    def test_unmapped_accounts_as_uncategorised(self, chart):
        """CHA-276: unmapped accounts appear as _uncategorised_expenses."""
        from app.csv_import import build_account_lookup
        lookup = build_account_lookup(chart)
        snapshot = FinancialSnapshot(
            report_date="2026-01-31",
            from_date="2026-01-01",
            to_date="2026-01-31",
            source="csv_import",
            rows=[
                SnapshotRow(account_code="99999", account_name="Unknown", amount=500.0),
            ],
        )
        result = _snapshot_to_monthly_actuals(snapshot, lookup)
        # Code 99999 starts with 9 -> expenses
        assert "_uncategorised_expenses" in result
        assert result["_uncategorised_expenses"]["2026-01"] == 500.0


# ---------------------------------------------------------------------------
# load_all_snapshots tests
# ---------------------------------------------------------------------------

class TestLoadAllSnapshots:
    def test_returns_empty_for_missing_dir(self, tmp_path):
        result = load_all_snapshots(tmp_path / "nonexistent")
        assert result == []

    def test_returns_empty_for_empty_dir(self, tmp_path):
        result = load_all_snapshots(tmp_path)
        assert result == []

    def test_loads_multiple_snapshots_sorted(self, tmp_path):
        snap1 = {
            "report_date": "2026-01-31",
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "source": "csv_import",
            "rows": [],
        }
        snap2 = {
            "report_date": "2026-02-28",
            "from_date": "2026-02-01",
            "to_date": "2026-02-28",
            "source": "csv_import",
            "rows": [],
        }
        # Write in reverse order to verify sorting
        (tmp_path / "pl_feb.json").write_text(json.dumps(snap2))
        (tmp_path / "pl_jan.json").write_text(json.dumps(snap1))

        result = load_all_snapshots(tmp_path)
        assert len(result) == 2
        assert result[0].from_date == "2026-01-01"
        assert result[1].from_date == "2026-02-01"

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("not json")
        (tmp_path / "also_bad.json").write_text('{"foo": "bar"}')
        result = load_all_snapshots(tmp_path)
        assert result == []

    def test_loads_wrapped_snapshot(self, tmp_path):
        wrapped = {
            "snapshot_metadata": {"saved_at": "2026-01-31T00:00:00Z"},
            "response": {
                "report_date": "2026-01-31",
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "source": "xero_api",
                "rows": [],
            },
        }
        (tmp_path / "pl_wrapped.json").write_text(json.dumps(wrapped))
        result = load_all_snapshots(tmp_path)
        assert len(result) == 1
        assert result[0].source == "xero_api"


# ---------------------------------------------------------------------------
# compute_council_report tests
# ---------------------------------------------------------------------------

class TestComputeCouncilReport:
    def test_no_snapshots_returns_empty(self, chart, tmp_path):
        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget={},
        )
        assert data.has_data is False
        assert data.year == 2026
        assert data.income_rows == []
        assert data.expense_rows == []

    def test_with_q1_snapshot(self, chart, tmp_path, q1_snapshot, budget_data):
        # Write snapshot to disk
        snap_path = tmp_path / "pl_q1.json"
        snap_path.write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        assert data.has_data is True
        assert data.year == 2026
        assert len(data.month_keys) == 3
        assert data.month_labels == ["Jan", "Feb", "Mar"]

    def test_income_rows_present(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        assert len(data.income_rows) > 0
        assert all(r.section == "income" for r in data.income_rows)

        # Find offertory
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        assert offertory.ytd_actual == pytest.approx(63700.0, abs=0.02)  # 62500 + 1200

    def test_expense_rows_present(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        assert len(data.expense_rows) > 0
        assert all(r.section == "expenses" for r in data.expense_rows)

    def test_ytd_budget_prorated(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        # 100000 * 3/12 = 68750
        assert offertory.ytd_budget == 68750.0

    def test_variance_calculations(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        expected_variance = 63700.0 - 68750.0
        assert offertory.variance_dollar == pytest.approx(expected_variance, abs=0.02)
        assert offertory.variance_pct is not None

    def test_section_summaries(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        assert data.income_summary is not None
        assert data.income_summary.label == "Total Income"
        assert data.income_summary.ytd_actual > 0

        assert data.expense_summary is not None
        assert data.expense_summary.label == "Total Expenses"
        assert data.expense_summary.ytd_actual > 0

    def test_net_position(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        expected_net = data.income_summary.ytd_actual - data.expense_summary.ytd_actual
        assert data.net_ytd == expected_net

    def test_monthly_distribution(self, chart, tmp_path, q1_snapshot, budget_data):
        (tmp_path / "pl_q1.json").write_text(q1_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=3, chart=chart,
            snapshots_dir=tmp_path, budget=budget_data,
        )
        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        # Q1 snapshot: 63700 / 3 months = ~21233.33 per month
        expected = round(63700.0 / 3, 2)
        assert offertory.monthly_actuals["2026-01"] == expected
        assert offertory.monthly_actuals["2026-02"] == expected
        assert offertory.monthly_actuals["2026-03"] == expected

    def test_multiple_monthly_snapshots(self, chart, tmp_path, jan_snapshot, feb_snapshot):
        (tmp_path / "pl_jan.json").write_text(jan_snapshot.model_dump_json())
        (tmp_path / "pl_feb.json").write_text(feb_snapshot.model_dump_json())

        budget = {"offertory": 240000.0, "administration": 6000.0}
        data = compute_council_report(
            year=2026, end_month=2, chart=chart,
            snapshots_dir=tmp_path, budget=budget,
        )
        assert data.has_data is True
        assert len(data.month_keys) == 2

        offertory = next((r for r in data.income_rows if r.category_key == "offertory"), None)
        assert offertory is not None
        assert offertory.monthly_actuals["2026-01"] == 20000.0
        assert offertory.monthly_actuals["2026-02"] == 22000.0
        assert offertory.ytd_actual == 42000.0

    def test_single_month_report(self, chart, tmp_path, jan_snapshot):
        (tmp_path / "pl_jan.json").write_text(jan_snapshot.model_dump_json())

        data = compute_council_report(
            year=2026, end_month=1, chart=chart,
            snapshots_dir=tmp_path, budget={"offertory": 240000.0},
        )
        assert len(data.month_keys) == 1
        assert data.month_labels == ["Jan"]


# ---------------------------------------------------------------------------
# CouncilReportRow tests
# ---------------------------------------------------------------------------

class TestCouncilReportRow:
    def test_expense_over_budget_status(self):
        row = CouncilReportRow(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            monthly_actuals={},
            ytd_actual=6000,
            ytd_budget=5000,
            variance_dollar=1000,
            variance_pct=20.0,
        )
        assert row.status == "danger"

    def test_expense_under_budget_status(self):
        row = CouncilReportRow(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            monthly_actuals={},
            ytd_actual=3000,
            ytd_budget=5000,
            variance_dollar=-2000,
            variance_pct=-40.0,
        )
        assert row.status == "success"

    def test_income_above_target_status(self):
        row = CouncilReportRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            monthly_actuals={},
            ytd_actual=70000,
            ytd_budget=60000,
            variance_dollar=10000,
            variance_pct=16.7,
        )
        assert row.status == "success"

    def test_income_below_target_status(self):
        row = CouncilReportRow(
            category_key="offertory",
            budget_label="Offertory",
            section="income",
            monthly_actuals={},
            ytd_actual=40000,
            ytd_budget=60000,
            variance_dollar=-20000,
            variance_pct=-33.3,
        )
        assert row.status == "danger"

    def test_zero_budget_status(self):
        row = CouncilReportRow(
            category_key="misc",
            budget_label="Misc",
            section="expenses",
            monthly_actuals={},
            ytd_actual=500,
            ytd_budget=0,
            variance_dollar=500,
            variance_pct=None,
        )
        assert row.status == "neutral"
