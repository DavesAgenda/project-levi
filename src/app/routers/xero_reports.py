"""FastAPI router for Xero report fetching and snapshot saving.

Endpoints:
    GET  /api/xero/pl           — Fetch and snapshot P&L report
    GET  /api/xero/trial-balance — Fetch and snapshot Trial Balance
    GET  /api/xero/balance-sheet — Fetch and snapshot Balance Sheet
    GET  /api/xero/tracking     — Fetch and snapshot Tracking Categories
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.xero.client import (
    fetch_balance_sheet,
    fetch_profit_and_loss,
    fetch_tracking_categories,
    fetch_trial_balance,
)
from app.xero.parser import parse_report, report_to_flat_rows
from app.xero.snapshots import (
    save_balance_sheet_snapshot,
    save_pl_snapshot,
    save_tracking_categories_snapshot,
    save_trial_balance_snapshot,
)
from app.services.property_assets import (
    map_balance_sheet_to_properties,
    get_manual_property_values,
    load_properties_config,
)

router = APIRouter(prefix="/api/xero", tags=["xero-reports"])


@router.get("/pl")
async def get_profit_and_loss(
    from_date: str = Query(..., description="Report start date (YYYY-MM-DD)"),
    to_date: str = Query(..., description="Report end date (YYYY-MM-DD)"),
    periods: int | None = Query(None, description="Number of comparison periods"),
    timeframe: str | None = Query(None, description="MONTH, QUARTER, or YEAR"),
    tracking_category_id: str | None = Query(None, description="Tracking category UUID"),
    tracking_option_id: str | None = Query(None, description="Tracking option UUID"),
    payments_only: bool = Query(False, description="True for cash basis"),
    save: bool = Query(True, description="Save snapshot to data/snapshots/"),
    flat: bool = Query(False, description="Return flat rows instead of parsed structure"),
):
    """Fetch a Profit & Loss report from Xero."""
    try:
        raw = await fetch_profit_and_loss(
            from_date=from_date,
            to_date=to_date,
            periods=periods,
            timeframe=timeframe,
            tracking_category_id=tracking_category_id,
            tracking_option_id=tracking_option_id,
            payments_only=payments_only,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    snapshot_path = None
    if save:
        snapshot_path = save_pl_snapshot(
            raw, from_date, to_date,
            tracking=tracking_category_id is not None,
        )

    parsed = parse_report(raw)

    if flat:
        return {
            "report": parsed.report_name,
            "columns": parsed.column_headers,
            "rows": report_to_flat_rows(parsed),
            "snapshot": str(snapshot_path) if snapshot_path else None,
        }

    return {
        "report": parsed.report_name,
        "columns": parsed.column_headers,
        "sections": [
            {
                "title": s.title,
                "rows": [
                    {
                        "account_name": r.account_name,
                        "account_id": r.account_id,
                        "values": {k: float(v) for k, v in r.values.items()},
                    }
                    for r in s.rows
                ],
                "summary": (
                    {"label": s.summary.label, "values": {k: float(v) for k, v in s.summary.values.items()}}
                    if s.summary else None
                ),
            }
            for s in parsed.sections
        ],
        "summaries": [
            {"label": s.label, "values": {k: float(v) for k, v in s.values.items()}}
            for s in parsed.summaries
        ],
        "snapshot": str(snapshot_path) if snapshot_path else None,
    }


@router.get("/trial-balance")
async def get_trial_balance(
    date: str | None = Query(None, description="Report date (YYYY-MM-DD)"),
    save: bool = Query(True, description="Save snapshot"),
):
    """Fetch a Trial Balance report from Xero."""
    try:
        raw = await fetch_trial_balance(date=date)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if save and date:
        save_trial_balance_snapshot(raw, date)

    parsed = parse_report(raw)
    return {"report": parsed.report_name, "sections": len(parsed.sections)}


@router.get("/balance-sheet")
async def get_balance_sheet(
    date: str | None = Query(None, description="Report date (YYYY-MM-DD)"),
    save: bool = Query(True, description="Save snapshot"),
    flat: bool = Query(False, description="Return flat rows instead of parsed structure"),
    include_assets: bool = Query(True, description="Include property asset mapping"),
):
    """Fetch a Balance Sheet report from Xero.

    Parses the response, optionally saves a snapshot, and maps fixed asset
    values to properties from config/properties.yaml.
    """
    try:
        raw = await fetch_balance_sheet(date=date)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    snapshot_path = None
    if save and date:
        snapshot_path = save_balance_sheet_snapshot(raw, date)

    parsed = parse_report(raw)

    # Build property asset mapping if requested
    asset_data = None
    if include_assets:
        try:
            summary = map_balance_sheet_to_properties(parsed)
            asset_data = {
                "total_land": summary.total_land,
                "total_buildings": summary.total_buildings,
                "total_assets": summary.total_assets,
                "unmatched_codes": summary.unmatched_codes,
                "properties": [
                    {
                        "property_key": p.property_key,
                        "address": p.address,
                        "land_code": p.land_code,
                        "land_value": p.land_value,
                        "building_code": p.building_code,
                        "building_value": p.building_value,
                        "total_value": p.total_value,
                    }
                    for p in summary.properties
                ],
            }
        except Exception:
            # Asset mapping is non-critical — degrade gracefully
            asset_data = None

    if flat:
        return {
            "report": parsed.report_name,
            "columns": parsed.column_headers,
            "rows": report_to_flat_rows(parsed),
            "assets": asset_data,
            "snapshot": str(snapshot_path) if snapshot_path else None,
        }

    return {
        "report": parsed.report_name,
        "columns": parsed.column_headers,
        "sections": [
            {
                "title": s.title,
                "rows": [
                    {
                        "account_name": r.account_name,
                        "account_id": r.account_id,
                        "values": {k: float(v) for k, v in r.values.items()},
                    }
                    for r in s.rows
                ],
                "summary": (
                    {"label": s.summary.label, "values": {k: float(v) for k, v in s.summary.values.items()}}
                    if s.summary else None
                ),
            }
            for s in parsed.sections
        ],
        "summaries": [
            {"label": s.label, "values": {k: float(v) for k, v in s.values.items()}}
            for s in parsed.summaries
        ],
        "assets": asset_data,
        "snapshot": str(snapshot_path) if snapshot_path else None,
    }


@router.get("/balance-sheet/assets")
async def get_balance_sheet_assets(
    date: str | None = Query(None, description="Report date (YYYY-MM-DD)"),
    fallback: bool = Query(False, description="Use manual values if API unavailable"),
):
    """Fetch property asset values from the balance sheet.

    If fallback=True and the API call fails, returns manual values from
    properties.yaml (if configured).
    """
    if fallback:
        summary = get_manual_property_values()
        return {
            "source": "manual",
            "total_land": summary.total_land,
            "total_buildings": summary.total_buildings,
            "total_assets": summary.total_assets,
            "properties": [
                {
                    "property_key": p.property_key,
                    "address": p.address,
                    "land_value": p.land_value,
                    "building_value": p.building_value,
                    "total_value": p.total_value,
                }
                for p in summary.properties
            ],
        }

    try:
        raw = await fetch_balance_sheet(date=date)
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    parsed = parse_report(raw)
    summary = map_balance_sheet_to_properties(parsed)

    return {
        "source": "xero",
        "total_land": summary.total_land,
        "total_buildings": summary.total_buildings,
        "total_assets": summary.total_assets,
        "unmatched_codes": summary.unmatched_codes,
        "properties": [
            {
                "property_key": p.property_key,
                "address": p.address,
                "land_code": p.land_code,
                "land_value": p.land_value,
                "building_code": p.building_code,
                "building_value": p.building_value,
                "total_value": p.total_value,
            }
            for p in summary.properties
        ],
    }


@router.get("/tracking")
async def get_tracking_categories(
    save: bool = Query(True, description="Save snapshot"),
):
    """Fetch Tracking Categories from Xero."""
    try:
        raw = await fetch_tracking_categories()
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if save:
        save_tracking_categories_snapshot(raw)

    # Return a simplified view
    categories = raw.get("TrackingCategories", [])
    return {
        "categories": [
            {
                "id": cat.get("TrackingCategoryID"),
                "name": cat.get("Name"),
                "status": cat.get("Status"),
                "options": [
                    {"id": opt.get("TrackingOptionID"), "name": opt.get("Name"), "status": opt.get("Status")}
                    for opt in cat.get("Options", [])
                ],
            }
            for cat in categories
        ],
    }
