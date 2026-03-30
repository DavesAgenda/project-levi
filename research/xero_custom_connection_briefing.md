# Xero Research Briefing: Custom Connection Setup
**Date**: 2026-03-30
**Agent**: Lois
**Confidence**: High (core setup and scopes), Medium (JSON structure detail — based on documented patterns, not live API test)
**Linear**: CHA-169

## Executive Summary

The Xero Custom Connection setup process and granular scope requirements are confirmed current as of March 2026. All four required scopes exist and are available for new apps. The key change is that apps created on or after 2 March 2026 **must** use granular scopes — the old broad `accounting.reports.read` scope has been split into per-report-type scopes. The `accounting.settings` scope is **unchanged**. The PRD's assumptions are accurate with no corrections needed. One nuance: when tracking categories are included in P&L reports, they appear as additional columns (not additional rows), which affects how Felicity designs the response parser.

## 1. Custom Connection Setup Process

### Current Steps

1. **Create app**: Go to [developer.xero.com](https://developer.xero.com) > My Apps > New App > select **Custom Connection** type
2. **Select scopes**: Choose the granular scopes the app needs (see Section 2 below). For apps created after 2 March 2026, only granular scopes are available — the old broad scopes are not offered.
3. **Select authorising user**: Nominate the Xero organisation admin who will authorise the connection (this is the human step — the church treasurer or warden with Xero admin access).
4. **Authorise the connection**: The nominated user completes a one-time OAuth consent flow, linking the app to the specific Xero organisation. This is done once and does not need to be repeated.
5. **Retrieve credentials**: Copy the `client_id` and `client_secret` from the app dashboard. Store these securely as environment variables / Docker secrets on the server.

### Authentication Flow (Client Credentials Grant)

```
POST https://identity.xero.com/connect/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(client_id:client_secret)

grant_type=client_credentials&scope=accounting.reports.profitandloss.read accounting.reports.trialbalance.read accounting.reports.balancesheet.read accounting.settings
```

- **Token expiry**: 30 minutes. Silently request a new token — no user interaction required.
- **No refresh tokens**: Not needed. Client credentials grant issues a fresh access token each time.
- **No `xero-tenant-id` header needed**: Custom Connections are locked to one organisation, so only the access token is required in API calls.

### Changes Since PRD

**None.** The PRD accurately describes the Custom Connection setup. All assumptions hold:
- Client credentials grant: Confirmed
- 30-minute token expiry: Confirmed
- No xero-tenant-id header: Confirmed
- Single-organisation lock: Confirmed
- Granular scopes mandatory for new apps: Confirmed (effective 2 March 2026)

## 2. Granular Scope Verification

### Required Scopes

| Scope | Purpose | Status |
|-------|---------|--------|
| `accounting.reports.profitandloss.read` | P&L reports | **Confirmed available** |
| `accounting.reports.trialbalance.read` | Trial balance | **Confirmed available** |
| `accounting.reports.balancesheet.read` | Balance sheet (property values) | **Confirmed available** |
| `accounting.settings` | Chart of accounts, tracking categories | **Confirmed available (unchanged)** |

### Availability Status

All four scopes are confirmed available and correctly named in the PRD.

**Key context from the February 2026 Xero Dev Blog announcement:**
- The broad `accounting.reports.read` scope has been **split** into per-report-type granular scopes (e.g., `accounting.reports.profitandloss.read`, `accounting.reports.trialbalance.read`, etc.)
- `accounting.settings` is **not changing** — it remains as-is
- Apps created **on or after 2 March 2026** must use granular scopes (broad scopes not available)
- Apps created **before 2 March 2026** can request granular scopes from April 2026 and must switch by September 2027

Since this is a new app (not yet created), it will use granular scopes from the start. No migration concerns.

### Scopes NOT Needed (Confirmed)

The following are correctly excluded per PRD:
- `accounting.transactions.*` — we don't read individual transactions
- `accounting.contacts.*` — no contact data needed
- Any `.write` scope — the app is strictly read-only

## 3. P&L Report Response Structure

### Endpoint

```
GET https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss
Authorization: Bearer {access_token}
```

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `fromDate` | string (YYYY-MM-DD) | Start date for the report period |
| `toDate` | string (YYYY-MM-DD) | End date for the report period |
| `periods` | integer | Number of periods to compare (e.g., 11 for monthly breakdown over a year) |
| `timeframe` | string | Period size: `MONTH`, `QUARTER`, `YEAR` |
| `trackingCategoryID` | string (UUID) | Include breakdown by tracking category options as additional columns |
| `trackingOptionID` | string (UUID) | Filter to a single tracking option (use with trackingCategoryID) |
| `standardLayout` | boolean | `true` for standard layout, `false` for cash-based |
| `paymentsOnly` | boolean | `true` for cash basis, `false` for accrual |

### JSON Structure

The response follows Xero's standard report format. Below is a representative example based on documented patterns:

```json
{
  "Reports": [
    {
      "ReportID": "ProfitAndLoss",
      "ReportName": "Profit and Loss",
      "ReportType": "ProfitAndLoss",
      "ReportTitles": [
        "Profit and Loss",
        "New Light Anglican Church",
        "1 January 2026 to 31 March 2026"
      ],
      "ReportDate": "30 March 2026",
      "UpdatedDateUTC": "/Date(1743321600000+0000)/",
      "Rows": [
        {
          "RowType": "Header",
          "Cells": [
            { "Value": "" },
            { "Value": "30 Mar 2026" }
          ]
        },
        {
          "RowType": "Section",
          "Title": "Income",
          "Rows": [
            {
              "RowType": "Row",
              "Cells": [
                { "Value": "Offering EFT", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] },
                { "Value": "68750.00", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] }
              ]
            },
            {
              "RowType": "Row",
              "Cells": [
                { "Value": "Example Street 6 Rent", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] },
                { "Value": "8294.40", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] }
              ]
            },
            {
              "RowType": "SummaryRow",
              "Cells": [
                { "Value": "Total Income" },
                { "Value": "125000.00" }
              ]
            }
          ]
        },
        {
          "RowType": "Section",
          "Title": "Less Operating Expenses",
          "Rows": [
            {
              "RowType": "Row",
              "Cells": [
                { "Value": "Ministry Staff Salaries", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] },
                { "Value": "45000.00", "Attributes": [{ "Value": "account-id-uuid", "Id": "account" }] }
              ]
            },
            {
              "RowType": "SummaryRow",
              "Cells": [
                { "Value": "Total Operating Expenses" },
                { "Value": "110000.00" }
              ]
            }
          ]
        },
        {
          "RowType": "Section",
          "Title": "",
          "Rows": [
            {
              "RowType": "SummaryRow",
              "Cells": [
                { "Value": "Net Profit" },
                { "Value": "15000.00" }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

### Parsing Notes

1. **Nested structure**: Rows are nested inside Sections. A flat iteration will miss account-level rows. The parser must walk: `Reports[0].Rows` -> find `RowType: "Section"` -> iterate inner `Rows` -> find `RowType: "Row"` for individual accounts.

2. **Cell indexing**: The first Cell is typically the account name; subsequent Cells contain amounts (one per period if `periods` param is used). When `trackingCategoryID` is specified, additional columns appear for each tracking option.

3. **Attributes contain account IDs**: Each Cell in a "Row" may have an `Attributes` array containing the Xero account UUID. This can be used for reliable matching instead of account name string matching.

4. **SummaryRow**: These contain subtotals ("Total Income", "Total Operating Expenses") and the final "Net Profit" row. They do NOT have `Attributes`.

5. **Section titles**: "Income", "Less Operating Expenses", and an untitled section for Net Profit. These map to the income/expense split in the chart of accounts.

6. **Date format quirk**: `UpdatedDateUTC` uses the `/Date(milliseconds)/` format, not ISO 8601.

7. **Multiple periods**: When `periods` and `timeframe` are specified, each Row's Cells array extends with one value per period. The Header row's Cells will show the corresponding period dates.

## 4. Tracking Category Support

### Availability in P&L

**Yes, tracking categories are supported in P&L reports.** This is critical for the ministry activity breakdown (Playtime, Youth Camp, etc.) on the consolidated `30000 Ministry Income` and `41000 Ministry Expenses` accounts.

### How It Works

When you include the `trackingCategoryID` parameter in a P&L request, the response structure changes: instead of a single amount column per account, the report adds **one column per tracking option** within that category. Each tracking option appears as an additional Cell in every Row.

**Two usage modes:**

1. **Full category breakdown** — pass only `trackingCategoryID`:
   ```
   GET /Reports/ProfitAndLoss?fromDate=2026-01-01&toDate=2026-03-31&trackingCategoryID={ministry-activities-uuid}
   ```
   Response includes columns for each option: Playtime, Youth Camp, Coffee Ministry, etc.

2. **Single option filter** — pass both `trackingCategoryID` and `trackingOptionID`:
   ```
   GET /Reports/ProfitAndLoss?fromDate=2026-01-01&toDate=2026-03-31&trackingCategoryID={uuid}&trackingOptionID={playtime-uuid}
   ```
   Response is filtered to show only that tracking option's amounts.

### Query Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `trackingCategoryID` | Yes (for breakdown) | UUID of the tracking category (e.g., "Ministry Activities") |
| `trackingOptionID` | Optional | UUID of a specific option to filter to (e.g., "Playtime") |

### Implications for Architecture

- **Single call suffices** for the full ministry breakdown — no need for separate calls per tracking option.
- However, if the consolidated P&L (without tracking) is also needed, that is a **separate call** (one without the trackingCategoryID parameter).
- Felicity's parser needs to handle the variable-width Cells array: without tracking it has N period columns; with tracking it has N columns per tracking option.
- **First, retrieve tracking category UUIDs** via `GET /TrackingCategories` (covered by `accounting.settings` scope) to know the `trackingCategoryID` and each `trackingOptionID`.

## 5. Rate Limits & Constraints

### Current Limits (Confirmed Unchanged)

| Limit | Value | Notes |
|-------|-------|-------|
| Per-minute | 60 API calls | Per app, across all tenants |
| Daily | 5,000 API calls | Per connection, per 24-hour rolling window |
| Concurrent | Not explicitly documented | Standard HTTP connection limits apply |

### 2026 Pricing Changes (Non-Breaking)

Xero introduced a new pricing model effective 2 March 2026 with five tiers (Starter, Core, Plus, Advanced, Enterprise) based on connections and data egress. **This does not affect Custom Connections for bespoke integrations** — it primarily targets certified app partners with many connections.

- **Rapid Sync** (elevated rate limits for first 30 minutes of a new connection) is now available to all certified apps. Not relevant to our use case.
- Rate limit thresholds themselves have **not changed**.

### Impact Assessment

At church usage levels (monthly P&L pull + occasional ad-hoc queries), we would use approximately 5-10 API calls per month. The 60/min and 5,000/day limits are irrelevant. No rate limiting logic is strictly needed, but implementing basic retry with exponential backoff for 429 responses is good practice.

## 6. Flags & Risks

### FLAG 1: Verify Scope Names at App Creation Time (Low Risk)
The four scope names (`accounting.reports.profitandloss.read`, etc.) are confirmed from multiple sources, but the granular scope system is new as of 2 March 2026. **When the human creates the app at developer.xero.com, verify the exact scope names are selectable in the UI.** If any scope name has been adjusted since the documentation was last updated, this is the moment we'll discover it.

**Mitigation**: The human creating the app should screenshot the scope selection screen for the team record.

### FLAG 2: Tracking Category Column Expansion (Medium Risk)
When tracking categories are included in P&L reports, the response structure changes (additional columns per tracking option). If new tracking options are added in Xero after the parser is built, the parser must handle unknown/new columns gracefully rather than breaking.

**Mitigation**: Felicity should build the parser to dynamically read column headers from the Header row rather than hardcoding column positions.

### FLAG 3: `xero-python` SDK vs Direct API (Low Risk)
The PRD mentions the `xero-python` SDK. The skill documentation (and our architecture) prefers direct `httpx` calls for simplicity. The SDK adds dependency management overhead for only 4 endpoints. **Recommendation: stick with direct `httpx` calls** as documented in the xero-integration skill.

### FLAG 4: No Deprecation Notices Found (Informational)
No deprecation notices or breaking changes were found for the Reporting API endpoints (`/Reports/ProfitAndLoss`, `/Reports/TrialBalance`, `/Reports/BalanceSheet`). The Xero Finance API offers a newer `GET /FinancialStatements` endpoint, but this is separate from and does not replace the Accounting API reports.

### FLAG 5: September 2027 Broad Scope Sunset (Informational Only)
Apps created before 2 March 2026 must migrate from broad to granular scopes by September 2027. **This does not affect us** — our app will be created after 2 March 2026 and will use granular scopes from the start.

## Implications for Build

1. **No architecture changes needed.** The PRD assumptions about Custom Connection setup, scopes, auth flow, and API endpoints are all confirmed accurate.

2. **Felicity's Xero client implementation** can proceed exactly as documented in the `xero-integration` skill — `httpx` direct calls, client credentials grant, no tenant ID header.

3. **Parser design**: The P&L response parser must handle:
   - Nested `Section` > `Row` structure (not flat)
   - Variable-width Cells array (changes with periods and tracking categories)
   - `Attributes` array on Cells for account UUID matching
   - `SummaryRow` entries for subtotals
   - Dynamic column headers from the Header row

4. **Tracking category workflow**: First call `GET /TrackingCategories` to retrieve UUIDs, then pass `trackingCategoryID` to P&L endpoint for ministry activity breakdown. This is two API calls, not one per tracking option.

5. **Human action required**: The app creation at developer.xero.com (CHA-170) is unblocked by this briefing. The human needs:
   - Xero admin access to the church organisation
   - The four scope names listed in Section 2
   - Somewhere secure to store the resulting client_id and client_secret

## Open Questions

1. **Exact tracking category UUID**: We need the UUID of the "Ministry Activities" (or equivalent) tracking category from the church's Xero organisation. This can only be obtained after the Custom Connection is created and we make our first `GET /TrackingCategories` call. Not a blocker — Felicity can code the integration and populate the UUID from config after CHA-170 is complete.

2. **Historical report availability via API**: Can we pull P&L reports for 2020-2024 via the API, or only for periods while the Custom Connection is active? This is a Phase 2 research question (historical data may need CSV import regardless). To be investigated in a follow-up task.

3. **Balance Sheet structure for property values**: The Balance Sheet endpoint likely uses the same `Rows`/`Sections`/`Cells` structure as P&L, but the specific section titles and how fixed asset accounts (land, buildings) appear needs verification. Phase 2 research.

## Sources

- [Custom Connections Guide](https://developer.xero.com/documentation/guides/oauth2/custom-connections/) — accessed via search 2026-03-30
- [Client Credentials Grant](https://developer.xero.com/documentation/guides/oauth2/client-credentials/) — accessed via search 2026-03-30
- [Granular Scopes](https://developer.xero.com/documentation/guides/oauth2/scopes/) — accessed via search 2026-03-30
- [Granular Scopes FAQs](https://developer.xero.com/faq/granular-scopes) — accessed via search 2026-03-30
- [Upcoming Changes to Xero Accounting API Scopes (Dev Blog, Feb 2026)](https://devblog.xero.com/upcoming-changes-to-xero-accounting-api-scopes-705c5a9621a0) — accessed via search 2026-03-30
- [Accounting API Reports](https://developer.xero.com/documentation/api/accounting/reports) — accessed via search 2026-03-30
- [Tracking Categories API](https://developer.xero.com/documentation/api/accounting/trackingcategories) — accessed via search 2026-03-30
- [API Rate Limits](https://developer.xero.com/documentation/guides/oauth2/limits/) — accessed via search 2026-03-30
- [Xero Tenants / Tenant ID](https://developer.xero.com/documentation/guides/oauth2/tenants) — accessed via search 2026-03-30
- [Custom Connection Announcement](https://developer.xero.com/announcements/introducing-custom-connections) — accessed via search 2026-03-30
- [SyncHub Blog: Building Charts with Xero Reporting API](https://blog.synchub.io/articles/building-meaningful-charts-using-xeros-reporting-api) — P&L JSON structure reference
- Project skill: `.claude/skills/xero-research/SKILL.md` — internal reference documentation
- Project skill: `.claude/skills/xero-integration/SKILL.md` — internal reference documentation

---

*Briefing prepared by Lois (Research & Intelligence). Ready for handoff to Clark (documentation) and Felicity (implementation). CHA-170 (Human: Create Xero Developer App) is unblocked.*
