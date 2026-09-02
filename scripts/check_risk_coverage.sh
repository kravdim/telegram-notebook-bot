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
bot/main.py 25
bot/handlers/commands.py 45
bot/llm/client.py 40
bot/llm/dispatcher.py 50
bot/db/crud/interaction_states.py 60
bot/handlers/voice.py 65
EOF
