#!/bin/bash
# Double-click to restart the ERCOT settlement portals.
#
# Shows every discovered portal and its status, then restarts the ones that are
# currently running (so your latest code edits take effect). To start a portal
# that's stopped, or restart everything, run in Terminal:
#     ./restart_portals.sh all          # start/restart every portal
#     ./restart_portals.sh heart        # restart just Heart of Texas
cd "$(dirname "$0")" || exit 1

echo "════════════════════════════════════════════════════════"
echo "  ERCOT Portals"
echo "════════════════════════════════════════════════════════"
./restart_portals.sh list
echo
./restart_portals.sh running
echo
echo "────────────────────────────────────────────────────────"
echo "Tip:  ./restart_portals.sh all        (start/restart all)"
echo "      ./restart_portals.sh <name>      (just one, e.g. heart)"
echo "You can close this window."
