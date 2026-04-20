"""Tests for the payroll scenario modelling service and router."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from app.services.payroll_scenarios import (
    CLERGY_ROLES,
    DEFAULT_PCR_RATES,
    DEFAULT_TRAVEL,
    PayrollScenario,
    StaffScenarioEntry,
    add_staff,
    apply_step_change,
    apply_uplift,
    change_fte,
    compute_scenario,
    load_scenario_from_config,
    remove_staff,
    restore_staff,
    save_scenario_to_config,
    update_diocese_scales,
)
from app.services.payroll import DioceseScales


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = textwrap.dedent("""\
    diocese_scales:
      source: "Test Diocese"
      year: 2026
      uplift_factor: 0.012
      notes: "Test notes"

    staff:
      - name: "Staff A"
        role: "Permanent"
        fte: 0.8
        base_salary: 70000
        super_rate: 0.115
        workers_comp: 1200
        recoveries: []

      - name: "Staff B"
        role: "Rector"
        grade: "Accredited"
        fte: 1.0
        base_salary: 80000
        pcr: 20000
        fixed_travel: 9000
        recoveries:
          - name: "RCEA"
            amount: -10000
""")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Write a sample payroll config and return its path."""
    p = tmp_path / "payroll.yaml"
    p.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return p


@pytest.fixture
def scenario(config_path: Path) -> PayrollScenario:
    """Load a scenario from the sample config."""
    return load_scenario_from_config(config_path)


# ---------------------------------------------------------------------------
# Loading tests
# ---------------------------------------------------------------------------

class TestLoadScenario:
    def test_loads_diocese_scales(self, scenario: PayrollScenario):
        assert scenario.diocese_scales.source == "Test Diocese"
        assert scenario.diocese_scales.year == 2026
        assert scenario.diocese_scales.uplift_factor == 0.012

    def test_loads_staff(self, scenario: PayrollScenario):
        assert len(scenario.staff) == 2
        assert scenario.staff[0].name == "Staff A"
        assert scenario.staff[1].name == "Staff B"

    def test_loads_staff_details(self, scenario: PayrollScenario):
        a = scenario.staff[0]
        assert a.role == "Permanent"
        assert a.fte == 0.8
        assert a.base_salary == 70000
        assert a.super_rate == 0.115

    def test_loads_clergy_details(self, scenario: PayrollScenario):
        b = scenario.staff[1]
        assert b.role == "Rector"
        assert b.grade == "Accredited"
        assert b.pcr == 20000
        assert b.fixed_travel == 9000

    def test_missing_config_returns_empty(self, tmp_path: Path):
        s = load_scenario_from_config(tmp_path / "nonexistent.yaml")
        assert len(s.staff) == 0


# ---------------------------------------------------------------------------
# Diocese scale editing
# ---------------------------------------------------------------------------

class TestDioceseScales:
    def test_update_source(self, scenario: PayrollScenario):
        update_diocese_scales(scenario, source="New Source")
        assert scenario.diocese_scales.source == "New Source"

    def test_update_year(self, scenario: PayrollScenario):
        update_diocese_scales(scenario, year=2027)
        assert scenario.diocese_scales.year == 2027

    def test_update_uplift(self, scenario: PayrollScenario):
        update_diocese_scales(scenario, uplift_factor=0.025)
        assert scenario.diocese_scales.uplift_factor == 0.025

    def test_partial_update(self, scenario: PayrollScenario):
        update_diocese_scales(scenario, year=2027)
        assert scenario.diocese_scales.source == "Test Diocese"  # unchanged


# ---------------------------------------------------------------------------
# Staff operations
# ---------------------------------------------------------------------------

class TestStaffOperations:
    def test_add_permanent_staff(self, scenario: PayrollScenario):
        add_staff(scenario, name="New Person", role="Permanent", base_salary=60000)
        assert len(scenario.staff) == 3
        new = scenario.staff[-1]
        assert new.name == "New Person"
        assert new.is_new is True
        assert new.pcr == 0
        assert new.super_rate == 0.115

    def test_add_clergy_gets_pcr(self, scenario: PayrollScenario):
        add_staff(scenario, name="New Rector", role="Rector", grade="Accredited", base_salary=80000)
        new = scenario.staff[-1]
        assert new.pcr == DEFAULT_PCR_RATES["Accredited"]
        assert new.fixed_travel == DEFAULT_TRAVEL
        assert new.super_rate == 0.0  # clergy don't get super

    def test_remove_staff(self, scenario: PayrollScenario):
        remove_staff(scenario, "Staff A")
        assert scenario.staff[0].is_removed is True

    def test_remove_nonexistent_raises(self, scenario: PayrollScenario):
        with pytest.raises(ValueError, match="not found"):
            remove_staff(scenario, "Nobody")

    def test_restore_staff(self, scenario: PayrollScenario):
        remove_staff(scenario, "Staff A")
        restore_staff(scenario, "Staff A")
        assert scenario.staff[0].is_removed is False

    def test_change_fte(self, scenario: PayrollScenario):
        change_fte(scenario, "Staff A", 0.5)
        assert scenario.staff[0].fte == 0.5

    def test_change_fte_invalid_raises(self, scenario: PayrollScenario):
        with pytest.raises(ValueError, match="between 0 and 1.0"):
            change_fte(scenario, "Staff A", 1.5)

    def test_step_change_updates_grade_and_pcr(self, scenario: PayrollScenario):
        apply_step_change(scenario, "Staff B", "3rd Yr Asst")
        assert scenario.staff[1].grade == "3rd Yr Asst"
        assert scenario.staff[1].pcr == DEFAULT_PCR_RATES["3rd Yr Asst"]


# ---------------------------------------------------------------------------
# Uplift
# ---------------------------------------------------------------------------

class TestUplift:
    def test_uplift_single(self, scenario: PayrollScenario):
        original = scenario.staff[0].base_salary
        apply_uplift(scenario, name="Staff A")
        expected = round(original * (1 + 0.012), 2)
        assert scenario.staff[0].base_salary == expected

    def test_uplift_all(self, scenario: PayrollScenario):
        originals = [s.base_salary for s in scenario.staff]
        apply_uplift(scenario)
        for i, s in enumerate(scenario.staff):
            assert s.base_salary == round(originals[i] * 1.012, 2)

    def test_uplift_custom_factor(self, scenario: PayrollScenario):
        original = scenario.staff[0].base_salary
        apply_uplift(scenario, name="Staff A", uplift_factor=0.05)
        assert scenario.staff[0].base_salary == round(original * 1.05, 2)

    def test_uplift_zero_factor_no_change(self, scenario: PayrollScenario):
        update_diocese_scales(scenario, uplift_factor=0.0)
        original = scenario.staff[0].base_salary
        apply_uplift(scenario, name="Staff A")
        assert scenario.staff[0].base_salary == original


# ---------------------------------------------------------------------------
# Scenario computation
# ---------------------------------------------------------------------------

class TestComputeScenario:
    def test_baseline_matches_config(self, scenario: PayrollScenario, config_path: Path):
        result = compute_scenario(scenario, config_path=config_path)
        assert result.baseline_total > 0
        assert result.delta == 0.0  # no changes yet

    def test_add_staff_increases_total(self, scenario: PayrollScenario, config_path: Path):
        add_staff(scenario, name="Extra", role="Permanent", base_salary=50000)
        result = compute_scenario(scenario, config_path=config_path)
        assert result.delta > 0
        assert result.scenario_total > result.baseline_total

    def test_remove_staff_decreases_total(self, scenario: PayrollScenario, config_path: Path):
        remove_staff(scenario, "Staff A")
        result = compute_scenario(scenario, config_path=config_path)
        assert result.delta < 0

    def test_changes_tracked(self, scenario: PayrollScenario, config_path: Path):
        add_staff(scenario, name="New", role="Permanent", base_salary=40000)
        result = compute_scenario(scenario, config_path=config_path)
        assert len(result.staff_changes) == 1
        assert result.staff_changes[0]["type"] == "added"

    def test_uplift_tracked_as_modified(self, scenario: PayrollScenario, config_path: Path):
        apply_uplift(scenario, name="Staff A")
        result = compute_scenario(scenario, config_path=config_path)
        modified = [c for c in result.staff_changes if c["type"] == "modified"]
        assert len(modified) == 1
        assert modified[0]["name"] == "Staff A"

    def test_net_includes_recoveries(self, scenario: PayrollScenario, config_path: Path):
        result = compute_scenario(scenario, config_path=config_path)
        # Staff B has -10000 recovery
        assert result.baseline_net < result.baseline_total


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestSaveScenario:
    def test_save_roundtrips(self, scenario: PayrollScenario, tmp_path: Path):
        out = tmp_path / "saved.yaml"
        save_scenario_to_config(scenario, config_path=out)
        reloaded = load_scenario_from_config(out)
        assert len(reloaded.staff) == 2
        assert reloaded.diocese_scales.year == 2026

    def test_save_excludes_removed(self, scenario: PayrollScenario, tmp_path: Path):
        remove_staff(scenario, "Staff A")
        out = tmp_path / "saved.yaml"
        save_scenario_to_config(scenario, config_path=out)
        reloaded = load_scenario_from_config(out)
        assert len(reloaded.staff) == 1
        assert reloaded.staff[0].name == "Staff B"

    def test_save_includes_new(self, scenario: PayrollScenario, tmp_path: Path):
        add_staff(scenario, name="New", role="Permanent", base_salary=50000)
        out = tmp_path / "saved.yaml"
        save_scenario_to_config(scenario, config_path=out)
        reloaded = load_scenario_from_config(out)
        assert len(reloaded.staff) == 3

    def test_save_preserves_diocese_changes(self, scenario: PayrollScenario, tmp_path: Path):
        update_diocese_scales(scenario, year=2027, uplift_factor=0.03)
        out = tmp_path / "saved.yaml"
        save_scenario_to_config(scenario, config_path=out)
        reloaded = load_scenario_from_config(out)
        assert reloaded.diocese_scales.year == 2027
        assert reloaded.diocese_scales.uplift_factor == 0.03


# ---------------------------------------------------------------------------
# Router tests (FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestRouter:
    @pytest.fixture(autouse=True)
    def setup_client(self, config_path: Path, monkeypatch):
        """Patch config path and create a test client."""
        from fastapi.testclient import TestClient
        import app.services.payroll_scenarios as svc_mod
        import app.routers.payroll_scenarios as router_mod

        monkeypatch.setattr(svc_mod, "PAYROLL_CONFIG_PATH", config_path)
        # Reset active scenario
        router_mod._active_scenario = None

        from app.main import app
        self.client = TestClient(app)

    def test_get_scenarios_page(self):
        resp = self.client.get("/budget/payroll-scenarios")
        assert resp.status_code == 200
        assert "Payroll What-If Scenarios" in resp.text

    def test_preview_json(self):
        resp = self.client.get("/budget/payroll-scenarios/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "baseline_total" in data
        assert "delta" in data

    def test_add_staff_via_form(self):
        resp = self.client.post("/budget/payroll-scenarios/staff/add", data={
            "name": "Test Person",
            "role": "Permanent",
            "fte": "1.0",
            "base_salary": "55000",
            "super_rate": "0.115",
            "grade": "",
        })
        assert resp.status_code == 200
        assert "Test Person" in resp.text

    def test_reset_clears_changes(self):
        # Add someone, then reset
        self.client.post("/budget/payroll-scenarios/staff/add", data={
            "name": "Temp",
            "role": "Casual",
            "fte": "0.5",
            "base_salary": "30000",
            "super_rate": "0.115",
            "grade": "",
        })
        resp = self.client.post("/budget/payroll-scenarios/reset")
        assert resp.status_code == 200
        assert "Temp" not in resp.text
