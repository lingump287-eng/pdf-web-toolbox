from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Literal

import fitz
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse\nfrom starlette.concurrency import run_in_threadpool

from app.services.jobs import (
    cancel_job,
    cleanup_job,
    create_job,
    get_job,
    job_payload,
    submit_job,
)
from app.services.pdf_ops import (
    IMAGE_EXTS,
    add_page_numbers,
    add_text_watermark,
    decrypt_pdf,
    delete_pages,
    encrypt_pdf,
    extract_pages,
    images_to_pdf,
    merge_pdfs,
    pdf_basic_info,
    pdf_to_images,
    rotate_pdf,
    split_each_page,
)
from app.utils.files import (
    MAX_DPI,
    MAX_FILES_PER_REQUEST,
    MAX_PREVIEW_PAGES,
    MAX_TOTAL_PAGES,
    new_task_dir,
    remove_tree,
    save_upload,
    validate_total_size,
    zip_folder,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pdf", tags=["pdf"])
job_router = APIRouter(prefix="/api/jobs", tags=["jobs"])
PDF_ALLOWED = {".pdf"}
IMAGE_ALLOWED = IMAGE_EXTS


def handle_upload_error(task_dir: Path, error: Exception) -> None:
    remove_tree(task_dir)
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error))
    logger.exception("PDF upload failed", exc_info=error)
    raise HTTPException(status_code=500, detail="服务器处理失败，请确认文件有效后重试。")


def validate_file_count(files: list[UploadFile]) -> None:
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")


def accepted(job_id: str) -> JSONResponse:
    return JSONResponse(
        {"job_id": job_id, "status": "queued", "status_url": f"/api/jobs/{job_id}"},
        status_code=202,
    )


async def save_one_pdf(file: UploadFile, task_dir: Path) -> Path:
    path = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
    validate_total_size([path])
    return path


@router.post("/merge")
async def merge_endpoint(files: list[UploadFile] = File(...)):
    task_dir = new_task_dir()
    try:
        if len(files) < 2:
            raise ValueError("PDF 合并至少需要上传 2 个 PDF 文件")
        validate_file_count(files)
        input_dir = task_dir / "input"
        paths = [await save_upload(f, input_dir, f"file_{i}.pdf", PDF_ALLOWED) for i, f in enumerate(files, start=1)]
        validate_total_size(paths)
        output = task_dir / "output" / "merged.pdf"
        job = create_job(task_dir)

        def work(progress, cancelled):
            progress(20, "正在检查 PDF")
            if cancelled():
                raise ValueError("任务已取消")
            progress(45, "正在合并页面")
            merge_pdfs(paths, output)
            progress(90, "正在生成结果")
            return output, "merged.pdf", "application/pdf"

        submit_job(job.id, work)
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/extract")
async def extract_endpoint(file: UploadFile = File(...), range_text: str = Form("")):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "extracted.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: extract_pages(pdf, output, range_text),
            output, "extracted.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/delete-pages")
async def delete_pages_endpoint(file: UploadFile = File(...), range_text: str = Form(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "deleted_pages.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: delete_pages(pdf, output, range_text),
            output, "deleted_pages.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/split")
async def split_endpoint(file: UploadFile = File(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output_dir = task_dir / "output" / "split"
        output_zip = task_dir / "output" / "split_pages.zip"
        job = create_job(task_dir)

        def work(progress, cancelled):
            progress(30, "正在拆分页面")
            if cancelled():
                raise ValueError("任务已取消")
            split_each_page(pdf, output_dir)
            progress(75, "正在打包 ZIP")
            if cancelled():
                raise ValueError("任务已取消")
            zip_folder(output_dir, output_zip)
            return output_zip, "split_pages.zip", "application/zip"

        submit_job(job.id, work)
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/rotate")
async def rotate_endpoint(
    file: UploadFile = File(...),
    degrees: int = Form(90),
    mode: Literal["all", "odd", "even", "range"] = Form("all"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "rotated.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: rotate_pdf(pdf, output, degrees, mode, range_text),
            output, "rotated.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/to-images")
async def to_images_endpoint(
    file: UploadFile = File(...),
    dpi: int = Form(200),
    image_format: Literal["png", "jpg"] = Form("png"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        if dpi < 72 or dpi > MAX_DPI:
            raise ValueError(f"DPI 必须在 72 到 {MAX_DPI} 之间")
        pdf = await save_one_pdf(file, task_dir)
        output_dir = task_dir / "output" / "images"
        output_zip = task_dir / "output" / "pdf_images.zip"
        job = create_job(task_dir)

        def work(progress, cancelled):
            progress(20, "正在渲染 PDF 页面")
            if cancelled():
                raise ValueError("任务已取消")
            pdf_to_images(pdf, output_dir, dpi=dpi, image_format=image_format, range_text=range_text)
            progress(80, "正在打包图片")
            if cancelled():
                raise ValueError("任务已取消")
            zip_folder(output_dir, output_zip)
            return output_zip, "pdf_images.zip", "application/zip"

        submit_job(job.id, work)
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/images-to-pdf")
async def images_to_pdf_endpoint(files: list[UploadFile] = File(...)):
    task_dir = new_task_dir()
    try:
        if not files:
            raise ValueError("请至少上传 1 张图片")
        validate_file_count(files)
        input_dir = task_dir / "input"
        paths = [await save_upload(f, input_dir, f"image_{i}.png", IMAGE_ALLOWED) for i, f in enumerate(files, start=1)]
        validate_total_size(paths)
        output = task_dir / "output" / "images.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: images_to_pdf(paths, output),
            output, "images.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/watermark")
async def watermark_endpoint(
    file: UploadFile = File(...),
    text: str = Form(...),
    font_size: int = Form(36),
    position: Literal["center", "top-left", "bottom-center", "bottom-right"] = Form("center"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "watermarked.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: add_text_watermark(
                pdf, output, text=text, font_size=font_size, range_text=range_text, position=position
            ),
            output, "watermarked.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/page-number")
async def page_number_endpoint(
    file: UploadFile = File(...),
    prefix: str = Form("第 "),
    suffix: str = Form(" 页"),
    start_number: int = Form(1),
    font_size: int = Form(10),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "page_numbered.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: add_page_numbers(
                pdf, output, prefix=prefix, suffix=suffix, start_number=start_number,
                font_size=font_size, range_text=range_text
            ),
            output, "page_numbered.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/encrypt")
async def encrypt_endpoint(
    file: UploadFile = File(...),
    user_password: str = Form(...),
    owner_password: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "encrypted.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: encrypt_pdf(
                pdf, output, user_password=user_password, owner_password=owner_password or None
            ),
            output, "encrypted.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/decrypt")
async def decrypt_endpoint(file: UploadFile = File(...), password: str = Form(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        output = task_dir / "output" / "decrypted.pdf"
        job = create_job(task_dir)
        submit_job(job.id, lambda progress, cancelled: _simple_pdf_job(
            progress, cancelled, lambda: decrypt_pdf(pdf, output, password=password),
            output, "decrypted.pdf"
        ))
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


@router.post("/info")
async def info_endpoint(file: UploadFile = File(...), password: str = Form("")):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        job = create_job(task_dir)

        def work(progress, cancelled):
            progress(45, "正在读取 PDF 信息")
            if cancelled():
                raise ValueError("任务已取消")
            return pdf_basic_info(pdf, password=password or None)

        submit_job(job.id, work)
        return accepted(job.id)
    except Exception as e:
        handle_upload_error(task_dir, e)


def _build_preview(pdf: Path) -> dict:
    doc = fitz.open(str(pdf))
    try:
        if doc.needs_pass:
            raise ValueError("加密 PDF 暂不支持预览，请先解密")
        if doc.page_count > MAX_TOTAL_PAGES:
            raise ValueError(f"PDF 页数超过限制：最多 {MAX_TOTAL_PAGES} 页")
        count = min(doc.page_count, MAX_PREVIEW_PAGES)
        pages = []
        matrix = fitz.Matrix(0.42, 0.42)
        for i in range(count):
            pix = doc.load_page(i).get_pixmap(matrix=matrix, alpha=False)
            data = base64.b64encode(pix.tobytes("jpeg", jpg_quality=52)).decode("ascii")
            pages.append({"page": i + 1, "image": f"data:image/jpeg;base64,{data}"})
        return {"page_count": doc.page_count, "previewed": count, "pages": pages}
    finally:
        doc.close()


@router.post("/preview")
async def preview_endpoint(file: UploadFile = File(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_one_pdf(file, task_dir)
        return await run_in_threadpool(_build_preview, pdf)
    except Exception as e:
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e))
        handle_upload_error(task_dir, e)
    finally:
        remove_tree(task_dir)


def _simple_pdf_job(progress, cancelled, action, output: Path, name: str):
    progress(35, "正在处理 PDF")
    if cancelled():
        raise ValueError("任务已取消")
    action()
    if cancelled():
        raise ValueError("任务已取消")
    progress(90, "正在准备下载")
    return output, name, "application/pdf"


@job_router.get("/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job_payload(job)


@job_router.post("/{job_id}/cancel")
def job_cancel(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if not cancel_job(job_id):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")
    return {"status": "cancelled"}


@job_router.get("/{job_id}/download")
def job_download(job_id: str, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not job or job.status != "done" or not job.result_path or not job.result_path.exists():
        raise HTTPException(status_code=404, detail="结果不存在、尚未完成或已过期")
    background_tasks.add_task(cleanup_job, job_id)
    return FileResponse(
        str(job.result_path),
        media_type=job.media_type,
        filename=job.result_name or job.result_path.name,
        background=background_tasks,
    )
