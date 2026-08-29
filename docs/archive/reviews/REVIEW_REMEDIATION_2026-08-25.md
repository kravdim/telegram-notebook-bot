# Исправления по повторному аудиту — 25.08.2026

Источник: [`archive/reviews/REVIEW_2026-08-25.md`](archive/reviews/REVIEW_2026-08-25.md).

## Принятые изменения

- ORM metadata теперь объявляет production partial unique index
  `uq_tasks_one_open_frog_per_user`; CI выполняет `alembic check` после upgrade.
- Recurrence использует единый due occurrence и исходный reminder offset.
  Если reminder следующего occurrence уже прошёл, политика продукта — поставить
  его на текущее время, но никогда не позже due time задачи.
- PostgreSQL стал единственным источником interaction state. Claim/transition/
  clear используют compare-and-set semantics; memoir, chronometry, voice и
  project completion не заменяют чужой активный workflow. Memoir date marker
  не фиксируется, пока state ответа не сохранён.
- Groq API key передаётся из `Settings.runtime_config_errors()` в общий runtime
  validator.
- Privacy deletion восстанавливает исходный whitelist при rollback/ошибке БД.
- Delivery outbox продлевает lease после каждой Telegram-части, fence-ит запись
  token/expiry и сообщает completion только после успешного финального update.
- Backup SLO проверяет архив, записанный размер и корректный checksum sidecar;
  persisted daily slot выполняет catch-up после пропущенного окна.
- Повторные task/frog/reminder callbacks сообщают фактический закрытый статус.
- p95 использует nearest-rank (`ceil`), включая малые выборки.
- В README зафиксирован наблюдавшийся RSS local Whisper и рекомендуемый запас
  RAM для macOS production.

## Проверка

- `alembic check`: `No new upgrade operations detected` на production schema;
- default unit/scenario suite: 190 passed, 12 PostgreSQL tests skipped;
- combined suite with PostgreSQL integration enabled: 202 passed;
- Ruff: успешно; mypy: 98 source files без ошибок;
- coverage: 47,72% при floor 42%;
- Bandit medium/high, secret scan и LLM saved-response contracts: успешно.

## Выполненный архитектурный этап

После reliability-исправлений выполнена совместимая миграция рекомендованных
границ: выделены `IntentNormalizer`, строгие typed intents,
`InteractionService` и общий application command bus. Детерминированные и LLM
команды проходят один контракт и реестр исполнителей; Telegram handlers и
schedulers используют application service для interaction workflows. Старые
business handlers сохранены за адаптерами, поэтому миграция не меняет внешний
контракт и может продолжаться по частям.

Метрика `utterance_contract_accuracy` остаётся контрактом сохранённых provider
responses, а не измерением online intent accuracy; документация уже использует
это точное определение.
