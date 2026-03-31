"""Pydantic models for historical data verification (CHA-206).

Compares CSV-imported actuals against Xero API snapshots,
returning account-by-account comparison results.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MatchStatus(str, Enum):
    """Colour-coded match status for a single account comparison."""

    MATCH = "match"              # green  — amounts identical (within $10)
    MINOR_VARIANCE = "minor"     # yellow — variance $10–$100
    MAJOR_VARIANCE = "major"     # red    — variance > $100
    CSV_ONLY = "csv_only"        # account exists only in CSV
    SNAPSHOT_ONLY = "snapshot_only"  # account exists only in snapshot


class AccountComparison(BaseModel):
    """Comparison result for a single account code."""

    account_code: str
    account_name: str
    csv_amount: float | None = None
    snapshot_amount: float | None = None
    variance: float = 0.0
    abs_variance: float = 0.0
    status: MatchStatus

    @property
    def css_class(self) -> str:
        """Return a Tailwind background class for the status."""
        return {
            MatchStatus.MATCH: "bg-green-50",
            MatchStatus.MINOR_VARIANCE: "bg-yellow-50",
            MatchStatus.MAJOR_VARIANCE: "bg-red-50",
            MatchStatus.CSV_ONLY: "bg-orange-50",
            MatchStatus.SNAPSHOT_ONLY: "bg-blue-50",
        }[self.status]

    @property
    def status_label(self) -> str:
        """Human-readable status label."""
        return {
            MatchStatus.MATCH: "Match",
            MatchStatus.MINOR_VARIANCE: "Minor Variance",
            MatchStatus.MAJOR_VARIANCE: "Major Variance",
            MatchStatus.CSV_ONLY: "CSV Only",
            MatchStatus.SNAPSHOT_ONLY: "Snapshot Only",
        }[self.status]


class VerificationResult(BaseModel):
    """Complete verification result for a single year."""

    year: int
    csv_source: str = ""          # filename or description of CSV data
    snapshot_source: str = ""     # filename or description of snapshot data
    comparisons: list[AccountComparison] = Field(default_factory=list)
    has_csv_data: bool = False
    has_snapshot_data: bool = False

    @property
    def matches(self) -> list[AccountComparison]:
        return [c for c in self.comparisons if c.status == MatchStatus.MATCH]

    @property
    def minor_variances(self) -> list[AccountComparison]:
        return [c for c in self.comparisons if c.status == MatchStatus.MINOR_VARIANCE]

    @property
    def major_variances(self) -> list[AccountComparison]:
        return [c for c in self.comparisons if c.status == MatchStatus.MAJOR_VARIANCE]

    @property
    def csv_only(self) -> list[AccountComparison]:
        return [c for c in self.comparisons if c.status == MatchStatus.CSV_ONLY]

    @property
    def snapshot_only(self) -> list[AccountComparison]:
        return [c for c in self.comparisons if c.status == MatchStatus.SNAPSHOT_ONLY]

    @property
    def total_accounts(self) -> int:
        return len(self.comparisons)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def match_percentage(self) -> float:
        if self.total_accounts == 0:
            return 0.0
        return round(self.match_count / self.total_accounts * 100, 1)

    @property
    def total_discrepancy(self) -> float:
        return round(sum(c.abs_variance for c in self.comparisons), 2)
