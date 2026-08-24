#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/notebook-bot}"
REPORT_PATH="${RECOVERY_DRILL_REPORT:-$HOME/Library/Logs/notebook-bot/recovery-drills.jsonl}"
ROLE_NAME="dailyplanner_recovery"
KEYCHAIN_SERVICE="dailyplanner-db-operator"

operator_secret=$(/usr/bin/security find-generic-password \
    -a "$ROLE_NAME" -s "$KEYCHAIN_SERVICE" -w)
trap 'unset operator_secret OPERATOR_DATABASE_URL' EXIT
export OPERATOR_DATABASE_URL="postgresql://$ROLE_NAME:$operator_secret@127.0.0.1:5432/postgres"
unset operator_secret

cd "$PROJECT_DIR"
exec "$PYTHON" scripts/restore_drill.py \
    --latest-backup-dir "$BACKUP_DIR" \
    --max-backup-age-hours 30 \
    --report "$REPORT_PATH"
