#!/bin/bash
# Double-click launcher: starts the Miller Settlement Portal.
cd "$(dirname "$0")" || exit 1
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi
exec ./.venv/bin/streamlit run app/Home.py
