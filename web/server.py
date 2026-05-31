"""FastAPI server for the job application pipeline UI.

Run from project root:
    python3 -m uvicorn web.server:app --reload --port 8000
"""
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

import secrets
from urllib.parse import urlencode

import httpx
import yaml
from fastapi import Cookie, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure project root is on sys.path so pipeline imports work
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from web.auth import require_admin, require_api_key  # noqa: E402
from web.tasks import create_task, get_task           # noqa: E402
from pipeline.store import JobStore, JobStatus        # noqa: E402

DB_PATH    = os.environ.get("DB_PATH",      str(ROOT / "jobs.db"))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR",   str(ROOT / "output"))
CONFIG_DIR = os.environ.get("CONFIG_DIR",   str(ROOT / "config"))
PROFILE_DIR = os.environ.get("PROFILE_DIR", str(ROOT / "profile"))
LOG_DIR    = os.environ.get("LOG_DIR",      str(ROOT / "logs"))
AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", str(ROOT / "auth.db"))
DATA_DIR   = os.environ.get("DATA_DIR",     str(ROOT / "data"))

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Sync versioned config files from the image into the volume on every startup.
# The volume owns jobs.db/output/profile (mutable state); config files are
# versioned in the image and must stay current after deploys.
_IMAGE_CONFIG = ROOT / "config"
for _cfg_file in ["companies.yaml", "preferences.yaml"]:
    _src = _IMAGE_CONFIG / _cfg_file
    _dst = Path(CONFIG_DIR) / _cfg_file
    if _src.exists() and _src != _dst:
        import shutil as _shutil
        _shutil.copy2(str(_src), str(_dst))

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI   = os.environ.get(
    "OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback"
)

_GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

app = FastAPI(title="Job Application Pipeline", version="1.0.0")

# Initialise auth DB on startup
import web.auth_db as _auth_db  # noqa: E402
_auth_db.AUTH_DB_PATH = AUTH_DB_PATH
_auth_db.init_db()

# ── APScheduler setup ─────────────────────────────────────────────────────────
_scheduler = None
try:
    from apscheduler.schedulers.background import BackgroundScheduler as _BgScheduler
    _scheduler = _BgScheduler(timezone="UTC")
except ImportError:
    logger.warning("APScheduler not installed — per-profile scheduling unavailable")


def _scheduler_run_profile(profile_id: str):
    """Called by APScheduler to trigger a pipeline run for one profile."""
    profile = _auth_db.get_profile(profile_id)
    if not profile:
        return
    if profile.get("is_legacy"):
        paths = _legacy_paths()
    else:
        paths = _make_profile_paths(profile["user_id"], profile_id)
    create_task(_do_run, None, None, "source_and_score", paths)


def _reload_scheduler():
    """Sync APScheduler jobs from the schedules DB table."""
    if _scheduler is None:
        return
    _scheduler.remove_all_jobs()
    for sched in _auth_db.list_all_schedules():
        tz = sched.get("timezone") or "UTC"
        for slot in ("time_1", "time_2"):
            hhmm = sched.get(slot)
            if not hhmm:
                continue
            try:
                hour, minute = (int(x) for x in hhmm.split(":"))
            except ValueError:
                continue
            from apscheduler.triggers.cron import CronTrigger
            _scheduler.add_job(
                _scheduler_run_profile,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                args=[sched["profile_id"]],
                id=f"{sched['profile_id']}_{slot}",
                replace_existing=True,
            )


# On startup, mark any runs left in "running" state as "error" — they were
# orphaned by a previous machine shutdown mid-task.
_startup_store = JobStore(DB_PATH)
_startup_store._conn.execute(
    "UPDATE pipeline_runs SET status='error', error_msg='Server restarted mid-run' "
    "WHERE status='running'"
)
_startup_store._conn.commit()
_startup_store.close()

if _scheduler is not None:
    _reload_scheduler()
    _scheduler.start()


# ── Profile path resolution ───────────────────────────────────────────────────

@dataclass
class ProfilePaths:
    db_path: str
    config_dir: str
    output_dir: str
    resume_dir: str
    log_dir: str
    profile_id: str


def _legacy_paths() -> ProfilePaths:
    """Paths for dev/service users and legacy (is_legacy=True) profiles."""
    return ProfilePaths(
        db_path=DB_PATH,
        config_dir=CONFIG_DIR,
        output_dir=OUTPUT_DIR,
        resume_dir=PROFILE_DIR,
        log_dir=LOG_DIR,
        profile_id="legacy",
    )


def _make_profile_paths(user_id: str, profile_id: str) -> ProfilePaths:
    base = os.path.join(DATA_DIR, "users", user_id, "profiles", profile_id)
    paths = ProfilePaths(
        db_path=os.path.join(base, "jobs.db"),
        config_dir=os.path.join(base, "config"),
        output_dir=os.path.join(base, "output"),
        resume_dir=os.path.join(base, "resume"),
        log_dir=os.path.join(base, "logs"),
        profile_id=profile_id,
    )
    for d in [paths.config_dir, paths.output_dir, paths.resume_dir, paths.log_dir]:
        os.makedirs(d, exist_ok=True)
    # Seed config files from image defaults if not yet present
    for fname in ["companies.yaml", "preferences.yaml"]:
        dst = os.path.join(paths.config_dir, fname)
        src = os.path.join(CONFIG_DIR, fname)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)
    return paths


def get_profile_paths(
    user: dict = Depends(require_api_key),
    active_profile_id: Optional[str] = Cookie(default=None),
) -> ProfilePaths:
    """Resolve data paths for the current user + active profile."""
    if user["user_id"] in ("dev", "service"):
        return _legacy_paths()

    profiles = _auth_db.list_profiles(user["user_id"])
    if not profiles:
        # First login after Phase 2 deploy: create a legacy profile so existing
        # data at the module-level paths continues to work.
        p = _auth_db.create_profile(user["user_id"], "Engineering Manager", is_legacy=True)
        profiles = [p]

    # Pick the active profile (cookie hint), fallback to first
    profile = next(
        (p for p in profiles if p["profile_id"] == active_profile_id),
        profiles[0],
    )

    if profile.get("is_legacy"):
        return _legacy_paths()
    return _make_profile_paths(user["user_id"], profile["profile_id"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_prefs(paths: "ProfilePaths") -> dict:
    """Load preferences from DB, seeding from preferences.yaml on first access."""
    store = JobStore(paths.db_path)
    try:
        prefs = store.get_prefs()
        if prefs is None:
            yaml_path = os.path.join(paths.config_dir, "preferences.yaml")
            if os.path.exists(yaml_path):
                with open(yaml_path) as f:
                    prefs = yaml.safe_load(f) or {}
            else:
                prefs = {}
            store.set_prefs(prefs, changed_by="system:migration")
    finally:
        store.close()
    return prefs


def _deserialize_job(job: dict) -> dict:
    """Parse JSON string fields into Python lists for API responses."""
    for field in ("match_strengths", "match_gaps", "match_requirements", "match_resume_suggestions"):
        val = job.get(field)
        if isinstance(val, str):
            try:
                job[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                job[field] = []
        elif val is None:
            job[field] = []
    return job


def _job_materials(company: str, job_id: str, output_dir: str) -> dict:
    """Return cover letter text, diff text, and pdf path for a job."""
    job_dir = os.path.join(output_dir, f"{company}_{job_id}")
    materials = {"cover_letter": None, "resume_diff": None, "pdf_path": None}

    cl_path = os.path.join(job_dir, "cover_letter.md")
    if os.path.exists(cl_path):
        with open(cl_path) as f:
            materials["cover_letter"] = f.read()

    diff_path = os.path.join(job_dir, "resume_diff.patch")
    if os.path.exists(diff_path):
        with open(diff_path) as f:
            materials["resume_diff"] = f.read()

    pdf_path = os.path.join(job_dir, "cover_letter.pdf")
    if os.path.exists(pdf_path):
        materials["pdf_path"] = f"/output/{company}_{job_id}/cover_letter.pdf"

    return materials


# ── Jobs ─────────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def list_jobs(
    status: Optional[str] = None,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    store = JobStore(paths.db_path)
    try:
        jobs = store.get_jobs_by_status(status) if status else store.list_all_jobs()
        jobs = [_deserialize_job(j) for j in jobs]
        jobs = sorted(jobs, key=lambda j: (j.get("match_score") or 0), reverse=True)
        return {"jobs": jobs}
    finally:
        store.close()


@app.get("/api/jobs/{company}/{job_id}")
def get_job(
    company: str,
    job_id: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    store = JobStore(paths.db_path)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {**_deserialize_job(job), **_job_materials(company, job_id, paths.output_dir)}
    finally:
        store.close()


class StatusUpdate(BaseModel):
    status: str


class JobRef(BaseModel):
    company: str
    job_id: str


class BulkStatusUpdate(BaseModel):
    jobs: List[JobRef]
    status: str


@app.patch("/api/jobs/{company}/{job_id}")
def update_job_status(
    company: str,
    job_id: str,
    body: StatusUpdate,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    allowed = {JobStatus.APPROVED, JobStatus.SKIPPED, JobStatus.APPLIED,
               JobStatus.ALERTED, JobStatus.NEW, JobStatus.INTERVIEWING,
               JobStatus.REJECTED, JobStatus.OFFER, JobStatus.INTERESTING,
               JobStatus.NOT_AVAILABLE}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    store = JobStore(paths.db_path)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        store.update_status(company, job_id, body.status)
        return {"ok": True, "status": body.status}
    finally:
        store.close()


@app.post("/api/jobs/bulk-status")
def bulk_update_status(
    body: BulkStatusUpdate,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    allowed = {JobStatus.APPROVED, JobStatus.SKIPPED, JobStatus.APPLIED,
               JobStatus.ALERTED, JobStatus.NEW, JobStatus.INTERVIEWING,
               JobStatus.REJECTED, JobStatus.OFFER, JobStatus.INTERESTING,
               JobStatus.NOT_AVAILABLE}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    store = JobStore(paths.db_path)
    try:
        updated = 0
        for ref in body.jobs:
            if store.get_job(ref.company, ref.job_id):
                store.update_status(ref.company, ref.job_id, body.status)
                updated += 1
        return {"ok": True, "updated": updated}
    finally:
        store.close()


# ── Per-job actions ───────────────────────────────────────────────────────────

def _do_generate_cover_letter(log, company: str, job_id: str, paths: ProfilePaths):
    from pipeline.profile import ProfileLoader
    from pipeline.generator import ContentGenerator
    from pipeline.llm import create_provider

    prefs = _load_prefs(paths)
    store = JobStore(paths.db_path)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise ValueError(f"Job not found: {company}/{job_id}")
    finally:
        store.close()

    log(f"Loading profile...")
    provider = create_provider(prefs)
    loader = ProfileLoader(
        profile_dir=paths.resume_dir,
        google_docs_links=prefs.get("google_docs_links", []),
        provider=provider,
    )
    profile = loader.load(job=job)

    log(f"Generating cover letter for {company} — {job['title']}...")
    gen = ContentGenerator(provider=provider, output_dir=paths.output_dir)
    result = gen.generate(job, profile)
    log(f"Done → {result.output_dir}/cover_letter.md")
    return {"output_dir": result.output_dir}


@app.post("/api/jobs/{company}/{job_id}/generate-cover-letter")
def generate_cover_letter(
    company: str,
    job_id: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    task_id = create_task(_do_generate_cover_letter, company, job_id, paths)
    return {"task_id": task_id}


def _do_export_cover_letter_pdf(log, company: str, job_id: str, paths: ProfilePaths):
    job_dir = os.path.join(paths.output_dir, f"{company}_{job_id}")
    src = os.path.join(job_dir, "cover_letter.md")
    dst = os.path.join(job_dir, "cover_letter.pdf")
    if not os.path.exists(src):
        raise FileNotFoundError(f"No cover letter at {src}")
    log(f"Exporting {src} → {dst}...")
    result = subprocess.run(
        ["pandoc", src, "-f", "markdown", "-t", "pdf",
         "--pdf-engine=typst", "-V", "mainfont=Carlito", "-V", "fontsize=11pt",
         "-o", dst],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    log(f"PDF saved: {dst}")
    return {"pdf_path": f"/output/{company}_{job_id}/cover_letter.pdf"}


@app.post("/api/jobs/{company}/{job_id}/export-cover-letter-pdf")
def export_cover_letter_pdf(
    company: str,
    job_id: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    task_id = create_task(_do_export_cover_letter_pdf, company, job_id, paths)
    return {"task_id": task_id}


class CoverLetterUpdate(BaseModel):
    content: str


@app.put("/api/jobs/{company}/{job_id}/cover-letter")
def update_cover_letter(
    company: str,
    job_id: str,
    body: CoverLetterUpdate,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    store = JobStore(paths.db_path)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    job_dir = os.path.join(paths.output_dir, f"{company}_{job_id}")
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, "cover_letter.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body.content)
    shutil.move(tmp, path)
    return {"ok": True}


# ── Pipeline actions ──────────────────────────────────────────────────────────

_PLAYWRIGHT_ATS = {"google", "apple", "meta"}


def _resolve_companies(all_companies: list, group: Optional[str], company_filter: Optional[List[str]]) -> list:
    """Return company config list based on group name or explicit names."""
    if company_filter:
        names = set(company_filter)
        return [c for c in all_companies if c["name"] in names]
    if group == "playwright":
        return [c for c in all_companies if c.get("ats") in _PLAYWRIGHT_ATS]
    if group == "http":
        return [c for c in all_companies if c.get("ats") not in _PLAYWRIGHT_ATS]
    return all_companies


def _send_pipeline_email(all_scored: list, alert_jobs: list, stats: dict):
    """Send run summary email if SMTP credentials are configured."""
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    alert_email = os.environ.get("ALERT_EMAIL")
    if not (smtp_user and smtp_password and alert_email):
        return
    from pipeline.alerter import GmailAlerter
    try:
        GmailAlerter(alert_email).send_alert(
            alert_jobs, smtp_user, smtp_password,
            all_scored=all_scored, stats=stats,
        )
        logger.info("Pipeline email sent to %s", alert_email)
    except Exception as exc:
        logger.error("Failed to send pipeline email: %s", exc)


def _score_job_list(log, jobs: list, prefs: dict, provider, threshold: int, loader,
                    db_path: str) -> tuple:
    """Score a list of jobs. Returns (scored_count, failed_count, scored_jobs_list)."""
    from pipeline.scorer import JobScorer
    scorer = JobScorer(provider=provider)
    scored = 0
    failed = 0
    scored_jobs = []
    n = len(jobs)
    for i, job in enumerate(jobs, 1):
        company, job_id, title = job["company"], job["job_id"], job["title"]
        log(f"[{i}/{n}] Scoring: {company} — {title}")
        try:
            profile = loader.load(job=job)
            result = scorer.score(job, profile, prefs)
            store = JobStore(db_path)
            store.set_match_score(company, job_id, result.adjusted_score, result.summary,
                                  strengths=result.strengths, gaps=result.gaps)
            if result.meets_threshold(threshold):
                store.update_status(company, job_id, JobStatus.ALERTED)
            else:
                store.update_status(company, job_id, JobStatus.SKIPPED)
            store.close()
            scored += 1
            log(f"  → {result.adjusted_score}/10  {result.summary[:80]}")
            scored_jobs.append({**job, "match_score": result.adjusted_score,
                                 "match_summary": result.summary})
        except Exception as e:
            failed += 1
            logger.error("Scoring failed for %s/%s: %s", company, job_id, e, exc_info=True)
            log(f"  ERROR: {e}")
    return scored, failed, scored_jobs


def _do_run(log, group: str = None, company_filter: list = None,
            action: str = "source_and_score", paths: ProfilePaths = None):
    """Unified pipeline action.

    action:
      source          — fetch only, no scoring
      source_and_score — fetch then score newly added jobs
      score           — score unscored jobs for selected companies (no fetch)
      rescore         — force-rescore all jobs for selected companies (no fetch)
    group: "http" | "playwright" | None (all)
    company_filter: list of specific company names (overrides group)
    """
    from pipeline.fetcher import fetch_all_companies
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    if paths is None:
        paths = _legacy_paths()

    with open(os.path.join(paths.config_dir, "companies.yaml")) as f:
        all_companies_cfg = yaml.safe_load(f).get("companies", [])
    prefs = _load_prefs(paths)
    companies = _resolve_companies(all_companies_cfg, group, company_filter)
    company_names = {c["name"] for c in companies}
    group_label = group or "all"
    threshold = prefs.get("match_threshold", 7)
    provider = create_provider(prefs)
    loader = ProfileLoader(profile_dir=paths.resume_dir,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)

    # Record run start
    _run_store = JobStore(paths.db_path)
    run_id = _run_store.start_run(action, group_label, len(companies))
    _run_store.close()

    fetched_all = []
    new_jobs = []
    scored = 0
    failed_scoring = 0
    all_scored_jobs = []
    fetch_errors = {}
    _run_error = None

    try:
        # ── Fetch phase ──────────────────────────────────────────────────────
        if action in ("source", "source_and_score"):
            log(f"Fetching from {len(companies)} companies ({group_label})...")
            uses_playwright = any(c.get("ats") in _PLAYWRIGHT_ATS for c in companies)
            if uses_playwright:
                log("Launching browser (Playwright) — this may take several minutes...")
            fetched_all = fetch_all_companies(companies, prefs, log=log,
                                              fetch_errors=fetch_errors)
            store = JobStore(paths.db_path)
            seen_per_company: dict = {}
            for job in fetched_all:
                if store.upsert_job(job):
                    new_jobs.append(job)
                cname = job["company"]
                if cname not in seen_per_company:
                    seen_per_company[cname] = set()
                seen_per_company[cname].add(job["job_id"])

            # Mark jobs not returned this run as not_available, per company.
            n_run = len(companies)
            for ci, company_cfg in enumerate(companies, 1):
                cname = company_cfg["name"]
                seen_ids = seen_per_company.get(cname, set())
                log(f"[{ci}/{n_run}] Marking unavailable — {cname}")
                marked, n_active, reason = store.mark_unavailable_jobs(cname, seen_ids)
                if reason:
                    log(f"  → skipped ({reason})")
                else:
                    log(f"  → {marked} marked not_available ({len(seen_ids)} seen / {n_active} active)")

            store.close()
            log(f"Fetched {len(fetched_all)} matching jobs, {len(new_jobs)} new")
            if action == "source":
                log(f"\nDone. Fetched: {len(fetched_all)}  New: {len(new_jobs)}")
                return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": 0}

        # ── Determine jobs to score ───────────────────────────────────────────
        if action == "source_and_score":
            jobs_to_score = new_jobs
            log(f"{len(jobs_to_score)} new job(s) to score.")
        else:
            store = JobStore(paths.db_path)
            all_db_jobs = store.list_all_jobs()
            store.close()
            if action == "score":
                jobs_to_score = [j for j in all_db_jobs
                                 if j.get("match_score") is None
                                 and (not company_names or j["company"] in company_names)]
                log(f"{len(jobs_to_score)} unscored job(s) for selected companies.")
            else:  # rescore
                jobs_to_score = [j for j in all_db_jobs
                                 if not company_names or j["company"] in company_names]
                log(f"Force-rescoring {len(jobs_to_score)} job(s) for selected companies.")

        if not jobs_to_score:
            log("Nothing to score.")
            return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": 0}

        scored, failed_scoring, all_scored_jobs = _score_job_list(
            log, jobs_to_score, prefs, provider, threshold, loader, paths.db_path)
        log(f"\nDone. Fetched: {len(fetched_all)}  New: {len(new_jobs)}  Scored: {scored}")
        return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": scored}

    except Exception as exc:
        _run_error = str(exc)
        raise

    finally:
        _fin_store = JobStore(paths.db_path)
        _fin_store.finish_run(
            run_id,
            jobs_fetched=len(fetched_all),
            jobs_new=len(new_jobs),
            jobs_scored=scored,
            jobs_generated=0,
            status="error" if _run_error else "done",
            error_msg=_run_error,
        )
        _fin_store.close()

        try:
            from datetime import date as _date
            _stats = {
                "total_fetched": len(fetched_all),
                "new_jobs": len(new_jobs),
                "rescored_jobs": 0,
                "scored_jobs": scored,
                "failed_scoring": failed_scoring,
                "threshold": threshold,
                "run_date": str(_date.today()),
                "fetch_errors": fetch_errors,
                "run_error": _run_error,
            }
            _alert_jobs = [j for j in all_scored_jobs if (j.get("match_score") or 0) >= threshold]
            _send_pipeline_email(all_scored_jobs, _alert_jobs, _stats)
        except Exception as e:
            logger.error("Failed to send pipeline summary email: %s", e)


def _do_rescore_job(log, company: str, job_id: str, paths: ProfilePaths = None):
    """Rescore a single job and update its score/strengths/gaps in the DB."""
    from pipeline.scorer import JobScorer
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    if paths is None:
        paths = _legacy_paths()
    prefs = _load_prefs(paths)
    provider = create_provider(prefs)
    store = JobStore(paths.db_path)
    try:
        job = store.get_job(company, job_id)
    finally:
        store.close()
    if not job:
        raise ValueError(f"Job not found: {company}/{job_id}")

    log(f"Rescoring: {job['company']} — {job['title']}")
    loader = ProfileLoader(profile_dir=paths.resume_dir,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)
    scorer = JobScorer(provider=provider)
    profile = loader.load(job=job)
    result = scorer.score(job, profile, prefs)

    store2 = JobStore(paths.db_path)
    store2.set_match_score(company, job_id, result.adjusted_score, result.summary,
                           strengths=result.strengths, gaps=result.gaps)
    store2.close()
    log(f"  → {result.adjusted_score}/10  {result.summary[:80]}")
    return {"match_score": result.adjusted_score, "match_summary": result.summary,
            "match_strengths": result.strengths, "match_gaps": result.gaps}


class RunRequest(BaseModel):
    action: str = "source_and_score"  # source | source_and_score | score | rescore
    group: Optional[str] = None       # http | playwright | None (all)
    companies: Optional[List[str]] = None  # specific names; overrides group


@app.get("/api/companies")
def list_companies(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return company list with name and playwright flag for the run dropdown."""
    with open(os.path.join(paths.config_dir, "companies.yaml")) as f:
        data = yaml.safe_load(f)
    return [
        {"name": c["name"], "playwright": c.get("ats") in _PLAYWRIGHT_ATS}
        for c in data.get("companies", [])
    ]


# ── Settings: companies ───────────────────────────────────────────────────────

def _read_companies_cfg(config_dir: str = None) -> list:
    with open(os.path.join(config_dir or CONFIG_DIR, "companies.yaml")) as f:
        return yaml.safe_load(f).get("companies", [])


def _write_companies_cfg(companies: list, config_dir: str = None):
    d = config_dir or CONFIG_DIR
    path = os.path.join(d, "companies.yaml")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump({"companies": companies}, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)
    shutil.move(tmp, path)


class CompanyDetectRequest(BaseModel):
    name: str


class CompanyAddRequest(BaseModel):
    name: str
    ats: str
    board_slug: Optional[str] = None
    department: Optional[str] = None
    company_id: Optional[str] = None


@app.post("/api/companies/detect")
def detect_company_ats(body: CompanyDetectRequest, _=Depends(require_api_key)):
    """Auto-detect ATS platform for a company name."""
    from pipeline.detect_ats import detect_ats
    return detect_ats(body.name)


@app.get("/api/settings/companies")
def settings_list_companies(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return full company config list for the settings panel."""
    return _read_companies_cfg(paths.config_dir)


@app.post("/api/settings/companies")
def settings_add_company(
    body: CompanyAddRequest,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    companies = _read_companies_cfg(paths.config_dir)
    if any(c["name"].lower() == body.name.lower() for c in companies):
        raise HTTPException(status_code=409, detail=f"'{body.name}' already exists")
    entry: dict = {"name": body.name, "ats": body.ats}
    if body.board_slug:
        entry["board_slug"] = body.board_slug
    if body.department:
        entry["department"] = body.department
    if body.company_id:
        entry["company_id"] = body.company_id
    companies.append(entry)
    _write_companies_cfg(companies, paths.config_dir)
    return {"ok": True, "company": entry}


@app.delete("/api/settings/companies/{name}")
def settings_remove_company(
    name: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    companies = _read_companies_cfg(paths.config_dir)
    filtered = [c for c in companies if c["name"] != name]
    if len(filtered) == len(companies):
        raise HTTPException(status_code=404, detail=f"'{name}' not found")
    _write_companies_cfg(filtered, paths.config_dir)
    return {"ok": True}


# ── Settings: preferences ─────────────────────────────────────────────────────

_PREFS_UI_KEYS = frozenset({
    "match_threshold", "preferred_locations", "acceptable_locations",
    "excluded_location_keywords", "us_only", "title_keywords",
    "title_exclude_keywords", "llm_provider",
})


@app.get("/api/settings/preferences")
def settings_get_preferences(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    prefs = _load_prefs(paths)
    return {k: v for k, v in prefs.items() if k in _PREFS_UI_KEYS}


class PreferencesUpdate(BaseModel):
    match_threshold: Optional[int] = None
    preferred_locations: Optional[List[str]] = None
    acceptable_locations: Optional[List[str]] = None
    excluded_location_keywords: Optional[List[str]] = None
    us_only: Optional[bool] = None
    title_keywords: Optional[List[str]] = None
    title_exclude_keywords: Optional[List[str]] = None
    llm_provider: Optional[str] = None


@app.put("/api/settings/preferences")
def settings_save_preferences(
    body: PreferencesUpdate,
    user: dict = Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    current = _load_prefs(paths)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if body.us_only is not None:
        updates["us_only"] = body.us_only
    current.update(updates)
    store = JobStore(paths.db_path)
    try:
        store.set_prefs(current, changed_by=user.get("email", "unknown"))
    finally:
        store.close()
    return {"ok": True}


@app.post("/api/pipeline/run")
def pipeline_run(
    body: RunRequest = RunRequest(),
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Run a pipeline action for selected companies."""
    task_id = create_task(_do_run, body.group, body.companies or None, body.action, paths)
    return {"task_id": task_id}


@app.get("/api/runs")
def list_runs(
    limit: int = 20,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return recent pipeline run records, newest first."""
    store = JobStore(paths.db_path)
    try:
        return store.list_runs(limit)
    finally:
        store.close()


def _do_analyze_job(log, company: str, job_id: str, paths: ProfilePaths = None):
    """Run two-call deep evaluation for a single job and persist results."""
    from pipeline.evaluator import JobEvaluator
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    if paths is None:
        paths = _legacy_paths()
    prefs = _load_prefs(paths)
    provider = create_provider(prefs)
    store = JobStore(paths.db_path)
    try:
        job = store.get_job(company, job_id)
    finally:
        store.close()
    if not job:
        raise ValueError(f"Job not found: {company}/{job_id}")

    log(f"Deep evaluation: {job['company']} — {job['title']}")
    loader = ProfileLoader(profile_dir=paths.resume_dir,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)
    profile = loader.load(job=job)

    evaluator = JobEvaluator(provider=provider)
    result = evaluator.evaluate(job, profile, log=log)

    store2 = JobStore(paths.db_path)
    store2.set_analysis(company, job_id, result.requirements, result.resume_suggestions)
    store2.close()
    log(f"\nDone. {len(result.requirements)} requirements evaluated, "
        f"{len(result.resume_suggestions)} resume suggestions generated.")
    return {
        "requirements": result.requirements,
        "resume_suggestions": result.resume_suggestions,
    }


@app.post("/api/jobs/{company}/{job_id}/analyze")
def analyze_job(
    company: str,
    job_id: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Run deep two-call analysis for a single job."""
    store = JobStore(paths.db_path)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    task_id = create_task(_do_analyze_job, company, job_id, paths)
    return {"task_id": task_id}


@app.post("/api/jobs/{company}/{job_id}/rescore")
def rescore_job(
    company: str,
    job_id: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Rescore a single job (useful after model or resume changes)."""
    store = JobStore(paths.db_path)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    task_id = create_task(_do_rescore_job, company, job_id, paths)
    return {"task_id": task_id}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
def task_list(_=Depends(require_api_key)):
    from web.tasks import list_tasks
    return list_tasks()


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str, _=Depends(require_api_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── Log file endpoints ───────────────────────────────────────────────────────

@app.get("/api/logs")
def list_logs(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return metadata for the last 20 run log files, newest first."""
    import glob as _glob
    pattern = os.path.join(paths.log_dir, "run_*.log")
    files = []
    for path in _glob.glob(pattern):
        fname = os.path.basename(path)
        try:
            parts = fname[len("run_"):-len(".log")].rsplit("_", 1)
            task_id = parts[0]
            date_str = parts[1]
            size = os.path.getsize(path)
            files.append({"filename": fname, "task_id": task_id,
                          "date": date_str, "size_bytes": size})
        except (IndexError, OSError):
            continue
    files.sort(key=lambda f: f["date"], reverse=True)
    return files[:20]


@app.get("/api/logs/{filename}")
def get_log(
    filename: str,
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return the content of a single log file."""
    if "/" in filename or "\\" in filename or not filename.startswith("run_"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(paths.log_dir, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        with open(path, encoding="utf-8") as f:
            return {"filename": filename, "content": f.read()}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Auth endpoints ───────────────────────────────────────────────────────────

@app.get("/api/auth/me")
def auth_me(user: dict = Depends(require_api_key)):
    return user


@app.get("/api/auth/google")
async def auth_google():
    state = secrets.token_urlsafe(32)
    _auth_db.save_oauth_state(state)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return RedirectResponse(url=_GOOGLE_AUTH_URL + "?" + urlencode(params), status_code=302)


@app.get("/api/auth/google/callback")
async def auth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error or not code or not state:
        return RedirectResponse(url="/?auth_error=oauth_failed", status_code=302)

    if not _auth_db.consume_oauth_state(state):
        return RedirectResponse(url="/?auth_error=invalid_state", status_code=302)

    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(_GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            })
            token_data = token_resp.json()

        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("No access_token in response")

        async with httpx.AsyncClient() as client:
            userinfo_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_info = userinfo_resp.json()
    except Exception as e:
        logger.error("OAuth token exchange error: %s", e)
        return RedirectResponse(url="/?auth_error=oauth_failed", status_code=302)

    google_id = user_info.get("sub")
    email = user_info.get("email", "")
    name = user_info.get("name", email)

    if not google_id or not email:
        return RedirectResponse(url="/?auth_error=oauth_failed", status_code=302)

    existing = _auth_db.find_user_by_google_id(google_id)
    if existing:
        user = existing
    else:
        first = _auth_db.user_count() == 0
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        is_admin = first or (bool(admin_email) and email.lower() == admin_email.lower())

        if not is_admin and not _auth_db.is_email_allowed(email):
            return RedirectResponse(url="/?auth_error=not_allowed", status_code=302)

        if first:
            _auth_db.add_allowed_email(email, added_by="bootstrap")

        user = _auth_db.create_user(google_id, email, name, is_admin=is_admin)

    session_token = _auth_db.create_session(user["user_id"])
    prod = not OAUTH_REDIRECT_URI.startswith("http://localhost")
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=prod,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout(
    session_token: Optional[str] = Cookie(default=None),
    user: dict = Depends(require_api_key),
):
    if session_token:
        _auth_db.revoke_session(session_token)
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("session_token")
    return response


# ── Admin endpoints ───────────────────────────────────────────────────────────

class AllowedEmailRequest(BaseModel):
    email: str


class SetAdminRequest(BaseModel):
    is_admin: bool


@app.get("/api/admin/users")
def admin_list_users(user: dict = Depends(require_admin)):
    return _auth_db.list_users()


@app.get("/api/admin/allowed-emails")
def admin_list_emails(user: dict = Depends(require_admin)):
    return _auth_db.list_allowed_emails()


@app.post("/api/admin/allowed-emails")
def admin_add_email(body: AllowedEmailRequest, user: dict = Depends(require_admin)):
    _auth_db.add_allowed_email(body.email, added_by=user["email"])
    return {"ok": True}


@app.delete("/api/admin/allowed-emails/{email}")
def admin_remove_email(email: str, user: dict = Depends(require_admin)):
    _auth_db.remove_allowed_email(email)
    return {"ok": True}


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(
    user_id: str, body: SetAdminRequest, user: dict = Depends(require_admin)
):
    if user_id == user["user_id"] and not body.is_admin:
        raise HTTPException(status_code=400, detail="Cannot remove your own admin status")
    _auth_db.set_admin(user_id, body.is_admin)
    return {"ok": True}


# ── Resume upload ─────────────────────────────────────────────────────────────

_ALLOWED_RESUME_EXTS = {".pdf", ".docx", ".txt"}


try:
    @app.post("/api/resume")
    async def upload_resume(
        file: UploadFile = File(...),
        _=Depends(require_api_key),
        paths: ProfilePaths = Depends(get_profile_paths),
    ):
        """Accept a resume file (PDF/DOCX/TXT) and store it in the profile's resume dir."""
        suffix = Path(file.filename).suffix.lower() if file.filename else ""
        if suffix not in _ALLOWED_RESUME_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(_ALLOWED_RESUME_EXTS)}"
            )
        os.makedirs(paths.resume_dir, exist_ok=True)
        dest = os.path.join(paths.resume_dir, f"resume{suffix}")
        tmp = dest + ".tmp"
        content = await file.read()
        with open(tmp, "wb") as f_out:
            f_out.write(content)
        shutil.move(tmp, dest)
        return {"ok": True, "filename": f"resume{suffix}", "size_bytes": len(content)}
except RuntimeError:
    # python-multipart not installed; upload endpoint unavailable
    logger.warning("python-multipart not installed — /api/resume POST unavailable")


@app.get("/api/resume")
def get_resume_info(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Return info about the uploaded resume, if any."""
    for ext in _ALLOWED_RESUME_EXTS:
        p = os.path.join(paths.resume_dir, f"resume{ext}")
        if os.path.exists(p):
            return {"filename": f"resume{ext}", "size_bytes": os.path.getsize(p), "extension": ext}
    return {"filename": None}


@app.delete("/api/resume")
def delete_resume(
    _=Depends(require_api_key),
    paths: ProfilePaths = Depends(get_profile_paths),
):
    """Delete the current resume file."""
    deleted = False
    for ext in _ALLOWED_RESUME_EXTS:
        p = os.path.join(paths.resume_dir, f"resume{ext}")
        if os.path.exists(p):
            os.remove(p)
            deleted = True
    if not deleted:
        raise HTTPException(status_code=404, detail="No resume found")
    return {"ok": True}


# ── Profile endpoints ─────────────────────────────────────────────────────────

class ProfileCreateRequest(BaseModel):
    name: str


class ProfileRenameRequest(BaseModel):
    name: str


@app.get("/api/profiles")
def list_profiles(user: dict = Depends(require_api_key)):
    """List all profiles for the current user."""
    if user["user_id"] in ("dev", "service"):
        return [{"profile_id": "legacy", "name": "Default", "is_legacy": True}]
    return _auth_db.list_profiles(user["user_id"])


@app.post("/api/profiles")
def create_profile(body: ProfileCreateRequest, user: dict = Depends(require_api_key)):
    """Create a new profile for the current user."""
    if user["user_id"] in ("dev", "service"):
        raise HTTPException(status_code=400, detail="Cannot create profiles in dev/service mode")
    profile = _auth_db.create_profile(user["user_id"], body.name, is_legacy=False)
    # Pre-initialise the profile directories and seed config files
    _make_profile_paths(user["user_id"], profile["profile_id"])
    return profile


@app.patch("/api/profiles/{profile_id}")
def rename_profile(
    profile_id: str,
    body: ProfileRenameRequest,
    user: dict = Depends(require_api_key),
):
    profile = _auth_db.get_profile(profile_id)
    if not profile or profile["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Profile not found")
    _auth_db.rename_profile(profile_id, body.name)
    return {"ok": True}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str, user: dict = Depends(require_api_key)):
    profile = _auth_db.get_profile(profile_id)
    if not profile or profile["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Profile not found")
    remaining = _auth_db.list_profiles(user["user_id"])
    if len(remaining) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only profile")
    _auth_db.delete_profile(profile_id)
    _auth_db.delete_schedule(profile_id)
    return {"ok": True}


# ── Schedule endpoints ────────────────────────────────────────────────────────

class ScheduleUpdate(BaseModel):
    time_1: Optional[str] = None         # "HH:MM" in the given timezone
    time_2: Optional[str] = None
    timezone: str = "UTC"
    enabled: bool = True
    action: str = "source_and_score"


def _own_profile_or_404(profile_id: str, user: dict):
    profile = _auth_db.get_profile(profile_id)
    if not profile or profile["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.get("/api/profiles/{profile_id}/schedule")
def get_schedule(profile_id: str, user: dict = Depends(require_api_key)):
    _own_profile_or_404(profile_id, user)
    sched = _auth_db.get_schedule(profile_id)
    return sched or {"profile_id": profile_id, "time_1": None, "time_2": None,
                     "timezone": "UTC", "enabled": False, "action": "source_and_score"}


@app.put("/api/profiles/{profile_id}/schedule")
def set_schedule(
    profile_id: str,
    body: ScheduleUpdate,
    user: dict = Depends(require_api_key),
):
    _own_profile_or_404(profile_id, user)
    sched = _auth_db.set_schedule(
        profile_id=profile_id,
        user_id=user["user_id"],
        time_1=body.time_1,
        time_2=body.time_2,
        timezone=body.timezone,
        enabled=body.enabled,
        action=body.action,
    )
    _reload_scheduler()
    return sched


@app.delete("/api/profiles/{profile_id}/schedule")
def delete_schedule(profile_id: str, user: dict = Depends(require_api_key)):
    _own_profile_or_404(profile_id, user)
    _auth_db.delete_schedule(profile_id)
    _reload_scheduler()
    return {"ok": True}


# ── Static files & SPA ───────────────────────────────────────────────────────

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")


@app.get("/")
def serve_spa():
    return FileResponse(str(ROOT / "web" / "static" / "index.html"))
