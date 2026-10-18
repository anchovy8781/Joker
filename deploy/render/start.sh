#!/bin/bash
# Migrate, then run API (127.0.0.1:8000) and web (public $PORT). If either exits, the
# container exits and Render restarts it.
set -euo pipefail
cd /app/backend
python -m alembic upgrade head
python -m app.cli bootstrap   # first admin / demo stocks from env (Render free has no shell)

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --log-level warning &
API=$!
# Open the public port only once the API answers, so health checks and early visitors are not
# proxied to a port that is still starting (ECONNREFUSED noise in the logs).
for _ in $(seq 1 90); do
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ping', timeout=2)" 2>/dev/null && break
  kill -0 "$API" 2>/dev/null || { echo "API exited during startup" >&2; exit 1; }
  sleep 1
done
cd /app/web
PORT="${PORT:-10000}" HOSTNAME=0.0.0.0 node server.js &
WEB=$!

# Optional: publish this service's address for the app's auto-connect (needs YTX_GITHUB_TOKEN).
if [ -n "${YTX_GITHUB_TOKEN:-}" ] && [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  (cd /app && YTX_PUBLIC_URL="$RENDER_EXTERNAL_URL" YTX_DATA_DIR=/tmp/ytx python address_watcher.py --once) || true
fi

wait -n "$API" "$WEB"
echo "a process exited; stopping so the platform restarts the service" >&2
kill "$API" "$WEB" 2>/dev/null || true
exit 1
