# DailyPlanner: remediation checkpoint 05.09.2026

## Запрос и границы

Пользователь одобрил исполнение
[плана комплексного ревью](REMEDIATION_PLAN_2026-09-04.md).
Проект личный, внешних клиентов и отдельной юридической приёмки не предполагается.
Весь план **ещё не завершён**. Это checkpoint реализации, а не новый релиз.

Ветка: `remediation/comprehensive-2026-09-04`.
Предыдущие коммиты: `4192af6` (review/plan), `196b60d` (callback/state failure fixes).
Production в рамках этой работы не изменялся. Последний известный release —
`v0.5.0`, runtime SHA `27ce9e0620a18b00e199584cf013351bb9b8040b`; в этом checkpoint
он не перепроверялся live. Версия пакета не повышалась.

## Реализовано локально

- Структурированные CommandResult и application contracts вместо sentinel text.
- Explicit null/False в update; bounded HTML splitting без зависания.
- Task lifecycle service для update/complete/cancel, связанная очистка alarms;
  generic status bypass запрещён. Recurring reopen пока безопасно отклоняется.
- Durable reminder claims, общий main/sweep sender, retry/backoff/failed,
  `/reminder_errors`, timezone серии/backfill, weekly multipart DeliveryBatch.
- Persisted request plan и atomic action effects/results; legacy nested commits
  внутри command transaction только flush. `/retry` продолжает сохранённый план,
  включая отдельную phase декомпозиции проекта; новый текст — отдельный запрос.
- InteractionPort без persistence dependency; SQLAlchemy adapter в services.
  Architecture test запрещает application imports из DB/LLM/handlers/services.
- Release зависит от reusable CI на том же SHA; portable SHA256SUMS и manifest.
- CI audit обоих cloud/STT dependency profiles; image пока только cloud.
- Parser metrics честно названы saved-response metrics, nested schema validation;
  отдельный corpus 13 настоящих utterances проверяет текущий task recognizer.
- Live runner зафиксирован commit + file checksums (включая lockfile/client code),
  корпус 85 cases. Full gate отвергает subset/skip-voice и неполное число cases.

Гарантии и ограничения: [ADR retry/delivery](ADR_2026-09-04_RETRY_AND_DELIVERY.md).

## Evidence

Полный `scripts/run_local_test_gate.sh` после runtime/architecture/eval изменений:
**532 passed, 1 skipped, coverage 73.52%**; Alembic upgrade до `c8e1f3a5b702`,
ORM drift отсутствует; complexity и critical/risk coverage gates проходят.
Изолированный Docker PostgreSQL и его временные данные удалены gate-скриптом.
Ruff, mypy (120 файлов), Bandit, version/docs checks прошли.
Runner lock read-only проверка возвращает 85; Telegram этим не затрагивался.
ShellCheck на Mac отсутствует; `bash -n` прошёл, ShellCheck остаётся CI проверкой.

Ни release workflow, ни production live E2E не считаются выполненными. Проверка
manifest/CI YAML локальная и не заменяет выпуск реальных скачиваемых артефактов.

## План на входе в продолжение (история checkpoint 7e63d56)

1. Добрать lifecycle tests на clear/reschedule/reopen, DST/month/catch-up и in-flight
   cancellation; убрать оставшиеся direct completion CRUD helpers (runtime ими не
   пользуется, но публичный bypass ещё существует).
2. Добрать request journal сценарии: конкурентный retry, потеря ответа Telegram,
   неизвестный исход COMMIT; voice transcript после partial failure. Заменить
   переходный ambient session adapter явными typed UoW/ports у task creation.
3. Consent при смене провайдеров сейчас зависит от статического notice version;
   нужен fingerprint получателей, negative egress tests и обновление privacy docs.
   STT audit добавлен в CI, но отдельный STT SBOM/image/drill ещё не выполнен.
4. До deploy доказать rollback новой схемы: v0.5.0 sender не понимает lease/failed.
   Не объявлять heads backward-compatible без реального старого runtime drill.
   Down migrations отказываются терять failed reminders и незавершённые plans.
5. R15/R16: актуализировать CLAUDE/README/OPERATIONS, ERD/glossary, UX walkthrough,
   защищённый undo, синтетические screenshots/demo и измеренные benchmarks.
6. Затем точный release SHA → CI/assets → deploy → полный live с state/cleanup
   oracles → окончательное acceptance evidence и context handoff.

Подробные открытые критерии и таблица R01–R16 остаются в плане. Не подменять их
одним общим зелёным прогоном и не сообщать пользователю «всё исправлено».

## Продолжение по просьбе пользователя 05.09.2026

В том же remediation branch реализован следующий блок:

- Legacy direct-completion CRUD helpers удалены. DB tests используют общий
  workflow; обход синхронизации reminders больше не остаётся в публичном API CRUD.
- При upsert/reschedule reminder отзывается старый lease, очищаются backoff/error
  state и attempts; failed reminder переиспользуется, а не дублируется новой записью.
- Добавлены PostgreSQL тесты clear/reschedule/stale ack, protected recurring reopen,
  concurrent request retries, injection потери ack после настоящего COMMIT.
  Это client-side failure injection, не реальное разрушение TCP-соединения.
- CommandSession нельзя унаследовать в дочерний asyncio Task и использовать
  одновременно. Переход к явным typed UoW/ports всё ещё остаётся отдельной работой.
- Consent fingerprint хранится в User; main/fallback LLM, embedding endpoint и
  STT provider входят в identity. API key/model tuning не являются новым получателем.
  Старые кнопки обычного privacy и onboarding не могут включить изменившийся набор.
- Text/voice блокируются до обработки при старом consent. Cloud reindex проверяет
  согласие перед каждой записью; DB тест отзывает его во время первого embed и
  доказывает, что вторая запись не отправляется. Уже in-flight запрос не отзывается.
- Privacy rejection сохраняет незавершённый `/retry`, а не завершает его навсегда.
- Добавлены DST-hour и leap-February/monthly-31 проверки; обновлены PRIVACY,
  ARCHITECTURE и [THREAT_MODEL](THREAT_MODEL.md). Threat model входит в doc gate.

Миграция `d9f2a4b6c803` добавляет fingerprint без фиктивного consent backfill.
После будущего deploy нужно один раз подтвердить `/privacy`; старое согласие
не принимается как согласие новому набору получателей. При downgrade именно этой
миграции consent отключается. Остальные ограничения rollback остаются неизменными.

Следующий блок: доказанный migration/runtime rollback; explicit typed task-creation
ports; полный voice/retry live; STT SBOM/profile acceptance; R15/R16 docs/UX/undo/demo.
Production не изменялся, main не затрагивался, новый release не создавался.

Итоговая локальная проверка этого продолжения: **557 passed, 1 skipped; coverage
74.01%**. `scripts/run_local_test_gate.sh` применил миграции до `d9f2a4b6c803`,
подтвердил отсутствие ORM drift и прошёл complexity/critical/risk gates.
Ruff, mypy (120 файлов), Bandit, documentation (11 активных файлов), version check
и `git diff --check` проходят. Это по-прежнему не live/release evidence.

## Следующий шаг: реальный migration rollback drill

Добавлены `scripts/run_migration_rollback_drill.py` и `migration_rollback_probe.py`.
Драйвер принимает только previous Git revision, а не production URL; сам создаёт
изолированный Docker PG и экспортирует старый source в tempdir. У v0.5.0 своя
`uv sync --frozen --no-dev --extra stt` среда и sample config. Новые migrations
и synthetic data выполняются отдельно от production. Дочерний probe проверяет,
что импортировал именно выбранный source tree.

Фактически проверено:

- старый preflight отвергает новый head;
- override head недостаточен: old sender выбирает failed/leased reminders;
- downgrade с pending request блокируется без частичной смены schema/consent;
- snapshot restore в отдельную БД проходит старые preflight, singleton/schema/vector
  smoke, чтение baseline и запись через старый task workflow;
- canary данных кандидата остаётся в исходной БД до cleanup.

Evidence: [JSON](evidence/MIGRATION_ROLLBACK_2026-09-05.json), previous `27ce9e0`,
candidate runtime `0676c18`, SHA проверяющих скриптов отдельно в JSON.
Temporary databases/snapshot удалены после успешной проверки. Production не затронут.
Drill добавлен в обязательный reusable CI, но сам remote CI пока не запускался.

**Следующее действие:** реализовать и проверить guarded maintenance-deploy.
Обычный `platform/macos/install.sh` остаётся fail-closed; не добавлять head в
compatibility allowlist. Нужны freeze writers до снимка, восстановление в новую
БД с сохранением failed candidate, exact snapshot/runtime identity и явный отказ
от автоматического restore, если уже принимались новые пользовательские updates.
Подробный контракт: [MIGRATION_ROLLBACK](MIGRATION_ROLLBACK.md).

Локальный quality gate после добавления drill: **564 passed, 1 skipped; coverage
72.89%**. Новые operational scripts входят в общий denominator; реальный Docker
drill выполнялся отдельно от pytest coverage. Migration/schema drift, complexity,
critical/risk floors, Ruff, mypy (122 файла), Bandit и docs gate прошли.
Дополнительная проверка драйвера запрещает exact-SHA evidence при dirty/untracked
runtime в `bot/` или незакоммиченном изменении зависимостей.

## Maintenance-deploy: ядро реализовано, адаптеры ещё впереди

Добавлен `bot/operations/maintenance.py`: durable журнал (atomic replace + fsync,
0600), общий с installer каталог `deploy.lock`, проверка checksum снимка,
проверки freeze/identity/data через порт, восстановление только в отдельную БД.
Разрешение snapshot rollback необратимо закрывается в журнале до runtime activation.
При неопределённом результате записи журнала перечитывается состояние с диска.
После admission автоматический restore запрещён; нужен разбор оператором.

21 новый fault-injection тест проверяет orchestration, повреждённый снимок,
изменённую identity/data, сбои миграции/валидации/активации, восстановление после
прерывания и installer lock. Это fake-port тесты с реальным файловым журналом,
не доказательство production freeze/restore. Полный локальный gate: **585 passed,
1 skipped, coverage 73.24%**, migrations/drift/complexity/critical/risk проходят;
Ruff, mypy bot и documentation gate проходят.

**Задача maintenance-deploy ещё не завершена.** Исполняемого CLI нет. Следующий
шаг — PostgreSQL snapshot/data-guard/restore adapter и macOS disable/bootout,
singleton lease, private plist/database override и bounded activation. Затем
failure injection конкретных адаптеров с реальным PostgreSQL; до этого production
deploy запрещён существующим schema gate. Не менять compatibility allowlist.
Детальный контракт дополнен в MIGRATION_ROLLBACK.md. Production не затронут.

## Продолжение: PostgreSQL snapshot/restore и data guard

Добавлены `bot/operations/maintenance_postgres.py` и `maintenance_data.py`.
Снимок pg_dump и fingerprint используют один exported MVCC snapshot. Проверяется
checksum; до восстановления требуется least-privilege CREATEDB operator и template.
Восстановление создаёт отдельную БД, проверяет head и fingerprint, выдаёт приложению
права на таблицы/последовательности. Исходная БД не изменяется. Имена recovery targets
фиксируются в fsynced 0600 manifest **до** createdb; неудачные targets сохраняются.

Data guard проверяет baseline rows и известные новые поля reminder leases,
action plan/results, timezone backfill и consent fingerprint. Неизвестные изменения
схемы или новые данные запрещают restore. Нельзя просто игнорировать новые колонки.
Пароли не передаются в argv; native subprocess timeout/cancellation завершают и
дожидаются child process. Требуется совпадение major PostgreSQL-клиента и сервера:
local PG17 tool против Docker PG16 показал отказ restore, поэтому тесты используют
реальные PG16 clients из изолированного контейнера. Это подключено и к CI, но remote
CI в этом продолжении не запускался. Нативный запуск клиентов покрыт unit tests.

Добавлены 23 теста: 10 real-DB и 13 client/unit. Реальные проверки используют
малую representative schema, не весь старый runtime: отдельный exact-release drill
остаётся предыдущим evidence. Проверены отдельный restore, application-role writes,
сохранение candidate canary, concurrent write во время dump, неизвестные schema
changes, новые recovery-bearing values и сохранение failed restore target.
Тестовые БД/роли удаляются внутри disposable gate; production не затронут.

Итоговый полный local gate: **608 passed, 1 skipped, coverage 73.66%**.
Migrations/drift, complexity, critical/risk gates, Ruff, mypy (126 файлов), Bandit,
documentation/version checks и diff whitespace проходят. B608 suppression ограничен
двумя динамическими SQL с dialect-quoted identifiers и константным whitelist;
экранирование нестандартных имён проверено реальной БД.

**Следующее действие:** связать компоненты с macOS freeze/lease/activation adapter
и runtime/config identity validation. CLI ещё нет, orchestration с реальными
launchd/Telegram не проверено; production schema gate/allowlist не менялись.
Восстановленные объекты принадлежат operator: будущие DDL/миграции требуют явного
решения по роли/ownership. Изменения этого и предыдущего maintenance checkpoint
остаются в рабочем дереве; commit/push/deploy в этом продолжении не выполнялись.

## Продолжение: launchd control и живой maintenance lease

Добавлены `maintenance_launchd.py` и `maintenance_lease.py`. Контроллер ограничен
`gui/<uid>/com.notebook-bot`; проверяет plist label/ownership/permissions и запрещает
symlink plist. Persistent disable выполняется до bootout; неизвестный disabled
output, недоступный domain и любой статус отсутствия кроме 113 блокируют процесс.
Вывод launchctl — diagnostic, не стабильный API; реальный macOS не переключался,
его версия/ответы требуют подтверждения перед production window.

Lease использует тот же advisory key, что SingletonLease runtime. Проверяется
реальный backend PID и точное владение lock, отсутствие остальных DB sessions
и prepared transactions. `pg_stat_clear_snapshot()` вызывается при каждой проверке,
чтобы новый клиент не был скрыт cached statistics. На реальном PostgreSQL проверены
конкуренция runtime/maintenance, snapshot connection cleanup, новый клиент после
успешного freeze, explicit unlock и invalidated connection. Factory PostgreSQL
теперь ограничивает connect timeout 10s и command timeout 60s.

Activation требует durable admission с rollback=false и identity:
`identity.candidate`, `identity.previous` — полные SHA; `identity.database` —
source identity hash, `identity.source_database` — имя исходной БД. Для previous
target берётся `activating_database`. Plist с DATABASE_URL пишется atomic/fsync/0600;
shared `.env` не меняется. Запускается release Python `-m bot.main`, **не run.sh**:
обычный скрипт запуска выполняет Alembic/seed до получения singleton, что недопустимо
для этого перехода. Migration/preflight/seeding policy должны жить в composition.
Lease снимается только после admission; затем enable/bootstrap и exact-SHA heartbeat
по новому уникальному readiness path. Любой неопределённый запуск ведёт к halt,
а не восстановлению снимка. current-release обновляется после readiness.

Добавлено 27 тестов: 23 launchd/unit и 4 real-DB lease. Проверены failure injection,
ошибка acknowledgement после фактического bootstrap, ошибка после записи plist,
native subprocess timeout/cancellation, подмена release/database и неверный plist.
Итоговый полный gate: **635 passed, 1 skipped, coverage 74.01%**; migrations/drift,
complexity/critical/risk, Ruff, mypy (128 файлов), Bandit, docs/version/diff проходят.
Production, Telegram, реальный launchd не затронуты; Git commit/push не выполнялись.

**Следующий шаг:** concrete composition MaintenancePort + exact artifact/config
validation и explicit maintenance CLI. Нужно связать все компоненты, получить
отдельный lease на восстановленной БД при old-runtime validation (source lease её
не защищает), проверить migration/preflight без раннего polling и сквозной failure
matrix. До этой приёмки нельзя считать maintenance-deploy завершённым и нельзя
менять rollback allowlist. Все maintenance-изменения нескольких checkpoints пока
незакоммичены; проверять весь diff, сохраняя текущее рабочее дерево.

## Следующий checkpoint: единая процедура и explicit CLI

Добавлены `bot/operations/maintenance_release.py`, `maintenance_deploy.py` и
`scripts/maintenance_deploy.py`. `MacMaintenance` реализует весь MaintenancePort:
initial validation -> persistent freeze/source lease -> snapshot -> реальный
verification restore + отдельный target lease + old preflight/smoke -> migrate ->
candidate preflight/smoke -> data guard -> journal admission -> launchd activation.
При восстановлении target lease удерживается до admission; shared `.env` не меняется.
Seeding не выполняется автоматически: release с seed changes требует отдельной
проверенной политики и адаптации data guard.

Release verification сравнивает prepared files и executable modes с exact Git
objects (без replacement refs), отклоняет extras/symlinks/submodules, требует private
dotenv, проверяет `uv sync --check --frozen --offline --no-dev --extra stt` и связывает
source/config/lock/interpreter fingerprints с release paths. Это не побайтовая
аттестация всех установленных dependencies: host/venv остаются trusted assets.
Команды и runtime используют новый PYTHONPYCACHEPREFIX и запрещают bytecode writes,
чтобы старый `.pyc` не подменил проверенный source. `.env` и config повторно хэшируются
при переходах; административные редакторы/писатели должны быть остановлены на окно.

CLI запускается как `.venv/bin/python -m scripts.maintenance_deploy` из repo.
Обязательны --repository, --release-root, --previous/--candidate (полные commit SHA),
--plist, --state-dir. DATABASE_URL и OPERATOR_DATABASE_URL только через защищённое
environment, не argv. По умолчанию plan-only: без stop/migration/restore/journal write.
Execution только на macOS с `--execute --confirm <MAINTENANCE-...>`; token связывает
operation+identity. Recovery имеет отдельный token (`--recover`) и подчиняется
durable запрету после admission. Journal state dir сверяется с installed READINESS_FILE
для общего deploy.lock. Чужой installed SHA/source DB отклоняется до freeze.
Exit 0 = plan/deployed; 2 = candidate failed, restored_previous; 1 = failure/manual
reconciliation. MaintenanceError содержит только специально безопасные diagnostics;
raw SQL/OS exception messages не печатаются. Не снимать stale lock автоматически.

Добавлен 21 тест: real disposable Git для artifact verification (uv check в этих
unit tests подменён), default-no-write/confirmation/error privacy CLI и 5 composition
сценариев с реальными PostgreSQL dump/restore/leases. В composition launchd и release
команды simulated на малой схеме: success, migration failure, validation failure,
post-snapshot new data, uncertain activation. Это **не** native macOS/exact-release
rehearsal. Предыдущий exact-old-release drill остаётся отдельным evidence, не новым.

Итоговый полный quality gate завершился с exit 0: **656 passed, 1 skipped**, coverage
**74.53%** (657 collected). Migrations/drift, complexity/critical/risk gates, Ruff,
mypy (131 файл), Bandit, docs/version и diff whitespace проходят. Production,
реальный launchd, Telegram и remote CI не затронуты. Commit/push/deploy не выполнялись.

**Дальше:** зафиксировать текущий проверенный код, подготовить exact-SHA releases и
провести rehearsal всей процедуры с настоящими old/candidate командами; отдельно
подтвердить native launchctl responses на целевой macOS и пройти remote release CI,
live/profile gates. Production window требует явного согласования. Compatibility
allowlist и обычный staged installer не ослаблялись. Все maintenance checkpoints
пока находятся в общем незакоммиченном рабочем дереве; не терять их при продолжении.

## Commit и exact-release composition rehearsal

Все накопленные maintenance runtime/CLI/tests изменения зафиксированы в
**`f79514052c249838215b3e2dba3c4764bc2c987a`** (`feat: add guarded maintenance deployment
workflow`). Ветка остаётся `remediation/comprehensive-2026-09-04`; main не менялась.

Добавлен `scripts/run_maintenance_rehearsal.py`. Два самостоятельных export/uv frozen
окружения old `27ce9e0` и candidate `f795140`; настоящие source verification, migration,
preflight, schema/vector smoke, PostgreSQL snapshot/restore и source/target leases.
Launchd и heartbeat simulated; bot.main/Telegram polling и внешние AI не запускаются.
Драйвер не принимает production URL, очищает inherited DB/UV/Git overrides и требует
committed runtime. Каждый сценарий имеет отдельную БД. Инъекция должна быть реально
достигнута; проверяются admissions/restores counts, чтобы случайный отказ не был
ошибочно принят за успешный failure test.

Итог **5/5**: success; failure после настоящей migration; failure после настоящего
candidate preflight; сохранение post-snapshot canary без restore; uncertain activation
с halt и rollback=false. Evidence: `docs/evidence/MAINTENANCE_REHEARSAL_2026-09-05.json`.
В нём exact previous/candidate SHA, source/config/lock/interpreter fingerprints,
snapshot checksums, driver/helper SHA256 и timestamps. Runtime f795140 не менялся
после репетиции; драйвер, CI/docs/evidence фиксируются отдельно. Нельзя выдавать это
за native-launchd или live/release доказательство другого будущего SHA.

Драйвер включён в reusable CI migration-rollback job; JSON сохраняется рядом со старым
drill report. Remote CI не запускался. Восемь новых unit/contract tests проверяют
disposable boundary, dirty-runtime rejection, environment isolation и CI obligation.
Полный local gate: **664 passed, 1 skipped; coverage 73.72%**, Ruff, mypy (132 файла),
Bandit, migrations/drift, complexity/critical/risk gates проходят. Реальный drill
выполнялся отдельно от pytest coverage, новый driver входит в общий denominator.

Временные release dirs, Docker container, synthetic databases и snapshots удалены
после проверки; JSON не является retained backup. Production не затронут, Git push
и deploy не выполнялись. Следующий этап — публикация ветки/remote CI, затем native
macOS и live/profile acceptance; production maintenance window согласовать явно.
