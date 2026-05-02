"""Unit tests for the Gmail alerter."""
import pytest
from unittest.mock import patch, MagicMock, call
from pipeline.alerter import GmailAlerter, build_summary_email, _sanitize


SAMPLE_JOBS = [
    {
        "job_id": "1001",
        "company": "Uber",
        "title": "Senior Engineering Manager, Ads",
        "location": "New York, NY",
        "url": "https://boards.greenhouse.io/uber/jobs/1001",
        "apply_url": "https://boards.greenhouse.io/uber/jobs/1001",
        "match_score": 9,
        "match_summary": "Exceptional match. Strong leadership alignment.",
        "status": "new",
        "date_seen": "2026-04-14T10:00:00Z",
    },
    {
        "job_id": "2002",
        "company": "DoorDash",
        "title": "Director of Engineering, Merchant",
        "location": "Remote",
        "url": "https://boards.greenhouse.io/doordash/jobs/2002",
        "apply_url": "https://boards.greenhouse.io/doordash/jobs/2002",
        "match_score": 7,
        "match_summary": "Good match. Some gap in consumer product experience.",
        "status": "new",
        "date_seen": "2026-04-14T10:00:00Z",
    },
]

SAMPLE_STATS = {
    "total_fetched": 2,
    "new_jobs": 2,
    "rescored_jobs": 0,
    "scored_jobs": 2,
    "failed_scoring": 0,
    "threshold": 7,
}


class TestSanitize:
    def test_replaces_non_breaking_space(self):
        assert _sanitize("Remote\xa0- USA") == "Remote - USA"

    def test_replaces_em_dash(self):
        assert _sanitize("matched — review") == "matched - review"

    def test_replaces_en_dash(self):
        assert _sanitize("2020–2024") == "2020-2024"

    def test_replaces_curly_quotes(self):
        assert _sanitize("“quoted”") == '"quoted"'
        assert _sanitize("it’s") == "it's"

    def test_plain_ascii_unchanged(self):
        text = "Engineering Manager, New York, NY"
        assert _sanitize(text) == text


class TestBuildAlertEmail:
    def test_subject_contains_job_count(self):
        subject, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        # Subject format: "[Job Pipeline] DATE — 2 scanned, 2 new, 2 high-match"
        assert "2" in subject

    def test_body_contains_company_names(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        assert "Uber" in body
        assert "DoorDash" in body

    def test_body_contains_job_titles(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        assert "Senior Engineering Manager, Ads" in body
        assert "Director of Engineering, Merchant" in body

    def test_body_contains_match_scores(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        assert "9" in body
        assert "7" in body

    def test_body_contains_apply_cli_commands(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        assert "approve" in body.lower() or "python3" in body.lower()

    def test_body_contains_job_urls(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        assert "boards.greenhouse.io/uber" in body

    def test_jobs_sorted_by_score_descending(self):
        _, body = build_summary_email(SAMPLE_JOBS, SAMPLE_JOBS, SAMPLE_STATS)
        uber_pos = body.find("Uber")
        doordash_pos = body.find("DoorDash")
        assert uber_pos < doordash_pos  # higher score (9) appears first

    def test_empty_jobs_list(self):
        empty_stats = {
            "total_fetched": 0,
            "new_jobs": 0,
            "rescored_jobs": 0,
            "scored_jobs": 0,
            "failed_scoring": 0,
            "threshold": 7,
        }
        subject, body = build_summary_email([], [], empty_stats)
        assert "0" in subject


class TestGmailAlerter:
    @pytest.fixture
    def alerter(self):
        return GmailAlerter(recipient_email="test@example.com")

    def test_send_calls_smtp(self, alerter):
        with patch("pipeline.alerter.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            alerter.send_alert(
                SAMPLE_JOBS,
                smtp_user="user@gmail.com",
                smtp_password="pass",
                all_scored=SAMPLE_JOBS,
                stats=SAMPLE_STATS,
            )
        mock_smtp.send_message.assert_called_once()

    def test_always_sends_even_with_empty_jobs(self, alerter):
        """send_alert now always sends, even when alert_jobs is empty."""
        empty_stats = {
            "total_fetched": 0,
            "new_jobs": 0,
            "rescored_jobs": 0,
            "scored_jobs": 0,
            "failed_scoring": 0,
            "threshold": 7,
        }
        with patch("pipeline.alerter.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            alerter.send_alert(
                [],
                smtp_user="user@gmail.com",
                smtp_password="pass",
                all_scored=[],
                stats=empty_stats,
            )
        mock_smtp_cls.assert_called_once()

    def test_recipient_in_message(self, alerter):
        with patch("pipeline.alerter.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            alerter.send_alert(
                SAMPLE_JOBS,
                smtp_user="user@gmail.com",
                smtp_password="pass",
                all_scored=SAMPLE_JOBS,
                stats=SAMPLE_STATS,
            )
        msg = mock_smtp.send_message.call_args[0][0]
        assert msg["To"] == "test@example.com"

    def test_non_ascii_content_does_not_raise(self, alerter):
        """Job data with non-ASCII chars (e.g. \xa0 from Gemini) must not crash the mailer."""
        jobs_with_unicode = [{
            **SAMPLE_JOBS[0],
            "location": "Remote\xa0- USA",      # non-breaking space from scraped content
            "match_summary": "Exceptional\xa0match.",
        }]
        with patch("pipeline.alerter.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            # Should not raise — previously crashed with ascii codec error
            alerter.send_alert(
                jobs_with_unicode,
                smtp_user="user@gmail.com",
                smtp_password="pass",
                all_scored=jobs_with_unicode,
                stats=SAMPLE_STATS,
            )
        mock_smtp.send_message.assert_called_once()
