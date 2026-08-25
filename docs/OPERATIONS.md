# Operations runbook

## Reliability targets

- Reminder delivery lag: no more than 120 seconds under normal operation.
- Backup RPO: at most 24 hours (daily scheduled `pg_dump`).
- Restore RTO objective: 30 minutes. Every release drill prints the measured
  `rto_seconds`; retain that value with release evidence.
- Backup freshness alert: 30 hours, allowing for the scheduled window.

`/status` shows PostgreSQL/LLM/embedding/STT health, the latest observed STT
transcription latency, plus reminder and backup SLOs.
Backup status is `ok` only when the persistent marker is fresh and its named
archive, recorded byte size and checksum sidecar still agree. The hourly
maintenance loop uses that marker as a daily slot and performs catch-up after a
restart that missed the configured backup hour.
Violations are logged and sent to configured Telegram admins, throttled to one
alert per SLO per hour. In-process counters include LLM/STT errors, LLM queue
depth, reminder lag and scheduler job duration.

Telegram delivery is at-least-once at the message boundary. If a multipart
digest or memoir fails partway through, its durable ledger resumes from the
first part not acknowledged in PostgreSQL. A crash after Telegram accepts a
part but before its database commit can still repeat that one part; Telegram
offers no atomic transaction with PostgreSQL.
The worker renews its lease after every acknowledged part and fences every
progress/final update by lease token; a worker that loses ownership never
reports the batch as completed.
Completed delivery payloads are removed by the transient-state retention job
after 30 days; pending batches are retained for recovery and investigation.

DailyPlanner userbot E2E uses the dedicated account listed under
`testing.e2e_user_ids`. Each run has a `DP-<UTC>-<random>` audit ID, but cleanup
does not trust the LLM to preserve that marker in generated titles: pre-cleanup
and mandatory `finally` teardown wipe all domain/transient data for that
dedicated account while preserving its registration and settings. The cleanup
script rejects users outside the configured allowlist and is dry-run unless
`--execute --all-user-data` are both supplied.

`scripts/evaluate_llm_contracts.py` reports parser accuracy and utterance
contract accuracy for anonymized saved provider responses. It verifies tool
names, expected arguments and the current function schemas. Runtime counters
additionally expose fallback and invalid-tool frequency; extend the fixture for
every production misclassification before changing prompts.

The credentialed release gate runs locally on the production Mac, where the
dedicated Telegram test session exists:

```bash
scripts/run_live_e2e_gate.sh
```

The wrapper runs preflight, invokes the isolated messy-human suite, requires
every executed case to pass, and relies on the runner's mandatory `finally`
teardown. Its full report remains in the userbot repository. Hosted CI runs the
deterministic 22-case LLM contract gate and container E2E; it intentionally has
no access to a personal Telegram session.

## Release checklist

1. Start from a clean checkout and run `uv sync --frozen --dev --extra stt`.
2. Run `uv run ruff check ...`, `uv run mypy ...`, `uv run pytest` and
   `uv run alembic heads`; CI is the canonical command list.
3. Create a pre-release backup and verify its `.sha256` sidecar.
4. Run `uv run python scripts/restore_drill.py --backup BACKUP.sql.gz` with
   `OPERATOR_DATABASE_URL` supplied by the platform credential wrapper. It
   always creates a random `dailyplanner_restore_drill_*` database and removes
   it afterwards; it cannot select the production database as its restore target.
   When drilling a backup made before the release migration, pass the database's
   recorded revision as `--expected-revision REVISION`.
5. For the official macOS target run its installer/preflight. For Docker run
   the same two-file Compose E2E used by the `container-e2e` CI job. It must
   reach healthy from an empty PostgreSQL volume before a VPS release.
6. Restart exactly one service. The PostgreSQL singleton lease makes a second
   instance exit before Telegram polling or scheduler startup.
7. Check logs, `/status`, current Alembic revision and one non-mutating Telegram
   command. Confirm the next reminder sweep and backup marker.
8. Run `scripts/run_live_e2e_gate.sh`; release only when every live case passes
   and teardown reports success.

## LaunchAgent log maintenance

Install the independent daily maintenance job after the main service:

```bash
platform/macos/install-log-maintenance.sh
launchctl print gui/$(id -u)/com.notebook-bot-log-maintenance
```

It runs at 02:30 and rotates only four exact regular files in
`~/Library/Logs/notebook-bot`: main stdout/stderr and recovery-drill
stdout/stderr. Files larger than 10 MiB are copied to `.1`, seven generations
are retained, and the active inode is truncated in place because launchd and
the bot keep their descriptors open. Symlinks and non-regular files are
rejected. The first installer run also kickstarts the job; verify its JSON
summary in `log-maintenance.stdout.log` and confirm the main bot PID remains
healthy. Rotated artifacts are evidence and must not be deleted during deploy.

## Verified privacy deletion

Run `scripts/delete_user_data.py TELEGRAM_ID` first and review content-free row
counts. If the target and scope are correct, stop the main bot LaunchAgent and
repeat with `--execute --confirm DELETE-TELEGRAM_ID`. The command refuses
administrator accounts and `ALLOW_ALL_USERS`, and acquires the bot's PostgreSQL
singleton lease so the runtime cannot be active or restart mid-operation. It
atomically removes ordinary-user access, executes deletion in one transaction
and exits non-zero unless every associated table verifies zero. Restart the bot
only after the zero-verification result.
Never test this command against a production user; the PostgreSQL integration
suite creates and deletes a disposable account in a disposable database.

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

## Recovery operator and scheduled drill

The application role must remain `NOSUPERUSER NOCREATEDB NOCREATEROLE`. Recovery
uses a separate `dailyplanner_recovery` login with only `CREATEDB`. The drill
rejects both an underprivileged role and a role with `SUPERUSER`, `CREATEROLE`
or `REPLICATION`.

Pgvector is not a trusted PostgreSQL extension, so a CREATEDB-only role cannot
install it. Provisioning therefore creates an admin-owned, non-connectable
`dailyplanner_recovery_template` containing `vector`, `pg_trgm` and `pgcrypto`.
Extension objects inside the template are owned by the recovery role. The
one-time ownership setup is atomic: elevated capability is visible only inside
the uncommitted admin transaction and is revoked before commit. Every drill DB
is cloned from this template and remains owned by the recovery role.

On the production Mac mini:

```bash
scripts/provision_recovery_operator_macos.sh
platform/macos/install-recovery-drill.sh
platform/macos/run-recovery-drill.sh
```

The provisioning script reuses an existing password from macOS Keychain or
generates a new random password and stores it there before changing PostgreSQL.
The service is `dailyplanner-db-operator` and the account is
`dailyplanner_recovery`. This fail-fast ordering prevents a locked Keychain from
leaving the database role with an unknown password.

If macOS rejects non-interactive Keychain creation with `User interaction is
not allowed`, open **Keychain Access**, select the login keychain and create a
new **Password Item** with those exact service/account values. Then run this in
an ordinary logged-in Terminal and choose **Always Allow** if macOS asks whether
`/usr/bin/security` may read it:

```bash
security find-generic-password \
  -a dailyplanner_recovery -s dailyplanner-db-operator -w >/dev/null \
  && echo "Keychain access OK"
scripts/provision_recovery_operator_macos.sh
```

Do not put the password or `OPERATOR_DATABASE_URL` in `.env`, shell history,
plist or repository files. The wrapper assembles the URL in memory and runs the
latest verified backup with a 30-hour freshness limit.

LaunchAgent `com.notebook-bot-recovery-drill` runs Sunday at 04:30, after the
normal 03:00 backup. Successful evidence is appended as JSONL to
`~/Library/Logs/notebook-bot/recovery-drills.jsonl`; stdout/stderr have separate
logs. Each record includes backup name/size/SHA-256, migration, restored row
counts and `rto_seconds`, never credentials. A failed drill exits non-zero and
does not append a success record.

After every run verify that no database matching `dailyplanner_restore_drill_%`
remains. Rollback is: unload the recovery LaunchAgent, remove its Keychain item,
drop `dailyplanner_recovery_template`, then drop role `dailyplanner_recovery`
only after confirming it owns no remaining databases or objects. This rollback
does not affect the application role or `notebook_bot` database.

## Docker readiness and E2E

Docker is a cloud-adapter target; it intentionally omits the local Whisper
extra and Ollama. Copy `platform/linux/config.docker.yaml.example`, provide the
required environment values, and use `docker compose up -d --wait`. Do not
publish PostgreSQL unless an operator explicitly needs temporary local access.

The container entrypoint rejects a missing config, migrates to Alembic head,
seeds knowledge and runs preflight before the application command. Runtime
readiness is stricter than process liveness: a separate probe checks the event
loop heartbeat, runtime PID, database query and migration revision. The CI
smoke override additionally verifies required PostgreSQL extensions, an ORM
write/read/delete cycle and a 768-dimensional pgvector roundtrip without using
Telegram or provider secrets. This hermetic smoke does not replace the release
check of `/status` and one non-mutating Telegram command with real adapters.
