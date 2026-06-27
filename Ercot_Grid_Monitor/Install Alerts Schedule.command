#!/bin/bash
# Installs a macOS launchd job that runs the ERCOT alert check every 15 minutes
# (and once on login). Each run evaluates alerts_config.json and sends email/SMS
# for any freshly-triggered rule (cooldowns prevent re-alert spam).
# Double-click to enable. Re-running it is safe.

set -e
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LABEL="com.ercot.gridmonitor.alerts"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PYTHON" ]; then
  echo "✗ No venv yet — run 'Open ERCOT Monitor.command' once first."; exit 1
fi
if [ ! -f "$PROJECT_DIR/alerts_config.json" ]; then
  echo "⚠ No alerts_config.json — copying the example so the job has something to read:"
  cp "$PROJECT_DIR/alerts_config.example.json" "$PROJECT_DIR/alerts_config.json"
  echo "  → edit $PROJECT_DIR/alerts_config.json (rules + email/SMS) before relying on it."
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
        <string>$PROJECT_DIR/run_alerts.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/alerts.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/alerts.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ Installed $LABEL — alert checks run every 15 min. Log: $PROJECT_DIR/alerts.log"
echo "  To stop:  launchctl unload \"$PLIST\""
