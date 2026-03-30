"""Property what-if scenario service.

Computes base vs scenario property income with composable overrides:
- Vacancy: weeks vacant (0-52) reduces rental weeks
- Rent change: override weekly rate
- Major repair: one-off cost deducted from net income

Each property can have multiple scenario adjustments applied simultaneously.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.services.budget import PROPERTIES_PATH


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScenarioInput:
    """What-if overrides for a single property."""
    vacancy_weeks: int = 0          # 0-52, weeks with no tenant
    weekly_rate: float | None = None  # override weekly rate (None = use base)
    major_repair: float = 0.0       # one-off cost to deduct


@dataclass
class PropertyResult:
    """Base and scenario results for a single property."""
    key: str
    address: str
    base_weekly_rate: float
    base_weeks: int
    management_fee_pct: float

    # Base calculation
    base_annual_gross: float
    base_annual_net: float

    # Scenario calculation
    scenario_weekly_rate: float
    scenario_weeks: int
    scenario_annual_gross: float
    scenario_annual_net: float  # after mgmt fee and major repair
    major_repair: float

    # Delta
    delta: float  # scenario_annual_net - base_annual_net


@dataclass
class ScenarioSummary:
    """Aggregate results across all properties."""
    properties: list[PropertyResult] = field(default_factory=list)
    base_total: float = 0.0
    scenario_total: float = 0.0
    delta_total: float = 0.0


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def load_properties(*, properties_path: Path | None = None) -> dict:
    """Load raw property config from YAML."""
    pp = properties_path or PROPERTIES_PATH
    if not pp.exists():
        return {}
    raw = yaml.safe_load(pp.read_text(encoding="utf-8"))
    return raw.get("properties", {})


def compute_property_base(prop: dict) -> tuple[float, float]:
    """Return (annual_gross, annual_net) for a property at base rates."""
    weekly = prop.get("weekly_rate", 0)
    weeks = prop.get("weeks_per_year", 48)
    fee_pct = prop.get("management_fee_pct", 0)
    gross = weekly * weeks
    net = gross * (1 - fee_pct)
    return round(gross, 2), round(net, 2)


def compute_scenario(
    scenarios: dict[str, ScenarioInput],
    *,
    properties_path: Path | None = None,
) -> ScenarioSummary:
    """Compute base vs scenario for all properties.

    Args:
        scenarios: {property_key: ScenarioInput} — only properties with
            overrides need entries; others keep base values.
        properties_path: override for testing.

    Returns:
        ScenarioSummary with per-property and aggregate results.
    """
    props = load_properties(properties_path=properties_path)
    summary = ScenarioSummary()

    for key, prop in props.items():
        weekly = prop.get("weekly_rate", 0)
        weeks = prop.get("weeks_per_year", 48)
        fee_pct = prop.get("management_fee_pct", 0)
        address = prop.get("address", key)

        base_gross, base_net = compute_property_base(prop)

        # Apply scenario
        sc = scenarios.get(key, ScenarioInput())
        sc_weekly = sc.weekly_rate if sc.weekly_rate is not None else weekly
        sc_weeks = max(0, weeks - sc.vacancy_weeks)
        sc_gross = round(sc_weekly * sc_weeks, 2)
        sc_net = round(sc_gross * (1 - fee_pct) - sc.major_repair, 2)

        delta = round(sc_net - base_net, 2)

        result = PropertyResult(
            key=key,
            address=address,
            base_weekly_rate=weekly,
            base_weeks=weeks,
            management_fee_pct=fee_pct,
            base_annual_gross=base_gross,
            base_annual_net=base_net,
            scenario_weekly_rate=sc_weekly,
            scenario_weeks=sc_weeks,
            scenario_annual_gross=sc_gross,
            scenario_annual_net=sc_net,
            major_repair=sc.major_repair,
            delta=delta,
        )
        summary.properties.append(result)
        summary.base_total += base_net
        summary.scenario_total += sc_net
        summary.delta_total += delta

    summary.base_total = round(summary.base_total, 2)
    summary.scenario_total = round(summary.scenario_total, 2)
    summary.delta_total = round(summary.delta_total, 2)

    return summary


def scenarios_from_form(form_data: dict) -> dict[str, ScenarioInput]:
    """Parse flat form data into ScenarioInput dict.

    Expected keys: {prop_key}_vacancy, {prop_key}_rate, {prop_key}_repair
    """
    # Collect unique property keys
    keys: set[str] = set()
    for k in form_data:
        for suffix in ("_vacancy", "_rate", "_repair"):
            if k.endswith(suffix):
                keys.add(k[: -len(suffix)])

    scenarios: dict[str, ScenarioInput] = {}
    for key in keys:
        vacancy = _safe_int(form_data.get(f"{key}_vacancy", "0"))
        rate_str = form_data.get(f"{key}_rate", "").strip()
        rate = _safe_float(rate_str) if rate_str else None
        repair = _safe_float(form_data.get(f"{key}_repair", "0"))
        # Only include if something is overridden
        if vacancy or rate is not None or repair:
            scenarios[key] = ScenarioInput(
                vacancy_weeks=vacancy,
                weekly_rate=rate,
                major_repair=repair,
            )
    return scenarios


def _safe_int(val: str) -> int:
    try:
        return max(0, min(52, int(val)))
    except (ValueError, TypeError):
        return 0


def _safe_float(val: str) -> float:
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
