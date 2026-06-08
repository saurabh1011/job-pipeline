"""Unit tests for job fetchers."""
import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from pipeline.fetcher import (
    GreenhouseFetcher, UberFetcher, MicrosoftFetcher, WalmartFetcher,
    CapitalOneFetcher, JSearchFetcher,
    fetch_all_companies, _matches_location, _build_company_prefs,
)
import pipeline.fetcher as fetcher_mod
import yaml


# ── Greenhouse Fetcher ────────────────────────────────────────────────────────

GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 1001,
            "title": "Senior Engineering Manager, Ads",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://boards.greenhouse.io/uber/jobs/1001",
            "content": "<p>Lead a team building ads infrastructure...</p>",
            "updated_at": "2026-04-14T00:00:00Z",
        },
        {
            "id": 1002,
            "title": "Software Engineer, Backend",  # should be filtered out
            "location": {"name": "San Francisco, CA"},
            "absolute_url": "https://boards.greenhouse.io/uber/jobs/1002",
            "content": "<p>Build backend services...</p>",
            "updated_at": "2026-04-14T00:00:00Z",
        },
        {
            "id": 1003,
            "title": "Director of Engineering, Platform",
            "location": {"name": "Remote"},
            "absolute_url": "https://boards.greenhouse.io/uber/jobs/1003",
            "content": "<p>Lead platform engineering org...</p>",
            "updated_at": "2026-04-14T00:00:00Z",
        },
    ]
}

PREFERENCES = {
    "title_keywords": [
        "Engineering Manager",
        "Senior Engineering Manager",
        "Director of Engineering",
    ],
    "title_exclude_keywords": [
        "Software Engineer",
        "Product Manager",
    ],
}


@pytest.fixture
def greenhouse_fetcher():
    return GreenhouseFetcher(board_slug="uber", company_name="Uber")


class TestGreenhouseFetcher:
    def test_filters_by_title_keywords(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = GREENHOUSE_RESPONSE
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        titles = [j["title"] for j in jobs]
        assert "Senior Engineering Manager, Ads" in titles
        assert "Director of Engineering, Platform" in titles
        assert "Software Engineer, Backend" not in titles

    def test_returns_normalized_job_dicts(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = GREENHOUSE_RESPONSE
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert "job_id" in job
        assert "company" in job
        assert "title" in job
        assert "location" in job
        assert "url" in job
        assert "apply_url" in job
        assert "description" in job

    def test_company_name_set_on_jobs(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = GREENHOUSE_RESPONSE
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        assert all(j["company"] == "Uber" for j in jobs)

    def test_strips_html_from_description(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = GREENHOUSE_RESPONSE
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        for job in jobs:
            assert "<p>" not in job["description"]
            assert "<" not in job["description"]

    def test_returns_empty_on_http_error(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception("HTTP 500")
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        assert jobs == []

    def test_correct_api_url_called(self, greenhouse_fetcher):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"jobs": []}
            mock_get.return_value.raise_for_status = MagicMock()
            greenhouse_fetcher.fetch(PREFERENCES)
        url = mock_get.call_args[0][0]
        assert "uber" in url
        assert "greenhouse" in url.lower()


# ── JSearch Fetcher ───────────────────────────────────────────────────────────

JSEARCH_RESPONSE_PAGE1 = {
    "data": [
        {
            "job_id": "js_001",
            "job_title": "Engineering Manager, Ads",
            "job_city": "New York",
            "job_state": "NY",
            "job_country": "US",
            "job_apply_link": "https://jobs.apple.com/001",
            "job_description": "Lead a team building ads infrastructure.",
        },
        {
            "job_id": "js_002",
            "job_title": "Software Engineer",  # filtered by title
            "job_city": "Remote",
            "job_state": "",
            "job_country": "US",
            "job_apply_link": "https://jobs.apple.com/002",
            "job_description": "Build backend services.",
        },
    ]
}

JSEARCH_EMPTY = {"data": []}


@pytest.fixture(autouse=True)
def reset_jsearch_counter():
    fetcher_mod._jsearch_calls_today = 0
    fetcher_mod._jsearch_date = None
    yield
    fetcher_mod._jsearch_calls_today = 0
    fetcher_mod._jsearch_date = None


class TestJSearchFetcher:
    def _mock_get(self, pages):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.side_effect = pages
        return mock

    def test_returns_title_filtered_jobs(self):
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = self._mock_get([JSEARCH_RESPONSE_PAGE1, JSEARCH_EMPTY])
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            jobs = fetcher.fetch(PREFERENCES)
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Engineering Manager, Ads"
        assert jobs[0]["company"] == "Apple"

    def test_returns_normalized_job_dict_fields(self):
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = self._mock_get([JSEARCH_RESPONSE_PAGE1, JSEARCH_EMPTY])
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            jobs = fetcher.fetch(PREFERENCES)
        job = jobs[0]
        for field in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert field in job

    def test_raises_when_api_key_missing(self):
        fetcher = JSearchFetcher("Apple", "Apple", "")
        with pytest.raises(ValueError, match="JSEARCH_API_KEY"):
            fetcher.fetch(PREFERENCES)

    def test_raises_on_non_200_response(self):
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Too Many Requests"
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            with pytest.raises(Exception, match="429"):
                fetcher.fetch(PREFERENCES)

    def test_stops_fetching_pages_when_empty_page_returned(self):
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = self._mock_get([JSEARCH_EMPTY])
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp) as mock_get:
            fetcher.fetch(PREFERENCES)
        assert mock_get.call_count == 1

    def test_increments_daily_counter(self):
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = self._mock_get([JSEARCH_EMPTY])
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            fetcher.fetch(PREFERENCES)
        assert fetcher_mod._jsearch_calls_today == 1

    def test_skips_all_pages_when_daily_limit_reached(self, monkeypatch):
        monkeypatch.setattr(fetcher_mod, "_jsearch_calls_today", 40)
        monkeypatch.setattr(fetcher_mod, "_jsearch_date", date.today())
        monkeypatch.setenv("JSEARCH_DAILY_LIMIT", "40")
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        with patch("pipeline.fetcher.requests.get") as mock_get:
            jobs = fetcher.fetch(PREFERENCES)
        mock_get.assert_not_called()
        assert jobs == []

    def test_resets_counter_on_new_day(self, monkeypatch):
        from datetime import date as d
        monkeypatch.setattr(fetcher_mod, "_jsearch_calls_today", 99)
        monkeypatch.setattr(fetcher_mod, "_jsearch_date", d(2020, 1, 1))
        fetcher = JSearchFetcher("Apple", "Apple", "test_key")
        mock_resp = self._mock_get([JSEARCH_EMPTY])
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            fetcher.fetch(PREFERENCES)
        assert fetcher_mod._jsearch_calls_today == 1


class TestFetchAllCompaniesWithCounts:
    def test_fetch_counts_populated_zero_results(self):
        companies = [{"name": "Acme", "ats": "greenhouse", "board_slug": "acme"}]
        prefs = PREFERENCES.copy()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": []}
        mock_resp.raise_for_status = MagicMock()
        fetch_counts = {}
        with patch("pipeline.fetcher.requests.get", return_value=mock_resp):
            fetch_all_companies(companies, prefs, fetch_counts=fetch_counts)
        assert "Acme" in fetch_counts
        assert fetch_counts["Acme"] == 0

    def test_errored_company_in_fetch_errors_not_zero_companies(self):
        # JSearchFetcher raises ValueError for missing API key — this propagates
        # through the thread to fetch_errors, not into zero_companies
        companies = [{"name": "Google", "ats": "jsearch", "employer": "Google"}]
        prefs = PREFERENCES.copy()
        fetch_errors = {}
        fetch_counts = {}
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("JSEARCH_API_KEY", None)
            fetch_all_companies(companies, prefs,
                                fetch_errors=fetch_errors,
                                fetch_counts=fetch_counts)
        assert "Google" in fetch_errors
        zero_companies = [n for n, c in fetch_counts.items() if c == 0 and n not in fetch_errors]
        assert "Google" not in zero_companies


# ── Title Filter Logic ────────────────────────────────────────────────────────

class TestTitleFilter:
    @pytest.mark.parametrize("title,expected", [
        ("Engineering Manager", True),
        ("Senior Engineering Manager", True),
        ("Director of Engineering", True),
        ("Engineering Director", False),  # not in PREFERENCES fixture keywords
        ("Software Engineer", False),
        ("Product Manager", False),
        ("Staff Engineering Manager", True),   # substring "Engineering Manager" matches
    ])
    def test_title_matching(self, greenhouse_fetcher, title, expected):
        job = {
            "id": 9999,
            "title": title,
            "location": {"name": "New York, NY"},
            "absolute_url": "https://example.com/jobs/9999",
            "content": "Job description",
            "updated_at": "2026-04-14T00:00:00Z",
        }
        response = {"jobs": [job]}
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = response
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = greenhouse_fetcher.fetch(PREFERENCES)
        found = any(j["title"] == title for j in jobs)
        assert found == expected


# ── UberFetcher ───────────────────────────────────────────────────────────────

UBER_RESPONSE = {
    "status": "success",
    "data": {
        "results": [
            {
                "id": 153366,
                "title": "Engineering Manager, Marketplace",
                "location": {"city": "New York", "countryName": "United States"},
                "description": "Lead marketplace engineering teams...",
            },
            {
                "id": 153367,
                "title": "Software Engineer, Backend",
                "location": {"city": "San Francisco", "countryName": "United States"},
                "description": "Build backend services...",
            },
        ]
    },
}


@pytest.fixture
def uber_fetcher():
    return UberFetcher(company_name="Uber")


class TestUberFetcher:
    def test_filters_by_title_keywords(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = UBER_RESPONSE
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        titles = [j["title"] for j in jobs]
        assert "Engineering Manager, Marketplace" in titles
        assert "Software Engineer, Backend" not in titles

    def test_returns_normalized_job_dicts(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = UBER_RESPONSE
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        for key in ["job_id", "company", "title", "location", "url", "apply_url", "description"]:
            assert key in job

    def test_company_name_set(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = UBER_RESPONSE
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        assert all(j["company"] == "Uber" for j in jobs)

    def test_returns_empty_on_http_error(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 500")
            jobs = uber_fetcher.fetch(PREFERENCES)
        assert jobs == []

    def test_returns_empty_on_non_success_status(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "status": "failure",
                "data": {"message": "Error searching jobs"}
            }
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        assert jobs == []

    def test_url_links_to_specific_job(self, uber_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = UBER_RESPONSE
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert job["url"] == "https://www.uber.com/global/en/careers/list/153366/"
        assert job["apply_url"] == job["url"]

    def test_deduplicates_across_keywords(self, uber_fetcher):
        single_job = {
            "status": "success",
            "data": {"results": [{
                "id": 999,
                "title": "Engineering Manager, Platform",
                "location": {"city": "New York", "countryName": "United States"},
                "description": "Lead platform teams...",
            }]}
        }
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.json.return_value = single_job
            mock_post.return_value.raise_for_status = MagicMock()
            jobs = uber_fetcher.fetch(PREFERENCES)
        assert len([j for j in jobs if j["job_id"] == "999"]) == 1


# ── fetch_all_companies ───────────────────────────────────────────────────────

COMPANIES_CONFIG = [
    {"name": "Stripe", "ats": "greenhouse", "board_slug": "stripe"},
    {"name": "DoorDash", "ats": "greenhouse", "board_slug": "doordashusa"},
]


class TestFetchAllCompanies:
    def test_aggregates_jobs_from_all_companies(self):
        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {
                "jobs": [{
                    "id": 1,
                    "title": "Engineering Manager",
                    "location": {"name": "New York, NY"},
                    "absolute_url": "https://example.com/1",
                    "content": "Description",
                    "updated_at": "2026-04-14T00:00:00Z",
                }]
            }
            mock_get.return_value.raise_for_status = MagicMock()
            jobs = fetch_all_companies(COMPANIES_CONFIG, PREFERENCES)
        companies = {j["company"] for j in jobs}
        assert "Stripe" in companies
        assert "DoorDash" in companies

    def test_returns_empty_list_when_no_companies(self):
        jobs = fetch_all_companies([], PREFERENCES)
        assert jobs == []


# ── MicrosoftFetcher ──────────────────────────────────────────────────────────

MS_SEARCH_RESPONSE = {
    "status": 200,
    "error": {"message": "", "body": ""},
    "data": {
        "positions": [
            {
                "id": 1970393556735311,
                "displayJobId": "200021885",
                "name": "Member of Technical Staff - Copilot AI Evaluation Engineering Manager",
                "locations": ["United States, California, Mountain View"],
                "postedTs": 1773255862,
                "department": "Software Engineering",
                "workLocationOption": "onsite",
                "atsJobId": "200021885",
                "positionUrl": "/careers/job/1970393556735311",
            },
            {
                "id": 1970393556999999,
                "displayJobId": "200099999",
                "name": "Software Engineer II",  # should be filtered out
                "locations": ["United States, Washington, Redmond"],
                "postedTs": 1773255000,
                "department": "Software Engineering",
                "workLocationOption": "onsite",
                "atsJobId": "200099999",
                "positionUrl": "/careers/job/1970393556999999",
            },
        ],
        "count": 2,
    },
}

MS_DETAIL_RESPONSE = {
    "status": 200,
    "data": {
        "id": 1970393556735311,
        "displayJobId": "200021885",
        "name": "Member of Technical Staff - Copilot AI Evaluation Engineering Manager",
        "locations": ["United States, California, Mountain View"],
        "postedTs": 1773255862,
        "department": "Software Engineering",
        "workLocationOption": "onsite",
        "efcustomTextEmploymentType": ["Full-Time"],
        "efcustomTextRoletype": ["People Manager"],
        "jobDescription": "<b>Overview</b><p>Lead LLM evaluation solutions for Copilot.</p>"
                          "<b>Required Qualifications</b><ul><li>5+ years EM experience</li></ul>"
                          "<b>Preferred Qualifications</b><ul><li>AI/ML background</li></ul>",
    },
}


@pytest.fixture
def ms_fetcher():
    return MicrosoftFetcher(company_name="Microsoft")


def _make_ms_mock(search_resp, detail_resp, search_raises=None, detail_raises=None):
    """Returns a mock for requests.get that handles both search and detail calls."""
    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        if "position_details" in url:
            if detail_raises:
                mock.raise_for_status.side_effect = detail_raises
            else:
                mock.json.return_value = detail_resp
        else:
            if search_raises:
                mock.raise_for_status.side_effect = search_raises
            else:
                mock.json.return_value = search_resp
        return mock
    return side_effect


class TestMicrosoftFetcher:
    def test_filters_by_title_keywords(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        titles = [j["title"] for j in jobs]
        assert any("Engineering Manager" in t for t in titles)
        assert all("Software Engineer II" not in t for t in titles)

    def test_returns_all_required_fields(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        assert len(jobs) == 1
        job = jobs[0]
        for key in ["job_id", "company", "title", "location", "url", "apply_url", "description", "date_posted"]:
            assert key in job, f"Missing key: {key}"

    def test_description_is_populated_and_html_stripped(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert len(job["description"]) > 50, "Description should be non-trivially populated"
        assert "<b>" not in job["description"], "HTML tags should be stripped"
        assert "<p>" not in job["description"], "HTML tags should be stripped"
        assert "Qualifications" in job["description"], "Description content should be present"

    def test_date_posted_populated(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert job["date_posted"] == "2026-03-11"

    def test_location_populated(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert job["location"] == "United States, California, Mountain View"

    def test_url_format(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        job = jobs[0]
        assert job["url"] == "https://apply.careers.microsoft.com/careers/job/1970393556735311"
        assert job["apply_url"] == job["url"]

    def test_company_name_set(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch(PREFERENCES)
        assert all(j["company"] == "Microsoft" for j in jobs)

    def test_deduplicates_across_keywords(self, ms_fetcher):
        single_position_resp = {
            "status": 200,
            "data": {"positions": [MS_SEARCH_RESPONSE["data"]["positions"][0]]},
        }
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(single_position_resp, MS_DETAIL_RESPONSE)):
            jobs = ms_fetcher.fetch({**PREFERENCES, "title_keywords": ["Engineering Manager", "EM"]})
        assert len([j for j in jobs if j["job_id"] == "1970393556735311"]) == 1

    def test_returns_empty_on_search_error(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(None, None, search_raises=Exception("HTTP 500"))):
            jobs = ms_fetcher.fetch(PREFERENCES)
        assert jobs == []

    def test_description_empty_on_detail_error(self, ms_fetcher):
        with patch("pipeline.fetcher.requests.get", side_effect=_make_ms_mock(MS_SEARCH_RESPONSE, None, detail_raises=Exception("HTTP 429"))):
            jobs = ms_fetcher.fetch(PREFERENCES)
        assert len(jobs) == 1
        assert jobs[0]["description"] == ""


# ── _matches_location with location_filter ────────────────────────────────────

class TestMatchesLocationFilter:
    def test_passes_when_no_filter(self):
        assert _matches_location("New York, NY", {}) is True
        assert _matches_location("Seattle, WA", {}) is True

    def test_passes_when_location_matches_filter(self):
        prefs = {"location_filter": ["new york", "remote"]}
        assert _matches_location("New York, NY", prefs) is True
        assert _matches_location("Remote", prefs) is True

    def test_fails_when_location_not_in_filter(self):
        prefs = {"location_filter": ["new york", "remote"]}
        assert _matches_location("San Francisco, CA", prefs) is False
        assert _matches_location("Seattle, WA", prefs) is False

    def test_filter_is_case_insensitive(self):
        prefs = {"location_filter": ["New York"]}
        assert _matches_location("new york, ny", prefs) is True

    def test_filter_and_excluded_both_apply(self):
        prefs = {"location_filter": ["new york", "remote"], "excluded_location_keywords": ["canada"]}
        assert _matches_location("New York, NY", prefs) is True
        assert _matches_location("Remote, Canada", prefs) is False  # excluded denylist wins

    def test_empty_filter_list_does_not_restrict(self):
        prefs = {"location_filter": []}
        assert _matches_location("San Francisco, CA", prefs) is True


# ── _build_company_prefs ──────────────────────────────────────────────────────

class TestBuildCompanyPrefs:
    def test_returns_copy_of_global_prefs(self):
        global_prefs = {"title_keywords": ["Engineering Manager"], "us_only": True}
        result = _build_company_prefs({}, global_prefs)
        assert result == global_prefs
        result["title_keywords"].append("extra")
        assert "extra" not in global_prefs["title_keywords"]

    def test_overrides_title_keywords(self):
        global_prefs = {"title_keywords": ["Engineering Manager"]}
        company = {"title_keywords": ["Senior Engineering Manager", "Director of Engineering"]}
        result = _build_company_prefs(company, global_prefs)
        assert result["title_keywords"] == ["Senior Engineering Manager", "Director of Engineering"]

    def test_adds_location_filter(self):
        global_prefs = {"title_keywords": ["Engineering Manager"]}
        company = {"location_filter": ["new york", "remote"]}
        result = _build_company_prefs(company, global_prefs)
        assert result["location_filter"] == ["new york", "remote"]

    def test_global_prefs_unchanged_when_company_has_overrides(self):
        global_prefs = {"title_keywords": ["Engineering Manager"]}
        company = {"title_keywords": ["Director"], "location_filter": ["remote"]}
        _build_company_prefs(company, global_prefs)
        assert global_prefs == {"title_keywords": ["Engineering Manager"]}
        assert "location_filter" not in global_prefs

    def test_preserves_other_global_prefs_fields(self):
        global_prefs = {"title_keywords": ["EM"], "us_only": True, "match_threshold": 8}
        company = {"title_keywords": ["Director"]}
        result = _build_company_prefs(company, global_prefs)
        assert result["us_only"] is True
        assert result["match_threshold"] == 8


# ── fetch_all_companies — per-company timeout ─────────────────────────────────

class TestFetchAllCompaniesErrors:
    def test_fetch_errors_populated_on_timeout(self):
        def slow_fetch(prefs):
            time.sleep(5)
            return []

        company = {"name": "SlowCo", "ats": "greenhouse", "board_slug": "slowco", "fetch_timeout": 1}
        errors = {}
        with patch("pipeline.fetcher.GreenhouseFetcher.fetch", side_effect=slow_fetch):
            fetch_all_companies([company], PREFERENCES, fetch_errors=errors)
        assert "SlowCo" in errors
        assert "TIMEOUT" in errors["SlowCo"]

    def test_fetch_errors_populated_on_exception(self):
        company = {"name": "Stripe", "ats": "greenhouse", "board_slug": "stripe"}
        errors = {}
        with patch("pipeline.fetcher.GreenhouseFetcher.fetch", side_effect=Exception("Connection refused")):
            fetch_all_companies([company], PREFERENCES, fetch_errors=errors)
        assert "Stripe" in errors
        assert "Connection refused" in errors["Stripe"]

    def test_fetch_errors_not_set_on_success(self):
        company = {"name": "Stripe", "ats": "greenhouse", "board_slug": "stripe"}
        errors = {}
        with patch("pipeline.fetcher.GreenhouseFetcher.fetch", return_value=[]):
            fetch_all_companies([company], PREFERENCES, fetch_errors=errors)
        assert "Stripe" not in errors

    def test_fetch_errors_none_param_does_not_crash_on_timeout(self):
        def slow_fetch(prefs):
            time.sleep(5)
            return []

        company = {"name": "SlowCo", "ats": "greenhouse", "board_slug": "slowco", "fetch_timeout": 1}
        with patch("pipeline.fetcher.GreenhouseFetcher.fetch", side_effect=slow_fetch):
            jobs = fetch_all_companies([company], PREFERENCES, fetch_errors=None)
        assert jobs == []


class TestFetchAllCompaniesTimeout:
    def test_skips_company_that_exceeds_timeout(self):
        def slow_fetch(prefs):
            time.sleep(5)
            return [{"job_id": "1", "company": "Slow", "title": "EM", "location": "NY",
                     "url": "https://x.com", "apply_url": "https://x.com", "description": ""}]

        slow_company = {"name": "SlowCo", "ats": "greenhouse", "board_slug": "slowco", "fetch_timeout": 1}
        fast_company = {"name": "Stripe", "ats": "greenhouse", "board_slug": "stripe"}

        logs = []

        with patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {
                "jobs": [{
                    "id": 99, "title": "Engineering Manager", "location": {"name": "New York, NY"},
                    "absolute_url": "https://boards.greenhouse.io/stripe/jobs/99",
                    "content": "Description", "updated_at": "2026-04-14T00:00:00Z",
                }]
            }
            mock_get.return_value.raise_for_status = MagicMock()
            with patch("pipeline.fetcher.GreenhouseFetcher.fetch", side_effect=slow_fetch):
                jobs = fetch_all_companies([slow_company], PREFERENCES, log=logs.append)

        assert jobs == []
        assert any("TIMEOUT" in msg for msg in logs)

    def test_company_specific_title_keywords_passed_to_fetcher(self):
        captured_prefs = {}

        def capture_fetch(prefs):
            captured_prefs.update(prefs)
            return []

        company = {
            "name": "Zillow", "ats": "zillow",
            "title_keywords": ["Senior Engineering Manager", "Director of Engineering"],
        }
        with patch("pipeline.fetcher.ZillowFetcher.fetch", side_effect=capture_fetch):
            fetch_all_companies([company], PREFERENCES)

        assert captured_prefs["title_keywords"] == ["Senior Engineering Manager", "Director of Engineering"]

    def test_global_title_keywords_used_when_no_override(self):
        captured_prefs = {}

        def capture_fetch(prefs):
            captured_prefs.update(prefs)
            return []

        company = {"name": "Stripe", "ats": "greenhouse", "board_slug": "stripe"}
        with patch("pipeline.fetcher.GreenhouseFetcher.fetch", side_effect=capture_fetch):
            fetch_all_companies([company], PREFERENCES)

        assert captured_prefs["title_keywords"] == PREFERENCES["title_keywords"]

    def test_playwright_company_timeout_sets_fetch_error(self):
        """A Playwright company that exceeds fetch_timeout is added to fetch_errors."""
        import pipeline.fetcher as fetcher_module
        company = {"name": "Google", "ats": "google", "fetch_timeout": 1}
        fetch_errors = {}
        logs = []

        def slow_playwright_fetch(prefs, page, log=None, timed_out_urls=None):
            time.sleep(5)
            return []

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch = slow_playwright_fetch
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        mock_page = MagicMock()
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_sp_instance = MagicMock()
        mock_sp_instance.__enter__ = lambda s: mock_pw_ctx
        mock_sp_instance.__exit__ = MagicMock(return_value=False)

        orig_ats = fetcher_module._PLAYWRIGHT_ATS
        fetcher_module._PLAYWRIGHT_ATS = {"google"}
        try:
            with patch("playwright.sync_api.sync_playwright", return_value=mock_sp_instance), \
                 patch("pipeline.playwright_fetcher._PLAYWRIGHT_FETCHER_MAP", {"google": mock_fetcher_cls}):
                fetch_all_companies([company], PREFERENCES, log=logs.append, fetch_errors=fetch_errors)
        finally:
            fetcher_module._PLAYWRIGHT_ATS = orig_ats

        assert "Google" in fetch_errors
        assert "TIMEOUT" in fetch_errors["Google"]
        assert any("TIMEOUT" in m for m in logs)

    def test_playwright_description_skips_populate_fetch_errors(self):
        """Description-level timeouts are reported in fetch_errors after a successful fetch."""
        import pipeline.fetcher as fetcher_module
        company = {"name": "Google", "ats": "google", "fetch_timeout": 30}
        fetch_errors = {}

        def fetch_with_skips(prefs, page, log=None, timed_out_urls=None):
            if timed_out_urls is not None:
                timed_out_urls.append("https://careers.google.com/jobs/results/123")
                timed_out_urls.append("https://careers.google.com/jobs/results/456")
            return [{"job_id": "1", "company": "Google", "title": "EM", "location": "NY",
                     "url": "https://x.com", "apply_url": "https://x.com", "description": ""}]

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch = fetch_with_skips
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        mock_page = MagicMock()
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_sp_instance = MagicMock()
        mock_sp_instance.__enter__ = lambda s: mock_pw_ctx
        mock_sp_instance.__exit__ = MagicMock(return_value=False)

        orig_ats = fetcher_module._PLAYWRIGHT_ATS
        fetcher_module._PLAYWRIGHT_ATS = {"google"}
        try:
            with patch("playwright.sync_api.sync_playwright", return_value=mock_sp_instance), \
                 patch("pipeline.playwright_fetcher._PLAYWRIGHT_FETCHER_MAP", {"google": mock_fetcher_cls}):
                jobs = fetch_all_companies([company], PREFERENCES, fetch_errors=fetch_errors)
        finally:
            fetcher_module._PLAYWRIGHT_ATS = orig_ats

        assert len(jobs) == 1
        assert "Google" in fetch_errors
        assert "2" in fetch_errors["Google"]
        assert "timed out" in fetch_errors["Google"]


# ── WalmartFetcher ────────────────────────────────────────────────────────────

def _walmart_search_response(jobs, total=None):
    return {"jobs": jobs, "totalCount": total or len(jobs)}


def _walmart_job(job_id="w001", title="Senior Manager, Software Engineering",
                 city="San Bruno", text="Job Posting Description: Lead teams.\nLocation: San Bruno, CA"):
    return {
        "id": job_id,
        "metadata": {"title": title, "primaryLocationCity": city},
        "text": text,
    }


WALMART_PREFS = {
    "title_keywords": ["Manager, Software Engineering"],
    "title_exclude_keywords": ["Warehouse", "Hourly"],
    "excluded_location_keywords": [],
}


class TestWalmartFetcher:
    def test_filters_by_title(self):
        matching = _walmart_job(title="Senior Manager, Software Engineering")
        non_matching = _walmart_job(job_id="w002", title="Warehouse Associate")
        mock_resp = MagicMock()
        mock_resp.json.return_value = _walmart_search_response([matching, non_matching])
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.fetcher.requests.post", return_value=mock_resp):
            jobs = WalmartFetcher().fetch(WALMART_PREFS)
        titles = [j["title"] for j in jobs]
        assert "Senior Manager, Software Engineering" in titles
        assert "Warehouse Associate" not in titles

    def test_returns_normalized_job_dict(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _walmart_search_response([_walmart_job()])
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.fetcher.requests.post", return_value=mock_resp):
            jobs = WalmartFetcher().fetch(WALMART_PREFS)
        assert len(jobs) == 1
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job
        assert job["company"] == "Walmart"
        assert job["url"] == "https://careers.walmart.com/us/jobs/w001/job"

    def test_extracts_description_after_header(self):
        text = "Job Posting Description: Lead amazing teams.\nLocation: San Bruno"
        mock_resp = MagicMock()
        mock_resp.json.return_value = _walmart_search_response([_walmart_job(text=text)])
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.fetcher.requests.post", return_value=mock_resp):
            jobs = WalmartFetcher().fetch(WALMART_PREFS)
        assert jobs[0]["description"].startswith("Lead amazing teams.")

    def test_falls_back_to_full_text_when_no_header(self):
        text = "Engineering leadership role with no header."
        mock_resp = MagicMock()
        mock_resp.json.return_value = _walmart_search_response([_walmart_job(text=text)])
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.fetcher.requests.post", return_value=mock_resp):
            jobs = WalmartFetcher().fetch(WALMART_PREFS)
        assert jobs[0]["description"] == text

    def test_deduplicates_across_keywords(self):
        prefs = {**WALMART_PREFS, "title_keywords": ["Manager, Software Engineering", "Director, Engineering"]}
        job = _walmart_job()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _walmart_search_response([job])
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.fetcher.requests.post", return_value=mock_resp):
            jobs = WalmartFetcher().fetch(prefs)
        assert len(jobs) == 1

    def test_returns_empty_on_network_error(self):
        with patch("pipeline.fetcher.requests.post", side_effect=Exception("Connection refused")):
            jobs = WalmartFetcher().fetch(WALMART_PREFS)
        assert jobs == []

    def test_stops_pagination_when_empty_page(self):
        # Use PAGE_SIZE=1 so a single-job page is "full" and pagination continues
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.raise_for_status = MagicMock()
            m.json.return_value = _walmart_search_response([]) if call_count > 1 else _walmart_search_response([_walmart_job()])
            return m

        fetcher = WalmartFetcher()
        with patch.object(fetcher, "_PAGE_SIZE", 1), \
             patch("pipeline.fetcher.requests.post", side_effect=side_effect):
            jobs = fetcher.fetch(WALMART_PREFS)
        assert call_count == 2
        assert len(jobs) == 1


# ── WalmartFetcher — integration ──────────────────────────────────────────────

_COMPANIES_YAML_PATH = (
    __import__("pathlib").Path(__file__).parent.parent / "config" / "companies.yaml"
)


def _load_walmart_company_config():
    with open(_COMPANIES_YAML_PATH) as f:
        config = yaml.safe_load(f)
    companies = config.get("companies", [])
    return next(c for c in companies if c.get("ats") == "walmart")


class TestWalmartFetcherIntegration:
    """Verify the full path: companies.yaml → fetch_all_companies → WalmartFetcher."""

    def test_companies_yaml_has_walmart_title_keywords(self):
        walmart = _load_walmart_company_config()
        assert "title_keywords" in walmart, "Walmart entry in companies.yaml must have title_keywords"
        keywords = walmart["title_keywords"]
        assert any("Manager" in kw for kw in keywords), (
            "At least one keyword should match Walmart's Senior Manager naming convention"
        )

    def test_fetch_all_companies_passes_yaml_keywords_to_walmart_fetcher(self):
        walmart = _load_walmart_company_config()
        expected_keywords = walmart["title_keywords"]
        captured_prefs = {}

        def capture_fetch(prefs):
            captured_prefs.update(prefs)
            return []

        with patch("pipeline.fetcher.WalmartFetcher.fetch", side_effect=capture_fetch):
            fetch_all_companies([walmart], PREFERENCES)

        assert captured_prefs.get("title_keywords") == expected_keywords, (
            "fetch_all_companies must pass companies.yaml title_keywords to WalmartFetcher, "
            f"not the global ones. Got: {captured_prefs.get('title_keywords')}"
        )

    def test_fetch_all_companies_returns_walmart_jobs_in_results(self):
        walmart = _load_walmart_company_config()
        fake_job = {
            "job_id": "R-integration-001",
            "company": "Walmart",
            "title": "Senior Manager, Software Engineering",
            "location": "Sunnyvale",
            "url": "https://careers.walmart.com/us/jobs/R-integration-001/job",
            "apply_url": "https://careers.walmart.com/us/jobs/R-integration-001/job",
            "description": "Lead engineering teams.",
        }

        with patch("pipeline.fetcher.WalmartFetcher.fetch", return_value=[fake_job]):
            jobs = fetch_all_companies([walmart], PREFERENCES)

        walmart_jobs = [j for j in jobs if j["company"] == "Walmart"]
        assert len(walmart_jobs) == 1
        assert walmart_jobs[0]["job_id"] == "R-integration-001"


# ── CapitalOneFetcher ─────────────────────────────────────────────────────────

CAPITALONE_LIST_RESPONSE = {
    "jobPostings": [
        {
            "title": "Manager, Software Engineering",
            "externalPath": "/job/McLean/Manager-Software-Engineering/890/123456001",
            "locationsText": "McLean, Virginia",
        },
        {
            "title": "Senior Manager, Software Engineering",
            "externalPath": "/job/New-York/Senior-Manager-Software-Engineering/890/123456002",
            "locationsText": "New York, New York",
        },
        {
            "title": "Software Engineer",  # should be filtered out
            "externalPath": "/job/Richmond/Software-Engineer/890/123456003",
            "locationsText": "Richmond, Virginia",
        },
    ],
    "total": 3,
}

CAPITALONE_DETAIL_RESPONSE = {
    "jobPostingInfo": {
        "jobDescription": "<p>Lead a team of engineers building payment systems.</p>"
                          "<ul><li>5+ years of EM experience</li></ul>",
    }
}

CAPITALONE_PREFS = {
    "title_keywords": ["Manager, Software Engineering", "Senior Manager, Software Engineering"],
    "title_exclude_keywords": ["Software Engineer"],
    "excluded_location_keywords": ["Canada"],
}


def _make_capitalone_mock(list_resp, detail_resp, list_raises=None, detail_raises=None):
    """Returns a requests side_effect that routes list vs. detail calls."""
    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        if "jobs" in url and kwargs.get("json") is not None:
            # list call (POST)
            if list_raises:
                mock.raise_for_status.side_effect = list_raises
            else:
                mock.json.return_value = list_resp
        else:
            # detail call (GET)
            if detail_raises:
                mock.raise_for_status.side_effect = detail_raises
            else:
                mock.json.return_value = detail_resp
        return mock
    return side_effect


@pytest.fixture
def capitalone_fetcher():
    return CapitalOneFetcher(company_name="Capital One")


class TestCapitalOneFetcher:
    def test_filters_by_title_keywords(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        titles = [j["title"] for j in jobs]
        assert "Manager, Software Engineering" in titles
        assert "Senior Manager, Software Engineering" in titles
        assert "Software Engineer" not in titles

    def test_returns_normalized_job_dicts(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        assert len(jobs) >= 1
        job = jobs[0]
        for key in ("job_id", "company", "title", "location", "url", "apply_url", "description"):
            assert key in job, f"Missing key: {key}"

    def test_company_name_set(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        assert all(j["company"] == "Capital One" for j in jobs)

    def test_strips_html_from_description(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        for job in jobs:
            assert "<p>" not in job["description"]
            assert "<ul>" not in job["description"]

    def test_returns_empty_on_list_http_error(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 500")
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        assert jobs == []

    def test_description_empty_on_detail_error(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, None
        )), patch("pipeline.fetcher.requests.get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception("HTTP 429")
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        assert len(jobs) >= 1
        for job in jobs:
            assert job["description"] == ""

    def test_deduplicates_across_keywords(self, capitalone_fetcher):
        single_posting = {
            "jobPostings": [{
                "title": "Manager, Software Engineering",
                "externalPath": "/job/McLean/Manager-Software-Engineering/890/999",
                "locationsText": "McLean, Virginia",
            }],
            "total": 1,
        }
        prefs = {**CAPITALONE_PREFS,
                 "title_keywords": ["Manager, Software Engineering", "Senior Manager, Software Engineering"]}
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            single_posting, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            single_posting, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(prefs)
        assert len([j for j in jobs if j["job_id"] == "999"]) == 1

    def test_url_points_to_capitalone_careers(self, capitalone_fetcher):
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(CAPITALONE_PREFS)
        for job in jobs:
            assert "capitalone" in job["url"]
            assert job["apply_url"] == job["url"]

    def test_filters_by_location(self, capitalone_fetcher):
        prefs = {**CAPITALONE_PREFS, "location_filter": ["new york"]}
        with patch("pipeline.fetcher.requests.post", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )), patch("pipeline.fetcher.requests.get", side_effect=_make_capitalone_mock(
            CAPITALONE_LIST_RESPONSE, CAPITALONE_DETAIL_RESPONSE
        )):
            jobs = capitalone_fetcher.fetch(prefs)
        locations = [j["location"] for j in jobs]
        assert all("New York" in loc for loc in locations)


# ── CapitalOneFetcher — integration ──────────────────────────────────────────


def _load_capitalone_company_config():
    with open(_COMPANIES_YAML_PATH) as f:
        config = yaml.safe_load(f)
    companies = config.get("companies", [])
    return next((c for c in companies if c.get("ats") == "capitalone"), None)


class TestCapitalOneFetcherIntegration:
    """Verify the full path: companies.yaml -> fetch_all_companies -> CapitalOneFetcher."""

    def test_companies_yaml_has_capitalone_entry(self):
        co = _load_capitalone_company_config()
        assert co is not None, "companies.yaml must have an entry with ats: capitalone"

    def test_companies_yaml_capitalone_has_title_keywords(self):
        co = _load_capitalone_company_config()
        assert co is not None
        assert "title_keywords" in co, "Capital One entry must define title_keywords"
        assert any("Manager" in kw for kw in co["title_keywords"]), (
            "At least one keyword should match Capital One's naming convention"
        )

    def test_fetch_all_companies_passes_yaml_keywords_to_fetcher(self):
        co = _load_capitalone_company_config()
        assert co is not None
        expected_keywords = co["title_keywords"]
        captured_prefs = {}

        def capture_fetch(prefs):
            captured_prefs.update(prefs)
            return []

        with patch("pipeline.fetcher.CapitalOneFetcher.fetch", side_effect=capture_fetch):
            fetch_all_companies([co], PREFERENCES)

        assert captured_prefs.get("title_keywords") == expected_keywords

    def test_fetch_all_companies_returns_capitalone_jobs(self):
        co = _load_capitalone_company_config()
        assert co is not None
        fake_job = {
            "job_id": "123456001",
            "company": "Capital One",
            "title": "Manager, Software Engineering",
            "location": "McLean, Virginia",
            "url": "https://capitalone.wd1.myworkdayjobs.com/En-US/External_Careers/job/123456001",
            "apply_url": "https://capitalone.wd1.myworkdayjobs.com/En-US/External_Careers/job/123456001",
            "description": "Lead engineering teams.",
        }

        with patch("pipeline.fetcher.CapitalOneFetcher.fetch", return_value=[fake_job]):
            jobs = fetch_all_companies([co], PREFERENCES)

        co_jobs = [j for j in jobs if j["company"] == "Capital One"]
        assert len(co_jobs) == 1
        assert co_jobs[0]["job_id"] == "123456001"
