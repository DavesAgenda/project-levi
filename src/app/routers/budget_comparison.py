"""FastAPI router for the budget comparison view."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.budget_comparison import compute_budget_comparison

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/budget", tags=["budget"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/{year}/compare", response_class=HTMLResponse)
async def budget_comparison(
    request: Request,
    year: int,
):
    """Render the budget comparison view — draft vs current vs prior year."""
    data = compute_budget_comparison(target_year=year)

    return templates.TemplateResponse(
        request,
        "budget_comparison.html",
        {
            "data": data,
            "selected_year": year,
        },
    )
