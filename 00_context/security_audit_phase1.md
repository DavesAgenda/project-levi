# Security Audit Report -- Phase 1 Pre-Deploy

**Project**: Church Budget Tool (Levi)
**Auditor**: Kryptonite (Risk & Security Agent)
**Date**: 2026-03-30
**Linear Issue**: CHA-176
**Scope**: Phase 1 codebase -- Authentication, Input Validation, Templates, Docker, Dependencies

---

## Summary Table

| # | Severity | Category | Finding | File | Fixed? |
|---|----------|----------|---------|------|--------|
| 1 | CRITICAL | Credentials | Docker secrets not readable by settings.py | `src/app/xero/settings.py` | YES |
| 2 | CRITICAL | Git Safety | `secrets/` directory not in `.gitignore` | `.gitignore` | YES |
| 3 | HIGH | Token Storage | Token file written with world-readable permissions | `src/app/xero/oauth.py:65` | YES |
| 4 | HIGH | Input Validation | No file size limit on CSV upload | `src/app/routers/csv_upload.py` | YES |
| 5 | HIGH | Input Validation | No content-type validation on CSV upload | `src/app/routers/csv_upload.py` | YES |
| 6 | HIGH | Info Disclosure | Exception details exposed to client in OAuth callback | `src/app/routers/xero_auth.py:72` | YES |
| 7 | MEDIUM | Token Storage | Tokens persisted to disk (not memory-only) | `src/app/xero/oauth.py:39` | No (documented) |
| 8 | MEDIUM | CORS | No CORS policy configured | `src/app/main.py` | No (documented) |
| 9 | MEDIUM | FastAPI Config | Debug mode not explicitly disabled | `src/app/main.py:18` | No (documented) |
| 10 | MEDIUM | Dependencies | Dependency versions use >= (floating) | `pyproject.toml` | No (documented) |
| 11 | MEDIUM | API Security | No rate limiting on upload endpoints | `src/app/routers/csv_upload.py` | No (documented) |
| 12 | LOW | Templates | Jinja2 autoescape not explicitly enabled | `src/app/main.py:29` | No (documented) |
| 13 | LOW | Docker | Shared network with n8n, no explicit egress rules | `docker-compose.yml:29` | No (documented) |
| 14 | INFO | Docker | Good -- non-root user, slim image, resource limits | `Dockerfile`, `docker-compose.yml` | N/A |
| 15 | INFO | Docker | Good -- port binding to localhost only | `docker-compose.yml:8` | N/A |
| 16 | INFO | OAuth | Good -- CSRF state validation with expiry | `src/app/xero/oauth.py:83-97` | N/A |
| 17 | INFO | Scopes | Good -- read-only Xero scopes only | `src/app/xero/oauth.py:30-37` | N/A |
| 18 | INFO | .dockerignore | Good -- secrets, .env, tokens excluded from build | `.dockerignore` | N/A |

---

## Detailed Findings

### Finding 1: CRITICAL -- Docker Secrets Not Readable by Settings

**Location**: `src/app/xero/settings.py`
**Risk**: Docker Compose sets `XERO_CLIENT_ID_FILE=/run/secrets/xero_client_id` and `XERO_CLIENT_SECRET_FILE=/run/secrets/xero_client_secret`, but `settings.py` only reads `XERO_CLIENT_ID` and `XERO_CLIENT_SECRET`. In a Docker deployment, credentials will never load -- the app will silently start without Xero access.

**Fix applied**: Rewrote `settings.py` to read `_FILE` env vars first (Docker secrets pattern), falling back to plain env vars for local dev. The `_read_secret()` helper reads the file contents from the path specified in the `_FILE` variable.

---

### Finding 2: CRITICAL -- `secrets/` Directory Not in `.gitignore`

**Location**: `.gitignore`
**Risk**: `docker-compose.yml` references `./secrets/xero_client_id.txt` and `./secrets/xero_client_secret.txt`. Without `secrets/` in `.gitignore`, a developer could `git add .` and commit plaintext Xero credentials to the repository. This is a one-mistake-away-from-breach scenario.

**Fix applied**: Added `secrets/` to `.gitignore`.

---

### Finding 3: HIGH -- Token File Written Without Restricted Permissions

**Location**: `src/app/xero/oauth.py:65` (`_save_tokens`)
**Risk**: `.xero_tokens.json` contains access tokens, refresh tokens, and tenant IDs. Written with default file permissions, other users on the shared VPS could read it.

**Fix applied**: Added `os.chmod(TOKEN_FILE, 0o600)` after writing the token file, restricting access to the file owner only. Wrapped in try/except for Windows compatibility.

---

### Finding 4: HIGH -- No File Size Limit on CSV Upload

**Location**: `src/app/routers/csv_upload.py` (`upload_csv`, `preview_csv`)
**Risk**: An attacker could upload a multi-gigabyte file, causing memory exhaustion and DoS on the shared VPS (which also hosts n8n). `await file.read()` loads the entire file into memory.

**Fix applied**: Added `_read_and_validate_upload()` helper that enforces a 10 MB maximum upload size. Returns HTTP 413 if exceeded.

---

### Finding 5: HIGH -- No Content-Type Validation on CSV Upload

**Location**: `src/app/routers/csv_upload.py`
**Risk**: Without content-type and extension validation, non-CSV files (executables, scripts, etc.) can be uploaded and processed by the CSV parser. While the parser itself is reasonably safe, defense-in-depth requires validating the input at the boundary.

**Fix applied**: Added content-type validation (allowlist of CSV-related MIME types) and filename extension check (.csv required) in `_read_and_validate_upload()`.

---

### Finding 6: HIGH -- Exception Details Exposed in OAuth Callback

**Location**: `src/app/routers/xero_auth.py:72`
**Risk**: `detail=f"Token exchange failed: {exc}"` sends the raw Python exception message to the client. This could leak internal URLs, credential fragments, network topology, or library version info to an attacker.

**Fix applied**: Changed to log the exception server-side and return a generic message to the client: `"Token exchange failed. Check server logs for details."`

---

### Finding 7: MEDIUM -- Tokens Persisted to Disk

**Location**: `src/app/xero/oauth.py:39`
**Description**: The security audit checklist specifies "Token cached in memory only -- never written to disk or logs." However, the current implementation writes tokens to `.xero_tokens.json` on disk. This is a design trade-off: disk persistence survives restarts (important for the 60-day refresh token lifecycle), but increases the blast radius if the filesystem is compromised.

**Recommendation**: For Phase 1, the disk-based approach with 0o600 permissions (Finding 3 fix) is acceptable. In Phase 4 (Auth & Automation), consider encrypting the token file at rest or moving to an in-memory store with a separate persistence layer.

---

### Finding 8: MEDIUM -- No CORS Policy Configured

**Location**: `src/app/main.py`
**Description**: No CORS middleware is configured. While the app is currently accessed through a reverse proxy and renders server-side HTML (not a separate SPA), any future API consumers or htmx requests from different origins would be blocked by browser defaults -- which is actually secure. However, explicit CORS configuration is better than relying on defaults.

**Recommendation**: Add CORSMiddleware before production deploy:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://budget.yourdomain.com"],  # production domain only
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

---

### Finding 9: MEDIUM -- Debug Mode Not Explicitly Disabled

**Location**: `src/app/main.py:18`
**Description**: FastAPI is instantiated as `FastAPI(title="Church Budget Tool")` without `debug=False`. While FastAPI defaults to `debug=False`, explicitly setting it prevents accidental activation and makes the security posture auditable.

**Recommendation**:
```python
app = FastAPI(title="Church Budget Tool", debug=False)
```

---

### Finding 10: MEDIUM -- Floating Dependency Versions

**Location**: `pyproject.toml`
**Description**: All dependencies use `>=` version specifiers (e.g., `fastapi>=0.115.0`). In production, a `pip install` could pull a newer version with breaking changes or newly discovered vulnerabilities.

**Recommendation**: Pin exact versions for production. Generate a lockfile:
```bash
pip compile pyproject.toml -o requirements.lock
```
And use `requirements.lock` in the Dockerfile instead of `pyproject.toml`.

---

### Finding 11: MEDIUM -- No Rate Limiting on Upload Endpoints

**Location**: `src/app/routers/csv_upload.py`
**Description**: The `/api/csv/upload` and `/api/csv/preview` endpoints have no rate limiting. An attacker could send many requests in rapid succession, consuming CPU (CSV parsing) and memory.

**Recommendation**: Add rate limiting middleware (e.g., `slowapi`):
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@router.post("/upload")
@limiter.limit("10/minute")
async def upload_csv(...):
```

---

### Finding 12: LOW -- Jinja2 Autoescape Not Explicitly Enabled

**Location**: `src/app/main.py:29`
**Description**: `Jinja2Templates(directory=...)` is used without explicitly setting `autoescape=True`. FastAPI's `Jinja2Templates` does enable autoescape by default for `.html` files, but explicit configuration is a defense-in-depth best practice.

**Current risk**: LOW. The existing templates (`base.html`, `dashboard.html`) contain no user-generated content -- only static text. No XSS vector exists today. But this should be locked down before any user data is rendered in Phase 2+.

**Recommendation**:
```python
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    autoescape=True,
)
```

---

### Finding 13: LOW -- Shared Docker Network with n8n

**Location**: `docker-compose.yml:29`
**Description**: `church-net` is marked `external: true` and shared with n8n. If the budget app is compromised, the attacker has network access to n8n. Docker's default bridge networking provides some isolation, but there are no explicit egress rules.

**Recommendation**: If the budget app does not need to communicate with n8n, use a separate network. If it does (e.g., n8n triggers budget snapshots), document the required communication paths and consider using Docker network policies.

---

### Finding 14-17: INFO -- Positive Findings

These items from the security checklist PASS:

- **Non-root Docker user**: `USER nobody` in Dockerfile -- PASS
- **Minimal base image**: `python:3.11-slim` -- PASS
- **Resource limits**: `mem_limit: 512m`, `cpus: 0.5` in docker-compose -- PASS
- **Localhost port binding**: `127.0.0.1:8000:8000` -- PASS
- **OAuth CSRF protection**: State parameter with 10-minute expiry -- PASS
- **Read-only Xero scopes**: Only `*.read` and `accounting.settings` -- PASS
- **`.dockerignore` coverage**: `.env`, `secrets/`, `.xero_tokens.json`, `*.secret` all excluded -- PASS
- **`.gitignore` coverage**: `.env`, `.env.*`, `*.secret`, `.xero_tokens.json` excluded -- PASS (after fix)
- **Token expiry handling**: Automatic refresh with 5-minute buffer -- PASS
- **Health check**: `/health` returns only `{"status": "ok"}` -- no sensitive data leaked -- PASS

---

## Pre-Deploy Checklist (Pass/Fail)

| Checklist Item | Status |
|---|---|
| Xero credentials stored as Docker secrets, not in code/git | PASS (after fix #1, #2) |
| No secrets in git history | PASS (no credentials found in `git log`) |
| `.env` files in `.gitignore` | PASS |
| Docker secrets mounted as files | PASS (docker-compose uses secrets) |
| Only read-only Xero scopes requested | PASS |
| Token expiry handled | PASS |
| CORS restricted to known origins | FAIL (Finding #8 -- no CORS configured) |
| Debug mode disabled in production | WARN (Finding #9 -- not explicit) |
| No stack traces exposed | PASS (after fix #6) |
| Rate limiting on CSV upload | FAIL (Finding #11 -- not implemented) |
| CSV upload file size limit | PASS (after fix #4) |
| CSV upload content-type validation | PASS (after fix #5) |
| Jinja2 autoescape enabled | PASS (default) / WARN (not explicit) |
| Non-root Docker user | PASS |
| Minimal base image | PASS |
| Only required ports exposed | PASS |
| Health check does not leak info | PASS |
| Docker resource limits set | PASS |
| Dependency versions pinned | FAIL (Finding #10 -- floating) |
| No known CVEs in dependencies | UNTESTED (run `pip audit` before deploy) |

---

## Risk Summary

**Overall Phase 1 Security Posture**: ACCEPTABLE after fixes applied.

The two CRITICAL findings (Docker secrets integration, gitignore gap) would have caused a deployment failure and a potential credential leak, respectively. Both are now fixed.

The four HIGH findings (file permissions, upload validation, error disclosure) are now fixed.

The remaining MEDIUM/LOW items are acceptable risks for a Phase 1 internal tool with no public authentication. They should be addressed before:
- Phase 2 (any user-facing data rendering -- fix autoescape explicitly)
- Phase 4 (auth & automation -- fix CORS, add rate limiting, pin dependencies)

**Blast radius on shared VPS**: Mitigated by Docker resource limits, localhost-only port binding, and non-root container user. The shared network with n8n remains a lateral movement concern but is acceptable for Phase 1.
