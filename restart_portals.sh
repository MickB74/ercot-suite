#!/usr/bin/env bash
# Restart ERCOT portal Streamlit servers.
#
# AUTO-DISCOVERS every portal in the suite — any directory with `app/Home.py`, a
# `.venv/bin/streamlit`, and an `Open *.command` launcher — and reads each
# portal's PINNED port straight from that launcher (`PORT=…` or `--server.port`).
# So when you add a new portal, this script picks it up with NO edits.
#
# Running portals are found and stopped BY THEIR PORT (the only reliable handle —
# the streamlit process command line doesn't carry the portal's directory name),
# then relaunched headless on that same pinned port.
#
# Usage:
#   ./restart_portals.sh                  restart whichever portals are running
#   ./restart_portals.sh all              restart (start) every discovered portal
#   ./restart_portals.sh list             list portals, ports, and running status
#   ./restart_portals.sh heart markum     restart portals whose name matches a word
#
set -uo pipefail

SUITE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── discover portals → parallel arrays DIRS / PORTS / KEYS ───────────────────
DIRS=(); PORTS=(); KEYS=()
discover() {
  local d base cmd port key
  for d in "$SUITE"/*/; do
    d="${d%/}"; base="$(basename "$d")"
    [ -f "$d/app/Home.py" ] || continue
    [ -x "$d/.venv/bin/streamlit" ] || continue
    cmd="$(ls "$d"/Open*.command 2>/dev/null | head -1)"
    port=""
    if [ -n "$cmd" ]; then
      port="$(grep -m1 -E '^PORT=' "$cmd" | sed -E 's/^PORT=//; s/"//g')"
      [ -z "$port" ] && port="$(grep -m1 -oE -- '--server.port[ =]+[0-9]+' "$cmd" \
                                | grep -oE '[0-9]+' | head -1)"
    fi
    [ -z "$port" ] && port=8501                      # Streamlit default (Data Hub)
    key="$(echo "$base" | tr '[:upper:]' '[:lower:]' | sed -E 's/^e?rcot_//')"
    DIRS+=("$base"); PORTS+=("$port"); KEYS+=("$key")
  done
}

port_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

pids_on_port() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

kill_port() {
  local port="$1" pids
  pids="$(pids_on_port "$port")"
  [ -z "$pids" ] && return 0
  echo "    stopping PID(s) $(echo "$pids" | tr '\n' ' ')on :$port…"
  kill $pids 2>/dev/null || true
  for _ in 1 2 3 4 5; do port_listening "$port" || break; sleep 1; done
  if port_listening "$port"; then
    echo "    forcing…"; kill -9 $pids 2>/dev/null || true; sleep 1
  fi
}

start_portal() {
  local i="$1" dir="${DIRS[$1]}" port="${PORTS[$1]}" key="${KEYS[$1]}" log
  log="/tmp/streamlit_${key}.log"
  echo "    starting ${dir} on :${port} …"
  ( cd "$SUITE/$dir" && nohup ./.venv/bin/streamlit run app/Home.py \
      --server.port "$port" --server.headless true > "$log" 2>&1 & )
  echo "      log: $log"
}

restart_index() {
  local i="$1"
  echo "  • ${DIRS[$i]}  (:${PORTS[$i]})"
  kill_port "${PORTS[$i]}"
  start_portal "$i"
}

discover
if [ "${#DIRS[@]}" -eq 0 ]; then
  echo "No portals found under $SUITE."; exit 1
fi

target="${1:-running}"

# ── list mode ────────────────────────────────────────────────────────────────
if [ "$target" = "list" ]; then
  echo "Portals discovered under $SUITE:"
  printf "  %-30s %-6s %s\n" "PORTAL" "PORT" "STATUS"
  for i in "${!DIRS[@]}"; do
    if port_listening "${PORTS[$i]}"; then status="● running"; else status="○ stopped"; fi
    printf "  %-30s %-6s %s\n" "${DIRS[$i]}" "${PORTS[$i]}" "$status"
  done
  exit 0
fi

# ── pick which indices to restart ────────────────────────────────────────────
selected=()
if [ "$target" = "all" ]; then
  for i in "${!DIRS[@]}"; do selected+=("$i"); done
elif [ "$target" = "running" ]; then
  for i in "${!DIRS[@]}"; do port_listening "${PORTS[$i]}" && selected+=("$i"); done
  if [ "${#selected[@]}" -eq 0 ]; then
    echo "No portals are currently running."
    echo "Run with 'all' to start every portal, 'list' to see them, or name one:"
    printf '   %s\n' "${KEYS[@]}"
    exit 0
  fi
else
  # one or more name words → case-insensitive substring match on dir or key
  for arg in "$@"; do
    a="$(echo "$arg" | tr '[:upper:]' '[:lower:]')"
    found=0
    for i in "${!DIRS[@]}"; do
      d="$(echo "${DIRS[$i]}" | tr '[:upper:]' '[:lower:]')"
      if [[ "$d" == *"$a"* || "${KEYS[$i]}" == *"$a"* ]]; then
        selected+=("$i"); found=1
      fi
    done
    [ "$found" -eq 0 ] && echo "⚠️  no portal matches '${arg}' (try: ./restart_portals.sh list)"
  done
  # de-dup indices
  selected=($(printf '%s\n' "${selected[@]}" | sort -un))
  [ "${#selected[@]}" -eq 0 ] && { echo "Nothing to do."; exit 1; }
fi

echo "=== Restarting ${#selected[@]} portal(s) ==="
for i in "${selected[@]}"; do restart_index "$i"; done
sleep 2

echo "=== Status ==="
for i in "${selected[@]}"; do
  if port_listening "${PORTS[$i]}"; then mark="✅ up"; else mark="❌ not listening (check /tmp/streamlit_${KEYS[$i]}.log)"; fi
  printf "  %-30s :%-5s %s\n" "${DIRS[$i]}" "${PORTS[$i]}" "$mark"
done
echo "Done."
