"""Regression suite: blank-page suggestions require explicit human confirmation."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.blank_scan import scan_blank_pages
from app.services.workflow import WorkflowOptions, run_workflow


def sample_file(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page(width=595, height=842)  # Truly blank.
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "IMPORTANT CONTENT", fontsize=16)
    page = doc.new_page(width=595, height=842)
    # Light text/watermarks must never be silently considered blank.
    page.insert_text((70, 130), "FAINT WATERMARK", fontsize=11, color=(.96, .96, .96))
    doc.save(str(path))
    doc.close()
    return path


def poll(client, job_id):
    for _ in range(100):
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] in {"done", "failed", "cancelled"}:
            return data
        time.sleep(.03)
    pytest.fail("workflow job not completed")


def test_scan_only_suggests_blank_and_includes_preview(tmp_path):
    path = sample_file(tmp_path / "sample.pdf")
    result = scan_blank_pages([path])
    assert result["total_pages"] == 3
    assert result["scanned_pages"] == 3
    assert not result["truncated"]
    assert [(c["file_index"], c["page"]) for c in result["candidates"]] == [(0, 1)]
    assert result["candidates"][0]["image"].startswith("data:image/jpeg;base64,")
    with fitz.open(str(path)) as doc:
        assert doc.page_count == 3  # Scanning never modifies inputs.


def test_no_automatic_deletion_when_unconfirmed(tmp_path):
    original = sample_file(tmp_path / "sample.pdf")
    output, _, _ = run_workflow([original], tmp_path / "output", WorkflowOptions())
    with fitz.open(str(output)) as doc:
        assert doc.page_count == 3


def test_confirmed_deletion_keeps_contents_and_page_numbering(tmp_path):
    original = sample_file(tmp_path / "sample.pdf")
    options = WorkflowOptions(
        delete_pages={0: [1]},
        deletions_confirmed=True,
        page_size="A4",
        page_numbers=True,
    )
    result, _, _ = run_workflow([original], tmp_path / "output", options)
    with fitz.open(str(result)) as doc:
        assert doc.page_count == 2
        assert "IMPORTANT CONTENT" in doc[0].get_text()
        assert "FAINT WATERMARK" in doc[1].get_text()
    with fitz.open(str(original)) as doc:
        assert doc.page_count == 3  # Original is untouched.


def test_unconfirmed_deletions_are_rejected(tmp_path):
    path = sample_file(tmp_path / "sample.pdf")
    with pytest.raises(ValueError, match="必须明确确认"):
        run_workflow([path], tmp_path / "out", WorkflowOptions(delete_pages={0: [1]}))


def test_all_page_or_invalid_deletion_rejected(tmp_path):
    path = sample_file(tmp_path / "sample.pdf")
    for pages in ([1, 2, 3], [4], [0]):
        with pytest.raises(ValueError, match="不能删除|超出"):
            run_workflow([path], tmp_path / ("out" + str(len(pages))),
                         WorkflowOptions(delete_pages={0: pages}, deletions_confirmed=True))


def test_scan_api_then_workflow_api_removes_only_confirmed_page(tmp_path):
    path = sample_file(tmp_path / "sample.pdf")
    original = path.read_bytes()
    with TestClient(app) as client:
        preview = client.post("/api/pdf/workflow/blank-scan", files=[
            ("files", ("sample.pdf", original, "application/pdf")),
        ])
        assert preview.status_code == 200, preview.text
        assert [v["page"] for v in preview.json()["candidates"]] == [1]
        denied = client.post("/api/pdf/workflow", files=[
            ("files", ("sample.pdf", original, "application/pdf")),
        ], data={"settings": json.dumps({"delete_pages": {"0": [1]}})})
        # Workflow submits asynchronous processing; deletion guard is applied within job.
        if denied.status_code == 202:
            state = poll(client, denied.json()["job_id"])
            assert state["status"] == "failed"
        else:
            assert denied.status_code == 400

        allowed = client.post("/api/pdf/workflow", files=[
            ("files", ("sample.pdf", original, "application/pdf")),
        ], data={"settings": json.dumps({
            "delete_pages": {"0": [1]}, "deletions_confirmed": True,
            "page_size": "A4", "page_numbers": True
        })})
        assert allowed.status_code == 202, allowed.text
        job = poll(client, allowed.json()["job_id"])
        assert job["status"] == "done", job.get("error")
        response = client.get(job["download_url"])
        assert response.status_code == 200
        with fitz.open(stream=response.content, filetype="pdf") as doc:
            assert doc.page_count == 2
            assert "IMPORTANT CONTENT" in doc[0].get_text()


def test_scanner_rejects_invalid_pdf():
    with TestClient(app) as client:
        r = client.post("/api/pdf/workflow/blank-scan", files=[
            ("files", ("broken.pdf", b"%PDF-1.7\n", "application/pdf")),
        ])
    assert r.status_code == 400
