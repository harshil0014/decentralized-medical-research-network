from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
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
