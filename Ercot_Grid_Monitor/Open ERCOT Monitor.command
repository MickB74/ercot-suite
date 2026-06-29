#!/bin/bash
# Double-click launcher: starts the ERCOT Grid Monitor (price map + alerts) (port 8520).
cd "$(dirname "$0")" || exit 1
PORT=8520
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec ./.venv/bin/streamlit run app.py --server.port "$PORT" --server.headless true
