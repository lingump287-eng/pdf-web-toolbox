"""Bounded, read-only preview of completed PDF workflow outputs.

The job identifier is an unguessable capability until account-based
authorization is introduced. Never show input files or failed partial output.
"""
from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import fitz

MAX_LISTED_RESULTS = 100
MAX_PREVIEW_MEMBER_BYTES = 24 * 1024 * 1024
MAX_PREVIEW_PAGE = 1000


def _require_pdf_member(archive: ZipFile, file_index: int):
    members = [info for info in archive.infolist()
               if not info.is_dir() and info.filename.lower().endswith(".pdf")]
    if not 0 <= file_index < len(members):
        raise ValueError("结果文件序号无效")
    member = members[file_index]
    if member.file_size > MAX_PREVIEW_MEMBER_BYTES:
        raise ValueError("该文件过大，不能在线预览，请直接下载")
    return member


def result_summary(result: Path, display_name: str) -> dict:
    """Return only metadata needed for the result panel."""
    if result.suffix.lower() == ".pdf":
        with fitz.open(str(result)) as doc:
            return {
                "kind": "pdf",
                "filename": display_name,
                "size_bytes": result.stat().st_size,
                "page_count": doc.page_count,
                "preview_pages": min(6, doc.page_count),
                "page_sizes": [
                    [round(doc[i].rect.width, 1), round(doc[i].rect.height, 1)]
                    for i in range(min(6, doc.page_count))
                ],
                "files": [],
            }
    if result.suffix.lower() == ".zip":
        with ZipFile(result) as archive:
            members = [info for info in archive.infolist()
                       if not info.is_dir() and info.filename.lower().endswith(".pdf")]
            return {
                "kind": "zip",
                "filename": display_name,
                "size_bytes": result.stat().st_size,
                "page_count": None,
                "preview_pages": 0,
                "page_sizes": [],
                "files": [
                    {"index": i, "name": member.filename, "size_bytes": member.file_size,
                     "previewable": member.file_size <= MAX_PREVIEW_MEMBER_BYTES}
                    for i, member in enumerate(members[:MAX_LISTED_RESULTS])
                ],
                "total_files": len(members),
                "truncated": len(members) > MAX_LISTED_RESULTS,
            }
    raise ValueError("此格式不支持结果预览")


def result_preview_jpeg(result: Path, *, page: int = 1, file_index: int = 0) -> bytes:
    """Render one bounded preview page. Page numbers are 1-based."""
    if page < 1 or page > MAX_PREVIEW_PAGE:
        raise ValueError("预览页码超出范围")
    if result.suffix.lower() == ".pdf":
        if file_index != 0:
            raise ValueError("文件序号无效")
        doc = fitz.open(str(result))
    elif result.suffix.lower() == ".zip":
        with ZipFile(result) as archive:
            member = _require_pdf_member(archive, file_index)
            with archive.open(member) as fd:
                data = fd.read(MAX_PREVIEW_MEMBER_BYTES + 1)
            if len(data) > MAX_PREVIEW_MEMBER_BYTES:
                raise ValueError("预览文件过大")
        doc = fitz.open(stream=data, filetype="pdf")
    else:
        raise ValueError("不支持此类型的文件预览")
    try:
        if doc.needs_pass:
            raise ValueError("加密 PDF 不支持在线预览")
        if page > doc.page_count:
            raise ValueError("预览页码超出范围")
        target = doc[page - 1]
        longest = max(target.rect.width, target.rect.height, 1.0)
        scale = min(1.0, 850.0 / longest)
        pix = target.get_pixmap(matrix=fitz.Matrix(scale, scale),
                                colorspace=fitz.csRGB, alpha=False, annots=True)
        return pix.tobytes("jpeg", jpg_quality=66)
    finally:
        doc.close()
