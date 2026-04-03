"""Historical data verification service (CHA-206).

Loads CSV-imported actuals and Xero API snapshots for the same period,
compares account-by-account, and flags discrepancies above configurable
thresholds.

The primary entry point is ``verify_year(year)``.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.csv_import import (
    build_account_lookup,
    load_chart_of_accounts,
    parse_csv,
)
from app.models import ChartOfAccounts, FinancialSnapshot, SnapshotRow
from app.models.verification import (
    AccountComparison,
    MatchStatus,
    VerificationResult,
)
from app.services.dashboard import CHART_PATH, SNAPSHOTS_DIR
from app.xero.snapshots import xero_snapshot_to_financial

# Historical CSV directory — parallel to data/snapshots/
HISTORICAL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "historical"

# Thresholds for match classification (absolute dollar amounts)
MATCH_THRESHOLD = 10.0       # <= $10 variance = match (green)
MINOR_THRESHOLD = 100.0      # <= $100 variance = minor (yellow)
# > $100 = major variance (red)


def _load_csv_actuals(
    year: int,
    chart: ChartOfAccounts,
    historical_dir: Path | None = None,
) -> tuple[dict[str, float], str]:
    """Load CSV actuals for a year from the historical directory.

    Looks for files matching patterns like ``sample_2023.csv``, ``2023.csv``,
    or any CSV whose first data column header contains the year string.

    Returns: (account_code -> total_amount, source_description)
    """
    search_dir = historical_dir or HISTORICAL_DIR
    if not search_dir.exists():
        return {}, ""

    # Try common filename patterns
    candidates = list(search_dir.glob(f"*{year}*.csv"))
    if not candidates:
        # Fallback: check all CSVs for year in header
        candidates = list(search_dir.glob("*.csv"))

    for csv_path in sorted(candidates):
        try:
            raw_bytes = csv_path.read_bytes()
            period_headers, rows, errors = parse_csv(raw_bytes, filename=csv_path.name)

            if errors or not rows:
                continue

            # Check if any period header contains the year
            year_str = str(year)
            matching_periods = [p for p in period_headers if year_str in p]
            if not matching_periods:
                continue

            # Sum amounts across all matching period columns for each account
            actuals: dict[str, float] = {}
            for row in rows:
                if row.account_code:
                    total = sum(
                        row.amounts.get(p, 0.0) for p in matching_periods
                    )
                    if total != 0.0:
                        actuals[row.account_code] = (
                            actuals.get(row.account_code, 0.0) + total
                        )

            if actuals:
                return actuals, csv_path.name

        except Exception:
            continue

    return {}, ""


def _load_snapshot_actuals(
    year: int,
    snapshots_dir: Path | None = None,
) -> tuple[dict[str, float], str]:
    """Load Xero snapshot actuals for a year.

    Scans all JSON snapshot files for P&L reports covering the given year.
    Aggregates amounts by account_code across all matching snapshots.

    Returns: (account_code -> total_amount, source_description)
    """
    search_dir = snapshots_dir or SNAPSHOTS_DIR
    if not search_dir.exists():
        return {}, ""

    year_str = str(year)
    actuals: dict[str, float] = {}
    sources: list[str] = []

    for json_path in sorted(search_dir.glob("pl_*.json")):
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))

            # Handle both wrapper and direct formats
            snapshot = None
            if "report_date" in raw:
                snapshot = FinancialSnapshot(**raw)
            elif "snapshot_metadata" in raw:
                resp = raw.get("response", {})
                if "report_date" in resp:
                    snapshot = FinancialSnapshot(**resp)
                else:
                    snapshot = xero_snapshot_to_financial(raw)

            if snapshot is None:
                continue

            # Check if this snapshot covers the requested year
            if not (snapshot.from_date.startswith(year_str)
                    or snapshot.to_date.startswith(year_str)):
                continue

            for row in snapshot.rows:
                if row.account_code:
                    actuals[row.account_code] = (
                        actuals.get(row.account_code, 0.0) + row.amount
                    )

            sources.append(json_path.name)

        except Exception:
            continue

    source_desc = ", ".join(sources) if sources else ""
    return actuals, source_desc


def _classify_variance(abs_variance: float) -> MatchStatus:
    """Classify a variance amount into a match status."""
    if abs_variance <= MATCH_THRESHOLD:
        return MatchStatus.MATCH
    elif abs_variance <= MINOR_THRESHOLD:
        return MatchStatus.MINOR_VARIANCE
    else:
        return MatchStatus.MAJOR_VARIANCE


def _build_account_name_lookup(chart: ChartOfAccounts) -> dict[str, str]:
    """Build {account_code: account_name} from chart of accounts."""
    names: dict[str, str] = {}
    for section_field in [chart.income, chart.expenses]:
        for cat in section_field.values():
            for acct in cat.accounts + cat.legacy_accounts + cat.property_costs:
                names[acct.code] = acct.name
    return names


def verify_year(
    year: int,
    *,
    chart: ChartOfAccounts | None = None,
    historical_dir: Path | None = None,
    snapshots_dir: Path | None = None,
) -> VerificationResult:
    """Compare CSV-imported actuals against Xero snapshots for a given year.

    Args:
        year: The calendar year to verify.
        chart: Chart of accounts (loaded from disk if None).
        historical_dir: Override historical CSV directory (for testing).
        snapshots_dir: Override snapshot directory (for testing).

    Returns:
        VerificationResult with account-by-account comparison.
    """
    # Load chart of accounts
    if chart is None:
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
        else:
            return VerificationResult(year=year)

    account_names = _build_account_name_lookup(chart)

    # Load data from both sources
    csv_actuals, csv_source = _load_csv_actuals(
        year, chart, historical_dir=historical_dir,
    )
    snapshot_actuals, snapshot_source = _load_snapshot_actuals(
        year, snapshots_dir=snapshots_dir,
    )

    has_csv = bool(csv_actuals)
    has_snapshot = bool(snapshot_actuals)

    # Collect all account codes from both sources
    all_codes = sorted(set(csv_actuals.keys()) | set(snapshot_actuals.keys()))

    comparisons: list[AccountComparison] = []

    for code in all_codes:
        csv_amt = csv_actuals.get(code)
        snap_amt = snapshot_actuals.get(code)
        name = account_names.get(code, f"Unknown ({code})")

        if csv_amt is not None and snap_amt is not None:
            # Both sources have data — compare
            variance = round(csv_amt - snap_amt, 2)
            abs_var = abs(variance)
            status = _classify_variance(abs_var)
        elif csv_amt is not None:
            # CSV only
            variance = round(csv_amt, 2)
            abs_var = abs(variance)
            status = MatchStatus.CSV_ONLY
        else:
            # Snapshot only
            variance = round(-(snap_amt or 0.0), 2)
            abs_var = abs(snap_amt or 0.0)
            status = MatchStatus.SNAPSHOT_ONLY

        comparisons.append(AccountComparison(
            account_code=code,
            account_name=name,
            csv_amount=round(csv_amt, 2) if csv_amt is not None else None,
            snapshot_amount=round(snap_amt, 2) if snap_amt is not None else None,
            variance=variance,
            abs_variance=round(abs_var, 2),
            status=status,
        ))

    return VerificationResult(
        year=year,
        csv_source=csv_source,
        snapshot_source=snapshot_source,
        comparisons=comparisons,
        has_csv_data=has_csv,
        has_snapshot_data=has_snapshot,
    )


def get_available_years(
    *,
    historical_dir: Path | None = None,
    snapshots_dir: Path | None = None,
) -> list[int]:
    """Return a sorted list of years for which verification data exists.

    Scans both the historical CSV directory and the snapshots directory
    for year references.
    """
    import re

    years: set[int] = set()

    # Scan historical CSVs for years in filenames
    hist_dir = historical_dir or HISTORICAL_DIR
    if hist_dir.exists():
        for csv_path in hist_dir.glob("*.csv"):
            matches = re.findall(r"20\d{2}", csv_path.stem)
            for m in matches:
                years.add(int(m))

    # Scan snapshot JSONs for years in from_date/to_date
    snap_dir = snapshots_dir or SNAPSHOTS_DIR
    if snap_dir.exists():
        for json_path in snap_dir.glob("*.json"):
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
                snapshot_data = None
                if "report_date" in raw:
                    snapshot_data = raw
                elif "snapshot_metadata" in raw:
                    meta = raw["snapshot_metadata"]
                    snapshot_data = {
                        "from_date": meta.get("from_date", ""),
                        "to_date": meta.get("to_date", ""),
                    }
                if snapshot_data:
                    for key in ("from_date", "to_date"):
                        val = snapshot_data.get(key, "")
                        if val:
                            matches = re.findall(r"20\d{2}", val)
                            for m in matches:
                                years.add(int(m))
            except Exception:
                continue

    return sorted(years)
