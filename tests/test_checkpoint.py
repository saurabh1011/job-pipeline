"""Unit tests for pipeline/checkpoint.py."""
import json
import os
import pytest
from unittest.mock import patch
from pipeline.checkpoint import RunCheckpoint


@pytest.fixture
def tmp_path_str(tmp_path):
    return str(tmp_path / "checkpoint.json")


class TestRunCheckpointFreshStart:
    def test_is_not_resumable_when_no_file(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        assert not cp.is_resumable

    def test_alert_sent_false_initially(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        assert not cp.alert_sent

    def test_get_fetched_jobs_none_initially(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        assert cp.get_fetched_jobs() is None

    def test_get_job_result_none_for_unknown_job(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        assert cp.get_job_result("Uber", "1001") is None


class TestRunCheckpointPersistence:
    def test_set_fetched_jobs_writes_file(self, tmp_path_str):
        jobs = [{"job_id": "1", "company": "Uber", "title": "EM"}]
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs(jobs)
        assert os.path.exists(tmp_path_str)

    def test_set_fetched_jobs_round_trips(self, tmp_path_str):
        jobs = [{"job_id": "1", "company": "Uber", "title": "EM"}]
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs(jobs)

        cp2 = RunCheckpoint(tmp_path_str)
        assert cp2.get_fetched_jobs() == jobs

    def test_set_job_scored_round_trips(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_job_scored("Uber", "1001", is_new=True, adjusted_score=9,
                          summary="Great match", meets_threshold=True)

        cp2 = RunCheckpoint(tmp_path_str)
        result = cp2.get_job_result("Uber", "1001")
        assert result["scored"] is True
        assert result["adjusted_score"] == 9
        assert result["summary"] == "Great match"
        assert result["meets_threshold"] is True
        assert result["generated"] is False

    def test_set_job_generated_updates_flag(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_job_scored("Uber", "1001", is_new=True, adjusted_score=9,
                          summary="Great match", meets_threshold=True)
        cp.set_job_generated("Uber", "1001")

        cp2 = RunCheckpoint(tmp_path_str)
        assert cp2.get_job_result("Uber", "1001")["generated"] is True

    def test_set_job_skipped_stores_result(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_job_skipped("Uber", "1001", is_new=False)

        cp2 = RunCheckpoint(tmp_path_str)
        result = cp2.get_job_result("Uber", "1001")
        assert result["is_new"] is False
        assert result["meets_threshold"] is False

    def test_mark_alert_sent_persists(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([])
        cp.mark_alert_sent()

        cp2 = RunCheckpoint(tmp_path_str)
        assert cp2.alert_sent is True


class TestRunCheckpointResumable:
    def test_is_resumable_after_fetch_but_before_alert(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([{"job_id": "1", "company": "Uber", "title": "EM"}])
        assert cp.is_resumable

    def test_not_resumable_after_alert_sent(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([])
        cp.mark_alert_sent()
        assert not cp.is_resumable

    def test_not_resumable_when_fetched_jobs_is_none(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        # No set_fetched_jobs called — fresh state
        assert not cp.is_resumable


class TestRunCheckpointAtomicWrite:
    def test_tmp_file_cleaned_up_after_save(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([])
        assert not os.path.exists(tmp_path_str + ".tmp")

    def test_file_is_valid_json_after_save(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([{"job_id": "1", "company": "Uber", "title": "EM"}])
        with open(tmp_path_str) as f:
            data = json.load(f)
        assert "fetched_jobs" in data
        assert "job_results" in data

    def test_corrupted_file_starts_fresh(self, tmp_path_str):
        with open(tmp_path_str, "w") as f:
            f.write("not valid json {{{{")
        cp = RunCheckpoint(tmp_path_str)
        assert cp.get_fetched_jobs() is None
        assert not cp.is_resumable


class TestRunCheckpointDelete:
    def test_delete_removes_file(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.set_fetched_jobs([])
        cp.delete()
        assert not os.path.exists(tmp_path_str)

    def test_delete_is_idempotent_when_no_file(self, tmp_path_str):
        cp = RunCheckpoint(tmp_path_str)
        cp.delete()  # should not raise
