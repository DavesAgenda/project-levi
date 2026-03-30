"""FastAPI router for the Trend Explorer — multi-year budget category charts."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.trend_explorer import (
    MONTH_LABELS,
    compute_trend_data,
    get_all_categories,
)

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(prefix="/reports", tags=["trends"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _trend_data_to_chart_json(data) -> dict:
    """Convert TrendData into a dict suitable for Chart.js rendering."""
    # Yearly chart data
    yearly_labels = [str(yt.year) for yt in data.primary_yearly]
    yearly_primary_values = [yt.total for yt in data.primary_yearly]

    # For compare mode, align years — use None for gaps
    all_years = data.available_years
    yearly_labels_full = [str(y) for y in all_years]

    # Build primary data aligned to all_years (None for missing years)
    primary_by_year = {yt.year: yt.total for yt in data.primary_yearly}
    primary_aligned = [primary_by_year.get(y) for y in all_years]

    compare_aligned = None
    if data.compare_category and data.compare_yearly:
        compare_by_year = {yt.year: yt.total for yt in data.compare_yearly}
        compare_aligned = [compare_by_year.get(y) for y in all_years]

    # Monthly chart data
    monthly_labels = []
    monthly_primary_values = []
    monthly_compare_values = []

    if data.has_monthly:
        primary_monthly_map = {
            (mt.year, mt.month): mt.total for mt in data.primary_monthly
        }
        compare_monthly_map = {}
        if data.compare_category and data.compare_monthly:
            compare_monthly_map = {
                (mt.year, mt.month): mt.total for mt in data.compare_monthly
            }

        # Build all month keys present in either series
        all_month_keys = sorted(
            set(primary_monthly_map.keys()) | set(compare_monthly_map.keys())
        )
        for y, m in all_month_keys:
            monthly_labels.append(f"{MONTH_LABELS[m - 1]} {y}")
            monthly_primary_values.append(primary_monthly_map.get((y, m)))
            if compare_monthly_map:
                monthly_compare_values.append(compare_monthly_map.get((y, m)))

    return {
        "yearly_labels": yearly_labels_full,
        "yearly_primary": primary_aligned,
        "yearly_compare": compare_aligned,
        "monthly_labels": monthly_labels,
        "monthly_primary": monthly_primary_values,
        "monthly_compare": monthly_compare_values if monthly_compare_values else None,
        "primary_label": data.primary_category.label,
        "compare_label": data.compare_category.label if data.compare_category else None,
    }


def _build_table_rows(data) -> list[dict]:
    """Build data table rows from TrendData yearly totals."""
    all_years = data.available_years
    primary_by_year = {yt.year: yt.total for yt in data.primary_yearly}
    compare_by_year = (
        {yt.year: yt.total for yt in data.compare_yearly}
        if data.compare_yearly
        else {}
    )

    rows = []
    for year in all_years:
        row = {"year": year, "primary": primary_by_year.get(year)}
        if data.compare_category:
            row["compare"] = compare_by_year.get(year)
        rows.append(row)

    return rows


@router.get("/trends", response_class=HTMLResponse)
async def trend_explorer_page(request: Request):
    """Render the full Trend Explorer page with category selector."""
    categories = get_all_categories()

    # Default to first category if available
    default_key = categories[0].key if categories else ""

    data = compute_trend_data(default_key) if default_key else None
    chart_json = _trend_data_to_chart_json(data) if data and data.has_data else None
    table_rows = _build_table_rows(data) if data and data.has_data else []

    return templates.TemplateResponse(
        request,
        "trend_explorer.html",
        {
            "categories": categories,
            "selected_key": default_key,
            "compare_key": "",
            "granularity": "yearly",
            "data": data,
            "chart_json": chart_json,
            "table_rows": table_rows,
        },
    )


@router.get("/trends/chart", response_class=HTMLResponse)
async def trend_chart_partial(
    request: Request,
    category: str = Query(..., description="Primary category key"),
    compare: str = Query("", description="Compare category key (optional)"),
    granularity: str = Query("yearly", description="yearly or monthly"),
):
    """Return htmx partial with updated chart + data table for selected category."""
    compare_key = compare if compare else None
    data = compute_trend_data(category, compare_key=compare_key)

    chart_json = _trend_data_to_chart_json(data) if data.has_data else None
    table_rows = _build_table_rows(data) if data.has_data else []

    return templates.TemplateResponse(
        request,
        "partials/trend_chart.html",
        {
            "data": data,
            "chart_json": chart_json,
            "table_rows": table_rows,
            "granularity": granularity,
            "selected_key": category,
            "compare_key": compare or "",
        },
    )
