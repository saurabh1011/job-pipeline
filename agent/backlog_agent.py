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
  4. Commit the changes to the branch and open a PR
  5. Remove the worktree

Requirements:
  - ANTHROPIC_API_KEY set in environment
  - GH_TOKEN or `gh auth login` for GitHub access
  - `claude` CLI installed: npm install -g @anthropic-ai/claude-code
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
    """Build the env for the spawned `claude` process from an allowlist,
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


def run_agent(issue: dict, worktree: Path) -> bool:
    prompt = f"""You are a software engineer implementing a feature from the GitHub issue backlog.

Issue #{issue['number']}: {issue['title']}

---
{issue['body']}
---

Follow these steps:
1. Read CLAUDE.md first — it defines all project conventions you must follow.
2. Understand the existing code structure before making any changes.
3. Write unit tests first (in tests/), then implement to make them pass.
4. Run `python -m pytest tests/ -q` and fix any failures before finishing.
5. Do NOT commit, push, or open a PR — the workflow handles that.

Be thorough. Follow the design guidelines in CLAUDE.md exactly."""

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

    try:
        output = json.loads(proc.stdout)
        print(output.get("result", "").strip())
        cost = output.get("total_cost_usd")
        if cost:
            print(f"\nAgent cost: ${cost:.4f}")
    except (json.JSONDecodeError, AttributeError):
        print(proc.stdout)

    if proc.returncode != 0:
        print(f"\nAgent exited with error:\n{proc.stderr}", file=sys.stderr)
        return False

    return True


def create_pr(issue: dict, worktree: Path, branch: str) -> None:
    # Stage only modified tracked files + new files in code directories.
    # Never use -A — the repo has untracked mobile/, docs/, logs/ etc.
    # that must not be swept into agent PRs.
    subprocess.run(["git", "add", "-u"], cwd=worktree, check=True)
    for code_dir in ["pipeline", "tests", "web", "config", "agent"]:
        if (worktree / code_dir).is_dir():
            subprocess.run(["git", "add", code_dir], cwd=worktree, check=True)

    # Check if there's anything staged
    diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree)
    if diff_check.returncode == 0:
        print("Agent made no file changes — nothing to commit.")
        return

    subprocess.run([
        "git", "commit", "-m",
        f"Implement #{issue['number']}: {issue['title']}\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
    ], cwd=worktree, check=True)

    subprocess.run(["git", "push", "-u", "origin", branch], cwd=worktree, check=True)

    subprocess.run([
        "gh", "pr", "create",
        "--title", f"[Agent] {issue['title']}",
        "--body", (
            f"Implements #{issue['number']}\n\n"
            f"Generated by the backlog agent. Please review before merging.\n\n"
            f"**Checklist:**\n"
            f"- [ ] Tests pass\n"
            f"- [ ] Code follows project conventions\n"
            f"- [ ] No unintended changes"
        ),
        "--base", "main",
    ], cwd=worktree, check=True)

    print(f"\n✓ PR opened for issue #{issue['number']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python agent/backlog_agent.py <issue-number>")
        sys.exit(1)

    issue_number = int(sys.argv[1])
    issue = get_issue(issue_number)
    branch = f"agent/issue-{issue_number}"
    worktree = make_worktree(branch)

    try:
        success = run_agent(issue, worktree)
        if not success:
            sys.exit(1)
        create_pr(issue, worktree, branch)
    finally:
        remove_worktree(worktree)
