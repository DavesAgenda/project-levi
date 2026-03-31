"""FastAPI router for the payroll summary view."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.payroll import compute_payroll_data

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(tags=["payroll"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _staff_to_row(staff) -> dict:
    """Convert a StaffCost to a dict for the sortable table component."""
    return {
        "name": staff.name,
        "role": staff.role,
        "fte": staff.fte,
        "base_salary": staff.base_salary,
        "super_amount": staff.super_amount,
        "pcr": staff.pcr,
        "allowances": staff.allowances,
        "recoveries": staff.recoveries,
        "total_cost": staff.total_cost,
        "net_cost": staff.net_cost,
        "diocese_grade": staff.diocese_grade or "—",
    }


def _category_to_row(cat) -> dict:
    """Convert a PayrollCategoryActuals to a dict for the variance table."""
    return {
        "label": cat.label,
        "actual": cat.actual,
        "budget": cat.budget,
        "variance_dollar": cat.variance_dollar,
        "variance_pct": cat.variance_pct,
        "_status": cat.status,
        "_variance_positive": cat.variance_dollar < 0,  # under budget is good for expenses
        "_variance_negative": cat.variance_dollar > 0,  # over budget is bad for expenses
    }


@router.get("/reports/payroll", response_class=HTMLResponse)
async def payroll_summary(request: Request):
    """Render the payroll summary report page."""
    data = compute_payroll_data()

    # Payroll detail redaction flag (CHA-204 prep):
    # admin + board see full detail; staff gets redacted
    user = getattr(request.state, "user", None)
    redact_payroll = True  # default: redact
    if user is not None and user.has_permission("payroll_detail"):
        redact_payroll = False

    staff_rows = [_staff_to_row(s) for s in data.staff]
    category_rows = [_category_to_row(c) for c in data.category_actuals]

    # Totals for summary rows
    staff_summary = {
        "name": "Total",
        "role": "",
        "fte": round(sum(s.fte for s in data.staff), 2),
        "base_salary": round(sum(s.base_salary for s in data.staff), 2),
        "super_amount": round(sum(s.super_amount for s in data.staff), 2),
        "pcr": round(sum(s.pcr for s in data.staff), 2),
        "allowances": round(sum(s.allowances for s in data.staff), 2),
        "recoveries": data.total_recoveries,
        "total_cost": data.total_payroll_cost,
        "net_cost": data.net_payroll_cost,
        "diocese_grade": "",
    }

    total_actual = sum(c.actual for c in data.category_actuals)
    total_budget = sum(c.budget for c in data.category_actuals)
    total_var = round(total_actual - total_budget, 2)
    category_summary = {
        "label": "Total Payroll",
        "actual": round(total_actual, 2),
        "budget": round(total_budget, 2),
        "variance_dollar": total_var,
        "variance_pct": round(total_var / total_budget * 100, 1) if total_budget > 0 else None,
    }

    return templates.TemplateResponse(
        request,
        "payroll.html",
        {
            "data": data,
            "staff_rows": staff_rows,
            "staff_summary": staff_summary,
            "category_rows": category_rows,
            "category_summary": category_summary,
            "redact_payroll": redact_payroll,
        },
    )
