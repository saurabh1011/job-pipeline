"""Unit tests for web/tasks.py — in-memory background task runner."""
import os
import threading
import time
import pytest

from web.tasks import create_task, get_task, list_tasks, _tasks, _lock
import web.tasks as tasks_module


@pytest.fixture(autouse=True)
def clear_tasks():
    """Wipe task registry before each test to avoid cross-test contamination."""
    with _lock:
        _tasks.clear()
    yield
    with _lock:
        _tasks.clear()


def _wait_for(task_id: str, status: str, timeout: float = 3.0):
    """Poll until the task reaches the given status or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = get_task(task_id)
        if task and task["status"] == status:
            return task
        time.sleep(0.05)
    return get_task(task_id)


class TestCreateTask:
    def test_returns_string_id(self):
        def fn(log): pass
        task_id = create_task(fn)
        assert isinstance(task_id, str)
        assert len(task_id) == 12

    def test_task_exists_immediately(self):
        def fn(log): pass
        task_id = create_task(fn)
        assert get_task(task_id) is not None

    def test_task_initial_status_pending_or_running(self):
        def fn(log): time.sleep(0.1)
        task_id = create_task(fn)
        task = get_task(task_id)
        assert task["status"] in ("pending", "running")

    def test_task_reaches_done(self):
        def fn(log): return {"ok": True}
        task_id = create_task(fn)
        task = _wait_for(task_id, "done")
        assert task["status"] == "done"

    def test_result_stored_on_completion(self):
        def fn(log): return {"answer": 42}
        task_id = create_task(fn)
        task = _wait_for(task_id, "done")
        assert task["result"] == {"answer": 42}

    def test_error_status_on_exception(self):
        def fn(log): raise ValueError("boom")
        task_id = create_task(fn)
        task = _wait_for(task_id, "error")
        assert task["status"] == "error"

    def test_error_message_in_logs(self):
        def fn(log): raise RuntimeError("test error")
        task_id = create_task(fn)
        task = _wait_for(task_id, "error")
        log_text = "\n".join(task["logs"])
        assert "test error" in log_text

    def test_log_callable_appends_to_logs(self):
        def fn(log):
            log("step one")
            log("step two")
        task_id = create_task(fn)
        task = _wait_for(task_id, "done")
        log_text = "\n".join(task["logs"])
        assert "step one" in log_text
        assert "step two" in log_text

    def test_args_passed_to_fn(self):
        received = []
        def fn(log, x, y): received.extend([x, y])
        task_id = create_task(fn, "hello", 99)
        _wait_for(task_id, "done")
        assert received == ["hello", 99]

    def test_unique_ids_for_concurrent_tasks(self):
        def fn(log): pass
        ids = [create_task(fn) for _ in range(5)]
        assert len(set(ids)) == 5

    def test_started_at_set_when_running(self):
        event = threading.Event()
        def fn(log): event.wait(timeout=2)
        task_id = create_task(fn)
        task = _wait_for(task_id, "running")
        assert task["started_at"] is not None
        event.set()

    def test_ended_at_set_when_done(self):
        def fn(log): return {}
        task_id = create_task(fn)
        task = _wait_for(task_id, "done")
        assert task["ended_at"] is not None


class TestGetTask:
    def test_returns_none_for_unknown_id(self):
        assert get_task("nonexistent") is None

    def test_returns_task_dict(self):
        def fn(log): pass
        task_id = create_task(fn)
        task = get_task(task_id)
        assert isinstance(task, dict)
        assert task["id"] == task_id


class TestListTasks:
    def test_empty_when_no_tasks(self):
        assert list_tasks() == []

    def test_returns_all_tasks(self):
        def fn(log): pass
        id1 = create_task(fn)
        id2 = create_task(fn)
        tasks = list_tasks()
        ids = {t["id"] for t in tasks}
        assert id1 in ids
        assert id2 in ids

    def test_list_is_copy_not_reference(self):
        def fn(log): pass
        create_task(fn)
        list1 = list_tasks()
        create_task(fn)
        list2 = list_tasks()
        assert len(list2) == len(list1) + 1


# ── Log writing ───────────────────────────────────────────────────────────────

@pytest.fixture()
def log_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "logs")
    monkeypatch.setattr(tasks_module, "_LOG_DIR", d)
    return d


class TestWriteTaskLog:
    def test_log_file_created_after_done(self, log_dir):
        def fn(log):
            log("step one")
        task_id = create_task(fn)
        _wait_for(task_id, "done")
        time.sleep(0.1)
        files = os.listdir(log_dir)
        assert any(f.startswith(f"run_{task_id}_") and f.endswith(".log") for f in files)

    def test_log_file_created_after_error(self, log_dir):
        def fn(log):
            raise RuntimeError("task failed")
        task_id = create_task(fn)
        _wait_for(task_id, "error")
        time.sleep(0.1)
        files = os.listdir(log_dir)
        assert any(f.startswith(f"run_{task_id}_") and f.endswith(".log") for f in files)

    def test_log_file_contains_task_logs(self, log_dir):
        def fn(log):
            log("hello from task")
        task_id = create_task(fn)
        _wait_for(task_id, "done")
        time.sleep(0.1)
        files = [f for f in os.listdir(log_dir) if f.startswith(f"run_{task_id}_")]
        content = open(os.path.join(log_dir, files[0])).read()
        assert "hello from task" in content

    def test_log_file_contains_status_header(self, log_dir):
        def fn(log): pass
        task_id = create_task(fn)
        _wait_for(task_id, "done")
        time.sleep(0.1)
        files = [f for f in os.listdir(log_dir) if f.startswith(f"run_{task_id}_")]
        content = open(os.path.join(log_dir, files[0])).read()
        assert f"Task ID: {task_id}" in content
        assert "Status:  done" in content

    def test_no_tmp_file_left_behind(self, log_dir):
        def fn(log): pass
        task_id = create_task(fn)
        _wait_for(task_id, "done")
        time.sleep(0.1)
        tmp_files = [f for f in os.listdir(log_dir) if f.endswith(".tmp")]
        assert tmp_files == []


# ── Log rotation ──────────────────────────────────────────────────────────────

class TestRotateLogs:
    def test_deletes_files_older_than_retention(self, log_dir, monkeypatch):
        from web.tasks import _rotate_logs
        os.makedirs(log_dir, exist_ok=True)
        # Write a file dated 91 days ago
        old_date = (
            __import__("datetime").datetime.now()
            - __import__("datetime").timedelta(days=91)
        ).strftime("%Y-%m-%d")
        old_file = os.path.join(log_dir, f"run_abc123_{old_date}.log")
        open(old_file, "w").close()
        _rotate_logs()
        assert not os.path.exists(old_file)

    def test_keeps_files_within_retention(self, log_dir, monkeypatch):
        from web.tasks import _rotate_logs
        os.makedirs(log_dir, exist_ok=True)
        recent_date = (
            __import__("datetime").datetime.now()
            - __import__("datetime").timedelta(days=10)
        ).strftime("%Y-%m-%d")
        recent_file = os.path.join(log_dir, f"run_abc123_{recent_date}.log")
        open(recent_file, "w").close()
        _rotate_logs()
        assert os.path.exists(recent_file)

    def test_keeps_files_exactly_at_boundary(self, log_dir):
        from web.tasks import _rotate_logs
        os.makedirs(log_dir, exist_ok=True)
        boundary_date = (
            __import__("datetime").datetime.now()
            - __import__("datetime").timedelta(days=90)
        ).strftime("%Y-%m-%d")
        boundary_file = os.path.join(log_dir, f"run_abc123_{boundary_date}.log")
        open(boundary_file, "w").close()
        _rotate_logs()
        assert os.path.exists(boundary_file)

    def test_no_crash_when_log_dir_missing(self, log_dir):
        from web.tasks import _rotate_logs
        # log_dir doesn't exist yet — should not raise
        assert not os.path.isdir(log_dir)
        _rotate_logs()  # should be silent

    def test_ignores_files_with_unexpected_names(self, log_dir):
        from web.tasks import _rotate_logs
        os.makedirs(log_dir, exist_ok=True)
        odd_file = os.path.join(log_dir, "run_no_date.log")
        open(odd_file, "w").close()
        _rotate_logs()  # should not crash or delete it
        assert os.path.exists(odd_file)
