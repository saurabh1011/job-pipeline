"""Unit tests for pipeline/profile.py — ProfileLoader."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

from pipeline.profile import ProfileLoader, _extract_doc_id


# ── _extract_doc_id ────────────────────────────────────────────────────────────

class TestExtractDocId:
    def test_extracts_id_from_standard_url(self):
        url = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
        assert _extract_doc_id(url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"

    def test_extracts_id_from_short_url(self):
        url = "https://docs.google.com/document/d/abc123XYZ/view"
        assert _extract_doc_id(url) == "abc123XYZ"

    def test_returns_none_for_non_gdoc_url(self):
        assert _extract_doc_id("https://example.com/foo") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_doc_id("") is None


# ── ProfileLoader._read_file ──────────────────────────────────────────────────

class TestReadFile:
    def test_reads_existing_file(self, tmp_path):
        (tmp_path / "resume.md").write_text("# My Resume")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        assert loader._read_file("resume.md") == "# My Resume"

    def test_returns_empty_for_missing_file(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path))
        assert loader._read_file("missing.md") == ""

    def test_strips_leading_trailing_whitespace(self, tmp_path):
        (tmp_path / "exp.md").write_text("  content  \n")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        assert loader._read_file("exp.md") == "content"


# ── ProfileLoader.full_text ───────────────────────────────────────────────────

class TestFullText:
    def _loader(self, tmp_path):
        return ProfileLoader(profile_dir=str(tmp_path))

    def test_resume_section_included(self, tmp_path):
        profile = {"resume": "My Resume", "experience": "", "google_docs": "", "stories": ""}
        result = self._loader(tmp_path).full_text(profile)
        assert "=== RESUME ===" in result
        assert "My Resume" in result

    def test_experience_section_included(self, tmp_path):
        profile = {"resume": "", "experience": "10 years", "google_docs": "", "stories": ""}
        result = self._loader(tmp_path).full_text(profile)
        assert "=== ADDITIONAL EXPERIENCE" in result
        assert "10 years" in result

    def test_google_docs_section_included(self, tmp_path):
        profile = {"resume": "", "experience": "", "google_docs": "Doc content", "stories": ""}
        result = self._loader(tmp_path).full_text(profile)
        assert "=== SUPPLEMENTARY DOCUMENTS ===" in result
        assert "Doc content" in result

    def test_stories_section_included(self, tmp_path):
        profile = {"resume": "", "experience": "", "google_docs": "", "stories": "Story A"}
        result = self._loader(tmp_path).full_text(profile)
        assert "=== RELEVANT CAREER STORIES ===" in result
        assert "Story A" in result

    def test_empty_fields_omitted(self, tmp_path):
        profile = {"resume": "Resume here", "experience": "", "google_docs": "", "stories": ""}
        result = self._loader(tmp_path).full_text(profile)
        assert "ADDITIONAL EXPERIENCE" not in result
        assert "SUPPLEMENTARY" not in result

    def test_all_sections_present(self, tmp_path):
        profile = {
            "resume": "R", "experience": "E", "google_docs": "G", "stories": "S"
        }
        result = self._loader(tmp_path).full_text(profile)
        assert "RESUME" in result
        assert "ADDITIONAL EXPERIENCE" in result
        assert "SUPPLEMENTARY" in result
        assert "CAREER STORIES" in result


# ── ProfileLoader.load ────────────────────────────────────────────────────────

class TestLoad:
    def test_load_returns_dict_with_required_keys(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.load()
        assert set(result.keys()) >= {"resume", "experience", "google_docs", "stories"}

    def test_load_reads_resume_md(self, tmp_path):
        (tmp_path / "resume.md").write_text("# Jane Doe")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.load()
        assert result["resume"] == "# Jane Doe"

    def test_load_reads_experience_md(self, tmp_path):
        (tmp_path / "experience.md").write_text("10 years at Acme")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.load()
        assert result["experience"] == "10 years at Acme"

    def test_load_empty_profile_dir(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.load()
        assert result["resume"] == ""
        assert result["experience"] == ""

    def test_load_no_stories_without_job(self, tmp_path):
        stories_dir = tmp_path / "stories"
        stories_dir.mkdir()
        (stories_dir / "story1.md").write_text("A story")
        (tmp_path / "stories_index.md").write_text("index")
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.load()  # no job passed
        assert result["stories"] == ""

    def test_load_no_google_docs_when_none_configured(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path), google_docs_links=[])
        result = loader.load()
        assert result["google_docs"] == ""

    def test_load_fetches_google_docs(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.text = "Document content"
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.profile.requests.get", return_value=mock_resp):
            loader = ProfileLoader(
                profile_dir=str(tmp_path),
                google_docs_links=[{
                    "url": "https://docs.google.com/document/d/abc123/edit",
                    "description": "My Doc",
                }],
            )
            result = loader.load()
        assert "Document content" in result["google_docs"]

    def test_load_google_doc_failure_returns_empty(self, tmp_path):
        with patch("pipeline.profile.requests.get", side_effect=Exception("timeout")):
            loader = ProfileLoader(
                profile_dir=str(tmp_path),
                google_docs_links=[{
                    "url": "https://docs.google.com/document/d/abc/edit",
                    "description": "Failing Doc",
                }],
            )
            result = loader.load()
        assert result["google_docs"] == ""


# ── ProfileLoader.select_stories ──────────────────────────────────────────────

class TestSelectStories:
    def _setup_stories(self, tmp_path):
        stories_dir = tmp_path / "stories"
        stories_dir.mkdir()
        (stories_dir / "reorg.md").write_text("I led a reorg...")
        (stories_dir / "launch.md").write_text("I launched a product...")
        (tmp_path / "stories_index.md").write_text(
            "## reorg.md\nKeywords: reorg, team\nSummary: Led an org reorg.\n\n"
            "## launch.md\nKeywords: launch, product\nSummary: Launched ads platform.\n"
        )
        return tmp_path

    def test_returns_empty_without_index(self, tmp_path):
        loader = ProfileLoader(profile_dir=str(tmp_path))
        result = loader.select_stories({"company": "X", "title": "EM", "description": "d"})
        assert result == ""

    def test_falls_back_to_all_stories_without_provider(self, tmp_path):
        self._setup_stories(tmp_path)
        loader = ProfileLoader(profile_dir=str(tmp_path), provider=None)
        result = loader.select_stories({"company": "X", "title": "EM", "description": "d"})
        assert "reorg" in result.lower() or "launch" in result.lower()

    def test_uses_provider_for_story_selection(self, tmp_path):
        self._setup_stories(tmp_path)
        mock_provider = MagicMock()
        mock_provider.complete_fast.return_value = '["reorg.md"]'
        loader = ProfileLoader(profile_dir=str(tmp_path), provider=mock_provider)
        result = loader.select_stories({"company": "X", "title": "EM", "description": "d"})
        assert "I led a reorg" in result
        assert "I launched" not in result

    def test_falls_back_when_provider_returns_bad_json(self, tmp_path):
        self._setup_stories(tmp_path)
        mock_provider = MagicMock()
        mock_provider.complete_fast.return_value = "not json"
        loader = ProfileLoader(profile_dir=str(tmp_path), provider=mock_provider)
        result = loader.select_stories({"company": "X", "title": "EM", "description": "d"})
        # Should fall back to all stories
        assert result != ""

    def test_missing_selected_file_skipped_gracefully(self, tmp_path):
        self._setup_stories(tmp_path)
        mock_provider = MagicMock()
        mock_provider.complete_fast.return_value = '["nonexistent.md"]'
        loader = ProfileLoader(profile_dir=str(tmp_path), provider=mock_provider)
        # Should not raise, returns empty or fallback
        result = loader.select_stories({"company": "X", "title": "EM", "description": "d"})
        assert isinstance(result, str)
