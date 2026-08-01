#!/bin/bash
# Installs everything needed to run the job pipeline locally, twice a day,
# with a browsable web UI at http://localhost:8000 — instead of relying on
# the deployed Fly.io server (whose IP gets blocked by some job boards).
#
# What this does:
#   1. Writes .env.local (gitignored) with the secrets needed to run the
#      server, captured from your current shell environment.
#   2. Installs + starts a launchd LaunchAgent that keeps
#      `uvicorn web.server:app` running on 127.0.0.1:8000 (survives reboots,
#      restarts on crash).
#   3. Adds two crontab entries (8am / 6pm) that trigger a pipeline run via
#      the local server's own API, same as the old GitHub Actions workflow
#      did against the remote server.
#
# Safe to re-run: each step is idempotent.
#
# Prerequisite: GEMINI_API_KEY, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL must
# already be exported in the shell you run this from (e.g. via ~/.zshrc).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABEL="com.jobpipeline.local-server"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
CRON_MARKER="# job-pipeline-local-trigger"

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

mkdir -p "$REPO_ROOT/logs"

# ── 1. .env.local ────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/.env.local" ]; then
  echo "→ .env.local already exists, leaving it as-is"
else
  umask 077
  {
    echo "APP_MODE=local"
    # %q shell-quotes each value so values containing spaces or special
    # characters (e.g. a Gmail app password like "abcd efgh ijkl mnop")
    # survive being `source`d intact instead of being word-split.
    printf 'GEMINI_API_KEY=%q\n' "$GEMINI_API_KEY"
    printf 'SMTP_USER=%q\n' "$SMTP_USER"
    printf 'SMTP_PASSWORD=%q\n' "$SMTP_PASSWORD"
    printf 'ALERT_EMAIL=%q\n' "$ALERT_EMAIL"
  } > "$REPO_ROOT/.env.local"
  echo "→ wrote .env.local"
fi
chmod 600 "$REPO_ROOT/.env.local"

# ── 2. launchd LaunchAgent ──────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__REPO_ROOT__|$REPO_ROOT|g" \
  "$REPO_ROOT/scripts/local/com.jobpipeline.local-server.plist.template" \
  > "$PLIST_DEST"
chmod 600 "$PLIST_DEST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST_DEST"
launchctl enable "gui/$UID_NUM/$LABEL"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"
echo "→ launchd agent '$LABEL' installed and started"

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
echo "  1. If cron doesn't fire, grant Full Disk Access to cron in"
echo "     System Settings > Privacy & Security > Full Disk Access."
echo "  2. On the deployed app (job-pipeline.fly.dev), open the Schedule tab"
echo "     for your profile and clear the schedule, so it stops auto-running"
echo "     from the blocked IP."
