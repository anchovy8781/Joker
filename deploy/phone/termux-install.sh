#!/data/data/com.termux/files/usr/bin/bash
# YouTube Exchange — phone server installer (run inside the Termux app).
#   curl -fsSL https://raw.githubusercontent.com/anchovy8781/Joker/HEAD/deploy/phone/termux-install.sh | bash
#
# Layout: PostgreSQL runs natively in Termux (Android-patched build); the Python API, worker
# and Next.js web server run inside an Ubuntu container (proot-distro) where prebuilt arm64
# packages exist. Everything is free; no root needed.
set -euo pipefail

REPO="${YTX_REPO:-https://github.com/anchovy8781/Joker.git}"
PGDATA_DIR="$HOME/ytx-pg"
SECRETS="$HOME/.ytx-secrets"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "1/5 Termux 패키지 설치"
pkg update -y
pkg install -y postgresql proot-distro git openssl-tool termux-api || pkg install -y postgresql proot-distro git openssl-tool

say "2/5 데이터베이스(PostgreSQL) 준비"
if [ ! -f "$SECRETS" ]; then
  umask 077
  echo "YTX_DB_PASSWORD=$(openssl rand -hex 24)" > "$SECRETS"
fi
# shellcheck disable=SC1090
. "$SECRETS"
if [ ! -d "$PGDATA_DIR" ]; then
  initdb -D "$PGDATA_DIR" -E UTF8 --locale=C >/dev/null
  # Only local connections; password auth for the app user.
  cat > "$PGDATA_DIR/pg_hba.conf" <<HBA
local   all   all                 trust
host    all   all   127.0.0.1/32  scram-sha-256
host    all   all   ::1/128       scram-sha-256
HBA
  echo "listen_addresses = '127.0.0.1'" >> "$PGDATA_DIR/postgresql.conf"
fi
pg_ctl -D "$PGDATA_DIR" -l "$HOME/ytx-pg.log" status >/dev/null 2>&1 || pg_ctl -D "$PGDATA_DIR" -l "$HOME/ytx-pg.log" start
sleep 2
psql -d postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='yex'" | grep -q 1 || \
  psql -d postgres -c "CREATE ROLE yex LOGIN PASSWORD '$YTX_DB_PASSWORD'"
psql -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='yex'" | grep -q 1 || createdb -O yex yex

say "3/5 Ubuntu 설치 (처음 한 번, 몇 분 걸려요)"
proot-distro list 2>/dev/null | grep -q "ubuntu.*installed" || proot-distro install ubuntu || true

say "4/5 앱 설치 (처음 한 번, 10~30분 걸릴 수 있어요)"
proot-distro login ubuntu --env YTX_DB_PASSWORD="$YTX_DB_PASSWORD" --env YTX_REPO="$REPO" -- bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y && apt-get install -y git ca-certificates curl
  if [ -d /opt/ytx/.git ]; then git -C /opt/ytx pull --ff-only; else git clone --depth 1 "$YTX_REPO" /opt/ytx; fi
  bash /opt/ytx/deploy/phone/setup.sh
'

say "5/5 자동 시작 설정"
mkdir -p "$HOME/.termux/boot" "$HOME/bin"
cat > "$HOME/bin/ytx-start" <<'START'
#!/data/data/com.termux/files/usr/bin/bash
# Start everything (also used by Termux:Boot after a reboot).
termux-wake-lock 2>/dev/null || true
pg_ctl -D "$HOME/ytx-pg" -l "$HOME/ytx-pg.log" status >/dev/null 2>&1 || pg_ctl -D "$HOME/ytx-pg" -l "$HOME/ytx-pg.log" start
sleep 3
exec proot-distro login ubuntu -- bash /opt/ytx/deploy/phone/start.sh
START
cat > "$HOME/bin/ytx-stop" <<'STOP'
#!/data/data/com.termux/files/usr/bin/bash
proot-distro login ubuntu -- bash /opt/ytx/deploy/phone/stop.sh || true
pg_ctl -D "$HOME/ytx-pg" stop || true
termux-wake-unlock 2>/dev/null || true
STOP
cat > "$HOME/bin/ytx-status" <<'STATUS'
#!/data/data/com.termux/files/usr/bin/bash
proot-distro login ubuntu -- bash /opt/ytx/deploy/phone/status.sh
STATUS
cat > "$HOME/bin/ytx-update" <<'UPDATE'
#!/data/data/com.termux/files/usr/bin/bash
. "$HOME/.ytx-secrets"
proot-distro login ubuntu -- bash /opt/ytx/deploy/phone/stop.sh || true
proot-distro login ubuntu --env YTX_DB_PASSWORD="$YTX_DB_PASSWORD" -- bash -c 'git -C /opt/ytx pull --ff-only && bash /opt/ytx/deploy/phone/setup.sh'
echo "업데이트 완료. ytx-start 로 다시 시작하세요."
UPDATE
cat > "$HOME/bin/ytx-admin" <<'ADMIN'
#!/data/data/com.termux/files/usr/bin/bash
# Usage: ytx-admin create-admin <name> | seed-demo --approve-by <name> | ...
proot-distro login ubuntu -- bash -c "cd /opt/ytx/backend && set -a && . /opt/ytx-data/env && set +a && .venv/bin/python -m app.cli $*"
ADMIN
cat > "$HOME/bin/ytx-token" <<'TOKEN'
#!/data/data/com.termux/files/usr/bin/bash
# Save the GitHub token that lets this phone publish its address for the app's auto-connect.
printf "GitHub 토큰 붙여넣기 (화면에 표시되지 않아요): "
read -rs TOKEN_VALUE; echo
[ -n "$TOKEN_VALUE" ] || { echo "취소됨"; exit 1; }
proot-distro login ubuntu --env T="$TOKEN_VALUE" -- bash -c \
  'sed -i "s|^YTX_GITHUB_TOKEN=.*|YTX_GITHUB_TOKEN=$T|" /opt/ytx-data/env && chmod 600 /opt/ytx-data/env'
echo "저장됨. ytx-start 로 서버를 (재)시작하면 앱이 자동으로 연결돼요."
TOKEN
chmod +x "$HOME/bin/"ytx-*
ln -sf "$HOME/bin/ytx-start" "$HOME/.termux/boot/ytx-start"
grep -q 'HOME/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"

cat <<MSG

==============================================================
 설치 완료!
 1) 관리자 만들기:   ~/bin/ytx-admin create-admin 내아이디
 2) (선택) 데모 종목: ~/bin/ytx-admin seed-demo --approve-by 내아이디
 3) 앱 자동 연결:    ~/bin/ytx-token   (GitHub 토큰 1회 저장, 안내서 참고)
 4) 서버 시작:       ~/bin/ytx-start
    → 앱을 열면 자동으로 연결돼요.
 재부팅 후 자동 시작: F-Droid에서 'Termux:Boot' 앱을 설치하고 한 번 열어두세요.
==============================================================
MSG
