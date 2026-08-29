# Контекст post-remediation релиза DailyPlanner — 26.08.2026

## Итог

Повторный аудит `REVIEW_2026-08-26_POST_REMEDIATION.md` закрыт и развёрнут.
Основной remediation commit `7dd73f6` и follow-up live-gate commit `5fa100c`
находятся в `origin/main`. GitHub Actions runs `32999761929` и `33004194325`
полностью зелёные: quality, secrets, container E2E, coverage, LLM contracts,
schema drift, dependency/security checks и backup/restore прошли.

## Что исправлено

- Mutation request считается успешным только после реального mutating
  `CommandResult`; свободный текст провайдера больше не доказывает side effect.
- Добавлены typed `clarify_request` и `MessageOutcome`; retryable failures не
  закрывают idempotency request и не стирают voice transcript/session.
- Privacy deletion после повторного onboarding открывает новую journal
  generation и заново подтверждает нулевые row counts перед release lease.
- Memoir skip подтверждается только после успешного удаления state.
- Частая фраза живого reminder, явная запись `запиши в дневник: ...`, прошлые
  task/reminder даты, нулевой интервал и некорректные/относительные birthday
  даты получили узкие детерминированные пути без зависимости от LLM.

## Проверки

- Локально: Ruff PASS, mypy PASS (98 production/ops files), полный pytest
  `209 passed, 17 skipped`; целевые post-remediation regression tests 34/34.
- До основного коммита: disposable PostgreSQL integration 17/17, migration
  zero-to-head, backup/restore 20 tables, RTO 0,17 с; coverage 45,90% при floor
  45%; Bandit, dependency audit, compileall, secret scan и LLM fixtures PASS.
- Production Alembic: `f4b8c2d6e1a0 (head)`.

## Backup и recovery

Pre-deploy backup:
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-26_212903.sql.gz`,
1 177 904 байта, gzip integrity PASS, SHA-256
`830b804656cd21c6d5ff899504ce766bf6af0fe70365fb6ce0a35ef9e863a12e`;
sidecar совпал.

Recovery drill выполнен через Aqua LaunchAgent
`com.notebook-bot-recovery-drill`: exit code 0, 20 public tables, 2 users,
115 tasks, 12 delivery batches, migration head, RTO 0,47 с. Остаточной
`dailyplanner_restore_drill_%` базы нет.

## Production acceptance

`platform/macos/install.sh` перезапускал только `com.notebook-bot`. После
follow-up deploy активен один `Python -m bot.main`, стабильный PID `91533`;
tmux-дубля DailyPlanner нет. Singleton lease, PostgreSQL preflight, Telegram
polling, Ollama embedding и Whisper medium warm-up подтверждены.

Первый post-deploy gate на `7dd73f6` дал 76/82 за 1302 с и успешный teardown.
Он обнаружил пять безопасных, но слишком общих отказов на некорректных датах и
один реальный LLM miss для дневника. После follow-up `5fa100c` повторный gate
прошёл `82/82 PASS` за 792 с с успешными pre-cleanup и teardown. Отчёт:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260826_223045.md`.

Финальный SLO snapshot: reminders `ok`, lag 0 с, pending 0, target 120 с;
backup `ok`, age 1,0 ч, artifact `metadata-ok`, target 30 ч. `HEAD` и
`origin/main` совпадают на `5fa100c81735fab30eddef7ce6182fa35f2e3a8d`.

## Открытые замечания

- Annotated tag `v0.2.0` не создавался: запрос этого цикла включал
  commit/push/deploy, а аудит отдельно просит перед тегом документированный
  provider-error fault check для voice. Unit fault-injection уже покрывает
  handled и thrown failure; намеренно ломать production provider не стали.
- В macOS/PyAV остаётся известное duplicate Objective-C class warning во время
  voice probes; все четыре live voice/STT кейса прошли без crash/restart.
- Recurring Studio `payload generate:types` — отдельный host-risk, не дефект
  DailyPlanner.
