"""Pydantic models for Xero Journal data (CHA-262).

Journals provide transaction-level detail — each JournalEntry contains
JournalLines with account codes, amounts, and tracking category tags.
This replaces the fuzzy name-matching approach used by P&L report snapshots.
"""

from __future__ import annotations

from pydantic import BaseModel


class TrackingTag(BaseModel):
    """A tracking category assignment on a journal line."""

    tracking_category_id: str
    tracking_category_name: str
    option_id: str
    option_name: str


class JournalLine(BaseModel):
    """A single debit/credit line within a journal entry."""

    journal_line_id: str
    account_id: str
    account_code: str
    account_name: str
    account_type: str  # e.g., "REVENUE", "EXPENSE", "BANK"
    net_amount: float
    gross_amount: float = 0.0
    tax_amount: float = 0.0
    description: str = ""
    tracking: list[TrackingTag] = []


class JournalEntry(BaseModel):
    """A complete journal entry from Xero.

    Each entry represents a balanced transaction (debits == credits)
    and contains one or more JournalLines.
    """

    journal_id: str
    journal_number: str
    journal_date: str  # ISO date YYYY-MM-DD
    source_id: str = ""
    source_type: str = ""  # e.g., "ACCREC", "ACCPAY", "MANJOURNAL"
    reference: str = ""
    lines: list[JournalLine] = []
    created_date_utc: str = ""
