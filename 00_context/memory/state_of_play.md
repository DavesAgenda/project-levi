# State of Play
**Last updated**: 2026-03-30

## Current Phase
Phase 3: Budget Planning — **COMPLETE** (7/7 issues)

## Active Milestone
M3: Budget Planning — 7/7 issues complete
Linear: https://linear.app/chart-reporter/project/church-budget-tool-1028413ce92d

## Linear Issue Status
| Issue | Title | Agent | Status |
|-------|-------|-------|--------|
| CHA-192 | Budget data service (load, save, validate YAML) | Felicity | Done |
| CHA-193 | Budget editing UI (view and edit budget line items) | Felicity + Jimmy | Done |
| CHA-194 | Property what-if scenarios | Felicity + Superman | Done |
| CHA-195 | Payroll what-if scenarios and diocese scale intake | Felicity | Done |
| CHA-196 | Budget approval workflow (draft → proposed → approved) | Felicity | Done |
| CHA-197 | Budget comparison view (draft vs current vs prior year) | Felicity + Superman | Done |
| CHA-198 | Security review: Phase 3 pre-deploy | Kryptonite | Done |

## Key Deliverables — M3
| Component | Location | Tests |
|-----------|----------|-------|
| Budget Pydantic models | `src/app/models/budget.py` | — |
| Budget data service (CRUD, versioning, changelog) | `src/app/services/budget.py` | 34 |
| Budget editing UI (view/edit, inline htmx) | `src/app/routers/budget.py`, `src/app/templates/budget.html` | 15 |
| Budget line partial | `src/app/templates/partials/budget_line.html` | — |
| Budget approval workflow | `src/app/routers/budget_workflow.py` | 15 |
| Budget history partial | `src/app/templates/partials/budget_history.html` | — |
| Property scenario service | `src/app/services/property_scenarios.py` | 20 |
| Property scenario routes | `src/app/routers/property_scenarios.py` | — |
| Property scenario partial | `src/app/templates/partials/property_scenarios.html` | — |
| Payroll scenario service | `src/app/services/payroll_scenarios.py` | 30 |
| Payroll scenario routes | `src/app/routers/payroll_scenarios.py` | — |
| Payroll scenario partial | `src/app/templates/partials/payroll_scenarios.html` | — |
| Budget comparison service | `src/app/services/budget_comparison.py` | 18 |
| Budget comparison routes | `src/app/routers/budget_comparison.py` | — |
| Budget comparison template | `src/app/templates/budget_comparison.html` | — |
| Security audit report | `00_context/security/m3_security_audit.md` | 19 |

## Routes Added — M3
| Route | View |
|-------|------|
| `/budget/{year}` | Budget view (all categories, status badge) |
| `/budget/{year}?edit=true` | Budget edit mode (inline htmx) |
| `/budget/{year}/line/{section}/{key}/{item}` | Inline line item update (PUT) |
| `/budget/{year}/notes/{section}/{key}` | Section notes update (PUT) |
| `/budget/{year}/status` | Status transition (POST) |
| `/budget/{year}/totals` | Live totals partial |
| `/budget/{year}/transition` | Workflow status transition |
| `/budget/{year}/create-amendment` | Amend approved budget |
| `/budget/{year}/history` | Changelog/history view |
| `/budget/{year}/compare` | Side-by-side comparison (draft vs current vs prior) |
| `/budget/create-draft` | Create new draft budget |
| `/budget/scenarios/property/{year}` | Property what-if scenarios |
| `/budget/scenarios/property/{year}/preview` | Property scenario preview |
| `/budget/payroll-scenarios` | Payroll scenario modelling |

## Test Suite
478 tests passing, 6 pre-existing failures (tracking matrix — needs live Xero), ~3.7s total

## Security Findings — M3
- 6 issues fixed (input validation, key injection, XSS, user spoofing, amount bounds, year range)
- 2 deferred (CSRF → M4, changelog integrity → accepted risk)
- Full report: `00_context/security/m3_security_audit.md`

## Feedback Notes
- Balance sheet view and tracking matrix need revisiting once live Xero data is connected
- Xero tracking categories for 2026: "Congregations" and "Ministry & Funds"
- CSRF middleware deferred to Phase 4 (auth milestone)

## Prior Milestones
- M1: Foundation (MVP) — 100% complete
- M2: Reporting & Property — 100% complete (13/13 issues)

## Next Phase
Phase 4: Auth & Automation — Not yet planned in Linear.
Key areas: Firebase Auth, scheduled auto-sync, n8n notifications, historical data verification, CSRF middleware.
