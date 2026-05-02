"""Unit tests for job fetchers."""
import pytest
from unittest.mock import patch, MagicMock
from pipeline.fetcher import GreenhouseFetcher, UberFetcher, MicrosoftFetcher, fetch_all_companies
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
