#!/usr/bin/env python3
"""CLI for reviewing, approving, and managing job applications.

Usage:
    python3 cli.py list                                          # show all tracked jobs
    python3 cli.py list --status alerted                        # filter by status
    python3 cli.py review                                        # interactively review all alerted jobs
    python3 cli.py review --company Uber --job-id 1001          # review a specific job
    python3 cli.py approve --company Uber --job-id 1001         # approve for application
    python3 cli.py skip   --company Uber --job-id 1001          # skip this job
    python3 cli.py show-diff --company Uber --job-id 1001       # view resume diff
    python3 cli.py show-materials --company Uber --job-id 1001  # open output folder
"""
import os
import sys
import click
from pipeline.store import JobStore, JobStatus
from pipeline.fetcher import _strip_html

DB_PATH = "jobs.db"
OUTPUT_DIR = "output"


@click.group()
def cli():
    """Job application pipeline — review and manage matched roles."""
    pass


@cli.command("list")
@click.option("--status", default=None, help="Filter by status (new, alerted, approved, applied, skipped)")
def list_jobs(status):
    """List all tracked jobs."""
    store = JobStore(DB_PATH)
    try:
        if status:
            jobs = store.get_jobs_by_status(status)
        else:
            jobs = store.list_all_jobs()

        if not jobs:
            click.echo("No jobs found.")
            return

        click.echo(f"\n{'#':<4} {'Score':<7} {'Status':<10} {'Company':<12} {'Title':<45} {'Location'}")
        click.echo("-" * 100)
        for i, job in enumerate(jobs, 1):
            score = job.get("match_score")
            score_str = f"{score}/10" if score is not None else "  N/A"
            click.echo(
                f"{i:<4} {score_str:<7} {job['status']:<10} {job['company']:<12} "
                f"{job['title'][:44]:<45} {job.get('location', '')}"
            )
            if job.get("match_summary"):
                click.echo(f"     └─ {job['match_summary'][:90]}")
        click.echo()
    finally:
        store.close()


def _print_section(title: str, content: str):
    click.echo(click.style(f"\n{'─' * 60}", fg="bright_black"))
    click.echo(click.style(f"  {title}", fg="cyan", bold=True))
    click.echo(click.style(f"{'─' * 60}", fg="bright_black"))
    click.echo(content)


def _review_one(store: JobStore, job: dict) -> str:
    """Display materials for a job and prompt for a decision. Returns 'approve', 'skip', or 'quit'."""
    company = job["company"]
    job_id = job["job_id"]
    output_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")

    click.clear()
    click.echo(click.style("=" * 60, fg="yellow", bold=True))
    click.echo(click.style(f"  {job['title']}", fg="yellow", bold=True))
    click.echo(click.style(f"  {company}  •  {job.get('location', 'N/A')}", fg="yellow"))
    click.echo(click.style("=" * 60, fg="yellow", bold=True))

    score = job.get("match_score")
    click.echo(f"\n  Score:   {score}/10")
    click.echo(f"  Status:  {job['status']}")
    click.echo(f"  URL:     {job.get('apply_url') or job.get('url', 'N/A')}")
    if job.get("match_summary"):
        click.echo(f"\n  {job['match_summary']}")

    # Cover letter
    cover_path = os.path.join(output_dir, "cover_letter.md")
    if os.path.exists(cover_path):
        with open(cover_path) as f:
            _print_section("COVER LETTER", f.read())
    else:
        click.echo(click.style("\n  [No cover letter found]", fg="red"))

    # Resume diff
    diff_path = os.path.join(output_dir, "resume_diff.patch")
    if os.path.exists(diff_path):
        with open(diff_path) as f:
            diff = f.read()
        if diff.strip():
            click.echo(click.style(f"\n{'─' * 60}", fg="bright_black"))
            click.echo(click.style("  RESUME DIFF  (green = added, red = removed)", fg="cyan", bold=True))
            click.echo(click.style(f"{'─' * 60}", fg="bright_black"))
            for line in diff.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    click.echo(click.style(line, fg="green"))
                elif line.startswith("-") and not line.startswith("---"):
                    click.echo(click.style(line, fg="red"))
                elif line.startswith("@@"):
                    click.echo(click.style(line, fg="cyan"))
                else:
                    click.echo(line)
        else:
            click.echo("\n  (Resume unchanged from base)")

    click.echo(click.style(f"\n{'─' * 60}", fg="bright_black"))
    while True:
        choice = click.prompt(
            click.style("  Decision", fg="bright_white", bold=True),
            type=click.Choice(["a", "s", "q"], case_sensitive=False),
            prompt_suffix=click.style("  [a]pprove / [s]kip / [q]uit: ", fg="bright_white"),
            show_choices=False,
            show_default=False,
        )
        if choice == "a":
            store.update_status(company, job_id, JobStatus.APPROVED)
            click.echo(click.style(f"  ✓ Approved: {job['title']} at {company}", fg="green"))
            return "approve"
        elif choice == "s":
            store.update_status(company, job_id, JobStatus.SKIPPED)
            click.echo(click.style(f"  — Skipped: {job['title']} at {company}", fg="yellow"))
            return "skip"
        elif choice == "q":
            return "quit"


@cli.command("review")
@click.option("--company", default=None, help="Review a specific company")
@click.option("--job-id", default=None, help="Review a specific job ID")
def review(company, job_id):
    """Interactively review generated materials and approve or skip jobs.

    Without flags, cycles through all jobs with status 'alerted', sorted by score descending.
    """
    store = JobStore(DB_PATH)
    try:
        if company and job_id:
            job = store.get_job(company, job_id)
            if not job:
                click.echo(f"Error: Job not found — company='{company}' job_id='{job_id}'", err=True)
                sys.exit(1)
            jobs = [job]
        else:
            jobs = store.get_jobs_by_status(JobStatus.ALERTED)
            jobs = sorted(jobs, key=lambda j: j.get("match_score") or 0, reverse=True)

        if not jobs:
            click.echo("No jobs pending review (status: alerted).")
            return

        click.echo(f"\n{len(jobs)} job(s) to review, sorted by score.\n")
        approved = skipped = 0
        for i, job in enumerate(jobs, 1):
            click.echo(f"  [{i}/{len(jobs)}] {job['company']} — {job['title']} ({job.get('match_score', '?')}/10)")
        click.echo()

        for job in jobs:
            decision = _review_one(store, job)
            if decision == "approve":
                approved += 1
            elif decision == "skip":
                skipped += 1
            elif decision == "quit":
                break

        click.echo(f"\nDone. Approved: {approved}  Skipped: {skipped}  Remaining: {len(jobs) - approved - skipped}\n")
    finally:
        store.close()


@cli.command("approve")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
def approve(company, job_id):
    """Approve a job for application. Generates materials if not already done."""
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            click.echo(f"Error: Job not found — company='{company}' job_id='{job_id}'", err=True)
            sys.exit(1)

        store.update_status(company, job_id, JobStatus.APPROVED)
        click.echo(f"\n✓ Approved: {job['title']} at {job['company']}")
        click.echo(f"  Status updated to: approved")

        output_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
        if os.path.exists(output_dir):
            click.echo(f"\nGenerated materials are in: {output_dir}/")
            click.echo(f"  - cover_letter.md")
            click.echo(f"  - resume_tailored.md")
            click.echo(f"  - resume_diff.patch")
        else:
            click.echo(f"\nNote: No generated materials found at {output_dir}/")
            click.echo(f"Run the pipeline manually to generate them.")

        click.echo(f"\nApply URL: {job.get('apply_url', job.get('url', 'N/A'))}")
    finally:
        store.close()


@cli.command("skip")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
def skip(company, job_id):
    """Skip a job (mark as not interested)."""
    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            click.echo(f"Error: Job not found — company='{company}' job_id='{job_id}'", err=True)
            sys.exit(1)

        store.update_status(company, job_id, JobStatus.SKIPPED)
        click.echo(f"✓ Skipped: {job['title']} at {job['company']}")
    finally:
        store.close()


@cli.command("show-diff")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
def show_diff(company, job_id):
    """Display the diff between your base resume and the tailored version."""
    patch_path = os.path.join(OUTPUT_DIR, f"{company}_{job_id}", "resume_diff.patch")
    if not os.path.exists(patch_path):
        click.echo(f"Error: Diff not found at {patch_path}", err=True)
        sys.exit(1)

    with open(patch_path) as f:
        content = f.read()

    if not content.strip():
        click.echo("(No changes — tailored resume is identical to base resume)")
        return

    # Colorize diff output if terminal supports it
    for line in content.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            click.echo(click.style(line, fg="green"))
        elif line.startswith("-") and not line.startswith("---"):
            click.echo(click.style(line, fg="red"))
        elif line.startswith("@@"):
            click.echo(click.style(line, fg="cyan"))
        else:
            click.echo(line)


@cli.command("show-materials")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
def show_materials(company, job_id):
    """Show the path to generated application materials."""
    output_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
    if not os.path.exists(output_dir):
        click.echo(f"Error: No materials found at {output_dir}", err=True)
        sys.exit(1)

    click.echo(f"\nGenerated materials for {company} / Job {job_id}:")
    click.echo(f"  Directory: {os.path.abspath(output_dir)}")
    for fname in sorted(os.listdir(output_dir)):
        fpath = os.path.join(output_dir, fname)
        size = os.path.getsize(fpath)
        click.echo(f"  - {fname} ({size} bytes)")


@cli.command("apply")
@click.option("--url", default=None, help="URL of the job posting")
@click.option("--text", default=None, help="Raw job description text")
@click.option("--company", default=None, help="Company name (required if using --text)")
@click.option("--title", default=None, help="Job title (required if using --text)")
def apply(url, text, company, title):
    """Score and generate a cover letter for any job via URL or pasted text."""
    import re
    import hashlib
    import yaml
    import requests
    from bs4 import BeautifulSoup
    from pipeline.profile import ProfileLoader
    from pipeline.generator import ContentGenerator
    from pipeline.matcher import MatchEngine
    from pipeline.llm import create_provider

    if not url and not text:
        click.echo("Error: provide --url or --text.", err=True)
        sys.exit(1)

    description = ""
    gh_title = None
    gh_location = ""
    if url:
        click.echo(f"Fetching {url} ...")

        # Try Greenhouse API first if URL contains a gh_jid or numeric job ID
        gh_job_id = None
        gh_match = re.search(r"gh_jid=(\d+)", url) or re.search(r"/jobs/(\d+)", url)
        if gh_match:
            gh_job_id = gh_match.group(1)

        if gh_job_id:
            parsed = requests.utils.urlparse(url)
            if "greenhouse.io" in parsed.netloc:
                # Slug is in the path: job-boards.greenhouse.io/marqeta/jobs/123
                path_parts = parsed.path.strip("/").split("/")
                board_slug = company.lower() if company else (path_parts[0] if path_parts else "")
            else:
                # Slug inferred from subdomain: careers.duolingo.com -> duolingo
                domain_parts = re.sub(r"^(www|careers|about|jobs)\.", "", parsed.netloc).split(".")
                board_slug = company.lower() if company else domain_parts[0]
            gh_url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs/{gh_job_id}?questions=true"
            try:
                gh_resp = requests.get(gh_url, timeout=10)
                if gh_resp.status_code == 200:
                    gh_data = gh_resp.json()
                    description = _strip_html(gh_data.get("content", ""))
                    gh_title = gh_data.get("title")
                    gh_location = (gh_data.get("location") or {}).get("name", "")
                    click.echo(click.style(f"Fetched via Greenhouse API: {gh_title}", fg="green"))
            except Exception:
                pass

        # Fall back to plain HTTP scraping
        if not description:
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                description = re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()
            except Exception as e:
                click.echo(f"Error fetching URL: {e}\nTry --text instead.", err=True)
                sys.exit(1)

        if len(description) < 200:
            click.echo(click.style(
                "Warning: could not extract job description.\n"
                "Try re-running with --text and paste the job description directly.",
                fg="yellow"
            ))

        # Infer company from URL if not provided
        if not company:
            parsed = requests.utils.urlparse(url)
            if "greenhouse.io" in parsed.netloc:
                path_parts = parsed.path.strip("/").split("/")
                company = path_parts[0].capitalize() if path_parts else "Unknown"
            else:
                domain = re.sub(r"^(www|careers|about|jobs)\.", "", parsed.netloc)
                company = domain.split(".")[0].capitalize()
        if not title:
            title = gh_title or click.prompt("Job title")
    else:
        description = text
        if not company:
            company = click.prompt("Company name")
        if not title:
            title = click.prompt("Job title")

    job_id = hashlib.md5(f"{company}{title}{description[:100]}".encode()).hexdigest()[:8]
    job = {
        "job_id": job_id,
        "company": company,
        "title": title,
        "location": gh_location or "",
        "url": url or "",
        "apply_url": url or "",
        "description": description,
    }

    with open("config/preferences.yaml") as f:
        prefs = yaml.safe_load(f)

    profile = ProfileLoader().load("profile")
    provider = create_provider(prefs)

    click.echo("Scoring ...")
    matcher = MatchEngine(provider=provider)
    result_match = matcher.score(job, profile, prefs)
    score = result_match.adjusted_score
    summary = result_match.summary
    click.echo(click.style(f"\nScore: {score}/10", fg="cyan", bold=True))
    click.echo(f"{summary}\n")

    click.echo("Generating cover letter ...")
    gen = ContentGenerator(provider=provider, output_dir=OUTPUT_DIR)
    result = gen.generate(job, profile)

    store = JobStore(DB_PATH)
    try:
        existing = store.get_job(company, job_id)
        if not existing:
            store.upsert_job({**job, "match_score": score, "match_summary": summary,
                              "status": "alerted"})
    finally:
        store.close()

    click.echo(click.style(f"Cover letter: {result.output_dir}/cover_letter.md", fg="green"))
    click.echo(f"Export PDF:   python3 cli.py export --company {company} --job-id {job_id}")


@cli.command("generate")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
def generate(company, job_id):
    """Generate (or regenerate) cover letter for any job regardless of status."""
    import yaml
    from pipeline.profile import ProfileLoader
    from pipeline.generator import ContentGenerator
    from pipeline.llm import create_provider

    store = JobStore(DB_PATH)
    try:
        job = store.get_job(company, job_id)
        if not job:
            click.echo(f"Error: Job not found — company='{company}' job_id='{job_id}'", err=True)
            sys.exit(1)
    finally:
        store.close()

    with open("config/preferences.yaml") as f:
        prefs = yaml.safe_load(f)

    profile = ProfileLoader().load("profile")
    provider = create_provider(prefs)
    gen = ContentGenerator(provider, output_dir=OUTPUT_DIR)
    result = gen.generate(job, profile)
    click.echo(click.style(f"Generated: {result.output_dir}/cover_letter.md", fg="green"))


@cli.command("export")
@click.option("--company", required=True, help="Company name")
@click.option("--job-id", required=True, help="Job ID")
@click.option("--out-dir", default=None, help="Output directory (default: same as materials folder)")
def export_pdf(company, job_id, out_dir):
    """Export cover letter and resume to PDF."""
    import subprocess
    job_dir = os.path.join(OUTPUT_DIR, f"{company}_{job_id}")
    if not os.path.exists(job_dir):
        click.echo(f"Error: No materials found at {job_dir}", err=True)
        sys.exit(1)

    dest = out_dir or job_dir
    os.makedirs(dest, exist_ok=True)

    src = os.path.join(job_dir, "cover_letter.md")
    if not os.path.exists(src):
        click.echo("Error: No cover letter found.", err=True)
        sys.exit(1)

    dst = os.path.join(dest, "cover_letter.pdf")
    result = subprocess.run(
        ["pandoc", src, "-f", "markdown", "-t", "pdf",
         "--pdf-engine=typst",
         "-V", "mainfont=Carlito", "-V", "fontsize=11pt",
         "-o", dst],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        click.echo(click.style(f"  Exported: {dst}", fg="green"))
    else:
        click.echo(click.style(f"  Failed: {result.stderr.strip()}", fg="red"))


if __name__ == "__main__":
    cli()
