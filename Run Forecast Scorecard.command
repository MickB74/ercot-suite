#!/bin/bash
# Run the forecast scorecard once, now. Double-click to refresh
# data/scorecard/forecast_scorecard*.csv from the current data lake.
set -e
cd "$(dirname "$0")"
PYTHON="./Ercot_Data_Hub/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "✗ No venv — open the ERCOT Data Hub once first."; exit 1
fi
"$PYTHON" forecast_scorecard.py
echo
echo "✓ Done. View it in the Data Hub → Analyze → Forecast Scorecard."
