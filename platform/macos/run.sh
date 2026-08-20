#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/alembic" upgrade head
"$PYTHON" scripts/seed_knowledge.py
"$PYTHON" scripts/preflight.py
exec "$PYTHON" -m bot.main
