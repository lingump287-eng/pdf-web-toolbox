import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import jobs


@pytest.fixture
def executor(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=1)
    futures = []
    submit = pool.submit
    def capture(fn):
        future = submit(fn)
        futures.append(future)
        return future
    monkeypatch.setattr(jobs, '_executor', type('Executor', (), {'submit': staticmethod(capture)})())
    yield futures
    pool.shutdown(wait=True)


@pytest.mark.parametrize('raises', [False, True])
def test_cancelled_worker_protected_until_exit(tmp_path, monkeypatch, executor, raises):
    monkeypatch.setattr(jobs, 'TEMP_ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'JOB_TTL_SECONDS', 1)
    task = tmp_path / 'active'
    task.mkdir()
    (task / 'input.pdf').write_bytes(b'private')
    job = jobs.create_job(task)
    started, release = threading.Event(), threading.Event()
    def work(progress, cancelled):
        started.set()
        assert release.wait(5)
        assert task.exists() and cancelled()
        if raises:
            raise ValueError('cancelled')
        return task / 'output.pdf', 'output.pdf', 'application/pdf'
    try:
        jobs.submit_job(job.id, work)
        assert started.wait(5)
        assert jobs.cancel_job(job.id)
        job.updated_at = time.time() - 10
        os.utime(task, (1, 1))
        jobs.cleanup_expired_jobs()
        jobs.cleanup_orphaned_task_dirs()
        jobs.cleanup_job(job.id)
        assert task.exists() and jobs.get_job(job.id) is job
        assert job.worker_active
    finally:
        release.set()
        executor[0].result(timeout=5)
    assert not job.worker_active and job.status == 'cancelled'
    job.updated_at = time.time() - 10
    jobs.cleanup_expired_jobs()
    assert not task.exists() and jobs.get_job(job.id) is None


def test_queued_cancel_and_base_exception_release_reservation(tmp_path, executor):
    entered, release = threading.Event(), threading.Event()
    first = jobs.create_job(tmp_path / 'first')
    second = jobs.create_job(tmp_path / 'second')
    def block(progress, cancelled):
        entered.set(); assert release.wait(5)
        raise SystemExit('worker exited abnormally')
    def must_not_run(progress, cancelled):
        pytest.fail('cancelled queued worker was called')
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
    assert first.status == 'failed' and not first.worker_active
    assert second.status == 'cancelled' and not second.worker_active
    jobs.cleanup_job(first.id)
    jobs.cleanup_job(second.id)


@pytest.mark.parametrize('raises', [False, True])
def test_failed_deletion_is_retried(tmp_path, monkeypatch, raises):
    task = tmp_path / 'job'
    task.mkdir()
    job = jobs.create_job(task)
    job.worker_active = False
    job.status = 'failed'
    actual = jobs.remove_tree
    def fail_removal(path):
        if raises:
            raise OSError('simulated cleanup failure')
    monkeypatch.setattr(jobs, 'remove_tree', fail_removal)
    jobs.cleanup_job(job.id)
    assert jobs.get_job(job.id) is job and task.exists()
    monkeypatch.setattr(jobs, 'remove_tree', actual)
    jobs.cleanup_job(job.id)
    assert jobs.get_job(job.id) is None and not task.exists()


def test_submission_failure_releases_reservation(tmp_path, monkeypatch):
    task = tmp_path / 'job'
    task.mkdir()
    job = jobs.create_job(task)
    class FailedExecutor:
        def submit(self, fn):
            raise RuntimeError('executor stopped')
    monkeypatch.setattr(jobs, '_executor', FailedExecutor())
    with pytest.raises(RuntimeError, match='executor stopped'):
        jobs.submit_job(job.id, lambda *args: None)
    assert not job.worker_active and job.status == 'failed'
    jobs.cleanup_job(job.id)
    assert not task.exists()


def test_download_and_preview_share_output_boundary(tmp_path):
    task = tmp_path / 'task'
    output = task / 'output'
    output.mkdir(parents=True)
    inside = output / 'result.pdf'
    inside.write_bytes(b'valid download bytes')
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'outside secret')
    job = jobs.create_job(task)
    job.worker_active = False
    job.status = 'done'
    job.result_name = 'result.pdf'
    job.media_type = 'application/pdf'
    try:
        with TestClient(app) as client:
            job.result_path = inside
            response = client.get(f'/api/jobs/{job.id}/download')
            assert response.status_code == 200 and response.content == inside.read_bytes()
            for path in (outside, output / '..' / '..' / 'outside.pdf'):
                job.result_path = path
                assert client.get(f'/api/jobs/{job.id}/download').status_code == 404
                assert client.get(f'/api/jobs/{job.id}/result-summary').status_code == 404
                assert client.get(f'/api/jobs/{job.id}/result-preview').status_code == 404
            job.result_path = output
            assert client.get(f'/api/jobs/{job.id}/download').status_code == 404
            job.result_path = output / 'missing.pdf'
            assert client.get(f'/api/jobs/{job.id}/download').status_code == 404
    finally:
        jobs.cleanup_job(job.id)
