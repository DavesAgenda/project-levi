"""Tests for journal sync service and LLM-friendly storage (CHA-263, CHA-264).

Covers:
- Journal storage (JSON and text formats)
- LLM-friendly text formatting
- Monthly summary generation
- Sync state persistence
- Group-by-month logic
- Full sync orchestration (mocked Xero API)
- Incremental sync (offset resume)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models.journal import JournalEntry, JournalLine, TrackingTag
from app.services.journal_sync import (
    _format_journal_text,
    _group_by_month,
    load_sync_state,
    save_journals_json,
    save_journals_text,
    save_monthly_summary_text,
    save_sync_state,
    sync_journals,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    journal_id: str = "j-1",
    number: str = "1",
    journal_date: str = "2026-03-15",
    lines: list[JournalLine] | None = None,
    **kwargs,
) -> JournalEntry:
    if lines is None:
        lines = [
            JournalLine(
                journal_line_id="jl-1",
                account_id="acc-10001",
                account_code="10001",
                account_name="Offering EFT",
                account_type="REVENUE",
                net_amount=1500.00,
                tracking=[
                    TrackingTag(
                        tracking_category_id="tc-1",
                        tracking_category_name="Congregations",
                        option_id="opt-1",
                        option_name="Morning",
                    ),
                ],
            ),
            JournalLine(
                journal_line_id="jl-2",
                account_id="acc-bank",
                account_code="90001",
                account_name="Main Bank",
                account_type="BANK",
                net_amount=-1500.00,
            ),
        ]
    return JournalEntry(
        journal_id=journal_id,
        journal_number=number,
        journal_date=journal_date,
        source_type="ACCREC",
        reference="Test ref",
        lines=lines,
        **kwargs,
    )


@pytest.fixture
def sample_entries():
    return [
        _make_entry("j-1", "1", "2026-03-10"),
        _make_entry("j-2", "2", "2026-03-20"),
        _make_entry("j-3", "3", "2026-04-05"),
    ]


@pytest.fixture
def journals_dir(tmp_path):
    """Patch JOURNALS_DIR to use temp directory."""
    with patch("app.services.journal_sync.JOURNALS_DIR", tmp_path):
        with patch("app.services.journal_sync.SYNC_STATE_FILE", tmp_path / "_sync_state.json"):
            with patch("app.services.journal_sync.DATA_DIR", tmp_path.parent):
                yield tmp_path


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------


class TestFormatJournalText:
    def test_basic_format(self):
        entry = _make_entry()
        text = _format_journal_text(entry)
        assert "Journal #1" in text
        assert "2026-03-15" in text
        assert "Test ref" in text
        assert "10001" in text
        assert "Offering EFT" in text
        assert "DR" in text
        assert "CR" in text
        assert "Congregations: Morning" in text

    def test_no_tracking(self):
        entry = _make_entry(lines=[
            JournalLine(
                journal_line_id="jl-x",
                account_id="acc-x",
                account_code="99999",
                account_name="Test",
                account_type="EXPENSE",
                net_amount=100.0,
            ),
        ])
        text = _format_journal_text(entry)
        assert "99999" in text
        assert "[" not in text  # no tracking tags

    def test_description_shown(self):
        entry = _make_entry(lines=[
            JournalLine(
                journal_line_id="jl-x",
                account_id="acc-x",
                account_code="99999",
                account_name="Test",
                account_type="EXPENSE",
                net_amount=100.0,
                description="March electricity",
            ),
        ])
        text = _format_journal_text(entry)
        assert "March electricity" in text


# ---------------------------------------------------------------------------
# JSON storage
# ---------------------------------------------------------------------------


class TestSaveJournalsJson:
    def test_save_creates_file(self, journals_dir):
        entries = [_make_entry()]
        path = save_journals_json(entries, 2026, 3)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["journal_id"] == "j-1"

    def test_save_directory_structure(self, journals_dir):
        save_journals_json([_make_entry()], 2026, 3)
        expected = journals_dir / "2026" / "2026-03" / "journals.json"
        assert expected.exists()


# ---------------------------------------------------------------------------
# Text storage (LLM-friendly)
# ---------------------------------------------------------------------------


class TestSaveJournalsText:
    def test_save_creates_file(self, journals_dir):
        entries = [_make_entry()]
        path = save_journals_text(entries, 2026, 3)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# Journals for 2026-03" in content
        assert "Journal #1" in content
        assert "10001" in content

    def test_header_has_count(self, journals_dir):
        entries = [_make_entry("j-1", "1"), _make_entry("j-2", "2")]
        path = save_journals_text(entries, 2026, 3)
        content = path.read_text(encoding="utf-8")
        assert "# Count: 2" in content


# ---------------------------------------------------------------------------
# Monthly summary (LLM-friendly)
# ---------------------------------------------------------------------------


class TestSaveMonthlySummary:
    def test_save_creates_file(self, journals_dir):
        entries = [_make_entry()]
        path = save_monthly_summary_text(entries, 2026, 3)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Monthly Summary: 2026-03" in content
        assert "10001" in content
        assert "Offering EFT" in content
        assert "REVENUE" in content

    def test_summary_aggregates(self, journals_dir):
        # Two entries with same account code — should aggregate
        entries = [
            _make_entry("j-1", "1"),
            _make_entry("j-2", "2"),
        ]
        path = save_monthly_summary_text(entries, 2026, 3)
        content = path.read_text(encoding="utf-8")
        # 10001 should appear once with aggregated amount
        assert content.count("10001") == 1
        # Net amount should be 2 x 1500 = 3000
        assert "3,000.00" in content

    def test_summary_has_totals(self, journals_dir):
        entries = [_make_entry()]
        path = save_monthly_summary_text(entries, 2026, 3)
        content = path.read_text(encoding="utf-8")
        assert "Total Income" in content
        assert "Total Expenses" in content
        assert "Net Position" in content


# ---------------------------------------------------------------------------
# Sync state
# ---------------------------------------------------------------------------


class TestSyncState:
    def test_load_empty(self, journals_dir):
        state = load_sync_state()
        assert state["last_offset"] == 0
        assert state["last_sync"] is None

    def test_save_and_load(self, journals_dir):
        save_sync_state({"last_offset": 42, "last_sync": "2026-03-15T00:00:00Z", "total_journals": 100})
        state = load_sync_state()
        assert state["last_offset"] == 42
        assert state["total_journals"] == 100


# ---------------------------------------------------------------------------
# Group by month
# ---------------------------------------------------------------------------


class TestGroupByMonth:
    def test_groups_correctly(self, sample_entries):
        groups = _group_by_month(sample_entries)
        assert (2026, 3) in groups
        assert (2026, 4) in groups
        assert len(groups[(2026, 3)]) == 2
        assert len(groups[(2026, 4)]) == 1

    def test_empty_list(self):
        groups = _group_by_month([])
        assert groups == {}

    def test_invalid_date_skipped(self):
        bad = JournalEntry(
            journal_id="j-bad",
            journal_number="99",
            journal_date="not-a-date",
        )
        groups = _group_by_month([bad])
        assert groups == {}


# ---------------------------------------------------------------------------
# Full sync orchestration (mocked Xero)
# ---------------------------------------------------------------------------

SAMPLE_RAW_JOURNALS = [
    {
        "JournalID": "j-1",
        "JournalNumber": 1,
        "JournalDate": "2026-03-15T00:00:00",
        "SourceType": "ACCREC",
        "Reference": "March offering",
        "JournalLines": [
            {
                "JournalLineID": "jl-1",
                "AccountID": "acc-10001",
                "AccountCode": "10001",
                "AccountName": "Offering EFT",
                "AccountType": "REVENUE",
                "NetAmount": 1500.0,
                "GrossAmount": 1500.0,
                "TaxAmount": 0.0,
                "Description": "March EFT",
                "TrackingCategories": [],
            },
        ],
    },
]


class TestSyncJournals:
    @pytest.mark.asyncio
    async def test_full_sync(self, journals_dir):
        mock_fetch = AsyncMock(return_value=SAMPLE_RAW_JOURNALS)

        with patch("app.services.journal_sync.fetch_journals", mock_fetch):
            result = await sync_journals(
                from_date="2026-03-01",
                to_date="2026-03-31",
                incremental=False,
            )

        assert result["status"] == "ok"
        assert result["journal_count"] == 1
        assert result["months"] == 1
        assert len(result["files_written"]) == 3  # json + txt + summary
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_incremental_uses_offset(self, journals_dir):
        # Set up initial state
        save_sync_state({"last_offset": 50, "last_sync": "2026-03-14T00:00:00Z", "total_journals": 50})

        mock_fetch = AsyncMock(return_value=SAMPLE_RAW_JOURNALS)

        with patch("app.services.journal_sync.fetch_journals", mock_fetch):
            await sync_journals(incremental=True)

        # Should have been called with offset=50
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args
        assert call_kwargs.kwargs.get("offset") == 50 or call_kwargs[1].get("offset") == 50

    @pytest.mark.asyncio
    async def test_sync_error_handling(self, journals_dir):
        mock_fetch = AsyncMock(side_effect=Exception("API timeout"))

        with patch("app.services.journal_sync.fetch_journals", mock_fetch):
            result = await sync_journals(incremental=False)

        assert result["status"] == "error"
        assert result["journal_count"] == 0
        assert "API timeout" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_empty_sync(self, journals_dir):
        mock_fetch = AsyncMock(return_value=[])

        with patch("app.services.journal_sync.fetch_journals", mock_fetch):
            result = await sync_journals(incremental=False)

        assert result["status"] == "ok"
        assert result["journal_count"] == 0
        assert result["files_written"] == []

    @pytest.mark.asyncio
    async def test_sync_updates_state(self, journals_dir):
        mock_fetch = AsyncMock(return_value=SAMPLE_RAW_JOURNALS)

        with patch("app.services.journal_sync.fetch_journals", mock_fetch):
            await sync_journals(incremental=False)

        state = load_sync_state()
        assert state["last_offset"] == 1  # JournalNumber of last entry
        assert state["last_sync"] is not None
        assert state["total_journals"] == 1
