---
name: xero-integration
description: Xero Custom Connection API integration — auth, P&L reports, snapshot-to-git pattern
metadata:
  internal: false
---

# Xero Integration Patterns

This skill covers the Xero API integration for the church budget tool. Felicity uses this when building the Xero client service.

## Authentication: Custom Connection

Xero Custom Connections use **OAuth 2.0 client credentials grant** — no user-facing auth flow, no refresh tokens.

```python
import httpx

async def get_xero_token(client_id: str, client_secret: str) -> str:
    """Request access token using client credentials. Tokens expire after 30 minutes."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://identity.xero.com/connect/token",
            data={
                "grant_type": "client_credentials",
                "scope": " ".join(XERO_SCOPES),
            },
            auth=(client_id, client_secret),
        )
        response.raise_for_status()
        return response.json()["access_token"]
```

**No `xero-tenant-id` header needed** — Custom Connections are locked to one organisation.

## Required Scopes (Granular — post-March 2026)

Apps created after 2 March 2026 MUST use granular scopes:

```python
XERO_SCOPES = [
    "accounting.reports.profitandloss.read",
    "accounting.reports.trialbalance.read",
    "accounting.reports.balancesheet.read",
    "accounting.settings",
]
```

These are **read-only**. The app cannot modify any Xero data.

## API Endpoints

### Profit & Loss Report
```python
async def pull_profit_and_loss(token: str, from_date: str, to_date: str) -> dict:
    """Pull P&L report for a date range."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "fromDate": from_date,    # YYYY-MM-DD
                "toDate": to_date,        # YYYY-MM-DD
                "standardLayout": "true",
                "paymentsOnly": "false",
            },
        )
        response.raise_for_status()
        return response.json()
```

### Other Endpoints
| Endpoint | Purpose | Params |
|----------|---------|--------|
| `GET /Reports/TrialBalance` | Full trial balance | `date` (YYYY-MM-DD) |
| `GET /Reports/BalanceSheet` | Property asset values | `date` |
| `GET /Accounts` | Chart of accounts sync | none |

### Rate Limits
- 60 calls/minute, 5,000/day — no concern at church usage levels
- Implement basic retry with exponential backoff for transient errors

## Snapshot Pattern

Every API pull is saved as a JSON snapshot and committed to git:

```
actuals/{year}/monthly/{year}-{month}.json
```

Commit message convention: `"Xero sync: {year}-{month} P&L as at {date}"`

The snapshot preserves the exact Xero API response. The mapping engine (see `data-modeling` skill) transforms it into budget categories at read time.

## Environment Variables

```
XERO_CLIENT_ID=your_client_id
XERO_CLIENT_SECRET=your_client_secret
```

Store as Docker secrets in production (see `hostinger-deploy` skill / Tony).

## Error Handling

- **401 Unauthorized**: Token expired — silently request a new one and retry
- **429 Rate Limited**: Back off and retry (unlikely at church usage)
- **500 Server Error**: Log, retry once, then fail gracefully and serve from latest snapshot
- **Unrecognised account**: Flag in response, don't silently drop — the mapping engine handles this

## SDK Note

The `xero-python` SDK is available but adds complexity. For the 4 endpoints we need, direct `httpx` calls are simpler and more maintainable. Prefer direct API calls unless we need SDK-specific features.
