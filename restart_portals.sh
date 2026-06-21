#!/usr/bin/env bash
# Restart all ERCOT portal Streamlit servers.
# Usage:  ./restart_portals.sh [markum|hidalgo|azure|miller|all]
# Default (no arg) = restart whichever portals are currently running.

set -euo pipefail

SUITE="/Users/michaelbarry/Documents/Github/ercot-suite"

ALL_KEYS=(markum hidalgo azure miller)

dir_for() {
  case "$1" in
    markum)  echo "ERCOT_Markum" ;;
    hidalgo) echo "ERCOT_Hidalgo_Mirasole_Wind" ;;
    azure)   echo "ERCOT_Azure_Sky" ;;
    miller)  echo "ERCOT_Miller" ;;
    *) return 1 ;;
  esac
}

port_for() {
  case "$1" in
    markum)  echo 8501 ;;
    hidalgo) echo 8502 ;;
    azure)   echo 8503 ;;
    miller)  echo 8504 ;;
    *) return 1 ;;
  esac
}

target="${1:-running}"

kill_portal() {
  local dir="$1"
  local pids
  pids=$(pgrep -f "ercot-suite/${dir}/.venv.*streamlit run" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "  Stopping ${dir} (PIDs: ${pids})…"
    kill $pids 2>/dev/null || true
    sleep 1
  fi
}

start_portal() {
  local key="$1"
  local dir port app venv
  dir=$(dir_for "$key")
  port=$(port_for "$key")
  app="${SUITE}/${dir}/app/Home.py"
  venv="${SUITE}/${dir}/.venv/bin/streamlit"
  if [[ ! -f "$venv" ]]; then
    echo "  ⚠️  No venv found for ${dir}, skipping."
    return
  fi
  echo "  Starting ${dir} on port ${port}…"
  cd "${SUITE}/${dir}"
  nohup "$venv" run "$app" --server.port "$port" --server.headless true \
    > /tmp/streamlit_${key}.log 2>&1 &
  echo "    PID $! · log: /tmp/streamlit_${key}.log"
}

if [[ "$target" == "all" ]]; then
  keys=("${ALL_KEYS[@]}")
elif [[ "$target" == "running" ]]; then
  keys=()
  for key in "${ALL_KEYS[@]}"; do
    dir=$(dir_for "$key")
    if pgrep -f "ercot-suite/${dir}/.venv.*streamlit run" &>/dev/null; then
      keys+=("$key")
    fi
  done
  if [[ ${#keys[@]} -eq 0 ]]; then
    echo "No portals currently running. Pass a name or 'all' to start one."
    exit 0
  fi
else
  if ! dir_for "$target" >/dev/null 2>&1; then
    echo "Unknown portal '${target}'. Choose: markum | hidalgo | azure | miller | all"
    exit 1
  fi
  keys=("$target")
fi

echo "=== Restarting: ${keys[*]} ==="
for key in "${keys[@]}"; do
  kill_portal "$(dir_for "$key")"
done
sleep 1
for key in "${keys[@]}"; do
  start_portal "$key"
done
echo "Done."
