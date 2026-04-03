"""Journal sync service — fetch and store Xero journals (CHA-263 + CHA-264).

Handles:
- Full journal sync: fetch all journals for a date range
- Incremental sync: resume from last known offset
- Storage in both JSON (structured) and LLM-friendly text format
- Sync state tracking (last offset, last sync timestamp)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.xero.client import fetch_journals, parse_journal_entries
from app.models.journal import JournalEntry

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
JOURNALS_DIR = DATA_DIR / "journals"
SYNC_STATE_FILE = JOURNALS_DIR / "_sync_state.json"


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def _ensure_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)


def _month_dir(year: int, month: int) -> Path:
    """Return path like data/journals/2026/2026-03/."""
    d = JOURNALS_DIR / str(year) / f"{year}-{month:02d}"
    _ensure_dir(d)
    return d


# ---------------------------------------------------------------------------
# Sync state persistence
# ---------------------------------------------------------------------------

def load_sync_state() -> dict[str, Any]:
    """Load the last sync state (offset, timestamp, count)."""
    if SYNC_STATE_FILE.exists():
        try:
            return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_offset": 0, "last_sync": None, "total_journals": 0}


def save_sync_state(state: dict[str, Any]) -> None:
    """Persist sync state."""
    _ensure_dir(JOURNALS_DIR)
    SYNC_STATE_FILE.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Journal storage — JSON (structured)
# ---------------------------------------------------------------------------

def save_journals_json(
    entries: list[JournalEntry],
    year: int,
    month: int,
) -> Path:
    """Save journal entries as a structured JSON file.

    File: data/journals/{year}/{year}-{month}/journals.json
    Contains all journal entries for that month, sorted by date.
    """
    out_dir = _month_dir(year, month)
    out_path = out_dir / "journals.json"

    data = [e.model_dump() for e in entries]
    out_path.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Saved %d journals to %s", len(entries), out_path)
    return out_path


# ---------------------------------------------------------------------------
# Journal storage — LLM-friendly text (CHA-264)
# ---------------------------------------------------------------------------

def _format_journal_text(entry: JournalEntry) -> str:
    """Format a single journal entry as human/LLM-readable text."""
    lines = []
    lines.append(f"=== Journal #{entry.journal_number} | {entry.journal_date} ===")
    if entry.reference:
        lines.append(f"  Ref: {entry.reference}")
    if entry.source_type:
        lines.append(f"  Source: {entry.source_type} ({entry.source_id})")

    for jl in entry.lines:
        amount_str = f"${abs(jl.net_amount):,.2f}"
        direction = "DR" if jl.net_amount >= 0 else "CR"
        line = f"  {direction} {amount_str:>12}  {jl.account_code} {jl.account_name}"
        if jl.description:
            line += f"  | {jl.description}"
        lines.append(line)

        if jl.tracking:
            tags = ", ".join(f"{t.tracking_category_name}: {t.option_name}" for t in jl.tracking)
            lines.append(f"                        [{tags}]")

    return "\n".join(lines)


def save_journals_text(
    entries: list[JournalEntry],
    year: int,
    month: int,
) -> Path:
    """Save journal entries as LLM-friendly plain text.

    File: data/journals/{year}/{year}-{month}/journals.txt

    Format designed for LLM interrogation:
    - Clear delimiters between entries
    - Account codes and names on every line
    - Tracking categories inline
    - DR/CR direction explicit
    - Human-readable amounts
    """
    out_dir = _month_dir(year, month)
    out_path = out_dir / "journals.txt"

    header = (
        f"# Journals for {year}-{month:02d}\n"
        f"# Generated: {datetime.now(timezone.utc).isoformat()}\n"
        f"# Count: {len(entries)}\n"
        f"#\n"
        f"# Format: DR/CR $Amount AccountCode AccountName | Description\n"
        f"#         [TrackingCategory: Option]\n\n"
    )

    text_entries = [_format_journal_text(e) for e in entries]
    content = header + "\n\n".join(text_entries) + "\n"

    out_path.write_text(content, encoding="utf-8")
    logger.info("Saved LLM-friendly journal text to %s", out_path)
    return out_path


def save_monthly_summary_text(
    entries: list[JournalEntry],
    year: int,
    month: int,
) -> Path:
    """Save a monthly account-level summary as LLM-friendly text.

    File: data/journals/{year}/{year}-{month}/summary.txt

    Aggregates all journal lines by account code, showing net totals.
    This is the file most useful for quick LLM queries about monthly totals.
    """
    out_dir = _month_dir(year, month)
    out_path = out_dir / "summary.txt"

    # Aggregate by account code
    account_totals: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for line in entry.lines:
            key = line.account_code
            if key not in account_totals:
                account_totals[key] = {
                    "code": line.account_code,
                    "name": line.account_name,
                    "type": line.account_type,
                    "net": 0.0,
                    "count": 0,
                }
            account_totals[key]["net"] += line.net_amount
            account_totals[key]["count"] += 1

    # Sort by account code
    sorted_accounts = sorted(account_totals.values(), key=lambda a: a["code"])

    lines = [
        f"# Monthly Summary: {year}-{month:02d}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        f"# Total journal entries: {len(entries)}",
        f"# Total accounts with activity: {len(sorted_accounts)}",
        "",
        f"{'Code':<8} {'Account Name':<40} {'Type':<12} {'Net Amount':>14} {'Txns':>6}",
        "-" * 84,
    ]

    for acct in sorted_accounts:
        lines.append(
            f"{acct['code']:<8} {acct['name']:<40} {acct['type']:<12} "
            f"${acct['net']:>12,.2f} {acct['count']:>6}"
        )

    # Section totals
    income_total = sum(a["net"] for a in sorted_accounts if a["type"] == "REVENUE")
    expense_total = sum(a["net"] for a in sorted_accounts if a["type"] in ("EXPENSE", "OVERHEADS", "DIRECTCOSTS"))

    lines.extend([
        "-" * 84,
        f"{'':8} {'Total Income':<40} {'':12} ${income_total:>12,.2f}",
        f"{'':8} {'Total Expenses':<40} {'':12} ${expense_total:>12,.2f}",
        f"{'':8} {'Net Position':<40} {'':12} ${income_total + expense_total:>12,.2f}",
    ])

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Saved monthly summary to %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------

def _group_by_month(entries: list[JournalEntry]) -> dict[tuple[int, int], list[JournalEntry]]:
    """Group journal entries by (year, month)."""
    groups: dict[tuple[int, int], list[JournalEntry]] = {}
    for entry in entries:
        try:
            d = date.fromisoformat(entry.journal_date)
            key = (d.year, d.month)
        except ValueError:
            continue
        groups.setdefault(key, []).append(entry)
    return groups


async def sync_journals(
    from_date: str | None = None,
    to_date: str | None = None,
    incremental: bool = True,
) -> dict[str, Any]:
    """Sync journals from Xero and save in both JSON and text formats.

    Args:
        from_date: Start date (YYYY-MM-DD). Defaults to Jan 1 of current year.
        to_date: End date (YYYY-MM-DD). Defaults to today.
        incremental: If True, resume from last known offset.

    Returns:
        {status, journal_count, files_written, duration_seconds, errors}
    """
    start_time = time.monotonic()
    today = date.today()
    errors: list[str] = []
    files_written: list[str] = []

    if from_date is None:
        from_date = date(today.year, 1, 1).isoformat()
    if to_date is None:
        to_date = today.isoformat()

    # Determine starting offset
    offset = 0
    if incremental:
        state = load_sync_state()
        offset = state.get("last_offset", 0)

    # Fetch from Xero
    try:
        raw_journals = await fetch_journals(
            from_date=from_date,
            to_date=to_date,
            offset=offset,
        )
    except Exception as exc:
        msg = f"Journal fetch failed: {exc}"
        logger.error("Journal sync: %s", msg)
        return {
            "status": "error",
            "journal_count": 0,
            "files_written": [],
            "errors": [msg],
            "duration_seconds": round(time.monotonic() - start_time, 2),
        }

    # Parse to models
    entries = parse_journal_entries(raw_journals)
    logger.info("Journal sync: fetched %d entries", len(entries))

    # Group by month and save
    by_month = _group_by_month(entries)
    for (year, month), month_entries in sorted(by_month.items()):
        month_entries.sort(key=lambda e: (e.journal_date, e.journal_number))

        try:
            json_path = save_journals_json(month_entries, year, month)
            files_written.append(str(json_path.relative_to(DATA_DIR)))

            text_path = save_journals_text(month_entries, year, month)
            files_written.append(str(text_path.relative_to(DATA_DIR)))

            summary_path = save_monthly_summary_text(month_entries, year, month)
            files_written.append(str(summary_path.relative_to(DATA_DIR)))
        except Exception as exc:
            msg = f"Save failed for {year}-{month:02d}: {exc}"
            logger.error("Journal sync: %s", msg)
            errors.append(msg)

    # Update sync state
    new_offset = offset
    if raw_journals:
        last_num = raw_journals[-1].get("JournalNumber", offset)
        new_offset = last_num

    save_sync_state({
        "last_offset": new_offset,
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "total_journals": len(entries),
        "from_date": from_date,
        "to_date": to_date,
    })

    duration = round(time.monotonic() - start_time, 2)
    status = "ok" if not errors else ("partial" if files_written else "error")

    return {
        "status": status,
        "journal_count": len(entries),
        "months": len(by_month),
        "files_written": files_written,
        "errors": errors,
        "duration_seconds": duration,
    }
