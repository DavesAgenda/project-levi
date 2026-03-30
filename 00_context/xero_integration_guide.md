# Xero Integration Reference Guide

**Created**: 2026-03-30
**Author**: Clark (Documentarian)
**Audience**: Felicity (Lead Engineer) and future maintainers
**For**: CHA-171 (Build Xero API Client)
**Full briefing**: `research/xero_custom_connection_briefing.md` (Lois, CHA-169)

---

## 1. Auth Approach: Web App (OAuth 2.0 Auth Code Grant)

The team **pivoted from Custom Connection to Web App**. The reason: Custom Connection requires a paid Xero partner subscription, while Web App is free for up to 5 orgs.

| Property | Value |
|----------|-------|
| App type | Web App |
| OAuth grant | Authorization code (NOT client credentials) |
| Initial auth | One-time browser consent flow |
| Token renewal | Refresh tokens (60-day expiry, silent renewal) |
| `xero-tenant-id` header | **Required** (Web Apps are not locked to one org) |
| Tenant ID source | Store from `GET /connections` response after auth |
| HTTP client | Direct `httpx` calls (NOT the `xero-python` SDK) |

### Token Endpoint

```
POST https://identity.xero.com/connect/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(client_id:client_secret)
```

For token refresh, pass `grant_type=refresh_token&refresh_token={token}`.

### Key Difference from Original Briefing

Lois's briefing documents the Custom Connection flow (client credentials grant, no tenant ID header, 30-minute tokens with no refresh). The decisions log records the pivot to Web App. The auth implementation must use auth code grant with refresh token storage, and must include `xero-tenant-id` on every API call.

---

## 2. Required Scopes (4 Granular)

Apps created after 2026-03-02 **must** use granular scopes. The old broad `accounting.reports.read` is not available.

| Scope | What It Provides |
|-------|-----------------|
| `accounting.reports.profitandloss.read` | Profit & Loss reports — the primary financial report for budget tracking |
| `accounting.reports.trialbalance.read` | Trial Balance reports — cross-checks and account-level verification |
| `accounting.reports.balancesheet.read` | Balance Sheet reports — property asset values (Phase 2) |
| `accounting.settings` | Chart of accounts and tracking categories (e.g., ministry activity UUIDs) |

Scopes explicitly **not needed**: `accounting.transactions.*`, `accounting.contacts.*`, any `.write` scope. The app is strictly read-only.

---

## 3. API Endpoints & Parameters

### 3.1 Profit & Loss Report

```
GET https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss
Authorization: Bearer {access_token}
xero-tenant-id: {tenant_id}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `fromDate` | string (YYYY-MM-DD) | Report period start |
| `toDate` | string (YYYY-MM-DD) | Report period end |
| `periods` | integer | Number of comparison periods (e.g., 11 for 12-month breakdown) |
| `timeframe` | string | `MONTH`, `QUARTER`, or `YEAR` |
| `trackingCategoryID` | UUID | Adds columns per tracking option (ministry activity breakdown) |
| `trackingOptionID` | UUID | Filters to a single tracking option (use with `trackingCategoryID`) |
| `standardLayout` | boolean | `true` for standard layout |
| `paymentsOnly` | boolean | `true` for cash basis, `false` for accrual |

### 3.2 Trial Balance

```
GET https://api.xero.com/api.xro/2.0/Reports/TrialBalance
Authorization: Bearer {access_token}
xero-tenant-id: {tenant_id}
```

### 3.3 Balance Sheet

```
GET https://api.xero.com/api.xro/2.0/Reports/BalanceSheet
Authorization: Bearer {access_token}
xero-tenant-id: {tenant_id}
```

### 3.4 Tracking Categories

```
GET https://api.xero.com/api.xro/2.0/TrackingCategories
Authorization: Bearer {access_token}
xero-tenant-id: {tenant_id}
```

Required to retrieve the `trackingCategoryID` and `trackingOptionID` UUIDs before calling P&L with tracking breakdown.

Covered by the `accounting.settings` scope.

---

## 4. Rate Limits

| Limit | Value |
|-------|-------|
| Per-minute | 60 API calls per app |
| Daily | 5,000 API calls per connection (24-hour rolling window) |

At expected usage (5-10 calls/month), these limits are irrelevant. Still, implement basic retry with exponential backoff for HTTP 429 responses as good practice.

---

## 5. Snapshot-to-Git Pattern

All financial data lives in the git repo, not a database.

1. **Fetch**: Call Xero API for P&L / Trial Balance / Balance Sheet
2. **Snapshot**: Write the JSON response to a file in the repo (e.g., `data/snapshots/pl_2026-Q1.json`)
3. **Commit**: Git commit the snapshot with a meaningful message and date
4. **Reference**: AGM and board reports reference committed snapshots (pinned point-in-time data, not live)
5. **Audit**: Git history provides a full audit trail; diffs show what changed between periods

This means no database migrations, no backup strategy beyond git remote, and Claude Code can read the repo directly.

---

## 6. Gotchas & Constraints for Building the API Client

### Parser Design (Critical)

1. **Nested row structure**: Report responses use `Section` > `Row` nesting. Walk `Reports[0].Rows` to find `RowType: "Section"`, then iterate inner `Rows` for `RowType: "Row"` entries. A flat iteration will miss account-level data.

2. **Variable-width Cells**: The number of cells per row changes depending on whether `periods` and/or `trackingCategoryID` are included. Read column headers dynamically from the `Header` row -- never hardcode column positions.

3. **Account matching via Attributes**: Each Cell in a data row may have an `Attributes` array containing the Xero account UUID. Use this for reliable matching instead of string-matching account names.

4. **SummaryRow entries**: Subtotals ("Total Income", "Total Operating Expenses") and "Net Profit" appear as `RowType: "SummaryRow"`. These do not have `Attributes`.

5. **Tracking category columns**: When `trackingCategoryID` is passed, one column per tracking option is added. New tracking options added in Xero after the parser is built must not break it -- handle unknown columns gracefully.

6. **Date format quirk**: `UpdatedDateUTC` uses `/Date(milliseconds)/` format, not ISO 8601.

### Auth & Connection

7. **`xero-tenant-id` is required**: Unlike Custom Connections, Web Apps must include this header on every API call. Retrieve it once from `GET /connections` after authorization and store it in config.

8. **Refresh token storage**: Refresh tokens expire after 60 days. Store securely and refresh proactively. If the token expires, a new browser consent flow is required.

9. **Scope string for token requests**: Use the exact granular scope names. The space-separated string is:
   ```
   accounting.reports.profitandloss.read accounting.reports.trialbalance.read accounting.reports.balancesheet.read accounting.settings
   ```

### Architecture

10. **Use `httpx`, not `xero-python` SDK**: Direct HTTP calls are simpler for only 4 endpoints and avoid SDK dependency overhead.

11. **Tracking category workflow is two calls**: First `GET /TrackingCategories` for UUIDs, then `GET /Reports/ProfitAndLoss?trackingCategoryID={uuid}`. A single call with `trackingCategoryID` returns all options as columns -- no need for per-option calls.

12. **No deprecation risk**: No deprecation notices exist for the Reporting API endpoints. The newer Finance API (`/FinancialStatements`) is separate and does not replace these.

---

## 7. Open Questions (Non-Blocking)

- **Ministry Activities tracking category UUID**: Only obtainable after app creation (CHA-170) and first `GET /TrackingCategories` call. Code the integration now, populate UUID from config later.
- **Historical report availability**: Can we pull 2020-2024 P&L via API, or only post-connection periods? Phase 2 research; CSV import is the fallback.
- **Balance Sheet section structure**: Likely same `Rows`/`Sections`/`Cells` pattern but section titles and fixed asset account layout need verification. Phase 2.

---

*For the full research briefing including JSON response examples and source links, see `research/xero_custom_connection_briefing.md`.*
