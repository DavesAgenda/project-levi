"""Tests for the CSV import engine.

Covers parsing, mapping, error handling, encoding, and snapshot conversion.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from app.csv_import import (
    build_account_lookup,
    build_name_lookup,
    import_csv,
    map_rows,
    parse_csv,
    to_snapshot,
    _clean_amount,
    _detect_account_code,
    _normalise,
)
from app.models import CSVRow, ChartOfAccounts


# ===================================================================
# Unit tests — helper functions
# ===================================================================

class TestCleanAmount:
    def test_plain_number(self):
        assert _clean_amount("1500.00") == 1500.00

    def test_with_commas(self):
        assert _clean_amount("1,500.00") == 1500.00

    def test_with_dollar_sign(self):
        assert _clean_amount("$1,500.00") == 1500.00

    def test_negative_parentheses(self):
        assert _clean_amount("(500.00)") == -500.00

    def test_dash_is_zero(self):
        assert _clean_amount("-") == 0.0

    def test_empty_is_zero(self):
        assert _clean_amount("") == 0.0

    def test_whitespace_is_zero(self):
        assert _clean_amount("   ") == 0.0

    def test_invalid_returns_zero(self):
        assert _clean_amount("N/A") == 0.0


class TestDetectAccountCode:
    def test_code_dash_name(self):
        code, name = _detect_account_code("10001 - Offering EFT")
        assert code == "10001"
        assert name == "Offering EFT"

    def test_code_endash_name(self):
        code, name = _detect_account_code("10001\u2013Offering EFT")
        assert code == "10001"
        assert name == "Offering EFT"

    def test_name_only(self):
        code, name = _detect_account_code("Offering EFT")
        assert code is None
        assert name == "Offering EFT"

    def test_bare_code(self):
        code, name = _detect_account_code("10001")
        assert code == "10001"


class TestNormalise:
    def test_basic(self):
        assert _normalise("Offering EFT") == "offeringeft"

    def test_punctuation(self):
        assert _normalise("Repairs & Maintenance") == "repairsmaintenance"


# ===================================================================
# Lookup builder tests
# ===================================================================

class TestBuildAccountLookup:
    def test_current_accounts_mapped(self, chart: ChartOfAccounts):
        lookup = build_account_lookup(chart)
        assert "10001" in lookup
        cat_key, section, label, is_legacy = lookup["10001"]
        assert cat_key == "offertory"
        assert section == "income"
        assert is_legacy is False

    def test_legacy_accounts_mapped(self, chart: ChartOfAccounts):
        lookup = build_account_lookup(chart)
        assert "10005" in lookup
        _, _, _, is_legacy = lookup["10005"]
        assert is_legacy is True

    def test_property_costs_mapped(self, chart: ChartOfAccounts):
        lookup = build_account_lookup(chart)
        assert "89010" in lookup
        cat_key, section, _, _ = lookup["89010"]
        assert cat_key == "property_maintenance"
        assert section == "expenses"


class TestBuildNameLookup:
    def test_name_resolves_to_code(self, chart: ChartOfAccounts):
        name_lookup = build_name_lookup(chart)
        assert name_lookup[_normalise("Offering EFT")] == "10001"

    def test_legacy_name_resolves(self, chart: ChartOfAccounts):
        name_lookup = build_name_lookup(chart)
        assert name_lookup[_normalise("Offering Family 8AM")] == "10005"


# ===================================================================
# CSV parsing tests
# ===================================================================

class TestParseCSV:
    def test_basic_parse(self):
        csv_text = dedent("""\
            Account,Jan-24,Feb-24
            10001 - Offering EFT,5000.00,6000.00
            20060 - Goodhew Street 6 Rent,2500.00,2500.00
        """)
        headers, rows, errors = parse_csv(csv_text)
        assert not errors
        assert headers == ["Jan-24", "Feb-24"]
        assert len(rows) == 2
        assert rows[0].account_code == "10001"
        assert rows[0].account_name == "Offering EFT"
        assert rows[0].amounts["Jan-24"] == 5000.00

    def test_skips_title_rows(self):
        csv_text = dedent("""\
            Profit & Loss

            Account,Jan-24,Feb-24
            10001 - Offering EFT,5000.00,6000.00
        """)
        headers, rows, errors = parse_csv(csv_text)
        assert not errors
        assert len(rows) == 1

    def test_skips_total_rows(self):
        csv_text = dedent("""\
            Account,Jan-24
            10001 - Offering EFT,5000.00
            Total Income,5000.00
            Net Profit,5000.00
        """)
        _, rows, _ = parse_csv(csv_text)
        assert len(rows) == 1

    def test_handles_missing_values(self):
        csv_text = dedent("""\
            Account,Jan-24,Feb-24,Mar-24
            10001 - Offering EFT,5000.00,,
        """)
        _, rows, _ = parse_csv(csv_text)
        assert rows[0].amounts["Feb-24"] == 0.0
        assert rows[0].amounts["Mar-24"] == 0.0

    def test_name_only_no_code(self):
        csv_text = dedent("""\
            Account,Jan-24
            Offering EFT,5000.00
        """)
        _, rows, _ = parse_csv(csv_text)
        assert rows[0].account_code is None
        assert rows[0].account_name == "Offering EFT"

    def test_empty_csv(self):
        _, _, errors = parse_csv("")
        assert len(errors) == 1
        assert "empty" in errors[0].message.lower()

    def test_bytes_input(self):
        csv_bytes = b"Account,Jan-24\n10001 - Offering EFT,5000.00\n"
        headers, rows, errors = parse_csv(csv_bytes)
        assert not errors
        assert len(rows) == 1

    def test_utf8_bom(self):
        csv_bytes = b"\xef\xbb\xbfAccount,Jan-24\n10001 - Offering EFT,5000.00\n"
        headers, rows, errors = parse_csv(csv_bytes)
        assert not errors
        assert len(rows) == 1

    def test_dollar_signs_and_commas(self):
        csv_text = dedent("""\
            Account,Jan-24
            10001 - Offering EFT,"$5,000.00"
        """)
        _, rows, _ = parse_csv(csv_text)
        assert rows[0].amounts["Jan-24"] == 5000.00

    def test_parenthesised_negatives(self):
        csv_text = dedent("""\
            Account,Jan-24
            40180 - Ministry Staff LSL Recover,"(1,200.00)"
        """)
        _, rows, _ = parse_csv(csv_text)
        assert rows[0].amounts["Jan-24"] == -1200.00


# ===================================================================
# Mapping tests
# ===================================================================

class TestMapRows:
    def test_code_based_mapping(self, chart):
        rows = [CSVRow(account_code="10001", account_name="Offering EFT", amounts={"Jan": 100})]
        mapped, errors, unrec = map_rows(rows, chart)
        assert len(mapped) == 1
        assert mapped[0].category_key == "offertory"
        assert mapped[0].category_section == "income"
        assert not errors

    def test_legacy_mapping(self, chart):
        rows = [CSVRow(account_code="10005", account_name="Offering Family 8AM", amounts={"Jan": 50})]
        mapped, errors, unrec = map_rows(rows, chart)
        assert len(mapped) == 1
        assert mapped[0].category_key == "offertory"
        assert mapped[0].is_legacy is True

    def test_name_fallback(self, chart):
        rows = [CSVRow(account_code=None, account_name="Offering EFT", amounts={"Jan": 100})]
        mapped, errors, unrec = map_rows(rows, chart)
        assert len(mapped) == 1
        assert mapped[0].account_code == "10001"

    def test_unrecognised_account(self, chart):
        rows = [CSVRow(account_code="99999", account_name="Unknown Account", amounts={"Jan": 100})]
        mapped, errors, unrec = map_rows(rows, chart)
        assert len(mapped) == 0
        assert len(errors) == 1
        assert "99999" in unrec[0]

    def test_property_costs_mapping(self, chart):
        rows = [CSVRow(account_code="89010", account_name="Hamilton Street 33 Costs", amounts={"Jan": 200})]
        mapped, _, _ = map_rows(rows, chart)
        assert len(mapped) == 1
        assert mapped[0].category_key == "property_maintenance"


# ===================================================================
# Full import pipeline tests
# ===================================================================

class TestImportCSV:
    def test_successful_import(self, chart):
        csv_text = dedent("""\
            Account,Jan-24,Feb-24
            10001 - Offering EFT,5000.00,6000.00
            41510 - Administrative Expenses,200.00,300.00
        """)
        result = import_csv(csv_text, chart)
        assert result.success is True
        assert result.total_rows == 2
        assert result.mapped_rows == 2
        assert len(result.errors) == 0

    def test_strict_rejects_unrecognised(self, chart):
        csv_text = dedent("""\
            Account,Jan-24
            10001 - Offering EFT,5000.00
            99999 - Mystery Account,100.00
        """)
        result = import_csv(csv_text, chart, strict=True)
        assert result.success is False
        assert len(result.errors) == 1
        assert "99999" in result.unrecognised_accounts[0]

    def test_lenient_allows_unrecognised(self, chart):
        csv_text = dedent("""\
            Account,Jan-24
            10001 - Offering EFT,5000.00
            99999 - Mystery Account,100.00
        """)
        result = import_csv(csv_text, chart, strict=False)
        assert result.success is True
        assert result.mapped_rows == 1
        assert len(result.warnings) == 1

    def test_filename_preserved(self, chart):
        csv_text = "Account,Jan-24\n10001 - Offering EFT,5000.00\n"
        result = import_csv(csv_text, chart, filename="test_2024.csv")
        assert result.filename == "test_2024.csv"


# ===================================================================
# Snapshot conversion tests
# ===================================================================

class TestToSnapshot:
    def test_produces_snapshot(self, chart):
        csv_text = dedent("""\
            Account,Jan-24,Feb-24
            10001 - Offering EFT,5000.00,6000.00
            41510 - Administrative Expenses,200.00,300.00
        """)
        result = import_csv(csv_text, chart)
        snapshot = to_snapshot(result, from_date="2024-01-01", to_date="2024-02-28")
        assert snapshot.source == "csv_import"
        assert snapshot.from_date == "2024-01-01"
        assert len(snapshot.rows) == 2
        # Offering EFT: 5000 + 6000 = 11000
        offering_row = next(r for r in snapshot.rows if r.account_code == "10001")
        assert offering_row.amount == 11000.00

    def test_zero_rows_excluded(self, chart):
        csv_text = dedent("""\
            Account,Jan-24
            10001 - Offering EFT,0.00
            41510 - Administrative Expenses,200.00
        """)
        result = import_csv(csv_text, chart)
        snapshot = to_snapshot(result, from_date="2024-01-01", to_date="2024-01-31")
        assert len(snapshot.rows) == 1


# ===================================================================
# Integration test with real chart_of_accounts.yaml
# ===================================================================

class TestRealChart:
    def test_all_accounts_mapped(self, real_chart):
        """Verify the real chart loads without error and has reasonable coverage."""
        lookup = build_account_lookup(real_chart)
        # Should have a substantial number of accounts
        assert len(lookup) > 50

    def test_realistic_csv(self, real_chart):
        """Import a CSV with accounts from the real chart."""
        csv_text = dedent("""\
            Account,Jan-26,Feb-26,Mar-26
            10001 - Offering EFT,"$22,916.67","$22,916.67","$22,916.67"
            10010 - Offertory Cash,0.00,0.00,0.00
            20060 - Goodhew Street 6 Rent,"$2,736.00","$2,736.00","$2,736.00"
            20010 - Hamilton Street 33 Rent,0.00,0.00,0.00
            40100 - Ministry Staff Salaries,"$6,607.75","$6,607.75","$6,607.75"
            41510 - Administrative Expenses,150.00,175.00,200.00
            44601 - Repairs & Maintenance,0.00,"$1,200.00",0.00
            89010 - Hamilton Street 33 Costs,0.00,0.00,450.00
            42501 - Mission Giving - Church Budget,708.33,708.33,708.33
            44901 - Church Land Acquisition Costs,458.33,458.33,458.33
        """)
        result = import_csv(csv_text, real_chart, strict=True)
        assert result.success is True, f"Import failed: {result.errors}"
        assert result.total_rows == 10
        assert result.mapped_rows == 10

    def test_legacy_accounts_in_csv(self, real_chart):
        """Legacy accounts from pre-2025 data should map correctly."""
        csv_text = dedent("""\
            Account,2024
            10005 - Offering Family 8AM,"$45,000.00"
            30055 - Playtime,"$1,200.00"
            12050 - Rectory Rent,"$18,000.00"
        """)
        result = import_csv(csv_text, real_chart, strict=True)
        assert result.success is True
        assert result.mapped_rows == 3
        # All should map to their parent categories
        categories = {r.category_key for r in result.rows}
        assert "offertory" in categories
        assert "ministry_income" in categories
        assert "property_income" in categories
        # All should be flagged as legacy
        assert all(r.is_legacy for r in result.rows)
