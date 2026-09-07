#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"
if [ -n "${DAILYPLANNER_COMPATIBLE_DATABASE_HEAD:-}" ]; then
    # Code rollback: never ask the old migration graph to upgrade a newer DB.
    "$PYTHON" scripts/preflight.py \
        --compatible-database-head "$DAILYPLANNER_COMPATIBLE_DATABASE_HEAD"
else
    "$PROJECT_DIR/.venv/bin/alembic" upgrade head
    "$PYTHON" scripts/seed_knowledge.py
    "$PYTHON" scripts/preflight.py
fi
exec "$PYTHON" -m bot.main
