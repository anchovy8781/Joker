#!/usr/bin/env bash
DATA="${YTX_DATA_DIR:-/opt/ytx-data}"
for f in "$DATA"/run/*.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  pkill -TERM -P "$pid" 2>/dev/null || true  # the running service
  kill "$pid" 2>/dev/null || true             # its restart loop
  rm -f "$f"
done
rm -f "$DATA/run/public-url"
echo "중지됨"
