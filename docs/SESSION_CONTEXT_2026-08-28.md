# Контекст релиза DailyPlanner — 28.08.2026

## Итог

Quality milestone независимого аудита закрыт и принят в production. Кодовый
commit `b147525` поднял общий coverage gate с 46% до 70%, добавил отдельный
порог 85% для критических access/privacy/export/delivery/reminder путей и
расширил набор на 116 тестов. Commit отправлен в `origin/main` и развёрнут
штатным `platform/macos/install.sh`.

Следующий documentation-only commit сохраняет этот handoff и не требует
повторного рестарта macOS runtime. Production application остаётся на
`b147525`; migration head не менялся: `a6c9d1e4f7b2`.

## Что реализовано

- Общий CI floor поднят до 70%; измеренный результат с PostgreSQL integration
  suite — 70,38%.
- `scripts/check_critical_coverage.sh` отдельно требует 85% для middleware,
  privacy, deletion/export, durable delivery и обоих reminder-контуров.
- Добавлены портфельные тесты для CRUD, access/privacy, dispatcher,
  scheduler/runtime, вторичных handlers, formatters, adapters и ops scripts.
- Coverage-артефакты `.coverage`, `coverage.xml` и `htmlcov/` исключены из Git.
- Три security unit-теста больше не открывают скрытые production DB connections:
  persisted interaction lookup теперь явно подменяется. Asyncpg resource warning
  устранён; финальный прогон считает RuntimeWarning и unraisable warnings
  ошибками.

## Проверки и CI

- Изолированный PostgreSQL 17 + pgvector: `371 passed, 1 skipped`, warnings 0.
- Overall coverage 70,38%; critical modules 85–100%.
- Ruff PASS; mypy PASS для 103 production/ops файлов; `git diff --check` PASS.
- GitHub Actions run `33176822117` полностью зелёный: secrets, quality,
  PostgreSQL migrations/schema drift, coverage gates, LLM contracts,
  backup/restore, container readiness, CycloneDX SBOM и Trivy PASS.
- В run появились non-blocking annotations о будущем отключении Node.js 20 для
  некоторых pinned GitHub Actions; текущий run исполнялся на Node.js 24 и прошёл.

## Backup и recovery

Перед deploy создан backup
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-28_164653.sql.gz`:
1 195 107 байт, gzip integrity PASS, SHA-256
`a4f7099c97c8f34ff3006f4777cf6cf873061cf737095ae9708a7983ef239dc5`;
sidecar совпадает.

Прямой noninteractive запуск `platform/macos/run-recovery-drill.sh` из текущего
automation-контекста получил Keychain exit 36 без раскрытия секрета. Уже
установленный LaunchAgent `com.notebook-bot-recovery-drill` имеет разрешённый
Keychain path: ручной `kickstart` завершился exit 0, восстановил 20 public tables
на migration `a6c9d1e4f7b2` с RTO 0,5 секунды. После cleanup осталось 0 баз
`dailyplanner_restore_drill_%`.

## Deploy и production acceptance

`platform/macos/install.sh` развернул application commit `b147525` и
перезапустил только `com.notebook-bot`. Финальное состояние LaunchAgent:
`running`, PID `41066`, `runs=1`, `last exit=(never exited)`. Активен один
процесс `python -m bot.main`; DailyPlanner tmux-дубля нет.

Post-deploy подтверждены:

- preflight и Alembic `a6c9d1e4f7b2 (head)`;
- singleton lease, Telegram polling и non-mutating `getMe` для
  `@daily76planner_bot`;
- MiniMax HTTP 200, Ollama embedding HTTP 200 и Whisper medium warm-up;
- reminder SLO `ok`: lag 0 секунд, pending 0;
- backup SLO `ok`: age 0,8 часа, artifact `metadata-ok`.

Production live gate `DP-20260828T143426-f0be1b` прошёл `82/82 PASS` за
773 секунды. PostgreSQL state oracle — `12/12`; teardown, immediate cleanup и
повторный cleanup через 15 секунд подтвердили нулевой остаток при сохранённой
регистрации E2E-пользователя. Отчёт:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260828_174724.md`.

## Открытые release-gates

- Юридическая проверка privacy notice и сроков хранения для целевого рынка —
  внешнее product/legal решение.
- Native STT resource drill на 20 транскрипций остаётся отдельным тяжёлым
  evidence-gate; production voice acceptance 4/4 прошёл.
- Следует обновить pinned Actions до выпусков с native Node.js 24 support, когда
  доступны проверенные immutable SHA.
- Release tag не создавался: запрос включал commit, push и deploy, но не
  публикацию новой версии.
