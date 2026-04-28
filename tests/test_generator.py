"""Unit tests for the content generator."""
import os
import pytest
from unittest.mock import patch, MagicMock
from pipeline.generator import ContentGenerator, GeneratedContent


SAMPLE_PROFILE = {
    "resume": "# John Doe\nEngineering Manager at Acme Corp\n\n## Experience\n- Led teams of 20+ engineers\n- Built platform serving 100M users",
    "experience": "Strong distributed systems background. Prefer NY-based roles.",
    "google_docs": "",
}

SAMPLE_JOB = {
    "job_id": "1001",
    "company": "Uber",
    "title": "Senior Engineering Manager, Ads",
    "location": "New York, NY",
    "url": "https://boards.greenhouse.io/uber/jobs/1001",
    "apply_url": "https://boards.greenhouse.io/uber/jobs/1001",
    "description": "Lead a 15-person ads infrastructure team. Requirements: 5+ years managing managers...",
}

MOCK_COVER_LETTER = """Dear Hiring Team,

I am excited to apply for the Senior Engineering Manager, Ads role at Uber...

[Cover letter body]

Sincerely,
John Doe"""

MOCK_TAILORED_RESUME = """# John Doe
Engineering Manager | New York, NY

## Summary
Experienced Engineering Manager with focus on ads infrastructure and platform...

## Experience
- **Acme Corp** — Engineering Manager, Platform (2020-present)
  - Led teams of 20+ engineers building platform serving 100M users
  - Drove ads infrastructure initiative reducing latency by 40%

## Education
..."""


@pytest.fixture
def generator(tmp_path, mock_provider):
    return ContentGenerator(provider=mock_provider, output_dir=str(tmp_path))


class TestContentGenerator:
    def test_returns_generated_content_object(self, generator):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        assert isinstance(result, GeneratedContent)

    def test_cover_letter_contains_company_name(self, generator):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        assert "Uber" in result.cover_letter

    def test_tailored_resume_returned(self, generator):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        assert len(result.tailored_resume) > 0

    def test_diff_is_generated(self, generator):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        assert result.resume_diff is not None
        # Diff should be a non-empty string since resumes differ
        assert len(result.resume_diff) > 0

    def test_diff_shows_additions_and_removals(self, generator):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        # Unified diff format uses + for additions, - for removals
        assert "+" in result.resume_diff or "-" in result.resume_diff

    def test_files_saved_to_output_dir(self, generator, tmp_path):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        job_dir = os.path.join(str(tmp_path), "Uber_1001")
        assert os.path.exists(os.path.join(job_dir, "cover_letter.md"))
        assert os.path.exists(os.path.join(job_dir, "resume_tailored.md"))
        assert os.path.exists(os.path.join(job_dir, "resume_diff.patch"))

    def test_output_dir_named_company_jobid(self, generator, tmp_path):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        assert result.output_dir == os.path.join(str(tmp_path), "Uber_1001")

    def test_cover_letter_file_contents_match(self, generator, tmp_path):
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
            result = generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        cover_path = os.path.join(str(tmp_path), "Uber_1001", "cover_letter.md")
        with open(cover_path) as f:
            content = f.read()
        assert result.cover_letter in content

    def test_identical_resume_produces_empty_diff(self, generator):
        """If tailored resume is identical to base, diff should be empty."""
        same_resume = SAMPLE_PROFILE["resume"]
        profile_same = {**SAMPLE_PROFILE}
        with patch.object(generator, "_call_claude") as mock_claude:
            mock_claude.side_effect = [MOCK_COVER_LETTER, same_resume]
            result = generator.generate(SAMPLE_JOB, profile_same)
        assert result.resume_diff == ""

    def test_existing_output_overwritten(self, generator, tmp_path):
        """Second generate call for same job should overwrite files."""
        for _ in range(2):
            with patch.object(generator, "_call_claude") as mock_claude:
                mock_claude.side_effect = [MOCK_COVER_LETTER, MOCK_TAILORED_RESUME]
                generator.generate(SAMPLE_JOB, SAMPLE_PROFILE)
        # Should not raise; files just get overwritten
        assert os.path.exists(os.path.join(str(tmp_path), "Uber_1001", "cover_letter.md"))
