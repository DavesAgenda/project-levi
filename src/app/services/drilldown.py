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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
JOURNALS_DIR = PROJECT_ROOT / "data" / "journals"
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


@dataclass
class CategoryDrilldown:
    """Complete drill-down for a single budget category."""

    category_key: str
    budget_label: str
    section: str
    net_amount: float
    accounts: list[AccountDetail] = field(default_factory=list)
    detail_level: str = "summary"  # "summary", "accounts", or "transactions"


def get_category_drilldown(
    section: str,
    category_key: str,
    role: str = "staff",
    from_date: str | None = None,
    to_date: str | None = None,
    chart_path: Path | None = None,
    journals_dir: Path | None = None,
) -> CategoryDrilldown | None:
    """Get drill-down data for a budget category, filtered by role.

    Args:
        section: "income" or "expenses"
        category_key: The category key from chart_of_accounts.yaml
        role: User role — determines detail level
        from_date: Start date filter
        to_date: End date filter

    Returns:
        CategoryDrilldown with appropriate detail level, or None if not found.
    """
    from app.services.journal_aggregation import load_journals

    cp = chart_path or CHART_PATH
    chart = load_chart_of_accounts(cp)
    lookup = build_account_lookup(chart)

    # Find the category metadata
    section_dict = getattr(chart, section, None)
    if section_dict is None or category_key not in section_dict:
        return None

    cat = section_dict[category_key]
    today = date.today()
    from_date = from_date or f"{today.year}-01-01"
    to_date = to_date or today.isoformat()

    entries = load_journals(
        from_date=from_date,
        to_date=to_date,
        journals_dir=journals_dir or JOURNALS_DIR,
    )

    # Determine detail level based on role
    if role == "admin":
        detail_level = "transactions"
    elif role == "board":
        detail_level = "accounts"
    else:
        detail_level = "summary"

    # Collect account codes that belong to this category
    cat_codes: set[str] = set()
    code_is_legacy: dict[str, bool] = {}
    for code, (ck, sec, label, is_legacy) in lookup.items():
        if ck == category_key:
            cat_codes.add(code)
            code_is_legacy[code] = is_legacy

    # Aggregate by account code
    account_data: dict[str, AccountDetail] = {}
    for entry in entries:
        for line in entry.lines:
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

            # Only collect transactions for admin
            if detail_level == "transactions":
                ad.transactions.append(TransactionDetail(
                    journal_date=entry.journal_date,
                    journal_number=entry.journal_number,
                    description=line.description,
                    amount=line.net_amount,
                    reference=entry.reference,
                    source_type=entry.source_type,
                ))

    accounts = sorted(account_data.values(), key=lambda a: a.code)
    total = sum(a.net_amount for a in accounts)

    # For summary level, return no account details
    if detail_level == "summary":
        accounts = []

    return CategoryDrilldown(
        category_key=category_key,
        budget_label=cat.budget_label,
        section=section,
        net_amount=round(total, 2),
        accounts=accounts,
        detail_level=detail_level,
    )
