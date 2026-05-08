"""Unit tests for Playwright-based job fetchers.

Parsing logic is tested with mock page objects. Browser-level integration
(navigation, wait_for_timeout) is not tested here — those are covered by
manually running `python3 run.py` against real sites.
"""
import pytest
from unittest.mock import MagicMock, patch, call

from pipeline.playwright_fetcher import (
    GooglePlaywrightFetcher,
    ApplePlaywrightFetcher,
    MetaPlaywrightFetcher,
    MicrosoftPlaywrightFetcher,
    _PLAYWRIGHT_FETCHER_MAP,
)

PREFERENCES = {
    "title_keywords": ["Engineering Manager", "Director of Engineering"],
    "title_exclude_keywords": ["Software Engineer", "Product Manager"],
    "excluded_location_keywords": [],
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _el(text="", href=None, attribute=None):
    """Build a minimal mock Playwright element handle."""
    el = MagicMock()
    el.inner_text.return_value = text
    el.get_attribute.return_value = href if attribute is None else attribute
    el.query_selector.return_value = None
    el.query_selector_all.return_value = []
    return el


def _mock_page(goto=None, evaluate=None, query_selector=None, query_selector_all=None):
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_timeout.return_value = None
    if evaluate is not None:
        page.evaluate.return_value = evaluate
    if query_selector is not None:
        page.query_selector.side_effect = query_selector
    if query_selector_all is not None:
        page.query_selector_all.side_effect = query_selector_all
    return page


# ── GooglePlaywrightFetcher ────────────────────────────────────────────────────

class TestGooglePlaywrightFetcher:
    def _make_card(self, job_id, title, location, href):
        card = MagicMock()
        card.get_attribute.return_value = f"Aiqs8c;{job_id};$2"
        title_el = _el(title)
        location_el = _el(location)
        link_el = _el(href=href)
        def qs(selector):
            if "h3" in selector:
                return title_el
            if "r0wTof" in selector:
                return location_el
            if "jobs/results" in selector:
                return link_el
            return None
        card.query_selector.side_effect = qs
        return card

    def test_extracts_matching_jobs(self):
        card1 = self._make_card("134620137398379206", "Engineering Manager, Ads", "New York, NY, USA",
                                "jobs/results/134620137398379206-engineering-manager-ads")
        card2 = self._make_card("103210984117543622", "Software Engineer, Backend", "San Francisco, CA",
                                "jobs/results/103210984117543622-software-engineer")
        page = _mock_page()
        page.query_selector_all.return_value = [card1, card2]

        fetcher = GooglePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="desc"):
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page, lambda x: None, set())

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Engineering Manager, Ads"
        assert jobs[0]["job_id"] == "134620137398379206"

    def test_skips_cards_without_jsdata_job_id(self):
        card = MagicMock()
        card.get_attribute.return_value = "other;notanid;$0"
        card.query_selector.return_value = None
        page = _mock_page()
        page.query_selector_all.return_value = [card]
        fetcher = GooglePlaywrightFetcher()
        jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page, lambda x: None, set())
        assert jobs == []

    def test_returns_normalized_job_dict(self):
        card = self._make_card("131416680206082758", "Director of Engineering, Platform", "Remote",
                               "jobs/results/131416680206082758-director-of-engineering")
        page = _mock_page()
        page.query_selector_all.return_value = [card]
        fetcher = GooglePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="full jd"):
            jobs = fetcher._fetch_keyword("Director of Engineering", PREFERENCES, page, lambda x: None, set())
        assert len(jobs) == 1
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Google"
        assert job["description"] == "full jd"

    def test_deduplicates_across_keywords(self):
        card = self._make_card("112567344349225670", "Engineering Manager, Ads", "New York, NY, USA",
                               "jobs/results/112567344349225670-engineering-manager-ads")
        page = _mock_page()
        page.query_selector_all.return_value = [card]
        fetcher = GooglePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher.fetch(PREFERENCES, page)
        ids = [j["job_id"] for j in jobs]
        assert ids.count("112567344349225670") == 1

    def test_returns_empty_on_page_load_failure(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = GooglePlaywrightFetcher()
        jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page, lambda x: None, set())
        assert jobs == []

    def test_get_description_returns_empty_on_error(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = GooglePlaywrightFetcher()
        assert fetcher._get_description(page, "https://example.com") == ""


# ── ApplePlaywrightFetcher ─────────────────────────────────────────────────────

def _apple_api_response(jobs):
    """Build a mock Apple API response for the given job dicts."""
    return {
        "res": {
            "searchResults": [
                {
                    "id": j["id"],
                    "postingTitle": j["title"],
                    "locations": [{"name": j["location"]}],
                }
                for j in jobs
            ],
            "totalRecords": len(jobs),
        }
    }


class TestApplePlaywrightFetcher:
    def _mock_api(self, jobs, empty_page=2):
        """Return a requests.post side_effect that returns jobs on page 1, empty on page 2+."""
        def side_effect(url, json=None, **kwargs):
            page_num = (json or {}).get("page", 1)
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            mock.json.return_value = _apple_api_response(jobs) if page_num == 1 else {"res": {"searchResults": []}}
            return mock
        return side_effect

    def test_filters_by_title(self):
        api_jobs = [
            {"id": "100", "title": "Engineering Manager, Siri", "location": "New York, NY"},
            {"id": "200", "title": "Software Engineer, iOS", "location": "Cupertino, CA"},
        ]
        page = _mock_page()
        fetcher = ApplePlaywrightFetcher()
        with patch("pipeline.playwright_fetcher.requests.post", side_effect=self._mock_api(api_jobs)):
            with patch.object(fetcher, "_get_description", return_value=""):
                jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Engineering Manager, Siri"

    def test_extracts_job_id(self):
        api_jobs = [{"id": "ABC123", "title": "Engineering Manager, Maps", "location": "Seattle, WA"}]
        page = _mock_page()
        fetcher = ApplePlaywrightFetcher()
        with patch("pipeline.playwright_fetcher.requests.post", side_effect=self._mock_api(api_jobs)):
            with patch.object(fetcher, "_get_description", return_value=""):
                jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs[0]["job_id"] == "ABC123"

    def test_returns_empty_on_api_failure(self):
        page = _mock_page()
        fetcher = ApplePlaywrightFetcher()
        with patch("pipeline.playwright_fetcher.requests.post", side_effect=Exception("Connection error")):
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs == []

    def test_returns_normalized_job_dict(self):
        api_jobs = [{"id": "XYZ", "title": "Engineering Manager, AI", "location": "Remote"}]
        page = _mock_page()
        fetcher = ApplePlaywrightFetcher()
        with patch("pipeline.playwright_fetcher.requests.post", side_effect=self._mock_api(api_jobs)):
            with patch.object(fetcher, "_get_description", return_value="jd"):
                jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Apple"
        assert job["url"] == "https://jobs.apple.com/en-us/details/XYZ"


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


# ── _PLAYWRIGHT_FETCHER_MAP ────────────────────────────────────────────────────

class TestPlaywrightFetcherMap:
    @pytest.mark.parametrize("ats_key,expected_cls", [
        ("google", GooglePlaywrightFetcher),
        ("apple", ApplePlaywrightFetcher),
        ("meta", MetaPlaywrightFetcher),
    ])
    def test_map_contains_all_fetchers(self, ats_key, expected_cls):
        assert ats_key in _PLAYWRIGHT_FETCHER_MAP
        assert _PLAYWRIGHT_FETCHER_MAP[ats_key] is expected_cls

    def test_walmart_not_in_playwright_map(self):
        assert "walmart" not in _PLAYWRIGHT_FETCHER_MAP
