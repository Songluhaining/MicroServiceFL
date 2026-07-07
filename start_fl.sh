#!/usr/bin/env bash
# start_fl.sh - start/stop the autonomous fault-localization service
# (metric+log collectors + the statistical monitor). No fault injection.
#
#   ./start_fl.sh [start|stop|status|restart]     (default: start)
#
# Config comes from ./fl.env (+ ./deepseek_key.txt). Watch knobs can be
# overridden via env: FL_INTERVAL / FL_WINDOW / FL_WARMUP / FL_COOLDOWN / FL_K.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "venv missing - run ./install-fl.sh first" >&2; exit 1; }

# load deployment config + key
if [ -f "$ROOT/fl.env" ]; then set -a; . "$ROOT/fl.env"; set +a; fi
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$ROOT/deepseek_key.txt" ]; then
  export DEEPSEEK_API_KEY="$(tr -d '[:space:]' < "$ROOT/deepseek_key.txt")"
fi

# where logs/reports go: alongside the metric CSV, else ./live-data
LIVE_DIR="$(dirname "${OH_FL_METRIC_CSV:-$ROOT/live-data/metric.csv}")"
mkdir -p "$LIVE_DIR" "$LIVE_DIR/incidents"

start() {
  stop_quiet
  echo "starting collectors -> $LIVE_DIR/collect.log"
  nohup "$PY" -m microservice_fl collect > "$LIVE_DIR/collect.log" 2>&1 &
  sleep 3
  echo "starting monitor    -> $LIVE_DIR/watch.log"
  nohup "$PY" -m microservice_fl watch \
    --interval "${FL_INTERVAL:-60}" --window "${FL_WINDOW:-180}" \
    --warmup "${FL_WARMUP:-15}" --cooldown "${FL_COOLDOWN:-600}" --k "${FL_K:-3.0}" \
    --out "$LIVE_DIR/incidents" > "$LIVE_DIR/watch.log" 2>&1 &
  sleep 2
  echo
  status
  echo
  echo "watching normal traffic to learn baselines; incidents -> $LIVE_DIR/incidents/"
  echo "tail:  tail -f $LIVE_DIR/watch.log      stop:  ./start_fl.sh stop"
}

stop_quiet() {
  pkill -f "microservice_fl collect" 2>/dev/null || true
  pkill -f "microservice_fl watch" 2>/dev/null || true
  sleep 1
}

stop() { echo "stopping..."; stop_quiet; status; }

status() {
  if pgrep -f "microservice_fl collect" >/dev/null; then
    echo "collect: RUNNING (pid $(pgrep -f 'microservice_fl collect' | tr '\n' ' '))"
  else echo "collect: stopped"; fi
  if pgrep -f "microservice_fl watch" >/dev/null; then
    echo "watch  : RUNNING (pid $(pgrep -f 'microservice_fl watch' | tr '\n' ' '))"
  else echo "watch  : stopped"; fi
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  status)  status ;;
  restart) stop_quiet; start ;;
  *) echo "usage: $0 [start|stop|status|restart]" >&2; exit 1 ;;
esac
