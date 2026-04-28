"""FastAPI router for the Tracking Category Matrix report.

Endpoints:
    GET /reports/tracking-matrix          — Full page with category picker + matrix
    GET /reports/tracking-matrix/partial  — htmx partial for category switching
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.tracking_matrix import (
    TrackingMatrixData,
    compute_tracking_matrix,
    discover_tracking_categories,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["tracking-matrix"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _default_date_range() -> tuple[str, str]:
    """Return sensible default date range (current financial year to today)."""
    today = date.today()
    # Australian financial year: 1 Jan to 31 Dec (church year)
    from_date = f"{today.year}-01-01"
    to_date = today.isoformat()
    return from_date, to_date


def _matrix_to_template_context(data: TrackingMatrixData) -> dict:
    """Convert TrackingMatrixData to template-friendly dicts with float values."""
    def _row_dict(row):
        return {
            "budget_label": row.budget_label,
            "category_key": row.category_key,
            "section": row.section,
            "amounts": {k: float(v) for k, v in row.values.items()},
            "total": float(row.total),
        }

    return {
        "column_headers": data.column_headers,
        "income_rows": [_row_dict(r) for r in data.income_rows],
        "expense_rows": [_row_dict(r) for r in data.expense_rows],
        "income_totals": {k: float(v) for k, v in data.income_totals.items()},
        "expense_totals": {k: float(v) for k, v in data.expense_totals.items()},
        "income_grand_total": float(data.income_grand_total),
        "expense_grand_total": float(data.expense_grand_total),
        "net_position": {k: float(v) for k, v in data.net_position.items()},
        "net_grand_total": float(data.net_grand_total),
        "has_data": data.has_data,
        "from_date": data.from_date,
        "to_date": data.to_date,
        "error": data.error,
        "tracking_category": data.tracking_category,
    }


@router.get("/tracking-matrix", response_class=HTMLResponse)
async def tracking_matrix_page(
    request: Request,
    tracking_category_id: str | None = Query(None, description="Tracking category UUID"),
    from_date: str | None = Query(None, description="Report start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="Report end date (YYYY-MM-DD)"),
):
    """Render the full tracking matrix page.

    If no tracking_category_id is provided, shows a picker listing
    available tracking categories.  Otherwise, computes and renders
    the matrix.
    """
    # Discover available categories
    categories = await discover_tracking_categories()

    # Default date range
    default_from, default_to = _default_date_range()
    effective_from = from_date or default_from
    effective_to = to_date or default_to

    matrix_ctx: dict | None = None

    if tracking_category_id:
        data = await compute_tracking_matrix(
            tracking_category_id=tracking_category_id,
            from_date=effective_from,
            to_date=effective_to,
        )
        matrix_ctx = _matrix_to_template_context(data)

    return templates.TemplateResponse(
        request,
        "tracking_matrix.html",
        {
            "categories": categories,
            "selected_category_id": tracking_category_id or "",
            "from_date": effective_from,
            "to_date": effective_to,
            "matrix": matrix_ctx,
        },
    )


@router.get("/tracking-matrix/partial", response_class=HTMLResponse)
async def tracking_matrix_partial(
    request: Request,
    tracking_category_id: str = Query(..., description="Tracking category UUID"),
    from_date: str | None = Query(None, description="Report start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="Report end date (YYYY-MM-DD)"),
):
    """Return htmx partial with the matrix table for the selected category."""
    default_from, default_to = _default_date_range()
    effective_from = from_date or default_from
    effective_to = to_date or default_to

    data = await compute_tracking_matrix(
        tracking_category_id=tracking_category_id,
        from_date=effective_from,
        to_date=effective_to,
    )
    matrix_ctx = _matrix_to_template_context(data)

    return templates.TemplateResponse(
        request,
        "partials/tracking_matrix_table.html",
        {
            "matrix": matrix_ctx,
            "from_date": effective_from,
            "to_date": effective_to,
        },
    )
