#!/usr/bin/env bash
# Inside Ubuntu: start API, worker, web and the public tunnel, each restarted automatically
# if it crashes. Prints the public https address.
set -uo pipefail
APP="${YTX_APP_DIR:-/opt/ytx}"
DATA="${YTX_DATA_DIR:-/opt/ytx-data}"
export PATH="$DATA/node/bin:$DATA/bin:$PATH"
set -a; . "$DATA/env"; set +a
LOGS="$DATA/logs"; RUN="$DATA/run"
mkdir -p "$LOGS" "$RUN"

bash "$(dirname "$0")/stop.sh" >/dev/null 2>&1 || true

# Wait for the database (runs in Termux).
for _ in $(seq 1 30); do
  "$APP/backend/.venv/bin/python" - <<'PY' 2>/dev/null && break
import os, psycopg
psycopg.connect(os.environ["YEX_DATABASE_URL"].replace("postgresql+psycopg", "postgresql"), connect_timeout=2).close()
PY
  echo "데이터베이스 대기 중…"; sleep 2
done

supervise() {  # name, command...  -> restarts the command whenever it exits
  local name="$1"; shift
  ( while true; do
      echo "[$(date '+%F %T')] start $name" >> "$LOGS/$name.log"
      "$@" >> "$LOGS/$name.log" 2>&1
      echo "[$(date '+%F %T')] $name exited ($?), restarting in 5s" >> "$LOGS/$name.log"
      sleep 5
    done ) &
  echo $! > "$RUN/$name.pid"
}

cd "$APP/backend"
supervise api .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
supervise worker .venv/bin/python -m app.worker
cd "$APP/frontend/.next/standalone"
supervise web env PORT=3000 HOSTNAME=127.0.0.1 NODE_ENV=production node server.js

rm -f "$RUN/public-url"
: > "$LOGS/tunnel.log"  # fresh log so an old quick-tunnel address is never re-published
if [ -n "${NGROK_AUTHTOKEN:-}" ] && [ -n "${NGROK_DOMAIN:-}" ] && [ -x "$DATA/bin/ngrok" ]; then
  supervise tunnel "$DATA/bin/ngrok" http --authtoken "$NGROK_AUTHTOKEN" --domain "$NGROK_DOMAIN" --log stdout 127.0.0.1:3000
elif [ -x "$DATA/bin/cloudflared" ]; then
  supervise tunnel cloudflared tunnel --no-autoupdate --url http://127.0.0.1:3000
fi
# Publishes the current address for the app's auto-connect (also after tunnel restarts).
supervise address env YTX_DATA_DIR="$DATA" "$APP/backend/.venv/bin/python" "$APP/deploy/phone/address_watcher.py"

echo "서버 시작 중…"
for _ in $(seq 1 90); do
  curl -fs -o /dev/null http://127.0.0.1:3000/ && [ -s "$RUN/public-url" ] && break
  sleep 2
done
bash "$(dirname "$0")/status.sh"

# Keep the session alive (Termux:Boot / proot) while the supervised processes run.
if [ -z "${YTX_NO_WAIT:-}" ]; then wait; fi
