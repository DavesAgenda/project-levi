"""Council Report service — multi-month YTD vs budget for parish council.

Loads all available snapshots, computes monthly actuals by category,
and builds a table with YTD actual, YTD budget, and variance columns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot
from app.xero.snapshots import xero_snapshot_to_financial
from app.services.dashboard import CHART_PATH, SNAPSHOTS_DIR


# ---------------------------------------------------------------------------
# Month helpers
# ---------------------------------------------------------------------------

MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _month_key(year: int, month: int) -> str:
    """Return a sortable month key like '2026-01'."""
    return f"{year}-{month:02d}"


def _month_label(month_key: str) -> str:
    """Convert '2026-03' to 'Mar'."""
    month_num = int(month_key.split("-")[1])
    return MONTH_LABELS[month_num - 1]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MonthlyAmount:
    """Actual amount for a single month."""

    month_key: str
    month_label: str
    amount: float


@dataclass
class CouncilReportRow:
    """One row in the council report table."""

    category_key: str
    budget_label: str
    section: str  # "income" or "expenses"
    monthly_actuals: dict[str, float]  # month_key -> amount
    ytd_actual: float
    ytd_budget: float
    variance_dollar: float
    variance_pct: float | None

    @property
    def status(self) -> str:
        """Return 'success', 'warning', or 'danger' for colour coding."""
        if self.ytd_budget == 0:
            return "neutral"
        pct = abs(self.variance_pct or 0)
        if self.section == "expenses":
            if self.ytd_actual > self.ytd_budget:
                return "danger"
            if pct <= 10:
                return "warning"
            return "success"
        else:  # income
            if self.ytd_actual >= self.ytd_budget:
                return "success"
            if pct <= 10:
                return "warning"
            return "danger"


@dataclass
class SectionSummary:
    """Totals for an income or expenses section."""

    label: str
    monthly_totals: dict[str, float]
    ytd_actual: float
    ytd_budget: float
    variance_dollar: float
    variance_pct: float | None


@dataclass
class CouncilReportData:
    """Complete council report context for template rendering."""

    year: int
    view_mode: str = "ytd"  # "ytd" or "month"
    month_keys: list[str] = field(default_factory=list)
    month_labels: list[str] = field(default_factory=list)
    income_rows: list[CouncilReportRow] = field(default_factory=list)
    expense_rows: list[CouncilReportRow] = field(default_factory=list)
    income_summary: SectionSummary | None = None
    expense_summary: SectionSummary | None = None
    net_monthly: dict[str, float] = field(default_factory=dict)
    net_ytd: float = 0.0
    net_ytd_budget: float = 0.0
    net_variance_dollar: float = 0.0
    net_variance_pct: float | None = None
    has_data: bool = False
    generated_date: str = ""


# ---------------------------------------------------------------------------
# Snapshot loading — all snapshots for a year
# ---------------------------------------------------------------------------

def load_all_snapshots(
    directory: Path | None = None,
) -> list[FinancialSnapshot]:
    """Load all valid FinancialSnapshot JSON files from the snapshots directory.

    Returns a list sorted by from_date ascending.
    """
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return []

    snapshots: list[FinancialSnapshot] = []

    for path in snap_dir.glob("pl_*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if "report_date" in raw:
                snapshots.append(FinancialSnapshot(**raw))
            elif "snapshot_metadata" in raw:
                resp = raw.get("response", {})
                if "report_date" in resp:
                    snapshots.append(FinancialSnapshot(**resp))
                else:
                    snap = xero_snapshot_to_financial(raw)
                    if snap:
                        snapshots.append(snap)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return sorted(snapshots, key=lambda s: s.from_date)


def _snapshot_to_monthly_actuals(
    snapshot: FinancialSnapshot,
    account_lookup: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, float]]:
    """Convert a snapshot into category actuals distributed across its months.

    For a snapshot covering N months, the total is divided equally across
    each month in the range.  If the snapshot covers a single month, the
    full amount is assigned to that month.

    Returns: {category_key: {month_key: amount}}
    """
    from_dt = datetime.strptime(snapshot.from_date, "%Y-%m-%d").date()
    to_dt = datetime.strptime(snapshot.to_date, "%Y-%m-%d").date()

    # Build list of month_keys covered by this snapshot
    covered_months: list[str] = []
    current = from_dt.replace(day=1)
    while current <= to_dt:
        covered_months.append(_month_key(current.year, current.month))
        # Advance to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    num_months = len(covered_months) if covered_months else 1

    # Aggregate actuals by category
    category_totals: dict[str, float] = {}
    for row in snapshot.rows:
        if row.account_code in account_lookup:
            cat_key = account_lookup[row.account_code][0]
            category_totals[cat_key] = category_totals.get(cat_key, 0) + row.amount

    # Distribute evenly across covered months
    result: dict[str, dict[str, float]] = {}
    for cat_key, total in category_totals.items():
        monthly = total / num_months
        result[cat_key] = {}
        for mk in covered_months:
            result[cat_key][mk] = round(monthly, 2)

    return result


# ---------------------------------------------------------------------------
# Budget proration
# ---------------------------------------------------------------------------

def _prorate_budget(
    annual_budget: dict[str, float],
    num_months: int,
) -> dict[str, float]:
    """Prorate annual budget figures to a YTD amount based on number of months.

    Simple straight-line: YTD budget = annual * (months / 12).
    """
    factor = num_months / 12.0
    return {k: round(v * factor, 2) for k, v in annual_budget.items()}


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_council_report(
    year: int | None = None,
    end_month: int | None = None,
    view_mode: str = "ytd",
    chart: ChartOfAccounts | None = None,
    snapshots_dir: Path | None = None,
    budget: dict[str, float] | None = None,
) -> CouncilReportData:
    """Build the council report data for a given year.

    Args:
        year: Financial year (default: current year).
        end_month: Last month to include (1-12, default: current month).
        view_mode: "ytd" for all months Jan-end_month, "month" for single month only.
        chart: Chart of accounts (loaded from disk if None).
        snapshots_dir: Override snapshot directory for testing.
        budget: Annual budget dict (loaded from disk if None).

    Returns:
        CouncilReportData ready for template rendering.
    """
    today = date.today()
    if year is None:
        year = today.year
    if end_month is None:
        end_month = today.month

    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return CouncilReportData(year=year, generated_date=today.isoformat())

    account_lookup = build_account_lookup(chart)

    # Load budget
    if budget is None:
        from app.services.dashboard import load_budget
        budget = load_budget(year=year, chart=chart)

    # Load snapshots and filter to the requested year
    all_snapshots = load_all_snapshots(snapshots_dir)
    year_snapshots = [
        s for s in all_snapshots
        if s.from_date.startswith(str(year)) or s.to_date.startswith(str(year))
    ]

    if not year_snapshots:
        return CouncilReportData(year=year, view_mode=view_mode, generated_date=today.isoformat())

    # Build month keys for the report columns
    if view_mode == "month":
        # Single month view — show only the selected month
        month_keys = [_month_key(year, end_month)]
        month_labels = [_month_label(month_keys[0])]
    else:
        # YTD view — Jan through end_month
        month_keys = [_month_key(year, m) for m in range(1, end_month + 1)]
        month_labels = [_month_label(mk) for mk in month_keys]

    # Aggregate monthly actuals from all snapshots
    # category_key -> month_key -> amount
    category_monthly: dict[str, dict[str, float]] = {}

    for snapshot in year_snapshots:
        snapshot_monthly = _snapshot_to_monthly_actuals(snapshot, account_lookup)
        for cat_key, months in snapshot_monthly.items():
            if cat_key not in category_monthly:
                category_monthly[cat_key] = {}
            for mk, amount in months.items():
                if mk in month_keys:  # Only include months in range
                    category_monthly[cat_key][mk] = (
                        category_monthly[cat_key].get(mk, 0) + amount
                    )

    # Build category metadata lookup
    cat_meta: dict[str, tuple[str, str]] = {}  # cat_key -> (budget_label, section)
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            cat_meta[cat_key] = (cat.budget_label, section_name)

    # Prorate annual budget
    budget_months = 1 if view_mode == "month" else end_month
    ytd_budget = _prorate_budget(budget, budget_months)

    # Build rows
    all_keys = set(list(category_monthly.keys()) + list(budget.keys()))
    income_rows: list[CouncilReportRow] = []
    expense_rows: list[CouncilReportRow] = []

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

        monthly = category_monthly.get(cat_key, {})
        ytd_actual = sum(monthly.get(mk, 0) for mk in month_keys)
        cat_ytd_budget = ytd_budget.get(cat_key, 0.0)

        variance_dollar = ytd_actual - cat_ytd_budget
        variance_pct = (
            round(variance_dollar / cat_ytd_budget * 100, 1)
            if cat_ytd_budget != 0
            else None
        )

        row = CouncilReportRow(
            category_key=cat_key,
            budget_label=label,
            section=section,
            monthly_actuals={mk: round(monthly.get(mk, 0), 2) for mk in month_keys},
            ytd_actual=round(ytd_actual, 2),
            ytd_budget=round(cat_ytd_budget, 2),
            variance_dollar=round(variance_dollar, 2),
            variance_pct=variance_pct,
        )

        if section == "income":
            income_rows.append(row)
        else:
            expense_rows.append(row)

    # Section summaries
    def _section_summary(
        rows: list[CouncilReportRow], label: str,
    ) -> SectionSummary:
        monthly_totals = {mk: 0.0 for mk in month_keys}
        ytd_actual = 0.0
        ytd_bgt = 0.0
        for r in rows:
            for mk in month_keys:
                monthly_totals[mk] += r.monthly_actuals.get(mk, 0)
            ytd_actual += r.ytd_actual
            ytd_bgt += r.ytd_budget

        monthly_totals = {mk: round(v, 2) for mk, v in monthly_totals.items()}
        variance = round(ytd_actual - ytd_bgt, 2)
        pct = round(variance / ytd_bgt * 100, 1) if ytd_bgt != 0 else None

        return SectionSummary(
            label=label,
            monthly_totals=monthly_totals,
            ytd_actual=round(ytd_actual, 2),
            ytd_budget=round(ytd_bgt, 2),
            variance_dollar=variance,
            variance_pct=pct,
        )

    income_summary = _section_summary(income_rows, "Total Income")
    expense_summary = _section_summary(expense_rows, "Total Expenses")

    # Net position
    net_monthly = {
        mk: round(
            income_summary.monthly_totals.get(mk, 0)
            - expense_summary.monthly_totals.get(mk, 0),
            2,
        )
        for mk in month_keys
    }
    net_ytd = round(income_summary.ytd_actual - expense_summary.ytd_actual, 2)
    net_ytd_budget = round(income_summary.ytd_budget - expense_summary.ytd_budget, 2)
    net_variance = round(net_ytd - net_ytd_budget, 2)
    net_variance_pct = (
        round(net_variance / abs(net_ytd_budget) * 100, 1)
        if net_ytd_budget != 0
        else None
    )

    return CouncilReportData(
        year=year,
        view_mode=view_mode,
        month_keys=month_keys,
        month_labels=month_labels,
        income_rows=income_rows,
        expense_rows=expense_rows,
        income_summary=income_summary,
        expense_summary=expense_summary,
        net_monthly=net_monthly,
        net_ytd=net_ytd,
        net_ytd_budget=net_ytd_budget,
        net_variance_dollar=net_variance,
        net_variance_pct=net_variance_pct,
        has_data=True,
        generated_date=today.isoformat(),
    )
