"""Tests for budget editing UI routes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.services import budget as budget_service
from app.services import budget_forecast as forecast_service

client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_BUDGET = {
    "year": 2099,
    "status": "draft",
    "income": {
        "offertory": {
            "10001_offering_eft": 100000,
            "10010_offertory_cash": 0,
        },
        "other_income": {
            "12200_interest_income": None,
        },
    },
    "expenses": {
        "administration": {
            "41520_software_licencing": 2000,
            "notes": "Test note",
        },
        "mission_giving": {
            "42501_church_budget": 8500,
        },
    },
}

SAMPLE_BUDGET_APPROVED = {
    "year": 2098,
    "status": "approved",
    "approved_date": "2098-02-15",
    "income": {
        "offertory": {
            "10001_offering_eft": 90000,
        },
    },
    "expenses": {
        "administration": {
            "41520_software_licencing": 1800,
        },
    },
}

SAMPLE_BUDGET_PROPOSED = {
    "year": 2097,
    "status": "proposed",
    "income": {
        "offertory": {
            "10001_offering_eft": 85000,
        },
    },
    "expenses": {},
}


@pytest.fixture
def budget_dir(tmp_path: Path):
    """Create a temp budget directory with a sample budget."""
    bdir = tmp_path / "budgets"
    bdir.mkdir()
    path = bdir / "2099.yaml"
    path.write_text(yaml.dump(SAMPLE_BUDGET, sort_keys=False), encoding="utf-8")

    # Patch the budget service to use our temp dir
    original = budget_service.BUDGETS_DIR
    original_forecast = forecast_service.BUDGETS_DIR
    budget_service.BUDGETS_DIR = bdir
    forecast_service.BUDGETS_DIR = bdir
    yield bdir
    budget_service.BUDGETS_DIR = original
    forecast_service.BUDGETS_DIR = original_forecast


@pytest.fixture
def multi_year_budget_dir(tmp_path: Path):
    """Create a temp budget directory with multiple years for year-selector tests."""
    bdir = tmp_path / "budgets"
    bdir.mkdir()
    (bdir / "2099.yaml").write_text(yaml.dump(SAMPLE_BUDGET, sort_keys=False), encoding="utf-8")
    (bdir / "2098.yaml").write_text(yaml.dump(SAMPLE_BUDGET_APPROVED, sort_keys=False), encoding="utf-8")
    (bdir / "2097.yaml").write_text(yaml.dump(SAMPLE_BUDGET_PROPOSED, sort_keys=False), encoding="utf-8")

    original = budget_service.BUDGETS_DIR
    original_forecast = forecast_service.BUDGETS_DIR
    budget_service.BUDGETS_DIR = bdir
    forecast_service.BUDGETS_DIR = bdir
    yield bdir
    budget_service.BUDGETS_DIR = original
    forecast_service.BUDGETS_DIR = original_forecast


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class TestBudgetView:
    def test_view_returns_200(self, budget_dir):
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        assert "2099 Budget" in resp.text
        assert "Draft" in resp.text

    def test_view_shows_income(self, budget_dir):
        resp = client.get("/budget/2099")
        assert "Offertory" in resp.text
        assert "$100,000" in resp.text

    def test_view_shows_tbd(self, budget_dir):
        resp = client.get("/budget/2099")
        assert "TBD" in resp.text

    def test_view_shows_notes(self, budget_dir):
        resp = client.get("/budget/2099")
        assert "Test note" in resp.text

    def test_view_404_for_missing(self, budget_dir):
        resp = client.get("/budget/2098")
        assert resp.status_code == 404

    def test_edit_mode(self, budget_dir):
        resp = client.get("/budget/2099?edit=true")
        assert resp.status_code == 200
        assert 'name="value"' in resp.text  # edit inputs present
        assert "Done Editing" in resp.text


# ---------------------------------------------------------------------------
# Inline update tests
# ---------------------------------------------------------------------------

class TestInlineUpdate:
    def test_update_line_item(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "120000", "mtime": str(mtime)},
        )
        assert resp.status_code == 200
        assert "120,000" in resp.text

        # Verify persisted
        reloaded = budget_service.load_budget_file(2099, budgets_dir=budget_dir)
        assert reloaded.income["offertory"].account_items()["10001_offering_eft"] == 120000.0

    def test_update_to_null(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "", "mtime": str(mtime)},
        )
        assert resp.status_code == 200
        assert "TBD" in resp.text

    def test_update_invalid_number(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "abc", "mtime": str(mtime)},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Notes update tests
# ---------------------------------------------------------------------------

class TestNotesUpdate:
    def test_update_notes(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/notes/expenses/administration",
            data={"notes": "Updated note", "mtime": str(mtime)},
        )
        assert resp.status_code == 200
        assert "Updated note" in resp.text


# ---------------------------------------------------------------------------
# Status transition tests
# ---------------------------------------------------------------------------

class TestStatusTransition:
    def test_draft_to_proposed(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.post(
            "/budget/2099/status",
            data={"target": "proposed", "mtime": str(mtime)},
        )
        assert resp.status_code == 200
        reloaded = budget_service.load_budget_file(2099, budgets_dir=budget_dir)
        assert reloaded.status.value == "proposed"

    def test_invalid_transition(self, budget_dir):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.post(
            "/budget/2099/status",
            data={"target": "approved", "mtime": str(mtime)},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Create draft tests
# ---------------------------------------------------------------------------

class TestCreateDraft:
    def test_create_new_draft(self, budget_dir):
        resp = client.post(
            "/budget/create-draft",
            data={"year": "2100", "base_year": "0"},
        )
        assert resp.status_code == 200
        assert (budget_dir / "2100.yaml").exists()

    def test_clone_from_existing(self, budget_dir):
        resp = client.post(
            "/budget/create-draft",
            data={"year": "2100", "base_year": "2099"},
        )
        assert resp.status_code == 200
        cloned = budget_service.load_budget_file(2100, budgets_dir=budget_dir)
        assert cloned.status.value == "draft"
        assert "offertory" in cloned.income

    def test_create_duplicate_fails(self, budget_dir):
        resp = client.post(
            "/budget/create-draft",
            data={"year": "2099", "base_year": "0"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Year selector tests
# ---------------------------------------------------------------------------

class TestYearSelector:
    def test_year_selector_shown(self, multi_year_budget_dir):
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        # Should show year links for all budgets
        assert "2099" in resp.text
        assert "2098" in resp.text
        assert "2097" in resp.text

    def test_current_year_highlighted(self, multi_year_budget_dir):
        resp = client.get("/budget/2098")
        assert resp.status_code == 200
        # The current year should have the primary bg class (highlighted)
        # Other years should have border links
        assert 'href="/budget/2099"' in resp.text
        assert 'href="/budget/2097"' in resp.text

    def test_year_selector_shows_status_labels(self, multi_year_budget_dir):
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        assert "2099-draft" in resp.text
        assert "2098" in resp.text  # approved shows just the year
        assert "2097-proposed" in resp.text


# ---------------------------------------------------------------------------
# Lock/Unlock tests
# ---------------------------------------------------------------------------

class TestLockUnlock:
    def test_edit_button_shown_for_draft(self, multi_year_budget_dir):
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        assert "Edit Budget" in resp.text
        assert "Unlock to Edit" not in resp.text

    def test_unlock_button_shown_for_approved(self, multi_year_budget_dir):
        resp = client.get("/budget/2098")
        assert resp.status_code == 200
        assert "Unlock to Edit" in resp.text
        assert "Edit Budget" not in resp.text

    def test_unlock_button_shown_for_proposed(self, multi_year_budget_dir):
        resp = client.get("/budget/2097")
        assert resp.status_code == 200
        assert "Unlock to Edit" in resp.text

    def test_edit_mode_blocked_for_non_draft(self, multi_year_budget_dir):
        """Requesting edit=true on a non-draft budget should not enable edit mode."""
        resp = client.get("/budget/2098?edit=true")
        assert resp.status_code == 200
        # Should NOT show edit inputs since it's approved
        assert 'name="value"' not in resp.text

    def test_unlock_confirmation_dialog_shown(self, multi_year_budget_dir):
        resp = client.get("/budget/2098")
        assert resp.status_code == 200
        assert "unlock-dialog" in resp.text
        assert "Editing will revert it to" in resp.text
        assert "approved" in resp.text

    def test_unlock_transitions_to_draft(self, multi_year_budget_dir):
        mtime = (multi_year_budget_dir / "2098.yaml").stat().st_mtime
        resp = client.post(
            "/budget/2098/unlock",
            data={"mtime": str(mtime)},
        )
        assert resp.status_code == 200
        # Should redirect to edit mode
        assert resp.headers.get("HX-Redirect") == "/budget/2098?edit=true"

        # Verify status changed
        reloaded = budget_service.load_budget_file(2098, budgets_dir=multi_year_budget_dir)
        assert reloaded.status.value == "draft"

    def test_unlock_proposed_transitions_to_draft(self, multi_year_budget_dir):
        mtime = (multi_year_budget_dir / "2097.yaml").stat().st_mtime
        resp = client.post(
            "/budget/2097/unlock",
            data={"mtime": str(mtime)},
        )
        assert resp.status_code == 200
        reloaded = budget_service.load_budget_file(2097, budgets_dir=multi_year_budget_dir)
        assert reloaded.status.value == "draft"

    def test_unlock_already_draft_redirects(self, multi_year_budget_dir):
        mtime = (multi_year_budget_dir / "2099.yaml").stat().st_mtime
        resp = client.post(
            "/budget/2099/unlock",
            data={"mtime": str(mtime)},
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == "/budget/2099?edit=true"


# ---------------------------------------------------------------------------
# Reference columns tests
# ---------------------------------------------------------------------------

class TestReferenceColumns:
    def test_reference_columns_in_table_header(self, multi_year_budget_dir):
        """When viewing 2099, should show 2098 reference columns."""
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        assert "2098 Forecast" in resp.text
        assert "2098 Budget" in resp.text
        assert "Var $" in resp.text
        assert "Var %" in resp.text

    def test_prior_year_budget_values_shown(self, multi_year_budget_dir):
        """Prior year budget amounts should appear in reference columns."""
        resp = client.get("/budget/2099")
        assert resp.status_code == 200
        # The 2098 budget has $90,000 for offertory and $1,800 for software
        assert "$90,000" in resp.text
        assert "$1,800" in resp.text
