#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.sibylla.kalshibot.watchdog.plist"
LOG_DIR="$REPO_DIR/logs"
INTERVAL_SECONDS="${KALSHIBOT_WATCHDOG_INTERVAL_SECONDS:-300}"
PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

case "$REPO_DIR/" in
  "$HOME/Downloads/"*|"$HOME/Desktop/"*|"$HOME/Documents/"*)
    cat >&2 <<EOF
Refusing to install LaunchAgent from a macOS protected folder:
  $REPO_DIR

Move or clone the repo somewhere like:
  $HOME/Projects/kalshibot

Then run this installer again from that location. macOS background LaunchAgents
often cannot read scripts or compose files under Downloads, Desktop, or Documents
without extra privacy permissions, which makes the watchdog exit before it can
restart the bot.
EOF
    exit 1
    ;;
esac

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.sibylla.kalshibot.watchdog</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_DIR/scripts/watchdog.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>

  <key>StartInterval</key>
  <integer>$INTERVAL_SECONDS</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/watchdog.launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/watchdog.launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.sibylla.kalshibot.watchdog"

echo "Installed KalshiBot watchdog LaunchAgent:"
echo "  $PLIST"
echo ""
echo "Logs:"
echo "  $LOG_DIR/watchdog.log"
echo "  $LOG_DIR/watchdog.launchd.out.log"
echo "  $LOG_DIR/watchdog.launchd.err.log"
echo ""
echo "To uninstall:"
echo "  launchctl bootout gui/$(id -u) $PLIST"
