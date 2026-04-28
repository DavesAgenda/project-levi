"""Snapshot writer — save Xero API responses as JSON to data/snapshots/.

Implements the snapshot-to-git pattern: every API pull is saved as a
timestamped JSON file that gets committed to the repo for audit trail.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models import FinancialSnapshot, SnapshotRow
from app.xero.accounts import load_uuid_to_code
from app.xero.parser import parse_report

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "snapshots"
CONFIG_DIR = SNAPSHOTS_DIR.parent.parent / "config"


def _ensure_dir(directory: Path) -> None:
    """Create directory if it doesn't exist."""
    directory.mkdir(parents=True, exist_ok=True)


def _build_filename(
    report_type: str,
    from_date: str | None = None,
    to_date: str | None = None,
    suffix: str | None = None,
) -> str:
    """Build a descriptive filename for a snapshot.

    Examples:
        pl_2026-01-01_2026-03-31.json
        pl_2026-01-01_2026-03-31_by-ministry.json
        trial_balance_2026-03-31.json
        tracking_categories.json
    """
    parts = [report_type]
    if from_date:
        parts.append(from_date)
    if to_date:
        parts.append(to_date)
    if suffix:
        parts.append(suffix)
    return "_".join(parts) + ".json"


def save_snapshot(
    data: dict[str, Any],
    report_type: str,
    from_date: str | None = None,
    to_date: str | None = None,
    suffix: str | None = None,
    directory: Path | None = None,
) -> Path:
    """Save a Xero API response as a JSON snapshot.

    Args:
        data: Raw Xero API JSON response.
        report_type: Short name (e.g., "pl", "trial_balance", "balance_sheet").
        from_date: Report start date (YYYY-MM-DD).
        to_date: Report end date (YYYY-MM-DD).
        suffix: Optional suffix (e.g., "by-ministry").
        directory: Override snapshot directory (for testing).

    Returns:
        Path to the written file.
    """
    target_dir = directory or SNAPSHOTS_DIR
    _ensure_dir(target_dir)

    filename = _build_filename(report_type, from_date, to_date, suffix)
    filepath = target_dir / filename

    # Add snapshot metadata wrapper
    snapshot = {
        "snapshot_metadata": {
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "report_type": report_type,
            "from_date": from_date,
            "to_date": to_date,
        },
        "response": data,
    }

    filepath.write_text(
        json.dumps(snapshot, indent=2, default=str),
        encoding="utf-8",
    )
    return filepath


def save_pl_snapshot(
    data: dict[str, Any],
    from_date: str,
    to_date: str,
    tracking: bool = False,
    tracking_category_name: str | None = None,
) -> Path:
    """Save a P&L report snapshot.

    For tracking breakdowns, include the category name in the filename
    so Congregations and Ministry & Funds don't overwrite each other.
    """
    if tracking:
        # Slugify category name for filename (e.g. "Ministry & Funds" -> "ministry-funds")
        slug = re.sub(r"[^a-z0-9]+", "-", (tracking_category_name or "tracking").lower()).strip("-")
        suffix = f"by-{slug}"
    else:
        suffix = None
    return save_snapshot(data, "pl", from_date, to_date, suffix=suffix)


def save_trial_balance_snapshot(data: dict[str, Any], as_of_date: str) -> Path:
    """Save a Trial Balance snapshot."""
    return save_snapshot(data, "trial_balance", to_date=as_of_date)


def save_balance_sheet_snapshot(data: dict[str, Any], as_of_date: str) -> Path:
    """Save a Balance Sheet snapshot."""
    return save_snapshot(data, "balance_sheet", to_date=as_of_date)


def save_tracking_categories_snapshot(data: dict[str, Any]) -> Path:
    """Save a Tracking Categories snapshot."""
    return save_snapshot(data, "tracking_categories")


# ---------------------------------------------------------------------------
# Snapshot → FinancialSnapshot converter (Xero raw → flat model)
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    """Lowercase, strip non-alphanumeric for fuzzy name matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _build_name_lookup() -> dict[str, str]:
    """Build {normalised_account_name: account_code} from chart_of_accounts.yaml."""
    import yaml
    from app.models import ChartOfAccounts

    chart_path = CONFIG_DIR / "chart_of_accounts.yaml"
    if not chart_path.exists():
        return {}
    with open(chart_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    chart = ChartOfAccounts(**raw)

    name_map: dict[str, str] = {}
    for section_field in [chart.income, chart.expenses]:
        for cat in section_field.values():
            for acct in cat.accounts + cat.legacy_accounts + cat.property_costs:
                name_map[_normalise(acct.name)] = acct.code
    return name_map


def xero_snapshot_to_financial(raw: dict[str, Any]) -> FinancialSnapshot | None:
    """Convert a Xero-wrapped snapshot dict to a FinancialSnapshot.

    Handles the {snapshot_metadata, response} envelope produced by save_snapshot().
    Returns None if the data cannot be parsed.
    """
    metadata = raw.get("snapshot_metadata")
    response = raw.get("response")
    if not metadata or not response:
        return None

    try:
        parsed = parse_report(response)
    except (ValueError, KeyError):
        logger.warning("Failed to parse Xero report from snapshot")
        return None

    uuid_lookup = load_uuid_to_code()
    name_lookup = _build_name_lookup()

    rows: list[SnapshotRow] = []
    for section in parsed.sections:
        for row in section.rows:
            amount = float(next(iter(row.values.values()), Decimal("0")))
            # Prefer UUID lookup (deterministic, idempotent); fall back to
            # fuzzy name match against chart_of_accounts.yaml.
            code = ""
            if row.account_id and row.account_id in uuid_lookup:
                code = uuid_lookup[row.account_id]
            if not code:
                code = name_lookup.get(_normalise(row.account_name), "")
            rows.append(SnapshotRow(
                account_code=code,
                account_name=row.account_name,
                amount=amount,
                account_id=row.account_id,
            ))

    from_date = metadata.get("from_date") or metadata.get("to_date", "")
    to_date = metadata.get("to_date", "")

    return FinancialSnapshot(
        report_date=parsed.report_date or to_date,
        from_date=from_date,
        to_date=to_date,
        source="xero_api",
        rows=rows,
    )
