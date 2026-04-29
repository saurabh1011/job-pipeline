"""FastAPI server for the job application pipeline UI.

Run from project root:
    python3 -m uvicorn web.server:app --reload --port 8000
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import yaml
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure project root is on sys.path so pipeline imports work
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from web.auth import require_api_key           # noqa: E402
from web.tasks import create_task, get_task    # noqa: E402
from pipeline.store import JobStore, JobStatus  # noqa: E402

DB_PATH    = os.environ.get("DB_PATH",      str(ROOT / "jobs.db"))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR",   str(ROOT / "output"))
CONFIG_DIR = os.environ.get("CONFIG_DIR",   str(ROOT / "config"))
PROFILE_DIR = os.environ.get("PROFILE_DIR", str(ROOT / "profile"))

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

app = FastAPI(title="Job Application Pipeline", version="1.0.0")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_prefs() -> dict:
    with open(os.path.join(CONFIG_DIR, "preferences.yaml")) as f:
        return yaml.safe_load(f) or {}


def _deserialize_job(job: dict) -> dict:
    """Parse JSON string fields into Python lists for API responses."""
    for field in ("match_strengths", "match_gaps"):
        val = job.get(field)
        if isinstance(val, str):
            try:
                job[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                job[field] = []
        elif val is None:
            job[field] = []
    return job


def _job_materials(company: str, job_id: str) -> dict:
    """Return cover letter text, diff text, and pdf path for a job."""
    job_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
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
def list_jobs(status: Optional[str] = None, _=Depends(require_api_key)):
    store = JobStore(DB_PATH)
    try:
        jobs = store.get_jobs_by_status(status) if status else store.list_all_jobs()
        jobs = [_deserialize_job(j) for j in jobs]
        jobs = sorted(jobs, key=lambda j: (j.get("match_score") or 0), reverse=True)
        return {"jobs": jobs}
    finally:
        store.close()


@app.get("/api/jobs/{company}/{job_id}")
def get_job(company: str, job_id: str, _=Depends(require_api_key)):
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {**_deserialize_job(job), **_job_materials(company, job_id)}
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
def update_job_status(company: str, job_id: str, body: StatusUpdate, _=Depends(require_api_key)):
    allowed = {JobStatus.APPROVED, JobStatus.SKIPPED, JobStatus.APPLIED,
               JobStatus.ALERTED, JobStatus.NEW, JobStatus.INTERVIEWING,
               JobStatus.REJECTED, JobStatus.OFFER, JobStatus.INTERESTING}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        store.update_status(company, job_id, body.status)
        return {"ok": True, "status": body.status}
    finally:
        store.close()


@app.post("/api/jobs/bulk-status")
def bulk_update_status(body: BulkStatusUpdate, _=Depends(require_api_key)):
    allowed = {JobStatus.APPROVED, JobStatus.SKIPPED, JobStatus.APPLIED,
               JobStatus.ALERTED, JobStatus.NEW, JobStatus.INTERVIEWING,
               JobStatus.REJECTED, JobStatus.OFFER, JobStatus.INTERESTING}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    store = JobStore(DB_PATH)
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

def _do_generate(log, company: str, job_id: str):
    from pipeline.profile import ProfileLoader
    from pipeline.generator import ContentGenerator
    from pipeline.llm import create_provider

    prefs = _load_prefs()
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            raise ValueError(f"Job not found: {company}/{job_id}")
    finally:
        store.close()

    log(f"Loading profile...")
    provider = create_provider(prefs)
    loader = ProfileLoader(
        profile_dir=PROFILE_DIR,
        google_docs_links=prefs.get("google_docs_links", []),
        provider=provider,
    )
    profile = loader.load(job=job)

    log(f"Generating cover letter for {company} — {job['title']}...")
    gen = ContentGenerator(provider=provider, output_dir=OUTPUT_DIR)
    result = gen.generate(job, profile)
    log(f"Done → {result.output_dir}/cover_letter.md")
    return {"output_dir": result.output_dir}


@app.post("/api/jobs/{company}/{job_id}/generate")
def generate_cover_letter(company: str, job_id: str, _=Depends(require_api_key)):
    task_id = create_task(_do_generate, company, job_id)
    return {"task_id": task_id}


def _do_export(log, company: str, job_id: str):
    job_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
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


@app.post("/api/jobs/{company}/{job_id}/export")
def export_pdf(company: str, job_id: str, _=Depends(require_api_key)):
    task_id = create_task(_do_export, company, job_id)
    return {"task_id": task_id}


# ── Pipeline actions ──────────────────────────────────────────────────────────

def _do_process(log):
    """Score unscored jobs + generate materials for those that meet threshold."""
    from pipeline.matcher import MatchEngine
    from pipeline.generator import ContentGenerator
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    prefs = _load_prefs()
    threshold = prefs.get("match_threshold", 7)
    provider = create_provider(prefs)
    store = JobStore(DB_PATH)

    try:
        all_jobs = store.list_all_jobs()
        unscored = [j for j in all_jobs if j.get("match_score") is None]
    finally:
        store.close()

    log(f"Found {len(unscored)} unscored job(s). Threshold: {threshold}/10")
    if not unscored:
        log("Nothing to do.")
        return {"scored": 0, "generated": 0}

    engine = MatchEngine(provider=provider)
    gen = ContentGenerator(provider=provider, output_dir=OUTPUT_DIR)
    loader = ProfileLoader(
        profile_dir=PROFILE_DIR,
        google_docs_links=prefs.get("google_docs_links", []),
        provider=provider,
    )

    scored = 0
    generated = 0
    for i, job in enumerate(unscored, 1):
        company, job_id, title = job["company"], job["job_id"], job["title"]
        log(f"[{i}/{len(unscored)}] Scoring: {company} — {title}")
        try:
            profile = loader.load(job=job)
            result = engine.score(job, profile, prefs)
            store2 = JobStore(DB_PATH)
            store2.set_match_score(
                company, job_id, result.adjusted_score, result.summary,
                strengths=result.strengths, gaps=result.gaps,
            )
            scored += 1
            log(f"  → {result.adjusted_score}/10  {result.summary[:80]}")

            if result.meets_threshold(threshold):
                log(f"  Generating materials...")
                gen.generate(job, profile)
                store2.update_status(company, job_id, JobStatus.ALERTED)
                generated += 1
                log(f"  → saved to output/{company}_{job_id}/")
            else:
                store2.update_status(company, job_id, JobStatus.SKIPPED)
            store2.close()
        except Exception as e:
            log(f"  ERROR: {e}")

    log(f"\nDone. Scored: {scored}  Generated: {generated}")
    return {"scored": scored, "generated": generated}


@app.post("/api/pipeline/process")
def pipeline_process(_=Depends(require_api_key)):
    """Score unscored jobs and generate materials for high-match ones."""
    task_id = create_task(_do_process)
    return {"task_id": task_id}


def _do_full_run(log, company_filter: list = None):
    """Fetch new jobs from selected companies, then score and generate.

    Args:
        company_filter: list of company names to run (None = all companies)
    """
    from pipeline.fetcher import fetch_all_companies
    from pipeline.matcher import MatchEngine
    from pipeline.generator import ContentGenerator
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    with open(os.path.join(CONFIG_DIR, "companies.yaml")) as f:
        companies_data = yaml.safe_load(f)
    prefs = _load_prefs()
    all_companies = companies_data.get("companies", [])
    if company_filter:
        companies = [c for c in all_companies if c["name"] in company_filter]
        log(f"Fetching jobs from {len(companies)} selected companies: {', '.join(company_filter)}")
    else:
        companies = all_companies
        log(f"Fetching jobs from {len(companies)} companies...")
    threshold = prefs.get("match_threshold", 7)
    provider = create_provider(prefs)

    all_jobs = fetch_all_companies(companies, prefs)
    log(f"Fetched {len(all_jobs)} matching jobs")

    store = JobStore(DB_PATH)
    new_jobs = []
    for job in all_jobs:
        if store.upsert_job(job):
            new_jobs.append(job)
    store.close()
    log(f"{len(new_jobs)} new job(s) added to database")

    if not new_jobs:
        log("No new jobs to score.")
        return {"fetched": len(all_jobs), "new": 0, "scored": 0, "generated": 0}

    engine = MatchEngine(provider=provider)
    gen = ContentGenerator(provider=provider, output_dir=OUTPUT_DIR)
    loader = ProfileLoader(
        profile_dir=PROFILE_DIR,
        google_docs_links=prefs.get("google_docs_links", []),
        provider=provider,
    )

    scored = generated = 0
    for i, job in enumerate(new_jobs, 1):
        company, job_id, title = job["company"], job["job_id"], job["title"]
        log(f"[{i}/{len(new_jobs)}] Scoring: {company} — {title}")
        try:
            profile = loader.load(job=job)
            result = engine.score(job, profile, prefs)
            store2 = JobStore(DB_PATH)
            store2.set_match_score(
                company, job_id, result.adjusted_score, result.summary,
                strengths=result.strengths, gaps=result.gaps,
            )
            scored += 1
            log(f"  → {result.adjusted_score}/10  {result.summary[:80]}")
            if result.meets_threshold(threshold):
                log(f"  Generating materials...")
                gen.generate(job, profile)
                store2.update_status(company, job_id, JobStatus.ALERTED)
                generated += 1
                log(f"  → saved to output/{company}_{job_id}/")
            else:
                store2.update_status(company, job_id, JobStatus.SKIPPED)
            store2.close()
        except Exception as e:
            log(f"  ERROR: {e}")

    log(f"\nDone. Fetched: {len(all_jobs)}  New: {len(new_jobs)}  Scored: {scored}  Generated: {generated}")
    return {"fetched": len(all_jobs), "new": len(new_jobs), "scored": scored, "generated": generated}


class RunRequest(BaseModel):
    companies: Optional[List[str]] = None


@app.get("/api/companies")
def list_companies(_=Depends(require_api_key)):
    """Return the list of configured company names."""
    with open(os.path.join(CONFIG_DIR, "companies.yaml")) as f:
        data = yaml.safe_load(f)
    return [c["name"] for c in data.get("companies", [])]


@app.post("/api/pipeline/run")
def pipeline_full_run(body: RunRequest = RunRequest(), _=Depends(require_api_key)):
    """Full run: fetch new jobs from selected (or all) companies, score, and generate materials."""
    task_id = create_task(_do_full_run, body.companies or None)
    return {"task_id": task_id}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks/{task_id}")
def task_status(task_id: str, _=Depends(require_api_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── Static files & SPA ───────────────────────────────────────────────────────

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")


@app.get("/")
def serve_spa():
    return FileResponse(str(ROOT / "web" / "static" / "index.html"))
