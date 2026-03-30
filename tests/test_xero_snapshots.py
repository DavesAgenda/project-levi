"""Unit tests for the Xero snapshot writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.xero.snapshots import (
    _build_filename,
    save_snapshot,
    save_pl_snapshot,
    save_tracking_categories_snapshot,
)


class TestBuildFilename:
    def test_pl_with_dates(self):
        assert _build_filename("pl", "2026-01-01", "2026-03-31") == "pl_2026-01-01_2026-03-31.json"

    def test_pl_with_suffix(self):
        result = _build_filename("pl", "2026-01-01", "2026-03-31", "by-ministry")
        assert result == "pl_2026-01-01_2026-03-31_by-ministry.json"

    def test_tracking_no_dates(self):
        assert _build_filename("tracking_categories") == "tracking_categories.json"

    def test_trial_balance_one_date(self):
        result = _build_filename("trial_balance", to_date="2026-03-31")
        assert result == "trial_balance_2026-03-31.json"


class TestSaveSnapshot:
    def test_creates_file(self, tmp_path: Path):
        data = {"Reports": [{"ReportID": "test"}]}
        path = save_snapshot(data, "pl", "2026-01-01", "2026-03-31", directory=tmp_path)

        assert path.exists()
        assert path.name == "pl_2026-01-01_2026-03-31.json"

        content = json.loads(path.read_text(encoding="utf-8"))
        assert "snapshot_metadata" in content
        assert content["snapshot_metadata"]["report_type"] == "pl"
        assert content["response"]["Reports"][0]["ReportID"] == "test"

    def test_creates_directory(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested"
        data = {"test": True}
        path = save_snapshot(data, "test", directory=nested)
        assert path.exists()
        assert nested.exists()

    def test_metadata_fields(self, tmp_path: Path):
        data = {"test": True}
        path = save_snapshot(data, "pl", "2026-01-01", "2026-03-31", directory=tmp_path)
        content = json.loads(path.read_text(encoding="utf-8"))
        meta = content["snapshot_metadata"]
        assert "saved_at" in meta
        assert meta["from_date"] == "2026-01-01"
        assert meta["to_date"] == "2026-03-31"
