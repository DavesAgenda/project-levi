"""Journal aggregation pipeline — build P&L from raw journal data (CHA-265).

Replaces the fuzzy name-matching P&L report approach with deterministic
account-code-based aggregation from Xero journal entries.

Key advantage: journal lines carry ``AccountCode`` directly, so mapping
is a simple dict lookup — no normalisation or name matching required.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts, FinancialSnapshot, SnapshotRow
from app.models.journal import JournalEntry, JournalLine

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
JOURNALS_DIR = PROJECT_ROOT / "data" / "journals"
CONFIG_DIR = PROJECT_ROOT / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AccountTotal:
    """Aggregated total for one account code."""

    code: str
    name: str
    account_type: str  # REVENUE, EXPENSE, etc.
    net_amount: float = 0.0
    transaction_count: int = 0
    category_key: str = ""
    section: str = ""
    budget_label: str = ""
    is_legacy: bool = False


@dataclass
class CategoryTotal:
    """Aggregated total for one budget category."""

    key: str
    section: str  # "income" or "expenses"
    budget_label: str
    net_amount: float = 0.0
    accounts: list[AccountTotal] = field(default_factory=list)

    @property
    def account_count(self) -> int:
        return len(self.accounts)


@dataclass
class TrackingBreakdown:
    """Amounts broken down by tracking category option."""

    category_name: str
    option_totals: dict[str, float] = field(default_factory=dict)
    # option_name -> net_amount


@dataclass
class AggregationResult:
    """Complete aggregation output."""

    from_date: str
    to_date: str
    total_income: float = 0.0
    total_expenses: float = 0.0
    net_position: float = 0.0
    categories: list[CategoryTotal] = field(default_factory=list)
    unmapped_accounts: list[AccountTotal] = field(default_factory=list)
    tracking_breakdown: dict[str, TrackingBreakdown] = field(default_factory=dict)
    journal_count: int = 0

    @property
    def income_categories(self) -> list[CategoryTotal]:
        return [c for c in self.categories if c.section == "income"]

    @property
    def expense_categories(self) -> list[CategoryTotal]:
        return [c for c in self.categories if c.section == "expenses"]


# ---------------------------------------------------------------------------
# Journal loading
# ---------------------------------------------------------------------------

def load_journals(
    from_date: str | None = None,
    to_date: str | None = None,
    journals_dir: Path | None = None,
) -> list[JournalEntry]:
    """Load journal entries from disk for the given date range.

    Reads JSON files from data/journals/{year}/{year}-{month}/journals.json.
    """
    jdir = journals_dir or JOURNALS_DIR
    if not jdir.exists():
        return []

    all_entries: list[JournalEntry] = []

    for json_path in sorted(jdir.rglob("journals.json")):
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            entries = [JournalEntry(**e) for e in raw]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Skipping %s: %s", json_path, exc)
            continue

        for entry in entries:
            if from_date and entry.journal_date < from_date:
                continue
            if to_date and entry.journal_date > to_date:
                continue
            all_entries.append(entry)

    return all_entries


# ---------------------------------------------------------------------------
# Aggregation engine
# ---------------------------------------------------------------------------

def aggregate_journals(
    entries: list[JournalEntry],
    chart: ChartOfAccounts | None = None,
    chart_path: Path | None = None,
    from_date: str = "",
    to_date: str = "",
) -> AggregationResult:
    """Aggregate journal entries into category totals.

    Uses deterministic account-code lookup (no name matching).
    Returns categorised totals plus unmapped accounts and tracking breakdown.
    """
    if chart is None:
        cp = chart_path or CHART_PATH
        chart = load_chart_of_accounts(cp)

    lookup = build_account_lookup(chart)

    # Aggregate by account code
    account_totals: dict[str, AccountTotal] = {}
    tracking_data: dict[str, dict[str, float]] = {}  # tc_name -> {option -> amount}

    for entry in entries:
        for line in entry.lines:
            code = line.account_code
            if code not in account_totals:
                account_totals[code] = AccountTotal(
                    code=code,
                    name=line.account_name,
                    account_type=line.account_type,
                )
            at = account_totals[code]
            at.net_amount += line.net_amount
            at.transaction_count += 1

            # Tracking categories
            for tag in line.tracking:
                tc_name = tag.tracking_category_name
                if tc_name not in tracking_data:
                    tracking_data[tc_name] = {}
                opt = tag.option_name
                tracking_data[tc_name][opt] = tracking_data[tc_name].get(opt, 0) + line.net_amount

    # Map accounts to categories
    mapped: dict[str, CategoryTotal] = {}
    unmapped: list[AccountTotal] = []

    for code, at in account_totals.items():
        if code in lookup:
            cat_key, section, label, is_legacy = lookup[code]
            at.category_key = cat_key
            at.section = section
            at.budget_label = label
            at.is_legacy = is_legacy

            if cat_key not in mapped:
                mapped[cat_key] = CategoryTotal(
                    key=cat_key,
                    section=section,
                    budget_label=label,
                )
            mapped[cat_key].net_amount += at.net_amount
            mapped[cat_key].accounts.append(at)
        else:
            # Skip bank/equity accounts (not in P&L)
            if at.account_type not in ("BANK", "EQUITY", "LIABILITY", "CURRLIAB", "TERMLIAB", "FIXED", "CURRENT"):
                unmapped.append(at)

    # Sort categories by budget_label (matching dashboard behaviour)
    categories = sorted(mapped.values(), key=lambda c: (0 if c.section == "income" else 1, c.budget_label))

    # Compute totals
    total_income = sum(c.net_amount for c in categories if c.section == "income")
    total_expenses = sum(c.net_amount for c in categories if c.section == "expenses")

    # Build tracking breakdown
    tracking_breakdown = {
        name: TrackingBreakdown(category_name=name, option_totals=opts)
        for name, opts in tracking_data.items()
    }

    return AggregationResult(
        from_date=from_date,
        to_date=to_date,
        total_income=round(total_income, 2),
        total_expenses=round(total_expenses, 2),
        net_position=round(total_income + total_expenses, 2),  # expenses already negative
        categories=categories,
        unmapped_accounts=unmapped,
        tracking_breakdown=tracking_breakdown,
        journal_count=len(entries),
    )


# ---------------------------------------------------------------------------
# Snapshot conversion — produce FinancialSnapshot for dashboard compatibility
# ---------------------------------------------------------------------------

def aggregation_to_snapshot(result: AggregationResult) -> FinancialSnapshot:
    """Convert an AggregationResult to a FinancialSnapshot.

    This makes journal-aggregated data drop-in compatible with the
    existing dashboard service which consumes FinancialSnapshots.
    """
    rows: list[SnapshotRow] = []
    for cat in result.categories:
        for acct in cat.accounts:
            if acct.net_amount != 0:
                rows.append(SnapshotRow(
                    account_code=acct.code,
                    account_name=acct.name,
                    amount=round(acct.net_amount, 2),
                ))

    return FinancialSnapshot(
        report_date=result.to_date or date.today().isoformat(),
        from_date=result.from_date or f"{date.today().year}-01-01",
        to_date=result.to_date or date.today().isoformat(),
        source="journal_aggregation",
        rows=rows,
    )


# ---------------------------------------------------------------------------
# High-level convenience functions
# ---------------------------------------------------------------------------

def aggregate_ytd(
    year: int | None = None,
    journals_dir: Path | None = None,
    chart_path: Path | None = None,
) -> AggregationResult:
    """Aggregate all journals for the current year-to-date."""
    today = date.today()
    year = year or today.year
    from_date = f"{year}-01-01"
    to_date = today.isoformat()

    entries = load_journals(from_date=from_date, to_date=to_date, journals_dir=journals_dir)
    return aggregate_journals(
        entries,
        chart_path=chart_path,
        from_date=from_date,
        to_date=to_date,
    )


def aggregate_month(
    year: int,
    month: int,
    journals_dir: Path | None = None,
    chart_path: Path | None = None,
) -> AggregationResult:
    """Aggregate journals for a specific month."""
    import calendar
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])

    entries = load_journals(
        from_date=first.isoformat(),
        to_date=last.isoformat(),
        journals_dir=journals_dir,
    )
    return aggregate_journals(
        entries,
        chart_path=chart_path,
        from_date=first.isoformat(),
        to_date=last.isoformat(),
    )
