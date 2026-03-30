"""Verify migrated snapshots in data/snapshots/.

Usage:
    python -m scripts.verify_migration [--snapshots-dir data/snapshots] [--chart config/chart_of_accounts.yaml]

Reads all JSON snapshot files and produces a summary table:
  - Year, total income, total expenses, net position
  - Flags unrecognised account codes or validation issues
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from app.csv_import import build_account_lookup, load_chart_of_accounts
from app.models import FinancialSnapshot


def load_snapshot(path: Path) -> FinancialSnapshot:
    """Load a FinancialSnapshot from a JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FinancialSnapshot(**raw)


def verify_snapshot(
    snapshot: FinancialSnapshot,
    account_lookup: dict,
) -> dict:
    """Verify a single snapshot and return a summary dict."""
    income = 0.0
    expenses = 0.0
    unrecognised: list[str] = []
    issues: list[str] = []
    legacy_count = 0

    for row in snapshot.rows:
        if row.account_code in account_lookup:
            cat_key, section, label, is_legacy = account_lookup[row.account_code]
            if section == "income":
                income += row.amount
            else:
                expenses += row.amount
            if is_legacy:
                legacy_count += 1
        else:
            unrecognised.append(f"{row.account_code} - {row.account_name}")
            # Still count toward totals for reporting
            if row.amount >= 0:
                income += row.amount
            else:
                expenses += abs(row.amount)

    # Validation checks
    if not snapshot.rows:
        issues.append("Snapshot has no rows")
    if income == 0.0:
        issues.append("Zero income — possibly incomplete data")
    if expenses == 0.0:
        issues.append("Zero expenses — possibly incomplete data")

    return {
        "from_date": snapshot.from_date,
        "to_date": snapshot.to_date,
        "source": snapshot.source,
        "row_count": len(snapshot.rows),
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "net_position": round(income - expenses, 2),
        "unrecognised": unrecognised,
        "legacy_count": legacy_count,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify migrated snapshot JSON files."
    )
    parser.add_argument(
        "--snapshots-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "snapshots",
        help="Directory containing snapshot JSON files (default: data/snapshots/)",
    )
    parser.add_argument(
        "--chart",
        type=Path,
        default=PROJECT_ROOT / "config" / "chart_of_accounts.yaml",
        help="Path to chart_of_accounts.yaml",
    )

    args = parser.parse_args(argv)

    if not args.snapshots_dir.is_dir():
        print(f"Error: snapshots directory does not exist: {args.snapshots_dir}")
        return 1
    if not args.chart.is_file():
        print(f"Error: chart of accounts not found: {args.chart}")
        return 1

    chart = load_chart_of_accounts(args.chart)
    account_lookup = build_account_lookup(chart)

    snapshot_files = sorted(args.snapshots_dir.glob("*.json"))
    if not snapshot_files:
        print(f"No JSON snapshot files found in {args.snapshots_dir}")
        return 1

    print(f"Found {len(snapshot_files)} snapshot(s) in {args.snapshots_dir}\n")

    all_results: list[dict] = []
    has_issues = False

    for path in snapshot_files:
        try:
            snapshot = load_snapshot(path)
        except Exception as e:
            print(f"ERROR loading {path.name}: {e}")
            has_issues = True
            continue

        result = verify_snapshot(snapshot, account_lookup)
        result["file"] = path.name
        all_results.append(result)

    # Print summary table
    print(f"{'File':<25} {'Period':<25} {'Rows':>5} {'Income':>14} {'Expenses':>14} {'Net':>14} {'Legacy':>7} {'Issues':<10}")
    print("-" * 120)

    for r in all_results:
        period = f"{r['from_date']} to {r['to_date']}"
        issue_flag = "YES" if r["issues"] or r["unrecognised"] else ""
        print(
            f"{r['file']:<25} {period:<25} "
            f"{r['row_count']:>5} "
            f"${r['income']:>12,.2f} "
            f"${r['expenses']:>12,.2f} "
            f"${r['net_position']:>12,.2f} "
            f"{r['legacy_count']:>7} "
            f"{issue_flag:<10}"
        )

    # Detail section for issues
    any_issues = False
    for r in all_results:
        if r["unrecognised"] or r["issues"]:
            if not any_issues:
                print("\n--- Issues ---")
                any_issues = True
                has_issues = True
            print(f"\n{r['file']}:")
            for issue in r["issues"]:
                print(f"  WARNING: {issue}")
            for acct in r["unrecognised"]:
                print(f"  UNRECOGNISED: {acct}")

    if not any_issues:
        print("\nAll snapshots passed verification.")

    # Grand totals
    if len(all_results) > 1:
        print("\n--- Grand Totals ---")
        total_income = sum(r["income"] for r in all_results)
        total_expenses = sum(r["expenses"] for r in all_results)
        print(f"  Total Income:   ${total_income:>12,.2f}")
        print(f"  Total Expenses: ${total_expenses:>12,.2f}")
        print(f"  Net Position:   ${total_income - total_expenses:>12,.2f}")

    return 1 if has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
