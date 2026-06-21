#!/bin/bash
# Double-click to top up Mesquite Star's generation + node prices to the latest ERCOT
# date. Runs refresh.py with the Hub's virtualenv (it has the ERCOT-pull deps
# and the API credentials).
cd "$(dirname "$0")" || exit 1

# Find the Hub's python: env override, sibling repo, then the usual location.
CANDIDATES=(
  "${MESQUITE_STAR_HUB_ROOT:-}/.venv/bin/python"
  "../Ercot_Data_Hub/.venv/bin/python"
  "$HOME/Documents/Github/Ercot_Data_Hub/.venv/bin/python"
)
HUB_PY=""
for c in "${CANDIDATES[@]}"; do
  if [ -x "$c" ]; then HUB_PY="$c"; break; fi
done

if [ -z "$HUB_PY" ]; then
  echo "Could not find the Ercot_Data_Hub virtualenv (.venv)."
  echo "Set MESQUITE_STAR_HUB_ROOT to the Hub repo, or keep it as a sibling directory."
  read -r -p "Press Return to close." _
  exit 1
fi

echo "Using Hub venv: $HUB_PY"
"$HUB_PY" refresh.py "$@"
echo
read -r -p "Done. Press Return to close." _
