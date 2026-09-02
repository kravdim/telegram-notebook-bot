#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USERBOT_DIR="${DAILYPLANNER_USERBOT_DIR:-/Users/moltbot/Projects/userbot}"
RUNNER="$USERBOT_DIR/tests_dailyplanner/run_messy_human.py"
CURRENT_RELEASE_FILE="${DAILYPLANNER_CURRENT_RELEASE_FILE:-$HOME/Library/Application Support/notebook-bot/state/current-release}"
TESTED_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_TMP="$(mktemp -d /tmp/dailyplanner-live-e2e.XXXXXX)"
chmod 700 "$RUN_TMP"
LOG_FILE="$RUN_TMP/runner.log"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
KEEP_FAILURE_LOG=false

cleanup_tmp() {
    if [[ "$KEEP_FAILURE_LOG" == "true" ]]; then
        echo "Failure bundle retained by request: $RUN_TMP" >&2
        return
    fi
    rm -f "$LOG_FILE"
    rmdir "$RUN_TMP" 2>/dev/null || true
}
trap cleanup_tmp EXIT

if [[ ! -f "$RUNNER" || ! -x "$USERBOT_DIR/.venv/bin/python" ]]; then
    echo "DailyPlanner live E2E runner or its virtualenv is missing" >&2
    exit 2
fi
if [[ ! -f "$CURRENT_RELEASE_FILE" ]]; then
    echo "Production release marker is missing: $CURRENT_RELEASE_FILE" >&2
    exit 2
fi
DEPLOYED_SHA="$(head -1 "$CURRENT_RELEASE_FILE")"
if [[ "$DEPLOYED_SHA" != "$TESTED_SHA" ]]; then
    echo "Live E2E SHA mismatch: checkout=$TESTED_SHA production=$DEPLOYED_SHA" >&2
    exit 1
fi

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" scripts/preflight.py
"$PROJECT_DIR/.venv/bin/python" tests/live/run_memoir_gate.py \
    --userbot-dir "$USERBOT_DIR"

set +e
(
    cd "$USERBOT_DIR"
    "$USERBOT_DIR/.venv/bin/python" "$RUNNER" "$@"
) 2>&1 | tee "$LOG_FILE"
runner_status=${PIPESTATUS[0]}
set -e

if [[ $runner_status -ne 0 ]]; then
    if [[ "${DAILYPLANNER_KEEP_FAILURE_LOG:-0}" == "1" ]]; then
        KEEP_FAILURE_LOG=true
    fi
    echo "Live E2E runner failed" >&2
    exit "$runner_status"
fi

summary="$(grep -E 'Готово: [0-9]+/[0-9]+ PASS' "$LOG_FILE" | tail -1 || true)"
if [[ ! "$summary" =~ Готово:\ ([0-9]+)/([0-9]+)\ PASS ]] || \
   [[ "${BASH_REMATCH[1]}" != "${BASH_REMATCH[2]}" ]]; then
    if [[ "${DAILYPLANNER_KEEP_FAILURE_LOG:-0}" == "1" ]]; then
        KEEP_FAILURE_LOG=true
    fi
    echo "Live E2E gate failed: ${summary:-summary not found}" >&2
    exit 1
fi

if ! grep -q 'State oracle:.*"ok": true' "$LOG_FILE"; then
    if [[ "${DAILYPLANNER_KEEP_FAILURE_LOG:-0}" == "1" ]]; then
        KEEP_FAILURE_LOG=true
    fi
    echo "Live E2E gate failed: state oracle evidence missing" >&2
    exit 1
fi
if ! grep -q 'Cleanup oracle:.*"ok": true' "$LOG_FILE"; then
    if [[ "${DAILYPLANNER_KEEP_FAILURE_LOG:-0}" == "1" ]]; then
        KEEP_FAILURE_LOG=true
    fi
    echo "Live E2E gate failed: cleanup oracle evidence missing" >&2
    exit 1
fi
if [[ "$(head -1 "$CURRENT_RELEASE_FILE")" != "$TESTED_SHA" ]]; then
    echo "Production SHA changed during live E2E" >&2
    exit 1
fi

echo "Live E2E gate passed: $summary"
echo "Evidence: tested_sha=$TESTED_SHA started_at=$STARTED_AT finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Runner report is stored under $USERBOT_DIR/tests_dailyplanner/results"
