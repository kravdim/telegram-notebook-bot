# Контекст релиза DailyPlanner — 27.08.2026

## Итог

Независимый аудит `REVIEW_2026-08-26_INDEPENDENT.md` разобран, полезные
замечания реализованы и приняты в production. Основной пакет — `beb070c`,
операционные follow-up — `6e8a5be`, `48655a5`, `75db606` и `ecca251`.
Последний исполняемый/code commit — `ecca251`; следующий documentation-only
commit сохраняет этот handoff. GitHub Actions run `33115110093` полностью
зелёный, включая quality, secrets, container readiness, SBOM и Trivy.

Companion live-runner в `/Users/moltbot/Projects/userbot` зафиксирован локально
коммитами `ea87f0f`, `4d90078`, `199b885` и `e0f99ef`. У этого репозитория нет
remote, поэтому отправка этих четырёх коммитов не выполнялась.

## Что дополнительно исправлено при приёмке

- Частично запущенный runtime теперь закрывает LLM queue, STT, Telegram session,
  singleton lease и DB engine даже при ошибке `setMyCommands`.
- PostgreSQL E2E oracle проверяет семантические side effects и отрицательные
  эффекты независимо от очистки заголовков LLM.
- Разговорная фраза `ну блин надо сегодня вечером ... наверное, если не забуду`
  получила узкий детерминированный task-path без зависимости от провайдера.
- Voice acceptance fixture стала самодостаточной и ждёт терминальный результат,
  поэтому teardown не гоняется с незавершённой voice-командой.
- Pinned Linux layers обновлены до Python 3.12 slim digest
  `09f7da3b...d85217` и `uv 0.12.6` digest `88bc6eb1...a4d3d`;
  OpenSSL security packages явно обновляются при сборке. Это закрыло новые
  HIGH findings без ignore/VEX исключений.

## Проверки

- Локально перед deploy: pytest `236 passed, 20 skipped`; Ruff PASS; mypy PASS
  для 103 production/ops файлов.
- Ранее в этом remediation: disposable PostgreSQL integration `19/19`, fresh
  migration и schema drift PASS; coverage 47,45% при floor 46%; Bandit,
  pip-audit, secret scan и LLM contracts PASS.
- Production migration: `a6c9d1e4f7b2 (head)`.
- Финальный live run: `DP-20260827T202240-0cc44a`, `82/82 PASS` за 789 секунд;
  state oracle `12/12`, teardown PASS, немедленный и повторный через 15 секунд
  cleanup-oracle — нулевой остаток. Отчёт:
  `/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260827_233554.md`.
- Финальный SLO snapshot: reminders `ok`, lag 0 секунд, pending 0; backup `ok`,
  age 9,2 часа, artifact `metadata-ok`.
- CI run `33115110093`: quality, secrets и container-e2e PASS; Trivy application
  image scan PASS. Отдельный scan `uv 0.12.6` также показал 0 HIGH/CRITICAL.

## Backup и deploy

Pre-deploy backup:
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-27_142241.sql.gz`,
1 185 952 байта, gzip integrity и sidecar PASS, SHA-256
`6011ad8380243103646a956d3fe2ed25232fbb4dee6ef9c04ee3f65f7cbf1146`.

`platform/macos/install.sh` развернул application commit `75db606`.
`ecca251` меняет только Linux Docker build и не требует повторного рестарта
macOS runtime. Production обслуживает один LaunchAgent `com.notebook-bot`:
PID `70842`, `runs=1`, `last exit=(never exited)`, устойчив более часа во время
финальной сверки. Telegram polling, singleton, PostgreSQL preflight, Ollama
embedding и Whisper medium warm-up подтверждены; tmux-дубля нет.

## Открытые release-gates

- Юридическая проверка privacy notice и сроков хранения для целевого рынка —
  внешнее product/legal решение, кодовый контур закрыт.
- Coverage 70% overall / 85% critical остаётся отдельным quality milestone;
  текущий измеренный уровень 47,45%, CI floor 46%.
- Native STT resource drill на 20 транскрипций остаётся отдельным тяжёлым
  evidence-gate; lifecycle и повторные циклы покрыты unit-тестами, live voice
  4/4 прошёл.
- Новый release tag не создавался, потому что запрос включал commit, push и
  deploy, но не публикацию версии. Tag-triggered provenance/checksums остаются
  до явно авторизованного релиза.
