"""Unit tests for web/auth_db.py."""
import os
import pytest
import web.auth_db as adb


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(adb, "AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    adb.init_db()


# ── User operations ───────────────────────────────────────────────────────────

class TestUsers:
    def test_user_count_starts_at_zero(self):
        assert adb.user_count() == 0

    def test_create_and_find_user(self):
        u = adb.create_user("google_1", "alice@example.com", "Alice")
        assert u["user_id"]
        assert u["email"] == "alice@example.com"
        assert u["is_admin"] is False

    def test_find_user_by_google_id(self):
        adb.create_user("gid_1", "bob@example.com", "Bob")
        u = adb.find_user_by_google_id("gid_1")
        assert u["email"] == "bob@example.com"

    def test_find_user_by_google_id_missing(self):
        assert adb.find_user_by_google_id("nope") is None

    def test_find_user_by_email_case_insensitive(self):
        adb.create_user("gid_2", "carol@example.com", "Carol")
        u = adb.find_user_by_email("CAROL@EXAMPLE.COM")
        assert u is not None
        assert u["email"] == "carol@example.com"

    def test_create_admin_user(self):
        u = adb.create_user("gid_3", "admin@example.com", "Admin", is_admin=True)
        assert u["is_admin"] is True

    def test_set_admin(self):
        u = adb.create_user("gid_4", "dave@example.com", "Dave")
        adb.set_admin(u["user_id"], True)
        found = adb.find_user_by_google_id("gid_4")
        assert bool(found["is_admin"])

    def test_list_users(self):
        adb.create_user("g1", "a@x.com", "A")
        adb.create_user("g2", "b@x.com", "B")
        users = adb.list_users()
        assert len(users) == 2

    def test_user_count_increments(self):
        adb.create_user("g1", "a@x.com", "A")
        adb.create_user("g2", "b@x.com", "B")
        assert adb.user_count() == 2


# ── Session operations ────────────────────────────────────────────────────────

class TestSessions:
    def test_create_and_validate_session(self):
        u = adb.create_user("gid_s1", "e@x.com", "E")
        token = adb.create_session(u["user_id"])
        assert token
        session = adb.validate_session(token)
        assert session is not None
        assert session["email"] == "e@x.com"
        assert session["user_id"] == u["user_id"]

    def test_validate_returns_is_admin(self):
        u = adb.create_user("gid_s2", "f@x.com", "F", is_admin=True)
        token = adb.create_session(u["user_id"])
        session = adb.validate_session(token)
        assert session["is_admin"] is True

    def test_invalid_token_returns_none(self):
        assert adb.validate_session("fake-token") is None

    def test_revoke_session(self):
        u = adb.create_user("gid_s3", "g@x.com", "G")
        token = adb.create_session(u["user_id"])
        adb.revoke_session(token)
        assert adb.validate_session(token) is None

    def test_revoke_nonexistent_session_is_safe(self):
        adb.revoke_session("does-not-exist")  # should not raise


# ── Allowlist operations ──────────────────────────────────────────────────────

class TestAllowlist:
    def test_email_not_allowed_by_default(self):
        assert adb.is_email_allowed("new@example.com") is False

    def test_add_and_check_email(self):
        adb.add_allowed_email("allowed@example.com", added_by="admin")
        assert adb.is_email_allowed("allowed@example.com") is True

    def test_email_check_is_case_insensitive(self):
        adb.add_allowed_email("mixed@example.com")
        assert adb.is_email_allowed("MIXED@EXAMPLE.COM") is True

    def test_remove_allowed_email(self):
        adb.add_allowed_email("remove@example.com")
        adb.remove_allowed_email("remove@example.com")
        assert adb.is_email_allowed("remove@example.com") is False

    def test_list_allowed_emails(self):
        adb.add_allowed_email("a@x.com", added_by="admin")
        adb.add_allowed_email("b@x.com", added_by="admin")
        emails = adb.list_allowed_emails()
        assert len(emails) == 2

    def test_add_duplicate_is_idempotent(self):
        adb.add_allowed_email("dup@x.com")
        adb.add_allowed_email("dup@x.com")
        assert len(adb.list_allowed_emails()) == 1

    def test_admin_email_env_var_bypasses_allowlist(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "superadmin@example.com")
        assert adb.is_email_allowed("superadmin@example.com") is True
        assert adb.is_email_allowed("other@example.com") is False


# ── Profile operations ───────────────────────────────────────────────────────

class TestProfiles:
    def _user(self, suffix="a"):
        u = adb.create_user(f"gid_{suffix}", f"{suffix}@x.com", suffix.title())
        return u["user_id"]

    def test_create_and_get_profile(self):
        uid = self._user()
        p = adb.create_profile(uid, "EM Roles")
        assert p["profile_id"]
        assert p["name"] == "EM Roles"
        assert p["is_legacy"] is False
        found = adb.get_profile(p["profile_id"])
        assert found["name"] == "EM Roles"

    def test_create_legacy_profile(self):
        uid = self._user("b")
        p = adb.create_profile(uid, "Default", is_legacy=True)
        assert p["is_legacy"] is True

    def test_list_profiles_ordered_by_created_at(self):
        uid = self._user("c")
        adb.create_profile(uid, "First")
        adb.create_profile(uid, "Second")
        profiles = adb.list_profiles(uid)
        assert len(profiles) == 2
        assert profiles[0]["name"] == "First"

    def test_list_profiles_empty_for_new_user(self):
        uid = self._user("d")
        assert adb.list_profiles(uid) == []

    def test_rename_profile(self):
        uid = self._user("e")
        p = adb.create_profile(uid, "Old Name")
        adb.rename_profile(p["profile_id"], "New Name")
        assert adb.get_profile(p["profile_id"])["name"] == "New Name"

    def test_delete_profile(self):
        uid = self._user("f")
        p = adb.create_profile(uid, "To Delete")
        adb.delete_profile(p["profile_id"])
        assert adb.get_profile(p["profile_id"]) is None

    def test_get_profile_missing_returns_none(self):
        assert adb.get_profile("no-such-id") is None

    def test_profiles_isolated_per_user(self):
        uid1 = self._user("g")
        uid2 = self._user("h")
        adb.create_profile(uid1, "P1")
        assert adb.list_profiles(uid2) == []

    def test_profiles_isolated_per_user_multi(self):
        uid1 = self._user("j")
        uid2 = self._user("k")
        adb.create_profile(uid1, "J Profile")
        adb.create_profile(uid2, "K Profile")
        assert len(adb.list_profiles(uid1)) == 1
        assert adb.list_profiles(uid1)[0]["name"] == "J Profile"


# ── Schedule operations ───────────────────────────────────────────────────────

class TestSchedules:
    def _profile(self, suffix="s"):
        u = adb.create_user(f"gid_sch_{suffix}", f"sch_{suffix}@x.com", suffix)
        p = adb.create_profile(u["user_id"], "Profile")
        return u["user_id"], p["profile_id"]

    def test_get_missing_schedule_returns_none(self):
        uid, pid = self._profile("a")
        assert adb.get_schedule(pid) is None

    def test_set_and_get_schedule(self):
        uid, pid = self._profile("b")
        adb.set_schedule(pid, uid, "09:00", "18:00", timezone="America/New_York")
        s = adb.get_schedule(pid)
        assert s["time_1"] == "09:00"
        assert s["time_2"] == "18:00"
        assert s["timezone"] == "America/New_York"
        assert bool(s["enabled"])

    def test_set_schedule_upserts(self):
        uid, pid = self._profile("c")
        adb.set_schedule(pid, uid, "09:00", None)
        adb.set_schedule(pid, uid, "10:00", "20:00")
        s = adb.get_schedule(pid)
        assert s["time_1"] == "10:00"
        assert s["time_2"] == "20:00"

    def test_delete_schedule(self):
        uid, pid = self._profile("d")
        adb.set_schedule(pid, uid, "08:00", None)
        adb.delete_schedule(pid)
        assert adb.get_schedule(pid) is None

    def test_list_all_schedules_returns_enabled_only(self):
        uid, pid1 = self._profile("e")
        uid2, pid2 = (lambda u, p: (u, p))(*self._profile("f"))
        adb.set_schedule(pid1, uid, "09:00", None, enabled=True)
        adb.set_schedule(pid2, uid2, "10:00", None, enabled=False)
        schedules = adb.list_all_schedules()
        ids = [s["profile_id"] for s in schedules]
        assert pid1 in ids
        assert pid2 not in ids

    def test_disabled_schedule_not_in_list(self):
        uid, pid = self._profile("g")
        adb.set_schedule(pid, uid, "09:00", None, enabled=False)
        assert not any(s["profile_id"] == pid for s in adb.list_all_schedules())


# ── OAuth state operations ────────────────────────────────────────────────────

class TestOAuthState:
    def test_consume_valid_state(self):
        adb.save_oauth_state("abc123")
        assert adb.consume_oauth_state("abc123") is True

    def test_consume_removes_state(self):
        adb.save_oauth_state("once")
        adb.consume_oauth_state("once")
        assert adb.consume_oauth_state("once") is False

    def test_consume_unknown_state(self):
        assert adb.consume_oauth_state("does-not-exist") is False

    def test_consume_expired_state(self, monkeypatch):
        from datetime import datetime, timedelta
        old_time = (datetime.utcnow() - timedelta(minutes=11)).isoformat()
        import sqlite3
        import web.auth_db as _adb
        with sqlite3.connect(_adb.AUTH_DB_PATH) as c:
            c.execute("INSERT INTO oauth_states (state, created_at) VALUES (?,?)",
                      ("expired-state", old_time))
        assert adb.consume_oauth_state("expired-state") is False
