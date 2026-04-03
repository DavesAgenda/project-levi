"""Account mapping router — admin UI for managing chart of accounts (CHA-268).

Provides full-page HTML at /settings/accounts and htmx partials for
all CRUD operations on budget categories and their account mappings.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies.auth import require_role
from app.models.auth import User
from app.services.account_mapping import (
    add_account,
    create_category,
    delete_category,
    list_categories,
    move_account,
    remove_account,
    rename_category,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------


@router.get("/accounts", response_class=HTMLResponse)
async def account_mapping_page(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Render the full account mapping management page."""
    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "account_mapping.html",
        {"categories": categories, "user": user},
    )


# ---------------------------------------------------------------------------
# htmx partial — category list
# ---------------------------------------------------------------------------


@router.get("/accounts/categories", response_class=HTMLResponse)
async def category_list_partial(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Return the category list partial for htmx swap."""
    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


# ---------------------------------------------------------------------------
# Category CRUD
# ---------------------------------------------------------------------------


@router.post("/accounts/category", response_class=HTMLResponse)
async def create_category_endpoint(
    request: Request,
    section: str = Form(...),
    budget_label: str = Form(...),
    key: str = Form(default=""),
    user: User = Depends(require_role("admin")),
):
    """Create a new empty category."""
    try:
        create_category(section, budget_label, key=key or None)
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


@router.put("/accounts/category/{section}/{key}", response_class=HTMLResponse)
async def rename_category_endpoint(
    request: Request,
    section: str,
    key: str,
    new_label: str = Form(...),
    user: User = Depends(require_role("admin")),
):
    """Rename a category's budget label."""
    try:
        rename_category(section, key, new_label)
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


@router.delete("/accounts/category/{section}/{key}", response_class=HTMLResponse)
async def delete_category_endpoint(
    request: Request,
    section: str,
    key: str,
    user: User = Depends(require_role("admin")),
):
    """Delete an empty category."""
    try:
        delete_category(section, key)
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


# ---------------------------------------------------------------------------
# Account operations
# ---------------------------------------------------------------------------


@router.post("/accounts/account", response_class=HTMLResponse)
async def add_account_endpoint(
    request: Request,
    section: str = Form(...),
    category: str = Form(...),
    code: str = Form(...),
    name: str = Form(...),
    account_type: str = Form(default="current"),
    user: User = Depends(require_role("admin")),
):
    """Add an account to a category."""
    try:
        add_account(
            section,
            category,
            code,
            name,
            is_legacy=(account_type == "legacy"),
            is_property=(account_type == "property"),
        )
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


@router.delete("/accounts/account/{section}/{category}/{code}", response_class=HTMLResponse)
async def remove_account_endpoint(
    request: Request,
    section: str,
    category: str,
    code: str,
    user: User = Depends(require_role("admin")),
):
    """Remove an account from a category."""
    try:
        remove_account(section, category, code)
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )


@router.post("/accounts/move", response_class=HTMLResponse)
async def move_account_endpoint(
    request: Request,
    from_section: str = Form(...),
    from_category: str = Form(...),
    to_section: str = Form(...),
    to_category: str = Form(...),
    code: str = Form(...),
    target_list: str = Form(default="accounts"),
    user: User = Depends(require_role("admin")),
):
    """Move an account between categories."""
    try:
        move_account(
            from_section, from_category,
            to_section, to_category,
            code,
            target_list=target_list,
        )
    except (ValueError, KeyError) as e:
        categories = list_categories()
        return templates.TemplateResponse(
            request,
            "partials/category_list.html",
            {"categories": categories, "error": str(e)},
        )

    categories = list_categories()
    return templates.TemplateResponse(
        request,
        "partials/category_list.html",
        {"categories": categories},
    )
