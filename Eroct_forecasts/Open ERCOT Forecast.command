#!/bin/bash
# Double-click launcher: starts the ERCOT Price Forecast Streamlit app (port 8523).
cd "$(dirname "$0")" || exit 1
PORT=8523
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec ./.venv/bin/streamlit run app.py --server.port "$PORT" --server.headless true
