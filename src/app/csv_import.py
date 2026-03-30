"""CSV Import Engine for the Church Budget Tool.

Parses Xero P&L CSV exports, validates against chart_of_accounts.yaml,
maps accounts to budget categories, and produces structured import results.

Handles:
- Multiple CSV encodings (utf-8, utf-8-sig, latin-1)
- Missing / blank values treated as zero
- Date format flexibility in period headers
- Legacy account reconciliation for historical imports
- Clear error reporting for unrecognised accounts
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import IO

import yaml

from app.models import (
    Account,
    BudgetCategory,
    ChartOfAccounts,
    CSVRow,
    FinancialSnapshot,
    ImportIssue,
    ImportResult,
    MappedRow,
    SnapshotRow,
)


# ---------------------------------------------------------------------------
# Chart of Accounts loader + lookup builder
# ---------------------------------------------------------------------------

def load_chart_of_accounts(config_path: Path) -> ChartOfAccounts:
    """Load and validate chart_of_accounts.yaml into a Pydantic model."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return ChartOfAccounts(**raw)


AccountLookup = dict[str, tuple[str, str, str, bool]]
# key: account_code  ->  (category_key, section, budget_label, is_legacy)


def build_account_lookup(chart: ChartOfAccounts) -> AccountLookup:
    """Build a flat {account_code: (category_key, section, label, is_legacy)} map.

    Both current ``accounts`` and ``legacy_accounts`` (and ``property_costs``)
    are included so that historical CSVs can be mapped through the same engine.
    """
    lookup: AccountLookup = {}

    for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
        for cat_key, cat in section_field.items():
            for acct in cat.accounts:
                lookup[acct.code] = (cat_key, section_name, cat.budget_label, False)
            for acct in cat.legacy_accounts:
                lookup[acct.code] = (cat_key, section_name, cat.budget_label, True)
            for acct in cat.property_costs:
                lookup[acct.code] = (cat_key, section_name, cat.budget_label, False)

    return lookup


def build_name_lookup(chart: ChartOfAccounts) -> dict[str, str]:
    """Build a {normalised_account_name: account_code} map for name-based matching.

    Used as a fallback when the CSV does not include account codes.
    """
    name_map: dict[str, str] = {}

    for section_field in [chart.income, chart.expenses]:
        for cat in section_field.values():
            for acct in cat.accounts + cat.legacy_accounts + cat.property_costs:
                name_map[_normalise(acct.name)] = acct.code

    return name_map


def _normalise(name: str) -> str:
    """Lowercase, strip whitespace and punctuation for fuzzy name matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

_ENCODINGS = ["utf-8-sig", "utf-8", "latin-1"]

# Rows to skip — Xero P&L CSVs sometimes have title / blank rows before the header
_SKIP_PATTERNS = re.compile(
    r"^(profit\s*&?\s*loss|$)",
    re.IGNORECASE,
)

# Amount cleaning — strip dollar signs, commas, parentheses (negative)
_AMOUNT_RE = re.compile(r"[,$\s]")


def _clean_amount(raw: str) -> float:
    """Parse a dollar string into a float.  Parenthesised values are negative."""
    raw = raw.strip()
    if not raw or raw == "-":
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = _AMOUNT_RE.sub("", raw.strip("()"))
    try:
        value = float(cleaned)
    except ValueError:
        return 0.0
    return -value if negative else value


def _decode(raw_bytes: bytes) -> str:
    """Try multiple encodings and return decoded text."""
    for enc in _ENCODINGS:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError("Unable to decode CSV — tried utf-8-sig, utf-8, latin-1")


def _detect_account_code(name_cell: str) -> tuple[str | None, str]:
    """Try to extract an account code prefix from the first column.

    Xero exports sometimes use ``"10001 - Offering EFT"`` or just ``"Offering EFT"``.
    Returns (code_or_None, clean_name).
    """
    m = re.match(r"^(\d{3,6})\s*[-–]\s*(.+)$", name_cell.strip())
    if m:
        return m.group(1), m.group(2).strip()
    # Might be just a bare code
    if re.fullmatch(r"\d{3,6}", name_cell.strip()):
        return name_cell.strip(), name_cell.strip()
    return None, name_cell.strip()


def parse_csv(
    source: bytes | str | IO[bytes],
    *,
    filename: str = "upload.csv",
) -> tuple[list[str], list[CSVRow], list[ImportIssue]]:
    """Parse raw CSV bytes into a list of period headers and CSVRow objects.

    Returns (period_headers, rows, errors).
    """
    # Normalise to text
    if isinstance(source, (bytes, bytearray)):
        text = _decode(source)
    elif isinstance(source, str):
        text = source
    else:
        # file-like
        text = _decode(source.read())

    errors: list[ImportIssue] = []

    # Strip BOM if present (utf-8-sig should handle, but belt-and-braces)
    text = text.lstrip("\ufeff")

    reader = csv.reader(io.StringIO(text))
    all_lines = list(reader)

    if not all_lines:
        errors.append(ImportIssue(message="CSV file is empty"))
        return [], [], errors

    # Find the header row — skip title / blank rows
    header_idx = 0
    for i, row in enumerate(all_lines):
        first = (row[0] if row else "").strip()
        if _SKIP_PATTERNS.match(first):
            header_idx = i + 1
            continue
        break

    if header_idx >= len(all_lines):
        errors.append(ImportIssue(message="Could not find a header row in the CSV"))
        return [], [], errors

    header = all_lines[header_idx]
    # First column is account name; remaining columns are period labels
    period_headers = [h.strip() for h in header[1:] if h.strip()]

    if not period_headers:
        errors.append(ImportIssue(message="No period columns found in header row"))
        return [], [], errors

    rows: list[CSVRow] = []
    data_lines = all_lines[header_idx + 1 :]

    for line_no, line in enumerate(data_lines, start=header_idx + 2):
        if not line or not any(cell.strip() for cell in line):
            continue  # skip blank rows

        name_cell = line[0].strip()
        if not name_cell:
            continue

        # Skip summary / total rows
        if re.match(r"^(total\s|net\s|gross\s)", name_cell, re.IGNORECASE):
            continue

        code, name = _detect_account_code(name_cell)

        amounts: dict[str, float] = {}
        for col_idx, period in enumerate(period_headers):
            raw = line[col_idx + 1].strip() if col_idx + 1 < len(line) else ""
            amounts[period] = _clean_amount(raw)

        rows.append(CSVRow(account_code=code, account_name=name, amounts=amounts))

    return period_headers, rows, errors


# ---------------------------------------------------------------------------
# Mapping engine
# ---------------------------------------------------------------------------

def map_rows(
    rows: list[CSVRow],
    chart: ChartOfAccounts,
) -> tuple[list[MappedRow], list[ImportIssue], list[str]]:
    """Map parsed CSV rows to budget categories using the chart of accounts.

    Returns (mapped_rows, errors, unrecognised_account_descriptions).
    """
    code_lookup = build_account_lookup(chart)
    name_lookup = build_name_lookup(chart)
    errors: list[ImportIssue] = []
    unrecognised: list[str] = []
    mapped: list[MappedRow] = []

    for idx, row in enumerate(rows):
        # Try code-based lookup first
        match = None
        if row.account_code and row.account_code in code_lookup:
            match = code_lookup[row.account_code]

        # Fallback to name-based lookup
        if match is None:
            norm = _normalise(row.account_name)
            code_from_name = name_lookup.get(norm)
            if code_from_name:
                match = code_lookup[code_from_name]
                # Backfill the code for downstream use
                row = row.model_copy(update={"account_code": code_from_name})

        if match is None:
            label = f"{row.account_code} - {row.account_name}" if row.account_code else row.account_name
            unrecognised.append(label)
            errors.append(ImportIssue(
                row=idx + 1,
                field="account",
                message=f"Unrecognised account: {label}",
            ))
            continue

        cat_key, section, budget_label, is_legacy = match
        mapped.append(MappedRow(
            account_code=row.account_code,
            account_name=row.account_name,
            category_key=cat_key,
            category_section=section,
            budget_label=budget_label,
            is_legacy=is_legacy,
            amounts=row.amounts,
        ))

    return mapped, errors, unrecognised


# ---------------------------------------------------------------------------
# High-level import orchestrator
# ---------------------------------------------------------------------------

def import_csv(
    source: bytes | str | IO[bytes],
    chart: ChartOfAccounts,
    *,
    filename: str = "upload.csv",
    strict: bool = True,
) -> ImportResult:
    """Full CSV import pipeline: parse -> validate -> map -> result.

    Args:
        source: Raw CSV content (bytes, string, or file-like).
        chart: Validated ChartOfAccounts instance.
        filename: Original filename for reporting.
        strict: If True, any unrecognised account causes ``success=False``.
                 If False, unrecognised accounts are treated as warnings.

    Returns:
        ImportResult with mapped rows and error details.
    """
    period_headers, parsed_rows, parse_errors = parse_csv(source, filename=filename)

    if parse_errors:
        return ImportResult(
            success=False,
            filename=filename,
            errors=parse_errors,
        )

    mapped_rows, map_errors, unrecognised = map_rows(parsed_rows, chart)

    warnings: list[ImportIssue] = []
    errors: list[ImportIssue] = []

    if strict:
        errors = map_errors
    else:
        warnings = map_errors

    success = len(errors) == 0

    return ImportResult(
        success=success,
        filename=filename,
        total_rows=len(parsed_rows),
        mapped_rows=len(mapped_rows),
        errors=errors,
        warnings=warnings,
        rows=mapped_rows,
        unrecognised_accounts=unrecognised,
    )


# ---------------------------------------------------------------------------
# Snapshot conversion — produce the same format as Xero API snapshots
# ---------------------------------------------------------------------------

def to_snapshot(
    result: ImportResult,
    *,
    from_date: str,
    to_date: str,
    report_date: str | None = None,
) -> FinancialSnapshot:
    """Convert a successful ImportResult into a FinancialSnapshot.

    Each mapped row with non-zero total is emitted as a SnapshotRow.
    The snapshot format is identical to what the Xero API integration produces,
    ensuring both import paths feed the same downstream reports.
    """
    if report_date is None:
        report_date = date.today().isoformat()

    snapshot_rows: list[SnapshotRow] = []
    for mr in result.rows:
        total = sum(mr.amounts.values())
        if total == 0.0:
            continue
        snapshot_rows.append(SnapshotRow(
            account_code=mr.account_code or "",
            account_name=mr.account_name,
            amount=round(total, 2),
        ))

    return FinancialSnapshot(
        report_date=report_date,
        from_date=from_date,
        to_date=to_date,
        source="csv_import",
        rows=snapshot_rows,
    )
