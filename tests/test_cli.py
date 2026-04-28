"""Unit tests for the CLI commands."""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from cli import cli, approve, skip, list_jobs, show_diff


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_job.return_value = {
        "job_id": "1001",
        "company": "Uber",
        "title": "Senior Engineering Manager",
        "location": "New York, NY",
        "url": "https://example.com/1001",
        "apply_url": "https://example.com/apply/1001",
        "match_score": 8,
        "match_summary": "Strong match",
        "status": "alerted",
        "date_seen": "2026-04-14T10:00:00Z",
    }
    store.list_all_jobs.return_value = [
        {
            "job_id": "1001",
            "company": "Uber",
            "title": "Senior Engineering Manager",
            "location": "New York, NY",
            "url": "https://example.com/1001",
            "match_score": 8,
            "match_summary": "Strong match",
            "status": "alerted",
            "date_seen": "2026-04-14T10:00:00Z",
        }
    ]
    return store


class TestListCommand:
    def test_list_shows_jobs(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(list_jobs, [])
        assert result.exit_code == 0
        assert "Uber" in result.output

    def test_list_shows_match_score(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(list_jobs, [])
        assert "8" in result.output

    def test_list_shows_status(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(list_jobs, [])
        assert "alerted" in result.output

    def test_list_filter_by_status(self, runner, mock_store):
        mock_store.get_jobs_by_status.return_value = mock_store.list_all_jobs.return_value
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(list_jobs, ["--status", "alerted"])
        assert result.exit_code == 0


class TestApproveCommand:
    def test_approve_updates_status(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(approve, ["--company", "Uber", "--job-id", "1001"])
        assert result.exit_code == 0
        mock_store.update_status.assert_called_once_with("Uber", "1001", "approved")

    def test_approve_prints_confirmation(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(approve, ["--company", "Uber", "--job-id", "1001"])
        assert "approved" in result.output.lower() or "Uber" in result.output

    def test_approve_job_not_found_shows_error(self, runner, mock_store):
        mock_store.get_job.return_value = None
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(approve, ["--company", "NoCompany", "--job-id", "999"])
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_approve_requires_company_and_job_id(self, runner):
        result = runner.invoke(approve, [])
        assert result.exit_code != 0


class TestSkipCommand:
    def test_skip_updates_status(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(skip, ["--company", "Uber", "--job-id", "1001"])
        assert result.exit_code == 0
        mock_store.update_status.assert_called_once_with("Uber", "1001", "skipped")

    def test_skip_prints_confirmation(self, runner, mock_store):
        with patch("cli.JobStore", return_value=mock_store):
            result = runner.invoke(skip, ["--company", "Uber", "--job-id", "1001"])
        assert "skipped" in result.output.lower() or "Uber" in result.output


class TestShowDiffCommand:
    def test_diff_displays_patch(self, runner, tmp_path):
        patch_file = tmp_path / "Uber_1001" / "resume_diff.patch"
        patch_file.parent.mkdir(parents=True)
        patch_file.write_text("--- a\n+++ b\n@@ -1 +1 @@\n-old line\n+new line\n")
        with patch("cli.OUTPUT_DIR", str(tmp_path)):
            result = runner.invoke(show_diff, ["--company", "Uber", "--job-id", "1001"])
        assert result.exit_code == 0
        assert "old line" in result.output or "new line" in result.output

    def test_diff_not_found_shows_error(self, runner, tmp_path):
        with patch("cli.OUTPUT_DIR", str(tmp_path)):
            result = runner.invoke(show_diff, ["--company", "NoCompany", "--job-id", "999"])
        assert "not found" in result.output.lower() or result.exit_code != 0
