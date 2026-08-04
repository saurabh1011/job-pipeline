"""Unit tests for web/feedback.py — GitHub issue creation."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
import web.feedback as fb


USER = {"user_id": "u1", "email": "alice@example.com", "name": "Alice", "is_admin": False}


def _unmocked_post(*args, **kwargs):
    raise AssertionError(
        "web.feedback.httpx.post was called without being mocked in this test — "
        "that would hit the real GitHub API. Wrap the call in "
        "`with patch('web.feedback.httpx.post', ...)`."
    )


@pytest.fixture(autouse=True)
def reset_feedback_state(monkeypatch):
    """Isolate each test: clear cooldown dict, inject token/repo via module attrs,
    and block any real network call by default (a test must explicitly patch
    httpx.post to make one — see _unmocked_post)."""
    fb._last_submission.clear()
    monkeypatch.setattr(fb, "GH_FEEDBACK_TOKEN", "")
    monkeypatch.setattr(fb, "GH_FEEDBACK_REPO", "")
    monkeypatch.setattr(fb.httpx, "post", _unmocked_post)
    yield
    fb._last_submission.clear()


@pytest.fixture()
def mock_env(monkeypatch):
    monkeypatch.setattr(fb, "GH_FEEDBACK_TOKEN", "ghp_test")
    monkeypatch.setattr(fb, "GH_FEEDBACK_REPO", "owner/repo")


class TestCreateGithubIssue:
    def test_raises_503_when_token_missing(self):
        with pytest.raises(HTTPException) as exc:
            fb.create_github_issue("title", "body", USER)
        assert exc.value.status_code == 503

    def test_posts_issue_with_correct_payload(self, mock_env):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/owner/repo/issues/1", "number": 1}

        with patch("web.feedback.httpx.post", return_value=mock_resp) as mock_post:
            result = fb.create_github_issue("My bug", "Something broke", USER)

        assert result["number"] == 1
        payload = mock_post.call_args.kwargs["json"]
        assert payload["title"] == "My bug"
        assert "Alice" in payload["body"]
        assert "alice@example.com" in payload["body"]
        assert "user-feedback" in payload["labels"]

    def test_uses_default_title_when_title_empty(self, mock_env):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/owner/repo/issues/2", "number": 2}

        with patch("web.feedback.httpx.post", return_value=mock_resp) as mock_post:
            fb.create_github_issue("", "body text", USER)

        payload = mock_post.call_args.kwargs["json"]
        assert "Alice" in payload["title"]

    def test_raises_502_on_github_api_error(self, mock_env):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = "Validation Failed"

        with patch("web.feedback.httpx.post", return_value=mock_resp):
            with pytest.raises(HTTPException) as exc:
                fb.create_github_issue("title", "body", USER)
        assert exc.value.status_code == 502

    def test_enforces_per_user_cooldown(self, mock_env):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/owner/repo/issues/3", "number": 3}

        with patch("web.feedback.httpx.post", return_value=mock_resp):
            fb.create_github_issue("title", "body", USER)

        with pytest.raises(HTTPException) as exc:
            fb.create_github_issue("title", "body", USER)
        assert exc.value.status_code == 429

    def test_different_users_not_rate_limited_together(self, mock_env):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/owner/repo/issues/4", "number": 4}

        user2 = {**USER, "user_id": "u2", "email": "bob@example.com", "name": "Bob"}

        with patch("web.feedback.httpx.post", return_value=mock_resp):
            fb.create_github_issue("title", "body", USER)
            fb.create_github_issue("title", "body", user2)
