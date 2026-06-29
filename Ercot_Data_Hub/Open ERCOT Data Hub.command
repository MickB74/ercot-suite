#!/bin/bash
# Double-click launcher: starts the unified ERCOT Data Hub Streamlit app.
cd "$(dirname "$0")" || exit 1
source "$(cd "$(dirname "$0")" && pwd)/../_ensure_venv.sh"
exec ./.venv/bin/streamlit run app/Home.py
