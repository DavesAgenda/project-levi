"""Tests for property what-if scenario service and router."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.services.property_scenarios import (
    ScenarioInput,
    ScenarioSummary,
    compute_scenario,
    load_properties,
    scenarios_from_form,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_YAML = dedent("""\
    properties:
      house_a:
        address: "1 Test Street"
        weekly_rate: 500
        weeks_per_year: 48
        management_fee_pct: 0.05
        status: occupied
      house_b:
        address: "2 Test Avenue"
        weekly_rate: 700
        weeks_per_year: 48
        management_fee_pct: 0.10
        status: occupied
""")


@pytest.fixture
def props_path(tmp_path: Path) -> Path:
    p = tmp_path / "properties.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_properties
# ---------------------------------------------------------------------------

def test_load_properties(props_path: Path):
    props = load_properties(properties_path=props_path)
    assert "house_a" in props
    assert "house_b" in props
    assert props["house_a"]["weekly_rate"] == 500


def test_load_properties_missing():
    props = load_properties(properties_path=Path("/nonexistent/path.yaml"))
    assert props == {}


# ---------------------------------------------------------------------------
# compute_scenario — base only (no overrides)
# ---------------------------------------------------------------------------

def test_base_scenario_no_overrides(props_path: Path):
    summary = compute_scenario({}, properties_path=props_path)
    assert len(summary.properties) == 2

    # house_a: 500 * 48 * 0.95 = 22800
    a = summary.properties[0]
    assert a.key == "house_a"
    assert a.base_annual_net == 22800.0
    assert a.scenario_annual_net == 22800.0
    assert a.delta == 0.0

    # house_b: 700 * 48 * 0.90 = 30240
    b = summary.properties[1]
    assert b.base_annual_net == 30240.0
    assert b.delta == 0.0


def test_base_totals(props_path: Path):
    summary = compute_scenario({}, properties_path=props_path)
    assert summary.base_total == 22800.0 + 30240.0
    assert summary.delta_total == 0.0


# ---------------------------------------------------------------------------
# Vacancy scenario
# ---------------------------------------------------------------------------

def test_vacancy_reduces_income(props_path: Path):
    scenarios = {"house_a": ScenarioInput(vacancy_weeks=12)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    # 500 * (48-12) * 0.95 = 500 * 36 * 0.95 = 17100
    assert a.scenario_annual_net == 17100.0
    assert a.delta == 17100.0 - 22800.0  # -5700


def test_vacancy_full_year(props_path: Path):
    scenarios = {"house_a": ScenarioInput(vacancy_weeks=48)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    assert a.scenario_annual_net == 0.0
    assert a.delta == -22800.0


# ---------------------------------------------------------------------------
# Rent change scenario
# ---------------------------------------------------------------------------

def test_rent_increase(props_path: Path):
    scenarios = {"house_a": ScenarioInput(weekly_rate=600)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    # 600 * 48 * 0.95 = 27360
    assert a.scenario_annual_net == 27360.0
    assert a.delta == 27360.0 - 22800.0  # +4560


def test_rent_decrease(props_path: Path):
    scenarios = {"house_a": ScenarioInput(weekly_rate=400)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    # 400 * 48 * 0.95 = 18240
    assert a.scenario_annual_net == 18240.0


# ---------------------------------------------------------------------------
# Major repair scenario
# ---------------------------------------------------------------------------

def test_major_repair_deducted(props_path: Path):
    scenarios = {"house_b": ScenarioInput(major_repair=5000)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    b = summary.properties[1]
    # 700 * 48 * 0.90 - 5000 = 30240 - 5000 = 25240
    assert b.scenario_annual_net == 25240.0
    assert b.delta == -5000.0


# ---------------------------------------------------------------------------
# Composable scenarios
# ---------------------------------------------------------------------------

def test_vacancy_plus_rent_change(props_path: Path):
    scenarios = {"house_a": ScenarioInput(vacancy_weeks=4, weekly_rate=550)}
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    # 550 * (48-4) * 0.95 = 550 * 44 * 0.95 = 22990  (actually 23980? let me calc)
    # 550 * 44 = 24200, * 0.95 = 22990
    assert a.scenario_annual_net == 22990.0


def test_all_three_combined(props_path: Path):
    scenarios = {
        "house_a": ScenarioInput(vacancy_weeks=4, weekly_rate=550, major_repair=2000),
    }
    summary = compute_scenario(scenarios, properties_path=props_path)
    a = summary.properties[0]
    # 550 * 44 * 0.95 - 2000 = 22990 - 2000 = 20990
    assert a.scenario_annual_net == 20990.0
    assert a.delta == 20990.0 - 22800.0  # -1810


def test_multiple_properties_scenario(props_path: Path):
    scenarios = {
        "house_a": ScenarioInput(vacancy_weeks=4),
        "house_b": ScenarioInput(major_repair=3000),
    }
    summary = compute_scenario(scenarios, properties_path=props_path)
    # house_a: 500 * 44 * 0.95 = 20900
    # house_b: 700 * 48 * 0.90 - 3000 = 27240
    assert summary.properties[0].scenario_annual_net == 20900.0
    assert summary.properties[1].scenario_annual_net == 27240.0
    assert summary.delta_total == (20900 - 22800) + (27240 - 30240)  # -4900


# ---------------------------------------------------------------------------
# scenarios_from_form
# ---------------------------------------------------------------------------

def test_form_parsing_basic():
    form = {
        "house_a_vacancy": "4",
        "house_a_rate": "550",
        "house_a_repair": "2000",
    }
    result = scenarios_from_form(form)
    assert "house_a" in result
    sc = result["house_a"]
    assert sc.vacancy_weeks == 4
    assert sc.weekly_rate == 550.0
    assert sc.major_repair == 2000.0


def test_form_parsing_empty_rate():
    form = {"house_a_vacancy": "0", "house_a_rate": "", "house_a_repair": "0"}
    result = scenarios_from_form(form)
    # No overrides — all defaults
    assert result == {}


def test_form_parsing_only_vacancy():
    form = {"house_a_vacancy": "10", "house_a_rate": "", "house_a_repair": ""}
    result = scenarios_from_form(form)
    assert "house_a" in result
    assert result["house_a"].vacancy_weeks == 10
    assert result["house_a"].weekly_rate is None


def test_form_parsing_clamps_vacancy():
    form = {"house_a_vacancy": "99", "house_a_rate": "", "house_a_repair": ""}
    result = scenarios_from_form(form)
    assert result["house_a"].vacancy_weeks == 52  # clamped


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_zero_weekly_rate_property(tmp_path: Path):
    """Property with zero weekly rate (e.g. warden's residence)."""
    yaml_text = dedent("""\
        properties:
          rectory:
            address: "The Rectory"
            weekly_rate: 0
            weeks_per_year: 48
            management_fee_pct: 0
            status: occupied_warden
    """)
    p = tmp_path / "properties.yaml"
    p.write_text(yaml_text, encoding="utf-8")

    summary = compute_scenario({}, properties_path=p)
    assert summary.properties[0].base_annual_net == 0.0

    # Even with a major repair, net goes negative
    scenarios = {"rectory": ScenarioInput(major_repair=5000)}
    summary = compute_scenario(scenarios, properties_path=p)
    assert summary.properties[0].scenario_annual_net == -5000.0


def test_empty_properties_file(tmp_path: Path):
    p = tmp_path / "properties.yaml"
    p.write_text("properties: {}\n", encoding="utf-8")
    summary = compute_scenario({}, properties_path=p)
    assert summary.properties == []
    assert summary.base_total == 0.0
