#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USERBOT_DIR="${DAILYPLANNER_USERBOT_DIR:-/Users/moltbot/Projects/userbot}"
RUNNER="$USERBOT_DIR/tests_dailyplanner/run_messy_human.py"
LOG_FILE="$(mktemp /tmp/dailyplanner-live-e2e.XXXXXX.log)"

if [[ ! -f "$RUNNER" || ! -x "$USERBOT_DIR/.venv/bin/python" ]]; then
    echo "DailyPlanner live E2E runner or its virtualenv is missing" >&2
    exit 2
fi

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" scripts/preflight.py

set +e
(
    cd "$USERBOT_DIR"
    "$USERBOT_DIR/.venv/bin/python" "$RUNNER" "$@"
) 2>&1 | tee "$LOG_FILE"
runner_status=${PIPESTATUS[0]}
set -e

if [[ $runner_status -ne 0 ]]; then
    echo "Live E2E runner failed (log: $LOG_FILE)" >&2
    exit "$runner_status"
fi

summary="$(grep -E 'Готово: [0-9]+/[0-9]+ PASS' "$LOG_FILE" | tail -1 || true)"
if [[ ! "$summary" =~ Готово:\ ([0-9]+)/([0-9]+)\ PASS ]] || \
   [[ "${BASH_REMATCH[1]}" != "${BASH_REMATCH[2]}" ]]; then
    echo "Live E2E gate failed: ${summary:-summary not found} (log: $LOG_FILE)" >&2
    exit 1
fi

echo "Live E2E gate passed: $summary"
echo "Runner report is stored under $USERBOT_DIR/tests_dailyplanner/results"
rm -f "$LOG_FILE"
