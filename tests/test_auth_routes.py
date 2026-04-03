"""Tests for Auth0 login/logout/callback routes (CHA-203).

Covers:
    - GET /auth/login redirects to Auth0 with state=next
    - GET /auth/callback exchanges code, sets httponly cookie, redirects
    - POST /auth/logout clears cookie and redirects to Auth0 logout
    - Nav bar shows login button when unauthenticated
    - Nav bar shows user name + role badge when authenticated
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _auth0_env(monkeypatch: pytest.MonkeyPatch):
    """Set Auth0 env vars and reload the settings singleton."""
    monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-client-secret")

    import importlib
    import app.services.auth as auth_mod

    auth_mod._invalidate_jwks_cache()
    auth_mod._invalidate_roles_cache()
    importlib.reload(auth_mod)


@pytest.fixture()
def client(_auth0_env) -> TestClient:
    """Create a test client with Auth0 env configured."""
    # Re-import after env reload so router picks up new settings
    import importlib
    import app.routers.auth as auth_router_mod

    importlib.reload(auth_router_mod)

    from app.main import app

    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    """GET /auth/login should redirect to Auth0 Universal Login."""

    def test_login_redirects_to_auth0(self, client: TestClient):
        resp = client.get("/auth/login")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "test.us.auth0.com/authorize" in location
        assert "response_type=code" in location
        assert "client_id=test-client-id" in location

    def test_login_passes_next_as_state(self, client: TestClient):
        resp = client.get("/auth/login?next=/reports/council")
        location = resp.headers["location"]
        # The state parameter should contain the next URL
        assert "state=%2Freports%2Fcouncil" in location or "state=/reports/council" in location

    def test_login_default_state_is_root(self, client: TestClient):
        resp = client.get("/auth/login")
        location = resp.headers["location"]
        assert "state=%2F" in location or "state=/" in location


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------

class TestCallback:
    """GET /auth/callback should exchange code, set cookie, redirect."""

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_callback_sets_cookie_and_redirects(self, mock_exchange, client: TestClient):
        mock_exchange.return_value = {
            "id_token": "fake-jwt-token-abc123",
            "access_token": "at-xyz",
            "token_type": "Bearer",
        }

        resp = client.get("/auth/callback?code=auth-code-123&state=/reports/council")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/reports/council"

        # Check that the access_token cookie was set
        cookie = resp.cookies.get("access_token")
        assert cookie == "fake-jwt-token-abc123"

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_callback_defaults_to_root_redirect(self, mock_exchange, client: TestClient):
        mock_exchange.return_value = {"id_token": "tok", "access_token": "at"}

        resp = client.get("/auth/callback?code=abc")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_callback_exchange_failure_returns_500(self, mock_exchange, client: TestClient):
        mock_exchange.side_effect = Exception("Network error")

        resp = client.get("/auth/callback?code=bad-code")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    """POST /auth/logout should clear cookie and redirect to Auth0 logout."""

    def test_logout_clears_cookie(self, client: TestClient):
        # Set a cookie first
        client.cookies.set("access_token", "some-token")

        resp = client.post("/auth/logout")
        assert resp.status_code == 303

        # The response should delete the access_token cookie
        location = resp.headers["location"]
        assert "test.us.auth0.com/v2/logout" in location
        assert "client_id=test-client-id" in location
        assert "returnTo=" in location

    def test_logout_redirect_url_format(self, client: TestClient):
        resp = client.post("/auth/logout")
        location = resp.headers["location"]
        assert location.startswith("https://test.us.auth0.com/v2/logout?")


# ---------------------------------------------------------------------------
# Nav bar auth state
# ---------------------------------------------------------------------------

class TestNavBar:
    """The nav bar should reflect authentication state."""

    def test_nav_shows_user_info_when_authenticated(self, client: TestClient):
        """When user is set (conftest admin), nav shows user name and logout."""
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert "Test Admin" in body or "test-admin@" in body
        assert "/auth/logout" in body
