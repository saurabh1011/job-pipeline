"""In-memory background task runner with database persistence.

Each task has:
  id       - unique string (uuid4 short)
  status   - pending | running | done | error
  logs     - list of strings emitted during the run
  result   - dict with outcome data (set on completion)

Tasks are stored in memory for quick access and persisted to database for recovery across restarts.
"""
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import requests

_tasks: Dict[str, dict] = {}
_lock = threading.Lock()

_KEEPALIVE_INTERVAL = 45  # seconds between pings — must be less than Fly's ~90s idle timeout
_KEEPALIVE_URL = (
    f"https://{os.getenv('FLY_APP_NAME')}.fly.dev/"
    if os.getenv("FLY_APP_NAME")
    else None
)

# Count of active tasks so we share one keepalive thread across concurrent tasks
_active_tasks = 0
_active_lock = threading.Lock()
_keepalive_thread: Optional[threading.Thread] = None


def _keepalive_loop():
    """Ping the app's own external URL every 2 minutes while tasks are running.

    Pings immediately on start so the first keepalive lands before any idle
    timeout can fire, then continues on the regular interval.
    """
    while True:
        try:
            requests.get(_KEEPALIVE_URL, timeout=10)
        except Exception:
            pass
        time.sleep(_KEEPALIVE_INTERVAL)
        with _active_lock:
            if _active_tasks == 0:
                return


def _start_keepalive():
    global _keepalive_thread
    if not _KEEPALIVE_URL:
        return
    with _active_lock:
        global _active_tasks
        _active_tasks += 1
        if _keepalive_thread is None or not _keepalive_thread.is_alive():
            _keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
            _keepalive_thread.start()


def _stop_keepalive():
    with _active_lock:
        global _active_tasks
        _active_tasks = max(0, _active_tasks - 1)


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def get_task(task_id: str) -> Optional[dict]:
    # Check memory first
    if task_id in _tasks:
        return _tasks[task_id]

    # Fall back to database if app restarted
    try:
        from pipeline.store import JobStore
        db_path = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "jobs.db"))
        store = JobStore(db_path)
        task = store.get_task(task_id)
        store.close()
        return task
    except Exception:
        return None


def list_tasks() -> list:
    with _lock:
        return list(_tasks.values())


def create_task(fn: Callable, *args, **kwargs) -> str:
    """Start fn(*args, **kwargs) in a background thread. Returns task_id."""
    task_id = _make_id()
    task = {"id": task_id, "status": "pending", "logs": [], "result": None,
            "started_at": None, "ended_at": None}

    with _lock:
        _tasks[task_id] = task

    def _save_task_to_db():
        """Persist task state to database."""
        try:
            from pipeline.store import JobStore
            db_path = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "jobs.db"))
            store = JobStore(db_path)
            with _lock:
                store.save_task(task_id, _tasks[task_id]["status"], _tasks[task_id]["logs"],
                               _tasks[task_id]["result"], _tasks[task_id]["started_at"],
                               _tasks[task_id]["ended_at"])
            store.close()
        except Exception:
            pass  # Fail silently — in-memory state is still valid

    def _log(msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with _lock:
            _tasks[task_id]["logs"].append(f"[{ts}] {msg}")
        _save_task_to_db()

    def _run():
        _start_keepalive()
        with _lock:
            _tasks[task_id]["status"] = "running"
            _tasks[task_id]["started_at"] = datetime.now(timezone.utc).isoformat()
        _save_task_to_db()
        try:
            result = fn(_log, *args, **kwargs)
            with _lock:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["result"] = result
                _tasks[task_id]["ended_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            with _lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["logs"].append(f"ERROR: {e}")
                _tasks[task_id]["logs"].append(traceback.format_exc())
                _tasks[task_id]["ended_at"] = datetime.now(timezone.utc).isoformat()
        finally:
            _save_task_to_db()
            _stop_keepalive()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return task_id
