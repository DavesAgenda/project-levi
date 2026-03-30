"""Tests for the property portfolio service."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.models import FinancialSnapshot, SnapshotRow
from app.services.property_portfolio import (
    PortfolioSummary,
    PropertyPL,
    compute_3yr_average,
    compute_levy_shares,
    compute_property_portfolio,
    load_historical_costs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def properties_yaml(tmp_path: Path) -> Path:
    """Write a minimal properties.yaml and return the path."""
    content = dedent("""\
        properties:
          goodhew_6:
            address: "6 Example Street"
            tenant: "TenantA"
            weekly_rate: 720
            weeks_per_year: 48
            management_fee_pct: 0.055
            status: occupied
            income_account: "20060"
            cost_account: "89050"
            land_asset: "65010"
            building_asset: "66010"

          hamilton_33:
            address: "33 Example Avenue"
            tenant: "ExampleStaffB"
            weekly_rate: 0
            weeks_per_year: 48
            management_fee_pct: 0
            status: occupied_warden
            income_account: "20010"
            cost_account: "89010"
            land_asset: "65003"

          loane_33:
            address: "33 Example Road"
            tenant: "TenantB"
            weekly_rate: 675
            weeks_per_year: 48
            management_fee_pct: 0.055
            status: occupied
            income_account: "20040"
            cost_account: "89040"
            land_asset: "65007"
            building_asset: "66007"
    """)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "properties.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def sample_snapshot() -> FinancialSnapshot:
    """A minimal snapshot with property income and costs."""
    return FinancialSnapshot(
        report_date="2026-03-31",
        from_date="2026-01-01",
        to_date="2026-03-31",
        source="csv_import",
        rows=[
            SnapshotRow(account_code="20060", account_name="Goodhew St 6 Rent", amount=7800.0),
            SnapshotRow(account_code="20010", account_name="Hamilton St 33 Rent", amount=0.0),
            SnapshotRow(account_code="20040", account_name="Example Road 33 Rent", amount=6500.0),
            SnapshotRow(account_code="89050", account_name="Goodhew St 6 Costs", amount=320.0),
            SnapshotRow(account_code="89010", account_name="Hamilton St 33 Costs", amount=450.0),
            SnapshotRow(account_code="89040", account_name="Example Road 33 Costs", amount=180.0),
            SnapshotRow(account_code="44903", account_name="Property Receipts Levy", amount=1800.0),
        ],
    )


@pytest.fixture()
def historical_dir(tmp_path: Path) -> Path:
    """Create a historical CSV directory with sample data."""
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir()
    csv_content = dedent("""\
        Account,2023
        89050 - Example Street 6 Costs,"$3,600.00"
        89010 - Example Avenue 33 Costs,"$3,500.00"
        89040 - Example Road 33 Costs,"$2,200.00"
    """)
    (hist_dir / "sample_2023.csv").write_text(csv_content, encoding="utf-8")
    return hist_dir


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestComputeLevyShares:
    """Tests for levy share allocation."""

    def test_proportional_allocation(self):
        """Levy is split proportionally by income."""
        properties = {
            "a": {"income_account": "20060"},
            "b": {"income_account": "20010"},
        }
        actuals = {"20060": 8000.0, "20010": 2000.0}
        shares = compute_levy_shares(1000.0, properties, actuals)

        assert shares["a"] == 800.0
        assert shares["b"] == 200.0

    def test_zero_income_gets_zero_levy(self):
        """Warden property with zero income gets no levy."""
        properties = {
            "a": {"income_account": "20060"},
            "b": {"income_account": "20010"},
        }
        actuals = {"20060": 10000.0, "20010": 0.0}
        shares = compute_levy_shares(500.0, properties, actuals)

        assert shares["a"] == 500.0
        assert shares["b"] == 0.0

    def test_zero_levy(self):
        """Zero levy means zero for all."""
        properties = {"a": {"income_account": "20060"}}
        actuals = {"20060": 10000.0}
        shares = compute_levy_shares(0.0, properties, actuals)

        assert shares["a"] == 0.0


class TestCompute3yrAverage:
    """Tests for 3-year rolling average."""

    def test_three_years_data(self):
        """Average of current + 2 historical years."""
        result = compute_3yr_average(300.0, [400.0, 500.0])
        assert result == 400.0

    def test_current_only(self):
        """Only current year available."""
        result = compute_3yr_average(600.0, [])
        assert result == 600.0

    def test_all_zeros(self):
        """All zeros returns 0."""
        result = compute_3yr_average(0.0, [0.0, 0.0])
        assert result == 0.0

    def test_excess_historical_ignored(self):
        """Only the 2 most recent historical years are used."""
        result = compute_3yr_average(100.0, [200.0, 300.0, 999.0])
        assert result == 200.0  # (100 + 200 + 300) / 3


class TestLoadHistoricalCosts:
    """Tests for CSV historical cost loading."""

    def test_loads_matching_account(self, historical_dir: Path):
        costs = load_historical_costs("89050", historical_dir)
        assert len(costs) == 1
        assert costs[0] == 3600.0

    def test_no_match_returns_empty(self, historical_dir: Path):
        costs = load_historical_costs("99999", historical_dir)
        assert costs == []

    def test_no_directory_returns_empty(self, tmp_path: Path):
        costs = load_historical_costs("89050", tmp_path / "nonexistent")
        assert costs == []


class TestComputePropertyPortfolio:
    """Integration tests for the main portfolio computation."""

    def test_basic_computation(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Portfolio computes per-property P&L and summary."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )

        assert result.has_data is True
        assert len(result.properties) == 3

        # Check property ordering matches YAML order
        assert result.properties[0].address == "6 Example Street"
        assert result.properties[1].address == "33 Example Avenue"
        assert result.properties[2].address == "33 Example Road"

    def test_goodhew_income_and_costs(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Goodhew property has correct income and costs."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        goodhew = result.properties[0]

        assert goodhew.gross_rent == 7800.0
        assert goodhew.maintenance_costs == 320.0
        # Mgmt fee = 7800 * 0.055 = 429.0
        assert goodhew.management_fee == 429.0

    def test_hamilton_warden_occupied(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Hamilton (warden occupied) has costs but no income."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        hamilton = result.properties[1]

        assert hamilton.is_warden_occupied is True
        assert hamilton.gross_rent == 0.0
        assert hamilton.maintenance_costs == 450.0
        assert hamilton.management_fee == 0.0
        assert hamilton.levy_share == 0.0  # No income = no levy share

    def test_net_income_formula(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Net income = gross - mgmt_fee - costs - levy_share."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        goodhew = result.properties[0]

        expected_net = (
            goodhew.gross_rent
            - goodhew.management_fee
            - goodhew.maintenance_costs
            - goodhew.levy_share
        )
        assert goodhew.net_income == round(expected_net, 2)

    def test_budget_computation(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Budget is prorated based on snapshot period."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        goodhew = result.properties[0]

        # Annual budget gross = 720 * 48 = 34560
        # Snapshot covers 90 days (Jan 1 - Mar 31)
        # Prorated = 34560 * (90/365)
        annual_gross = 720 * 48
        prorate = 90 / 365.0
        expected_budget = round(annual_gross * prorate, 2)
        assert goodhew.budget_gross_rent == expected_budget

    def test_portfolio_totals(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Summary totals are correct."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )

        assert result.total_gross_rent == 7800.0 + 0.0 + 6500.0
        total_maint = sum(p.maintenance_costs for p in result.properties)
        assert result.total_maintenance_costs == total_maint

    def test_no_snapshot_returns_empty(self, properties_yaml):
        """Returns empty summary when no snapshot data."""
        result = compute_property_portfolio(
            config_path=properties_yaml,
            snapshots_dir=Path("/nonexistent"),
        )
        assert result.has_data is False
        assert result.properties == []

    def test_levy_shares_sum_to_total(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """All levy shares should approximately sum to total levy."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        total_shares = sum(p.levy_share for p in result.properties)
        # Total levy in snapshot is 1800
        assert abs(total_shares - 1800.0) < 0.10  # rounding tolerance

    def test_3yr_average_populated(
        self, sample_snapshot, properties_yaml, historical_dir,
    ):
        """Properties with historical data get a 3yr average."""
        result = compute_property_portfolio(
            snapshot=sample_snapshot,
            config_path=properties_yaml,
            historical_dir=historical_dir,
        )
        goodhew = result.properties[0]
        assert goodhew.avg_maintenance_3yr is not None
        assert goodhew.avg_maintenance_3yr > 0
