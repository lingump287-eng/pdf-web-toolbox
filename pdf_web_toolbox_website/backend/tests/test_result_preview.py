"""Real generated-PDF integration checks for post-processing result previews."""
from __future__ import annotations

import io
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import jobs
from app.utils import task_store
from app.services.result_preview import result_preview_jpeg


def make_pdf(label: str) -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((70, 100), label, fontsize=18)
        return doc.tobytes()


def await_completion(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        res = client.get(f"/api/jobs/{job_id}")
        assert res.status_code == 200
        result = res.json()
        if result["status"] in {"done", "failed", "cancelled"}:
            return result
        time.sleep(0.025)
    pytest.fail("Timed out waiting for PDF result")


def test_pdf_result_preview_and_summary():
    with TestClient(app) as client:
        submit = client.post("/api/pdf/merge", files=[
            ("files", ("first.pdf", make_pdf("FIRST"), "application/pdf")),
            ("files", ("second.pdf", make_pdf("SECOND"), "application/pdf")),
        ])
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        finished = await_completion(client, job_id)
        assert finished["status"] == "done", finished.get("error")
        summary = client.get(f"/api/jobs/{job_id}/result-summary")
        assert summary.status_code == 200
        assert summary.headers["cache-control"] == "private, no-store"
        assert summary.json()["kind"] == "pdf"
        assert summary.json()["page_count"] == 2
        assert summary.json()["size_bytes"] > 100
        first = client.get(f"/api/jobs/{job_id}/result-preview?page=1")
        second = client.get(f"/api/jobs/{job_id}/result-preview?page=2")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.headers["content-type"].startswith("image/jpeg")
        assert first.headers["cache-control"] == "private, no-store"
        assert first.content[:2] == b"\xff\xd8"
        assert second.content[:2] == b"\xff\xd8"
        assert first.content != second.content
        assert client.get(f"/api/jobs/{job_id}/result-preview?page=3").status_code == 400
        assert client.get(f"/api/jobs/{job_id}/result-preview?page=-5").status_code == 400
        assert client.get(f"/api/jobs/{job_id}/result-preview?file_index=1").status_code == 400


def test_zip_workflow_result_metadata_and_member_preview():
    with TestClient(app) as client:
        submit = client.post(
            "/api/pdf/workflow",
            data={"settings": json.dumps({"merge": False, "page_size": "A4"})},
            files=[
                ("files", ("one.pdf", make_pdf("ONE"), "application/pdf")),
                ("files", ("two.pdf", make_pdf("TWO"), "application/pdf")),
            ],
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        done = await_completion(client, job_id)
        assert done["status"] == "done", done.get("error")
        summary = client.get(f"/api/jobs/{job_id}/result-summary")
        assert summary.status_code == 200
        data = summary.json()
        assert data["kind"] == "zip"
        assert data["total_files"] == 2
        assert all(file["previewable"] for file in data["files"])
        a = client.get(f"/api/jobs/{job_id}/result-preview?file_index=0&page=1")
        b = client.get(f"/api/jobs/{job_id}/result-preview?file_index=1&page=1")
        assert a.status_code == 200
        assert b.status_code == 200
        assert a.content[:2] == b"\xff\xd8" and b.content[:2] == b"\xff\xd8"
        assert a.content != b.content
        assert client.get(f"/api/jobs/{job_id}/result-preview?file_index=2&page=1").status_code == 400


def test_results_unavailable_when_not_complete_or_unknown(tmp_path):
    queued = jobs.create_job(tmp_path / "task")
    try:
        with TestClient(app) as client:
            for endpoint in ("result-summary", "result-preview"):
                assert client.get(f"/api/jobs/{queued.id}/{endpoint}").status_code == 404
                assert client.get(f"/api/jobs/{'0'*32}/{endpoint}").status_code == 404
    finally:
        task_store.update_job_record(tmp_path, queued.id, {"worker_active": 0, "status": "failed"})
        task_store.release_task_lock(tmp_path, queued.id)
        jobs.cleanup_job(queued.id)


def test_output_path_cannot_escape_job_directory(tmp_path):
    job = jobs.create_job(tmp_path / "inside")
    outside = tmp_path / "private.pdf"
    outside.write_bytes(make_pdf("SECRET"))
    task_store.update_job_record(tmp_path, job.id, {
        "status": "done",
        "worker_active": 0,
        "result_path": str(outside),
        "result_name": "private.pdf",
    })
    task_store.release_task_lock(tmp_path, job.id)
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/jobs/{job.id}/result-summary").status_code == 404
            assert client.get(f"/api/jobs/{job.id}/result-preview").status_code == 404
    finally:
        jobs.cleanup_job(job.id)


def test_invalid_pages_do_not_render(tmp_path):
    f = tmp_path / "simple.pdf"
    f.write_bytes(make_pdf("HI"))
    with pytest.raises(ValueError, match="页码"):
        result_preview_jpeg(f, page=1001)
    with pytest.raises(ValueError, match="页码"):
        result_preview_jpeg(f, page=0)
