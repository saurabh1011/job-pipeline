"""Unit tests for the job store (SQLite backend)."""
import os
import tempfile
import pytest
from pipeline.store import JobStore, JobStatus


@pytest.fixture
def store():
    """Provide a fresh in-memory store for each test."""
    s = JobStore(":memory:")
    yield s
    s.close()


def make_job(**kwargs):
    defaults = {
        "job_id": "123",
        "company": "Acme",
        "title": "Senior Engineering Manager",
        "location": "New York, NY",
        "url": "https://example.com/jobs/123",
        "description": "Lead a team of engineers...",
        "apply_url": "https://example.com/apply/123",
    }
    defaults.update(kwargs)
    return defaults


class TestJobStoreInit:
    def test_creates_schema_on_init(self, store):
        # Should be able to insert without error if schema exists
        store.upsert_job(make_job())

    def test_file_db_created_on_disk(self, tmp_path):
        db_path = str(tmp_path / "jobs.db")
        s = JobStore(db_path)
        s.close()
        assert os.path.exists(db_path)


class TestUpsertAndFetch:
    def test_upsert_new_job_returns_true(self, store):
        is_new = store.upsert_job(make_job())
        assert is_new is True

    def test_upsert_same_job_twice_returns_false(self, store):
        store.upsert_job(make_job())
        is_new = store.upsert_job(make_job())
        assert is_new is False

    def test_different_companies_same_id_are_distinct(self, store):
        assert store.upsert_job(make_job(company="Google")) is True
        assert store.upsert_job(make_job(company="Uber")) is True

    def test_get_job_returns_inserted_record(self, store):
        job = make_job()
        store.upsert_job(job)
        result = store.get_job("Acme", "123")
        assert result["title"] == "Senior Engineering Manager"
        assert result["location"] == "New York, NY"

    def test_get_job_missing_returns_none(self, store):
        assert store.get_job("NoCompany", "999") is None

    def test_date_last_sourced_set_on_new_job(self, store):
        store.upsert_job(make_job())
        job = store.get_job("Acme", "123")
        assert job["date_last_sourced"] is not None

    def test_date_posted_stored_on_new_job(self, store):
        store.upsert_job(make_job(date_posted="2024-01-15"))
        job = store.get_job("Acme", "123")
        assert job["date_posted"] == "2024-01-15"

    def test_date_posted_none_when_not_provided(self, store):
        store.upsert_job(make_job())
        job = store.get_job("Acme", "123")
        assert job["date_posted"] is None

    def test_date_last_sourced_updated_on_re_upsert(self, store):
        store.upsert_job(make_job())
        first_sourced = store.get_job("Acme", "123")["date_last_sourced"]
        import time
        time.sleep(0.01)
        store.upsert_job(make_job())
        second_sourced = store.get_job("Acme", "123")["date_last_sourced"]
        assert second_sourced >= first_sourced

    def test_date_posted_not_overwritten_on_re_upsert(self, store):
        store.upsert_job(make_job(date_posted="2024-01-10"))
        store.upsert_job(make_job(date_posted="2024-02-20"))  # existing job, should keep original
        job = store.get_job("Acme", "123")
        assert job["date_posted"] == "2024-01-10"

    def test_date_posted_set_on_re_upsert_when_previously_null(self, store):
        store.upsert_job(make_job())  # no date_posted
        store.upsert_job(make_job(date_posted="2024-03-01"))  # now provide one
        job = store.get_job("Acme", "123")
        assert job["date_posted"] == "2024-03-01"


class TestStatusUpdates:
    def test_default_status_is_new(self, store):
        store.upsert_job(make_job())
        job = store.get_job("Acme", "123")
        assert job["status"] == JobStatus.NEW

    def test_update_status(self, store):
        store.upsert_job(make_job())
        store.update_status("Acme", "123", JobStatus.ALERTED)
        job = store.get_job("Acme", "123")
        assert job["status"] == JobStatus.ALERTED

    def test_all_statuses_are_valid(self, store):
        for status in [JobStatus.NEW, JobStatus.ALERTED, JobStatus.APPROVED,
                       JobStatus.APPLIED, JobStatus.SKIPPED]:
            store.upsert_job(make_job(job_id=status))
            store.update_status("Acme", status, status)


class TestMatchScore:
    def test_set_and_retrieve_match_score(self, store):
        store.upsert_job(make_job())
        store.set_match_score("Acme", "123", 8, "Strong alignment on team leadership.")
        job = store.get_job("Acme", "123")
        assert job["match_score"] == 8
        assert "Strong alignment" in job["match_summary"]


class TestQueries:
    def test_get_new_jobs_returns_only_new(self, store):
        store.upsert_job(make_job(job_id="1"))
        store.upsert_job(make_job(job_id="2"))
        store.upsert_job(make_job(job_id="3"))
        store.update_status("Acme", "2", JobStatus.ALERTED)
        new_jobs = store.get_jobs_by_status(JobStatus.NEW)
        ids = [j["job_id"] for j in new_jobs]
        assert "1" in ids
        assert "3" in ids
        assert "2" not in ids

    def test_get_approved_jobs(self, store):
        store.upsert_job(make_job(job_id="approved1"))
        store.update_status("Acme", "approved1", JobStatus.APPROVED)
        jobs = store.get_jobs_by_status(JobStatus.APPROVED)
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "approved1"

    def test_list_all_jobs(self, store):
        store.upsert_job(make_job(job_id="a"))
        store.upsert_job(make_job(job_id="b"))
        all_jobs = store.list_all_jobs()
        assert len(all_jobs) == 2


class TestPipelineRuns:
    def test_start_run_returns_int_id(self, store):
        run_id = store.start_run("source_and_score", "all", 5)
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_start_run_creates_running_record(self, store):
        run_id = store.start_run("source", "http", 10)
        runs = store.list_runs()
        assert len(runs) == 1
        assert runs[0]["id"] == run_id
        assert runs[0]["status"] == "running"
        assert runs[0]["action"] == "source"
        assert runs[0]["group_type"] == "http"
        assert runs[0]["companies_count"] == 10

    def test_start_run_records_started_at(self, store):
        store.start_run("score", "all", 3)
        runs = store.list_runs()
        assert runs[0]["started_at"] is not None

    def test_finish_run_sets_done_status(self, store):
        run_id = store.start_run("source_and_score", "all", 5)
        store.finish_run(run_id, jobs_fetched=20, jobs_new=5, jobs_scored=5, jobs_generated=2)
        runs = store.list_runs()
        assert runs[0]["status"] == "done"

    def test_finish_run_stores_stats(self, store):
        run_id = store.start_run("source_and_score", "all", 5)
        store.finish_run(run_id, jobs_fetched=30, jobs_new=8, jobs_scored=8, jobs_generated=3)
        run = store.list_runs()[0]
        assert run["jobs_fetched"] == 30
        assert run["jobs_new"] == 8
        assert run["jobs_scored"] == 8
        assert run["jobs_generated"] == 3

    def test_finish_run_sets_ended_at(self, store):
        run_id = store.start_run("score", "playwright", 4)
        store.finish_run(run_id)
        run = store.list_runs()[0]
        assert run["ended_at"] is not None

    def test_finish_run_error_status(self, store):
        run_id = store.start_run("rescore", "all", 2)
        store.finish_run(run_id, status="error", error_msg="Connection timeout")
        run = store.list_runs()[0]
        assert run["status"] == "error"
        assert run["error_msg"] == "Connection timeout"

    def test_list_runs_newest_first(self, store):
        id1 = store.start_run("source", "all", 1)
        id2 = store.start_run("score", "all", 1)
        runs = store.list_runs()
        assert runs[0]["id"] == id2
        assert runs[1]["id"] == id1

    def test_list_runs_respects_limit(self, store):
        for i in range(5):
            store.start_run("source", "all", i)
        assert len(store.list_runs(limit=3)) == 3

    def test_list_runs_empty(self, store):
        assert store.list_runs() == []

    def test_multiple_runs_independent(self, store):
        id1 = store.start_run("source", "http", 10)
        id2 = store.start_run("score", "playwright", 4)
        store.finish_run(id1, jobs_fetched=50, jobs_new=10)
        store.finish_run(id2, jobs_fetched=0, jobs_scored=4)
        runs = {r["id"]: r for r in store.list_runs()}
        assert runs[id1]["jobs_fetched"] == 50
        assert runs[id2]["jobs_scored"] == 4


class TestSetAnalysis:
    def test_set_and_retrieve_requirements(self, store):
        store.upsert_job(make_job())
        reqs = [
            {"requirement": "Lead a team", "fit": "Strong", "evidence": "Led 10 engineers", "resume_suggestion": None},
            {"requirement": "Own roadmap", "fit": "Partial", "evidence": "Contributed to roadmap", "resume_suggestion": "Add roadmap ownership"},
        ]
        suggestions = ["Add metrics to bullets", "Highlight roadmap ownership"]
        store.set_analysis("Acme", "123", reqs, suggestions)
        job = store.get_job("Acme", "123")
        assert job["match_requirements"] is not None
        assert job["match_resume_suggestions"] is not None

    def test_requirements_stored_as_json(self, store):
        import json
        store.upsert_job(make_job())
        reqs = [{"requirement": "Build systems", "fit": "Strong", "evidence": "Built X", "resume_suggestion": None}]
        store.set_analysis("Acme", "123", reqs, [])
        job = store.get_job("Acme", "123")
        parsed = json.loads(job["match_requirements"])
        assert parsed[0]["requirement"] == "Build systems"
        assert parsed[0]["fit"] == "Strong"

    def test_resume_suggestions_stored_as_json(self, store):
        import json
        store.upsert_job(make_job())
        suggestions = ["Highlight EM experience", "Add scope metrics"]
        store.set_analysis("Acme", "123", [], suggestions)
        job = store.get_job("Acme", "123")
        parsed = json.loads(job["match_resume_suggestions"])
        assert parsed == suggestions

    def test_set_analysis_empty_lists(self, store):
        import json
        store.upsert_job(make_job())
        store.set_analysis("Acme", "123", [], [])
        job = store.get_job("Acme", "123")
        assert json.loads(job["match_requirements"]) == []
        assert json.loads(job["match_resume_suggestions"]) == []

    def test_set_analysis_overwrites_previous(self, store):
        import json
        store.upsert_job(make_job())
        store.set_analysis("Acme", "123", [{"requirement": "Old", "fit": "Gap", "evidence": "e", "resume_suggestion": None}], [])
        store.set_analysis("Acme", "123", [{"requirement": "New", "fit": "Strong", "evidence": "e", "resume_suggestion": None}], ["new tip"])
        job = store.get_job("Acme", "123")
        reqs = json.loads(job["match_requirements"])
        assert reqs[0]["requirement"] == "New"
        assert json.loads(job["match_resume_suggestions"]) == ["new tip"]

    def test_columns_exist_on_fresh_store(self, store):
        store.upsert_job(make_job())
        job = store.get_job("Acme", "123")
        assert "match_requirements" in job
        assert "match_resume_suggestions" in job
        assert job["match_requirements"] is None
        assert job["match_resume_suggestions"] is None


class TestMarkUnavailableJobs:
    def test_marks_missing_active_jobs_not_available(self, store):
        for jid in ["j1", "j2", "j3"]:
            store.upsert_job(make_job(job_id=jid))
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"j1", "j2"})
        assert marked == 1
        assert reason is None
        assert store.get_job("Acme", "j3")["status"] == "not_available"
        assert store.get_job("Acme", "j1")["status"] == "new"
        assert store.get_job("Acme", "j2")["status"] == "new"

    def test_threshold_guard_skips_marking(self, store):
        for i in range(10):
            store.upsert_job(make_job(job_id=str(i)))
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"0", "1", "2", "3"})
        assert marked == 0
        assert reason is not None
        assert "threshold" in reason.lower()
        for i in range(10):
            assert store.get_job("Acme", str(i))["status"] == "new"

    def test_already_unavailable_jobs_not_in_active_set(self, store):
        for jid in ["j1", "j2", "j3", "j4"]:
            store.upsert_job(make_job(job_id=jid))
        store.update_status("Acme", "j4", "not_available")
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"j1", "j2", "j3"})
        assert marked == 0
        assert n_active == 3
        assert reason is None

    def test_terminal_status_jobs_excluded_from_active(self, store):
        store.upsert_job(make_job(job_id="applied1"))
        store.update_status("Acme", "applied1", "applied")
        store.upsert_job(make_job(job_id="new1"))
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"new1"})
        assert marked == 0
        assert n_active == 1
        assert reason is None
        assert store.get_job("Acme", "applied1")["status"] == "applied"

    def test_all_terminal_statuses_excluded(self, store):
        for status in ["applied", "interviewing", "rejected", "offer", "skipped", "not_available"]:
            store.upsert_job(make_job(job_id=status))
            store.update_status("Acme", status, status)
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", set())
        assert marked == 0
        assert n_active == 0
        assert reason is None

    def test_returns_correct_marked_count(self, store):
        for jid in ["j1", "j2", "j3", "j4"]:
            store.upsert_job(make_job(job_id=jid))
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"j1", "j2"})
        assert marked == 2
        assert n_active == 4
        assert reason is None

    def test_empty_seen_below_threshold_skips(self, store):
        for jid in ["j1", "j2", "j3", "j4"]:
            store.upsert_job(make_job(job_id=jid))
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", set())
        assert marked == 0
        assert reason is not None

    def test_no_active_jobs_returns_zero_no_skip(self, store):
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", set())
        assert marked == 0
        assert n_active == 0
        assert reason is None

    def test_isolates_by_company(self, store):
        store.upsert_job(make_job(company="Acme", job_id="j1"))
        store.upsert_job(make_job(company="Acme", job_id="j2"))
        store.upsert_job(make_job(company="Google", job_id="g1"))
        marked, _, _ = store.mark_unavailable_jobs("Acme", {"j1"})
        assert marked == 1
        assert store.get_job("Acme", "j2")["status"] == "not_available"
        assert store.get_job("Google", "g1")["status"] == "new"

    def test_seen_at_exactly_threshold_does_not_skip(self, store):
        for i in range(4):
            store.upsert_job(make_job(job_id=str(i)))
        # 2 seen / 4 active = 50%, not less than 50%, so should mark
        marked, n_active, reason = store.mark_unavailable_jobs("Acme", {"0", "1"})
        assert reason is None
        assert marked == 2


class TestUpsertReAvailability:
    def test_not_available_job_reappearing_resets_to_new(self, store):
        store.upsert_job(make_job())
        store.update_status("Acme", "123", "not_available")
        assert store.get_job("Acme", "123")["status"] == "not_available"
        store.upsert_job(make_job())
        assert store.get_job("Acme", "123")["status"] == "new"

    def test_upsert_of_not_available_job_still_returns_false(self, store):
        store.upsert_job(make_job())
        store.update_status("Acme", "123", "not_available")
        is_new = store.upsert_job(make_job())
        assert is_new is False

    def test_non_not_available_status_unchanged_on_upsert(self, store):
        store.upsert_job(make_job())
        store.update_status("Acme", "123", "approved")
        store.upsert_job(make_job())
        assert store.get_job("Acme", "123")["status"] == "approved"

    def test_not_available_reset_updates_date_last_sourced(self, store):
        import time
        store.upsert_job(make_job())
        store.update_status("Acme", "123", "not_available")
        first_sourced = store.get_job("Acme", "123")["date_last_sourced"]
        time.sleep(0.01)
        store.upsert_job(make_job())
        second_sourced = store.get_job("Acme", "123")["date_last_sourced"]
        assert second_sourced >= first_sourced
