from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Sequence

import fitz  # PyMuPDF
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.utils.files import MAX_DPI, MAX_PAGES_PER_PDF, MAX_TOTAL_PAGES

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXT = ".pdf"
CJK_FONT = "china-s"


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def ensure_parent_dir(path: str | Path) -> Path:
    p = normalize_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def file_size_text(path: str | Path) -> str:
    size = normalize_path(path).stat().st_size
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def parse_page_ranges(range_text: str, total_pages: int) -> list[int]:
    """Parse a 1-based page range such as '1-3, 5, 8-10' into 0-based indices."""
    text = (range_text or "").strip()
    if not text:
        return list(range(total_pages))

    selected: list[int] = []
    seen: set[int] = set()
    for raw in text.replace("，", ",").split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            left, right = [x.strip() for x in part.split("-", 1)]
            if not left.isdigit() or not right.isdigit():
                raise ValueError(f"页码范围格式错误：{part}")
            start = int(left)
            end = int(right)
            if start > end:
                raise ValueError(f"页码范围起止顺序错误：{part}")
            for page in range(start, end + 1):
                if page < 1 or page > total_pages:
                    raise ValueError(f"页码超出范围：{page}，总页数为 {total_pages}")
                idx = page - 1
                if idx not in seen:
                    seen.add(idx)
                    selected.append(idx)
        else:
            if not part.isdigit():
                raise ValueError(f"页码格式错误：{part}")
            page = int(part)
            if page < 1 or page > total_pages:
                raise ValueError(f"页码超出范围：{page}，总页数为 {total_pages}")
            idx = page - 1
            if idx not in seen:
                seen.add(idx)
                selected.append(idx)
    if not selected:
        raise ValueError("未解析到有效页码")
    return selected


def page_count(pdf_path: str | Path, password: str | None = None) -> int:
    reader = PdfReader(str(normalize_path(pdf_path)))
    if reader.is_encrypted:
        if not password:
            return -1
        if not reader.decrypt(password):
            raise ValueError("密码错误，无法读取 PDF 页数")
    return len(reader.pages)


def pdf_basic_info(pdf_path: str | Path, password: str | None = None) -> dict[str, str | bool | int | list[str]]:
    p = normalize_path(pdf_path)
    reader = PdfReader(str(p))
    encrypted = bool(reader.is_encrypted)
    decrypted = False
    if encrypted and password:
        decrypted = bool(reader.decrypt(password))
    can_read_pages = not encrypted or decrypted
    page_num = len(reader.pages) if can_read_pages else -1
    if page_num > MAX_PAGES_PER_PDF:
        raise ValueError(f"PDF 页数超过限制：最多 {MAX_PAGES_PER_PDF} 页")
    metadata = reader.metadata if can_read_pages and reader.metadata else {}

    page_sizes: list[str] = []
    if can_read_pages:
        for i in range(min(page_num, 10)):
            page = reader.pages[i]
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            page_sizes.append(f"第 {i + 1} 页：{width:.1f} × {height:.1f} pt")

    return {
        "filename": p.name,
        "size": file_size_text(p),
        "page_count": page_num,
        "encrypted": encrypted,
        "page_sizes": page_sizes,
        "title": str(metadata.get("/Title", "")) if metadata else "",
        "author": str(metadata.get("/Author", "")) if metadata else "",
        "subject": str(metadata.get("/Subject", "")) if metadata else "",
        "creator": str(metadata.get("/Creator", "")) if metadata else "",
        "producer": str(metadata.get("/Producer", "")) if metadata else "",
        "created_at": str(metadata.get("/CreationDate", "")) if metadata else "",
        "modified_at": str(metadata.get("/ModDate", "")) if metadata else "",
    }


def merge_pdfs(pdf_files: Sequence[str | Path], output_path: str | Path) -> int:
    if not pdf_files:
        raise ValueError("PDF 文件列表为空")
    writer = PdfWriter()
    total = 0
    for pdf in pdf_files:
        reader = PdfReader(str(normalize_path(pdf)))
        if reader.is_encrypted:
            raise ValueError(f"文件已加密，无法合并：{Path(pdf).name}")
        for page in reader.pages:
            writer.add_page(page)
            total += 1
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return total


def extract_pages(pdf_path: str | Path, output_path: str | Path, range_text: str = "") -> int:
    reader = PdfReader(str(normalize_path(pdf_path)))
    if reader.is_encrypted:
        raise ValueError("该 PDF 已加密，请先解密后再提取页面")
    pages = parse_page_ranges(range_text, len(reader.pages))
    writer = PdfWriter()
    for idx in pages:
        writer.add_page(reader.pages[idx])
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return len(pages)


def delete_pages(pdf_path: str | Path, output_path: str | Path, range_text: str) -> int:
    reader = PdfReader(str(normalize_path(pdf_path)))
    if reader.is_encrypted:
        raise ValueError("该 PDF 已加密，请先解密后再删除页面")
    delete_indices = set(parse_page_ranges(range_text, len(reader.pages)))
    writer = PdfWriter()
    kept = 0
    for idx, page in enumerate(reader.pages):
        if idx not in delete_indices:
            writer.add_page(page)
            kept += 1
    if kept == 0:
        raise ValueError("删除范围包含全部页面，无法生成空 PDF")
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return kept


def split_each_page(pdf_path: str | Path, output_folder: str | Path, prefix: str | None = None) -> int:
    p = normalize_path(pdf_path)
    reader = PdfReader(str(p))
    if reader.is_encrypted:
        raise ValueError("该 PDF 已加密，请先解密后再拆分")
    folder = normalize_path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    name_prefix = prefix or p.stem
    for idx, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out = folder / f"{name_prefix}_page_{idx:03d}.pdf"
        with out.open("wb") as f:
            writer.write(f)
    return len(reader.pages)


def rotate_pdf(pdf_path: str | Path, output_path: str | Path, degrees: int, mode: str = "all", range_text: str = "") -> int:
    if degrees not in {90, 180, 270}:
        raise ValueError("旋转角度只能为 90、180 或 270")
    reader = PdfReader(str(normalize_path(pdf_path)))
    if reader.is_encrypted:
        raise ValueError("该 PDF 已加密，请先解密后再旋转")
    total = len(reader.pages)
    if mode in {"all", "全部页面"}:
        indices = set(range(total))
    elif mode in {"odd", "奇数页"}:
        indices = {i for i in range(total) if (i + 1) % 2 == 1}
    elif mode in {"even", "偶数页"}:
        indices = {i for i in range(total) if (i + 1) % 2 == 0}
    elif mode in {"range", "指定页码"}:
        indices = set(parse_page_ranges(range_text, total))
    else:
        raise ValueError(f"未知旋转模式：{mode}")

    writer = PdfWriter()
    changed = 0
    for i, page in enumerate(reader.pages):
        if i in indices:
            page.rotate(degrees)
            changed += 1
        writer.add_page(page)
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return changed


def pdf_to_images(pdf_path: str | Path, output_folder: str | Path, dpi: int = 300, image_format: str = "png", range_text: str = "") -> int:
    p = normalize_path(pdf_path)
    folder = normalize_path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    fmt = image_format.lower().strip().replace(".", "")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg"}:
        raise ValueError("图片格式只支持 PNG、JPG")
    if dpi <= 0 or dpi > 600:
        raise ValueError("DPI 必须在 1 到 600 之间")

    doc = fitz.open(str(p))
    try:
        if doc.needs_pass:
            raise ValueError("该 PDF 已加密，请先解密后再添加水印")
        if doc.page_count > MAX_PAGES_PER_PDF:
            raise ValueError(f"PDF 页数超过限制：最多 {MAX_PAGES_PER_PDF} 页")
        pages = parse_page_ranges(range_text, doc.page_count)
        for idx in pages:
            page = doc.load_page(idx)
            # Use the dpi argument directly so PyMuPDF stores the physical
            # resolution in the exported PNG/JPG. Using Matrix(dpi / 72)
            # changes pixel dimensions but leaves image DPI at the default
            # (typically 96), which makes a later image->PDF conversion
            # produce physically oversized PDF pages.
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            out = folder / f"{p.stem}_page_{idx + 1:03d}.{fmt}"
            pix.save(str(out))
        return len(pages)
    finally:
        doc.close()


def _safe_image_dpi(info: dict) -> tuple[float, float]:
    """Return trustworthy image DPI, falling back to 96 when metadata is absent."""
    raw = info.get("dpi") if info else None

    def valid(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 30 or number > 1200:
            return None
        return number

    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        x_dpi = valid(raw[0])
        y_dpi = valid(raw[1])
        if x_dpi and y_dpi:
            return x_dpi, y_dpi
    elif raw is not None:
        dpi = valid(raw)
        if dpi:
            return dpi, dpi

    # A bitmap without physical-resolution metadata has no objectively
    # recoverable print size. 96 DPI is a conservative screen-image fallback.
    return 96.0, 96.0


def images_to_pdf(image_files: Sequence[str | Path], output_path: str | Path) -> int:
    if not image_files:
        raise ValueError("图片列表为空")

    out_doc = fitz.open()
    try:
        for img_path in image_files:
            p = normalize_path(img_path)
            if p.suffix.lower() not in IMAGE_EXTS:
                continue

            with Image.open(p) as image:
                frame_count = max(1, int(getattr(image, "n_frames", 1)))
                for frame_index in range(frame_count):
                    if frame_count > 1:
                        image.seek(frame_index)

                    x_dpi, y_dpi = _safe_image_dpi(image.info)
                    width_pt = image.width * 72.0 / x_dpi
                    height_pt = image.height * 72.0 / y_dpi
                    if width_pt <= 0 or height_pt <= 0:
                        raise ValueError(f"图片尺寸无效：{p.name}")

                    page = out_doc.new_page(width=width_pt, height=height_pt)

                    if frame_count == 1:
                        # Keep the original image stream when possible to avoid
                        # unnecessary re-encoding and quality loss.
                        page.insert_image(
                            page.rect,
                            filename=str(p),
                            keep_proportion=False,
                        )
                    else:
                        # Multi-frame formats (for example TIFF) need one PDF
                        # page per frame. Encode only the current frame to PNG.
                        frame = image.copy()
                        if frame.mode not in {"RGB", "RGBA", "L"}:
                            has_alpha = "A" in frame.getbands()
                            frame = frame.convert("RGBA" if has_alpha else "RGB")
                        buffer = io.BytesIO()
                        frame.save(buffer, format="PNG")
                        page.insert_image(
                            page.rect,
                            stream=buffer.getvalue(),
                            keep_proportion=False,
                        )

        if out_doc.page_count == 0:
            raise ValueError("未找到可转换的图片文件")

        out = ensure_parent_dir(output_path)
        out_doc.save(str(out))
        return out_doc.page_count
    finally:
        out_doc.close()


def encrypt_pdf(pdf_path: str | Path, output_path: str | Path, user_password: str, owner_password: str | None = None) -> int:
    if not user_password:
        raise ValueError("打开密码不能为空")
    reader = PdfReader(str(normalize_path(pdf_path)))
    if reader.is_encrypted:
        raise ValueError("该 PDF 已经加密，如需重新设置密码请先解密")
    if len(reader.pages) > MAX_PAGES_PER_PDF:
        raise ValueError(f"PDF 页数超过限制：最多 {MAX_PAGES_PER_PDF} 页")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if reader.metadata:
        writer.add_metadata(reader.metadata)
    writer.encrypt(user_password=user_password, owner_password=owner_password or user_password)
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return len(reader.pages)


def decrypt_pdf(pdf_path: str | Path, output_path: str | Path, password: str) -> int:
    if not password:
        raise ValueError("请输入原 PDF 密码")
    reader = PdfReader(str(normalize_path(pdf_path)))
    if not reader.is_encrypted:
        raise ValueError("该 PDF 未加密，无需解密")
    if not reader.decrypt(password):
        raise ValueError("密码错误，无法解密")
    if len(reader.pages) > MAX_PAGES_PER_PDF:
        raise ValueError(f"PDF 页数超过限制：最多 {MAX_PAGES_PER_PDF} 页")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if reader.metadata:
        writer.add_metadata(reader.metadata)
    out = ensure_parent_dir(output_path)
    with out.open("wb") as f:
        writer.write(f)
    return len(reader.pages)


def add_text_watermark(
    pdf_path: str | Path,
    output_path: str | Path,
    text: str,
    font_size: int = 36,
    range_text: str = "",
    position: str = "center",
) -> int:
    if not text.strip():
        raise ValueError("水印文字不能为空")
    p = normalize_path(pdf_path)
    out = ensure_parent_dir(output_path)
    doc = fitz.open(str(p))
    try:
        pages = parse_page_ranges(range_text, doc.page_count)
        for idx in pages:
            page = doc.load_page(idx)
            rect = page.rect
            text_width = fitz.get_text_length(text, fontname=CJK_FONT, fontsize=font_size)
            if position in {"top-left", "左上角"}:
                point = fitz.Point(36, 54)
            elif position in {"bottom-right", "右下角"}:
                point = fitz.Point(max(36, rect.width - text_width - 36), max(54, rect.height - 36))
            elif position in {"bottom-center", "底部居中"}:
                point = fitz.Point(max(36, (rect.width - text_width) / 2), max(54, rect.height - 36))
            else:
                point = fitz.Point(max(36, (rect.width - text_width) / 2), rect.height / 2)
            page.insert_text(point, text, fontname=CJK_FONT, fontsize=font_size, color=(0.72, 0.72, 0.72), overlay=True)
        doc.save(str(out), garbage=4, deflate=True)
        return len(pages)
    finally:
        doc.close()


def add_page_numbers(
    pdf_path: str | Path,
    output_path: str | Path,
    prefix: str = "第 ",
    suffix: str = " 页",
    start_number: int = 1,
    font_size: int = 10,
    range_text: str = "",
) -> int:
    p = normalize_path(pdf_path)
    out = ensure_parent_dir(output_path)
    doc = fitz.open(str(p))
    try:
        if doc.needs_pass:
            raise ValueError("该 PDF 已加密，请先解密后再添加页码")
        if doc.page_count > MAX_PAGES_PER_PDF:
            raise ValueError(f"PDF 页数超过限制：最多 {MAX_PAGES_PER_PDF} 页")
        pages = parse_page_ranges(range_text, doc.page_count)
        for n, idx in enumerate(pages, start=start_number):
            page = doc.load_page(idx)
            rect = page.rect
            text = f"{prefix}{n}{suffix}"
            text_width = fitz.get_text_length(text, fontname=CJK_FONT, fontsize=font_size)
            point = fitz.Point(max(36, (rect.width - text_width) / 2), rect.height - 28)
            page.insert_text(point, text, fontname=CJK_FONT, fontsize=font_size, color=(0, 0, 0), overlay=True)
        doc.save(str(out), garbage=4, deflate=True)
        return len(pages)
    finally:
        doc.close()
