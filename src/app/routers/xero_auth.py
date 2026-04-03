"""FastAPI router for Xero Web App OAuth (authorization code flow).

Endpoints:
    GET  /auth/xero/login    — Redirect to Xero consent page
    GET  /auth/xero/callback — Exchange code for tokens, store, redirect home
    GET  /auth/xero/status   — Check connection status
    POST /auth/xero/logout   — Clear stored tokens
"""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/xero", tags=["xero-auth"])


@router.get("/login")
async def xero_login(request: Request):
    """Redirect to Xero consent page to authorize scopes."""
    login_url = build_authorize_url(xero_settings.redirect_uri)
    return RedirectResponse(login_url, status_code=302)


@router.get("/callback")
async def xero_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Handle Xero OAuth callback — exchange code for tokens."""
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Xero auth error: {error} — {error_description}",
        )

    if not validate_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    try:
        await exchange_code_for_tokens(code, xero_settings.redirect_uri)
    except Exception as exc:
        logger.error("Xero token exchange failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Xero token exchange failed. Check server logs.",
        ) from exc

    return RedirectResponse("/", status_code=302)


@router.get("/status")
async def xero_status():
    """Check current Xero connection status."""
    if not xero_settings.client_id or not xero_settings.client_secret:
        return {
            "connected": False,
            "message": "Xero credentials not configured.",
        }

    tokens = get_stored_tokens()
    if tokens is None:
        return {
            "connected": False,
            "message": "Not connected. Visit /auth/xero/login to connect.",
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
