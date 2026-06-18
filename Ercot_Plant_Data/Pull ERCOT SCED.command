#!/bin/bash
# Double-click this file to pull ERCOT plant SCED data interactively.
# It sets up the Python environment on first run, then launches the menu.

cd "$(dirname "$0")" || exit 1

if [ ! -d ".venv" ]; then
    echo "First run: creating Python environment..."
    python3 -m venv .venv || { echo "Could not create venv. Is Python 3 installed?"; read -r; exit 1; }
    ./.venv/bin/pip install -q -r requirements.txt
fi

./.venv/bin/python interactive.py
