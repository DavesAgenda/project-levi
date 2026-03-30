"""Dashboard data service — loads snapshots, budgets, and computes variances.

Provides data ready for both Jinja2 templates and Chart.js visualisations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot
from app.services.budget import load_budget_flat

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
CONFIG_DIR = PROJECT_ROOT / "config"
BUDGETS_DIR = PROJECT_ROOT / "budgets"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"


# ---------------------------------------------------------------------------
# Data classes for dashboard context
# ---------------------------------------------------------------------------

@dataclass
class CategoryVariance:
    """Variance data for a single budget category."""

    category_key: str
    budget_label: str
    section: str  # "income" or "expenses"
    actual: float
    budget: float
    variance_dollar: float  # actual - budget
    variance_pct: float | None  # (actual - budget) / budget * 100, None if budget is 0

    @property
    def is_over_budget(self) -> bool:
        """For expenses: over budget when actual > budget.
        For income: under target when actual < budget."""
        if self.section == "expenses":
            return self.actual > self.budget and self.budget > 0
        return self.actual < self.budget and self.budget > 0

    @property
    def status(self) -> str:
        """Return 'success', 'warning', or 'danger' for colour coding."""
        if self.budget == 0:
            return "success"
        pct = abs(self.variance_pct or 0)
        if self.section == "expenses":
            if self.actual > self.budget:
                return "danger"
            if pct <= 10:
                return "warning"
            return "success"
        else:  # income
            if self.actual >= self.budget:
                return "success"
            if pct <= 10:
                return "warning"
            return "danger"


@dataclass
class DashboardData:
    """Complete dashboard context for template rendering."""

    total_income: float = 0.0
    total_expenses: float = 0.0
    net_position: float = 0.0
    budget_total_income: float = 0.0
    budget_total_expenses: float = 0.0
    budget_consumed_pct: float = 0.0
    categories: list[CategoryVariance] = field(default_factory=list)
    has_data: bool = False
    snapshot_date: str = ""
    snapshot_period: str = ""

    @property
    def income_categories(self) -> list[CategoryVariance]:
        return [c for c in self.categories if c.section == "income"]

    @property
    def expense_categories(self) -> list[CategoryVariance]:
        return [c for c in self.categories if c.section == "expenses"]


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------

def find_latest_snapshot(directory: Path | None = None) -> FinancialSnapshot | None:
    """Find and load the most recent FinancialSnapshot JSON file.

    Looks for files matching the FinancialSnapshot schema in the snapshots
    directory.  Returns None if no snapshots exist.
    """
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return None

    json_files = sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    for path in json_files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Handle both raw FinancialSnapshot and snapshot-writer wrapped format
            if "report_date" in raw:
                return FinancialSnapshot(**raw)
            if "response" in raw and "report_date" in raw.get("response", {}):
                return FinancialSnapshot(**raw["response"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return None


# ---------------------------------------------------------------------------
# Budget loading
# ---------------------------------------------------------------------------

def load_budget(
    year: int = 2026,
    chart: ChartOfAccounts | None = None,
) -> dict[str, float]:
    """Load budget figures from budgets/{year}.yaml.

    Returns a dict mapping category_key -> budgeted dollar amount.
    Delegates to the budget service for the actual loading logic.
    """
    return load_budget_flat(
        year,
        chart=chart,
        budgets_dir=BUDGETS_DIR,
        chart_path=CHART_PATH,
    )


# ---------------------------------------------------------------------------
# Variance computation
# ---------------------------------------------------------------------------

def compute_dashboard_data(
    snapshot: FinancialSnapshot | None = None,
    budget: dict[str, float] | None = None,
    chart: ChartOfAccounts | None = None,
    snapshots_dir: Path | None = None,
) -> DashboardData:
    """Build complete dashboard data from a snapshot and budget.

    If snapshot is None, attempts to load the latest from disk.
    If budget is None, attempts to load from the default budget YAML.
    """
    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return DashboardData()

    if snapshot is None:
        snapshot = find_latest_snapshot(snapshots_dir)

    if budget is None:
        budget = load_budget(chart=chart)

    if snapshot is None:
        return DashboardData()

    account_lookup = build_account_lookup(chart)

    # Aggregate actuals by category_key
    category_actuals: dict[str, float] = {}
    for row in snapshot.rows:
        if row.account_code in account_lookup:
            cat_key = account_lookup[row.account_code][0]
            category_actuals[cat_key] = category_actuals.get(cat_key, 0) + row.amount

    # Build a combined set of all category keys
    all_keys = set(list(category_actuals.keys()) + list(budget.keys()))

    # Build category-level lookup for labels and sections
    cat_meta: dict[str, tuple[str, str]] = {}  # cat_key -> (budget_label, section)
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            cat_meta[cat_key] = (cat.budget_label, section_name)

    categories: list[CategoryVariance] = []
    total_income = 0.0
    total_expenses = 0.0
    budget_total_income = 0.0
    budget_total_expenses = 0.0

    sorted_keys = sorted(
        all_keys,
        key=lambda k: (
            (0 if cat_meta[k][1] == "income" else 1, cat_meta[k][0])
            if k in cat_meta
            else (2, k)
        ),
    )

    for cat_key in sorted_keys:
        if cat_key not in cat_meta:
            continue
        label, section = cat_meta[cat_key]
        actual = category_actuals.get(cat_key, 0.0)
        budgeted = budget.get(cat_key, 0.0)

        variance_dollar = actual - budgeted
        variance_pct = (variance_dollar / budgeted * 100) if budgeted != 0 else None

        categories.append(CategoryVariance(
            category_key=cat_key,
            budget_label=label,
            section=section,
            actual=round(actual, 2),
            budget=round(budgeted, 2),
            variance_dollar=round(variance_dollar, 2),
            variance_pct=round(variance_pct, 1) if variance_pct is not None else None,
        ))

        if section == "income":
            total_income += actual
            budget_total_income += budgeted
        else:
            total_expenses += actual
            budget_total_expenses += budgeted

    # Budget consumed % — what fraction of expense budget has been spent
    budget_consumed_pct = 0.0
    if budget_total_expenses > 0:
        budget_consumed_pct = round(total_expenses / budget_total_expenses * 100, 1)

    return DashboardData(
        total_income=round(total_income, 2),
        total_expenses=round(total_expenses, 2),
        net_position=round(total_income - total_expenses, 2),
        budget_total_income=round(budget_total_income, 2),
        budget_total_expenses=round(budget_total_expenses, 2),
        budget_consumed_pct=budget_consumed_pct,
        categories=categories,
        has_data=True,
        snapshot_date=snapshot.report_date,
        snapshot_period=f"{snapshot.from_date} to {snapshot.to_date}",
    )
