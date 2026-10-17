#!/usr/bin/env bash
# Run the whole exchange on this computer and expose it on a free public HTTPS address.
#   ./scripts/start-public.sh            first run: creates .env with random secrets
# Requirements: Docker (Docker Desktop on Windows/macOS). No paid service, account or domain.
set -euo pipefail
cd "$(dirname "$0")/.."

rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))" 2>/dev/null || openssl rand -base64 "$1" | tr -d '/+=\n'; }

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i.bak "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 24)|" .env
  sed -i.bak "s|^YEX_IP_HASH_SECRET=.*|YEX_IP_HASH_SECRET=$(rand 48)|" .env
  rm -f .env.bak
  echo "Created .env with random secrets."
fi
# Public mode: HTTPS via the tunnel, production safety checks on.
set_kv() { grep -q "^$1=" .env && sed -i.bak "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; rm -f .env.bak; }
set_kv YEX_ENV production
set_kv YEX_COOKIE_SECURE true
set_kv YEX_TRUSTED_PROXY_HOPS 1
set_kv YEX_CORS_ORIGINS '[]'

docker compose --profile public up -d --build

echo "Waiting for the public address..."
for _ in $(seq 1 60); do
  URL=$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
  [ -n "${URL:-}" ] && break
  sleep 2
done

cat <<MSG

==========================================================
 YouTube Exchange is running.
 Public address (enter this in the Android app):
   ${URL:-<not ready yet: run 'docker compose logs tunnel'>}

 First time only:
   docker compose exec api python -m app.cli create-admin <your-name>
   docker compose exec api python -m app.cli seed-demo --approve-by <your-name>   # optional demo stocks
 Stop:  docker compose --profile public down   (data is kept)
==========================================================
MSG
