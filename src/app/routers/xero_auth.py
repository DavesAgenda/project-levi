"""FastAPI router for Xero OAuth 2.0 authorization flow.

Endpoints:
    GET  /auth/xero/login    — Redirect user to Xero for consent
    GET  /auth/xero/callback — Handle authorization code exchange
    GET  /auth/xero/status   — Check current auth status
    POST /auth/xero/logout   — Clear stored tokens
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.xero.oauth import (
    build_authorize_url,
    clear_tokens,
    exchange_code_for_tokens,
    get_stored_tokens,
    is_token_expired,
    validate_oauth_state,
)
from app.xero.settings import xero_settings

router = APIRouter(prefix="/auth/xero", tags=["xero-auth"])


@router.get("/login")
async def xero_login():
    """Redirect to Xero's authorization page for OAuth consent."""
    if not xero_settings.client_id:
        raise HTTPException(
            status_code=500,
            detail="XERO_CLIENT_ID not configured. Set it as an environment variable.",
        )
    url = build_authorize_url(xero_settings.redirect_uri)
    return RedirectResponse(url=url)


@router.get("/callback")
async def xero_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle Xero OAuth callback with authorization code.

    Exchanges the code for access + refresh tokens and stores them.
    """
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Xero authorization failed: {error} — {error_description or ''}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing authorization code or state parameter.",
        )

    if not validate_oauth_state(state):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state parameter. Please try logging in again.",
        )

    try:
        token_data = await exchange_code_for_tokens(code, xero_settings.redirect_uri)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Token exchange failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Token exchange failed. Check server logs for details.",
        ) from exc

    return {
        "status": "connected",
        "tenant_id": token_data.get("tenant_id"),
        "message": "Xero authorization successful. You can now fetch reports.",
    }


@router.get("/status")
async def xero_status():
    """Check current Xero connection status."""
    tokens = get_stored_tokens()
    if tokens is None:
        return {
            "connected": False,
            "message": "Not connected. Visit /auth/xero/login to authorize.",
        }

    expired = is_token_expired(tokens)
    has_refresh = bool(tokens.get("refresh_token"))

    return {
        "connected": True,
        "token_expired": expired,
        "has_refresh_token": has_refresh,
        "tenant_id": tokens.get("tenant_id"),
        "message": (
            "Connected and token is valid."
            if not expired
            else "Token expired — will auto-refresh on next API call."
            if has_refresh
            else "Token expired and no refresh token available."
        ),
    }


@router.post("/logout")
async def xero_logout():
    """Clear stored Xero tokens."""
    clear_tokens()
    return {"status": "disconnected", "message": "Xero tokens cleared."}
