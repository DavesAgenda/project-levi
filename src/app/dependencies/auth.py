"""FastAPI dependencies for role-based access control.

Usage in routers::

    from app.dependencies.auth import require_role, require_permission

    @router.post("/budget/{year}/line/...")
    async def update_line(
        ...,
        user: User = Depends(require_role("admin")),
    ):
        ...

    @router.get("/payroll/detail")
    async def payroll_detail(
        ...,
        user: User = Depends(require_permission("payroll_detail")),
    ):
        ...
"""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request

from app.models.auth import User


def get_current_user(request: Request) -> User:
    """Extract the current user from request.state (set by AuthMiddleware).

    Returns a ``User`` instance or raises 401 if not authenticated.
    """
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_role(*roles: str) -> Callable:
    """Return a dependency that enforces one of the given roles.

    Example::

        Depends(require_role("admin"))
        Depends(require_role("admin", "board"))
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: requires role {' or '.join(roles)}, you have {user.role}",
            )
        return user

    return _check


def require_permission(permission: str) -> Callable:
    """Return a dependency that enforces a specific permission."""

    def _check(user: User = Depends(get_current_user)) -> User:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: requires permission '{permission}'",
            )
        return user

    return _check
