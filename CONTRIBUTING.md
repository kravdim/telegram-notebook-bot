# Contributing to DailyPlanner

DailyPlanner is a production Telegram planner with PostgreSQL as its durable
source of truth. Keep changes small, typed and covered by regression tests.

## Local checks

```bash
uv sync --frozen --dev --extra stt
uv run ruff check .
uv run mypy --explicit-package-bases bot scripts
uv run pytest --cov=bot --cov=scripts --cov-report=term-missing
uv run bandit -q -r bot scripts -x bot/db/migrations -ll
```

Do not weaken a gate to merge a change. PostgreSQL integration tests require a
disposable migrated database and `RUN_DB_TESTS=1`; never point them at
production. Update architecture, operations and privacy docs whenever a public
contract or operator procedure changes.

Never commit `.env`, Telegram sessions, backups, user content or credentials.
Report security issues using [SECURITY.md](SECURITY.md), not a public issue.
