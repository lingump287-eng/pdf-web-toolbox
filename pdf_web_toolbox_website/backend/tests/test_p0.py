"""P0 security and configuration regression checks."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

# Run from backend/: python -m pytest -q tests/test_p0.py
os.environ.setdefault("PDF_TOOLBOX_TEMP_ROOT", tempfile.mkdtemp(prefix="pdf-toolbox-tests-"))
from app.main import app
from app.services import jobs
from app.utils import task_store


def test_config_is_valid_and_has_frontend_version():
    with TestClient(app) as client:
        response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["frontend_version"], str)
    assert isinstance(data["max_files_per_request"], int)


def test_health_is_available():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_frontend_route_does_not_serve_backend_code():
    with TestClient(app) as client:
        response = client.get("/app/main.py")
    assert "from fastapi import FastAPI" not in response.text


def test_cleanup_removes_stale_orphan_but_not_active_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "JOB_TTL_SECONDS", 60)
    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / "data").write_text("unused")
    os.utime(stale, (1, 1))
    active = tmp_path / "active"
    active.mkdir()
    os.utime(active, (1, 1))
    job = jobs.create_job(active)
    try:
        jobs.cleanup_orphaned_task_dirs()
        assert not stale.exists()
        assert active.exists()
    finally:
        task_store.update_job_record(tmp_path, job.id, {"worker_active": 0, "status": "failed"})
        task_store.release_task_lock(tmp_path, job.id)
        jobs.cleanup_job(job.id)
