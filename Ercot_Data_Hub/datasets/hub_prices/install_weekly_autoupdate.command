#!/bin/bash
# Installs a macOS launchd job that refreshes ERCOT hub prices once a week
# (Mondays ~07:30), and catches up on login if the Mac was asleep at that time.
# Double-click to enable. Re-running it is safe.

set -e
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LABEL="com.ercotprice.weekly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/data"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT_DIR/ercot_api.py</string>
        <string>update</string>
        <string>--auto</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>1</integer>
        <key>Hour</key><integer>7</integer>
        <key>Minute</key><integer>30</integer>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/data/autoupdate.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/data/autoupdate.log</string>
</dict>
</plist>
EOF

# Reload the job
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Weekly auto-update installed."
echo "  Job:   $LABEL"
echo "  Runs:  Mondays 07:30 (with login catch-up), only fetches if data is stale."
echo "  Log:   $PROJECT_DIR/data/autoupdate.log"
echo ""
echo "To disable later:  launchctl unload \"$PLIST\" && rm \"$PLIST\""
