"""Tests for the Xero Journals API client and models (CHA-262).

Covers:
- JournalEntry / JournalLine / TrackingTag model validation
- parse_journal_entries() with realistic Xero fixture data
- fetch_journals() pagination logic (mocked)
- Client-side date filtering
- Empty response handling
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.journal import JournalEntry, JournalLine, TrackingTag
from app.xero.client import parse_journal_entries


# ---------------------------------------------------------------------------
# Fixture data — mirrors real Xero Journals API response shape
# ---------------------------------------------------------------------------

SAMPLE_JOURNAL_RAW = {
    "JournalID": "j-001",
    "JournalNumber": 42,
    "JournalDate": "2026-03-15T00:00:00",
    "SourceID": "inv-123",
    "SourceType": "ACCREC",
    "Reference": "Offering March",
    "CreatedDateUTC": "2026-03-16T08:30:00",
    "JournalLines": [
        {
            "JournalLineID": "jl-001",
            "AccountID": "acc-10001",
            "AccountCode": "10001",
            "AccountName": "Offering EFT",
            "AccountType": "REVENUE",
            "NetAmount": 1500.00,
            "GrossAmount": 1500.00,
            "TaxAmount": 0.0,
            "Description": "March EFT offering",
            "TrackingCategories": [
                {
                    "TrackingCategoryID": "tc-001",
                    "Name": "Congregations",
                    "Option": "Morning",
                },
            ],
        },
        {
            "JournalLineID": "jl-002",
            "AccountID": "acc-bank",
            "AccountCode": "90001",
            "AccountName": "Main Bank Account",
            "AccountType": "BANK",
            "NetAmount": -1500.00,
            "GrossAmount": -1500.00,
            "TaxAmount": 0.0,
            "Description": "",
            "TrackingCategories": [],
        },
    ],
}

SAMPLE_JOURNAL_MINIMAL = {
    "JournalID": "j-002",
    "JournalNumber": 43,
    "JournalDate": "2026-03-20T00:00:00",
    "JournalLines": [],
}


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------


class TestTrackingTag:
    def test_create(self):
        tag = TrackingTag(
            tracking_category_id="tc-001",
            tracking_category_name="Congregations",
            option_id="opt-001",
            option_name="Morning",
        )
        assert tag.tracking_category_name == "Congregations"
        assert tag.option_name == "Morning"


class TestJournalLine:
    def test_full_line(self):
        line = JournalLine(
            journal_line_id="jl-001",
            account_id="acc-10001",
            account_code="10001",
            account_name="Offering EFT",
            account_type="REVENUE",
            net_amount=1500.00,
            gross_amount=1500.00,
            tax_amount=0.0,
            description="March EFT offering",
            tracking=[
                TrackingTag(
                    tracking_category_id="tc-001",
                    tracking_category_name="Congregations",
                    option_id="opt-001",
                    option_name="Morning",
                )
            ],
        )
        assert line.net_amount == 1500.00
        assert len(line.tracking) == 1

    def test_defaults(self):
        line = JournalLine(
            journal_line_id="jl-x",
            account_id="acc-x",
            account_code="99999",
            account_name="Test",
            account_type="EXPENSE",
            net_amount=100.0,
        )
        assert line.gross_amount == 0.0
        assert line.tax_amount == 0.0
        assert line.description == ""
        assert line.tracking == []


class TestJournalEntry:
    def test_full_entry(self):
        entry = JournalEntry(
            journal_id="j-001",
            journal_number="42",
            journal_date="2026-03-15",
            source_id="inv-123",
            source_type="ACCREC",
            reference="Offering March",
            lines=[
                JournalLine(
                    journal_line_id="jl-001",
                    account_id="acc-10001",
                    account_code="10001",
                    account_name="Offering EFT",
                    account_type="REVENUE",
                    net_amount=1500.00,
                ),
            ],
            created_date_utc="2026-03-16T08:30:00",
        )
        assert entry.journal_date == "2026-03-15"
        assert len(entry.lines) == 1
        assert entry.source_type == "ACCREC"

    def test_defaults(self):
        entry = JournalEntry(
            journal_id="j-x",
            journal_number="1",
            journal_date="2026-01-01",
        )
        assert entry.source_id == ""
        assert entry.source_type == ""
        assert entry.reference == ""
        assert entry.lines == []
        assert entry.created_date_utc == ""


# ---------------------------------------------------------------------------
# parse_journal_entries tests
# ---------------------------------------------------------------------------


class TestParseJournalEntries:
    def test_parse_full_journal(self):
        entries = parse_journal_entries([SAMPLE_JOURNAL_RAW])
        assert len(entries) == 1
        entry = entries[0]
        assert entry.journal_id == "j-001"
        assert entry.journal_number == "42"
        assert entry.journal_date == "2026-03-15"
        assert entry.source_type == "ACCREC"
        assert entry.reference == "Offering March"
        assert len(entry.lines) == 2

        # First line — revenue with tracking
        line0 = entry.lines[0]
        assert line0.account_code == "10001"
        assert line0.account_name == "Offering EFT"
        assert line0.net_amount == 1500.00
        assert len(line0.tracking) == 1
        assert line0.tracking[0].tracking_category_name == "Congregations"
        assert line0.tracking[0].option_name == "Morning"

        # Second line — bank (debit side)
        line1 = entry.lines[1]
        assert line1.account_code == "90001"
        assert line1.net_amount == -1500.00
        assert line1.tracking == []

    def test_parse_minimal_journal(self):
        entries = parse_journal_entries([SAMPLE_JOURNAL_MINIMAL])
        assert len(entries) == 1
        entry = entries[0]
        assert entry.journal_id == "j-002"
        assert entry.journal_number == "43"
        assert entry.lines == []
        assert entry.source_id == ""

    def test_parse_empty_list(self):
        entries = parse_journal_entries([])
        assert entries == []

    def test_parse_multiple_journals(self):
        entries = parse_journal_entries([SAMPLE_JOURNAL_RAW, SAMPLE_JOURNAL_MINIMAL])
        assert len(entries) == 2
        assert entries[0].journal_id == "j-001"
        assert entries[1].journal_id == "j-002"

    def test_parse_missing_fields_uses_defaults(self):
        raw = {
            "JournalID": "j-sparse",
            "JournalNumber": 99,
            "JournalDate": "2026-01-01T00:00:00",
            "JournalLines": [
                {
                    "JournalLineID": "jl-sparse",
                    "AccountID": "acc-x",
                    "AccountCode": "11111",
                    "AccountName": "Unknown",
                    "AccountType": "EXPENSE",
                    "NetAmount": 50.0,
                    # GrossAmount, TaxAmount, Description, TrackingCategories missing
                },
            ],
        }
        entries = parse_journal_entries([raw])
        line = entries[0].lines[0]
        assert line.gross_amount == 0.0
        assert line.tax_amount == 0.0
        assert line.description == ""
        assert line.tracking == []

    def test_parse_null_description(self):
        """Xero sometimes returns null for Description."""
        raw = {
            "JournalID": "j-null",
            "JournalNumber": 100,
            "JournalDate": "2026-02-01T00:00:00",
            "JournalLines": [
                {
                    "JournalLineID": "jl-null",
                    "AccountID": "acc-y",
                    "AccountCode": "22222",
                    "AccountName": "Test",
                    "AccountType": "REVENUE",
                    "NetAmount": 75.0,
                    "Description": None,
                    "TrackingCategories": [],
                },
            ],
        }
        entries = parse_journal_entries([raw])
        assert entries[0].lines[0].description == ""


# ---------------------------------------------------------------------------
# fetch_journals pagination tests (mocked)
# ---------------------------------------------------------------------------


class TestFetchJournals:
    @pytest.mark.asyncio
    async def test_single_page(self):
        """Fewer than 100 journals — single page, no further requests."""
        journals_page = [
            {"JournalID": f"j-{i}", "JournalNumber": i, "JournalDate": "2026-03-01T00:00:00", "JournalLines": []}
            for i in range(5)
        ]
        mock_request = AsyncMock(return_value={"Journals": journals_page})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals()

        assert len(result) == 5
        mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        """Exactly 100 on first page triggers second request."""
        page1 = [
            {"JournalID": f"j-{i}", "JournalNumber": i, "JournalDate": "2026-03-01T00:00:00", "JournalLines": []}
            for i in range(100)
        ]
        page2 = [
            {"JournalID": f"j-{i}", "JournalNumber": i, "JournalDate": "2026-03-01T00:00:00", "JournalLines": []}
            for i in range(100, 130)
        ]

        mock_request = AsyncMock(side_effect=[
            {"Journals": page1},
            {"Journals": page2},
        ])

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals()

        assert len(result) == 130
        assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """No journals at all."""
        mock_request = AsyncMock(return_value={"Journals": []})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals()

        assert result == []

    @pytest.mark.asyncio
    async def test_date_filtering_from(self):
        """from_date excludes earlier journals."""
        journals = [
            {"JournalID": "j-old", "JournalNumber": 1, "JournalDate": "2026-01-15T00:00:00", "JournalLines": []},
            {"JournalID": "j-new", "JournalNumber": 2, "JournalDate": "2026-03-15T00:00:00", "JournalLines": []},
        ]
        mock_request = AsyncMock(return_value={"Journals": journals})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals(from_date="2026-03-01")

        assert len(result) == 1
        assert result[0]["JournalID"] == "j-new"

    @pytest.mark.asyncio
    async def test_date_filtering_to(self):
        """to_date excludes later journals."""
        journals = [
            {"JournalID": "j-early", "JournalNumber": 1, "JournalDate": "2026-02-15T00:00:00", "JournalLines": []},
            {"JournalID": "j-late", "JournalNumber": 2, "JournalDate": "2026-04-15T00:00:00", "JournalLines": []},
        ]
        mock_request = AsyncMock(return_value={"Journals": journals})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals(to_date="2026-03-31")

        assert len(result) == 1
        assert result[0]["JournalID"] == "j-early"

    @pytest.mark.asyncio
    async def test_date_filtering_range(self):
        """Both from_date and to_date narrow results."""
        journals = [
            {"JournalID": "j-1", "JournalNumber": 1, "JournalDate": "2026-01-01T00:00:00", "JournalLines": []},
            {"JournalID": "j-2", "JournalNumber": 2, "JournalDate": "2026-02-15T00:00:00", "JournalLines": []},
            {"JournalID": "j-3", "JournalNumber": 3, "JournalDate": "2026-04-01T00:00:00", "JournalLines": []},
        ]
        mock_request = AsyncMock(return_value={"Journals": journals})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            result = await fetch_journals(from_date="2026-02-01", to_date="2026-03-31")

        assert len(result) == 1
        assert result[0]["JournalID"] == "j-2"

    @pytest.mark.asyncio
    async def test_offset_parameter_passed(self):
        """Starting offset is used in first request."""
        mock_request = AsyncMock(return_value={"Journals": []})

        with patch("app.xero.client._xero_request", mock_request):
            from app.xero.client import fetch_journals
            await fetch_journals(offset=500)

        mock_request.assert_called_once_with("GET", "/Journals", params={"offset": 500})


# ---------------------------------------------------------------------------
# Scope test
# ---------------------------------------------------------------------------


class TestJournalsScope:
    def test_journals_scope_not_included(self):
        """Journals API requires Xero Advanced tier — scope must not be requested."""
        from app.xero.oauth import XERO_SCOPES
        assert "accounting.journals.read" not in XERO_SCOPES
