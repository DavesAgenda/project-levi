"""FastAPI router for property what-if scenarios."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import require_role
from app.models.auth import User

from app.services.property_scenarios import (
    ScenarioInput,
    compute_scenario,
    load_properties,
    scenarios_from_form,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/budget/scenarios/property", tags=["property-scenarios"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/{year}", response_class=HTMLResponse)
async def property_scenario_panel(request: Request, year: int):
    """Render the property scenario controls panel (full partial)."""
    props = load_properties()
    summary = compute_scenario({})  # base only

    return templates.TemplateResponse(
        request,
        "partials/property_scenarios.html",
        {
            "year": year,
            "properties": props,
            "summary": summary,
            "scenarios": {},
        },
    )


@router.post("/{year}/preview", response_class=HTMLResponse)
async def property_scenario_preview(request: Request, year: int, current_user: User = Depends(require_role("admin"))):
    """Compute scenario preview from form inputs. Returns updated partial via htmx."""
    form = await request.form()
    form_dict = dict(form)

    scenarios = scenarios_from_form(form_dict)
    summary = compute_scenario(scenarios)
    props = load_properties()

    return templates.TemplateResponse(
        request,
        "partials/property_scenarios.html",
        {
            "year": year,
            "properties": props,
            "summary": summary,
            "scenarios": scenarios,
        },
    )
