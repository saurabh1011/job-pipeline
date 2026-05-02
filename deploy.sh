#!/usr/bin/env bash
# Safe deploy: checks for running tasks on the remote app before deploying.
# Usage: ./deploy.sh [fly deploy args...]

set -euo pipefail

APP_URL="https://job-pipeline.fly.dev"
API_KEY="${JOB_API_KEY:-}"

if [[ -z "$API_KEY" ]]; then
  echo "Error: JOB_API_KEY env var not set. Export it before deploying."
  exit 1
fi

echo "Checking for running tasks on $APP_URL..."

# Try to fetch the task list; if the endpoint doesn't exist yet, skip the check.
HTTP_STATUS=$(curl -s -o /tmp/tasks_response.json -w "%{http_code}" \
  -H "X-API-Key: $API_KEY" \
  "$APP_URL/api/tasks" 2>/dev/null || echo "000")

if [[ "$HTTP_STATUS" == "000" ]]; then
  echo "Warning: Could not reach $APP_URL — machine may be stopped. Proceeding."
elif [[ "$HTTP_STATUS" == "404" ]]; then
  echo "Warning: /api/tasks endpoint not found (old deploy?). Proceeding without check."
elif [[ "$HTTP_STATUS" != "200" ]]; then
  echo "Warning: Unexpected status $HTTP_STATUS from tasks API. Proceeding."
else
  RUNNING=$(python3 -c "
import json, sys
tasks = json.load(open('/tmp/tasks_response.json'))
running = [t for t in tasks if t.get('status') in ('pending', 'running')]
for t in running:
    print(f'  [{t[\"status\"]}] {t[\"id\"]} — started {t.get(\"started_at\", \"unknown\")}')
sys.exit(1 if running else 0)
" 2>/dev/null && echo "" || true)

  if python3 -c "
import json, sys
tasks = json.load(open('/tmp/tasks_response.json'))
sys.exit(1 if any(t.get('status') in ('pending','running') for t in tasks) else 0)
" 2>/dev/null; then
    echo "No running tasks. Safe to deploy."
  else
    echo ""
    echo "WARNING: Tasks are currently running on the remote app:"
    python3 -c "
import json
tasks = json.load(open('/tmp/tasks_response.json'))
for t in tasks:
    if t.get('status') in ('pending', 'running'):
        print(f'  [{t[\"status\"]}] id={t[\"id\"]}  started={t.get(\"started_at\", \"unknown\")}')
"
    echo ""
    printf "Deploy anyway and kill the running job? [y/N] "
    read -r answer
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
      echo "Aborted. Wait for the job to finish, then re-run ./deploy.sh"
      exit 0
    fi
    echo "Confirmed — deploying and interrupting running task."
  fi
fi

echo ""
echo "Running: fly deploy $*"
fly deploy "$@"
