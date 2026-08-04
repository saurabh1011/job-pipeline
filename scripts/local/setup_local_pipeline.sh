#!/bin/bash
# Installs everything needed to run the job pipeline locally, twice a day,
# with a browsable web UI at http://localhost:8000 — instead of relying on
# the deployed Fly.io server (whose IP gets blocked by some job boards).
#
# What this does:
#   1. Writes .env.local (gitignored) with the secrets needed to run the
#      server, captured from your current shell environment.
#   2. Builds and starts the server via `docker compose` — the exact same
#      image (Dockerfile) that runs in production, so pandoc/typst/chromium
#      and every other system dependency are guaranteed to match what Fly.io
#      runs. `restart: unless-stopped` keeps it running across crashes and
#      Docker restarts.
#   3. Adds two crontab entries (8am / 6pm) that trigger a pipeline run via
#      the local server's own API, same as the old GitHub Actions workflow
#      did against the remote server.
#
# Safe to re-run: each step is idempotent.
#
# Prerequisites:
#   - Docker Desktop installed and running (`docker info` must succeed).
#     For the container to survive a reboot, enable Docker Desktop's
#     "Start Docker Desktop when you log in" setting (Settings > General) —
#     that part can't be scripted.
#   - GEMINI_API_KEY, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL must already be
#     exported in the shell you run this from (e.g. via ~/.zshrc).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CRON_MARKER="# job-pipeline-local-trigger"

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop isn't running. Start it and re-run this script." >&2
  exit 1
fi

missing=()
for var in GEMINI_API_KEY SMTP_USER SMTP_PASSWORD ALERT_EMAIL; do
  if [ -z "${!var:-}" ]; then
    missing+=("$var")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing required environment variable(s): ${missing[*]}" >&2
  echo "Export them in this shell (they're already in your ~/.zshrc — open a" >&2
  echo "fresh terminal or 'source ~/.zshrc') and re-run this script." >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/data" "$REPO_ROOT/output" "$REPO_ROOT/config" "$REPO_ROOT/profile"

# ── 1. .env.local ────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/.env.local" ]; then
  echo "→ .env.local already exists, leaving it as-is"
else
  umask 077
  {
    echo "APP_MODE=local"
    echo "GEMINI_API_KEY=$GEMINI_API_KEY"
    echo "SMTP_USER=$SMTP_USER"
    echo "SMTP_PASSWORD=$SMTP_PASSWORD"
    echo "ALERT_EMAIL=$ALERT_EMAIL"
  } > "$REPO_ROOT/.env.local"
  echo "→ wrote .env.local"
fi
chmod 600 "$REPO_ROOT/.env.local"

# ── 2. docker compose ────────────────────────────────────────────────────
( cd "$REPO_ROOT" && docker compose up -d --build )
echo "→ container 'job-pipeline-local' built and started"

# ── 3. crontab entries ───────────────────────────────────────────────────
existing_cron="$(crontab -l 2>/dev/null || true)"
if echo "$existing_cron" | grep -qF "$CRON_MARKER"; then
  echo "→ crontab entries already present, leaving as-is"
else
  {
    echo "$existing_cron"
    echo "0 8 * * * curl -s -X POST http://localhost:8000/api/pipeline/run >> $REPO_ROOT/logs/cron-trigger.log 2>&1 $CRON_MARKER"
    echo "0 18 * * * curl -s -X POST http://localhost:8000/api/pipeline/run >> $REPO_ROOT/logs/cron-trigger.log 2>&1 $CRON_MARKER"
  } | crontab -
  echo "→ added crontab entries for 8:00 AM and 6:00 PM"
fi

echo
echo "Done. Web UI: http://localhost:8000"
echo
echo "Remaining manual steps:"
echo "  1. Enable Docker Desktop > Settings > General >"
echo "     'Start Docker Desktop when you log in', so the container comes"
echo "     back up after a reboot."
echo "  2. If cron doesn't fire, grant Full Disk Access to cron in"
echo "     System Settings > Privacy & Security > Full Disk Access."
echo "  3. On the deployed app (job-pipeline.fly.dev), open the Schedule tab"
echo "     for your profile and clear the schedule, so it stops auto-running"
echo "     from the blocked IP."
