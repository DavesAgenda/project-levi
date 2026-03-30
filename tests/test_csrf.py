"""Tests for CSRF middleware (CHA-200).

Verifies the double-submit cookie pattern:
- POST without token -> 403
- POST with valid token -> 2xx
- GET requests unaffected
- PUT / DELETE also protected
"""

from __future__ import annotations

from starlette.testclient import TestClient as _StarletteTestClient

from app.main import app
from tests.conftest import _original_request

# We need a *raw* TestClient that does NOT auto-inject CSRF tokens so we
# can verify that the middleware actually rejects unauthenticated requests.

_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"


class _RawTestClient(_StarletteTestClient):
    """TestClient that bypasses the CSRF auto-injection monkeypatch."""

    def request(self, method: str, url, **kwargs):
        return _original_request(self, method, url, **kwargs)


raw_client = _RawTestClient(app)
auto_client = _StarletteTestClient(app)  # uses the patched request


# ---------------------------------------------------------------------------
# 1. GET is unaffected
# ---------------------------------------------------------------------------

class TestGetUnaffected:
    def test_get_health(self):
        resp = raw_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_get_sets_csrf_cookie(self):
        client = _RawTestClient(app)  # fresh client with no cookies
        resp = client.get("/health")
        assert _CSRF_COOKIE in resp.cookies


# ---------------------------------------------------------------------------
# 2. POST without token -> 403
# ---------------------------------------------------------------------------

class TestPostWithoutToken:
    def test_post_no_cookie_no_header(self):
        # Fresh client with no cookies at all
        client = _RawTestClient(app)
        resp = client.post("/health")  # endpoint doesn't matter
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    def test_post_with_cookie_but_no_header(self):
        client = _RawTestClient(app)
        # Prime the cookie via GET
        client.get("/health")
        # POST without the header
        resp = client.post("/health")
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    def test_post_with_wrong_token(self):
        client = _RawTestClient(app)
        client.get("/health")
        resp = client.post(
            "/health",
            headers={_CSRF_HEADER: "wrong-token-value"},
        )
        assert resp.status_code == 403
        assert "mismatch" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. POST with valid token -> passes through to app
# ---------------------------------------------------------------------------

class TestPostWithValidToken:
    def test_post_with_matching_header(self):
        client = _RawTestClient(app)
        # Prime the cookie
        get_resp = client.get("/health")
        token = get_resp.cookies[_CSRF_COOKIE]
        # POST with matching header — /health is GET-only so we'll get 405,
        # but NOT 403, proving CSRF passed.
        resp = client.post(
            "/health",
            headers={_CSRF_HEADER: token},
        )
        assert resp.status_code == 405  # Method Not Allowed, not 403

    def test_auto_client_handles_csrf(self):
        """The patched TestClient should auto-inject CSRF and pass through."""
        client = _StarletteTestClient(app)
        resp = client.post("/health")
        # Should get 405 (Method Not Allowed for GET-only endpoint),
        # NOT 403 (CSRF rejection)
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# 4. PUT / DELETE also protected
# ---------------------------------------------------------------------------

class TestOtherMethods:
    def test_put_without_token_rejected(self):
        client = _RawTestClient(app)
        resp = client.put("/health")
        assert resp.status_code == 403

    def test_delete_without_token_rejected(self):
        client = _RawTestClient(app)
        resp = client.delete("/health")
        assert resp.status_code == 403

    def test_put_with_valid_token(self):
        client = _RawTestClient(app)
        get_resp = client.get("/health")
        token = get_resp.cookies[_CSRF_COOKIE]
        resp = client.put("/health", headers={_CSRF_HEADER: token})
        assert resp.status_code == 405  # not 403


# ---------------------------------------------------------------------------
# 5. Token rotation
# ---------------------------------------------------------------------------

class TestTokenRotation:
    def test_token_rotated_after_post(self):
        client = _RawTestClient(app)
        get_resp = client.get("/health")
        token1 = get_resp.cookies[_CSRF_COOKIE]

        # Use token successfully
        post_resp = client.post(
            "/health",
            headers={_CSRF_HEADER: token1},
        )
        assert post_resp.status_code == 405  # CSRF passed

        # The response should have set a new cookie
        assert _CSRF_COOKIE in post_resp.cookies
        token2 = post_resp.cookies[_CSRF_COOKIE]
        assert token1 != token2
