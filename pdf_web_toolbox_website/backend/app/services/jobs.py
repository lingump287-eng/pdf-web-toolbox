from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.utils.files import JOB_TTL_SECONDS, MAX_CONCURRENT_JOBS, TEMP_ROOT, remove_tree
from app.utils.task_store import (
    acquire_task_lock,
    ensure_task_reserved,
    get_job_record,
    list_job_records,
    release_task_lock,
    registry_lock,
    save_job_record,
    update_job_record,
    cancel_job_record,
    delete_job_record,
    update_job_record_if_not_cancelled,
    start_job_record,
    finish_worker_record,
    recover_abandoned_job_record,
)


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
    worker_active: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    json_result: dict[str, Any] | None = None


# This cache is only an optimization. SQLite is the source of truth shared by
# every process; no request or worker relies on another process's Python memory.
_jobs: dict[str, Job] = {}
_job_roots: dict[str, Path] = {}
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="pdf-job")


def _root_for(job_id: str) -> Path:
    with _lock:
        return _job_roots.get(job_id, TEMP_ROOT).resolve()


def _job_from_record(root: Path, record: dict[str, Any]) -> Job:
    job_id = record["job_id"]
    raw_json = record.get("json_result")
    if isinstance(raw_json, str):
        try:
            json_result = json.loads(raw_json)
        except (TypeError, ValueError):
            json_result = {}
    else:
        json_result = raw_json

    values = {
        "task_dir": Path(record["task_dir"]),
        "status": record["status"],
        "progress": int(record.get("progress") or 0),
        "message": record.get("message") or "",
        "result_path": Path(record["result_path"]) if record.get("result_path") else None,
        "result_name": record.get("result_name"),
        "media_type": record.get("media_type") or "application/octet-stream",
        "error": record.get("error"),
        "cancelled": bool(record.get("cancelled")),
        "worker_active": bool(record.get("worker_active")),
        "created_at": float(record.get("created_at") or time.time()),
        "updated_at": float(record.get("updated_at") or time.time()),
        "json_result": json_result,
    }
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            job = Job(id=job_id, **values)
            _jobs[job_id] = job
        else:
            for key, value in values.items():
                setattr(job, key, value)
        _job_roots[job_id] = Path(root).resolve()
        return job


def _update_cache(job_id: str, **values: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            for key, value in values.items():
                setattr(job, key, value)


def create_job(task_dir: Path) -> Job:
    task_dir = Path(task_dir).resolve()
    root = task_dir.parent
    cleanup_expired_jobs()
    ensure_task_reserved(root, task_dir)
    job_id = task_dir.name
    now = time.time()
    existing = get_job_record(root, job_id) or {}
    record = {
        "job_id": job_id,
        "task_dir": str(task_dir),
        "status": "queued",
        "progress": 0,
        "message": "等待处理",
        "result_path": None,
        "result_name": None,
        "media_type": "application/octet-stream",
        "error": None,
        "cancelled": 0,
        "worker_active": 1,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "json_result": None,
    }
    save_job_record(root, record)
    with _lock:
        _job_roots[job_id] = root
    return _job_from_record(root, record)


def get_job(job_id: str) -> Job | None:
    cleanup_expired_jobs()
    root = _root_for(job_id)
    record = get_job_record(root, job_id)
    if not record or record["status"] == "uploading":
        return None
    return _job_from_record(root, record)


def update_job(job_id: str, *, progress: int | None = None, message: str | None = None) -> None:
    root = _root_for(job_id)
    record = get_job_record(root, job_id)
    if not record:
        return
    updates: dict[str, Any] = {"updated_at": time.time()}
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    if message is not None:
        updates["message"] = message
    if update_job_record_if_not_cancelled(root, job_id, updates):
        _update_cache(job_id, **updates)


def is_cancelled(job_id: str) -> bool:
    record = get_job_record(_root_for(job_id), job_id)
    return bool(record and record.get("cancelled"))


def cancel_job(job_id: str) -> bool:
    root = _root_for(job_id)
    changed = cancel_job_record(root, job_id, time.time())
    if changed:
        _update_cache(job_id, cancelled=True, status="cancelled", message="任务已取消", updated_at=time.time())
    return changed


def submit_job(
    job_id: str,
    worker: Callable[[Callable[[int, str], None], Callable[[], bool]], tuple[Path, str, str] | dict[str, Any]],
) -> None:
    root = _root_for(job_id)
    initial = get_job_record(root, job_id)
    if not initial:
        raise KeyError(f"Unknown job: {job_id}")
    task_dir = Path(initial["task_dir"])

    def run() -> None:
        def progress(value: int, message: str) -> None:
            update_job(job_id, progress=value, message=message)

        try:
            now = time.time()
            if not start_job_record(root, job_id, now):
                return
            _update_cache(job_id, status="running", progress=5, message="开始处理", updated_at=now)

            result = worker(progress, lambda: is_cancelled(job_id))
            record = get_job_record(root, job_id)
            if not record:
                return
            if record.get("cancelled"):
                updates = {"status": "cancelled", "message": "任务已取消", "updated_at": time.time()}
            elif isinstance(result, dict):
                updates = {
                    "status": "done",
                    "progress": 100,
                    "message": "处理完成",
                    "result_name": "__json__",
                    "result_path": None,
                    "error": None,
                    "json_result": result,
                    "updated_at": time.time(),
                }
            else:
                result_path, result_name, media_type = result
                updates = {
                    "status": "done",
                    "progress": 100,
                    "message": "处理完成",
                    "result_path": str(result_path),
                    "result_name": result_name,
                    "media_type": media_type,
                    "error": None,
                    "json_result": None,
                    "updated_at": time.time(),
                }
            if update_job_record_if_not_cancelled(root, job_id, updates):
                _update_cache(job_id, **updates)
            else:
                latest = get_job_record(root, job_id)
                if latest:
                    _job_from_record(root, latest)
        except BaseException as exc:
            record = get_job_record(root, job_id)
            if record and not record.get("cancelled"):
                error = str(exc) if isinstance(exc, ValueError) else "服务器处理失败，请确认文件有效后重试。"
                updates = {
                    "status": "failed",
                    "error": error,
                    "message": error,
                    "updated_at": time.time(),
                }
                if update_job_record_if_not_cancelled(root, job_id, updates):
                    _update_cache(job_id, **updates)
                else:
                    latest = get_job_record(root, job_id)
                    if latest:
                        _job_from_record(root, latest)
            if isinstance(exc, Exception) and not isinstance(exc, ValueError):
                logging.getLogger(__name__).exception(
                    "Background PDF job failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            if not isinstance(exc, Exception):
                raise
        finally:
            finish_worker_record(root, job_id, time.time())
            latest = get_job_record(root, job_id)
            if latest:
                _job_from_record(root, latest)
            # The OS releases this lock automatically if the worker process crashes.
            release_task_lock(root, job_id)

    try:
        _executor.submit(run)
    except BaseException:
        now = time.time()
        updates = {
            "worker_active": 0,
            "status": "failed",
            "error": "任务提交失败，请稍后重试。",
            "message": "任务提交失败，请稍后重试。",
            "updated_at": now,
        }
        update_job_record(root, job_id, updates)
        _update_cache(job_id, **updates)
        release_task_lock(root, job_id)
        raise


def cleanup_job(job_id: str) -> None:
    root = _root_for(job_id)
    with registry_lock(root):
        record = get_job_record(root, job_id)
        if not record:
            return
        task_dir = Path(record["task_dir"])
        if not acquire_task_lock(root, job_id, blocking=False):
            # Another process still owns the task directory.
            return
        try:
            record = get_job_record(root, job_id)
            if not record:
                return
            if record.get("worker_active"):
                # The OS lock is free but the database still says active: recover
                # the reservation atomically without overwriting a concurrent cancel.
                recover_abandoned_job_record(root, job_id, time.time())
                latest = get_job_record(root, job_id)
                if latest:
                    _job_from_record(root, latest)
                return

            try:
                remove_tree(task_dir)
            except OSError:
                logging.getLogger(__name__).exception("Could not clean up job %s", job_id)
                return
            if not task_dir.exists():
                delete_job_record(root, job_id)
                with _lock:
                    _jobs.pop(job_id, None)
                    _job_roots.pop(job_id, None)
                lock_path = root / ".locks" / f"{job_id}.lock"
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            release_task_lock(root, job_id)


def cleanup_orphaned_task_dirs(temp_root: Path | None = None) -> None:
    """Remove stale unregistered directories without crossing process boundaries."""
    roots = {Path(temp_root).resolve()} if temp_root is not None else _known_roots()
    now = time.time()
    for root in roots:
        records = list_job_records(root)
        known_paths = {Path(record["task_dir"]).resolve() for record in records}
        with registry_lock(root):
            # Re-read after acquiring the cross-process registry lock so a newly
            # registered upload cannot be mistaken for an orphan.
            records = list_job_records(root)
            known_paths = {Path(record["task_dir"]).resolve() for record in records}
            if not root.exists():
                continue
            for folder in root.iterdir():
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                resolved = folder.resolve()
                if resolved in known_paths:
                    continue
                try:
                    if now - folder.stat().st_mtime <= JOB_TTL_SECONDS:
                        continue
                    if not acquire_task_lock(root, folder.name, blocking=False):
                        continue
                    try:
                        remove_tree(folder)
                        lock_path = root / ".locks" / f"{folder.name}.lock"
                        try:
                            lock_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    finally:
                        release_task_lock(root, folder.name)
                except OSError:
                    continue


def _known_roots() -> set[Path]:
    with _lock:
        return {Path(TEMP_ROOT).resolve(), *(root.resolve() for root in _job_roots.values())}


def _periodic_cleanup() -> None:
    while True:
        time.sleep(min(300, max(60, JOB_TTL_SECONDS // 2)))
        cleanup_expired_jobs()
        cleanup_orphaned_task_dirs()


def start_cleanup_thread() -> None:
    thread = threading.Thread(target=_periodic_cleanup, daemon=True, name="pdf-cleanup")
    thread.start()


def cleanup_expired_jobs() -> None:
    now = time.time()
    for root in _known_roots():
        for record in list_job_records(root):
            job_id = record["job_id"]
            age = now - float(record.get("updated_at") or now)
            if record.get("worker_active"):
                # If a live process owns the lock, cleanup_job is a no-op. If the
                # lock is free, it marks the abandoned task failed for inspection.
                cleanup_job(job_id)
            elif record["status"] == "uploading" and age > JOB_TTL_SECONDS:
                cleanup_job(job_id)
            elif record["status"] not in {"queued", "running", "uploading"} and age > JOB_TTL_SECONDS:
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
            payload["result"] = job.json_result or {}
        elif job.result_path:
            payload["download_url"] = f"/api/jobs/{job.id}/download"
            payload["filename"] = job.result_name
    return payload
