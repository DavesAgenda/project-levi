"""Tests for budget approval workflow routes (CHA-196).

Covers: status transitions via HTTP, changelog/history view,
edit prevention on locked budgets, create-amendment flow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.models.budget import BudgetFile, BudgetStatus
from app.services.budget import (
    load_budget_file,
    load_changelog,
    save_budget_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_budgets(tmp_path: Path) -> Path:
    d = tmp_path / "budgets"
    d.mkdir()
    return d


@pytest.fixture
def draft_budget_on_disk(tmp_budgets: Path) -> Path:
    """Write a minimal draft budget YAML and return the budgets dir."""
    data = {
        "year": 2026,
        "status": "draft",
        "income": {"tithes": {"4100_tithes": 100000}},
        "expenses": {"staff": {"6100_salaries": 80000}},
    }
    (tmp_budgets / "2026.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8"
    )
    return tmp_budgets


@pytest.fixture
def app_with_tmp_budgets(draft_budget_on_disk: Path):
    """Create a FastAPI test client with budget service pointed at tmp dir."""
    import app.services.budget as budget_svc
    from app.routers import budget_workflow as bw_mod

    # Patch the module-level BUDGETS_DIR
    original_dir = budget_svc.BUDGETS_DIR
    budget_svc.BUDGETS_DIR = draft_budget_on_disk

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)

    yield client

    budget_svc.BUDGETS_DIR = original_dir


# ---------------------------------------------------------------------------
# Status transition tests
# ---------------------------------------------------------------------------

class TestStatusTransition:
    def test_draft_to_proposed(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition",
            data={"new_status": "proposed", "user": "treasurer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "proposed"
        assert body["year"] == 2026

    def test_proposed_to_approved(self, app_with_tmp_budgets: TestClient, draft_budget_on_disk: Path):
        # First transition to proposed
        app_with_tmp_budgets.post(
            "/budget/2026/transition",
            data={"new_status": "proposed"},
        )
        # Then approve
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition",
            data={"new_status": "approved", "user": "vestry"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_date"] is not None

    def test_proposed_revert_to_draft(self, app_with_tmp_budgets: TestClient):
        # Propose first
        app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "proposed"}
        )
        # Revert
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "draft"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "draft"

    def test_cannot_skip_proposed(self, app_with_tmp_budgets: TestClient):
        """Draft cannot jump directly to approved."""
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "approved"}
        )
        assert resp.status_code == 409

    def test_invalid_status_value(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "bogus"}
        )
        assert resp.status_code == 400

    def test_nonexistent_year(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.post(
            "/budget/2098/transition", data={"new_status": "proposed"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Approved budget protection
# ---------------------------------------------------------------------------

class TestApprovedProtection:
    def _approve(self, client: TestClient):
        client.post("/budget/2026/transition", data={"new_status": "proposed"})
        client.post("/budget/2026/transition", data={"new_status": "approved"})

    def test_cannot_transition_approved_without_amendment(self, app_with_tmp_budgets: TestClient):
        self._approve(app_with_tmp_budgets)
        resp = app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "draft"}
        )
        assert resp.status_code == 409

    def test_status_endpoint_shows_not_editable(self, app_with_tmp_budgets: TestClient):
        self._approve(app_with_tmp_budgets)
        resp = app_with_tmp_budgets.get("/budget/2026/status")
        assert resp.status_code == 200
        assert resp.json()["editable"] is False

    def test_draft_is_editable(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.get("/budget/2026/status")
        assert resp.json()["editable"] is True

    def test_proposed_is_not_editable(self, app_with_tmp_budgets: TestClient):
        app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "proposed"}
        )
        resp = app_with_tmp_budgets.get("/budget/2026/status")
        assert resp.json()["editable"] is False


# ---------------------------------------------------------------------------
# Create amendment
# ---------------------------------------------------------------------------

class TestCreateAmendment:
    def _approve(self, client: TestClient):
        client.post("/budget/2026/transition", data={"new_status": "proposed"})
        client.post("/budget/2026/transition", data={"new_status": "approved"})

    def test_amend_approved_budget(self, app_with_tmp_budgets: TestClient):
        self._approve(app_with_tmp_budgets)
        resp = app_with_tmp_budgets.post(
            "/budget/2026/create-amendment", data={"user": "treasurer"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"
        assert "amendment" in body["message"].lower() or "draft" in body["message"].lower()

    def test_cannot_amend_draft(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.post(
            "/budget/2026/create-amendment", data={"user": "treasurer"}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Changelog / history view
# ---------------------------------------------------------------------------

class TestBudgetHistory:
    def test_history_returns_html(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.get("/budget/2026/history")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Budget 2026" in resp.text

    def test_history_after_transitions(self, app_with_tmp_budgets: TestClient):
        app_with_tmp_budgets.post(
            "/budget/2026/transition", data={"new_status": "proposed"}
        )
        resp = app_with_tmp_budgets.get("/budget/2026/history")
        assert resp.status_code == 200
        assert "proposed" in resp.text.lower()

    def test_history_nonexistent_year(self, app_with_tmp_budgets: TestClient):
        resp = app_with_tmp_budgets.get("/budget/2098/history")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Changelog entries are recorded
# ---------------------------------------------------------------------------

class TestChangelogRecording:
    def test_transition_creates_changelog(self, app_with_tmp_budgets: TestClient, draft_budget_on_disk: Path):
        app_with_tmp_budgets.post(
            "/budget/2026/transition",
            data={"new_status": "proposed", "user": "treasurer"},
        )
        entries = load_changelog(2026, budgets_dir=draft_budget_on_disk)
        status_changes = [e for e in entries if e.action == "status_change"]
        assert len(status_changes) >= 1
        assert status_changes[-1].summary == "Status changed: draft -> proposed"

    def test_versioned_snapshot_on_approval(self, app_with_tmp_budgets: TestClient, draft_budget_on_disk: Path):
        """When approved, a history file should exist."""
        app_with_tmp_budgets.post("/budget/2026/transition", data={"new_status": "proposed"})
        app_with_tmp_budgets.post("/budget/2026/transition", data={"new_status": "approved"})
        history_dir = draft_budget_on_disk / "history"
        assert history_dir.exists()
        history_files = list(history_dir.glob("2026_v*.yaml"))
        assert len(history_files) >= 1
