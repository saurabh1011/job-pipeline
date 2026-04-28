"""Gmail alerter — sends a daily run summary email.

Input:
    alert_jobs:   list of high-match job dicts (score >= threshold)
    all_scored:   list of ALL job dicts scored this run (any score)
    stats:        run stats dict — see build_summary_email for keys
    smtp_user:    Gmail address to send from
    smtp_password: Gmail App Password (not your main password)
    recipient_email: address to receive the summary

The email always sends and includes:
  - Run stats header (total scanned, new today, score distribution)
  - High-match jobs with full details + approve/skip commands
  - Full table of all jobs scanned this run
"""
import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Tuple

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465


def _sanitize(text: str) -> str:
    """Normalize text from external sources to plain ASCII-safe unicode."""
    return (
        text
        .replace('\xa0', ' ')
        .replace('\u2014', '-')
        .replace('\u2013', '-')
        .replace('\u2019', "'")
        .replace('\u201c', '"')
        .replace('\u201d', '"')
    )


_ROLE_SIGNALS = [
    r"\byou (will|will be|are|'ll)\b",
    r"\bthis role\b",
    r"\bthis position\b",
    r"\byour (team|responsibilities|work|day|mission|goal)\b",
    r"\bresponsible for\b",
    r"\bwe('re| are) looking for\b",
    r"\bwe need\b",
    r"\blead (a |the |an )?(team|group|org|organization|engineers)\b",
    r"\bmanage (a |the |an )?(team|group|engineers)\b",
    r"\bthe (team|engineering team|group) is\b",
    r"\bour (team|engineering team|group)\b",
    r"\bin this role\b",
]


def _job_snippet(description: str, max_chars: int = 220) -> str:
    """Extract a 1-2 sentence role-specific snippet from a job description.

    Looks for the first sentence containing role-specific signals (what you'll do,
    who the team is) rather than generic company intro boilerplate.
    Falls back to the first non-trivial sentence if no signal found.
    """
    import re
    from bs4 import BeautifulSoup
    if "<" in description:
        description = BeautifulSoup(description, "html.parser").get_text(separator=" ")
    text = _sanitize(re.sub(r"\s+", " ", description).strip())
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]

    # Pass 1: find first sentence with a role signal
    for i, sent in enumerate(sentences):
        if len(sent) < 30:
            continue
        if any(re.search(p, sent, re.IGNORECASE) for p in _ROLE_SIGNALS):
            snippet = sent
            # Try to append the next sentence if it fits
            if i + 1 < len(sentences) and len(sent) + len(sentences[i + 1]) + 1 <= max_chars:
                snippet += " " + sentences[i + 1]
            if len(snippet) > max_chars:
                snippet = snippet[:max_chars].rsplit(" ", 1)[0] + "..."
            return snippet

    # Pass 2: fallback — first sentence over 60 chars
    for sent in sentences:
        if len(sent) >= 60:
            if len(sent) > max_chars:
                sent = sent[:max_chars].rsplit(" ", 1)[0] + "..."
            return sent

    return ""


def build_summary_email(
    alert_jobs: List[dict],
    all_scored: List[dict],
    stats: dict,
) -> Tuple[str, str]:
    """Build subject and plain-text body for the daily run summary email.

    Input:
        alert_jobs:  jobs with adjusted_score >= threshold, enriched with
                     match_score and match_summary
        all_scored:  every job scored this run (any score), same shape
        stats: {
            total_fetched:  int  — raw jobs returned by all fetchers
            new_jobs:       int  — truly new (not seen before)
            rescored_jobs:  int  — previously unscored jobs retried today
            scored_jobs:    int  — total scored this run (new + rescored)
            failed_scoring: int  — jobs where scoring threw an exception
            threshold:      int  — current match threshold
            run_date:       str  — ISO date string
        }

    Output: (subject: str, body: str)
    """
    high_count = len(alert_jobs)
    threshold = stats.get("threshold", 8)
    run_date = stats.get("run_date", str(date.today()))

    subject = (
        f"[Job Pipeline] {run_date} — "
        f"{stats.get('total_fetched', 0)} scanned, "
        f"{stats.get('new_jobs', 0)} new, "
        f"{high_count} high-match"
    )

    # Score distribution buckets
    buckets = {"9-10": 0, "8": 0, "6-7": 0, "1-5": 0, "failed": stats.get("failed_scoring", 0)}
    for j in all_scored:
        s = j.get("match_score")
        if s is None:
            continue
        if s >= 9:
            buckets["9-10"] += 1
        elif s == 8:
            buckets["8"] += 1
        elif s >= 6:
            buckets["6-7"] += 1
        else:
            buckets["1-5"] += 1

    lines = [
        f"Job Pipeline Run — {run_date}",
        "=" * 60,
        "",
        "SUMMARY",
        f"  Total fetched:   {stats.get('total_fetched', 0)}",
        f"  New today:       {stats.get('new_jobs', 0)}",
        f"  Retried (prev.): {stats.get('rescored_jobs', 0)}",
        f"  Scored this run: {stats.get('scored_jobs', 0)}",
        f"  Failed scoring:  {stats.get('failed_scoring', 0)}",
        "",
        "SCORE DISTRIBUTION (threshold ≥ {})".format(threshold),
        f"  Score 9-10  (exceptional): {buckets['9-10']}",
        f"  Score 8     (high-match):  {buckets['8']}",
        f"  Score 6-7   (moderate):    {buckets['6-7']}",
        f"  Score 1-5   (weak):        {buckets['1-5']}",
        f"  Failed/unscored:           {buckets['failed']}",
        "",
    ]

    # ── High-match jobs section ───────────────────────────────────────────────
    if alert_jobs:
        sorted_alerts = sorted(alert_jobs, key=lambda j: j.get("match_score", 0), reverse=True)
        lines += [
            "=" * 60,
            f"HIGH-MATCH JOBS (Score ≥ {threshold}) — {high_count} role{'s' if high_count != 1 else ''}",
            "=" * 60,
            "",
        ]
        for job in sorted_alerts:
            score = job.get("match_score", "N/A")
            snippet = _job_snippet(job.get("description", ""))
            lines += [
                f"Score: {score}/10",
                f"  Title:    {_sanitize(job.get('title', ''))}",
                f"  Company:  {_sanitize(job.get('company', ''))}",
                f"  Location: {_sanitize(job.get('location', ''))}",
                f"  URL:      {job.get('url', '')}",
                f"  Role:     {snippet}" if snippet else "",
                f"  Match:    {_sanitize(job.get('match_summary', ''))}",
                "",
                f"  Approve:  python3 cli.py approve --company \"{job.get('company', '')}\" --job-id \"{job.get('job_id', '')}\"",
                f"  Skip:     python3 cli.py skip --company \"{job.get('company', '')}\" --job-id \"{job.get('job_id', '')}\"",
                "",
                "-" * 60,
                "",
            ]
    else:
        lines += [
            "=" * 60,
            f"No jobs met the high-match threshold (≥ {threshold}) this run.",
            "",
        ]

    # ── Full job table ────────────────────────────────────────────────────────
    if all_scored:
        sorted_all = sorted(all_scored, key=lambda j: j.get("match_score") or 0, reverse=True)
        lines += [
            "=" * 60,
            f"ALL JOBS SCORED THIS RUN ({len(all_scored)} total)",
            "=" * 60,
            "",
            f"{'Score':<7} {'Company':<14} {'Location':<30} {'Title'}",
            "-" * 90,
        ]
        for job in sorted_all:
            score = job.get("match_score")
            score_str = f"{score}/10" if score is not None else "—"
            lines.append(
                f"{score_str:<7} "
                f"{_sanitize(job.get('company', '')):<14} "
                f"{_sanitize(job.get('location', '')):<30} "
                f"{_sanitize(job.get('title', ''))}"
            )
        lines.append("")

    body = "\n".join(lines)
    return subject, body


def build_console_summary(
    alert_jobs: List[dict],
    all_scored: List[dict],
    stats: dict,
) -> str:
    """Build a concise console-friendly summary string."""
    lines = [
        "",
        "=" * 60,
        f"RUN SUMMARY — {stats.get('run_date', str(date.today()))}",
        "=" * 60,
        f"  Fetched: {stats.get('total_fetched', 0)} | "
        f"New: {stats.get('new_jobs', 0)} | "
        f"Unscored: {stats.get('rescored_jobs', 0)} | "
        f"Scored: {stats.get('scored_jobs', 0)} | "
        f"Failed: {stats.get('failed_scoring', 0)}",
        f"  Total scored in DB: {stats.get('total_scored_in_db', '?')} | "
        f"High-match (≥{stats.get('threshold', 8)}): {len(alert_jobs)}",
        "",
    ]
    if all_scored:
        sorted_all = sorted(all_scored, key=lambda j: j.get("match_score") or 0, reverse=True)
        lines.append(f"  {'Score':<7} {'Company':<14} {'Location':<28} {'Title'}")
        lines.append(f"  {'-' * 80}")
        for job in sorted_all:
            score = job.get("match_score")
            score_str = f"{score}/10" if score is not None else "—"
            lines.append(
                f"  {score_str:<7} "
                f"{job.get('company', ''):<14} "
                f"{job.get('location', ''):<28} "
                f"{job.get('title', '')}"
            )
    lines.append("=" * 60)
    return "\n".join(lines)


class GmailAlerter:
    def __init__(self, recipient_email: str):
        self.recipient_email = recipient_email

    def send_alert(
        self,
        alert_jobs: List[dict],
        smtp_user: str,
        smtp_password: str,
        all_scored: List[dict] = None,
        stats: dict = None,
    ):
        """Send the daily summary email. Always sends (even zero high-match jobs)."""
        all_scored = all_scored or []
        stats = stats or {}

        subject, body = build_summary_email(alert_jobs, all_scored, stats)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = self.recipient_email
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as smtp:
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
            logger.info(
                "Summary email sent to %s (%d high-match, %d total scored)",
                self.recipient_email, len(alert_jobs), len(all_scored),
            )
        except Exception as exc:
            logger.error("Failed to send alert email: %s", exc)
            raise
