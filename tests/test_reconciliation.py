"""Tests for journal vs P&L reconciliation service (CHA-270).

Covers:
- Matching categories (journal == snapshot)
- Minor variance detection (1-5%)
- Major variance detection (>5%)
- Journal-only and snapshot-only categories
- Match rate calculation
- Empty data handling
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.models import FinancialSnapshot, SnapshotRow
from app.models.journal import JournalEntry, JournalLine
from app.services.reconciliation import ReconciliationResult, _classify, reconcile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CHART_YAML = {
    "income": {
        "offertory": {
            "budget_label": "1 - Offertory",
            "accounts": [
                {"code": "10001", "name": "Offering EFT"},
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
    },
}


@pytest.fixture
def chart_path(tmp_path):
    path = tmp_path / "config" / "chart_of_accounts.yaml"
    path.parent.mkdir(parents=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(SAMPLE_CHART_YAML, f, default_flow_style=False, sort_keys=False)
    return path


def _setup_journals(tmp_path, entries_data):
    """Write journal entries to the expected directory structure."""
    jdir = tmp_path / "journals"
    month_dir = jdir / "2026" / "2026-03"
    month_dir.mkdir(parents=True)
    (month_dir / "journals.json").write_text(
        json.dumps(entries_data, default=str), encoding="utf-8",
    )
    return jdir


def _setup_snapshots(tmp_path, rows):
    """Write a P&L snapshot with the given rows."""
    sdir = tmp_path / "snapshots"
    sdir.mkdir(parents=True)
    snapshot = FinancialSnapshot(
        report_date="2026-03-31",
        from_date="2026-01-01",
        to_date="2026-03-31",
        source="xero_api",
        rows=rows,
    )
    (sdir / "pl_2026-01-01_2026-03-31.json").write_text(
        snapshot.model_dump_json(indent=2), encoding="utf-8",
    )
    return sdir


def _make_journal_data(code, name, amount, acct_type="REVENUE"):
    return {
        "journal_id": f"j-{code}",
        "journal_number": "1",
        "journal_date": "2026-03-15",
        "lines": [{
            "journal_line_id": f"jl-{code}",
            "account_id": f"acc-{code}",
            "account_code": code,
            "account_name": name,
            "account_type": acct_type,
            "net_amount": amount,
        }],
    }


# ---------------------------------------------------------------------------
# Classify helper
# ---------------------------------------------------------------------------


class TestClassify:
    def test_exact_match(self):
        assert _classify(0.0, 1000.0) == "match"

    def test_small_variance_is_match(self):
        assert _classify(5.0, 1000.0) == "match"  # 0.5%

    def test_minor_variance(self):
        assert _classify(30.0, 1000.0) == "minor"  # 3%

    def test_major_variance(self):
        assert _classify(100.0, 1000.0) == "major"  # 10%

    def test_zero_reference_is_major(self):
        assert _classify(100.0, 0) == "major"


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_perfect_match(self, tmp_path, chart_path):
        """When journal and snapshot agree, all rows should be 'match'."""
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
        ])
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1500.0),
        ])

        result = reconcile(
            chart_path=chart_path,
            journals_dir=jdir,
            snapshots_dir=sdir,
            year=2026,
        )
        assert result.has_data is True
        assert result.match_count >= 1
        offertory = next(r for r in result.rows if r.category_key == "offertory")
        assert offertory.status == "match"
        assert offertory.variance == 0.0

    def test_minor_variance(self, tmp_path, chart_path):
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
        ])
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1530.0),
        ])

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        offertory = next(r for r in result.rows if r.category_key == "offertory")
        assert offertory.status == "minor"
        assert offertory.variance == -30.0

    def test_major_variance(self, tmp_path, chart_path):
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
        ])
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=2000.0),
        ])

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        offertory = next(r for r in result.rows if r.category_key == "offertory")
        assert offertory.status == "major"

    def test_journal_only(self, tmp_path, chart_path):
        """Category exists in journals but not in snapshots."""
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
        ])
        sdir = _setup_snapshots(tmp_path, [])  # empty snapshot

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        offertory = next(r for r in result.rows if r.category_key == "offertory")
        assert offertory.status == "journal_only"

    def test_snapshot_only(self, tmp_path, chart_path):
        """Category exists in snapshots but not in journals."""
        jdir = tmp_path / "journals"
        jdir.mkdir()  # empty
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1500.0),
        ])

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        offertory = next(r for r in result.rows if r.category_key == "offertory")
        assert offertory.status == "snapshot_only"

    def test_match_rate(self, tmp_path, chart_path):
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
            _make_journal_data("40100", "Ministry Staff", -3000.0, "EXPENSE"),
        ])
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1500.0),
            SnapshotRow(account_code="40100", account_name="Ministry Staff", amount=-3000.0),
        ])

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        assert result.match_rate == 100.0

    def test_no_data(self, tmp_path, chart_path):
        jdir = tmp_path / "journals"
        jdir.mkdir()
        sdir = tmp_path / "snapshots"
        sdir.mkdir()

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        assert result.has_data is False

    def test_income_expense_totals(self, tmp_path, chart_path):
        jdir = _setup_journals(tmp_path, [
            _make_journal_data("10001", "Offering EFT", 1500.0),
            _make_journal_data("40100", "Ministry Staff", -3000.0, "EXPENSE"),
        ])
        sdir = _setup_snapshots(tmp_path, [
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=1500.0),
            SnapshotRow(account_code="40100", account_name="Ministry Staff", amount=-3000.0),
        ])

        result = reconcile(chart_path=chart_path, journals_dir=jdir, snapshots_dir=sdir, year=2026)
        assert result.total_journal_income == 1500.0
        assert result.total_journal_expenses == -3000.0
