"""Conservative blank-page suggestions. No pages are deleted by this scanner.

A user must inspect and explicitly confirm suggested pages before a separate
workflow request can remove them. Detection is intentionally conservative.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Sequence

import fitz

from app.services.workflow import validate_pdfs

MAX_SUGGESTIONS = 80
INK_THRESHOLD = 250
MAX_INK_RATIO = 0.0001


def scan_blank_pages(paths: Sequence[Path]) -> dict:
    """Return 1-based page numbers and 0-based *upload* indices.

    A blank-looking page may contain extremely light content; all suggestions
    are reviewed by the human before removal, never silently deleted.
    """
    total_pages = validate_pdfs(paths)
    candidates: list[dict] = []
    reviewed = 0
    truncated = False
    for file_index, path in enumerate(paths):
        with fitz.open(str(path)) as doc:
            if doc.needs_pass:
                raise ValueError(f"加密文件无法检测：{path.name}")
            for page_index in range(doc.page_count):
                page = doc[page_index]
                # Searchable text includes some faint, almost invisible watermarks.
                # Do not propose deletion for *any* page with extracted text.
                if page.get_text("text").strip():
                    reviewed += 1
                    continue
                # Low DPI grayscale is sufficient for proposing visually white pages;
                # rich previews are rendered only for possible candidates.
                pix = page.get_pixmap(matrix=fitz.Matrix(0.7, 0.7),
                                      colorspace=fitz.csGRAY, alpha=False,
                                      annots=True)
                samples = pix.samples
                ink = sum(value < INK_THRESHOLD for value in samples)
                reviewed += 1
                if ink / max(1, len(samples)) > MAX_INK_RATIO:
                    continue
                if len(candidates) >= MAX_SUGGESTIONS:
                    truncated = True
                    continue
                preview = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55),
                                          alpha=False, annots=True)
                image = base64.b64encode(preview.tobytes("jpeg", jpg_quality=58)).decode("ascii")
                candidates.append({
                    "file_index": file_index,
                    "file_name": path.name,
                    "page": page_index + 1,
                    "image": "data:image/jpeg;base64," + image,
                })
    return {
        "total_pages": total_pages,
        "scanned_pages": reviewed,
        "candidates": candidates,
        "truncated": truncated,
        "notice": "仅供人工确认的疑似空白页，不会自动删除。请逐页检查后明确确认。",
    }
