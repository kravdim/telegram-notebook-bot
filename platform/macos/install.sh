#!/bin/bash
# Staged macOS deployment with versioned releases and automatic rollback.
set -Eeuo pipefail

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
REPORT_WRITTEN=0
DEPLOY_PHASE="initialization"

write_report() {
    local status="$1" reason="$2" active="${3:-${PREVIOUS_SHA:-none}}"
    local report_tmp="$DEPLOY_REPORT.tmp.$$"
    printf 'status=%s\ncandidate=%s\nprevious=%s\nactive=%s\nphase=%s\nreason=%s\n' \
        "$status" "${CANDIDATE_SHA:-unknown}" "${PREVIOUS_SHA:-none}" "$active" \
        "$DEPLOY_PHASE" "$reason" > "$report_tmp"
    mv "$report_tmp" "$DEPLOY_REPORT"
    REPORT_WRITTEN=1
}

cleanup() {
    local exit_code=$?
    if [ "$exit_code" -ne 0 ] && [ "$REPORT_WRITTEN" -eq 0 ]; then
        write_report "failed" "unexpected failure (exit $exit_code)" || true
    fi
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
    local ready_marker="$release_dir/.dailyplanner-release-ready"
    local failed_dir=""
    if [ -f "$ready_marker" ] && [ -x "$release_dir/.venv/bin/python" ]; then
        echo "$release_dir"
        return
    fi
    if [ -e "$release_dir" ]; then
        failed_dir="$RELEASE_ROOT/.incomplete-$revision-$(date +%s)"
        mv "$release_dir" "$failed_dir"
        echo "Moved incomplete release to: $failed_dir" >&2
    fi
    local staging_dir
    staging_dir="$(mktemp -d "$RELEASE_ROOT/.staging-$revision.XXXXXX")"
    git archive "$revision" | tar -x -C "$staging_dir"
    [ -f "$SOURCE_DIR/.env" ] && ln -s "$SOURCE_DIR/.env" "$staging_dir/.env"
    [ -f "$SOURCE_DIR/config.yaml" ] && ln -s "$SOURCE_DIR/config.yaml" "$staging_dir/config.yaml"
    mv "$staging_dir" "$release_dir"
    if ! uv sync --project "$release_dir" --frozen --no-dev --extra stt >&2; then
        failed_dir="$RELEASE_ROOT/.failed-deps-$revision-$(date +%s)"
        mv "$release_dir" "$failed_dir"
        echo "Release dependency preparation failed; preserved at: $failed_dir" >&2
        return 1
    fi
    touch "$ready_marker"
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
    local release_dir="$1" expected_sha="$2" timeout="$3" compatible_head="${4:-}" waited=0
    local readiness_args=(--file "$READINESS_FILE" --max-age-seconds 15)
    if [ -n "$expected_sha" ]; then
        readiness_args+=(--expected-release "$expected_sha")
    fi
    while [ "$waited" -lt "$timeout" ]; do
        if env "PYTHONPATH=$release_dir" "$release_dir/.venv/bin/python" \
            "$release_dir/scripts/check_runtime_readiness.py" \
            "${readiness_args[@]}" >/dev/null 2>&1; then
            if [ -n "$compatible_head" ]; then
                if env "PYTHONPATH=$release_dir" "$release_dir/.venv/bin/python" \
                    "$release_dir/scripts/preflight.py" \
                    --compatible-database-head "$compatible_head" >/dev/null 2>&1; then
                    return 0
                fi
            elif env "PYTHONPATH=$release_dir" "$release_dir/.venv/bin/python" \
                "$release_dir/scripts/preflight.py" >/dev/null 2>&1; then
                return 0
            fi
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

DEPLOY_PHASE="prepare_candidate"
if ! CANDIDATE_DIR="$(prepare_release "$CANDIDATE_SHA")"; then
    write_report "pre_switch_failed" "candidate preparation failed"
    exit 1
fi
runtime_env[0]="PYTHONPATH=$CANDIDATE_DIR"
runtime_env+=("DAILYPLANNER_STT_CACHE=$HOME/Library/Caches/notebook-bot/huggingface")

echo "Running candidate config/database preflight..."
DEPLOY_PHASE="database_preflight"
if ! env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/preflight.py" --allow-pending-migration; then
    write_report "pre_switch_failed" "database preflight failed"
    exit 1
fi
echo "Checking Telegram credentials with getMe..."
DEPLOY_PHASE="telegram_credentials"
if ! env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/check_telegram_credentials.py"; then
    write_report "pre_switch_failed" "Telegram credential check failed"
    exit 1
fi
echo "Warming the configured STT model..."
DEPLOY_PHASE="stt_warmup"
if ! env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/prefetch_stt_model.py"; then
    write_report "pre_switch_failed" "STT warmup failed"
    exit 1
fi

DEPLOY_PHASE="render_candidate"
if ! "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/platform/macos/render_launchagent.py" \
    --template "$CANDIDATE_DIR/platform/macos/com.notebook-bot.plist" \
    --output "$CANDIDATE_PLIST" --project "$CANDIDATE_DIR" --home "$HOME" \
    --readiness-file "$READINESS_FILE" --release-sha "$CANDIDATE_SHA" \
    ${proxy_args[@]+"${proxy_args[@]}"}; then
    write_report "pre_switch_failed" "candidate LaunchAgent render failed"
    exit 1
fi
if ! plutil -lint "$CANDIDATE_PLIST"; then
    write_report "pre_switch_failed" "candidate LaunchAgent validation failed"
    exit 1
fi

PREVIOUS_DIR=""
PREVIOUS_EXPECTED_SHA=""
PREVIOUS_DB_HEAD=""
CANDIDATE_DB_HEAD="$(
    "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/scripts/get_migration_head.py" \
        --project "$CANDIDATE_DIR"
)"
ROLLBACK_COMPATIBLE_HEAD=""
if [ -n "$PREVIOUS_SHA" ]; then
    DEPLOY_PHASE="prepare_rollback"
    if ! PREVIOUS_DIR="$(prepare_release "$PREVIOUS_SHA")"; then
        write_report "pre_switch_failed" "rollback release preparation failed"
        exit 1
    fi
    if grep -q 'release_sha' "$PREVIOUS_DIR/bot/runtime/readiness.py"; then
        PREVIOUS_EXPECTED_SHA="$PREVIOUS_SHA"
    fi
    PREVIOUS_DB_HEAD="$(
        "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/scripts/get_migration_head.py" \
            --project "$PREVIOUS_DIR"
    )"
    if [ "$PREVIOUS_DB_HEAD" != "$CANDIDATE_DB_HEAD" ]; then
        if grep -Eq "^[[:space:]]*$CANDIDATE_DB_HEAD[[:space:]]*$" \
            "$CANDIDATE_DIR/bot/db/migrations/rollback_compatible_heads.txt"; then
            ROLLBACK_COMPATIBLE_HEAD="$CANDIDATE_DB_HEAD"
        else
            DEPLOY_PHASE="migration_compatibility"
            write_report "pre_switch_failed" \
                "migration is not declared compatible with previous release"
            echo "Candidate migration requires a maintenance/restore plan" >&2
            exit 1
        fi
    fi
    if ! "$CANDIDATE_DIR/.venv/bin/python" "$CANDIDATE_DIR/platform/macos/render_launchagent.py" \
        --template "$CANDIDATE_DIR/platform/macos/com.notebook-bot.plist" \
        --output "$ROLLBACK_PLIST" --project "$PREVIOUS_DIR" --home "$HOME" \
        --readiness-file "$READINESS_FILE" --release-sha "$PREVIOUS_SHA" \
        ${proxy_args[@]+"${proxy_args[@]}"}; then
        write_report "pre_switch_failed" "rollback LaunchAgent render failed"
        exit 1
    fi
    if ! plutil -lint "$ROLLBACK_PLIST"; then
        write_report "pre_switch_failed" "rollback LaunchAgent validation failed"
        exit 1
    fi
fi

echo "Applying backward-compatible candidate migrations..."
DEPLOY_PHASE="migration"
if ! env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/alembic" upgrade head; then
    write_report "pre_switch_failed" "migration failed"
    exit 1
fi
if ! env "${runtime_env[@]}" "$CANDIDATE_DIR/.venv/bin/python" \
    "$CANDIDATE_DIR/scripts/preflight.py"; then
    write_report "pre_switch_failed" "post-migration preflight failed"
    exit 1
fi

rollback() {
    local reason="$1"
    DEPLOY_PHASE="rollback"
    echo "Candidate readiness failed: $reason" >&2
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f -- "$READINESS_FILE"
    if [ -n "$PREVIOUS_SHA" ]; then
        if ! cp "$ROLLBACK_PLIST" "$PLIST_DST"; then
            write_report "rollback_failed" "$reason; previous LaunchAgent install failed" "none"
            echo "CRITICAL: rollback LaunchAgent install failed" >&2
        elif ! launchctl load "$PLIST_DST"; then
            write_report "rollback_failed" "$reason; previous LaunchAgent load failed" "none"
            echo "CRITICAL: rollback LaunchAgent load failed" >&2
        elif wait_for_release "$PREVIOUS_DIR" "$PREVIOUS_EXPECTED_SHA" \
            "$READINESS_TIMEOUT" "$ROLLBACK_COMPATIBLE_HEAD"; then
            write_report "rolled_back" "$reason" "$PREVIOUS_SHA"
            echo "$PREVIOUS_SHA" > "$CURRENT_RELEASE_FILE"
            echo "Rollback succeeded: $PREVIOUS_SHA" >&2
        else
            write_report "rollback_failed" "$reason; previous release readiness failed" "unknown"
            echo "CRITICAL: rollback readiness failed" >&2
        fi
    else
        write_report "failed_no_previous" "$reason" "none"
    fi
    exit 1
}

echo "Switching LaunchAgent to candidate..."
DEPLOY_PHASE="candidate_switch"
launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f -- "$READINESS_FILE"
cp "$CANDIDATE_PLIST" "$PLIST_DST" || rollback "candidate LaunchAgent install failed"
launchctl load "$PLIST_DST" || rollback "launchctl load failed"
wait_for_release "$CANDIDATE_DIR" "$CANDIDATE_SHA" "$READINESS_TIMEOUT" || \
    rollback "bounded readiness timeout"

echo "$CANDIDATE_SHA" > "$CURRENT_RELEASE_FILE"
DEPLOY_PHASE="complete"
write_report "deployed" "none" "$CANDIDATE_SHA"
echo "Deploy succeeded: $CANDIDATE_SHA"
echo "Report: $DEPLOY_REPORT"
