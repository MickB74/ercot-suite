#!/bin/bash
# Double-click launcher: starts the Hornet Solar Settlement Portal (port 8505).
cd "$(dirname "$0")" || exit 1
PORT=8505
source "$(cd "$(dirname "$0")" && pwd)/../_ensure_venv.sh"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  open "http://localhost:$PORT"
else
  exec ./.venv/bin/streamlit run app/Home.py --server.port "$PORT" --server.headless true
fi
