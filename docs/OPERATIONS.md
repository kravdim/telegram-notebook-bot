# Operations runbook

## Исправления ревью 7 сентября 2026

Статус работы и результаты проверок: [REMEDIATION_2026-09-07.md](REMEDIATION_2026-09-07.md).

Docker Compose разделяет bootstrap PostgreSQL (`postgres`), владельца миграций
(`notebook_migrator`) и runtime (`notebook`). Задайте три разных пароля:
`POSTGRES_PASSWORD`, `MIGRATION_DATABASE_PASSWORD`, `APP_DATABASE_PASSWORD`.
Рекомендуются случайные hex-значения, чтобы URL не требовал escaping.
Bootstrap-пароль не передаётся контейнеру бота; runtime не имеет SUPERUSER,
CREATEDB, CREATEROLE, BYPASSRLS и права создавать таблицы. Одноразовый сервис
`migrate` должен завершиться успешно до старта бота.

**Существующий volume не перепровизионируется переменными Compose.** Перед
переходом со старого superuser-профиля нужен maintenance-план: проверенная копия,
новый изолированный кластер/volume с разделёнными ролями, восстановление через
операторский workflow, проверка данных и runtime-прав, затем переключение.
Старый volume сохраняется до проверки восстановления. Не запускать `down -v`
для существующего deployment и не считать замену паролей в YAML миграцией ролей.

В macOS rollback на более новый schema head разрешён только при явном allowlist
и поддержке `DAILYPLANNER_COMPATIBLE_DATABASE_HEAD` старым startup. Этот режим
проверяет точный head и запускает код без повторной миграции/seed. При отсутствии
такого startup installer останавливается до изменения БД; нужен maintenance/restore.

Backup публикуется после окончания потокового dump/gzip, новая копия имеет права
0600. Ошибка backup не запускает возрастную ротацию; последняя точка восстановления
сохраняется даже старше обычного срока хранения. Срок удаления персональных данных
из последней старой копии требует решения оператора, если новые копии не появляются.
Полное отсутствие успешного backup считается SLO-ошибкой.
Локальный backup не защищает от потери хоста: оператор должен настроить независимое
зашифрованное хранение и проверять восстановление из него. Адрес внешнего хранилища
и ключи не заданы проектом и не создаются автоматически.

Outbox восстанавливается каждые 30 секунд и при старте. Повторы используют
сохранённые части, backoff от 60 секунд до часа, максимум восемь попыток;
ошибки запрета доступа/невалидного Telegram payload терминальны. Общая доставка
имеет срок 24 часа; периодический список задач действует до конца своего часа.
Сбой после принятия сообщения Telegram до записи acknowledgment по-прежнему может
повторить последнюю часть — API не предоставляет exactly-once отправку.

Readiness живого бота требует успешного getUpdates за последние 90 секунд,
а не только heartbeat event loop. Пустой успешный long poll тоже считается успехом.
Метрики содержат время последнего успеха каждого фонового job. Внешний монитор
должен проверять readiness/процесс независимо от Telegram-канала оповещения.
Синтетический container smoke использует только heartbeat и не доказывает polling.

Локальный Whisper допускает одну native работу без очереди. Timeout пользовательского
запроса не освобождает native слот; новые задания отклоняются до окончания работы.
Native-библиотеку нельзя безопасно прервать из Python thread: постоянно зависший
worker требует перезапуска процесса. Export ограничен одним параллельным архивом
и настроенным размером; ZIP сжимается вне event loop на согласованном DB-снимке.

## Reliability targets

- Reminder delivery lag: no more than 120 seconds under normal operation.
- Backup RPO: at most 24 hours (daily scheduled `pg_dump`).
- Restore RTO objective: 30 minutes. Every release drill prints the measured
  `rto_seconds`; retain that value with release evidence.
- Backup freshness alert: 30 hours, allowing for the scheduled window.

`/status` shows PostgreSQL/LLM/embedding/STT health, the latest observed STT
transcription latency, plus reminder and backup SLOs.
Backup status is `ok` only when the persistent marker is fresh and its named
archive, recorded byte size, recorded digest and checksum sidecar metadata
agree. This frequent check is structural; the recovery drill performs the full
archive SHA-256 verification. The five-minute
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
every executed case to pass, and requires an independent PostgreSQL oracle for
the multi-intent effects, negative non-effects and zero-residual teardown. The
report records `response matched`, `state verified` and `cleanup verified`
separately. Voice files and wrapper logs live in mode-0700 run directories and
are removed in `finally`; a failed log is retained only when
`DAILYPLANNER_KEEP_FAILURE_LOG=1` is explicitly set. Hosted CI runs the
deterministic 22-case LLM contract gate and container E2E; it intentionally has
no access to a personal Telegram session.

After a faster-whisper or PyAV upgrade, run the native resource drill on the
production-class Mac with a non-sensitive audio fixture. It performs 20 full
transcriptions, explicitly unloads the model and fails on unbounded worker-thread
growth; the process must also exit without `resource_tracker` warnings:

```bash
RUN_STT_RESOURCE_TESTS=1 STT_RESOURCE_AUDIO=/absolute/path/to/fixture.ogg \
  uv run pytest -q tests/integration/test_local_stt_resources.py
```

## Release checklist

1. Start from a clean checkout and run `uv sync --frozen --dev --extra stt`.
2. Run `scripts/run_local_test_gate.sh`; CI invokes this same disposable
   PostgreSQL lifecycle in `canonical-local-gate`, in addition to focused
   quality/security/container jobs.
3. Create a pre-release backup and verify its `.sha256` sidecar.
4. Run `uv run python scripts/restore_drill.py --backup BACKUP.sql.gz` with
   `OPERATOR_DATABASE_URL` supplied by the platform credential wrapper. It
   always creates a random `dailyplanner_restore_drill_*` database and removes
   it afterwards; it cannot select the production database as its restore target.
   When drilling a backup made before the release migration, pass the database's
   recorded revision as `--expected-revision REVISION`.
5. For the official macOS target run its staged installer. It refuses dirty
   tracked files, creates a versioned release, validates config/DB/migrations,
   Telegram credentials, STT warmup and candidate plist before switch, then
   requires a fresh heartbeat carrying the exact release SHA. On failure it
   restores the previous plist/revision and records the result under
   `~/Library/Application Support/notebook-bot/state/last-deploy-report.txt`.
   For Docker run
   the same two-file Compose E2E used by the `container-e2e` CI job. It must
   reach healthy from an empty PostgreSQL volume before a VPS release.
   Standalone systemd installation is not a supported target.
6. Restart exactly one service. The PostgreSQL singleton lease makes a second
   instance exit before Telegram polling or scheduler startup.
7. Check logs, `/status`, current Alembic revision and one non-mutating Telegram
   command. Confirm the next reminder sweep and backup marker.
8. Run `scripts/run_live_e2e_gate.sh` only after deployment. The wrapper refuses
   to start unless repository `HEAD` exactly matches the production release
   marker, rechecks that SHA after the run, and records start/finish timestamps.
   Release acceptance requires every live case and teardown oracle to pass.

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
journals the cross-system operation, atomically replaces the YAML access list,
executes database deletion in one transaction and exits non-zero unless every
associated table verifies zero. If the host stops between phases, repeat the
same confirmed command to reconcile and resume it idempotently. Restart the bot
only after the zero-verification result.
Never test this command against a production user; the PostgreSQL integration
suite creates and deletes a disposable account in a disposable database.

## Rollback

The macOS installer performs this code rollback automatically on load or
bounded-readiness failure. The restored release is checked with its own Python,
readiness script and preflight. When candidate and previous Alembic heads differ,
the candidate head must be listed in
`bot/db/migrations/rollback_compatible_heads.txt`; the previous preflight then
accepts only that exact newer head. Additive expand/contract migrations may be
listed only after verifying that the immediately previous release can still
read and write the schema. An unlisted or destructive migration is rejected
before the process switch and requires a documented maintenance/restore plan.
A destructive schema rollback is never performed automatically.

Every attempt that reaches the deployment state directory atomically writes
`last-deploy-report.txt`, including pre-switch failures and failures while
loading or validating the previous LaunchAgent. Telegram credentials and STT
warmup are checked before migration; the expand/contract migration is applied
immediately before the process switch. Run the isolated intentional-failure
matrix with `scripts/run_macos_deploy_failure_drill.sh`. It executes dependency,
database, Telegram, STT, plist, migration, candidate load/readiness and broken
rollback cases with a fake LaunchAgent boundary and asserts both the report and
the preserved previous revision.

1. Stop the new process. Keep the pre-release backup and checksum immutable.
2. If the migration is backward compatible, deploy the previous Git revision and
   run its preflight. Do not downgrade the database merely to roll back code.
3. For a destructive/schema rollback, provision a separate database, restore the
   pre-release backup with the drill procedure, validate it, then switch
   `DATABASE_URL` during a maintenance window.
4. Start one instance and verify `/status`. Preserve failed-release logs and the
   measured recovery time.

Never pipe an unverified archive directly into the production database.

Repository-level immutable releases are enabled. The release workflow creates
a draft, attaches every image/SBOM/checksum/attestation asset, publishes it, and
then verifies the API `immutable` flag. Immutability applies to releases created
after the repository setting was enabled; historical `v0.3.0` remains mutable.

## macOS network profiles

`platform/macos/install.sh` renders a direct-network LaunchAgent by default;
the tracked plist contains no site-specific proxy endpoints. A proxy is an
explicit host profile, for example:

```bash
platform/macos/install.sh \
  --http-proxy http://127.0.0.1:1081 \
  --all-proxy socks5://127.0.0.1:1080
```

The installer rejects proxy URLs containing credentials and verifies Telegram
reachability through every requested endpoint before replacing/restarting the
LaunchAgent. Keep authenticated proxy material outside plist and `.env`.

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

The supported Compose profile runs migrations and seeding once in the separate
`migrate` service. The application entrypoint rejects a missing config, skips DDL
with `DAILYPLANNER_SKIP_MIGRATIONS=1`, and runs preflight before the bot command.
Direct image invocation retains the migration step unless that flag is set. Runtime
readiness is stricter than process liveness: a separate probe checks the event
loop heartbeat, runtime PID, database query and migration revision. The CI
smoke override additionally verifies required PostgreSQL extensions, an ORM
write/read/delete cycle and a 768-dimensional pgvector roundtrip without using
Telegram or provider secrets. This hermetic smoke does not replace the release
check of `/status` and one non-mutating Telegram command with real adapters.


## Review release candidate and recovery limits

Current local evidence is in [REMEDIATION_2026-09-07.md](REMEDIATION_2026-09-07.md).
The new head is `e0a3b5c7d914`. It is not allowlisted for automatic rollback into
an older runtime. Use the maintenance/snapshot procedure for an existing install.
A schema downgrade removes vector provenance, recurrence timezone and outbox
retry metadata; it maps failed/expired batches to legacy terminal delivered status
so the old sender cannot resurrect stale messages. This is intentionally lossy:
use a verified pre-upgrade snapshot to recover the old schema and audit meaning.

Backup archives are privately permissioned, streamed through gzip, fsynced and
published from a unique temporary path. Checksum sidecars are published atomically;
rotation occurs only after a successful archive and always retains the newest copy.
A crash between publishing archive and sidecar can leave an unverified archive;
restore automation only selects archives with valid sidecars. No prior recovery
point is removed in that window. Ordinary failure/cancellation cleans its partial
files; a hard kill can leave hidden `.partial` files for operator inspection.

The daily local backup objective does not protect against loss of the host.
An independent destination has not been configured by this change. Before calling
host-loss recovery ready, the operator must choose an off-host destination, encrypt
archives before transfer, retain the decryption key independently, enforce remote
retention and periodically restore a downloaded copy. Suggested acceptance targets:
remote verified copy age under 24 hours and measured full recovery under 30 minutes.
The local synthetic restore measurement is not evidence for those remote targets.

A local Whisper timeout cannot interrupt native inference safely. The process
admits no second inference until the native job finishes and queues unload behind
it. A permanently hung native library still requires a supervised process restart.
For personal use, the current concurrency budget is one LLM worker (queue limit
100), one native STT worker and one export. PostgreSQL pool limits are 5 regular
connections plus 10 overflow. Large-tenant pagination and distributed concurrency
are future capacity work; the included SQL benchmark does not certify such a load.
