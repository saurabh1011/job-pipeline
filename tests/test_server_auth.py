"""Integration tests for auth and admin endpoints in web/server.py."""
import pytest
import yaml
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

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
    """Patch auth DB, jobs DB, and WEB_API_KEY so auth is enforced."""
    db_path = str(tmp_path / "test.db")
    auth_path = str(tmp_path / "auth.db")

    monkeypatch.setattr(adb, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setenv("WEB_API_KEY", "test-key")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)

    # Re-initialise auth DB with the test path
    adb.init_db()
    # Also point the module-level _auth_db reference
    server_module._auth_db.AUTH_DB_PATH = auth_path

    with TestClient(app) as c:
        yield c


def _make_session(email="user@example.com", name="Test User", is_admin=False) -> str:
    """Create a user + session in the auth DB, return the token."""
    existing = adb.find_user_by_email(email)
    if existing:
        user_id = existing["user_id"]
    else:
        u = adb.create_user(f"gid_{email}", email, name, is_admin=is_admin)
        user_id = u["user_id"]
    return adb.create_session(user_id)


# ── /api/auth/me ─────────────────────────────────────────────────────────────

class TestAuthMe:
    def test_unauthenticated_returns_401(self, auth_setup):
        r = auth_setup.get("/api/auth/me")
        assert r.status_code == 401

    def test_valid_session_returns_user(self, auth_setup):
        token = _make_session("alice@example.com", "Alice")
        r = auth_setup.get("/api/auth/me", cookies={"session_token": token})
        assert r.status_code == 200
        assert r.json()["email"] == "alice@example.com"

    def test_api_key_returns_service_user(self, auth_setup):
        r = auth_setup.get("/api/auth/me", headers={"x-api-key": "test-key"})
        assert r.status_code == 200
        assert r.json()["user_id"] == "service"

    def test_invalid_session_returns_401(self, auth_setup):
        r = auth_setup.get("/api/auth/me", cookies={"session_token": "bad-token"})
        assert r.status_code == 401

    def test_dev_mode_open_access(self, tmp_path, monkeypatch, cfg_dir):
        """Without WEB_API_KEY configured, all requests are allowed (dev mode)."""
        monkeypatch.setattr(adb, "AUTH_DB_PATH", str(tmp_path / "auth.db"))
        monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
        monkeypatch.setattr(server_module, "DB_PATH", str(tmp_path / "db.db"))
        monkeypatch.delenv("WEB_API_KEY", raising=False)
        adb.init_db()
        with TestClient(app) as c:
            r = c.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["user_id"] == "dev"


# ── /api/auth/logout ─────────────────────────────────────────────────────────

class TestAuthLogout:
    def test_logout_revokes_session(self, auth_setup):
        token = _make_session("bob@example.com", "Bob")
        # Confirm session is valid
        r = auth_setup.get("/api/auth/me", cookies={"session_token": token})
        assert r.status_code == 200
        # Logout
        auth_setup.post("/api/auth/logout", cookies={"session_token": token})
        # Session should now be invalid
        r = auth_setup.get("/api/auth/me", cookies={"session_token": token})
        assert r.status_code == 401

    def test_logout_clears_cookie(self, auth_setup):
        token = _make_session("carol@example.com", "Carol")
        r = auth_setup.post("/api/auth/logout", cookies={"session_token": token})
        # Should redirect to /
        assert r.status_code in (200, 302, 307)


# ── Google OAuth callback ─────────────────────────────────────────────────────

def _fake_httpx_client(email, google_id, name="Test"):
    """Async context manager mock for httpx.AsyncClient.

    First call (POST /token) returns access_token.
    Second call (GET /userinfo) returns user info.
    """
    class FakeResp:
        def __init__(self, data):
            self._data = data
        def json(self):
            return self._data

    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResp({"access_token": "fake_token"})
        async def get(self, *args, **kwargs):
            return FakeResp({"sub": google_id, "email": email, "name": name})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    return patch("web.server.httpx.AsyncClient", side_effect=lambda: FakeClient())


class TestOAuthCallback:
    def _call_callback(self, client, email, google_id, name="Test"):
        """Save a real state token then call the callback endpoint with mocked httpx."""
        adb.save_oauth_state("test-state")
        with _fake_httpx_client(email, google_id, name):
            return client.get(
                "/api/auth/google/callback",
                params={"code": "fake_code", "state": "test-state"},
            )

    def test_callback_creates_first_user_as_admin(self, auth_setup):
        r = self._call_callback(auth_setup, "firstuser@example.com", "gid_first")
        assert r.status_code in (200, 302, 307)
        user = adb.find_user_by_email("firstuser@example.com")
        assert user is not None
        assert bool(user["is_admin"])

    def test_callback_rejects_non_allowed_email(self, auth_setup):
        adb.create_user("gid_existing", "existing@x.com", "Existing", is_admin=True)
        adb.save_oauth_state("test-state")
        with _fake_httpx_client("stranger@example.com", "gid_stranger"):
            r = auth_setup.get(
                "/api/auth/google/callback",
                params={"code": "fake_code", "state": "test-state"},
            )
        location = r.headers.get("location", str(r.url))
        assert "not_allowed" in location

    def test_callback_allows_email_on_allowlist(self, auth_setup):
        adb.create_user("gid_a", "admin@x.com", "Admin", is_admin=True)
        adb.add_allowed_email("invited@example.com", added_by="admin@x.com")
        self._call_callback(auth_setup, "invited@example.com", "gid_inv")
        user = adb.find_user_by_email("invited@example.com")
        assert user is not None

    def test_callback_sets_session_cookie(self, auth_setup):
        r = self._call_callback(auth_setup, "newuser@example.com", "gid_new")
        # Cookie may be on the redirect response or carried in the client jar
        set_cookie = r.headers.get("set-cookie", "")
        assert "session_token" in set_cookie or "session_token" in auth_setup.cookies

    def test_existing_user_can_log_in_again(self, auth_setup):
        adb.create_user("gid_ret", "returning@x.com", "Returning", is_admin=True)
        r = self._call_callback(auth_setup, "returning@x.com", "gid_ret")
        assert r.status_code in (200, 302, 307)
        assert adb.user_count() == 1  # no duplicate created

    def test_callback_missing_code_returns_error(self, auth_setup):
        r = auth_setup.get("/api/auth/google/callback", params={"error": "access_denied"})
        location = r.headers.get("location", str(r.url))
        assert "auth_error" in location

    def test_callback_invalid_state_returns_error(self, auth_setup):
        with _fake_httpx_client("x@x.com", "gid_x"):
            r = auth_setup.get(
                "/api/auth/google/callback",
                params={"code": "fake_code", "state": "bad-state"},
            )
        location = r.headers.get("location", str(r.url))
        assert "auth_error" in location


# ── New-user email notification ───────────────────────────────────────────────

class TestNewUserNotification:
    def _call_callback(self, client, email, google_id, name="Test"):
        adb.save_oauth_state("test-state")
        with _fake_httpx_client(email, google_id, name):
            return client.get(
                "/api/auth/google/callback",
                params={"code": "fake_code", "state": "test-state"},
            )

    def test_notification_sent_for_new_user(self, auth_setup, monkeypatch):
        monkeypatch.setenv("SMTP_USER", "sender@gmail.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("ALERT_EMAIL", "admin@example.com")
        with patch("web.server.smtplib.SMTP_SSL") as mock_ssl:
            mock_ctx = MagicMock()
            mock_ssl.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ssl.return_value.__exit__ = MagicMock(return_value=False)
            self._call_callback(auth_setup, "newuser@example.com", "gid_nu")
            import time; time.sleep(0.1)  # let daemon thread run
        mock_ssl.assert_called_once()
        msg = mock_ctx.send_message.call_args[0][0]
        assert "newuser@example.com" in str(msg)

    def test_notification_not_sent_for_existing_user(self, auth_setup, monkeypatch):
        adb.create_user("gid_ret2", "returning@x.com", "Returning", is_admin=True)
        monkeypatch.setenv("SMTP_USER", "sender@gmail.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("ALERT_EMAIL", "admin@example.com")
        with patch("web.server.smtplib.SMTP_SSL") as mock_ssl:
            mock_ctx = MagicMock()
            mock_ssl.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ssl.return_value.__exit__ = MagicMock(return_value=False)
            self._call_callback(auth_setup, "returning@x.com", "gid_ret2")
            import time; time.sleep(0.1)
        mock_ssl.assert_not_called()

    def test_smtp_failure_does_not_block_login(self, auth_setup, monkeypatch):
        monkeypatch.setenv("SMTP_USER", "sender@gmail.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("ALERT_EMAIL", "admin@example.com")
        with patch("web.server.smtplib.SMTP_SSL", side_effect=Exception("SMTP down")):
            r = self._call_callback(auth_setup, "newuser2@example.com", "gid_nu2")
        assert r.status_code in (200, 302, 307)

    def test_notification_skipped_when_smtp_not_configured(self, auth_setup, monkeypatch):
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        monkeypatch.delenv("ALERT_EMAIL", raising=False)
        with patch("web.server.smtplib.SMTP_SSL") as mock_ssl:
            self._call_callback(auth_setup, "newuser3@example.com", "gid_nu3")
            import time; time.sleep(0.1)
        mock_ssl.assert_not_called()


# ── Admin endpoints ───────────────────────────────────────────────────────────

class TestAdminEndpoints:
    def test_admin_list_emails_requires_admin(self, auth_setup):
        token = _make_session("nonadmin@x.com", is_admin=False)
        r = auth_setup.get("/api/admin/allowed-emails",
                           cookies={"session_token": token})
        assert r.status_code == 403

    def test_admin_list_emails(self, auth_setup):
        token = _make_session("admin@x.com", is_admin=True)
        adb.add_allowed_email("test@x.com", added_by="admin@x.com")
        r = auth_setup.get("/api/admin/allowed-emails",
                           cookies={"session_token": token})
        assert r.status_code == 200
        emails = [e["email"] for e in r.json()]
        assert "test@x.com" in emails

    def test_admin_add_email(self, auth_setup):
        token = _make_session("admin2@x.com", is_admin=True)
        r = auth_setup.post("/api/admin/allowed-emails",
                            json={"email": "newbie@x.com"},
                            cookies={"session_token": token})
        assert r.status_code == 200
        assert adb.is_email_allowed("newbie@x.com")

    def test_admin_remove_email(self, auth_setup):
        token = _make_session("admin3@x.com", is_admin=True)
        adb.add_allowed_email("todelete@x.com")
        r = auth_setup.delete("/api/admin/allowed-emails/todelete%40x.com",
                              cookies={"session_token": token})
        assert r.status_code == 200
        assert not adb.is_email_allowed("todelete@x.com")

    def test_admin_list_users(self, auth_setup):
        token = _make_session("admin4@x.com", is_admin=True)
        r = auth_setup.get("/api/admin/users", cookies={"session_token": token})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_nonadmin_cannot_list_users(self, auth_setup):
        token = _make_session("plain@x.com", is_admin=False)
        r = auth_setup.get("/api/admin/users", cookies={"session_token": token})
        assert r.status_code == 403

    def test_admin_cannot_remove_own_admin(self, auth_setup):
        token = _make_session("selfadmin@x.com", is_admin=True)
        user = adb.find_user_by_email("selfadmin@x.com")
        r = auth_setup.patch(f"/api/admin/users/{user['user_id']}",
                             json={"is_admin": False},
                             cookies={"session_token": token})
        assert r.status_code == 400
