"""Tests for the budget data service (CHA-192).

Covers: load, save, validate, status transitions, changelog,
optimistic concurrency, property income, payroll computation,
draft creation, and null-vs-zero preservation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from app.models.budget import BudgetFile, BudgetSection, BudgetStatus, ChangelogEntry
from app.services.budget import (
    BudgetConcurrencyError,
    BudgetNotFoundError,
    BudgetServiceError,
    BudgetStatusError,
    BudgetValidationError,
    compute_payroll_budget,
    compute_property_income,
    create_draft_budget,
    load_budget_file,
    load_budget_flat,
    load_changelog,
    save_budget_file,
    get_budget_mtime,
    transition_status,
    validate_budget,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_budgets(tmp_path: Path) -> Path:
    """Create a temporary budgets directory."""
    d = tmp_path / "budgets"
    d.mkdir()
    return d


@pytest.fixture
def sample_budget_yaml() -> dict:
    """Minimal budget YAML dict matching the real structure."""
    return {
        "year": 2026,
        "status": "draft",
        "income": {
            "offertory": {
                "10001_offering_eft": 100000,
                "10010_offertory_cash": 0,
            },
            "property_income": {
                "overrides": {
                    "loane_39": {"weekly_rate": 600},
                },
                "vacancy_weeks": {},
            },
            "other_income": {
                "12200_interest_income": 3000,
                "12100_surplice_fees": None,  # TBD — null preserved
            },
        },
        "expenses": {
            "payroll": {
                "notes": "See payroll.yaml",
            },
            "mission_giving": {
                "42501_church_budget": 8500,
                "42502_other_missions": 2500,
                "notes": "Total $11,000",
            },
            "ministry_expenses": {
                "41000_ministry_expenses": None,
            },
        },
    }


@pytest.fixture
def budget_file(tmp_budgets: Path, sample_budget_yaml: dict) -> Path:
    """Write a sample budget YAML file and return its path."""
    p = tmp_budgets / "2026.yaml"
    p.write_text(yaml.dump(sample_budget_yaml, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture
def chart_path() -> Path:
    """Path to the real chart_of_accounts.yaml in the project."""
    p = Path(__file__).resolve().parent.parent / "config" / "chart_of_accounts.yaml"
    if not p.exists():
        pytest.skip("chart_of_accounts.yaml not found")
    return p


@pytest.fixture
def properties_path() -> Path:
    p = Path(__file__).resolve().parent.parent / "config" / "properties.yaml"
    if not p.exists():
        pytest.skip("properties.yaml not found")
    return p


@pytest.fixture
def payroll_path() -> Path:
    p = Path(__file__).resolve().parent.parent / "config" / "payroll.yaml"
    if not p.exists():
        pytest.skip("payroll.yaml not found")
    return p


# ---------------------------------------------------------------------------
# Load tests
# ---------------------------------------------------------------------------

class TestLoadBudgetFile:
    def test_load_returns_budget_file(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        assert isinstance(budget, BudgetFile)
        assert budget.year == 2026
        assert budget.status == BudgetStatus.draft

    def test_load_preserves_null_values(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        other = budget.income["other_income"]
        items = other.account_items()
        assert items["12100_surplice_fees"] is None
        assert items["12200_interest_income"] == 3000

    def test_load_preserves_zero(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        offertory = budget.income["offertory"]
        items = offertory.account_items()
        assert items["10010_offertory_cash"] == 0

    def test_load_not_found_raises(self, tmp_budgets: Path):
        with pytest.raises(BudgetNotFoundError):
            load_budget_file(2098, budgets_dir=tmp_budgets)

    def test_load_sections_have_notes(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        assert budget.expenses["mission_giving"].notes == "Total $11,000"

    def test_load_sections_have_overrides(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        pi = budget.income["property_income"]
        assert pi.overrides is not None
        assert "loane_39" in pi.overrides
        assert pi.overrides["loane_39"].weekly_rate == 600


# ---------------------------------------------------------------------------
# Save tests
# ---------------------------------------------------------------------------

class TestSaveBudgetFile:
    def test_save_creates_file(self, tmp_budgets: Path):
        budget = BudgetFile(year=2027, status=BudgetStatus.draft)
        save_budget_file(budget, budgets_dir=tmp_budgets)
        assert (tmp_budgets / "2027.yaml").exists()

    def test_save_archives_prior_version(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        budget.status = BudgetStatus.draft
        save_budget_file(budget, budgets_dir=tmp_budgets)
        history = tmp_budgets / "history"
        assert history.exists()
        assert (history / "2026_v1.yaml").exists()

    def test_save_increments_version(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        save_budget_file(budget, budgets_dir=tmp_budgets)
        save_budget_file(budget, budgets_dir=tmp_budgets)
        assert (tmp_budgets / "history" / "2026_v1.yaml").exists()
        assert (tmp_budgets / "history" / "2026_v2.yaml").exists()

    def test_save_creates_changelog(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        save_budget_file(budget, budgets_dir=tmp_budgets, user="test_user")
        cl_path = tmp_budgets / "2026.changelog.json"
        assert cl_path.exists()
        entries = json.loads(cl_path.read_text())
        assert len(entries) >= 1
        assert entries[-1]["user"] == "test_user"

    def test_save_preserves_null_roundtrip(self, tmp_budgets: Path):
        budget = BudgetFile(
            year=2027,
            status=BudgetStatus.draft,
            expenses={
                "ministry_expenses": BudgetSection(
                    **{"41000_ministry_expenses": None}
                ),
            },
        )
        save_budget_file(budget, budgets_dir=tmp_budgets)
        reloaded = load_budget_file(2027, budgets_dir=tmp_budgets)
        items = reloaded.expenses["ministry_expenses"].account_items()
        assert "41000_ministry_expenses" in items
        assert items["41000_ministry_expenses"] is None


# ---------------------------------------------------------------------------
# Optimistic concurrency tests
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_rejects_stale_mtime(self, budget_file: Path, tmp_budgets: Path):
        mtime = get_budget_mtime(2026, budgets_dir=tmp_budgets)
        # Simulate external modification
        time.sleep(0.05)
        budget_file.write_text(budget_file.read_text() + "\n# modified", encoding="utf-8")
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        with pytest.raises(BudgetConcurrencyError):
            save_budget_file(budget, budgets_dir=tmp_budgets, expected_mtime=mtime)

    def test_accepts_current_mtime(self, budget_file: Path, tmp_budgets: Path):
        mtime = get_budget_mtime(2026, budgets_dir=tmp_budgets)
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        # Should not raise
        save_budget_file(budget, budgets_dir=tmp_budgets, expected_mtime=mtime)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_budget_passes(self, budget_file: Path, tmp_budgets: Path, chart_path: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        result = validate_budget(budget, chart_path=chart_path)
        assert result == []

    def test_invalid_code_raises(self, tmp_budgets: Path, chart_path: Path):
        budget = BudgetFile(
            year=2027,
            status=BudgetStatus.draft,
            income={
                "fake": BudgetSection(**{"99999_nonexistent": 1000}),
            },
        )
        with pytest.raises(BudgetValidationError) as exc_info:
            validate_budget(budget, chart_path=chart_path)
        assert "99999" in exc_info.value.invalid_codes


# ---------------------------------------------------------------------------
# Status transition tests
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def test_draft_to_proposed(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        assert budget.status == BudgetStatus.draft
        result = transition_status(budget, BudgetStatus.proposed, budgets_dir=tmp_budgets)
        assert result.status == BudgetStatus.proposed

    def test_proposed_to_approved(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        budget.status = BudgetStatus.proposed
        save_budget_file(budget, budgets_dir=tmp_budgets)
        result = transition_status(budget, BudgetStatus.approved, budgets_dir=tmp_budgets)
        assert result.status == BudgetStatus.approved
        assert result.approved_date is not None

    def test_cannot_skip_proposed(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        with pytest.raises(BudgetStatusError):
            transition_status(budget, BudgetStatus.approved, budgets_dir=tmp_budgets)

    def test_cannot_reverse_approved(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        budget.status = BudgetStatus.approved
        save_budget_file(budget, budgets_dir=tmp_budgets)
        with pytest.raises(BudgetStatusError):
            transition_status(budget, BudgetStatus.draft, budgets_dir=tmp_budgets)

    def test_override_allows_any_transition(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        budget.status = BudgetStatus.approved
        save_budget_file(budget, budgets_dir=tmp_budgets)
        result = transition_status(
            budget, BudgetStatus.draft, override=True, budgets_dir=tmp_budgets
        )
        assert result.status == BudgetStatus.draft

    def test_proposed_can_revert_to_draft(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        budget.status = BudgetStatus.proposed
        save_budget_file(budget, budgets_dir=tmp_budgets)
        result = transition_status(budget, BudgetStatus.draft, budgets_dir=tmp_budgets)
        assert result.status == BudgetStatus.draft


# ---------------------------------------------------------------------------
# Changelog tests
# ---------------------------------------------------------------------------

class TestChangelog:
    def test_changelog_append_only(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        save_budget_file(budget, budgets_dir=tmp_budgets, summary="First save")
        save_budget_file(budget, budgets_dir=tmp_budgets, summary="Second save")
        entries = load_changelog(2026, budgets_dir=tmp_budgets)
        assert len(entries) >= 2
        assert entries[0].summary == "First save"

    def test_changelog_has_timestamps(self, budget_file: Path, tmp_budgets: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        save_budget_file(budget, budgets_dir=tmp_budgets)
        entries = load_changelog(2026, budgets_dir=tmp_budgets)
        assert entries[0].timestamp is not None


# ---------------------------------------------------------------------------
# Draft creation tests
# ---------------------------------------------------------------------------

class TestCreateDraft:
    def test_create_empty_draft(self, tmp_budgets: Path):
        budget = create_draft_budget(2028, budgets_dir=tmp_budgets)
        assert budget.year == 2028
        assert budget.status == BudgetStatus.draft

    def test_clone_from_base_year(self, budget_file: Path, tmp_budgets: Path):
        budget = create_draft_budget(2027, base_year=2026, budgets_dir=tmp_budgets)
        assert budget.year == 2027
        assert budget.status == BudgetStatus.draft
        # Should have same sections as base
        assert "offertory" in budget.income

    def test_clone_resets_status(self, budget_file: Path, tmp_budgets: Path):
        # Make 2026 approved first
        b = load_budget_file(2026, budgets_dir=tmp_budgets)
        b.status = BudgetStatus.approved
        save_budget_file(b, budgets_dir=tmp_budgets)

        draft = create_draft_budget(2027, base_year=2026, budgets_dir=tmp_budgets)
        assert draft.status == BudgetStatus.draft
        assert draft.approved_date is None

    def test_duplicate_year_raises(self, budget_file: Path, tmp_budgets: Path):
        with pytest.raises(BudgetServiceError):
            create_draft_budget(2026, budgets_dir=tmp_budgets)


# ---------------------------------------------------------------------------
# Property income computation tests
# ---------------------------------------------------------------------------

class TestPropertyIncome:
    def test_compute_from_config(self, properties_path: Path):
        result = compute_property_income(properties_path=properties_path)
        assert "goodhew_6" in result
        # 720 * 48 * (1 - 0.055) = 32,659.20
        assert result["goodhew_6"] == pytest.approx(32659.20, abs=0.01)

    def test_zero_rate_property(self, properties_path: Path):
        result = compute_property_income(properties_path=properties_path)
        assert result["hamilton_33"] == 0.0

    def test_budget_overrides_applied(self, budget_file: Path, tmp_budgets: Path, properties_path: Path):
        budget = load_budget_file(2026, budgets_dir=tmp_budgets)
        result = compute_property_income(budget, properties_path=properties_path)
        # loane_39 overridden to 600/week: 600 * 48 * (1 - 0.055) = 27,216.00
        assert result["loane_39"] == pytest.approx(27216.00, abs=0.01)


# ---------------------------------------------------------------------------
# Payroll computation tests
# ---------------------------------------------------------------------------

class TestPayrollBudget:
    def test_compute_payroll(self, payroll_path: Path):
        result = compute_payroll_budget(payroll_path=payroll_path)
        assert "_total" in result
        assert result["_total"] > 0
        assert "Walmsley D" in result

    def test_includes_recoveries(self, payroll_path: Path):
        result = compute_payroll_budget(payroll_path=payroll_path)
        # Stepniewski has -15000 recovery, should reduce total
        assert "Stepniewski M" in result


# ---------------------------------------------------------------------------
# Flat budget loader (backward compat) tests
# ---------------------------------------------------------------------------

class TestLoadBudgetFlat:
    def test_flat_load_returns_category_amounts(
        self, budget_file: Path, tmp_budgets: Path, chart_path: Path
    ):
        result = load_budget_flat(2026, budgets_dir=tmp_budgets, chart_path=chart_path)
        assert isinstance(result, dict)
        # offertory accounts should map to "offertory" category
        assert "offertory" in result
        assert result["offertory"] == 100000

    def test_flat_load_missing_year_returns_empty(self, tmp_budgets: Path, chart_path: Path):
        result = load_budget_flat(2098, budgets_dir=tmp_budgets, chart_path=chart_path)
        assert result == {}
