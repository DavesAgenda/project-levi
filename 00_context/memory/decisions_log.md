# Decisions Log

## 2026-03-30 — Use FastAPI + Jinja2 + htmx + Tailwind (not SPA)
**Decision**: Server-rendered app with htmx for interactivity, not a React/Vue SPA
**Rationale**: The UI is mostly read-only dashboards. Server-side rendering is simpler — report generation reads multiple data files (easier in Python), Xero credentials stay server-side, and htmx gives responsive feel with minimal frontend complexity. Volunteer admins don't need a complex SPA.
**Agent**: Felicity (architecture recommendation from PRD)
**Impact**: No Node.js build step, no frontend framework, simpler deployment

---

## 2026-03-30 — Xero Web App with OAuth 2.0 auth code flow (not Custom Connection)
**Decision**: Use standard Web App (auth code grant) instead of Custom Connection (client credentials grant)
**Rationale**: Custom Connection requires a paid Xero partner subscription. Web App is free (up to 5 orgs), uses a one-time browser authorization, and refresh tokens handle silent renewal (60-day expiry). Tested and confirmed working against New Light Anglican Church org.
**Agent**: Perry (discovered during credential testing)
**Impact**: Auth flow requires one-time browser consent + refresh token storage. No xero-tenant-id auto-detection — must store tenant ID from /connections response. CSV import retained as fallback.

---

## 2026-03-30 — Docker on Hostinger KVM1 (not Cloud Run)
**Decision**: Deploy alongside existing n8n on Hostinger KVM1 VPS
**Rationale**: Free (infrastructure exists), n8n co-location simplifies notifications, single infrastructure to manage. Cloud Run remains fallback if VPS becomes resource-constrained.
**Agent**: Tony (deployment recommendation from PRD)
**Impact**: Shared VPS — must respect resource limits (512MB/0.5CPU for budget app). Tony owns deployment pipeline.

---

## 2026-03-30 — Git-as-database for data versioning
**Decision**: Store all financial data (YAML configs, JSON snapshots, CSV imports) in a git repo, not a database
**Rationale**: Audit trail via git history, diffing between snapshots, AI-readable (Claude Code reads repo directly), no database operational overhead for a tiny dataset. AGM reports reference committed snapshots (pinned, not live).
**Agent**: Felicity (architecture from PRD)
**Impact**: No database migrations, no backup strategy needed beyond git remote. Data changes are git commits.

---

## 2026-03-30 — Design token system for white-label support
**Decision**: All UI colors via CSS custom properties (9 tokens). Tailwind extends tokens, never uses built-in colors. Restyling = swapping one CSS file.
**Rationale**: If open-sourced for other churches, each parish needs its own branding without touching component code. Tokens are the single restyling mechanism.
**Agent**: Jimmy (design architecture)
**Impact**: Jimmy produces design_spec.md before any UI work. Felicity implements only from tokens. Superman's chart colors derive from same tokens.

---

## 2026-03-30 — Agent team structure with 8 agents and 10 skills
**Decision**: 8 agents (Perry, Felicity, Jimmy, Superman, Kryptonite, Lois, Clark, Tony) with 10 targeted skills. Dev-platform skills report to Felicity who coordinates.
**Rationale**: Each agent has clear domain ownership. Skills are project-specific (not generic). Felicity as engineering lead coordinates Jimmy (design), Superman (charts), and Tony (deploy). Clark handles documentation separately from Perry's Linear task management.
**Agent**: Perry (orchestration decision)
**Impact**: All agents defined in `.claude/agents/`, all skills in `.claude/skills/`. CLAUDE.md updated as manifest.

---

## 2026-03-30 — Xero granular scopes confirmed, direct httpx over SDK
**Decision**: Use the four granular scopes (`accounting.reports.profitandloss.read`, `accounting.reports.trialbalance.read`, `accounting.reports.balancesheet.read`, `accounting.settings`). Use direct `httpx` calls, not the `xero-python` SDK.
**Rationale**: All four scopes confirmed available and **mandatory** for apps created after 2 March 2026. Broad scope `accounting.reports.read` is rejected — must use granular. Verified live: P&L report returns 10 row sections for New Light Anglican Church. SDK adds dependency overhead for only 4 endpoints; direct `httpx` is simpler.
**Agent**: Lois (research CHA-169) + Perry (live credential testing)
**Impact**: Felicity must use granular scope names in token requests. `xero-tenant-id` header **is** required for Web Apps (unlike Custom Connections). Parser must handle dynamic column widths when tracking categories are included.
**Reference**: `00_context/research/xero_custom_connection_briefing.md`

---

## 2026-03-30 — Unified data pipeline: CSV + Xero → FinancialSnapshot
**Decision**: Both data paths (CSV import and Xero API) produce identical `FinancialSnapshot` Pydantic models. CHA-172 (snapshots), CHA-173 (mapping engine), and CHA-174 (CSV import) were built as a single unified module rather than three separate services.
**Rationale**: Shared output format means the dashboard only needs one rendering path. Building them together ensured consistent account lookup, validation, and error reporting across both data sources. Strict mode (default) rejects unrecognised accounts per PRD requirement; lenient mode available for preview.
**Agent**: Felicity (engineering decision during Wave 3)
**Impact**: `src/app/csv_import.py` owns parsing + mapping + validation. `src/app/xero/` owns API client + parser + snapshots. Both output `FinancialSnapshot`. Dashboard (CHA-175) consumes one format.

---

## 2026-03-30 — Docker secrets for Xero credentials (not env vars)
**Decision**: Xero client_id and client_secret stored via Docker secrets, not plain environment variables.
**Rationale**: Shared VPS with n8n — env vars visible to any process on the host. Docker secrets are mounted as files in `/run/secrets/`, readable only by the container process.
**Agent**: Tony (deployment architecture during Wave 3)
**Impact**: `docker-compose.yml` declares secrets. App reads from environment in dev, secrets file in production.

---

## 2026-03-30 — Component architecture: Jinja2 macros + Alpine.js
**Decision**: Adopt Jinja2 macros as reusable UI components (`templates/components/`) with Alpine.js for client-side interactivity (sorting, toggles). Not React, not Web Components.
**Rationale**: The app is server-rendered (FastAPI + Jinja2 + htmx). Alpine.js is the idiomatic companion to htmx — lightweight reactivity without a build step or virtual DOM. Jinja2 macros give parameterized, reusable components that M2's 5+ report views can share. Inline JS ad-hoc scripts don't scale.
**Agent**: Jimmy (architecture decision, prompted by user feedback on sort bug)
**Impact**: All new UI components go in `templates/components/` as importable macros. Alpine.js state lives in `static/js/`. Three foundation components established: `sortable_table`, `kpi_card`, `chart_card`. CTA buttons use `bg-primary text-white` (orange on white).

---

## 2026-03-30 — Rebrand to New Light Anglican Church identity
**Decision**: Replace generic blue/amber design tokens with official New Light Anglican Church brand colors from https://newlightanglican.church/brand/. Primary: `#ff7300` (orange), text: `#313638` (dark charcoal), background: `#FFFFFF` (white), surface accents: `#e0dfd5` (cream), `#e8e9eb` (light gray). Logo SVG added to nav. Font weights updated to match brand (Inter Display for headings at 900/700/600).
**Rationale**: The tool should feel like a New Light product, not a generic dashboard. Brand page provides a complete design system with exact hex values, typography, and border radii.
**Agent**: Jimmy (design token update)
**Impact**: Only `tokens.css` and `base.html` changed — the entire UI inherits the rebrand because all components use CSS custom properties. Zero hardcoded colors in templates.

---

## 2026-03-30 — Linear project "Church Budget Tool" with slug `lev`
**Decision**: Track all work in Linear under team "Valid Agenda", project "Church Budget Tool", using agent name labels for assignment and `repo:lev` for repo work.
**Rationale**: Consistent with existing Valid Agenda Linear workflow. 4 milestones map to PRD phases. 13 Phase 1 issues with acceptance criteria and agent assignments.
**Agent**: Perry (task management)
**Impact**: All task tracking in Linear. Perry manages backlog, Clark documents outcomes.

---

## 2026-03-30 — File-based versioning for budgets (not git-from-web-server)
**Decision**: Budget saves use file-level versioning (changelog JSON + history snapshots), not `git commit` from the web server.
**Rationale**: Running `git commit` inside a Docker web request is fragile — concurrency lock issues, git config requirements, no push = no backup, subprocess error handling complexity. File-based approach is simpler and more robust: append-only changelog JSON per budget year (`budgets/{year}.changelog.json`), prior versions saved to `budgets/history/{year}_v{n}.yaml`, optimistic concurrency via file mtime comparison. Git versioning happens naturally through manual or CI commits of the repo — not from the app process.
**Agent**: Perry (architecture decision prompted by user question)
**Impact**: CHA-192 and CHA-196 updated. No subprocess git calls in budget save path. Changelog service replaces git commit audit trail. Security review (CHA-198) scope simplified — no git injection to audit.

---

## 2026-03-30 — Auth0 for authentication (not homebrew, not Supabase)
**Decision**: Use Auth0 free tier for user authentication. Not rolling our own (bcrypt + signed cookies) and not migrating storage to Supabase.
**Rationale**: The app handles financial data including individual payroll — passphrase auth without MFA is insufficient. Auth0 provides: MFA (one toggle), brute force protection, anomaly detection, audit logs, OIDC standard. Free tier covers 7,500 MAU (need ~10). Supabase was considered but would require rewriting the entire file-based data layer (15 services, 505 tests) for marginal benefit — RLS is unnecessary with one org and no multi-tenancy. Auth0 plugs the auth gap without touching working code.
**Agent**: Perry (architecture decision prompted by user security concern)
**Impact**: M4 Wave 1 adds `authlib` dependency. Auth0 tenant already set up (keys in `.env.local`). Role mapping via `config/roles.yaml` — 4 roles: admin (treasurer/rector), board (wardens), staff (limited payroll visibility).

---

## 2026-03-30 — Four-tier role model with payroll redaction
**Decision**: Four roles — `admin` (treasurer/rector, full access), `board` (wardens, read-only full visibility), `staff` (read-only, payroll detail redacted). Staff see rollup totals but not individual names/salaries/PCR.
**Rationale**: Payroll data is sensitive — staff should not see each other's salary details. Rector needs full access same as treasurer. Board (wardens) need full visibility for governance oversight but not edit capability.
**Agent**: Perry (prompted by user requirement)
**Impact**: CHA-204 implements payroll redaction. Service layer returns filtered data for staff role. Templates use `{% if not redact_payroll %}` blocks. `/budget/payroll-scenarios` returns 403 for staff.
