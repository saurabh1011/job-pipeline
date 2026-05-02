"""Integration tests for web/server.py — jobs CRUD, status, bulk, pipeline, tasks endpoints."""
import json
import pytest
import yaml
from fastapi.testclient import TestClient
from unittest.mock import patch

import web.server as server_module
from web.server import app

INITIAL_COMPANIES = [{"name": "Acme", "ats": "greenhouse", "board_slug": "acme"}]
INITIAL_PREFS = {
    "match_threshold": 7, "llm_provider": "gemini", "us_only": False,
    "title_keywords": ["Engineering Manager"], "title_exclude_keywords": [],
    "preferred_locations": ["New York"], "acceptable_locations": ["Remote"],
    "excluded_location_keywords": [],
}


@pytest.fixture()
def cfg_dir(tmp_path):
    (tmp_path / "companies.yaml").write_text(yaml.dump({"companies": INITIAL_COMPANIES}))
    (tmp_path / "preferences.yaml").write_text(yaml.dump(INITIAL_PREFS))
    return tmp_path


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def client(cfg_dir, db_path, monkeypatch):
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c


def _seed(db_path, company="Acme", job_id="j1", **kwargs):
    from pipeline.store import JobStore
    store = JobStore(db_path)
    store.upsert_job({
        "company": company, "job_id": job_id,
        "title": "Engineering Manager", "location": "New York, NY",
        "url": "https://example.com/j1", "description": "Lead teams.",
        "apply_url": "https://example.com/j1",
        **kwargs,
    })
    store.close()


# ── GET /api/jobs ─────────────────────────────────────────────────────────────

class TestListJobs:
    def test_empty_returns_empty_list(self, client):
        r = client.get("/api/jobs")
        assert r.status_code == 200
        assert r.json()["jobs"] == []

    def test_returns_all_jobs(self, client, db_path):
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        r = client.get("/api/jobs")
        assert len(r.json()["jobs"]) == 2

    def test_filter_by_status(self, client, db_path):
        from pipeline.store import JobStore, JobStatus
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        store = JobStore(db_path)
        store.update_status("Acme", "j2", JobStatus.APPROVED)
        store.close()
        r = client.get("/api/jobs?status=approved")
        jobs = r.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "j2"

    def test_sorted_by_score_descending(self, client, db_path):
        from pipeline.store import JobStore
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        store = JobStore(db_path)
        store.set_match_score("Acme", "j1", 6, "ok")
        store.set_match_score("Acme", "j2", 9, "great")
        store.close()
        r = client.get("/api/jobs")
        jobs = r.json()["jobs"]
        assert jobs[0]["job_id"] == "j2"  # score 9 first

    def test_match_fields_always_lists(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs")
        job = r.json()["jobs"][0]
        assert isinstance(job["match_requirements"], list)
        assert isinstance(job["match_resume_suggestions"], list)


# ── GET /api/jobs/{company}/{job_id} ──────────────────────────────────────────

class TestGetJob:
    def test_returns_job(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs/Acme/j1")
        assert r.status_code == 200
        assert r.json()["title"] == "Engineering Manager"

    def test_missing_job_returns_404(self, client):
        r = client.get("/api/jobs/Acme/nonexistent")
        assert r.status_code == 404

    def test_includes_cover_letter_none_when_no_file(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["cover_letter"] is None


# ── PATCH /api/jobs/{company}/{job_id} ────────────────────────────────────────

class TestUpdateJobStatus:
    def test_update_to_approved(self, client, db_path):
        _seed(db_path)
        r = client.patch("/api/jobs/Acme/j1", json={"status": "approved"})
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_status_persisted(self, client, db_path):
        _seed(db_path)
        client.patch("/api/jobs/Acme/j1", json={"status": "skipped"})
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["status"] == "skipped"

    def test_invalid_status_returns_400(self, client, db_path):
        _seed(db_path)
        r = client.patch("/api/jobs/Acme/j1", json={"status": "garbage"})
        assert r.status_code == 400

    def test_missing_job_returns_404(self, client):
        r = client.patch("/api/jobs/Acme/missing", json={"status": "approved"})
        assert r.status_code == 404

    @pytest.mark.parametrize("status", [
        "approved", "applied", "interviewing", "offer",
        "rejected", "skipped", "interesting", "alerted", "new",
    ])
    def test_all_valid_statuses_accepted(self, client, db_path, status):
        _seed(db_path)
        r = client.patch("/api/jobs/Acme/j1", json={"status": status})
        assert r.status_code == 200


# ── POST /api/jobs/bulk-status ────────────────────────────────────────────────

class TestBulkUpdateStatus:
    def test_bulk_update_two_jobs(self, client, db_path):
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        r = client.post("/api/jobs/bulk-status", json={
            "jobs": [{"company": "Acme", "job_id": "j1"}, {"company": "Acme", "job_id": "j2"}],
            "status": "skipped",
        })
        assert r.status_code == 200
        assert r.json()["updated"] == 2

    def test_nonexistent_job_ignored(self, client, db_path):
        _seed(db_path, job_id="j1")
        r = client.post("/api/jobs/bulk-status", json={
            "jobs": [{"company": "Acme", "job_id": "j1"}, {"company": "Acme", "job_id": "missing"}],
            "status": "approved",
        })
        assert r.json()["updated"] == 1

    def test_invalid_status_returns_400(self, client, db_path):
        _seed(db_path)
        r = client.post("/api/jobs/bulk-status", json={
            "jobs": [{"company": "Acme", "job_id": "j1"}],
            "status": "invalid",
        })
        assert r.status_code == 400


# ── GET /api/companies ────────────────────────────────────────────────────────

class TestListCompanies:
    def test_returns_company_names(self, client):
        r = client.get("/api/companies")
        assert r.status_code == 200
        assert "Acme" in r.json()

    def test_returns_list_of_strings(self, client):
        r = client.get("/api/companies")
        assert isinstance(r.json(), list)
        assert all(isinstance(n, str) for n in r.json())


# ── POST /api/pipeline/run ────────────────────────────────────────────────────

class TestPipelineRun:
    def test_returns_task_id(self, client):
        with patch("web.server._do_run", return_value={}):
            r = client.post("/api/pipeline/run", json={})
        assert r.status_code == 200
        assert "task_id" in r.json()

    def test_task_id_is_string(self, client):
        with patch("web.server._do_run", return_value={}):
            r = client.post("/api/pipeline/run", json={"action": "source"})
        assert isinstance(r.json()["task_id"], str)


# ── GET /api/tasks & GET /api/tasks/{id} ──────────────────────────────────────

class TestTaskEndpoints:
    def test_list_tasks_returns_list(self, client):
        r = client.get("/api/tasks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_task_after_creation(self, client):
        with patch("web.server._do_run", return_value={"ok": True}):
            run_r = client.post("/api/pipeline/run", json={})
        task_id = run_r.json()["task_id"]
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200
        assert r.json()["id"] == task_id

    def test_get_unknown_task_returns_404(self, client):
        r = client.get("/api/tasks/nonexistent")
        assert r.status_code == 404


# ── GET /api/runs ─────────────────────────────────────────────────────────────

class TestListRuns:
    def test_empty_returns_empty_list(self, client):
        r = client.get("/api/runs")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_run_records(self, client, db_path):
        from pipeline.store import JobStore
        store = JobStore(db_path)
        run_id = store.start_run("source_and_score", "all", 5)
        store.finish_run(run_id, jobs_fetched=20, jobs_new=5, jobs_scored=5)
        store.close()
        r = client.get("/api/runs")
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) == 1
        assert runs[0]["action"] == "source_and_score"
        assert runs[0]["jobs_fetched"] == 20
        assert runs[0]["status"] == "done"

    def test_newest_first(self, client, db_path):
        from pipeline.store import JobStore
        store = JobStore(db_path)
        id1 = store.start_run("source", "http", 3)
        id2 = store.start_run("score", "all", 5)
        store.close()
        r = client.get("/api/runs")
        runs = r.json()
        assert runs[0]["id"] == id2

    def test_limit_param(self, client, db_path):
        from pipeline.store import JobStore
        store = JobStore(db_path)
        for _ in range(5):
            store.start_run("source", "all", 1)
        store.close()
        r = client.get("/api/runs?limit=2")
        assert len(r.json()) == 2

    def test_error_run_included(self, client, db_path):
        from pipeline.store import JobStore
        store = JobStore(db_path)
        run_id = store.start_run("rescore", "all", 2)
        store.finish_run(run_id, status="error", error_msg="boom")
        store.close()
        r = client.get("/api/runs")
        run = r.json()[0]
        assert run["status"] == "error"
        assert run["error_msg"] == "boom"


# ── Date fields ───────────────────────────────────────────────────────────────

class TestDateFields:
    def test_date_last_sourced_in_api_response(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs")
        job = r.json()["jobs"][0]
        assert "date_last_sourced" in job
        assert job["date_last_sourced"] is not None

    def test_date_posted_null_when_not_provided(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs")
        job = r.json()["jobs"][0]
        assert "date_posted" in job
        assert job["date_posted"] is None

    def test_date_posted_returned_when_set(self, client, db_path):
        _seed(db_path, date_posted="2024-06-15")
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["date_posted"] == "2024-06-15"

    def test_date_last_sourced_in_single_job_response(self, client, db_path):
        _seed(db_path)
        r = client.get("/api/jobs/Acme/j1")
        assert r.status_code == 200
        assert r.json()["date_last_sourced"] is not None


# ── Auth enforcement ──────────────────────────────────────────────────────────

class TestAuthEnforcement:
    def test_jobs_requires_auth_when_key_set(self, client, monkeypatch):
        import web.auth as auth_mod
        monkeypatch.setattr(auth_mod, "_API_KEY", "testkey")
        r = client.get("/api/jobs")
        assert r.status_code == 401

    def test_jobs_accepts_correct_key(self, client, monkeypatch):
        import web.auth as auth_mod
        monkeypatch.setattr(auth_mod, "_API_KEY", "testkey")
        r = client.get("/api/jobs", headers={"x-api-key": "testkey"})
        assert r.status_code == 200
