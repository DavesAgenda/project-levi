---
name: fastapi-htmx
description: FastAPI + Jinja2 + htmx + Tailwind patterns for server-rendered church budget dashboard
metadata:
  internal: false
---

# FastAPI + htmx Stack Patterns

This skill defines the web application patterns for the church budget tool. Felicity uses this when building endpoints, templates, and tests.

## Project Structure

```
app/
  main.py                  # FastAPI app factory, middleware, lifespan
  config.py                # Settings via pydantic-settings (env vars)
  routers/
    dashboard.py           # Dashboard views (YTD summary, budget vs actuals)
    reports.py             # Council report, AGM report
    properties.py          # Property portfolio view
    payroll.py             # Payroll summary view
    trends.py              # Trend explorer (multi-year charts)
    budget.py              # Budget planning UI (Phase 3)
    xero.py                # Xero sync trigger, CSV upload
    auth.py                # Firebase auth routes (Phase 4)
  services/
    xero_client.py         # Xero API client (see xero-integration skill)
    snapshot.py            # JSON snapshot read/write + git commit
    mapping.py             # Chart of accounts mapping engine
    budget_calc.py         # Budget computation from YAML assumptions + config
    property_calc.py       # Per-property net yield calculations
    payroll_calc.py        # Payroll computation from diocese scales
    csv_import.py          # CSV upload validation and import
  models/
    schemas.py             # Pydantic models for all data structures
  templates/
    base.html              # Base template: Tailwind CDN, Alpine.js, htmx, nav, logo
    partials/              # htmx partial templates (swappable fragments)
    components/            # Reusable Jinja2 macro components:
      sortable_table.html  #   Data table with column sorting, pinned summary row
      kpi_card.html        #   Metric card with accent border, progress bar
      chart_card.html      #   Chart.js canvas wrapper with title
  static/
    tokens.css             # CSS custom properties (New Light brand tokens)
    logo.svg               # New Light Anglican Church logo
    js/
      sortable-table.js    # Alpine.js data component for table sorting
tests/
  conftest.py              # Fixtures: test client, sample YAML/JSON data
  test_routers/            # Endpoint tests
  test_services/           # Unit tests for business logic
```

## htmx Patterns

### Partial Template Responses
Endpoints that serve htmx requests return partial HTML (no base template):

```python
from fastapi import Request
from fastapi.responses import HTMLResponse

@router.get("/dashboard/ytd-summary")
async def ytd_summary(request: Request):
    data = compute_ytd_summary()
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/ytd_summary.html", {"request": request, **data})
    return templates.TemplateResponse("dashboard.html", {"request": request, **data})
```

### htmx Triggers
Use `hx-get` for dashboard refresh, `hx-post` for CSV upload:
```html
<div hx-get="/dashboard/ytd-summary" hx-trigger="load, every 60s" hx-swap="innerHTML">
  Loading...
</div>
```

### Form Uploads (CSV Import)
```html
<form hx-post="/xero/csv-upload" hx-encoding="multipart/form-data" hx-swap="innerHTML" hx-target="#import-result">
  <input type="file" name="csv_file" accept=".csv">
  <button type="submit">Import</button>
</form>
```

## Tailwind CSS Setup
- Use CDN for MVP (no build step): `<script src="https://cdn.tailwindcss.com"></script>`
- Custom config in `<script>` block extending design tokens from `tokens.css`
- All colors via CSS custom properties — see `design-tokens` skill (Jimmy)

## Testing Patterns

```python
# tests/conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app

@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

# tests/test_routers/test_dashboard.py
@pytest.mark.anyio
async def test_dashboard_returns_html(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
```

## Dependencies
```
fastapi>=0.115
uvicorn[standard]
jinja2
python-multipart    # for file uploads
pydantic-settings   # for env var config
httpx               # for async HTTP + testing
pyyaml              # for YAML config loading
pytest
anyio               # for async test support
```

## Component Architecture
All views use Jinja2 macro components from `templates/components/`:

```html
{# Import components at the top of the template #}
{% from "components/kpi_card.html" import kpi_card %}
{% from "components/chart_card.html" import chart_card %}
{% from "components/sortable_table.html" import sortable_table %}

{# KPI card #}
{{ kpi_card(label="Total Income", value="$101,300", subtitle="Budget: $279,500") }}
{{ kpi_card(label="Net Position", value="+$25K", accent="--success", color_class="text-success") }}
{{ kpi_card(label="Budget Used", value="36%", progress=36.2) }}

{# Chart card #}
{{ chart_card(title="Budget vs Actuals", canvas_id="budgetChart") }}
{{ chart_card(title="Progress", canvas_id="ring", centered=true, width="280px") }}

{# Sortable table — total row pinned, default sort A-Z #}
{{ sortable_table(
    id="income",
    columns=[
      {"label": "Category", "key": "label", "align": "left", "format": "string"},
      {"label": "Actual", "key": "actual", "format": "currency"},
      {"label": "Variance", "key": "variance_dollar", "format": "currency_signed"},
      {"label": "Var %", "key": "variance_pct", "format": "percent_status"},
    ],
    rows=row_data,
    summary=summary_dict,
    sort_by="label",
) }}
```

New components: create a macro in `templates/components/`, document parameters in a Jinja2 comment block at the top. Client-side JS is self-contained within the macro's `<script>` block.

## Key Conventions
- Router functions are `async def` — use async throughout
- All business logic lives in `services/`, routers are thin
- Pydantic models validate all inputs and config loading
- Templates use Jinja2 macros for reusable components (tables, cards, charts)
- Static files served from `app/static/` via `StaticFiles` mount
- Alpine.js loaded in `base.html` for reactive UI state
- New Light branding via `tokens.css` — primary `#ff7300`, Inter Display headings
