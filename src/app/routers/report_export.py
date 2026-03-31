"""FastAPI router for report exports — markdown download and PDF (browser print).

Endpoints:
    GET /reports/{report_type}/export?format=md   → Markdown file download
    GET /reports/{report_type}/export?format=pdf   → Redirect to print-friendly view

Supported report types: council, agm, properties, payroll.

PDF generation: Rather than pulling in weasyprint (which requires system-level
Cairo/Pango libraries), PDF export redirects to the print-friendly HTML view.
Each report template already includes @media print CSS that hides navigation
and produces clean typography. Users can Ctrl+P / Cmd+P to save as PDF.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.dependencies.auth import require_permission, should_redact_payroll
from app.models.auth import User
from app.services.agm_report import compute_agm_report
from app.services.council_report import compute_council_report
from app.services.payroll import compute_payroll_data
from app.services.property_portfolio import compute_property_portfolio
from app.services.report_export import REPORT_TYPES

router = APIRouter(prefix="/reports", tags=["export"])


# ---------------------------------------------------------------------------
# Data loaders — one per report type
# ---------------------------------------------------------------------------

def _load_council_data(year: int | None, month: int | None):
    """Load council report data with optional year/month override."""
    today = date.today()
    return compute_council_report(
        year=year or today.year,
        end_month=month or today.month,
    )


def _load_agm_data(year: int | None, **_):
    """Load AGM report data with optional year override."""
    today = date.today()
    return compute_agm_report(year=year or (today.year - 1))


def _load_properties_data(**_):
    """Load property portfolio data."""
    return compute_property_portfolio()


def _load_payroll_data(**_):
    """Load payroll summary data."""
    return compute_payroll_data()


_DATA_LOADERS = {
    "council": _load_council_data,
    "agm": _load_agm_data,
    "properties": _load_properties_data,
    "payroll": _load_payroll_data,
}

# Map report types to their HTML view URLs for print redirect
_VIEW_URLS = {
    "council": "/reports/council",
    "agm": "/reports/agm/{year}",
    "properties": "/reports/properties",
    "payroll": "/reports/payroll",
}


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------

@router.get("/{report_type}/export")
async def export_report(
    request: Request,
    report_type: str,
    format: str = Query(
        ...,
        description="Export format: 'md' for Markdown, 'pdf' for PDF (browser print)",
    ),
    year: int | None = Query(default=None, description="Year filter (council, agm)"),
    month: int | None = Query(
        default=None, ge=1, le=12,
        description="End month filter (council only)",
    ),
):
    """Export a report as Markdown or redirect to print view for PDF.

    Markdown export returns a downloadable .md file with preserved table
    formatting and report metadata.

    PDF export redirects to the report's HTML view with a print=1 query
    parameter. The browser's print dialog can then save as PDF using the
    @media print CSS already embedded in each template.
    """
    if report_type not in REPORT_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown report type: {report_type}. "
                   f"Valid types: {', '.join(REPORT_TYPES.keys())}",
        )

    if format not in ("md", "pdf"):
        raise HTTPException(
            status_code=400,
            detail="Invalid format. Use 'md' for Markdown or 'pdf' for PDF.",
        )

    # Payroll export: enforce payroll_detail permission (CHA-207 audit fix)
    # Blocks both markdown and PDF export for users without payroll_detail.
    if report_type == "payroll":
        user = getattr(request.state, "user", None)
        if should_redact_payroll(user):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: payroll export requires payroll_detail permission",
            )

    # PDF: redirect to printable HTML view
    if format == "pdf":
        url = _VIEW_URLS[report_type]
        if "{year}" in url:
            resolved_year = year or (date.today().year - 1)
            url = url.replace("{year}", str(resolved_year))
        # Add query params
        params: list[str] = ["print=1"]
        if report_type == "council":
            if year:
                params.append(f"year={year}")
            if month:
                params.append(f"month={month}")
        url = url + "?" + "&".join(params)
        return RedirectResponse(url=url, status_code=303)

    # Markdown export
    loader = _DATA_LOADERS[report_type]
    data = loader(year=year, month=month)

    report_info = REPORT_TYPES[report_type]
    markdown_fn = report_info["markdown_fn"]
    markdown_content = markdown_fn(data)

    # Build filename
    today = date.today()
    if report_type == "council":
        report_month = month or today.month
        filename = f"council_report_{year or today.year}_{report_month:02d}.md"
    elif report_type == "agm":
        filename = f"agm_report_{year or (today.year - 1)}.md"
    elif report_type == "properties":
        filename = f"property_portfolio_{today.isoformat()}.md"
    else:
        filename = f"payroll_summary_{today.isoformat()}.md"

    return Response(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
