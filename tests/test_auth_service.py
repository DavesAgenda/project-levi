"""Tests for src/app/services/auth.py — Auth0 integration service.

Covers:
    - JWT verification (valid / expired / tampered / missing kid)
    - Role mapping (known email / unknown email / case-insensitive)
    - JWKS caching behaviour
    - get_auth0_login_url construction
    - exchange_code token exchange
    - build_user convenience helper
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# authlib helpers for generating test JWTs
from authlib.jose import JsonWebKey, jwt as authlib_jwt  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _auth0_env(monkeypatch: pytest.MonkeyPatch):
    """Set Auth0 env vars and reload the settings singleton."""
    monkeypatch.setenv("AUTH0_DOMAIN", "test.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-client-secret")

    # Reload module so the singleton picks up the new env vars
    import importlib
    import app.services.auth as auth_mod

    auth_mod._invalidate_jwks_cache()
    auth_mod._invalidate_roles_cache()
    importlib.reload(auth_mod)


@pytest.fixture()
def rsa_key_pair():
    """Generate a fresh RSA key-pair in JWK format."""
    private_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    private_jwk = private_key.as_dict(is_private=True)
    private_jwk["kid"] = "test-key-1"
    private_jwk["use"] = "sig"
    private_jwk["alg"] = "RS256"

    public_jwk = private_key.as_dict(is_private=False)
    public_jwk["kid"] = "test-key-1"
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"

    return private_jwk, public_jwk


@pytest.fixture()
def make_token(rsa_key_pair):
    """Factory that mints a signed JWT with configurable claims."""
    private_jwk, _ = rsa_key_pair

    def _make(
        *,
        sub: str = "auth0|abc123",
        email: str = "user@example.com",
        name: str = "Test User",
        iss: str = "https://test.us.auth0.com/",
        aud: str = "test-client-id",
        exp_offset: int = 3600,
        kid: str | None = "test-key-1",
        extra_claims: dict | None = None,
    ) -> str:
        now = int(time.time())
        header = {"alg": "RS256"}
        if kid is not None:
            header["kid"] = kid
        payload: dict = {
            "sub": sub,
            "email": email,
            "name": name,
            "iss": iss,
            "aud": aud,
            "iat": now,
            "exp": now + exp_offset,
        }
        if extra_claims:
            payload.update(extra_claims)
        token = authlib_jwt.encode(header, payload, private_jwk)
        return token.decode() if isinstance(token, bytes) else token

    return _make


@pytest.fixture()
def roles_yaml(tmp_path: Path) -> Path:
    """Write a test roles.yaml and return the path."""
    content = textwrap.dedent("""\
        roles:
          admin:
            emails:
              - treasurer@church.org
              - rector@church.org
            permissions:
              - read
              - write
              - payroll_detail
              - payroll_scenarios
          board:
            emails:
              - warden@church.org
            permissions:
              - read
              - payroll_detail
          staff:
            emails:
              - office@church.org
            permissions:
              - read
    """)
    p = tmp_path / "roles.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Role mapping tests
# ---------------------------------------------------------------------------

class TestGetUserRole:
    def test_known_admin(self, roles_yaml: Path):
        from app.services.auth import get_user_role

        role, perms = get_user_role("treasurer@church.org", roles_path=roles_yaml)
        assert role == "admin"
        assert "write" in perms
        assert "payroll_scenarios" in perms

    def test_known_board(self, roles_yaml: Path):
        from app.services.auth import get_user_role

        role, perms = get_user_role("warden@church.org", roles_path=roles_yaml)
        assert role == "board"
        assert "read" in perms
        assert "write" not in perms

    def test_known_staff(self, roles_yaml: Path):
        from app.services.auth import get_user_role

        role, perms = get_user_role("office@church.org", roles_path=roles_yaml)
        assert role == "staff"
        assert perms == ["read"]

    def test_unknown_email(self, roles_yaml: Path):
        from app.services.auth import get_user_role

        role, perms = get_user_role("stranger@gmail.com", roles_path=roles_yaml)
        assert role is None
        assert perms == []

    def test_case_insensitive(self, roles_yaml: Path):
        from app.services.auth import get_user_role

        role, _ = get_user_role("Treasurer@Church.ORG", roles_path=roles_yaml)
        assert role == "admin"


# ---------------------------------------------------------------------------
# JWT verification tests
# ---------------------------------------------------------------------------

class TestVerifyJwt:
    @pytest.mark.asyncio
    async def test_valid_token(self, _auth0_env, rsa_key_pair, make_token):
        from app.services.auth import verify_jwt, _invalidate_jwks_cache

        _, public_jwk = rsa_key_pair
        jwks = {"keys": [public_jwk]}
        _invalidate_jwks_cache()

        token = make_token()

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            claims = await verify_jwt(
                token,
                audience="test-client-id",
                issuer="https://test.us.auth0.com/",
            )

        assert claims["email"] == "user@example.com"
        assert claims["sub"] == "auth0|abc123"

    @pytest.mark.asyncio
    async def test_expired_token(self, _auth0_env, rsa_key_pair, make_token):
        from app.services.auth import verify_jwt, _invalidate_jwks_cache

        _, public_jwk = rsa_key_pair
        jwks = {"keys": [public_jwk]}
        _invalidate_jwks_cache()

        token = make_token(exp_offset=-3600)  # expired 1 hour ago

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            with pytest.raises(ValueError, match="JWT verification failed"):
                await verify_jwt(
                    token,
                    audience="test-client-id",
                    issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio
    async def test_wrong_issuer(self, _auth0_env, rsa_key_pair, make_token):
        from app.services.auth import verify_jwt, _invalidate_jwks_cache

        _, public_jwk = rsa_key_pair
        jwks = {"keys": [public_jwk]}
        _invalidate_jwks_cache()

        token = make_token(iss="https://evil.auth0.com/")

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            with pytest.raises(ValueError, match="JWT verification failed"):
                await verify_jwt(
                    token,
                    audience="test-client-id",
                    issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio
    async def test_tampered_token(self, _auth0_env, rsa_key_pair, make_token):
        """Modify the payload after signing — signature check should fail."""
        from app.services.auth import verify_jwt, _invalidate_jwks_cache

        _, public_jwk = rsa_key_pair
        jwks = {"keys": [public_jwk]}
        _invalidate_jwks_cache()

        token = make_token()
        # Tamper: flip a character in the payload section
        parts = token.split(".")
        payload_chars = list(parts[1])
        payload_chars[5] = "X" if payload_chars[5] != "X" else "Y"
        parts[1] = "".join(payload_chars)
        tampered = ".".join(parts)

        with patch("app.services.auth._fetch_jwks", new_callable=AsyncMock, return_value=jwks):
            with pytest.raises((ValueError, Exception)):
                await verify_jwt(
                    tampered,
                    audience="test-client-id",
                    issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio
    async def test_unknown_kid_triggers_refresh(self, _auth0_env, rsa_key_pair, make_token):
        """If kid is not found, JWKS should be re-fetched once."""
        from app.services.auth import verify_jwt, _invalidate_jwks_cache

        _, public_jwk = rsa_key_pair
        jwks = {"keys": [public_jwk]}
        empty_jwks = {"keys": []}
        _invalidate_jwks_cache()

        token = make_token()

        fetch_mock = AsyncMock(side_effect=[empty_jwks, jwks])
        with patch("app.services.auth._fetch_jwks", fetch_mock):
            claims = await verify_jwt(
                token,
                audience="test-client-id",
                issuer="https://test.us.auth0.com/",
            )

        assert claims["email"] == "user@example.com"
        assert fetch_mock.call_count == 2  # initial + retry


# ---------------------------------------------------------------------------
# JWKS caching tests
# ---------------------------------------------------------------------------

class TestJwksCaching:
    @pytest.mark.asyncio
    async def test_cache_hit(self, _auth0_env):
        """Second call within TTL should not fetch again."""
        from app.services.auth import _fetch_jwks, _invalidate_jwks_cache
        import app.services.auth as auth_mod

        _invalidate_jwks_cache()

        fake_jwks = {"keys": [{"kid": "k1"}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = fake_jwks
            mock_resp.raise_for_status.return_value = None

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result1 = await _fetch_jwks()
            result2 = await _fetch_jwks()

        assert result1 == result2 == fake_jwks
        assert mock_client.get.call_count == 1  # only fetched once


# ---------------------------------------------------------------------------
# Login URL tests
# ---------------------------------------------------------------------------

class TestGetAuth0LoginUrl:
    def test_url_contains_required_params(self, _auth0_env):
        from app.services.auth import get_auth0_login_url

        url = get_auth0_login_url("http://localhost:8000/callback")

        assert "test.us.auth0.com/authorize" in url
        assert "response_type=code" in url
        assert "client_id=test-client-id" in url
        assert "scope=openid" in url
        assert "redirect_uri=" in url
        assert "state=" in url

    def test_url_includes_redirect_uri(self, _auth0_env):
        from app.services.auth import get_auth0_login_url

        url = get_auth0_login_url("https://budget.church.org/callback")
        assert "budget.church.org" in url


# ---------------------------------------------------------------------------
# exchange_code tests
# ---------------------------------------------------------------------------

class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_posts_to_token_endpoint(self, _auth0_env):
        from app.services.auth import exchange_code

        fake_tokens = {
            "access_token": "at_xxx",
            "id_token": "id_xxx",
            "token_type": "Bearer",
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = fake_tokens
            mock_resp.raise_for_status.return_value = None

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await exchange_code("auth-code-123", "http://localhost:8000/callback")

        assert result == fake_tokens
        call_kwargs = mock_client.post.call_args
        assert "oauth/token" in call_kwargs.args[0]
        body = call_kwargs.kwargs["json"]
        assert body["code"] == "auth-code-123"
        assert body["grant_type"] == "authorization_code"


# ---------------------------------------------------------------------------
# build_user tests
# ---------------------------------------------------------------------------

class TestBuildUser:
    def test_known_user(self, roles_yaml: Path):
        from app.services.auth import build_user

        claims = {"email": "treasurer@church.org", "name": "Alice Treasurer"}
        user = build_user(claims, roles_path=roles_yaml)

        assert user.email == "treasurer@church.org"
        assert user.name == "Alice Treasurer"
        assert user.role == "admin"
        assert user.is_admin
        assert user.has_permission("write")

    def test_unknown_user(self, roles_yaml: Path):
        from app.services.auth import build_user

        claims = {"email": "nobody@gmail.com", "name": "Nobody"}
        user = build_user(claims, roles_path=roles_yaml)

        assert user.role is None
        assert user.permissions == []
        assert not user.is_admin


# ---------------------------------------------------------------------------
# User model tests
# ---------------------------------------------------------------------------

class TestUserModel:
    def test_has_permission(self):
        from app.models.auth import User

        u = User(email="a@b.com", role="admin", permissions=["read", "write"])
        assert u.has_permission("read")
        assert not u.has_permission("payroll_detail")

    def test_is_authenticated(self):
        from app.models.auth import User

        assert User(email="a@b.com").is_authenticated
        assert not User(email="").is_authenticated

    def test_is_admin(self):
        from app.models.auth import User

        assert User(email="a@b.com", role="admin").is_admin
        assert not User(email="a@b.com", role="board").is_admin
