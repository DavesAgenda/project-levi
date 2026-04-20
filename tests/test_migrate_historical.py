"""Tests for the historical data migration and verification scripts."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

# Import via sys.path manipulation matching the scripts themselves
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrate_historical import (
    MigrationReport,
    detect_year,
    main as migrate_main,
    process_file,
    year_date_range,
)
from scripts.verify_migration import (
    load_snapshot,
    main as verify_main,
    verify_snapshot,
)
from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import FinancialSnapshot, SnapshotRow


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture()
def chart_path(tmp_path: Path) -> Path:
    """Write a minimal chart_of_accounts.yaml and return its path."""
    yaml_content = dedent("""\
        income:
          offertory:
            budget_label: "1 - Offertory"
            accounts:
              - { code: "10001", name: "Offering EFT" }
              - { code: "10010", name: "Offertory Cash" }
            legacy_accounts:
              - { code: "10005", name: "Offering Family 8AM" }
          property_income:
            budget_label: "2 - Housing Income"
            accounts:
              - { code: "20060", name: "Goodhew Street 6 Rent" }
        expenses:
          administration:
            budget_label: "Administration"
            accounts:
              - { code: "41510", name: "Administrative Expenses" }
          property_maintenance:
            budget_label: "Property & Maintenance"
            accounts:
              - { code: "44601", name: "Repairs & Maintenance" }
            property_costs:
              - { code: "89010", name: "Hamilton Street 33 Costs" }
    """)
    p = tmp_path / "chart_of_accounts.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    return p


@pytest.fixture()
def chart(chart_path: Path):
    return load_chart_of_accounts(chart_path)


@pytest.fixture()
def sample_csv_2023(tmp_path: Path) -> Path:
    """Write a sample CSV for 2023."""
    csv_text = dedent("""\
        Account,2023
        10001 - Offering EFT,"$245,000.00"
        10010 - Offertory Cash,"$12,500.00"
        20060 - Goodhew Street 6 Rent,"$32,832.00"
        41510 - Administrative Expenses,"$4,800.00"
        44601 - Repairs & Maintenance,"$15,000.00"
        89010 - Hamilton Street 33 Costs,"$3,500.00"
    """)
    csv_path = tmp_path / "historical" / "pl_2023.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_text, encoding="utf-8")
    return csv_path


@pytest.fixture()
def sample_csv_2022(tmp_path: Path) -> Path:
    """Write a sample CSV for 2022 with a legacy account."""
    csv_text = dedent("""\
        Account,2022
        10005 - Offering Family 8AM,"$180,000.00"
        20060 - Goodhew Street 6 Rent,"$30,000.00"
        41510 - Administrative Expenses,"$3,500.00"
    """)
    csv_path = tmp_path / "historical" / "pl_2022.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_text, encoding="utf-8")
    return csv_path


# ===================================================================
# Year detection tests
# ===================================================================

class TestDetectYear:
    def test_year_in_filename(self):
        assert detect_year("profit_and_loss_2023.csv") == 2023

    def test_year_prefix(self):
        assert detect_year("2021_annual.csv") == 2021

    def test_no_year(self):
        assert detect_year("monthly_report.csv") is None

    def test_year_range(self):
        assert detect_year("pl_2020.csv") == 2020
        assert detect_year("pl_2024.csv") == 2024

    def test_non_matching_year(self):
        # Years outside 20xx range won't match
        assert detect_year("data_1999.csv") is None


class TestYearDateRange:
    def test_basic(self):
        assert year_date_range(2023) == ("2023-01-01", "2023-12-31")

    def test_2020(self):
        assert year_date_range(2020) == ("2020-01-01", "2020-12-31")


# ===================================================================
# Process file tests
# ===================================================================

class TestProcessFile:
    def test_successful_process(self, sample_csv_2023, chart):
        snapshot, result, year = process_file(sample_csv_2023, chart)
        assert year == 2023
        assert result.success is True
        assert snapshot is not None
        assert snapshot.from_date == "2023-01-01"
        assert snapshot.to_date == "2023-12-31"
        assert snapshot.source == "csv_import"
        assert len(snapshot.rows) == 6

    def test_legacy_accounts_process(self, sample_csv_2022, chart):
        snapshot, result, year = process_file(sample_csv_2022, chart)
        assert year == 2022
        assert result.success is True
        assert snapshot is not None
        assert len(snapshot.rows) == 3

    def test_strict_mode_with_unknown(self, tmp_path, chart):
        csv_text = dedent("""\
            Account,2023
            10001 - Offering EFT,5000.00
            99999 - Unknown Account,100.00
        """)
        csv_path = tmp_path / "bad_2023.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        snapshot, result, year = process_file(csv_path, chart, strict=True)
        assert snapshot is None
        assert result.success is False

    def test_year_override(self, sample_csv_2023, chart):
        snapshot, result, year = process_file(
            sample_csv_2023, chart, year_override=2020
        )
        assert year == 2020
        assert snapshot.from_date == "2020-01-01"


# ===================================================================
# Migration report tests
# ===================================================================

class TestMigrationReport:
    def test_add_entry(self, sample_csv_2023, chart):
        _, result, year = process_file(sample_csv_2023, chart)
        report = MigrationReport()
        report.add(sample_csv_2023, result, year, saved=True)
        assert len(report.entries) == 1
        assert report.entries[0]["year"] == 2023
        assert report.entries[0]["saved"] is True
        assert report.entries[0]["income_total"] > 0
        assert report.entries[0]["expense_total"] > 0

    def test_to_dict(self, sample_csv_2023, chart):
        _, result, year = process_file(sample_csv_2023, chart)
        report = MigrationReport()
        report.add(sample_csv_2023, result, year, saved=True)
        d = report.to_dict()
        assert d["summary"]["total_files"] == 1
        assert d["summary"]["saved"] == 1
        assert d["summary"]["failed"] == 0


# ===================================================================
# Full migration CLI tests
# ===================================================================

class TestMigrateCLI:
    def test_full_migration(self, tmp_path, chart_path, sample_csv_2023):
        output_dir = tmp_path / "snapshots"
        result = migrate_main([
            "--input-dir", str(sample_csv_2023.parent),
            "--output-dir", str(output_dir),
            "--chart", str(chart_path),
        ])
        assert result == 0
        # Check output file exists
        out_file = output_dir / "annual_2023.json"
        assert out_file.exists()
        # Verify it's valid JSON
        snapshot = FinancialSnapshot(**json.loads(out_file.read_text()))
        assert snapshot.from_date == "2023-01-01"
        assert len(snapshot.rows) > 0

    def test_multiple_years(self, tmp_path, chart_path, sample_csv_2023, sample_csv_2022):
        output_dir = tmp_path / "snapshots"
        result = migrate_main([
            "--input-dir", str(sample_csv_2023.parent),
            "--output-dir", str(output_dir),
            "--chart", str(chart_path),
        ])
        assert result == 0
        assert (output_dir / "annual_2023.json").exists()
        assert (output_dir / "annual_2022.json").exists()

    def test_save_report(self, tmp_path, chart_path, sample_csv_2023):
        output_dir = tmp_path / "snapshots"
        report_path = tmp_path / "report.json"
        migrate_main([
            "--input-dir", str(sample_csv_2023.parent),
            "--output-dir", str(output_dir),
            "--chart", str(chart_path),
            "--save-report", str(report_path),
        ])
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert "entries" in report
        assert "summary" in report

    def test_no_csv_files(self, tmp_path, chart_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = migrate_main([
            "--input-dir", str(empty_dir),
            "--chart", str(chart_path),
        ])
        assert result == 1

    def test_missing_input_dir(self, tmp_path, chart_path):
        result = migrate_main([
            "--input-dir", str(tmp_path / "nonexistent"),
            "--chart", str(chart_path),
        ])
        assert result == 1


# ===================================================================
# Verification script tests
# ===================================================================

class TestLoadSnapshot:
    def test_load_valid_snapshot(self, tmp_path):
        snapshot = FinancialSnapshot(
            report_date="2026-03-30",
            from_date="2023-01-01",
            to_date="2023-12-31",
            source="csv_import",
            rows=[
                SnapshotRow(account_code="10001", account_name="Offering EFT", amount=245000.0),
            ],
        )
        path = tmp_path / "test.json"
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        loaded = load_snapshot(path)
        assert loaded.from_date == "2023-01-01"
        assert len(loaded.rows) == 1


class TestVerifySnapshot:
    def test_all_recognised(self, chart):
        lookup = build_account_lookup(chart)
        snapshot = FinancialSnapshot(
            report_date="2026-03-30",
            from_date="2023-01-01",
            to_date="2023-12-31",
            source="csv_import",
            rows=[
                SnapshotRow(account_code="10001", account_name="Offering EFT", amount=245000.0),
                SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=4800.0),
            ],
        )
        result = verify_snapshot(snapshot, lookup)
        assert result["income"] == 245000.0
        assert result["expenses"] == 4800.0
        assert result["net_position"] == 245000.0 - 4800.0
        assert len(result["unrecognised"]) == 0
        assert len(result["issues"]) == 0

    def test_unrecognised_flagged(self, chart):
        lookup = build_account_lookup(chart)
        snapshot = FinancialSnapshot(
            report_date="2026-03-30",
            from_date="2023-01-01",
            to_date="2023-12-31",
            source="csv_import",
            rows=[
                SnapshotRow(account_code="99999", account_name="Unknown", amount=100.0),
            ],
        )
        result = verify_snapshot(snapshot, lookup)
        assert len(result["unrecognised"]) == 1

    def test_empty_snapshot_flagged(self, chart):
        lookup = build_account_lookup(chart)
        snapshot = FinancialSnapshot(
            report_date="2026-03-30",
            from_date="2023-01-01",
            to_date="2023-12-31",
            source="csv_import",
            rows=[],
        )
        result = verify_snapshot(snapshot, lookup)
        assert any("no rows" in i.lower() for i in result["issues"])


class TestVerifyCLI:
    def test_verify_snapshots(self, tmp_path, chart_path):
        """Full integration: migrate then verify."""
        # Create a snapshot file
        snapshot = FinancialSnapshot(
            report_date="2026-03-30",
            from_date="2023-01-01",
            to_date="2023-12-31",
            source="csv_import",
            rows=[
                SnapshotRow(account_code="10001", account_name="Offering EFT", amount=245000.0),
                SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=4800.0),
            ],
        )
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "annual_2023.json").write_text(
            snapshot.model_dump_json(indent=2), encoding="utf-8"
        )

        result = verify_main([
            "--snapshots-dir", str(snap_dir),
            "--chart", str(chart_path),
        ])
        assert result == 0

    def test_verify_empty_dir(self, tmp_path, chart_path):
        empty_dir = tmp_path / "empty_snaps"
        empty_dir.mkdir()
        result = verify_main([
            "--snapshots-dir", str(empty_dir),
            "--chart", str(chart_path),
        ])
        assert result == 1
