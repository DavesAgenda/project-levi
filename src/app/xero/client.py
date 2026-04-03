"""Xero API client — fetches reports from the Xero Accounting API.

Uses httpx with automatic token acquisition, exponential backoff retry for
rate limits (429) and transient errors.

Web Apps require the ``xero-tenant-id`` header on every API call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.xero.oauth import clear_tokens, get_valid_access_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XERO_API_BASE = "https://api.xero.com/api.xro/2.0"

# Retry config
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
BACKOFF_MULTIPLIER = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Core HTTP helper with retry
# ---------------------------------------------------------------------------

async def _xero_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict:
    """Make an authenticated Xero API request with retry logic.

    Handles:
    - Automatic token acquisition (via get_valid_access_token)
    - Exponential backoff for 429 and 5xx errors
    - 401 retry with fresh token (once)

    Web Apps require the ``xero-tenant-id`` header to identify the org.
    """
    access_token, tenant_id = await get_valid_access_token()
    url = f"{XERO_API_BASE}{path}"

    backoff = INITIAL_BACKOFF
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "xero-tenant-id": tenant_id,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method, url, headers=headers, params=params,
                )

                if response.status_code == 401 and attempt == 0:
                    # Token may have just expired — clear cache and get fresh one
                    logger.info("Xero 401 — clearing cached token and retrying")
                    clear_tokens()
                    access_token, tenant_id = await get_valid_access_token()
                    continue

                if response.status_code in RETRYABLE_STATUS_CODES:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else backoff
                    logger.warning(
                        "Xero %d on %s — retrying in %.1fs (attempt %d/%d)",
                        response.status_code, path, wait, attempt + 1, MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    backoff *= BACKOFF_MULTIPLIER
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    continue

                response.raise_for_status()
                return response.json()

        except httpx.TimeoutException as exc:
            logger.warning(
                "Xero timeout on %s — retrying (attempt %d/%d)",
                path, attempt + 1, MAX_RETRIES,
            )
            last_exc = exc
            await asyncio.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
            continue

    raise last_exc or RuntimeError(f"Xero request failed after {MAX_RETRIES} retries")


# ---------------------------------------------------------------------------
# Report endpoints
# ---------------------------------------------------------------------------

async def fetch_profit_and_loss(
    from_date: str,
    to_date: str,
    *,
    periods: int | None = None,
    timeframe: str | None = None,
    tracking_category_id: str | None = None,
    tracking_option_id: str | None = None,
    standard_layout: bool = True,
    payments_only: bool = False,
) -> dict:
    """Fetch a Profit & Loss report from Xero.

    Args:
        from_date: Report start date (YYYY-MM-DD).
        to_date: Report end date (YYYY-MM-DD).
        periods: Number of comparison periods (e.g., 11 for 12 months).
        timeframe: MONTH, QUARTER, or YEAR.
        tracking_category_id: UUID for tracking breakdown columns.
        tracking_option_id: UUID to filter to one tracking option.
        standard_layout: True for standard layout.
        payments_only: True for cash basis, False for accrual.

    Returns:
        Raw Xero API JSON response.
    """
    params: dict[str, Any] = {
        "fromDate": from_date,
        "toDate": to_date,
        "standardLayout": str(standard_layout).lower(),
        "paymentsOnly": str(payments_only).lower(),
    }
    if periods is not None:
        params["periods"] = periods
    if timeframe is not None:
        params["timeframe"] = timeframe
    if tracking_category_id is not None:
        params["trackingCategoryID"] = tracking_category_id
    if tracking_option_id is not None:
        params["trackingOptionID"] = tracking_option_id

    return await _xero_request("GET", "/Reports/ProfitAndLoss", params=params)


async def fetch_trial_balance(date: str | None = None) -> dict:
    """Fetch a Trial Balance report from Xero.

    Args:
        date: Report date (YYYY-MM-DD). Defaults to today if omitted.
    """
    params: dict[str, Any] = {}
    if date is not None:
        params["date"] = date
    return await _xero_request("GET", "/Reports/TrialBalance", params=params)


async def fetch_balance_sheet(date: str | None = None) -> dict:
    """Fetch a Balance Sheet report from Xero.

    Args:
        date: Report date (YYYY-MM-DD). Defaults to today if omitted.
    """
    params: dict[str, Any] = {}
    if date is not None:
        params["date"] = date
    return await _xero_request("GET", "/Reports/BalanceSheet", params=params)


async def fetch_journals(
    from_date: str | None = None,
    to_date: str | None = None,
    offset: int = 0,
) -> list[dict]:
    """Fetch all journals from Xero with offset-based pagination.

    Xero returns max 100 journals per request.  We keep fetching until
    a page returns fewer than 100 entries, then return the complete list.

    Args:
        from_date: Optional ISO date — exclude journals before this date.
        to_date: Optional ISO date — exclude journals after this date.
        offset: Starting offset (for resumable sync).

    Returns:
        List of raw Xero journal dicts (use ``parse_journal_entries``
        to convert to Pydantic models).
    """
    all_journals: list[dict] = []
    page_size = 100

    while True:
        data = await _xero_request(
            "GET", "/Journals", params={"offset": offset},
        )
        page = data.get("Journals", [])
        if not page:
            break

        # Client-side date filtering
        for journal in page:
            jdate = journal.get("JournalDate", "")[:10]  # "YYYY-MM-DDT..." → "YYYY-MM-DD"
            if from_date and jdate < from_date:
                continue
            if to_date and jdate > to_date:
                continue
            all_journals.append(journal)

        if len(page) < page_size:
            break  # last page

        # Xero offset = JournalNumber of last entry (not a simple +100)
        offset = page[-1].get("JournalNumber", offset + page_size)

    logger.info("Fetched %d journals from Xero (offset started at %d)", len(all_journals), offset)
    return all_journals


def parse_journal_entries(raw_journals: list[dict]) -> list:
    """Convert raw Xero journal dicts to JournalEntry Pydantic models.

    Imported here to avoid circular imports at module level.
    """
    from app.models.journal import JournalEntry, JournalLine, TrackingTag

    entries = []
    for j in raw_journals:
        lines = []
        for jl in j.get("JournalLines", []):
            tracking = [
                TrackingTag(
                    tracking_category_id=tc.get("TrackingCategoryID", ""),
                    tracking_category_name=tc.get("Name", ""),
                    option_id=tc.get("Option", ""),
                    option_name=tc.get("Option", ""),
                )
                for tc in jl.get("TrackingCategories", [])
            ]
            lines.append(JournalLine(
                journal_line_id=jl.get("JournalLineID", ""),
                account_id=jl.get("AccountID", ""),
                account_code=jl.get("AccountCode", ""),
                account_name=jl.get("AccountName", ""),
                account_type=jl.get("AccountType", ""),
                net_amount=float(jl.get("NetAmount", 0)),
                gross_amount=float(jl.get("GrossAmount", 0)),
                tax_amount=float(jl.get("TaxAmount", 0)),
                description=jl.get("Description", "") or "",
                tracking=tracking,
            ))

        entries.append(JournalEntry(
            journal_id=j.get("JournalID", ""),
            journal_number=str(j.get("JournalNumber", "")),
            journal_date=j.get("JournalDate", "")[:10],
            source_id=j.get("SourceID", "") or "",
            source_type=j.get("SourceType", "") or "",
            reference=j.get("Reference", "") or "",
            lines=lines,
            created_date_utc=j.get("CreatedDateUTC", "") or "",
        ))

    return entries


async def fetch_tracking_categories() -> dict:
    """Fetch all tracking categories and their options from Xero.

    Returns the raw API response containing tracking category UUIDs
    needed for P&L breakdown by ministry activity.
    """
    return await _xero_request("GET", "/TrackingCategories")
