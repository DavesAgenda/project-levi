"""Budget Comparison service — side-by-side draft vs current vs prior year.

Assembles data for the budget comparison view:
- Draft budget (from budget YAML for target year)
- Current year actuals (from snapshots)
- Current year budget (from budget YAML for current year)
- Prior year actuals (from snapshots)
- Variance columns between draft and current year
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts
from app.services.budget import load_budget_flat, BUDGETS_DIR, CHART_PATH
from app.services.council_report import load_all_snapshots, SNAPSHOTS_DIR


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRow:
    """One category row in the comparison table."""

    category_key: str
    budget_label: str
    section: str  # "income" or "expenses"
    draft_budget: float
    current_actual: float
    current_budget: float
    prior_actual: float
    variance_dollar: float  # draft_budget - current_actual
    variance_pct: float | None  # percentage difference

    @property
    def is_significant(self) -> bool:
        """True if draft differs from current actual by more than 20%."""
        if self.current_actual == 0:
            return self.draft_budget != 0
        pct = abs(self.draft_budget - self.current_actual) / abs(self.current_actual) * 100
        return pct > 20

    @property
    def variance_status(self) -> str:
        """Return CSS class for variance display."""
        if self.variance_dollar == 0:
            return "neutral"
        if self.section == "income":
            return "positive" if self.variance_dollar > 0 else "negative"
        else:  # expenses
            return "negative" if self.variance_dollar > 0 else "positive"


@dataclass
class DatasetSummary:
    """Summary totals for one dataset (draft, current, prior)."""

    total_income: float = 0.0
    total_expenses: float = 0.0

    @property
    def net_position(self) -> float:
        return round(self.total_income - self.total_expenses, 2)


@dataclass
class ComparisonData:
    """Complete comparison context for template rendering."""

    target_year: int = 0
    current_year: int = 0
    prior_year: int = 0
    income_rows: list[ComparisonRow] = field(default_factory=list)
    expense_rows: list[ComparisonRow] = field(default_factory=list)
    draft_summary: DatasetSummary = field(default_factory=DatasetSummary)
    current_summary: DatasetSummary = field(default_factory=DatasetSummary)
    prior_summary: DatasetSummary = field(default_factory=DatasetSummary)
    has_data: bool = False


# ---------------------------------------------------------------------------
# Actuals aggregation from snapshots
# ---------------------------------------------------------------------------

def _load_actuals_by_category(
    year: int,
    chart: ChartOfAccounts,
    snapshots_dir: Path | None = None,
) -> dict[str, float]:
    """Load all snapshots for a given year and aggregate by category.

    Returns {category_key: total_amount}.
    """
    account_lookup = build_account_lookup(chart)
    snapshots = load_all_snapshots(snapshots_dir)

    # Filter snapshots to the requested year
    year_snapshots = [
        s for s in snapshots
        if s.from_date.startswith(str(year))
    ]

    category_totals: dict[str, float] = {}
    for snap in year_snapshots:
        for row in snap.rows:
            if row.account_code in account_lookup:
                cat_key = account_lookup[row.account_code][0]
                category_totals[cat_key] = category_totals.get(cat_key, 0) + row.amount

    return category_totals


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_budget_comparison(
    target_year: int,
    *,
    chart: ChartOfAccounts | None = None,
    chart_path: Path | None = None,
    budgets_dir: Path | None = None,
    snapshots_dir: Path | None = None,
) -> ComparisonData:
    """Build the comparison data for draft vs current vs prior year.

    Args:
        target_year: The year of the draft budget being compared.
        chart: Chart of accounts (loaded from disk if None).
        chart_path: Path to chart_of_accounts.yaml.
        budgets_dir: Override budget directory for testing.
        snapshots_dir: Override snapshot directory for testing.

    Returns:
        ComparisonData ready for template rendering.
    """
    cp = chart_path or CHART_PATH
    bdir = budgets_dir or BUDGETS_DIR

    if chart is None:
        if not cp.exists():
            return ComparisonData(target_year=target_year)
        chart = load_chart_of_accounts(cp)

    current_year = target_year - 1
    prior_year = target_year - 2

    # Load all datasets
    draft_budget = load_budget_flat(target_year, chart=chart, budgets_dir=bdir, chart_path=cp)
    current_budget = load_budget_flat(current_year, chart=chart, budgets_dir=bdir, chart_path=cp)
    current_actuals = _load_actuals_by_category(current_year, chart, snapshots_dir)
    prior_actuals = _load_actuals_by_category(prior_year, chart, snapshots_dir)

    # Build category metadata lookup
    cat_meta: dict[str, tuple[str, str]] = {}  # cat_key -> (budget_label, section)
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            cat_meta[cat_key] = (cat.budget_label, section_name)

    # Collect all category keys across all datasets
    all_keys = set()
    for d in (draft_budget, current_budget, current_actuals, prior_actuals):
        all_keys.update(d.keys())

    # Sort: income first (by label), then expenses (by label)
    sorted_keys = sorted(
        (k for k in all_keys if k in cat_meta),
        key=lambda k: (0 if cat_meta[k][1] == "income" else 1, cat_meta[k][0]),
    )

    income_rows: list[ComparisonRow] = []
    expense_rows: list[ComparisonRow] = []
    draft_summary = DatasetSummary()
    current_summary = DatasetSummary()
    prior_summary = DatasetSummary()

    for cat_key in sorted_keys:
        label, section = cat_meta[cat_key]
        draft = draft_budget.get(cat_key, 0.0)
        cur_actual = current_actuals.get(cat_key, 0.0)
        cur_budget = current_budget.get(cat_key, 0.0)
        prior = prior_actuals.get(cat_key, 0.0)

        var_dollar = round(draft - cur_actual, 2)
        var_pct = (
            round((draft - cur_actual) / abs(cur_actual) * 100, 1)
            if cur_actual != 0
            else None
        )

        row = ComparisonRow(
            category_key=cat_key,
            budget_label=label,
            section=section,
            draft_budget=round(draft, 2),
            current_actual=round(cur_actual, 2),
            current_budget=round(cur_budget, 2),
            prior_actual=round(prior, 2),
            variance_dollar=var_dollar,
            variance_pct=var_pct,
        )

        if section == "income":
            income_rows.append(row)
            draft_summary.total_income += draft
            current_summary.total_income += cur_actual
            prior_summary.total_income += prior
        else:
            expense_rows.append(row)
            draft_summary.total_expenses += draft
            current_summary.total_expenses += cur_actual
            prior_summary.total_expenses += prior

    # Round summaries
    for s in (draft_summary, current_summary, prior_summary):
        s.total_income = round(s.total_income, 2)
        s.total_expenses = round(s.total_expenses, 2)

    has_data = bool(draft_budget or current_actuals or prior_actuals)

    return ComparisonData(
        target_year=target_year,
        current_year=current_year,
        prior_year=prior_year,
        income_rows=income_rows,
        expense_rows=expense_rows,
        draft_summary=draft_summary,
        current_summary=current_summary,
        prior_summary=prior_summary,
        has_data=has_data,
    )
