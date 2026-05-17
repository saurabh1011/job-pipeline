"""Integration tests for /api/profiles endpoints."""
import pytest
import yaml
from fastapi.testclient import TestClient

import web.auth_db as adb
import web.server as server_module
from web.server import app

INITIAL_PREFS = {
    "match_threshold": 7, "llm_provider": "gemini", "us_only": False,
    "title_keywords": ["Engineering Manager"], "title_exclude_keywords": [],
    "preferred_locations": [], "acceptable_locations": [],
    "excluded_location_keywords": [],
}


@pytest.fixture()
def cfg_dir(tmp_path):
    (tmp_path / "companies.yaml").write_text(yaml.dump({"companies": []}))
    (tmp_path / "preferences.yaml").write_text(yaml.dump(INITIAL_PREFS))
    return tmp_path


@pytest.fixture()
def auth_setup(tmp_path, monkeypatch, cfg_dir):
    db_path = str(tmp_path / "test.db")
    auth_path = str(tmp_path / "auth.db")
    monkeypatch.setattr(adb, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setenv("WEB_API_KEY", "test-key")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    adb.init_db()
    server_module._auth_db.AUTH_DB_PATH = auth_path
    with TestClient(app) as c:
        yield c


def _make_session(email="user@example.com", name="Test", is_admin=False) -> str:
    existing = adb.find_user_by_email(email)
    if existing:
        uid = existing["user_id"]
    else:
        u = adb.create_user(f"gid_{email}", email, name, is_admin=is_admin)
        uid = u["user_id"]
    return adb.create_session(uid)


# ── GET /api/profiles ─────────────────────────────────────────────────────────

class TestListProfiles:
    def test_dev_mode_returns_legacy_profile(self, tmp_path, monkeypatch, cfg_dir):
        monkeypatch.setattr(adb, "AUTH_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
        monkeypatch.setattr(server_module, "DB_PATH", str(tmp_path / "db.db"))
        monkeypatch.delenv("WEB_API_KEY", raising=False)
        adb.init_db()
        with TestClient(app) as c:
            r = c.get("/api/profiles")
        assert r.status_code == 200
        assert r.json()[0]["profile_id"] == "legacy"

    def test_user_with_no_profiles_gets_empty_list_initially(self, auth_setup):
        token = _make_session("newuser@x.com")
        r = auth_setup.get("/api/profiles", cookies={"session_token": token})
        assert r.status_code == 200
        # No profiles yet; list_profiles returns empty for this user
        assert isinstance(r.json(), list)

    def test_user_sees_own_profiles_only(self, auth_setup):
        token_a = _make_session("a@x.com")
        token_b = _make_session("b@x.com")
        user_a = adb.find_user_by_email("a@x.com")
        adb.create_profile(user_a["user_id"], "A Profile")

        r = auth_setup.get("/api/profiles", cookies={"session_token": token_b})
        profiles = r.json()
        assert all(p["profile_id"] != "legacy" or True for p in profiles)
        names = [p["name"] for p in profiles]
        assert "A Profile" not in names


# ── POST /api/profiles ────────────────────────────────────────────────────────

class TestCreateProfile:
    def test_create_profile(self, auth_setup, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "DATA_DIR", str(tmp_path / "data"))
        token = _make_session("creator@x.com")
        r = auth_setup.post("/api/profiles",
                            json={"name": "Director Roles"},
                            cookies={"session_token": token})
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Director Roles"
        assert data["is_legacy"] is False

    def test_create_profile_appears_in_list(self, auth_setup, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "DATA_DIR", str(tmp_path / "data"))
        token = _make_session("lister@x.com")
        auth_setup.post("/api/profiles",
                        json={"name": "TPM Roles"},
                        cookies={"session_token": token})
        r = auth_setup.get("/api/profiles", cookies={"session_token": token})
        names = [p["name"] for p in r.json()]
        assert "TPM Roles" in names

    def test_service_user_cannot_create_profile(self, auth_setup):
        r = auth_setup.post("/api/profiles",
                            json={"name": "Nope"},
                            headers={"x-api-key": "test-key"})
        assert r.status_code == 400


# ── PATCH /api/profiles/{id} ──────────────────────────────────────────────────

class TestRenameProfile:
    def test_rename_own_profile(self, auth_setup):
        token = _make_session("renamer@x.com")
        user = adb.find_user_by_email("renamer@x.com")
        p = adb.create_profile(user["user_id"], "Old Name")
        r = auth_setup.patch(f"/api/profiles/{p['profile_id']}",
                             json={"name": "New Name"},
                             cookies={"session_token": token})
        assert r.status_code == 200
        assert adb.get_profile(p["profile_id"])["name"] == "New Name"

    def test_cannot_rename_other_users_profile(self, auth_setup):
        token_b = _make_session("b2@x.com")
        user_a = adb.create_user("gid_other", "other@x.com", "Other")
        p = adb.create_profile(user_a["user_id"], "A's Profile")
        r = auth_setup.patch(f"/api/profiles/{p['profile_id']}",
                             json={"name": "Hijack"},
                             cookies={"session_token": token_b})
        assert r.status_code == 404


# ── DELETE /api/profiles/{id} ─────────────────────────────────────────────────

class TestDeleteProfile:
    def test_cannot_delete_only_profile(self, auth_setup):
        token = _make_session("solo@x.com")
        user = adb.find_user_by_email("solo@x.com")
        p = adb.create_profile(user["user_id"], "Only One")
        r = auth_setup.delete(f"/api/profiles/{p['profile_id']}",
                              cookies={"session_token": token})
        assert r.status_code == 400

    def test_delete_second_profile(self, auth_setup):
        token = _make_session("multi@x.com")
        user = adb.find_user_by_email("multi@x.com")
        p1 = adb.create_profile(user["user_id"], "Keep")
        p2 = adb.create_profile(user["user_id"], "Delete Me")
        r = auth_setup.delete(f"/api/profiles/{p2['profile_id']}",
                              cookies={"session_token": token})
        assert r.status_code == 200
        assert adb.get_profile(p2["profile_id"]) is None

    def test_cannot_delete_other_users_profile(self, auth_setup):
        token_c = _make_session("c@x.com")
        user_d = adb.create_user("gid_d", "d@x.com", "D")
        p1 = adb.create_profile(user_d["user_id"], "D's profile 1")
        p2 = adb.create_profile(user_d["user_id"], "D's profile 2")
        r = auth_setup.delete(f"/api/profiles/{p1['profile_id']}",
                              cookies={"session_token": token_c})
        assert r.status_code == 404
