"""Tests for the Xero sync service and endpoints.

Covers:
- Date calculation (prior month, YTD range)
- Sync service: monthly + manual, with mock Xero client
- Idempotency (calling twice overwrites same files)
- Error handling (partial failures)
- Sync logging
- Router auth: admin session, API key, unauthorized
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.models.auth import User
from app.services.sync import (
    SYNC_LOG_FILE,
    _append_sync_log,
    current_ytd_range,
    prior_completed_month,
    sync_monthly,
    sync_now,
)


@pytest.fixture(autouse=True)
def _stub_accounts_cache_refresh():
    """Every sync now refreshes the Xero accounts UUID->code cache.
    These tests mock Xero report endpoints directly; stub the accounts
    fetch too so the helper doesn't try to hit the real API."""
    with patch(
        "app.services.sync.fetch_and_cache_accounts",
        new=AsyncMock(return_value={"Accounts": []}),
    ):
        yield


# ---------------------------------------------------------------------------
# Date helper tests
# ---------------------------------------------------------------------------


class TestPriorCompletedMonth:
    def test_april_gives_march(self):
        first, last = prior_completed_month(date(2026, 4, 3))
        assert first == date(2026, 3, 1)
        assert last == date(2026, 3, 31)

    def test_january_gives_december(self):
        first, last = prior_completed_month(date(2026, 1, 15))
        assert first == date(2025, 12, 1)
        assert last == date(2025, 12, 31)

    def test_march_gives_february_non_leap(self):
        first, last = prior_completed_month(date(2026, 3, 1))
        assert first == date(2026, 2, 1)
        assert last == date(2026, 2, 28)

    def test_march_gives_february_leap(self):
        first, last = prior_completed_month(date(2028, 3, 1))
        assert first == date(2028, 2, 1)
        assert last == date(2028, 2, 29)

    def test_first_day_of_month(self):
        first, last = prior_completed_month(date(2026, 7, 1))
        assert first == date(2026, 6, 1)
        assert last == date(2026, 6, 30)

    def test_last_day_of_month(self):
        first, last = prior_completed_month(date(2026, 5, 31))
        assert first == date(2026, 4, 1)
        assert last == date(2026, 4, 30)


class TestCurrentYtdRange:
    def test_mid_year(self):
        start, end = current_ytd_range(date(2026, 4, 3))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 4, 3)

    def test_january_first(self):
        start, end = current_ytd_range(date(2026, 1, 1))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 1, 1)

    def test_december(self):
        start, end = current_ytd_range(date(2026, 12, 31))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Sync log tests
# ---------------------------------------------------------------------------


class TestSyncLog:
    def test_append_creates_file(self, tmp_path: Path, monkeypatch):
        log_dir = tmp_path / "sync"
        log_file = log_dir / "sync_log.json"
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_file)

        _append_sync_log({"test": "entry1"})
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["test"] == "entry1"

    def test_append_adds_to_existing(self, tmp_path: Path, monkeypatch):
        log_dir = tmp_path / "sync"
        log_file = log_dir / "sync_log.json"
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_file)

        _append_sync_log({"n": 1})
        _append_sync_log({"n": 2})

        data = json.loads(log_file.read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["n"] == 1
        assert data[1]["n"] == 2


# ---------------------------------------------------------------------------
# Sync service tests (mock Xero client)
# ---------------------------------------------------------------------------

MOCK_PL_RESPONSE = {"Reports": [{"ReportID": "ProfitAndLoss"}]}
MOCK_BS_RESPONSE = {"Reports": [{"ReportID": "BalanceSheet"}]}


class TestSyncMonthly:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path, monkeypatch):
        """Monthly sync fetches P&L + BS for prior month and saves snapshots."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            result = await sync_monthly(today=date(2026, 4, 3))

        assert result["status"] == "ok"
        assert result["month"] == "2026-03"
        assert len(result["snapshots"]) == 2
        assert result["errors"] == []

        # Verify correct date args
        mock_pl.assert_called_once_with("2026-03-01", "2026-03-31")
        mock_bs.assert_called_once_with("2026-03-31")

        # Verify files written
        files = list(snapshot_dir.iterdir())
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_idempotent(self, tmp_path: Path, monkeypatch):
        """Calling sync_monthly twice overwrites the same files."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            await sync_monthly(today=date(2026, 4, 3))
            await sync_monthly(today=date(2026, 4, 3))

        # Still only 2 files (overwritten, not duplicated)
        files = list(snapshot_dir.iterdir())
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_partial_failure(self, tmp_path: Path, monkeypatch):
        """If P&L succeeds but BS fails, return partial status."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(side_effect=RuntimeError("Xero timeout"))

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            result = await sync_monthly(today=date(2026, 4, 3))

        assert result["status"] == "partial"
        assert len(result["snapshots"]) == 1
        assert len(result["errors"]) == 1
        assert "Balance Sheet fetch failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_total_failure(self, tmp_path: Path, monkeypatch):
        """If both fetches fail, return error status."""
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(side_effect=RuntimeError("No tokens"))
        mock_bs = AsyncMock(side_effect=RuntimeError("No tokens"))

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            result = await sync_monthly(today=date(2026, 4, 3))

        assert result["status"] == "error"
        assert len(result["snapshots"]) == 0
        assert len(result["errors"]) == 2

    @pytest.mark.asyncio
    async def test_sync_log_written(self, tmp_path: Path, monkeypatch):
        """Sync writes to the sync log file."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        log_file = log_dir / "sync_log.json"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_file)

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            await sync_monthly(today=date(2026, 4, 3))

        assert log_file.exists()
        entries = json.loads(log_file.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["type"] == "monthly"
        assert entries[0]["status"] == "ok"
        assert entries[0]["month"] == "2026-03"


class TestSyncNow:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path, monkeypatch):
        """Manual sync fetches monthly P&L snapshots + current BS + tracking categories."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)
        mock_tc = AsyncMock(return_value={"TrackingCategories": []})

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs), \
             patch("app.services.sync.fetch_tracking_categories", mock_tc):
            result = await sync_now(today=date(2026, 4, 3))

        assert result["status"] == "ok"
        assert result["period"] == "2026-01 to 2026-04"
        # 4 monthly P&L (Jan, Feb, Mar, Apr) + 1 BS + 1 tracking categories = 6 snapshots
        assert len(result["snapshots"]) == 6

        # Monthly P&L calls: Jan, Feb, Mar complete months + Apr partial
        assert mock_pl.call_count == 4
        mock_pl.assert_any_call("2026-01-01", "2026-01-31")
        mock_pl.assert_any_call("2026-02-01", "2026-02-28")
        mock_pl.assert_any_call("2026-03-01", "2026-03-31")
        mock_pl.assert_any_call("2026-04-01", "2026-04-03")
        mock_bs.assert_called_once_with("2026-04-03")
        mock_tc.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_log_written(self, tmp_path: Path, monkeypatch):
        """Manual sync writes to the sync log."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        log_file = log_dir / "sync_log.json"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_file)

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)
        mock_tc = AsyncMock(return_value={"TrackingCategories": []})

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs), \
             patch("app.services.sync.fetch_tracking_categories", mock_tc):
            await sync_now(today=date(2026, 4, 3))

        entries = json.loads(log_file.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["type"] == "manual"
        assert entries[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Router / endpoint tests
# ---------------------------------------------------------------------------


client = TestClient(app)


class TestSyncMonthlyEndpoint:
    def test_admin_session_success(self, monkeypatch, tmp_path: Path):
        """Admin user can call sync-monthly via session cookie."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            resp = client.post("/api/xero/sync-monthly")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["snapshots"]) == 2

    def test_api_key_success(self, monkeypatch, tmp_path: Path):
        """Valid API key can call sync-monthly."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")
        monkeypatch.setenv("SYNC_API_KEY", "test-secret-key-123")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs):
            resp = client.post(
                "/api/xero/sync-monthly",
                headers={"X-API-Key": "test-secret-key-123"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_invalid_api_key_rejected(self, monkeypatch):
        """Invalid API key returns 401."""
        monkeypatch.setenv("SYNC_API_KEY", "correct-key")

        resp = client.post(
            "/api/xero/sync-monthly",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_non_admin_session_rejected(self, monkeypatch):
        """Non-admin user without API key is rejected."""
        import app.middleware.auth as auth_mod
        original_user = auth_mod.override_user

        staff_user = User(
            email="staff@newlightanglican.org",
            name="Staff User",
            role="staff",
            permissions=["read"],
        )
        auth_mod.override_user = staff_user

        try:
            resp = client.post("/api/xero/sync-monthly")
            assert resp.status_code == 403
        finally:
            auth_mod.override_user = original_user


class TestSyncNowEndpoint:
    def test_admin_success(self, monkeypatch, tmp_path: Path):
        """Admin user can call sync-now."""
        snapshot_dir = tmp_path / "snapshots"
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.xero.snapshots.SNAPSHOTS_DIR", snapshot_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(return_value=MOCK_PL_RESPONSE)
        mock_bs = AsyncMock(return_value=MOCK_BS_RESPONSE)
        mock_tc = AsyncMock(return_value={"TrackingCategories": []})

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs), \
             patch("app.services.sync.fetch_tracking_categories", mock_tc):
            resp = client.post("/api/xero/sync-now")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "period" in data

    def test_non_admin_rejected(self, monkeypatch):
        """Non-admin user is rejected from sync-now."""
        import app.middleware.auth as auth_mod
        original_user = auth_mod.override_user

        staff_user = User(
            email="staff@newlightanglican.org",
            name="Staff User",
            role="staff",
            permissions=["read"],
        )
        auth_mod.override_user = staff_user

        try:
            resp = client.post("/api/xero/sync-now")
            assert resp.status_code == 403
        finally:
            auth_mod.override_user = original_user

    def test_error_handling(self, monkeypatch, tmp_path: Path):
        """Xero errors are caught and returned as JSON."""
        log_dir = tmp_path / "sync"
        monkeypatch.setattr("app.services.sync.SYNC_LOG_DIR", log_dir)
        monkeypatch.setattr("app.services.sync.SYNC_LOG_FILE", log_dir / "sync_log.json")

        mock_pl = AsyncMock(side_effect=RuntimeError("Token expired"))
        mock_bs = AsyncMock(side_effect=RuntimeError("Token expired"))
        mock_tc = AsyncMock(side_effect=RuntimeError("Token expired"))

        with patch("app.services.sync.fetch_profit_and_loss", mock_pl), \
             patch("app.services.sync.fetch_balance_sheet", mock_bs), \
             patch("app.services.sync.fetch_tracking_categories", mock_tc):
            resp = client.post("/api/xero/sync-now")

        assert resp.status_code == 200  # Errors returned in body, not as HTTP error
        data = resp.json()
        assert data["status"] == "error"
        # Monthly P&L errors (one per month of current year) + 1 BS error
        assert len(data["errors"]) >= 2

    def test_api_key_not_accepted(self, monkeypatch):
        """sync-now requires admin session — API key alone is not accepted."""
        import app.middleware.auth as auth_mod
        original_user = auth_mod.override_user

        # Clear session user to test API-key-only path
        auth_mod.override_user = None

        try:
            monkeypatch.setenv("SYNC_API_KEY", "test-key")
            no_redirect_client = TestClient(app, follow_redirects=False)
            resp = no_redirect_client.post(
                "/api/xero/sync-now",
                headers={"X-API-Key": "test-key"},
            )
            # sync-now uses require_role("admin"), which needs session user.
            # Without a session, auth middleware redirects to login.
            assert resp.status_code == 302
            assert "/auth/login" in resp.headers.get("location", "")
        finally:
            auth_mod.override_user = original_user
