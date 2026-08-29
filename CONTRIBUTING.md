# Contributing to DailyPlanner

DailyPlanner is a production Telegram planner with PostgreSQL as its durable
source of truth. Keep changes small, typed and covered by regression tests.

## Local checks

Полный локальный test/coverage gate сам поднимает disposable pgvector
PostgreSQL, применяет миграции, включает integration tests и всегда удаляет
контейнер вместе с volume:

```bash
scripts/run_local_test_gate.sh
```

Быстрые проверки без coverage gate можно запускать отдельно:

```bash
uv sync --frozen --group dev
uv run ruff check .
uv run mypy --explicit-package-bases bot scripts
uv run pytest -q
uv run bandit -q -r bot scripts -x bot/db/migrations -ll
```

Do not weaken a gate to merge a change. PostgreSQL integration tests require a
disposable migrated database and `RUN_DB_TESTS=1`; the canonical script above
owns that lifecycle and must never point at production. Update architecture,
operations and privacy docs whenever a public contract or operator procedure
changes.

Never commit `.env`, Telegram sessions, backups, user content or credentials.
Report security issues using [SECURITY.md](SECURITY.md), not a public issue.
