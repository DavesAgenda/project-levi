"""Snapshot writer — save Xero API responses as JSON to data/snapshots/.

Implements the snapshot-to-git pattern: every API pull is saved as a
timestamped JSON file that gets committed to the repo for audit trail.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "snapshots"


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
) -> Path:
    """Save a P&L report snapshot."""
    suffix = "by-ministry" if tracking else None
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
