"""Migrate historical CSV data (2020-2024) into FinancialSnapshot JSON files.

Usage:
    python -m scripts.migrate_historical [--input-dir data/historical] [--output-dir data/snapshots] [--strict]

Reads all CSV files from the input directory, processes each through the
CSV import engine, and saves the resulting FinancialSnapshot JSON files.
Generates a migration report summarising what mapped, what didn't, and
totals verification.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# Ensure src/ is importable when run from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from app.csv_import import import_csv, load_chart_of_accounts, to_snapshot
from app.models import FinancialSnapshot, ImportResult


# ---------------------------------------------------------------------------
# Year detection
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(20[12]\d)")


def detect_year(filename: str) -> int | None:
    """Try to extract a 4-digit year (2020-2029) from a filename."""
    m = _YEAR_RE.search(filename)
    return int(m.group(1)) if m else None


def year_date_range(year: int) -> tuple[str, str]:
    """Return (from_date, to_date) strings for a full calendar year."""
    return f"{year}-01-01", f"{year}-12-31"


# ---------------------------------------------------------------------------
# Single-file processing
# ---------------------------------------------------------------------------

def process_file(
    csv_path: Path,
    chart,
    *,
    strict: bool = False,
    year_override: int | None = None,
) -> tuple[FinancialSnapshot | None, ImportResult, int | None]:
    """Import one CSV file and convert to a snapshot.

    Returns (snapshot_or_None, import_result, detected_year).
    """
    raw = csv_path.read_bytes()
    result = import_csv(raw, chart, filename=csv_path.name, strict=strict)

    year = year_override or detect_year(csv_path.name)

    if not result.success:
        return None, result, year

    if year is None:
        # Try to infer from period headers — fall back to first column header
        for key in (result.rows[0].amounts.keys() if result.rows else []):
            m = _YEAR_RE.search(key)
            if m:
                year = int(m.group(1))
                break

    if year is None:
        return None, result, None

    from_date, to_date = year_date_range(year)
    snapshot = to_snapshot(
        result,
        from_date=from_date,
        to_date=to_date,
        report_date=date.today().isoformat(),
    )
    return snapshot, result, year


# ---------------------------------------------------------------------------
# Migration report
# ---------------------------------------------------------------------------

class MigrationReport:
    """Accumulates results across all files for a summary report."""

    def __init__(self):
        self.entries: list[dict] = []
        self.all_unrecognised: dict[str, list[str]] = {}  # account -> [files]

    def add(self, csv_path: Path, result: ImportResult, year: int | None, saved: bool):
        entry = {
            "file": csv_path.name,
            "year": year,
            "success": result.success,
            "total_rows": result.total_rows,
            "mapped_rows": result.mapped_rows,
            "unrecognised": len(result.unrecognised_accounts),
            "errors": len(result.errors),
            "warnings": len(result.warnings),
            "saved": saved,
        }

        # Compute totals from mapped rows
        income_total = 0.0
        expense_total = 0.0
        for row in result.rows:
            row_total = sum(row.amounts.values())
            if row.category_section == "income":
                income_total += row_total
            else:
                expense_total += row_total

        entry["income_total"] = round(income_total, 2)
        entry["expense_total"] = round(expense_total, 2)
        entry["net_position"] = round(income_total - expense_total, 2)

        self.entries.append(entry)

        for acct in result.unrecognised_accounts:
            self.all_unrecognised.setdefault(acct, []).append(csv_path.name)

    def print_report(self):
        print("\n" + "=" * 72)
        print("HISTORICAL DATA MIGRATION REPORT")
        print("=" * 72)

        if not self.entries:
            print("No CSV files were processed.")
            return

        # Per-file summary
        print(f"\n{'File':<30} {'Year':<6} {'Mapped':>7} {'Unrec':>6} {'Income':>14} {'Expense':>14} {'Net':>14} {'Status':<8}")
        print("-" * 105)
        for e in sorted(self.entries, key=lambda x: x.get("year") or 0):
            status = "OK" if e["saved"] else "FAILED"
            year_str = str(e["year"]) if e["year"] else "????"
            print(
                f"{e['file']:<30} {year_str:<6} "
                f"{e['mapped_rows']:>5}/{e['total_rows']:<2} "
                f"{e['unrecognised']:>5} "
                f"${e['income_total']:>12,.2f} "
                f"${e['expense_total']:>12,.2f} "
                f"${e['net_position']:>12,.2f} "
                f"{status:<8}"
            )

        # Unrecognised accounts
        if self.all_unrecognised:
            print(f"\nUnrecognised accounts ({len(self.all_unrecognised)}):")
            for acct, files in sorted(self.all_unrecognised.items()):
                print(f"  - {acct}  (in: {', '.join(files)})")

        # Totals
        total_files = len(self.entries)
        saved_files = sum(1 for e in self.entries if e["saved"])
        failed_files = total_files - saved_files
        print(f"\nSummary: {saved_files} saved, {failed_files} failed, {total_files} total")
        print("=" * 72)

    def to_dict(self) -> dict:
        return {
            "entries": self.entries,
            "unrecognised_accounts": {
                acct: files for acct, files in self.all_unrecognised.items()
            },
            "summary": {
                "total_files": len(self.entries),
                "saved": sum(1 for e in self.entries if e["saved"]),
                "failed": sum(1 for e in self.entries if not e["saved"]),
            },
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

VALID_YEARS = range(2020, 2025)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate historical CSV files to FinancialSnapshot JSON."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "historical",
        help="Directory containing CSV files (default: data/historical/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "snapshots",
        help="Directory for output JSON snapshots (default: data/snapshots/)",
    )
    parser.add_argument(
        "--chart",
        type=Path,
        default=PROJECT_ROOT / "config" / "chart_of_accounts.yaml",
        help="Path to chart_of_accounts.yaml",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on unrecognised accounts (default: lenient / warnings only)",
    )
    parser.add_argument(
        "--save-report",
        type=Path,
        default=None,
        help="Save migration report as JSON to this path",
    )

    args = parser.parse_args(argv)

    # Validate paths
    if not args.input_dir.is_dir():
        print(f"Error: input directory does not exist: {args.input_dir}")
        return 1
    if not args.chart.is_file():
        print(f"Error: chart of accounts not found: {args.chart}")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    chart = load_chart_of_accounts(args.chart)

    csv_files = sorted(args.input_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {args.input_dir}")
        return 1

    print(f"Found {len(csv_files)} CSV file(s) in {args.input_dir}")

    report = MigrationReport()

    for csv_path in csv_files:
        print(f"\nProcessing: {csv_path.name} ...")
        snapshot, result, year = process_file(csv_path, chart, strict=args.strict)

        saved = False
        if snapshot is not None and year is not None:
            out_file = args.output_dir / f"annual_{year}.json"
            out_file.write_text(
                snapshot.model_dump_json(indent=2),
                encoding="utf-8",
            )
            print(f"  -> Saved: {out_file.name}")
            saved = True
        else:
            reasons = []
            if not result.success:
                reasons.append(f"{len(result.errors)} error(s)")
            if year is None:
                reasons.append("could not detect year")
            print(f"  -> SKIPPED: {', '.join(reasons)}")

        report.add(csv_path, result, year, saved)

    report.print_report()

    if args.save_report:
        args.save_report.write_text(
            json.dumps(report.to_dict(), indent=2),
            encoding="utf-8",
        )
        print(f"\nReport saved to: {args.save_report}")

    # Return non-zero if any file failed
    if any(not e["saved"] for e in report.entries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
