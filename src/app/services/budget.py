"""Budget data service — load, save, validate, and version budget YAML files.

This is the M3 foundation service. It handles:
- Loading/saving budget YAML with Pydantic validation
- Status transitions (draft -> proposed -> approved)
- Append-only changelog per budget year
- Prior-version archival to budgets/history/
- Optimistic concurrency via file mtime comparison
- Property income computation from properties.yaml
- Payroll budget computation from payroll.yaml
- Validation against chart_of_accounts.yaml
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import ChartOfAccounts
from app.models.budget import (
    BudgetFile,
    BudgetSection,
    BudgetStatus,
    ChangelogEntry,
    PropertyOverride,
)

# ---------------------------------------------------------------------------
# Project paths (overridable for testing)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BUDGETS_DIR = PROJECT_ROOT / "budgets"
CONFIG_DIR = PROJECT_ROOT / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"
PROPERTIES_PATH = CONFIG_DIR / "properties.yaml"
PAYROLL_PATH = CONFIG_DIR / "payroll.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BudgetServiceError(Exception):
    """Base exception for budget service errors."""


class BudgetNotFoundError(BudgetServiceError):
    """Raised when a budget file does not exist."""


class BudgetConcurrencyError(BudgetServiceError):
    """Raised when the budget file was modified since it was loaded."""


class BudgetValidationError(BudgetServiceError):
    """Raised when budget validation fails."""

    def __init__(self, message: str, invalid_codes: list[str] | None = None):
        super().__init__(message)
        self.invalid_codes = invalid_codes or []


class BudgetStatusError(BudgetServiceError):
    """Raised when a status transition is not allowed."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_budget_file(
    year: int,
    *,
    budgets_dir: Path | None = None,
) -> BudgetFile:
    """Load and parse a budget YAML file, returning the full structured model.

    Returns BudgetFile with all sections, null values preserved.
    Raises BudgetNotFoundError if the file doesn't exist.
    """
    if not (2000 <= year <= 2100):
        raise BudgetValidationError(f"Year {year} is out of valid range (2000-2100)")
    bdir = budgets_dir or BUDGETS_DIR
    path = bdir / f"{year}.yaml"
    # Ensure resolved path stays within budgets directory
    if not path.resolve().is_relative_to(bdir.resolve()):
        raise BudgetValidationError(f"Invalid budget path for year {year}")
    if not path.exists():
        raise BudgetNotFoundError(f"No budget file for year {year}: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise BudgetNotFoundError(f"Budget file is empty: {path}")

    return BudgetFile(**raw)


def get_budget_mtime(year: int, *, budgets_dir: Path | None = None) -> float:
    """Return the mtime of the budget file for optimistic concurrency."""
    bdir = budgets_dir or BUDGETS_DIR
    path = bdir / f"{year}.yaml"
    if not path.exists():
        raise BudgetNotFoundError(f"No budget file for year {year}")
    return path.stat().st_mtime


# ---------------------------------------------------------------------------
# Saving with versioning
# ---------------------------------------------------------------------------

def _next_version_number(year: int, history_dir: Path) -> int:
    """Determine the next version number for history files."""
    if not history_dir.exists():
        return 1
    existing = list(history_dir.glob(f"{year}_v*.yaml"))
    if not existing:
        return 1
    nums = []
    for p in existing:
        stem = p.stem  # e.g. "2026_v3"
        parts = stem.split("_v")
        if len(parts) == 2 and parts[1].isdigit():
            nums.append(int(parts[1]))
    return max(nums, default=0) + 1


def save_budget_file(
    budget: BudgetFile,
    *,
    budgets_dir: Path | None = None,
    expected_mtime: float | None = None,
    user: str = "system",
    summary: str = "",
) -> int:
    """Write budget YAML to disk with versioning and changelog.

    1. If expected_mtime is provided, checks optimistic concurrency.
    2. Archives prior version to budgets/history/{year}_v{n}.yaml.
    3. Writes the new YAML.
    4. Appends a changelog entry.

    Returns the version number of the archived file (0 if new file).
    """
    if not (2000 <= budget.year <= 2100):
        raise BudgetValidationError(f"Year {budget.year} is out of valid range (2000-2100)")
    bdir = budgets_dir or BUDGETS_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    path = bdir / f"{budget.year}.yaml"
    # Ensure resolved path stays within budgets directory
    if not path.resolve().is_relative_to(bdir.resolve()):
        raise BudgetValidationError(f"Invalid budget path for year {budget.year}")
    history_dir = bdir / "history"

    version = 0

    # Optimistic concurrency check
    if expected_mtime is not None and path.exists():
        actual_mtime = path.stat().st_mtime
        if abs(actual_mtime - expected_mtime) > 0.001:
            raise BudgetConcurrencyError(
                f"Budget {budget.year} was modified since load "
                f"(expected mtime {expected_mtime}, actual {actual_mtime})"
            )

    # Archive prior version
    if path.exists():
        history_dir.mkdir(parents=True, exist_ok=True)
        version = _next_version_number(budget.year, history_dir)
        archive_path = history_dir / f"{budget.year}_v{version}.yaml"
        shutil.copy2(path, archive_path)

    # Serialize — preserve None values as YAML null
    data = _budget_to_dict(budget)
    yaml_text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    path.write_text(yaml_text, encoding="utf-8")

    # Changelog
    _append_changelog(
        budget.year,
        action="update" if version > 0 else "create",
        user=user,
        summary=summary or f"Budget {budget.year} saved",
        version=version,
        budgets_dir=bdir,
    )

    return version


def _budget_to_dict(budget: BudgetFile) -> dict:
    """Convert BudgetFile to a dict suitable for YAML serialization.

    Preserves None values (they become YAML null).
    """
    d: dict = {"year": budget.year, "status": budget.status.value}
    if budget.approved_date:
        d["approved_date"] = budget.approved_date.isoformat()

    for top_key in ("income", "expenses"):
        sections: dict[str, BudgetSection] = getattr(budget, top_key)
        d[top_key] = {}
        for section_key, section in sections.items():
            sec_dict: dict = {}
            # Account items (preserving None)
            for k, v in section.account_items().items():
                sec_dict[k] = v
            # Meta fields
            if section.notes is not None:
                sec_dict["notes"] = section.notes
            if section.overrides is not None:
                sec_dict["overrides"] = {
                    pk: pv.model_dump(exclude_none=False)
                    for pk, pv in section.overrides.items()
                }
            if section.vacancy_weeks is not None:
                sec_dict["vacancy_weeks"] = section.vacancy_weeks
            d[top_key][section_key] = sec_dict

    return d


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------

def _changelog_path(year: int, budgets_dir: Path) -> Path:
    return budgets_dir / f"{year}.changelog.json"


def _append_changelog(
    year: int,
    *,
    action: str,
    user: str,
    summary: str,
    version: int | None = None,
    details: dict | None = None,
    budgets_dir: Path | None = None,
) -> ChangelogEntry:
    bdir = budgets_dir or BUDGETS_DIR
    path = _changelog_path(year, bdir)

    entries: list[dict] = []
    if path.exists():
        entries = json.loads(path.read_text(encoding="utf-8"))

    entry = ChangelogEntry(
        timestamp=datetime.now(tz=__import__("datetime").timezone.utc),
        action=action,
        user=user,
        summary=summary,
        version=version,
        details=details or {},
    )
    entries.append(entry.model_dump(mode="json"))
    path.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
    return entry


def load_changelog(year: int, *, budgets_dir: Path | None = None) -> list[ChangelogEntry]:
    """Load all changelog entries for a budget year."""
    bdir = budgets_dir or BUDGETS_DIR
    path = _changelog_path(year, bdir)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ChangelogEntry(**e) for e in raw]


# ---------------------------------------------------------------------------
# Draft creation (clone prior year)
# ---------------------------------------------------------------------------

def create_draft_budget(
    year: int,
    base_year: int | None = None,
    *,
    budgets_dir: Path | None = None,
    user: str = "system",
) -> BudgetFile:
    """Create a new draft budget, optionally cloning from a base year.

    If base_year is provided, clones its structure with status=draft.
    If base_year is None, creates a minimal empty draft.
    Saves immediately to disk.
    """
    bdir = budgets_dir or BUDGETS_DIR

    # Check target doesn't already exist
    target = bdir / f"{year}.yaml"
    if target.exists():
        raise BudgetServiceError(f"Budget for {year} already exists")

    if base_year is not None:
        base = load_budget_file(base_year, budgets_dir=bdir)
        # Clone: reset status and approved_date
        data = _budget_to_dict(base)
        data["year"] = year
        data["status"] = "draft"
        data.pop("approved_date", None)
        budget = BudgetFile(**data)
    else:
        budget = BudgetFile(year=year, status=BudgetStatus.draft)

    save_budget_file(budget, budgets_dir=bdir, user=user, summary=f"Draft budget {year} created")

    _append_changelog(
        year,
        action="clone" if base_year else "create",
        user=user,
        summary=f"Created draft for {year}" + (f" from {base_year}" if base_year else ""),
        budgets_dir=bdir,
    )

    return budget


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_budget(
    budget: BudgetFile,
    *,
    chart: ChartOfAccounts | None = None,
    chart_path: Path | None = None,
) -> list[str]:
    """Validate that all account codes in the budget exist in chart_of_accounts.

    Returns a list of invalid/unrecognised account codes (empty = valid).
    Raises BudgetValidationError if there are invalid codes.
    """
    if chart is None:
        cp = chart_path or CHART_PATH
        if not cp.exists():
            raise BudgetServiceError(f"Chart of accounts not found: {cp}")
        chart = load_chart_of_accounts(cp)

    account_lookup = build_account_lookup(chart)
    budget_codes = budget.all_account_codes()
    invalid = sorted(code for code in budget_codes if code not in account_lookup)

    if invalid:
        raise BudgetValidationError(
            f"Budget references {len(invalid)} unrecognised account code(s): {', '.join(invalid)}",
            invalid_codes=invalid,
        )

    return []


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def transition_status(
    budget: BudgetFile,
    target: BudgetStatus,
    *,
    override: bool = False,
    budgets_dir: Path | None = None,
    expected_mtime: float | None = None,
    user: str = "system",
) -> BudgetFile:
    """Transition budget status with enforcement.

    draft -> proposed -> approved (no skipping, no reversal without override).
    Saves the updated budget to disk.
    """
    if not budget.status.can_transition_to(target, override=override):
        raise BudgetStatusError(
            f"Cannot transition from {budget.status.value} to {target.value}"
            + (" (use override=True to force)" if not override else "")
        )

    old_status = budget.status
    budget.status = target
    if target == BudgetStatus.approved:
        from datetime import date as _date
        budget.approved_date = _date.today()

    save_budget_file(
        budget,
        budgets_dir=budgets_dir,
        expected_mtime=expected_mtime,
        user=user,
        summary=f"Status: {old_status.value} -> {target.value}",
    )

    _append_changelog(
        budget.year,
        action="status_change",
        user=user,
        summary=f"Status changed: {old_status.value} -> {target.value}",
        details={"from": old_status.value, "to": target.value},
        budgets_dir=budgets_dir,
    )

    return budget


# ---------------------------------------------------------------------------
# Computed budget helpers
# ---------------------------------------------------------------------------

def compute_property_income(
    budget: BudgetFile | None = None,
    *,
    properties_path: Path | None = None,
) -> dict[str, float]:
    """Compute property income from properties.yaml, with budget overrides.

    Returns {property_key: annual_net_income}.
    Formula: weekly_rate * weeks_per_year * (1 - management_fee_pct)
    """
    pp = properties_path or PROPERTIES_PATH
    if not pp.exists():
        return {}

    raw = yaml.safe_load(pp.read_text(encoding="utf-8"))
    properties = raw.get("properties", {})

    # Extract overrides from budget
    overrides: dict[str, PropertyOverride] = {}
    if budget and "property_income" in budget.income:
        section = budget.income["property_income"]
        if section.overrides:
            overrides = section.overrides

    result: dict[str, float] = {}
    for key, prop in properties.items():
        weekly = prop.get("weekly_rate", 0)
        weeks = prop.get("weeks_per_year", 48)
        fee_pct = prop.get("management_fee_pct", 0)

        # Apply overrides
        if key in overrides:
            ov = overrides[key]
            if ov.weekly_rate is not None:
                weekly = ov.weekly_rate

        annual = weekly * weeks * (1 - fee_pct)
        result[key] = round(annual, 2)

    return result


def compute_payroll_budget(
    *,
    payroll_path: Path | None = None,
) -> dict[str, float]:
    """Compute payroll budget from payroll.yaml.

    Returns summary dict with per-staff and total costs.
    """
    pp = payroll_path or PAYROLL_PATH
    if not pp.exists():
        return {}

    raw = yaml.safe_load(pp.read_text(encoding="utf-8"))
    staff_list = raw.get("staff", [])

    result: dict[str, float] = {}
    total = 0.0

    for person in staff_list:
        name = person.get("name", "Unknown")
        base = person.get("base_salary", 0)
        super_rate = person.get("super_rate", 0)
        pcr = person.get("pcr", 0)
        travel = person.get("fixed_travel", 0)
        workers_comp = person.get("workers_comp", 0)
        recoveries_total = sum(r.get("amount", 0) for r in person.get("recoveries", []))

        cost = base + (base * super_rate) + pcr + travel + workers_comp + recoveries_total
        result[name] = round(cost, 2)
        total += cost

    result["_total"] = round(total, 2)
    return result


# ---------------------------------------------------------------------------
# Flattened budget loader (backwards-compatible with dashboard.py)
# ---------------------------------------------------------------------------

def load_budget_flat(
    year: int = 2026,
    *,
    chart: ChartOfAccounts | None = None,
    budgets_dir: Path | None = None,
    chart_path: Path | None = None,
) -> dict[str, float]:
    """Load budget and return flattened {category_key: amount} dict.

    This replaces the old ``dashboard.load_budget()`` function.
    """
    bdir = budgets_dir or BUDGETS_DIR
    cp = chart_path or CHART_PATH

    budget_path = bdir / f"{year}.yaml"
    if not budget_path.exists():
        return {}

    raw = yaml.safe_load(budget_path.read_text(encoding="utf-8")) or {}

    if chart is None:
        if not cp.exists():
            return {}
        chart = load_chart_of_accounts(cp)

    account_lookup = build_account_lookup(chart)

    category_budgets: dict[str, float] = {}
    for section_name in ("income", "expenses"):
        section_data = raw.get(section_name, {})
        if not isinstance(section_data, dict):
            continue
        for group_key, group_val in section_data.items():
            if not isinstance(group_val, dict):
                continue
            for item_key, amount in group_val.items():
                if item_key in ("notes", "overrides", "vacancy_weeks"):
                    continue
                if amount is None:
                    continue
                parts = item_key.split("_", 1)
                code = parts[0] if parts and parts[0].isdigit() else None
                if code and code in account_lookup:
                    cat_key = account_lookup[code][0]
                    category_budgets[cat_key] = category_budgets.get(cat_key, 0) + float(amount)

    return category_budgets
