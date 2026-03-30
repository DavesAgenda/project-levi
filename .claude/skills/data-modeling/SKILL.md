---
name: data-modeling
description: YAML config schemas, JSON snapshots, CSV import, legacy account reconciliation, Pydantic models
metadata:
  internal: false
---

# Data Modeling Patterns

This skill defines how the church budget tool stores and processes financial data. Felicity uses this for config loading, data validation, and the mapping engine.

## Data Directory Structure

```
church-budget/
  config/
    chart_of_accounts.yaml   # Canonical account mapping (the spine)
    properties.yaml          # Rental property config
    payroll.yaml             # Staff and salary scale config
  actuals/
    {year}/
      profit_and_loss.csv    # Full year P&L (legacy CSV import)
      monthly/
        {year}-{month}.json  # Monthly API snapshot
  budgets/
    {year}.yaml              # Budget assumptions (approved)
    {year}-draft.yaml        # Working draft
```

## Pydantic Models

All config files are loaded and validated via Pydantic models:

```python
from pydantic import BaseModel

class Account(BaseModel):
    code: str
    name: str

class BudgetCategory(BaseModel):
    budget_label: str
    accounts: list[Account]
    legacy_accounts: list[Account] = []
    note: str | None = None

class ChartOfAccounts(BaseModel):
    income: dict[str, BudgetCategory]
    expenses: dict[str, BudgetCategory]
```

Load with: `ChartOfAccounts(**yaml.safe_load(path.read_text()))`

## Chart of Accounts Mapping Engine

The mapping engine is the core of the system — it translates Xero account codes into budget categories.

### Lookup Logic
1. Build a flat lookup: `{account_code: category_key}` from both `accounts` and `legacy_accounts`
2. For each line item in a Xero report/CSV, find its category
3. If a code isn't found, flag it as **unrecognised** — never silently drop
4. Sum all accounts in a category to get the category total

### Legacy Reconciliation
- `legacy_accounts` are only matched when importing historical data (pre-2026)
- Both current and legacy accounts map to the SAME parent category
- This enables continuous multi-year trend lines across chart-of-accounts changes
- Example: `30055 Playtime` (legacy) and `30000 Ministry Income` (current) both → `ministry_income`

### Year-Boundary Handling
During transition years (e.g. 2025), both old and new accounts may appear. The engine maps both to the same category and sums them. The raw data preserves original coding for audit.

## JSON Snapshot Format

Xero API responses stored verbatim as JSON. The app reads snapshots and applies mapping at query time:

```json
{
  "report_date": "2026-03-29",
  "from_date": "2026-01-01",
  "to_date": "2026-03-31",
  "source": "xero_api",
  "rows": [
    {"account_code": "10001", "account_name": "Offering EFT", "amount": 68750.00},
    {"account_code": "20060", "account_name": "Example Street 6 Rent", "amount": 32832.00}
  ]
}
```

## CSV Import Format

Expected format matches Xero P&L CSV export:
- Row 0: Header row with account names
- Column 0: Account name
- Column 1+: Period amounts
- Validate: account names must map to chart_of_accounts.yaml (current or legacy)
- Reject with clear error if unrecognised accounts found

## Budget YAML Structure

Budgets capture **assumptions**, not raw numbers. The app computes final figures:

```yaml
year: 2026
status: approved  # draft | proposed | approved
income:
  offertory:
    "10001_offering_eft": 100000
  property_income:
    overrides:
      loane_39:
        weekly_rate: 600
expenses:
  payroll:
    notes: "See payroll.yaml for per-staff detail"
```

Property budget computed from: `weekly_rate * weeks_per_year * (1 - management_fee_pct)`

## Git-as-Database Conventions

- Every data change is a git commit
- Commit messages: `"Xero sync: {period}"`, `"Budget update: {year} {field}"`, `"CSV import: {filename}"`
- Snapshots are immutable once committed — if Xero data changes, a new snapshot is created
- `git diff` between snapshots shows exactly what changed
- AGM reports reference a specific committed snapshot (pinned, not live)
