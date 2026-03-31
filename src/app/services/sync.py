"""Xero sync service — monthly and manual live sync orchestration.

Handles:
- Monthly sync: fetch P&L + Balance Sheet for the prior completed month
- Manual (live) sync: fetch YTD P&L + current Balance Sheet
- Sync logging to data/sync/sync_log.json (append-only)
- Idempotent: calling twice for the same month overwrites the same files
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.xero.client import fetch_balance_sheet, fetch_profit_and_loss
from app.xero.snapshots import save_balance_sheet_snapshot, save_pl_snapshot

logger = logging.getLogger(__name__)

SYNC_LOG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "sync"
SYNC_LOG_FILE = SYNC_LOG_DIR / "sync_log.json"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def prior_completed_month(today: date | None = None) -> tuple[date, date]:
    """Return (first_day, last_day) of the prior completed month.

    Example: called on 2026-04-03 -> (2026-03-01, 2026-03-31).
    """
    today = today or date.today()
    first_of_current = today.replace(day=1)
    last_of_prior = first_of_current - timedelta(days=1)
    first_of_prior = last_of_prior.replace(day=1)
    return first_of_prior, last_of_prior


def current_ytd_range(today: date | None = None) -> tuple[date, date]:
    """Return (Jan 1, today) for the current year.

    Example: called on 2026-04-03 -> (2026-01-01, 2026-04-03).
    """
    today = today or date.today()
    return date(today.year, 1, 1), today


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------

def _append_sync_log(entry: dict[str, Any]) -> None:
    """Append a sync log entry to data/sync/sync_log.json."""
    SYNC_LOG_DIR.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    if SYNC_LOG_FILE.exists():
        try:
            entries = json.loads(SYNC_LOG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append(entry)

    SYNC_LOG_FILE.write_text(
        json.dumps(entries, indent=2, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Sync operations
# ---------------------------------------------------------------------------

async def sync_monthly(today: date | None = None) -> dict[str, Any]:
    """Sync the prior completed month's P&L and Balance Sheet from Xero.

    Idempotent: calling twice for the same month overwrites the same files.

    Returns:
        {status, month, snapshots: [...], errors: []}
    """
    start_time = time.monotonic()
    first_day, last_day = prior_completed_month(today)
    month_label = first_day.strftime("%Y-%m")

    from_date = first_day.isoformat()
    to_date = last_day.isoformat()

    snapshots: list[str] = []
    errors: list[str] = []

    # Fetch P&L for the month
    try:
        pl_data = await fetch_profit_and_loss(from_date, to_date)
        path = save_pl_snapshot(pl_data, from_date, to_date)
        snapshots.append(str(path.name))
        logger.info("Monthly sync: saved P&L snapshot %s", path.name)
    except Exception as exc:
        msg = f"P&L fetch failed: {exc}"
        logger.error("Monthly sync: %s", msg)
        errors.append(msg)

    # Fetch Balance Sheet as of month-end
    try:
        bs_data = await fetch_balance_sheet(to_date)
        path = save_balance_sheet_snapshot(bs_data, to_date)
        snapshots.append(str(path.name))
        logger.info("Monthly sync: saved Balance Sheet snapshot %s", path.name)
    except Exception as exc:
        msg = f"Balance Sheet fetch failed: {exc}"
        logger.error("Monthly sync: %s", msg)
        errors.append(msg)

    duration = round(time.monotonic() - start_time, 2)
    status = "ok" if not errors else ("partial" if snapshots else "error")

    result = {
        "status": status,
        "month": month_label,
        "snapshots": snapshots,
        "errors": errors,
        "duration_seconds": duration,
    }

    _append_sync_log({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "type": "monthly",
        "status": status,
        "month": month_label,
        "duration_seconds": duration,
        "snapshots": snapshots,
        "errors": errors,
    })

    return result


async def sync_now(today: date | None = None) -> dict[str, Any]:
    """Manual live sync: fetch YTD P&L and current Balance Sheet.

    Saves/overwrites the current month's snapshot.

    Returns:
        {status, period, snapshots: [...], errors: []}
    """
    start_time = time.monotonic()
    today = today or date.today()
    ytd_start, ytd_end = current_ytd_range(today)

    from_date = ytd_start.isoformat()
    to_date = ytd_end.isoformat()

    snapshots: list[str] = []
    errors: list[str] = []

    # Fetch YTD P&L
    try:
        pl_data = await fetch_profit_and_loss(from_date, to_date)
        path = save_pl_snapshot(pl_data, from_date, to_date)
        snapshots.append(str(path.name))
        logger.info("Manual sync: saved YTD P&L snapshot %s", path.name)
    except Exception as exc:
        msg = f"P&L fetch failed: {exc}"
        logger.error("Manual sync: %s", msg)
        errors.append(msg)

    # Fetch current Balance Sheet
    try:
        bs_data = await fetch_balance_sheet(to_date)
        path = save_balance_sheet_snapshot(bs_data, to_date)
        snapshots.append(str(path.name))
        logger.info("Manual sync: saved Balance Sheet snapshot %s", path.name)
    except Exception as exc:
        msg = f"Balance Sheet fetch failed: {exc}"
        logger.error("Manual sync: %s", msg)
        errors.append(msg)

    duration = round(time.monotonic() - start_time, 2)
    status = "ok" if not errors else ("partial" if snapshots else "error")

    period_label = f"{from_date} to {to_date}"

    result = {
        "status": status,
        "period": period_label,
        "snapshots": snapshots,
        "errors": errors,
        "duration_seconds": duration,
    }

    _append_sync_log({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "type": "manual",
        "status": status,
        "period": period_label,
        "duration_seconds": duration,
        "snapshots": snapshots,
        "errors": errors,
    })

    return result
