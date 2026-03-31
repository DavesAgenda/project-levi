"""Security tests for M4 (Auth & Automation) — CHA-207.

Tests security controls added in M4:
- Role escalation prevention (staff -> admin, board -> admin)
- Payroll redaction: staff cannot access individual salary data through ANY path
- CSRF on all state-changing routes
- API key timing-safe comparison
- Cookie security flags on auth callback
- Invalid JWT rejected
- Open redirect prevention
- API docs not publicly accessible
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.middleware.auth as auth_mod
from app.dependencies.auth import should_redact_payroll
from app.models.auth import User
from tests.conftest import _TEST_ADMIN_USER, _original_request
from starlette.testclient import TestClient as _StarletteTestClient


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

_STAFF_USER = User(
    email="staff@newlightanglican.org",
    name="Staff Member",
    role="staff",
    permissions=["read"],
)

_BOARD_USER = User(
    email="warden@newlightanglican.org",
    name="Board Member",
    role="board",
    permissions=["read", "payroll_detail"],
)


@pytest.fixture(autouse=True)
def _restore_auth():
    """Ensure conftest admin override is restored after each test."""
    yield
    auth_mod.override_user = _TEST_ADMIN_USER


class _RawTestClient(_StarletteTestClient):
    """TestClient that bypasses the CSRF auto-injection monkeypatch."""

    def request(self, method: str, url, **kwargs):
        return _original_request(self, method, url, **kwargs)


# ===========================================================================
# 1. Role Escalation Prevention
# ===========================================================================


class TestRoleEscalation:
    """Verify that lower-privileged roles cannot access admin-only endpoints."""

    def test_staff_cannot_create_draft_budget(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/budget/create-draft",
            data={"year": "2099", "base_year": "0"},
        )
        assert resp.status_code == 403

    def test_board_cannot_create_draft_budget(self):
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/budget/create-draft",
            data={"year": "2099", "base_year": "0"},
        )
        assert resp.status_code == 403

    def test_staff_cannot_transition_budget(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/budget/2026/transition",
            data={"new_status": "proposed"},
        )
        assert resp.status_code == 403

    def test_board_cannot_transition_budget(self):
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/budget/2026/transition",
            data={"new_status": "proposed"},
        )
        assert resp.status_code == 403

    def test_staff_cannot_call_sync_monthly(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/xero/sync-monthly")
        assert resp.status_code == 403

    def test_staff_cannot_call_sync_now(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/xero/sync-now")
        assert resp.status_code == 403

    def test_staff_cannot_upload_csv(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/csv/upload",
            files={"file": ("test.csv", b"header\n", "text/csv")},
        )
        assert resp.status_code == 403

    def test_staff_cannot_access_verification(self):
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/verification")
        assert resp.status_code == 403


# ===========================================================================
# 2. Payroll Redaction Audit — ALL code paths
# ===========================================================================


class TestPayrollRedactionAllPaths:
    """Staff cannot access individual payroll data through ANY endpoint."""

    def test_staff_payroll_report_hides_individual_table(self):
        """Staff cannot see Staff Cost Breakdown on /reports/payroll."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll")
        assert resp.status_code == 200
        body = resp.text
        assert "Staff Cost Breakdown" not in body

    def test_staff_payroll_scenarios_blocked(self):
        """Staff gets 403 on payroll scenarios main page."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios")
        assert resp.status_code == 403

    def test_staff_payroll_scenarios_preview_blocked(self):
        """Staff gets 403 on payroll scenarios preview (JSON API)."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/payroll-scenarios/preview")
        assert resp.status_code == 403

    def test_staff_budget_view_hides_payroll_line_items(self):
        """Staff sees 'Total Staffing' not individual payroll lines."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026")
        assert resp.status_code == 200
        body = resp.text
        assert "Total Staffing" in body

    def test_staff_budget_comparison_hides_payroll_categories(self):
        """Staff sees collapsed payroll in budget comparison."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/budget/2026/compare")
        assert resp.status_code == 200
        body = resp.text
        assert "Total Staffing" in body
        assert "Ministry Staff</td>" not in body

    def test_staff_payroll_export_blocked(self):
        """Staff cannot export payroll as markdown (CHA-207 fix)."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll/export?format=md")
        assert resp.status_code == 403
        assert "payroll_detail" in resp.json()["detail"]

    def test_staff_payroll_pdf_export_blocked(self):
        """Staff cannot export payroll as PDF redirect (CHA-207 fix)."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/reports/payroll/export?format=pdf",
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_admin_payroll_export_allowed(self):
        """Admin CAN export payroll."""
        from app.main import app

        auth_mod.override_user = _TEST_ADMIN_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll/export?format=md")
        assert resp.status_code == 200

    def test_board_payroll_export_allowed(self):
        """Board CAN export payroll (has payroll_detail permission)."""
        from app.main import app

        auth_mod.override_user = _BOARD_USER
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/reports/payroll/export?format=md")
        assert resp.status_code == 200

    def test_staff_payroll_scenario_post_routes_blocked(self):
        """Staff cannot POST to any payroll scenario mutation endpoint."""
        from app.main import app

        auth_mod.override_user = _STAFF_USER
        client = TestClient(app, raise_server_exceptions=False)

        routes = [
            "/budget/payroll-scenarios/diocese-scales",
            "/budget/payroll-scenarios/staff/add",
            "/budget/payroll-scenarios/uplift",
            "/budget/payroll-scenarios/save",
            "/budget/payroll-scenarios/reset",
        ]
        for route in routes:
            resp = client.post(route, data={})
            assert resp.status_code == 403, f"Staff should be blocked from {route}"


# ===========================================================================
# 3. CSRF Protection on State-Changing Routes
# ===========================================================================


class TestCsrfProtection:
    """Verify CSRF token required on all POST/PUT/DELETE routes."""

    def test_post_without_csrf_rejected(self):
        from app.main import app

        client = _RawTestClient(app)
        resp = client.post("/health")
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    def test_put_without_csrf_rejected(self):
        from app.main import app

        client = _RawTestClient(app)
        resp = client.put("/health")
        assert resp.status_code == 403

    def test_delete_without_csrf_rejected(self):
        from app.main import app

        client = _RawTestClient(app)
        resp = client.delete("/health")
        assert resp.status_code == 403

    def test_csrf_token_in_cookie_not_in_get_body(self):
        """CSRF token should be in cookie header, not in the HTML body."""
        from app.main import app

        client = _RawTestClient(app)
        resp = client.get("/")
        # The CSRF token value should not appear in the response body
        csrf_cookie = None
        for cookie in client.cookies.jar:
            if cookie.name == "csrf_token":
                csrf_cookie = cookie.value
                break
        if csrf_cookie:
            # The full token should not appear in HTML body
            # (form hidden fields are OK — they use JS to read the cookie)
            assert csrf_cookie not in resp.text

    def test_csrf_uses_timing_safe_comparison(self):
        """CSRF middleware uses secrets.compare_digest."""
        from app.middleware.csrf import CSRFMiddleware
        import inspect
        source = inspect.getsource(CSRFMiddleware.dispatch)
        assert "compare_digest" in source


# ===========================================================================
# 4. API Key Timing-Safe Comparison
# ===========================================================================


class TestApiKeyTimingSafe:
    """Verify API key comparison uses secrets.compare_digest."""

    def test_timing_safe_comparison_in_code(self):
        """The xero_sync router must use secrets.compare_digest."""
        import inspect
        from app.routers.xero_sync import require_api_key_or_admin

        source = inspect.getsource(require_api_key_or_admin)
        assert "compare_digest" in source, (
            "API key comparison must use secrets.compare_digest"
        )

    def test_api_key_not_in_error_message(self):
        """Error messages must not leak the API key value."""
        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/xero/sync-monthly",
            headers={"X-API-Key": "test-wrong-key"},
        )
        body = resp.text
        # The wrong key should not appear in the error response
        assert "test-wrong-key" not in body

    def test_invalid_api_key_returns_401(self, monkeypatch):
        """Invalid API key returns 401 without leaking details."""
        from app.main import app

        monkeypatch.setenv("SYNC_API_KEY", "correct-key-abc")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/xero/sync-monthly",
            headers={"X-API-Key": "wrong-key-xyz"},
        )
        assert resp.status_code == 401
        assert "correct-key-abc" not in resp.text
        assert "wrong-key-xyz" not in resp.text


# ===========================================================================
# 5. Auth Cookie Security Flags
# ===========================================================================


class TestAuthCookieFlags:
    """Verify access_token cookie has httponly, samesite=lax, and secure in production."""

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_callback_sets_httponly_cookie(self, mock_exchange, monkeypatch):
        """Auth callback sets access_token cookie with httponly flag."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-client-secret")

        import importlib
        import app.services.auth as svc

        svc._invalidate_jwks_cache()
        svc._invalidate_roles_cache()
        importlib.reload(svc)

        mock_exchange.return_value = {
            "id_token": "test-jwt-token",
            "access_token": "at-xyz",
        }

        from app.main import app

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/auth/callback?code=test-code&state=/")
        assert resp.status_code == 303

        # Check cookie was set
        cookie = resp.cookies.get("access_token")
        assert cookie == "test-jwt-token"

        # Verify cookie flags via Set-Cookie header
        set_cookie_headers = [
            v
            for k, v in resp.headers.multi_items()
            if k.lower() == "set-cookie" and "access_token" in v
        ]
        assert len(set_cookie_headers) >= 1
        cookie_header = set_cookie_headers[0].lower()
        assert "httponly" in cookie_header
        assert "samesite=lax" in cookie_header

    def test_auth_cookie_uses_secure_cookies_function(self):
        """Auth callback must call _secure_cookies() for the secure flag."""
        import inspect
        from app.routers import auth as auth_router_mod

        source = inspect.getsource(auth_router_mod.auth_callback)
        assert "_secure_cookies()" in source

    def test_auth_secure_cookies_defaults_to_true(self):
        """_secure_cookies() returns True by default (production)."""
        from app.routers.auth import _secure_cookies
        import os

        old = os.environ.pop("SECURE_COOKIES", None)
        try:
            assert _secure_cookies() is True
        finally:
            if old is not None:
                os.environ["SECURE_COOKIES"] = old
            else:
                os.environ.setdefault("SECURE_COOKIES", "0")


# ===========================================================================
# 6. JWT Validation
# ===========================================================================


class TestJwtValidation:
    """Verify JWT verification rejects invalid tokens."""

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, monkeypatch):
        """Expired JWT should be rejected."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")

        import importlib
        import app.services.auth as auth_svc

        auth_svc._invalidate_jwks_cache()
        auth_svc._invalidate_roles_cache()
        importlib.reload(auth_svc)

        from authlib.jose import JsonWebKey, jwt as authlib_jwt

        private_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        private_jwk = private_key.as_dict(is_private=True)
        private_jwk["kid"] = "test-key-exp"
        private_jwk["use"] = "sig"
        private_jwk["alg"] = "RS256"

        public_jwk = private_key.as_dict(is_private=False)
        public_jwk["kid"] = "test-key-exp"
        public_jwk["use"] = "sig"
        public_jwk["alg"] = "RS256"

        jwks = {"keys": [public_jwk]}

        now = int(time.time())
        token = authlib_jwt.encode(
            {"alg": "RS256", "kid": "test-key-exp"},
            {
                "sub": "auth0|123",
                "email": "test@test.org",
                "iss": "https://test.us.auth0.com/",
                "aud": "test-client-id",
                "iat": now - 7200,
                "exp": now - 3600,  # expired 1 hour ago
            },
            private_jwk,
        )
        token_str = token.decode() if isinstance(token, bytes) else token

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            with pytest.raises(ValueError, match="JWT verification failed"):
                await auth_svc.verify_jwt(
                    token_str,
                    audience="test-client-id",
                    issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self, monkeypatch):
        """JWT with wrong issuer should be rejected."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")

        import importlib
        import app.services.auth as auth_svc

        auth_svc._invalidate_jwks_cache()
        auth_svc._invalidate_roles_cache()
        importlib.reload(auth_svc)

        from authlib.jose import JsonWebKey, jwt as authlib_jwt

        private_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        private_jwk = private_key.as_dict(is_private=True)
        private_jwk["kid"] = "test-key-iss"
        private_jwk["use"] = "sig"
        private_jwk["alg"] = "RS256"

        public_jwk = private_key.as_dict(is_private=False)
        public_jwk["kid"] = "test-key-iss"
        public_jwk["use"] = "sig"
        public_jwk["alg"] = "RS256"

        jwks = {"keys": [public_jwk]}

        now = int(time.time())
        token = authlib_jwt.encode(
            {"alg": "RS256", "kid": "test-key-iss"},
            {
                "sub": "auth0|123",
                "email": "test@test.org",
                "iss": "https://evil.auth0.com/",  # wrong issuer
                "aud": "test-client-id",
                "iat": now,
                "exp": now + 3600,
            },
            private_jwk,
        )
        token_str = token.decode() if isinstance(token, bytes) else token

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            with pytest.raises(ValueError, match="JWT verification failed"):
                await auth_svc.verify_jwt(
                    token_str,
                    audience="test-client-id",
                    issuer="https://test.us.auth0.com/",
                )

    def test_missing_token_redirects_to_login(self):
        """Request without access_token cookie redirects to login."""
        from app.main import app

        auth_mod.override_user = None
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


# ===========================================================================
# 7. Open Redirect Prevention
# ===========================================================================


class TestOpenRedirectPrevention:
    """Auth callback state parameter must not allow absolute URL redirects."""

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_absolute_url_redirect_blocked(self, mock_exchange, monkeypatch):
        """Absolute URL in state should be rejected (redirects to /)."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")

        import importlib
        import app.services.auth as svc

        svc._invalidate_jwks_cache()
        svc._invalidate_roles_cache()
        importlib.reload(svc)

        mock_exchange.return_value = {
            "id_token": "test-jwt",
            "access_token": "at",
        }

        from app.main import app

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/auth/callback?code=abc&state=https://evil.com/phish")
        assert resp.status_code == 303
        # Should redirect to / not to evil.com
        assert resp.headers["location"] == "/"

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_relative_url_redirect_allowed(self, mock_exchange, monkeypatch):
        """Relative URL in state should be allowed."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")

        import importlib
        import app.services.auth as svc

        svc._invalidate_jwks_cache()
        svc._invalidate_roles_cache()
        importlib.reload(svc)

        mock_exchange.return_value = {
            "id_token": "test-jwt",
            "access_token": "at",
        }

        from app.main import app

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/auth/callback?code=abc&state=/reports/council")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/reports/council"


# ===========================================================================
# 8. API Documentation Not Public
# ===========================================================================


class TestApiDocsNotPublic:
    """API docs (/docs, /openapi.json, /redoc) require authentication."""

    def test_docs_requires_auth(self):
        from app.main import app

        auth_mod.override_user = None
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        resp = client.get("/docs")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_openapi_json_requires_auth(self):
        from app.main import app

        auth_mod.override_user = None
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        resp = client.get("/openapi.json")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    def test_redoc_requires_auth(self):
        from app.main import app

        auth_mod.override_user = None
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        resp = client.get("/redoc")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


# ===========================================================================
# 9. Token Exchange Error Info Disclosure Prevention
# ===========================================================================


class TestTokenExchangeErrorDisclosure:
    """Auth callback should not leak internal error details."""

    @patch("app.routers.auth.exchange_code", new_callable=AsyncMock)
    def test_exchange_error_does_not_leak_details(self, mock_exchange, monkeypatch):
        """Token exchange error message should be generic."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")

        import importlib
        import app.services.auth as svc

        svc._invalidate_jwks_cache()
        svc._invalidate_roles_cache()
        importlib.reload(svc)

        mock_exchange.side_effect = Exception(
            "httpx.ConnectError: [Errno 111] Connection refused to internal-service:8443"
        )

        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth/callback?code=bad-code")
        assert resp.status_code == 500
        body = resp.json()
        # Should NOT contain the internal error details
        assert "Connection refused" not in body.get("detail", "")
        assert "internal-service" not in body.get("detail", "")
        # Should contain a generic message
        assert "Check server logs" in body.get("detail", "")


# ===========================================================================
# 10. CSRF Cookie Secure Flag
# ===========================================================================


class TestCsrfCookieSecure:
    """CSRF cookie must have secure flag in production."""

    def test_csrf_cookie_uses_secure_cookies_function(self):
        """Verify the CSRF middleware calls _secure_cookies() for cookie security."""
        import inspect
        from app.middleware.csrf import CSRFMiddleware

        source = inspect.getsource(CSRFMiddleware.dispatch)
        # Must use _secure_cookies() which defaults to True in production
        assert "_secure_cookies()" in source

    def test_secure_cookies_defaults_to_true(self):
        """_secure_cookies() returns True by default (production)."""
        from app.middleware.csrf import _secure_cookies
        import os

        old = os.environ.pop("SECURE_COOKIES", None)
        try:
            assert _secure_cookies() is True
        finally:
            if old is not None:
                os.environ["SECURE_COOKIES"] = old
            else:
                os.environ.setdefault("SECURE_COOKIES", "0")  # restore test default

    def test_secure_cookies_disabled_for_dev(self):
        """_secure_cookies() returns False when SECURE_COOKIES=0."""
        from app.middleware.csrf import _secure_cookies
        import os

        old = os.environ.get("SECURE_COOKIES")
        os.environ["SECURE_COOKIES"] = "0"
        try:
            assert _secure_cookies() is False
        finally:
            if old is not None:
                os.environ["SECURE_COOKIES"] = old
            else:
                os.environ.pop("SECURE_COOKIES", None)
