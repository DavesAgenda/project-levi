"""Tests for historical data verification (CHA-206).

Tests the verification service and route with controlled fixtures
containing known matching and mismatching values.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

from app.csv_import import load_chart_of_accounts
from app.main import app
from app.models import ChartOfAccounts
from app.models.verification import (
    AccountComparison,
    MatchStatus,
    VerificationResult,
)
from app.services import verification as verification_service
from app.services.verification import (
    _classify_variance,
    _load_csv_actuals,
    _load_snapshot_actuals,
    get_available_years,
    verify_year,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINI_CHART_YAML = dedent("""\
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
          - { code: "41517", name: "Bank Fees" }
      property_maintenance:
        budget_label: "Property & Maintenance"
        accounts:
          - { code: "44601", name: "Repairs & Maintenance" }
        property_costs:
          - { code: "89010", name: "Hamilton Street 33 Costs" }
""")


@pytest.fixture()
def mini_chart(tmp_path: Path) -> ChartOfAccounts:
    """Provide a minimal chart of accounts for verification tests."""
    yaml_path = tmp_path / "chart_of_accounts.yaml"
    yaml_path.write_text(MINI_CHART_YAML, encoding="utf-8")
    return load_chart_of_accounts(yaml_path)


@pytest.fixture()
def historical_dir(tmp_path: Path) -> Path:
    """Create a temporary historical directory with a sample CSV."""
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    return hist_dir


@pytest.fixture()
def snapshots_dir(tmp_path: Path) -> Path:
    """Create a temporary snapshots directory."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    return snap_dir


@pytest.fixture()
def csv_2023(historical_dir: Path) -> Path:
    """Create a CSV file with 2023 data containing known values."""
    csv_content = dedent("""\
        Account,2023
        10001 - Offering EFT,"$245,000.00"
        10010 - Offertory Cash,"$12,500.00"
        20060 - Goodhew Street 6 Rent,"$32,832.00"
        41510 - Administrative Expenses,"$4,800.00"
        41517 - Bank Fees,"$1,200.00"
        44601 - Repairs & Maintenance,"$15,000.00"
        89010 - Hamilton Street 33 Costs,"$3,500.00"
    """)
    csv_path = historical_dir / "sample_2023.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return csv_path


@pytest.fixture()
def snapshot_2023_matching(snapshots_dir: Path) -> Path:
    """Create a snapshot JSON that exactly matches the CSV values."""
    snapshot = {
        "report_date": "2023-12-31",
        "from_date": "2023-01-01",
        "to_date": "2023-12-31",
        "source": "xero_api",
        "rows": [
            {"account_code": "10001", "account_name": "Offering EFT", "amount": 245000.00},
            {"account_code": "10010", "account_name": "Offertory Cash", "amount": 12500.00},
            {"account_code": "20060", "account_name": "Goodhew Street 6 Rent", "amount": 32832.00},
            {"account_code": "41510", "account_name": "Administrative Expenses", "amount": 4800.00},
            {"account_code": "41517", "account_name": "Bank Fees", "amount": 1200.00},
            {"account_code": "44601", "account_name": "Repairs & Maintenance", "amount": 15000.00},
            {"account_code": "89010", "account_name": "Hamilton Street 33 Costs", "amount": 3500.00},
        ],
    }
    path = snapshots_dir / "pl_2023-01-01_2023-12-31.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


@pytest.fixture()
def snapshot_2023_mismatching(snapshots_dir: Path) -> Path:
    """Create a snapshot JSON with deliberate discrepancies against the CSV.

    Discrepancies:
    - 10001: exact match ($245,000)
    - 10010: minor variance ($12,500 vs $12,550 = $50 diff)
    - 20060: major variance ($32,832 vs $33,000 = $168 diff)
    - 41510: within match threshold ($4,800 vs $4,805 = $5 diff)
    - 41517: missing from snapshot (CSV only)
    - 44601: exact match ($15,000)
    - 89010: exact match ($3,500)
    - 10005: snapshot only (not in CSV)
    """
    snapshot = {
        "report_date": "2023-12-31",
        "from_date": "2023-01-01",
        "to_date": "2023-12-31",
        "source": "xero_api",
        "rows": [
            {"account_code": "10001", "account_name": "Offering EFT", "amount": 245000.00},
            {"account_code": "10010", "account_name": "Offertory Cash", "amount": 12550.00},
            {"account_code": "20060", "account_name": "Goodhew Street 6 Rent", "amount": 33000.00},
            {"account_code": "41510", "account_name": "Administrative Expenses", "amount": 4805.00},
            # 41517 Bank Fees intentionally missing
            {"account_code": "44601", "account_name": "Repairs & Maintenance", "amount": 15000.00},
            {"account_code": "89010", "account_name": "Hamilton Street 33 Costs", "amount": 3500.00},
            # Extra account not in CSV
            {"account_code": "10005", "account_name": "Offering Family 8AM", "amount": 800.00},
        ],
    }
    path = snapshots_dir / "pl_2023-01-01_2023-12-31.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


@pytest.fixture()
def snapshot_2023_wrapped(snapshots_dir: Path) -> Path:
    """Create a snapshot in the wrapper format (from save_snapshot())."""
    snapshot = {
        "snapshot_metadata": {
            "saved_at": "2023-12-31T00:00:00Z",
            "report_type": "pl",
            "from_date": "2023-01-01",
            "to_date": "2023-12-31",
        },
        "response": {
            "report_date": "2023-12-31",
            "from_date": "2023-01-01",
            "to_date": "2023-12-31",
            "source": "xero_api",
            "rows": [
                {"account_code": "10001", "account_name": "Offering EFT", "amount": 245000.00},
                {"account_code": "10010", "account_name": "Offertory Cash", "amount": 12500.00},
            ],
        },
    }
    path = snapshots_dir / "pl_2023-01-01_2023-12-31.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestMatchStatus:
    """Test the MatchStatus enum and AccountComparison model."""

    def test_match_status_values(self):
        assert MatchStatus.MATCH == "match"
        assert MatchStatus.MINOR_VARIANCE == "minor"
        assert MatchStatus.MAJOR_VARIANCE == "major"
        assert MatchStatus.CSV_ONLY == "csv_only"
        assert MatchStatus.SNAPSHOT_ONLY == "snapshot_only"

    def test_account_comparison_css_class(self):
        comp = AccountComparison(
            account_code="10001",
            account_name="Test",
            csv_amount=100.0,
            snapshot_amount=100.0,
            status=MatchStatus.MATCH,
        )
        assert comp.css_class == "bg-green-50"

    def test_account_comparison_css_class_minor(self):
        comp = AccountComparison(
            account_code="10001",
            account_name="Test",
            status=MatchStatus.MINOR_VARIANCE,
        )
        assert comp.css_class == "bg-yellow-50"

    def test_account_comparison_css_class_major(self):
        comp = AccountComparison(
            account_code="10001",
            account_name="Test",
            status=MatchStatus.MAJOR_VARIANCE,
        )
        assert comp.css_class == "bg-red-50"

    def test_status_labels(self):
        for status, label in [
            (MatchStatus.MATCH, "Match"),
            (MatchStatus.MINOR_VARIANCE, "Minor Variance"),
            (MatchStatus.MAJOR_VARIANCE, "Major Variance"),
            (MatchStatus.CSV_ONLY, "CSV Only"),
            (MatchStatus.SNAPSHOT_ONLY, "Snapshot Only"),
        ]:
            comp = AccountComparison(
                account_code="X", account_name="X", status=status,
            )
            assert comp.status_label == label


class TestVerificationResult:
    """Test VerificationResult computed properties."""

    def test_empty_result(self):
        result = VerificationResult(year=2023)
        assert result.total_accounts == 0
        assert result.match_count == 0
        assert result.match_percentage == 0.0
        assert result.total_discrepancy == 0.0

    def test_result_with_comparisons(self):
        result = VerificationResult(
            year=2023,
            comparisons=[
                AccountComparison(
                    account_code="10001", account_name="A",
                    csv_amount=100, snapshot_amount=100,
                    variance=0, abs_variance=0, status=MatchStatus.MATCH,
                ),
                AccountComparison(
                    account_code="10002", account_name="B",
                    csv_amount=100, snapshot_amount=150,
                    variance=-50, abs_variance=50, status=MatchStatus.MINOR_VARIANCE,
                ),
                AccountComparison(
                    account_code="10003", account_name="C",
                    csv_amount=100, snapshot_amount=300,
                    variance=-200, abs_variance=200, status=MatchStatus.MAJOR_VARIANCE,
                ),
            ],
        )
        assert result.total_accounts == 3
        assert result.match_count == 1
        assert result.match_percentage == 33.3
        assert result.total_discrepancy == 250.0
        assert len(result.matches) == 1
        assert len(result.minor_variances) == 1
        assert len(result.major_variances) == 1

    def test_filtered_lists(self):
        result = VerificationResult(
            year=2023,
            comparisons=[
                AccountComparison(
                    account_code="A", account_name="A",
                    csv_amount=100, status=MatchStatus.CSV_ONLY,
                    abs_variance=100,
                ),
                AccountComparison(
                    account_code="B", account_name="B",
                    snapshot_amount=200, status=MatchStatus.SNAPSHOT_ONLY,
                    abs_variance=200,
                ),
            ],
        )
        assert len(result.csv_only) == 1
        assert len(result.snapshot_only) == 1
        assert result.csv_only[0].account_code == "A"
        assert result.snapshot_only[0].account_code == "B"


# ---------------------------------------------------------------------------
# Service: classify_variance
# ---------------------------------------------------------------------------

class TestClassifyVariance:
    """Test the _classify_variance helper."""

    def test_exact_match(self):
        assert _classify_variance(0.0) == MatchStatus.MATCH

    def test_within_match_threshold(self):
        assert _classify_variance(5.0) == MatchStatus.MATCH
        assert _classify_variance(10.0) == MatchStatus.MATCH

    def test_minor_variance(self):
        assert _classify_variance(10.01) == MatchStatus.MINOR_VARIANCE
        assert _classify_variance(50.0) == MatchStatus.MINOR_VARIANCE
        assert _classify_variance(100.0) == MatchStatus.MINOR_VARIANCE

    def test_major_variance(self):
        assert _classify_variance(100.01) == MatchStatus.MAJOR_VARIANCE
        assert _classify_variance(500.0) == MatchStatus.MAJOR_VARIANCE
        assert _classify_variance(10000.0) == MatchStatus.MAJOR_VARIANCE


# ---------------------------------------------------------------------------
# Service: CSV loading
# ---------------------------------------------------------------------------

class TestLoadCSVActuals:
    """Test the _load_csv_actuals helper."""

    def test_loads_csv_by_year_in_filename(
        self, mini_chart, csv_2023, historical_dir,
    ):
        actuals, source = _load_csv_actuals(2023, mini_chart, historical_dir)
        assert source == "sample_2023.csv"
        assert actuals["10001"] == 245000.00
        assert actuals["10010"] == 12500.00
        assert actuals["41510"] == 4800.00

    def test_empty_for_missing_year(self, mini_chart, historical_dir):
        actuals, source = _load_csv_actuals(2099, mini_chart, historical_dir)
        assert actuals == {}
        assert source == ""

    def test_empty_for_missing_dir(self, mini_chart, tmp_path):
        actuals, source = _load_csv_actuals(
            2023, mini_chart, tmp_path / "nonexistent",
        )
        assert actuals == {}

    def test_skips_zero_amounts(self, mini_chart, historical_dir):
        """Accounts with $0.00 should not appear in results."""
        csv_content = dedent("""\
            Account,2023
            10001 - Offering EFT,"$1,000.00"
            10010 - Offertory Cash,"$0.00"
        """)
        (historical_dir / "test_2023.csv").write_text(csv_content, encoding="utf-8")
        actuals, _ = _load_csv_actuals(2023, mini_chart, historical_dir)
        assert "10001" in actuals
        assert "10010" not in actuals


# ---------------------------------------------------------------------------
# Service: Snapshot loading
# ---------------------------------------------------------------------------

class TestLoadSnapshotActuals:
    """Test the _load_snapshot_actuals helper."""

    def test_loads_direct_format(self, snapshot_2023_matching, snapshots_dir):
        actuals, source = _load_snapshot_actuals(2023, snapshots_dir)
        assert actuals["10001"] == 245000.00
        assert actuals["10010"] == 12500.00
        assert "pl_2023-01-01_2023-12-31.json" in source

    def test_loads_wrapped_format(self, snapshot_2023_wrapped, snapshots_dir):
        actuals, source = _load_snapshot_actuals(2023, snapshots_dir)
        assert actuals["10001"] == 245000.00
        assert actuals["10010"] == 12500.00

    def test_empty_for_missing_year(self, snapshots_dir):
        actuals, source = _load_snapshot_actuals(2099, snapshots_dir)
        assert actuals == {}
        assert source == ""

    def test_empty_for_missing_dir(self, tmp_path):
        actuals, source = _load_snapshot_actuals(
            2023, tmp_path / "nonexistent",
        )
        assert actuals == {}


# ---------------------------------------------------------------------------
# Service: verify_year — perfect match
# ---------------------------------------------------------------------------

class TestVerifyYearPerfectMatch:
    """Test verify_year when CSV and snapshot data match exactly."""

    def test_all_accounts_match(
        self, mini_chart, csv_2023, snapshot_2023_matching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )

        assert result.year == 2023
        assert result.has_csv_data is True
        assert result.has_snapshot_data is True
        assert result.total_accounts == 7
        assert result.match_count == 7
        assert result.match_percentage == 100.0
        assert result.total_discrepancy == 0.0
        assert len(result.minor_variances) == 0
        assert len(result.major_variances) == 0

    def test_all_statuses_are_match(
        self, mini_chart, csv_2023, snapshot_2023_matching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )

        for comp in result.comparisons:
            assert comp.status == MatchStatus.MATCH, (
                f"Account {comp.account_code} expected MATCH, got {comp.status}"
            )


# ---------------------------------------------------------------------------
# Service: verify_year — known discrepancies
# ---------------------------------------------------------------------------

class TestVerifyYearDiscrepancies:
    """Test verify_year with known discrepancies in test fixtures."""

    def test_discrepancy_detection(
        self, mini_chart, csv_2023, snapshot_2023_mismatching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )

        assert result.year == 2023
        assert result.has_csv_data is True
        assert result.has_snapshot_data is True

        # Build lookup by account code for easier assertions
        by_code = {c.account_code: c for c in result.comparisons}

        # 10001: exact match
        assert by_code["10001"].status == MatchStatus.MATCH
        assert by_code["10001"].variance == 0.0

        # 10010: minor variance ($12,500 csv vs $12,550 snapshot = -$50)
        assert by_code["10010"].status == MatchStatus.MINOR_VARIANCE
        assert by_code["10010"].variance == -50.0
        assert by_code["10010"].abs_variance == 50.0

        # 20060: major variance ($32,832 csv vs $33,000 snapshot = -$168)
        assert by_code["20060"].status == MatchStatus.MAJOR_VARIANCE
        assert by_code["20060"].variance == -168.0
        assert by_code["20060"].abs_variance == 168.0

        # 41510: within match threshold ($4,800 vs $4,805 = $5)
        assert by_code["41510"].status == MatchStatus.MATCH
        assert by_code["41510"].abs_variance == 5.0

        # 41517: CSV only (missing from snapshot)
        assert by_code["41517"].status == MatchStatus.CSV_ONLY
        assert by_code["41517"].csv_amount == 1200.00
        assert by_code["41517"].snapshot_amount is None

        # 44601: exact match
        assert by_code["44601"].status == MatchStatus.MATCH

        # 89010: exact match
        assert by_code["89010"].status == MatchStatus.MATCH

        # 10005: snapshot only
        assert by_code["10005"].status == MatchStatus.SNAPSHOT_ONLY
        assert by_code["10005"].snapshot_amount == 800.00
        assert by_code["10005"].csv_amount is None

    def test_summary_statistics(
        self, mini_chart, csv_2023, snapshot_2023_mismatching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )

        # 10001 match, 10010 minor, 20060 major, 41510 match, 41517 csv_only,
        # 44601 match, 89010 match, 10005 snapshot_only = 8 total, 4 matches
        assert result.total_accounts == 8
        assert result.match_count == 4
        assert result.match_percentage == 50.0
        assert len(result.minor_variances) == 1
        assert len(result.major_variances) == 1
        assert len(result.csv_only) == 1
        assert len(result.snapshot_only) == 1


# ---------------------------------------------------------------------------
# Service: verify_year — edge cases
# ---------------------------------------------------------------------------

class TestVerifyYearEdgeCases:
    """Test verify_year with edge cases and missing data."""

    def test_no_data_at_all(self, mini_chart, historical_dir, snapshots_dir):
        result = verify_year(
            2099,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert result.year == 2099
        assert result.has_csv_data is False
        assert result.has_snapshot_data is False
        assert result.total_accounts == 0

    def test_csv_only_no_snapshot(
        self, mini_chart, csv_2023, historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert result.has_csv_data is True
        assert result.has_snapshot_data is False
        # All accounts should be CSV-only
        assert all(c.status == MatchStatus.CSV_ONLY for c in result.comparisons)

    def test_snapshot_only_no_csv(
        self, mini_chart, snapshot_2023_matching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert result.has_csv_data is False
        assert result.has_snapshot_data is True
        # All accounts should be snapshot-only
        assert all(c.status == MatchStatus.SNAPSHOT_ONLY for c in result.comparisons)

    def test_accounts_sorted_by_code(
        self, mini_chart, csv_2023, snapshot_2023_matching,
        historical_dir, snapshots_dir,
    ):
        result = verify_year(
            2023,
            chart=mini_chart,
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        codes = [c.account_code for c in result.comparisons]
        assert codes == sorted(codes)


# ---------------------------------------------------------------------------
# Service: get_available_years
# ---------------------------------------------------------------------------

class TestGetAvailableYears:
    """Test the get_available_years helper."""

    def test_finds_years_from_csv(self, csv_2023, historical_dir, snapshots_dir):
        years = get_available_years(
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert 2023 in years

    def test_finds_years_from_snapshots(
        self, snapshot_2023_matching, historical_dir, snapshots_dir,
    ):
        years = get_available_years(
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert 2023 in years

    def test_empty_dirs(self, historical_dir, snapshots_dir):
        years = get_available_years(
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert years == []

    def test_years_sorted(self, historical_dir, snapshots_dir):
        # Create CSVs for multiple years
        for yr in [2025, 2023, 2024]:
            csv = f"Account,{yr}\n10001 - Test,$100.00\n"
            (historical_dir / f"data_{yr}.csv").write_text(csv, encoding="utf-8")

        years = get_available_years(
            historical_dir=historical_dir,
            snapshots_dir=snapshots_dir,
        )
        assert years == [2023, 2024, 2025]


# ---------------------------------------------------------------------------
# Route: GET /reports/verification
# ---------------------------------------------------------------------------

class TestVerificationRoute:
    """Test the verification report route."""

    def test_page_loads_for_admin(self):
        """Admin users should see the verification page."""
        resp = client.get("/reports/verification")
        assert resp.status_code == 200
        assert "Data Verification" in resp.text

    def test_page_loads_with_year_param(self):
        """Year parameter should be accepted."""
        resp = client.get("/reports/verification?year=2023")
        assert resp.status_code == 200
        assert "Data Verification" in resp.text

    def test_contains_year_selector(self):
        """Page should contain a year selector dropdown."""
        resp = client.get("/reports/verification")
        assert resp.status_code == 200
        assert 'name="year"' in resp.text


class TestVerificationRouteAccessControl:
    """Test that staff users are blocked from the verification page."""

    def test_staff_blocked(self):
        """Staff users should get a 403."""
        import app.middleware.auth as auth_mod
        from app.models.auth import User

        original = auth_mod.override_user
        try:
            auth_mod.override_user = User(
                email="staff@test.org",
                name="Staff User",
                role="staff",
                permissions=["read"],
            )
            resp = client.get("/reports/verification")
            assert resp.status_code == 403
        finally:
            auth_mod.override_user = original

    def test_board_allowed(self):
        """Board users should see the page."""
        import app.middleware.auth as auth_mod
        from app.models.auth import User

        original = auth_mod.override_user
        try:
            auth_mod.override_user = User(
                email="board@test.org",
                name="Board Member",
                role="board",
                permissions=["read", "payroll_detail"],
            )
            resp = client.get("/reports/verification")
            assert resp.status_code == 200
            assert "Data Verification" in resp.text
        finally:
            auth_mod.override_user = original

    def test_admin_allowed(self):
        """Admin users should see the page."""
        # Default test user is admin, so this should work
        resp = client.get("/reports/verification")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration: route with real fixture data
# ---------------------------------------------------------------------------

class TestVerificationRouteWithData:
    """Test the route renders correctly with actual data in place."""

    def test_renders_comparison_table(
        self, mini_chart, csv_2023, snapshot_2023_mismatching,
        historical_dir, snapshots_dir,
    ):
        """Patch service dirs and verify the HTML contains expected elements."""
        original_hist = verification_service.HISTORICAL_DIR
        original_snap = verification_service.SNAPSHOTS_DIR
        original_chart = verification_service.CHART_PATH

        chart_path = historical_dir.parent / "chart_of_accounts.yaml"
        chart_path.write_text(MINI_CHART_YAML, encoding="utf-8")

        try:
            verification_service.HISTORICAL_DIR = historical_dir
            verification_service.SNAPSHOTS_DIR = snapshots_dir
            verification_service.CHART_PATH = chart_path

            resp = client.get("/reports/verification?year=2023")
            assert resp.status_code == 200

            html = resp.text
            # Should contain account codes
            assert "10001" in html
            assert "10010" in html
            # Should contain status badges
            assert "Match" in html
            # Should contain the summary cards
            assert "Total Accounts" in html
            assert "Match Rate" in html
            assert "Total Discrepancy" in html
        finally:
            verification_service.HISTORICAL_DIR = original_hist
            verification_service.SNAPSHOTS_DIR = original_snap
            verification_service.CHART_PATH = original_chart
