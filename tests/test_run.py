"""Integration tests for the run.py pipeline orchestrator."""
import pytest
from unittest.mock import patch, MagicMock, call
from run import run_pipeline


SAMPLE_CONFIG = {
    "companies": [
        {"name": "Uber", "ats": "greenhouse", "board_slug": "uber"},
    ],
    "preferences": {
        "match_threshold": 7,
        "preferred_locations": ["New York", "Remote"],
        "acceptable_locations": ["San Francisco"],
        "location_penalties": {"preferred": 0, "acceptable": 1, "other": 3},
        "title_keywords": ["Engineering Manager", "Director of Engineering"],
        "title_exclude_keywords": ["Software Engineer"],
        "google_docs_links": [],
    },
}

SAMPLE_JOBS = [
    {
        "job_id": "1001",
        "company": "Uber",
        "title": "Senior Engineering Manager",
        "location": "New York, NY",
        "url": "https://example.com/1001",
        "apply_url": "https://example.com/1001",
        "description": "Lead a team...",
    }
]


@pytest.fixture
def mock_components():
    """Patch all external-facing components for integration testing."""
    mock_store = MagicMock()
    mock_store.upsert_job.return_value = True  # all jobs are "new"
    mock_store.set_match_score = MagicMock()
    mock_store.update_status = MagicMock()
    mock_store.get_jobs_by_status.return_value = []  # no previously alerted jobs
    mock_store.__enter__ = MagicMock(return_value=mock_store)
    mock_store.__exit__ = MagicMock(return_value=False)

    mock_match_result = MagicMock()
    mock_match_result.adjusted_score = 8
    mock_match_result.score = 8
    mock_match_result.summary = "Strong match"
    mock_match_result.meets_threshold.return_value = True

    return {
        "store": mock_store,
        "match_result": mock_match_result,
    }


class TestRunPipeline:
    def test_fetches_jobs_from_all_companies(self, mock_components):
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS) as mock_fetch, \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_engine_cls.return_value.score.return_value = mock_components["match_result"]
            mock_gen_cls.return_value.generate.return_value = MagicMock()
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_fetch.assert_called_once()

    def test_new_jobs_are_stored(self, mock_components):
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS), \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_engine_cls.return_value.score.return_value = mock_components["match_result"]
            mock_gen_cls.return_value.generate.return_value = MagicMock()
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_components["store"].upsert_job.assert_called()

    def test_duplicate_jobs_not_scored(self, mock_components):
        """Jobs already in store (upsert returns False) should not be scored."""
        mock_components["store"].upsert_job.return_value = False  # all duplicates
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS), \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_engine_cls.return_value.score.assert_not_called()

    def test_high_match_jobs_generate_materials(self, mock_components):
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS), \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_engine_cls.return_value.score.return_value = mock_components["match_result"]
            mock_gen_cls.return_value.generate.return_value = MagicMock()
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_gen_cls.return_value.generate.assert_called_once()

    def test_below_threshold_jobs_skip_generation(self, mock_components):
        """Jobs scoring below threshold should not have materials generated."""
        low_result = MagicMock()
        low_result.adjusted_score = 5
        low_result.score = 5
        low_result.summary = "Weak match"
        low_result.meets_threshold.return_value = False
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS), \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_engine_cls.return_value.score.return_value = low_result
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_gen_cls.return_value.generate.assert_not_called()

    def test_alert_sent_for_high_match_new_jobs(self, mock_components):
        with patch("run.fetch_all_companies", return_value=SAMPLE_JOBS), \
             patch("run.JobStore", return_value=mock_components["store"]), \
             patch("run.MatchEngine") as mock_engine_cls, \
             patch("run.ContentGenerator") as mock_gen_cls, \
             patch("run.GmailAlerter") as mock_alerter_cls, \
             patch("run.ProfileLoader") as mock_loader_cls, \
             patch("run.create_provider"), \
             patch("run.run_ingestion_for_pipeline", return_value={"processed": 0, "skipped": 0, "errors": 0, "copied": 0, "index_entries": 0}):
            mock_engine_cls.return_value.score.return_value = mock_components["match_result"]
            mock_gen_cls.return_value.generate.return_value = MagicMock()
            mock_loader_cls.return_value.load.return_value = {"resume": "test", "experience": "", "google_docs": ""}
            run_pipeline(SAMPLE_CONFIG, smtp_user="u", smtp_password="p", recipient="r@r.com")
        mock_alerter_cls.return_value.send_alert.assert_called_once()
