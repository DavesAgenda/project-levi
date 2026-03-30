# New Light Anglican Church — Budget Management Tool

## Project Design Document

### Problem

The church's budget planning and AGM reporting currently relies on a monolithic Excel spreadsheet (`Annual Budget Planning (1).xlsx`) that has accumulated 6+ years of data across 11 sheets. The file suffers from:

- Account name inconsistencies between years (e.g. "Offering Cash" vs "Offering Family 8AM")
- Manual data entry creating drift between Xero actuals and budget figures
- Discrepancies between actuals and budgets surfaced at the 2026 AGM
- No version history — impossible to trace when or why a number changed
- Difficult to generate consistent reports across years

### Who This Is For

This tool is for **volunteer church administrators** — wardens and a treasurer who serve the church alongside their day jobs. Not CPAs, not bookkeepers. Xero handles the double-entry accounting, but its reporting and planning tools are built for businesses with professional finance staff. This tool bridges the gap by providing:

- **Church-language reporting** — "What's our offertory trend?" not "Revenue by account code"
- **Property investment visibility** — net yield per property (rent minus costs, management fees, levy)
- **Payroll clarity** — diocese salary scales are opaque; the tool should make staff costs understandable
- **AGM-ready outputs** — the reports a parish council actually needs, not generic P&L statements
- **Budget planning for non-accountants** — "what if we have a vacancy at Example Road 35?" as a simple scenario, not a spreadsheet formula exercise

The rector and wardens need to see where the church stands financially and make informed decisions. The treasurer needs to reconcile with Xero and prepare reports without manually copying numbers between spreadsheets.

### Goals

1. **Single source of truth** for church financial data, reconcilable with Xero
2. **Text-based storage** (CSV/YAML/JSON) for git versioning and AI-assisted analysis
3. **Web dashboard** for wardens and rector to view reports
4. **Automated Xero import** via API
5. **Budget planning workflow** with scenario modelling (e.g. rental vacancies, salary changes)
6. **AGM report generation** — variance analysis, multi-year trends
7. **Legacy account reconciliation** — continuous trend lines across chart-of-accounts changes
8. **Property portfolio view** — income, costs, and net yield per investment property

---

## Architecture

```
                         ┌──────────────────────────────────────┐
                         │  Hostinger KVM1 (Docker)             │
                         │                                      │
┌──────────┐   API       │  ┌──────────────┐    ┌───────────┐  │
│   Xero   │ ◄────────> │  │  Budget App  │    │   n8n     │  │
│          │  (Custom    │  │  (FastAPI)   │ ── │(notifs,   │  │
└──────────┘  Connection)│  └──────┬───────┘    │ reminders)│  │
                         │         │            └───────────┘  │
                         └─────────┼───────────────────────────┘
                                   │ snapshots + reads/writes
                            ┌──────▼───────┐
                            │  Git Repo    │
                            │  actuals/    │
                            │  budgets/    │
                            │  config/     │
                            └──────┬───────┘
                                   │ serves
                            ┌──────▼───────┐
                            │  Browser UI  │
                            │  (wardens,   │
                            │   rector)    │
                            └──────────────┘
```

### Why a server-side app, not a static frontend?

A server-side app (FastAPI) is simpler for this use case because:
- Report generation involves reading and processing multiple data files — easier in Python
- Budget calculations (rental formulas, payroll) are cleaner in Python than client-side JS
- Xero API integration lives server-side (credentials stay on the server)
- The UI is mostly read-only dashboards, not a complex interactive SPA
- Jinja2 templates + htmx gives a responsive feel with minimal frontend complexity

---

## Data Model

### Directory Structure

```
church-budget/
├── config/
│   ├── chart_of_accounts.yaml    # Canonical account mapping
│   ├── properties.yaml           # Rental property config
│   ├── payroll.yaml              # Staff and salary scale config
│   └── mission_giving.yaml       # Mission partner commitments
├── actuals/
│   ├── 2024/
│   │   ├── profit_and_loss.csv   # Full year P&L from Xero
│   │   └── monthly/              # Monthly YTD exports for council
│   │       ├── 2024-01.csv
│   │       ├── 2024-02.csv
│   │       └── ...
│   ├── 2025/
│   │   ├── profit_and_loss.csv
│   │   └── monthly/
│   │       └── ...
│   └── 2026/
│       └── monthly/
│           └── ...
├── budgets/
│   ├── 2025.yaml                 # Budget assumptions for 2025
│   ├── 2026.yaml                 # Budget assumptions for 2026
│   └── 2027-draft.yaml           # Working draft for next year
├── reports/                      # Generated (gitignored or committed for AGM record)
│   ├── agm_2026.md
│   └── council_2026_03.md
└── README.md
```

### Chart of Accounts (`config/chart_of_accounts.yaml`)

Maps Xero account codes/names to budget categories. This is the spine — all imports and reports key off this file. The 2026 cleanup archived many legacy accounts and consolidated ministry income/expenses into single accounts with Xero tracking codes.

```yaml
# config/chart_of_accounts.yaml
#
# Based on Xero chart as of 2026 (post-cleanup).
# Legacy/archived account codes are listed for historical import only.

income:
  offertory:
    budget_label: "1 - Offertory"
    accounts:
      - { code: "10001", name: "Offering EFT" }
      - { code: "10010", name: "Offertory Cash" }
      - { code: "10020", name: "Tap Offertory" }        # New for 2025+
    legacy_accounts:  # Archived — needed for pre-2025 import only
      - { code: "10005", name: "Offering Family 8AM" }
      - { code: "10015", name: "Offering Family 10AM" }
      - { code: "10300", name: "Offering Special Services" }

  thanksgiving:
    budget_label: "Thanksgiving"
    accounts:
      - { code: "10500", name: "Thanksgiving" }
    note: "One-off gifts, not included in recurring budget"

  property_income:
    budget_label: "2 - Housing Income"
    accounts:
      - { code: "20060", name: "Example Street 6 Rent" }
      - { code: "20010", name: "Example Avenue 33 Rent" }
      - { code: "20040", name: "Example Road 33 Rent" }
      - { code: "20020", name: "Example Road 35 Rent" }
      - { code: "20030", name: "Example Road 39 Rent" }
    legacy_accounts:
      - { code: "12050", name: "Rectory Rent" }        # Archived
      - { code: "20070", name: "40 Hunter Street Rent" } # Archived

  building_hire:
    budget_label: "3 - Building Hire"
    accounts:
      - { code: "12500", name: "Hall Hire" }
    legacy_accounts:
      - { code: "12010", name: "Church Building Hire" }  # Archived

  ministry_income:
    budget_label: "Ministry Income"
    note: "Consolidated in 2026 — individual ministry activities now tracked via Xero tracking codes"
    accounts:
      - { code: "30000", name: "Ministry Income" }       # New consolidated account
    legacy_accounts:  # All archived into 30000 with tracking codes
      - { code: "30010", name: "Coffee Ministry" }
      - { code: "30055", name: "Playtime" }
      - { code: "30060", name: "Riv Stem Studio" }
      - { code: "30076", name: "Creator Kids" }
      - { code: "30077", name: "Men's Breakfast" }
      - { code: "30078", name: "Night Church Dinner" }
      - { code: "30079", name: "Women's Events" }
      - { code: "30080", name: "Men's Events" }
      - { code: "30081", name: "Hot Drink" }
      - { code: "30085", name: "Toy Library" }
      - { code: "30086", name: "Gingerbread Event" }
      - { code: "30087", name: "Youth Camp" }

  other_income:
    budget_label: "4 - Other Incomes"
    accounts:
      - { code: "12200", name: "Interest Income" }
      - { code: "12100", name: "Surplice Fees (weddings etc)" }
      - { code: "30100", name: "Building Donations" }
    legacy_accounts:
      - { code: "11000", name: "Grants" }               # Archived
      - { code: "12300", name: "Diocese Locum Contribution" } # Archived
      - { code: "13000", name: "Other Income" }          # Archived

expenses:
  ministry_staff:
    budget_label: "Ministry Staff"
    accounts:
      - { code: "40100", name: "Ministry Staff Salaries" }
      - { code: "40105", name: "Ministry Staff PCR" }
      - { code: "40110", name: "Ministry Staff Allowances" }
      - { code: "40180", name: "Ministry Staff LSL Recover" }
      - { code: "40185", name: "Ministry Staff Other Salary Recovery" }

  ministry_support:
    budget_label: "Ministry Support Staff"
    accounts:
      - { code: "40200", name: "Ministry Support Salaries" }
      - { code: "40205", name: "Ministry Support Super" }
      - { code: "40210", name: "Ministry Support Workers Comp" }
      - { code: "40220", name: "Ministry Support Allowances" }
      - { code: "40280", name: "Ministry Support Salary Recovery" }
    legacy_accounts:
      - { code: "40215", name: "Ministry Support LSL" }
      - { code: "40225", name: "Ministry Support Housing" }
      - { code: "40240", name: "Ministry Support Other" }

  admin_staff:
    budget_label: "Administration Staff"
    accounts:
      - { code: "40300", name: "Administration Staff Salaries" }
      - { code: "40305", name: "Administration Super" }
    legacy_accounts:
      - { code: "40310", name: "Administration Workers Comp" }
      - { code: "40315", name: "Administration LSL" }
      - { code: "40320", name: "Administration Allowances" }
      - { code: "40325", name: "Administration Housing" }
      - { code: "40340", name: "Administration Other" }
      - { code: "40380", name: "Administration Payroll Recovery" }

  property_rental:
    budget_label: "Property Rental Expense"
    accounts:
      - { code: "40401", name: "Property rental Expense" } # Renamed from "Girl Guide Hall Hire"
      - { code: "40415", name: "Intern Accommodation" }

  ministry_expenses:
    budget_label: "Ministry Expenses"
    note: "Consolidated in 2026 — individual activities now tracked via Xero tracking codes"
    accounts:
      - { code: "41000", name: "Ministry Expenses" }     # New consolidated account
    legacy_accounts:
      - { code: "40306", name: "Playtime Expense" }
      - { code: "40307", name: "Youth Camp Expense" }
      - { code: "41100", name: "Ministry Expense" }      # Old granular account

  administration:
    budget_label: "Administration"
    accounts:
      - { code: "41505", name: "Accounting and Legal Fees" }
      - { code: "41510", name: "Administrative Expenses" }
      - { code: "41515", name: "Advertising" }
      - { code: "41517", name: "Bank Fees" }
      - { code: "41520", name: "Computer Software Licencing" }
      - { code: "41525", name: "Copyright Fees and Licencing" }
      - { code: "41530", name: "Merchant Fees" }
      - { code: "41535", name: "Other Expenses" }
      - { code: "41540", name: "Other Office Expenses" }
      - { code: "41545", name: "Phone Internet Ministry Team" }
      - { code: "41555", name: "Postage" }
      - { code: "41560", name: "Printing" }
      - { code: "41565", name: "Stationery" }
      - { code: "41570", name: "Telephone" }

  mission_giving:
    budget_label: "Mission Giving"
    accounts:
      - { code: "42501", name: "Mission Giving - Church Budget" }
      - { code: "42502", name: "Other Missions" }

  operations:
    budget_label: "Operations"
    accounts:
      - { code: "42001", name: "Council Waste Collection" }
      - { code: "43005", name: "Conferences and Seminars" }
      - { code: "43030", name: "Hospitality" }
      - { code: "43040", name: "Rates and Utilities" }
      - { code: "43050", name: "Other Outreach" }
      - { code: "43060", name: "Workers Compensation Insurance exp" }
      - { code: "43070", name: "Service Setup" }
      - { code: "43075", name: "Resource Materials" }
      - { code: "43080", name: "Car Insurance Exp" }
      - { code: "43085", name: "Training" }
      - { code: "43095", name: "Fuel & Gas" }

  property_maintenance:
    budget_label: "Property & Maintenance"
    accounts:
      - { code: "44601", name: "Repairs & Maintenance" }
      - { code: "44602", name: "Rectory Repairs & Maint" }
      - { code: "44610", name: "Cleaning and Waste Removal" }
    property_costs:  # Per-property cost tracking
      - { code: "89010", name: "Example Avenue 33 Costs" }
      - { code: "89020", name: "Example Road 35 Costs" }
      - { code: "89030", name: "Example Road 39 Costs" }
      - { code: "89040", name: "Example Road 33 Costs" }
      - { code: "89050", name: "Example Street 6 Costs" }

  diocesan:
    budget_label: "Diocesan Costs"
    accounts:
      - { code: "44901", name: "Church Land Acquisition Costs" }
      - { code: "44902", name: "Diocesan Administrative Costs" }
      - { code: "44903", name: "Property Receipts Levy" }
```

**Note on legacy accounts:** The `legacy_accounts` entries are only used when importing historical data (pre-2026). The import script should map these to their parent category so that multi-year trend reports work correctly even though the underlying Xero accounts have changed.

### Properties Config (`config/properties.yaml`)

Encodes the rental calculation assumptions separately from actuals. Each property also links to its Xero cost account for net yield calculation.

```yaml
# config/properties.yaml
properties:
  goodhew_6:
    address: "6 Example Street"
    tenant: "TenantA"
    weekly_rate: 720
    weeks_per_year: 48
    management_fee_pct: 0.055
    status: occupied
    income_account: "20060"      # Example Street 6 Rent
    cost_account: "89050"        # Example Street 6 Costs
    land_asset: "65010"          # Land value: $PLACEHOLDER
    building_asset: "66010"      # Building value: $PLACEHOLDER
    notes: "Less management fee"

  hamilton_33:
    address: "33 Example Avenue"
    tenant: "ExampleStaffB"
    weekly_rate: 0
    weeks_per_year: 48
    management_fee_pct: 0
    status: occupied_warden      # Warden-occupied, no rental income
    income_account: "20010"
    cost_account: "89010"
    land_asset: "65003"          # Investment in Example Avenue: $PLACEHOLDER
    notes: "ExampleStaffB occupied — costs only, no rental income"

  loane_33:
    address: "33 Example Road"
    tenant: "TenantB"
    weekly_rate: 675
    weeks_per_year: 48
    management_fee_pct: 0.055
    status: occupied
    income_account: "20040"
    cost_account: "89040"
    land_asset: "65007"          # Land value: $PLACEHOLDER
    building_asset: "66007"      # Building value: $PLACEHOLDER

  loane_35:
    address: "35 Example Road"
    tenant: "TenantC"
    weekly_rate: 780
    weeks_per_year: 48
    management_fee_pct: 0.055
    status: occupied
    income_account: "20020"
    cost_account: "89020"
    land_asset: "65008"          # Land value: $PLACEHOLDER
    building_asset: "66008"      # Building value: $PLACEHOLDER

  loane_39:
    address: "39 Example Road"
    tenant: "Mach"
    weekly_rate: 600
    weeks_per_year: 48
    management_fee_pct: 0.055
    status: occupied
    income_account: "20030"
    cost_account: "89030"
    land_asset: "65009"          # Land value: $PLACEHOLDER
    building_asset: "66009"      # Building value: $PLACEHOLDER

# Computed budget for each property:
# annual_budget = weekly_rate * weeks_per_year * (1 - management_fee_pct)
#
# Net yield = (actual_rent - actual_costs - mgmt_fee - property_levy_share) / (land + building value)
```

### Payroll Config (`config/payroll.yaml`)

References diocese-published salary standards.

```yaml
# config/payroll.yaml
diocese_scales:
  source: "Sydney Anglican Diocese Stipend & Salary Standards"
  year: 2026
  uplift_factor: 0.012   # 1.2% annual adjustment
  notes: "Zero Jan-Jun, 2.4% Jul-Dec (equivalent to 1.2% annual)"

staff:
  - name: "ExampleStaffA M"
    role: "Permanent"
    fte: 0.8333
    base_salary: 70000
    super_rate: 0.115
    workers_comp: 1300
    recoveries: []

  - name: "ExampleStaffB D"
    role: "Rector"
    grade: "Accredited"
    fte: 1.0
    base_salary: 80000
    pcr: 20000
    fixed_travel: 9000
    recoveries: []

  - name: "ExampleStaffC M"
    role: "Lay Minister"
    grade: "3rd Yr Asst"
    fte: 1.0
    base_salary: 70000
    pcr: 15000
    fixed_travel: 9000
    recoveries:
      - name: "ExampleRecovery"
        amount: -15000

  # Children's ministry, student positions, etc.
```

### Budget File (`budgets/2026.yaml`)

Captures planning assumptions, not raw numbers. The app computes final budget figures from these assumptions + config.

```yaml
# budgets/2026.yaml
year: 2026
status: approved        # draft | proposed | approved
approved_date: 2026-02-15

income:
  offertory:
    "10001_offering_eft": 100000          # Planning target — 17% YoY growth
    "10010_offertory_cash": 0             # Transitioning to all-EFT
    "10020_tap_offertory": 0              # New channel, not yet budgeted
    "10500_thanksgiving": 0               # Unpredictable, not budgeted

  property_income:
    # References config/properties.yaml — override only what differs from config
    overrides:
      hamilton_33:
        weekly_rate: 0                    # Warden-occupied, no income
      loane_39:
        weekly_rate: 600                  # Below market, review mid-year
    vacancy_weeks: {}                     # Per-property vacancy adjustments if needed

  building_hire:
    "12500_hall_hire": 0                  # Not budgeting for 2026

  ministry_income:
    "30000_ministry_income": 1500         # Consolidated — was Playtime + events
    notes: "Primarily Playtime income, tracked via Xero tracking codes"

  other_income:
    "12200_interest_income": 3000
    "12100_surplice_fees": 0
    "30100_building_donations": 0         # One-off 2025, not budgeted recurring

expenses:
  payroll:
    # References config/payroll.yaml — uses diocese 2026 salary standards
    notes: "Based on diocese published standards. See payroll.yaml for per-staff detail."

  ministry_expenses:
    "41000_ministry_expenses": null       # TBD — consolidated account, first year

  mission_giving:
    "42501_church_budget": 8500
    "42502_other_missions": 2500
    notes: "Total $11,000 — ExampleRecovery, GRN (ExampleOrg1, ExampleOrg2), CMS (ExampleOrg3)"

  administration:
    "41505_accounting_legal": null
    "41520_software_licencing": 2000
    "41525_copyright": 1100
    notes: "Individual line items TBD from prior year actuals"

  operations:
    "43040_rates_utilities": null          # TBD
    "43070_service_setup": null
    "43085_training": null
    "44610_cleaning": null

  property_maintenance:
    "44601_repairs_maintenance": null
    notes: "Varies significantly year to year — budget based on 3-year average"

  diocesan:
    "44901_land_acquisition": 5500        # Fairly stable
    "44902_diocesan_admin": 23000         # Fairly stable
    "44903_property_levy": null           # Varies with property income
```

---

## Xero Integration

### Primary Method: Direct API via Custom Connection

The budget app connects directly to Xero via the Accounting API using a **Custom
Connection** (OAuth 2.0 client credentials grant). This is Xero's mechanism for
single-organisation, machine-to-machine integrations — no user-facing auth flow,
no refresh token management, no multi-tenant complexity.

**Why direct API instead of n8n or CSV exports:**
- Full control over report parameters (date ranges, tracking categories, periods)
- No dependency on n8n node compatibility or Xero node quirks
- Simpler architecture — one fewer moving part in the critical data path
- The `xero-python` SDK gives complete access to the Reporting API

**Setup (one-time):**
1. Create a Xero app at developer.xero.com with "Custom Connection" type
2. Configure granular scopes (apps created after 2 March 2026 must use these):
   - `accounting.reports.profitandloss.read` — P&L reports
   - `accounting.reports.trialbalance.read` — trial balance
   - `accounting.reports.balancesheet.read` — balance sheet (property values)
   - `accounting.settings` — chart of accounts, tracking categories
3. Authorise the app against the church Xero organisation
4. Store `client_id` and `client_secret` as environment variables / Docker secrets

**Note on granular scopes:** We request only read-only report scopes plus settings.
No access to invoices, payments, bank transactions, or any write operations. This
means the authorisation prompt will be minimal and the app cannot modify any Xero data.

**Authentication flow:**
- Request access token using `client_id` + `client_secret` (client credentials grant)
- Tokens expire after 30 minutes — silently request a new one, no user interaction
- No `xero-tenant-id` header needed — custom connections are locked to one org

### API Endpoints Used

| Endpoint | Purpose | Frequency |
|----------|---------|-----------|
| `GET /Reports/ProfitAndLoss` | Monthly/YTD P&L by account | Monthly (council) |
| `GET /Reports/TrialBalance` | Full trial balance | Quarterly / annual |
| `GET /Reports/BalanceSheet` | Balance sheet for property values | Annual |
| `GET /Accounts` | Chart of accounts sync | On-demand |

**Rate limits:** 60 calls/min, 5,000/day — no concern at church usage levels.

### Snapshot Architecture: Live API + Git Versioning

Every API pull is saved as a snapshot in the git repo. This gives live data *and*
a complete audit trail:

```
┌──────────┐               ┌──────────────────────┐
│   Xero   │ ◄── API ───  │  Budget App (FastAPI) │
│   API    │  ── JSON ──> │                      │
└──────────┘               │  1. Pull from Xero    │
                           │  2. Validate + map     │
                           │  3. Snapshot to git    │
                           │  4. Serve dashboard    │
                           └──────────┬─────────────┘
                                      │ writes
                               ┌──────▼───────┐
                               │  Git Repo    │
                               │  actuals/    │
                               │  2026/monthly/│
                               │  2026-03.json │
                               └──────────────┘
```

**Why snapshot to git, not just live queries?**
- **Audit trail** — every data pull is a committed snapshot with timestamp
- **AI analysis** — Claude Code reads the git repo directly, no API credentials needed
- **Diffing** — `git diff` between monthly snapshots shows exactly what changed
- **AGM preparation** — the approved actuals for an AGM report are a committed snapshot,
  not a live query that could shift if a late journal entry is posted afterward
- **Resilience** — if Xero API is down, the app serves from the latest snapshot

### Data Pull Workflow

1. **Trigger:** Scheduled (cron on Hostinger, or triggered from dashboard)
2. App requests access token using client credentials
3. App calls Xero Reporting API for the requested period
4. Response mapped through `chart_of_accounts.yaml` — unrecognised accounts flagged
5. Snapshot saved as JSON in `actuals/{year}/monthly/{year}-{month}.json`
6. Git commit: `"Xero sync: 2026-03 P&L as at 2026-03-29"`
7. Dashboard refreshes with latest data

### Reconciliation

The app compares:
- Xero actuals (from snapshots) against budget assumptions (from YAML)
- Flags variances above configurable thresholds (e.g. >10% or >$1,000)
- Highlights accounts in Xero that don't map to any budget category
- Shows month-on-month movement to catch unusual transactions early

### Fallback: Manual CSV Import

CSV import remains available for situations where the API isn't suitable:
- **Historical migration** — importing pre-existing Xero exports for 2020-2024
- **Offline use** — uploading a CSV if the API connection has issues
- **Cross-checking** — manually exported CSV can be compared against API snapshots

The import engine validates and maps CSVs identically to API responses, using
the same `chart_of_accounts.yaml` mappings. Both paths produce the same snapshot
format in the git repo.

### n8n Role (complementary, not critical path)

With the API built into the app directly, n8n is freed up for what it does best:
- **Notifications** — alert treasurer when a monthly sync completes or variances exceed thresholds
- **Reminders** — prompt for budget review before council meetings
- **Workflow automation** — trigger report generation before AGM
- **Not** in the data pipeline itself — that's handled directly by the budget app

---

## Core Features

### Legacy Account Reconciliation

The chart of accounts has evolved significantly over the church's history. Account names have changed ("Offering Cash" → "Offering Family 8AM" → "Offertory Cash"), accounts have been split and re-merged (individual ministry accounts → consolidated "Ministry Income" with tracking codes), and some accounts have been archived entirely.

For multi-year trend reporting to work (especially the AGM 5-year summary), the system must transparently reconcile these changes. The approach:

**Account lineage mapping.** Each budget category in `chart_of_accounts.yaml` lists both current `accounts` and `legacy_accounts`. The import engine uses these to map any historical Xero export into the current category structure. A transaction coded to `30055 Playtime` in 2023 rolls up to `ministry_income` the same way a 2026 transaction coded to `30000 Ministry Income` does.

**Import-time validation.** When importing a CSV, the script checks every account code/name against both current and legacy mappings. If an account appears that isn't in either list, the import stops and asks the treasurer to classify it. This prevents the silent data loss that caused the AGM discrepancies.

**Audit trail.** Each imported CSV is stored verbatim (the raw Xero export). The reconciled/categorised view is computed, never stored. If the mapping rules change, historical reports automatically update — no manual re-entry.

**Year-boundary handling.** Some accounts were active for part of a year during transitions (e.g. both `30055 Playtime` and `30000 Ministry Income` may appear in 2025 data). The system handles this by mapping both to the same category, and the raw CSV preserves the original coding for audit.

### Property Portfolio Analysis

The church holds 5 investment properties plus the church/rectory site. Currently, rental income (20xxx accounts) and property costs (89xxx accounts) are tracked in Xero but never brought together in a single view. The property receipts levy (44903) is also a function of property income but isn't allocated per-property.

The property manager view brings all of this together:

**Per-property P&L.** For each property, show: gross rent received (from Xero actuals), management fees deducted, maintenance/repair costs (from 89xxx accounts), share of property receipts levy, and net income. This answers: "Is Example Road 39 actually making us money after that $16K in costs this year?"

**Budget vs actual per property.** The budget assumes weekly rate × 48 weeks × (1 - mgmt fee). The actual may differ due to vacancies, late payments, or rate changes mid-year. Show the variance.

**Yield calculation.** Net income as a percentage of property value (land + building from Xero fixed assets). This helps wardens make informed decisions about property investment and whether a property is worth retaining.

**What-if scenarios.** Model the impact of: a vacancy (set weeks to 0 for a property), a rent increase (change weekly rate), a major repair (add one-off cost), or selling a property (remove from portfolio and show impact on total income).

**Historical property costs.** Looking at the trial balance, property costs vary wildly year to year (e.g. Hamilton St 33 went from $36 in 2024 to $PLACEHOLDER in 2025, Example Road 39 from $PLACEHOLDER to $PLACEHOLDER). Surfacing 3-year rolling averages helps set realistic maintenance budgets.

### Views

1. **Dashboard** — Current year summary: YTD actuals vs budget, projected full-year based on run rate
2. **Budget Planning** — Edit budget assumptions for next year, with live preview of impact
3. **Council Report** — Monthly YTD vs budget table, suitable for printing or emailing to council
4. **AGM Report** — Prior year actuals vs budget, variance analysis, multi-year trends (replaces the "5year summary" sheet)
5. **Property Manager** — Per-property income, costs, net yield, and what-if scenarios (see above)
6. **Payroll Summary** — Staff costs broken down by role, compared to diocese scales
7. **Trend Explorer** — Multi-year charts for any budget category, with legacy account reconciliation applied transparently

### Authentication

- Firebase Auth with email-link sign-in
- Treasurer (Dave) manages an invite list of email addresses
- Read-only access for wardens and rector
- Write access (budget editing) for treasurer only

### Tech Stack

- **Backend**: Python 3.11+, FastAPI
- **Frontend**: Jinja2 templates + htmx for interactivity, Tailwind CSS for styling
- **Data**: YAML (config/budgets), JSON snapshots (Xero API), CSV (fallback import)
- **Xero integration**: Custom Connection (client credentials), `xero-python` SDK
- **Auth**: Firebase Admin SDK (email-link sign-in) or simple invite-based auth
- **Data storage**: GitHub repo (private), cloned at container startup
- **Notifications**: n8n (sync confirmations, variance alerts, meeting reminders)

### Deployment Options

The church already runs **n8n in Docker on a Hostinger KVM1 instance**. This opens up
two deployment paths:

**Option A: GCP Cloud Run** — scales to zero, near-zero cost, managed infrastructure.
Good if you want to keep the church's existing VPS focused on n8n and not load it further.

**Option B: Docker on Hostinger KVM1** — deploy alongside n8n on existing infrastructure.
No additional hosting cost. n8n and the budget app can communicate directly (n8n drops
CSV exports into a shared volume or hits the app's import endpoint on localhost). Simpler
networking, but you manage the infrastructure yourself.

**Recommendation:** Start with Option B for the prototype — it's free, the infra already
exists, and having n8n right next door simplifies the automated import workflow. Move to
Cloud Run later if you need more reliability or want to separate concerns.

---

## Development Phases

### Phase 1: Foundation (MVP)
- Set up git repo with data model (config files, chart of accounts YAML)
- Set up Xero Custom Connection (developer app, scopes, credentials)
- Build Xero API integration: pull P&L, trial balance, chart of accounts
- Snapshot storage: save each pull as JSON in `actuals/` with git commit
- Chart of accounts mapping with legacy reconciliation and unrecognised account detection
- CSV import fallback for historical data migration (2020-2024 from spreadsheet)
- Basic FastAPI app with dashboard view (current year YTD vs budget)
- Deploy as Docker container on Hostinger KVM1 alongside n8n

### Phase 2: Reporting & Property Analysis
- Council report view (monthly YTD vs budget)
- AGM report view (annual actuals vs budget, multi-year comparison with legacy reconciliation)
- Property portfolio view: per-property income, costs, net yield
- Payroll summary view
- Trend explorer: multi-year charts for any budget category
- Export reports as PDF or markdown

### Phase 3: Budget Planning & Scenarios
- Budget editing UI (treasurer only)
- Property what-if scenarios (rent changes, vacancy, major repairs)
- Payroll what-if scenarios (salary scale changes, new hires, departures)
- Budget approval workflow (draft → proposed → approved)
- Git commit on save (budget changes tracked in version history)

### Phase 4: Auth, Automation & Polish
- Authentication setup (Firebase Auth email-link or simpler invite-based approach)
- Scheduled auto-sync (cron triggers monthly Xero pull)
- n8n notifications: sync confirmations, variance alerts, council meeting reminders
- Historical data verification (cross-check migrated spreadsheet data against API pulls)
- Xero MCP server setup for Claude Code / Cowork ad-hoc analysis sessions

---

## Migration Plan

The existing Excel spreadsheet contains valuable historical data. Migration approach:

1. Extract chart of accounts from all year sheets, resolve naming inconsistencies
2. Build canonical `chart_of_accounts.yaml` mapping all historical account name variants
3. Extract actuals for 2020-2025 into CSV format in `actuals/` directory
4. Extract budget assumptions for each year into `budgets/` YAML files
5. Verify: regenerated reports from new data should match existing spreadsheet totals
6. Document any discrepancies found during migration (these may explain AGM issues)

---

## Key Design Decisions

**Why YAML for config/budgets, not JSON?**
YAML supports comments, which are essential for budget assumptions ("17% increase based on growth trajectory"). JSON doesn't. YAML is also more readable for non-developers who may review the files.

**Why JSON snapshots for actuals?**
The Xero API returns JSON. Storing the API response as a JSON snapshot preserves the exact data Xero produced, including account codes, names, and amounts. JSON is also easy for Python to parse and for Claude to read in analysis sessions. Manual CSV import remains as a fallback.

**Why git for versioning, not a database?**
Git gives us an audit trail, branching for draft budgets, and the ability to open the data in Claude Code for analysis. A database would add operational complexity (backups, migrations) for a tiny dataset.

**Why Cloud Run, not Cloud Functions?**
The app serves multiple views with shared state (loaded YAML/CSV files). Cloud Run keeps the app warm between requests within a session, avoiding cold-start reload of all data files on every request. Also simpler to develop locally.

**Why Xero Custom Connection, not CSV-only or n8n?**
Direct API integration gives full control over report parameters (date ranges, tracking
categories, periods) without depending on n8n node compatibility. Custom Connection uses
client credentials (no user-facing auth, no refresh tokens) — designed for exactly this
kind of single-org bespoke integration. CSV import is retained as a fallback for
historical migration and offline use.

**Why deploy on Hostinger KVM1, not GCP Cloud Run?**
The church already runs n8n in Docker on a Hostinger VPS. Deploying alongside it is free,
keeps infrastructure consolidated, and allows n8n to communicate with the budget app
directly. Cloud Run remains an option if the app outgrows the VPS or if separation of
concerns becomes important.

**Why not use Xero's built-in reporting?**
Xero's reports are built for businesses with professional finance staff. They don't reconcile across chart-of-accounts changes, don't show per-property net yield, and don't present data in the categories a church council thinks in. The tool adds a church-specific lens on top of Xero's data.
                                        