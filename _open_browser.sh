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

# True if some browser is already running. We deliberately do NOT cold-start a
# browser from here: launching one fresh (e.g. Chrome) with `open` can trip its
# own startup crash (EXC_BREAKPOINT in ChromeMain). If none is running we just
# print the URL and let the user open it in their already-recovered browser.
_obp_browser_running() {
  local b
  for b in "Google Chrome" "Safari" "Arc" "firefox" "Microsoft Edge" \
           "Brave Browser" "Google Chrome Beta" "Chromium"; do
    pgrep -x "$b" >/dev/null 2>&1 && return 0
  done
  return 1
}

(
  # Poll for up to ~30s for the server to answer.
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$_OBP_URL"; then
      if _obp_browser_running; then
        open "$_OBP_URL"
      else
        printf '\n  ▶ App is ready — open this in your browser:\n     %s\n\n' "$_OBP_URL"
      fi
      break
    fi
    sleep 0.5
  done
) &
