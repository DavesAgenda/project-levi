"""Xero OAuth 2.0 Authorization Code Grant flow.

Handles:
- Redirect to Xero for consent
- Callback to exchange code for tokens
- Token storage (file-based, gitignored)
- Automatic token refresh before expiry
- Tenant ID retrieval from /connections
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import time
from pathlib import Path

import httpx

from app.xero.settings import xero_settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"

XERO_SCOPES = [
    "openid",
    "offline_access",
    "accounting.reports.profitandloss.read",
    "accounting.reports.trialbalance.read",
    "accounting.reports.balancesheet.read",
    "accounting.settings",
]

TOKEN_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".xero_tokens.json"

# In-memory state store for CSRF protection (single-user app, no DB needed)
_oauth_states: dict[str, float] = {}

# Refresh buffer — refresh tokens 5 minutes before expiry
_REFRESH_BUFFER_SECONDS = 300


# ---------------------------------------------------------------------------
# Token storage (file-based, gitignored)
# ---------------------------------------------------------------------------

def _load_tokens() -> dict | None:
    """Load stored tokens from disk. Returns None if no tokens exist."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_tokens(token_data: dict) -> None:
    """Persist tokens to disk with restricted file permissions (owner-only)."""
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass  # Windows may not support Unix permissions


def get_stored_tokens() -> dict | None:
    """Public accessor for stored tokens."""
    return _load_tokens()


def clear_tokens() -> None:
    """Remove stored tokens (for re-auth)."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


# ---------------------------------------------------------------------------
# OAuth state management (CSRF protection)
# ---------------------------------------------------------------------------

def generate_oauth_state() -> str:
    """Generate a random state parameter and store it."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()
    return state


def validate_oauth_state(state: str) -> bool:
    """Validate and consume a state parameter. Returns True if valid."""
    if state in _oauth_states:
        created = _oauth_states.pop(state)
        # State tokens valid for 10 minutes
        if time.time() - created < 600:
            return True
    return False


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------

def build_authorize_url(redirect_uri: str) -> str:
    """Build the Xero authorization URL for the consent flow."""
    state = generate_oauth_state()
    params = {
        "response_type": "code",
        "client_id": xero_settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(XERO_SCOPES),
        "state": state,
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})}" for k, v in params.items())
    # Use httpx URL building for proper encoding
    url = httpx.URL(XERO_AUTHORIZE_URL, params=params)
    return str(url)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            XERO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(xero_settings.client_id, xero_settings.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_data = response.json()

    # Add metadata
    token_data["obtained_at"] = time.time()

    # Fetch tenant ID
    tenant_id = await _fetch_tenant_id(token_data["access_token"])
    token_data["tenant_id"] = tenant_id

    _save_tokens(token_data)
    return token_data


async def _fetch_tenant_id(access_token: str) -> str:
    """Retrieve the tenant ID from GET /connections."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            XERO_CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        connections = response.json()

    if not connections:
        raise ValueError("No Xero tenants found. Has the app been authorized?")

    # Use the first (and typically only) connection
    return connections[0]["tenantId"]


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to obtain a new access token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            XERO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(xero_settings.client_id, xero_settings.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_data = response.json()

    token_data["obtained_at"] = time.time()

    # Preserve tenant_id from previous tokens
    existing = _load_tokens()
    if existing and "tenant_id" in existing:
        token_data["tenant_id"] = existing["tenant_id"]

    _save_tokens(token_data)
    return token_data


def is_token_expired(token_data: dict) -> bool:
    """Check if the access token is expired or about to expire."""
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 1800)  # Default 30 min
    return time.time() >= (obtained_at + expires_in - _REFRESH_BUFFER_SECONDS)


async def get_valid_access_token() -> tuple[str, str]:
    """Get a valid access token and tenant ID, refreshing if needed.

    Returns:
        Tuple of (access_token, tenant_id).

    Raises:
        RuntimeError: If no tokens are stored or refresh token is expired.
    """
    tokens = _load_tokens()
    if tokens is None:
        raise RuntimeError(
            "No Xero tokens found. Complete the OAuth flow at /auth/xero/login first."
        )

    if is_token_expired(tokens):
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "Access token expired and no refresh token available. "
                "Re-authorize at /auth/xero/login."
            )
        try:
            tokens = await refresh_access_token(refresh_token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                # Refresh token likely expired (60-day limit)
                clear_tokens()
                raise RuntimeError(
                    "Refresh token expired (60-day limit). "
                    "Re-authorize at /auth/xero/login."
                ) from exc
            raise

    tenant_id = tokens.get("tenant_id")
    if not tenant_id:
        raise RuntimeError(
            "No tenant ID stored. Re-authorize at /auth/xero/login."
        )

    return tokens["access_token"], tenant_id
