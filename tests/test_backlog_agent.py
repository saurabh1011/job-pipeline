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
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.backlog_agent import (
    build_judge_prompt,
    build_pr_body,
    build_prompt,
    commit_and_push,
    make_worktree,
    open_pr,
    post_judge_comment,
    python_for_tests,
    remove_worktree,
    run_agent,
    run_judge,
    run_secret_scan,
    run_test_suite,
    sanitized_env,
    stage_changes,
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


class TestStageChanges:
    def test_returns_true_when_something_staged(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return MagicMock(returncode=1)  # something staged
            return MagicMock(returncode=0)

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run):
            assert stage_changes(worktree) is True

    def test_returns_false_when_nothing_staged(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return MagicMock(returncode=0)  # nothing staged
            return MagicMock(returncode=0)

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run):
            assert stage_changes(worktree) is False

    def test_runs_in_worktree_not_repo_root(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        ok = MagicMock(returncode=0)
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            stage_changes(worktree)

        cwds = {call.kwargs.get("cwd") for call in mock_run.call_args_list}
        assert cwds == {worktree}


class TestPythonForTests:
    def test_prefers_repo_venv_when_present(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        venv_python = tmp_path / ".venv" / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n")

        assert python_for_tests() == str(venv_python)

    def test_falls_back_to_running_interpreter_without_venv(self, tmp_path, monkeypatch):
        import agent.backlog_agent as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        assert python_for_tests() == sys.executable


class TestRunTestSuite:
    def test_passes_when_pytest_exits_zero(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0, stdout="5 passed", stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            passed, output = run_test_suite(worktree)

        assert passed is True
        assert "5 passed" in output
        assert mock_run.call_args.kwargs["cwd"] == worktree

    def test_fails_when_pytest_exits_nonzero(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        bad = MagicMock(returncode=1, stdout="1 failed", stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=bad):
            passed, output = run_test_suite(worktree)

        assert passed is False
        assert "1 failed" in output


class TestRunSecretScan:
    def test_fails_closed_when_gitleaks_not_installed(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        with patch("agent.backlog_agent.shutil.which", return_value=None):
            clean, message = run_secret_scan(worktree)

        assert clean is False
        assert "gitleaks is not installed" in message

    def test_clean_when_gitleaks_exits_zero(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0, stdout="no leaks found", stderr="")
        with patch("agent.backlog_agent.shutil.which", return_value="/usr/local/bin/gitleaks"), \
             patch("agent.backlog_agent.subprocess.run", return_value=ok):
            clean, _ = run_secret_scan(worktree)

        assert clean is True

    def test_dirty_when_gitleaks_finds_a_leak(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        leak_found = MagicMock(returncode=1, stdout="", stderr="leak: github_pat_... found in fetcher.py")
        with patch("agent.backlog_agent.shutil.which", return_value="/usr/local/bin/gitleaks"), \
             patch("agent.backlog_agent.subprocess.run", return_value=leak_found):
            clean, message = run_secret_scan(worktree)

        assert clean is False
        assert "github_pat" in message

    def test_scans_staged_diff_in_worktree(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0, stdout="", stderr="")
        with patch("agent.backlog_agent.shutil.which", return_value="/usr/local/bin/gitleaks"), \
             patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            run_secret_scan(worktree)

        cmd = mock_run.call_args.args[0]
        assert "--staged" in cmd
        assert str(worktree) in cmd


class TestCommitAndPush:
    def test_runs_in_worktree_not_repo_root(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0)
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            commit_and_push(SAMPLE_ISSUE, worktree, "agent/issue-10")

        cwds = {call.kwargs.get("cwd") for call in mock_run.call_args_list}
        assert cwds == {worktree}
        commands = [call.args[0][:2] for call in mock_run.call_args_list]
        assert ["git", "commit"] in commands
        assert ["git", "push"] in commands


class TestOpenPr:
    def test_returns_pr_url_from_gh_output(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0, stdout="https://github.com/owner/repo/pull/99\n", stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=ok):
            url = open_pr(SAMPLE_ISSUE, worktree, "summary text")

        assert url == "https://github.com/owner/repo/pull/99"

    def test_body_includes_summary(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0, stdout="https://github.com/owner/repo/pull/99\n", stderr="")
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            open_pr(SAMPLE_ISSUE, worktree, "Chose option A.")

        body_index = mock_run.call_args.args[0].index("--body") + 1
        assert "Chose option A." in mock_run.call_args.args[0][body_index]


class TestJudgePrompt:
    def test_includes_diff_and_issue(self):
        prompt = build_judge_prompt(SAMPLE_ISSUE, "diff --git a/x b/x\n+foo")
        assert SAMPLE_ISSUE["title"] in prompt
        assert "diff --git a/x b/x" in prompt

    def test_asks_for_a_verdict(self):
        prompt = build_judge_prompt(SAMPLE_ISSUE, "some diff")
        assert "verdict" in prompt.lower()

    def test_frames_it_as_independent_review(self):
        prompt = build_judge_prompt(SAMPLE_ISSUE, "some diff")
        assert "no context from the implementing run" not in prompt  # that framing lives in the PR comment, not the prompt itself


class TestRunJudge:
    def test_reads_diff_and_returns_result_text(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "diff"]:
                return MagicMock(returncode=0, stdout="diff --git a/x b/x", stderr="")
            return MagicMock(returncode=0, stdout='{"result": "LOOKS GOOD"}', stderr="")

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run):
            verdict = run_judge(SAMPLE_ISSUE, worktree)

        assert verdict == "LOOKS GOOD"

    def test_judge_subprocess_is_read_only(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["git", "diff"]:
                return MagicMock(returncode=0, stdout="diff", stderr="")
            return MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run):
            run_judge(SAMPLE_ISSUE, worktree)

        claude_call = next(c for c in calls if c[0] == "claude")
        tools_index = claude_call.index("--allowedTools") + 1
        allowed = claude_call[tools_index]
        assert "Edit" not in allowed
        assert "Write" not in allowed
        assert "Bash" not in allowed

    def test_judge_gets_sanitized_env(self, tmp_path, monkeypatch):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        monkeypatch.setenv("GH_FEEDBACK_TOKEN", "should_not_leak")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[:2] == ["git", "diff"]:
                return MagicMock(returncode=0, stdout="diff", stderr="")
            return MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")

        with patch("agent.backlog_agent.subprocess.run", side_effect=fake_run):
            run_judge(SAMPLE_ISSUE, worktree)

        claude_call = next(cmd_kwargs for cmd_kwargs in calls if cmd_kwargs[0][0] == "claude")
        assert "GH_FEEDBACK_TOKEN" not in claude_call[1]["env"]


class TestPostJudgeComment:
    def test_posts_via_gh_pr_comment(self, tmp_path):
        worktree = tmp_path / "some-worktree"
        worktree.mkdir()
        ok = MagicMock(returncode=0)
        with patch("agent.backlog_agent.subprocess.run", return_value=ok) as mock_run:
            post_judge_comment(worktree, "agent/issue-10", "LOOKS GOOD")

        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["gh", "pr", "comment"]
        assert "LOOKS GOOD" in cmd[cmd.index("--body") + 1]


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
