---
name: xero-research
description: Xero API documentation reference, Custom Connection constraints, granular scopes, research task templates
metadata:
  internal: false
---

# Xero Research Reference

Lois's skill for investigating Xero API capabilities and constraints for the church budget tool.

## Key Documentation

| Resource | URL | Purpose |
|----------|-----|---------|
| API Reference | https://developer.xero.com/documentation/api/accounting/overview | Full endpoint docs |
| Custom Connections | https://developer.xero.com/documentation/guides/oauth2/custom-connections | Auth setup guide |
| Granular Scopes | https://developer.xero.com/documentation/guides/oauth2/scopes | Scope reference (post-March 2026) |
| Reporting API | https://developer.xero.com/documentation/api/accounting/reports | P&L, Trial Balance, Balance Sheet |
| Rate Limits | https://developer.xero.com/documentation/guides/oauth2/limits | 60/min, 5000/day |

## Custom Connection Constraints

- **Single-organisation**: Locked to one Xero org at connection time
- **Client credentials grant**: No user-facing auth, no redirect URIs
- **No refresh tokens**: Request new access token every 30 minutes
- **No tenant ID needed**: Unlike standard OAuth apps
- **Setup**: Created at developer.xero.com → My Apps → Custom Connection
- **Authorisation**: One-time consent by an org admin

## Granular Scopes (Post-March 2026)

Apps created after 2 March 2026 **must** use granular scopes. The old `accounting.transactions.read` blanket scope is not available.

Required for this project:
```
accounting.reports.profitandloss.read
accounting.reports.trialbalance.read
accounting.reports.balancesheet.read
accounting.settings
```

**Not needed** (and should not be requested):
- `accounting.transactions.*` — we don't read individual transactions
- `accounting.contacts.*` — no contact data needed
- Any `.write` scope — the app is read-only

## Reporting API Response Format

P&L reports return a structured JSON with:
- `Reports[0].Rows` — array of row objects
- Each row has `RowType`: "Header", "Section", "Row", "SummaryRow"
- Sections contain nested rows with account details
- Account rows have `Cells` array: [account name, amount, ...]

**Known quirk**: The P&L report nests accounts inside sections (Income, Expenses, etc.). Parsing requires walking the nested structure, not just flat iteration.

## Tracking Categories

Xero tracking categories are used for ministry activity breakdown (e.g. Playtime, Youth Camp) on the consolidated `30000 Ministry Income` account. The Reporting API can filter by tracking category:

```
GET /Reports/ProfitAndLoss?trackingCategoryID={id}&trackingOptionID={id}
```

Research whether tracking category breakdown is available in the standard P&L response or requires separate calls per category.

## Research Tasks for Lois

### Pre-Build Research (Phase 1)
1. Verify Custom Connection setup process is current (post-March 2026 changes)
2. Confirm granular scope availability for the 4 required scopes
3. Test P&L report response format — document the exact JSON structure
4. Investigate tracking category support in P&L reports
5. Confirm rate limits haven't changed

### Phase 2 Research
1. Balance Sheet API — how property asset values are structured
2. Trial Balance API — how it differs from P&L for reconciliation
3. Historical report availability — can we pull P&L for 2020-2024 via API or only CSV?

### Phase 4 Research
1. Webhook availability — can Xero notify us of journal changes?
2. Chart of accounts sync — how to detect account additions/archival

## Briefing Output Format

When Lois completes research, produce a structured briefing:

```markdown
## Xero Research Briefing: [Topic]
**Date**: YYYY-MM-DD
**Confidence**: High / Medium / Low

### Findings
[Structured findings with evidence]

### Implications for Build
[How this affects Felicity's implementation]

### Open Questions
[Anything still uncertain]

### Sources
[URLs and documentation references]
```

Hand off to Clark for documentation, Felicity for implementation.
