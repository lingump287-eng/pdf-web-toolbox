from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import UploadFile


def app_data_root() -> Path:
    """Writable storage root that also works after PyInstaller packaging."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        root = Path(base) / "PDFWebToolbox"
    else:
        root = Path(__file__).resolve().parents[2]
    root.mkdir(parents=True, exist_ok=True)
    return root


ROOT_DIR = app_data_root()
TEMP_ROOT = Path(os.getenv("PDF_TOOLBOX_TEMP_ROOT", str(ROOT_DIR / "storage" / "tasks")))
MAX_FILE_MB = max(1, int(os.getenv("PDF_TOOLBOX_MAX_FILE_MB", "100")))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_FILES_PER_REQUEST = max(1, int(os.getenv("PDF_TOOLBOX_MAX_FILES", "20")))
MAX_TOTAL_MB = max(MAX_FILE_MB, int(os.getenv("PDF_TOOLBOX_MAX_TOTAL_MB", "250")))
MAX_TOTAL_BYTES = MAX_TOTAL_MB * 1024 * 1024
MAX_PAGES_PER_PDF = max(1, int(os.getenv("PDF_TOOLBOX_MAX_PAGES", "1000")))
MAX_TOTAL_PAGES = max(MAX_PAGES_PER_PDF, int(os.getenv("PDF_TOOLBOX_MAX_TOTAL_PAGES", "1500")))
MAX_DPI = min(600, max(72, int(os.getenv("PDF_TOOLBOX_MAX_DPI", "400"))))
MAX_PREVIEW_PAGES = max(10, int(os.getenv("PDF_TOOLBOX_MAX_PREVIEW_PAGES", "80")))
MAX_CONCURRENT_JOBS = max(1, min(8, int(os.getenv("PDF_TOOLBOX_MAX_CONCURRENT_JOBS", "2"))))
JOB_TTL_SECONDS = max(300, int(os.getenv("PDF_TOOLBOX_JOB_TTL_SECONDS", "3600")))
RATE_LIMIT_JOBS = max(1, int(os.getenv("PDF_TOOLBOX_RATE_LIMIT_JOBS", "30")))
RATE_LIMIT_WINDOW_SECONDS = max(60, int(os.getenv("PDF_TOOLBOX_RATE_LIMIT_WINDOW_SECONDS", "600")))
MAX_REQUEST_BYTES = MAX_TOTAL_BYTES + 12 * 1024 * 1024


def new_task_dir() -> Path:
    task_dir = TEMP_ROOT / uuid.uuid4().hex
    (task_dir / "input").mkdir(parents=True, exist_ok=True)
    (task_dir / "output").mkdir(parents=True, exist_ok=True)
    return task_dir


def safe_filename(filename: str | None, fallback: str) -> str:
    name = Path(filename or fallback).name
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip()
    return cleaned or fallback


async def save_upload(
    upload: UploadFile,
    folder: Path,
    fallback: str,
    allowed_suffixes: set[str] | None = None,
) -> Path:
    filename = safe_filename(upload.filename, fallback)
    suffix = Path(filename).suffix.lower()
    if allowed_suffixes and suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"不支持的文件类型：{filename}。允许类型：{allowed}")

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 2
        while (folder / f"{stem}_{i}{suffix}").exists():
            i += 1
        target = folder / f"{stem}_{i}{suffix}"

    total = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise ValueError(f"单个文件超过限制：{MAX_FILE_MB} MB")
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if total == 0:
        target.unlink(missing_ok=True)
        raise ValueError(f"文件为空：{filename}")

    if suffix == ".pdf":
        with target.open("rb") as f:
            if f.read(5) != b"%PDF-":
                target.unlink(missing_ok=True)
                raise ValueError(f"文件内容不是有效 PDF：{filename}")

    return target


def validate_total_size(paths: list[Path]) -> int:
    total = sum(path.stat().st_size for path in paths)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"单次上传总大小超过限制：{MAX_TOTAL_MB} MB")
    return total


def zip_folder(folder: Path, output_zip: Path) -> Path:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_zip, "w", ZIP_DEFLATED, allowZip64=True) as zf:
        for file in sorted(folder.rglob("*")):
            if file.is_file() and file != output_zip:
                zf.write(file, file.relative_to(folder))
    return output_zip


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
