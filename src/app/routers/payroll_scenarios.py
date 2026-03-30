"""FastAPI router for payroll what-if scenario modelling."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.payroll_scenarios import (
    PayrollScenario,
    add_staff,
    apply_step_change,
    apply_uplift,
    change_fte,
    compute_scenario,
    load_scenario_from_config,
    remove_staff,
    restore_staff,
    save_scenario_to_config,
    update_diocese_scales,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/budget/payroll-scenarios", tags=["payroll-scenarios"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-memory scenario state (single-user tool, no DB needed)
_active_scenario: PayrollScenario | None = None


def _get_scenario() -> PayrollScenario:
    """Get the active scenario, loading from config if needed."""
    global _active_scenario
    if _active_scenario is None:
        _active_scenario = load_scenario_from_config()
    return _active_scenario


def _reset_scenario() -> PayrollScenario:
    """Reset scenario to current config state."""
    global _active_scenario
    _active_scenario = load_scenario_from_config()
    return _active_scenario


def _build_context(scenario: PayrollScenario) -> dict:
    """Build template context from scenario."""
    result = compute_scenario(scenario)
    return {
        "scenario": scenario,
        "result": result,
        "diocese": scenario.diocese_scales,
        "staff_entries": scenario.staff,
        "scenario_staff": result.scenario_staff,
        "baseline_total": result.baseline_total,
        "scenario_total": result.scenario_total,
        "delta": result.delta,
        "delta_pct": result.delta_pct,
        "baseline_net": result.baseline_net,
        "scenario_net": result.scenario_net,
        "delta_net": result.delta_net,
        "staff_changes": result.staff_changes,
        "has_changes": len(result.staff_changes) > 0,
    }


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def payroll_scenarios_page(request: Request):
    """Render the payroll scenarios page."""
    scenario = _get_scenario()
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


# ---------------------------------------------------------------------------
# Diocese scale editing
# ---------------------------------------------------------------------------

@router.post("/diocese-scales", response_class=HTMLResponse)
async def update_diocese(
    request: Request,
    source: str = Form(""),
    year: int = Form(2026),
    uplift_factor: float = Form(0.0),
    notes: str = Form(""),
):
    """Update diocese scale settings."""
    scenario = _get_scenario()
    update_diocese_scales(
        scenario,
        source=source,
        year=year,
        uplift_factor=uplift_factor,
        notes=notes,
    )
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


# ---------------------------------------------------------------------------
# Staff CRUD
# ---------------------------------------------------------------------------

@router.post("/staff/add", response_class=HTMLResponse)
async def add_staff_position(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    fte: float = Form(1.0),
    base_salary: float = Form(0.0),
    super_rate: float = Form(0.115),
    grade: str = Form(""),
):
    """Add a new staff position to the scenario."""
    scenario = _get_scenario()
    add_staff(
        scenario,
        name=name,
        role=role,
        fte=fte,
        base_salary=base_salary,
        super_rate=super_rate,
        grade=grade or None,
    )
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/staff/{staff_name}/remove", response_class=HTMLResponse)
async def remove_staff_position(request: Request, staff_name: str):
    """Remove a staff position from the scenario."""
    scenario = _get_scenario()
    try:
        remove_staff(scenario, staff_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/staff/{staff_name}/restore", response_class=HTMLResponse)
async def restore_staff_position(request: Request, staff_name: str):
    """Restore a previously removed staff member."""
    scenario = _get_scenario()
    try:
        restore_staff(scenario, staff_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/staff/{staff_name}/fte", response_class=HTMLResponse)
async def update_staff_fte(
    request: Request,
    staff_name: str,
    fte: float = Form(...),
):
    """Change FTE for a staff member."""
    scenario = _get_scenario()
    try:
        change_fte(scenario, staff_name, fte)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/staff/{staff_name}/step", response_class=HTMLResponse)
async def update_staff_step(
    request: Request,
    staff_name: str,
    grade: str = Form(...),
):
    """Apply a salary scale step change."""
    scenario = _get_scenario()
    try:
        apply_step_change(scenario, staff_name, grade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


# ---------------------------------------------------------------------------
# Uplift
# ---------------------------------------------------------------------------

@router.post("/uplift", response_class=HTMLResponse)
async def apply_uplift_all(request: Request):
    """Apply diocese uplift to all staff base salaries."""
    scenario = _get_scenario()
    apply_uplift(scenario)
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/staff/{staff_name}/uplift", response_class=HTMLResponse)
async def apply_uplift_single(request: Request, staff_name: str):
    """Apply diocese uplift to a single staff member."""
    scenario = _get_scenario()
    apply_uplift(scenario, name=staff_name)
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


# ---------------------------------------------------------------------------
# Impact preview (JSON API)
# ---------------------------------------------------------------------------

@router.get("/preview")
async def scenario_preview():
    """Return scenario impact as JSON (for programmatic use)."""
    scenario = _get_scenario()
    result = compute_scenario(scenario)
    return {
        "baseline_total": result.baseline_total,
        "scenario_total": result.scenario_total,
        "delta": result.delta,
        "delta_pct": result.delta_pct,
        "baseline_net": result.baseline_net,
        "scenario_net": result.scenario_net,
        "delta_net": result.delta_net,
        "staff_changes": result.staff_changes,
    }


# ---------------------------------------------------------------------------
# Save / Reset
# ---------------------------------------------------------------------------

@router.post("/save", response_class=HTMLResponse)
async def save_scenario(request: Request):
    """Save the current scenario to payroll.yaml."""
    scenario = _get_scenario()
    save_scenario_to_config(scenario)
    ctx = _build_context(scenario)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)


@router.post("/reset", response_class=HTMLResponse)
async def reset_scenario(request: Request):
    """Reset the scenario to the current payroll.yaml state."""
    scenario = _reset_scenario()
    ctx = _build_context(scenario)
    return templates.TemplateResponse(request, "partials/payroll_scenarios.html", ctx)
