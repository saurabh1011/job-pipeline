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
    WalmartPlaywrightFetcher,
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
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)

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
        jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs == []

    def test_returns_normalized_job_dict(self):
        card = self._make_card("131416680206082758", "Director of Engineering, Platform", "Remote",
                               "jobs/results/131416680206082758-director-of-engineering")
        page = _mock_page()
        page.query_selector_all.return_value = [card]
        fetcher = GooglePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="full jd"):
            jobs = fetcher._fetch_keyword("Director of Engineering", PREFERENCES, page)
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
        jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs == []

    def test_get_description_returns_empty_on_error(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = GooglePlaywrightFetcher()
        assert fetcher._get_description(page, "https://example.com") == ""


# ── ApplePlaywrightFetcher ─────────────────────────────────────────────────────

class TestApplePlaywrightFetcher:
    def _make_link(self, href, title, location, team="Software Engineering"):
        link = MagicMock()
        link.get_attribute.return_value = href
        link.inner_text.return_value = title
        parent = MagicMock()
        loc_el = _el(location)
        def qs(sel):
            if "location" in sel.lower() or "span" in sel.lower():
                return loc_el
            return None
        parent.query_selector.side_effect = qs
        parent.inner_text.return_value = f"{title}\n{team}\nApr 20, 2026\nLocation\n{location}"
        link.evaluate.return_value = parent
        return link

    def test_filters_by_title(self):
        link1 = self._make_link("/en-us/details/100/engineering-manager?team=SFTWR",
                                "Engineering Manager, Siri", "New York, NY")
        link2 = self._make_link("/en-us/details/200/software-engineer?team=SFTWR",
                                "Software Engineer, iOS", "Cupertino, CA")
        page = _mock_page()
        page.query_selector_all.return_value = [link1, link2]
        fetcher = ApplePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Engineering Manager, Siri"

    def test_extracts_job_id_from_href(self):
        link = self._make_link("/en-us/details/ABC123/engineering-manager?team=SFTWR",
                               "Engineering Manager, Maps", "Seattle, WA")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = ApplePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value=""):
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs[0]["job_id"] == "ABC123"

    def test_returns_empty_on_page_load_failure(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = ApplePlaywrightFetcher()
        jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        assert jobs == []

    def test_returns_normalized_job_dict(self):
        link = self._make_link("/en-us/details/XYZ/eng-manager?team=SFTWR",
                               "Engineering Manager, AI", "Remote")
        page = _mock_page()
        page.query_selector_all.return_value = [link]
        fetcher = ApplePlaywrightFetcher()
        with patch.object(fetcher, "_get_description", return_value="jd"):
            jobs = fetcher._fetch_keyword("Engineering Manager", PREFERENCES, page)
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Apple"


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



# ── WalmartPlaywrightFetcher ───────────────────────────────────────────────────

WALMART_API_RESPONSE = {
    "data": {
        "jobSearch": {
            "jobs": [
                {
                    "id": "w001",
                    "title": "Senior Engineering Manager, Supply Chain",
                    "location": "San Bruno, CA",
                    "postedDate": "2026-04-18",
                    "jobUrl": "https://careers.walmart.com/us/jobs/w001/job",
                    "jobDescription": "Lead supply chain engineering teams...",
                },
                {
                    "id": "w002",
                    "title": "Warehouse Associate",
                    "location": "Bentonville, AR",
                    "postedDate": "2026-04-18",
                    "jobUrl": "https://careers.walmart.com/us/jobs/w002/job",
                    "jobDescription": "Fulfill orders...",
                },
            ],
            "totalCount": 2,
        }
    }
}


class TestWalmartPlaywrightFetcher:
    def test_filters_by_title(self):
        page = _mock_page(evaluate=WALMART_API_RESPONSE)
        fetcher = WalmartPlaywrightFetcher()
        with patch.object(fetcher, "_load_page", return_value=None):
            jobs = fetcher.fetch(PREFERENCES, page)
        titles = [j["title"] for j in jobs]
        assert "Senior Engineering Manager, Supply Chain" in titles
        assert "Warehouse Associate" not in titles

    def test_returns_normalized_job_dict(self):
        page = _mock_page(evaluate=WALMART_API_RESPONSE)
        fetcher = WalmartPlaywrightFetcher()
        with patch.object(fetcher, "_load_page", return_value=None):
            jobs = fetcher.fetch(PREFERENCES, page)
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Walmart"

    def test_returns_empty_on_api_failure(self):
        page = _mock_page()
        page.evaluate.side_effect = Exception("GraphQL error")
        fetcher = WalmartPlaywrightFetcher()
        with patch.object(fetcher, "_load_page", return_value=None):
            jobs = fetcher.fetch(PREFERENCES, page)
        assert jobs == []

    def test_returns_empty_on_page_load_failure(self):
        page = _mock_page()
        page.goto.side_effect = Exception("Timeout")
        fetcher = WalmartPlaywrightFetcher()
        jobs = fetcher.fetch(PREFERENCES, page)
        assert jobs == []


# ── _PLAYWRIGHT_FETCHER_MAP ────────────────────────────────────────────────────

class TestPlaywrightFetcherMap:
    @pytest.mark.parametrize("ats_key,expected_cls", [
        ("google", GooglePlaywrightFetcher),
        ("apple", ApplePlaywrightFetcher),
        ("meta", MetaPlaywrightFetcher),
        ("microsoft", MicrosoftPlaywrightFetcher),
        ("walmart", WalmartPlaywrightFetcher),
    ])
    def test_map_contains_all_fetchers(self, ats_key, expected_cls):
        assert ats_key in _PLAYWRIGHT_FETCHER_MAP
        assert _PLAYWRIGHT_FETCHER_MAP[ats_key] is expected_cls
