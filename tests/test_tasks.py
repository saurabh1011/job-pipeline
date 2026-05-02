"""Unit tests for web/tasks.py — in-memory background task runner."""
import threading
import time
import pytest

from web.tasks import create_task, get_task, list_tasks, _tasks, _lock


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
