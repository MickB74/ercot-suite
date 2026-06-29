#!/bin/bash
# Double-click launcher: starts the unified ERCOT Data Hub Streamlit app.
cd "$(dirname "$0")" || exit 1
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi

PORT=8501
URL="http://localhost:${PORT}"

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
