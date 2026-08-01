"""Integration tests for /api/profiles/{id}/schedule endpoints."""
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


def _make_session(email="u@x.com", name="U", is_admin=False):
    existing = adb.find_user_by_email(email)
    uid = existing["user_id"] if existing else adb.create_user(f"gid_{email}", email, name, is_admin=is_admin)["user_id"]
    return adb.create_session(uid), adb.find_user_by_email(email)["user_id"]


class TestScheduleEndpoints:
    def _make_profile(self, user_id, name="P"):
        return adb.create_profile(user_id, name)

    def test_get_schedule_no_schedule_returns_defaults(self, auth_setup):
        token, uid = _make_session("a@x.com")
        p = self._make_profile(uid)
        r = auth_setup.get(f"/api/profiles/{p['profile_id']}/schedule",
                           cookies={"session_token": token})
        assert r.status_code == 200
        data = r.json()
        assert data["time_1"] is None
        assert data["enabled"] is False

    def test_set_schedule(self, auth_setup, monkeypatch):
        monkeypatch.setattr(server_module, "_reload_scheduler", lambda: None)
        token, uid = _make_session("b@x.com")
        p = self._make_profile(uid)
        r = auth_setup.put(
            f"/api/profiles/{p['profile_id']}/schedule",
            json={"time_1": "09:00", "time_2": "18:00", "timezone": "America/New_York", "enabled": True},
            cookies={"session_token": token},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["time_1"] == "09:00"
        assert data["time_2"] == "18:00"
        assert data["timezone"] == "America/New_York"

    def test_set_schedule_persisted(self, auth_setup, monkeypatch):
        monkeypatch.setattr(server_module, "_reload_scheduler", lambda: None)
        token, uid = _make_session("c@x.com")
        p = self._make_profile(uid)
        auth_setup.put(
            f"/api/profiles/{p['profile_id']}/schedule",
            json={"time_1": "07:30", "enabled": True},
            cookies={"session_token": token},
        )
        s = adb.get_schedule(p["profile_id"])
        assert s["time_1"] == "07:30"

    def test_delete_schedule(self, auth_setup, monkeypatch):
        monkeypatch.setattr(server_module, "_reload_scheduler", lambda: None)
        token, uid = _make_session("d@x.com")
        p = self._make_profile(uid)
        adb.set_schedule(p["profile_id"], uid, "09:00", None)
        r = auth_setup.delete(f"/api/profiles/{p['profile_id']}/schedule",
                              cookies={"session_token": token})
        assert r.status_code == 200
        assert adb.get_schedule(p["profile_id"]) is None

    def test_cannot_get_schedule_for_other_users_profile(self, auth_setup):
        token, uid = _make_session("e@x.com")
        uid2 = adb.create_user("gid_f", "f@x.com", "F")["user_id"]
        p = self._make_profile(uid2)
        r = auth_setup.get(f"/api/profiles/{p['profile_id']}/schedule",
                           cookies={"session_token": token})
        assert r.status_code == 404

    def test_cannot_set_schedule_for_other_users_profile(self, auth_setup, monkeypatch):
        monkeypatch.setattr(server_module, "_reload_scheduler", lambda: None)
        token, uid = _make_session("g@x.com")
        uid2 = adb.create_user("gid_h", "h@x.com", "H")["user_id"]
        p = self._make_profile(uid2)
        r = auth_setup.put(
            f"/api/profiles/{p['profile_id']}/schedule",
            json={"time_1": "09:00", "enabled": True},
            cookies={"session_token": token},
        )
        assert r.status_code == 404

    def test_reload_scheduler_noop_in_local_mode(self, auth_setup, monkeypatch):
        """APP_MODE=local installs are cron-driven; the in-process APScheduler
        must stay inert even when a schedule is set, to avoid duplicate runs."""
        monkeypatch.setattr(server_module, "APP_MODE", "local")
        token, uid = _make_session("local-mode@x.com")
        p = self._make_profile(uid)
        auth_setup.put(
            f"/api/profiles/{p['profile_id']}/schedule",
            json={"time_1": "09:00", "time_2": "18:00", "enabled": True},
            cookies={"session_token": token},
        )
        assert server_module._scheduler.get_jobs() == []

    def test_reload_scheduler_schedules_job_in_remote_mode(self, auth_setup):
        assert server_module.APP_MODE == "remote"
        token, uid = _make_session("remote-mode@x.com")
        p = self._make_profile(uid)
        auth_setup.put(
            f"/api/profiles/{p['profile_id']}/schedule",
            json={"time_1": "09:00", "time_2": "18:00", "enabled": True},
            cookies={"session_token": token},
        )
        job_ids = {j.id for j in server_module._scheduler.get_jobs()}
        assert f"{p['profile_id']}_time_1" in job_ids
        assert f"{p['profile_id']}_time_2" in job_ids
