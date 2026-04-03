"""FastAPI router for Xero sync endpoints.

Provides:
- POST /api/xero/sync-monthly  — automated monthly sync (API key or admin session)
- POST /api/xero/sync-now      — manual live sync (admin session only)
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.dependencies.auth import get_current_user, require_role
from app.models.auth import User
from app.services.sync import sync_historical, sync_monthly, sync_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/xero", tags=["xero-sync"])


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

def _read_sync_api_key() -> str:
    """Read the sync API key from Docker secret or environment variable."""
    # Docker secrets file
    file_path = os.environ.get("SYNC_API_KEY_FILE", "")
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()

    # Plain environment variable
    return os.environ.get("SYNC_API_KEY", "")


def require_api_key_or_admin(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
) -> User | str:
    """Authenticate via X-API-Key header OR session cookie (admin role).

    For API key auth: validates against SYNC_API_KEY secret.
    For session auth: requires admin role.

    Returns the User (session) or "api_key" (API key auth).
    """
    # Try API key first
    if x_api_key:
        expected = _read_sync_api_key()
        if not expected:
            raise HTTPException(
                status_code=500,
                detail="SYNC_API_KEY not configured on server",
            )
        if not secrets.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="Invalid API key")
        return "api_key"

    # Fall through to session auth (admin required)
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: requires admin role, you have {user.role}",
        )
    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/sync-monthly")
async def sync_monthly_endpoint(
    auth: User | str = Depends(require_api_key_or_admin),
):
    """Sync the prior completed month's P&L and Balance Sheet from Xero.

    Callable via:
    - Admin session cookie (browser)
    - X-API-Key header (n8n / cron automation)
    """
    result = await sync_monthly()
    return result


@router.post("/sync-now")
async def sync_now_endpoint(
    user: User = Depends(require_role("admin")),
):
    """Manual live sync: fetch monthly P&L snapshots for the current year.

    Admin only (session cookie required).
    """
    result = await sync_now()
    return result


@router.post("/sync-historical")
async def sync_historical_endpoint(
    user: User = Depends(require_role("admin")),
    from_year: int = 2021,
    to_year: int = 2026,
):
    """Backfill monthly P&L snapshots for a range of years.

    Admin only (session cookie required).
    Defaults to 2021–2026 (5+ years of history).
    """
    result = await sync_historical(from_year, to_year)
    return result
