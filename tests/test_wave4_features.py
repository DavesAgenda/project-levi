"""Tests for Wave 4 features: CHA-266, CHA-267, CHA-269.

CHA-266: Dashboard YTD/full-year toggle with budget pro-rating
CHA-267: Tracking matrix from journal data
CHA-269: Report drill-down with role-based detail levels
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.models.journal import JournalEntry, JournalLine, TrackingTag
from app.services.dashboard import compute_dashboard_data
from app.models import FinancialSnapshot, SnapshotRow


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_CHART_YAML = {
    "income": {
        "offertory": {
            "budget_label": "1 - Offertory",
            "accounts": [
                {"code": "10001", "name": "Offering EFT"},
                {"code": "10010", "name": "Offertory Cash"},
            ],
            "legacy_accounts": [
                {"code": "10005", "name": "Offering Family 8AM"},
            ],
        },
    },
    "expenses": {
        "ministry_staff": {
            "budget_label": "Ministry Staff",
            "accounts": [
                {"code": "40100", "name": "Ministry Staff Salaries"},
            ],
        },
    },
}


@pytest.fixture
def chart_path(tmp_path):
    path = tmp_path / "chart_of_accounts.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(SAMPLE_CHART_YAML, f, default_flow_style=False, sort_keys=False)
    return path


@pytest.fixture
def chart(chart_path):
    from app.csv_import import load_chart_of_accounts
    return load_chart_of_accounts(chart_path)


def _line(code, name, amount, acct_type="REVENUE", tracking=None):
    return JournalLine(
        journal_line_id=f"jl-{code}",
        account_id=f"acc-{code}",
        account_code=code,
        account_name=name,
        account_type=acct_type,
        net_amount=amount,
        tracking=tracking or [],
    )


def _entry(jid, jdate, lines):
    return JournalEntry(
        journal_id=jid,
        journal_number=jid.split("-")[-1],
        journal_date=jdate,
        source_type="ACCREC",
        reference=f"Ref {jid}",
        lines=lines,
    )


@pytest.fixture
def journals_dir(tmp_path):
    """Create journal files for testing."""
    month_dir = tmp_path / "2026" / "2026-03"
    month_dir.mkdir(parents=True)
    entries = [
        _entry("j-1", "2026-03-10", [
            _line("10001", "Offering EFT", 1500.0, "REVENUE",
                  tracking=[TrackingTag(
                      tracking_category_id="tc-1",
                      tracking_category_name="Congregations",
                      option_id="opt-1",
                      option_name="Morning",
                  )]),
            _line("90001", "Bank", -1500.0, "BANK"),
        ]),
        _entry("j-2", "2026-03-15", [
            _line("10010", "Offertory Cash", 500.0, "REVENUE",
                  tracking=[TrackingTag(
                      tracking_category_id="tc-1",
                      tracking_category_name="Congregations",
                      option_id="opt-2",
                      option_name="Evening",
                  )]),
            _line("90001", "Bank", -500.0, "BANK"),
        ]),
        _entry("j-3", "2026-03-20", [
            _line("40100", "Ministry Staff Salaries", -3000.0, "EXPENSE"),
            _line("90001", "Bank", 3000.0, "BANK"),
        ]),
    ]
    (month_dir / "journals.json").write_text(
        json.dumps([e.model_dump() for e in entries], default=str),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# CHA-266: Dashboard YTD/full-year toggle
# ---------------------------------------------------------------------------


class TestDashboardBudgetScale:
    def test_full_year_no_scaling(self, chart):
        snapshot = FinancialSnapshot(
            report_date="2026-03-31",
            from_date="2026-01-01",
            to_date="2026-03-31",
            source="test",
            rows=[SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1500.0)],
        )
        budget = {"offertory": 12000.0}

        data = compute_dashboard_data(
            snapshot=snapshot, budget=budget, chart=chart,
            budget_scale=None,  # full year — no scaling
        )
        offertory = next(c for c in data.categories if c.category_key == "offertory")
        assert offertory.budget == 12000.0

    def test_ytd_march_scales_budget(self, chart):
        """YTD in March: budget should be 3/12 = 25% of annual."""
        snapshot = FinancialSnapshot(
            report_date="2026-03-31",
            from_date="2026-01-01",
            to_date="2026-03-31",
            source="test",
            rows=[SnapshotRow(account_code="10001", account_name="Offering EFT", amount=3000.0)],
        )
        budget = {"offertory": 12000.0}

        data = compute_dashboard_data(
            snapshot=snapshot, budget=budget, chart=chart,
            budget_scale=3 / 12.0,  # March = 3 months elapsed
        )
        offertory = next(c for c in data.categories if c.category_key == "offertory")
        assert offertory.budget == 3000.0  # 12000 * 3/12

    def test_ytd_june_scales_budget(self, chart):
        """YTD in June: budget should be 50% of annual."""
        snapshot = FinancialSnapshot(
            report_date="2026-06-30",
            from_date="2026-01-01",
            to_date="2026-06-30",
            source="test",
            rows=[SnapshotRow(account_code="40100", account_name="Ministry Staff", amount=6000.0)],
        )
        budget = {"ministry_staff": 24000.0}

        data = compute_dashboard_data(
            snapshot=snapshot, budget=budget, chart=chart,
            budget_scale=6 / 12.0,
        )
        staff = next(c for c in data.categories if c.category_key == "ministry_staff")
        assert staff.budget == 12000.0  # 24000 * 6/12


# ---------------------------------------------------------------------------
# CHA-267: Tracking matrix from journal data
# ---------------------------------------------------------------------------


class TestTrackingMatrixFromJournals:
    def test_basic_matrix(self, chart_path, journals_dir):
        from app.services.tracking_matrix import compute_tracking_matrix_from_journals

        result = compute_tracking_matrix_from_journals(
            tracking_category_name="Congregations",
            from_date="2026-03-01",
            to_date="2026-03-31",
            journals_dir=journals_dir,
            chart=None,
        )
        # Need to pass chart_path since default CHART_PATH won't work in test
        from app.csv_import import load_chart_of_accounts
        chart = load_chart_of_accounts(chart_path)
        result = compute_tracking_matrix_from_journals(
            tracking_category_name="Congregations",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart=chart,
            journals_dir=journals_dir,
        )

        assert result.has_data is True
        assert "Morning" in result.column_headers
        assert "Evening" in result.column_headers
        assert len(result.income_rows) == 1  # offertory

        # Check amounts
        offertory = result.income_rows[0]
        assert offertory.values["Morning"] == Decimal("1500.0")
        assert offertory.values["Evening"] == Decimal("500.0")

    def test_no_matching_category(self, chart, journals_dir):
        from app.services.tracking_matrix import compute_tracking_matrix_from_journals

        result = compute_tracking_matrix_from_journals(
            tracking_category_name="Nonexistent",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart=chart,
            journals_dir=journals_dir,
        )
        assert result.has_data is False
        assert "No journal lines" in (result.error or "")

    def test_empty_journals(self, chart, tmp_path):
        from app.services.tracking_matrix import compute_tracking_matrix_from_journals

        result = compute_tracking_matrix_from_journals(
            tracking_category_name="Congregations",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart=chart,
            journals_dir=tmp_path,
        )
        assert result.has_data is False

    def test_column_totals(self, chart_path, journals_dir):
        from app.csv_import import load_chart_of_accounts
        from app.services.tracking_matrix import compute_tracking_matrix_from_journals

        chart = load_chart_of_accounts(chart_path)
        result = compute_tracking_matrix_from_journals(
            tracking_category_name="Congregations",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart=chart,
            journals_dir=journals_dir,
        )
        assert result.income_totals["Morning"] == Decimal("1500.0")
        assert result.income_totals["Evening"] == Decimal("500.0")
        assert result.income_grand_total == Decimal("2000.0")


# ---------------------------------------------------------------------------
# CHA-269: Report drill-down with role-based detail
# ---------------------------------------------------------------------------


class TestDrilldown:
    def test_admin_gets_transactions(self, chart_path, journals_dir):
        from app.services.drilldown import get_category_drilldown

        result = get_category_drilldown(
            section="income",
            category_key="offertory",
            role="admin",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        assert result is not None
        assert result.detail_level == "transactions"
        assert len(result.accounts) == 2  # 10001 + 10010
        # Admin should see individual transactions
        eft = next(a for a in result.accounts if a.code == "10001")
        assert len(eft.transactions) == 1
        assert eft.transactions[0].amount == 1500.0

    def test_board_gets_accounts_only(self, chart_path, journals_dir):
        from app.services.drilldown import get_category_drilldown

        result = get_category_drilldown(
            section="income",
            category_key="offertory",
            role="board",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        assert result is not None
        assert result.detail_level == "accounts"
        assert len(result.accounts) == 2
        # Board should NOT see individual transactions
        eft = next(a for a in result.accounts if a.code == "10001")
        assert eft.transactions == []

    def test_staff_gets_summary_only(self, chart_path, journals_dir):
        from app.services.drilldown import get_category_drilldown

        result = get_category_drilldown(
            section="income",
            category_key="offertory",
            role="staff",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        assert result is not None
        assert result.detail_level == "summary"
        assert result.accounts == []  # No account details for staff

    def test_nonexistent_category(self, chart_path, journals_dir):
        from app.services.drilldown import get_category_drilldown

        result = get_category_drilldown(
            section="income",
            category_key="nonexistent",
            role="admin",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        assert result is None

    def test_legacy_account_flagged(self, chart_path, journals_dir):
        """Legacy accounts should be flagged in drill-down results."""
        # Add a legacy journal entry
        month_dir = journals_dir / "2026" / "2026-03"
        existing = json.loads((month_dir / "journals.json").read_text(encoding="utf-8"))
        legacy_entry = _entry("j-legacy", "2026-03-25", [
            _line("10005", "Offering Family 8AM", 200.0, "REVENUE"),
        ])
        existing.append(legacy_entry.model_dump())
        (month_dir / "journals.json").write_text(
            json.dumps(existing, default=str), encoding="utf-8",
        )

        from app.services.drilldown import get_category_drilldown
        result = get_category_drilldown(
            section="income",
            category_key="offertory",
            role="admin",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        legacy = next(a for a in result.accounts if a.code == "10005")
        assert legacy.is_legacy is True

    def test_expense_drilldown(self, chart_path, journals_dir):
        from app.services.drilldown import get_category_drilldown

        result = get_category_drilldown(
            section="expenses",
            category_key="ministry_staff",
            role="admin",
            from_date="2026-03-01",
            to_date="2026-03-31",
            chart_path=chart_path,
            journals_dir=journals_dir,
        )
        assert result is not None
        assert result.net_amount == -3000.0
        assert len(result.accounts) == 1
        assert result.accounts[0].code == "40100"
