# Контекст reliability-релиза DailyPlanner — 26.08.2026

## Итог

Аудит `REVIEW_2026-08-26.md` применён. Product commit `43f9de6`
отправлен в `origin/main`, GitHub Actions run `32962136475` завершён
успешно, production развёрнут штатным macOS LaunchAgent. Аудит
сохранён в `docs/archive/reviews/`, а принятые изменения описаны в
`docs/REVIEW_REMEDIATION_2026-08-26.md`.

## Что доведено до контракта

- LLM critical path получил общий deadline, отключённые вложенные
  SDK retries и детерминированное bounded history без отдельного
  provider-вызова для compression.
- Voice и memoir callbacks привязаны к session token и Telegram message ID;
  устаревшие кнопки fail closed. Voice processing восстанавливается
  после restart.
- Memoir/chronometry writes и consume interaction state атомарны;
  project state не теряется до успешного side effect.
- Typed `CommandResult` доходит до Telegram adapter; delivery error path
  fenced тем же lease token.
- Privacy deletion получил crash-resumable journal. Backup marker сверяет
  имя, размер, digest и sidecar metadata; полный SHA остаётся за
  recovery drill.
- Версия проекта поднята до `0.2.0`, coverage floor — до 45%.

## Quality evidence

- Локально: `195 passed, 15 skipped`; все 15 PostgreSQL integration tests
  отдельно прошли на одноразовом pgvector PostgreSQL.
- Coverage 45,57% при floor 45%; Ruff, mypy (98 files), Bandit,
  dependency audit, compileall, secret scan, LLM contracts и `git diff --check`
  прошли.
- Production Alembic: `f4b8c2d6e1a0 (head)`, schema drift нет.
- GitHub Actions run `32962136475`: quality, secrets и container E2E зелёные.

## Backup и recovery

Перед deploy создан backup
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-26_141634.sql.gz`:
1 176 284 байта, gzip integrity прошла, SHA-256
`6fb7230527338f9e94d43b225c6eec86d11e31995129bbfd4bc2dd26889f7d3a`
совпал с sidecar.

Recovery drill запущен через `com.notebook-bot-recovery-drill`, чтобы
Keychain читался в реальном Aqua user context. Exit code 0: 20 public
tables, 2 users, 115 tasks, 7 delivery batches, migration head, RTO 0,60 с.
Disposable restore database удалена.

## Production acceptance

`platform/macos/install.sh` перезагрузил только `com.notebook-bot`.
Старый PID `1826` корректно освободил singleton lease; новый PID
`7044` пережил весь live gate без restart. Активен один
`python -m bot.main`; tmux-дубля DailyPlanner нет. Singleton, Telegram
polling, PostgreSQL preflight, Ollama embedding и Whisper medium warm-up
подтверждены.

Полный live runner завершил `82/82 PASS` за 846 с, включая `/status`,
reminders, callbacks, export, prompt injection и 4 voice/STT сценария.
Pre-cleanup и mandatory teardown успешны. Отчёт:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260826_164524.md`.

Post-load SLO snapshot: reminders lag 0 с, pending 0, target 120 с; backup age
2,5 ч, artifact `metadata-ok`, target 30 ч. Все status `ok`.

В stderr во время voice проб остаётся известное macOS/PyAV
Objective-C duplicate-class warning. Оно не вызвало crash, restart или
деградацию: все 4 voice сценария прошли. Внешний host-risk
с recurring Studio `payload generate:types` остаётся отдельной
инфраструктурной задачей и не является дефектом DailyPlanner.

## Состояние для следующей сессии

Релиз `0.2.0` завершён и принят. При восстановлении контекста
начать с `43f9de6`, CI `32962136475`, PID `7044` и этого handoff.
Новых применимых пунктов review не осталось; следующую продуктовую
работу начинать как новый этап, а не как продолжение remediation.
