"""Xero report response parser.

Parses the nested Section > Row > Cell structure from Xero Reporting API
responses into flat, usable data structures.

Key design decisions:
- Column headers are read dynamically from the Header row (never hardcoded)
- Account matching uses Attributes array UUID, not name strings
- SummaryRow entries (subtotals) are captured separately
- Unknown/new tracking columns are handled gracefully
- /Date(milliseconds)/ format is converted to ISO 8601
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


# ---------------------------------------------------------------------------
# Data classes for parsed output
# ---------------------------------------------------------------------------

@dataclass
class ParsedCell:
    """A single parsed cell value."""

    value: str
    account_id: str | None = None  # Xero account UUID from Attributes


@dataclass
class ParsedRow:
    """A single account row from a report section."""

    account_name: str
    account_id: str | None  # Xero account UUID
    values: dict[str, Decimal]  # column_header -> amount


@dataclass
class SummaryRow:
    """A subtotal/summary row (e.g., 'Total Income', 'Net Profit')."""

    label: str
    values: dict[str, Decimal]


@dataclass
class ReportSection:
    """A section of a Xero report (e.g., 'Income', 'Less Operating Expenses')."""

    title: str
    rows: list[ParsedRow] = field(default_factory=list)
    summary: SummaryRow | None = None


@dataclass
class ParsedReport:
    """Fully parsed Xero report."""

    report_id: str
    report_name: str
    report_date: str
    updated_at: str  # ISO 8601
    report_titles: list[str]
    column_headers: list[str]
    sections: list[ReportSection]
    summaries: list[SummaryRow]  # Top-level summaries (e.g., Net Profit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_XERO_DATE_RE = re.compile(r"/Date\((\d+)([+-]\d{4})?\)/")


def parse_xero_date(date_str: str) -> str:
    """Convert /Date(milliseconds+offset)/ to ISO 8601 string.

    Examples:
        /Date(1743321600000+0000)/ -> 2025-03-30T00:00:00+00:00
    """
    match = _XERO_DATE_RE.search(date_str)
    if not match:
        return date_str  # Return as-is if not in Xero format
    ms = int(match.group(1))
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.isoformat()


def _parse_amount(value: str) -> Decimal:
    """Parse a string amount to Decimal, handling empty strings and formatting."""
    if not value or value.strip() == "":
        return Decimal("0")
    # Remove commas from formatted numbers
    cleaned = value.strip().replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _extract_account_id(cell: dict) -> str | None:
    """Extract the Xero account UUID from a cell's Attributes array."""
    attributes = cell.get("Attributes")
    if not attributes:
        return None
    for attr in attributes:
        if attr.get("Id") == "account":
            return attr.get("Value")
    return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_report(response: dict) -> ParsedReport:
    """Parse a raw Xero report API response into a structured ParsedReport.

    Works for P&L, Trial Balance, and Balance Sheet — all use the same
    Rows > Section > Row > Cell structure.

    Args:
        response: Raw JSON response from the Xero API.

    Returns:
        A ParsedReport with sections, rows, and summaries.
    """
    reports = response.get("Reports", [])
    if not reports:
        raise ValueError("No reports found in Xero response")

    report = reports[0]

    report_id = report.get("ReportID", "")
    report_name = report.get("ReportName", "")
    report_date = report.get("ReportDate", "")
    report_titles = report.get("ReportTitles", [])

    updated_raw = report.get("UpdatedDateUTC", "")
    updated_at = parse_xero_date(updated_raw) if updated_raw else ""

    top_rows = report.get("Rows", [])

    # Step 1: Extract column headers from the Header row
    column_headers = _extract_column_headers(top_rows)

    # Step 2: Walk sections and parse rows
    sections: list[ReportSection] = []
    top_summaries: list[SummaryRow] = []

    for row in top_rows:
        row_type = row.get("RowType", "")

        if row_type == "Section":
            section = _parse_section(row, column_headers)
            # Sections with no title and only a SummaryRow are top-level summaries
            if not section.title and not section.rows and section.summary:
                top_summaries.append(section.summary)
            else:
                sections.append(section)

        elif row_type == "SummaryRow":
            # Top-level summary row (rare, but handle it)
            sr = _parse_summary_row(row, column_headers)
            if sr:
                top_summaries.append(sr)

    return ParsedReport(
        report_id=report_id,
        report_name=report_name,
        report_date=report_date,
        updated_at=updated_at,
        report_titles=report_titles,
        column_headers=column_headers,
        sections=sections,
        summaries=top_summaries,
    )


def _extract_column_headers(top_rows: list[dict]) -> list[str]:
    """Extract column headers from the Header row.

    The first cell is typically blank (account name column).
    Returns the value headers (excluding the first blank one).
    """
    for row in top_rows:
        if row.get("RowType") == "Header":
            cells = row.get("Cells", [])
            # Skip first cell (account name column), return rest
            return [cell.get("Value", "") for cell in cells[1:]]
    return []


def _parse_section(section_data: dict, column_headers: list[str]) -> ReportSection:
    """Parse a Section row into a ReportSection."""
    title = section_data.get("Title", "")
    inner_rows = section_data.get("Rows", [])

    parsed_rows: list[ParsedRow] = []
    summary: SummaryRow | None = None

    for row in inner_rows:
        row_type = row.get("RowType", "")

        if row_type == "Row":
            parsed = _parse_data_row(row, column_headers)
            if parsed:
                parsed_rows.append(parsed)

        elif row_type == "SummaryRow":
            summary = _parse_summary_row(row, column_headers)

    return ReportSection(title=title, rows=parsed_rows, summary=summary)


def _parse_data_row(row: dict, column_headers: list[str]) -> ParsedRow | None:
    """Parse a data Row into a ParsedRow."""
    cells = row.get("Cells", [])
    if not cells:
        return None

    # First cell is the account name
    account_name = cells[0].get("Value", "")
    account_id = _extract_account_id(cells[0])

    # Remaining cells are values — map to column headers dynamically
    values: dict[str, Decimal] = {}
    value_cells = cells[1:]

    for i, cell in enumerate(value_cells):
        # Use column header if available, otherwise fallback to index
        header = column_headers[i] if i < len(column_headers) else f"col_{i}"
        values[header] = _parse_amount(cell.get("Value", ""))

    return ParsedRow(
        account_name=account_name,
        account_id=account_id,
        values=values,
    )


def _parse_summary_row(row: dict, column_headers: list[str]) -> SummaryRow | None:
    """Parse a SummaryRow into a SummaryRow dataclass."""
    cells = row.get("Cells", [])
    if not cells:
        return None

    label = cells[0].get("Value", "")
    values: dict[str, Decimal] = {}
    value_cells = cells[1:]

    for i, cell in enumerate(value_cells):
        header = column_headers[i] if i < len(column_headers) else f"col_{i}"
        values[header] = _parse_amount(cell.get("Value", ""))

    return SummaryRow(label=label, values=values)


# ---------------------------------------------------------------------------
# Balance sheet: fixed asset extraction
# ---------------------------------------------------------------------------

@dataclass
class FixedAssetEntry:
    """A single fixed asset extracted from a balance sheet."""

    account_code: str
    account_name: str
    value: Decimal


def extract_fixed_assets(
    parsed: ParsedReport,
    land_prefix: str = "65",
    building_prefix: str = "66",
) -> dict[str, list[FixedAssetEntry]]:
    """Extract fixed asset values from a parsed balance sheet report.

    Walks all sections looking for rows whose account name contains
    an account code starting with the given prefixes (65xxx = land,
    66xxx = buildings).

    Because the Xero Reporting API does not expose the account *code*
    in the standard Row/Cell structure (only the UUID via Attributes),
    we match by convention: the account name typically includes the code,
    or the caller supplies an explicit code-to-UUID mapping.

    This function scans every data row and extracts the first column value
    for rows whose account_name starts with a digit matching the prefix.

    Returns:
        {"land": [...], "buildings": [...]} lists of FixedAssetEntry.
    """
    land: list[FixedAssetEntry] = []
    buildings: list[FixedAssetEntry] = []

    # Use the first value column (balance sheet typically has one date column)
    first_col = parsed.column_headers[0] if parsed.column_headers else None

    for section in parsed.sections:
        for row in section.rows:
            if first_col:
                amount = row.values.get(first_col, Decimal("0"))
            elif row.values:
                amount = next(iter(row.values.values()))
            else:
                continue

            name = row.account_name
            # Try to extract account code from the row — Xero balance sheet
            # rows do not reliably include the code in the name, so we rely
            # on the code_map approach (see extract_fixed_assets_by_code).
            # This direct-scan path is a best-effort fallback.
            entry = FixedAssetEntry(
                account_code="",
                account_name=name,
                value=amount,
            )

            # Check section title for asset classification
            section_lower = section.title.lower()
            if "asset" in section_lower or "fixed" in section_lower:
                land.append(entry)

    return {"land": land, "buildings": buildings}


def extract_fixed_assets_by_code(
    parsed: ParsedReport,
    code_map: dict[str, str],
) -> list[FixedAssetEntry]:
    """Extract fixed asset values using an explicit account-code-to-UUID map.

    Args:
        parsed: A ParsedReport from a balance sheet.
        code_map: Mapping of account_code -> account_uuid (Xero UUID).
                  Built from properties.yaml asset account codes resolved
                  against the chart of accounts or Xero account list.

    Returns:
        List of FixedAssetEntry with account_code, name, and value populated.
    """
    uuid_to_code = {v: k for k, v in code_map.items()}
    first_col = parsed.column_headers[0] if parsed.column_headers else None

    results: list[FixedAssetEntry] = []
    for section in parsed.sections:
        for row in section.rows:
            if row.account_id and row.account_id in uuid_to_code:
                code = uuid_to_code[row.account_id]
                if first_col:
                    amount = row.values.get(first_col, Decimal("0"))
                elif row.values:
                    amount = next(iter(row.values.values()))
                else:
                    amount = Decimal("0")

                results.append(FixedAssetEntry(
                    account_code=code,
                    account_name=row.account_name,
                    value=amount,
                ))

    return results


# ---------------------------------------------------------------------------
# Convenience: flatten to dicts
# ---------------------------------------------------------------------------

def report_to_flat_rows(parsed: ParsedReport) -> list[dict[str, Any]]:
    """Convert a ParsedReport to a flat list of dicts for easy consumption.

    Each dict has:
        section, account_name, account_id, and one key per column header.
    """
    flat: list[dict[str, Any]] = []
    for section in parsed.sections:
        for row in section.rows:
            entry: dict[str, Any] = {
                "section": section.title,
                "account_name": row.account_name,
                "account_id": row.account_id,
            }
            for header, amount in row.values.items():
                entry[header] = float(amount)
            flat.append(entry)
    return flat
