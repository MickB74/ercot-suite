#!/bin/bash
# Sourced by each portal/app launcher (not run directly).
# Opens the default browser at the app's URL — in the background — once the
# Streamlit server is actually reachable. We do this ourselves (with the
# server started --server.headless true) instead of letting Streamlit auto-open
# the browser: Streamlit flings the URL at the browser the instant it boots,
# and when the browser isn't already running that race crashes it on cold start.
#
# Usage (after cd into the app dir):  source "<suite_root>/_open_browser.sh" "$PORT"

_OBP_PORT="${1:-8501}"
_OBP_URL="http://localhost:${_OBP_PORT}"
(
  # Poll for up to ~30s for the server to answer, then open the tab once.
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$_OBP_URL"; then
      open "$_OBP_URL"
      break
    fi
    sleep 0.5
  done
) &
