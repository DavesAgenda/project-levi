"""Budget approval workflow routes (CHA-196).

Endpoints for status transitions (draft -> proposed -> approved)
and changelog/history viewing. The main budget CRUD routes live
in the budget router (CHA-193).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import require_role
from app.models.auth import User

from app.models.budget import BudgetStatus
from app.services.budget import (
    BudgetNotFoundError,
    BudgetServiceError,
    BudgetStatusError,
    BudgetConcurrencyError,
    BudgetValidationError,
    get_budget_mtime,
    load_budget_file,
    load_changelog,
    transition_status,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(tags=["budget-workflow"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.post("/budget/{year}/transition")
async def budget_transition(
    year: int,
    new_status: str = Form(...),
    current_user: User = Depends(require_role("admin")),
):
    """Transition a budget's status (draft->proposed->approved, or revert).

    Returns JSON with the new status and changelog summary.
    """
    user = current_user.email

    # Validate year range
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail=f"Year {year} out of valid range")

    # Validate target status
    try:
        target = BudgetStatus(new_status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {new_status}. Must be one of: draft, proposed, approved",
        )

    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    mtime = get_budget_mtime(year)

    # Determine if this is a revert (proposed -> draft) which needs override=False
    # The model allows proposed->draft natively, so no override needed.
    # Only approved->anything needs override.
    override = False

    try:
        updated = transition_status(
            budget,
            target,
            override=override,
            expected_mtime=mtime,
            user=user,
        )
    except BudgetStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BudgetConcurrencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "year": year,
        "status": updated.status.value,
        "approved_date": updated.approved_date.isoformat() if updated.approved_date else None,
        "message": f"Budget {year} is now {updated.status.value}",
    }


@router.post("/budget/{year}/create-amendment")
async def create_amendment(
    year: int,
    current_user: User = Depends(require_role("admin")),
):
    """Create an amendment from an approved budget — reverts to draft with override."""
    user = current_user.email
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    if budget.status != BudgetStatus.approved:
        raise HTTPException(
            status_code=400,
            detail=f"Can only amend approved budgets (current status: {budget.status.value})",
        )

    mtime = get_budget_mtime(year)

    try:
        updated = transition_status(
            budget,
            BudgetStatus.draft,
            override=True,
            expected_mtime=mtime,
            user=user,
        )
    except BudgetConcurrencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "year": year,
        "status": updated.status.value,
        "message": f"Amendment created — budget {year} reverted to draft",
    }


@router.get("/budget/{year}/history", response_class=HTMLResponse)
async def budget_history(request: Request, year: int):
    """Show changelog/history for a budget year."""
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail=f"Year {year} out of valid range")
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    changelog = load_changelog(year)

    return templates.TemplateResponse(
        request,
        "partials/budget_history.html",
        {
            "year": year,
            "budget": budget,
            "changelog": changelog,
        },
    )


@router.get("/budget/{year}/status")
async def budget_status(year: int):
    """Return the current status of a budget (JSON, for htmx polling)."""
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    return {
        "year": year,
        "status": budget.status.value,
        "approved_date": budget.approved_date.isoformat() if budget.approved_date else None,
        "editable": budget.status == BudgetStatus.draft,
    }
