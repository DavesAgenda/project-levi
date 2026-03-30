"""Report export service — markdown and PDF generation for all report types.

Generates downloadable markdown files with preserved table formatting and
metadata headers. PDF export is handled via the browser's @media print CSS
(each report template already includes print-friendly styling) to avoid
heavy system dependencies like weasyprint/Cairo/Pango.

Supported report types: council, agm, properties, payroll.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@dataclass
class ReportMetadata:
    """Metadata header for exported reports."""

    title: str
    report_type: str
    generated_date: str
    data_period: str
    snapshot_reference: str = ""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_dollar(value: float) -> str:
    """Format a dollar amount: $1,234.56 or ($1,234.56) for negative."""
    if value < 0:
        return f"(${abs(value):,.2f})"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    """Format a percentage or return '—' if None."""
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def _md_table(headers: list[str], rows: list[list[str]], alignments: list[str] | None = None) -> str:
    """Build a markdown table string.

    Args:
        headers: Column header labels.
        rows: List of rows, each a list of cell strings.
        alignments: Optional list of 'l', 'r', or 'c' per column.

    Returns:
        Formatted markdown table string.
    """
    if not headers:
        return ""

    num_cols = len(headers)
    if alignments is None:
        alignments = ["l"] * num_cols

    # Build separator row
    sep_parts: list[str] = []
    for align in alignments:
        if align == "r":
            sep_parts.append("---:")
        elif align == "c":
            sep_parts.append(":---:")
        else:
            sep_parts.append("---")

    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(sep_parts) + " |")

    for row in rows:
        # Pad row to match header length
        padded = row + [""] * (num_cols - len(row))
        lines.append("| " + " | ".join(padded[:num_cols]) + " |")

    return "\n".join(lines)


def _metadata_header(meta: ReportMetadata) -> str:
    """Generate the metadata block at the top of the markdown export."""
    lines = [
        f"# {meta.title}",
        "",
        f"- **Report Type**: {meta.report_type}",
        f"- **Generated**: {meta.generated_date}",
        f"- **Data Period**: {meta.data_period}",
    ]
    if meta.snapshot_reference:
        lines.append(f"- **Snapshot**: {meta.snapshot_reference}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Council report → Markdown
# ---------------------------------------------------------------------------

def council_report_to_markdown(data) -> str:
    """Convert CouncilReportData to a markdown string.

    Args:
        data: CouncilReportData instance from the council report service.

    Returns:
        Complete markdown document string.
    """
    meta = ReportMetadata(
        title=f"Parish Council Financial Report — {data.year}",
        report_type="Council Report",
        generated_date=data.generated_date or date.today().isoformat(),
        data_period=f"Jan–{data.month_labels[-1]} {data.year}" if data.month_labels else str(data.year),
    )
    parts: list[str] = [_metadata_header(meta)]

    if not data.has_data:
        parts.append("*No data available for this period.*\n")
        return "\n".join(parts)

    # Build column headers: Category | month columns... | YTD Actual | YTD Budget | Variance | Var %
    headers = ["Category"] + data.month_labels + ["YTD Actual", "YTD Budget", "Variance", "Var %"]
    alignments = ["l"] + ["r"] * (len(data.month_labels) + 4)

    # Income section
    parts.append("## Income\n")
    income_rows: list[list[str]] = []
    for row in data.income_rows:
        cells = [row.budget_label]
        for mk in data.month_keys:
            cells.append(_fmt_dollar(row.monthly_actuals.get(mk, 0)))
        cells.extend([
            _fmt_dollar(row.ytd_actual),
            _fmt_dollar(row.ytd_budget),
            _fmt_dollar(row.variance_dollar),
            _fmt_pct(row.variance_pct),
        ])
        income_rows.append(cells)

    # Income summary row
    if data.income_summary:
        s = data.income_summary
        summary_cells = ["**Total Income**"]
        for mk in data.month_keys:
            summary_cells.append(f"**{_fmt_dollar(s.monthly_totals.get(mk, 0))}**")
        summary_cells.extend([
            f"**{_fmt_dollar(s.ytd_actual)}**",
            f"**{_fmt_dollar(s.ytd_budget)}**",
            f"**{_fmt_dollar(s.variance_dollar)}**",
            f"**{_fmt_pct(s.variance_pct)}**",
        ])
        income_rows.append(summary_cells)

    parts.append(_md_table(headers, income_rows, alignments))
    parts.append("")

    # Expenses section
    parts.append("## Expenses\n")
    expense_rows: list[list[str]] = []
    for row in data.expense_rows:
        cells = [row.budget_label]
        for mk in data.month_keys:
            cells.append(_fmt_dollar(row.monthly_actuals.get(mk, 0)))
        cells.extend([
            _fmt_dollar(row.ytd_actual),
            _fmt_dollar(row.ytd_budget),
            _fmt_dollar(row.variance_dollar),
            _fmt_pct(row.variance_pct),
        ])
        expense_rows.append(cells)

    if data.expense_summary:
        s = data.expense_summary
        summary_cells = ["**Total Expenses**"]
        for mk in data.month_keys:
            summary_cells.append(f"**{_fmt_dollar(s.monthly_totals.get(mk, 0))}**")
        summary_cells.extend([
            f"**{_fmt_dollar(s.ytd_actual)}**",
            f"**{_fmt_dollar(s.ytd_budget)}**",
            f"**{_fmt_dollar(s.variance_dollar)}**",
            f"**{_fmt_pct(s.variance_pct)}**",
        ])
        expense_rows.append(summary_cells)

    parts.append(_md_table(headers, expense_rows, alignments))
    parts.append("")

    # Net position
    parts.append("## Net Position\n")
    net_headers = [""] + data.month_labels + ["YTD Actual", "YTD Budget", "Variance", "Var %"]
    net_alignments = ["l"] + ["r"] * (len(data.month_labels) + 4)
    net_cells = ["**Net (Income − Expenses)**"]
    for mk in data.month_keys:
        net_cells.append(_fmt_dollar(data.net_monthly.get(mk, 0)))
    net_cells.extend([
        _fmt_dollar(data.net_ytd),
        _fmt_dollar(data.net_ytd_budget),
        _fmt_dollar(data.net_variance_dollar),
        _fmt_pct(data.net_variance_pct),
    ])
    parts.append(_md_table(net_headers, [net_cells], net_alignments))
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# AGM report → Markdown
# ---------------------------------------------------------------------------

def agm_report_to_markdown(data) -> str:
    """Convert AGMReportData to a markdown string.

    Args:
        data: AGMReportData instance from the AGM report service.

    Returns:
        Complete markdown document string.
    """
    meta = ReportMetadata(
        title=f"Annual General Meeting Financial Report — {data.year}",
        report_type="AGM Report",
        generated_date=data.generated_date or date.today().isoformat(),
        data_period=f"Full Year {data.year}",
    )
    parts: list[str] = [_metadata_header(meta)]

    if not data.has_data:
        parts.append("*No data available for this year.*\n")
        return "\n".join(parts)

    # Actuals vs Budget table
    headers = ["Category", "Actual", "Budget", "Variance", "Var %"]
    alignments = ["l", "r", "r", "r", "r"]

    # Income
    parts.append("## Income\n")
    income_rows: list[list[str]] = []
    for row in data.income_rows:
        income_rows.append([
            row.budget_label,
            _fmt_dollar(row.actual),
            _fmt_dollar(row.budget),
            _fmt_dollar(row.variance_dollar),
            _fmt_pct(row.variance_pct),
        ])
    if data.income_summary:
        s = data.income_summary
        income_rows.append([
            f"**{s.label}**",
            f"**{_fmt_dollar(s.actual)}**",
            f"**{_fmt_dollar(s.budget)}**",
            f"**{_fmt_dollar(s.variance_dollar)}**",
            f"**{_fmt_pct(s.variance_pct)}**",
        ])
    parts.append(_md_table(headers, income_rows, alignments))
    parts.append("")

    # Expenses
    parts.append("## Expenses\n")
    expense_rows: list[list[str]] = []
    for row in data.expense_rows:
        expense_rows.append([
            row.budget_label,
            _fmt_dollar(row.actual),
            _fmt_dollar(row.budget),
            _fmt_dollar(row.variance_dollar),
            _fmt_pct(row.variance_pct),
        ])
    if data.expense_summary:
        s = data.expense_summary
        expense_rows.append([
            f"**{s.label}**",
            f"**{_fmt_dollar(s.actual)}**",
            f"**{_fmt_dollar(s.budget)}**",
            f"**{_fmt_dollar(s.variance_dollar)}**",
            f"**{_fmt_pct(s.variance_pct)}**",
        ])
    parts.append(_md_table(headers, expense_rows, alignments))
    parts.append("")

    # Net position
    parts.append("## Net Position\n")
    net_rows = [[
        "**Net (Income − Expenses)**",
        f"**{_fmt_dollar(data.net_actual)}**",
        f"**{_fmt_dollar(data.net_budget)}**",
        f"**{_fmt_dollar(data.net_variance_dollar)}**",
        f"**{_fmt_pct(data.net_variance_pct)}**",
    ]]
    parts.append(_md_table(headers, net_rows, alignments))
    parts.append("")

    # Multi-year trend table
    if data.trend_years and data.trend_data:
        parts.append("## 5-Year Trend\n")
        trend_headers = ["Year", "Total Income", "Total Expenses", "Net Position"]
        trend_alignments = ["l", "r", "r", "r"]
        trend_rows: list[list[str]] = []
        for td in data.trend_data:
            trend_rows.append([
                str(td.year),
                _fmt_dollar(td.total_income),
                _fmt_dollar(td.total_expenses),
                _fmt_dollar(td.net_position),
            ])
        parts.append(_md_table(trend_headers, trend_rows, trend_alignments))
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Property portfolio → Markdown
# ---------------------------------------------------------------------------

def property_portfolio_to_markdown(data) -> str:
    """Convert PortfolioSummary to a markdown string.

    Args:
        data: PortfolioSummary instance from the property portfolio service.

    Returns:
        Complete markdown document string.
    """
    meta = ReportMetadata(
        title="Property Portfolio Report",
        report_type="Property Portfolio",
        generated_date=date.today().isoformat(),
        data_period=data.snapshot_period or "Current",
    )
    parts: list[str] = [_metadata_header(meta)]

    if not data.has_data:
        parts.append("*No property data available.*\n")
        return "\n".join(parts)

    # Per-property P&L table
    parts.append("## Property Profit & Loss\n")
    headers = [
        "Property", "Tenant", "Gross Rent", "Mgmt Fee",
        "Maintenance", "Levy Share", "Net Income",
    ]
    alignments = ["l", "l", "r", "r", "r", "r", "r"]

    rows: list[list[str]] = []
    for p in data.properties:
        rows.append([
            p.address,
            p.tenant,
            _fmt_dollar(p.gross_rent),
            _fmt_dollar(p.management_fee),
            _fmt_dollar(p.maintenance_costs),
            _fmt_dollar(p.levy_share),
            _fmt_dollar(p.net_income),
        ])

    # Summary row
    rows.append([
        "**Total**", "",
        f"**{_fmt_dollar(data.total_gross_rent)}**",
        f"**{_fmt_dollar(data.total_management_fees)}**",
        f"**{_fmt_dollar(data.total_maintenance_costs)}**",
        f"**{_fmt_dollar(data.total_levy_share)}**",
        f"**{_fmt_dollar(data.total_net_income)}**",
    ])
    parts.append(_md_table(headers, rows, alignments))
    parts.append("")

    # Budget comparison
    parts.append("## Budget Comparison\n")
    budget_headers = ["Property", "Budget Gross", "Actual Gross", "Variance", "Var %"]
    budget_alignments = ["l", "r", "r", "r", "r"]
    budget_rows: list[list[str]] = []
    for p in data.properties:
        budget_rows.append([
            p.address,
            _fmt_dollar(p.budget_gross_rent),
            _fmt_dollar(p.gross_rent),
            _fmt_dollar(p.budget_variance),
            _fmt_pct(p.budget_variance_pct),
        ])
    parts.append(_md_table(budget_headers, budget_rows, budget_alignments))
    parts.append("")

    # Yield analysis
    parts.append("## Net Yield Analysis\n")
    yield_headers = ["Property", "Asset Value", "Net Income (Ann.)", "Net Yield %"]
    yield_alignments = ["l", "r", "r", "r"]
    yield_rows: list[list[str]] = []
    for p in data.properties:
        yield_rows.append([
            p.address,
            _fmt_dollar(p.total_asset_value),
            _fmt_dollar(p.net_income),
            _fmt_pct(p.net_yield_pct) if p.net_yield_pct is not None else "—",
        ])
    yield_rows.append([
        "**Portfolio**",
        f"**{_fmt_dollar(data.total_asset_value)}**",
        f"**{_fmt_dollar(data.total_net_income)}**",
        f"**{_fmt_pct(data.portfolio_yield_pct)}**" if data.portfolio_yield_pct is not None else "**—**",
    ])
    parts.append(_md_table(yield_headers, yield_rows, yield_alignments))
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Payroll summary → Markdown
# ---------------------------------------------------------------------------

def payroll_to_markdown(data) -> str:
    """Convert PayrollData to a markdown string.

    Args:
        data: PayrollData instance from the payroll service.

    Returns:
        Complete markdown document string.
    """
    meta = ReportMetadata(
        title="Payroll Summary Report",
        report_type="Payroll Summary",
        generated_date=date.today().isoformat(),
        data_period=data.snapshot_period or "Current",
        snapshot_reference=data.snapshot_date or "",
    )
    parts: list[str] = [_metadata_header(meta)]

    if not data.has_data:
        parts.append("*No payroll data available.*\n")
        return "\n".join(parts)

    # Staff cost breakdown
    parts.append("## Staff Cost Breakdown\n")
    staff_headers = [
        "Name", "Role", "FTE", "Base Salary", "Super",
        "Allowances", "Recoveries", "Total Cost", "Net Cost",
    ]
    staff_alignments = ["l", "l", "r", "r", "r", "r", "r", "r", "r"]

    staff_rows: list[list[str]] = []
    for s in data.staff:
        staff_rows.append([
            s.name,
            s.role,
            f"{s.fte:.2f}",
            _fmt_dollar(s.base_salary),
            _fmt_dollar(s.super_amount),
            _fmt_dollar(s.allowances),
            _fmt_dollar(s.recoveries),
            _fmt_dollar(s.total_cost),
            _fmt_dollar(s.net_cost),
        ])

    # Staff summary
    total_base = sum(s.base_salary for s in data.staff)
    total_super = sum(s.super_amount for s in data.staff)
    total_allow = sum(s.allowances for s in data.staff)
    total_fte = sum(s.fte for s in data.staff)
    staff_rows.append([
        "**Total**", "",
        f"**{total_fte:.2f}**",
        f"**{_fmt_dollar(total_base)}**",
        f"**{_fmt_dollar(total_super)}**",
        f"**{_fmt_dollar(total_allow)}**",
        f"**{_fmt_dollar(data.total_recoveries)}**",
        f"**{_fmt_dollar(data.total_payroll_cost)}**",
        f"**{_fmt_dollar(data.net_payroll_cost)}**",
    ])
    parts.append(_md_table(staff_headers, staff_rows, staff_alignments))
    parts.append("")

    # Budget vs Actual by category
    if data.category_actuals:
        parts.append("## Budget vs Actual by Category\n")
        cat_headers = ["Category", "Actual", "Budget", "Variance", "Var %"]
        cat_alignments = ["l", "r", "r", "r", "r"]
        cat_rows: list[list[str]] = []
        for c in data.category_actuals:
            cat_rows.append([
                c.label,
                _fmt_dollar(c.actual),
                _fmt_dollar(c.budget),
                _fmt_dollar(c.variance_dollar),
                _fmt_pct(c.variance_pct),
            ])

        total_actual = sum(c.actual for c in data.category_actuals)
        total_budget = sum(c.budget for c in data.category_actuals)
        total_var = total_actual - total_budget
        total_var_pct = round(total_var / total_budget * 100, 1) if total_budget > 0 else None
        cat_rows.append([
            "**Total Payroll**",
            f"**{_fmt_dollar(total_actual)}**",
            f"**{_fmt_dollar(total_budget)}**",
            f"**{_fmt_dollar(total_var)}**",
            f"**{_fmt_pct(total_var_pct)}**",
        ])
        parts.append(_md_table(cat_headers, cat_rows, cat_alignments))
        parts.append("")

    # Key metrics
    parts.append("## Key Metrics\n")
    parts.append(f"- **Total Payroll Cost**: {_fmt_dollar(data.total_payroll_cost)}")
    parts.append(f"- **Total Recoveries**: {_fmt_dollar(data.total_recoveries)}")
    parts.append(f"- **Net Payroll Cost**: {_fmt_dollar(data.net_payroll_cost)}")
    if data.total_income > 0:
        parts.append(f"- **Total Parish Income**: {_fmt_dollar(data.total_income)}")
    if data.payroll_pct_of_income is not None:
        parts.append(f"- **Payroll as % of Income**: {data.payroll_pct_of_income:.1f}%")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

# Map of report type key -> (compute_function, markdown_function)
# Used by the export router to look up the correct functions.

REPORT_TYPES = {
    "council": {
        "label": "Council Report",
        "markdown_fn": council_report_to_markdown,
    },
    "agm": {
        "label": "AGM Report",
        "markdown_fn": agm_report_to_markdown,
    },
    "properties": {
        "label": "Property Portfolio",
        "markdown_fn": property_portfolio_to_markdown,
    },
    "payroll": {
        "label": "Payroll Summary",
        "markdown_fn": payroll_to_markdown,
    },
}
