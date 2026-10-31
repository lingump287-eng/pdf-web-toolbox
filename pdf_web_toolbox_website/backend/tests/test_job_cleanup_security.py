from __future__ import annotations

import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import jobs
from app.utils import files, task_store


def _child_update_job(root_text: str, job_id: str, ready) -> None:
    root = Path(root_text)
    task_store.update_job_record(root, job_id, {
        "progress": 73,
        "message": "updated by another process",
        "updated_at": time.time(),
    })
    ready.set()


def _child_hold_task_lock(root_text: str, job_id: str, ready, release, acquired, crash: bool = False) -> None:
    root = Path(root_text)
    with task_store.registry_lock(root):
        ok = task_store.acquire_task_lock(root, job_id, blocking=False)
    acquired.value = 1 if ok else 0
    ready.set()
    if not ok:
        return
    if crash:
        # Simulate an abrupt worker-process exit. The OS must release its lock.
        os._exit(0)
    release.wait(15)
    task_store.release_task_lock(root, job_id)


def _child_cleanup_orphans(root_text: str, ready) -> None:
    jobs.cleanup_orphaned_task_dirs(Path(root_text))
    ready.set()


@pytest.fixture
def executor(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=1)
    futures = []
    submit = pool.submit

    def capture(fn):
        future = submit(fn)
        futures.append(future)
        return future

    monkeypatch.setattr(jobs, "_executor", type("Executor", (), {"submit": staticmethod(capture)})())
    yield futures
    pool.shutdown(wait=True)


@pytest.mark.parametrize("raises", [False, True])
def test_cancelled_worker_protected_until_exit(tmp_path, monkeypatch, executor, raises):
    monkeypatch.setattr(jobs, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "JOB_TTL_SECONDS", 1)
    task = tmp_path / "active"
    task.mkdir()
    (task / "input.pdf").write_bytes(b"private")
    job = jobs.create_job(task)
    started, release = threading.Event(), threading.Event()

    def work(progress, cancelled):
        started.set()
        assert release.wait(5)
        assert task.exists() and cancelled()
        if raises:
            raise ValueError("cancelled")
        return task / "output.pdf", "output.pdf", "application/pdf"

    try:
        jobs.submit_job(job.id, work)
        assert started.wait(5)
        assert jobs.cancel_job(job.id)
        task_store.update_job_record(tmp_path, job.id, {"updated_at": time.time() - 10})
        os.utime(task, (1, 1))
        jobs.cleanup_expired_jobs()
        jobs.cleanup_orphaned_task_dirs(tmp_path)
        jobs.cleanup_job(job.id)
        assert task.exists() and jobs.get_job(job.id) is job
        assert job.worker_active
    finally:
        release.set()
        executor[0].result(timeout=5)
    assert not job.worker_active and job.status == "cancelled"
    task_store.update_job_record(tmp_path, job.id, {"updated_at": time.time() - 10})
    jobs.cleanup_expired_jobs()
    assert not task.exists() and jobs.get_job(job.id) is None


def test_queued_cancel_and_base_exception_release_reservation(tmp_path, executor):
    entered, release = threading.Event(), threading.Event()
    first = jobs.create_job(tmp_path / "first")
    second = jobs.create_job(tmp_path / "second")

    def block(progress, cancelled):
        entered.set()
        assert release.wait(5)
        raise SystemExit("worker exited abnormally")

    def must_not_run(progress, cancelled):
        pytest.fail("cancelled queued worker was called")

    try:
        jobs.submit_job(first.id, block)
        assert entered.wait(5)
        jobs.submit_job(second.id, must_not_run)
        assert jobs.cancel_job(second.id)
        jobs.cleanup_job(second.id)
        assert jobs.get_job(second.id) is second
    finally:
        release.set()
    with pytest.raises(SystemExit):
        executor[0].result(timeout=5)
    executor[1].result(timeout=5)
    assert first.status == "failed" and not first.worker_active
    assert second.status == "cancelled" and not second.worker_active
    jobs.cleanup_job(first.id)
    jobs.cleanup_job(second.id)


def test_duplicate_submission_cannot_release_the_active_worker_lock(tmp_path, executor):
    task = tmp_path / "single-worker"
    job = jobs.create_job(task)
    started, release = threading.Event(), threading.Event()
    result = task / "output" / "result.pdf"
    result.parent.mkdir(parents=True, exist_ok=True)

    def work(progress, cancelled):
        started.set()
        assert release.wait(5)
        result.write_bytes(b"done")
        return result, "result.pdf", "application/pdf"

    try:
        jobs.submit_job(job.id, work)
        assert started.wait(5)
        with pytest.raises(RuntimeError, match="already been submitted"):
            jobs.submit_job(job.id, work)
        jobs.cleanup_job(job.id)
        assert task.exists() and job.worker_active
    finally:
        release.set()
    executor[0].result(timeout=5)
    assert job.status == "done" and not job.worker_active
    jobs.cleanup_job(job.id)
    assert not task.exists()


@pytest.mark.parametrize("raises", [False, True])
def test_failed_deletion_is_retried(tmp_path, monkeypatch, raises):
    task = tmp_path / "job"
    task.mkdir()
    job = jobs.create_job(task)
    task_store.update_job_record(tmp_path, job.id, {"worker_active": 0, "status": "failed"})
    task_store.release_task_lock(tmp_path, job.id)
    actual = jobs.remove_tree

    def fail_removal(path):
        if raises:
            raise OSError("simulated cleanup failure")

    monkeypatch.setattr(jobs, "remove_tree", fail_removal)
    jobs.cleanup_job(job.id)
    assert jobs.get_job(job.id) is job and task.exists()
    monkeypatch.setattr(jobs, "remove_tree", actual)
    jobs.cleanup_job(job.id)
    assert jobs.get_job(job.id) is None and not task.exists()


def test_submission_failure_releases_reservation(tmp_path, monkeypatch):
    task = tmp_path / "job"
    task.mkdir()
    job = jobs.create_job(task)

    class FailedExecutor:
        def submit(self, fn):
            raise RuntimeError("executor stopped")

    monkeypatch.setattr(jobs, "_executor", FailedExecutor())
    with pytest.raises(RuntimeError, match="executor stopped"):
        jobs.submit_job(job.id, lambda *args: None)
    assert not job.worker_active and job.status == "failed"
    jobs.cleanup_job(job.id)
    assert not task.exists()


def test_download_and_preview_share_output_boundary(tmp_path):
    task = tmp_path / "task"
    output = task / "output"
    output.mkdir(parents=True)
    inside = output / "result.pdf"
    inside.write_bytes(b"valid download bytes")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside secret")
    job = jobs.create_job(task)
    task_store.update_job_record(tmp_path, job.id, {
        "worker_active": 0,
        "status": "done",
        "result_path": str(inside),
        "result_name": "result.pdf",
        "media_type": "application/pdf",
    })
    task_store.release_task_lock(tmp_path, job.id)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/jobs/{job.id}/download")
            assert response.status_code == 200 and response.content == inside.read_bytes()
            for path in (outside, output / ".." / ".." / "outside.pdf"):
                task_store.update_job_record(tmp_path, job.id, {"result_path": str(path)})
                assert client.get(f"/api/jobs/{job.id}/download").status_code == 404
                assert client.get(f"/api/jobs/{job.id}/result-summary").status_code == 404
                assert client.get(f"/api/jobs/{job.id}/result-preview").status_code == 404
            task_store.update_job_record(tmp_path, job.id, {"result_path": str(output)})
            assert client.get(f"/api/jobs/{job.id}/download").status_code == 404
            task_store.update_job_record(tmp_path, job.id, {"result_path": str(output / "missing.pdf")})
            assert client.get(f"/api/jobs/{job.id}/download").status_code == 404
    finally:
        jobs.cleanup_job(job.id)


def test_job_state_is_visible_between_processes(tmp_path):
    task = tmp_path / "shared-state"
    job = jobs.create_job(task)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_child_update_job, args=(str(tmp_path), job.id, ready))
    try:
        process.start()
        assert ready.wait(10)
        process.join(10)
        assert process.exitcode == 0
        refreshed = jobs.get_job(job.id)
        assert refreshed is not None
        assert refreshed.progress == 73
        assert refreshed.message == "updated by another process"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        task_store.update_job_record(tmp_path, job.id, {"worker_active": 0, "status": "failed"})
        task_store.release_task_lock(tmp_path, job.id)
        jobs.cleanup_job(job.id)


def test_cleanup_in_another_process_cannot_delete_a_live_task(tmp_path):
    task = tmp_path / "live-worker"
    job = jobs.create_job(task)
    task_store.release_task_lock(tmp_path, job.id)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    acquired = context.Value("b", 0)
    process = context.Process(
        target=_child_hold_task_lock,
        args=(str(tmp_path), job.id, ready, release, acquired),
    )
    try:
        process.start()
        assert ready.wait(10) and acquired.value == 1
        task_store.update_job_record(tmp_path, job.id, {"updated_at": time.time() - 10000})
        jobs.cleanup_expired_jobs()
        jobs.cleanup_orphaned_task_dirs(tmp_path)
        assert task.exists()
        record = task_store.get_job_record(tmp_path, job.id)
        assert record is not None and record["worker_active"] == 1
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    # Once the owning process releases/exits, stale active state is recovered
    # as a failed job first; the directory is retained until its normal TTL.
    jobs.cleanup_expired_jobs()
    record = task_store.get_job_record(tmp_path, job.id)
    assert record is not None
    assert record["status"] == "failed" and record["worker_active"] == 0
    assert task.exists()
    jobs.cleanup_job(job.id)
    assert not task.exists()


def test_abrupt_worker_process_exit_releases_lock_and_is_recovered(tmp_path):
    task = tmp_path / "crashed-worker"
    job = jobs.create_job(task)
    task_store.release_task_lock(tmp_path, job.id)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    acquired = context.Value("b", 0)
    process = context.Process(
        target=_child_hold_task_lock,
        args=(str(tmp_path), job.id, ready, release, acquired, True),
    )
    process.start()
    assert ready.wait(10)
    process.join(10)
    assert process.exitcode == 0 and acquired.value == 1

    jobs.cleanup_expired_jobs()
    record = task_store.get_job_record(tmp_path, job.id)
    assert record is not None
    assert record["status"] == "failed" and record["worker_active"] == 0
    assert task.exists()
    jobs.cleanup_job(job.id)
    assert not task.exists()


def test_upload_reservation_is_registered_before_files_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "TEMP_ROOT", tmp_path)
    task = files.new_task_dir()
    os.utime(task, (1, 1))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_child_cleanup_orphans, args=(str(tmp_path), ready))
    try:
        process.start()
        assert ready.wait(10)
        process.join(10)
        assert process.exitcode == 0
        assert task.exists()
        record = task_store.get_job_record(tmp_path, task.name)
        assert record is not None and record["status"] == "uploading"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        files.discard_task_dir(task)
    assert not task.exists()
