#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$ROOT/logs/tripwire.pid"
if [[ ! -f "$PIDFILE" ]]; then
  echo "No pid file at $PIDFILE"
  exit 0
fi
PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped Tripwire pid=$PID"
else
  echo "Process $PID not running"
fi
rm -f "$PIDFILE"
