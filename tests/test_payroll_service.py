"""Tests for the payroll data service layer."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.models import FinancialSnapshot, SnapshotRow
from app.services.payroll import (
    DioceseScales,
    PayrollCategoryActuals,
    PayrollData,
    StaffCost,
    compute_payroll_data,
    extract_payroll_actuals,
    extract_total_income,
    load_payroll_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def payroll_yaml(tmp_path: Path) -> Path:
    """Write a minimal payroll.yaml and return its path."""
    content = dedent("""\
        diocese_scales:
          source: "Sydney Anglican Diocese Stipend & Salary Standards"
          year: 2026
          uplift_factor: 0.012
          notes: "Zero Jan-Jun, 2.4% Jul-Dec"

        staff:
          - name: "Smith A"
            role: "Permanent"
            fte: 0.8
            base_salary: 70000
            super_rate: 0.115
            workers_comp: 1200
            recoveries: []

          - name: "Jones B"
            role: "Rector"
            grade: "Accredited"
            fte: 1.0
            base_salary: 80000
            pcr: 20000
            fixed_travel: 9000
            recoveries:
              - name: "RCEA"
                amount: -15000
    """)
    path = tmp_path / "payroll.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def payroll_snapshot() -> FinancialSnapshot:
    """A snapshot with payroll and income rows."""
    return FinancialSnapshot(
        report_date="2026-03-31",
        from_date="2026-01-01",
        to_date="2026-03-31",
        source="csv_import",
        rows=[
            # Income
            SnapshotRow(account_code="10001", account_name="Offering EFT", amount=62500.0),
            SnapshotRow(account_code="20060", account_name="Goodhew Street 6 Rent", amount=7800.0),
            # Ministry staff payroll (40100-40199)
            SnapshotRow(account_code="40100", account_name="Ministry Staff Salaries", amount=38500.0),
            SnapshotRow(account_code="40105", account_name="Ministry Staff PCR", amount=4200.0),
            SnapshotRow(account_code="40110", account_name="Ministry Staff Allowances", amount=1800.0),
            # Ministry support (40200-40299)
            SnapshotRow(account_code="40200", account_name="Ministry Support Salaries", amount=12500.0),
            SnapshotRow(account_code="40205", account_name="Ministry Support Super", amount=1500.0),
            # Admin staff (40300-40399)
            SnapshotRow(account_code="40300", account_name="Administration Staff Salaries", amount=8200.0),
            SnapshotRow(account_code="40305", account_name="Administration Super", amount=984.0),
            # Non-payroll expense (should not be counted)
            SnapshotRow(account_code="41510", account_name="Administrative Expenses", amount=680.0),
        ],
    )


# ---------------------------------------------------------------------------
# load_payroll_config tests
# ---------------------------------------------------------------------------

class TestLoadPayrollConfig:
    def test_loads_staff(self, payroll_yaml):
        staff, diocese = load_payroll_config(payroll_yaml)
        assert len(staff) == 2
        assert staff[0].name == "Smith A"
        assert staff[1].name == "Jones B"

    def test_staff_cost_computation(self, payroll_yaml):
        staff, _ = load_payroll_config(payroll_yaml)

        # Smith A: base 70000, super 70000*0.115=8050, allowances=1200 (workers_comp)
        smith = staff[0]
        assert smith.base_salary == 70000
        assert smith.super_amount == 8050.0
        assert smith.allowances == 1200.0
        assert smith.recoveries == 0.0
        assert smith.total_cost == 70000 + 8050 + 1200

    def test_clergy_cost_with_pcr_and_recoveries(self, payroll_yaml):
        staff, _ = load_payroll_config(payroll_yaml)

        # Jones B: base 80000, super=0, pcr=20000, fixed_travel=9000
        jones = staff[1]
        assert jones.base_salary == 80000
        assert jones.super_amount == 0.0
        assert jones.pcr == 20000.0
        assert jones.fixed_travel == 9000.0
        assert jones.allowances == 9000.0  # fixed_travel + workers_comp (excludes PCR)
        assert jones.recoveries == -15000.0
        assert jones.total_cost == 80000 + 0 + 20000 + 9000  # base + super + pcr + allowances
        assert jones.net_cost == 80000 + 20000 + 9000 - 15000
        assert jones.diocese_grade == "Accredited"

    def test_diocese_scales(self, payroll_yaml):
        _, diocese = load_payroll_config(payroll_yaml)
        assert diocese.source == "Sydney Anglican Diocese Stipend & Salary Standards"
        assert diocese.year == 2026
        assert diocese.uplift_factor == 0.012

    def test_missing_config_returns_empty(self, tmp_path):
        staff, diocese = load_payroll_config(tmp_path / "nonexistent.yaml")
        assert staff == []
        assert diocese.source == ""


# ---------------------------------------------------------------------------
# extract_payroll_actuals tests
# ---------------------------------------------------------------------------

class TestExtractPayrollActuals:
    def test_categorises_40xxx_accounts(self, payroll_snapshot):
        actuals = extract_payroll_actuals(payroll_snapshot)
        assert actuals["ministry_staff"] == 38500.0 + 4200.0 + 1800.0
        assert actuals["ministry_support"] == 12500.0 + 1500.0
        assert actuals["admin_staff"] == 8200.0 + 984.0

    def test_excludes_non_payroll(self, payroll_snapshot):
        actuals = extract_payroll_actuals(payroll_snapshot)
        # 41510 is admin expense, not payroll
        total = sum(actuals.values())
        assert 680.0 not in actuals.values()
        assert total == 38500 + 4200 + 1800 + 12500 + 1500 + 8200 + 984


class TestExtractTotalIncome:
    def test_sums_income_accounts(self, payroll_snapshot):
        total = extract_total_income(payroll_snapshot)
        assert total == 62500.0 + 7800.0

    def test_excludes_expense_accounts(self, payroll_snapshot):
        total = extract_total_income(payroll_snapshot)
        # Should equal only income accounts (1x/2x/3x), not 4xxxx expenses
        assert total == 62500.0 + 7800.0


# ---------------------------------------------------------------------------
# compute_payroll_data tests
# ---------------------------------------------------------------------------

class TestComputePayrollData:
    def test_no_snapshot_still_returns_staff(self, payroll_yaml, tmp_path):
        data = compute_payroll_data(
            snapshot=None,
            config_path=payroll_yaml,
            snapshots_dir=tmp_path,
            budget={},
        )
        assert data.has_data is True
        assert len(data.staff) == 2
        assert data.total_payroll_cost > 0
        assert data.category_actuals == []

    def test_with_snapshot_and_budget(self, payroll_yaml, payroll_snapshot):
        budget = {
            "ministry_staff": 180000.0,
            "ministry_support": 60000.0,
            "admin_staff": 40000.0,
        }
        data = compute_payroll_data(
            snapshot=payroll_snapshot,
            config_path=payroll_yaml,
            budget=budget,
        )
        assert data.has_data is True
        assert len(data.category_actuals) == 3
        assert data.total_income == 62500.0 + 7800.0
        assert data.payroll_pct_of_income is not None

    def test_variance_calculation(self, payroll_yaml, payroll_snapshot):
        budget = {"ministry_staff": 180000.0}
        data = compute_payroll_data(
            snapshot=payroll_snapshot,
            config_path=payroll_yaml,
            budget=budget,
        )
        ministry = next(
            (c for c in data.category_actuals if c.category_key == "ministry_staff"),
            None,
        )
        assert ministry is not None
        assert ministry.actual == 38500.0 + 4200.0 + 1800.0
        assert ministry.budget == 180000.0
        assert ministry.variance_dollar == ministry.actual - 180000.0
        assert ministry.variance_pct is not None

    def test_snapshot_metadata(self, payroll_yaml, payroll_snapshot):
        data = compute_payroll_data(
            snapshot=payroll_snapshot,
            config_path=payroll_yaml,
            budget={},
        )
        assert data.snapshot_date == "2026-03-31"
        assert data.snapshot_period == "2026-01-01 to 2026-03-31"


# ---------------------------------------------------------------------------
# PayrollCategoryActuals status tests
# ---------------------------------------------------------------------------

class TestPayrollCategoryActualsStatus:
    def test_over_budget(self):
        cat = PayrollCategoryActuals(
            category_key="admin",
            label="Admin",
            actual=50000,
            budget=40000,
            variance_dollar=10000,
            variance_pct=25.0,
        )
        assert cat.status == "danger"

    def test_under_budget(self):
        cat = PayrollCategoryActuals(
            category_key="admin",
            label="Admin",
            actual=30000,
            budget=40000,
            variance_dollar=-10000,
            variance_pct=-25.0,
        )
        assert cat.status == "success"

    def test_near_budget(self):
        cat = PayrollCategoryActuals(
            category_key="admin",
            label="Admin",
            actual=37000,
            budget=40000,
            variance_dollar=-3000,
            variance_pct=-7.5,
        )
        assert cat.status == "warning"

    def test_zero_budget(self):
        cat = PayrollCategoryActuals(
            category_key="admin",
            label="Admin",
            actual=5000,
            budget=0,
            variance_dollar=5000,
            variance_pct=None,
        )
        assert cat.status == "success"


# ---------------------------------------------------------------------------
# StaffCost property tests
# ---------------------------------------------------------------------------

class TestStaffCost:
    def test_net_cost(self):
        s = StaffCost(
            name="Test",
            role="Role",
            fte=1.0,
            base_salary=80000,
            super_amount=0,
            pcr=10000,
            fixed_travel=5000,
            workers_comp=5000,
            allowances=10000,
            recoveries=-15000,
            total_cost=100000,
        )
        assert s.net_cost == 85000

    def test_net_cost_no_recoveries(self):
        s = StaffCost(
            name="Test",
            role="Role",
            fte=1.0,
            base_salary=80000,
            super_amount=9200,
            pcr=0,
            fixed_travel=0,
            workers_comp=0,
            allowances=0,
            recoveries=0,
            total_cost=89200,
        )
        assert s.net_cost == 89200
