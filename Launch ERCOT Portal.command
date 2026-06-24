#!/bin/bash
# Double-click to launch an ERCOT portal from a simple menu.
# Pick a number; it starts the portal (if needed) and opens it in your browser.
cd "$(dirname "$0")" || exit 1
./launch_portal.sh
