"""Shared helpers for P&L account classification (CHA-276).

Used by dashboard, council report, AGM report, and trend explorer
to handle unmapped accounts consistently across all P&L surfaces.
"""

from __future__ import annotations

from app.models import SnapshotRow

# Xero P&L summary/total row names that are not real accounts
_XERO_SUMMARY_NAMES = frozenset({
    "gross profit", "net profit", "total income", "total expenses",
    "total operating expenses", "total operating income",
    "total cost of sales", "total revenue",
})


def is_summary_row(row: SnapshotRow) -> bool:
    """Return True for Xero P&L summary/total rows that aren't real accounts."""
    if row.account_code:
        return False  # Has a real account code — not a summary row
    name_lower = row.account_name.lower().strip()
    if name_lower in _XERO_SUMMARY_NAMES:
        return True
    if name_lower.startswith("total "):
        return True
    return False


def infer_pl_section(code: str, name: str) -> str:
    """Infer whether an unmapped P&L account is income or expenses.

    Uses the Xero account code prefix convention:
    - Codes < 40000 -> income (1xxxx revenue, 2xxxx property, 3xxxx ministry)
    - Codes >= 40000 -> expenses (4xxxx staff/admin, 8xxxx property costs)
    Falls back to name-based heuristics.
    """
    if code:
        try:
            code_num = int(code[:2])
            return "income" if code_num < 40 else "expenses"
        except (ValueError, IndexError):
            pass
    # Fallback: keyword match on account name
    lower = name.lower()
    income_kw = ("income", "revenue", "rent", "offering", "offertory",
                 "donation", "grant", "hire", "interest", "thanksgiving")
    if any(kw in lower for kw in income_kw):
        return "income"
    return "expenses"
