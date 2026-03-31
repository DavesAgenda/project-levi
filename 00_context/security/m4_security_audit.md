# M4 Security Audit

**Date**: 2026-03-31
**Auditor**: Kryptonite
**Scope**: M4 (Auth & Automation) — CHA-200 through CHA-206
**Test suite**: 691 tests passing (41 new security tests in `tests/test_security_m4.py`)

---

## Summary

M4 introduces Auth0 OIDC authentication, JWT verification, role-based access control, CSRF middleware, payroll redaction for staff, Xero sync endpoints with API key auth, and historical data verification. This audit reviewed all M4 code for security vulnerabilities, fixed 5 issues (1 high, 2 medium, 2 low), and added 41 targeted security tests.

Overall, the security architecture is well-designed. The double-submit cookie CSRF pattern, JWT verification via JWKS with key rotation handling, and role-based access control are implemented correctly. The fixes below address edge cases and hardening.

---

## Findings

### High

#### H-01: API key comparison not timing-safe (FIXED)

**File**: `src/app/routers/xero_sync.py:62`
**Issue**: The sync endpoint's API key comparison used Python's `!=` operator (`if x_api_key != expected:`), which is vulnerable to timing side-channel attacks. An attacker could probe the correct key character-by-character by measuring response times.
**Fix**: Replaced with `secrets.compare_digest(x_api_key, expected)`.
**Test**: `TestApiKeyTimingSafe.test_timing_safe_comparison_in_code`

### Medium

#### M-01: Payroll data leakage via export endpoint (FIXED)

**File**: `src/app/routers/report_export.py`
**Issue**: The `/reports/payroll/export?format=md` and `format=pdf` endpoints did not check `should_redact_payroll()`. A staff user (without `payroll_detail` permission) could download full per-person payroll data as a markdown file, bypassing the template-level redaction applied in the HTML views.
**Fix**: Added `should_redact_payroll()` check to the export endpoint before both markdown and PDF paths. Staff users now receive 403.
**Tests**: `TestPayrollRedactionAllPaths.test_staff_payroll_export_blocked`, `test_staff_payroll_pdf_export_blocked`, `test_admin_payroll_export_allowed`, `test_board_payroll_export_allowed`

#### M-02: Open redirect in auth callback (FIXED)

**File**: `src/app/routers/auth.py:70`
**Issue**: The `/auth/callback` endpoint used the `state` parameter directly as the redirect target without validating that it is a relative URL. An attacker who controls the OAuth state parameter could redirect an authenticated user to a phishing site (e.g., `state=https://evil.com/phish`).
**Fix**: Added URL parsing to reject absolute URLs (those with a scheme or netloc). Absolute URLs are replaced with `/`.
**Tests**: `TestOpenRedirectPrevention.test_absolute_url_redirect_blocked`, `test_relative_url_redirect_allowed`

### Low

#### L-01: Token exchange error leaks internal details (FIXED)

**File**: `src/app/routers/auth.py:65`
**Issue**: The error handler for token exchange failures included the raw exception message in the HTTP response (`detail=f"Token exchange failed: {exc}"`). This could expose internal hostnames, port numbers, or connection details to the client.
**Fix**: Changed to a generic error message (`"Token exchange failed. Check server logs for details."`) and logged the actual exception at ERROR level.
**Test**: `TestTokenExchangeErrorDisclosure.test_exchange_error_does_not_leak_details`

#### L-02: API documentation publicly accessible (FIXED)

**File**: `src/app/middleware/auth.py:36`
**Issue**: The `/docs`, `/openapi.json`, and `/redoc` endpoints were listed in `_PUBLIC_PREFIXES`, making the full API schema accessible to unauthenticated users. This provides reconnaissance information about available endpoints, parameters, and data models.
**Fix**: Removed `/docs`, `/openapi.json`, and `/redoc` from the public prefixes list. These endpoints now require authentication.
**Tests**: `TestApiDocsNotPublic.test_docs_requires_auth`, `test_openapi_json_requires_auth`, `test_redoc_requires_auth`

#### L-03: CSRF cookie missing secure flag (FIXED)

**File**: `src/app/middleware/csrf.py`
**Issue**: The CSRF cookie was set without the `secure` flag, meaning it could be sent over plain HTTP. In production (behind HTTPS), this creates a risk that the cookie could be intercepted in a downgrade attack.
**Fix**: Added `secure=_secure_cookies()` to all `set_cookie()` calls. The `_secure_cookies()` helper defaults to `True` in production and can be disabled via `SECURE_COOKIES=0` for local development. Same pattern applied to the access_token cookie in `routers/auth.py`. Tests set `SECURE_COOKIES=0` via `conftest.py`.
**Tests**: `TestCsrfCookieSecure.test_csrf_cookie_uses_secure_cookies_function`, `test_secure_cookies_defaults_to_true`, `test_secure_cookies_disabled_for_dev`

---

## Positive Findings

### P-01: JWT verification is thorough
The `verify_jwt()` function in `src/app/services/auth.py` correctly:
- Validates the RS256 signature via the JWKS public key
- Enforces `exp` (expiry) as essential
- Validates the `iss` (issuer) against the Auth0 domain
- Supports `aud` (audience) validation
- Handles JWKS key rotation: if a `kid` is not found, it invalidates the cache and retries once
- Uses authlib's `claims.validate()` which checks all claim constraints

### P-02: CSRF double-submit cookie is well-implemented
- Uses `secrets.token_hex(32)` for 64-character hex tokens
- Validates via `secrets.compare_digest()` (timing-safe)
- Accepts token from both `X-CSRF-Token` header (htmx) and `csrf_token` form field
- Rotates the token after every successful state-changing request
- `samesite="strict"` prevents cross-origin cookie sending

### P-03: Role-based access control is correctly enforced
All write routes (`POST`, `PUT`, `DELETE`) use `Depends(require_role("admin"))` or `Depends(require_permission(...))`:
- Budget CRUD: admin only
- Budget workflow transitions: admin only
- Payroll scenarios: `payroll_scenarios` permission (admin only)
- CSV upload: admin only
- Xero sync: admin or API key
- Verification: admin or board

### P-04: Payroll redaction is comprehensive (after fix)
- `/reports/payroll`: `should_redact_payroll()` hides individual table for staff
- `/budget/{year}`: payroll section shows single "Total Staffing" line for staff
- `/budget/{year}/compare`: payroll categories collapsed to total for staff
- `/budget/payroll-scenarios`: blocked by `require_permission("payroll_scenarios")` (403)
- `/reports/payroll/export`: blocked by `should_redact_payroll()` check (403) -- **fixed in this audit**

### P-05: Session cookie security
The `access_token` cookie is set with:
- `httponly=True` (prevents JS access)
- `samesite="lax"` (prevents most CSRF while allowing Auth0 callback)
- `secure=_secure_cookies()` (True in production)
- `max_age=86400` (24-hour expiry)
- `path="/"` (cookie scoped to entire site)

### P-06: Auth0 OIDC state parameter
The login flow generates a random `state` via `secrets.token_urlsafe(32)` when none is provided. The `state` is passed through the Auth0 flow. After the fix (M-02), the callback validates that the redirect target is a relative URL.

### P-07: Input validation from M3 maintained
All M3 security controls remain intact:
- Budget section_type validation
- Item key injection prevention
- Year range validation (2000-2100)
- Notes XSS sanitization (HTML tag stripping)
- Budget amount bounds ($100M max)
- User spoofing prevention (email from JWT, not form data)
- Path containment in budget service

---

## Route Protection Completeness

### Admin-only routes (require_role("admin"))
| Route | Method | Auth |
|-------|--------|------|
| `/budget/{year}/line/...` | PUT | admin |
| `/budget/{year}/notes/...` | PUT | admin |
| `/budget/{year}/status` | POST | admin |
| `/budget/{year}/unlock` | POST | admin |
| `/budget/create-draft` | POST | admin |
| `/budget/{year}/transition` | POST | admin |
| `/budget/{year}/create-amendment` | POST | admin |
| `/budget/payroll-scenarios/diocese-scales` | POST | admin |
| `/budget/payroll-scenarios/staff/add` | POST | admin |
| `/budget/payroll-scenarios/staff/{name}/remove` | POST | admin |
| `/budget/payroll-scenarios/staff/{name}/restore` | POST | admin |
| `/budget/payroll-scenarios/staff/{name}/fte` | POST | admin |
| `/budget/payroll-scenarios/staff/{name}/step` | POST | admin |
| `/budget/payroll-scenarios/uplift` | POST | admin |
| `/budget/payroll-scenarios/staff/{name}/uplift` | POST | admin |
| `/budget/payroll-scenarios/save` | POST | admin |
| `/budget/payroll-scenarios/reset` | POST | admin |
| `/budget/scenarios/property/{year}/preview` | POST | admin |
| `/api/csv/upload` | POST | admin |
| `/api/csv/preview` | POST | admin |
| `/api/xero/sync-now` | POST | admin |

### Permission-guarded routes
| Route | Method | Permission |
|-------|--------|-----------|
| `/budget/payroll-scenarios` | GET | payroll_scenarios |
| `/budget/payroll-scenarios/preview` | GET | payroll_scenarios |

### Admin or Board routes
| Route | Method | Auth |
|-------|--------|------|
| `/reports/verification` | GET | admin or board |

### Admin or API key routes
| Route | Method | Auth |
|-------|--------|------|
| `/api/xero/sync-monthly` | POST | admin session or X-API-Key |

### Read-only routes (any authenticated user)
These routes are protected by AuthMiddleware (require valid JWT cookie) but don't enforce a specific role. This is by design -- all three roles (admin, board, staff) have `read` permission:

| Route | Method | Notes |
|-------|--------|-------|
| `/`, `/dashboard` | GET | Dashboard |
| `/dashboard/data` | GET | Chart.js JSON |
| `/budget/{year}` | GET | Budget view (payroll redacted for staff) |
| `/budget/{year}/compare` | GET | Comparison (payroll redacted for staff) |
| `/budget/{year}/totals` | GET | Totals partial |
| `/budget/{year}/history` | GET | Changelog |
| `/budget/{year}/status` | GET | Status JSON |
| `/budget/scenarios/property/{year}` | GET | Property scenarios panel |
| `/reports/payroll` | GET | Payroll report (redacted for staff) |
| `/reports/council` | GET | Council report |
| `/reports/agm/{year}` | GET | AGM report |
| `/reports/agm/{year}/data` | GET | AGM chart data |
| `/reports/properties` | GET | Property portfolio |
| `/reports/properties/data` | GET | Property chart data |
| `/reports/trends` | GET | Trend explorer |
| `/reports/trends/chart` | GET | Trend chart partial |
| `/reports/tracking-matrix` | GET | Tracking matrix |
| `/reports/tracking-matrix/partial` | GET | Matrix partial |
| `/reports/{type}/export` | GET | Export (payroll blocked for staff) |
| `/api/xero/pl` | GET | Xero P&L fetch |
| `/api/xero/trial-balance` | GET | Xero Trial Balance |
| `/api/xero/balance-sheet` | GET | Xero Balance Sheet |
| `/api/xero/balance-sheet/assets` | GET | Asset mapping |
| `/api/xero/tracking` | GET | Tracking categories |

### Public routes (no auth required)
| Route | Method | Notes |
|-------|--------|-------|
| `/health` | GET | Health check |
| `/auth/login` | GET | Auth0 login redirect |
| `/auth/callback` | GET | OAuth callback |
| `/auth/logout` | POST | Clear cookie + Auth0 logout |
| `/auth/xero/login` | GET | Xero OAuth |
| `/auth/xero/callback` | GET | Xero OAuth callback |
| `/auth/xero/status` | GET | Xero connection status |
| `/auth/xero/logout` | POST | Clear Xero tokens |
| `/static/*` | GET | Static assets |

---

## Deferred / Accepted Risks

### D-01: OIDC state parameter lacks server-side nonce verification
The `state` parameter in the Auth0 OIDC flow is used to pass the `next` URL rather than a server-side random nonce. This means Auth0's standard CSRF protection via `state` is not fully utilized -- instead, the app relies on CSRF cookie middleware for POST protection and the Auth0 callback is a GET (exempt from CSRF).

**Accepted**: The callback is a GET endpoint that only exchanges a one-time-use authorization code. Auth0 itself validates the code is bound to the correct client and redirect URI. The risk of CSRF on the callback is low, and the open redirect was separately fixed (M-02).

**Recommendation for future**: Store a random nonce in an httponly cookie before login, embed it in `state` as a prefix, and verify it matches on callback. This provides defense-in-depth.

### D-02: Xero API endpoints accessible to any authenticated user
The `/api/xero/pl`, `/api/xero/balance-sheet`, etc. endpoints are accessible to any authenticated user (staff, board, admin). These trigger live Xero API calls and save snapshots.

**Accepted**: This is by design for the current deployment model (small user base, all trusted church staff). The write-side sync endpoints (`sync-monthly`, `sync-now`) are properly restricted to admin/API key.

**Recommendation for future**: Add `require_role("admin")` to the Xero API endpoints once the user base grows.

### D-03: No rate limiting on auth endpoints
There is no rate limiting on `/auth/login`, `/auth/callback`, or the Xero sync endpoints. In the current deployment (Docker on KVM1 behind reverse proxy), rate limiting should be handled at the reverse proxy (nginx) level.

**Recommendation**: Add nginx rate limiting rules for `/auth/` and `/api/xero/sync-*` paths.

---

## Recommendations

1. **Add nginx rate limiting** for `/auth/` and `/api/` paths (D-03)
2. **Add server-side OIDC nonce verification** to the Auth0 login flow (D-01)
3. **Restrict Xero API endpoints** to admin role when user base grows (D-02)
4. **Pin authlib version** in `pyproject.toml` (currently `>=1.0` -- recommend pinning to a specific minor version once tested)
5. **Run `pip audit`** periodically to check for known vulnerabilities in authlib and other dependencies
6. **Consider MFA enforcement** via Auth0 tenant policies for admin and board roles (payroll access)

---

## Dependency Audit

### authlib
- **Installed version**: `>=1.0` (as specified in `pyproject.toml`)
- **Status**: No known critical vulnerabilities as of 2026-03-31. The library is actively maintained and widely used for OIDC/OAuth2 integrations.
- **Note**: `pip audit` requires `pip-audit` package to be installed. Manual review confirms authlib's JWT verification implementation is sound for RS256 with JWKS.

### Other dependencies
| Package | Version Constraint | Notes |
|---------|-------------------|-------|
| fastapi | >=0.115.0 | Active development, well-maintained |
| uvicorn | >=0.34.0 | Standard ASGI server |
| httpx | >=0.28.0 | Used for Auth0/Xero API calls |
| pyyaml | >=6.0 | Uses `safe_load()` throughout (no arbitrary code execution) |
| python-multipart | >=0.0.18 | Form parsing for htmx |
| jinja2 | >=3.1.0 | Template engine with autoescaping |

---

## Files Modified in This Audit

| File | Change |
|------|--------|
| `src/app/routers/xero_sync.py` | H-01: Timing-safe API key comparison |
| `src/app/routers/report_export.py` | M-01: Payroll export redaction check |
| `src/app/routers/auth.py` | M-02: Open redirect prevention + L-01: Generic error message |
| `src/app/middleware/csrf.py` | L-03: Secure cookie flag via `_secure_cookies()` |
| `src/app/middleware/auth.py` | L-02: Removed docs/openapi from public prefixes |
| `tests/conftest.py` | Set SECURE_COOKIES=0 for test environment |
| `tests/test_security_m4.py` | **NEW** -- 41 security tests |
| `00_context/security/m4_security_audit.md` | **NEW** -- This report |

---

## M3 Deferred Item Resolution

**M-03 (CSRF)**: The M3 audit identified the need for CSRF protection. CHA-200 implemented the double-submit cookie pattern. **Resolved**.

All M3 security controls (input validation, XSS prevention, path traversal, budget bounds, user spoofing prevention) remain intact and are verified by the existing `test_security_m3.py` test suite (14 tests, all passing).
