"""Payroll scenario modelling service — what-if analysis for staff changes.

Supports:
- Diocese scale editing (source, year, uplift_factor)
- Per-staff scenario controls (add/remove, FTE change, uplift, step change)
- Total payroll impact preview with delta from current baseline
- PCR auto-calculation for clergy positions based on diocese rates
- Saves modified config back to payroll.yaml
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.services.payroll import (
    CONFIG_DIR,
    DioceseScales,
    PAYROLL_CONFIG_PATH,
    StaffCost,
    load_payroll_config,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLERGY_ROLES = {"Rector", "Assistant Minister", "Curate", "Lay Minister"}

# Default PCR rates (diocese standard) — keyed by grade
DEFAULT_PCR_RATES: dict[str, float] = {
    "Accredited": 20000,
    "3rd Yr Asst": 15000,
    "2nd Yr Asst": 14500,
    "1st Yr Asst": 13500,
    "Curate": 12000,
}

DEFAULT_TRAVEL: float = 9000.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StaffScenarioEntry:
    """A single staff member in a scenario (mirrors payroll.yaml staff entry)."""

    name: str
    role: str
    fte: float = 1.0
    base_salary: float = 0.0
    super_rate: float = 0.0
    pcr: float = 0.0
    fixed_travel: float = 0.0
    workers_comp: float = 0.0
    grade: str | None = None
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    is_new: bool = False
    is_removed: bool = False

    def to_yaml_dict(self) -> dict[str, Any]:
        """Convert to dict for YAML serialization."""
        d: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "fte": self.fte,
            "base_salary": self.base_salary,
        }
        if self.super_rate > 0:
            d["super_rate"] = self.super_rate
        if self.pcr > 0:
            d["pcr"] = self.pcr
        if self.fixed_travel > 0:
            d["fixed_travel"] = self.fixed_travel
        if self.workers_comp > 0:
            d["workers_comp"] = self.workers_comp
        if self.grade:
            d["grade"] = self.grade
        d["recoveries"] = self.recoveries
        return d


@dataclass
class ScenarioResult:
    """Result of a scenario computation with deltas from baseline."""

    baseline_staff: list[StaffCost] = field(default_factory=list)
    scenario_staff: list[StaffCost] = field(default_factory=list)
    baseline_total: float = 0.0
    scenario_total: float = 0.0
    delta: float = 0.0
    delta_pct: float | None = None
    baseline_net: float = 0.0
    scenario_net: float = 0.0
    delta_net: float = 0.0
    staff_changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PayrollScenario:
    """Full scenario state: diocese scales + modified staff list."""

    diocese_scales: DioceseScales = field(default_factory=DioceseScales)
    staff: list[StaffScenarioEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_scenario_from_config(
    config_path: Path | None = None,
) -> PayrollScenario:
    """Load current payroll.yaml as a mutable scenario."""
    path = config_path or PAYROLL_CONFIG_PATH
    if not path.exists():
        return PayrollScenario()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    ds_raw = raw.get("diocese_scales", {})
    diocese = DioceseScales(
        source=ds_raw.get("source", ""),
        year=ds_raw.get("year", 0),
        uplift_factor=ds_raw.get("uplift_factor", 0.0),
        notes=ds_raw.get("notes", ""),
    )

    staff: list[StaffScenarioEntry] = []
    for entry in raw.get("staff", []):
        staff.append(StaffScenarioEntry(
            name=entry.get("name", "Unknown"),
            role=entry.get("role", ""),
            fte=float(entry.get("fte", 1.0)),
            base_salary=float(entry.get("base_salary", 0)),
            super_rate=float(entry.get("super_rate", 0)),
            pcr=float(entry.get("pcr", 0)),
            fixed_travel=float(entry.get("fixed_travel", 0)),
            workers_comp=float(entry.get("workers_comp", 0)),
            grade=entry.get("grade"),
            recoveries=entry.get("recoveries", []),
        ))

    return PayrollScenario(diocese_scales=diocese, staff=staff)


# ---------------------------------------------------------------------------
# Diocese scale editing
# ---------------------------------------------------------------------------

def update_diocese_scales(
    scenario: PayrollScenario,
    *,
    source: str | None = None,
    year: int | None = None,
    uplift_factor: float | None = None,
    notes: str | None = None,
) -> PayrollScenario:
    """Update diocese scale metadata on a scenario."""
    if source is not None:
        scenario.diocese_scales.source = source
    if year is not None:
        scenario.diocese_scales.year = year
    if uplift_factor is not None:
        scenario.diocese_scales.uplift_factor = uplift_factor
    if notes is not None:
        scenario.diocese_scales.notes = notes
    return scenario


# ---------------------------------------------------------------------------
# Staff scenario operations
# ---------------------------------------------------------------------------

def add_staff(
    scenario: PayrollScenario,
    *,
    name: str,
    role: str,
    fte: float = 1.0,
    base_salary: float = 0.0,
    super_rate: float = 0.115,
    grade: str | None = None,
) -> PayrollScenario:
    """Add a new staff position to the scenario."""
    is_clergy = role in CLERGY_ROLES
    pcr = DEFAULT_PCR_RATES.get(grade or "", 0.0) if is_clergy else 0.0
    travel = DEFAULT_TRAVEL if is_clergy else 0.0

    entry = StaffScenarioEntry(
        name=name,
        role=role,
        fte=fte,
        base_salary=base_salary,
        super_rate=0.0 if is_clergy else super_rate,
        pcr=pcr,
        fixed_travel=travel,
        grade=grade,
        is_new=True,
    )
    scenario.staff.append(entry)
    return scenario


def remove_staff(scenario: PayrollScenario, name: str) -> PayrollScenario:
    """Mark a staff member as removed (excluded from scenario total)."""
    for s in scenario.staff:
        if s.name == name:
            s.is_removed = True
            return scenario
    raise ValueError(f"Staff member '{name}' not found")


def restore_staff(scenario: PayrollScenario, name: str) -> PayrollScenario:
    """Restore a previously removed staff member."""
    for s in scenario.staff:
        if s.name == name:
            s.is_removed = False
            return scenario
    raise ValueError(f"Staff member '{name}' not found")


def change_fte(scenario: PayrollScenario, name: str, new_fte: float) -> PayrollScenario:
    """Change the FTE for a staff member."""
    if new_fte < 0 or new_fte > 1.0:
        raise ValueError(f"FTE must be between 0 and 1.0, got {new_fte}")
    for s in scenario.staff:
        if s.name == name:
            s.fte = new_fte
            return scenario
    raise ValueError(f"Staff member '{name}' not found")


def apply_uplift(
    scenario: PayrollScenario,
    name: str | None = None,
    uplift_factor: float | None = None,
) -> PayrollScenario:
    """Apply diocese uplift to base salary.

    If name is None, applies to all staff. Uses scenario's diocese uplift
    if uplift_factor is not provided.
    """
    factor = uplift_factor if uplift_factor is not None else scenario.diocese_scales.uplift_factor
    if factor == 0:
        return scenario

    for s in scenario.staff:
        if name is not None and s.name != name:
            continue
        s.base_salary = round(s.base_salary * (1 + factor), 2)

    return scenario


def apply_step_change(
    scenario: PayrollScenario,
    name: str,
    new_grade: str,
) -> PayrollScenario:
    """Apply a salary scale step change (updates grade and PCR for clergy)."""
    for s in scenario.staff:
        if s.name == name:
            s.grade = new_grade
            if s.role in CLERGY_ROLES:
                s.pcr = DEFAULT_PCR_RATES.get(new_grade, s.pcr)
            return scenario
    raise ValueError(f"Staff member '{name}' not found")


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _compute_staff_cost(entry: StaffScenarioEntry) -> StaffCost:
    """Compute cost breakdown for a scenario staff entry."""
    super_amount = round(entry.base_salary * entry.super_rate, 2)
    allowances = round(entry.fixed_travel + entry.workers_comp, 2)
    recoveries = round(sum(r.get("amount", 0) for r in entry.recoveries), 2)
    total_cost = round(entry.base_salary + super_amount + entry.pcr + allowances, 2)

    return StaffCost(
        name=entry.name,
        role=entry.role,
        fte=entry.fte,
        base_salary=entry.base_salary,
        super_amount=super_amount,
        pcr=round(entry.pcr, 2),
        fixed_travel=round(entry.fixed_travel, 2),
        workers_comp=round(entry.workers_comp, 2),
        allowances=allowances,
        recoveries=recoveries,
        total_cost=total_cost,
        diocese_grade=entry.grade,
    )


def compute_scenario(
    scenario: PayrollScenario,
    config_path: Path | None = None,
) -> ScenarioResult:
    """Compute scenario results with deltas from the baseline (current config).

    Baseline is loaded fresh from payroll.yaml.
    Scenario uses the modified staff list (excluding removed entries).
    """
    baseline_staff, _ = load_payroll_config(config_path)
    baseline_total = sum(s.total_cost for s in baseline_staff)
    baseline_recoveries = sum(s.recoveries for s in baseline_staff)
    baseline_net = baseline_total + baseline_recoveries

    # Scenario: compute costs for active staff only
    active = [s for s in scenario.staff if not s.is_removed]
    scenario_costs = [_compute_staff_cost(s) for s in active]
    scenario_total = sum(s.total_cost for s in scenario_costs)
    scenario_recoveries = sum(s.recoveries for s in scenario_costs)
    scenario_net = scenario_total + scenario_recoveries

    delta = round(scenario_total - baseline_total, 2)
    delta_pct = round(delta / baseline_total * 100, 1) if baseline_total else None
    delta_net = round(scenario_net - baseline_net, 2)

    # Build change summary
    changes: list[dict[str, Any]] = []
    baseline_names = {s.name for s in baseline_staff}
    for s in scenario.staff:
        if s.is_new:
            changes.append({"name": s.name, "type": "added", "impact": _compute_staff_cost(s).total_cost})
        elif s.is_removed:
            orig = next((b for b in baseline_staff if b.name == s.name), None)
            if orig:
                changes.append({"name": s.name, "type": "removed", "impact": -orig.total_cost})
        elif s.name in baseline_names:
            orig = next(b for b in baseline_staff if b.name == s.name)
            new_cost = _compute_staff_cost(s)
            if abs(new_cost.total_cost - orig.total_cost) > 0.01:
                changes.append({
                    "name": s.name,
                    "type": "modified",
                    "impact": round(new_cost.total_cost - orig.total_cost, 2),
                })

    return ScenarioResult(
        baseline_staff=baseline_staff,
        scenario_staff=scenario_costs,
        baseline_total=round(baseline_total, 2),
        scenario_total=round(scenario_total, 2),
        delta=delta,
        delta_pct=delta_pct,
        baseline_net=round(baseline_net, 2),
        scenario_net=round(scenario_net, 2),
        delta_net=delta_net,
        staff_changes=changes,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_scenario_to_config(
    scenario: PayrollScenario,
    config_path: Path | None = None,
) -> Path:
    """Save the scenario (active staff only) back to payroll.yaml.

    Removed staff are excluded. Returns the path written.
    """
    path = config_path or PAYROLL_CONFIG_PATH
    active = [s for s in scenario.staff if not s.is_removed]

    data: dict[str, Any] = {
        "diocese_scales": {
            "source": scenario.diocese_scales.source,
            "year": scenario.diocese_scales.year,
            "uplift_factor": scenario.diocese_scales.uplift_factor,
            "notes": scenario.diocese_scales.notes,
        },
        "staff": [s.to_yaml_dict() for s in active],
    }

    yaml_text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    path.write_text(yaml_text, encoding="utf-8")
    return path
