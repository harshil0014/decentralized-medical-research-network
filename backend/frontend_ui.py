from pathlib import Path
import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api_auth import require_hospital
from backend.he_service import (
    fetch_ipfs_dataset_bytes,
    verify_dataset_bytes,
)
from backend.he_sum_api import router as he_sum_router
from backend.storage_crypto import (
    decrypt_bytes,
    is_encrypted_dataset,
)


router = APIRouter()
router.include_router(he_sum_router)
FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"


@router.get("/", include_in_schema=False)
def frontend_index():
    return FileResponse(FRONTEND_ROOT / "index.html")


@router.get("/frontend.css", include_in_schema=False)
def frontend_css():
    return FileResponse(
        FRONTEND_ROOT / "styles.css",
        media_type="text/css",
    )


@router.get("/frontend.js", include_in_schema=False)
def frontend_js():
    return FileResponse(
        FRONTEND_ROOT / "app.js",
        media_type="application/javascript",
    )


@router.get("/frontend-preview.js", include_in_schema=False)
def frontend_preview_js():
    return FileResponse(
        FRONTEND_ROOT / "preview.js",
        media_type="application/javascript",
    )


@router.get(
    "/datasets/{dataset_id}/preview",
    dependencies=[Depends(require_hospital)],
    include_in_schema=False,
)
def hospital_dataset_preview(dataset_id: str):
    # Lazy import avoids a circular import while backend.app is
    # registering this router during application startup.
    from backend.app import query

    try:
        dataset = json.loads(
            query(
                "ReadDatasetPrivate",
                [dataset_id],
                "org1",
            )
        )

        data_type = (dataset.get("dataType") or "").upper()
        if data_type not in {"CSV", "LAB_CSV", "NUMERIC_CSV"}:
            raise HTTPException(
                status_code=400,
                detail="Preview is currently available for CSV datasets only",
            )

        stored = fetch_ipfs_dataset_bytes(dataset["cid"])
        verify_dataset_bytes(stored, dataset["sha256"])

        if not is_encrypted_dataset(stored):
            raise HTTPException(
                status_code=422,
                detail="Dataset is not an encrypted MEDAES object",
            )
        content = decrypt_bytes(dataset_id, stored)

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="CSV preview requires UTF-8 text",
            ) from exc

        reader = csv.DictReader(io.StringIO(text))
        columns = [c for c in (reader.fieldnames or []) if c is not None]
        if not columns:
            raise HTTPException(
                status_code=400,
                detail="CSV has no header row",
            )

        rows = []
        truncated = False
        for index, row in enumerate(reader):
            if index >= 50:
                truncated = True
                break
            rows.append({column: row.get(column, "") for column in columns})

        return {
            "datasetId": dataset_id,
            "dataType": dataset.get("dataType"),
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "previewLimit": 50,
        }

    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Requested frontend asset is unavailable") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Dataset preview failed; verify encrypted object availability",
        ) from exc
