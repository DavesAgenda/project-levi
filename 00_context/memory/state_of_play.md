# State of Play
**Last updated**: 2026-03-30

## Current Phase
Phase 4: Auth & Automation — **IN PROGRESS** (1/9 issues done)

## Active Milestone
M4: Auth & Automation — 1/9 issues complete (CHA-199 Auth0 setup done)
Linear: https://linear.app/chart-reporter/project/church-budget-tool-1028413ce92d

## Linear Issue Status
| Issue | Title | Agent | Status |
|-------|-------|-------|--------|
| CHA-199 | Set up Auth0 tenant | Human | Done |
| CHA-200 | CSRF middleware | Kryptonite + Felicity | Backlog |
| CHA-201 | Auth0 integration service (OIDC, JWT, roles) | Felicity | Backlog |
| CHA-202 | Auth middleware + route protection | Felicity + Kryptonite | Backlog |
| CHA-203 | Login/logout UI (Auth0 Universal Login) | Jimmy + Felicity | Backlog |
| CHA-204 | Payroll data redaction for staff role | Felicity | Backlog |
| CHA-205 | Monthly Xero sync endpoint | Felicity | Backlog |
| CHA-206 | Historical data verification | Felicity + Superman | Backlog |
| CHA-207 | Security review: Phase 4 pre-deploy | Kryptonite | Backlog |

## Wave Plan — M4
| Wave | Issues | Dependencies |
|------|--------|-------------|
| 0 | CHA-199 Auth0 setup (Done) | None |
| 1 | CHA-200 CSRF + CHA-201 Auth0 service | None (parallel) |
| 2 | CHA-202 Middleware + CHA-203 Login UI + CHA-204 Payroll redaction | Wave 1 |
| 3 | CHA-205 Sync + CHA-206 Verification | Wave 2 |
| 4 | CHA-207 Security review | All above |

## Role Model
| Role | Who | Budget Edit | Payroll Detail | Payroll Scenarios |
|------|-----|------------|---------------|-------------------|
| admin | Treasurer, Rector | Full read/write | Individual amounts | Full access |
| board | Wardens | Read-only | Individual amounts | View-only |
| staff | Church staff | Read-only | Rollup totals only | Blocked (403) |

## Auth0 Setup
- Tenant created, keys in `.env.local`
- Localhost callback URLs configured
- No custom domain yet (VPS deploy pending)
- MFA available

## Test Suite
505 tests passing, 6 pre-existing failures (tracking matrix — needs live Xero), ~5.5s total

## Prior Milestones
- M1: Foundation (MVP) — 100% complete
- M2: Reporting & Property — 100% complete (13/13 issues)
- M3: Budget Planning — 100% complete (7/7 issues, 505 tests)

## Key Deliverables — M3 (just completed)
- Budget data service with YAML CRUD, changelog, optimistic concurrency
- Budget editing UI with inline htmx, year selector, reference columns, forecast
- Approval workflow (draft → proposed → approved) with lock/unlock
- Property and payroll what-if scenario modelling
- Budget comparison view (draft vs current vs prior year)
- Security hardening (input validation, XSS prevention, path traversal)

## Feedback Notes
- Auth0 chosen over homebrew auth — payroll data requires MFA capability
- Supabase considered but rejected — would require rewriting entire file-based data layer
- n8n integration deferred — non-critical once tool is functional
- Balance sheet view and tracking matrix need revisiting once live Xero data is connected
- Xero tracking categories for 2026: "Congregations" and "Ministry & Funds"
