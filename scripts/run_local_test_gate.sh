#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/platform/dev/docker-compose.yml"
QUALITY_PROJECT="dailyplanner-quality-$$"
QUALITY_BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dailyplanner-quality-backups.XXXXXX")"
# Keep parallel runs isolated without requiring a local probe socket (some CI
# sandboxes forbid socket creation before Docker starts the service).
QUALITY_PORT="$((55000 + ($$ % 10000)))"

compose=(docker compose -p "$QUALITY_PROJECT" -f "$COMPOSE_FILE")
cleanup() {
    DAILYPLANNER_DEV_DB_PORT="$QUALITY_PORT" "${compose[@]}" \
        down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "$QUALITY_BACKUP_DIR"
}
trap cleanup EXIT

cd "$PROJECT_DIR"
command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the local test gate" >&2
    exit 1
}
command -v uv >/dev/null 2>&1 || {
    echo "uv is required for the local test gate" >&2
    exit 1
}
if [ "${DAILYPLANNER_SKIP_STT_EXTRA:-0}" = "1" ]; then
    uv sync --frozen --group dev
else
    uv sync --frozen --group dev --extra stt
fi
DAILYPLANNER_DEV_DB_PORT="$QUALITY_PORT" "${compose[@]}" up -d --wait

export DATABASE_URL="postgresql+asyncpg://notebook:password@127.0.0.1:${QUALITY_PORT}/notebook_bot"
export BOT_TOKEN="local-quality-placeholder"
export MINIMAX_API_KEY="local-quality-placeholder"
export ALLOW_ALL_USERS="true"
export RUN_DB_TESTS="1"
export BACKUP_DIR="$QUALITY_BACKUP_DIR"
export PYTHONPATH="."

uv run alembic upgrade head
uv run alembic check
uv run python scripts/check_complexity_ratchet.py
uv run pytest --cov --cov-report=term-missing --cov-report=xml
scripts/check_critical_coverage.sh
scripts/check_risk_coverage.sh
