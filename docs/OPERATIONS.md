# Operations runbook

## Reliability targets

- Reminder delivery lag: no more than 120 seconds under normal operation.
- Backup RPO: at most 24 hours (daily scheduled `pg_dump`).
- Restore RTO objective: 30 minutes. Every release drill prints the measured
  `rto_seconds`; retain that value with release evidence.
- Backup freshness alert: 30 hours, allowing for the scheduled window.

`/status` shows PostgreSQL/LLM/embedding health plus reminder and backup SLOs.
Violations are logged and sent to configured Telegram admins, throttled to one
alert per SLO per hour. In-process counters include LLM/STT errors, LLM queue
depth, reminder lag and scheduler job duration.

Telegram delivery is currently at-least-once at the message boundary. If a
multipart send fails partway through, a retry can repeat the already delivered
parts. Durable per-part delivery ledger/outbox is tracked as a separate
architectural change.

`scripts/evaluate_llm_contracts.py` reports tool-call parser accuracy and
invalid-tool rate for anonymized saved provider responses; это не online intent
evaluation пользовательских utterance. Runtime counters additionally expose
fallback and invalid-tool frequency; extend the fixture for every production
misclassification before changing prompts.

## Release checklist

1. Start from a clean checkout and run `uv sync --frozen --dev --extra stt`.
2. Run `uv run ruff check ...`, `uv run mypy ...`, `uv run pytest` and
   `uv run alembic heads`; CI is the canonical command list.
3. Create a pre-release backup and verify its `.sha256` sidecar.
4. Run `uv run python scripts/restore_drill.py BACKUP.sql.gz`. It always creates
   a random `dailyplanner_restore_drill_*` database and removes it afterwards;
   it cannot select the production database as its restore target.
   When drilling a backup made before the release migration, pass the database's
   recorded revision as `--expected-revision REVISION`.
5. For the official macOS target run its installer/preflight. Docker build is an
   experimental compatibility check until container E2E/readiness becomes a
   release gate.
6. Restart exactly one service. The PostgreSQL singleton lease makes a second
   instance exit before Telegram polling or scheduler startup.
7. Check logs, `/status`, current Alembic revision and one non-mutating Telegram
   command. Confirm the next reminder sweep and backup marker.

## Rollback

1. Stop the new process. Keep the pre-release backup and checksum immutable.
2. If the migration is backward compatible, deploy the previous Git revision and
   run its preflight. Do not downgrade the database merely to roll back code.
3. For a destructive/schema rollback, provision a separate database, restore the
   pre-release backup with the drill procedure, validate it, then switch
   `DATABASE_URL` during a maintenance window.
4. Start one instance and verify `/status`. Preserve failed-release logs and the
   measured recovery time.

Never pipe an unverified archive directly into the production database.
