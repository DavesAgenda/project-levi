"""FastAPI router for the property portfolio view."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.property_portfolio import compute_property_portfolio

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/properties", response_class=HTMLResponse)
async def property_portfolio(request: Request):
    """Render the property portfolio — per-property income, costs, net yield."""
    data = compute_property_portfolio()

    return templates.TemplateResponse(
        request,
        "property_portfolio.html",
        {"data": data},
    )


@router.get("/properties/data")
async def property_portfolio_data():
    """Return property portfolio data as JSON for Chart.js."""
    data = compute_property_portfolio()
    if not data.has_data:
        return {"has_data": False}

    # Filter to income-producing properties for charts
    income_properties = [p for p in data.properties if not p.is_warden_occupied]
    all_properties = data.properties

    return {
        "has_data": True,
        "income_vs_costs": {
            "labels": [p.address for p in all_properties],
            "datasets": [
                {
                    "label": "Gross Rent",
                    "data": [p.gross_rent for p in all_properties],
                },
                {
                    "label": "Management Fee",
                    "data": [p.management_fee for p in all_properties],
                },
                {
                    "label": "Maintenance",
                    "data": [p.maintenance_costs for p in all_properties],
                },
                {
                    "label": "Levy Share",
                    "data": [p.levy_share for p in all_properties],
                },
            ],
        },
        "yield_comparison": {
            "labels": [p.address for p in income_properties],
            "data": [p.net_yield_pct or 0 for p in income_properties],
        },
        "budget_comparison": {
            "labels": [p.address for p in income_properties],
            "datasets": [
                {
                    "label": "Budget",
                    "data": [p.budget_gross_rent for p in income_properties],
                },
                {
                    "label": "Actual",
                    "data": [p.gross_rent for p in income_properties],
                },
            ],
        },
    }
