"""Integration tests for web/server.py — jobs CRUD, status, bulk, pipeline, tasks endpoints."""
import json
import pytest
import yaml
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

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


# ── PUT /api/jobs/{company}/{job_id}/cover-letter ─────────────────────────────

class TestUpdateCoverLetter:
    def test_saves_cover_letter(self, client, db_path, tmp_path, monkeypatch):
        monkeypatch.setattr("web.server.OUTPUT_DIR", str(tmp_path))
        _seed(db_path)
        r = client.put("/api/jobs/Acme/j1/cover-letter", json={"content": "Hello world"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_content_readable_back_via_get(self, client, db_path, tmp_path, monkeypatch):
        monkeypatch.setattr("web.server.OUTPUT_DIR", str(tmp_path))
        _seed(db_path)
        client.put("/api/jobs/Acme/j1/cover-letter", json={"content": "My cover letter."})
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["cover_letter"] == "My cover letter."

    def test_overwrites_existing_cover_letter(self, client, db_path, tmp_path, monkeypatch):
        monkeypatch.setattr("web.server.OUTPUT_DIR", str(tmp_path))
        _seed(db_path)
        client.put("/api/jobs/Acme/j1/cover-letter", json={"content": "First version"})
        client.put("/api/jobs/Acme/j1/cover-letter", json={"content": "Second version"})
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["cover_letter"] == "Second version"

    def test_missing_job_returns_404(self, client):
        r = client.put("/api/jobs/Acme/missing/cover-letter", json={"content": "x"})
        assert r.status_code == 404

    def test_creates_output_dir_if_missing(self, client, db_path, tmp_path, monkeypatch):
        out = tmp_path / "output"
        monkeypatch.setattr("web.server.OUTPUT_DIR", str(out))
        _seed(db_path)
        r = client.put("/api/jobs/Acme/j1/cover-letter", json={"content": "hi"})
        assert r.status_code == 200
        assert (out / "Acme_j1" / "cover_letter.md").read_text() == "hi"

    def test_unicode_content_preserved(self, client, db_path, tmp_path, monkeypatch):
        monkeypatch.setattr("web.server.OUTPUT_DIR", str(tmp_path))
        _seed(db_path)
        content = "Dear team,\n\nI'm excited — résumé enclosed.\n\nSincerely,\nSaurabh"
        client.put("/api/jobs/Acme/j1/cover-letter", json={"content": content})
        r = client.get("/api/jobs/Acme/j1")
        assert r.json()["cover_letter"] == content


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
        names = [c["name"] for c in r.json()]
        assert "Acme" in names

    def test_returns_list_of_objects_with_name_and_playwright(self, client):
        r = client.get("/api/companies")
        assert isinstance(r.json(), list)
        for c in r.json():
            assert "name" in c
            assert "playwright" in c
            assert isinstance(c["name"], str)
            assert isinstance(c["playwright"], bool)


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
        monkeypatch.setenv("WEB_API_KEY", "testkey")
        r = client.get("/api/jobs")
        assert r.status_code == 401

    def test_jobs_accepts_correct_key(self, client, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "testkey")
        r = client.get("/api/jobs", headers={"x-api-key": "testkey"})
        assert r.status_code == 200


# ── _score_job_list unit tests ────────────────────────────────────────────────

def _fake_job(company="Acme", job_id="j1", title="Engineering Manager"):
    return {"company": company, "job_id": job_id, "title": title,
            "location": "New York", "url": "https://example.com",
            "apply_url": "https://example.com", "description": "Lead teams."}


def _mock_score_result(score=9, summary="Great match", meets=True):
    r = MagicMock()
    r.adjusted_score = score
    r.summary = summary
    r.strengths = []
    r.gaps = []
    r.meets_threshold.return_value = meets
    return r


class TestScoreJobList:
    def test_returns_tuple_of_three(self, db_path):
        from web.server import _score_job_list
        scorer = MagicMock()
        scorer.score.return_value = _mock_score_result()
        loader = MagicMock()
        with patch("pipeline.scorer.JobScorer", return_value=scorer):
            result = _score_job_list(lambda m: None, [_fake_job()], {}, MagicMock(), 7, loader, db_path)
        assert len(result) == 3

    def test_scored_count_increments_on_success(self, db_path):
        from web.server import _score_job_list
        scorer = MagicMock()
        scorer.score.return_value = _mock_score_result()
        loader = MagicMock()
        with patch("pipeline.scorer.JobScorer", return_value=scorer):
            scored, failed, _ = _score_job_list(
                lambda m: None, [_fake_job(), _fake_job(job_id="j2")], {}, MagicMock(), 7, loader, db_path)
        assert scored == 2
        assert failed == 0

    def test_failed_count_increments_on_exception(self, db_path):
        from web.server import _score_job_list
        scorer = MagicMock()
        scorer.score.side_effect = Exception("LLM error")
        loader = MagicMock()
        with patch("pipeline.scorer.JobScorer", return_value=scorer):
            scored, failed, scored_jobs = _score_job_list(
                lambda m: None, [_fake_job()], {}, MagicMock(), 7, loader, db_path)
        assert scored == 0
        assert failed == 1
        assert scored_jobs == []

    def test_scored_jobs_contain_match_score_and_summary(self, db_path):
        from web.server import _score_job_list
        scorer = MagicMock()
        scorer.score.return_value = _mock_score_result(score=8, summary="Strong alignment")
        loader = MagicMock()
        with patch("pipeline.scorer.JobScorer", return_value=scorer):
            _, _, scored_jobs = _score_job_list(
                lambda m: None, [_fake_job()], {}, MagicMock(), 7, loader, db_path)
        assert scored_jobs[0]["match_score"] == 8
        assert scored_jobs[0]["match_summary"] == "Strong alignment"

    def test_partial_failure_mixes_counted_correctly(self, db_path):
        from web.server import _score_job_list
        scorer = MagicMock()
        scorer.score.side_effect = [_mock_score_result(score=9), Exception("LLM timeout")]
        loader = MagicMock()
        with patch("pipeline.scorer.JobScorer", return_value=scorer):
            scored, failed, scored_jobs = _score_job_list(
                lambda m: None, [_fake_job(), _fake_job(job_id="j2")], {}, MagicMock(), 7, loader, db_path)
        assert scored == 1
        assert failed == 1
        assert len(scored_jobs) == 1


# ── _do_run → email integration ───────────────────────────────────────────────

@pytest.fixture()
def do_run_patches(cfg_dir, db_path, monkeypatch):
    """Fixture that sets up monkeypatches and common mocks for _do_run tests."""
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setattr(server_module, "PROFILE_DIR", str(cfg_dir))
    with patch("pipeline.llm.create_provider", return_value=MagicMock()), \
         patch("pipeline.profile.ProfileLoader", return_value=MagicMock()):
        yield


class TestDoRunEmailIntegration:
    def test_email_sent_after_successful_run(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        fake_job = _fake_job()
        scored_job = {**fake_job, "match_score": 9, "match_summary": "Great match"}
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job]), \
             patch("web.server._score_job_list", return_value=(1, 0, [scored_job])), \
             patch("web.server._send_pipeline_email", return_value=None) as mock_email:
            _do_run(lambda m: None)
        mock_email.assert_called_once()

    def test_email_stats_include_scored_count(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        fake_job = _fake_job()
        scored_job = {**fake_job, "match_score": 9, "match_summary": "Great match"}
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job]), \
             patch("web.server._score_job_list", return_value=(1, 0, [scored_job])), \
             patch("web.server._send_pipeline_email", return_value=None) as mock_email:
            _do_run(lambda m: None)
        _, _, _, stats = mock_email.call_args[0]
        assert stats["scored_jobs"] == 1
        assert stats["failed_scoring"] == 0

    def test_email_sent_even_when_fetch_crashes(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        with patch("pipeline.fetcher.fetch_all_companies", side_effect=Exception("Network down")), \
             patch("web.server._send_pipeline_email", return_value=None) as mock_email:
            with pytest.raises(Exception, match="Network down"):
                _do_run(lambda m: None)
        mock_email.assert_called_once()
        _, _, _, stats = mock_email.call_args[0]
        assert stats["run_error"] == "Network down"

    def test_fetch_errors_flow_to_email_stats(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run

        def fetch_with_errors(companies, prefs, log=None, fetch_errors=None, fetch_counts=None):
            if fetch_errors is not None:
                fetch_errors["Acme"] = "TIMEOUT after 60s"
            return []

        with patch("pipeline.fetcher.fetch_all_companies", side_effect=fetch_with_errors), \
             patch("web.server._send_pipeline_email", return_value=None) as mock_email:
            _do_run(lambda m: None)

        _, _, _, stats = mock_email.call_args[0]
        assert "Acme" in stats["fetch_errors"]
        assert "TIMEOUT" in stats["fetch_errors"]["Acme"]

    def test_alert_jobs_filtered_by_threshold(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        fake_job = _fake_job()
        high = {**fake_job, "match_score": 9, "match_summary": "Great"}
        low = {**_fake_job(job_id="j2"), "match_score": 5, "match_summary": "Weak"}
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job, _fake_job(job_id="j2")]), \
             patch("web.server._score_job_list", return_value=(2, 0, [high, low])), \
             patch("web.server._send_pipeline_email", return_value=None) as mock_email:
            _do_run(lambda m: None)
        _, all_scored, alert_jobs, _ = mock_email.call_args[0]
        assert len(all_scored) == 2
        assert all(j["match_score"] >= 7 for j in alert_jobs)
        assert len(alert_jobs) == 1

    def test_run_status_done_email_failed_when_send_fails(self, do_run_patches, cfg_dir, db_path):
        """Regression test: a pipeline that fetches/scores fine but fails to
        email must NOT be marked status='done' with the failure invisible.
        It also must not be marked status='error' — the pipeline itself
        succeeded, only the notification failed."""
        from web.server import _do_run
        from pipeline.store import JobStore
        fake_job = _fake_job()
        scored_job = {**fake_job, "match_score": 9, "match_summary": "Great match"}
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job]), \
             patch("web.server._score_job_list", return_value=(1, 0, [scored_job])), \
             patch("web.server._send_pipeline_email", return_value="535 Bad Credentials"):
            _do_run(lambda m: None)

        store = JobStore(db_path)
        latest = store.list_runs(limit=1)[0]
        store.close()
        assert latest["status"] == "done_email_failed"
        assert "Bad Credentials" in latest["error_msg"]

    def test_run_status_stays_done_when_email_succeeds(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        from pipeline.store import JobStore
        fake_job = _fake_job()
        scored_job = {**fake_job, "match_score": 9, "match_summary": "Great match"}
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job]), \
             patch("web.server._score_job_list", return_value=(1, 0, [scored_job])), \
             patch("web.server._send_pipeline_email", return_value=None):
            _do_run(lambda m: None)

        store = JobStore(db_path)
        latest = store.list_runs(limit=1)[0]
        store.close()
        assert latest["status"] == "done"
        assert latest["error_msg"] is None

    def test_email_failure_reaches_log_callback(self, do_run_patches, cfg_dir, db_path, monkeypatch):
        """The task drawer log() must show the email failure — logger.error
        alone (what the old code used) never reaches the UI. Exercises the
        real _send_pipeline_email (only the SMTP transport is mocked), not a
        stand-in, so this covers the actual wiring end to end."""
        from web.server import _do_run
        monkeypatch.setenv("SMTP_USER", "u@g.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pw")
        monkeypatch.setenv("ALERT_EMAIL", "me@g.com")
        fake_job = _fake_job()
        scored_job = {**fake_job, "match_score": 9, "match_summary": "Great match"}
        logs = []
        with patch("pipeline.fetcher.fetch_all_companies", return_value=[fake_job]), \
             patch("web.server._score_job_list", return_value=(1, 0, [scored_job])), \
             patch("pipeline.alerter.smtplib.SMTP_SSL") as mock_ssl:
            mock_ssl.return_value.__enter__.side_effect = Exception("535 Bad Credentials")
            _do_run(logs.append)
        assert any("Bad Credentials" in line for line in logs)


def _seed_jobs(db_path, company, job_ids):
    from pipeline.store import JobStore
    store = JobStore(db_path)
    for jid in job_ids:
        store.upsert_job({
            "company": company, "job_id": jid, "title": "EM",
            "location": "NY", "url": "http://x", "apply_url": "http://x",
            "description": "d",
        })
    store.close()


def _make_fetched(company, job_ids):
    return [
        {"company": company, "job_id": jid, "title": "EM",
         "location": "NY", "url": "http://x", "apply_url": "http://x", "description": "d"}
        for jid in job_ids
    ]


class TestNotAvailableStatus:
    def test_patch_accepts_not_available(self, client, db_path):
        _seed(db_path)
        r = client.patch("/api/jobs/Acme/j1", json={"status": "not_available"})
        assert r.status_code == 200
        assert r.json()["status"] == "not_available"

    def test_bulk_accepts_not_available(self, client, db_path):
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        r = client.post("/api/jobs/bulk-status", json={
            "jobs": [{"company": "Acme", "job_id": "j1"}, {"company": "Acme", "job_id": "j2"}],
            "status": "not_available",
        })
        assert r.status_code == 200
        assert r.json()["updated"] == 2

    def test_not_available_filterable_via_status_query(self, client, db_path):
        from pipeline.store import JobStore
        _seed(db_path, job_id="j1")
        _seed(db_path, job_id="j2")
        store = JobStore(db_path)
        store.update_status("Acme", "j1", "not_available")
        store.close()
        r = client.get("/api/jobs?status=not_available")
        jobs = r.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "j1"


class TestMarkUnavailableIntegration:
    def test_missing_jobs_marked_not_available(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        from pipeline.store import JobStore, JobStatus

        _seed_jobs(db_path, "Acme", ["j1", "j2", "j3"])

        with patch("pipeline.fetcher.fetch_all_companies",
                   return_value=_make_fetched("Acme", ["j1", "j2"])), \
             patch("web.server._send_pipeline_email", return_value=None):
            _do_run(lambda m: None)

        store = JobStore(db_path)
        assert store.get_job("Acme", "j3")["status"] == JobStatus.NOT_AVAILABLE
        assert store.get_job("Acme", "j1")["status"] != JobStatus.NOT_AVAILABLE
        assert store.get_job("Acme", "j2")["status"] != JobStatus.NOT_AVAILABLE
        store.close()

    def test_threshold_guard_prevents_marking(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        from pipeline.store import JobStore, JobStatus

        _seed_jobs(db_path, "Acme", [f"j{i}" for i in range(10)])

        # 4 seen / 10 active = 40% < 50% threshold
        with patch("pipeline.fetcher.fetch_all_companies",
                   return_value=_make_fetched("Acme", ["j0", "j1", "j2", "j3"])), \
             patch("web.server._send_pipeline_email", return_value=None):
            _do_run(lambda m: None)

        store = JobStore(db_path)
        for i in range(10):
            assert store.get_job("Acme", f"j{i}")["status"] == JobStatus.NEW
        store.close()

    def test_reappearing_not_available_job_reset_to_new(self, do_run_patches, cfg_dir, db_path):
        from web.server import _do_run
        from pipeline.store import JobStore, JobStatus

        _seed_jobs(db_path, "Acme", ["j1", "j2"])
        store = JobStore(db_path)
        store.update_status("Acme", "j1", "not_available")
        store.close()

        # j1 reappears in current fetch alongside j2
        with patch("pipeline.fetcher.fetch_all_companies",
                   return_value=_make_fetched("Acme", ["j1", "j2"])), \
             patch("web.server._send_pipeline_email", return_value=None):
            _do_run(lambda m: None)

        store2 = JobStore(db_path)
        assert store2.get_job("Acme", "j1")["status"] == JobStatus.NEW
        store2.close()


class TestLogEndpoints:
    @pytest.fixture()
    def log_dir(self, tmp_path):
        return tmp_path / "logs"

    @pytest.fixture()
    def log_client(self, cfg_dir, db_path, monkeypatch, log_dir):
        log_dir.mkdir()
        monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
        monkeypatch.setattr(server_module, "DB_PATH", db_path)
        monkeypatch.setattr(server_module, "LOG_DIR", str(log_dir))
        monkeypatch.delenv("WEB_API_KEY", raising=False)
        with TestClient(app) as c:
            yield c, log_dir

    def test_list_logs_returns_files_sorted_by_date(self, log_client):
        client, log_dir = log_client
        (log_dir / "run_abc123_2026-05-10.log").write_text("older log")
        (log_dir / "run_def456_2026-05-14.log").write_text("newer log")
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        files = resp.json()
        assert len(files) == 2
        assert files[0]["date"] == "2026-05-14"
        assert files[0]["task_id"] == "def456"
        assert files[1]["date"] == "2026-05-10"
        assert files[1]["task_id"] == "abc123"

    def test_list_logs_returns_empty_when_no_files(self, log_client):
        client, log_dir = log_client
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_logs_caps_at_20(self, log_client):
        client, log_dir = log_client
        for i in range(25):
            day = f"2026-05-{i+1:02d}" if i < 9 else f"2026-05-{i+1}"
            # Use a safe date range: 01–25
            day = f"2026-04-{i+1:02d}"
            (log_dir / f"run_task{i}_{day}.log").write_text(f"log {i}")
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        assert len(resp.json()) == 20

    def test_list_logs_includes_size(self, log_client):
        client, log_dir = log_client
        content = "hello world"
        (log_dir / "run_xyz_2026-05-15.log").write_text(content)
        resp = client.get("/api/logs")
        files = resp.json()
        assert files[0]["size_bytes"] == len(content.encode())

    def test_get_log_returns_content(self, log_client):
        client, log_dir = log_client
        content = "line1\nline2\nline3\n"
        (log_dir / "run_abc_2026-05-15.log").write_text(content)
        resp = client.get("/api/logs/run_abc_2026-05-15.log")
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "run_abc_2026-05-15.log"
        assert body["content"] == content

    def test_get_log_returns_404_for_missing_file(self, log_client):
        client, _ = log_client
        resp = client.get("/api/logs/run_missing_2026-05-15.log")
        assert resp.status_code == 404

    def test_get_log_rejects_path_traversal(self, log_client):
        client, _ = log_client
        resp = client.get("/api/logs/..%2Fsecrets.txt")
        assert resp.status_code in (400, 404, 422)

    def test_get_log_rejects_non_run_filename(self, log_client):
        client, _ = log_client
        resp = client.get("/api/logs/secrets.txt")
        assert resp.status_code == 400

    def test_get_log_rejects_backslash_traversal(self, log_client):
        client, _ = log_client
        resp = client.get("/api/logs/run_..\\etc\\passwd.log")
        assert resp.status_code == 400
