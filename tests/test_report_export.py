"""Tests for the report export service — markdown generation for all report types."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.report_export import (
    _fmt_dollar,
    _fmt_pct,
    _md_table,
    _metadata_header,
    ReportMetadata,
    agm_report_to_markdown,
    council_report_to_markdown,
    payroll_to_markdown,
    property_portfolio_to_markdown,
    REPORT_TYPES,
)


# ---------------------------------------------------------------------------
# Helper formatting tests
# ---------------------------------------------------------------------------

class TestFmtDollar:
    def test_positive(self):
        assert _fmt_dollar(1234.56) == "$1,234.56"

    def test_zero(self):
        assert _fmt_dollar(0) == "$0.00"

    def test_negative(self):
        assert _fmt_dollar(-500.0) == "($500.00)"

    def test_large_number(self):
        assert _fmt_dollar(1234567.89) == "$1,234,567.89"


class TestFmtPct:
    def test_positive(self):
        assert _fmt_pct(12.5) == "+12.5%"

    def test_negative(self):
        assert _fmt_pct(-5.3) == "-5.3%"

    def test_none(self):
        assert _fmt_pct(None) == "—"

    def test_zero(self):
        assert _fmt_pct(0.0) == "+0.0%"


class TestMdTable:
    def test_basic_table(self):
        result = _md_table(["A", "B"], [["1", "2"], ["3", "4"]])
        lines = result.split("\n")
        assert len(lines) == 4  # header, separator, 2 data rows
        assert "| A | B |" in lines[0]
        assert "| 1 | 2 |" in lines[2]

    def test_right_alignment(self):
        result = _md_table(["Name", "Amount"], [["Foo", "$100"]], ["l", "r"])
        lines = result.split("\n")
        assert "---:" in lines[1]  # right-aligned separator

    def test_empty_headers(self):
        assert _md_table([], []) == ""

    def test_row_padding(self):
        """Rows with fewer cells than headers should be padded."""
        result = _md_table(["A", "B", "C"], [["1"]])
        lines = result.split("\n")
        assert lines[2].count("|") == 4  # 3 cells + trailing pipe


class TestMetadataHeader:
    def test_basic_metadata(self):
        meta = ReportMetadata(
            title="Test Report",
            report_type="Test",
            generated_date="2026-03-30",
            data_period="Jan–Mar 2026",
        )
        result = _metadata_header(meta)
        assert "# Test Report" in result
        assert "**Report Type**: Test" in result
        assert "**Generated**: 2026-03-30" in result
        assert "**Data Period**: Jan–Mar 2026" in result
        assert "**Snapshot**" not in result  # no snapshot ref

    def test_with_snapshot_reference(self):
        meta = ReportMetadata(
            title="Test",
            report_type="Test",
            generated_date="2026-03-30",
            data_period="2026",
            snapshot_reference="2026-03-31",
        )
        result = _metadata_header(meta)
        assert "**Snapshot**: 2026-03-31" in result


# ---------------------------------------------------------------------------
# Stub data classes — minimal fakes matching the real service dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _StubCouncilRow:
    category_key: str = "offertory"
    budget_label: str = "1 - Offertory"
    section: str = "income"
    monthly_actuals: dict = field(default_factory=lambda: {"2026-01": 5000.0, "2026-02": 6000.0})
    ytd_actual: float = 11000.0
    ytd_budget: float = 10000.0
    variance_dollar: float = 1000.0
    variance_pct: float | None = 10.0


@dataclass
class _StubSectionSummary:
    label: str = "Total Income"
    monthly_totals: dict = field(default_factory=lambda: {"2026-01": 5000.0, "2026-02": 6000.0})
    ytd_actual: float = 11000.0
    ytd_budget: float = 10000.0
    variance_dollar: float = 1000.0
    variance_pct: float | None = 10.0


@dataclass
class _StubCouncilData:
    year: int = 2026
    month_keys: list = field(default_factory=lambda: ["2026-01", "2026-02"])
    month_labels: list = field(default_factory=lambda: ["Jan", "Feb"])
    income_rows: list = field(default_factory=list)
    expense_rows: list = field(default_factory=list)
    income_summary: _StubSectionSummary | None = None
    expense_summary: _StubSectionSummary | None = None
    net_monthly: dict = field(default_factory=lambda: {"2026-01": 3000.0, "2026-02": 4000.0})
    net_ytd: float = 7000.0
    net_ytd_budget: float = 5000.0
    net_variance_dollar: float = 2000.0
    net_variance_pct: float | None = 40.0
    has_data: bool = True
    generated_date: str = "2026-03-30"


@dataclass
class _StubAGMRow:
    category_key: str = "offertory"
    budget_label: str = "1 - Offertory"
    section: str = "income"
    actual: float = 120000.0
    budget: float = 110000.0
    variance_dollar: float = 10000.0
    variance_pct: float | None = 9.1
    is_significant: bool = True
    trend_values: list = field(default_factory=lambda: [90000, 95000, 100000, 105000, 120000])


@dataclass
class _StubTrendYear:
    year: int = 2025
    total_income: float = 200000.0
    total_expenses: float = 180000.0
    net_position: float = 20000.0


@dataclass
class _StubAGMSectionSummary:
    label: str = "Total Income"
    actual: float = 200000.0
    budget: float = 180000.0
    variance_dollar: float = 20000.0
    variance_pct: float | None = 11.1
    trend_values: list = field(default_factory=lambda: [150000, 160000, 170000, 180000, 200000])


@dataclass
class _StubAGMData:
    year: int = 2025
    trend_years: list = field(default_factory=lambda: [2021, 2022, 2023, 2024, 2025])
    income_rows: list = field(default_factory=list)
    expense_rows: list = field(default_factory=list)
    income_summary: _StubAGMSectionSummary | None = None
    expense_summary: _StubAGMSectionSummary | None = None
    net_actual: float = 20000.0
    net_budget: float = 10000.0
    net_variance_dollar: float = 10000.0
    net_variance_pct: float | None = 100.0
    net_trend_values: list = field(default_factory=list)
    trend_data: list = field(default_factory=list)
    has_data: bool = True
    generated_date: str = "2026-03-30"


@dataclass
class _StubPropertyPL:
    property_key: str = "hamilton_33"
    address: str = "33 Hamilton St"
    tenant: str = "John Smith"
    status: str = "occupied"
    gross_rent: float = 15000.0
    management_fee: float = 1500.0
    maintenance_costs: float = 2000.0
    levy_share: float = 500.0
    net_income: float = 11000.0
    budget_gross_rent: float = 14000.0
    budget_net_rent: float = 12600.0
    budget_variance: float = 1000.0
    budget_variance_pct: float | None = 7.1
    land_value: float = 300000.0
    building_value: float = 200000.0
    total_asset_value: float = 500000.0
    net_yield_pct: float | None = 2.2
    avg_maintenance_3yr: float | None = 1800.0

    @property
    def is_warden_occupied(self) -> bool:
        return self.status == "occupied_warden"


@dataclass
class _StubPortfolioSummary:
    properties: list = field(default_factory=list)
    total_gross_rent: float = 15000.0
    total_management_fees: float = 1500.0
    total_maintenance_costs: float = 2000.0
    total_levy_share: float = 500.0
    total_net_income: float = 11000.0
    total_budget_gross: float = 14000.0
    total_budget_net: float = 12600.0
    total_budget_variance: float = 1000.0
    total_asset_value: float = 500000.0
    portfolio_yield_pct: float | None = 2.2
    has_data: bool = True
    snapshot_period: str = "2026-01-01 to 2026-03-31"


@dataclass
class _StubStaffCost:
    name: str = "Rev. John"
    role: str = "Rector"
    fte: float = 1.0
    base_salary: float = 80000.0
    super_amount: float = 0.0
    allowances: float = 15000.0
    recoveries: float = -5000.0
    total_cost: float = 95000.0
    diocese_grade: str | None = "Incumbent"

    @property
    def net_cost(self) -> float:
        return self.total_cost + self.recoveries


@dataclass
class _StubCategoryActuals:
    category_key: str = "ministry_staff"
    label: str = "Ministry Staff"
    actual: float = 90000.0
    budget: float = 95000.0
    variance_dollar: float = -5000.0
    variance_pct: float | None = -5.3


@dataclass
class _StubPayrollData:
    staff: list = field(default_factory=list)
    category_actuals: list = field(default_factory=list)
    total_payroll_cost: float = 95000.0
    total_payroll_budget: float = 95000.0
    total_recoveries: float = -5000.0
    net_payroll_cost: float = 90000.0
    total_income: float = 200000.0
    payroll_pct_of_income: float | None = 47.5
    has_data: bool = True
    snapshot_date: str = "2026-03-31"
    snapshot_period: str = "2026-01-01 to 2026-03-31"


# ---------------------------------------------------------------------------
# Council report markdown tests
# ---------------------------------------------------------------------------

class TestCouncilReportMarkdown:
    def test_no_data(self):
        data = _StubCouncilData(has_data=False)
        result = council_report_to_markdown(data)
        assert "Parish Council Financial Report" in result
        assert "No data available" in result

    def test_with_data(self):
        income_row = _StubCouncilRow()
        expense_row = _StubCouncilRow(
            category_key="admin",
            budget_label="Administration",
            section="expenses",
            ytd_actual=4000.0,
            ytd_budget=5000.0,
            variance_dollar=-1000.0,
            variance_pct=-20.0,
        )
        income_summary = _StubSectionSummary()
        expense_summary = _StubSectionSummary(
            label="Total Expenses",
            ytd_actual=4000.0,
            ytd_budget=5000.0,
            variance_dollar=-1000.0,
            variance_pct=-20.0,
        )

        data = _StubCouncilData(
            income_rows=[income_row],
            expense_rows=[expense_row],
            income_summary=income_summary,
            expense_summary=expense_summary,
        )
        result = council_report_to_markdown(data)

        # Metadata
        assert "Parish Council Financial Report — 2026" in result
        assert "Jan–Feb 2026" in result

        # Income section
        assert "## Income" in result
        assert "1 - Offertory" in result
        assert "$11,000.00" in result  # YTD actual

        # Expense section
        assert "## Expenses" in result
        assert "Administration" in result

        # Net position
        assert "## Net Position" in result
        assert "$7,000.00" in result  # net_ytd

    def test_table_formatting(self):
        """Verify markdown tables have pipe delimiters and alignment markers."""
        data = _StubCouncilData(
            income_rows=[_StubCouncilRow()],
            income_summary=_StubSectionSummary(),
            expense_rows=[],
            expense_summary=_StubSectionSummary(label="Total Expenses"),
        )
        result = council_report_to_markdown(data)

        # Should have table formatting
        assert "| Category |" in result
        assert "---:" in result  # right-aligned columns


# ---------------------------------------------------------------------------
# AGM report markdown tests
# ---------------------------------------------------------------------------

class TestAGMReportMarkdown:
    def test_no_data(self):
        data = _StubAGMData(has_data=False)
        result = agm_report_to_markdown(data)
        assert "Annual General Meeting" in result
        assert "No data available" in result

    def test_with_data(self):
        trend_data = [
            _StubTrendYear(year=y, total_income=150000 + i * 10000,
                           total_expenses=130000 + i * 10000,
                           net_position=20000)
            for i, y in enumerate([2021, 2022, 2023, 2024, 2025])
        ]
        income_summary = _StubAGMSectionSummary()
        expense_summary = _StubAGMSectionSummary(
            label="Total Expenses",
            actual=180000.0,
            budget=170000.0,
        )
        data = _StubAGMData(
            income_rows=[_StubAGMRow()],
            expense_rows=[_StubAGMRow(
                category_key="admin",
                budget_label="Administration",
                section="expenses",
            )],
            income_summary=income_summary,
            expense_summary=expense_summary,
            trend_data=trend_data,
        )
        result = agm_report_to_markdown(data)

        assert "Annual General Meeting Financial Report — 2025" in result
        assert "Full Year 2025" in result
        assert "## Income" in result
        assert "## Expenses" in result
        assert "## Net Position" in result
        assert "## 5-Year Trend" in result
        assert "2021" in result
        assert "2025" in result

    def test_trend_table(self):
        trend_data = [
            _StubTrendYear(year=2025, total_income=200000,
                           total_expenses=180000, net_position=20000),
        ]
        data = _StubAGMData(
            trend_years=[2025],
            trend_data=trend_data,
            income_rows=[],
            expense_rows=[],
            income_summary=_StubAGMSectionSummary(),
            expense_summary=_StubAGMSectionSummary(label="Total Expenses"),
        )
        result = agm_report_to_markdown(data)
        assert "| 2025 |" in result
        assert "$200,000.00" in result


# ---------------------------------------------------------------------------
# Property portfolio markdown tests
# ---------------------------------------------------------------------------

class TestPropertyPortfolioMarkdown:
    def test_no_data(self):
        data = _StubPortfolioSummary(has_data=False)
        result = property_portfolio_to_markdown(data)
        assert "Property Portfolio Report" in result
        assert "No property data available" in result

    def test_with_data(self):
        prop = _StubPropertyPL()
        data = _StubPortfolioSummary(properties=[prop])
        result = property_portfolio_to_markdown(data)

        assert "Property Portfolio Report" in result
        assert "2026-01-01 to 2026-03-31" in result
        assert "## Property Profit & Loss" in result
        assert "33 Hamilton St" in result
        assert "John Smith" in result
        assert "$15,000.00" in result  # gross rent
        assert "## Budget Comparison" in result
        assert "## Net Yield Analysis" in result
        assert "+2.2%" in result  # yield


# ---------------------------------------------------------------------------
# Payroll markdown tests
# ---------------------------------------------------------------------------

class TestPayrollMarkdown:
    def test_no_data(self):
        data = _StubPayrollData(has_data=False)
        result = payroll_to_markdown(data)
        assert "Payroll Summary Report" in result
        assert "No payroll data available" in result

    def test_with_data(self):
        staff = _StubStaffCost()
        cat = _StubCategoryActuals()
        data = _StubPayrollData(
            staff=[staff],
            category_actuals=[cat],
        )
        result = payroll_to_markdown(data)

        assert "Payroll Summary Report" in result
        assert "## Staff Cost Breakdown" in result
        assert "Rev. John" in result
        assert "Rector" in result
        assert "$80,000.00" in result  # base salary
        assert "## Budget vs Actual by Category" in result
        assert "Ministry Staff" in result
        assert "## Key Metrics" in result
        assert "47.5%" in result  # payroll % of income

    def test_snapshot_metadata(self):
        data = _StubPayrollData(staff=[], category_actuals=[])
        result = payroll_to_markdown(data)
        assert "**Snapshot**: 2026-03-31" in result
        assert "**Data Period**: 2026-01-01 to 2026-03-31" in result


# ---------------------------------------------------------------------------
# REPORT_TYPES registry
# ---------------------------------------------------------------------------

class TestReportTypes:
    def test_all_types_registered(self):
        assert "council" in REPORT_TYPES
        assert "agm" in REPORT_TYPES
        assert "properties" in REPORT_TYPES
        assert "payroll" in REPORT_TYPES

    def test_each_has_markdown_fn(self):
        for key, info in REPORT_TYPES.items():
            assert "markdown_fn" in info, f"Missing markdown_fn for {key}"
            assert callable(info["markdown_fn"]), f"markdown_fn not callable for {key}"
