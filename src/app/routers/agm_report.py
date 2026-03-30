"""FastAPI router for the AGM report view."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.agm_report import compute_agm_report

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/agm/{year}", response_class=HTMLResponse)
async def agm_report(request: Request, year: int):
    """Render the AGM report — annual actuals vs budget with multi-year trends."""
    data = compute_agm_report(year=year)

    return templates.TemplateResponse(
        request,
        "agm_report.html",
        {
            "data": data,
            "selected_year": year,
            "current_year": date.today().year,
        },
    )


@router.get("/agm/{year}/data")
async def agm_report_data(request: Request, year: int):
    """Return AGM report data as JSON for Chart.js consumption."""
    data = compute_agm_report(year=year)

    if not data.has_data:
        return {"has_data": False}

    return {
        "has_data": True,
        "year": data.year,
        "trend_years": data.trend_years,
        "income_trend": [t.total_income for t in data.trend_data],
        "expense_trend": [t.total_expenses for t in data.trend_data],
        "net_trend": [t.net_position for t in data.trend_data],
        "income_categories": [
            {
                "label": r.budget_label,
                "trend_values": r.trend_values,
            }
            for r in data.income_rows
        ],
        "expense_categories": [
            {
                "label": r.budget_label,
                "trend_values": r.trend_values,
            }
            for r in data.expense_rows
        ],
    }
