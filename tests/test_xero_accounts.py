"""Tests for the Xero Accounts UUID->code cache and its integration with
snapshot conversion.

Covers:
- ``load_uuid_to_code`` reads both enveloped and bare Accounts responses,
  skips accounts missing a code, and returns {} on a missing cache.
- ``fetch_and_cache_accounts`` writes an enveloped cache and returns data.
- ``xero_snapshot_to_financial`` prefers UUID match over name match and
  preserves ``account_id`` on every SnapshotRow.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.xero.accounts import (
    fetch_and_cache_accounts,
    load_uuid_to_code,
)


# ---------------------------------------------------------------------------
# load_uuid_to_code
# ---------------------------------------------------------------------------


class TestLoadUuidToCode:
    def test_missing_cache_returns_empty(self, tmp_path: Path):
        assert load_uuid_to_code(tmp_path / "nope.json") == {}

    def test_reads_enveloped_response(self, tmp_path: Path):
        cache = tmp_path / "xero_accounts.json"
        cache.write_text(json.dumps({
            "fetched_at": "2026-04-20T00:00:00Z",
            "response": {
                "Accounts": [
                    {"AccountID": "uuid-a", "Code": "10001", "Name": "Offering EFT"},
                    {"AccountID": "uuid-b", "Code": "40100", "Name": "Salaries"},
                ],
            },
        }))
        assert load_uuid_to_code(cache) == {"uuid-a": "10001", "uuid-b": "40100"}

    def test_reads_bare_response(self, tmp_path: Path):
        cache = tmp_path / "xero_accounts.json"
        cache.write_text(json.dumps({
            "Accounts": [{"AccountID": "uuid-a", "Code": "10001", "Name": "X"}],
        }))
        assert load_uuid_to_code(cache) == {"uuid-a": "10001"}

    def test_skips_accounts_without_code(self, tmp_path: Path):
        cache = tmp_path / "xero_accounts.json"
        cache.write_text(json.dumps({
            "response": {
                "Accounts": [
                    {"AccountID": "uuid-a", "Code": "10001", "Name": "Real"},
                    {"AccountID": "uuid-b", "Name": "No code"},
                    {"Code": "99999", "Name": "No uuid"},
                ],
            },
        }))
        assert load_uuid_to_code(cache) == {"uuid-a": "10001"}

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        cache = tmp_path / "xero_accounts.json"
        cache.write_text("{not json")
        assert load_uuid_to_code(cache) == {}


# ---------------------------------------------------------------------------
# fetch_and_cache_accounts
# ---------------------------------------------------------------------------


class TestFetchAndCacheAccounts:
    @pytest.mark.asyncio
    async def test_writes_enveloped_cache(self, tmp_path: Path):
        sample = {"Accounts": [{"AccountID": "u1", "Code": "10001", "Name": "Offering"}]}
        cache = tmp_path / "xero_accounts.json"

        with patch("app.xero.accounts.fetch_accounts", return_value=sample):
            result = await fetch_and_cache_accounts(cache)

        assert result == sample
        on_disk = json.loads(cache.read_text())
        assert on_disk["response"] == sample
        assert "fetched_at" in on_disk
        # Lookup roundtrips correctly
        assert load_uuid_to_code(cache) == {"u1": "10001"}


# ---------------------------------------------------------------------------
# xero_snapshot_to_financial — UUID-first resolution
# ---------------------------------------------------------------------------


def _raw_pl_snapshot(rows: list[tuple[str, str, str]]) -> dict:
    """Build a minimal enveloped raw P&L snapshot.

    rows: list of (account_name, account_uuid, amount_str) tuples for the
    Income section. Only the shape the converter needs is provided.
    """
    section_rows = []
    for name, uuid, amount in rows:
        section_rows.append({
            "RowType": "Row",
            "Cells": [
                {"Value": name, "Attributes": [{"Value": uuid, "Id": "account"}]},
                {"Value": amount, "Attributes": [{"Value": uuid, "Id": "account"}]},
            ],
        })

    return {
        "snapshot_metadata": {
            "saved_at": "2026-04-20T00:00:00Z",
            "report_type": "pl",
            "from_date": "2026-01-01",
            "to_date": "2026-03-31",
        },
        "response": {
            "Reports": [{
                "ReportID": "ProfitAndLoss",
                "ReportName": "Profit and Loss",
                "ReportDate": "31 March 2026",
                "UpdatedDateUTC": "/Date(1743321600000+0000)/",
                "Rows": [
                    {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "31 Mar 2026"}]},
                    {"RowType": "Section", "Title": "Income", "Rows": section_rows},
                ],
            }],
        },
    }


class TestXeroSnapshotToFinancialUuidMatch:
    def test_uuid_match_resolves_code_when_name_lookup_misses(self, tmp_path: Path):
        """A row whose name isn't in chart_of_accounts.yaml still gets a
        code via the UUID cache."""
        cache = tmp_path / "xero_accounts.json"
        cache.write_text(json.dumps({"response": {"Accounts": [
            {"AccountID": "uuid-tap", "Code": "10099", "Name": "Tap Offertory"},
        ]}}))

        raw = _raw_pl_snapshot([("Tap Offertory", "uuid-tap", "401.19")])

        with patch("app.xero.snapshots.load_uuid_to_code", return_value=load_uuid_to_code(cache)), \
             patch("app.xero.snapshots._build_name_lookup", return_value={}):
            from app.xero.snapshots import xero_snapshot_to_financial
            snap = xero_snapshot_to_financial(raw)

        assert snap is not None
        assert len(snap.rows) == 1
        row = snap.rows[0]
        assert row.account_code == "10099"
        assert row.account_id == "uuid-tap"
        assert row.account_name == "Tap Offertory"

    def test_uuid_match_wins_over_name_match(self, tmp_path: Path):
        """If UUID and name lookups disagree, UUID wins (deterministic)."""
        raw = _raw_pl_snapshot([("Renamed In Xero", "uuid-x", "100.00")])

        with patch("app.xero.snapshots.load_uuid_to_code", return_value={"uuid-x": "22222"}), \
             patch("app.xero.snapshots._build_name_lookup", return_value={"renamedinxero": "99999"}):
            from app.xero.snapshots import xero_snapshot_to_financial
            snap = xero_snapshot_to_financial(raw)

        assert snap.rows[0].account_code == "22222"

    def test_name_fallback_when_uuid_absent(self):
        """Rows whose UUID isn't cached fall back to name-based matching."""
        raw = _raw_pl_snapshot([("Offering EFT", "uuid-not-cached", "500.00")])

        with patch("app.xero.snapshots.load_uuid_to_code", return_value={}), \
             patch("app.xero.snapshots._build_name_lookup", return_value={"offeringeft": "10001"}):
            from app.xero.snapshots import xero_snapshot_to_financial
            snap = xero_snapshot_to_financial(raw)

        assert snap.rows[0].account_code == "10001"
        assert snap.rows[0].account_id == "uuid-not-cached"

    def test_account_id_always_preserved(self):
        """Even when no code can be resolved, the UUID is kept on the row."""
        raw = _raw_pl_snapshot([("Mystery Account", "uuid-mystery", "250.00")])

        with patch("app.xero.snapshots.load_uuid_to_code", return_value={}), \
             patch("app.xero.snapshots._build_name_lookup", return_value={}):
            from app.xero.snapshots import xero_snapshot_to_financial
            snap = xero_snapshot_to_financial(raw)

        assert snap.rows[0].account_code == ""
        assert snap.rows[0].account_id == "uuid-mystery"
