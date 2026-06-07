"""Submit user feedback as GitHub issues."""
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

GH_FEEDBACK_TOKEN = os.environ.get("GH_FEEDBACK_TOKEN", "")
GH_FEEDBACK_REPO = os.environ.get("GH_FEEDBACK_REPO", "")
_GH_API_BASE = "https://api.github.com"
_FEEDBACK_LABEL = "user-feedback"
_COOLDOWN_SECS = 60

_last_submission: dict[str, float] = {}


def create_github_issue(title: str, body: str, user: dict) -> dict:
    token = os.environ.get("GH_FEEDBACK_TOKEN", GH_FEEDBACK_TOKEN)
    repo = os.environ.get("GH_FEEDBACK_REPO", GH_FEEDBACK_REPO)
    if not token or not repo:
        raise HTTPException(
            status_code=503,
            detail="Feedback not configured (missing GH_FEEDBACK_TOKEN or GH_FEEDBACK_REPO)",
        )

    user_id = user.get("user_id", "unknown")
    now = time.monotonic()
    if now - _last_submission.get(user_id, 0) < _COOLDOWN_SECS:
        raise HTTPException(status_code=429, detail="Please wait before submitting another feedback")

    submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue_body = (
        f"**From:** {user['name']} ({user['email']})\n"
        f"**Submitted:** {submitted_at}\n\n"
        f"{body}"
    )
    issue_title = title if title.strip() else f"Feedback from {user['name']}"

    resp = httpx.post(
        f"{_GH_API_BASE}/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": issue_title, "body": issue_body, "labels": [_FEEDBACK_LABEL]},
        timeout=10.0,
    )

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"GitHub API error: {resp.text}")

    _last_submission[user_id] = now
    return resp.json()
