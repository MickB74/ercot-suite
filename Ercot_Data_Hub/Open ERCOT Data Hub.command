#!/bin/bash
# Double-click launcher: starts the unified ERCOT Data Hub Streamlit app.
cd "$(dirname "$0")" || exit 1

PORT=8501
URL="http://localhost:${PORT}"

# If it's already running, just open the browser and stop.
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  open "$URL"
  exit 0
fi

# Ensure .venv exists, matches the current python3, and has a working streamlit.
source "$(cd "$(dirname "$0")" && pwd)/../_ensure_venv.sh"

# Open the browser ourselves once the server is actually reachable, instead of
# letting Streamlit fling the URL at the browser during its own cold start
# (that race crashes Chrome when Chrome isn't already running).
(
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$URL"; then
      open "$URL"
      break
    fi
    sleep 0.5
  done
) &

# --server.headless=true stops Streamlit from auto-launching the browser.
exec ./.venv/bin/streamlit run app/Home.py \
  --server.port "$PORT" \
  --server.headless true
