"""Xero Budgets sync — pull the Xero-stored budget into a local overlay.

Uses the ``/Budgets`` endpoint (scope ``accounting.budgets.read``). Apps
created after 2026-03-02 cannot use the broad ``accounting.reports.read``
scope, so ``/Reports/BudgetSummary`` is not accessible — but ``/Budgets``
returns the same data keyed by ``AccountID`` and per-month balances.

Produces ``data/xero_budget_{year}.json`` mapping account code → annual budget
amount for the target year. This overlay is consumed by
``app.services.budget.load_budget_flat`` so figures missing (``null``) from
``budgets/{year}.yaml`` are backfilled from Xero.

The raw Xero response is also written to ``data/snapshots/budget_summary_{year}.json``
for audit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.xero.accounts import load_uuid_to_code
from app.xero.client import fetch_budget, fetch_budgets

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def _overlay_path(year: int, data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / f"xero_budget_{year}.json"


def _pick_budget_for_year(budgets: list[dict], year: int) -> dict | None:
    """Pick the best matching budget from the /Budgets list.

    Preference order:
    1. A budget whose description mentions the target year.
    2. Any OVERALL budget (most common — the default org budget).
    3. The first budget in the list.
    """
    year_str = str(year)
    for b in budgets:
        if year_str in (b.get("Description") or ""):
            return b
    for b in budgets:
        if (b.get("Type") or "").upper() == "OVERALL":
            return b
    return budgets[0] if budgets else None


def parse_budget(
    budget_detail: dict,
    year: int,
    uuid_to_code: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse a ``/Budgets/{id}`` response into ``{code: {name, amount}}``.

    Sums each line's ``BudgetBalances`` entries whose ``Period`` falls in the
    target year. Lines without a mappable account code (via ``AccountID`` →
    UUID lookup, or directly via ``AccountCode``) are skipped.
    """
    uuid_to_code = uuid_to_code or {}
    budgets = budget_detail.get("Budgets") or []
    if not budgets:
        return {}
    budget = budgets[0]

    year_str = str(year)
    result: dict[str, dict[str, Any]] = {}

    for line in budget.get("BudgetLines", []) or []:
        code = line.get("AccountCode")
        if not code:
            acct_id = line.get("AccountID")
            if acct_id and acct_id in uuid_to_code:
                code = uuid_to_code[acct_id]
        if not code:
            logger.debug(
                "Budget line with no mappable code: %r", line.get("AccountID"),
            )
            continue

        annual = 0.0
        for bal in line.get("BudgetBalances", []) or []:
            period = bal.get("Period") or ""
            if not period.startswith(year_str):
                continue
            amount = bal.get("Amount")
            if amount is None:
                continue
            try:
                annual += float(amount)
            except (TypeError, ValueError):
                continue

        name = line.get("AccountName") or line.get("Description") or ""
        if code in result:
            result[code]["amount"] += annual
        else:
            result[code] = {"name": name, "amount": annual}

    return result


async def sync_budget_from_xero(
    year: int,
    *,
    data_dir: Path | None = None,
    snapshots_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch Xero's budget for ``year`` and write the overlay file.

    Workflow:
    1. ``GET /Budgets`` — list budgets.
    2. Pick the best match for the target year.
    3. ``GET /Budgets/{id}?DateFrom=YYYY-01-01&DateTo=YYYY-12-31`` — line items.
    4. Sum per-account amounts for the year.
    5. Persist raw response + ``{code: {name, amount}}`` overlay.
    """
    ddir = data_dir or DATA_DIR
    sdir = snapshots_dir or SNAPSHOTS_DIR
    ddir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)

    list_response = await fetch_budgets()
    budgets = list_response.get("Budgets") or []
    picked = _pick_budget_for_year(budgets, year)
    if picked is None:
        raise RuntimeError(
            f"No budgets found in Xero org. Configure a budget in Xero "
            f"(Reports → Budget Manager) for {year} and try again."
        )

    budget_id = picked.get("BudgetID")
    if not budget_id:
        raise RuntimeError("Xero budget list response missing BudgetID")

    detail = await fetch_budget(
        budget_id,
        date_from=f"{year}-01-01",
        date_to=f"{year}-12-31",
    )

    snap_path = sdir / f"budget_summary_{year}.json"
    snap_path.write_text(
        json.dumps(
            {
                "snapshot_metadata": {
                    "report_type": "budget",
                    "year": year,
                    "budget_id": budget_id,
                    "budget_description": picked.get("Description") or "",
                    "fetched_at": datetime.utcnow().isoformat() + "Z",
                },
                "list_response": list_response,
                "detail_response": detail,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    uuid_to_code = load_uuid_to_code()
    by_code = parse_budget(detail, year, uuid_to_code)

    overlay_path = _overlay_path(year, data_dir=ddir)
    overlay_path.write_text(
        json.dumps(
            {
                "year": year,
                "budget_id": budget_id,
                "budget_description": picked.get("Description") or "",
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "accounts": by_code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Xero budget sync for %d: %d accounts, total $%.2f, written to %s",
        year,
        len(by_code),
        sum(v["amount"] for v in by_code.values()),
        overlay_path,
    )

    return {
        "year": year,
        "budget_id": budget_id,
        "budget_description": picked.get("Description") or "",
        "account_count": len(by_code),
        "total": round(sum(v["amount"] for v in by_code.values()), 2),
        "overlay_path": str(overlay_path),
        "snapshot_path": str(snap_path),
    }


def load_xero_budget_overlay(
    year: int,
    *,
    data_dir: Path | None = None,
) -> dict[str, float]:
    """Return ``{account_code: annual_amount}`` from the local overlay file.

    Empty dict if the overlay doesn't exist.
    """
    path = _overlay_path(year, data_dir=data_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read Xero budget overlay at %s", path)
        return {}
    return {
        code: float(entry.get("amount", 0.0))
        for code, entry in raw.get("accounts", {}).items()
    }
