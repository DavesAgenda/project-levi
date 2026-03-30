"""Auth0 integration service — OIDC login, JWT verification, role mapping.

Provides four public helpers consumed by the auth middleware (CHA-202):
    get_auth0_login_url  — build the /authorize redirect URL
    exchange_code        — swap an authorization code for tokens
    verify_jwt           — validate an ID-token / access-token via JWKS
    get_user_role        — map an email to (role, permissions) from roles.yaml

Auth0 credentials are loaded following the same _read_secret pattern used by
``src/app/xero/settings.py`` so that Docker secrets work in production while
plain env vars work in development.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.models.auth import User

# ---------------------------------------------------------------------------
# Secret loading (mirrors xero/settings.py)
# ---------------------------------------------------------------------------

def _read_secret(env_var: str, file_env_var: str, default: str = "") -> str:
    """Read a secret from an env var or a Docker-secret file."""
    file_path = os.environ.get(file_env_var, "")
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return os.environ.get(env_var, default)


@dataclass(frozen=True)
class Auth0Settings:
    """Auth0 tenant credentials loaded from env vars / Docker secrets."""

    domain: str
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> Auth0Settings:
        """Load Auth0 settings.

        Env-var pairs (plain / Docker-secret):
            AUTH0_DOMAIN      / AUTH0_DOMAIN_FILE
            AUTH0_CLIENT_ID   / AUTH0_CLIENT_ID_FILE
            AUTH0_CLIENT_SECRET / AUTH0_CLIENT_SECRET_FILE
        """
        return cls(
            domain=_read_secret("AUTH0_DOMAIN", "AUTH0_DOMAIN_FILE"),
            client_id=_read_secret("AUTH0_CLIENT_ID", "AUTH0_CLIENT_ID_FILE"),
            client_secret=_read_secret(
                "AUTH0_CLIENT_SECRET", "AUTH0_CLIENT_SECRET_FILE"
            ),
        )

    @property
    def issuer(self) -> str:
        return f"https://{self.domain}/"

    @property
    def jwks_uri(self) -> str:
        return f"https://{self.domain}/.well-known/jwks.json"

    @property
    def authorize_url(self) -> str:
        return f"https://{self.domain}/authorize"

    @property
    def token_url(self) -> str:
        return f"https://{self.domain}/oauth/token"


# Singleton — import this wherever Auth0 config is needed
auth0_settings = Auth0Settings.from_env()


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL_SECONDS: float = 3600.0  # re-fetch keys once per hour


async def _fetch_jwks(domain: str | None = None) -> dict[str, Any]:
    """Fetch the JWKS key-set, using a 1-hour cache."""
    global _jwks_cache, _jwks_fetched_at  # noqa: PLW0603

    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at < _JWKS_TTL_SECONDS):
        return _jwks_cache

    uri = (
        f"https://{domain}/.well-known/jwks.json"
        if domain
        else auth0_settings.jwks_uri
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(uri)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        return _jwks_cache


def _invalidate_jwks_cache() -> None:
    """Force the next ``_fetch_jwks`` call to hit the network."""
    global _jwks_cache, _jwks_fetched_at  # noqa: PLW0603
    _jwks_cache = {}
    _jwks_fetched_at = 0.0


# ---------------------------------------------------------------------------
# 1. get_auth0_login_url
# ---------------------------------------------------------------------------

def get_auth0_login_url(redirect_uri: str) -> str:
    """Return the Auth0 Universal Login URL the browser should be redirected to.

    Includes a random ``state`` parameter for CSRF protection.
    """
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": auth0_settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
    }
    url = httpx.URL(auth0_settings.authorize_url, params=params)
    return str(url)


# ---------------------------------------------------------------------------
# 2. exchange_code
# ---------------------------------------------------------------------------

async def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an authorization *code* for Auth0 tokens.

    Returns the raw token payload (``access_token``, ``id_token``, etc.).
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            auth0_settings.token_url,
            json={
                "grant_type": "authorization_code",
                "client_id": auth0_settings.client_id,
                "client_secret": auth0_settings.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# 3. verify_jwt
# ---------------------------------------------------------------------------

def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    """Locate a JWK by *kid* in the JWKS key-set."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_jwt(
    token: str,
    *,
    audience: str | None = None,
    issuer: str | None = None,
) -> dict[str, Any]:
    """Decode and verify an Auth0 JWT (ID token or access token).

    Verification steps:
    1. Decode header to extract ``kid``.
    2. Fetch JWKS and find the matching public key.
    3. Verify signature (RS256), expiry, issuer, and audience via *authlib*.

    Returns the decoded claims dict on success; raises on failure.
    """
    import base64
    from authlib.jose import JsonWebToken, JoseError  # type: ignore[import-untyped]

    jwt_obj = JsonWebToken(["RS256"])

    # Decode header without full verification — just parse the first segment
    try:
        header_b64 = token.split(".")[0]
        # Add padding
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))
    except Exception as exc:
        raise ValueError(f"Cannot decode JWT header: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise ValueError("JWT missing 'kid' header")

    # Fetch JWKS and find the matching key
    jwks = await _fetch_jwks()
    key = _find_key(jwks, kid)
    if key is None:
        # Key rotation may have happened — force refresh and retry once
        _invalidate_jwks_cache()
        jwks = await _fetch_jwks()
        key = _find_key(jwks, kid)
        if key is None:
            raise ValueError(f"No JWKS key found for kid={kid!r}")

    # Build claim options for verification
    claims_options: dict[str, Any] = {
        "exp": {"essential": True},
        "iss": {
            "essential": True,
            "value": issuer or auth0_settings.issuer,
        },
    }
    if audience:
        claims_options["aud"] = {"essential": True, "value": audience}

    try:
        from authlib.jose import JsonWebKey, KeySet  # type: ignore[import-untyped]

        key_set = KeySet([JsonWebKey.import_key(key)])
        claims = jwt_obj.decode(
            token,
            key=key_set,
            claims_options=claims_options,
        )
        claims.validate()
        return dict(claims)
    except JoseError as exc:
        raise ValueError(f"JWT verification failed: {exc}") from exc


# ---------------------------------------------------------------------------
# 4. get_user_role  (+ build_user helper)
# ---------------------------------------------------------------------------

_roles_cache: dict[str, Any] | None = None


def _load_roles(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache ``config/roles.yaml``."""
    global _roles_cache  # noqa: PLW0603
    if _roles_cache is not None and path is None:
        return _roles_cache

    if path is None:
        path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "roles.yaml"
    else:
        path = Path(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if path is None or _roles_cache is None:
        _roles_cache = data
    return data


def _invalidate_roles_cache() -> None:
    global _roles_cache  # noqa: PLW0603
    _roles_cache = None


def get_user_role(email: str, *, roles_path: str | Path | None = None) -> tuple[str | None, list[str]]:
    """Return ``(role_name, permissions)`` for *email*.

    Looks up the email in ``config/roles.yaml``.  Returns ``(None, [])`` if the
    email is not listed under any role.
    """
    data = _load_roles(roles_path)
    email_lower = email.lower()

    for role_name, role_cfg in data.get("roles", {}).items():
        emails = [e.lower() for e in role_cfg.get("emails", [])]
        if email_lower in emails:
            return role_name, list(role_cfg.get("permissions", []))

    return None, []


def build_user(claims: dict[str, Any], *, roles_path: str | Path | None = None) -> User:
    """Convenience: build a ``User`` from decoded JWT claims + roles.yaml."""
    email = claims.get("email", "")
    name = claims.get("name", "") or claims.get("nickname", "")
    role, permissions = get_user_role(email, roles_path=roles_path)
    return User(email=email, name=name, role=role, permissions=permissions)
