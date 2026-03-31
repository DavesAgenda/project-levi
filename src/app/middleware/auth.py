"""Authentication middleware — JWT cookie verification and user injection.

Reads the ``access_token`` httponly cookie on every request, verifies the JWT
via Auth0 JWKS, and sets ``request.state.user`` to a ``User`` model instance.

Public routes (health, auth, static) are exempt from authentication.
Unauthenticated requests are redirected to ``/auth/login?next=<current_url>``.
"""

from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.models.auth import User

logger = logging.getLogger(__name__)

_ACCESS_TOKEN_COOKIE = "access_token"

# Test hook: set to a User instance to bypass JWT verification entirely.
# The conftest sets this to a default admin user so all existing tests pass.
override_user: User | None = None

# Path prefixes that do NOT require authentication
_PUBLIC_PREFIXES = (
    "/health",
    "/auth/",
    "/static/",
)


def _is_public(path: str) -> bool:
    """Return True if the path is exempt from auth."""
    # Exact match for /health
    if path == "/health":
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Verify JWT from httponly cookie and populate request.state.user."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Test hook: if override_user is set, bypass JWT entirely
        if override_user is not None:
            request.state.user = override_user
            return await call_next(request)

        # Skip auth for public routes
        if _is_public(request.url.path):
            # Still set an anonymous user so templates don't break
            request.state.user = None
            return await call_next(request)

        token = request.cookies.get(_ACCESS_TOKEN_COOKIE)
        if not token:
            return self._redirect_to_login(request)

        try:
            from app.services.auth import verify_jwt, build_user

            claims = await verify_jwt(token)
            user = build_user(claims)
            request.state.user = user
        except Exception:
            logger.debug("JWT verification failed for %s", request.url.path, exc_info=True)
            return self._redirect_to_login(request)

        return await call_next(request)

    @staticmethod
    def _redirect_to_login(request: Request) -> Response:
        """Redirect to login page preserving the original URL."""
        next_url = str(request.url)
        return RedirectResponse(
            url=f"/auth/login?next={quote(next_url, safe='')}",
            status_code=302,
        )
