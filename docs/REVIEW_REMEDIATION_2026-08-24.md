# План по профессиональному ревью 24.08.2026

Источник: [`../REVIEW_2026-08-24.md`](../REVIEW_2026-08-24.md).

## Применено в текущем изменении

- [x] Мемуарник использует только PostgreSQL interaction state с TTL и принимает
  ответ только как reply на конкретный вопрос.
- [x] Неполная команда мутации уточняется локально и не получает старый LLM
  context.
- [x] Все способы завершения задачи используют единый application service.
- [x] Completion атомарно закрывает связанные напоминания, продолжает recurrence
  ближайшей будущей датой и защищён row lock от двойного callback.
- [x] Потенциально длинные списки команд и periodic task list разбиваются по
  лимиту Telegram с сохранением HTML.
- [x] Диагностический fake-mutation log больше не пишет пользовательский текст.
- [x] Очередь LLM ограничивает общим timeout и ожидание свободного места.
- [x] LLM health check всегда делает реальный probe.
- [x] Cloud STT передаёт bytes tuple, cloud embedding запрашивает и проверяет
  размерность 768.
- [x] Backup параллельно дренирует stderr, имеет общий timeout и секунды в имени.
- [x] Retention job удаляет expired interaction state и transient FSM/request
  записи старше 30 дней.
- [x] Онбординг отклоняет невалидное время и обратный рабочий интервал;
  настройки не позволяют отключить последний рабочий день.
- [x] README/architecture/runbook синхронизированы с кодом; Docker честно отмечен
  experimental; добавлены LICENSE и SECURITY.md.

## Отдельные архитектурные этапы

- [x] Delivery ledger/outbox для multipart digest/memoir с DB lease,
  возобновлением по частям и формальной гарантией at-least-once.
- [x] Выделенный userbot account, уникальный run ID, pre-cleanup и обязательный
  teardown с allowlist-защитой от удаления данных обычного пользователя.
- [x] Cloud-first Docker target с обязательным mounted config, чистым
  PostgreSQL/Alembic/schema/pgvector E2E в CI и readiness по свежему heartbeat,
  runtime PID, DB и точному migration head; negative stale-heartbeat probe
  проверен на disposable Compose stack.
- [x] Отдельный CREATEDB-only operator URL из macOS Keychain, закрытая extension
  template для pgvector, fail-closed capability checks, weekly LaunchAgent,
  JSONL evidence с SHA-256/row counts/RTO и гарантированный cleanup drill DB.
- [x] Полная выплата Ruff/mypy debt с расширением CI gate.
- [x] Файловая ротация launchd stdout/stderr и проверяемый deletion workflow.
- [x] Coverage/security/dependency gates и UX-полировка статистики и
  хронометража.

Все этапы выше закрыты отдельными контрактами, CI-gates и эксплуатационными
проверками; production-evidence текущего этапа фиксируется после установки
LaunchAgent ротации.

## Проверка и production

- `pytest`: 152 passed, 10 skipped, coverage 42,31% при обязательном floor 40%;
- PostgreSQL integration suite: 10 passed на disposable migrated PostgreSQL,
  включая verified privacy deletion, partial retry и конкурентный DB lease;
- полный Ruff и mypy по `bot` и `scripts`: успешно;
- coverage floor 40%, Bandit high/medium gate и frozen production dependency
  audit без известных уязвимостей включены в CI;
- tool-call fixtures: 6/6, invalid tool rate 0;
- свежий production backup `notebook_bot_2026-08-24_085512.sql.gz` прошёл
  SHA-256 и `gzip -t`;
- restore drill ожидаемо заблокирован отсутствием `CREATEDB` у application role;
- LaunchAgent перезапущен одним экземпляром; migration `f4b8c2d6e1a0`, обе
  outbox-таблицы, PostgreSQL preflight, Telegram, Ollama embedding, LLM health и
  прогрев Whisper проверены в production.
