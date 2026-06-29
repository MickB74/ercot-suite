#!/bin/bash
# Double-click launcher: starts the Aguayo Wind Settlement Portal (port 8511).
cd "$(dirname "$0")" || exit 1
PORT=8511
source "$(cd "$(dirname "$0")" && pwd)/../_ensure_venv.sh"
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec ./.venv/bin/streamlit run app/Home.py --server.port "$PORT" --server.headless true
