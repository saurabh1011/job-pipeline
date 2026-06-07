"""SQLite-backed job store.

Input:  job dicts with keys: job_id, company, title, location, url,
        description, apply_url
Output: job dicts with all input keys plus: status, match_score,
        match_summary, match_strengths, match_gaps, date_seen
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional


class JobStatus:
    NEW = "new"
    ALERTED = "alerted"
    APPROVED = "approved"
    APPLIED = "applied"
    SKIPPED = "skipped"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    OFFER = "offer"
    INTERESTING = "interesting"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company          TEXT NOT NULL,
    job_id           TEXT NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    apply_url        TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'new',
    match_score      INTEGER,
    match_summary    TEXT,
    score_attempted  INTEGER NOT NULL DEFAULT 0,
    date_seen        TEXT NOT NULL,
    PRIMARY KEY (company, job_id)
);
"""

_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    action          TEXT NOT NULL DEFAULT '',
    group_type      TEXT NOT NULL DEFAULT 'all',
    companies_count INTEGER NOT NULL DEFAULT 0,
    jobs_fetched    INTEGER NOT NULL DEFAULT 0,
    jobs_new        INTEGER NOT NULL DEFAULT 0,
    jobs_scored     INTEGER NOT NULL DEFAULT 0,
    jobs_generated  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    error_msg       TEXT
);
"""

_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',
    logs            TEXT NOT NULL DEFAULT '[]',
    result          TEXT,
    started_at      TEXT,
    ended_at        TEXT,
    created_at      TEXT NOT NULL
);
"""

_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_submissions (
    id                  TEXT PRIMARY KEY,
    user_email          TEXT,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    category            TEXT NOT NULL,
    github_issue_number INTEGER,
    github_issue_url    TEXT,
    app_version         TEXT,
    ip_address          TEXT,
    submitted_at        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
);
"""

_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN score_attempted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN match_strengths TEXT",
    "ALTER TABLE jobs ADD COLUMN match_gaps TEXT",
    "ALTER TABLE jobs ADD COLUMN match_requirements TEXT",
    "ALTER TABLE jobs ADD COLUMN match_resume_suggestions TEXT",
    "ALTER TABLE jobs ADD COLUMN date_posted TEXT",
    "ALTER TABLE jobs ADD COLUMN date_last_sourced TEXT",
]


class JobStore:
    def __init__(self, db_path: str = "jobs.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.executescript(_RUNS_SCHEMA)
        self._conn.executescript(_TASKS_SCHEMA)
        self._conn.executescript(_FEEDBACK_SCHEMA)
        self._apply_migrations()
        self._conn.commit()

    def _apply_migrations(self):
        for sql in _MIGRATIONS:
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists

    def close(self):
        self._conn.close()

    def upsert_job(self, job: dict) -> bool:
        """Insert job if not seen before; update sourcing timestamps if already exists.

        Returns True if new, False if duplicate.
        date_last_sourced is always updated to now on re-fetch.
        date_posted uses COALESCE so an existing value is never overwritten.
        """
        existing = self.get_job(job["company"], job["job_id"])
        now = datetime.now(timezone.utc).isoformat()
        if existing is not None:
            self._conn.execute(
                """UPDATE jobs
                   SET date_last_sourced = ?,
                       date_posted = COALESCE(date_posted, ?)
                   WHERE company = ? AND job_id = ?""",
                (now, job.get("date_posted"), job["company"], job["job_id"]),
            )
            self._conn.commit()
            return False
        self._conn.execute(
            """
            INSERT INTO jobs (company, job_id, title, location, url, apply_url,
                              description, status, date_seen, date_last_sourced, date_posted)
            VALUES (:company, :job_id, :title, :location, :url, :apply_url,
                    :description, 'new', :date_seen, :date_seen, :date_posted)
            """,
            {**job, "date_seen": now, "date_posted": job.get("date_posted")},
        )
        self._conn.commit()
        return True

    def get_job(self, company: str, job_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE company = ? AND job_id = ?",
            (company, job_id),
        ).fetchone()
        return dict(row) if row else None

    def update_status(self, company: str, job_id: str, status: str):
        self._conn.execute(
            "UPDATE jobs SET status = ? WHERE company = ? AND job_id = ?",
            (status, company, job_id),
        )
        self._conn.commit()

    def set_match_score(
        self,
        company: str,
        job_id: str,
        score: int,
        summary: str,
        strengths: Optional[List[str]] = None,
        gaps: Optional[List[str]] = None,
    ):
        self._conn.execute(
            """UPDATE jobs
               SET match_score = ?, match_summary = ?, match_strengths = ?, match_gaps = ?
               WHERE company = ? AND job_id = ?""",
            (
                score,
                summary,
                json.dumps(strengths or []),
                json.dumps(gaps or []),
                company,
                job_id,
            ),
        )
        self._conn.commit()

    def set_analysis(
        self,
        company: str,
        job_id: str,
        requirements: list,
        resume_suggestions: list,
    ):
        self._conn.execute(
            """UPDATE jobs
               SET match_requirements = ?, match_resume_suggestions = ?
               WHERE company = ? AND job_id = ?""",
            (
                json.dumps(requirements),
                json.dumps(resume_suggestions),
                company,
                job_id,
            ),
        )
        self._conn.commit()

    def start_run(self, action: str, group_type: str, companies_count: int) -> int:
        """Insert a new run record with status=running. Returns the run id."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO pipeline_runs (started_at, action, group_type, companies_count, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (now, action, group_type, companies_count),
        )
        self._conn.commit()
        return cur.lastrowid

    def finish_run(
        self,
        run_id: int,
        jobs_fetched: int = 0,
        jobs_new: int = 0,
        jobs_scored: int = 0,
        jobs_generated: int = 0,
        status: str = "done",
        error_msg: Optional[str] = None,
    ):
        """Update run record with final stats and status."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE pipeline_runs
               SET ended_at = ?, jobs_fetched = ?, jobs_new = ?, jobs_scored = ?,
                   jobs_generated = ?, status = ?, error_msg = ?
               WHERE id = ?""",
            (now, jobs_fetched, jobs_new, jobs_scored, jobs_generated, status, error_msg, run_id),
        )
        self._conn.commit()

    def list_runs(self, limit: int = 20) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_jobs_by_status(self, status: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY date_seen DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_scored(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE match_score IS NOT NULL"
        ).fetchone()
        return row[0]

    def list_all_jobs(self) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY date_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def save_task(self, task_id: str, status: str, logs: List[str], result=None, started_at=None, ended_at=None):
        """Persist task state to database."""
        self._conn.execute(
            """INSERT OR REPLACE INTO tasks (id, status, logs, result, started_at, ended_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, status, json.dumps(logs), json.dumps(result) if result else None,
             started_at, ended_at, datetime.now(timezone.utc).isoformat())
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> Optional[dict]:
        """Retrieve task from database."""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,)
        ).fetchone()
        if not row:
            return None
        task = dict(row)
        # Parse JSON fields
        if task['logs']:
            task['logs'] = json.loads(task['logs'])
        if task['result']:
            task['result'] = json.loads(task['result'])
        return task

    def save_feedback(self, submission_id: str, user_email: Optional[str], title: str,
                     description: str, category: str, ip_address: str,
                     app_version: str = "1.0.0") -> None:
        """Save feedback submission to database.

        Args:
            submission_id: Unique feedback submission ID
            user_email: Optional email for follow-up
            title: Feedback title
            description: Feedback description
            category: Category (bug, feature, feedback)
            ip_address: Submitter IP address
            app_version: App version at time of submission
        """
        self._conn.execute(
            """INSERT INTO feedback_submissions
               (id, user_email, title, description, category, ip_address, app_version, submitted_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (submission_id, user_email, title, description, category, ip_address, app_version,
             datetime.now(timezone.utc).isoformat(), "pending")
        )
        self._conn.commit()

    def get_feedback(self, submission_id: str) -> Optional[dict]:
        """Retrieve feedback submission from database.

        Args:
            submission_id: Feedback submission ID

        Returns:
            Feedback dict or None if not found
        """
        row = self._conn.execute(
            "SELECT * FROM feedback_submissions WHERE id = ?",
            (submission_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_feedback_status(self, submission_id: str, status: str,
                              github_issue_number: Optional[int] = None,
                              github_issue_url: Optional[str] = None) -> None:
        """Update feedback submission status.

        Args:
            submission_id: Feedback submission ID
            status: New status (pending, created, failed)
            github_issue_number: GitHub issue number if created
            github_issue_url: GitHub issue URL if created
        """
        self._conn.execute(
            """UPDATE feedback_submissions
               SET status = ?, github_issue_number = ?, github_issue_url = ?
               WHERE id = ?""",
            (status, github_issue_number, github_issue_url, submission_id)
        )
        self._conn.commit()

    def close(self):
        """Close database connection."""
        self._conn.close()
