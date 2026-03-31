"""CSRF protection middleware using the double-submit cookie pattern.

How it works:
1. On every response, set a ``csrf_token`` cookie (non-HttpOnly so JS/htmx
   can read it).
2. On POST / PUT / DELETE requests, require a matching ``X-CSRF-Token`` header
   **or** a ``csrf_token`` form field whose value equals the cookie.
3. GET / HEAD / OPTIONS are exempt (safe methods).
4. A mismatch or missing token returns **403 Forbidden**.
"""

from __future__ import annotations

import os
import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_FORM_FIELD = "csrf_token"
_TOKEN_BYTES = 32  # 64 hex chars


def _secure_cookies() -> bool:
    """Return True unless SECURE_COOKIES=0 (e.g. local dev over plain HTTP)."""
    return os.environ.get("SECURE_COOKIES", "1") != "0"


def _generate_token() -> str:
    return secrets.token_hex(_TOKEN_BYTES)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF middleware for FastAPI / Starlette."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # ------------------------------------------------------------------
        # 1. For safe methods, just ensure the cookie exists and pass through
        # ------------------------------------------------------------------
        if request.method in _SAFE_METHODS:
            response = await call_next(request)
            if _COOKIE_NAME not in request.cookies:
                token = _generate_token()
                response.set_cookie(
                    _COOKIE_NAME,
                    token,
                    httponly=False,  # JS must read it
                    secure=_secure_cookies(),
                    samesite="strict",
                    path="/",
                )
            return response

        # ------------------------------------------------------------------
        # 2. State-changing methods: validate the token
        # ------------------------------------------------------------------
        cookie_token = request.cookies.get(_COOKIE_NAME)
        if not cookie_token:
            return JSONResponse(
                {"detail": "CSRF cookie missing"},
                status_code=403,
            )

        # Accept token from header (htmx) or form field (plain forms)
        submitted_token = request.headers.get(_HEADER_NAME)
        if not submitted_token:
            # Try form body — only if content type is form-encoded
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                form = await request.form()
                submitted_token = form.get(_FORM_FIELD)

        if not submitted_token or not secrets.compare_digest(submitted_token, cookie_token):
            return JSONResponse(
                {"detail": "CSRF token mismatch"},
                status_code=403,
            )

        # Token is valid — continue
        response = await call_next(request)

        # Rotate token after successful state-changing request
        new_token = _generate_token()
        response.set_cookie(
            _COOKIE_NAME,
            new_token,
            httponly=False,
            secure=_secure_cookies(),
            samesite="strict",
            path="/",
        )
        return response
