#!/usr/bin/env bash
# Inside Ubuntu (proot-distro). Idempotent: safe to re-run for updates.
# Needs YTX_DB_PASSWORD (passed from Termux). Database runs in Termux at 127.0.0.1:5432.
set -euo pipefail

APP="${YTX_APP_DIR:-/opt/ytx}"
DATA="${YTX_DATA_DIR:-/opt/ytx-data}"
NODE_VERSION="v22.22.0"
CLOUDFLARED_VERSION="2024.12.2"
say() { printf '\n\033[1;36m--> %s\033[0m\n' "$*"; }

case "$(uname -m)" in
  aarch64|arm64) NODE_ARCH=arm64; CF_ARCH=arm64 ;;
  x86_64|amd64) NODE_ARCH=x64; CF_ARCH=amd64 ;;
  armv7l) NODE_ARCH=armv7l; CF_ARCH=arm ;;
  *) echo "unsupported CPU: $(uname -m)"; exit 1 ;;
esac
mkdir -p "$DATA/bin" "$DATA/logs" "$DATA/run"

say "시스템 패키지"
if [ -z "${YTX_SKIP_APT:-}" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends python3 python3-venv python3-pip curl ca-certificates xz-utils git
fi

say "Node.js $NODE_VERSION"
if [ ! -x "$DATA/node/bin/node" ] || [ "$("$DATA/node/bin/node" --version)" != "$NODE_VERSION" ]; then
  rm -rf "$DATA/node" && mkdir -p "$DATA/node"
  curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-$NODE_ARCH.tar.xz" | tar -xJ -C "$DATA/node" --strip-components=1
fi
export PATH="$DATA/node/bin:$PATH"

say "공개 주소 도구 (cloudflared)"
if [ ! -x "$DATA/bin/cloudflared" ] && [ -z "${YTX_SKIP_TUNNEL_DOWNLOAD:-}" ]; then
  curl -fsSL -o "$DATA/bin/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/download/$CLOUDFLARED_VERSION/cloudflared-linux-$CF_ARCH"
  chmod +x "$DATA/bin/cloudflared"
fi

if [ -f "$DATA/env" ] && grep -q '^NGROK_AUTHTOKEN=.' "$DATA/env" && [ ! -x "$DATA/bin/ngrok" ]; then
  say "고정 주소 도구 (ngrok)"
  curl -fsSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-$CF_ARCH.tgz" | tar -xz -C "$DATA/bin"
fi

say "설정 파일 ($DATA/env)"
if [ ! -f "$DATA/env" ]; then
  : "${YTX_DB_PASSWORD:?YTX_DB_PASSWORD is required on first setup}"
  umask 077
  cat > "$DATA/env" <<ENV
YEX_ENV=production
YEX_DATABASE_URL=postgresql+psycopg://yex:${YTX_DB_PASSWORD}@127.0.0.1:5432/yex
YEX_IP_HASH_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
YEX_COOKIE_SECURE=true
YEX_CORS_ORIGINS=[]
YEX_TRUSTED_PROXY_HOPS=1
YEX_YOUTUBE_API_KEY=
# 앱 자동 연결: 이 저장소에만 Contents 읽기/쓰기 권한이 있는 GitHub fine-grained 토큰
YTX_GITHUB_TOKEN=
YTX_GITHUB_REPO=anchovy8781/Joker
# 고정 주소(선택): 무료 ngrok 계정의 authtoken 과 고정 도메인(예: abc.ngrok-free.app)
NGROK_AUTHTOKEN=
NGROK_DOMAIN=
ENV
fi

say "백엔드 (Python)"
cd "$APP/backend"
[ -x .venv/bin/python ] || python3 -m venv .venv
[ -x .venv/bin/pip ] || .venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
set -a; . "$DATA/env"; set +a
.venv/bin/alembic upgrade head

say "프론트엔드 (Next.js) 빌드 — 휴대폰에서는 몇 분 걸려요"
cd "$APP/frontend"
npm ci --no-audit --no-fund --omit=optional 2>/dev/null || npm ci --no-audit --no-fund
NEXT_TELEMETRY_DISABLED=1 BACKEND_URL=http://127.0.0.1:8000 NODE_OPTIONS=--max-old-space-size=1536 npx next build
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public

say "준비 완료"
