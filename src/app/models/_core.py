"""Pydantic models for the Church Budget Tool data layer.

Covers chart of accounts, CSV row data, and import results.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Chart of Accounts models (mirrors chart_of_accounts.yaml)
# ---------------------------------------------------------------------------

class Account(BaseModel):
    """A single Xero account code + name."""

    code: str
    name: str


class BudgetCategory(BaseModel):
    """One budget category containing current and legacy account mappings."""

    budget_label: str
    accounts: list[Account] = []
    legacy_accounts: list[Account] = []
    property_costs: list[Account] = []
    note: str | None = None


class ChartOfAccounts(BaseModel):
    """Top-level chart of accounts with income and expense categories."""

    income: dict[str, BudgetCategory] = {}
    expenses: dict[str, BudgetCategory] = {}


# ---------------------------------------------------------------------------
# CSV import data models
# ---------------------------------------------------------------------------

class CSVRow(BaseModel):
    """A single parsed row from a Xero P&L CSV export."""

    account_code: str | None = None
    account_name: str
    amounts: dict[str, float] = Field(
        default_factory=dict,
        description="Period label -> dollar amount, e.g. {'Jan-24': 1500.00}",
    )


class MappedRow(BaseModel):
    """A CSV row after mapping to a budget category."""

    account_code: str | None = None
    account_name: str
    category_key: str
    category_section: str  # "income" or "expenses"
    budget_label: str
    is_legacy: bool = False
    amounts: dict[str, float] = {}


class ImportIssue(BaseModel):
    """One validation / mapping error encountered during import.

    Named ImportIssue (not ImportError) to avoid shadowing the Python builtin.
    """

    row: int | None = None
    field: str | None = None
    message: str


class ImportResult(BaseModel):
    """Complete result of a CSV import attempt."""

    success: bool
    filename: str
    total_rows: int = 0
    mapped_rows: int = 0
    errors: list[ImportIssue] = []
    warnings: list[ImportIssue] = []
    rows: list[MappedRow] = []
    unrecognised_accounts: list[str] = []


# ---------------------------------------------------------------------------
# JSON snapshot model (shared between API pulls and CSV imports)
# ---------------------------------------------------------------------------

class SnapshotRow(BaseModel):
    """One line item in a financial snapshot."""

    account_code: str
    account_name: str
    amount: float


class FinancialSnapshot(BaseModel):
    """A point-in-time financial data snapshot stored as JSON."""

    report_date: str
    from_date: str
    to_date: str
    source: str  # "csv_import" or "xero_api"
    rows: list[SnapshotRow] = []
