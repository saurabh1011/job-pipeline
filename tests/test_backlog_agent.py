"""Unit tests for the backlog agent's directory confinement."""
from unittest.mock import patch, MagicMock

from agent.backlog_agent import REPO_ROOT, build_allowed_tools, run_agent, create_pr


SAMPLE_ISSUE = {
    "number": 10,
    "title": "Add retry logic to fetcher",
    "body": "Details here.",
    "labels": [],
}


def test_repo_root_resolves_to_jobapplications_dir():
    assert (REPO_ROOT / "CLAUDE.md").exists()
    assert (REPO_ROOT / "agent" / "backlog_agent.py").exists()


def test_build_allowed_tools_scopes_edit_and_write_to_repo_root():
    tools = build_allowed_tools(REPO_ROOT)

    assert f"Edit({REPO_ROOT}/**)" in tools
    assert f"Write({REPO_ROOT}/**)" in tools
    assert "Edit" not in tools
    assert "Write" not in tools


def test_build_allowed_tools_never_grants_blanket_bash():
    tools = build_allowed_tools(REPO_ROOT)

    assert "Bash" not in tools
    assert any(t.startswith("Bash(") for t in tools)


def test_build_allowed_tools_whitelists_git_and_pytest_only():
    tools = build_allowed_tools(REPO_ROOT)
    bash_tools = [t for t in tools if t.startswith("Bash(")]

    assert "Bash(git *)" in bash_tools
    assert "Bash(python -m pytest*)" in bash_tools
    assert "Bash(python3 -m pytest*)" in bash_tools
    assert "Bash(pytest*)" in bash_tools
    assert len(bash_tools) == 4


def test_build_allowed_tools_keeps_read_glob_grep_unscoped():
    tools = build_allowed_tools(REPO_ROOT)

    assert "Read" in tools
    assert "Glob" in tools
    assert "Grep" in tools


@patch("agent.backlog_agent.subprocess.run")
def test_run_agent_invokes_claude_with_scoped_tools_and_repo_root_cwd(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"result": "done", "total_cost_usd": 0.01}',
        stderr="",
    )

    run_agent(SAMPLE_ISSUE)

    assert mock_run.call_count == 1
    args, kwargs = mock_run.call_args
    cmd = args[0]

    assert kwargs["cwd"] == REPO_ROOT

    allowed_tools_index = cmd.index("--allowedTools") + 1
    assert cmd[allowed_tools_index] == ",".join(build_allowed_tools(REPO_ROOT))


@patch("agent.backlog_agent.subprocess.run")
def test_create_pr_runs_every_subprocess_call_in_repo_root(mock_run):
    mock_run.return_value = MagicMock(returncode=1)  # diff --cached --quiet: staged changes exist

    create_pr(SAMPLE_ISSUE)

    assert mock_run.call_count > 0
    for _, kwargs in mock_run.call_args_list:
        assert kwargs.get("cwd") == REPO_ROOT
