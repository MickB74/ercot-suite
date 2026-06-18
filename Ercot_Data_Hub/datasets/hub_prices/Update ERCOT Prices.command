#!/bin/bash
# Double-click this file in Finder to open the ERCOT Hub Price Downloader app.
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python ercot_gui.py
