"""Generated-file regression tests for real PDF operations and the first workflow MVP."""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.pdf_ops import images_to_pdf, merge_pdfs, pdf_to_images
from app.services.workflow import WorkflowOptions, normalize_pdf_pages, run_workflow

A4 = (595.2756, 841.8898)
A3 = (841.8898, 1190.5512)


def make_pdf(path: Path, label: str, width: float = A4[0], height: float = A4[1]) -> Path:
    with fitz.open() as doc:
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 70), label, fontsize=18)
        doc.save(str(path))
    return path


def pdf_bytes(label: str) -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_text((40, 70), label)
        return doc.tobytes()


def poll(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(.025)
    pytest.fail("PDF background task did not finish in time")


@pytest.mark.parametrize("image_format", ["png", "jpg"])
def test_pdf_image_pdf_roundtrip_keeps_physical_page_size(tmp_path, image_format):
    original = make_pdf(tmp_path / "original.pdf", "PAGE_A4")
    folder = tmp_path / "images"
    pdf_to_images(original, folder, dpi=200, image_format=image_format)
    restored = tmp_path / "restored.pdf"
    images_to_pdf(sorted(folder.iterdir()), restored)
    merged = tmp_path / "merged.pdf"
    merge_pdfs([original, restored], merged)
    with fitz.open(str(merged)) as doc:
        assert doc.page_count == 2
        first, second = doc[0].rect, doc[1].rect
        assert abs(first.width - second.width) < 1.0
        assert abs(first.height - second.height) < 1.0


def test_merge_keeps_order_and_mixed_sizes(tmp_path):
    first = make_pdf(tmp_path / "first.pdf", "FIRST", *A4)
    second = make_pdf(tmp_path / "second.pdf", "SECOND", A3[1], A3[0])
    output = tmp_path / "out.pdf"
    assert merge_pdfs([second, first], output) == 2
    with fitz.open(str(output)) as doc:
        assert doc.page_count == 2
        assert "SECOND" in doc[0].get_text()
        assert "FIRST" in doc[1].get_text()
        assert doc[0].rect.width > doc[0].rect.height
        assert doc[1].rect.width < doc[1].rect.height


def test_normalize_preserves_text_and_landscape(tmp_path):
    p1 = make_pdf(tmp_path / "wide.pdf", "WIDE", A3[1], A3[0])
    p2 = make_pdf(tmp_path / "normal.pdf", "NORMAL")
    output = tmp_path / "normalized.pdf"
    assert normalize_pdf_pages([p1, p2], output, "A4") == 2
    with fitz.open(str(output)) as doc:
        assert doc.page_count == 2
        assert abs(doc[0].rect.width - A4[1]) < 1
        assert abs(doc[0].rect.height - A4[0]) < 1
        assert abs(doc[1].rect.width - A4[0]) < 1
        assert abs(doc[1].rect.height - A4[1]) < 1
        assert "WIDE" in doc[0].get_text()
        assert "NORMAL" in doc[1].get_text()


def test_workflow_sorts_naturally_and_retains_searchable_text(tmp_path):
    p10 = make_pdf(tmp_path / "report10.pdf", "TEN")
    p2 = make_pdf(tmp_path / "report2.pdf", "TWO")
    result, filename, media = run_workflow(
        [p10, p2], tmp_path / "out",
        WorkflowOptions(page_size="A4", sort_by="filename", page_numbers=True, compress=True),
    )
    assert filename.endswith(".pdf") and media == "application/pdf"
    with fitz.open(str(result)) as doc:
        assert doc.page_count == 2
        assert "TWO" in doc[0].get_text()
        assert "TEN" in doc[1].get_text()


def test_workflow_zip_keeps_two_individual_files(tmp_path):
    p1 = make_pdf(tmp_path / "a.pdf", "ALPHA")
    p2 = make_pdf(tmp_path / "b.pdf", "BETA")
    path, filename, media = run_workflow(
        [p1, p2], tmp_path / "out", WorkflowOptions(merge=False, page_size="Letter")
    )
    assert filename.endswith(".zip") and media == "application/zip"
    with zipfile.ZipFile(path) as archive:
        pdfs = sorted(n for n in archive.namelist() if n.endswith(".pdf"))
        assert len(pdfs) == 2
        for name in pdfs:
            with fitz.open(stream=archive.read(name), filetype="pdf") as doc:
                assert doc.page_count == 1
                assert abs(doc[0].rect.width - 612) < 1


def test_api_merge_accepts_same_filename_without_overwriting():
    with TestClient(app) as client:
        response = client.post("/api/pdf/merge", files=[
            ("files", ("same.pdf", pdf_bytes("FIRST"), "application/pdf")),
            ("files", ("same.pdf", pdf_bytes("SECOND"), "application/pdf")),
        ])
        assert response.status_code == 202
        job = poll(client, response.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        result = client.get(job["download_url"])
        assert result.status_code == 200
        with fitz.open(stream=result.content, filetype="pdf") as doc:
            assert doc.page_count == 2
            assert "FIRST" in doc[0].get_text()
            assert "SECOND" in doc[1].get_text()


def test_workflow_api_e2e():
    with TestClient(app) as client:
        response = client.post("/api/pdf/workflow", data={
            "settings": json.dumps({
                "sort_by": "filename", "page_size": "A4",
                "merge": True, "page_numbers": True, "compress": True,
            })
        }, files=[
            ("files", ("10.pdf", pdf_bytes("TEN"), "application/pdf")),
            ("files", ("2.pdf", pdf_bytes("TWO"), "application/pdf")),
        ])
        assert response.status_code == 202, response.text
        job = poll(client, response.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        downloaded = client.get(job["download_url"])
        assert downloaded.status_code == 200
        with fitz.open(stream=downloaded.content, filetype="pdf") as doc:
            assert doc.page_count == 2
            assert "TWO" in doc[0].get_text()
            assert "TEN" in doc[1].get_text()


def test_workflow_rejects_unsupported_settings():
    with TestClient(app) as client:
        response = client.post("/api/pdf/workflow", data={
            "settings": json.dumps({"page_size": "INVALID"})
        }, files=[("files", ("valid.pdf", pdf_bytes("VALID"), "application/pdf"))])
    assert response.status_code == 400


def test_invalid_pdf_rejected_without_job():
    with TestClient(app) as client:
        response = client.post("/api/pdf/merge", files=[
            ("files", ("one.pdf", pdf_bytes("OK"), "application/pdf")),
            ("files", ("invalid.pdf", b"not actually a pdf", "application/pdf")),
        ])
    assert response.status_code == 400


def test_encrypted_pdf_fails_gracefully(tmp_path):
    original = make_pdf(tmp_path / "original.pdf", "PRIVATE")
    protected = tmp_path / "protected.pdf"
    with fitz.open(str(original)) as doc:
        doc.save(str(protected), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="test-pass", owner_pw="owner-pass")
    with pytest.raises(ValueError, match="加密"):
        run_workflow([protected], tmp_path / "out", WorkflowOptions())


def test_workflow_rejects_empty_pdf(tmp_path):
    broken = tmp_path / "empty.pdf"
    broken.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(ValueError, match="无法读取"):
        run_workflow([broken], tmp_path / "out", WorkflowOptions())
