"""Tests for account mapping service and router (CHA-268).

Covers:
- Service CRUD: load, save, create, rename, delete, add, remove, move
- Atomic write behaviour
- Duplicate code validation
- Delete non-empty category guard
- Unmapped account detection
- Router auth enforcement
- Router happy-path CRUD via TestClient
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from starlette.testclient import TestClient

from app.main import app
from app.models import Account, BudgetCategory, ChartOfAccounts
from app.models.auth import User
from app.services.account_mapping import (
    add_account,
    create_category,
    delete_category,
    find_unmapped_accounts,
    list_categories,
    load_chart,
    move_account,
    remove_account,
    rename_category,
    save_chart,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CHART = {
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
        "empty_cat": {
            "budget_label": "Empty Category",
        },
    },
}


@pytest.fixture
def chart_path(tmp_path):
    """Create a temporary chart YAML for testing."""
    path = tmp_path / "chart_of_accounts.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(SAMPLE_CHART, f, default_flow_style=False, sort_keys=False)
    return path


# ---------------------------------------------------------------------------
# Service: load / save
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_load_chart(self, chart_path):
        chart = load_chart(chart_path)
        assert "offertory" in chart.income
        assert chart.income["offertory"].budget_label == "1 - Offertory"
        assert len(chart.income["offertory"].accounts) == 2

    def test_save_chart_roundtrip(self, chart_path):
        chart = load_chart(chart_path)
        chart.income["offertory"].budget_label = "Modified Label"
        save_chart(chart, chart_path)

        reloaded = load_chart(chart_path)
        assert reloaded.income["offertory"].budget_label == "Modified Label"

    def test_save_chart_atomic_has_header(self, chart_path):
        chart = load_chart(chart_path)
        save_chart(chart, chart_path)

        content = chart_path.read_text(encoding="utf-8")
        assert content.startswith("# config/chart_of_accounts.yaml")

    def test_save_chart_no_temp_file_left(self, chart_path):
        chart = load_chart(chart_path)
        save_chart(chart, chart_path)

        temps = list(chart_path.parent.glob(".chart_*.yaml.tmp"))
        assert temps == []


# ---------------------------------------------------------------------------
# Service: list / get
# ---------------------------------------------------------------------------


class TestListCategories:
    def test_list_all(self, chart_path):
        result = list_categories(path=chart_path)
        assert "income" in result
        assert "expenses" in result
        assert len(result["income"]) == 2
        assert len(result["expenses"]) == 2

    def test_list_section_filter(self, chart_path):
        result = list_categories(path=chart_path, section="income")
        assert "income" in result
        assert "expenses" not in result

    def test_category_dict_structure(self, chart_path):
        result = list_categories(path=chart_path)
        offertory = result["income"][0]
        assert offertory["key"] == "offertory"
        assert offertory["budget_label"] == "1 - Offertory"
        assert offertory["current_count"] == 2
        assert offertory["legacy_count"] == 1
        assert offertory["total_accounts"] == 3


# ---------------------------------------------------------------------------
# Service: create category
# ---------------------------------------------------------------------------


class TestCreateCategory:
    def test_create_new(self, chart_path):
        result = create_category("income", "New Category", path=chart_path)
        assert result["key"] == "new_category"
        assert result["budget_label"] == "New Category"
        assert result["total_accounts"] == 0

        # Verify persisted
        chart = load_chart(chart_path)
        assert "new_category" in chart.income

    def test_create_with_explicit_key(self, chart_path):
        result = create_category("expenses", "Test", key="custom_key", path=chart_path)
        assert result["key"] == "custom_key"

    def test_create_duplicate_key_raises(self, chart_path):
        with pytest.raises(ValueError, match="already exists"):
            create_category("income", "Offertory Dup", key="offertory", path=chart_path)


# ---------------------------------------------------------------------------
# Service: rename category
# ---------------------------------------------------------------------------


class TestRenameCategory:
    def test_rename(self, chart_path):
        rename_category("income", "offertory", "Offertory Renamed", path=chart_path)
        chart = load_chart(chart_path)
        assert chart.income["offertory"].budget_label == "Offertory Renamed"

    def test_rename_nonexistent_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            rename_category("income", "nope", "Label", path=chart_path)


# ---------------------------------------------------------------------------
# Service: delete category
# ---------------------------------------------------------------------------


class TestDeleteCategory:
    def test_delete_empty(self, chart_path):
        delete_category("expenses", "empty_cat", path=chart_path)
        chart = load_chart(chart_path)
        assert "empty_cat" not in chart.expenses

    def test_delete_non_empty_raises(self, chart_path):
        with pytest.raises(ValueError, match="still has"):
            delete_category("income", "offertory", path=chart_path)

    def test_delete_nonexistent_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            delete_category("income", "nope", path=chart_path)


# ---------------------------------------------------------------------------
# Service: add account
# ---------------------------------------------------------------------------


class TestAddAccount:
    def test_add_current_account(self, chart_path):
        add_account("expenses", "empty_cat", "99999", "Test Account", path=chart_path)
        chart = load_chart(chart_path)
        accts = chart.expenses["empty_cat"].accounts
        assert len(accts) == 1
        assert accts[0].code == "99999"

    def test_add_legacy_account(self, chart_path):
        add_account("expenses", "empty_cat", "88888", "Legacy Test", is_legacy=True, path=chart_path)
        chart = load_chart(chart_path)
        assert len(chart.expenses["empty_cat"].legacy_accounts) == 1

    def test_add_property_account(self, chart_path):
        add_account("expenses", "empty_cat", "77777", "Property Test", is_property=True, path=chart_path)
        chart = load_chart(chart_path)
        assert len(chart.expenses["empty_cat"].property_costs) == 1

    def test_add_duplicate_code_raises(self, chart_path):
        with pytest.raises(ValueError, match="already exists"):
            add_account("expenses", "empty_cat", "10001", "Dup Code", path=chart_path)

    def test_add_to_nonexistent_category_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            add_account("income", "nope", "99999", "Test", path=chart_path)


# ---------------------------------------------------------------------------
# Service: remove account
# ---------------------------------------------------------------------------


class TestRemoveAccount:
    def test_remove_current(self, chart_path):
        remove_account("income", "offertory", "10001", path=chart_path)
        chart = load_chart(chart_path)
        codes = [a.code for a in chart.income["offertory"].accounts]
        assert "10001" not in codes

    def test_remove_legacy(self, chart_path):
        remove_account("income", "offertory", "10005", path=chart_path)
        chart = load_chart(chart_path)
        assert len(chart.income["offertory"].legacy_accounts) == 0

    def test_remove_nonexistent_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            remove_account("income", "offertory", "99999", path=chart_path)


# ---------------------------------------------------------------------------
# Service: move account
# ---------------------------------------------------------------------------


class TestMoveAccount:
    def test_move_between_categories(self, chart_path):
        move_account(
            "income", "offertory",
            "income", "property_income",
            "10001",
            path=chart_path,
        )
        chart = load_chart(chart_path)
        # Removed from source
        src_codes = [a.code for a in chart.income["offertory"].accounts]
        assert "10001" not in src_codes
        # Added to dest
        dst_codes = [a.code for a in chart.income["property_income"].accounts]
        assert "10001" in dst_codes

    def test_move_to_legacy_list(self, chart_path):
        move_account(
            "income", "offertory",
            "income", "property_income",
            "10001",
            target_list="legacy_accounts",
            path=chart_path,
        )
        chart = load_chart(chart_path)
        legacy_codes = [a.code for a in chart.income["property_income"].legacy_accounts]
        assert "10001" in legacy_codes

    def test_move_across_sections(self, chart_path):
        move_account(
            "income", "offertory",
            "expenses", "empty_cat",
            "10001",
            path=chart_path,
        )
        chart = load_chart(chart_path)
        assert "10001" not in [a.code for a in chart.income["offertory"].accounts]
        assert "10001" in [a.code for a in chart.expenses["empty_cat"].accounts]

    def test_move_nonexistent_code_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            move_account("income", "offertory", "income", "property_income", "99999", path=chart_path)

    def test_move_to_nonexistent_dest_raises(self, chart_path):
        with pytest.raises(KeyError, match="not found"):
            move_account("income", "offertory", "income", "nope", "10001", path=chart_path)


# ---------------------------------------------------------------------------
# Service: find unmapped
# ---------------------------------------------------------------------------


class TestFindUnmapped:
    def test_all_mapped(self, chart_path):
        result = find_unmapped_accounts(["10001", "40100"], path=chart_path)
        assert result == []

    def test_some_unmapped(self, chart_path):
        result = find_unmapped_accounts(["10001", "55555", "66666"], path=chart_path)
        assert result == ["55555", "66666"]

    def test_empty_input(self, chart_path):
        result = find_unmapped_accounts([], path=chart_path)
        assert result == []


# ---------------------------------------------------------------------------
# Router: auth enforcement
# ---------------------------------------------------------------------------

ADMIN_USER = User(email="admin@test.com", name="Admin", role="admin")
BOARD_USER = User(email="board@test.com", name="Board", role="board")
STAFF_USER = User(email="staff@test.com", name="Staff", role="staff")


def _auth_override(user: User):
    """Create a dependency override that returns the given user."""
    def _get_user(request):
        request.state.user = user
        return user
    return _get_user


class TestRouterAuth:
    def test_admin_can_access(self):
        import app.middleware.auth as auth_mod
        auth_mod.override_user = ADMIN_USER
        try:
            client = TestClient(app)
            response = client.get("/settings/accounts")
            assert response.status_code == 200
            assert "Account Mapping" in response.text
        finally:
            auth_mod.override_user = None

    def test_staff_user_gets_403(self):
        """Staff users (non-admin) should be blocked from account mapping."""
        import app.middleware.auth as auth_mod
        auth_mod.override_user = STAFF_USER
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/settings/accounts")
            assert response.status_code == 403
        finally:
            auth_mod.override_user = None


# ---------------------------------------------------------------------------
# Router: CRUD via TestClient (with patched service + auth)
# ---------------------------------------------------------------------------


class TestRouterCRUD:
    """Test router endpoints with a patched chart path."""

    @pytest.fixture(autouse=True)
    def setup_chart(self, tmp_path):
        import app.middleware.auth as auth_mod

        self.chart_path = tmp_path / "chart_of_accounts.yaml"
        with open(self.chart_path, "w", encoding="utf-8") as f:
            yaml.dump(SAMPLE_CHART, f, default_flow_style=False, sort_keys=False)

        # Patch the module-level CHART_PATH and set admin user
        self._chart_patcher = patch(
            "app.services.account_mapping.CHART_PATH",
            self.chart_path,
        )
        self._chart_patcher.start()
        auth_mod.override_user = ADMIN_USER
        self.client = TestClient(app, raise_server_exceptions=False)
        # Prime CSRF cookie
        self.client.get("/health")
        yield
        self._chart_patcher.stop()
        auth_mod.override_user = None

    def _get(self, path):
        return self.client.get(path)

    def _post(self, path, data=None):
        return self.client.post(path, data=data)

    def _put(self, path, data=None):
        return self.client.put(path, data=data)

    def _delete(self, path):
        return self.client.delete(path)

    def test_get_page(self):
        resp = self._get("/settings/accounts")
        assert resp.status_code == 200
        assert "Account Mapping" in resp.text

    def test_get_categories_partial(self):
        resp = self._get("/settings/accounts/categories")
        assert resp.status_code == 200
        assert "Offertory" in resp.text

    def test_create_category(self):
        resp = self._post("/settings/accounts/category", data={
            "section": "income",
            "budget_label": "Test Category",
        })
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        assert "test_category" in chart.income

    def test_rename_category(self):
        resp = self._put("/settings/accounts/category/income/offertory", data={
            "new_label": "Offertory Renamed",
        })
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        assert chart.income["offertory"].budget_label == "Offertory Renamed"

    def test_delete_empty_category(self):
        resp = self._delete("/settings/accounts/category/expenses/empty_cat")
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        assert "empty_cat" not in chart.expenses

    def test_delete_non_empty_returns_error(self):
        resp = self._delete("/settings/accounts/category/income/offertory")
        assert resp.status_code == 200  # still 200, error in HTML
        assert "still has" in resp.text

    def test_add_account(self):
        resp = self._post("/settings/accounts/account", data={
            "section": "expenses",
            "category": "empty_cat",
            "code": "99999",
            "name": "Test Account",
            "account_type": "current",
        })
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        codes = [a.code for a in chart.expenses["empty_cat"].accounts]
        assert "99999" in codes

    def test_remove_account(self):
        resp = self._delete("/settings/accounts/account/income/offertory/10001")
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        codes = [a.code for a in chart.income["offertory"].accounts]
        assert "10001" not in codes

    def test_move_account(self):
        resp = self._post("/settings/accounts/move", data={
            "from_section": "income",
            "from_category": "offertory",
            "to_section": "income",
            "to_category": "property_income",
            "code": "10001",
            "target_list": "accounts",
        })
        assert resp.status_code == 200
        chart = load_chart(self.chart_path)
        assert "10001" not in [a.code for a in chart.income["offertory"].accounts]
        assert "10001" in [a.code for a in chart.income["property_income"].accounts]
