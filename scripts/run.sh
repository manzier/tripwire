#!/usr/bin/env bash
# Start Tripwire under nohup with rotating-friendly log files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

mkdir -p logs
VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing venv at ${VENV}. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export PYTHONUNBUFFERED=1
nohup "$PY" -m tripwire run \
  --config "${TRIPWIRE_CONFIG:-config/watchlist.yaml}" \
  --log "${TRIPWIRE_ALERT_LOG:-logs/alerts.jsonl}" \
  --heartbeat "${TRIPWIRE_HEARTBEAT:-logs/heartbeat.json}" \
  >> logs/tripwire.out 2>> logs/tripwire.err &

echo $! > logs/tripwire.pid
echo "Tripwire started pid=$(cat logs/tripwire.pid)"
echo "  stdout -> logs/tripwire.out"
echo "  stderr -> logs/tripwire.err"
echo "  alerts -> logs/alerts.jsonl"
