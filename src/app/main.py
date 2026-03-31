"""Church Budget Tool — FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.middleware.auth import AuthMiddleware
from app.middleware.csrf import CSRFMiddleware

from app.routers.auth import router as auth_router
from app.routers.agm_report import router as agm_report_router
from app.routers.council_report import router as council_report_router
from app.routers.csv_upload import router as csv_router
from app.routers.dashboard import router as dashboard_router
from app.routers.payroll import router as payroll_router
from app.routers.property_portfolio import router as property_portfolio_router
from app.routers.xero_auth import router as xero_auth_router
from app.routers.trend_explorer import router as trend_explorer_router
from app.routers.report_export import router as report_export_router
from app.routers.tracking_matrix import router as tracking_matrix_router
from app.routers.budget import router as budget_router
from app.routers.budget_comparison import router as budget_comparison_router
from app.routers.budget_workflow import router as budget_workflow_router
from app.routers.payroll_scenarios import router as payroll_scenarios_router
from app.routers.property_scenarios import router as property_scenarios_router
from app.routers.xero_reports import router as xero_reports_router
from app.routers.xero_sync import router as xero_sync_router
from app.routers.verification import router as verification_router

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="Church Budget Tool")

# Middleware stack — order matters!
# CSRF runs first (innermost), then Auth (outermost).
# Starlette executes middleware in reverse registration order,
# so we register CSRF first, then Auth.
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthMiddleware)

# Register routers — auth first, then dashboard so "/" is handled there
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(agm_report_router)
app.include_router(council_report_router)
app.include_router(csv_router)
app.include_router(payroll_router)
app.include_router(property_portfolio_router)
app.include_router(report_export_router)
app.include_router(xero_auth_router)
app.include_router(trend_explorer_router)
app.include_router(tracking_matrix_router)
app.include_router(payroll_scenarios_router)
app.include_router(property_scenarios_router)
app.include_router(budget_comparison_router)
app.include_router(budget_workflow_router)
app.include_router(budget_router)
app.include_router(xero_reports_router)
app.include_router(xero_sync_router)
app.include_router(verification_router)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Inject ``user`` into all Jinja2 template contexts
# ---------------------------------------------------------------------------

@app.middleware("http")
async def inject_user_into_templates(request: Request, call_next):
    """Make ``request.state.user`` available as ``user`` in Jinja2 templates.

    We patch ``templates.TemplateResponse`` at the router level, but the
    simplest universal approach is an ``@app.middleware`` that modifies the
    response *context* before the template is rendered.  Since Jinja2Templates
    uses the ``request`` object, templates can already access
    ``request.state.user``.  This middleware is a no-op pass-through; templates
    should use ``request.state.user`` directly.
    """
    return await call_next(request)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
