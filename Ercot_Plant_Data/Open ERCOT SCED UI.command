#!/bin/bash
# Double-click to open the ERCOT Plant SCED web UI in your browser (port 8524).
cd "$(dirname "$0")" || exit 1
PORT=8524
if [ ! -d ".venv" ]; then
    echo "First run: creating Python environment..."
    python3 -m venv .venv || { echo "Could not create venv. Is Python 3 installed?"; read -r; exit 1; }
fi
# Ensure deps (incl. streamlit) are present.
./.venv/bin/pip install -q -r requirements.txt
source "$(cd "$(dirname "$0")" && pwd)/../_open_browser.sh" "$PORT"
echo "Starting the UI — your browser will open. Close this window to stop the app."
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0  # already running — the opener above will open the tab
fi
exec ./.venv/bin/streamlit run app.py --server.port "$PORT" --server.headless true
