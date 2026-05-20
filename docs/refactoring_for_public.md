# Refactoring Plan — Making the Codebase Public

## 1. Split `server.py` into FastAPI Routers (biggest win)

FastAPI has a built-in `APIRouter` for this. Current `server.py` has ~665 lines mixing
four unrelated concerns. It should be:

```
web/
  server.py          # app creation, mounts routers only (~30 lines)
  routers/
    jobs.py          # GET/PATCH /api/jobs, bulk status, cover letter
    pipeline.py      # POST /api/pipeline/run, /rescore, /analyze, /generate-cover-letter, /export-*
    settings.py      # /api/settings/companies, /api/settings/preferences
    tasks.py         # GET /api/tasks, /api/tasks/{id}
```

## 2. Extract Task Functions into an Orchestration Layer

`_do_run`, `_do_rescore_job`, `_do_analyze_job`, `_do_generate_cover_letter` are
pipeline logic — they don't belong in the HTTP layer. Move them to
`pipeline/orchestrator.py`. The routers become thin: validate input, call orchestrator,
return task ID.

## 3. Centralize Config/Env Loading

Right now `DB_PATH`, `OUTPUT_DIR`, `CONFIG_DIR`, `PROFILE_DIR` are defined at module
level in `server.py` and re-referenced everywhere. A single `web/config.py`:

```python
class Settings:
    db_path    = os.environ.get("DB_PATH", ...)
    output_dir = os.environ.get("OUTPUT_DIR", ...)
    config_dir = os.environ.get("CONFIG_DIR", ...)
    profile_dir = os.environ.get("PROFILE_DIR", ...)
```

## 4. Gitignore `profile/` and Provide Templates

`profile/resume.md` and `profile/experience.md` contain personal data. For a public repo:

- Add `profile/` to `.gitignore`
- Add `profile/resume.md.example` and `profile/experience.md.example` as templates

## 5. Add a README

No README exists. A public repo needs at minimum:

- What the project is
- How to run it locally
- What env vars are required
- How to configure companies

## 6. Unify `run.py` and `cli.py`

Both are CLI entry points with similar orchestration logic. For a public repo this is
confusing — `run.py` is the legacy batch runner and `cli.py` is the interactive tool.
Options:
- Unify under `cli.py` with a `run` subcommand
- Delete `run.py` if the web UI fully replaces it

---

## Summary

| Change | Effort | Impact |
|--------|--------|--------|
| Split server.py into routers | Medium | High — readability |
| Extract orchestration layer | Medium | High — separation of concerns |
| Centralize config | Small | Medium |
| Gitignore profile/ + templates | Small | Required for public |
| README | Small | Required for public |
| Unify run.py + cli.py | Small | Low |
