"""Journal vs P&L reconciliation service (CHA-270).

Compares category totals from journal aggregation against P&L report
snapshots to identify discrepancies.  This validates the journal pipeline
and surfaces any accounts that are being dropped by either method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot
from app.services.dashboard import load_ytd_snapshot
from app.services.journal_aggregation import (
    AggregationResult,
    aggregate_ytd,
    aggregation_to_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"


@dataclass
class ReconciliationRow:
    """Comparison of a single budget category between two sources."""

    category_key: str
    budget_label: str
    section: str
    journal_amount: float
    snapshot_amount: float
    variance: float
    variance_pct: float | None  # None if both are zero
    status: str  # "match", "minor", "major", "journal_only", "snapshot_only"


@dataclass
class ReconciliationResult:
    """Complete reconciliation output."""

    rows: list[ReconciliationRow] = field(default_factory=list)
    total_journal_income: float = 0.0
    total_snapshot_income: float = 0.0
    total_journal_expenses: float = 0.0
    total_snapshot_expenses: float = 0.0
    match_count: int = 0
    minor_count: int = 0
    major_count: int = 0
    match_rate: float = 0.0
    has_data: bool = False
    error: str | None = None

    @property
    def income_rows(self) -> list[ReconciliationRow]:
        return [r for r in self.rows if r.section == "income"]

    @property
    def expense_rows(self) -> list[ReconciliationRow]:
        return [r for r in self.rows if r.section == "expenses"]


def _classify(variance: float, reference: float) -> str:
    """Classify a variance as match, minor, or major."""
    if abs(variance) < 0.01:
        return "match"
    if reference == 0:
        return "major"
    pct = abs(variance / reference * 100)
    if pct <= 1.0:
        return "match"
    if pct <= 5.0:
        return "minor"
    return "major"


def reconcile(
    chart: ChartOfAccounts | None = None,
    chart_path: Path | None = None,
    journals_dir: Path | None = None,
    snapshots_dir: Path | None = None,
    year: int | None = None,
) -> ReconciliationResult:
    """Compare journal-aggregated data against P&L snapshot data.

    Returns a ReconciliationResult with per-category comparisons.
    """
    if chart is None:
        cp = chart_path or CHART_PATH
        if not cp.exists():
            return ReconciliationResult(error="Chart of accounts not found")
        chart = load_chart_of_accounts(cp)

    # Load journal aggregation
    agg = aggregate_ytd(year=year, journals_dir=journals_dir, chart_path=chart_path)

    # Load P&L snapshot
    snapshot = load_ytd_snapshot(year=year, directory=snapshots_dir)

    if agg.journal_count == 0 and snapshot is None:
        return ReconciliationResult(error="No data available from either source")

    # Build category totals from journals
    journal_totals: dict[str, float] = {}
    journal_sections: dict[str, str] = {}
    journal_labels: dict[str, str] = {}
    for cat in agg.categories:
        journal_totals[cat.key] = cat.net_amount
        journal_sections[cat.key] = cat.section
        journal_labels[cat.key] = cat.budget_label

    # Build category totals from snapshots
    snapshot_totals: dict[str, float] = {}
    if snapshot:
        lookup = build_account_lookup(chart)
        for row in snapshot.rows:
            if row.account_code in lookup:
                cat_key = lookup[row.account_code][0]
                snapshot_totals[cat_key] = snapshot_totals.get(cat_key, 0) + row.amount

    # Get section/label metadata from chart
    cat_meta: dict[str, tuple[str, str]] = {}
    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for key, cat in section_field.items():
            cat_meta[key] = (cat.budget_label, section_name)

    # Compare
    all_keys = set(list(journal_totals.keys()) + list(snapshot_totals.keys()))
    rows: list[ReconciliationRow] = []
    match_count = 0
    minor_count = 0
    major_count = 0

    for key in sorted(all_keys, key=lambda k: cat_meta.get(k, ("", "z"))):
        if key not in cat_meta:
            continue

        label, section = cat_meta[key]
        j_amt = round(journal_totals.get(key, 0), 2)
        s_amt = round(snapshot_totals.get(key, 0), 2)
        variance = round(j_amt - s_amt, 2)
        reference = max(abs(j_amt), abs(s_amt))
        variance_pct = round(variance / reference * 100, 1) if reference > 0 else None

        if j_amt != 0 and s_amt == 0:
            status = "journal_only"
        elif j_amt == 0 and s_amt != 0:
            status = "snapshot_only"
        else:
            status = _classify(variance, reference)

        if status == "match":
            match_count += 1
        elif status == "minor":
            minor_count += 1
        else:
            major_count += 1

        rows.append(ReconciliationRow(
            category_key=key,
            budget_label=label,
            section=section,
            journal_amount=j_amt,
            snapshot_amount=s_amt,
            variance=variance,
            variance_pct=variance_pct,
            status=status,
        ))

    total = len(rows)
    match_rate = round(match_count / total * 100, 1) if total > 0 else 0.0

    return ReconciliationResult(
        rows=rows,
        total_journal_income=round(sum(r.journal_amount for r in rows if r.section == "income"), 2),
        total_snapshot_income=round(sum(r.snapshot_amount for r in rows if r.section == "income"), 2),
        total_journal_expenses=round(sum(r.journal_amount for r in rows if r.section == "expenses"), 2),
        total_snapshot_expenses=round(sum(r.snapshot_amount for r in rows if r.section == "expenses"), 2),
        match_count=match_count,
        minor_count=minor_count,
        major_count=major_count,
        match_rate=match_rate,
        has_data=total > 0,
    )
