from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

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
from app.utils.files import MAX_FILES_PER_REQUEST, new_task_dir, remove_tree, save_upload, zip_folder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pdf", tags=["pdf"])
PDF_ALLOWED = {".pdf"}
IMAGE_ALLOWED = IMAGE_EXTS


def file_response(path: Path, task_dir: Path, background_tasks: BackgroundTasks, media_type: str, filename: str) -> FileResponse:
    background_tasks.add_task(remove_tree, task_dir)
    return FileResponse(path=str(path), media_type=media_type, filename=filename, background=background_tasks)


def handle_error(task_dir: Path, error: Exception) -> None:
    remove_tree(task_dir)
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error))
    logger.exception("PDF processing failed", exc_info=error)
    raise HTTPException(status_code=500, detail="服务器处理失败，请确认文件有效后重试。")


def validate_file_count(files: list[UploadFile]) -> None:
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")


@router.post("/merge")
async def merge_endpoint(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    task_dir = new_task_dir()
    try:
        if len(files) < 2:
            raise ValueError("PDF 合并至少需要上传 2 个 PDF 文件")
        validate_file_count(files)
        input_dir = task_dir / "input"
        output = task_dir / "output" / "merged.pdf"
        paths = [await save_upload(f, input_dir, f"file_{i}.pdf", PDF_ALLOWED) for i, f in enumerate(files, start=1)]
        merge_pdfs(paths, output)
        return file_response(output, task_dir, background_tasks, "application/pdf", "merged.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/extract")
async def extract_endpoint(background_tasks: BackgroundTasks, file: UploadFile = File(...), range_text: str = Form("")):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "extracted.pdf"
        extract_pages(pdf, output, range_text)
        return file_response(output, task_dir, background_tasks, "application/pdf", "extracted.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/delete-pages")
async def delete_pages_endpoint(background_tasks: BackgroundTasks, file: UploadFile = File(...), range_text: str = Form(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "deleted_pages.pdf"
        delete_pages(pdf, output, range_text)
        return file_response(output, task_dir, background_tasks, "application/pdf", "deleted_pages.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/split")
async def split_endpoint(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output_dir = task_dir / "output" / "split"
        split_each_page(pdf, output_dir)
        output_zip = task_dir / "output" / "split_pages.zip"
        zip_folder(output_dir, output_zip)
        return file_response(output_zip, task_dir, background_tasks, "application/zip", "split_pages.zip")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/rotate")
async def rotate_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    degrees: int = Form(90),
    mode: Literal["all", "odd", "even", "range"] = Form("all"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "rotated.pdf"
        rotate_pdf(pdf, output, degrees, mode, range_text)
        return file_response(output, task_dir, background_tasks, "application/pdf", "rotated.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/to-images")
async def to_images_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    dpi: int = Form(200),
    image_format: Literal["png", "jpg"] = Form("png"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output_dir = task_dir / "output" / "images"
        pdf_to_images(pdf, output_dir, dpi=dpi, image_format=image_format, range_text=range_text)
        output_zip = task_dir / "output" / "pdf_images.zip"
        zip_folder(output_dir, output_zip)
        return file_response(output_zip, task_dir, background_tasks, "application/zip", "pdf_images.zip")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/images-to-pdf")
async def images_to_pdf_endpoint(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    task_dir = new_task_dir()
    try:
        if not files:
            raise ValueError("请至少上传 1 张图片")
        validate_file_count(files)
        input_dir = task_dir / "input"
        output = task_dir / "output" / "images.pdf"
        paths = [await save_upload(f, input_dir, f"image_{i}.png", IMAGE_ALLOWED) for i, f in enumerate(files, start=1)]
        images_to_pdf(paths, output)
        return file_response(output, task_dir, background_tasks, "application/pdf", "images.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/watermark")
async def watermark_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    text: str = Form(...),
    font_size: int = Form(36),
    position: Literal["center", "top-left", "bottom-center", "bottom-right"] = Form("center"),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "watermarked.pdf"
        add_text_watermark(pdf, output, text=text, font_size=font_size, range_text=range_text, position=position)
        return file_response(output, task_dir, background_tasks, "application/pdf", "watermarked.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/page-number")
async def page_number_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prefix: str = Form("第 "),
    suffix: str = Form(" 页"),
    start_number: int = Form(1),
    font_size: int = Form(10),
    range_text: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "page_numbered.pdf"
        add_page_numbers(pdf, output, prefix=prefix, suffix=suffix, start_number=start_number, font_size=font_size, range_text=range_text)
        return file_response(output, task_dir, background_tasks, "application/pdf", "page_numbered.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/encrypt")
async def encrypt_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_password: str = Form(...),
    owner_password: str = Form(""),
):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "encrypted.pdf"
        encrypt_pdf(pdf, output, user_password=user_password, owner_password=owner_password or None)
        return file_response(output, task_dir, background_tasks, "application/pdf", "encrypted.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/decrypt")
async def decrypt_endpoint(background_tasks: BackgroundTasks, file: UploadFile = File(...), password: str = Form(...)):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        output = task_dir / "output" / "decrypted.pdf"
        decrypt_pdf(pdf, output, password=password)
        return file_response(output, task_dir, background_tasks, "application/pdf", "decrypted.pdf")
    except Exception as e:
        handle_error(task_dir, e)


@router.post("/info")
async def info_endpoint(file: UploadFile = File(...), password: str = Form("")):
    task_dir = new_task_dir()
    try:
        pdf = await save_upload(file, task_dir / "input", "input.pdf", PDF_ALLOWED)
        info = pdf_basic_info(pdf, password=password or None)
        remove_tree(task_dir)
        return JSONResponse(info)
    except Exception as e:
        handle_error(task_dir, e)
