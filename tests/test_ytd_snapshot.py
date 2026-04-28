"""Tests for load_ytd_snapshot — specifically that it does not double-count
overlapping snapshots or pull in tracking-split / multi-month files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.dashboard import load_ytd_snapshot


def _write_snapshot(
    dir_: Path,
    from_date: str,
    to_date: str,
    rows: list[tuple[str, str, float]],
    suffix: str = "",
) -> Path:
    name = f"pl_{from_date}_{to_date}"
    if suffix:
        name += f"_{suffix}"
    name += ".json"
    path = dir_ / name
    payload = {
        "report_date": to_date,
        "from_date": from_date,
        "to_date": to_date,
        "source": "xero_api",
        "rows": [
            {"account_code": c, "account_name": n, "amount": a} for c, n, a in rows
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadYtdSnapshot:
    def test_single_month_roundtrip(self, tmp_path: Path):
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-01-31",
            [("41517", "Bank Fees", 100.0)],
        )
        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=1)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        assert totals["41517"] == 100.0

    def test_overlapping_partial_month_snapshots_not_double_counted(
        self, tmp_path: Path
    ):
        """If April has several partial snapshots (04-03, 04-07, 04-20), only
        the latest should contribute — not the sum of all three."""
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-01-31",
            [("41517", "Bank Fees", 100.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-03",
            [("41517", "Bank Fees", 40.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-07",
            [("41517", "Bank Fees", 60.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-20",
            [("41517", "Bank Fees", 200.0)],
        )

        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=4)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        # Jan (100) + best April snap (200) = 300, NOT 100+40+60+200=400
        assert totals["41517"] == 300.0
        assert snap.to_date == "2026-04-20"

    def test_tracking_split_snapshots_excluded(self, tmp_path: Path):
        """_by-ministry-funds etc. snapshots must not be included in YTD sum."""
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-20",
            [("41517", "Bank Fees", 200.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-04-20",
            [("41517", "Bank Fees", 9999.0)],
            suffix="by-ministry-funds",
        )
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-04-20",
            [("41517", "Bank Fees", 9999.0)],
            suffix="by-congregations",
        )

        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=4)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        assert totals["41517"] == 200.0

    def test_multimonth_range_files_excluded(self, tmp_path: Path):
        """A YTD-range file (Jan 1 → Apr 20) sitting alongside monthly files
        should not be double-counted on top of the monthlies."""
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-01-31",
            [("41517", "Bank Fees", 100.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-20",
            [("41517", "Bank Fees", 200.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-04-20",
            [("41517", "Bank Fees", 300.0)],  # overlapping YTD file
        )

        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=4)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        assert totals["41517"] == 300.0  # Jan(100) + April(200)

    def test_end_month_cutoff(self, tmp_path: Path):
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-01-31",
            [("41517", "Bank Fees", 100.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-04-01",
            "2026-04-20",
            [("41517", "Bank Fees", 200.0)],
        )

        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=2)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        assert totals["41517"] == 100.0

    def test_other_year_ignored(self, tmp_path: Path):
        _write_snapshot(
            tmp_path,
            "2025-01-01",
            "2025-01-31",
            [("41517", "Bank Fees", 5000.0)],
        )
        _write_snapshot(
            tmp_path,
            "2026-01-01",
            "2026-01-31",
            [("41517", "Bank Fees", 100.0)],
        )
        snap = load_ytd_snapshot(year=2026, directory=tmp_path, end_month=12)
        assert snap is not None
        totals = {r.account_code: r.amount for r in snap.rows}
        assert totals["41517"] == 100.0

    def test_empty_directory_returns_none(self, tmp_path: Path):
        assert load_ytd_snapshot(year=2026, directory=tmp_path) is None
