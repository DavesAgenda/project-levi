"""FastAPI router for budget viewing and editing."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import require_role, should_redact_payroll
from app.models.auth import User

from app.models.budget import BudgetFile, BudgetSection, BudgetStatus
from app.services.budget import (
    BudgetNotFoundError,
    BudgetServiceError,
    BudgetStatusError,
    compute_payroll_budget,
    compute_property_income,
    create_draft_budget,
    get_budget_mtime,
    load_budget_file,
    save_budget_file,
    transition_status,
)
from app.services.budget_forecast import compute_forecast, list_budget_years

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/budget", tags=["budget"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Canonical section ordering and labels
INCOME_SECTIONS = [
    ("offertory", "Offertory"),
    ("property_income", "Property Income"),
    ("building_hire", "Building Hire"),
    ("ministry_income", "Ministry Income"),
    ("other_income", "Other Income"),
]

EXPENSE_SECTIONS = [
    ("payroll", "Payroll"),
    ("ministry_expenses", "Ministry Expenses"),
    ("mission_giving", "Mission Giving"),
    ("administration", "Administration"),
    ("operations", "Operations"),
    ("property_maintenance", "Property Maintenance"),
    ("diocesan", "Diocesan"),
]


def _section_total(section: BudgetSection) -> float | None:
    """Sum account items. Returns None if all items are None (TBD)."""
    items = section.account_items()
    if not items:
        return None
    values = [v for v in items.values() if v is not None]
    if not values:
        return None
    return sum(values)


def _is_valid_item_key(key: str) -> bool:
    """Validate that an item key matches expected patterns.

    Valid patterns: '12345_description' (account code + label) or simple
    alphanumeric/underscore keys. Rejects keys with path separators,
    dots, or special characters that could be used for injection.
    """
    import re
    return bool(re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_]{0,100}$", key))


def _is_valid_section_key(key: str) -> bool:
    """Validate section key is alphanumeric with underscores only."""
    import re
    return bool(re.match(r"^[a-z][a-z0-9_]{0,50}$", key))


def _validate_year(year: int) -> None:
    """Validate year is within a reasonable range."""
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail=f"Year {year} is out of valid range (2000-2100)")


def _format_key(key: str) -> str:
    """Turn '10001_offering_eft' into 'Offering Eft'."""
    parts = key.split("_", 1)
    label = parts[1] if len(parts) > 1 and parts[0].isdigit() else key
    return label.replace("_", " ").title()


def _build_view_data(budget: BudgetFile) -> dict:
    """Build template context from a BudgetFile."""
    property_income = compute_property_income(budget)
    payroll_budget = compute_payroll_budget()

    income_sections = []
    income_total = 0.0
    for key, label in INCOME_SECTIONS:
        section = budget.income.get(key, BudgetSection())
        items = section.account_items()
        # For property_income, merge computed values
        if key == "property_income":
            computed_items = {
                f"prop_{k}": v for k, v in property_income.items()
            }
            display_items = []
            for ik, iv in computed_items.items():
                display_items.append({
                    "key": ik,
                    "label": ik.replace("prop_", "").replace("_", " ").title(),
                    "amount": iv,
                    "computed": True,
                })
                if iv is not None:
                    income_total += iv
            # Also include any manual overrides stored in account items
            for ik, iv in items.items():
                display_items.append({
                    "key": ik,
                    "label": _format_key(ik),
                    "amount": iv,
                    "computed": False,
                })
                if iv is not None:
                    income_total += iv
        else:
            display_items = []
            for ik, iv in items.items():
                display_items.append({
                    "key": ik,
                    "label": _format_key(ik),
                    "amount": iv,
                    "computed": False,
                })
                if iv is not None:
                    income_total += iv

        income_sections.append({
            "key": key,
            "label": label,
            "line_items": display_items,
            "notes": section.notes,
            "total": _section_total(section),
        })

    expense_sections = []
    expense_total = 0.0
    for key, label in EXPENSE_SECTIONS:
        section = budget.expenses.get(key, BudgetSection())
        items = section.account_items()

        if key == "payroll":
            display_items = []
            for pk, pv in payroll_budget.items():
                if pk == "_total":
                    continue
                display_items.append({
                    "key": f"payroll_{pk}",
                    "label": pk,
                    "amount": pv,
                    "computed": True,
                })
            payroll_total = payroll_budget.get("_total", 0)
            expense_total += payroll_total
            for ik, iv in items.items():
                display_items.append({
                    "key": ik,
                    "label": _format_key(ik),
                    "amount": iv,
                    "computed": False,
                })
                if iv is not None:
                    expense_total += iv
        else:
            display_items = []
            for ik, iv in items.items():
                display_items.append({
                    "key": ik,
                    "label": _format_key(ik),
                    "amount": iv,
                    "computed": False,
                })
                if iv is not None:
                    expense_total += iv

        expense_sections.append({
            "key": key,
            "label": label,
            "line_items": display_items,
            "notes": section.notes,
            "total": _section_total(section) if key != "payroll" else payroll_budget.get("_total"),
        })

    return {
        "budget": budget,
        "income_sections": income_sections,
        "expense_sections": expense_sections,
        "income_total": income_total,
        "expense_total": expense_total,
        "surplus": income_total - expense_total,
    }


def _build_reference_data(
    year: int,
    income_sections: list[dict],
    expense_sections: list[dict],
) -> dict:
    """Build prior-year reference data (forecast + budget) for comparison columns.

    For budget year Y, loads:
    - {Y-1} forecast (annualized actuals)
    - {Y-1} budget amounts

    Returns a dict of reference data keyed by section_key, with section-level
    totals for forecast, prior budget, and variance.
    """
    prior_year = year - 1

    # Load prior year forecast (annualized actuals)
    prior_forecast = compute_forecast(prior_year)

    # Load prior year budget (flat by category key)
    from app.services.budget import load_budget_flat
    prior_budget_flat = load_budget_flat(prior_year)

    # Load prior year structured budget for section-level amounts
    prior_budget_sections: dict[str, dict[str, float | None]] = {}
    try:
        prior_budget = load_budget_file(prior_year)
        prior_property_income = compute_property_income(prior_budget)
        prior_payroll = compute_payroll_budget()

        for key, _label in INCOME_SECTIONS:
            section = prior_budget.income.get(key, BudgetSection())
            items = section.account_items()
            sec_data: dict[str, float | None] = {}
            if key == "property_income":
                for pk, pv in prior_property_income.items():
                    sec_data[f"prop_{pk}"] = pv
            for ik, iv in items.items():
                sec_data[ik] = iv
            prior_budget_sections[key] = sec_data

        for key, _label in EXPENSE_SECTIONS:
            section = prior_budget.expenses.get(key, BudgetSection())
            items = section.account_items()
            sec_data = {}
            if key == "payroll":
                for pk, pv in prior_payroll.items():
                    if pk == "_total":
                        continue
                    sec_data[f"payroll_{pk}"] = pv
            for ik, iv in items.items():
                sec_data[ik] = iv
            prior_budget_sections[key] = sec_data

    except BudgetNotFoundError:
        pass

    # Build per-section reference with totals
    ref_sections: dict[str, dict] = {}

    # Collect all category-level forecast/budget for section totals
    # The forecast data is keyed by category_key, not section_key.
    # We need to distribute forecast across sections based on the chart
    # of accounts mapping. For simplicity, we aggregate section totals
    # from the line-item level using prior_budget_sections.

    all_sections = [
        ("income", income_sections),
        ("expenses", expense_sections),
    ]

    prior_forecast_income_total = 0.0
    prior_forecast_expense_total = 0.0
    prior_budget_income_total = 0.0
    prior_budget_expense_total = 0.0

    for section_type, sections in all_sections:
        for section in sections:
            skey = section["key"]
            sec_ref: dict[str, dict] = {}
            sec_forecast_total = 0.0
            sec_budget_total = 0.0

            for item in section["line_items"]:
                ikey = item["key"]
                # Prior budget for this item
                pb = None
                if skey in prior_budget_sections:
                    pb = prior_budget_sections[skey].get(ikey)

                # We don't have per-item forecast — forecast is category-level.
                # For the reference columns we show section totals only for
                # forecast, but item-level for prior budget.
                sec_ref[ikey] = {
                    "prior_budget": pb,
                }
                if pb is not None:
                    sec_budget_total += pb

            ref_sections[skey] = {
                "line_items": sec_ref,
                "budget_total": sec_budget_total,
            }

            if section_type == "income":
                prior_budget_income_total += sec_budget_total
            else:
                prior_budget_expense_total += sec_budget_total

    # Sum forecast totals from category-level data
    # We need to map category keys to income/expense
    try:
        from app.csv_import import load_chart_of_accounts
        from app.services.budget import CHART_PATH
        if CHART_PATH.exists():
            chart = load_chart_of_accounts(CHART_PATH)
            for section_name, section_field in [("income", chart.income), ("expenses", chart.expenses)]:
                for cat_key in section_field:
                    fv = prior_forecast.get(cat_key, 0.0)
                    if section_name == "income":
                        prior_forecast_income_total += fv
                    else:
                        prior_forecast_expense_total += fv
    except Exception:
        for _cat_key, fv in prior_forecast.items():
            prior_forecast_income_total += fv  # fallback — imprecise

    return {
        "prior_year": prior_year,
        "ref_sections": ref_sections,
        "prior_forecast": prior_forecast,
        "prior_forecast_income_total": round(prior_forecast_income_total, 2),
        "prior_forecast_expense_total": round(prior_forecast_expense_total, 2),
        "prior_budget_income_total": round(prior_budget_income_total, 2),
        "prior_budget_expense_total": round(prior_budget_expense_total, 2),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{year}", response_class=HTMLResponse)
async def budget_view(request: Request, year: int, edit: bool = Query(False)):
    """Render budget view (or edit mode if ?edit=true)."""
    _validate_year(year)
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    # Only allow edit mode for draft budgets
    is_draft = budget.status == BudgetStatus.draft
    if edit and not is_draft:
        edit = False

    mtime = get_budget_mtime(year)
    ctx = _build_view_data(budget)

    # Year selector data
    budget_years = list_budget_years()

    # Reference data (prior year forecast + budget)
    ref_data = _build_reference_data(
        year,
        ctx["income_sections"],
        ctx["expense_sections"],
    )

    # Payroll redaction (CHA-204): staff sees single total line
    user = getattr(request.state, "user", None)
    redact = should_redact_payroll(user)

    ctx.update({
        "year": year,
        "edit_mode": edit,
        "mtime": mtime,
        "budget_years": budget_years,
        "is_draft": is_draft,
        "ref": ref_data,
        "redact_payroll": redact,
    })
    return templates.TemplateResponse(request, "budget.html", ctx)


@router.put("/{year}/line/{section_type}/{section_key}/{item_key}", response_class=HTMLResponse)
async def update_budget_line(
    request: Request,
    year: int,
    section_type: str,
    section_key: str,
    item_key: str,
    value: str = Form(""),
    mtime: float = Form(...),
    current_user: User = Depends(require_role("admin")),
):
    """Inline update a single budget line item. Returns updated partial."""
    _validate_year(year)
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    # Validate section_type
    if section_type not in ("income", "expenses"):
        raise HTTPException(status_code=400, detail="section_type must be 'income' or 'expenses'")

    # Validate item_key: must match account pattern (digits_name) or known meta keys
    if not _is_valid_item_key(item_key):
        raise HTTPException(status_code=400, detail=f"Invalid item key: {item_key}")

    # Parse value: empty string -> None (TBD), otherwise float
    if value.strip() == "":
        parsed_value = None
    else:
        try:
            parsed_value = float(value.strip().replace(",", ""))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid number: {value}")
        # Reject extreme values
        if abs(parsed_value) > 100_000_000:
            raise HTTPException(status_code=422, detail="Amount exceeds maximum allowed value")

    # Get or create the section
    sections = budget.income if section_type == "income" else budget.expenses
    if section_key not in sections:
        sections[section_key] = BudgetSection()

    section = sections[section_key]
    # Update via model_extra (since account items are stored as extra fields)
    if section.model_extra is None:
        object.__setattr__(section, "__pydantic_extra__", {})
    section.model_extra[item_key] = parsed_value

    # Save
    new_mtime_before = get_budget_mtime(year)
    save_budget_file(
        budget,
        expected_mtime=mtime,
        user=current_user.email,
        summary=f"Updated {section_key}.{item_key} to {parsed_value}",
    )
    new_mtime = get_budget_mtime(year)

    # Return the updated line partial
    return templates.TemplateResponse(request, "partials/budget_line.html", {
        "year": year,
        "section_type": section_type,
        "section_key": section_key,
        "item": {
            "key": item_key,
            "label": _format_key(item_key),
            "amount": parsed_value,
            "computed": False,
        },
        "edit_mode": True,
        "mtime": new_mtime,
    })


@router.put("/{year}/notes/{section_type}/{section_key}", response_class=HTMLResponse)
async def update_section_notes(
    request: Request,
    year: int,
    section_type: str,
    section_key: str,
    notes: str = Form(""),
    mtime: float = Form(...),
    current_user: User = Depends(require_role("admin")),
):
    """Update section notes."""
    _validate_year(year)
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    # Validate section_type
    if section_type not in ("income", "expenses"):
        raise HTTPException(status_code=400, detail="section_type must be 'income' or 'expenses'")

    sections = budget.income if section_type == "income" else budget.expenses
    if section_key not in sections:
        sections[section_key] = BudgetSection()

    # Sanitize notes: strip HTML tags to prevent stored XSS
    import re
    clean_notes = re.sub(r"<[^>]+>", "", notes.strip()) if notes.strip() else None
    sections[section_key].notes = clean_notes

    save_budget_file(
        budget,
        expected_mtime=mtime,
        user=current_user.email,
        summary=f"Updated notes for {section_key}",
    )
    new_mtime = get_budget_mtime(year)

    from markupsafe import escape
    display_notes = escape(clean_notes) if clean_notes else "No notes"
    return HTMLResponse(
        content=f'<span class="text-caption text-neutral italic">{display_notes}</span>'
        f'<input type="hidden" name="mtime" value="{new_mtime}">'
    )


@router.post("/{year}/status", response_class=HTMLResponse)
async def change_status(
    request: Request,
    year: int,
    target: str = Form(...),
    mtime: float = Form(...),
    current_user: User = Depends(require_role("admin")),
):
    """Transition budget status."""
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    try:
        target_status = BudgetStatus(target)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid status: {target}")

    try:
        budget = transition_status(
            budget,
            target_status,
            expected_mtime=mtime,
            user=current_user.email,
        )
    except BudgetStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Redirect to view page
    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": f"/budget/{year}"},
        content="",
    )


@router.post("/{year}/unlock", response_class=HTMLResponse)
async def unlock_budget(
    request: Request,
    year: int,
    mtime: float = Form(...),
    current_user: User = Depends(require_role("admin")),
):
    """Unlock a non-draft budget by reverting it to draft status.

    This is triggered by the confirmation dialog when a user wants to
    edit an approved or proposed budget.
    """
    _validate_year(year)
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    if budget.status == BudgetStatus.draft:
        # Already a draft — just redirect to edit mode
        return HTMLResponse(
            status_code=200,
            headers={"HX-Redirect": f"/budget/{year}?edit=true"},
            content="",
        )

    try:
        budget = transition_status(
            budget,
            BudgetStatus.draft,
            override=True,
            expected_mtime=mtime,
            user=current_user.email,
        )
    except BudgetStatusError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": f"/budget/{year}?edit=true"},
        content="",
    )


@router.post("/create-draft", response_class=HTMLResponse)
async def create_draft(
    request: Request,
    year: int = Form(...),
    base_year: int = Form(0),
    current_user: User = Depends(require_role("admin")),
):
    """Create a new draft budget, optionally cloned from a prior year."""
    try:
        create_draft_budget(
            year,
            base_year=base_year if base_year > 0 else None,
            user=current_user.email,
        )
    except BudgetServiceError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": f"/budget/{year}?edit=true"},
        content="",
    )


@router.get("/{year}/totals", response_class=HTMLResponse)
async def budget_totals(request: Request, year: int):
    """Return updated totals partial for live recalculation."""
    try:
        budget = load_budget_file(year)
    except BudgetNotFoundError:
        raise HTTPException(status_code=404, detail=f"No budget found for {year}")

    ctx = _build_view_data(budget)
    surplus_class = "text-success" if ctx["surplus"] >= 0 else "text-danger"
    return HTMLResponse(content=(
        f'<span class="font-mono text-figure-lg text-success">${ctx["income_total"]:,.0f}</span>'
        f'<span class="mx-2 text-neutral">\u2212</span>'
        f'<span class="font-mono text-figure-lg text-danger">${ctx["expense_total"]:,.0f}</span>'
        f'<span class="mx-2 text-neutral">=</span>'
        f'<span class="font-mono text-figure-lg {surplus_class}">${ctx["surplus"]:,.0f}</span>'
    ))
