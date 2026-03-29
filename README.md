# Church Budget Tool (Levi)

A budget management and reporting tool for church finances, integrating with Xero for actuals and providing budget-vs-actual dashboards.

## Tech Stack

- **Backend**: Python 3.11, FastAPI
- **Frontend**: Jinja2 + htmx, Tailwind CSS, Chart.js
- **Data**: YAML configs, JSON snapshots (Xero API), CSV fallback
- **Accounting**: Xero Custom Connection (client credentials, read-only)
- **Deploy**: Docker on Hostinger KVM1

## Directory Structure

```
project_levi/
├── config/                  # YAML configuration files
│   ├── chart_of_accounts.yaml   # Xero chart of accounts mapping
│   ├── properties.yaml          # Rental property details & rates
│   ├── payroll.yaml             # Staff and diocese salary scales
│   └── mission_giving.yaml      # Mission partner allocations
├── actuals/                 # Historical financial data
│   ├── 2024/
│   ├── 2025/
│   └── 2026/
├── budgets/                 # Annual budget files
│   └── 2026.yaml
├── reports/                 # Generated reports (PDFs gitignored)
├── 00_context/              # Project context and memory
└── .claude/                 # Agent definitions and skills
```

## Phases

1. **Foundation (MVP)** -- Config files, Xero connection, basic P&L import
2. **Reporting & Property** -- Budget vs actuals dashboards, property yield reports
3. **Budget Planning** -- Draft budgets, what-if scenarios
4. **Auth & Automation** -- User auth, scheduled Xero syncs
