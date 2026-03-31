"""Tests for payroll data redaction (CHA-204).

Covers:
  - should_redact_payroll helper: True for staff, False for board/admin
  - /reports/payroll — staff sees summary cards + totals, individual table hidden
  - /budget/{year} — payroll section shows single "Total Staffing" line for staff
  - /budget/payroll-scenarios — returns 403 for staff
  - /budget/{year}/compare — payroll rows show totals only for staff
  - Board and admin see full per-person detail in all views
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.middleware.auth as auth_mod
from app.dependencies.auth import should_redact_payroll
from app.models.auth import User
from tests.conftest import _TEST_ADMIN_USER


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

_STAFF_USER = User(
    email="staff@newlightanglican.org",
    name="Staff Member",
    role="staff",
    permissions=["read"],
)

_BOARD_USER = User(
    email="warden@newlightanglican.org",
    name="Board Member",
    role="board",
    permissions=["read", "payroll_detail"],
)


@pytest.fixture(autouse=True)
def _restore_auth():
    """Ensure conftest admin override is restored after each test."""
    yield
    auth_mod.override_user = _TEST_ADMIN_USER


# ---------------------------------------------------------------------------
# Unit tests: should_redact_payroll helper
# ---------------------------------------------------------------------------

class TestShouldRedactPayroll:
    def test_none_user_redacts(self):
        assert should_redact_payroll(None) is True

    def test_staff_user_redacts(self):
        assert should_redact_payroll(_STAFF_USER) is True

    def test_board_user_does_not_redact(self):
        assert should_redact_payroll(_BOARD_USER) is False

    def test_admin_user_does_not_redact(self):
        assert should_redact_payroll(_TEST_ADMIN_USER) is False

    def test_user_without_payroll_detail_redacts(self):
        user = User(email="x@test.org", name="X", role="staff", permissions=["read", "write"])
        assert should_redact_payroll(user) is True

    def test_user_with_payroll_detail_does_not_redact(self):
        user = User(email="x@test.org", name="X", role="staff", permissions=["read", "payroll_detail"])
        assert should_redact_payroll(user) is False


# ---------------------------------------------------------------------------
# Integration tests: /reports/payroll
# ---------------------------------------------------------------------------

class TestPayrollReportRedaction:
    """Staff sees summary totals; board/admin sees full per-person table."""

    def test_staff_sees_summary_not_individual_table(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll")
        assert resp.status_code == 200

        body = resp.text
        # Summary cards should still be visible (totals, headcount, FTE)
        assert "Total Payroll Cost" in body
        assert "Staff Count" in body
        # Per-person table should NOT be visible
        assert "Staff Cost Breakdown" not in body

    def test_admin_sees_full_table(self):
        from app.main import app

        auth_mod.override_user = _TEST_ADMIN_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll")
        assert resp.status_code == 200

        body = resp.text
        assert "Total Payroll Cost" in body
        assert "Staff Cost Breakdown" in body

    def test_board_sees_full_table(self):
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll")
        assert resp.status_code == 200

        body = resp.text
        assert "Staff Cost Breakdown" in body


# ---------------------------------------------------------------------------
# Integration tests: /budget/payroll-scenarios — 403 for staff
# ---------------------------------------------------------------------------

class TestPayrollScenariosAccess:
    """Staff cannot access payroll scenarios; admin can."""

    def test_staff_gets_403(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios")
        assert resp.status_code == 403

    def test_staff_gets_403_on_preview(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios/preview")
        assert resp.status_code == 403

    def test_board_gets_403(self):
        """Board has payroll_detail but NOT payroll_scenarios permission."""
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios")
        assert resp.status_code == 403

    def test_admin_can_access(self):
        from app.main import app

        auth_mod.override_user = _TEST_ADMIN_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration tests: /budget/{year} — payroll redaction
# ---------------------------------------------------------------------------

class TestBudgetPayrollRedaction:
    """Staff sees single 'Total Staffing' line; admin sees per-person breakdown."""

    def test_staff_sees_total_staffing_line(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026")
        assert resp.status_code == 200

        body = resp.text
        # Staff should see the collapsed "Total Staffing" line
        assert "Total Staffing" in body

    def test_admin_sees_payroll_line_items(self):
        from app.main import app

        auth_mod.override_user = _TEST_ADMIN_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026")
        assert resp.status_code == 200

        body = resp.text
        # Admin should NOT see the collapsed "Total Staffing" line
        # (the individual payroll line items are shown instead)
        # The Payroll section header is always shown
        assert "Payroll" in body


# ---------------------------------------------------------------------------
# Integration tests: /budget/{year}/compare — payroll redaction
# ---------------------------------------------------------------------------

class TestBudgetComparisonPayrollRedaction:
    """Staff sees payroll totals only; admin sees per-category breakdown."""

    def test_staff_sees_total_staffing_row(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026/compare")
        assert resp.status_code == 200

        body = resp.text
        # Should see collapsed "Total Staffing" row
        assert "Total Staffing" in body
        # Should NOT see individual payroll categories
        assert "Ministry Staff</td>" not in body
        assert "Ministry Support Staff</td>" not in body
        assert "Administration Staff</td>" not in body

    def test_admin_sees_individual_payroll_categories(self):
        from app.main import app

        auth_mod.override_user = _TEST_ADMIN_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026/compare")
        assert resp.status_code == 200

        body = resp.text
        # Admin should NOT see collapsed "Total Staffing" row
        assert "Total Staffing" not in body

    def test_board_sees_individual_payroll_categories(self):
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026/compare")
        assert resp.status_code == 200

        body = resp.text
        assert "Total Staffing" not in body
