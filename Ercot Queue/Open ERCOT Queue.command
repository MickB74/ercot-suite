#!/bin/bash
# Double-click launcher: starts the standalone ERCOT Queue Explorer app (port 8525).
# Reuses the ERCOT Data Hub's virtual environment + data lake (no duplicate
# install), so the queue, engine and data always match the Hub.
cd "$(dirname "$0")" || exit 1
PORT=8525
HUB="../Ercot_Data_Hub"
if [ ! -x "$HUB/.venv/bin/streamlit" ]; then
  echo "Setting up the ERCOT Data Hub environment (first run)…"
  ( cd "$HUB" && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt ) || exit 1
fi
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec "$HUB/.venv/bin/streamlit" run app.py --server.port "$PORT" --server.headless true
