"""Tracking category matrix service — budget categories x tracking options.

Fetches a P&L report broken down by a Xero tracking category (e.g.,
"Congregations" or "Ministry & Funds") and maps account rows to budget
categories, producing a matrix suitable for table rendering.

Key design:
- Tracking categories are discovered dynamically (never hardcoded)
- A single Xero P&L call with trackingCategoryID returns ALL options as columns
- Account-to-budget mapping reuses build_account_lookup() from csv_import
- Supports snapshot fallback when Xero API is unavailable
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts
from app.xero.client import fetch_profit_and_loss, fetch_tracking_categories
from app.xero.parser import parse_report, ParsedReport
from app.xero.snapshots import SNAPSHOTS_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrackingOption:
    """A single option within a tracking category."""

    option_id: str
    name: str
    status: str = "ACTIVE"


@dataclass
class TrackingCategory:
    """A Xero tracking category with its options."""

    category_id: str
    name: str
    status: str = "ACTIVE"
    options: list[TrackingOption] = field(default_factory=list)


@dataclass
class MatrixRow:
    """One row in the tracking matrix — a budget category with per-option amounts."""

    budget_label: str
    category_key: str
    section: str  # "income" or "expenses"
    values: dict[str, Decimal] = field(default_factory=dict)  # option_name -> amount
    total: Decimal = Decimal("0")


@dataclass
class TrackingMatrixData:
    """Complete matrix data ready for template rendering."""

    tracking_category: TrackingCategory | None = None
    column_headers: list[str] = field(default_factory=list)  # tracking option names
    income_rows: list[MatrixRow] = field(default_factory=list)
    expense_rows: list[MatrixRow] = field(default_factory=list)
    income_totals: dict[str, Decimal] = field(default_factory=dict)
    expense_totals: dict[str, Decimal] = field(default_factory=dict)
    income_grand_total: Decimal = Decimal("0")
    expense_grand_total: Decimal = Decimal("0")
    net_position: dict[str, Decimal] = field(default_factory=dict)
    net_grand_total: Decimal = Decimal("0")
    has_data: bool = False
    from_date: str = ""
    to_date: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# Discover tracking categories
# ---------------------------------------------------------------------------

async def discover_tracking_categories(
    *,
    snapshot_dir: Path | None = None,
) -> list[TrackingCategory]:
    """Fetch tracking categories from Xero API, falling back to snapshot.

    Returns a list of TrackingCategory dataclasses with their options.
    """
    raw: dict[str, Any] | None = None

    # Try live API first
    try:
        raw = await fetch_tracking_categories()
    except Exception:
        logger.info("Xero API unavailable — falling back to snapshot for tracking categories")

    # Fallback to snapshot
    if raw is None:
        raw = _load_tracking_categories_snapshot(snapshot_dir)

    if raw is None:
        return []

    return _parse_tracking_categories(raw)


def _load_tracking_categories_snapshot(
    snapshot_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Load tracking categories from the most recent snapshot file."""
    snap_dir = snapshot_dir or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return None

    # Look for tracking_categories*.json
    candidates = sorted(
        snap_dir.glob("tracking_categories*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Handle snapshot wrapper format
            if "response" in data:
                return data["response"]
            if "TrackingCategories" in data:
                return data
        except (json.JSONDecodeError, KeyError):
            continue

    return None


def _parse_tracking_categories(raw: dict[str, Any]) -> list[TrackingCategory]:
    """Parse raw Xero tracking categories response into dataclasses."""
    categories: list[TrackingCategory] = []
    for cat in raw.get("TrackingCategories", []):
        options = [
            TrackingOption(
                option_id=opt.get("TrackingOptionID", ""),
                name=opt.get("Name", ""),
                status=opt.get("Status", "ACTIVE"),
            )
            for opt in cat.get("Options", [])
        ]
        categories.append(TrackingCategory(
            category_id=cat.get("TrackingCategoryID", ""),
            name=cat.get("Name", ""),
            status=cat.get("Status", "ACTIVE"),
            options=options,
        ))
    return categories


# ---------------------------------------------------------------------------
# Compute tracking matrix
# ---------------------------------------------------------------------------

async def compute_tracking_matrix(
    tracking_category_id: str,
    from_date: str,
    to_date: str,
    chart: ChartOfAccounts | None = None,
    *,
    snapshot_dir: Path | None = None,
) -> TrackingMatrixData:
    """Fetch P&L with tracking breakdown and build the matrix.

    Makes a SINGLE Xero API call with trackingCategoryID — the response
    contains all tracking options as column headers automatically.
    """
    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return TrackingMatrixData(error="Chart of accounts not found")

    # Discover tracking categories to get the selected one's metadata
    categories = await discover_tracking_categories(snapshot_dir=snapshot_dir)
    selected_cat = next(
        (c for c in categories if c.category_id == tracking_category_id),
        None,
    )

    # Fetch P&L with tracking breakdown — single API call
    raw: dict[str, Any] | None = None
    try:
        raw = await fetch_profit_and_loss(
            from_date=from_date,
            to_date=to_date,
            tracking_category_id=tracking_category_id,
        )
    except Exception:
        logger.info("Xero API unavailable — falling back to snapshot for tracking P&L")

    # Fallback to snapshot — pass category name to load the right file
    if raw is None:
        cat_name = selected_cat.name if selected_cat else None
        raw = _load_tracking_pl_snapshot(
            from_date, to_date,
            tracking_category_name=cat_name,
            snapshot_dir=snapshot_dir,
        )

    if raw is None:
        return TrackingMatrixData(
            tracking_category=selected_cat,
            from_date=from_date,
            to_date=to_date,
            error="No data available. Connect to Xero or ensure snapshots exist.",
        )

    # Parse the Xero report — column headers will be tracking option names
    parsed = parse_report(raw)

    return _build_matrix(
        parsed=parsed,
        chart=chart,
        selected_cat=selected_cat,
        from_date=from_date,
        to_date=to_date,
    )


def _load_tracking_pl_snapshot(
    from_date: str,
    to_date: str,
    *,
    tracking_category_name: str | None = None,
    snapshot_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Load a tracking P&L snapshot from disk.

    Searches for a snapshot matching the tracking category name slug.
    Falls back to any ``pl_*_by-*.json`` if no specific match is found.
    """
    import re as _re

    snap_dir = snapshot_dir or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return None

    # Build a slug for the requested category (e.g. "Congregations" -> "congregations")
    if tracking_category_name:
        slug = _re.sub(r"[^a-z0-9]+", "-", tracking_category_name.lower()).strip("-")
        # Try exact match first
        pattern = f"pl_*_by-{slug}.json"
        candidates = sorted(
            snap_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if "response" in data:
                    return data["response"]
                if "Reports" in data:
                    return data
            except (json.JSONDecodeError, KeyError):
                continue

    # Fallback: any tracking P&L snapshot (legacy "by-ministry" or other)
    candidates = sorted(
        snap_dir.glob("pl_*_by-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "response" in data:
                return data["response"]
            if "Reports" in data:
                return data
        except (json.JSONDecodeError, KeyError):
            continue

    return None


def _build_matrix(
    parsed: ParsedReport,
    chart: ChartOfAccounts,
    selected_cat: TrackingCategory | None,
    from_date: str,
    to_date: str,
) -> TrackingMatrixData:
    """Build the matrix from a parsed P&L report with tracking columns."""
    account_lookup = build_account_lookup(chart)
    name_lookup = _build_name_lookup(chart)
    column_headers = [h for h in parsed.column_headers if h != "Total"]  # exclude Xero's computed Total
    has_xero_total = "Total" in parsed.column_headers

    # Aggregate amounts by (budget_label, category_key, section) x column
    aggregated: dict[str, MatrixRow] = {}

    for section in parsed.sections:
        for row in section.rows:
            # Match account to budget category by account_id (UUID)
            match = None

            # Try matching by account_id UUID — walk the chart to find it
            if row.account_id:
                match = _find_by_uuid(row.account_id, chart, account_lookup)

            # Fallback: try matching account_name to codes/names in the lookup
            if match is None:
                match = _find_by_name(row.account_name, account_lookup, name_lookup)

            if match is None:
                # Unknown account — skip it
                continue

            cat_key, section_name, budget_label, _is_legacy = match

            if cat_key not in aggregated:
                aggregated[cat_key] = MatrixRow(
                    budget_label=budget_label,
                    category_key=cat_key,
                    section=section_name,
                    values={h: Decimal("0") for h in column_headers},
                    total=Decimal("0"),
                )

            matrix_row = aggregated[cat_key]
            for header in column_headers:
                amount = row.values.get(header, Decimal("0"))
                matrix_row.values[header] += amount
            # Use Xero's pre-computed Total when available — it includes
            # amounts from accounts with no tracking option assigned, which
            # would otherwise be lost when we exclude the "Total" column.
            if has_xero_total:
                matrix_row.total += row.values.get("Total", Decimal("0"))
            else:
                matrix_row.total += sum(
                    row.values.get(h, Decimal("0")) for h in column_headers
                )

    # Split into income and expense rows, sorted by (section, label)
    all_rows = sorted(
        aggregated.values(),
        key=lambda r: (0 if r.section == "income" else 1, r.budget_label),
    )

    income_rows = [r for r in all_rows if r.section == "income"]
    expense_rows = [r for r in all_rows if r.section == "expenses"]

    # Compute column totals
    income_totals: dict[str, Decimal] = {h: Decimal("0") for h in column_headers}
    expense_totals: dict[str, Decimal] = {h: Decimal("0") for h in column_headers}
    income_grand_total = Decimal("0")
    expense_grand_total = Decimal("0")

    for row in income_rows:
        for h in column_headers:
            income_totals[h] += row.values.get(h, Decimal("0"))
        income_grand_total += row.total

    for row in expense_rows:
        for h in column_headers:
            expense_totals[h] += row.values.get(h, Decimal("0"))
        expense_grand_total += row.total

    # Net position per column
    net_position: dict[str, Decimal] = {}
    for h in column_headers:
        net_position[h] = income_totals.get(h, Decimal("0")) - expense_totals.get(h, Decimal("0"))
    net_grand_total = income_grand_total - expense_grand_total

    return TrackingMatrixData(
        tracking_category=selected_cat,
        column_headers=column_headers,
        income_rows=income_rows,
        expense_rows=expense_rows,
        income_totals=income_totals,
        expense_totals=expense_totals,
        income_grand_total=income_grand_total,
        expense_grand_total=expense_grand_total,
        net_position=net_position,
        net_grand_total=net_grand_total,
        has_data=len(income_rows) > 0 or len(expense_rows) > 0,
        from_date=from_date,
        to_date=to_date,
    )


def _find_by_uuid(
    account_id: str,
    chart: ChartOfAccounts,
    account_lookup: dict[str, tuple[str, str, str, bool]],
) -> tuple[str, str, str, bool] | None:
    """Find a budget category match by Xero account UUID.

    The chart of accounts uses account codes, not UUIDs, so this is a
    best-effort fallback — returns None if no match is found.
    """
    # UUIDs are not stored in the chart YAML, so this path currently
    # doesn't match.  Included for future extensibility.
    return None


def compute_tracking_matrix_from_journals(
    tracking_category_name: str,
    from_date: str,
    to_date: str,
    chart: ChartOfAccounts | None = None,
    *,
    journals_dir: Path | None = None,
) -> TrackingMatrixData:
    """Build tracking matrix from journal data instead of P&L reports (CHA-267).

    Journal lines carry TrackingCategories directly — just group by option name.
    This replaces the broken P&L report approach with deterministic aggregation.
    """
    from app.services.journal_aggregation import load_journals

    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return TrackingMatrixData(error="Chart of accounts not found")

    account_lookup = build_account_lookup(chart)
    entries = load_journals(from_date=from_date, to_date=to_date, journals_dir=journals_dir)

    if not entries:
        return TrackingMatrixData(
            from_date=from_date,
            to_date=to_date,
            error="No journal data available for this period.",
        )

    # Collect all option names for the requested tracking category
    option_names: set[str] = set()
    # Aggregate: (category_key) -> {option_name -> amount}
    cat_option_amounts: dict[str, dict[str, Decimal]] = {}

    for entry in entries:
        for line in entry.lines:
            code = line.account_code
            if code not in account_lookup:
                continue

            cat_key, section, budget_label, is_legacy = account_lookup[code]

            for tag in line.tracking:
                if tag.tracking_category_name != tracking_category_name:
                    continue

                opt = tag.option_name
                option_names.add(opt)

                if cat_key not in cat_option_amounts:
                    cat_option_amounts[cat_key] = {}
                cat_option_amounts[cat_key][opt] = (
                    cat_option_amounts[cat_key].get(opt, Decimal("0"))
                    + Decimal(str(line.net_amount))
                )

    if not option_names:
        return TrackingMatrixData(
            from_date=from_date,
            to_date=to_date,
            error=f"No journal lines found with tracking category '{tracking_category_name}'.",
        )

    column_headers = sorted(option_names)

    # Build matrix rows
    aggregated: dict[str, MatrixRow] = {}
    # Get category metadata
    cat_meta: dict[str, tuple[str, str]] = {}
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for k, cat in section_field.items():
            cat_meta[k] = (cat.budget_label, section_name)

    for cat_key, opts in cat_option_amounts.items():
        if cat_key not in cat_meta:
            continue
        label, section = cat_meta[cat_key]
        row = MatrixRow(
            budget_label=label,
            category_key=cat_key,
            section=section,
            values={h: opts.get(h, Decimal("0")) for h in column_headers},
            total=sum(opts.values(), Decimal("0")),
        )
        aggregated[cat_key] = row

    all_rows = sorted(
        aggregated.values(),
        key=lambda r: (0 if r.section == "income" else 1, r.budget_label),
    )

    income_rows = [r for r in all_rows if r.section == "income"]
    expense_rows = [r for r in all_rows if r.section == "expenses"]

    # Column totals
    income_totals: dict[str, Decimal] = {h: Decimal("0") for h in column_headers}
    expense_totals: dict[str, Decimal] = {h: Decimal("0") for h in column_headers}
    income_grand_total = Decimal("0")
    expense_grand_total = Decimal("0")

    for row in income_rows:
        for h in column_headers:
            income_totals[h] += row.values.get(h, Decimal("0"))
        income_grand_total += row.total

    for row in expense_rows:
        for h in column_headers:
            expense_totals[h] += row.values.get(h, Decimal("0"))
        expense_grand_total += row.total

    net_position: dict[str, Decimal] = {}
    for h in column_headers:
        net_position[h] = income_totals.get(h, Decimal("0")) - expense_totals.get(h, Decimal("0"))
    net_grand_total = income_grand_total - expense_grand_total

    return TrackingMatrixData(
        tracking_category=None,
        column_headers=column_headers,
        income_rows=income_rows,
        expense_rows=expense_rows,
        income_totals=income_totals,
        expense_totals=expense_totals,
        income_grand_total=income_grand_total,
        expense_grand_total=expense_grand_total,
        net_position=net_position,
        net_grand_total=net_grand_total,
        has_data=len(income_rows) > 0 or len(expense_rows) > 0,
        from_date=from_date,
        to_date=to_date,
    )


def _build_name_lookup(
    chart: ChartOfAccounts,
) -> dict[str, tuple[str, str, str, bool]]:
    """Build {account_name_lower: (cat_key, section, label, is_legacy)} from chart.

    Xero P&L report rows contain account names without codes, so we need
    to match on name directly.
    """
    name_map: dict[str, tuple[str, str, str, bool]] = {}
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            for acct in cat.accounts:
                name_map[acct.name.lower().strip()] = (cat_key, section_name, cat.budget_label, False)
            for acct in cat.legacy_accounts:
                name_map[acct.name.lower().strip()] = (cat_key, section_name, cat.budget_label, True)
            for acct in cat.property_costs:
                name_map[acct.name.lower().strip()] = (cat_key, section_name, cat.budget_label, False)
    return name_map


def _find_by_name(
    account_name: str,
    account_lookup: dict[str, tuple[str, str, str, bool]],
    name_lookup: dict[str, tuple[str, str, str, bool]] | None = None,
) -> tuple[str, str, str, bool] | None:
    """Try to match an account name to a budget category.

    Xero report rows show "Code - Name" or just "Name".  Try to extract
    the account code from the name string, then fall back to name matching.
    """
    import re

    # Try "12345 - Account Name" pattern
    m = re.match(r"^(\d{3,6})\s*[-\u2013]\s*", account_name)
    if m:
        code = m.group(1)
        if code in account_lookup:
            return account_lookup[code]

    # Try bare code
    stripped = account_name.strip()
    if stripped in account_lookup:
        return account_lookup[stripped]

    # Fall back to name-based matching
    if name_lookup:
        return name_lookup.get(stripped.lower())

    return None
