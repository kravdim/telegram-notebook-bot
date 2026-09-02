#!/bin/sh
set -eu

run_coverage() {
    if [ -x .venv/bin/coverage ]; then
        .venv/bin/coverage "$@"
    else
        uv run coverage "$@"
    fi
}

# Per-surface ratchets prevent the overall percentage from hiding regressions.
# Raise a floor only together with tests that exercise real behavior.
while read -r module minimum; do
    run_coverage report --include="$module" --fail-under="$minimum"
done <<'EOF'
bot/main.py 35
bot/runtime/background.py 75
bot/application/task_creation.py 90
bot/handlers/commands.py 48
bot/llm/client.py 42
bot/llm/dispatcher.py 58
bot/db/crud/interaction_states.py 65
bot/handlers/voice.py 67
EOF
