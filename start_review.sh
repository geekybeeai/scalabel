#!/usr/bin/env bash
# Start Scalabel (:8686), Batch Review (:8687) and the QA review pages (:8790) together.
# Ctrl+C stops all three. Logs: local-data/logs/. Redis must be running first:
#     docker start scalabel-redis
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/local-data/logs"
mkdir -p "$LOGS"

up() { nc -z localhost "$1" >/dev/null 2>&1; }

if ! up 6379; then
  echo "WARNING: Redis is not running on 6379; the Scalabel label page will stay blank."
  echo "         Start it with:  docker start scalabel-redis"
fi

PIDS=()
start() { # name port log dir command...
  local name=$1 port=$2 log=$3 dir=$4; shift 4
  if up "$port"; then
    echo "  $name is already running on port $port (left as is)"
    return
  fi
  (cd "$dir" && exec "$@") >"$LOGS/$log" 2>&1 &
  PIDS+=($!)
  echo "  starting $name (log: local-data/logs/$log)"
}

stop() {
  echo
  echo "Stopping..."
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  echo "Stopped."
  exit 0
}
trap stop INT TERM

echo "Starting:"
start "Scalabel" 8686 scalabel.log "$ROOT" \
  node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml
# Batch Review reads its project list from Scalabel, so give Scalabel a head start
for _ in $(seq 1 30); do up 8686 && break; sleep 1; done
start "Batch Review" 8687 batch_review.log "$ROOT/tools/batch_review" node server.js
start "QA review pages" 8790 qa_review.log "$ROOT/tools" \
  python3 -m annotation_qa review --out ../local-data/items/Full_Verfication/qa_2026_10_17 --port 8790

for _ in $(seq 1 20); do up 8687 && up 8790 && break; sleep 1; done
echo
for x in "8686|Scalabel       |http://localhost:8686" "8687|Batch Review   |http://localhost:8687" \
         "8790|QA review pages|http://127.0.0.1:8790/viewb"; do
  IFS='|' read -r port name url <<<"$x"
  if up "$port"; then echo "  OK    $name  $url"; else echo "  FAIL  $name  (see local-data/logs/)"; fi
done
echo
echo "Press Ctrl+C to stop all three."
if [ ${#PIDS[@]} -eq 0 ]; then exit 0; fi
wait
