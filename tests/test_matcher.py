"""Unit tests for the match engine and profile loader."""
import pytest
from unittest.mock import patch, MagicMock
from pipeline.matcher import MatchEngine, MatchResult
from pipeline.profile import ProfileLoader


# ── ProfileLoader ─────────────────────────────────────────────────────────────

class TestProfileLoader:
    def test_loads_resume_file(self, tmp_path):
        resume = tmp_path / "resume.md"
        resume.write_text("# John Doe\nEngineering Manager at Acme")
        exp = tmp_path / "experience.md"
        exp.write_text("Led teams of 15+")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        profile = loader.load()
        assert "John Doe" in profile["resume"]
        assert "Led teams of 15+" in profile["experience"]

    def test_missing_files_return_empty_strings(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path))
        profile = loader.load()
        assert profile["resume"] == ""
        assert profile["experience"] == ""

    def test_google_docs_content_appended(self, tmp_path):
        exp = tmp_path / "experience.md"
        exp.write_text("Base experience")
        loader = ProfileLoader(
            profile_dir=str(tmp_path),
            google_docs_links=[
                {"url": "https://docs.google.com/document/d/FAKE_ID/edit",
                 "description": "Leadership philosophy"}
            ]
        )
        with patch("pipeline.profile.requests.get") as mock_get:
            mock_get.return_value.text = "My leadership philosophy is..."
            mock_get.return_value.raise_for_status = MagicMock()
            profile = loader.load()
        assert "My leadership philosophy is..." in profile["google_docs"]

    def test_google_docs_fetch_failure_is_ignored(self, tmp_path):
        loader = ProfileLoader(
            profile_dir=str(tmp_path),
            google_docs_links=[
                {"url": "https://docs.google.com/document/d/FAKE_ID/edit",
                 "description": "Test doc"}
            ]
        )
        with patch("pipeline.profile.requests.get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception("403 Forbidden")
            profile = loader.load()
        # Should not raise; google_docs may be empty or have error note
        assert "google_docs" in profile

    def test_full_profile_text_combines_all_sources(self, tmp_path):
        (tmp_path / "resume.md").write_text("Resume text")
        (tmp_path / "experience.md").write_text("Experience text")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        profile = loader.load()
        full = loader.full_text(profile)
        assert "Resume text" in full
        assert "Experience text" in full


# ── MatchEngine ───────────────────────────────────────────────────────────────

MOCK_CLAUDE_RESPONSE = """{
  "score": 8,
  "summary": "Strong alignment on team leadership and distributed systems.",
  "strengths": ["Led teams of 20+", "Experience with infrastructure at scale"],
  "gaps": ["No direct ad-tech experience"],
  "location_note": "Preferred location (New York)"
}"""

SAMPLE_PROFILE = {
    "resume": "John Doe, Engineering Manager. Led teams of 20+ engineers.",
    "experience": "Strong background in distributed systems and platform engineering.",
    "google_docs": "",
}

SAMPLE_JOB = {
    "job_id": "1001",
    "company": "Uber",
    "title": "Senior Engineering Manager, Ads",
    "location": "New York, NY",
    "url": "https://boards.greenhouse.io/uber/jobs/1001",
    "apply_url": "https://boards.greenhouse.io/uber/jobs/1001",
    "description": "Lead a 15-person ads infrastructure team...",
}

PREFERENCES = {
    "preferred_locations": ["New York", "New York, NY", "Remote"],
    "acceptable_locations": ["San Francisco", "Seattle"],
    "location_penalties": {"preferred": 0, "acceptable": 1, "other": 3},
    "match_threshold": 7,
}


@pytest.fixture
def engine(mock_provider):
    return MatchEngine(provider=mock_provider)


class TestMatchEngine:
    def test_returns_match_result(self, engine):
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert isinstance(result, MatchResult)

    def test_score_extracted_correctly(self, engine):
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert result.score == 8

    def test_summary_extracted_correctly(self, engine):
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert "Strong alignment" in result.summary

    def test_strengths_and_gaps_extracted(self, engine):
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert len(result.strengths) > 0
        assert len(result.gaps) > 0

    def test_location_penalty_preferred(self, engine):
        """NY job (preferred) should have 0 penalty."""
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert result.location_penalty == 0
        assert result.adjusted_score == result.score

    def test_location_penalty_acceptable(self, engine):
        """SF job (acceptable) should have penalty of 1."""
        sf_job = {**SAMPLE_JOB, "location": "San Francisco, CA"}
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(sf_job, SAMPLE_PROFILE, PREFERENCES)
        assert result.location_penalty == 1
        assert result.adjusted_score == result.score - 1

    def test_location_penalty_other(self, engine):
        """Unknown location job should have penalty of 3."""
        other_job = {**SAMPLE_JOB, "location": "Denver, CO"}
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(other_job, SAMPLE_PROFILE, PREFERENCES)
        assert result.location_penalty == 3
        assert result.adjusted_score == result.score - 3

    def test_remote_job_has_no_penalty(self, engine):
        """Remote jobs are treated as preferred."""
        remote_job = {**SAMPLE_JOB, "location": "Remote"}
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(remote_job, SAMPLE_PROFILE, PREFERENCES)
        assert result.location_penalty == 0

    def test_meets_threshold_true(self, engine):
        with patch.object(engine, "_call_claude", return_value=MOCK_CLAUDE_RESPONSE):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert result.meets_threshold(threshold=7) is True

    def test_meets_threshold_false(self, engine):
        low_score_response = MOCK_CLAUDE_RESPONSE.replace('"score": 8', '"score": 5')
        with patch.object(engine, "_call_claude", return_value=low_score_response):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert result.meets_threshold(threshold=7) is False

    def test_malformed_claude_response_returns_low_score(self, engine):
        """If Claude returns non-JSON, gracefully return a low score."""
        with patch.object(engine, "_call_claude", return_value="I cannot assess this."):
            result = engine.score(SAMPLE_JOB, SAMPLE_PROFILE, PREFERENCES)
        assert result.score <= 5
        assert result.summary != ""
