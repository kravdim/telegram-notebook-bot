#!/bin/bash
# Staged macOS deployment with versioned releases and automatic rollback.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLIST_DST="${DAILYPLANNER_PLIST_DST:-$HOME/Library/LaunchAgents/com.notebook-bot.plist}"
LOG_DIR="${DAILYPLANNER_LOG_DIR:-$HOME/Library/Logs/notebook-bot}"
RELEASE_ROOT="${DAILYPLANNER_RELEASE_ROOT:-$HOME/Library/Application Support/notebook-bot/releases}"
STATE_DIR="${DAILYPLANNER_STATE_DIR:-$HOME/Library/Application Support/notebook-bot/state}"
READINESS_FILE="$STATE_DIR/runtime-readiness.json"
CURRENT_RELEASE_FILE="$STATE_DIR/current-release"
DEPLOY_REPORT="$STATE_DIR/last-deploy-report.txt"
HTTP_PROXY_URL=""
ALL_PROXY_URL=""
PREVIOUS_REVISION=""
READINESS_TIMEOUT=90

usage() {
    echo "Usage: $0 [--http-proxy URL] [--all-proxy URL] [--previous-revision SHA] [--readiness-timeout SECONDS]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --http-proxy)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            HTTP_PROXY_URL="$2"; shift 2 ;;
        --all-proxy)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            ALL_PROXY_URL="$2"; shift 2 ;;
        --previous-revision)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            PREVIOUS_REVISION="$2"; shift 2 ;;
        --readiness-timeout)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            READINESS_TIMEOUT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$READINESS_TIMEOUT" in
    ''|*[!0-9]*) echo "readiness timeout must be a positive integer" >&2; exit 2 ;;
esac
[ "$READINESS_TIMEOUT" -gt 0 ] || { echo "readiness timeout must be positive" >&2; exit 2; }

for command in git uv launchctl plutil; do
    command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 1; }
done

cd "$SOURCE_DIR"
CANDIDATE_SHA="$(git rev-parse HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing deploy from a dirty tracked worktree" >&2
    exit 1
fi

mkdir -p "$LOG_DIR" "$RELEASE_ROOT" "$STATE_DIR" "$(dirname "$PLIST_DST")"
DEPLOY_LOCK="$STATE_DIR/deploy.lock"
if ! mkdir "$DEPLOY_LOCK" 2>/dev/null; then
    echo "Another DailyPlanner deploy is already running" >&2
    exit 1
fi
CANDIDATE_PLIST=""
ROLLBACK_PLIST=""
cleanup() {
    [ -z "$CANDIDATE_PLIST" ] || rm -f -- "$CANDIDATE_PLIST"
    [ -z "$ROLLBACK_PLIST" ] || rm -f -- "$ROLLBACK_PLIST"
    rmdir "$DEPLOY_LOCK" 2>/dev/null || true
}
trap cleanup EXIT
CANDIDATE_PLIST="$(mktemp "$STATE_DIR/candidate-plist.XXXXXX")"
ROLLBACK_PLIST="$(mktemp "$STATE_DIR/rollback-plist.XXXXXX")"

proxy_args=()
runtime_env=("PYTHONPATH=")
if [ -n "$HTTP_PROXY_URL" ]; then
    proxy_args+=(--http-proxy "$HTTP_PROXY_URL")
    runtime_env+=("HTTP_PROXY=$HTTP_PROXY_URL" "HTTPS_PROXY=$HTTP_PROXY_URL")
fi
if [ -n "$ALL_PROXY_URL" ]; then
    proxy_args+=(--all-proxy "$ALL_PROXY_URL")
    runtime_env+=("ALL_PROXY=$ALL_PROXY_URL")
fi

prepare_release() {
    local revision="$1"
    local release_dir="$RELEASE_ROOT/$revision"
    if [ -x "$release_dir/.venv/bin/python" ]; then
        echo "$release_dir"
        return
    fi
    local staging_dir
    staging_dir="$(mktemp -d "$RELEASE_ROOT/.staging-$revision.XXXXXX")"
    git archive "$revision" | tar -x -C "$staging_dir"
    [ -f "$SOURCE_DIR/.env" ] && ln -s "$SOURCE_DIR/.env" "$staging_dir/.env"
    [ -f "$SOURCE_DIR/config.yaml" ] && ln -s "$SOURCE_DIR/config.yaml" "$staging_dir/config.yaml"
    uv sync --project "$staging_dir" --frozen --no-dev --extra stt >&2
    mv "$staging_dir" "$release_dir"
    echo "$release_dir"
}

read_installed_release() {
    if [ -f "$CURRENT_RELEASE_FILE" ]; then
        head -1 "$CURRENT_RELEASE_FILE"
    elif [ -f "$PLIST_DST" ]; then
        /usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:DAILYPLANNER_RELEASE_SHA' \
            "$PLIST_DST" 2>/dev/null || true
    fi
}

wait_for_release() {
    local release_dir="$1" expected_sha="$2" timeout="$3" waited=0
    local readiness_args=(--file "$READINESS_FILE" --max-age-seconds 15)
    if [ -n "$expected_sha" ]; then
        readiness_args+=(--expected-release "$expected_sha")
    fi
    while [ "$waited" -lt "$timeout" ]; do
        if env "PYTHONPATH=$CANDIDATE_DIR" "$CANDIDATE_DIR/.venv/bin/python" \
            "$CANDIDATE_DIR/scripts/check_runtime_readiness.py" \
            "${readiness_args[@]}" >/dev/null 2>&1 && \
           env "PYTHONPATH=$release_dir" "$release_dir/.venv/bin/python" \
            "$release_dir/scripts/preflight.py" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

PREVIOUS_SHA="$(read_installed_release)"
[ -n "$PREVIOUS_SHA" ] || PREVIOUS_SHA="$PREVIOUS_REVISION"
if [ -f "$PLIST_DST" ] && [ -z "$PREVIOUS_SHA" ]; then
    echo "Existing unversioned install requires --previous-revision SHA for rollback" >&2
    exit 1
fi

echo "=== Staged DailyPlanner deploy ==="
echo "candidate: $CANDIDATE_SHA"
echo "previous: ${PREVIOUS_SHA:-none}"

CANDIDATE_DIR="$(prepare_release "$CANDIDATE_SHA")"
runtime_env[0]="PYTHONPATH=$CANDIDATE_DIR"
runtime_env+=("DAILYPLANNER_STT_CACHE=$HOME/Library/Caches/notebook-bot/huggingface")

echo "Running candidate config/database/migration preflight..."
env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/alembic" upgrade head
env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/scripts/preflight.py"
echo "Checking Telegram credentials with getMe..."
env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/check_telegram_credentials.py"
echo "Warming the configured STT model..."
env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/prefetch_stt_model.py"

"$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/platform/macos/render_launchagent.py" \
    --template "$CANDIDATE_DIR/platform/macos/com.notebook-bot.plist" \
    --output "$CANDIDATE_PLIST" --project "$CANDIDATE_DIR" --home "$HOME" \
    --readiness-file "$READINESS_FILE" --release-sha "$CANDIDATE_SHA" \
    "${proxy_args[@]}"
plutil -lint "$CANDIDATE_PLIST"

PREVIOUS_DIR=""
PREVIOUS_EXPECTED_SHA=""
if [ -n "$PREVIOUS_SHA" ]; then
    PREVIOUS_DIR="$(prepare_release "$PREVIOUS_SHA")"
    if grep -q 'release_sha' "$PREVIOUS_DIR/bot/runtime/readiness.py"; then
        PREVIOUS_EXPECTED_SHA="$PREVIOUS_SHA"
    fi
    "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/platform/macos/render_launchagent.py" \
        --template "$CANDIDATE_DIR/platform/macos/com.notebook-bot.plist" \
        --output "$ROLLBACK_PLIST" --project "$PREVIOUS_DIR" --home "$HOME" \
        --readiness-file "$READINESS_FILE" --release-sha "$PREVIOUS_SHA" \
        "${proxy_args[@]}"
    plutil -lint "$ROLLBACK_PLIST"
fi

rollback() {
    local reason="$1"
    echo "Candidate readiness failed: $reason" >&2
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f -- "$READINESS_FILE"
    if [ -n "$PREVIOUS_SHA" ]; then
        cp "$ROLLBACK_PLIST" "$PLIST_DST"
        launchctl load "$PLIST_DST"
        if wait_for_release "$PREVIOUS_DIR" "$PREVIOUS_EXPECTED_SHA" "$READINESS_TIMEOUT"; then
            printf 'status=rolled_back\ncandidate=%s\nactive=%s\nreason=%s\n' \
                "$CANDIDATE_SHA" "$PREVIOUS_SHA" "$reason" > "$DEPLOY_REPORT"
            echo "$PREVIOUS_SHA" > "$CURRENT_RELEASE_FILE"
            echo "Rollback succeeded: $PREVIOUS_SHA" >&2
        else
            printf 'status=rollback_failed\ncandidate=%s\nprevious=%s\nreason=%s\n' \
                "$CANDIDATE_SHA" "$PREVIOUS_SHA" "$reason" > "$DEPLOY_REPORT"
            echo "CRITICAL: rollback readiness failed" >&2
        fi
    else
        printf 'status=failed_no_previous\ncandidate=%s\nreason=%s\n' \
            "$CANDIDATE_SHA" "$reason" > "$DEPLOY_REPORT"
    fi
    exit 1
}

echo "Switching LaunchAgent to candidate..."
launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f -- "$READINESS_FILE"
cp "$CANDIDATE_PLIST" "$PLIST_DST"
launchctl load "$PLIST_DST" || rollback "launchctl load failed"
wait_for_release "$CANDIDATE_DIR" "$CANDIDATE_SHA" "$READINESS_TIMEOUT" || \
    rollback "bounded readiness timeout"

echo "$CANDIDATE_SHA" > "$CURRENT_RELEASE_FILE"
printf 'status=deployed\ncandidate=%s\nprevious=%s\nrollback=not_required\n' \
    "$CANDIDATE_SHA" "${PREVIOUS_SHA:-none}" > "$DEPLOY_REPORT"
echo "Deploy succeeded: $CANDIDATE_SHA"
echo "Report: $DEPLOY_REPORT"
