#!/bin/sh
set -eu

if [ ! -f /app/config.yaml ]; then
    echo "Missing /app/config.yaml; copy config.docker.yaml.example and set DAILYPLANNER_CONFIG_PATH" >&2
    exit 1
fi

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

if [ "$#" -eq 0 ]; then
    set -- python -m bot.main
fi
exec "$@"
