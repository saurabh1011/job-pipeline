#!/usr/bin/env python3
"""Backlog agent: fetches a GitHub issue and implements it using Claude Code.

Usage:
    python agent/backlog_agent.py <issue-number>

Example:
    python agent/backlog_agent.py 10

The agent will:
  1. Fetch the issue title and body from GitHub
  2. Create an isolated git worktree on a fresh branch off main
  3. Run Claude Code non-interactively, inside that worktree, to implement it
  4. Independently verify the result — run the test suite and a secret scan
     itself, rather than trusting the implementing run's self-report
  5. Commit, push, and open a PR (only if both gates pass)
  6. Run a second, independent Claude Code pass (fresh context, read-only)
     to review the diff against the issue, and post its verdict as a PR
     comment for the human reviewer
  7. Remove the worktree

Requirements:
  - ANTHROPIC_API_KEY set in environment
  - GH_TOKEN or `gh auth login` for GitHub access
  - `claude` CLI installed: npm install -g @anthropic-ai/claude-code
  - `gitleaks` installed: brew install gitleaks (or see agent.yml for CI)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREE_PARENT = REPO_ROOT / ".agent-worktrees"
LOG_DIR = REPO_ROOT / "agent" / "logs"

# Env vars the spawned `claude` process actually needs. Everything else in
# the launching shell — GH_FEEDBACK_TOKEN, SMTP_PASSWORD, GEMINI_API_KEY,
# whatever a developer has exported for unrelated local tooling — is
# deliberately withheld. The agent runs with broad Bash/Edit/Write grants and
# no human review until after it pushes; it has no legitimate use for those
# credentials, and a misdirected run must not be able to reach real external
# APIs with them. (This is exactly how issues #51/#52 on this repo happened:
# a local agent run inherited a real GH_FEEDBACK_TOKEN and the test suite's
# safety guard hadn't landed yet.)
_ENV_ALLOWLIST_EXACT = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
    "ANTHROPIC_API_KEY", "GH_TOKEN",
}
_ENV_ALLOWLIST_PREFIXES = ("CLAUDE_",)


def sanitized_env(source_env: dict) -> dict:
    """Build the env for a spawned `claude` process from an allowlist,
    rather than passing the launching shell's full environment through."""
    return {
        k: v for k, v in source_env.items()
        if k in _ENV_ALLOWLIST_EXACT or k.startswith(_ENV_ALLOWLIST_PREFIXES)
    }


def get_issue(issue_number: int) -> dict:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number),
         "--json", "number,title,body,labels"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def make_worktree(branch: str) -> Path:
    """Create an isolated git worktree on a fresh branch off main.

    Running the agent directly in the live checkout risks colliding with
    whatever else is happening there — an interactive session, another
    agent run, uncommitted work. The worktree gives this run its own
    working tree and index, sharing only the repo's object store."""
    worktree_path = WORKTREE_PARENT / branch.replace("/", "-")
    if worktree_path.exists():
        shutil.rmtree(worktree_path)
    WORKTREE_PARENT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree_path), "main"],
        cwd=REPO_ROOT, check=True,
    )
    return worktree_path


def remove_worktree(worktree_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=REPO_ROOT, check=False,
    )


def build_prompt(issue: dict) -> str:
    return f"""You are a software engineer implementing a feature from the GitHub issue backlog.

Issue #{issue['number']}: {issue['title']}

---
{issue['body']}
---

Follow these steps:
1. Read CLAUDE.md first — it defines all project conventions you must follow.
2. Understand the existing code structure before making any changes.
3. Write unit tests first (in tests/), then implement to make them pass.
4. Run the test suite and fix any failures before finishing — use
   `.venv/bin/python3 -m pytest tests/ -q` if a .venv exists in the repo
   root, otherwise `python3 -m pytest tests/ -q`. Your run is independently
   re-verified after you finish, so this doesn't need to be perfect, but
   fix what you can catch.
5. Do NOT commit, push, or open a PR — the workflow handles that.

This is a fully non-interactive, unattended run. Nobody is available to
answer questions until AFTER you finish and a PR is opened for human
review — so if you reach a genuine design decision (e.g. a choice of
abstraction, a tradeoff between approaches), do NOT stop and ask. Pick
the most reasonable option yourself and implement it completely. Explain
what you chose, why, and what alternative(s) you considered in your
final summary — that summary is included directly in the PR description,
which is where a human actually reviews and can push back or redirect.
A finished implementation with a documented judgment call is far more
useful here than a partial one waiting on a question nobody can answer.

Be thorough. Follow the design guidelines in CLAUDE.md exactly."""


def run_agent(issue: dict, worktree: Path) -> tuple[bool, str]:
    prompt = build_prompt(issue)

    print(f"\n→ Running Claude agent on issue #{issue['number']}: {issue['title']}\n")

    proc = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep",
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
        ],
        cwd=worktree,
        env=sanitized_env(dict(os.environ)),
        text=True,
        capture_output=True,
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"issue-{issue['number']}-{int(time.time())}.log"
    log_path.write_text(
        f"=== stdout ===\n{proc.stdout}\n\n=== stderr ===\n{proc.stderr}\n"
    )
    print(f"Full transcript: {log_path}")

    summary = ""
    try:
        output = json.loads(proc.stdout)
        summary = output.get("result", "").strip()
        print(summary)
        cost = output.get("total_cost_usd")
        if cost:
            print(f"\nAgent cost: ${cost:.4f}")
    except (json.JSONDecodeError, AttributeError):
        print(proc.stdout)

    if proc.returncode != 0:
        print(f"\nAgent exited with error:\n{proc.stderr}", file=sys.stderr)
        return False, summary

    return True, summary


def stage_changes(worktree: Path) -> bool:
    """Stage modified tracked files + new files in code directories.

    Never use -A — the repo has untracked mobile/, docs/, logs/ etc. that
    must not be swept into agent PRs. Returns whether anything is staged.
    """
    subprocess.run(["git", "add", "-u"], cwd=worktree, check=True)
    for code_dir in ["pipeline", "tests", "web", "config", "agent"]:
        if (worktree / code_dir).is_dir():
            subprocess.run(["git", "add", code_dir], cwd=worktree, check=True)

    diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree)
    return diff_check.returncode != 0


def python_for_tests() -> str:
    """Prefer the repo's own venv (this Mac's local dev convention); a
    freshly created worktree doesn't get one (it's gitignored, not tracked
    content, so `git worktree add` never copies it over). Falls back to
    whatever interpreter is already running — CI installs dependencies at
    the system/interpreter level, no venv involved there."""
    venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def run_test_suite(worktree: Path) -> tuple[bool, str]:
    """Independently verify the suite passes. The agent is instructed to
    run tests itself and fix failures, but that's a self-report — this
    re-runs them from the harness, against the actual worktree content,
    before anything gets pushed."""
    proc = subprocess.run(
        [python_for_tests(), "-m", "pytest", "tests/", "-q"],
        cwd=worktree, capture_output=True, text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def run_secret_scan(worktree: Path) -> tuple[bool, str]:
    """Scan the staged diff for secrets before anything gets pushed.

    Fails closed: if gitleaks isn't installed, this refuses to proceed
    rather than silently skipping a security gate. This is a different
    layer than sanitized_env() — that stops the agent process from ever
    having a real secret available to use; this catches anything that
    ended up in the diff some other way regardless (a credential-bearing
    file it could Read, something hallucinated that looks real, etc).
    """
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        return False, (
            "gitleaks is not installed — refusing to push without a secret "
            "scan. Install it (`brew install gitleaks` locally; agent.yml "
            "installs it for CI) and retry."
        )
    proc = subprocess.run(
        [gitleaks, "protect", "--staged", "--source", str(worktree), "-v"],
        cwd=worktree, capture_output=True, text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


_SUMMARY_MAX_CHARS = 6000


def build_pr_body(issue: dict, summary: str) -> str:
    body = (
        f"Implements #{issue['number']}\n\n"
        f"Generated by the backlog agent. Please review before merging.\n\n"
        f"**Checklist:**\n"
        f"- [ ] Tests pass\n"
        f"- [ ] Code follows project conventions\n"
        f"- [ ] No unintended changes"
    )
    summary = summary.strip()
    if not summary:
        return body
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS] + "\n\n… (truncated — see full transcript in agent/logs/)"
    return body + f"\n\n---\n\n**Agent's summary:**\n\n{summary}"


def commit_and_push(issue: dict, worktree: Path, branch: str) -> None:
    """Assumes stage_changes() already confirmed there's something staged."""
    subprocess.run([
        "git", "commit", "-m",
        f"Implement #{issue['number']}: {issue['title']}\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
    ], cwd=worktree, check=True)

    subprocess.run(["git", "push", "-u", "origin", branch], cwd=worktree, check=True)


def open_pr(issue: dict, worktree: Path, summary: str) -> str:
    proc = subprocess.run([
        "gh", "pr", "create",
        "--title", f"[Agent] {issue['title']}",
        "--body", build_pr_body(issue, summary),
        "--base", "main",
    ], cwd=worktree, check=True, capture_output=True, text=True)

    pr_url = proc.stdout.strip()
    print(f"\n✓ PR opened for issue #{issue['number']}: {pr_url}")
    return pr_url


def build_judge_prompt(issue: dict, diff: str) -> str:
    return f"""You are reviewing a pull request opened by another AI agent, for a
human who has not looked at it yet. You did not write this diff and have
no stake in it — be direct and skeptical, not encouraging.

The original task was issue #{issue['number']}: {issue['title']}

---
{issue['body']}
---

Here is the full diff the agent produced:

```diff
{diff}
```

Answer, concisely:
1. Does this diff actually implement what the issue asked for? If it's
   partial, incomplete, or addresses something else entirely, say so
   plainly.
2. Does it touch any files unrelated to the stated task? Name them.
3. Any red flags — hardcoded credentials or anything resembling a real
   API key/token, unrelated refactors, deleted tests, disabled checks,
   suspicious dependencies?
4. One-line verdict: LOOKS GOOD / NEEDS A CLOSER LOOK / DO NOT MERGE.

Be brief — a few sentences per point, not an essay. You have read-only
access to the repository for additional context if useful, but the diff
above should usually be enough."""


def run_judge(issue: dict, worktree: Path) -> str:
    """A second, independent Claude Code pass with no context from the
    implementing run — reviews the diff against the issue and gives an
    honest second opinion, the way a human reviewer would. Read-only: it's
    reviewing, not changing anything."""
    diff = subprocess.run(
        ["git", "diff", "main...HEAD"], cwd=worktree,
        capture_output=True, text=True, check=True,
    ).stdout

    proc = subprocess.run(
        [
            "claude",
            "-p", build_judge_prompt(issue, diff),
            "--allowedTools", "Read,Glob,Grep",
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
        ],
        cwd=worktree,
        env=sanitized_env(dict(os.environ)),
        text=True,
        capture_output=True,
    )

    try:
        output = json.loads(proc.stdout)
        return output.get("result", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return f"(judge run produced no parseable verdict)\n{proc.stdout}\n{proc.stderr}"


def post_judge_comment(worktree: Path, branch: str, verdict: str) -> None:
    body = (
        "**Independent review** (separate agent pass, no context from the "
        f"implementing run):\n\n{verdict}"
    )
    subprocess.run(
        ["gh", "pr", "comment", branch, "--body", body],
        cwd=worktree, check=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python agent/backlog_agent.py <issue-number>")
        sys.exit(1)

    issue_number = int(sys.argv[1])
    issue = get_issue(issue_number)
    branch = f"agent/issue-{issue_number}"
    worktree = make_worktree(branch)

    try:
        success, summary = run_agent(issue, worktree)
        if not success:
            sys.exit(1)

        if not stage_changes(worktree):
            print("Agent made no file changes — nothing to commit.")
            sys.exit(0)

        tests_ok, test_output = run_test_suite(worktree)
        if not tests_ok:
            print(f"\nTest suite failed — not opening a PR.\n{test_output}", file=sys.stderr)
            sys.exit(1)

        scan_ok, scan_output = run_secret_scan(worktree)
        if not scan_ok:
            print(f"\nSecret scan blocked this run — not opening a PR.\n{scan_output}", file=sys.stderr)
            sys.exit(1)

        commit_and_push(issue, worktree, branch)
        open_pr(issue, worktree, summary)

        verdict = run_judge(issue, worktree)
        post_judge_comment(worktree, branch, verdict)
    finally:
        remove_worktree(worktree)
