#!/bin/bash
#
# One-command refresh of the ERCOT 15-minute generation-by-source data.
# Re-downloads the Fuel Mix Report for the current year (and the previous year,
# to pick up any late INITIAL -> FINAL revisions of December) and rewrites the
# yearly parquet files. Safe to run repeatedly / on a schedule (it's idempotent
# with a rollback guard).
#
#   ./update_all_data.sh              # previous + current year (report only)
#   ./update_all_data.sh 2023 2024    # specific years
#
set -e

# Run from this script's directory regardless of where it's launched from.
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No .venv found. Create it first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Years to refresh: CLI args if given, else previous + current year.
if [ "$#" -gt 0 ]; then
  YEARS=("$@")
else
  CUR=$(date +%Y)
  YEARS=($((CUR - 1)) "$CUR")
fi

echo "========================================="
echo "⚡️ ERCOT Generation Data Update ⚡️"
echo "Years: ${YEARS[*]}  (Fuel Mix Report only)"
echo "========================================="

"$PY" update_generation.py "${YEARS[@]}" --no-supplements

echo ""
echo "========================================="
echo "✅ Generation data updated."
echo "========================================="
