"""Payroll data service — loads payroll config, computes per-staff costs,
and extracts payroll actuals from Xero snapshots.

Provides data ready for both Jinja2 templates and JSON API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.models import FinancialSnapshot
from app.services.dashboard import find_latest_snapshot, load_ytd_snapshot

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PAYROLL_CONFIG_PATH = CONFIG_DIR / "payroll.yaml"

# ---------------------------------------------------------------------------
# Payroll category account code prefixes (40xxx accounts)
# ---------------------------------------------------------------------------

PAYROLL_CATEGORIES: dict[str, list[str]] = {
    "ministry_staff": ["40100", "40105", "40110", "40180", "40185"],
    "ministry_support": ["40200", "40205", "40210", "40220", "40280"],
    "admin_staff": ["40300", "40305", "40310", "40315", "40320", "40325", "40340", "40380"],
}

PAYROLL_CATEGORY_LABELS: dict[str, str] = {
    "ministry_staff": "Ministry Staff",
    "ministry_support": "Ministry Support Staff",
    "admin_staff": "Administration Staff",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StaffCost:
    """Computed cost breakdown for a single staff member."""

    name: str
    role: str
    fte: float
    base_salary: float
    super_amount: float
    pcr: float  # Parish Cost Recovery (clergy only)
    fixed_travel: float
    workers_comp: float
    allowances: float  # fixed_travel + workers_comp (excludes PCR)
    recoveries: float  # negative amounts (e.g. ExampleRecovery)
    total_cost: float
    diocese_grade: str | None = None

    @property
    def net_cost(self) -> float:
        """Total cost after recoveries."""
        return self.total_cost + self.recoveries  # recoveries are negative


@dataclass
class PayrollCategoryActuals:
    """Budget vs actual comparison for a payroll category."""

    category_key: str
    label: str
    actual: float
    budget: float
    variance_dollar: float
    variance_pct: float | None

    @property
    def status(self) -> str:
        """Return 'success', 'warning', or 'danger' for colour coding."""
        if self.budget == 0:
            return "success"
        pct = abs(self.variance_pct or 0)
        if self.actual > self.budget:
            return "danger"
        if pct <= 10:
            return "warning"
        return "success"


@dataclass
class DioceseScales:
    """Diocese stipend and salary scale reference data."""

    source: str = ""
    year: int = 0
    uplift_factor: float = 0.0
    notes: str = ""


@dataclass
class PayrollData:
    """Complete payroll context for template rendering."""

    staff: list[StaffCost] = field(default_factory=list)
    category_actuals: list[PayrollCategoryActuals] = field(default_factory=list)
    diocese_scales: DioceseScales = field(default_factory=DioceseScales)
    total_payroll_cost: float = 0.0
    total_payroll_budget: float = 0.0
    total_recoveries: float = 0.0
    net_payroll_cost: float = 0.0
    total_income: float = 0.0
    payroll_pct_of_income: float | None = None
    has_data: bool = False
    snapshot_date: str = ""
    snapshot_period: str = ""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_payroll_config(
    config_path: Path | None = None,
) -> tuple[list[StaffCost], DioceseScales]:
    """Load payroll.yaml and compute per-staff cost breakdowns.

    Returns a tuple of (staff_costs, diocese_scales).
    """
    path = config_path or PAYROLL_CONFIG_PATH
    if not path.exists():
        return [], DioceseScales()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Diocese scales
    ds_raw = raw.get("diocese_scales", {})
    diocese_scales = DioceseScales(
        source=ds_raw.get("source", ""),
        year=ds_raw.get("year", 0),
        uplift_factor=ds_raw.get("uplift_factor", 0.0),
        notes=ds_raw.get("notes", ""),
    )

    # Staff
    staff_list: list[StaffCost] = []
    for entry in raw.get("staff", []):
        name = entry.get("name", "Unknown")
        role = entry.get("role", "")
        fte = float(entry.get("fte", 1.0))
        base_salary = float(entry.get("base_salary", 0))

        # Super: either explicit super_rate on base_salary, or 0 for clergy (PCR instead)
        super_rate = float(entry.get("super_rate", 0))
        super_amount = round(base_salary * super_rate, 2)

        # PCR, fixed travel, workers comp — PCR shown separately
        pcr = float(entry.get("pcr", 0))
        fixed_travel = float(entry.get("fixed_travel", 0))
        workers_comp = float(entry.get("workers_comp", 0))
        allowances = round(fixed_travel + workers_comp, 2)  # excludes PCR

        # Recoveries (negative amounts)
        recoveries_total = 0.0
        for rec in entry.get("recoveries", []):
            recoveries_total += float(rec.get("amount", 0))
        recoveries_total = round(recoveries_total, 2)

        total_cost = round(base_salary + super_amount + pcr + allowances, 2)

        staff_list.append(StaffCost(
            name=name,
            role=role,
            fte=fte,
            base_salary=base_salary,
            super_amount=super_amount,
            pcr=round(pcr, 2),
            fixed_travel=round(fixed_travel, 2),
            workers_comp=round(workers_comp, 2),
            allowances=allowances,
            recoveries=recoveries_total,
            total_cost=total_cost,
            diocese_grade=entry.get("grade"),
        ))

    return staff_list, diocese_scales


# ---------------------------------------------------------------------------
# Actuals extraction from snapshots
# ---------------------------------------------------------------------------

def _account_in_category(code: str, category_codes: list[str]) -> bool:
    """Check if an account code belongs to a payroll category."""
    return code in category_codes


def extract_payroll_actuals(
    snapshot: FinancialSnapshot,
) -> dict[str, float]:
    """Extract payroll actuals from snapshot rows for 40xxx accounts.

    Returns dict mapping category_key -> total actual amount.
    """
    actuals: dict[str, float] = {}

    for row in snapshot.rows:
        for cat_key, codes in PAYROLL_CATEGORIES.items():
            if _account_in_category(row.account_code, codes):
                actuals[cat_key] = actuals.get(cat_key, 0) + row.amount
                break

    return {k: round(v, 2) for k, v in actuals.items()}


def extract_total_income(snapshot: FinancialSnapshot) -> float:
    """Sum all income rows (accounts starting with 1x or 2x or 3x)."""
    total = 0.0
    for row in snapshot.rows:
        if row.account_code and row.account_code[0] in ("1", "2", "3"):
            total += row.amount
    return round(total, 2)


# ---------------------------------------------------------------------------
# Budget loading for payroll categories
# ---------------------------------------------------------------------------

_ROLE_TO_CATEGORY: dict[str, str] = {
    "rector": "ministry_staff",
    "senior minister": "ministry_staff",
    "assistant minister": "ministry_staff",
    "lay minister": "ministry_support",
    "youth minister": "ministry_support",
    "children's minister": "ministry_support",
    "permanent": "admin_staff",
    "casual": "admin_staff",
    "part-time": "admin_staff",
}


def _staff_budget_from_config(
    config_path: Path | None = None,
) -> dict[str, float]:
    """Compute annual payroll budget per category from payroll.yaml staff list.

    Maps staff members to payroll categories by role and sums their total cost
    (including recoveries) to produce budget figures.
    """
    staff, _ = load_payroll_config(config_path)
    category_budgets: dict[str, float] = {}
    for s in staff:
        cat_key = _ROLE_TO_CATEGORY.get(s.role.lower(), "admin_staff")
        category_budgets[cat_key] = category_budgets.get(cat_key, 0) + s.net_cost
    return {k: round(v, 2) for k, v in category_budgets.items()}


def load_payroll_budget(year: int = 2026) -> dict[str, float]:
    """Load budget amounts for payroll categories.

    First checks budgets/{year}.yaml for explicit line items.
    Falls back to computing from payroll.yaml staff config.

    Returns dict mapping category_key -> budgeted amount.
    """
    budgets_dir = PROJECT_ROOT / "budgets"
    budget_path = budgets_dir / f"{year}.yaml"

    # Try explicit budget YAML first
    if budget_path.exists():
        with open(budget_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        expenses = raw.get("expenses", {})
        if isinstance(expenses, dict):
            category_budgets: dict[str, float] = {}
            for cat_key in PAYROLL_CATEGORIES:
                group = expenses.get(cat_key, {})
                if not isinstance(group, dict):
                    continue
                total = 0.0
                for item_key, amount in group.items():
                    if item_key in ("notes", "overrides", "vacancy_weeks"):
                        continue
                    if amount is not None:
                        total += float(amount)
                if total > 0:
                    category_budgets[cat_key] = round(total, 2)

            if category_budgets:
                return category_budgets

    # Fall back to computing from payroll config
    return _staff_budget_from_config()


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_payroll_data(
    snapshot: FinancialSnapshot | None = None,
    config_path: Path | None = None,
    snapshots_dir: Path | None = None,
    budget: dict[str, float] | None = None,
) -> PayrollData:
    """Build complete payroll data from config, snapshots, and budget.

    If snapshot is None, attempts to load the latest from disk.
    If budget is None, attempts to load from the default budget YAML.
    """
    staff, diocese_scales = load_payroll_config(config_path)

    if snapshot is None:
        snapshot = load_ytd_snapshot(directory=snapshots_dir)

    if budget is None:
        budget = load_payroll_budget()

    if snapshot is None:
        # Still return staff data even without actuals
        total_cost = sum(s.total_cost for s in staff)
        total_recoveries = sum(s.recoveries for s in staff)
        return PayrollData(
            staff=staff,
            diocese_scales=diocese_scales,
            total_payroll_cost=round(total_cost, 2),
            total_recoveries=round(total_recoveries, 2),
            net_payroll_cost=round(total_cost + total_recoveries, 2),
            has_data=len(staff) > 0,
        )

    # Extract actuals
    actuals = extract_payroll_actuals(snapshot)
    total_income = extract_total_income(snapshot)

    # Build category comparison rows
    category_actuals: list[PayrollCategoryActuals] = []
    total_payroll_actual = 0.0
    total_payroll_budget = 0.0

    for cat_key in PAYROLL_CATEGORIES:
        actual = actuals.get(cat_key, 0.0)
        budgeted = budget.get(cat_key, 0.0)
        variance = round(actual - budgeted, 2)
        variance_pct = round(variance / budgeted * 100, 1) if budgeted != 0 else None

        category_actuals.append(PayrollCategoryActuals(
            category_key=cat_key,
            label=PAYROLL_CATEGORY_LABELS.get(cat_key, cat_key),
            actual=round(actual, 2),
            budget=round(budgeted, 2),
            variance_dollar=variance,
            variance_pct=variance_pct,
        ))

        total_payroll_actual += actual
        total_payroll_budget += budgeted

    # Staff totals
    total_config_cost = sum(s.total_cost for s in staff)
    total_recoveries = sum(s.recoveries for s in staff)

    # Payroll as % of income
    payroll_pct = None
    if total_income > 0:
        payroll_pct = round(total_payroll_actual / total_income * 100, 1)

    return PayrollData(
        staff=staff,
        category_actuals=category_actuals,
        diocese_scales=diocese_scales,
        total_payroll_cost=round(total_config_cost, 2),
        total_payroll_budget=round(total_payroll_budget, 2),
        total_recoveries=round(total_recoveries, 2),
        net_payroll_cost=round(total_config_cost + total_recoveries, 2),
        total_income=round(total_income, 2),
        payroll_pct_of_income=payroll_pct,
        has_data=True,
        snapshot_date=snapshot.report_date,
        snapshot_period=f"{snapshot.from_date} to {snapshot.to_date}",
    )
