"""Tests for the Xero /Budgets parser, overlay loader, and budget merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.xero.budget_summary import (
    _pick_budget_for_year,
    load_xero_budget_overlay,
    parse_budget,
)


def _budget_detail(
    lines: list[tuple[str, str, str, list[tuple[str, float]]]],
) -> dict:
    """Build a minimal ``/Budgets/{id}`` response.

    lines: list of (account_id, account_code, account_name, [(period, amount)])
    """
    return {
        "Budgets": [
            {
                "BudgetID": "b-1",
                "Type": "OVERALL",
                "Description": "2026 Overall",
                "BudgetLines": [
                    {
                        "AccountID": aid,
                        "AccountCode": code,
                        "AccountName": name,
                        "BudgetBalances": [
                            {"Period": p, "Amount": amt, "Notes": ""}
                            for p, amt in bals
                        ],
                    }
                    for aid, code, name, bals in lines
                ],
            }
        ]
    }


class TestParseBudget:
    def test_sums_monthly_balances_in_target_year(self):
        resp = _budget_detail([
            (
                "uuid-10001", "10001", "Offering EFT",
                [(f"2026-{m:02d}-01", 22500.0) for m in range(1, 13)],
            ),
        ])
        result = parse_budget(resp, 2026)
        assert result == {
            "10001": {"name": "Offering EFT", "amount": 270000.0},
        }

    def test_excludes_periods_outside_target_year(self):
        resp = _budget_detail([
            (
                "uuid-10001", "10001", "Offering EFT",
                [
                    ("2025-12-01", 5000.0),  # prior year — excluded
                    ("2026-01-01", 100.0),
                    ("2026-02-01", 200.0),
                    ("2027-01-01", 9000.0),  # next year — excluded
                ],
            ),
        ])
        result = parse_budget(resp, 2026)
        assert result["10001"]["amount"] == 300.0

    def test_uses_uuid_fallback_when_account_code_missing(self):
        resp = {
            "Budgets": [{
                "BudgetLines": [{
                    "AccountID": "uuid-x",
                    "AccountCode": "",
                    "AccountName": "Bank Fees",
                    "BudgetBalances": [
                        {"Period": "2026-01-01", "Amount": 42.0},
                    ],
                }],
            }],
        }
        result = parse_budget(resp, 2026, uuid_to_code={"uuid-x": "41517"})
        assert result == {"41517": {"name": "Bank Fees", "amount": 42.0}}

    def test_skips_lines_with_no_resolvable_code(self):
        resp = {
            "Budgets": [{
                "BudgetLines": [{
                    "AccountID": "uuid-unknown",
                    "AccountCode": "",
                    "BudgetBalances": [{"Period": "2026-01-01", "Amount": 100}],
                }],
            }],
        }
        assert parse_budget(resp, 2026) == {}

    def test_empty_response_returns_empty(self):
        assert parse_budget({}, 2026) == {}
        assert parse_budget({"Budgets": []}, 2026) == {}


class TestPickBudgetForYear:
    def test_prefers_description_match(self):
        budgets = [
            {"BudgetID": "a", "Type": "OVERALL", "Description": "2025 Budget"},
            {"BudgetID": "b", "Type": "OVERALL", "Description": "2026 Budget"},
        ]
        assert _pick_budget_for_year(budgets, 2026)["BudgetID"] == "b"

    def test_falls_back_to_overall(self):
        budgets = [
            {"BudgetID": "a", "Type": "TRACKING", "Description": "Ministry"},
            {"BudgetID": "b", "Type": "OVERALL", "Description": "Default"},
        ]
        assert _pick_budget_for_year(budgets, 2026)["BudgetID"] == "b"

    def test_empty_returns_none(self):
        assert _pick_budget_for_year([], 2026) is None


class TestLoadXeroBudgetOverlay:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_xero_budget_overlay(2026, data_dir=tmp_path) == {}

    def test_reads_overlay_file(self, tmp_path: Path):
        (tmp_path / "xero_budget_2026.json").write_text(
            json.dumps({
                "year": 2026,
                "fetched_at": "2026-04-20T00:00:00Z",
                "accounts": {
                    "10001": {"name": "Offering EFT", "amount": 270000.0},
                    "41517": {"name": "Bank Fees", "amount": 504.0},
                },
            }),
            encoding="utf-8",
        )
        overlay = load_xero_budget_overlay(2026, data_dir=tmp_path)
        assert overlay == {"10001": 270000.0, "41517": 504.0}


class TestLoadBudgetFlatWithOverlay:
    """Ensures the overlay backfills YAML nulls but doesn't override explicit amounts."""

    @pytest.fixture
    def chart_path(self, tmp_path: Path) -> Path:
        cfg = tmp_path / "config"
        cfg.mkdir()
        path = cfg / "chart_of_accounts.yaml"
        # Minimal chart: one income, one expense category, each with one account
        path.write_text(
            """
income:
  offertory:
    budget_label: Offertory
    category: Offertory
    accounts:
      - code: "10001"
        name: Offering EFT
expenses:
  bank_fees:
    budget_label: Bank Fees
    category: Bank Fees
    accounts:
      - code: "41517"
        name: Bank Fees
""",
            encoding="utf-8",
        )
        return path

    def _write_budget(self, tmp_path: Path, content: str) -> Path:
        bdir = tmp_path / "budgets"
        bdir.mkdir()
        (bdir / "2026.yaml").write_text(content, encoding="utf-8")
        return bdir

    def test_overlay_backfills_null(self, tmp_path: Path, chart_path: Path):
        from app.services.budget import load_budget_flat

        bdir = self._write_budget(tmp_path, """
year: 2026
status: approved
income:
  offertory:
    "10001_offering_eft": 275000
expenses:
  bank_fees:
    "41517_bank_fees": null
""")
        result = load_budget_flat(
            2026,
            budgets_dir=bdir,
            chart_path=chart_path,
            xero_overlay={"41517": 504.0},
        )
        assert result.get("offertory") == 275000.0
        assert result.get("bank_fees") == 504.0

    def test_overlay_does_not_override_explicit_amount(
        self, tmp_path: Path, chart_path: Path,
    ):
        from app.services.budget import load_budget_flat

        bdir = self._write_budget(tmp_path, """
year: 2026
status: approved
income:
  offertory:
    "10001_offering_eft": 275000
expenses:
  bank_fees:
    "41517_bank_fees": 600
""")
        result = load_budget_flat(
            2026,
            budgets_dir=bdir,
            chart_path=chart_path,
            xero_overlay={"10001": 999999.0, "41517": 504.0},
        )
        assert result["offertory"] == 275000.0
        assert result["bank_fees"] == 600.0

    def test_overlay_adds_unmentioned_account(
        self, tmp_path: Path, chart_path: Path,
    ):
        """Accounts the YAML never references at all should still be picked up
        from Xero (so advertising/hospitality/etc. don't silently drop)."""
        from app.services.budget import load_budget_flat

        bdir = self._write_budget(tmp_path, """
year: 2026
status: approved
income:
  offertory:
    "10001_offering_eft": 275000
""")
        # 41517 isn't in the YAML at all, only in the overlay
        result = load_budget_flat(
            2026,
            budgets_dir=bdir,
            chart_path=chart_path,
            xero_overlay={"41517": 504.0},
        )
        assert result["bank_fees"] == 504.0

    def test_empty_overlay_preserves_existing_behaviour(
        self, tmp_path: Path, chart_path: Path,
    ):
        from app.services.budget import load_budget_flat

        bdir = self._write_budget(tmp_path, """
year: 2026
status: approved
income:
  offertory:
    "10001_offering_eft": 275000
expenses:
  bank_fees:
    "41517_bank_fees": null
""")
        result = load_budget_flat(
            2026,
            budgets_dir=bdir,
            chart_path=chart_path,
            xero_overlay={},
        )
        assert result["offertory"] == 275000.0
        assert "bank_fees" not in result
