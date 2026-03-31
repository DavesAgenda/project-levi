# State of Play
**Last updated**: 2026-03-31

## Current Phase
Phase 4: Auth & Automation — **COMPLETE** (9/9 issues done)

## Active Milestone
M4: Auth & Automation — 9/9 issues complete (CHA-199 through CHA-207)
Linear: https://linear.app/chart-reporter/project/church-budget-tool-1028413ce92d

## Linear Issue Status
| Issue | Title | Agent | Status |
|-------|-------|-------|--------|
| CHA-199 | Set up Auth0 tenant | Human | Done |
| CHA-200 | CSRF middleware | Kryptonite + Felicity | Done |
| CHA-201 | Auth0 integration service (OIDC, JWT, roles) | Felicity | Done |
| CHA-202 | Auth middleware + route protection | Felicity + Kryptonite | Done |
| CHA-203 | Login/logout UI (Auth0 Universal Login) | Jimmy + Felicity | Done |
| CHA-204 | Payroll data redaction for staff role | Felicity | Done |
| CHA-205 | Monthly Xero sync endpoint | Felicity | Done |
| CHA-206 | Historical data verification | Felicity + Superman | Done |
| CHA-207 | Security review: Phase 4 pre-deploy | Kryptonite | Done |

## M4 Key Deliverables
- CSRF double-submit cookie middleware on all state-changing endpoints
- Auth0 OIDC integration with JWT verification via JWKS
- Auth middleware with role-based route protection (admin/board/staff)
- Login/logout UI with Auth0 Universal Login, nav bar role badges
- Payroll data redaction: staff sees totals only, scenarios blocked
- Monthly Xero sync (API key + session auth) with "Sync Now" dashboard button
- Historical data verification (CSV vs Xero snapshot comparison)
- Security audit: 5 fixes (timing-safe API key, open redirect, payroll export leak, error disclosure, cookie flags)

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
691 tests passing, ~8.5s total

## All Milestones
- M1: Foundation (MVP) — 100% complete
- M2: Reporting & Property — 100% complete (13/13 issues)
- M3: Budget Planning — 100% complete (7/7 issues)
- M4: Auth & Automation — 100% complete (9/9 issues, 691 tests)

## What's Next
- Deploy to Hostinger KVM1 (Docker + Caddy reverse proxy)
- Configure Auth0 custom domain for production
- Populate `config/roles.yaml` with real email addresses
- Connect to live Xero data and run verification
- Set up n8n monthly sync schedule
- Pin dependency versions for production

## Feedback Notes
- Auth0 chosen over homebrew auth — payroll data requires MFA capability
- Supabase considered but rejected — would require rewriting entire file-based data layer
- n8n integration deferred — non-critical once tool is functional
- Balance sheet view and tracking matrix need revisiting once live Xero data is connected
- Xero tracking categories for 2026: "Congregations" and "Ministry & Funds"
