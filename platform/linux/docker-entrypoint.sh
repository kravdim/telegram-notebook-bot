#!/bin/sh
set -eu

attempt=0
until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Database migration failed after 30 attempts" >&2
        exit 1
    fi
    sleep 2
done

python scripts/seed_knowledge.py
python scripts/preflight.py
exec python -m bot.main
