# Job Pipeline

A self-hosted job tracking system for engineering managers and directors. It fetches new postings daily from 30+ companies, scores them against your preferences using an LLM, generates tailored cover letters for strong matches, and surfaces them in a web UI and mobile app.

![CI](https://github.com/saurabh1011/job-pipeline/actions/workflows/test.yml/badge.svg)

---

## What it does

1. **Fetches** job listings from 30+ companies across multiple ATS platforms (Greenhouse, Ashby, Lever, and custom APIs)
2. **Scores** each job 1–10 against your preferences — title, location, seniority — using an LLM
3. **Generates** a cover letter draft for jobs above your match threshold
4. **Alerts** you by email when strong matches appear
5. **Tracks** application status (new → applied → interviewing → offer / rejected)

Runs on a schedule you control (default: once daily) and deploys to [Fly.io](https://fly.io) with a single command.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Pipeline (Python)                    │
│                                                         │
│  Fetchers ──► Scorer ──► Generator ──► Alerter          │
│  (30+ ATS)    (LLM)      (LLM)         (email)          │
└──────────────────────┬──────────────────────────────────┘
                       │ SQLite (jobs.db)
          ┌────────────┴────────────┐
          │                         │
   ┌──────▼──────┐          ┌───────▼──────┐
   │  Web UI      │          │  Mobile App  │
   │  (FastAPI +  │          │  (React      │
   │   React SPA) │          │   Native /   │
   └─────────────┘          │   Expo)      │
                             └─────────────┘
```

- **Pipeline** runs on a cron schedule inside the server process (APScheduler)
- **Web UI** served by FastAPI; single-page React app for browsing jobs, managing status, editing cover letters
- **Mobile app** (Expo / React Native) connects to your deployed server — iOS, Android, and web

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, APScheduler |
| Browser scraping | Playwright (Chromium) |
| LLM | Gemini (default, free tier) · Anthropic · OpenAI · Ollama (local) |
| Database | SQLite |
| Mobile | React Native / Expo (SDK 54), Expo Router v6 |
| Deployment | Fly.io, Docker |
| CI | GitHub Actions |

---

## Supported companies

### ATS-based (add any company on these platforms — no new code needed)

| ATS | Example companies |
|-----|------------------|
| **Greenhouse** | DoorDash, Stripe, Anthropic, Figma, Databricks, Lyft, Roblox, HubSpot, 20+ more |
| **Ashby** | OpenAI, Cohere, Ramp, Notion |
| **Lever** | Spotify |

### Custom integrations

Google, Apple, Meta, Microsoft, Uber, Amazon, Netflix, Zillow, Walmart, LinkedIn

---

## Getting started

### Prerequisites

- Python 3.12+
- Node.js 18+ (mobile app only)
- An LLM API key — [Gemini](https://aistudio.google.com) has a generous free tier

### Local setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/saurabh1011/job-pipeline
cd job-pipeline
pip install -r requirements.txt
playwright install chromium

# 2. Configure
cp config/preferences.yaml.example config/preferences.yaml
# Edit config/preferences.yaml — set your title keywords, locations, LLM key

# 3. Add your profile (resume + background)
mkdir -p profile
# Copy your resume PDF → profile/resume.pdf

# 4. Run the server
uvicorn web.server:app --reload

# 5. Open http://localhost:8000
```

### Running the pipeline manually

```bash
# Fetch jobs from all companies
python run.py fetch

# Fetch from specific companies only
python run.py fetch --companies "Google,Meta,Stripe"

# Score already-fetched jobs
python run.py score

# Generate cover letters for strong matches
python run.py generate
```

### Running tests

```bash
python -m pytest tests/ -q
# 596 tests across 25 test files
```

---

## Configuration

### `config/preferences.yaml`

Controls what jobs you see and how they are scored. Copy from `preferences.yaml.example`:

```yaml
# Minimum score (1–10) to trigger alert + cover letter generation
match_threshold: 8

# Title keywords to include
title_keywords:
  - "Engineering Manager"
  - "Director of Engineering"
  - "VP of Engineering"

# Location preferences (affects score, not hard filter)
preferred_locations:
  - "New York"
  - "Remote"

# LLM provider: gemini | anthropic | openai | ollama
llm_provider: gemini
api_keys:
  gemini: "YOUR_KEY_HERE"
```

### `config/companies.yaml`

Add any Greenhouse, Ashby, or Lever company with two lines:

```yaml
- name: Acme Corp
  ats: greenhouse
  board_slug: acmecorp
```

Find `board_slug` from the company's job board URL: `boards.greenhouse.io/{board_slug}`.

---

## Deployment (Fly.io)

```bash
# Install flyctl and authenticate
brew install flyctl
fly auth login

# Deploy
fly deploy

# Set secrets
fly secrets set GEMINI_API_KEY=your_key_here
fly secrets set SMTP_PASSWORD=your_password

# View logs
fly logs
```

The app mounts a persistent volume at `/data` for the SQLite database, your config, and generated files.

---

## Mobile app

```bash
cd mobile
npm install
npx expo start
```

Point the app at your deployed server URL and log in with your API key. Works on iOS, Android, and web.

---

## Project structure

```
├── pipeline/
│   ├── fetcher.py          # HTTP-based fetchers (Greenhouse, Ashby, Amazon, etc.)
│   ├── playwright_fetcher.py  # Browser-based fetchers (Google, Meta, etc.)
│   ├── scorer.py           # LLM job scoring
│   ├── generator.py        # Cover letter generation
│   ├── alerter.py          # Email alerts
│   └── store.py            # SQLite persistence
├── web/
│   ├── server.py           # FastAPI app + REST API
│   ├── tasks.py            # Background task runner
│   └── static/             # React SPA
├── mobile/                 # Expo / React Native app
├── config/
│   ├── companies.yaml      # Company list
│   └── preferences.yaml.example
└── tests/                  # 596 tests across 25 files
```

---

## Roadmap

- [ ] Job aggregation API support (JSearch / SerpApi) — add any company without custom code ([#10](https://github.com/saurabh1011/job-pipeline/issues/10))
- [ ] Fix Apple fetcher — native search API returns 0 results ([#8](https://github.com/saurabh1011/job-pipeline/issues/8))
- [ ] Router split and orchestration layer refactor

---

## License

MIT
