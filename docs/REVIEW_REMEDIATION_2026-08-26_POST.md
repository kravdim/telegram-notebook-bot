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

## Проверка до CI/deploy

- Unit/scenario: `203 passed, 17 skipped`.
- Disposable PostgreSQL: `17 passed`, migrations from zero to
  `f4b8c2d6e1a0`, backup/restore — 20 tables, RTO 0,17 с.
- Coverage: 45,90% при floor 45%; messages 71%, voice 61%, privacy
  operator workflow 72%.
- Ruff, mypy (98 files), Bandit medium/high, dependency audit, compileall,
  secret scan и `git diff --check`: PASS.
- LLM fixtures: 6/6 parser cases, 17/17 utterance contracts.

## Осталось до release acceptance

1. Product commit и зелёный CI на этом SHA.
2. Production backup/recovery drill и deploy одного LaunchAgent.
3. Один чистый live gate 82/82 с teardown и отдельный voice
   handled-error fault check.
4. Только после этого обновить release handoff и решать вопрос
   об annotated `v0.2.0` tag.
