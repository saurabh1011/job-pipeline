"""In-memory background task runner.

Each task has:
  id       - unique string (uuid4 short)
  status   - pending | running | done | error
  logs     - list of strings emitted during the run
  result   - dict with outcome data (set on completion)
"""
import threading
import traceback
import uuid
from typing import Callable, Dict, Any, Optional

_tasks: Dict[str, dict] = {}
_lock = threading.Lock()


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def get_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


def create_task(fn: Callable, *args, **kwargs) -> str:
    """Start fn(*args, **kwargs) in a background thread. Returns task_id."""
    task_id = _make_id()
    task = {"id": task_id, "status": "pending", "logs": [], "result": None}

    with _lock:
        _tasks[task_id] = task

    def _log(msg: str):
        with _lock:
            _tasks[task_id]["logs"].append(msg)

    def _run():
        with _lock:
            _tasks[task_id]["status"] = "running"
        try:
            result = fn(_log, *args, **kwargs)
            with _lock:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["result"] = result
        except Exception as e:
            with _lock:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["logs"].append(f"ERROR: {e}")
                _tasks[task_id]["logs"].append(traceback.format_exc())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return task_id
