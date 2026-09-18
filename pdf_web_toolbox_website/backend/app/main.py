from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.pdf import router as pdf_router
from app.utils.files import MAX_FILE_MB, MAX_FILES_PER_REQUEST


def resource_path(relative_path: str) -> Path:
    """Return a path that works in source and packaged modes."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).resolve().parent / relative_path.replace("app/", "", 1)


FRONTEND_DIR = resource_path("app/frontend_dist")
STATIC_DIR = FRONTEND_DIR / "static"
INDEX_FILE = FRONTEND_DIR / "index.html"

app = FastAPI(
    title="PDF Web Toolbox",
    version="4.0.0",
    description="Browser-based PDF utilities with temporary server-side processing.",
)

allowed_origins = [
    item.strip()
    for item in os.getenv("PDF_TOOLBOX_ALLOWED_ORIGINS", "").split(",")
    if item.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

app.include_router(pdf_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, int]:
    return {"max_file_mb": MAX_FILE_MB, "max_files_per_request": MAX_FILES_PER_REQUEST}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))
    return JSONResponse(
        {"message": "PDF Web Toolbox API is running, but frontend files were not found."},
        status_code=500,
    )


@app.get("/{full_path:path}")
def serve_frontend_file(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "API route not found"}, status_code=404)

    target = FRONTEND_DIR / full_path
    if target.exists() and target.is_file():
        return FileResponse(str(target))
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))
    return JSONResponse({"detail": "Frontend file not found"}, status_code=404)
