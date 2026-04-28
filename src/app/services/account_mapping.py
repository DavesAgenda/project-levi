"""Account mapping service — CRUD operations on chart_of_accounts.yaml (CHA-268).

Provides a programmatic interface for managing which Xero account codes
roll into which budget summary categories.  All mutations are atomic
(write-to-temp-then-rename) so the YAML is never left in a partial state.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import Account, BudgetCategory, ChartOfAccounts, FinancialSnapshot
from app.services.pl_helpers import infer_pl_section, is_summary_row


@dataclass
class UnmappedSnapshotAccount:
    """An account in the latest snapshot that isn't mapped to any category."""

    code: str
    name: str
    section: str  # "income" or "expenses"
    amount: float

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"

YAML_HEADER = (
    "# config/chart_of_accounts.yaml\n"
    "#\n"
    "# Based on Xero chart as of 2026 (post-cleanup).\n"
    "# Legacy/archived account codes are listed for historical import only.\n"
    "\n"
)


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def load_chart(path: Path | None = None) -> ChartOfAccounts:
    """Load chart of accounts from YAML."""
    return load_chart_of_accounts(path or CHART_PATH)


def save_chart(chart: ChartOfAccounts, path: Path | None = None) -> None:
    """Atomic write: serialise to temp file, then rename over target."""
    target = path or CHART_PATH

    # Build a plain dict suitable for YAML serialisation
    data: dict = {}
    for section_name in ("income", "expenses"):
        section = getattr(chart, section_name)
        section_dict: dict = {}
        for key, cat in section.items():
            cat_dict: dict = {"budget_label": cat.budget_label}
            if cat.accounts:
                cat_dict["accounts"] = [
                    {"code": a.code, "name": a.name} for a in cat.accounts
                ]
            if cat.legacy_accounts:
                cat_dict["legacy_accounts"] = [
                    {"code": a.code, "name": a.name} for a in cat.legacy_accounts
                ]
            if cat.property_costs:
                cat_dict["property_costs"] = [
                    {"code": a.code, "name": a.name} for a in cat.property_costs
                ]
            if cat.note:
                cat_dict["note"] = cat.note
            section_dict[key] = cat_dict
        data[section_name] = section_dict

    # Write to temp file in same directory, then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".yaml.tmp", prefix=".chart_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(YAML_HEADER)
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        os.replace(tmp_path, str(target))
    except Exception:
        # Clean up temp on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def _section_dict(chart: ChartOfAccounts, section: str) -> dict[str, BudgetCategory]:
    """Get income or expenses dict, raising ValueError for bad section."""
    if section == "income":
        return chart.income
    if section == "expenses":
        return chart.expenses
    raise ValueError(f"Invalid section: {section!r} (must be 'income' or 'expenses')")


def _all_codes(chart: ChartOfAccounts) -> set[str]:
    """Collect every account code across all categories."""
    codes: set[str] = set()
    for section_name in ("income", "expenses"):
        for cat in getattr(chart, section_name).values():
            for a in cat.accounts + cat.legacy_accounts + cat.property_costs:
                codes.add(a.code)
    return codes


def _category_to_dict(key: str, cat: BudgetCategory, section: str) -> dict:
    """Serialise a category for API / template consumption."""
    return {
        "key": key,
        "section": section,
        "budget_label": cat.budget_label,
        "note": cat.note,
        "accounts": [{"code": a.code, "name": a.name, "type": "current"} for a in cat.accounts],
        "legacy_accounts": [{"code": a.code, "name": a.name, "type": "legacy"} for a in cat.legacy_accounts],
        "property_costs": [{"code": a.code, "name": a.name, "type": "property"} for a in cat.property_costs],
        "total_accounts": len(cat.accounts) + len(cat.legacy_accounts) + len(cat.property_costs),
        "current_count": len(cat.accounts),
        "legacy_count": len(cat.legacy_accounts),
        "property_count": len(cat.property_costs),
    }


def list_categories(
    path: Path | None = None,
    section: str | None = None,
) -> dict[str, list[dict]]:
    """List all categories grouped by section.

    Returns ``{"income": [...], "expenses": [...]}`` where each entry
    is a dict with key, budget_label, accounts, counts, etc.
    """
    chart = load_chart(path)
    result: dict[str, list[dict]] = {}

    for section_name in ("income", "expenses"):
        if section and section != section_name:
            continue
        cats = getattr(chart, section_name)
        result[section_name] = [
            _category_to_dict(key, cat, section_name) for key, cat in cats.items()
        ]

    return result


def get_category(section: str, key: str, path: Path | None = None) -> dict:
    """Get a single category's full details."""
    chart = load_chart(path)
    cats = _section_dict(chart, section)
    if key not in cats:
        raise KeyError(f"Category {key!r} not found in {section}")
    return _category_to_dict(key, cats[key], section)


# ---------------------------------------------------------------------------
# Mutation operations
# ---------------------------------------------------------------------------


def _slugify(label: str) -> str:
    """Convert a budget label to a YAML-safe key."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "unnamed"


def create_category(
    section: str,
    budget_label: str,
    key: str | None = None,
    path: Path | None = None,
) -> dict:
    """Create a new empty category in the given section."""
    chart = load_chart(path)
    cats = _section_dict(chart, section)
    cat_key = key or _slugify(budget_label)

    if cat_key in cats:
        raise ValueError(f"Category key {cat_key!r} already exists in {section}")

    new_cat = BudgetCategory(budget_label=budget_label)
    cats[cat_key] = new_cat
    save_chart(chart, path)
    return _category_to_dict(cat_key, new_cat, section)


def rename_category(
    section: str,
    key: str,
    new_label: str,
    path: Path | None = None,
) -> None:
    """Update the budget_label for a category."""
    chart = load_chart(path)
    cats = _section_dict(chart, section)
    if key not in cats:
        raise KeyError(f"Category {key!r} not found in {section}")
    cats[key].budget_label = new_label
    save_chart(chart, path)


def delete_category(
    section: str,
    key: str,
    path: Path | None = None,
) -> None:
    """Delete a category. Raises ValueError if it still has accounts."""
    chart = load_chart(path)
    cats = _section_dict(chart, section)
    if key not in cats:
        raise KeyError(f"Category {key!r} not found in {section}")

    cat = cats[key]
    total = len(cat.accounts) + len(cat.legacy_accounts) + len(cat.property_costs)
    if total > 0:
        raise ValueError(
            f"Cannot delete category {key!r}: still has {total} account(s). "
            "Move or remove all accounts first."
        )

    del cats[key]
    save_chart(chart, path)


def add_account(
    section: str,
    category: str,
    code: str,
    name: str,
    *,
    is_legacy: bool = False,
    is_property: bool = False,
    path: Path | None = None,
) -> None:
    """Add an account to a category. Validates no duplicate codes."""
    chart = load_chart(path)
    existing = _all_codes(chart)
    if code in existing:
        raise ValueError(f"Account code {code!r} already exists in another category")

    cats = _section_dict(chart, section)
    if category not in cats:
        raise KeyError(f"Category {category!r} not found in {section}")

    acct = Account(code=code, name=name)
    cat = cats[category]
    if is_property:
        cat.property_costs.append(acct)
    elif is_legacy:
        cat.legacy_accounts.append(acct)
    else:
        cat.accounts.append(acct)

    save_chart(chart, path)


def remove_account(
    section: str,
    category: str,
    code: str,
    path: Path | None = None,
) -> None:
    """Remove an account from a category."""
    chart = load_chart(path)
    cats = _section_dict(chart, section)
    if category not in cats:
        raise KeyError(f"Category {category!r} not found in {section}")

    cat = cats[category]
    for lst in (cat.accounts, cat.legacy_accounts, cat.property_costs):
        for i, a in enumerate(lst):
            if a.code == code:
                lst.pop(i)
                save_chart(chart, path)
                return

    raise KeyError(f"Account code {code!r} not found in category {category!r}")


def move_account(
    from_section: str,
    from_category: str,
    to_section: str,
    to_category: str,
    code: str,
    *,
    target_list: str = "accounts",
    path: Path | None = None,
) -> None:
    """Move an account from one category to another.

    The account is removed from its current list and added to
    ``target_list`` ('accounts', 'legacy_accounts', or 'property_costs')
    in the destination category.
    """
    chart = load_chart(path)

    # Find and remove from source
    src_cats = _section_dict(chart, from_section)
    if from_category not in src_cats:
        raise KeyError(f"Source category {from_category!r} not found")

    src_cat = src_cats[from_category]
    found: Account | None = None
    for lst in (src_cat.accounts, src_cat.legacy_accounts, src_cat.property_costs):
        for i, a in enumerate(lst):
            if a.code == code:
                found = lst.pop(i)
                break
        if found:
            break

    if not found:
        raise KeyError(f"Account code {code!r} not found in {from_category!r}")

    # Add to destination
    dst_cats = _section_dict(chart, to_section)
    if to_category not in dst_cats:
        raise KeyError(f"Destination category {to_category!r} not found")

    dst_cat = dst_cats[to_category]
    target = getattr(dst_cat, target_list, None)
    if target is None:
        raise ValueError(f"Invalid target list: {target_list!r}")
    target.append(found)

    save_chart(chart, path)


def find_unmapped_accounts(
    known_codes: list[str],
    path: Path | None = None,
) -> list[str]:
    """Find account codes from ``known_codes`` not mapped in any category."""
    chart = load_chart(path)
    mapped = _all_codes(chart)
    return sorted(c for c in known_codes if c not in mapped)


def collect_unmapped_from_snapshot(
    snapshot: FinancialSnapshot | None,
    chart: ChartOfAccounts | None = None,
) -> list[UnmappedSnapshotAccount]:
    """Return accounts in the snapshot that aren't mapped to any category.

    Mirrors the dashboard's unmapped-detection logic so the mapping admin
    page surfaces the same accounts that appear as "Uncategorised" in P&L
    views. Sorted by section (income first) then by amount desc.
    """
    if snapshot is None:
        return []
    if chart is None:
        chart = load_chart()

    account_lookup = build_account_lookup(chart)
    result: list[UnmappedSnapshotAccount] = []
    for row in snapshot.rows:
        if is_summary_row(row):
            continue
        if row.account_code in account_lookup:
            continue
        if row.amount == 0:
            continue
        section = infer_pl_section(row.account_code or "", row.account_name)
        result.append(UnmappedSnapshotAccount(
            code=row.account_code or "",
            name=row.account_name,
            section=section,
            amount=round(row.amount, 2),
        ))

    result.sort(key=lambda u: (0 if u.section == "income" else 1, -abs(u.amount)))
    return result
