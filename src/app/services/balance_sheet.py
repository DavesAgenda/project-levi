"""Balance sheet change analysis service — material changes between periods.

Loads two balance sheet snapshots (current and prior), walks all sections
and rows, computes dollar and percentage deltas, and filters to only
material changes for council report presentation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.xero.parser import ParsedReport, parse_report
from app.xero.snapshots import SNAPSHOTS_DIR


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BalanceSheetRow:
    """A single account row with period-over-period change."""

    account_name: str
    section: str
    current_value: float
    prior_value: float
    change_dollar: float
    change_pct: float | None
    is_material: bool


@dataclass
class BalanceSheetSection:
    """A group of rows under one balance sheet section heading."""

    title: str
    rows: list[BalanceSheetRow] = field(default_factory=list)
    current_total: float = 0.0
    prior_total: float = 0.0
    change_dollar: float = 0.0


@dataclass
class BalanceSheetData:
    """Complete balance sheet comparison context for template rendering."""

    sections: list[BalanceSheetSection] = field(default_factory=list)
    net_assets_current: float = 0.0
    net_assets_prior: float = 0.0
    net_assets_change: float = 0.0
    current_date: str = ""
    prior_date: str = ""
    has_data: bool = False


# ---------------------------------------------------------------------------
# Snapshot discovery and loading
# ---------------------------------------------------------------------------

_BS_FILENAME_RE = re.compile(r"balance_sheet_(\d{4}-\d{2}-\d{2})\.json")


def find_balance_sheet_snapshots(
    directory: Path | None = None,
) -> list[tuple[str, Path]]:
    """Scan directory for balance sheet snapshot files.

    Returns:
        List of (date_string, path) tuples sorted by date descending (newest first).
    """
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return []

    results: list[tuple[str, Path]] = []
    for path in snap_dir.glob("balance_sheet_*.json"):
        match = _BS_FILENAME_RE.match(path.name)
        if match:
            results.append((match.group(1), path))

    return sorted(results, key=lambda t: t[0], reverse=True)


def load_balance_sheet_snapshot(
    date_prefix: str,
    directory: Path | None = None,
) -> ParsedReport | None:
    """Find and parse a balance sheet snapshot by date prefix.

    Args:
        date_prefix: Date string like "2026-03-31" or partial "2026-03".
        directory: Override snapshot directory.

    Returns:
        ParsedReport if found, None otherwise.
    """
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return None

    # Try exact match first, then prefix match
    for path in snap_dir.glob("balance_sheet_*.json"):
        match = _BS_FILENAME_RE.match(path.name)
        if match and match.group(1).startswith(date_prefix):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # Handle snapshot wrapper format
                response = raw.get("response", raw)
                return parse_report(response)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    return None


# ---------------------------------------------------------------------------
# Change computation
# ---------------------------------------------------------------------------

def _build_section_map(
    parsed: ParsedReport,
) -> dict[str, dict[str, float]]:
    """Build a section -> {account_name: value} map from a parsed report.

    Uses the first column header as the value column.
    """
    first_col = parsed.column_headers[0] if parsed.column_headers else None
    section_map: dict[str, dict[str, float]] = {}

    for section in parsed.sections:
        if not section.title:
            continue
        accounts: dict[str, float] = {}
        for row in section.rows:
            if first_col:
                val = float(row.values.get(first_col, 0))
            elif row.values:
                val = float(next(iter(row.values.values())))
            else:
                val = 0.0
            accounts[row.account_name] = val
        section_map[section.title] = accounts

    return section_map


def _extract_net_assets(parsed: ParsedReport) -> float:
    """Extract the Net Assets total from top-level summaries."""
    first_col = parsed.column_headers[0] if parsed.column_headers else None
    for summary in parsed.summaries:
        if "net assets" in summary.label.lower():
            if first_col:
                return float(summary.values.get(first_col, 0))
            elif summary.values:
                return float(next(iter(summary.values.values())))
    return 0.0


def compute_balance_sheet_changes(
    current_date: str,
    prior_date: str,
    materiality_dollar: float = 500.0,
    materiality_pct: float = 5.0,
    directory: Path | None = None,
) -> BalanceSheetData:
    """Load two balance sheet snapshots and compute material changes.

    Args:
        current_date: Date prefix for the current period snapshot.
        prior_date: Date prefix for the prior period snapshot.
        materiality_dollar: Include row if abs(change) exceeds this.
        materiality_pct: Include row if abs(change_pct) exceeds this.
        directory: Override snapshot directory.

    Returns:
        BalanceSheetData with only material change rows included.
    """
    current_bs = load_balance_sheet_snapshot(current_date, directory)
    prior_bs = load_balance_sheet_snapshot(prior_date, directory)

    if current_bs is None or prior_bs is None:
        return BalanceSheetData(
            current_date=current_date,
            prior_date=prior_date,
        )

    current_map = _build_section_map(current_bs)
    prior_map = _build_section_map(prior_bs)

    # Collect all section titles from both periods
    all_section_titles = list(dict.fromkeys(
        list(current_map.keys()) + list(prior_map.keys())
    ))

    sections: list[BalanceSheetSection] = []

    for section_title in all_section_titles:
        current_accounts = current_map.get(section_title, {})
        prior_accounts = prior_map.get(section_title, {})

        # Merge account names from both periods
        all_accounts = list(dict.fromkeys(
            list(current_accounts.keys()) + list(prior_accounts.keys())
        ))

        rows: list[BalanceSheetRow] = []
        section_current_total = 0.0
        section_prior_total = 0.0

        for account_name in all_accounts:
            current_val = current_accounts.get(account_name, 0.0)
            prior_val = prior_accounts.get(account_name, 0.0)
            change_dollar = round(current_val - prior_val, 2)

            if prior_val != 0:
                change_pct = round(change_dollar / abs(prior_val) * 100, 1)
            else:
                change_pct = None

            is_material = (
                abs(change_dollar) > materiality_dollar
                or (change_pct is not None and abs(change_pct) > materiality_pct)
            )

            rows.append(BalanceSheetRow(
                account_name=account_name,
                section=section_title,
                current_value=round(current_val, 2),
                prior_value=round(prior_val, 2),
                change_dollar=change_dollar,
                change_pct=change_pct,
                is_material=is_material,
            ))

            section_current_total += current_val
            section_prior_total += prior_val

        # Only include sections that have at least one material row
        material_rows = [r for r in rows if r.is_material]
        if material_rows:
            sections.append(BalanceSheetSection(
                title=section_title,
                rows=material_rows,
                current_total=round(section_current_total, 2),
                prior_total=round(section_prior_total, 2),
                change_dollar=round(section_current_total - section_prior_total, 2),
            ))

    # Net assets
    net_current = _extract_net_assets(current_bs)
    net_prior = _extract_net_assets(prior_bs)

    return BalanceSheetData(
        sections=sections,
        net_assets_current=round(net_current, 2),
        net_assets_prior=round(net_prior, 2),
        net_assets_change=round(net_current - net_prior, 2),
        current_date=current_date,
        prior_date=prior_date,
        has_data=True,
    )
