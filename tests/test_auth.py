"""Unit tests for web/auth.py — API key authentication."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.auth import require_api_key


def _make_app():
    app = FastAPI()

    @app.get("/protected")
    def protected(_=__import__("fastapi").Depends(require_api_key)):
        return {"ok": True}

    return app


class TestRequireApiKey:
    def test_open_access_when_no_key_configured(self, monkeypatch):
        monkeypatch.delenv("WEB_API_KEY", raising=False)
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected")
        assert r.status_code == 200

    def test_valid_key_accepted(self, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "secret123")
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected", headers={"x-api-key": "secret123"})
        assert r.status_code == 200

    def test_wrong_key_rejected(self, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "secret123")
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected", headers={"x-api-key": "wrongkey"})
        assert r.status_code == 401

    def test_missing_key_header_rejected(self, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "secret123")
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected")
        assert r.status_code == 401

    def test_empty_key_header_rejected(self, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "secret123")
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected", headers={"x-api-key": ""})
        assert r.status_code == 401

    def test_key_is_case_sensitive(self, monkeypatch):
        monkeypatch.setenv("WEB_API_KEY", "SecretKey")
        app = _make_app()
        client = TestClient(app)
        r = client.get("/protected", headers={"x-api-key": "secretkey"})
        assert r.status_code == 401
