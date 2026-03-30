"""Shared fixtures for the Church Budget Tool test suite."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from starlette.testclient import TestClient as _StarletteTestClient

from app.csv_import import load_chart_of_accounts
from app.models import ChartOfAccounts

# ---------------------------------------------------------------------------
# CSRF auto-injection for all tests
# ---------------------------------------------------------------------------
# The app now requires a CSRF token (double-submit cookie) on every
# POST / PUT / DELETE.  Rather than touching every test file we
# monkey-patch TestClient so that state-changing requests automatically:
#   1. Obtain a csrf_token cookie (via a throwaway GET if necessary)
#   2. Send the cookie value in the X-CSRF-Token header
# ---------------------------------------------------------------------------

_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"
_MUTATING_METHODS = ("post", "put", "delete", "patch")

_original_request = _StarletteTestClient.request


def _csrf_request(self, method: str, url, **kwargs):
    """Wrapper around TestClient.request that injects CSRF tokens."""
    if method.lower() in _MUTATING_METHODS:
        # Ensure we have a CSRF cookie — do a lightweight GET first
        cookies = kwargs.get("cookies") or {}
        # Merge with session cookies already on the client
        csrf_value = None
        # Check if cookie already present from prior requests
        for cookie in self.cookies.jar:
            if cookie.name == _CSRF_COOKIE:
                csrf_value = cookie.value
                break
        # Also check explicit cookies dict
        if not csrf_value:
            csrf_value = cookies.get(_CSRF_COOKIE)
        # If still missing, prime the cookie via a GET to /health
        if not csrf_value:
            _original_request(self, "GET", "/health")
            for cookie in self.cookies.jar:
                if cookie.name == _CSRF_COOKIE:
                    csrf_value = cookie.value
                    break
        # Inject the header
        if csrf_value:
            headers = kwargs.get("headers") or {}
            if _CSRF_HEADER not in headers and _CSRF_HEADER.lower() not in {
                k.lower() for k in headers
            }:
                headers[_CSRF_HEADER] = csrf_value
                kwargs["headers"] = headers
    return _original_request(self, method, url, **kwargs)


# Patch at import time so module-level ``client = TestClient(app)`` objects
# created in test modules also benefit.
_StarletteTestClient.request = _csrf_request  # type: ignore[assignment]


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
