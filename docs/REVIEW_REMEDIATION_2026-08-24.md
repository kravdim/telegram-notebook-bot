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
- [ ] Изолированный userbot namespace с уникальным run ID и teardown.
- [ ] Полный container E2E/readiness либо удаление Docker target.
- [ ] Operator DATABASE_URL и регулярный измеряемый recovery drill.
- [ ] Полная выплата Ruff/mypy debt с расширением CI gate.
- [ ] Файловая ротация launchd stdout/stderr и проверяемый deletion workflow.
- [ ] Coverage/security/dependency gates и дальнейшая UX-полировка настроек.

Эти пункты не следует объявлять закрытыми локальными workaround: для них нужны
отдельные контракты, эксплуатационные проверки и миграционный план.

## Проверка и production

- `pytest`: 140 passed, 5 skipped;
- PostgreSQL integration suite: 7 passed на disposable `pgvector:pg16`, включая
  partial retry и конкурентный DB lease outbox;
- CI-critical Ruff, operational Ruff, выбранные mypy-модули и compileall:
  успешно;
- tool-call fixtures: 6/6, invalid tool rate 0;
- свежий production backup `notebook_bot_2026-08-24_085512.sql.gz` прошёл
  SHA-256 и `gzip -t`;
- restore drill ожидаемо заблокирован отсутствием `CREATEDB` у application role;
- LaunchAgent перезапущен одним экземпляром; migration `f4b8c2d6e1a0`, обе
  outbox-таблицы, PostgreSQL preflight, Telegram, Ollama embedding, LLM health и
  прогрев Whisper проверены в production.
