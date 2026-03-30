"""FastAPI router for CSV file upload and import."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.csv_import import import_csv, load_chart_of_accounts, to_snapshot
from app.models import ImportResult

router = APIRouter(prefix="/api/csv", tags=["csv-import"])

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"
CHART_PATH = CONFIG_DIR / "chart_of_accounts.yaml"

# Maximum upload size: 10 MB (Xero P&L CSVs are typically <1 MB)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/plain",
    "application/vnd.ms-excel",
    "application/octet-stream",  # Some browsers send this for .csv
}


async def _read_and_validate_upload(file: UploadFile) -> bytes:
    """Read upload bytes with size limit and content-type validation."""
    # Validate content type
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct and ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ct}'. Please upload a CSV file.",
        )

    # Validate filename extension
    filename = file.filename or ""
    if filename and not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file extension. Please upload a .csv file.",
        )

    # Read with size limit
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content)} bytes). Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    return content


@router.post("/upload", response_model=ImportResult)
async def upload_csv(
    file: UploadFile = File(..., description="Xero P&L CSV export"),
    strict: bool = Query(
        True,
        description="If true, unrecognised accounts cause the import to fail. "
        "Set to false to treat them as warnings.",
    ),
    from_date: str | None = Query(
        None,
        description="Period start date (YYYY-MM-DD) for the snapshot. "
        "Required only if you want a snapshot generated.",
    ),
    to_date: str | None = Query(
        None,
        description="Period end date (YYYY-MM-DD) for the snapshot.",
    ),
) -> ImportResult:
    """Upload and validate a Xero P&L CSV export.

    The CSV is parsed, validated against ``chart_of_accounts.yaml``, and each
    row is mapped to the appropriate budget category.  Unrecognised accounts
    are flagged clearly in the response.

    Returns an ``ImportResult`` with mapped rows, error details, and a list
    of any unrecognised accounts.
    """
    content = await _read_and_validate_upload(file)
    chart = load_chart_of_accounts(CHART_PATH)

    result = import_csv(
        content,
        chart,
        filename=file.filename or "upload.csv",
        strict=strict,
    )

    return result


@router.post("/preview", response_model=ImportResult)
async def preview_csv(
    file: UploadFile = File(..., description="Xero P&L CSV export"),
) -> ImportResult:
    """Preview a CSV import without committing.

    Always runs in non-strict mode so the user can see which accounts
    mapped successfully and which did not.
    """
    content = await _read_and_validate_upload(file)
    chart = load_chart_of_accounts(CHART_PATH)

    return import_csv(
        content,
        chart,
        filename=file.filename or "preview.csv",
        strict=False,
    )
