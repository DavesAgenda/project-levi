"""Xero Accounts cache — fetch and persist the UUID -> code/name lookup.

Xero's Reporting API (P&L, Balance Sheet) identifies each row by the
account UUID in the row's ``Attributes`` block and does NOT include the
numeric account code. Without a separate Accounts fetch, any unmapped
row would display as ``(no code)`` in the admin UI.

This module wraps ``GET /Accounts`` and persists the response to
``data/xero_accounts.json`` so the conversion step in
``xero_snapshot_to_financial`` can enrich snapshot rows deterministically
by UUID — both for future pulls and retroactively for snapshots already
on disk (since raw snapshots preserve the UUIDs).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.xero.client import fetch_accounts

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
ACCOUNTS_CACHE_FILE = DATA_DIR / "xero_accounts.json"


async def fetch_and_cache_accounts(
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Fetch the Xero chart of accounts and write it to the cache file.

    Returns the raw Xero response.
    """
    target = cache_path or ACCOUNTS_CACHE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    data = await fetch_accounts()

    envelope = {
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "response": data,
    }
    target.write_text(
        json.dumps(envelope, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Cached %d Xero accounts to %s", len(data.get("Accounts", [])), target)
    return data


def load_uuid_to_code(cache_path: Path | None = None) -> dict[str, str]:
    """Return {AccountID: Code} from the cached Xero accounts response.

    Includes archived accounts so historical snapshots referring to
    deleted accounts can still be resolved. Returns an empty dict if
    no cache exists or it can't be parsed.
    """
    target = cache_path or ACCOUNTS_CACHE_FILE
    if not target.exists():
        return {}
    try:
        envelope = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read Xero accounts cache at %s", target)
        return {}

    # Support both the enveloped form we write and a bare Accounts response
    response = envelope.get("response", envelope)
    accounts = response.get("Accounts", [])

    lookup: dict[str, str] = {}
    for acct in accounts:
        uuid = acct.get("AccountID")
        code = acct.get("Code")
        if uuid and code:
            lookup[uuid] = code
    return lookup
