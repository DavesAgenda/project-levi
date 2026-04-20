"""Tests for journal aggregation pipeline (CHA-265).

Covers:
- Account-code-based aggregation (no name matching)
- Category mapping via chart of accounts
- Unmapped account detection
- Tracking category breakdown
- FinancialSnapshot conversion for dashboard compatibility
- Month and YTD aggregation helpers
- Journal loading from disk
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.models import ChartOfAccounts, FinancialSnapshot
from app.models.journal import JournalEntry, JournalLine, TrackingTag
from app.services.journal_aggregation import (
    AccountTotal,
    AggregationResult,
    CategoryTotal,
    aggregate_journals,
    aggregate_month,
    aggregate_ytd,
    aggregation_to_snapshot,
    load_journals,
)


# ---------------------------------------------------------------------------
# Fixtures
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
        "property_income": {
            "budget_label": "2 - Housing Income",
            "accounts": [
                {"code": "20060", "name": "Goodhew Street 6 Rent"},
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
        "administration": {
            "budget_label": "Administration",
            "accounts": [
                {"code": "41510", "name": "Administrative Expenses"},
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


def _make_entry(
    journal_id: str,
    journal_date: str,
    lines: list[JournalLine],
) -> JournalEntry:
    return JournalEntry(
        journal_id=journal_id,
        journal_number=journal_id.split("-")[-1],
        journal_date=journal_date,
        lines=lines,
    )


def _line(code: str, name: str, amount: float, acct_type: str = "REVENUE", tracking=None) -> JournalLine:
    return JournalLine(
        journal_line_id=f"jl-{code}",
        account_id=f"acc-{code}",
        account_code=code,
        account_name=name,
        account_type=acct_type,
        net_amount=amount,
        tracking=tracking or [],
    )


@pytest.fixture
def sample_entries():
    return [
        _make_entry("j-1", "2026-03-10", [
            _line("10001", "Offering EFT", 1500.0, "REVENUE"),
            _line("90001", "Main Bank", -1500.0, "BANK"),
        ]),
        _make_entry("j-2", "2026-03-15", [
            _line("10010", "Offertory Cash", 500.0, "REVENUE"),
            _line("90001", "Main Bank", -500.0, "BANK"),
        ]),
        _make_entry("j-3", "2026-03-20", [
            _line("40100", "Ministry Staff Salaries", -3000.0, "EXPENSE"),
            _line("90001", "Main Bank", 3000.0, "BANK"),
        ]),
        _make_entry("j-4", "2026-03-25", [
            _line("20060", "Goodhew Street 6 Rent", 800.0, "REVENUE"),
            _line("90001", "Main Bank", -800.0, "BANK"),
        ]),
    ]


# ---------------------------------------------------------------------------
# Core aggregation tests
# ---------------------------------------------------------------------------


class TestAggregateJournals:
    def test_basic_aggregation(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart, from_date="2026-03-01", to_date="2026-03-31")

        assert result.journal_count == 4
        assert result.total_income == 2800.0  # 1500 + 500 + 800
        assert result.total_expenses == -3000.0
        # Net = income + expenses (expenses are already negative in journals)
        assert result.net_position == -200.0  # 2800 + (-3000)

    def test_category_mapping(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart)

        # Find offertory category
        offertory = next((c for c in result.categories if c.key == "offertory"), None)
        assert offertory is not None
        assert offertory.net_amount == 2000.0  # 1500 + 500
        assert offertory.account_count == 2  # 10001 + 10010
        assert offertory.section == "income"

        # Find property income
        prop = next((c for c in result.categories if c.key == "property_income"), None)
        assert prop is not None
        assert prop.net_amount == 800.0

        # Find ministry staff
        staff = next((c for c in result.categories if c.key == "ministry_staff"), None)
        assert staff is not None
        assert staff.net_amount == -3000.0

    def test_account_details_in_category(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart)
        offertory = next(c for c in result.categories if c.key == "offertory")

        codes = {a.code for a in offertory.accounts}
        assert codes == {"10001", "10010"}

        eft = next(a for a in offertory.accounts if a.code == "10001")
        assert eft.net_amount == 1500.0
        assert eft.transaction_count == 1

    def test_bank_accounts_not_unmapped(self, sample_entries, chart):
        """Bank accounts (type=BANK) should be silently excluded, not flagged unmapped."""
        result = aggregate_journals(sample_entries, chart=chart)
        unmapped_codes = {a.code for a in result.unmapped_accounts}
        assert "90001" not in unmapped_codes

    def test_unmapped_revenue_detected(self, chart):
        entries = [
            _make_entry("j-x", "2026-03-01", [
                _line("55555", "Unknown Income", 100.0, "REVENUE"),
            ]),
        ]
        result = aggregate_journals(entries, chart=chart)
        assert len(result.unmapped_accounts) == 1
        assert result.unmapped_accounts[0].code == "55555"

    def test_legacy_account_mapped(self, chart):
        """Legacy accounts (e.g., 10005) should still map correctly."""
        entries = [
            _make_entry("j-legacy", "2026-03-01", [
                _line("10005", "Offering Family 8AM", 200.0, "REVENUE"),
            ]),
        ]
        result = aggregate_journals(entries, chart=chart)
        offertory = next(c for c in result.categories if c.key == "offertory")
        assert offertory.net_amount == 200.0

        legacy_acct = next(a for a in offertory.accounts if a.code == "10005")
        assert legacy_acct.is_legacy is True

    def test_empty_entries(self, chart):
        result = aggregate_journals([], chart=chart)
        assert result.journal_count == 0
        assert result.total_income == 0.0
        assert result.total_expenses == 0.0
        assert result.categories == []

    def test_categories_sorted_income_first(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart)
        sections = [c.section for c in result.categories]
        # All income should come before expenses
        income_indices = [i for i, s in enumerate(sections) if s == "income"]
        expense_indices = [i for i, s in enumerate(sections) if s == "expenses"]
        if income_indices and expense_indices:
            assert max(income_indices) < min(expense_indices)


# ---------------------------------------------------------------------------
# Tracking category breakdown
# ---------------------------------------------------------------------------


class TestTrackingBreakdown:
    def test_tracking_aggregated(self, chart):
        entries = [
            _make_entry("j-t1", "2026-03-01", [
                _line(
                    "10001", "Offering EFT", 1000.0, "REVENUE",
                    tracking=[TrackingTag(
                        tracking_category_id="tc-1",
                        tracking_category_name="Congregations",
                        option_id="opt-1",
                        option_name="Morning",
                    )],
                ),
            ]),
            _make_entry("j-t2", "2026-03-02", [
                _line(
                    "10001", "Offering EFT", 500.0, "REVENUE",
                    tracking=[TrackingTag(
                        tracking_category_id="tc-1",
                        tracking_category_name="Congregations",
                        option_id="opt-2",
                        option_name="Evening",
                    )],
                ),
            ]),
        ]
        result = aggregate_journals(entries, chart=chart)
        assert "Congregations" in result.tracking_breakdown
        tb = result.tracking_breakdown["Congregations"]
        assert tb.option_totals["Morning"] == 1000.0
        assert tb.option_totals["Evening"] == 500.0

    def test_no_tracking(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart)
        # Sample entries have no tracking tags
        assert result.tracking_breakdown == {}


# ---------------------------------------------------------------------------
# FinancialSnapshot conversion
# ---------------------------------------------------------------------------


class TestAggregationToSnapshot:
    def test_produces_valid_snapshot(self, sample_entries, chart):
        result = aggregate_journals(
            sample_entries, chart=chart,
            from_date="2026-03-01", to_date="2026-03-31",
        )
        snapshot = aggregation_to_snapshot(result)

        assert isinstance(snapshot, FinancialSnapshot)
        assert snapshot.source == "journal_aggregation"
        assert snapshot.from_date == "2026-03-01"
        assert snapshot.to_date == "2026-03-31"
        assert len(snapshot.rows) > 0

    def test_snapshot_has_correct_amounts(self, sample_entries, chart):
        result = aggregate_journals(sample_entries, chart=chart)
        snapshot = aggregation_to_snapshot(result)

        # Find offering EFT in snapshot
        eft = next((r for r in snapshot.rows if r.account_code == "10001"), None)
        assert eft is not None
        assert eft.amount == 1500.0

    def test_zero_amounts_excluded(self, chart):
        entries = [
            _make_entry("j-zero", "2026-03-01", [
                _line("10001", "Offering EFT", 100.0, "REVENUE"),
                _line("10001", "Offering EFT", -100.0, "REVENUE"),  # nets to zero
            ]),
        ]
        result = aggregate_journals(entries, chart=chart)
        snapshot = aggregation_to_snapshot(result)
        # Account 10001 nets to zero, should be excluded
        codes = [r.account_code for r in snapshot.rows]
        assert "10001" not in codes


# ---------------------------------------------------------------------------
# Journal loading from disk
# ---------------------------------------------------------------------------


class TestLoadJournals:
    def test_load_from_disk(self, tmp_path, chart):
        # Create journal file
        month_dir = tmp_path / "2026" / "2026-03"
        month_dir.mkdir(parents=True)
        entry = _make_entry("j-disk", "2026-03-15", [
            _line("10001", "Offering EFT", 500.0, "REVENUE"),
        ])
        (month_dir / "journals.json").write_text(
            json.dumps([entry.model_dump()], default=str),
            encoding="utf-8",
        )

        entries = load_journals(journals_dir=tmp_path)
        assert len(entries) == 1
        assert entries[0].journal_id == "j-disk"

    def test_load_with_date_filter(self, tmp_path):
        month_dir = tmp_path / "2026" / "2026-03"
        month_dir.mkdir(parents=True)
        entries_data = [
            _make_entry("j-1", "2026-03-05", [_line("10001", "A", 100, "REVENUE")]),
            _make_entry("j-2", "2026-03-25", [_line("10001", "A", 200, "REVENUE")]),
        ]
        (month_dir / "journals.json").write_text(
            json.dumps([e.model_dump() for e in entries_data], default=str),
            encoding="utf-8",
        )

        result = load_journals(from_date="2026-03-10", to_date="2026-03-31", journals_dir=tmp_path)
        assert len(result) == 1
        assert result[0].journal_id == "j-2"

    def test_load_empty_dir(self, tmp_path):
        result = load_journals(journals_dir=tmp_path)
        assert result == []

    def test_load_nonexistent_dir(self):
        result = load_journals(journals_dir=Path("/nonexistent/path"))
        assert result == []


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_aggregate_month(self, tmp_path, chart_path):
        month_dir = tmp_path / "2026" / "2026-03"
        month_dir.mkdir(parents=True)
        entry = _make_entry("j-m", "2026-03-15", [
            _line("10001", "Offering EFT", 750.0, "REVENUE"),
            _line("90001", "Bank", -750.0, "BANK"),
        ])
        (month_dir / "journals.json").write_text(
            json.dumps([entry.model_dump()], default=str),
            encoding="utf-8",
        )

        result = aggregate_month(2026, 3, journals_dir=tmp_path, chart_path=chart_path)
        assert result.journal_count == 1
        assert result.total_income == 750.0

    def test_aggregate_ytd(self, tmp_path, chart_path):
        # Create journals for two months
        for month, amount in [(1, 1000.0), (2, 1500.0)]:
            month_dir = tmp_path / "2026" / f"2026-{month:02d}"
            month_dir.mkdir(parents=True)
            entry = _make_entry(f"j-{month}", f"2026-{month:02d}-15", [
                _line("10001", "Offering EFT", amount, "REVENUE"),
                _line("90001", "Bank", -amount, "BANK"),
            ])
            (month_dir / "journals.json").write_text(
                json.dumps([entry.model_dump()], default=str),
                encoding="utf-8",
            )

        result = aggregate_ytd(year=2026, journals_dir=tmp_path, chart_path=chart_path)
        assert result.journal_count == 2
        assert result.total_income == 2500.0
