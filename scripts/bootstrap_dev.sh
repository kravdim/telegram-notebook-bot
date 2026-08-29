#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/platform/dev/docker-compose.yml"
DEV_PORT="${DAILYPLANNER_DEV_DB_PORT:-55432}"
SMOKE_ONLY=0
CREATED_CONFIG=0

usage() {
    echo "Usage: $0 [--smoke]"
}

if [ "${1:-}" = "--smoke" ]; then
    SMOKE_ONLY=1
    shift
fi
if [ "$#" -ne 0 ]; then
    usage >&2
    exit 2
fi

command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the supported developer bootstrap" >&2
    exit 1
}
command -v uv >/dev/null 2>&1 || {
    echo "uv is required for the supported developer bootstrap" >&2
    exit 1
}

COMPOSE_PROJECT="dailyplanner-dev"
if [ "$SMOKE_ONLY" -eq 1 ]; then
    COMPOSE_PROJECT="dailyplanner-bootstrap-smoke-$$"
fi
compose=(docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")
cleanup() {
    if [ "$SMOKE_ONLY" -eq 1 ]; then
        "${compose[@]}" down --volumes --remove-orphans >/dev/null
    fi
    if [ "$CREATED_CONFIG" -eq 1 ]; then
        rm -f -- "$PROJECT_DIR/config.yaml"
    fi
}
trap cleanup EXIT

cd "$PROJECT_DIR"
if [ ! -f config.yaml ]; then
    cp config.yaml.example config.yaml
    if [ "$SMOKE_ONLY" -eq 1 ]; then
        CREATED_CONFIG=1
    fi
fi
uv sync --frozen --group dev --extra stt
DAILYPLANNER_DEV_DB_PORT="$DEV_PORT" "${compose[@]}" up -d --wait

runtime_env=(
    DATABASE_URL="postgresql+asyncpg://notebook:password@127.0.0.1:${DEV_PORT}/notebook_bot"
    BOT_TOKEN="developer-bootstrap-placeholder"
    MINIMAX_API_KEY="developer-bootstrap-placeholder"
    ALLOW_ALL_USERS="true"
    PYTHONPATH="."
)
env "${runtime_env[@]}" uv run alembic upgrade head
env "${runtime_env[@]}" uv run python scripts/preflight.py

if [ "$SMOKE_ONLY" -eq 1 ]; then
    echo "developer bootstrap smoke ok"
else
    echo "Developer PostgreSQL is ready on 127.0.0.1:${DEV_PORT}."
    echo "Stop it with: docker compose -p dailyplanner-dev -f platform/dev/docker-compose.yml down"
fi
