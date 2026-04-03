"""Tests for budget forecast service and enhanced budget routes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.services.budget_forecast import (
    _months_elapsed,
    compute_forecast,
    list_budget_years,
)


# ---------------------------------------------------------------------------
# _months_elapsed tests
# ---------------------------------------------------------------------------

class TestMonthsElapsed:
    def test_past_year_returns_12(self):
        assert _months_elapsed(2024, reference_date=date(2026, 3, 30)) == 12

    def test_current_year_returns_month(self):
        assert _months_elapsed(2026, reference_date=date(2026, 3, 30)) == 3

    def test_current_year_january(self):
        assert _months_elapsed(2026, reference_date=date(2026, 1, 15)) == 1

    def test_current_year_december(self):
        assert _months_elapsed(2026, reference_date=date(2026, 12, 31)) == 12

    def test_future_year_returns_0(self):
        assert _months_elapsed(2027, reference_date=date(2026, 3, 30)) == 0


# ---------------------------------------------------------------------------
# compute_forecast tests
# ---------------------------------------------------------------------------

SAMPLE_SNAPSHOT = {
    "report_date": "2026-03-31",
    "from_date": "2026-01-01",
    "to_date": "2026-03-31",
    "source": "csv_import",
    "rows": [
        {"account_code": "10001", "account_name": "Offering EFT", "amount": 62500.00},
        {"account_code": "41520", "account_name": "Software", "amount": 520.00},
    ],
}

SAMPLE_CHART = {
    "income": {
        "offertory": {
            "budget_label": "Offertory",
            "accounts": [
                {"code": "10001", "name": "Offering EFT"},
            ],
        },
    },
    "expenses": {
        "administration": {
            "budget_label": "Administration",
            "accounts": [
                {"code": "41520", "name": "Software Licencing"},
            ],
        },
    },
}


@pytest.fixture
def forecast_env(tmp_path: Path):
    """Set up snapshot and chart files for forecast tests."""
    # Snapshots
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "pl_sample_2026.json").write_text(
        json.dumps(SAMPLE_SNAPSHOT), encoding="utf-8"
    )

    # Chart of accounts
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    chart_path = config_dir / "chart_of_accounts.yaml"
    chart_path.write_text(yaml.dump(SAMPLE_CHART), encoding="utf-8")

    return snap_dir, chart_path


class TestComputeForecast:
    def test_annualizes_partial_year(self, forecast_env):
        snap_dir, chart_path = forecast_env
        from app.csv_import import load_chart_of_accounts

        chart = load_chart_of_accounts(chart_path)
        result = compute_forecast(
            2026,
            reference_date=date(2026, 3, 30),
            chart=chart,
            snapshots_dir=snap_dir,
        )
        # 62500 / 3 * 12 = 250000
        assert result["offertory"] == 250000.0
        # 520 / 3 * 12 = 2080
        assert result["administration"] == 2080.0

    def test_full_year_returns_actuals(self, forecast_env):
        snap_dir, chart_path = forecast_env
        from app.csv_import import load_chart_of_accounts

        chart = load_chart_of_accounts(chart_path)
        # Past year = 12 months elapsed
        result = compute_forecast(
            2026,
            reference_date=date(2027, 1, 15),
            chart=chart,
            snapshots_dir=snap_dir,
        )
        # Full year, no annualization
        assert result["offertory"] == 62500.0
        assert result["administration"] == 520.0

    def test_future_year_returns_empty(self, forecast_env):
        snap_dir, chart_path = forecast_env
        from app.csv_import import load_chart_of_accounts

        chart = load_chart_of_accounts(chart_path)
        result = compute_forecast(
            2028,
            reference_date=date(2026, 3, 30),
            chart=chart,
            snapshots_dir=snap_dir,
        )
        assert result == {}

    def test_no_snapshots_returns_empty(self, forecast_env):
        _snap_dir, chart_path = forecast_env
        from app.csv_import import load_chart_of_accounts

        chart = load_chart_of_accounts(chart_path)
        empty_dir = chart_path.parent.parent / "empty_snaps"
        empty_dir.mkdir()
        result = compute_forecast(
            2026,
            reference_date=date(2026, 3, 30),
            chart=chart,
            snapshots_dir=empty_dir,
        )
        assert result == {}

    def test_invalid_year_returns_empty(self, forecast_env):
        snap_dir, chart_path = forecast_env
        result = compute_forecast(1900, snapshots_dir=snap_dir, chart_path=chart_path)
        assert result == {}


# ---------------------------------------------------------------------------
# list_budget_years tests
# ---------------------------------------------------------------------------

class TestListBudgetYears:
    def test_lists_available_years(self, tmp_path: Path):
        bdir = tmp_path / "budgets"
        bdir.mkdir()
        (bdir / "2026.yaml").write_text(
            yaml.dump({"year": 2026, "status": "approved"}), encoding="utf-8"
        )
        (bdir / "2027.yaml").write_text(
            yaml.dump({"year": 2027, "status": "draft"}), encoding="utf-8"
        )

        result = list_budget_years(budgets_dir=bdir)
        assert len(result) == 2
        # Sorted descending
        assert result[0]["year"] == 2027
        assert result[0]["status"] == "draft"
        assert result[0]["label"] == "2027-draft"
        assert result[1]["year"] == 2026
        assert result[1]["status"] == "approved"
        assert result[1]["label"] == "2026"

    def test_empty_directory(self, tmp_path: Path):
        bdir = tmp_path / "budgets"
        bdir.mkdir()
        result = list_budget_years(budgets_dir=bdir)
        assert result == []

    def test_nonexistent_directory(self, tmp_path: Path):
        bdir = tmp_path / "nope"
        result = list_budget_years(budgets_dir=bdir)
        assert result == []

    def test_ignores_non_yaml_files(self, tmp_path: Path):
        bdir = tmp_path / "budgets"
        bdir.mkdir()
        (bdir / "2026.yaml").write_text(
            yaml.dump({"year": 2026, "status": "approved"}), encoding="utf-8"
        )
        (bdir / "2026.changelog.json").write_text("{}", encoding="utf-8")
        (bdir / "readme.txt").write_text("ignore me", encoding="utf-8")

        result = list_budget_years(budgets_dir=bdir)
        assert len(result) == 1
