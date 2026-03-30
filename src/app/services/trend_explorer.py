"""Trend Explorer service — multi-year category aggregation for trend charts.

Scans all available snapshot data across years, aggregates by budget category
with legacy account reconciliation, and produces data structures ready for
Chart.js visualisation and data tables.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.csv_import import (
    build_account_lookup,
    import_csv,
    load_chart_of_accounts,
    to_snapshot,
)
from app.models import ChartOfAccounts, FinancialSnapshot
from app.services.dashboard import CHART_PATH, CONFIG_DIR, SNAPSHOTS_DIR

# Historical CSVs live alongside snapshots
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"

_YEAR_RE = re.compile(r"(20[12]\d)")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CategoryInfo:
    """Metadata about a single budget category."""

    key: str
    label: str
    section: str  # "income" or "expenses"


@dataclass
class YearlyTotal:
    """Annual total for one budget category in one year."""

    year: int
    total: float


@dataclass
class MonthlyTotal:
    """Monthly total for one budget category in one month."""

    year: int
    month: int
    month_label: str
    total: float


@dataclass
class TrendData:
    """Trend data for one or two categories across all available years."""

    primary_category: CategoryInfo
    compare_category: CategoryInfo | None = None
    primary_yearly: list[YearlyTotal] = field(default_factory=list)
    compare_yearly: list[YearlyTotal] = field(default_factory=list)
    primary_monthly: list[MonthlyTotal] = field(default_factory=list)
    compare_monthly: list[MonthlyTotal] = field(default_factory=list)
    available_years: list[int] = field(default_factory=list)
    has_monthly: bool = False
    has_data: bool = False


MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ---------------------------------------------------------------------------
# Snapshot loading — all years
# ---------------------------------------------------------------------------


def _load_json_snapshots(directory: Path | None = None) -> list[FinancialSnapshot]:
    """Load all FinancialSnapshot JSON files from the snapshots directory."""
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return []

    snapshots: list[FinancialSnapshot] = []
    for path in snap_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if "report_date" in raw:
                snapshots.append(FinancialSnapshot(**raw))
            elif "response" in raw and "report_date" in raw.get("response", {}):
                snapshots.append(FinancialSnapshot(**raw["response"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return snapshots


def _load_historical_csv_snapshots(
    directory: Path | None = None,
    chart: ChartOfAccounts | None = None,
) -> list[FinancialSnapshot]:
    """Load historical CSVs and convert them to FinancialSnapshot objects.

    Each CSV is imported through the mapping engine so legacy accounts are
    properly reconciled into their parent categories.
    """
    hist_dir = directory or HISTORICAL_DIR
    if not hist_dir.exists():
        return []

    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return []

    snapshots: list[FinancialSnapshot] = []
    for csv_path in sorted(hist_dir.glob("*.csv")):
        year_match = _YEAR_RE.search(csv_path.stem)
        if not year_match:
            continue
        year = int(year_match.group(1))
        from_date = f"{year}-01-01"
        to_date = f"{year}-12-31"

        try:
            csv_bytes = csv_path.read_bytes()
            result = import_csv(csv_bytes, chart, filename=csv_path.name, strict=False)
            if result.rows:
                snapshot = to_snapshot(
                    result,
                    from_date=from_date,
                    to_date=to_date,
                    report_date=to_date,
                )
                snapshots.append(snapshot)
        except Exception:
            continue

    return snapshots


def load_all_snapshots_all_years(
    snapshots_dir: Path | None = None,
    historical_dir: Path | None = None,
    chart: ChartOfAccounts | None = None,
) -> list[FinancialSnapshot]:
    """Load all snapshots from both JSON files and historical CSVs.

    Returns a list sorted by from_date ascending, with duplicates for the
    same year resolved by preferring JSON snapshots over CSV-derived ones.
    """
    json_snapshots = _load_json_snapshots(snapshots_dir)
    csv_snapshots = _load_historical_csv_snapshots(historical_dir, chart)

    # Deduplicate: if we have a JSON snapshot covering the same year as a CSV,
    # prefer the JSON one (it may have been manually verified)
    json_years: set[int] = set()
    for s in json_snapshots:
        try:
            json_years.add(datetime.strptime(s.from_date, "%Y-%m-%d").year)
        except ValueError:
            pass

    combined = list(json_snapshots)
    for s in csv_snapshots:
        try:
            csv_year = datetime.strptime(s.from_date, "%Y-%m-%d").year
            if csv_year not in json_years:
                combined.append(s)
        except ValueError:
            pass

    return sorted(combined, key=lambda s: s.from_date)


# ---------------------------------------------------------------------------
# Category listing
# ---------------------------------------------------------------------------


def get_all_categories(chart: ChartOfAccounts | None = None) -> list[CategoryInfo]:
    """Return all budget categories from chart_of_accounts.yaml, sorted by section then label."""
    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return []

    categories: list[CategoryInfo] = []
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            categories.append(CategoryInfo(
                key=cat_key,
                label=cat.budget_label,
                section=section_name,
            ))

    # Sort: income first, then expenses, alpha by label within
    categories.sort(key=lambda c: (0 if c.section == "income" else 1, c.label))
    return categories


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _extract_year(date_str: str) -> int:
    """Extract year from an ISO date string."""
    return datetime.strptime(date_str, "%Y-%m-%d").year


def _extract_month(date_str: str) -> int:
    """Extract month from an ISO date string."""
    return datetime.strptime(date_str, "%Y-%m-%d").month


def _snapshot_covers_single_month(snapshot: FinancialSnapshot) -> bool:
    """Check if a snapshot covers a single month (for monthly granularity)."""
    try:
        from_dt = datetime.strptime(snapshot.from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(snapshot.to_date, "%Y-%m-%d")
        return from_dt.year == to_dt.year and from_dt.month == to_dt.month
    except ValueError:
        return False


def aggregate_category_by_year(
    snapshots: list[FinancialSnapshot],
    category_key: str,
    chart: ChartOfAccounts,
) -> list[YearlyTotal]:
    """Aggregate a category's total for each year across all snapshots.

    Legacy accounts are mapped to their parent category transparently via
    the account lookup.
    """
    account_lookup = build_account_lookup(chart)

    # year -> total
    year_totals: dict[int, float] = {}

    for snapshot in snapshots:
        try:
            year = _extract_year(snapshot.from_date)
        except ValueError:
            continue

        category_total = 0.0
        for row in snapshot.rows:
            if row.account_code in account_lookup:
                cat_key = account_lookup[row.account_code][0]
                if cat_key == category_key:
                    category_total += row.amount

        if category_total != 0.0:
            # Accumulate in case multiple snapshots cover parts of the same year
            year_totals[year] = year_totals.get(year, 0.0) + category_total

    return [
        YearlyTotal(year=y, total=round(t, 2))
        for y, t in sorted(year_totals.items())
    ]


def aggregate_category_by_month(
    snapshots: list[FinancialSnapshot],
    category_key: str,
    chart: ChartOfAccounts,
) -> list[MonthlyTotal]:
    """Aggregate a category's monthly totals from single-month snapshots.

    Only snapshots that cover a single month are included. Multi-month
    snapshots are excluded because distributing evenly would be misleading
    for trend analysis.
    """
    account_lookup = build_account_lookup(chart)

    # (year, month) -> total
    month_totals: dict[tuple[int, int], float] = {}

    for snapshot in snapshots:
        if not _snapshot_covers_single_month(snapshot):
            continue

        try:
            year = _extract_year(snapshot.from_date)
            month = _extract_month(snapshot.from_date)
        except ValueError:
            continue

        category_total = 0.0
        for row in snapshot.rows:
            if row.account_code in account_lookup:
                cat_key = account_lookup[row.account_code][0]
                if cat_key == category_key:
                    category_total += row.amount

        if category_total != 0.0:
            key = (year, month)
            month_totals[key] = month_totals.get(key, 0.0) + category_total

    return [
        MonthlyTotal(
            year=y,
            month=m,
            month_label=MONTH_LABELS[m - 1],
            total=round(t, 2),
        )
        for (y, m), t in sorted(month_totals.items())
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute_trend_data(
    category_key: str,
    compare_key: str | None = None,
    chart: ChartOfAccounts | None = None,
    snapshots_dir: Path | None = None,
    historical_dir: Path | None = None,
) -> TrendData:
    """Build complete trend data for one or two categories.

    Args:
        category_key: Primary category to chart.
        compare_key: Optional second category to overlay.
        chart: Chart of accounts (loaded from disk if None).
        snapshots_dir: Override for snapshot JSON directory.
        historical_dir: Override for historical CSV directory.

    Returns:
        TrendData with yearly (and optionally monthly) aggregations.
    """
    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return TrendData(
                primary_category=CategoryInfo(key=category_key, label=category_key, section="unknown"),
            )

    # Load all snapshots
    snapshots = load_all_snapshots_all_years(snapshots_dir, historical_dir, chart)

    if not snapshots:
        cat_info = _find_category_info(category_key, chart)
        return TrendData(primary_category=cat_info)

    # Find category metadata
    primary_info = _find_category_info(category_key, chart)
    compare_info = _find_category_info(compare_key, chart) if compare_key else None

    # Yearly aggregation
    primary_yearly = aggregate_category_by_year(snapshots, category_key, chart)
    compare_yearly = (
        aggregate_category_by_year(snapshots, compare_key, chart)
        if compare_key
        else []
    )

    # Monthly aggregation (only if single-month snapshots exist)
    primary_monthly = aggregate_category_by_month(snapshots, category_key, chart)
    compare_monthly = (
        aggregate_category_by_month(snapshots, compare_key, chart)
        if compare_key
        else []
    )

    # Determine available years
    all_years: set[int] = set()
    for yt in primary_yearly:
        all_years.add(yt.year)
    for yt in compare_yearly:
        all_years.add(yt.year)

    has_monthly = len(primary_monthly) > 0

    return TrendData(
        primary_category=primary_info,
        compare_category=compare_info,
        primary_yearly=primary_yearly,
        compare_yearly=compare_yearly,
        primary_monthly=primary_monthly,
        compare_monthly=compare_monthly,
        available_years=sorted(all_years),
        has_monthly=has_monthly,
        has_data=len(primary_yearly) > 0,
    )


def _find_category_info(category_key: str, chart: ChartOfAccounts) -> CategoryInfo:
    """Look up category metadata from the chart of accounts."""
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        if category_key in section_field:
            cat = section_field[category_key]
            return CategoryInfo(
                key=category_key,
                label=cat.budget_label,
                section=section_name,
            )

    return CategoryInfo(key=category_key, label=category_key, section="unknown")
