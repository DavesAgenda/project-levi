"""Unit tests for the Xero report parser.

Uses sample JSON structures from the Xero API documentation / research briefing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.xero.parser import (
    ParsedReport,
    ReportSection,
    SummaryRow,
    _extract_account_id,
    _parse_amount,
    parse_report,
    parse_xero_date,
    report_to_flat_rows,
)


# ---------------------------------------------------------------------------
# Sample P&L response (from research briefing)
# ---------------------------------------------------------------------------

SAMPLE_PL_RESPONSE = {
    "Reports": [
        {
            "ReportID": "ProfitAndLoss",
            "ReportName": "Profit and Loss",
            "ReportType": "ProfitAndLoss",
            "ReportTitles": [
                "Profit and Loss",
                "New Light Anglican Church",
                "1 January 2026 to 31 March 2026",
            ],
            "ReportDate": "30 March 2026",
            "UpdatedDateUTC": "/Date(1743321600000+0000)/",
            "Rows": [
                {
                    "RowType": "Header",
                    "Cells": [
                        {"Value": ""},
                        {"Value": "30 Mar 2026"},
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Income",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Offering EFT",
                                    "Attributes": [{"Value": "uuid-offering-eft", "Id": "account"}],
                                },
                                {
                                    "Value": "68750.00",
                                    "Attributes": [{"Value": "uuid-offering-eft", "Id": "account"}],
                                },
                            ],
                        },
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Goodhew Street 6 Rent",
                                    "Attributes": [{"Value": "uuid-rent", "Id": "account"}],
                                },
                                {
                                    "Value": "8294.40",
                                    "Attributes": [{"Value": "uuid-rent", "Id": "account"}],
                                },
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Income"},
                                {"Value": "125000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Less Operating Expenses",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Ministry Staff Salaries",
                                    "Attributes": [{"Value": "uuid-salaries", "Id": "account"}],
                                },
                                {
                                    "Value": "45000.00",
                                    "Attributes": [{"Value": "uuid-salaries", "Id": "account"}],
                                },
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Operating Expenses"},
                                {"Value": "110000.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Net Profit"},
                                {"Value": "15000.00"},
                            ],
                        },
                    ],
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Multi-period response (periods=2, timeframe=MONTH)
# ---------------------------------------------------------------------------

SAMPLE_MULTI_PERIOD_RESPONSE = {
    "Reports": [
        {
            "ReportID": "ProfitAndLoss",
            "ReportName": "Profit and Loss",
            "ReportType": "ProfitAndLoss",
            "ReportTitles": ["Profit and Loss", "Test Church", "Jan-Mar 2026"],
            "ReportDate": "30 March 2026",
            "UpdatedDateUTC": "/Date(1743321600000+0000)/",
            "Rows": [
                {
                    "RowType": "Header",
                    "Cells": [
                        {"Value": ""},
                        {"Value": "Jan 2026"},
                        {"Value": "Feb 2026"},
                        {"Value": "Mar 2026"},
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Income",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Offering EFT",
                                    "Attributes": [{"Value": "uuid-offering", "Id": "account"}],
                                },
                                {"Value": "22000.00"},
                                {"Value": "23000.00"},
                                {"Value": "23750.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Income"},
                                {"Value": "22000.00"},
                                {"Value": "23000.00"},
                                {"Value": "23750.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Net Profit"},
                                {"Value": "5000.00"},
                                {"Value": "6000.00"},
                                {"Value": "4000.00"},
                            ],
                        },
                    ],
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Tracking category response (extra columns per option)
# ---------------------------------------------------------------------------

SAMPLE_TRACKING_RESPONSE = {
    "Reports": [
        {
            "ReportID": "ProfitAndLoss",
            "ReportName": "Profit and Loss",
            "ReportType": "ProfitAndLoss",
            "ReportTitles": ["P&L by Ministry", "Test Church", "Q1 2026"],
            "ReportDate": "30 March 2026",
            "UpdatedDateUTC": "/Date(1743321600000+0000)/",
            "Rows": [
                {
                    "RowType": "Header",
                    "Cells": [
                        {"Value": ""},
                        {"Value": "Playtime"},
                        {"Value": "Youth Camp"},
                        {"Value": "Coffee Ministry"},
                        {"Value": "Unassigned"},
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "Income",
                    "Rows": [
                        {
                            "RowType": "Row",
                            "Cells": [
                                {
                                    "Value": "Ministry Income",
                                    "Attributes": [{"Value": "uuid-ministry-income", "Id": "account"}],
                                },
                                {"Value": "5000.00"},
                                {"Value": "3000.00"},
                                {"Value": "1500.00"},
                                {"Value": "500.00"},
                            ],
                        },
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Total Income"},
                                {"Value": "5000.00"},
                                {"Value": "3000.00"},
                                {"Value": "1500.00"},
                                {"Value": "500.00"},
                            ],
                        },
                    ],
                },
                {
                    "RowType": "Section",
                    "Title": "",
                    "Rows": [
                        {
                            "RowType": "SummaryRow",
                            "Cells": [
                                {"Value": "Net Profit"},
                                {"Value": "2000.00"},
                                {"Value": "1000.00"},
                                {"Value": "500.00"},
                                {"Value": "100.00"},
                            ],
                        },
                    ],
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Test: parse_xero_date
# ---------------------------------------------------------------------------

class TestParseXeroDate:
    def test_standard_format(self):
        result = parse_xero_date("/Date(1743321600000+0000)/")
        assert "2025" in result or "2026" in result  # Depends on TZ
        assert "T" in result  # ISO format

    def test_without_offset(self):
        result = parse_xero_date("/Date(1743321600000)/")
        assert "T" in result

    def test_non_xero_format_passthrough(self):
        result = parse_xero_date("2026-03-30")
        assert result == "2026-03-30"

    def test_empty_string(self):
        assert parse_xero_date("") == ""


# ---------------------------------------------------------------------------
# Test: _parse_amount
# ---------------------------------------------------------------------------

class TestParseAmount:
    def test_normal_amount(self):
        assert _parse_amount("68750.00") == Decimal("68750.00")

    def test_with_commas(self):
        assert _parse_amount("1,250,000.50") == Decimal("1250000.50")

    def test_empty_string(self):
        assert _parse_amount("") == Decimal("0")

    def test_whitespace(self):
        assert _parse_amount("  ") == Decimal("0")

    def test_negative(self):
        assert _parse_amount("-500.00") == Decimal("-500.00")

    def test_invalid_returns_zero(self):
        assert _parse_amount("N/A") == Decimal("0")


# ---------------------------------------------------------------------------
# Test: _extract_account_id
# ---------------------------------------------------------------------------

class TestExtractAccountId:
    def test_with_account_attribute(self):
        cell = {"Value": "Test", "Attributes": [{"Value": "uuid-123", "Id": "account"}]}
        assert _extract_account_id(cell) == "uuid-123"

    def test_without_attributes(self):
        cell = {"Value": "Total Income"}
        assert _extract_account_id(cell) is None

    def test_empty_attributes(self):
        cell = {"Value": "Test", "Attributes": []}
        assert _extract_account_id(cell) is None

    def test_non_account_attribute(self):
        cell = {"Value": "Test", "Attributes": [{"Value": "x", "Id": "other"}]}
        assert _extract_account_id(cell) is None


# ---------------------------------------------------------------------------
# Test: parse_report — basic P&L
# ---------------------------------------------------------------------------

class TestParseReportBasicPL:
    @pytest.fixture
    def parsed(self) -> ParsedReport:
        return parse_report(SAMPLE_PL_RESPONSE)

    def test_report_metadata(self, parsed: ParsedReport):
        assert parsed.report_id == "ProfitAndLoss"
        assert parsed.report_name == "Profit and Loss"
        assert parsed.report_date == "30 March 2026"
        assert len(parsed.report_titles) == 3

    def test_updated_at_converted(self, parsed: ParsedReport):
        # Should be ISO format, not /Date(...)/ format
        assert "/Date(" not in parsed.updated_at
        assert "T" in parsed.updated_at

    def test_column_headers(self, parsed: ParsedReport):
        assert parsed.column_headers == ["30 Mar 2026"]

    def test_section_count(self, parsed: ParsedReport):
        # Income + Expenses = 2 named sections (Net Profit section becomes a summary)
        assert len(parsed.sections) == 2

    def test_income_section(self, parsed: ParsedReport):
        income = parsed.sections[0]
        assert income.title == "Income"
        assert len(income.rows) == 2

    def test_income_accounts(self, parsed: ParsedReport):
        income = parsed.sections[0]
        offering = income.rows[0]
        assert offering.account_name == "Offering EFT"
        assert offering.account_id == "uuid-offering-eft"
        assert offering.values["30 Mar 2026"] == Decimal("68750.00")

        rent = income.rows[1]
        assert rent.account_name == "Goodhew Street 6 Rent"
        assert rent.account_id == "uuid-rent"
        assert rent.values["30 Mar 2026"] == Decimal("8294.40")

    def test_income_summary(self, parsed: ParsedReport):
        income = parsed.sections[0]
        assert income.summary is not None
        assert income.summary.label == "Total Income"
        assert income.summary.values["30 Mar 2026"] == Decimal("125000.00")

    def test_expenses_section(self, parsed: ParsedReport):
        expenses = parsed.sections[1]
        assert expenses.title == "Less Operating Expenses"
        assert len(expenses.rows) == 1
        assert expenses.rows[0].account_name == "Ministry Staff Salaries"
        assert expenses.rows[0].account_id == "uuid-salaries"

    def test_net_profit_summary(self, parsed: ParsedReport):
        # Net Profit section (untitled) becomes a top-level summary
        assert len(parsed.summaries) == 1
        assert parsed.summaries[0].label == "Net Profit"
        assert parsed.summaries[0].values["30 Mar 2026"] == Decimal("15000.00")

    def test_summary_rows_have_no_account_id(self, parsed: ParsedReport):
        # SummaryRow entries should not have account IDs
        for section in parsed.sections:
            if section.summary:
                # SummaryRow is a different type, doesn't have account_id
                assert isinstance(section.summary, SummaryRow)


# ---------------------------------------------------------------------------
# Test: parse_report — multi-period
# ---------------------------------------------------------------------------

class TestParseReportMultiPeriod:
    @pytest.fixture
    def parsed(self) -> ParsedReport:
        return parse_report(SAMPLE_MULTI_PERIOD_RESPONSE)

    def test_column_headers(self, parsed: ParsedReport):
        assert parsed.column_headers == ["Jan 2026", "Feb 2026", "Mar 2026"]

    def test_row_has_all_periods(self, parsed: ParsedReport):
        offering = parsed.sections[0].rows[0]
        assert offering.values["Jan 2026"] == Decimal("22000.00")
        assert offering.values["Feb 2026"] == Decimal("23000.00")
        assert offering.values["Mar 2026"] == Decimal("23750.00")

    def test_net_profit_all_periods(self, parsed: ParsedReport):
        net = parsed.summaries[0]
        assert net.label == "Net Profit"
        assert net.values["Jan 2026"] == Decimal("5000.00")
        assert net.values["Feb 2026"] == Decimal("6000.00")
        assert net.values["Mar 2026"] == Decimal("4000.00")


# ---------------------------------------------------------------------------
# Test: parse_report — tracking categories (variable-width columns)
# ---------------------------------------------------------------------------

class TestParseReportTracking:
    @pytest.fixture
    def parsed(self) -> ParsedReport:
        return parse_report(SAMPLE_TRACKING_RESPONSE)

    def test_column_headers(self, parsed: ParsedReport):
        assert parsed.column_headers == ["Playtime", "Youth Camp", "Coffee Ministry", "Unassigned"]

    def test_tracking_columns_mapped(self, parsed: ParsedReport):
        ministry = parsed.sections[0].rows[0]
        assert ministry.account_name == "Ministry Income"
        assert ministry.values["Playtime"] == Decimal("5000.00")
        assert ministry.values["Youth Camp"] == Decimal("3000.00")
        assert ministry.values["Coffee Ministry"] == Decimal("1500.00")
        assert ministry.values["Unassigned"] == Decimal("500.00")

    def test_handles_unknown_columns_gracefully(self):
        """If a new tracking option appears with more cells than headers, it should not crash."""
        response = {
            "Reports": [
                {
                    "ReportID": "ProfitAndLoss",
                    "ReportName": "P&L",
                    "ReportType": "ProfitAndLoss",
                    "ReportTitles": [],
                    "ReportDate": "2026-03-30",
                    "Rows": [
                        {
                            "RowType": "Header",
                            "Cells": [{"Value": ""}, {"Value": "Playtime"}],
                        },
                        {
                            "RowType": "Section",
                            "Title": "Income",
                            "Rows": [
                                {
                                    "RowType": "Row",
                                    "Cells": [
                                        {"Value": "Account", "Attributes": [{"Value": "uuid", "Id": "account"}]},
                                        {"Value": "100.00"},
                                        {"Value": "200.00"},  # Extra column beyond headers
                                    ],
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        parsed = parse_report(response)
        row = parsed.sections[0].rows[0]
        # First column maps to header, second gets fallback name
        assert row.values["Playtime"] == Decimal("100.00")
        assert row.values["col_1"] == Decimal("200.00")


# ---------------------------------------------------------------------------
# Test: report_to_flat_rows
# ---------------------------------------------------------------------------

class TestReportToFlatRows:
    def test_flat_output(self):
        parsed = parse_report(SAMPLE_PL_RESPONSE)
        flat = report_to_flat_rows(parsed)

        assert len(flat) == 3  # 2 income rows + 1 expense row
        assert flat[0]["section"] == "Income"
        assert flat[0]["account_name"] == "Offering EFT"
        assert flat[0]["account_id"] == "uuid-offering-eft"
        assert flat[0]["30 Mar 2026"] == 68750.00


# ---------------------------------------------------------------------------
# Test: error handling
# ---------------------------------------------------------------------------

class TestParserErrors:
    def test_empty_reports(self):
        with pytest.raises(ValueError, match="No reports found"):
            parse_report({"Reports": []})

    def test_missing_reports_key(self):
        with pytest.raises(ValueError, match="No reports found"):
            parse_report({})

    def test_no_header_row(self):
        """Report without a Header row should still parse (empty column headers)."""
        response = {
            "Reports": [
                {
                    "ReportID": "Test",
                    "ReportName": "Test",
                    "ReportType": "Test",
                    "ReportTitles": [],
                    "ReportDate": "",
                    "Rows": [
                        {
                            "RowType": "Section",
                            "Title": "Income",
                            "Rows": [
                                {
                                    "RowType": "Row",
                                    "Cells": [
                                        {"Value": "Account A"},
                                        {"Value": "100.00"},
                                    ],
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        parsed = parse_report(response)
        assert parsed.column_headers == []
        # Values should use fallback column names
        assert parsed.sections[0].rows[0].values["col_0"] == Decimal("100.00")
