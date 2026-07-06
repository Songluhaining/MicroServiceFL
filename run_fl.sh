#!/usr/bin/env bash
# run_fl.sh - launch the MicroServiceFL localizer (Linux/macOS).
#
#   ./run_fl.sh                        # interactive REPL (watch each tool call)
#   ./run_fl.sh "time=... symptom=..." # one-shot, final answer
#   ./run_fl.sh --trace "..."          # one-shot, full tool-call event stream
#
# Deployment config (paths, SkyWalking URL, key) goes in a local `fl.env` next to
# this script — copy fl.env.example to fl.env and edit. fl.env is git-ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT/.venv/bin/python"

[ -x "$VENV_PY" ] || { echo "venv missing - run ./install-fl.sh first" >&2; exit 1; }

# load deployment config
if [ -f "$ROOT/fl.env" ]; then set -a; . "$ROOT/fl.env"; set +a; fi
# load the DeepSeek key from a file if not already in the environment
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$ROOT/deepseek_key.txt" ]; then
  export DEEPSEEK_API_KEY="$(tr -d '[:space:]' < "$ROOT/deepseek_key.txt")"
fi

case "${1:-}" in
  "")       exec "$VENV_PY" -m microservice_fl.repl ;;
  --trace)  shift; exec "$VENV_PY" -m openharness -p "/locate $*" \
              --output-format stream-json --permission-mode auto ;;
  *)        exec "$VENV_PY" -m openharness -p "/locate $*" \
              --output-format text --permission-mode auto ;;
esac
