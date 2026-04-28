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

_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN score_attempted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN match_strengths TEXT",
    "ALTER TABLE jobs ADD COLUMN match_gaps TEXT",
]


class JobStore:
    def __init__(self, db_path: str = "jobs.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
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
        """Insert job if not seen before. Returns True if new, False if duplicate."""
        existing = self.get_job(job["company"], job["job_id"])
        if existing is not None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO jobs (company, job_id, title, location, url, apply_url,
                              description, status, date_seen)
            VALUES (:company, :job_id, :title, :location, :url, :apply_url,
                    :description, 'new', :date_seen)
            """,
            {**job, "date_seen": now},
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
