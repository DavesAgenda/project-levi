"""Pydantic models for budget files, changelog entries, and status transitions.

These models validate the YAML budget structure and enforce business rules
like status transitions and null-vs-zero semantics.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Budget status enum with transition enforcement
# ---------------------------------------------------------------------------

class BudgetStatus(str, enum.Enum):
    draft = "draft"
    proposed = "proposed"
    approved = "approved"

    _ALLOWED_TRANSITIONS: dict[str, list[str]] = {  # type: ignore[assignment]
        "draft": ["proposed"],
        "proposed": ["approved", "draft"],  # draft allowed as explicit revert
        "approved": [],  # no transitions without override
    }

    def can_transition_to(self, target: BudgetStatus, *, override: bool = False) -> bool:
        if override:
            return True
        allowed = {"draft": ["proposed"], "proposed": ["approved", "draft"], "approved": []}
        return target.value in allowed[self.value]


# ---------------------------------------------------------------------------
# Budget section line items
# ---------------------------------------------------------------------------

class PropertyOverride(BaseModel):
    """Override for a single property in the budget."""
    weekly_rate: float | None = None
    vacancy_weeks: int | None = None


class BudgetSection(BaseModel, extra="allow"):
    """A single section within income or expenses.

    Known meta-keys (notes, overrides, vacancy_weeks) are extracted;
    all other keys are account_key -> amount (float | None).
    """
    notes: str | None = None
    overrides: dict[str, PropertyOverride] | None = None
    vacancy_weeks: dict[str, int] | None = None

    def account_items(self) -> dict[str, float | None]:
        """Return only the account line items (excluding meta keys)."""
        meta = {"notes", "overrides", "vacancy_weeks"}
        extras = self.model_extra or {}
        result: dict[str, float | None] = {}
        for k, v in extras.items():
            if k not in meta:
                result[k] = v
        return result


# ---------------------------------------------------------------------------
# Top-level budget file model
# ---------------------------------------------------------------------------

class BudgetFile(BaseModel):
    """Represents a complete budget YAML file.

    Null values are preserved (TBD items) vs zero (explicitly budgeted at zero).
    """
    year: int
    status: BudgetStatus = BudgetStatus.draft
    approved_date: date | None = None
    income: dict[str, BudgetSection] = Field(default_factory=dict)
    expenses: dict[str, BudgetSection] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_sections(cls, data: Any) -> Any:
        """Wrap plain dicts into BudgetSection models."""
        if isinstance(data, dict):
            for top_key in ("income", "expenses"):
                section = data.get(top_key)
                if isinstance(section, dict):
                    for k, v in section.items():
                        if v is None:
                            section[k] = {}
                        elif not isinstance(v, (dict, BudgetSection)):
                            section[k] = {}
        return data

    def all_account_codes(self) -> set[str]:
        """Extract every account code referenced in this budget."""
        codes: set[str] = set()
        for sections in (self.income, self.expenses):
            for section in sections.values():
                for key in section.account_items():
                    parts = key.split("_", 1)
                    if parts and parts[0].isdigit():
                        codes.add(parts[0])
        return codes


# ---------------------------------------------------------------------------
# Changelog entry
# ---------------------------------------------------------------------------

class ChangelogEntry(BaseModel):
    """A single append-only changelog entry."""
    timestamp: datetime
    action: str  # "create", "update", "status_change", "clone"
    user: str = "system"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None  # file version number at time of change
