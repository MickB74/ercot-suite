#!/bin/bash
# Double-click launcher: starts the Millers Branch 2 Settlement Portal (port 8510).
cd "$(dirname "$0")" || exit 1
PORT=8510
source "$(cd "$(dirname "$0")" && pwd)/../_ensure_venv.sh"
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec ./.venv/bin/streamlit run app/Home.py --server.port "$PORT" --server.headless true
