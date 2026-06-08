"""Integration tests for /api/resume endpoints."""
import io
import pytest
import yaml
from fastapi.testclient import TestClient

import web.auth_db as adb
import web.server as server_module
from web.server import app

INITIAL_PREFS = {
    "match_threshold": 7, "llm_provider": "gemini", "us_only": False,
    "title_keywords": ["Engineering Manager"], "title_exclude_keywords": [],
    "preferred_locations": [], "acceptable_locations": [],
    "excluded_location_keywords": [],
}

try:
    import multipart  # noqa: F401
    _MULTIPART_AVAILABLE = True
except ImportError:
    try:
        import python_multipart  # noqa: F401
        _MULTIPART_AVAILABLE = True
    except ImportError:
        _MULTIPART_AVAILABLE = False


@pytest.fixture()
def cfg_dir(tmp_path):
    (tmp_path / "companies.yaml").write_text(yaml.dump({"companies": []}))
    (tmp_path / "preferences.yaml").write_text(yaml.dump(INITIAL_PREFS))
    return tmp_path


@pytest.fixture()
def auth_setup(tmp_path, monkeypatch, cfg_dir):
    db_path = str(tmp_path / "test.db")
    auth_path = str(tmp_path / "auth.db")
    profile_dir = str(tmp_path / "profile")
    monkeypatch.setattr(adb, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(server_module, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(server_module, "DB_PATH", db_path)
    monkeypatch.setattr(server_module, "PROFILE_DIR", profile_dir)
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    adb.init_db()
    server_module._auth_db.AUTH_DB_PATH = auth_path
    with TestClient(app) as c:
        yield c


class TestResumeInfo:
    def test_no_resume_returns_null_filename(self, auth_setup):
        r = auth_setup.get("/api/resume")
        assert r.status_code == 200
        assert r.json()["filename"] is None

    def test_resume_info_after_file_placed(self, auth_setup, tmp_path, monkeypatch):
        import os
        resume_dir = str(tmp_path / "resume")
        os.makedirs(resume_dir)
        # Manually place a resume file in the legacy profile dir
        monkeypatch.setattr(server_module, "PROFILE_DIR", resume_dir)
        with open(os.path.join(resume_dir, "resume.txt"), "w") as f:
            f.write("My resume")
        r = auth_setup.get("/api/resume")
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "resume.txt"
        assert data["extension"] == ".txt"
        assert data["size_bytes"] == 9


class TestResumeDelete:
    def test_delete_nonexistent_returns_404(self, auth_setup):
        r = auth_setup.delete("/api/resume")
        assert r.status_code == 404

    def test_delete_existing_resume(self, auth_setup, tmp_path, monkeypatch):
        import os
        resume_dir = str(tmp_path / "resume2")
        os.makedirs(resume_dir)
        monkeypatch.setattr(server_module, "PROFILE_DIR", resume_dir)
        with open(os.path.join(resume_dir, "resume.txt"), "w") as f:
            f.write("Resume content")
        r = auth_setup.delete("/api/resume")
        assert r.status_code == 200
        assert not os.path.exists(os.path.join(resume_dir, "resume.txt"))


@pytest.mark.skipif(not _MULTIPART_AVAILABLE, reason="python-multipart not installed")
class TestResumeUpload:
    def test_upload_txt(self, auth_setup):
        r = auth_setup.post(
            "/api/resume",
            files={"file": ("resume.txt", b"My resume content", "text/plain")},
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "resume.txt"

    def test_upload_unsupported_ext_rejected(self, auth_setup):
        r = auth_setup.post(
            "/api/resume",
            files={"file": ("resume.html", b"<html>", "text/html")},
        )
        assert r.status_code == 400

    def test_upload_overwrites_previous(self, auth_setup):
        auth_setup.post(
            "/api/resume",
            files={"file": ("resume.txt", b"First version", "text/plain")},
        )
        auth_setup.post(
            "/api/resume",
            files={"file": ("resume.txt", b"Second version", "text/plain")},
        )
        r = auth_setup.get("/api/resume")
        assert r.json()["size_bytes"] == len(b"Second version")
