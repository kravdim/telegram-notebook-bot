# Post-remediation план и evidence — 26.08.2026

Исходный аудит: `archive/reviews/REVIEW_2026-08-26_POST_REMEDIATION.md`.
Аудит верно отменил release acceptance: независимый live gate после
прежнего handoff дал 79/82, поэтому tag `v0.2.0` не создавался.

## Исправления

- Mutation request завершается успехом только после реального
  mutating `CommandResult`. Provider content и `respond_to_user` не
  доказывают side effect.
- Для уточнения добавлен отдельный non-mutating typed intent
  `clarify_request`; UI явно говорит, что изменение ещё не было
  выполнено.
- Точная падавшая live-фраза `слушай напомни через 2 минуты ...`
  получила узкий deterministic parser. Regression создаёт строку
  reminder в PostgreSQL, делает её due и проверяет реальную scheduler
  delivery.
- `process_text_message()` возвращает `MessageOutcome`: `completed`,
  `retryable_error`, `rejected` и `duplicate`. Handled provider/tool failure
  помечает request как failed для безопасного retry.
- Voice confirm очищает transcript/session только при `completed`;
  handled и thrown failures возвращают `voice_processing` в
  token-bound `voice_confirm` с кнопкой retry.
- Completed privacy journal теперь заново читает row counts и access
  list. После re-onboarding открывается новая operation generation с
  UUID; verification counts снимаются заново до освобождения
  singleton lease.
- Memoir skip подтверждается только после успешного clear;
  при ошибке state и кнопка остаются активными.

## Проверка

- Unit/scenario: `203 passed, 17 skipped`.
- Disposable PostgreSQL: `17 passed`, migrations from zero to
  `f4b8c2d6e1a0`, backup/restore — 20 tables, RTO 0,17 с.
- Coverage: 45,90% при floor 45%; messages 71%, voice 61%, privacy
  operator workflow 72%.
- Ruff, mypy (98 files), Bandit medium/high, dependency audit, compileall,
  secret scan и `git diff --check`: PASS.
- LLM fixtures: 6/6 parser cases, 17/17 utterance contracts.
- Product commits: `7dd73f6` и live follow-up `5fa100c`; GitHub Actions
  `32999761929` и `33004194325` зелёные.
- Follow-up добавил детерминированные явный diary write и отклонение прошлых,
  нулевых и некорректных дат; полный локальный pytest: `209 passed, 17 skipped`.
- Production backup `notebook_bot_2026-08-26_212903.sql.gz` проверен по gzip и
  SHA-256; recovery drill: 20 tables, migration head, RTO 0,47 с, exit 0.
- Финальный unified live gate: `82/82 PASS` за 792 с, teardown успешен; отчёт
  `report_20260826_223045.md`. Production PID `91533`, reminder/backup SLO `ok`.

## Перед annotated tag

Остаётся отдельный документированный voice provider-error fault check. Unit
fault-injection покрывает handled и thrown failure, но production provider
намеренно не ломался в этом deploy-цикле. До этого `v0.2.0` не создавать.
