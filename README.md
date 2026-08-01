# Job Pipeline

A job search pipeline that fetches, scores, and surfaces desired roles from a list of companies — delivered as a personal web app with a daily automated run and ability to kick off adhoc runs. The intent of creating this app is two fold:
1. linkedin sometimes has delays in job posting. Also it is not easy to narrow down a list of companies and organize your job tracker to jobs you are interested in. 
2. It takes a lot of clicks to generate a cover letter for a job that is fit. Linkedin doesn't allow you to edit it inline to curate the cover letter to your own liking
3. The job matching for select companies happens on a schedule with a gap analysis attached expalining the score - making it easier to sift through poor matches and get to the ones that are more interesting.

## What it does

1. **Fetches** open roles from each company's careers site (Greenhouse ATS, Ashby, and custom APIs)
2. **Scores** each job against your preferences using an LLM (configurable)
3. **Generates** a tailored (but editable) cover letter draft for high-scoring jobs. Also allows you to export the cover letter pdf instantly.
4. **Surfaces** results in a web UI with filtering, sorting, and a live task drawer showing pipeline progress

The pipeline runs daily via GitHub Actions and results persist in a SQLite database on Fly.io.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI |
| Job fetching | GitHub Actions for scheduled runs|
| Scoring / generation | GEMINI API (configurable) |
| Database | SQLite|
| Auth | Google OAuth2 |
| Frontend | Vanilla JS + HTML (served as static files) |
| Hosting | Fly.io (2 GB RAM, `ewr` region) |
| CI / CD | GitHub Actions |

## Repo structure

```
pipeline/        # Fetchers, scorer, generator, store
  fetcher.py     # HTTP-based fetchers (Greenhouse, Ashby, Amazon, …)
  scorer.py      # LLM scoring against preferences
  generator.py   # Cover letter generation
  store.py       # SQLite read/write
web/
  server.py      # FastAPI app + API routes
  tasks.py       # Background task runner (powers task drawer)
  static/        # Frontend (HTML/JS/CSS)
config/
  companies.yaml       # Which companies to fetch, which ATS each uses
  preferences.yaml     # Your job preferences (titles, locations, …)
agent/
  backlog_agent.py     # Headless Claude Code agent for implementing GitHub issues
tests/           # pytest unit + integration tests
.github/workflows/
  tests.yml      # Run pytest on every push / PR
  deploy.yml     # Deploy to Fly.io on merge to main
  agent.yml      # Run backlog agent on `agent-ready` label or manual dispatch
```

## Setup

### Prerequisites

- Python 3.12+
- A Hosted LLM Key (or someting like locally hosted LLM (through something like Ollama) 
- A Google OAuth2 client (for web auth)
- [Fly.io CLI](https://fly.io/docs/hands-on/install-flyctl/) (for deployment)

### Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in your preferences
cp config/preferences.yaml.example config/preferences.yaml

# Run the web server
python web/server.py
# → open http://localhost:8080
```

Set these environment variables (or add them to a `.env`):

```
<LLM>_API_KEY=sk-ant-...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
OAUTH_REDIRECT_URI=http://localhost:8080/api/auth/google/callback
```

### Run the pipeline manually

```bash
# All companies
curl -X POST http://localhost:8080/api/pipeline/run

# HTTP-only (no browser, fast)
curl -X POST http://localhost:8080/api/pipeline/run -d '{"mode":"http"}'

```

### Running locally on a schedule (avoiding IP blocks)

The deployed app (`job-pipeline.fly.dev`) fetches from a shared Fly.io IP,
which some job boards block. Running the pipeline from your own machine
instead sends requests from your home IP.

```bash
export GEMINI_API_KEY=...   # or ANTHROPIC_API_KEY / OPENAI_API_KEY, matching config/preferences.yaml
export SMTP_USER=...
export SMTP_PASSWORD=...
export ALERT_EMAIL=...

./scripts/local/setup_local_pipeline.sh
```

This installs:
- A launchd LaunchAgent (`com.jobpipeline.local-server`) that keeps
  `uvicorn web.server:app` running at `http://localhost:8000`, restarting on
  crash/reboot, so the web UI is always available to manage runs.
- Two crontab entries (8am / 6pm) that trigger a run via
  `POST http://localhost:8000/api/pipeline/run`, the same call the old
  GitHub Actions workflow made against the remote server.

Secrets are written once to `.env.local` (gitignored) and sourced by
`scripts/local/run_server.sh`. The script is safe to re-run.

`.env.local` also sets `APP_MODE=local`, which disables the server's
in-process APScheduler (used for per-profile schedules when deployed on
Fly.io) so it can't fire a duplicate run alongside the cron trigger. The
deployed app sets `APP_MODE=remote` (`fly.toml`), the default if unset.

If you also had a per-profile schedule configured on the deployed app,
clear it from the Schedule tab there so it isn't still running (and
getting blocked) from the remote IP.

## Configuration

### `config/companies.yaml`

Lists every company to fetch and which ATS it uses. Supported ATS types:

| ATS value | Used by |
|-----------|---------|
| `greenhouse` | DoorDash, Dropbox, AirBnB, Robinhood, and many others |
| `ashby` | Notion, Linear, Rippling, and others |
| `microsoft` | Microsoft |
| `uber` | Uber |
| `amazon` | Amazon |
| `netflix` | Netflix |
| `linkedin` | LinkedIn |

### `config/preferences.yaml`

Defines what roles you are looking for — title keywords, locations, seniority filters, and anything passed to the LLM scorer.

## Deployment

The app runs on [Fly.io](https://fly.io). Every merge to `main` triggers an automatic deploy via `.github/workflows/deploy.yml`.

**Required GitHub secrets:**

| Secret | Description |
|--------|-------------|
| `FLY_API_TOKEN` | From `fly tokens create deploy -x 999999h` |
| `<LLM>_API_KEY` | Your LLM API key |

To deploy manually:

```bash
flyctl deploy --remote-only
```

## CI

| Workflow | Trigger |
|----------|---------|
| `tests.yml` | Every push and pull request to `main` |
| `deploy.yml` | Every merge to `main` |
| `agent.yml` | `agent-ready` label on an issue, or manual dispatch |

Branch protection on `main` requires all tests to pass before merging.

## Backlog agent

The repo includes a headless Claude Code agent (`agent/backlog_agent.py`) that can implement GitHub issues automatically:

```bash
# Run locally
python agent/backlog_agent.py <issue-number>
```

Or trigger via GitHub Actions: add the `agent-ready` label to any issue, or use the manual dispatch in the Actions tab. The agent commits changes to a new branch and opens a PR for review.

## License

MIT — see [LICENSE](LICENSE).
