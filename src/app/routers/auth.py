"""FastAPI router for Auth0 user authentication (login/callback/logout).

Handles the OIDC authorization code flow:
    GET  /auth/login     — redirect to Auth0 Universal Login
    GET  /auth/callback  — exchange code for tokens, set httponly cookie
    POST /auth/logout    — clear access_token cookie
"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)


def _secure_cookies() -> bool:
    """Return True unless SECURE_COOKIES=0 (e.g. local dev over plain HTTP)."""
    return os.environ.get("SECURE_COOKIES", "1") != "0"

from app.services.auth import (
    auth0_settings,
    exchange_code,
    get_auth0_login_url,
    verify_jwt,
    build_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_ACCESS_TOKEN_COOKIE = "access_token"


@router.get("/login")
async def login(request: Request, next: str = Query("/")):
    """Redirect to Auth0 Universal Login page.

    The ``next`` query param is forwarded via the OAuth ``state`` so the
    callback can redirect the user back to their original page.
    """
    callback_url = str(request.url_for("auth_callback"))
    login_url = get_auth0_login_url(callback_url, state=next)
    return RedirectResponse(url=login_url, status_code=302)


@router.get("/callback")
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str = "/",
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle Auth0 callback — exchange code for tokens and set cookie."""
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Auth0 error: {error} — {error_description or ''}",
        )
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    callback_url = str(request.url_for("auth_callback"))

    try:
        token_data = await exchange_code(code, callback_url)
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Token exchange failed. Check server logs for details.",
        ) from exc

    id_token = token_data.get("id_token", "")

    # Validate redirect target to prevent open redirect
    target = state if state else "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        target = "/"  # reject absolute URLs

    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        _ACCESS_TOKEN_COOKIE,
        id_token,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(),
        path="/",
        max_age=86400,  # 24 hours
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """Clear the access_token cookie and redirect to Auth0 logout."""
    app_url = str(request.base_url).rstrip("/")
    logout_url = (
        f"https://{auth0_settings.domain}/v2/logout"
        f"?client_id={auth0_settings.client_id}"
        f"&returnTo={app_url}"
    )
    response = RedirectResponse(url=logout_url, status_code=303)
    response.delete_cookie(_ACCESS_TOKEN_COOKIE, path="/")
    return response
