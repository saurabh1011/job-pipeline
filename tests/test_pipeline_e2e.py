"""
CRITICAL: End-to-end integration test for the daily scheduled pipeline run.

This file guards against regressions in the daily scheduled pipeline triggered
by GitHub Actions (see .github/workflows/daily_pipeline.yml).

The workflow does exactly this:
  1. POST /api/pipeline/run  with X-API-Key and an empty JSON body {}
  2. Poll GET /api/tasks/{task_id} until status is "done" or "error"

These tests exercise that exact path — from HTTP request through background
task execution — without mocking away the task infrastructure or _do_run itself.

DO NOT delete, rename, or skip these tests without explicit approval from
the repo owner. Any PR that modifies this file requires owner review
(see .github/CODEOWNERS).

If this test suite breaks, the production daily run is at risk.
"""
import time
import pytest
import yaml
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import web.auth_db as adb
import web.server as server_module
import web.tasks as tasks_module
from web.server import app


INITIAL_PREFS = {
    "match_threshold": 7,
    "llm_provider": "gemini",
    "us_only": False,
    "title_keywords": ["Engineering Manager"],
    "title_exclude_keywords": [],
    "preferred_locations": [],
    "acceptable_locations": [],
    "excluded_location_keywords": [],
}

_API_KEY_HEADER = {"X-API-Key": "test-key"}


@pytest.fixture()
def cfg_dir(tmp_path):
    (tmp_path / "companies.yaml").write_text(yaml.dump({"companies": []}))
    (tmp_path / "preferences.yaml").write_text(yaml.dump(INITIAL_PREFS))
    return tmp_path


@pytest.fixture()
def e2e_client(tmp_path, monkeypatch, cfg_dir):
    """Full TestClient wired to temp DB and config — mirrors the production service-user setup."""
    db_path = str(tmp_path / "jobs.db")
    auth_path = str(tmp_path / "auth.db")
    log_dir = str(tmp_path / "logs")
    output_dir = str(tmp_path / "output")

    monkeypatch.setattr(adb, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setattr(server_module, "PROFILE_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(tasks_module, "_LOG_DIR", log_dir)
    monkeypatch.setenv("WEB_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)

    adb.init_db()
    server_module._auth_db.AUTH_DB_PATH = auth_path

    with TestClient(app) as client:
        yield client


def _wait_for_task(client, task_id, timeout_s=10):
    """Poll /api/tasks/{id} until terminal status or timeout. Returns final task dict."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}", headers=_API_KEY_HEADER)
        if r.status_code == 200 and r.json().get("status") in ("done", "error"):
            return r.json()
        time.sleep(0.1)
    return client.get(f"/api/tasks/{task_id}", headers=_API_KEY_HEADER).json()


def _pipeline_mocks():
    """Context manager that patches all external calls made by _do_run."""
    return (
        patch("pipeline.fetcher.fetch_all_companies", return_value=[]),
        patch("web.server._send_pipeline_email"),
        patch("pipeline.llm.create_provider", return_value=MagicMock()),
        patch("pipeline.profile.ProfileLoader", return_value=MagicMock()),
    )


# ── Critical tests — guard the GitHub Actions daily run path ──────────────────

class TestDailyPipelineRunE2E:
    """
    Mirrors the exact request sequence used by .github/workflows/daily_pipeline.yml.
    Every test in this class must remain passing for the production daily run to work.
    """

    def test_empty_body_with_api_key_returns_task_id(self, e2e_client):
        """GitHub Actions sends an empty body {}. Must return a task_id."""
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[]), \
             patch("web.server._send_pipeline_email"), \
             patch("pipeline.llm.create_provider", return_value=MagicMock()), \
             patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
            r = e2e_client.post("/api/pipeline/run", headers=_API_KEY_HEADER, json={})

        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "task_id" in data
        assert data["task_id"]

    def test_pipeline_task_reaches_done_status(self, e2e_client):
        """The background task must complete with status='done', not hang or error."""
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[]), \
             patch("web.server._send_pipeline_email"), \
             patch("pipeline.llm.create_provider", return_value=MagicMock()), \
             patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
            r = e2e_client.post("/api/pipeline/run", headers=_API_KEY_HEADER, json={})
            task_id = r.json()["task_id"]
            task = _wait_for_task(e2e_client, task_id)

        assert task["status"] == "done", (
            f"Pipeline task ended with status='{task['status']}'. "
            f"Last log lines: {task.get('logs', [])[-5:]}"
        )

    def test_completed_run_appears_in_history(self, e2e_client):
        """After a successful run, GET /api/runs must include a completed run record."""
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[]), \
             patch("web.server._send_pipeline_email"), \
             patch("pipeline.llm.create_provider", return_value=MagicMock()), \
             patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
            r = e2e_client.post("/api/pipeline/run", headers=_API_KEY_HEADER, json={})
            task_id = r.json()["task_id"]
            task = _wait_for_task(e2e_client, task_id)

        assert task["status"] == "done"
        runs_r = e2e_client.get("/api/runs", headers=_API_KEY_HEADER)
        assert runs_r.status_code == 200
        runs = runs_r.json()
        assert len(runs) >= 1, "Expected at least one run record in history"
        latest = runs[0]
        assert latest["status"] == "done", (
            f"Latest run has status='{latest['status']}', expected 'done'"
        )
        assert latest["action"] == "source_and_score"

    def test_email_alerter_called_after_run(self, e2e_client):
        """_send_pipeline_email must be called once per run (success or failure)."""
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[]), \
             patch("web.server._send_pipeline_email") as mock_email, \
             patch("pipeline.llm.create_provider", return_value=MagicMock()), \
             patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
            r = e2e_client.post("/api/pipeline/run", headers=_API_KEY_HEADER, json={})
            task_id = r.json()["task_id"]
            _wait_for_task(e2e_client, task_id)

        mock_email.assert_called_once()

    def test_unauthenticated_request_rejected(self, e2e_client):
        """Requests without X-API-Key must be rejected — the pipeline must not be publicly triggerable."""
        r = e2e_client.post("/api/pipeline/run", json={})
        assert r.status_code == 401

    def test_task_status_polling_returns_terminal_state(self, e2e_client):
        """GET /api/tasks/{id} must eventually return 'done' or 'error', never stay 'running' forever."""
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[]), \
             patch("web.server._send_pipeline_email"), \
             patch("pipeline.llm.create_provider", return_value=MagicMock()), \
             patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
            r = e2e_client.post("/api/pipeline/run", headers=_API_KEY_HEADER, json={})
            task_id = r.json()["task_id"]
            task = _wait_for_task(e2e_client, task_id)

        assert task["status"] in ("done", "error"), (
            f"Task never reached terminal state within timeout. status={task['status']}"
        )
        assert task["ended_at"] is not None, "Task missing ended_at timestamp"
