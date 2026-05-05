#!/usr/bin/env python3
"""Pipeline orchestrator — runs the full job search and application pipeline.

Flow:
    1. Load config (companies.yaml, preferences.yaml)
    2. Fetch all new jobs from configured companies
    3. Deduplicate against job store
    4. Score each new job against candidate profile
    5. Generate cover letter + tailored resume for high-match jobs
    6. Send email alert for all new high-match jobs
    7. Wait for CLI approval before applying

Usage:
    ANTHROPIC_API_KEY=... SMTP_USER=... SMTP_PASSWORD=... ALERT_EMAIL=... python3 run.py
"""
import logging
import os
import sys
import yaml

from datetime import date

from pipeline.fetcher import fetch_all_companies
from pipeline.store import JobStore, JobStatus
from pipeline.scorer import JobScorer
from pipeline.generator import ContentGenerator
from pipeline.alerter import GmailAlerter, build_console_summary
from pipeline.profile import ProfileLoader
from pipeline.llm import create_provider
from pipeline.checkpoint import RunCheckpoint
from ingest import run_ingestion_for_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _progress(idx: int, total: int, tag: str, company: str, title: str, location: str, suffix: str = ""):
    loc = f"({location})" if location else ""
    title_trunc = title[:45] + "…" if len(title) > 45 else title
    print(f"[{idx:>4}/{total}] {tag:<8} {company} — {title_trunc} {loc}{suffix}", flush=True)


def _progress_result(score: int, threshold: int, output_dir: str = ""):
    if score >= threshold:
        out = f" → {output_dir}" if output_dir else ""
        print(f"          └─ {score:>2}/10  ✓ high-match, generating materials{out}", flush=True)
    else:
        print(f"          └─ {score:>2}/10  below threshold", flush=True)


def load_config(
    companies_path: str = "config/companies.yaml",
    preferences_path: str = "config/preferences.yaml",
) -> dict:
    with open(companies_path) as f:
        companies_data = yaml.safe_load(f)
    with open(preferences_path) as f:
        prefs_data = yaml.safe_load(f)
    return {
        "companies": companies_data.get("companies", []),
        "preferences": prefs_data,
    }


def run_pipeline(
    config: dict,
    smtp_user: str,
    smtp_password: str,
    recipient: str,
    db_path: str = "jobs.db",
    output_dir: str = "output",
    profile_dir: str = "profile",
    checkpoint_path: str = "pipeline_checkpoint.json",
) -> dict:
    """Run the full pipeline. Returns a summary dict.

    Input:
        config:           dict with 'companies' list and 'preferences' dict
        smtp_user:        Gmail address to send alerts from
        smtp_password:    Gmail App Password
        recipient:        Email address to receive alerts
        db_path:          Path to SQLite database
        output_dir:       Directory for generated application materials
        profile_dir:      Directory containing resume.md and experience.md
        checkpoint_path:  Path to the run checkpoint JSON file

    Output:
        {
            new_jobs:       int,
            rescored_jobs:  int,
            scored_jobs:    int,
            failed_scoring: int,
            alerted_jobs:   int,
            skipped_jobs:   int,
            total_fetched:  int,
            resumed:        bool,
        }
    """
    preferences = config["preferences"]
    companies = config["companies"]
    threshold = preferences.get("match_threshold", 7)

    # Load checkpoint — determines whether this is a fresh run or a resume
    checkpoint = RunCheckpoint(checkpoint_path)

    if checkpoint.alert_sent:
        # Previous run completed — clean up and start fresh
        checkpoint.delete()
        checkpoint = RunCheckpoint(checkpoint_path)

    resuming = checkpoint.is_resumable
    if resuming:
        logger.info("Resuming previous run from checkpoint %s", checkpoint_path)
    else:
        logger.info("Starting fresh run")

    # Create LLM provider from config
    provider = create_provider(preferences)

    # Step 0: Run ingestion (skip on resume — already done)
    if not resuming:
        logger.info("Running story ingestion...")
        ingest_stats = run_ingestion_for_pipeline(provider)
        if ingest_stats["processed"] > 0:
            logger.info(
                "Ingestion: %d new files processed, %d finalized",
                ingest_stats["processed"], ingest_stats["copied"],
            )

    # Load candidate profile (without job-specific stories — loaded per-job below)
    loader = ProfileLoader(
        profile_dir=profile_dir,
        google_docs_links=preferences.get("google_docs_links", []),
        provider=provider,
    )
    loader.load(job=None)  # warm up base profile

    # Initialize components
    store = JobStore(db_path)
    engine = JobScorer(provider=provider)
    generator = ContentGenerator(provider=provider, output_dir=output_dir)
    alerter = GmailAlerter(recipient_email=recipient)

    # Step 1: Fetch jobs (skip on resume — use checkpointed list)
    if resuming:
        all_jobs = checkpoint.get_fetched_jobs()
        logger.info("Resumed: using %d checkpointed jobs", len(all_jobs))
        print(f"\nResuming previous run — {len(all_jobs)} jobs loaded from checkpoint\n", flush=True)
    else:
        print(f"\nFetching jobs from {len(companies)} companies...", flush=True)
        all_jobs = fetch_all_companies(companies, preferences)
        logger.info("Fetched %d total matching job titles", len(all_jobs))
        print(f"Fetched {len(all_jobs)} matching jobs\n", flush=True)
        checkpoint.set_fetched_jobs(all_jobs)

    # Track stats
    new_count = 0
    rescored_count = 0
    scored_count = 0
    failed_count = 0
    alert_candidates = []
    all_scored_jobs = []
    skipped_count = 0

    total_jobs = len(all_jobs)
    for idx, job in enumerate(all_jobs, start=1):
        company = job["company"]
        job_id = job["job_id"]
        title = job["title"]
        location = job["location"]
        prior = checkpoint.get_job_result(company, job_id)

        # ── Resume: job already fully processed ──────────────────────────────
        if prior is not None:
            if not prior["is_new"]:
                skipped_count += 1
                continue
            new_count += 1
            if prior["meets_threshold"] and prior["generated"]:
                scored_count += 1
                enriched = {
                    **job,
                    "match_score": prior["adjusted_score"],
                    "match_summary": prior["summary"],
                }
                alert_candidates.append(enriched)
                all_scored_jobs.append(enriched)
                logger.debug("Resumed (already done): %s/%s", company, job_id)
                continue
            # Falls through if scoring succeeded but generation was interrupted
            if prior["scored"] and prior["meets_threshold"] and not prior["generated"]:
                _progress(idx, total_jobs, "RESUME", company, title, location, " → generating materials...")
                profile = loader.load(job=job)
                try:
                    generator.generate(job, profile)
                    checkpoint.set_job_generated(company, job_id)
                    print(f"          └─ done → output/{company}_{job_id}", flush=True)
                except Exception as exc:
                    logger.error("Generation failed for %s/%s: %s", company, job_id, exc)
                store.update_status(company, job_id, JobStatus.ALERTED)
                enriched = {
                    **job,
                    "match_score": prior["adjusted_score"],
                    "match_summary": prior["summary"],
                }
                alert_candidates.append(enriched)
                all_scored_jobs.append(enriched)
                continue
            # Below-threshold jobs: already done, just count
            if prior["scored"] and not prior["meets_threshold"]:
                scored_count += 1
                all_scored_jobs.append({
                    **job,
                    "match_score": prior["adjusted_score"],
                    "match_summary": prior["summary"],
                })
                continue

        # ── Fresh or previously-unscored: upsert then score ──────────────────
        is_new = store.upsert_job(job)
        existing = store.get_job(company, job_id)
        already_scored = existing and existing.get("match_score") is not None

        if not is_new and already_scored:
            _progress(idx, total_jobs, "SKIP", company, title, location)
            checkpoint.set_job_skipped(company, job_id, is_new=False)
            skipped_count += 1
            continue

        tag = "NEW" if is_new else "UNSCORED"
        if is_new:
            new_count += 1
        else:
            rescored_count += 1
        _progress(idx, total_jobs, tag, company, title, location, " → scoring...")

        # Load job-specific profile (selects relevant stories for this job)
        profile = loader.load(job=job)

        # Score against profile
        try:
            result = engine.score(job, profile, preferences)
        except Exception as exc:
            logger.error("Scoring failed for %s/%s: %s", company, job_id, exc)
            print(f"          └─  FAILED: {exc}", flush=True)
            failed_count += 1
            continue

        scored_count += 1
        store.set_match_score(company, job_id, result.adjusted_score, result.summary)
        logger.info(
            "Score: %d/10 (adjusted: %d) — %s",
            result.score, result.adjusted_score, result.summary[:60]
        )

        enriched_job = {
            **job,
            "match_score": result.adjusted_score,
            "match_summary": result.summary,
        }
        all_scored_jobs.append(enriched_job)

        if not result.meets_threshold(threshold):
            _progress_result(result.adjusted_score, threshold)
            store.update_status(company, job_id, JobStatus.SKIPPED)
            checkpoint.set_job_skipped(company, job_id, is_new=is_new)
            continue

        # Save score to checkpoint before generation (so we can resume if generation crashes)
        checkpoint.set_job_scored(
            company, job_id,
            is_new=is_new,
            adjusted_score=result.adjusted_score,
            summary=result.summary,
            meets_threshold=True,
        )

        # Generate materials
        out_path = f"output/{company}_{job_id}"
        try:
            generated = generator.generate(job, profile)
            out_path = generated.output_dir
            checkpoint.set_job_generated(company, job_id)
        except Exception as exc:
            logger.error("Generation failed for %s/%s: %s", company, job_id, exc)

        _progress_result(result.adjusted_score, threshold, out_path)
        store.update_status(company, job_id, JobStatus.ALERTED)
        alert_candidates.append(enriched_job)

    # Step 3: Send summary email (always — even when no high-match jobs)
    run_stats = {
        "total_fetched": len(all_jobs),
        "new_jobs": new_count,
        "rescored_jobs": rescored_count,
        "scored_jobs": scored_count,
        "failed_scoring": failed_count,
        "alerted_jobs": len(alert_candidates),
        "skipped_jobs": skipped_count,
        "threshold": threshold,
        "run_date": str(date.today()),
        "total_scored_in_db": store.count_scored(),
    }

    logger.info("Sending run summary email (%d high-match, %d total scored)", len(alert_candidates), len(all_scored_jobs))
    try:
        alerter.send_alert(
            alert_jobs=alert_candidates,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            all_scored=all_scored_jobs,
            stats=run_stats,
        )
        checkpoint.mark_alert_sent()
    except Exception as exc:
        logger.error("Alert failed: %s", exc)
        checkpoint.mark_alert_sent()

    store.close()

    print(build_console_summary(alert_candidates, all_scored_jobs, run_stats))

    summary = {**run_stats, "resumed": resuming}
    logger.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    alert_email = os.environ.get("ALERT_EMAIL")

    missing = [k for k, v in {
        "SMTP_USER": smtp_user,
        "SMTP_PASSWORD": smtp_password,
        "ALERT_EMAIL": alert_email,
    }.items() if not v]

    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        print("\nSet them before running:")
        for var in missing:
            print(f"  export {var}=...")
        sys.exit(1)

    config = load_config()
    # API key comes from config file or env var (GEMINI_API_KEY by default)
    run_pipeline(
        config=config,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        recipient=alert_email,
    )
