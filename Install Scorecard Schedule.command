#!/bin/bash
# Installs a macOS launchd job that runs the forecast scorecard monthly (1st of
# the month, 06:00, and once at login) so grounding accuracy is tracked over
# time without anyone remembering to run it. Double-click to enable; re-running
# is safe. The scorecard reads only the cached data lake — no network calls.
set -e
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
PYTHON="$PROJECT_DIR/Ercot_Data_Hub/.venv/bin/python"
LABEL="com.ercot.datahub.scorecard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PYTHON" ]; then
  echo "✗ No venv yet — open the ERCOT Data Hub once first."; exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

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
        <string>$PROJECT_DIR/forecast_scorecard.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key><integer>1</integer>
        <key>Hour</key><integer>6</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/scorecard.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/scorecard.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ Installed $LABEL — scorecard runs monthly (1st, 06:00) + at login."
echo "  Log: $PROJECT_DIR/scorecard.log"
echo "  To stop:  launchctl unload \"$PLIST\""
