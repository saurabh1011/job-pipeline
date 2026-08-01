#!/bin/bash
# Runs the job-pipeline web server locally, bound to localhost only.
# Invoked by the launchd LaunchAgent installed via setup_local_pipeline.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/.env.local" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env.local"
  set +a
fi

exec "$REPO_ROOT/.venv/bin/python3" -m uvicorn web.server:app \
  --host 127.0.0.1 --port 8000
