#!/bin/bash
# Double-click to top up Azure Sky's VORTEX generation to the latest ERCOT date.
# Runs refresh.py with the Hub's virtualenv (it has the ERCOT-pull deps and the
# API credentials). HB_NORTH prices are a shared Hub resource — refresh.py reports
# their freshness and points you at the Hub's price updater if they're behind.
cd "$(dirname "$0")" || exit 1

# Find the Hub's python: env override, sibling repo, then the usual location.
CANDIDATES=(
  "${AZURE_HUB_ROOT:-}/.venv/bin/python"
  "../Ercot_Data_Hub/.venv/bin/python"
  "$HOME/Documents/Github/Ercot_Data_Hub/.venv/bin/python"
)
HUB_PY=""
for c in "${CANDIDATES[@]}"; do
  if [ -x "$c" ]; then HUB_PY="$c"; break; fi
done

if [ -z "$HUB_PY" ]; then
  echo "Could not find the Ercot_Data_Hub virtualenv (.venv)."
  echo "Set AZURE_HUB_ROOT to the Hub repo, or keep it as a sibling directory."
  read -r -p "Press Return to close." _
  exit 1
fi

echo "Using Hub venv: $HUB_PY"
"$HUB_PY" refresh.py "$@"
echo
read -r -p "Done. Press Return to close." _
