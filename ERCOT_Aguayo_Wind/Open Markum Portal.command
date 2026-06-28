#!/bin/bash
# Double-click launcher: starts the Aguayo Wind Settlement Portal.
# Pinned to port 8502 so the Control Tower "Open portal" link always matches,
# and so repeat double-clicks can't spawn stray instances on auto-picked ports.
cd "$(dirname "$0")" || exit 1
PORT=8502
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  open "http://localhost:$PORT"
else
  exec ./.venv/bin/streamlit run app/Home.py --server.port "$PORT"
fi
