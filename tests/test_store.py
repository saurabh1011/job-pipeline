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
