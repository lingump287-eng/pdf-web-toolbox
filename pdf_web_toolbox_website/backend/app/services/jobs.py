from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

from app.utils.files import JOB_TTL_SECONDS, MAX_CONCURRENT_JOBS, remove_tree


@dataclass
class Job:
    id: str
    task_dir: Path
    status: str = "queued"
    progress: int = 0
    message: str = "等待处理"
    result_path: Path | None = None
    result_name: str | None = None
    media_type: str = "application/octet-stream"
    error: str | None = None
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="pdf-job")


def _touch(job: Job) -> None:
    job.updated_at = time.time()


def create_job(task_dir: Path) -> Job:
    cleanup_expired_jobs()
    job = Job(id=uuid.uuid4().hex, task_dir=task_dir)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    cleanup_expired_jobs()
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, *, progress: int | None = None, message: str | None = None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if progress is not None:
            job.progress = max(0, min(100, int(progress)))
        if message is not None:
            job.message = message
        _touch(job)


def is_cancelled(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.cancelled)


def cancel_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.status in {"done", "failed", "cancelled"}:
            return False
        job.cancelled = True
        job.status = "cancelled"
        job.message = "任务已取消"
        _touch(job)
    return True


def submit_job(
    job_id: str,
    worker: Callable[[Callable[[int, str], None], Callable[[], bool]], tuple[Path, str, str] | dict[str, Any]],
) -> None:
    def run() -> None:
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            if job.cancelled:
                job.status = "cancelled"
                return
            job.status = "running"
            job.progress = 5
            job.message = "开始处理"
            _touch(job)

        def progress(value: int, message: str) -> None:
            update_job(job_id, progress=value, message=message)

        try:
            result = worker(progress, lambda: is_cancelled(job_id))
            with _lock:
                job = _jobs.get(job_id)
                if not job:
                    return
                if job.cancelled:
                    job.status = "cancelled"
                    job.message = "任务已取消"
                    remove_tree(job.task_dir)
                    return
                if isinstance(result, dict):
                    job.status = "done"
                    job.progress = 100
                    job.message = "处理完成"
                    job.result_name = "__json__"
                    job.error = None
                    setattr(job, "json_result", result)
                else:
                    result_path, result_name, media_type = result
                    job.result_path = result_path
                    job.result_name = result_name
                    job.media_type = media_type
                    job.status = "done"
                    job.progress = 100
                    job.message = "处理完成"
                _touch(job)
        except Exception as exc:
            with _lock:
                job = _jobs.get(job_id)
                if not job:
                    return
                if job.cancelled:
                    job.status = "cancelled"
                    job.message = "任务已取消"
                else:
                    job.status = "failed"
                    job.error = str(exc) if isinstance(exc, ValueError) else "服务器处理失败，请确认文件有效后重试。"
                    job.message = job.error
                _touch(job)
            if not isinstance(exc, ValueError):
                import logging
                logging.getLogger(__name__).exception("Background PDF job failed", exc_info=exc)

    _executor.submit(run)


def cleanup_job(job_id: str) -> None:
    with _lock:
        job = _jobs.pop(job_id, None)
    if job:
        remove_tree(job.task_dir)


def cleanup_expired_jobs() -> None:
    now = time.time()
    expired: list[str] = []
    with _lock:
        for job_id, job in _jobs.items():
            if now - job.updated_at > JOB_TTL_SECONDS:
                expired.append(job_id)
    for job_id in expired:
        cleanup_job(job_id)


def job_payload(job: Job) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
    }
    if job.status == "done":
        if job.result_name == "__json__":
            payload["result"] = getattr(job, "json_result", {})
        elif job.result_path:
            payload["download_url"] = f"/api/jobs/{job.id}/download"
            payload["filename"] = job.result_name
    return payload
