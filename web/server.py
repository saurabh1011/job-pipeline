"""FastAPI server for the job application pipeline UI.

Run from project root:
    python3 -m uvicorn web.server:app --reload --port 8000
"""
import json
import os
import shutil
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

app = FastAPI(title="Job Application Pipeline", version="1.0.0")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_prefs() -> dict:
    with open(os.path.join(CONFIG_DIR, "preferences.yaml")) as f:
        return yaml.safe_load(f) or {}


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


class CoverLetterUpdate(BaseModel):
    content: str


@app.put("/api/jobs/{company}/{job_id}/cover-letter")
def update_cover_letter(company: str, job_id: str, body: CoverLetterUpdate, _=Depends(require_api_key)):
    store = JobStore(DB_PATH)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    job_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, "cover_letter.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body.content)
    shutil.move(tmp, path)
    return {"ok": True}


# ── Pipeline actions ──────────────────────────────────────────────────────────

_PLAYWRIGHT_ATS = {"google", "apple", "meta", "walmart"}


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


def _score_job_list(log, jobs: list, prefs: dict, provider, threshold: int, gen, loader) -> tuple:
    """Score a list of jobs, generate materials for those above threshold. Returns (scored, generated)."""
    from pipeline.matcher import MatchEngine
    engine = MatchEngine(provider=provider)
    scored = generated = 0
    n = len(jobs)
    for i, job in enumerate(jobs, 1):
        company, job_id, title = job["company"], job["job_id"], job["title"]
        log(f"[{i}/{n}] Scoring: {company} — {title}")
        try:
            profile = loader.load(job=job)
            result = engine.score(job, profile, prefs)
            store = JobStore(DB_PATH)
            store.set_match_score(company, job_id, result.adjusted_score, result.summary,
                                  strengths=result.strengths, gaps=result.gaps)
            scored += 1
            log(f"  → {result.adjusted_score}/10  {result.summary[:80]}")
            if result.meets_threshold(threshold):
                log(f"  Generating materials...")
                gen.generate(job, profile)
                store.update_status(company, job_id, JobStatus.ALERTED)
                generated += 1
                log(f"  → saved to output/{company}_{job_id}/")
            else:
                store.update_status(company, job_id, JobStatus.SKIPPED)
            store.close()
        except Exception as e:
            log(f"  ERROR: {e}")
    return scored, generated


def _do_run(log, group: str = None, company_filter: list = None, action: str = "source_and_score"):
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
    from pipeline.generator import ContentGenerator
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    with open(os.path.join(CONFIG_DIR, "companies.yaml")) as f:
        all_companies_cfg = yaml.safe_load(f).get("companies", [])
    prefs = _load_prefs()
    companies = _resolve_companies(all_companies_cfg, group, company_filter)
    company_names = {c["name"] for c in companies}
    group_label = group or "all"
    threshold = prefs.get("match_threshold", 7)
    provider = create_provider(prefs)
    gen = ContentGenerator(provider=provider, output_dir=OUTPUT_DIR)
    loader = ProfileLoader(profile_dir=PROFILE_DIR,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)

    # Record run start
    _run_store = JobStore(DB_PATH)
    run_id = _run_store.start_run(action, group_label, len(companies))
    _run_store.close()

    fetched_all = []
    new_jobs = []
    scored = 0
    generated = 0
    _run_error = None

    try:
        # ── Fetch phase ──────────────────────────────────────────────────────
        if action in ("source", "source_and_score"):
            log(f"Fetching from {len(companies)} companies ({group_label})...")
            uses_playwright = any(c.get("ats") in _PLAYWRIGHT_ATS for c in companies)
            if uses_playwright:
                log("Launching browser (Playwright) — this may take several minutes...")
            fetched_all = fetch_all_companies(companies, prefs, log=log)
            store = JobStore(DB_PATH)
            for job in fetched_all:
                if store.upsert_job(job):
                    new_jobs.append(job)
            store.close()
            log(f"Fetched {len(fetched_all)} matching jobs, {len(new_jobs)} new")
            if action == "source":
                log(f"\nDone. Fetched: {len(fetched_all)}  New: {len(new_jobs)}")
                return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": 0, "generated": 0}

        # ── Determine jobs to score ───────────────────────────────────────────
        if action == "source_and_score":
            jobs_to_score = new_jobs
            log(f"{len(jobs_to_score)} new job(s) to score.")
        else:
            store = JobStore(DB_PATH)
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
            return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": 0, "generated": 0}

        scored, generated = _score_job_list(log, jobs_to_score, prefs, provider, threshold, gen, loader)
        log(f"\nDone. Fetched: {len(fetched_all)}  New: {len(new_jobs)}  Scored: {scored}  Generated: {generated}")
        return {"fetched": len(fetched_all), "new": len(new_jobs), "scored": scored, "generated": generated}

    except Exception as exc:
        _run_error = str(exc)
        raise

    finally:
        _fin_store = JobStore(DB_PATH)
        _fin_store.finish_run(
            run_id,
            jobs_fetched=len(fetched_all),
            jobs_new=len(new_jobs),
            jobs_scored=scored,
            jobs_generated=generated,
            status="error" if _run_error else "done",
            error_msg=_run_error,
        )
        _fin_store.close()


def _do_rescore_job(log, company: str, job_id: str):
    """Rescore a single job and update its score/strengths/gaps in the DB."""
    from pipeline.matcher import MatchEngine
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    prefs = _load_prefs()
    provider = create_provider(prefs)
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
    finally:
        store.close()
    if not job:
        raise ValueError(f"Job not found: {company}/{job_id}")

    log(f"Rescoring: {job['company']} — {job['title']}")
    loader = ProfileLoader(profile_dir=PROFILE_DIR,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)
    engine = MatchEngine(provider=provider)
    profile = loader.load(job=job)
    result = engine.score(job, profile, prefs)

    store2 = JobStore(DB_PATH)
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
def list_companies(_=Depends(require_api_key)):
    """Return the list of configured company names (used by run-group dropdown)."""
    with open(os.path.join(CONFIG_DIR, "companies.yaml")) as f:
        data = yaml.safe_load(f)
    return [c["name"] for c in data.get("companies", [])]


# ── Settings: companies ───────────────────────────────────────────────────────

def _read_companies_cfg() -> list:
    with open(os.path.join(CONFIG_DIR, "companies.yaml")) as f:
        return yaml.safe_load(f).get("companies", [])


def _write_companies_cfg(companies: list):
    path = os.path.join(CONFIG_DIR, "companies.yaml")
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
def settings_list_companies(_=Depends(require_api_key)):
    """Return full company config list for the settings panel."""
    return _read_companies_cfg()


@app.post("/api/settings/companies")
def settings_add_company(body: CompanyAddRequest, _=Depends(require_api_key)):
    companies = _read_companies_cfg()
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
    _write_companies_cfg(companies)
    return {"ok": True, "company": entry}


@app.delete("/api/settings/companies/{name}")
def settings_remove_company(name: str, _=Depends(require_api_key)):
    companies = _read_companies_cfg()
    filtered = [c for c in companies if c["name"] != name]
    if len(filtered) == len(companies):
        raise HTTPException(status_code=404, detail=f"'{name}' not found")
    _write_companies_cfg(filtered)
    return {"ok": True}


# ── Settings: preferences ─────────────────────────────────────────────────────

_PREFS_UI_KEYS = frozenset({
    "match_threshold", "preferred_locations", "acceptable_locations",
    "excluded_location_keywords", "us_only", "title_keywords",
    "title_exclude_keywords", "llm_provider",
})


@app.get("/api/settings/preferences")
def settings_get_preferences(_=Depends(require_api_key)):
    prefs = _load_prefs()
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
def settings_save_preferences(body: PreferencesUpdate, _=Depends(require_api_key)):
    path = os.path.join(CONFIG_DIR, "preferences.yaml")
    with open(path) as f:
        current = yaml.safe_load(f) or {}
    updates = {k: v for k, v in body.dict().items() if v is not None}
    # us_only can legitimately be False — include it even when False
    if body.us_only is not None:
        updates["us_only"] = body.us_only
    current.update(updates)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(current, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    shutil.move(tmp, path)
    return {"ok": True}


@app.post("/api/pipeline/run")
def pipeline_run(body: RunRequest = RunRequest(), _=Depends(require_api_key)):
    """Run a pipeline action for selected companies."""
    task_id = create_task(_do_run, body.group, body.companies or None, body.action)
    return {"task_id": task_id}


@app.get("/api/runs")
def list_runs(limit: int = 20, _=Depends(require_api_key)):
    """Return recent pipeline run records, newest first."""
    store = JobStore(DB_PATH)
    try:
        return store.list_runs(limit)
    finally:
        store.close()


def _do_analyze_job(log, company: str, job_id: str):
    """Run two-call deep analysis for a single job and persist results."""
    from pipeline.analyzer import Analyzer
    from pipeline.profile import ProfileLoader
    from pipeline.llm import create_provider

    prefs = _load_prefs()
    provider = create_provider(prefs)
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
    finally:
        store.close()
    if not job:
        raise ValueError(f"Job not found: {company}/{job_id}")

    log(f"Deep analysis: {job['company']} — {job['title']}")
    loader = ProfileLoader(profile_dir=PROFILE_DIR,
                           google_docs_links=prefs.get("google_docs_links", []),
                           provider=provider)
    profile = loader.load(job=job)

    analyzer = Analyzer(provider=provider)
    result = analyzer.analyze(job, profile, log=log)

    store2 = JobStore(DB_PATH)
    store2.set_analysis(company, job_id, result.requirements, result.resume_suggestions)
    store2.close()
    log(f"\nDone. {len(result.requirements)} requirements evaluated, "
        f"{len(result.resume_suggestions)} resume suggestions generated.")
    return {
        "requirements": result.requirements,
        "resume_suggestions": result.resume_suggestions,
    }


@app.post("/api/jobs/{company}/{job_id}/analyze")
def analyze_job(company: str, job_id: str, _=Depends(require_api_key)):
    """Run deep two-call analysis for a single job."""
    store = JobStore(DB_PATH)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    task_id = create_task(_do_analyze_job, company, job_id)
    return {"task_id": task_id}


@app.post("/api/jobs/{company}/{job_id}/rescore")
def rescore_job(company: str, job_id: str, _=Depends(require_api_key)):
    """Rescore a single job (useful after model or resume changes)."""
    store = JobStore(DB_PATH)
    try:
        if not store.get_job(company, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        store.close()
    task_id = create_task(_do_rescore_job, company, job_id)
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


# ── Static files & SPA ───────────────────────────────────────────────────────

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")


@app.get("/")
def serve_spa():
    return FileResponse(str(ROOT / "web" / "static" / "index.html"))
