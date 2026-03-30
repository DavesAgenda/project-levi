"""Security regression tests for M3 (Budget Planning) — CHA-198.

Tests the fixes applied during the M3 pre-deploy security audit:
- section_type validation
- item_key injection prevention
- year range validation
- notes XSS sanitization
- budget amount bounds
- user spoofing prevention
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.services import budget as budget_service

client = TestClient(app)

SAMPLE_BUDGET = {
    "year": 2099,
    "status": "draft",
    "income": {
        "offertory": {
            "10001_offering_eft": 100000,
        },
    },
    "expenses": {
        "administration": {
            "41520_software_licencing": 2000,
            "notes": "Original note",
        },
    },
}


@pytest.fixture
def budget_dir(tmp_path: Path):
    """Create a temp budget directory with a sample budget."""
    bdir = tmp_path / "budgets"
    bdir.mkdir()
    path = bdir / "2099.yaml"
    path.write_text(yaml.dump(SAMPLE_BUDGET, sort_keys=False), encoding="utf-8")
    original = budget_service.BUDGETS_DIR
    budget_service.BUDGETS_DIR = bdir
    yield bdir
    budget_service.BUDGETS_DIR = original


# ---------------------------------------------------------------------------
# section_type validation (H-01)
# ---------------------------------------------------------------------------


class TestSectionTypeValidation:
    """H-01: section_type must be 'income' or 'expenses'."""

    def test_invalid_section_type_on_line_update(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/INVALID/offertory/10001_offering_eft",
            data={"value": "5000", "mtime": str(mtime)},
        )
        assert resp.status_code == 400
        assert "section_type" in resp.text

    def test_invalid_section_type_on_notes_update(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/notes/INVALID/offertory",
            data={"notes": "test", "mtime": str(mtime)},
        )
        assert resp.status_code == 400

    def test_valid_section_types_accepted(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "5000", "mtime": str(mtime)},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# item_key injection (H-02)
# ---------------------------------------------------------------------------


class TestItemKeyInjection:
    """H-02: item_key must match safe patterns only."""

    def test_reject_dunder_key(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/__class__",
            data={"value": "999", "mtime": str(mtime)},
        )
        assert resp.status_code == 400
        assert "Invalid item key" in resp.text

    def test_reject_dotted_key(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/../../etc/passwd",
            data={"value": "999", "mtime": str(mtime)},
        )
        # FastAPI may return 404 for path with slashes, or 400 — either is acceptable
        assert resp.status_code in (400, 404, 422)

    def test_reject_overlong_key(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        long_key = "a" * 200
        resp = client.put(
            f"/budget/2099/line/income/offertory/{long_key}",
            data={"value": "999", "mtime": str(mtime)},
        )
        assert resp.status_code == 400

    def test_accept_normal_account_key(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "5000", "mtime": str(mtime)},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Year range validation
# ---------------------------------------------------------------------------


class TestYearValidation:
    """Year must be within 2000-2100."""

    def test_reject_negative_year(self, budget_dir: Path):
        resp = client.get("/budget/-1")
        assert resp.status_code == 400

    def test_reject_far_future_year(self, budget_dir: Path):
        resp = client.get("/budget/9999")
        assert resp.status_code == 400

    def test_accept_valid_year(self, budget_dir: Path):
        resp = client.get("/budget/2099")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Notes XSS (M-01)
# ---------------------------------------------------------------------------


class TestNotesXSS:
    """M-01: Notes must not allow stored XSS."""

    def test_script_tag_stripped_from_notes(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/notes/expenses/administration",
            data={
                "notes": '<script>alert("xss")</script>Clean text',
                "mtime": str(mtime),
            },
        )
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert "Clean text" in resp.text

    def test_img_onerror_stripped(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/notes/expenses/administration",
            data={
                "notes": '<img src=x onerror=alert(1)>Safe note',
                "mtime": str(mtime),
            },
        )
        assert resp.status_code == 200
        assert "onerror" not in resp.text


# ---------------------------------------------------------------------------
# Budget amount bounds (M-04)
# ---------------------------------------------------------------------------


class TestAmountBounds:
    """M-04: Budget amounts must be within reasonable bounds."""

    def test_reject_extreme_positive_value(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "999999999999", "mtime": str(mtime)},
        )
        assert resp.status_code == 422
        assert "maximum" in resp.text.lower()

    def test_reject_extreme_negative_value(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "-999999999999", "mtime": str(mtime)},
        )
        assert resp.status_code == 422

    def test_accept_reasonable_value(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "150000", "mtime": str(mtime)},
        )
        assert resp.status_code == 200

    def test_accept_empty_value_as_tbd(self, budget_dir: Path):
        mtime = (budget_dir / "2099.yaml").stat().st_mtime
        resp = client.put(
            "/budget/2099/line/income/offertory/10001_offering_eft",
            data={"value": "", "mtime": str(mtime)},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# User spoofing prevention (M-02)
# ---------------------------------------------------------------------------


class TestUserSpoofing:
    """M-02: Workflow endpoints should not accept arbitrary user from form."""

    def test_transition_ignores_user_param(self, budget_dir: Path):
        """The user field should be hardcoded, not taken from form data."""
        resp = client.post(
            "/budget/2099/transition",
            data={"new_status": "proposed", "user": "evil_admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "proposed"
        # Verify changelog uses hardcoded user, not form input
        from app.services.budget import load_changelog
        changelog = load_changelog(2099, budgets_dir=budget_dir)
        for entry in changelog:
            assert entry.user != "evil_admin", "User spoofing was not prevented"


# ---------------------------------------------------------------------------
# Path containment in budget service
# ---------------------------------------------------------------------------


class TestPathContainment:
    """Budget service must reject years that could cause path traversal."""

    def test_load_rejects_out_of_range_year(self):
        from app.services.budget import BudgetValidationError, load_budget_file
        with pytest.raises(BudgetValidationError):
            load_budget_file(0)

    def test_save_rejects_out_of_range_year(self):
        from app.models.budget import BudgetFile
        from app.services.budget import BudgetValidationError, save_budget_file
        budget = BudgetFile(year=0)
        with pytest.raises(BudgetValidationError):
            save_budget_file(budget)
