# M3 Security Audit Report

**Date**: 2026-03-30
**Auditor**: Kryptonite (Risk & Security Agent)
**Scope**: All M3 (Budget Planning) code — first write-capable milestone
**Linear Issue**: CHA-198

---

## Summary

M3 introduces write operations (budget editing, YAML saves, status transitions, scenario modelling). The codebase uses `yaml.safe_load` everywhere (good) and Jinja2 with default HTML autoescaping (good). However, several input validation gaps and one XSS vector were found.

**Totals**: 2 High, 4 Medium, 2 Low, 2 Info

---

## Findings

### H-01: No `section_type` validation on budget edit routes

- **Severity**: High
- **File**: `src/app/routers/budget.py` lines 209-268 and 271-310
- **Description**: The `section_type` path parameter was used directly to select `budget.income` or `budget.expenses` via a ternary, but any value other than "income" would silently fall through to `expenses`. An attacker could pass unexpected values.
- **Remediation**: Added explicit validation — `section_type` must be "income" or "expenses", else 400 error.
- **Status**: **Fixed**

### H-02: Arbitrary key injection via `item_key` path parameter

- **Severity**: High
- **File**: `src/app/routers/budget.py` line 243
- **Description**: The `item_key` parameter from the URL path was written directly into `section.model_extra` without validation. An attacker could inject arbitrary keys like `__class__`, `notes`, `overrides`, or other meta-fields into the YAML structure.
- **Remediation**: Added `_is_valid_item_key()` validator — only alphanumeric + underscore keys up to 100 chars are accepted. Keys must start with a letter or digit.
- **Status**: **Fixed**

### M-01: Stored XSS in notes endpoint HTML response

- **Severity**: Medium
- **File**: `src/app/routers/budget.py` lines 300-303
- **Description**: The `update_section_notes` endpoint returned user-supplied notes directly in a raw `HTMLResponse` without escaping. While Jinja2 templates autoescape, this inline HTML construction did not. An attacker could inject `<script>` tags via the notes field.
- **Remediation**: (a) Strip HTML tags from notes input with `re.sub`. (b) Use `markupsafe.escape()` on output in the HTMLResponse.
- **Status**: **Fixed**

### M-02: User identity spoofing in workflow routes

- **Severity**: Medium
- **File**: `src/app/routers/budget_workflow.py` lines 38, 89
- **Description**: The `user` parameter was accepted from form data, allowing any caller to impersonate any user in the changelog (e.g., `user=admin`). Until Phase 4 auth, the user should be hardcoded.
- **Remediation**: Removed `user` from Form parameters; hardcoded to "treasurer" with a comment noting Phase 4 will add real auth.
- **Status**: **Fixed**

### M-03: No CSRF protection on POST/PUT endpoints

- **Severity**: Medium
- **File**: `src/app/main.py` (global), all POST/PUT routes
- **Description**: No CSRF middleware is configured. Since this is a Jinja2+htmx server-rendered app, any authenticated session (once Phase 4 adds auth) would be vulnerable to cross-site request forgery. Currently mitigated by lack of auth (no session to forge).
- **Remediation**: Add CSRF middleware before Phase 4 (auth milestone). For now, this is accepted risk since the app is single-user with no authentication.
- **Status**: Open (deferred to Phase 4)

### M-04: No budget amount bounds validation

- **Severity**: Medium
- **File**: `src/app/routers/budget.py` line 230
- **Description**: Budget line item values had no upper/lower bounds. A malicious or erroneous input could set a value to `1e308` (float max) or `NaN`/`Inf`.
- **Remediation**: Added max absolute value check of $100M. Float parsing already rejects NaN/Inf via `float()` + comma strip.
- **Status**: **Fixed**

### L-01: Changelog integrity not protected

- **Severity**: Low
- **File**: `src/app/services/budget.py` lines 227-254
- **Description**: The changelog is a plain JSON file that can be edited or truncated by anyone with file system access. There is no hash chain or signature to detect tampering.
- **Remediation**: Acceptable for current deployment (single-user, Docker). Consider adding a hash chain (each entry includes hash of prior entry) when multi-user auth is added in Phase 4.
- **Status**: Open (accepted risk)

### L-02: In-memory scenario state in payroll router

- **Severity**: Low
- **File**: `src/app/routers/payroll_scenarios.py` line 32
- **Description**: `_active_scenario` is a module-level global. In a multi-worker deployment, state would not be shared across workers. Not a security issue per se, but a data integrity risk.
- **Remediation**: Acceptable for single-user tool. Document that uvicorn must run with `--workers 1` or scenario state should move to a session store.
- **Status**: Open (accepted risk)

### I-01: YAML deserialization uses `safe_load` throughout

- **Severity**: Info
- **File**: All services using `yaml.safe_load`
- **Description**: All YAML loading uses `yaml.safe_load`, which prevents arbitrary code execution via YAML deserialization. This is correct.
- **Status**: N/A (positive finding)

### I-02: Jinja2 autoescaping enabled by default

- **Severity**: Info
- **File**: All routers using `Jinja2Templates`
- **Description**: FastAPI's `Jinja2Templates` enables autoescaping for `.html` files by default. This protects against XSS in template-rendered content.
- **Status**: N/A (positive finding)

---

## Year & Path Validation

Added defense-in-depth year validation (2000-2100) at both the router layer and service layer, plus `path.resolve().is_relative_to()` checks in `load_budget_file` and `save_budget_file` to prevent any theoretical path traversal.

---

## Files Modified

| File | Changes |
|------|---------|
| `src/app/routers/budget.py` | Added `_is_valid_item_key`, `_is_valid_section_key`, `_validate_year`; section_type validation; item_key validation; amount bounds; notes sanitization + escape |
| `src/app/routers/budget_workflow.py` | Removed user form param, hardcoded user; added year validation |
| `src/app/services/budget.py` | Added year range + path containment checks in load/save |

## Tests

Security regression tests written to `tests/test_security_m3.py`.
