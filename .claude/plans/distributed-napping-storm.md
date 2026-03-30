# M2 Feedback: Fixes & New Features

## Context
M2 (Reporting & Property) delivered 7/7 issues. User reviewed all views and identified:
- Sorting bugs across all report views
- Missing single-month view on council report
- PCR bundled into allowances on payroll (should be separate)
- Missing sparkline tooltips on AGM report
- Two new features needed before M3: simplified balance sheet view + tracking category matrix report

## Wave 1: Quick Fixes (parallel)

### A1. Category sort order fix
**Problem:** All services use `sorted(all_keys)` (alphabetical by internal key). User wants sort by `budget_label`.
**Pattern to follow:** `trend_explorer.py` already sorts correctly: `sort(key=lambda c: (0 if section == "income" else 1, label))`

Files to modify:
- `src/app/services/council_report.py` — replace `for cat_key in sorted(all_keys)` with label-based sort via `cat_meta`
- `src/app/services/agm_report.py` — same fix
- `src/app/services/dashboard.py` — same fix
- Update tests in `tests/test_council_report_service.py`, `tests/test_agm_report_service.py`

### A2. AGM sparkline hover tooltips
**Problem:** Sparklines drawn with raw Canvas2D have no hover interaction.
**Fix:** Convert sparklines to Chart.js mini instances (consistent with existing category bar charts). Add `data-years` attribute to canvases for tooltip labels.

File to modify:
- `src/app/templates/agm_report.html` — replace Canvas2D sparkline code with Chart.js instances + tooltip config

## Wave 2: Enhancements (parallel)

### B1. Council report single month view
**Problem:** Only YTD view exists. User wants "Current Month" toggle showing one month's actuals vs monthly budget (annual/12).

Files to modify:
- `src/app/services/council_report.py` — add `view_mode: str = "ytd"` param to `compute_council_report()`. When `"month"`: use single month_key, budget = annual/12
- `src/app/routers/council_report.py` — add `view` query param, pass to service
- `src/app/templates/council_report.html` — add toggle buttons (Current Month / Year to Date), conditionally show single vs all month columns
- Update tests in `tests/test_council_report_service.py`

### C1. Payroll PCR unbundle
**Problem:** `allowances = PCR + fixed_travel + workers_comp` hides PCR. User needs PCR visible.

Files to modify:
- `src/app/services/payroll.py` — add `pcr`, `fixed_travel`, `workers_comp` fields to `StaffCost` dataclass. `allowances` becomes fixed_travel + workers_comp only. `total_cost` includes PCR separately.
- `src/app/routers/payroll.py` — add PCR column to table row data
- `src/app/templates/payroll.html` — add PCR column to staff table
- Update tests in `tests/test_payroll_service.py`

## Wave 3: New Features (parallel)

### D1. Balance sheet section in council report
**Concept:** Embedded in council report (user confirmed). Show only material balance sheet changes between periods. Suppress static rows.

Files to create:
- `src/app/services/balance_sheet.py` — load two snapshots, compute deltas, filter by materiality (>$500 or >5%)
- `src/app/templates/partials/balance_sheet_section.html` — filtered table grouped by section, change indicators, colour-coded
- `tests/test_balance_sheet_changes.py` — mock snapshot comparison tests

Files to modify:
- `src/app/services/council_report.py` — call balance sheet service, include data in context
- `src/app/routers/council_report.py` — pass balance sheet data to template
- `src/app/templates/council_report.html` — include balance sheet partial below P&L table

### D2. Tracking category matrix report
**Concept:** Matrix of budget categories x tracking options (rows = categories, columns = tracking codes). User confirmed: new tracking category in Xero for 2026, separate from ministry activities. Categories are called **"Congregations"** and **"Ministry & Funds"**. Service discovers dynamically from Xero.

Key: Xero P&L API with `trackingCategoryID` returns columns per tracking option in ONE call. Parser already handles dynamic columns. Service must discover tracking categories dynamically from Xero, never hardcode.

Files to create:
- `src/app/services/tracking_matrix.py` — discover tracking categories, fetch P&L with tracking breakdown, pivot into matrix using `build_account_lookup()` from `csv_import.py`
- `src/app/routers/tracking_matrix.py` — `GET /reports/tracking-matrix` with category picker + htmx partial
- `src/app/templates/tracking_matrix.html` ��� matrix table with tracking option columns
- `tests/test_tracking_matrix_service.py` — mock API tests

Files to modify:
- `src/app/main.py` — register router
- `src/app/templates/base.html` — add nav link

## Linear Issues to Create
- CHA-186: Fix category sort order across all report views
- CHA-187: Add sparkline hover tooltips to AGM report
- CHA-188: Add single month view to council report
- CHA-189: Unbundle PCR from payroll allowances
- CHA-190: Build simplified balance sheet view
- CHA-191: Build tracking category matrix report

## Verification
1. Run `python -m pytest tests/ -v` after each wave
2. Manual check: visit `/reports/council`, `/reports/agm/2025`, `/reports/payroll` — verify sort order matches budget labels
3. Manual check: council report toggle between Month/YTD views
4. Manual check: payroll table shows PCR column
5. Balance sheet + tracking matrix need Xero snapshots — verify with sample/mock data
