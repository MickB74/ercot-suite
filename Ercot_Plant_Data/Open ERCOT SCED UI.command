#!/bin/bash
# Double-click to open the ERCOT Plant SCED web UI in your browser.

cd "$(dirname "$0")" || exit 1

if [ ! -d ".venv" ]; then
    echo "First run: creating Python environment..."
    python3 -m venv .venv || { echo "Could not create venv. Is Python 3 installed?"; read -r; exit 1; }
fi
# Ensure deps (incl. streamlit) are present.
./.venv/bin/pip install -q -r requirements.txt

echo "Starting the UI — your browser will open. Close this window to stop the app."
./.venv/bin/streamlit run app.py
