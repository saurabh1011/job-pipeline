"""Pipeline run checkpoint — persists intermediate results so a crashed run
can resume without re-fetching or re-scoring jobs.

Lifecycle:
    - Created when jobs are fetched (start of a new run)
    - Updated per-job after scoring and generation
    - Kept on disk after completion for debugging
    - Deleted at the start of the next run if the previous run completed

File format: JSON, written atomically via a .tmp rename to avoid corruption.

Schema:
    {
        "run_started": "2026-04-14T21:34:26",
        "fetched_jobs": [...],
        "job_results": {
            "Company/job_id": {
                "is_new":          bool,
                "scored":          bool,
                "adjusted_score":  int | null,
                "summary":         str | null,
                "meets_threshold": bool | null,
                "generated":       bool
            }
        },
        "alert_sent": bool
    }
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "pipeline_checkpoint.json"


class RunCheckpoint:
    """Manages a single pipeline run checkpoint file."""

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = path
        self._tmp_path = path + ".tmp"
        self.data = self._load()

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def is_resumable(self) -> bool:
        """True if a previous incomplete run exists and can be resumed."""
        return (
            os.path.exists(self.path)
            and self.data.get("fetched_jobs") is not None
            and not self.data.get("alert_sent", False)
        )

    @property
    def alert_sent(self) -> bool:
        return self.data.get("alert_sent", False)

    def get_fetched_jobs(self) -> Optional[list]:
        return self.data.get("fetched_jobs")

    def set_fetched_jobs(self, jobs: list) -> None:
        self.data["fetched_jobs"] = jobs
        self._save()

    def get_job_result(self, company: str, job_id: str) -> Optional[dict]:
        return self.data["job_results"].get(_key(company, job_id))

    def set_job_scored(
        self,
        company: str,
        job_id: str,
        is_new: bool,
        adjusted_score: int,
        summary: str,
        meets_threshold: bool,
    ) -> None:
        self.data["job_results"][_key(company, job_id)] = {
            "is_new": is_new,
            "scored": True,
            "adjusted_score": adjusted_score,
            "summary": summary,
            "meets_threshold": meets_threshold,
            "generated": False,
        }
        self._save()

    def set_job_generated(self, company: str, job_id: str) -> None:
        key = _key(company, job_id)
        if key in self.data["job_results"]:
            self.data["job_results"][key]["generated"] = True
            self._save()

    def set_job_skipped(self, company: str, job_id: str, is_new: bool) -> None:
        """Record a job that was new but scored below threshold (or was a duplicate)."""
        self.data["job_results"][_key(company, job_id)] = {
            "is_new": is_new,
            "scored": True,
            "adjusted_score": None,
            "summary": None,
            "meets_threshold": False,
            "generated": False,
        }
        self._save()

    def mark_alert_sent(self) -> None:
        self.data["alert_sent"] = True
        self._save()
        logger.info("Checkpoint: run complete, file kept at %s for debugging", self.path)

    def delete(self) -> None:
        """Remove the checkpoint file (called at start of next fresh run)."""
        for path in (self.path, self._tmp_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        logger.info("Checkpoint: removed stale completed checkpoint %s", self.path)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    data = json.load(f)
                logger.info(
                    "Checkpoint: loaded existing checkpoint from %s (run started %s)",
                    self.path,
                    data.get("run_started", "unknown"),
                )
                return data
            except Exception as exc:
                logger.warning("Checkpoint: could not load %s (%s), starting fresh", self.path, exc)

        return {
            "run_started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "fetched_jobs": None,
            "job_results": {},
            "alert_sent": False,
        }

    def _save(self) -> None:
        """Atomic write: write to .tmp then rename so a crash mid-write can't corrupt the checkpoint."""
        try:
            with open(self._tmp_path, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(self._tmp_path, self.path)
        except Exception as exc:
            logger.warning("Checkpoint: failed to save %s: %s", self.path, exc)


def _key(company: str, job_id: str) -> str:
    return f"{company}/{job_id}"
