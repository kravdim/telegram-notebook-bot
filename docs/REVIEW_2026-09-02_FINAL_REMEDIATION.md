# Remediation финальной перепроверки 2 сентября 2026

Remediation опубликован релизом `v0.3.1`. Tag, release и production указывают
на `61d2dd457c067dbe36863845286a30359035f5db`; GitHub API подтверждает
`immutable=true`. Исходное независимое ревью сохранено без изменений в
[`REVIEW_2026-09-02_FINAL_VERIFICATION.md`](REVIEW_2026-09-02_FINAL_VERIFICATION.md).

## Результат по замечаниям

| Пункт | Статус | Реализация |
|---|---|---|
| FINAL-001 | Закрыт в коде и isolated drill | Installer исполняется в 11 failure-injection сценариях: dependencies, DB, Telegram, STT, plist, migration/post-migration, candidate load/readiness и два отказа rollback. Каждый выход имеет атомарный phase report; старый revision сохраняется. Реальный PostgreSQL migration/recovery остаётся частью canonical gate. |
| FINAL-002 | Внешний gate | Единственный collaborator — `kravdim`. Обязательный approval сделал бы репозиторий несмерживаемым. Stale dismissal включён; approval/CODEOWNERS включаются после добавления второго reviewer. |
| FINAL-003 | Первый milestone закрыт | `bot.main` уменьшен с 435 до 295 строк и лишён complexity suppression. Background lifecycle вынесен в `bot.runtime.background`. Создание задачи вынесено из 43-complexity/104-statement dispatcher function в typed application use case. Ratchet снижен до 16 нарушений в 9 функциях и теперь исполняется в CI. |
| FINAL-004 | Закрыт | Распознанные leading/trailing `сегодня`/`завтра`, `срочно` и explicit priority удаляются из title. Неизвестные qualifier-варианты fail closed. |
| FINAL-005 | Улучшен | Overall gate поднят с 70% до 71%, `main.py` с 25% до 35%, dispatcher с 50% до 58% и остальные low floors также подняты; новые task use case/background lifecycle имеют floors 90%/75%. |
| FINAL-006 | Закрыт для новых релизов | Repository immutable releases включены. Workflow публикует draft со всеми assets и после publish требует API `immutable=true`. Исторический `v0.3.0` GitHub не меняет ретроактивно. |
| FINAL-007 | Scope ограничен | До письменного legal/product sign-off запрещена обработка данных внешних клиентов; разрешён только закрытый owner/internal контур. |

## Проверки до релиза

- canonical PostgreSQL gate: 450 passed, 1 skipped;
- measured coverage: 72.24% при новом floor 71%;
- intentional macOS deploy failure drill: 11/11 passed;
- targeted task/startup/deploy suite: 80 passed;
- Ruff, strict application mypy, documentation/version contracts и complexity
  ratchet: PASS;
- complexity debt: 16 нарушений в 9 allowlisted функциях; новые и расширенные
  exceptions запрещены.

## Release и production evidence

- PR [#8](https://github.com/kravdim/telegram-notebook-bot/pull/8) прошёл все
  пять required checks и был влит rebase-merge;
- post-merge CI run `33653912307` и release workflow run `33660146451`: PASS;
- immutable [GitHub Release
  v0.3.1](https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.1)
  содержит image tar, CycloneDX SBOM, checksums и attestation;
- pre-deploy backup проверен; recovery drill восстановил 20 таблиц до migration
  `a6c9d1e4f7b2` за 0,54 s;
- staged production deploy завершён с `status=deployed`, `phase=complete`,
  previous release сохранён, rollback не потребовался;
- production heartbeat подтверждает SHA `61d2dd4`, process работает без
  restart/exit, reminders и backup SLO имеют статус `ok`;
- первый live E2E попал в ожидаемый fail-close conflict с плановым
  memoir/chronometry interaction; повторный изолированный run
  `DP-20260902T181030-4a57c7` завершился `85/85 PASS`, state oracle `12/12`,
  cleanup residual `{}`.

## Оставшийся backlog

- пригласить независимого GitHub reviewer и только после этого включить one
  approval, CODEOWNERS и last-push approval
  ([issue #6](https://github.com/kravdim/telegram-notebook-bot/issues/6));
- декомпозировать list/update task execution и message intent adaptation в
  [`v0.4.0`](https://github.com/kravdim/telegram-notebook-bot/issues/4), затем
  command presentation в
  [`v0.5.0`](https://github.com/kravdim/telegram-notebook-bot/issues/5);
- получить письменный legal/product sign-off перед внешним клиентским
  использованием
  ([issue #7](https://github.com/kravdim/telegram-notebook-bot/issues/7)).
