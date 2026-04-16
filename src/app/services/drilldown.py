"""Report drill-down service — expandable categories with role-based detail (CHA-269).

Provides account-level and transaction-level detail for budget categories.
Visibility is controlled by user role:
- admin: full transaction detail (individual journal lines)
- board: account-level totals only (no individual transactions)
- staff: summary categories only (no drill-down)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts
from app.models.journal import JournalEntry, JournalLine
from app.services.pl_helpers import infer_pl_section, is_summary_row

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
JOURNALS_DIR = PROJECT_ROOT / "data" / "journals"
SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
CONFIG_DIR = PROJECT_ROOT / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"


@dataclass
class TransactionDetail:
    """A single transaction (journal line) for admin-level drill-down."""

    journal_date: str
    journal_number: str
    description: str
    amount: float
    reference: str = ""
    source_type: str = ""


@dataclass
class AccountDetail:
    """Account-level detail within a category — visible to board+admin."""

    code: str
    name: str
    net_amount: float
    transaction_count: int
    is_legacy: bool = False
    transactions: list[TransactionDetail] = field(default_factory=list)
    monthly_amounts: dict[str, float] = field(default_factory=dict)  # month_key -> amount


@dataclass
class CategoryDrilldown:
    """Complete drill-down for a single budget category."""

    category_key: str
    budget_label: str
    section: str
    net_amount: float
    accounts: list[AccountDetail] = field(default_factory=list)
    detail_level: str = "summary"  # "summary", "accounts", or "transactions"
    month_keys: list[str] = field(default_factory=list)
    month_labels: list[str] = field(default_factory=list)
    view_mode: str = "month"  # "month" or "ytd"

    @property
    def monthly_totals(self) -> dict[str, float]:
        """Sum of all account monthly_amounts per month_key, for footer."""
        totals: dict[str, float] = {}
        for mk in self.month_keys:
            totals[mk] = round(sum(a.monthly_amounts.get(mk, 0) for a in self.accounts), 2)
        return totals


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def get_category_drilldown(
    section: str,
    category_key: str,
    role: str = "staff",
    from_date: str | None = None,
    to_date: str | None = None,
    chart_path: Path | None = None,
    journals_dir: Path | None = None,
    year: int | None = None,
    end_month: int | None = None,
    view_mode: str = "month",
) -> CategoryDrilldown | None:
    """Get drill-down data for a budget category, filtered by role.

    Args:
        section: "income" or "expenses"
        category_key: The category key from chart_of_accounts.yaml
        role: User role — determines detail level
        from_date: Start date filter
        to_date: End date filter
        year: Financial year for monthly breakdown
        end_month: End month (1-12)
        view_mode: "ytd" or "month" — controls monthly column display

    Returns:
        CategoryDrilldown with appropriate detail level, or None if not found.
    """
    from app.services.journal_aggregation import load_journals

    cp = chart_path or CHART_PATH
    chart = load_chart_of_accounts(cp)
    lookup = build_account_lookup(chart)
    mapped_codes = set(lookup.keys())

    is_uncategorised = category_key.startswith("_uncategorised")

    # For normal categories, verify the key exists in the chart
    if not is_uncategorised:
        section_dict = getattr(chart, section, None)
        if section_dict is None or category_key not in section_dict:
            return None
        budget_label = section_dict[category_key].budget_label
    else:
        budget_label = "Uncategorised"

    today = date.today()
    if year is None:
        year = today.year
    if end_month is None:
        end_month = today.month

    from_date = from_date or f"{year}-01-01"
    to_date = to_date or f"{year}-{end_month:02d}-28"

    # Build month keys/labels for the view
    if view_mode == "ytd":
        month_keys = [_month_key(year, m) for m in range(1, end_month + 1)]
    else:
        month_keys = [_month_key(year, end_month)]
    month_labels = [MONTH_LABELS[int(mk.split("-")[1]) - 1] for mk in month_keys]

    # Determine detail level based on role
    if role == "admin":
        detail_level = "transactions"
    elif role == "board":
        detail_level = "accounts"
    else:
        detail_level = "summary"

    # For mapped categories, collect account codes that belong to this category
    cat_codes: set[str] = set()
    code_is_legacy: dict[str, bool] = {}
    if not is_uncategorised:
        for code, (ck, sec, label, is_legacy) in lookup.items():
            if ck == category_key:
                cat_codes.add(code)
                code_is_legacy[code] = is_legacy

    account_data: dict[str, AccountDetail] = {}

    # Try journal data first (has transaction-level detail), fall back to snapshots
    entries = load_journals(
        from_date=from_date,
        to_date=to_date,
        journals_dir=journals_dir or JOURNALS_DIR,
    )

    if entries:
        # Journal-based aggregation — full transaction detail + monthly breakdown
        for entry in entries:
            entry_mk = entry.journal_date[:7]  # "2026-03"
            for line in entry.lines:
                if is_uncategorised:
                    if line.account_code in mapped_codes:
                        continue
                    line_section = infer_pl_section(line.account_code, line.account_name)
                    expected_section = "income" if category_key == "_uncategorised_income" else "expenses"
                    if line_section != expected_section:
                        continue
                else:
                    if line.account_code not in cat_codes:
                        continue

                code = line.account_code
                if code not in account_data:
                    account_data[code] = AccountDetail(
                        code=code,
                        name=line.account_name,
                        net_amount=0.0,
                        transaction_count=0,
                        is_legacy=code_is_legacy.get(code, False),
                    )

                ad = account_data[code]
                ad.net_amount += line.net_amount
                ad.transaction_count += 1
                if entry_mk in month_keys:
                    ad.monthly_amounts[entry_mk] = ad.monthly_amounts.get(entry_mk, 0) + line.net_amount

                if detail_level == "transactions":
                    ad.transactions.append(TransactionDetail(
                        journal_date=entry.journal_date,
                        journal_number=entry.journal_number,
                        description=line.description,
                        amount=line.net_amount,
                        reference=entry.reference,
                        source_type=entry.source_type,
                    ))
    else:
        # Snapshot fallback — account-level totals with monthly breakdown
        from app.services.council_report import load_all_snapshots
        from datetime import datetime

        all_snapshots = load_all_snapshots(SNAPSHOTS_DIR)
        year_snapshots = [
            s for s in all_snapshots
            if s.from_date.startswith(str(year)) or s.to_date.startswith(str(year))
        ]

        for snapshot in year_snapshots:
            from_dt = datetime.strptime(snapshot.from_date, "%Y-%m-%d").date()
            to_dt = datetime.strptime(snapshot.to_date, "%Y-%m-%d").date()

            # Build covered months for this snapshot
            covered_months: list[str] = []
            current = from_dt.replace(day=1)
            while current <= to_dt:
                mk = _month_key(current.year, current.month)
                if mk in month_keys:
                    covered_months.append(mk)
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)

            if not covered_months:
                continue

            num_months_in_snapshot = max(1, (to_dt.year - from_dt.year) * 12 + to_dt.month - from_dt.month + 1)

            for row in snapshot.rows:
                if is_summary_row(row):
                    continue

                if is_uncategorised:
                    if row.account_code in mapped_codes:
                        continue
                    if row.amount == 0:
                        continue
                    row_section = infer_pl_section(row.account_code or "", row.account_name)
                    expected_section = "income" if category_key == "_uncategorised_income" else "expenses"
                    if row_section != expected_section:
                        continue
                else:
                    if row.account_code not in cat_codes:
                        continue

                code = row.account_code
                if code not in account_data:
                    account_data[code] = AccountDetail(
                        code=code,
                        name=row.account_name,
                        net_amount=0.0,
                        transaction_count=0,
                        is_legacy=code_is_legacy.get(code, False),
                    )

                ad = account_data[code]
                monthly_share = row.amount / num_months_in_snapshot
                for mk in covered_months:
                    ad.monthly_amounts[mk] = ad.monthly_amounts.get(mk, 0) + round(monthly_share, 2)
                ad.net_amount += sum(round(monthly_share, 2) for mk in covered_months)

    accounts = sorted(account_data.values(), key=lambda a: a.code)
    total = sum(a.net_amount for a in accounts)

    # For summary level, return no account details
    if detail_level == "summary":
        accounts = []

    return CategoryDrilldown(
        category_key=category_key,
        budget_label=budget_label,
        section=section,
        net_amount=round(total, 2),
        accounts=accounts,
        detail_level=detail_level,
        month_keys=month_keys if view_mode == "ytd" else [],
        month_labels=month_labels if view_mode == "ytd" else [],
        view_mode=view_mode,
    )
