"""Integration tests for web/server.py — settings, company, and analyze endpoints.

Uses FastAPI TestClient with a temp CONFIG_DIR and in-memory/temp DB so the
real jobs.db and companies.yaml are never touched.
"""
import json
import os
import shutil
import tempfile

import pytest
import yaml
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import web.server as server_module
from web.server import app

# ── Fixtures ─────────────────────────────────────────────────────────────────

INITIAL_COMPANIES = [
    {"name": "Acme Corp", "ats": "greenhouse", "board_slug": "acme"},
    {"name": "GlobalTech", "ats": "lever", "board_slug": "globaltech"},
]

INITIAL_PREFS = {
    "match_threshold": 7,
    "llm_provider": "gemini",
    "us_only": False,
    "title_keywords": ["Engineering Manager", "Director"],
    "title_exclude_keywords": ["Junior"],
    "preferred_locations": ["New York, NY"],
    "acceptable_locations": ["Remote"],
    "excluded_location_keywords": ["India"],
}


@pytest.fixture()
def cfg_dir(tmp_path):
    """Populate a temp config dir with companies + prefs and wire into server."""
    companies_path = tmp_path / "companies.yaml"
    prefs_path = tmp_path / "preferences.yaml"
    companies_path.write_text(yaml.dump({"companies": INITIAL_COMPANIES}))
    prefs_path.write_text(yaml.dump(INITIAL_PREFS))
    return tmp_path


@pytest.fixture()
def db_path(tmp_path):
    """Separate temp DB path."""
    return str(tmp_path / "test_jobs.db")


@pytest.fixture()
def client(cfg_dir, db_path, monkeypatch):
    """TestClient with CONFIG_DIR and DB_PATH pointing at temp fixtures."""
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    # Unset WEB_API_KEY so auth is open-access in tests
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c


def _seed_job(db_path, company="Acme Corp", job_id="j1", **kwargs):
    """Insert a job into the temp DB for tests that need an existing record."""
    from pipeline.store import JobStore
    store = JobStore(db_path)
    store.upsert_job({
        "company": company,
        "job_id": job_id,
        "title": "Engineering Manager",
        "location": "New York, NY",
        "url": "https://example.com/jobs/j1",
        "description": "Lead a team of engineers.",
        "apply_url": "https://example.com/apply/j1",
        **kwargs,
    })
    store.close()


# ── GET /api/settings/companies ───────────────────────────────────────────────

class TestSettingsListCompanies:
    def test_returns_list(self, client):
        r = client.get("/api/settings/companies")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_company_fields_present(self, client):
        r = client.get("/api/settings/companies")
        names = [c["name"] for c in r.json()]
        assert "Acme Corp" in names
        assert "GlobalTech" in names

    def test_ats_field_returned(self, client):
        r = client.get("/api/settings/companies")
        by_name = {c["name"]: c for c in r.json()}
        assert by_name["Acme Corp"]["ats"] == "greenhouse"
        assert by_name["Acme Corp"]["board_slug"] == "acme"


# ── POST /api/settings/companies ──────────────────────────────────────────────

class TestSettingsAddCompany:
    def test_add_new_company_returns_ok(self, client):
        r = client.post("/api/settings/companies", json={
            "name": "NewCo",
            "ats": "ashby",
            "board_slug": "newco",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_added_company_appears_in_list(self, client):
        client.post("/api/settings/companies", json={
            "name": "NewCo",
            "ats": "ashby",
            "board_slug": "newco",
        })
        r = client.get("/api/settings/companies")
        names = [c["name"] for c in r.json()]
        assert "NewCo" in names

    def test_company_persisted_to_yaml(self, client, cfg_dir):
        client.post("/api/settings/companies", json={
            "name": "PersistedCo",
            "ats": "greenhouse",
            "board_slug": "persistedco",
        })
        with open(cfg_dir / "companies.yaml") as f:
            data = yaml.safe_load(f)
        names = [c["name"] for c in data["companies"]]
        assert "PersistedCo" in names

    def test_duplicate_name_returns_409(self, client):
        r = client.post("/api/settings/companies", json={
            "name": "Acme Corp",
            "ats": "greenhouse",
        })
        assert r.status_code == 409

    def test_duplicate_case_insensitive_returns_409(self, client):
        r = client.post("/api/settings/companies", json={
            "name": "acme corp",
            "ats": "lever",
        })
        assert r.status_code == 409

    def test_optional_fields_stored(self, client, cfg_dir):
        client.post("/api/settings/companies", json={
            "name": "Meta",
            "ats": "meta",
            "department": "Engineering",
        })
        with open(cfg_dir / "companies.yaml") as f:
            data = yaml.safe_load(f)
        meta = next(c for c in data["companies"] if c["name"] == "Meta")
        assert meta.get("department") == "Engineering"

    def test_company_without_board_slug(self, client):
        r = client.post("/api/settings/companies", json={
            "name": "Google",
            "ats": "google",
        })
        assert r.status_code == 200
        company = r.json()["company"]
        assert "board_slug" not in company or company.get("board_slug") is None


# ── DELETE /api/settings/companies/{name} ─────────────────────────────────────

class TestSettingsRemoveCompany:
    def test_remove_existing_company(self, client):
        r = client.delete("/api/settings/companies/Acme Corp")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_removed_company_not_in_list(self, client):
        client.delete("/api/settings/companies/Acme Corp")
        r = client.get("/api/settings/companies")
        names = [c["name"] for c in r.json()]
        assert "Acme Corp" not in names

    def test_removal_persisted_to_yaml(self, client, cfg_dir):
        client.delete("/api/settings/companies/Acme Corp")
        with open(cfg_dir / "companies.yaml") as f:
            data = yaml.safe_load(f)
        names = [c["name"] for c in data["companies"]]
        assert "Acme Corp" not in names

    def test_remove_nonexistent_returns_404(self, client):
        r = client.delete("/api/settings/companies/DoesNotExist")
        assert r.status_code == 404

    def test_other_companies_unaffected_after_remove(self, client):
        client.delete("/api/settings/companies/Acme Corp")
        r = client.get("/api/settings/companies")
        names = [c["name"] for c in r.json()]
        assert "GlobalTech" in names


# ── GET /api/settings/preferences ────────────────────────────────────────────

class TestSettingsGetPreferences:
    def test_returns_dict(self, client):
        r = client.get("/api/settings/preferences")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_known_keys_present(self, client):
        r = client.get("/api/settings/preferences")
        data = r.json()
        assert "match_threshold" in data
        assert "llm_provider" in data
        assert "title_keywords" in data

    def test_values_match_yaml(self, client):
        r = client.get("/api/settings/preferences")
        data = r.json()
        assert data["match_threshold"] == 7
        assert data["llm_provider"] == "gemini"
        assert "Engineering Manager" in data["title_keywords"]

    def test_non_ui_keys_not_exposed(self, client):
        """Keys not in _PREFS_UI_KEYS must not appear in response."""
        r = client.get("/api/settings/preferences")
        data = r.json()
        assert "google_docs_links" not in data
        assert "profile_dir" not in data


# ── PUT /api/settings/preferences ────────────────────────────────────────────

class TestSettingsSavePreferences:
    def test_update_threshold(self, client):
        r = client.put("/api/settings/preferences", json={"match_threshold": 8})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_updated_value_readable_back(self, client):
        client.put("/api/settings/preferences", json={"match_threshold": 9})
        r = client.get("/api/settings/preferences")
        assert r.json()["match_threshold"] == 9

    def test_update_persisted_to_yaml(self, client, cfg_dir):
        client.put("/api/settings/preferences", json={"match_threshold": 6})
        with open(cfg_dir / "preferences.yaml") as f:
            data = yaml.safe_load(f)
        assert data["match_threshold"] == 6

    def test_partial_update_preserves_other_fields(self, client):
        client.put("/api/settings/preferences", json={"match_threshold": 5})
        r = client.get("/api/settings/preferences")
        data = r.json()
        assert data["llm_provider"] == "gemini"  # unchanged
        assert data["match_threshold"] == 5

    def test_update_list_field(self, client):
        client.put("/api/settings/preferences", json={
            "title_keywords": ["Director", "VP Engineering"],
        })
        r = client.get("/api/settings/preferences")
        assert r.json()["title_keywords"] == ["Director", "VP Engineering"]

    def test_us_only_false_persisted(self, client):
        """us_only=False must be saved, not skipped as falsy."""
        client.put("/api/settings/preferences", json={"us_only": True})
        client.put("/api/settings/preferences", json={"us_only": False})
        r = client.get("/api/settings/preferences")
        assert r.json()["us_only"] is False

    def test_update_llm_provider(self, client):
        client.put("/api/settings/preferences", json={"llm_provider": "anthropic"})
        r = client.get("/api/settings/preferences")
        assert r.json()["llm_provider"] == "anthropic"


# ── POST /api/companies/detect ────────────────────────────────────────────────

class TestDetectCompanyAts:
    def test_detect_returns_ats_result(self, client):
        fake_result = {"ats": "greenhouse", "board_slug": "testco", "tried": [], "error": None}
        with patch("pipeline.detect_ats.detect_ats", return_value=fake_result):
            r = client.post("/api/companies/detect", json={"name": "TestCo"})
        assert r.status_code == 200
        assert r.json()["ats"] == "greenhouse"

    def test_detect_known_company_returns_immediately(self, client):
        r = client.post("/api/companies/detect", json={"name": "Google"})
        assert r.status_code == 200
        data = r.json()
        assert data["ats"] == "google"
        assert data["board_slug"] is None

    def test_detect_unknown_company_returns_none_ats(self, client):
        not_found = {"ats": None, "board_slug": None, "tried": ["greenhouse:xyz"], "error": "Could not detect"}
        with patch("pipeline.detect_ats.detect_ats", return_value=not_found):
            r = client.post("/api/companies/detect", json={"name": "UnknownXYZ"})
        assert r.status_code == 200
        assert r.json()["ats"] is None
        assert r.json()["error"] is not None


# ── POST /api/jobs/{company}/{job_id}/analyze ─────────────────────────────────

class TestAnalyzeJobEndpoint:
    def test_analyze_missing_job_returns_404(self, client):
        r = client.post("/api/jobs/Acme Corp/nonexistent/analyze")
        assert r.status_code == 404

    def test_analyze_existing_job_returns_task_id(self, client, db_path):
        _seed_job(db_path)
        r = client.post("/api/jobs/Acme Corp/j1/analyze")
        assert r.status_code == 200
        data = r.json()
        assert "task_id" in data
        assert isinstance(data["task_id"], str)

    def test_analyze_creates_distinct_task_ids(self, client, db_path):
        _seed_job(db_path, job_id="j1")
        _seed_job(db_path, job_id="j2")
        r1 = client.post("/api/jobs/Acme Corp/j1/analyze")
        r2 = client.post("/api/jobs/Acme Corp/j2/analyze")
        assert r1.json()["task_id"] != r2.json()["task_id"]


# ── GET /api/jobs ─────────────────────────────────────────────────────────────

class TestListJobs:
    def test_empty_db_returns_empty_list(self, client):
        r = client.get("/api/jobs")
        assert r.status_code == 200
        assert r.json()["jobs"] == []

    def test_seeded_job_appears(self, client, db_path):
        _seed_job(db_path)
        r = client.get("/api/jobs")
        jobs = r.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Engineering Manager"

    def test_match_fields_deserialized_as_lists(self, client, db_path):
        _seed_job(db_path)
        r = client.get("/api/jobs")
        job = r.json()["jobs"][0]
        assert isinstance(job["match_requirements"], list)
        assert isinstance(job["match_resume_suggestions"], list)

    def test_analysis_data_deserialized_correctly(self, client, db_path):
        """After set_analysis(), list endpoint must return parsed lists, not JSON strings."""
        from pipeline.store import JobStore
        _seed_job(db_path)
        store = JobStore(db_path)
        store.set_analysis("Acme Corp", "j1",
            [{"requirement": "Lead team", "fit": "Strong", "evidence": "Led 10 eng", "resume_suggestion": None}],
            ["Add metrics"],
        )
        store.close()

        r = client.get("/api/jobs")
        job = r.json()["jobs"][0]
        assert isinstance(job["match_requirements"], list)
        assert len(job["match_requirements"]) == 1
        assert job["match_requirements"][0]["fit"] == "Strong"
        assert job["match_resume_suggestions"] == ["Add metrics"]
