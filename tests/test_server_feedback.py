"""Integration tests for POST /api/feedback."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import web.feedback as feedback_module
import web.server as server_module
from web.server import app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setattr(feedback_module, "GH_FEEDBACK_TOKEN", "ghp_test")
    monkeypatch.setattr(feedback_module, "GH_FEEDBACK_REPO", "owner/repo")
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clear_cooldown():
    import web.feedback as fb
    fb._last_submission.clear()
    yield
    fb._last_submission.clear()


def _mock_gh_response(number=42):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "number": number,
    }
    return mock_resp


class TestSubmitFeedback:
    def test_valid_feedback_returns_201(self, client):
        with patch("web.feedback.httpx.post", return_value=_mock_gh_response()):
            r = client.post("/api/feedback", json={"body": "Something is broken"})
        assert r.status_code == 201
        data = r.json()
        assert data["issue_number"] == 42
        assert "github.com" in data["issue_url"]

    def test_with_title_included(self, client):
        with patch("web.feedback.httpx.post", return_value=_mock_gh_response()) as mock_post:
            r = client.post("/api/feedback", json={"title": "My Bug", "body": "Details here"})
        assert r.status_code == 201
        payload = mock_post.call_args.kwargs["json"]
        assert payload["title"] == "My Bug"

    def test_empty_body_returns_400(self, client):
        r = client.post("/api/feedback", json={"body": "   "})
        assert r.status_code == 400

    def test_missing_body_returns_422(self, client):
        r = client.post("/api/feedback", json={"title": "no body"})
        assert r.status_code == 422

    def test_github_failure_returns_502(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch("web.feedback.httpx.post", return_value=mock_resp):
            r = client.post("/api/feedback", json={"body": "feedback"})
        assert r.status_code == 502

    def test_rate_limit_returns_429_on_second_request(self, client):
        with patch("web.feedback.httpx.post", return_value=_mock_gh_response()):
            r1 = client.post("/api/feedback", json={"body": "first"})
        assert r1.status_code == 201

        with patch("web.feedback.httpx.post", return_value=_mock_gh_response()):
            r2 = client.post("/api/feedback", json={"body": "second"})
        assert r2.status_code == 429
