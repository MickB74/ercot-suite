#!/usr/bin/env bash
# Interactive launcher for the ERCOT portals.
#
# AUTO-DISCOVERS every portal (any dir with app/Home.py, a .venv/bin/streamlit,
# and an "Open *.command" launcher) and reads each one's PINNED port from that
# launcher. Shows a menu; pick one to start (or just open it if already running).
# New portals appear automatically — no edits needed.
#
# Usage:
#   ./launch_portal.sh              interactive menu
#   ./launch_portal.sh heart        start/open the portal matching a name
#   ./launch_portal.sh all          start/open every portal
#
set -uo pipefail
SUITE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DIRS=(); PORTS=(); KEYS=(); LABELS=()
discover() {
  local d base cmd port key label
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
    [ -z "$port" ] && port=8501
    key="$(echo "$base" | tr '[:upper:]' '[:lower:]' | sed -E 's/^e?rcot_//')"
    label="$(echo "$base" | sed -E 's/^E?RCOT_//; s/_/ /g')"
    DIRS+=("$base"); PORTS+=("$port"); KEYS+=("$key"); LABELS+=("$label")
  done
}

port_up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

launch() {
  local i="$1" dir="${DIRS[$1]}" port="${PORTS[$1]}" key="${KEYS[$1]}" url
  url="http://localhost:${port}"
  if port_up "$port"; then
    echo "  ${LABELS[$i]} already running on :${port} — opening…"
  else
    echo "  starting ${LABELS[$i]} on :${port} …"
    ( cd "$SUITE/$dir" && nohup ./.venv/bin/streamlit run app/Home.py \
        --server.port "$port" --server.headless true \
        > "/tmp/streamlit_${key}.log" 2>&1 & )
    printf "  waiting for it to come up"
    for _ in $(seq 1 20); do port_up "$port" && break; printf "."; sleep 1; done
    echo
    if ! port_up "$port"; then
      echo "  ⚠️  didn't start — see /tmp/streamlit_${key}.log"; return 1
    fi
  fi
  open "$url" 2>/dev/null || echo "  open $url in your browser"
}

discover
[ "${#DIRS[@]}" -eq 0 ] && { echo "No portals found under $SUITE."; exit 1; }

# ── non-interactive: name or 'all' ───────────────────────────────────────────
if [ "$#" -ge 1 ]; then
  if [ "$1" = "all" ]; then
    for i in "${!DIRS[@]}"; do launch "$i"; done; exit 0
  fi
  a="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  for i in "${!DIRS[@]}"; do
    d="$(echo "${DIRS[$i]}" | tr '[:upper:]' '[:lower:]')"
    if [[ "$d" == *"$a"* || "${KEYS[$i]}" == *"$a"* ]]; then launch "$i"; exit $?; fi
  done
  echo "No portal matches '$1'. Available: ${KEYS[*]}"; exit 1
fi

# ── interactive menu ─────────────────────────────────────────────────────────
while true; do
  echo
  echo "════════════════════════════════════════════════════════"
  echo "  Launch an ERCOT Portal"
  echo "════════════════════════════════════════════════════════"
  for i in "${!DIRS[@]}"; do
    if port_up "${PORTS[$i]}"; then dot="● running"; else dot="○ stopped"; fi
    printf "  %2d) %-28s :%-5s %s\n" "$((i+1))" "${LABELS[$i]}" "${PORTS[$i]}" "$dot"
  done
  echo "   a) Start / open ALL"
  echo "   q) Quit"
  echo
  printf "Pick a number: "
  read -r choice || exit 0
  case "$choice" in
    q|Q|"") echo "Bye."; exit 0 ;;
    a|A) for i in "${!DIRS[@]}"; do launch "$i"; done ;;
    *)
      if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#DIRS[@]}" ]; then
        launch "$((choice-1))"
      else
        echo "  ?? '$choice' isn't on the menu."
      fi ;;
  esac
done
