"""Property portfolio service — per-property P&L, budget vs actual, and net yield.

Loads property config from properties.yaml, extracts income/cost actuals from
financial snapshots, computes per-property profit & loss, budget comparisons,
and net yield (net income / asset value).

Net yield formula (from PRD):
    (actual_rent - actual_costs - mgmt_fee - property_levy_share) / (land + building value)
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import FinancialSnapshot
from app.services.dashboard import load_ytd_snapshot
from app.xero.snapshots import xero_snapshot_to_financial
from app.services.property_assets import (
    PropertyAssetSummary,
    PropertyAssetValue,
    get_manual_property_values,
    load_properties_config,
)

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
CONFIG_DIR = PROJECT_ROOT / "config"
PROPERTIES_PATH = CONFIG_DIR / "properties.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PropertyPL:
    """Per-property profit & loss with budget comparison and yield."""

    property_key: str
    address: str
    tenant: str
    status: str  # "occupied", "occupied_warden", etc.

    # Actuals (from snapshot)
    gross_rent: float = 0.0
    management_fee: float = 0.0
    maintenance_costs: float = 0.0
    levy_share: float = 0.0
    net_income: float = 0.0

    # Budget
    budget_gross_rent: float = 0.0
    budget_net_rent: float = 0.0
    budget_variance: float = 0.0
    budget_variance_pct: float | None = None

    # Asset values
    land_value: float = 0.0
    building_value: float = 0.0
    total_asset_value: float = 0.0

    # Yield
    net_yield_pct: float | None = None

    # 3-year rolling average maintenance
    avg_maintenance_3yr: float | None = None

    @property
    def is_warden_occupied(self) -> bool:
        return self.status == "occupied_warden"

    @property
    def yield_status(self) -> str:
        """Return colour status based on yield."""
        if self.net_yield_pct is None:
            return "neutral"
        if self.net_yield_pct >= 4.0:
            return "success"
        if self.net_yield_pct >= 2.0:
            return "warning"
        return "danger"

    @property
    def budget_status(self) -> str:
        """Return colour status based on budget variance."""
        if self.budget_net_rent == 0:
            return "neutral"
        if self.budget_variance >= 0:
            return "success"
        if self.budget_variance_pct is not None and abs(self.budget_variance_pct) <= 10:
            return "warning"
        return "danger"


@dataclass
class PortfolioSummary:
    """Summary of the entire property portfolio."""

    properties: list[PropertyPL] = field(default_factory=list)
    total_gross_rent: float = 0.0
    total_management_fees: float = 0.0
    total_maintenance_costs: float = 0.0
    total_levy_share: float = 0.0
    total_net_income: float = 0.0
    total_budget_gross: float = 0.0
    total_budget_net: float = 0.0
    total_budget_variance: float = 0.0
    total_asset_value: float = 0.0
    portfolio_yield_pct: float | None = None
    has_data: bool = False
    snapshot_period: str = ""


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------

def _load_latest_snapshot(directory: Path | None = None) -> FinancialSnapshot | None:
    """Load the most recent P&L snapshot JSON file."""
    snap_dir = directory or SNAPSHOTS_DIR
    if not snap_dir.exists():
        return None

    json_files = sorted(
        snap_dir.glob("pl_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for path in json_files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if "report_date" in raw:
                return FinancialSnapshot(**raw)
            if "snapshot_metadata" in raw:
                resp = raw.get("response", {})
                if "report_date" in resp:
                    return FinancialSnapshot(**resp)
                snap = xero_snapshot_to_financial(raw)
                if snap:
                    return snap
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return None


# ---------------------------------------------------------------------------
# Historical cost loading for 3-year rolling average
# ---------------------------------------------------------------------------

def _parse_csv_amount(value: str) -> float:
    """Parse a CSV dollar amount like '$3,600.00' or '3600' to float."""
    cleaned = value.strip().strip('"').replace("$", "").replace(",", "")
    if not cleaned or cleaned == "-":
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def load_historical_costs(
    cost_account: str,
    historical_dir: Path | None = None,
) -> list[float]:
    """Load historical annual costs for a property from CSV files.

    Scans all CSV files in the historical directory for rows matching the
    given cost account code. Returns a list of annual cost amounts (one per
    file found), most recent first.
    """
    hist_dir = historical_dir or HISTORICAL_DIR
    if not hist_dir.exists():
        return []

    costs: list[tuple[str, float]] = []  # (filename, amount)

    for csv_path in sorted(hist_dir.glob("*.csv")):
        if csv_path.name == "template.csv":
            continue
        try:
            text = csv_path.read_text(encoding="utf-8")
            reader = csv.reader(text.splitlines())
            for row in reader:
                if not row or len(row) < 2:
                    continue
                # Account column format: "89010 - Hamilton Street 33 Costs"
                account_field = row[0].strip()
                if account_field.startswith(cost_account):
                    amount = _parse_csv_amount(row[1])
                    costs.append((csv_path.name, amount))
                    break
        except (csv.Error, UnicodeDecodeError):
            continue

    # Sort by filename descending (most recent first)
    costs.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in costs]


def compute_3yr_average(
    current_annual_cost: float,
    historical_costs: list[float],
) -> float | None:
    """Compute a 3-year rolling average for maintenance costs.

    Uses the current year plus up to 2 years of historical data.
    Returns None if no data is available at all.
    """
    all_costs = [current_annual_cost] + historical_costs[:2]
    # Filter out zero entries only if we have non-zero data
    non_zero = [c for c in all_costs if c > 0]
    if not non_zero:
        return 0.0
    return round(sum(non_zero) / len(non_zero), 2)


# ---------------------------------------------------------------------------
# Property levy share allocation
# ---------------------------------------------------------------------------

def compute_levy_shares(
    total_levy: float,
    properties: dict[str, dict[str, Any]],
    actuals: dict[str, float],
) -> dict[str, float]:
    """Allocate the property receipts levy across properties by income share.

    The levy (account 44903) is shared proportionally based on each property's
    actual rental income as a fraction of total property income.

    Warden-occupied properties with zero income get zero levy share.
    """
    total_income = sum(actuals.get(p.get("income_account", ""), 0) for p in properties.values())
    if total_income <= 0 or total_levy <= 0:
        return {k: 0.0 for k in properties}

    shares: dict[str, float] = {}
    for prop_key, prop in properties.items():
        income = actuals.get(prop.get("income_account", ""), 0)
        share = (income / total_income) * total_levy if income > 0 else 0.0
        shares[prop_key] = round(share, 2)

    return shares


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_property_portfolio(
    snapshot: FinancialSnapshot | None = None,
    snapshots_dir: Path | None = None,
    config_path: Path | None = None,
    historical_dir: Path | None = None,
    asset_summary: PropertyAssetSummary | None = None,
) -> PortfolioSummary:
    """Build the property portfolio view with per-property P&L and yields.

    Args:
        snapshot: Financial snapshot (loaded from disk if None).
        snapshots_dir: Override snapshot directory.
        config_path: Override properties.yaml path.
        historical_dir: Override historical CSV directory.
        asset_summary: Pre-computed asset values (uses manual values if None).

    Returns:
        PortfolioSummary ready for template rendering.
    """
    properties = load_properties_config(config_path)
    if not properties:
        return PortfolioSummary()

    # Load snapshot (aggregate all monthly P&L snapshots for YTD)
    if snapshot is None:
        snapshot = load_ytd_snapshot(directory=snapshots_dir)

    if snapshot is None:
        return PortfolioSummary()

    # Build account code -> amount lookup from snapshot
    account_actuals: dict[str, float] = {}
    for row in snapshot.rows:
        account_actuals[row.account_code] = (
            account_actuals.get(row.account_code, 0) + row.amount
        )

    # Get total property levy (account 44903)
    total_levy = account_actuals.get("44903", 0.0)

    # Compute levy shares
    levy_shares = compute_levy_shares(total_levy, properties, account_actuals)

    # Get asset values (fallback to manual config values)
    if asset_summary is None:
        asset_summary = get_manual_property_values(properties, config_path)

    # Build asset lookup by property key
    asset_lookup: dict[str, PropertyAssetValue] = {
        a.property_key: a for a in asset_summary.properties
    }

    # Determine annualisation factor from snapshot period
    # Snapshot covers from_date to to_date; we need to annualise costs
    from datetime import datetime
    from_dt = datetime.strptime(snapshot.from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(snapshot.to_date, "%Y-%m-%d")
    days_covered = (to_dt - from_dt).days + 1
    annualisation_factor = 365.0 / days_covered if days_covered > 0 else 1.0

    # Build per-property P&L
    results: list[PropertyPL] = []
    total_gross = 0.0
    total_mgmt = 0.0
    total_maint = 0.0
    total_levy_allocated = 0.0
    total_net = 0.0
    total_budget_gross = 0.0
    total_budget_net = 0.0
    total_assets = 0.0

    for prop_key, prop in properties.items():
        address = prop.get("address", "")
        tenant = prop.get("tenant", "")
        status = prop.get("status", "occupied")
        income_code = str(prop.get("income_account", ""))
        cost_code = str(prop.get("cost_account", ""))
        weekly_rate = float(prop.get("weekly_rate", 0))
        weeks = float(prop.get("weeks_per_year", 48))
        mgmt_fee_pct = float(prop.get("management_fee_pct", 0))

        # Actuals from snapshot
        gross_rent = account_actuals.get(income_code, 0.0)
        maintenance_costs = account_actuals.get(cost_code, 0.0)
        management_fee = round(gross_rent * mgmt_fee_pct, 2) if mgmt_fee_pct > 0 else 0.0
        levy_share = levy_shares.get(prop_key, 0.0)

        net_income = round(gross_rent - management_fee - maintenance_costs - levy_share, 2)

        # Budget: annual_budget_gross = weekly_rate * weeks
        # Budget net = gross * (1 - mgmt_fee_pct)
        budget_gross = round(weekly_rate * weeks, 2)
        budget_net = round(budget_gross * (1 - mgmt_fee_pct), 2)

        # Prorate budget to match snapshot period
        prorate_factor = days_covered / 365.0 if days_covered > 0 else 1.0
        prorated_budget_gross = round(budget_gross * prorate_factor, 2)
        prorated_budget_net = round(budget_net * prorate_factor, 2)

        budget_variance = round(gross_rent - prorated_budget_gross, 2)
        budget_variance_pct = (
            round(budget_variance / prorated_budget_gross * 100, 1)
            if prorated_budget_gross > 0 else None
        )

        # Asset values
        asset = asset_lookup.get(prop_key)
        land_val = asset.land_value if asset else 0.0
        building_val = asset.building_value if asset else 0.0
        asset_total = land_val + building_val

        # Net yield (annualised)
        annualised_net = net_income * annualisation_factor
        net_yield = round(annualised_net / asset_total * 100, 2) if asset_total > 0 else None

        # 3-year rolling average maintenance
        historical = load_historical_costs(cost_code, historical_dir)
        annualised_maint = maintenance_costs * annualisation_factor
        avg_maint = compute_3yr_average(annualised_maint, historical)

        results.append(PropertyPL(
            property_key=prop_key,
            address=address,
            tenant=tenant,
            status=status,
            gross_rent=round(gross_rent, 2),
            management_fee=management_fee,
            maintenance_costs=round(maintenance_costs, 2),
            levy_share=levy_share,
            net_income=net_income,
            budget_gross_rent=prorated_budget_gross,
            budget_net_rent=prorated_budget_net,
            budget_variance=budget_variance,
            budget_variance_pct=budget_variance_pct,
            land_value=land_val,
            building_value=building_val,
            total_asset_value=asset_total,
            net_yield_pct=net_yield,
            avg_maintenance_3yr=avg_maint,
        ))

        total_gross += gross_rent
        total_mgmt += management_fee
        total_maint += maintenance_costs
        total_levy_allocated += levy_share
        total_net += net_income
        total_budget_gross += prorated_budget_gross
        total_budget_net += prorated_budget_net
        total_assets += asset_total

    total_budget_variance = round(total_gross - total_budget_gross, 2)

    # Portfolio yield
    annualised_total_net = total_net * annualisation_factor
    portfolio_yield = (
        round(annualised_total_net / total_assets * 100, 2)
        if total_assets > 0 else None
    )

    return PortfolioSummary(
        properties=results,
        total_gross_rent=round(total_gross, 2),
        total_management_fees=round(total_mgmt, 2),
        total_maintenance_costs=round(total_maint, 2),
        total_levy_share=round(total_levy_allocated, 2),
        total_net_income=round(total_net, 2),
        total_budget_gross=round(total_budget_gross, 2),
        total_budget_net=round(total_budget_net, 2),
        total_budget_variance=total_budget_variance,
        total_asset_value=round(total_assets, 2),
        portfolio_yield_pct=portfolio_yield,
        has_data=True,
        snapshot_period=f"{snapshot.from_date} to {snapshot.to_date}",
    )
