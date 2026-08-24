#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.notebook-bot-recovery-drill.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.notebook-bot-recovery-drill.plist"
LOG_DIR="$HOME/Library/Logs/notebook-bot"
DOMAIN="gui/$(id -u)"
LABEL="com.notebook-bot-recovery-drill"

/usr/bin/security find-generic-password \
    -a dailyplanner_recovery -s dailyplanner-db-operator >/dev/null
mkdir -p "$LOG_DIR"
sed \
    -e "s|__PROJECT_PATH__|$PROJECT_DIR|g" \
    -e "s|__HOME__|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl enable "$DOMAIN/$LABEL"
echo "Weekly recovery drill installed: Sunday 04:30"
