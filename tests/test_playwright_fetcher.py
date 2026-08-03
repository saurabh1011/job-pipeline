"""Unit tests for Playwright-based job fetchers.

Parsing logic is tested with mock page objects. Browser-level integration
(navigation, wait_for_timeout) is not tested here — those are covered by
manually running `python3 run.py` against real sites.
"""
import pytest
from unittest.mock import MagicMock, patch, call

import time

from pipeline.playwright_fetcher import (
    MetaPlaywrightFetcher,
    MicrosoftPlaywrightFetcher,
    _PLAYWRIGHT_FETCHER_MAP,
    _get_description_safe,
)
import pipeline.playwright_fetcher as pf_module

PREFERENCES = {
    "title_keywords": ["Engineering Manager", "Director of Engineering"],
    "title_exclude_keywords": ["Software Engineer", "Product Manager"],
    "excluded_location_keywords": [],
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_page(goto=None, evaluate=None, query_selector=None, query_selector_all=None):
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_timeout.return_value = None
    page.request = MagicMock()
    if evaluate is not None:
        page.evaluate.return_value = evaluate
    if query_selector is not None:
        page.query_selector.side_effect = query_selector
    if query_selector_all is not None:
        page.query_selector_all.side_effect = query_selector_all
    return page


# ── MetaPlaywrightFetcher ──────────────────────────────────────────────────────

class TestMetaPlaywrightFetcher:
    def _make_link(self, job_id, title, location):
        link = MagicMock()
        link.get_attribute.return_value = f"/profile/job_details/{job_id}"
        parent_text = f"{title}\n{location}\n⋅\nEngineering\n⋅\nSoftware Engineering"
        link.evaluate.return_value = {"title": title, "text": parent_text}
        link.inner_text.return_value = f"{title}\n{location}\n⋅\nEngineering"
        return link

    def test_filters_by_title(self):
        link1 = self._make_link("111", "Engineering Manager, Feed", "Menlo Park, CA")
        link2 = self._make_link("222", "Software Engineer, Infra", "Remote")
        page = _mock_page()
        page.query_selector_all.return_value = [link1, link2]
        fetcher = MetaPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        titles = [j["title"] for j in jobs]
        assert "Engineering Manager, Feed" in titles
        assert "Software Engineer, Infra" not in titles

    def test_extracts_job_id_from_href(self):
        link = self._make_link("999", "Engineering Manager, AI", "New York, NY")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = MetaPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        assert jobs[0]["job_id"] == "999"

    def test_returns_normalized_job_dict(self):
        link = self._make_link("777", "Director of Engineering, Platform", "Remote")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = MetaPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="desc"):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Meta"

    def test_deduplicates_across_keywords(self):
        link = self._make_link("555", "Engineering Manager, Core", "Seattle")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = MetaPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher.fetch(PREFERENCES, page)
        assert len([j for j in jobs if j["job_id"] == "555"]) == 1


# ── MicrosoftPlaywrightFetcher ─────────────────────────────────────────────────

class TestMicrosoftPlaywrightFetcher:
    def _make_link(self, job_id, title, location):
        link = MagicMock()
        link.get_attribute.return_value = f"/careers/job/{job_id}"
        link.inner_text.return_value = f"{title}\n{location}\nPosted 2 hours ago"
        return link

    def test_filters_by_title(self):
        link1 = self._make_link("100", "Senior Engineering Manager", "New York, NY, USA")
        link2 = self._make_link("200", "Senior Software Engineer", "Redmond, WA, USA")
        page = _mock_page()
        page.query_selector_all.return_value = [link1, link2]
        fetcher = MicrosoftPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Senior Engineering Manager"

    def test_extracts_job_id_from_href(self):
        link = self._make_link("12345", "Engineering Manager, Azure", "Redmond, WA")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = MicrosoftPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        assert jobs[0]["job_id"] == "12345"

    def test_returns_normalized_job_dict(self):
        link = self._make_link("99999", "Director of Engineering, M365", "Remote")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = MicrosoftPlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="jd"):
            jobs = fetcher._extract_jobs(page, PREFERENCES)
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Microsoft"

    def test_returns_empty_on_page_load_failure(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = MicrosoftPlaywrightFetcher()
        jobs = fetcher.fetch(PREFERENCES, page)
        assert jobs == []


# ── _get_description_safe ─────────────────────────────────────────────────────

class TestGetDescriptionSafe:
    def test_returns_description_on_success(self):
        page = _mock_page()
        result = _get_description_safe(lambda p, u: "job description", page, "https://example.com")
        assert result == "job description"

    def test_returns_empty_on_normal_empty(self):
        page = _mock_page()
        result = _get_description_safe(lambda p, u: "", page, "https://example.com")
        assert result == ""

    def test_times_out_and_closes_page_on_hang(self, monkeypatch):
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 0.2)

        page = _mock_page()
        closed = []
        page.close.side_effect = lambda: closed.append(True)

        def hanging_fetch(p, url):
            time.sleep(5)
            return "never reached"

        result = _get_description_safe(hanging_fetch, page, "https://example.com")
        assert result == ""
        assert closed == [True]

    def test_does_not_close_page_on_normal_completion(self, monkeypatch):
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 2.0)
        page = _mock_page()
        _get_description_safe(lambda p, u: "desc", page, "https://example.com")
        page.close.assert_not_called()

    def test_timeout_completes_within_reasonable_time(self, monkeypatch):
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 0.2)
        page = _mock_page()

        def hanging_fetch(p, url):
            time.sleep(5)
            return "never"

        t0 = time.time()
        _get_description_safe(hanging_fetch, page, "https://example.com")
        elapsed = time.time() - t0
        assert elapsed < 1.0

    def test_appends_to_timed_out_urls_on_timeout(self, monkeypatch):
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 0.2)
        page = _mock_page()
        timed_out = []
        _get_description_safe(
            lambda p, u: time.sleep(5) or "",
            page, "https://example.com/job/123",
            timed_out_urls=timed_out,
        )
        assert timed_out == ["https://example.com/job/123"]

    def test_does_not_append_on_success(self):
        page = _mock_page()
        timed_out = []
        _get_description_safe(lambda p, u: "desc", page, "https://example.com", timed_out_urls=timed_out)
        assert timed_out == []

    def test_calls_log_on_timeout(self, monkeypatch):
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 0.2)
        page = _mock_page()
        logged = []
        _get_description_safe(
            lambda p, u: time.sleep(5) or "",
            page, "https://example.com/job/456",
            log=logged.append,
        )
        assert any("SKIPPED" in m and "timed out" in m for m in logged)

    def test_does_not_call_log_on_success(self):
        page = _mock_page()
        logged = []
        _get_description_safe(lambda p, u: "desc", page, "https://example.com", log=logged.append)
        assert logged == []


# ── Integration: log + timed_out_urls threading through fetchers ──────────────

class TestDescriptionTimeoutIntegration:
    """Verify log and timed_out_urls flow end-to-end through each fetcher."""

    def test_meta_extract_jobs_propagates_timed_out_urls(self, monkeypatch):
        """Meta _extract_jobs threads timed_out_urls through to _get_description_safe."""
        monkeypatch.setattr(pf_module, "_DESCRIPTION_TIMEOUT_S", 0.1)

        link = MagicMock()
        link.get_attribute.return_value = "/profile/job_details/99999"
        link.inner_text.return_value = "Engineering Manager\nNew York, NY"
        page = _mock_page()
        page.query_selector_all.return_value = [link]

        def hanging_get_description(pg, url):
            time.sleep(5)
            return ""

        fetcher = MetaPlaywrightFetcher()
        timed_out = []
        with patch.object(fetcher, "_get_description", side_effect=hanging_get_description):
            fetcher._extract_jobs(page, PREFERENCES, timed_out_urls=timed_out)

        assert len(timed_out) == 1


# ── _PLAYWRIGHT_FETCHER_MAP ────────────────────────────────────────────────────

class TestPlaywrightFetcherMap:
    @pytest.mark.parametrize("ats_key,expected_cls", [
        ("meta", MetaPlaywrightFetcher),
    ])
    def test_map_contains_all_fetchers(self, ats_key, expected_cls):
        assert ats_key in _PLAYWRIGHT_FETCHER_MAP
        assert _PLAYWRIGHT_FETCHER_MAP[ats_key] is expected_cls

    def test_walmart_not_in_playwright_map(self):
        assert "walmart" not in _PLAYWRIGHT_FETCHER_MAP
