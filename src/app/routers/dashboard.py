"""FastAPI router for the dashboard view."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.dashboard import compute_dashboard_data

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _category_to_row(cat, section: str) -> dict:
    """Convert a CategoryVariance to a dict for the sortable table component."""
    return {
        "label": cat.budget_label,
        "actual": cat.actual,
        "budget": cat.budget,
        "variance_dollar": cat.variance_dollar,
        "variance_pct": cat.variance_pct,
        "_status": cat.status,
        "_variance_positive": (
            cat.variance_dollar > 0 if section == "income" else cat.variance_dollar < 0
        ),
        "_variance_negative": (
            cat.variance_dollar < 0 if section == "income" else cat.variance_dollar > 0
        ),
    }


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page with KPI cards, charts, and variance table."""
    data = compute_dashboard_data()

    income_rows = [_category_to_row(c, "income") for c in data.income_categories]
    expense_rows = [_category_to_row(c, "expenses") for c in data.expense_categories]

    income_var = data.total_income - data.budget_total_income
    expense_var = data.total_expenses - data.budget_total_expenses

    income_summary = {
        "label": "Total Income",
        "actual": data.total_income,
        "budget": data.budget_total_income,
        "variance_dollar": income_var,
        "variance_pct": (
            round(income_var / data.budget_total_income * 100, 1)
            if data.budget_total_income > 0
            else None
        ),
    }
    expense_summary = {
        "label": "Total Expenses",
        "actual": data.total_expenses,
        "budget": data.budget_total_expenses,
        "variance_dollar": expense_var,
        "variance_pct": (
            round(expense_var / data.budget_total_expenses * 100, 1)
            if data.budget_total_expenses > 0
            else None
        ),
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "data": data,
            "income_rows": income_rows,
            "expense_rows": expense_rows,
            "income_summary": income_summary,
            "expense_summary": expense_summary,
        },
    )


@router.get("/dashboard/data")
async def dashboard_data():
    """Return dashboard data as JSON for Chart.js or htmx partial updates."""
    data = compute_dashboard_data()
    if not data.has_data:
        return {"has_data": False}

    income_cats = data.income_categories
    expense_cats = data.expense_categories

    return {
        "has_data": True,
        "summary": {
            "total_income": data.total_income,
            "total_expenses": data.total_expenses,
            "net_position": data.net_position,
            "budget_consumed_pct": data.budget_consumed_pct,
        },
        "budget_vs_actuals": {
            "labels": [c.budget_label for c in expense_cats],
            "datasets": [
                {"label": "Budget", "data": [c.budget for c in expense_cats]},
                {"label": "Actual", "data": [c.actual for c in expense_cats]},
            ],
        },
        "income_vs_expenses": {
            "labels": ["Income", "Expenses"],
            "actual": [data.total_income, data.total_expenses],
            "budget": [data.budget_total_income, data.budget_total_expenses],
        },
    }
