#!/bin/zsh
# Runs `python -m outreach auto` every day at HOUR:00 (default 19 = 7 PM) via macOS launchd.
# Usage: ./install_schedule.sh [HOUR]      Remove: ./install_schedule.sh --remove
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.nilakshi.outreach-agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$1" == "--remove" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"; echo "schedule removed"; exit 0
fi

HOUR="${1:-19}"
PY="$DIR/.venv/bin/python"
[[ -x "$PY" ]] || { echo "no .venv found - run setup from README first"; exit 1; }
[[ -f "$DIR/token.json" ]] || { echo "run 'python -m outreach replies' once first to log in to Gmail"; exit 1; }
mkdir -p "$DIR/data" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-m</string><string>outreach</string><string>auto</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DIR/data/auto.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/auto.log</string>
</dict></plist>
PL

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "scheduled: outreach runs daily at ${HOUR}:00 (if the Mac is asleep, it runs when it wakes)"
echo "log: $DIR/data/auto.log      run now: launchctl start $LABEL"
echo
echo "If the log shows 'Operation not permitted' (macOS protects ~/Downloads):"
echo "  System Settings -> Privacy & Security -> Full Disk Access -> + -> add:"
echo "  $(python3 -c "import os;print(os.path.realpath('$PY'))")"
