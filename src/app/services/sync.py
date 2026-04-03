"""Xero sync service — monthly and manual live sync orchestration.

Handles:
- Monthly sync: fetch P&L + Balance Sheet for the prior completed month
- Manual (live) sync: fetch YTD P&L + current Balance Sheet
- Sync logging to data/sync/sync_log.json (append-only)
- Idempotent: calling twice for the same month overwrites the same files
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.xero.client import (
    fetch_balance_sheet,
    fetch_profit_and_loss,
    fetch_tracking_categories,
)
from app.xero.snapshots import (
    save_balance_sheet_snapshot,
    save_pl_snapshot,
    save_tracking_categories_snapshot,
)

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
    """Manual live sync: fetch each month of the current year individually.

    Pulls a separate P&L snapshot for each completed month, plus the partial
    current month.  Also fetches the current Balance Sheet.

    Returns:
        {status, period, snapshots: [...], errors: []}
    """
    start_time = time.monotonic()
    today = today or date.today()

    snapshots: list[str] = []
    errors: list[str] = []

    # Fetch each month of the current year individually
    for month in range(1, today.month + 1):
        first_day = date(today.year, month, 1)
        if month == today.month:
            last_day = today  # partial current month
        else:
            last_day = date(today.year, month, calendar.monthrange(today.year, month)[1])

        from_date = first_day.isoformat()
        to_date = last_day.isoformat()

        try:
            pl_data = await fetch_profit_and_loss(from_date, to_date)
            path = save_pl_snapshot(pl_data, from_date, to_date)
            snapshots.append(str(path.name))
            logger.info("Manual sync: saved P&L snapshot %s", path.name)
        except Exception as exc:
            msg = f"P&L fetch failed for {from_date}: {exc}"
            logger.error("Manual sync: %s", msg)
            errors.append(msg)

    # Fetch current Balance Sheet
    try:
        bs_data = await fetch_balance_sheet(today.isoformat())
        path = save_balance_sheet_snapshot(bs_data, today.isoformat())
        snapshots.append(str(path.name))
        logger.info("Manual sync: saved Balance Sheet snapshot %s", path.name)
    except Exception as exc:
        msg = f"Balance Sheet fetch failed: {exc}"
        logger.error("Manual sync: %s", msg)
        errors.append(msg)

    # Fetch tracking categories + YTD tracking P&L breakdown
    try:
        tc_data = await fetch_tracking_categories()
        path = save_tracking_categories_snapshot(tc_data)
        snapshots.append(str(path.name))
        logger.info("Manual sync: saved tracking categories %s", path.name)

        # Fetch YTD tracking P&L for each category
        ytd_start = date(today.year, 1, 1).isoformat()
        ytd_end = today.isoformat()
        for cat in tc_data.get("TrackingCategories", []):
            cat_id = cat.get("TrackingCategoryID")
            if cat_id:
                pl_data = await fetch_profit_and_loss(
                    ytd_start, ytd_end, tracking_category_id=cat_id,
                )
                path = save_pl_snapshot(pl_data, ytd_start, ytd_end, tracking=True)
                snapshots.append(str(path.name))
                logger.info("Manual sync: saved tracking P&L %s", path.name)
    except Exception as exc:
        msg = f"Tracking sync failed: {exc}"
        logger.error("Manual sync: %s", msg)
        errors.append(msg)

    duration = round(time.monotonic() - start_time, 2)
    status = "ok" if not errors else ("partial" if snapshots else "error")

    period_label = f"{today.year}-01 to {today.year}-{today.month:02d}"

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


async def sync_historical(
    from_year: int,
    to_year: int,
    today: date | None = None,
) -> dict[str, Any]:
    """Backfill monthly P&L snapshots for a range of years.

    Fetches one P&L snapshot per month from Jan of from_year through
    the last completed month of to_year (or the partial current month
    if to_year is the current year).

    Returns:
        {status, range, months_synced, snapshots: [...], errors: [...],
         duration_seconds}
    """
    start_time = time.monotonic()
    today = today or date.today()

    snapshots: list[str] = []
    errors: list[str] = []
    months_synced = 0

    for year in range(from_year, to_year + 1):
        last_month = 12
        if year == today.year:
            last_month = today.month

        for month in range(1, last_month + 1):
            first_day = date(year, month, 1)

            if year == today.year and month == today.month:
                last_day = today  # partial current month
            else:
                last_day = date(year, month, calendar.monthrange(year, month)[1])

            from_date = first_day.isoformat()
            to_date = last_day.isoformat()

            try:
                pl_data = await fetch_profit_and_loss(from_date, to_date)
                path = save_pl_snapshot(pl_data, from_date, to_date)
                snapshots.append(str(path.name))
                months_synced += 1
                logger.info("Historical sync: saved %s", path.name)
            except Exception as exc:
                msg = f"P&L fetch failed for {from_date}: {exc}"
                logger.error("Historical sync: %s", msg)
                errors.append(msg)

    # Fetch balance sheet as-of today for the latest position
    try:
        bs_data = await fetch_balance_sheet(today.isoformat())
        path = save_balance_sheet_snapshot(bs_data, today.isoformat())
        snapshots.append(str(path.name))
        logger.info("Historical sync: saved Balance Sheet %s", path.name)
    except Exception as exc:
        msg = f"Balance Sheet fetch failed: {exc}"
        logger.error("Historical sync: %s", msg)
        errors.append(msg)

    duration = round(time.monotonic() - start_time, 2)
    status = "ok" if not errors else ("partial" if snapshots else "error")

    range_label = f"{from_year} to {to_year}"

    result = {
        "status": status,
        "range": range_label,
        "months_synced": months_synced,
        "snapshots": snapshots,
        "errors": errors,
        "duration_seconds": duration,
    }

    _append_sync_log({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "type": "historical",
        "status": status,
        "range": range_label,
        "months_synced": months_synced,
        "duration_seconds": duration,
        "snapshots": snapshots,
        "errors": errors,
    })

    return result
