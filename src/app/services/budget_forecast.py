"""Budget Forecast service — annualize year-to-date actuals for reference columns.

Provides forecasted full-year figures by annualizing actuals:
  forecast = (year_to_date_actuals / months_elapsed) * 12

Used in the budget editor to show {Y-1} Forecast alongside the draft budget.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts
from app.services.budget import BUDGETS_DIR, CHART_PATH
from app.services.council_report import SNAPSHOTS_DIR, load_all_snapshots


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _months_elapsed(year: int, reference_date: date | None = None) -> int:
    """Return the number of months elapsed for a given year.

    For past years (year < reference year), returns 12.
    For the current year, returns the current month number (1-12).
    For future years, returns 0 (no actuals possible).
    """
    ref = reference_date or date.today()
    if year < ref.year:
        return 12
    if year == ref.year:
        return ref.month
    return 0


def _load_actuals_by_section_key(
    year: int,
    chart: ChartOfAccounts,
    snapshots_dir: Path | None = None,
) -> dict[str, dict[str, float]]:
    """Load all snapshots for a given year and aggregate by section/item key.

    Returns nested dict: {section_type: {section_key: {item_key: amount}}}.
    This matches the budget YAML structure so forecast values align with
    budget line items.
    """
    account_lookup = build_account_lookup(chart)
    snapshots = load_all_snapshots(snapshots_dir)

    # Filter snapshots to the requested year
    year_snapshots = [
        s for s in snapshots
        if s.from_date.startswith(str(year))
    ]

    # Build reverse lookup: account_code -> (section_type, cat_key)
    section_map: dict[str, tuple[str, str]] = {}
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            for acc in cat.accounts:
                section_map[acc.code] = (section_name, cat_key)

    # Aggregate actuals by category key
    category_totals: dict[str, float] = {}
    for snap in year_snapshots:
        for row in snap.rows:
            if row.account_code in account_lookup:
                cat_key = account_lookup[row.account_code][0]
                category_totals[cat_key] = category_totals.get(cat_key, 0) + row.amount

    return category_totals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_forecast(
    year: int,
    *,
    reference_date: date | None = None,
    chart: ChartOfAccounts | None = None,
    chart_path: Path | None = None,
    snapshots_dir: Path | None = None,
) -> dict[str, float]:
    """Compute forecasted full-year amounts per budget category.

    Formula: (year_to_date_actuals / months_elapsed) * 12

    Args:
        year: The year to forecast (e.g. 2026).
        reference_date: Override "today" for testing.
        chart: Chart of accounts (loaded from disk if None).
        chart_path: Path to chart_of_accounts.yaml.
        snapshots_dir: Override snapshot directory for testing.

    Returns:
        Dict mapping category_key -> forecasted annual amount.
        Returns empty dict if no data or months_elapsed is 0.
    """
    if not (2000 <= year <= 2100):
        return {}

    cp = chart_path or CHART_PATH
    if chart is None:
        if not cp.exists():
            return {}
        chart = load_chart_of_accounts(cp)

    months = _months_elapsed(year, reference_date)
    if months == 0:
        return {}

    actuals = _load_actuals_by_section_key(year, chart, snapshots_dir)

    if months >= 12:
        # Full year — just return actuals as-is
        return {k: round(v, 2) for k, v in actuals.items()}

    # Annualize partial-year actuals
    forecast: dict[str, float] = {}
    for cat_key, amount in actuals.items():
        forecast[cat_key] = round((amount / months) * 12, 2)

    return forecast


def list_budget_years(
    *,
    budgets_dir: Path | None = None,
) -> list[dict]:
    """List all available budget years with metadata.

    Returns list of dicts sorted by year descending:
      [{"year": 2027, "status": "draft", "label": "2027-draft"}, ...]
    """
    import yaml
    bdir = budgets_dir or BUDGETS_DIR
    if not bdir.exists():
        return []

    results = []
    for path in sorted(bdir.glob("*.yaml"), reverse=True):
        if path.stem.startswith(".") or path.stem == "history":
            continue
        try:
            year = int(path.stem)
            if not (2000 <= year <= 2100):
                continue
        except ValueError:
            continue

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            status = raw.get("status", "draft")
            label = f"{year}" if status == "approved" else f"{year}-{status}"
            results.append({
                "year": year,
                "status": status,
                "label": label,
            })
        except Exception:
            continue

    return results
