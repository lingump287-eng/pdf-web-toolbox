from __future__ import annotations

import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.pdf import job_router, router as pdf_router
from app.utils.files import (
    MAX_DPI,
    MAX_FILE_MB,
    MAX_FILES_PER_REQUEST,
    MAX_PAGES_PER_PDF,
    MAX_PREVIEW_PAGES,
    MAX_REQUEST_BYTES,
    MAX_TOTAL_MB,
    MAX_TOTAL_PAGES,
    RATE_LIMIT_JOBS,
    RATE_LIMIT_WINDOW_SECONDS,
)


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
    version="5.0.0",
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
app.include_router(job_router)

_rate_lock = threading.Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    {"detail": f"单次请求体过大，上传总大小上限约为 {MAX_TOTAL_MB} MB"},
                    status_code=413,
                )
        except ValueError:
            pass

    if request.method == "POST" and request.url.path.startswith("/api/pdf/") and request.url.path != "/api/pdf/preview":
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        now = time.time()
        with _rate_lock:
            events = _rate_events[client_ip]
            while events and now - events[0] > RATE_LIMIT_WINDOW_SECONDS:
                events.popleft()
            if len(events) >= RATE_LIMIT_JOBS:
                return JSONResponse(
                    {"detail": "提交任务过于频繁，请稍后再试。"},
                    status_code=429,
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
                )
            events.append(now)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, int]:
    return {
        "max_file_mb": MAX_FILE_MB,
        "max_files_per_request": MAX_FILES_PER_REQUEST,
        "max_total_mb": MAX_TOTAL_MB,
        "max_pages_per_pdf": MAX_PAGES_PER_PDF,
        "max_total_pages": MAX_TOTAL_PAGES,
        "max_dpi": MAX_DPI,
        "max_preview_pages": MAX_PREVIEW_PAGES,
    }


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
