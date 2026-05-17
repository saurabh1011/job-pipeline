"""Auth database — users, sessions, allowed_emails.

All functions read AUTH_DB_PATH at call time so tests can monkeypatch it.
"""
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", str(ROOT / "auth.db"))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   TEXT PRIMARY KEY,
                google_id TEXT UNIQUE NOT NULL,
                email     TEXT UNIQUE NOT NULL,
                name      TEXT NOT NULL,
                is_admin  INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS allowed_emails (
                email    TEXT PRIMARY KEY,
                added_by TEXT,
                added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state      TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                profile_id TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                is_legacy  INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedules (
                profile_id TEXT PRIMARY KEY REFERENCES profiles(profile_id) ON DELETE CASCADE,
                user_id    TEXT NOT NULL,
                time_1     TEXT,
                time_2     TEXT,
                timezone   TEXT NOT NULL DEFAULT 'UTC',
                enabled    INTEGER NOT NULL DEFAULT 1,
                action     TEXT NOT NULL DEFAULT 'source_and_score',
                updated_at TEXT NOT NULL
            );
        """)


# ── User operations ───────────────────────────────────────────────────────────

def user_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def find_user_by_google_id(google_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
        return dict(row) if row else None


def find_user_by_email(email: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE lower(email)=lower(?)", (email,)
        ).fetchone()
        return dict(row) if row else None


def create_user(google_id: str, email: str, name: str, is_admin: bool = False) -> dict:
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO users (user_id, google_id, email, name, is_admin, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, google_id, email, name, int(is_admin), now),
        )
    return {
        "user_id": user_id, "google_id": google_id, "email": email,
        "name": name, "is_admin": is_admin, "created_at": now,
    }


def list_users() -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT user_id, email, name, is_admin, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def set_admin(user_id: str, is_admin: bool) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET is_admin=? WHERE user_id=?", (int(is_admin), user_id))


# ── Session operations ────────────────────────────────────────────────────────

def create_session(user_id: str, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires_at = (now + timedelta(days=days)).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
            (token, user_id, expires_at, now.isoformat()),
        )
    return token


def validate_session(token: str) -> Optional[dict]:
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        row = c.execute(
            "SELECT s.user_id, u.email, u.name, u.is_admin "
            "FROM sessions s JOIN users u ON s.user_id=u.user_id "
            "WHERE s.token=? AND s.expires_at > ?",
            (token, now),
        ).fetchone()
        if not row:
            return None
        return {
            "user_id": row["user_id"], "email": row["email"],
            "name": row["name"], "is_admin": bool(row["is_admin"]),
        }


def revoke_session(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ── Profile operations ───────────────────────────────────────────────────────

def create_profile(user_id: str, name: str, is_legacy: bool = False) -> dict:
    profile_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO profiles (profile_id, user_id, name, is_legacy, created_at) "
            "VALUES (?,?,?,?,?)",
            (profile_id, user_id, name, int(is_legacy), now),
        )
    return {"profile_id": profile_id, "user_id": user_id, "name": name,
            "is_legacy": bool(is_legacy), "created_at": now}


def list_profiles(user_id: str) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT profile_id, user_id, name, is_legacy, created_at "
            "FROM profiles WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_profile(profile_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT profile_id, user_id, name, is_legacy, created_at "
            "FROM profiles WHERE profile_id=?",
            (profile_id,),
        ).fetchone()
        return dict(row) if row else None


def rename_profile(profile_id: str, name: str) -> None:
    with _conn() as c:
        c.execute("UPDATE profiles SET name=? WHERE profile_id=?", (name, profile_id))


def delete_profile(profile_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))


# ── Schedule operations ───────────────────────────────────────────────────────

def get_schedule(profile_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM schedules WHERE profile_id=?", (profile_id,)).fetchone()
        return dict(row) if row else None


def set_schedule(profile_id: str, user_id: str, time_1: Optional[str],
                 time_2: Optional[str], timezone: str = "UTC",
                 enabled: bool = True, action: str = "source_and_score") -> dict:
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO schedules "
            "(profile_id, user_id, time_1, time_2, timezone, enabled, action, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (profile_id, user_id, time_1, time_2, timezone, int(enabled), action, now),
        )
    return {"profile_id": profile_id, "user_id": user_id, "time_1": time_1,
            "time_2": time_2, "timezone": timezone, "enabled": enabled,
            "action": action, "updated_at": now}


def list_all_schedules() -> list:
    """Return all enabled schedules (used by the scheduler to load jobs)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM schedules WHERE enabled=1"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_schedule(profile_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM schedules WHERE profile_id=?", (profile_id,))


# ── OAuth state operations ────────────────────────────────────────────────────

def save_oauth_state(state: str) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        c.execute("INSERT INTO oauth_states (state, created_at) VALUES (?,?)", (state, now))


def consume_oauth_state(state: str) -> bool:
    """Verify state exists and is < 10 minutes old, then delete it atomically."""
    cutoff = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM oauth_states WHERE state=? AND created_at > ?", (state, cutoff)
        ).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        return True


# ── Allowlist operations ──────────────────────────────────────────────────────

def is_email_allowed(email: str) -> bool:
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    if admin_email and email.lower() == admin_email.lower():
        return True
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM allowed_emails WHERE lower(email)=lower(?)", (email,)
        ).fetchone()
        return row is not None


def add_allowed_email(email: str, added_by: str = "") -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO allowed_emails (email, added_by, added_at) VALUES (?,?,?)",
            (email.lower(), added_by, now),
        )


def remove_allowed_email(email: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM allowed_emails WHERE lower(email)=lower(?)", (email,))


def list_allowed_emails() -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT email, added_by, added_at FROM allowed_emails ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
