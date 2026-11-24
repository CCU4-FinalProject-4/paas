#!/usr/bin/env bash
# Usage: probe.sh <csv>. Hits the app every 100 ms and logs "epoch_ms,status,served_by" until killed.
set -uo pipefail

out=$1
url=${PROBE_URL:-http://localhost:8080/api/health}
echo "ts_ms,status,served_by" > "$out"

while true; do
  body=$(curl -s --max-time 2 -w '\n%{http_code}' "$url")
  status=${body##*$'\n'}
  served_by=$(sed -n 's/.*"served_by":"\([^"]*\)".*/\1/p' <<< "$body")
  echo "$(python3 -c 'import time; print(int(time.time() * 1000))'),$status,$served_by" >> "$out"
  sleep 0.1
done
