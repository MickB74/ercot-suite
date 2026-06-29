#!/bin/bash
# Sourced by each portal/hub launcher (not run directly).
# Ensures .venv exists, matches the current python3, and has a working streamlit.
# Must be sourced from the portal's own directory (after cd "$(dirname "$0")").

_SYS_PY=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
if [ -z "$_SYS_PY" ]; then
  echo "ERROR: python3 not found. Install Python 3.9+ and try again."
  read -r -p "Press Return to close." _dummy
  exit 1
fi

_need_rebuild=false

if [ ! -d ".venv" ]; then
  _need_rebuild=true
else
  _VENV_PY=$(grep "^version" .venv/pyvenv.cfg 2>/dev/null | awk '{print $3}' | cut -d. -f1,2)
  if [ "$_VENV_PY" != "$_SYS_PY" ]; then
    echo "Python version changed (venv: $_VENV_PY → system: $_SYS_PY) — rebuilding venv…"
    rm -rf .venv
    _need_rebuild=true
  elif ! ./.venv/bin/python -c "import streamlit" 2>/dev/null; then
    echo "venv appears broken — rebuilding…"
    rm -rf .venv
    _need_rebuild=true
  fi
fi

if [ "$_need_rebuild" = true ]; then
  echo "Setting up virtual environment (Python $_SYS_PY)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip -q
  ./.venv/bin/pip install -r requirements.txt
  if ! ./.venv/bin/python -c "import streamlit" 2>/dev/null; then
    echo "ERROR: install failed — check requirements.txt or your Python installation."
    read -r -p "Press Return to close." _dummy
    exit 1
  fi
  echo "Virtual environment ready."
fi
