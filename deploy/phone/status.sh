#!/usr/bin/env bash
DATA="${YTX_DATA_DIR:-/opt/ytx-data}"
ok() { curl -fs -o /dev/null --max-time 3 "$1" && echo "● 정상" || echo "✕ 응답 없음"; }
echo "API     : $(ok http://127.0.0.1:8000/healthz)"
echo "웹      : $(ok http://127.0.0.1:3000/)"
for n in worker tunnel; do
  if [ -f "$DATA/run/$n.pid" ] && kill -0 "$(cat "$DATA/run/$n.pid")" 2>/dev/null; then echo "$n$(printf '%*s' $((8-${#n})) ''): ● 실행 중"; else echo "$n$(printf '%*s' $((8-${#n})) ''): ✕ 꺼짐"; fi
done
echo
echo "공개 주소 (앱에 입력): $(cat "$DATA/run/public-url" 2>/dev/null || echo '아직 준비 안 됨 — 잠시 후 ytx-status')"
echo "로그: $DATA/logs/"
