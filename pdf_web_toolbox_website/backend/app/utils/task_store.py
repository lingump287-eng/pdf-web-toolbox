"""Cross-process job registry and task-directory locks.

The SQLite registry and lock files must live on the same local filesystem shared by
all worker processes. This coordinates processes in one host/container; it is not a
distributed lock for multiple containers or hosts.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_DB_NAME = ".jobs.sqlite3"
_LOCKS_DIR = ".locks"
_REGISTRY_LOCK = ".registry.lock"
_LOCAL_LOCKS: dict[str, Any] = {}
_LOCAL_LOCKS_GUARD = threading.RLock()
_REGISTRY_THREAD_LOCK = threading.RLock()
_INITIALIZED_ROOTS: set[str] = set()
_INITIALIZED_GUARD = threading.RLock()

_COLUMNS = {
    "task_dir",
    "status",
    "progress",
    "message",
    "result_path",
    "result_name",
    "media_type",
    "error",
    "cancelled",
    "worker_active",
    "created_at",
    "updated_at",
    "json_result",
}


def _root_key(root: Path) -> str:
    return str(Path(root).resolve())


def _connect(root: Path) -> sqlite3.Connection:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / _DB_NAME, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _initialize_unlocked(root: Path) -> None:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / _LOCKS_DIR).mkdir(parents=True, exist_ok=True)
    connection = _connect(root)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                task_dir TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                result_path TEXT,
                result_name TEXT,
                media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                error TEXT,
                cancelled INTEGER NOT NULL DEFAULT 0,
                worker_active INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                json_result TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at)")
    finally:
        connection.close()
    _INITIALIZED_ROOTS.add(_root_key(root))


def ensure_store(root: Path) -> None:
    root = Path(root).resolve()
    key = _root_key(root)
    with _INITIALIZED_GUARD:
        if key in _INITIALIZED_ROOTS:
            return
        with registry_lock(root):
            if key not in _INITIALIZED_ROOTS:
                _initialize_unlocked(root)


def _acquire_file_lock(path: Path, *, blocking: bool) -> Any | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(handle.fileno(), mode, 1)
        else:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), flags)
    except OSError:
        handle.close()
        return None
    return handle


def _release_file_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


@contextmanager
def registry_lock(root: Path) -> Iterator[None]:
    """Serialize task registration and cleanup across processes."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_THREAD_LOCK:
        handle = _acquire_file_lock(root / _REGISTRY_LOCK, blocking=True)
        if handle is None:
            raise RuntimeError(f"Could not acquire task registry lock for {root}")
        try:
            yield
        finally:
            _release_file_lock(handle)


def _task_lock_path(root: Path, job_id: str) -> Path:
    return Path(root).resolve() / _LOCKS_DIR / f"{job_id}.lock"


def _local_lock_key(root: Path, job_id: str) -> str:
    return f"{_root_key(root)}::{job_id}"


def has_local_task_lock(root: Path, job_id: str) -> bool:
    key = _local_lock_key(root, job_id)
    with _LOCAL_LOCKS_GUARD:
        return key in _LOCAL_LOCKS


def acquire_task_lock(root: Path, job_id: str, *, blocking: bool = False) -> bool:
    """Acquire a per-job OS lock. Call while holding registry_lock for new claims."""
    root = Path(root).resolve()
    key = _local_lock_key(root, job_id)
    with _LOCAL_LOCKS_GUARD:
        if key in _LOCAL_LOCKS:
            return False
        handle = _acquire_file_lock(_task_lock_path(root, job_id), blocking=blocking)
        if handle is None:
            return False
        _LOCAL_LOCKS[key] = handle
        return True


def release_task_lock(root: Path, job_id: str) -> None:
    key = _local_lock_key(root, job_id)
    with _LOCAL_LOCKS_GUARD:
        handle = _LOCAL_LOCKS.pop(key, None)
    if handle is not None:
        _release_file_lock(handle)


def _delete_record_unlocked(root: Path, job_id: str) -> None:
    connection = _connect(root)
    try:
        connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    finally:
        connection.close()


def reserve_task_dir(root: Path) -> Path:
    """Create and register an upload directory before the first byte is written."""
    root = Path(root).resolve()
    with registry_lock(root):
        if _root_key(root) not in _INITIALIZED_ROOTS:
            _initialize_unlocked(root)
        job_id = uuid.uuid4().hex
        task_dir = root / job_id
        task_dir.mkdir(parents=True, exist_ok=False)
        try:
            (task_dir / "input").mkdir()
            (task_dir / "output").mkdir()
            if not acquire_task_lock(root, job_id, blocking=True):
                raise RuntimeError(f"Could not reserve task directory {task_dir}")
            now = time.time()
            connection = _connect(root)
            try:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, task_dir, status, progress, message, media_type,
                        cancelled, worker_active, created_at, updated_at
                    ) VALUES (?, ?, 'uploading', 0, '上传中', 'application/octet-stream', 0, 0, ?, ?)
                    """,
                    (job_id, str(task_dir), now, now),
                )
            finally:
                connection.close()
        except BaseException:
            release_task_lock(root, job_id)
            shutil.rmtree(task_dir, ignore_errors=True)
            try:
                _task_lock_path(root, job_id).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return task_dir


def ensure_task_reserved(root: Path, task_dir: Path) -> None:
    """Support internal callers/tests that create a task path without new_task_dir."""
    root = Path(root).resolve()
    task_dir = Path(task_dir).resolve()
    job_id = task_dir.name
    with registry_lock(root):
        if _root_key(root) not in _INITIALIZED_ROOTS:
            _initialize_unlocked(root)
        task_dir.mkdir(parents=True, exist_ok=True)
        if not has_local_task_lock(root, job_id):
            if not acquire_task_lock(root, job_id, blocking=False):
                raise RuntimeError(f"Task directory is already active: {task_dir}")
        now = time.time()
        connection = _connect(root)
        try:
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_id, task_dir, status, progress, message, media_type,
                    cancelled, worker_active, created_at, updated_at
                ) VALUES (?, ?, 'uploading', 0, '上传中', 'application/octet-stream', 0, 0, ?, ?)
                """,
                (job_id, str(task_dir), now, now),
            )
        finally:
            connection.close()


def discard_task_dir(root: Path, task_dir: Path) -> bool:
    """Safely discard a request-only directory and its reservation."""
    root = Path(root).resolve()
    task_dir = Path(task_dir).resolve()
    job_id = task_dir.name
    ensure_store(root)
    with registry_lock(root):
        record = get_job_record(root, job_id)
        if record and record.get("worker_active"):
            # A submitted worker owns this directory; request error cleanup must
            # never release its lock or remove its input/output files.
            return False
        release_task_lock(root, job_id)
        if not acquire_task_lock(root, job_id, blocking=False):
            return False
        try:
            shutil.rmtree(task_dir, ignore_errors=True)
            if task_dir.exists():
                return False
            _delete_record_unlocked(root, job_id)
            return True
        finally:
            release_task_lock(root, job_id)
            lock_path = _task_lock_path(root, job_id)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def get_job_record(root: Path, job_id: str) -> dict[str, Any] | None:
    ensure_store(root)
    connection = _connect(root)
    try:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def list_job_records(root: Path) -> list[dict[str, Any]]:
    ensure_store(root)
    connection = _connect(root)
    try:
        rows = connection.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def save_job_record(root: Path, record: dict[str, Any]) -> None:
    ensure_store(root)
    values = dict(record)
    if isinstance(values.get("json_result"), (dict, list)):
        values["json_result"] = json.dumps(values["json_result"], ensure_ascii=False)
    columns = [
        "job_id", "task_dir", "status", "progress", "message", "result_path",
        "result_name", "media_type", "error", "cancelled", "worker_active",
        "created_at", "updated_at", "json_result",
    ]
    connection = _connect(root)
    try:
        connection.execute(
            f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            "ON CONFLICT(job_id) DO UPDATE SET "
            + ", ".join(f"{column}=excluded.{column}" for column in columns if column != "job_id"),
            tuple(values.get(column) for column in columns),
        )
    finally:
        connection.close()


def activate_job_record(root: Path, job_id: str, now: float) -> bool:
    """Promote one upload reservation to a queued job exactly once."""
    root = Path(root).resolve()
    ensure_store(root)
    with registry_lock(root):
        connection = _connect(root)
        try:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    progress = 0,
                    message = '等待处理',
                    result_path = NULL,
                    result_name = NULL,
                    media_type = 'application/octet-stream',
                    error = NULL,
                    cancelled = 0,
                    worker_active = 0,
                    updated_at = ?
                WHERE job_id = ? AND status = 'uploading'
                  AND worker_active = 0 AND cancelled = 0
                """,
                (now, job_id),
            )
            return cursor.rowcount > 0
        finally:
            connection.close()


def update_job_record(root: Path, job_id: str, updates: dict[str, Any]) -> bool:
    ensure_store(root)
    clean = {key: value for key, value in updates.items() if key in _COLUMNS}
    if not clean:
        return False
    if isinstance(clean.get("json_result"), (dict, list)):
        clean["json_result"] = json.dumps(clean["json_result"], ensure_ascii=False)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            f"UPDATE jobs SET {', '.join(f'{key} = ?' for key in clean)} WHERE job_id = ?",
            (*clean.values(), job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def update_job_record_if_not_cancelled(root: Path, job_id: str, updates: dict[str, Any]) -> bool:
    """Apply worker progress/result only if cancellation has not won the race."""
    ensure_store(root)
    clean = {key: value for key, value in updates.items() if key in _COLUMNS}
    if not clean:
        return False
    if isinstance(clean.get("json_result"), (dict, list)):
        clean["json_result"] = json.dumps(clean["json_result"], ensure_ascii=False)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            f"UPDATE jobs SET {', '.join(f'{key} = ?' for key in clean)} "
            "WHERE job_id = ? AND cancelled = 0",
            (*clean.values(), job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def reserve_worker_record(root: Path, job_id: str, now: float) -> bool:
    """Claim a queued task for exactly one process before submitting its worker."""
    root = Path(root).resolve()
    ensure_store(root)
    # Serialize the worker claim with request-error cleanup and expiry recovery.
    # Otherwise cleanup could observe worker_active=0 and delete the task directory
    # in the gap between checking the row and claiming the worker.
    with registry_lock(root):
        connection = _connect(root)
        try:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET worker_active = 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued' AND cancelled = 0 AND worker_active = 0
                """,
                (now, job_id),
            )
            return cursor.rowcount > 0
        finally:
            connection.close()


def recover_unsubmitted_job_record(root: Path, job_id: str, now: float) -> bool:
    """Recover a queued/running row whose owner lock is gone but worker was not active."""
    ensure_store(root)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = '任务提交进程意外退出，任务未完成。',
                message = '任务提交进程意外退出，任务未完成。',
                updated_at = ?
            WHERE job_id = ? AND worker_active = 0
              AND status IN ('queued', 'running') AND cancelled = 0
            """,
            (now, job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def start_job_record(root: Path, job_id: str, now: float) -> bool:
    """Atomically transition a queued job to running unless it was cancelled."""
    ensure_store(root)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'running', progress = 5, message = '开始处理', updated_at = ?
            WHERE job_id = ? AND status = 'queued' AND cancelled = 0
            """,
            (now, job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def recover_abandoned_job_record(root: Path, job_id: str, now: float) -> bool:
    """Mark a job whose OS lock is free but database reservation is still active."""
    ensure_store(root)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET worker_active = 0,
                updated_at = ?,
                status = CASE
                    WHEN status IN ('uploading', 'queued', 'running') AND cancelled = 0 THEN 'failed'
                    ELSE status
                END,
                error = CASE
                    WHEN status IN ('uploading', 'queued', 'running') AND cancelled = 0
                    THEN '后台工作进程意外退出，任务未完成。'
                    ELSE error
                END,
                message = CASE
                    WHEN status IN ('uploading', 'queued', 'running') AND cancelled = 0
                    THEN '后台工作进程意外退出，任务未完成。'
                    ELSE message
                END
            WHERE job_id = ? AND worker_active = 1
            """,
            (now, job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def finish_worker_record(root: Path, job_id: str, now: float) -> bool:
    """Release the worker reservation without overwriting a concurrent cancellation."""
    ensure_store(root)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET worker_active = 0,
                updated_at = ?,
                status = CASE
                    WHEN status IN ('queued', 'running') AND cancelled = 0 THEN 'failed'
                    ELSE status
                END,
                error = CASE
                    WHEN status IN ('queued', 'running') AND cancelled = 0
                    THEN '服务器处理失败，请确认文件有效后重试。'
                    ELSE error
                END,
                message = CASE
                    WHEN status IN ('queued', 'running') AND cancelled = 0
                    THEN '服务器处理失败，请确认文件有效后重试。'
                    ELSE message
                END
            WHERE job_id = ?
            """,
            (now, job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def cancel_job_record(root: Path, job_id: str, now: float) -> bool:
    ensure_store(root)
    connection = _connect(root)
    try:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET cancelled = 1, status = 'cancelled', message = '任务已取消', updated_at = ?
            WHERE job_id = ? AND status NOT IN ('done', 'failed', 'cancelled')
            """,
            (now, job_id),
        )
        return cursor.rowcount > 0
    finally:
        connection.close()


def delete_job_record(root: Path, job_id: str) -> None:
    ensure_store(root)
    _delete_record_unlocked(root, job_id)
