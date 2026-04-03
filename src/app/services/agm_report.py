"""AGM Report service — annual actuals vs budget with multi-year trends.

Loads historical snapshot data across multiple years (2020-current),
computes full-year actuals vs budget with variances, and builds
5-year trend data for Chart.js visualisation.

Legacy account reconciliation is applied transparently via the
chart_of_accounts mapping engine.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot, SnapshotRow
from app.xero.snapshots import xero_snapshot_to_financial
from app.services.dashboard import BUDGETS_DIR, CHART_PATH, SNAPSHOTS_DIR, load_budget

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"

# Amount cleaning — strip dollar signs, commas, parentheses (negative)
_AMOUNT_RE = re.compile(r"[,$\s]")


def _clean_amount(raw: str) -> float:
    """Parse a dollar string into a float."""
    raw = raw.strip()
    if not raw or raw == "-":
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = _AMOUNT_RE.sub("", raw.strip("()"))
    try:
        value = float(cleaned)
    except ValueError:
        return 0.0
    return -value if negative else value


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AGMCategoryRow:
    """One row in the AGM actuals-vs-budget table for the report year."""

    category_key: str
    budget_label: str
    section: str  # "income" or "expenses"
    actual: float
    budget: float
    variance_dollar: float
    variance_pct: float | None
    is_significant: bool  # True if |variance| > 10% or > $1,000
    trend_values: list[float]  # One value per trend year (oldest first)

    @property
    def status(self) -> str:
        """Return 'success', 'warning', or 'danger' for colour coding."""
        if self.budget == 0:
            return "neutral"
        if self.section == "expenses":
            if self.actual > self.budget:
                return "danger" if self.is_significant else "warning"
            return "success"
        else:  # income
            if self.actual >= self.budget:
                return "success"
            return "danger" if self.is_significant else "warning"


@dataclass
class SectionSummary:
    """Totals for an income or expenses section."""

    label: str
    actual: float
    budget: float
    variance_dollar: float
    variance_pct: float | None
    trend_values: list[float]


@dataclass
class TrendYear:
    """Holds summary data for a single year in the trend."""

    year: int
    total_income: float
    total_expenses: float
    net_position: float


@dataclass
class AGMReportData:
    """Complete AGM report context for template rendering."""

    year: int
    trend_years: list[int] = field(default_factory=list)  # e.g. [2020, 2021, ..., 2025]
    income_rows: list[AGMCategoryRow] = field(default_factory=list)
    expense_rows: list[AGMCategoryRow] = field(default_factory=list)
    income_summary: SectionSummary | None = None
    expense_summary: SectionSummary | None = None
    net_actual: float = 0.0
    net_budget: float = 0.0
    net_variance_dollar: float = 0.0
    net_variance_pct: float | None = None
    net_trend_values: list[float] = field(default_factory=list)
    trend_data: list[TrendYear] = field(default_factory=list)
    has_data: bool = False
    generated_date: str = ""


# ---------------------------------------------------------------------------
# Historical data loading — CSV files + JSON snapshots
# ---------------------------------------------------------------------------

def _detect_csv_year(filename: str) -> int | None:
    """Extract a 4-digit year (2020-2029) from a filename."""
    m = re.search(r"(20[12]\d)", filename)
    return int(m.group(1)) if m else None


def _load_csv_as_snapshot(path: Path, year: int) -> FinancialSnapshot | None:
    """Parse a historical CSV file into a FinancialSnapshot."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception:
            return None

    reader = csv.reader(io.StringIO(text))
    rows_list = list(reader)
    if len(rows_list) < 2:
        return None

    # Find header row (skip title/blank rows)
    header_idx = 0
    for i, row in enumerate(rows_list):
        first = (row[0] if row else "").strip()
        if re.match(r"^(profit\s*&?\s*loss|$)", first, re.IGNORECASE):
            header_idx = i + 1
            continue
        break

    if header_idx >= len(rows_list):
        return None

    snapshot_rows: list[SnapshotRow] = []
    for line in rows_list[header_idx + 1:]:
        if not line or not line[0].strip():
            continue
        name_cell = line[0].strip()

        # Skip summary rows
        if re.match(r"^(total\s|net\s|gross\s)", name_cell, re.IGNORECASE):
            continue

        # Extract account code
        m = re.match(r"^(\d{3,6})\s*[-–]\s*(.+)$", name_cell)
        if m:
            code = m.group(1)
            name = m.group(2).strip()
        else:
            continue  # Skip rows without account codes

        # Get the amount from the first data column
        raw_amount = line[1].strip() if len(line) > 1 else "0"
        amount = _clean_amount(raw_amount)

        if amount != 0.0:
            snapshot_rows.append(SnapshotRow(
                account_code=code,
                account_name=name,
                amount=round(amount, 2),
            ))

    if not snapshot_rows:
        return None

    return FinancialSnapshot(
        report_date=f"{year}-12-31",
        from_date=f"{year}-01-01",
        to_date=f"{year}-12-31",
        source="csv_import",
        rows=snapshot_rows,
    )


def _load_json_snapshots_for_year(
    year: int,
    snapshots_dir: Path | None = None,
) -> list[FinancialSnapshot]:
    """Load all JSON snapshots that cover any part of the given year."""
    snap_dir = snapshots_dir or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return []

    snapshots: list[FinancialSnapshot] = []
    year_str = str(year)

    for path in snap_dir.glob("pl_*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            snap = None
            if "report_date" in raw:
                snap = FinancialSnapshot(**raw)
            elif "snapshot_metadata" in raw:
                resp = raw.get("response", {})
                if "report_date" in resp:
                    snap = FinancialSnapshot(**resp)
                else:
                    snap = xero_snapshot_to_financial(raw)

            if snap and (snap.from_date.startswith(year_str) or snap.to_date.startswith(year_str)):
                snapshots.append(snap)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return snapshots


def _aggregate_snapshots_to_category_totals(
    snapshots: list[FinancialSnapshot],
    account_lookup: dict,
) -> dict[str, float]:
    """Sum all snapshot rows into category totals.

    For a year with multiple snapshots, we take the snapshot that covers
    the widest date range (most likely the full-year one). If no single
    full-year snapshot exists, we aggregate all available data.
    """
    if not snapshots:
        return {}

    # Prefer the widest-spanning snapshot (full year if available)
    best = max(snapshots, key=lambda s: (s.to_date, s.from_date))

    category_totals: dict[str, float] = {}
    for row in best.rows:
        if row.account_code in account_lookup:
            cat_key = account_lookup[row.account_code][0]
            category_totals[cat_key] = category_totals.get(cat_key, 0) + row.amount

    return category_totals


def load_year_actuals(
    year: int,
    account_lookup: dict,
    snapshots_dir: Path | None = None,
    historical_dir: Path | None = None,
) -> dict[str, float]:
    """Load actual figures for a given year from snapshots and/or historical CSV.

    Priority:
    1. JSON snapshots from data/snapshots/
    2. Historical CSV from data/historical/

    Returns: {category_key: total_amount}
    """
    # Try JSON snapshots first
    json_snapshots = _load_json_snapshots_for_year(year, snapshots_dir)
    if json_snapshots:
        return _aggregate_snapshots_to_category_totals(json_snapshots, account_lookup)

    # Fall back to historical CSV
    hist_dir = historical_dir or HISTORICAL_DIR
    if hist_dir.exists():
        for csv_path in hist_dir.glob("*.csv"):
            detected_year = _detect_csv_year(csv_path.name)
            if detected_year == year:
                snapshot = _load_csv_as_snapshot(csv_path, year)
                if snapshot:
                    return _aggregate_snapshots_to_category_totals(
                        [snapshot], account_lookup,
                    )

    return {}


# ---------------------------------------------------------------------------
# Variance helpers
# ---------------------------------------------------------------------------

def _is_significant_variance(
    variance_dollar: float,
    variance_pct: float | None,
) -> bool:
    """Determine if a variance is significant (>10% or >$1,000)."""
    if abs(variance_dollar) > 1000:
        return True
    if variance_pct is not None and abs(variance_pct) > 10:
        return True
    return False


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_agm_report(
    year: int | None = None,
    chart: ChartOfAccounts | None = None,
    snapshots_dir: Path | None = None,
    historical_dir: Path | None = None,
    budget: dict[str, float] | None = None,
    trend_start_year: int | None = None,
) -> AGMReportData:
    """Build the AGM report data for a given year with multi-year trends.

    Args:
        year: The AGM report year (default: previous year).
        chart: Chart of accounts (loaded from disk if None).
        snapshots_dir: Override snapshot directory for testing.
        historical_dir: Override historical CSV directory for testing.
        budget: Annual budget dict (loaded from disk if None).
        trend_start_year: First year for trend data (default: year - 4).

    Returns:
        AGMReportData ready for template rendering.
    """
    today = date.today()
    if year is None:
        year = today.year - 1  # AGM typically covers previous year

    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return AGMReportData(year=year, generated_date=today.isoformat())

    account_lookup = build_account_lookup(chart)

    # Load budget for the report year
    if budget is None:
        budget = load_budget(year=year, chart=chart)

    # Build category metadata
    cat_meta: dict[str, tuple[str, str]] = {}  # cat_key -> (budget_label, section)
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            cat_meta[cat_key] = (cat.budget_label, section_name)

    # Determine trend year range (5 years by default)
    if trend_start_year is None:
        trend_start_year = year - 4
    trend_years = list(range(trend_start_year, year + 1))

    # Load actuals for each trend year
    yearly_actuals: dict[int, dict[str, float]] = {}
    for y in trend_years:
        yearly_actuals[y] = load_year_actuals(
            y, account_lookup, snapshots_dir, historical_dir,
        )

    report_actuals = yearly_actuals.get(year, {})

    if not report_actuals:
        return AGMReportData(
            year=year,
            trend_years=trend_years,
            generated_date=today.isoformat(),
        )

    # Build all category keys
    all_keys: set[str] = set()
    all_keys.update(report_actuals.keys())
    all_keys.update(budget.keys())
    for y_actuals in yearly_actuals.values():
        all_keys.update(y_actuals.keys())

    # Build rows
    income_rows: list[AGMCategoryRow] = []
    expense_rows: list[AGMCategoryRow] = []

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

        actual = round(report_actuals.get(cat_key, 0.0), 2)
        budgeted = round(budget.get(cat_key, 0.0), 2)
        variance_dollar = round(actual - budgeted, 2)
        variance_pct = (
            round(variance_dollar / budgeted * 100, 1)
            if budgeted != 0 else None
        )

        trend_values = [
            round(yearly_actuals.get(y, {}).get(cat_key, 0.0), 2)
            for y in trend_years
        ]

        significant = _is_significant_variance(variance_dollar, variance_pct)

        row = AGMCategoryRow(
            category_key=cat_key,
            budget_label=label,
            section=section,
            actual=actual,
            budget=budgeted,
            variance_dollar=variance_dollar,
            variance_pct=variance_pct,
            is_significant=significant,
            trend_values=trend_values,
        )

        if section == "income":
            income_rows.append(row)
        else:
            expense_rows.append(row)

    # Section summaries
    def _build_summary(
        rows: list[AGMCategoryRow],
        label: str,
    ) -> SectionSummary:
        actual = round(sum(r.actual for r in rows), 2)
        budgeted = round(sum(r.budget for r in rows), 2)
        variance = round(actual - budgeted, 2)
        pct = round(variance / budgeted * 100, 1) if budgeted != 0 else None

        # Trend: sum category trends per year
        trend = []
        for i in range(len(trend_years)):
            trend.append(round(sum(r.trend_values[i] for r in rows), 2))

        return SectionSummary(
            label=label,
            actual=actual,
            budget=budgeted,
            variance_dollar=variance,
            variance_pct=pct,
            trend_values=trend,
        )

    income_summary = _build_summary(income_rows, "Total Income")
    expense_summary = _build_summary(expense_rows, "Total Expenses")

    # Net position
    net_actual = round(income_summary.actual - expense_summary.actual, 2)
    net_budget = round(income_summary.budget - expense_summary.budget, 2)
    net_variance = round(net_actual - net_budget, 2)
    net_variance_pct = (
        round(net_variance / abs(net_budget) * 100, 1) if net_budget != 0 else None
    )
    net_trend_values = [
        round(income_summary.trend_values[i] - expense_summary.trend_values[i], 2)
        for i in range(len(trend_years))
    ]

    # Build trend summary data for charts
    trend_data: list[TrendYear] = []
    for i, y in enumerate(trend_years):
        trend_data.append(TrendYear(
            year=y,
            total_income=income_summary.trend_values[i],
            total_expenses=expense_summary.trend_values[i],
            net_position=net_trend_values[i],
        ))

    return AGMReportData(
        year=year,
        trend_years=trend_years,
        income_rows=income_rows,
        expense_rows=expense_rows,
        income_summary=income_summary,
        expense_summary=expense_summary,
        net_actual=net_actual,
        net_budget=net_budget,
        net_variance_dollar=net_variance,
        net_variance_pct=net_variance_pct,
        net_trend_values=net_trend_values,
        trend_data=trend_data,
        has_data=True,
        generated_date=today.isoformat(),
    )
