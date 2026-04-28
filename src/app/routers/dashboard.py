"""FastAPI router for the dashboard view."""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import get_current_user
from app.models.auth import User
from app.services.dashboard import compute_dashboard_data
from app.services.drilldown import get_category_drilldown
from app.services.journal_aggregation import aggregate_ytd, aggregation_to_snapshot
from app.xero.oauth import get_stored_tokens, is_token_expired

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
        "_category_key": cat.category_key,
        "_section": section,
    }


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    view_mode: str = Query(default="ytd", description="ytd or full_year"),
    source: str = Query(default="auto", description="auto, journals, or snapshots"),
    budget_mode: str = Query(
        default="prorated",
        description="prorated (YTD months/12) or full_year (match Xero Budget Variance)",
    ),
    year: int = Query(default=None),
    month: int = Query(default=None, ge=1, le=12),
):
    """Render the main dashboard page with KPI cards, charts, and variance table.

    CHA-266: Supports view_mode toggle between YTD (pro-rated) and full_year.
    Also supports journal-based data source when available.

    YTD view accepts year/month to pick a "through month" cutoff so the user
    can exclude the in-progress current month from comparisons.
    """
    today = date.today()
    selected_year = year or today.year
    selected_month = month or today.month

    # For YTD view, aggregate through end of selected month; otherwise full year
    end_month = selected_month if view_mode == "ytd" else None

    # Try journal-based aggregation first if source allows
    snapshot = None
    if source in ("auto", "journals"):
        try:
            agg_result = aggregate_ytd(year=selected_year, end_month=end_month)
            if agg_result.journal_count > 0:
                snapshot = aggregation_to_snapshot(agg_result)
        except Exception:
            pass  # Fall through to snapshot-based

    # Compute with optional YTD pro-rating of budget.
    # budget_mode=full_year skips pro-rating so totals line up with Xero's
    # "Budget Variance" report (YTD actuals vs full-year budget).
    budget_scale = None
    if view_mode == "ytd" and budget_mode == "prorated":
        budget_scale = selected_month / 12.0

    data = compute_dashboard_data(
        snapshot=snapshot,
        budget_scale=budget_scale,
        year=selected_year,
        end_month=end_month,
    )

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

    tokens = get_stored_tokens()
    xero_connected = tokens is not None and not is_token_expired(tokens)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "data": data,
            "income_rows": income_rows,
            "expense_rows": expense_rows,
            "income_summary": income_summary,
            "expense_summary": expense_summary,
            "xero_connected": xero_connected,
            "view_mode": view_mode,
            "source": source,
            "budget_mode": budget_mode,
            "selected_year": selected_year,
            "selected_month": selected_month,
            "current_year": today.year,
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


@router.get("/dashboard/drilldown/{section}/{category_key}", response_class=HTMLResponse)
async def category_drilldown(
    request: Request,
    section: str,
    category_key: str,
    year: int = Query(default=None),
    month: int = Query(default=None, ge=1, le=12),
    view: str = Query(default="month"),
):
    """Return htmx partial with account-level detail for a category (CHA-269).

    Detail level is determined by the user's role:
    - admin: individual transactions
    - board: account totals
    - staff: summary only (no drill-down)
    """
    user = getattr(request.state, "user", None)
    role = user.role if user else "staff"

    drilldown = get_category_drilldown(
        section, category_key, role=role,
        year=year, end_month=month, view_mode=view,
    )

    if drilldown is None:
        return HTMLResponse("<p class='text-caption text-neutral p-4'>Category not found.</p>")

    return templates.TemplateResponse(
        request,
        "partials/category_drilldown.html",
        {"drilldown": drilldown},
    )
