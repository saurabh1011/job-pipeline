"""Unit tests for agent/backlog_agent.py — env sanitization, worktree
isolation, transcript persistence, and non-interactive prompt behavior.

Regression context: a local run of this agent (issue #13) leaked a real
GH_FEEDBACK_TOKEN into the test suite (spam issues #51/#52) and swept an
unrelated session's approved Bash permissions into its PR via `git add -u`
on a tracked .claude/settings.local.json. These tests guard the fixes for
both: the agent's subprocess env must be built from an explicit allowlist,
and it must operate in its own isolated worktree, never the live checkout.

Separately, that same run never touched the actual task (add companies to
config/companies.yaml) at all — it researched it thoroughly, then stopped
to ask two clarifying questions and committed only an unrelated side-quest,
because a non-interactive run has no way to receive an answer. build_prompt
and build_pr_body cover the fix: the agent is told explicitly to decide
rather than stall, and its own summary is threaded into the PR body so the
reasoning is visible where a human can actually act on it.
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.backlog_agent import (
    build_pr_body,
    build_prompt,
    create_pr,
    make_worktree,
    remove_worktree,
    run_agent,
    sanitized_env,
)


SAMPLE_ISSUE = {
    "number": 10,
    "title": "Add retry logic to fetcher",
    "body": "Details here.",
    "labels": [],
}


class TestSanitizedEnv:
    def test_keeps_allowlisted_exact_vars(self):
        result = sanitized_env({"PATH": "/usr/bin", "HOME": "/home/x",
                                 "ANTHROPIC_API_KEY": "sk-abc"})
        assert result == {"PATH": "/usr/bin", "HOME": "/home/x",
                           "ANTHROPIC_API_KEY": "sk-abc"}

    def test_keeps_claude_prefixed_vars(self):
        result = sanitized_env({"CLAUDE_CODE_SOMETHING": "1"})
        assert result == {"CLAUDE_CODE_SOMETHING": "1"}

    def test_strips_unrelated_secrets(self):
        """The exact scenario that leaked issues #51/#52: a real feedback
        token and SMTP password sitting in the launching shell must never
        reach the spawned claude subprocess."""
        result = sanitized_env({
            "GH_FEEDBACK_TOKEN": "github_pat_real",
            "GH_FEEDBACK_REPO": "owner/repo",
            "SMTP_PASSWORD": "wuoi pvfw dtnd ywnu",
            "GEMINI_API_KEY": "AIza...",
            "PATH": "/usr/bin",
        })
        assert "GH_FEEDBACK_TOKEN" not in result
        assert "GH_FEEDBACK_REPO" not in result
        assert "SMTP_PASSWORD" not in result
        assert "GEMINI_API_KEY" not in result
        assert result == {"PATH": "/usr/bin"}

    def test_empty_env_produces_empty_result(self):
        assert sanitized_env({}) == {}


@pytest.fixture()
def temp_repo(tmp_path):
    """A minimal real git repo with a main branch, for worktree tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class TestWorktreeIsolation:
    def test_make_worktree_creates_isolated_checkout_on_new_branch(self, temp_repo, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "REPO_ROOT", temp_repo)
        monkeypatch.setattr(mod, "WORKTREE_PARENT", temp_repo / ".agent-worktrees")

        worktree = make_worktree("agent/issue-10")

        assert worktree.exists()
        assert (worktree / "README.md").exists()
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=worktree,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert branch == "agent/issue-10"

        # The live repo's own working tree must be untouched — this is the
        # entire point of isolation.
        main_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=temp_repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert main_branch == "main"

    def test_remove_worktree_cleans_up(self, temp_repo, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "REPO_ROOT", temp_repo)
        monkeypatch.setattr(mod, "WORKTREE_PARENT", temp_repo / ".agent-worktrees")

        worktree = make_worktree("agent/issue-11")
        assert worktree.exists()

        remove_worktree(worktree)

        assert not worktree.exists()


class TestRunAgentIsolation:
    def test_claude_subprocess_runs_in_worktree_not_repo_root(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "LOG_DIR", tmp_path / "logs")
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        mock_proc = MagicMock(returncode=0, stdout='{"result": "done"}', stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=mock_proc) as mock_run:
            run_agent(SAMPLE_ISSUE, worktree)

        assert mock_run.call_args.kwargs["cwd"] == worktree

    def test_claude_subprocess_gets_sanitized_env_not_full_environ(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setenv("GH_FEEDBACK_TOKEN", "github_pat_should_not_leak")
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        mock_proc = MagicMock(returncode=0, stdout='{"result": "done"}', stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=mock_proc) as mock_run:
            run_agent(SAMPLE_ISSUE, worktree)

        passed_env = mock_run.call_args.kwargs["env"]
        assert "GH_FEEDBACK_TOKEN" not in passed_env

    def test_transcript_written_to_log_dir(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(mod, "LOG_DIR", log_dir)
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        mock_proc = MagicMock(returncode=0, stdout='{"result": "did the thing"}', stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=mock_proc):
            run_agent(SAMPLE_ISSUE, worktree)

        log_files = list(log_dir.glob(f"issue-{SAMPLE_ISSUE['number']}-*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text()
        assert "did the thing" in content

    def test_returns_false_on_nonzero_exit(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "LOG_DIR", tmp_path / "logs")
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        mock_proc = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("agent.backlog_agent.subprocess.run", return_value=mock_proc):
            success, _summary = run_agent(SAMPLE_ISSUE, worktree)

        assert success is False

    def test_returns_summary_from_result_field(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "LOG_DIR", tmp_path / "logs")
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        mock_proc = MagicMock(returncode=0, stdout='{"result": "Chose option A because X."}', stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=mock_proc):
            success, summary = run_agent(SAMPLE_ISSUE, worktree)

        assert success is True
        assert summary == "Chose option A because X."


class TestCreatePrIsolation:
    def test_git_commands_run_in_worktree_not_repo_root(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        ok = MagicMock(returncode=0)
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            create_pr(SAMPLE_ISSUE, worktree, "agent/issue-10")

        cwds = {call.kwargs.get("cwd") for call in mock_run.call_args_list}
        assert cwds == {worktree}

    def test_no_changes_skips_commit_and_push(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return MagicMock(returncode=0)  # nothing staged
            return MagicMock(returncode=0)

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run) as mock_run:
            create_pr(SAMPLE_ISSUE, worktree, "agent/issue-10")

        commands = [call.args[0][:2] for call in mock_run.call_args_list]
        assert ["git", "commit"] not in commands
        assert ["git", "push"] not in commands


class TestPromptForbidsStalling:
    def test_instructs_agent_not_to_stop_and_ask(self):
        prompt = build_prompt(SAMPLE_ISSUE)
        assert "do not stop" in prompt.lower() or "not stop and ask" in prompt.lower()

    def test_explains_run_is_non_interactive(self):
        prompt = build_prompt(SAMPLE_ISSUE)
        assert "non-interactive" in prompt.lower()

    def test_still_includes_issue_title_and_body(self):
        prompt = build_prompt(SAMPLE_ISSUE)
        assert SAMPLE_ISSUE["title"] in prompt
        assert SAMPLE_ISSUE["body"] in prompt


class TestPrBodyIncludesSummary:
    def test_includes_agent_summary_when_present(self):
        body = build_pr_body(SAMPLE_ISSUE, "Chose the Workday abstraction because X.")
        assert "Chose the Workday abstraction because X." in body
        assert f"Implements #{SAMPLE_ISSUE['number']}" in body

    def test_falls_back_to_checklist_only_when_summary_empty(self):
        body = build_pr_body(SAMPLE_ISSUE, "")
        assert "Agent's summary" not in body
        assert f"Implements #{SAMPLE_ISSUE['number']}" in body

    def test_truncates_very_long_summary(self):
        huge = "x" * 20000
        body = build_pr_body(SAMPLE_ISSUE, huge)
        assert len(body) < len(huge) + 1000
        assert "truncated" in body
