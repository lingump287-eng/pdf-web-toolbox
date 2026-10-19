"""Composable PDF processing for the first, non-AI workflow release.

Operations execute in a safe fixed order: input validation -> ordering ->
page normalization/merge -> page numbering -> lossless size optimization.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Literal, Sequence

import fitz
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from app.services.pdf_ops import add_page_numbers, merge_pdfs
from app.utils.files import MAX_PAGES_PER_PDF, MAX_TOTAL_PAGES, zip_folder

PageSize = Literal["keep", "A4", "A3", "Letter"]
SortMode = Literal["upload", "filename"]

PAGE_SIZES = {
    "A4": (595.2756, 841.8898),
    "A3": (841.8898, 1190.5512),
    "Letter": (612.0, 792.0),
}


class WorkflowOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_size: PageSize = "keep"
    sort_by: SortMode = "upload"
    merge: bool = True
    page_numbers: bool = False
    compress: bool = False
    start_number: int = Field(default=1, ge=1, le=10000)


def natural_name_key(path: Path):
    return [int(s) if s.isdigit() else s.casefold()
            for s in re.split(r"(\d+)", path.name)]


def validate_pdfs(paths: Sequence[Path]) -> int:
    if not paths:
        raise ValueError("请至少上传一个 PDF 文件")
    total = 0
    for path in paths:
        try:
            with path.open("rb") as fd:
                if fd.read(5) != b"%PDF-":
                    raise ValueError("文件头不是有效 PDF")
            # PyMuPDF detects encryption without requiring optional AES libraries.
            with fitz.open(str(path)) as peek:
                if peek.needs_pass:
                    raise ValueError("文件已加密，请先解密")
            reader = PdfReader(str(path), strict=True)
            if reader.is_encrypted:
                raise ValueError("文件已加密，请先解密")
            count = len(reader.pages)
        except Exception as exc:
            raise ValueError(f"无法读取 PDF 文件 {path.name}：{exc}") from exc
        if count < 1 or count > MAX_PAGES_PER_PDF:
            raise ValueError(f"{path.name} 页数不在允许范围内")
        total += count
        if total > MAX_TOTAL_PAGES:
            raise ValueError(f"处理总页数超过上限：{MAX_TOTAL_PAGES} 页")
    return total


def normalize_pdf_pages(paths: Sequence[Path], output: Path, target_size: str = "A4") -> int:
    """Vector-preserving page normalization; never rasterize source pages."""
    if target_size not in PAGE_SIZES:
        raise ValueError("不支持的页面尺寸")
    width, height = PAGE_SIZES[target_size]
    result = fitz.open()
    try:
        count = 0
        for path in paths:
            with fitz.open(str(path)) as source:
                if source.needs_pass:
                    raise ValueError(f"文件已加密：{path.name}")
                for idx in range(source.page_count):
                    src_page = source[idx]
                    # Landscape pages stay landscape, avoiding 90-degree shrinkage.
                    target_width, target_height = (height, width) if src_page.rect.width > src_page.rect.height else (width, height)
                    page = result.new_page(width=target_width, height=target_height)
                    # show_pdf_page embeds vector text, links aside; preserves proportions.
                    page.show_pdf_page(page.rect, source, idx, keep_proportion=True)
                    count += 1
        if count == 0:
            raise ValueError("没有可处理的 PDF 页面")
        output.parent.mkdir(parents=True, exist_ok=True)
        result.save(str(output), garbage=4, deflate=True)
        return count
    finally:
        result.close()


def optimize_pdf(source: Path, output: Path) -> None:
    """Safe lossless cleanup; use the original if optimization grows the file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(str(source)) as doc:
        doc.save(str(output), garbage=4, deflate=True)
    if output.stat().st_size >= source.stat().st_size:
        shutil.copyfile(source, output)


def run_workflow(
    pdf_files: Sequence[Path], result_dir: Path, options: WorkflowOptions,
    progress=None, cancelled=None,
) -> tuple[Path, str, str]:
    paths = [Path(p) for p in pdf_files]
    total_pages = validate_pdfs(paths)
    if options.sort_by == "filename":
        paths.sort(key=natural_name_key)
    result_dir.mkdir(parents=True, exist_ok=True)

    def stage(value: int, message: str):
        if cancelled and cancelled():
            raise ValueError("任务已取消")
        if progress:
            progress(value, message)

    def make_pdf(inputs: Sequence[Path], output: Path):
        stage(25, "正在检查并调整页面")
        base = result_dir / (output.stem + "_base.pdf")
        if options.page_size == "keep":
            merge_pdfs(inputs, base)
        else:
            normalize_pdf_pages(inputs, base, options.page_size)

        current = base
        if options.page_numbers:
            stage(55, "正在添加页码")
            numbered = result_dir / (output.stem + "_numbered.pdf")
            add_page_numbers(current, numbered, prefix="", suffix="", start_number=options.start_number)
            current = numbered
        if options.compress:
            stage(75, "正在优化文件大小")
            optimized = result_dir / (output.stem + "_optimized.pdf")
            optimize_pdf(current, optimized)
            current = optimized
        shutil.copyfile(current, output)
        with fitz.open(str(output)) as doc:
            expected = sum(len(PdfReader(str(p)).pages) for p in inputs)
            if doc.page_count != expected:
                raise ValueError("结果页数与输入不一致，已停止交付")
        return output

    if options.merge:
        output = make_pdf(paths, result_dir / "workflow_result.pdf")
        if total_pages < 1:
            raise ValueError("输出 PDF 无页面")
        stage(95, "正在准备结果")
        return output, "workflow_result.pdf", "application/pdf"

    output_dir = result_dir / "documents"
    output_dir.mkdir()
    for i, path in enumerate(paths, start=1):
        stage(20 + round(60 * i / len(paths)), f"正在处理第 {i}/{len(paths)} 份文件")
        make_pdf([path], output_dir / f"document_{i:03d}.pdf")
    archive = result_dir / "workflow_result.zip"
    zip_folder(output_dir, archive)
    stage(95, "正在准备打包结果")
    return archive, "workflow_result.zip", "application/zip"
