---
name: chart-viz
description: Chart.js visualization patterns for church financial dashboards — budget vs actuals, trends, property yields
metadata:
  internal: false
---

# Chart & Visualization Patterns

Superman's skill for financial data visualization. Defines chart types, data aggregation, and design-token-aware rendering.

## Charting Library: Chart.js

- Lightweight, renders via `<canvas>`, works with server-rendered htmx pages
- Load via CDN: `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- Charts initialized in inline `<script>` blocks within Jinja2 templates
- Data passed from FastAPI as JSON in template context

## Chart Catalog

### 1. YTD Budget vs Actuals (Dashboard)
- **Type**: Grouped bar chart
- **X-axis**: Budget categories (Offertory, Property Income, Staff, etc.)
- **Series**: Budget (muted), Actuals (primary or accent)
- **Question**: "Where do we stand year-to-date?"

### 2. Budget Progress Ring (Dashboard)
- **Type**: Doughnut chart (single metric)
- **Shows**: % of annual budget consumed YTD
- **Color**: primary → warning → danger as % increases
- **Question**: "How much runway do we have?"

### 3. Variance Chart (Budget vs Actuals Detail)
- **Type**: Horizontal diverging bar
- **Positive** (under budget): success color, extends right
- **Negative** (over budget): danger color, extends left
- **Question**: "Which categories have the biggest surprises?"

### 4. Multi-Year Trend (Trend Explorer)
- **Type**: Line chart with data points
- **X-axis**: Years (2020-2026)
- **Series**: One line per selected budget category
- **Legacy reconciliation applied**: Trend lines are continuous across account changes
- **Question**: "Is offertory growing or declining over time?"

### 5. Property Net Yield (Property Portfolio)
- **Type**: Horizontal bar chart
- **X-axis**: Net yield %
- **Y-axis**: Property addresses
- **Color**: Gradient from danger (low yield) to success (high yield)
- **Question**: "Which properties earn the most relative to their value?"

### 6. Property Income vs Costs (Property Portfolio)
- **Type**: Stacked bar per property
- **Series**: Gross rent, minus management fee, minus costs, minus levy share = net
- **Question**: "What's eating into Example Road 39's rental income?"

### 7. Monthly Run Rate (Dashboard)
- **Type**: Line chart with projection
- **Solid line**: Actual monthly totals (Jan-current)
- **Dashed line**: Projected monthly totals (current-Dec) based on run rate
- **Question**: "Are we on track for the full year?"

### 8. Offertory Trajectory (Trend Explorer)
- **Type**: Line chart with year-over-year overlay
- **Series**: Each year as a separate line (2022, 2023, 2024, 2025, 2026)
- **X-axis**: Months (Jan-Dec)
- **Question**: "Is this year's giving tracking ahead or behind prior years?"

## Token-Aware Color Palette

All chart colors reference CSS custom properties via JavaScript:

```javascript
function getTokenColor(token) {
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim();
}

const chartColors = {
  primary: getTokenColor('--primary'),
  accent: getTokenColor('--accent'),
  success: getTokenColor('--success'),
  danger: getTokenColor('--danger'),
  muted: getTokenColor('--muted'),
};
```

This ensures charts restyle automatically when a church overrides the token file.

## Data Endpoint Pattern

FastAPI endpoints serve chart data as JSON:

```python
@router.get("/api/charts/ytd-budget-vs-actuals")
async def ytd_budget_vs_actuals():
    return {
        "labels": ["Offertory", "Property", "Hire", "Other"],
        "datasets": [
            {"label": "Budget", "data": [100000, 135000, 0, 3000]},
            {"label": "Actual", "data": [68750, 33000, 200, 750]},
        ]
    }
```

htmx fetches and Chart.js renders:
```html
<canvas id="ytdChart" hx-get="/api/charts/ytd-budget-vs-actuals" hx-trigger="load" hx-swap="none"
        hx-on::after-request="renderYTDChart(event)"></canvas>
```

## Formatting Rules
- All dollar amounts: `$` prefix, comma-separated thousands, no decimals under $1000
- Percentages: one decimal place (e.g. 4.2%)
- Chart titles: sentence case, specific ("Offertory 2020-2026", not "Trend")
- Axis labels: always present, readable font size
- Maximum 7 series per chart — aggregate or split if more
- Print-friendly: council report tables must work at A4, no horizontal scroll

## Sparklines (Compact Indicators)
For summary cards, use inline sparklines (Chart.js with minimal config):
- No axes, no labels, no legend
- Just the trend line in primary color
- Height: 40px, width: fills container
- Use for: offertory trend, total income trend, property income trend
