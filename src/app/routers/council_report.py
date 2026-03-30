"""FastAPI router for the council report view."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.council_report import compute_council_report
from app.services.balance_sheet import (
    BalanceSheetData,
    compute_balance_sheet_changes,
    find_balance_sheet_snapshots,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/council", response_class=HTMLResponse)
async def council_report(
    request: Request,
    year: int = Query(default=None, description="Financial year"),
    month: int = Query(default=None, ge=1, le=12, description="End month (1-12)"),
    view: str = Query(default="ytd", description="View mode: ytd or month"),
):
    """Render the council report — monthly YTD vs budget by category."""
    today = date.today()
    report_year = year if year is not None else today.year
    report_month = month if month is not None else today.month
    view_mode = view if view in ("ytd", "month") else "ytd"

    data = compute_council_report(
        year=report_year, end_month=report_month, view_mode=view_mode,
    )

    # Balance sheet: compare the two most recent snapshots
    bs_data = _load_balance_sheet_comparison()

    return templates.TemplateResponse(
        request,
        "council_report.html",
        {
            "data": data,
            "bs_data": bs_data,
            "selected_year": report_year,
            "selected_month": report_month,
            "selected_view": view_mode,
            "current_year": today.year,
        },
    )


def _load_balance_sheet_comparison() -> BalanceSheetData:
    """Load the two most recent balance sheet snapshots and compute changes.

    Returns an empty BalanceSheetData if fewer than two snapshots exist.
    """
    snapshots = find_balance_sheet_snapshots()
    if len(snapshots) < 2:
        return BalanceSheetData()

    current_date = snapshots[0][0]  # newest
    prior_date = snapshots[1][0]    # second newest

    return compute_balance_sheet_changes(current_date, prior_date)
