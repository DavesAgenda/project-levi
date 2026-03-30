---
name: security-audit
description: Stack-specific security checklist for church budget tool — credentials, CORS, auth, input validation, dependencies
metadata:
  internal: false
---

# Security Audit Protocol

Kryptonite's skill for the church budget tool. Pre-deployment checklist and ongoing security review patterns.

## Pre-Deployment Checklist

### Credentials & Secrets
- [ ] `XERO_CLIENT_ID` and `XERO_CLIENT_SECRET` stored as Docker secrets or env vars — never in code, Dockerfile, or git
- [ ] Firebase service account JSON stored as Docker secret — never committed
- [ ] No secrets in git history (`git log --all -p | grep -i secret` — clean)
- [ ] `.env` files in `.gitignore`
- [ ] Docker secrets mounted as files, not passed via `docker run -e`

### Xero API Access
- [ ] Only read-only scopes requested — verify no write scopes in token request
- [ ] Scopes: `accounting.reports.*.read` + `accounting.settings` only
- [ ] Token cached in memory only — never written to disk or logs
- [ ] Token expiry handled silently (30-min lifecycle)

### FastAPI Configuration
- [ ] CORS restricted to known origins (dashboard domain only)
- [ ] No wildcard CORS (`*`) in production
- [ ] Debug mode disabled in production (`debug=False`)
- [ ] No stack traces exposed in error responses
- [ ] Rate limiting on CSV upload endpoint (prevent abuse)

### Firebase Authentication (Phase 4)
- [ ] Firebase token verified on every request (middleware)
- [ ] Token verification uses Firebase Admin SDK (server-side, not client-side)
- [ ] Expired/invalid tokens return 401
- [ ] Write endpoints (budget editing) check treasurer role
- [ ] Read endpoints check authenticated user against invite list

### Input Validation
- [ ] CSV upload: file size limit (e.g. 10MB max)
- [ ] CSV upload: content-type validation
- [ ] CSV upload: account code validation against chart_of_accounts.yaml
- [ ] No arbitrary file path access — uploads go to temp dir, validated, then processed
- [ ] All user inputs sanitized before rendering in Jinja2 templates (autoescaping ON)

### Docker & Infrastructure
- [ ] Non-root user in Dockerfile (`USER appuser`)
- [ ] Minimal base image (e.g. `python:3.11-slim`)
- [ ] Only required ports exposed (8000 for app)
- [ ] Health check endpoint does not leak sensitive info
- [ ] Reverse proxy (Caddy/nginx) terminates TLS — app only listens on localhost
- [ ] n8n and budget app on isolated Docker network — no unnecessary port exposure

### Dependencies
- [ ] Run `pip audit` or `safety check` against requirements
- [ ] Pin dependency versions (no floating `>=` in production)
- [ ] Review transitive dependencies of `xero-python` (if used), `firebase-admin`, `fastapi`
- [ ] No known CVEs in dependency tree

### Data Sensitivity
- **Financial data**: Church income/expense amounts — sensitive but not PII-regulated
- **Staff data**: Names and salaries in payroll.yaml — moderately sensitive
- **Property data**: Addresses and tenant names — low sensitivity
- **Xero credentials**: HIGH sensitivity — compromise gives read access to all church financials
- **Firebase credentials**: HIGH sensitivity — compromise gives auth bypass

## Blast Radius Assessment

This app shares a Hostinger KVM1 VPS with n8n:
- **Lateral movement risk**: If budget app is compromised, attacker is on same host as n8n
- **Mitigation**: Docker network isolation, separate Docker volumes, no shared secrets
- **Resource contention**: Budget app must not exhaust CPU/RAM and impact n8n
- **Mitigation**: Docker resource limits (`mem_limit`, `cpus`)

## Ongoing Review Triggers

Re-run this checklist when:
- New endpoints are added
- Authentication is implemented (Phase 4)
- Dependencies are updated
- Deployment config changes
- Any endpoint accepts user input
