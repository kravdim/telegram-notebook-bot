# Контекст доводки DailyPlanner — 03.09.2026

## Текущее состояние

Замечания из `REVIEW_2026-09-02_V032_INDEPENDENT.md` закрыты в production.
Финальный релиз — `v0.3.6`; release tag и production указывают на один code
SHA: `365d0a7e8c8754c306b10b4f97411219e04689ea`. После релиза в `main` добавлен
только этот documentation handoff; runtime tree релиза не менялся.

Production LaunchAgent работает из versioned release directory. Финальный
deploy report: `status=deployed`, `phase=complete`, `reason=none`; previous
release — `bf71268aa38e2b1f98d957d9e5b81a351f1334d3`. После переключения heartbeat
подтвердил exact release SHA; процесс запущен один раз и не завершался.

## Что исправлено по ревью

- мемуарник принимает только явный Telegram Reply на активный prompt;
- обычные сообщения, задачи, завершения задач и напоминания больше не
  перехватываются часовым memoir-state;
- prompt мемуарника содержит уникальную метку даты, сохранённую в persistent
  state: она строго связывает Reply с текущим prompt даже при разных
  message-ID spaces у Telegram Bot API и MTProto;
- fast-path создания задач fail-closed для естественных дат и конфликтующих
  дат/приоритетов, передавая такие фразы полному parser;
- rollback исполняет preflight из восстановленного release и допускает более
  новую схему только по явному compatibility manifest;
- deploy failure harness проверяет реальные failure-injection сценарии;
- complexity ratchet теперь хранит и проверяет числовые пределы complexity,
  branches, returns и statements для legacy-функций;
- live gate отказывается работать не на deployed SHA, отдельно проверяет
  memoir/task/reminder flow и затем запускает полный human-language E2E;
- PostgreSQL integration test проверяет restart persistence, TTL, concurrent
  single-consumer semantics и атомарные memoir/diary side effects.

Промежуточные immutable releases `v0.3.4` и `v0.3.5` сохранили найденные
этапы диагностики. `v0.3.4` показал, что inline keyboard не возвращается в
`reply_to_message`; `v0.3.5` доказал исправление product flow, после чего live
oracle был скорректирован с ошибочного `status=completed` на фактическую пару
`status=done`, `resolution=completed`. Финальная приёмка выполнена на
`v0.3.6`; опубликованные теги не изменялись задним числом.

## Test, CI и release evidence

- canonical local PostgreSQL gate: `467 passed, 1 skipped`, coverage `72.36%`;
- Ruff, mypy (114 source files), Alembic/schema drift, secret scan,
  documentation/version contracts и numerical complexity ratchet: PASS;
- remediation PRs:
  [#13](https://github.com/kravdim/telegram-notebook-bot/pull/13),
  [#14](https://github.com/kravdim/telegram-notebook-bot/pull/14),
  [#16](https://github.com/kravdim/telegram-notebook-bot/pull/16),
  [#17](https://github.com/kravdim/telegram-notebook-bot/pull/17);
- финальный post-merge CI run
  [33794517754](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33794517754):
  все пять jobs PASS на exact release SHA;
- release workflow
  [33795495401](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33795495401):
  PASS;
- immutable GitHub Release:
  <https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.6>;
- release assets: image tar, CycloneDX SBOM, SHA256SUMS и attestation;
  Release API возвращает `immutable=true`, `draft=false`;
- финальный pre-deploy backup:
  `notebook_bot_2026-09-03_221928.sql.gz`, 1 249 655 bytes, SHA-256
  `61d6ead575ea25c04e6921d9f485c69730ba4e77171a8d51c39b4d24bc31c76e`;
  `gzip -t` и checksum verification прошли.

## Production live evidence

Targeted memoir gate на exact deployed SHA прошёл дважды. Финальный run
`DP-20260903T222325-dbf7c2` подтвердил:

- обычная задача создана и завершена как `done/completed`;
- обычное напоминание создано;
- точный Reply записан ровно в один memoir и один diary entry;
- active memoir state очищен;
- cleanup удалил весь marker-scoped test data.

Полный live E2E завершился `85/85 PASS` за 807 секунд. State oracle — `ok=true`,
cleanup residual — `{}`, production SHA не менялся во время прогона. Evidence:
`tested_sha=365d0a7e8c8754c306b10b4f97411219e04689ea`,
`started_at=2026-09-03T19:23:23Z`, `finished_at=2026-09-03T19:37:12Z`.
Runner report:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260903_223710.md`.

## Оставшийся scope

- независимый GitHub approval остаётся организационным backlog: в repository
  один collaborator, и автор не может одобрить собственный PR;
- дальнейшая декомпозиция legacy-модулей ведётся в milestones `v0.4.0` и
  `v0.5.0`; рост всех зафиксированных complexity-метрик теперь блокируется CI;
- legal review неприменим к подтверждённому owner-only personal deployment без
  внешних клиентов.

Эти пункты не являются production blockers релиза `v0.3.6`.
