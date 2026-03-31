"""FastAPI router for the budget comparison view."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import should_redact_payroll
from app.services.budget_comparison import compute_budget_comparison

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

# Payroll-related category keys in chart_of_accounts.yaml
_PAYROLL_CATEGORY_KEYS = {"ministry_staff", "ministry_support", "admin_staff"}

router = APIRouter(prefix="/budget", tags=["budget"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/{year}/compare", response_class=HTMLResponse)
async def budget_comparison(
    request: Request,
    year: int,
):
    """Render the budget comparison view — draft vs current vs prior year."""
    data = compute_budget_comparison(target_year=year)

    user = getattr(request.state, "user", None)
    redact = should_redact_payroll(user)

    return templates.TemplateResponse(
        request,
        "budget_comparison.html",
        {
            "data": data,
            "selected_year": year,
            "redact_payroll": redact,
            "payroll_category_keys": _PAYROLL_CATEGORY_KEYS,
        },
    )
