"""Verification report routes (CHA-206).

Provides a UI for cross-checking CSV-imported actuals against Xero API
snapshots, accessible only to admin and board users.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import require_role
from app.models.auth import User
from app.services.verification import get_available_years, verify_year

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/verification", response_class=HTMLResponse)
async def verification_report(
    request: Request,
    year: int | None = Query(default=None, description="Year to verify"),
    user: User = Depends(require_role("admin", "board")),
):
    """Render the verification report — CSV vs Xero snapshot comparison."""
    available_years = get_available_years()

    if year is None and available_years:
        year = available_years[-1]  # default to most recent year

    result = None
    if year is not None:
        result = verify_year(year)

    return templates.TemplateResponse(
        request,
        "verification.html",
        {
            "result": result,
            "available_years": available_years,
            "selected_year": year,
            "user": user,
        },
    )
