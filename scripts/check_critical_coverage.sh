#!/bin/sh
set -eu

threshold="${CRITICAL_COVERAGE_MIN:-85}"

run_coverage() {
    if [ -x .venv/bin/coverage ]; then
        .venv/bin/coverage "$@"
    else
        uv run coverage "$@"
    fi
}

for critical_module in \
    bot/middleware.py \
    bot/handlers/privacy.py \
    bot/services/user_deletion.py \
    bot/services/export.py \
    bot/services/user_export.py \
    bot/services/delivery.py \
    bot/db/crud/reminders.py \
    bot/scheduler/reminders.py \
    bot/scheduler/task_reminders.py
do
    run_coverage report \
        --include="$critical_module" \
        --fail-under="$threshold"
done
