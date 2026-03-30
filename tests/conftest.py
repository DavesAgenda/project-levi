"""Shared fixtures for the Church Budget Tool test suite."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.csv_import import load_chart_of_accounts
from app.models import ChartOfAccounts


@pytest.fixture()
def chart(tmp_path: Path) -> ChartOfAccounts:
    """Provide a minimal ChartOfAccounts for testing."""
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
              - { code: "20060", name: "Example Street 6 Rent" }
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
              - { code: "89010", name: "Example Avenue 33 Costs" }
    """)
    yaml_path = tmp_path / "chart_of_accounts.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return load_chart_of_accounts(yaml_path)


@pytest.fixture()
def real_chart() -> ChartOfAccounts:
    """Load the real chart_of_accounts.yaml from the project config directory."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "chart_of_accounts.yaml"
    return load_chart_of_accounts(config_path)
