# Контекст доводки DailyPlanner — 04.09.2026

## Текущее состояние

Milestones `v0.4.0` и `v0.5.0` завершены одним последовательным релизом
`v0.5.0`. Production, annotated tag и GitHub Release указывают на code SHA
`27ce9e0620a18b00e199584cf013351bb9b8040b`. Issues
[#4](https://github.com/kravdim/telegram-notebook-bot/issues/4) и
[#5](https://github.com/kravdim/telegram-notebook-bot/issues/5) закрыты через
merged PR [#19](https://github.com/kravdim/telegram-notebook-bot/pull/19).

Production LaunchAgent `com.notebook-bot` работает из versioned release
directory этого SHA. Deploy report: `status=deployed`, `phase=complete`,
`reason=none`; rollback release сохранён на
`365d0a7e8c8754c306b10b4f97411219e04689ea` (`v0.3.6`). После deploy процесс
имеет `runs=1`, `last exit code=(never exited)`, а readiness heartbeat содержит
`ready=true` и exact release SHA.

## Что сделано

- декомпозированы все девять оставшихся hotspots: runtime config, Telegram
  intent extraction, list/update task execution, backup, privacy deletion,
  Telegram HTML splitting, morning digest и weekly review;
- удалены все 16 legacy suppressions `C901`, `PLR0911`, `PLR0912` и `PLR0915`;
- complexity ratchet переведён в zero-exception режим и блокирует любое новое
  подавление этих правил в `bot/` и `scripts/`;
- добавлены 15 параметризованных behavioral contracts для порядка intent
  extractors и ветвей presentation logic;
- публичные обработчики и формат вывода сохранены, миграций БД в релизе нет.

## Test, CI и release evidence

- canonical local PostgreSQL gate: `482 passed, 1 skipped`, coverage `73.14%`;
- Ruff, mypy (114 source files), Alembic/schema drift, secret scan,
  documentation/version contracts и zero-exception complexity ratchet: PASS;
- PR CI run
  [33836515849](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33836515849):
  все пять jobs PASS;
- post-merge CI run
  [33836626839](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33836626839):
  все пять jobs PASS на exact release SHA;
- release evidence run
  [33836735878](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33836735878):
  PASS;
- immutable GitHub Release:
  <https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.5.0>;
- release assets: image tar, CycloneDX SBOM, SHA256SUMS и attestation;
  Release API возвращает `immutable=true`, `draft=false`.

## Backup и production evidence

Pre-deploy backup
`notebook_bot_2026-09-04_072842.sql.gz` имеет размер 1 248 299 bytes и SHA-256
`21e39b4b83f8469023694f1ae6bb02385c39b2b3fa0b1c959603780fa9c3be95`.
`gzip -t` и checksum verification прошли. Recovery LaunchAgent восстановил его
в одноразовую БД: `status=ok`, 20 public tables, 124 tasks, 2 users,
41 delivery batches, migration `a6c9d1e4f7b2`, RTO 0.4 seconds.

Targeted production gate на exact deployed SHA подтвердил создание и завершение
задачи как `done/completed`, создание reminder, ровно одну memoir и diary
запись, очистку active memoir state и всего marker-scoped test data.

Полный live E2E завершился `85/85 PASS` за 807 секунд. State oracle — `ok=true`,
cleanup residual — `{}`, production SHA не менялся во время прогона. Evidence:
`tested_sha=27ce9e0620a18b00e199584cf013351bb9b8040b`,
`started_at=2026-09-04T05:32:51Z`, `finished_at=2026-09-04T05:46:43Z`.
Runner report находится в
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260904_084640.md`.

## Оставшийся scope

- независимый approval остаётся только организационным backlog issue #6: в
  owner-only repository автор не может одобрить собственный PR;
- legal review неприменим к подтверждённому персональному deployment без
  внешних клиентов;
- production blockers после `v0.5.0` не известны.
