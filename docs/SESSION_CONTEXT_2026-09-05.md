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
