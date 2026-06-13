#!/usr/bin/env bash
# OpenJarvis — start both servers with auto-restart
# Usage: ./start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_LOG="/tmp/jarvis-backend.log"
FRONTEND_LOG="/tmp/jarvis-frontend.log"
PIDFILE_BACK="/tmp/jarvis-backend.pid"
PIDFILE_FRONT="/tmp/jarvis-frontend.pid"

# Kill any existing instances
cleanup() {
  for pf in "$PIDFILE_BACK" "$PIDFILE_FRONT"; do
    if [[ -f "$pf" ]]; then
      kill "$(cat "$pf")" 2>/dev/null
      rm -f "$pf"
    fi
  done
  lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null
  lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null
}

trap cleanup EXIT

cleanup
sleep 1

# --- Backend (port 8000) with auto-restart ---
(
  while true; do
    echo "[$(date)] Starting backend..." >> "$BACKEND_LOG"
    "$SCRIPT_DIR/.venv/bin/python" -c \
      "from openjarvis.cli.serve import serve; serve.main(['--port', '8000'])" \
      >> "$BACKEND_LOG" 2>&1
    echo "[$(date)] Backend exited ($?). Restarting in 2s..." >> "$BACKEND_LOG"
    sleep 2
  done
) &
echo $! > "$PIDFILE_BACK"

# --- Frontend (port 5173) with auto-restart ---
(
  cd "$SCRIPT_DIR/frontend" || exit 1
  while true; do
    echo "[$(date)] Starting frontend..." >> "$FRONTEND_LOG"
    npx vite --host >> "$FRONTEND_LOG" 2>&1
    echo "[$(date)] Frontend exited ($?). Restarting in 2s..." >> "$FRONTEND_LOG"
    sleep 2
  done
) &
echo $! > "$PIDFILE_FRONT"

echo "OpenJarvis started"
echo "  Backend:  http://127.0.0.1:8000  (log: $BACKEND_LOG)"
echo "  Frontend: http://localhost:5173   (log: $FRONTEND_LOG)"
echo "  PIDs: backend=$(cat $PIDFILE_BACK) frontend=$(cat $PIDFILE_FRONT)"
echo ""
echo "Both servers auto-restart if they crash."
echo "To stop: kill $(cat $PIDFILE_BACK) $(cat $PIDFILE_FRONT)"

# Keep alive
wait
