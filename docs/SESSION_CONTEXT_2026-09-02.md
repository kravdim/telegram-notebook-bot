# Контекст доводки DailyPlanner — 02.09.2026

## Текущее состояние

Замечания из независимой финальной перепроверки закрыты релизом `v0.3.1`.
Release/tag/production revision совпадают:
`61d2dd457c067dbe36863845286a30359035f5db`. GitHub Release опубликован как
immutable, production LaunchAgent работает с versioned release directory,
heartbeat подтверждает точный release SHA.

Deploy report: `status=deployed`, `phase=complete`, previous
`070c9a732407d91d4f3a5ee61af6dae78e37dc0f`, rollback `not_required`.
Production process после переключения: PID `58957`, `runs=1`,
`last exit=(never exited)`.

## Что исправлено

- дата и приоритет больше не дублируются в полях задачи и её title;
- создание задачи вынесено из крупного dispatcher handler в typed application
  use case;
- startup/background lifecycle вынесен из `bot.main` в отдельный runtime
  module;
- complexity debt снижен до 16 нарушений в 9 allowlisted функциях; ratchet
  теперь исполняется локально и в CI;
- macOS installer пишет атомарный отчёт для каждой failure phase, выполняет
  migration непосредственно перед switch и сохраняет предыдущий release для
  rollback;
- rollback/install contract исполняется в 11 isolated failure-injection
  сценариях;
- общий coverage floor поднят до 71%, измеренное покрытие — 72,24%;
- future GitHub Releases публикуются draft-first и проверяются на
  `immutable=true` после публикации;
- проект формально зафиксирован как личный owner-only deployment без внешних
  клиентов; legal/product market review к текущему scope неприменим.

## Test, CI и release evidence

- canonical local PostgreSQL gate: `450 passed, 1 skipped`, coverage `72,24%`;
- Ruff, mypy (112 source files), docs/version/migration contracts и complexity
  ratchet: PASS;
- intentional macOS deploy failure drill: `11/11 PASS`;
- PR #8: <https://github.com/kravdim/telegram-notebook-bot/pull/8>;
- post-merge CI run `33653912307`: все пять required jobs PASS;
- release workflow run `33660146451`: PASS;
- immutable GitHub Release:
  <https://github.com/kravdim/telegram-notebook-bot/releases/tag/v0.3.1>;
- release assets: image tar, CycloneDX SBOM, SHA256SUMS и attestation; Release
  API возвращает `immutable=true`, `draft=false`;
- pre-deploy backup:
  `notebook_bot_2026-09-02_191842.sql.gz`, 1 231 142 bytes, SHA-256
  `ce1dfd3c9a216c19567167e7005363bbd560f117a596135c6aef513d99f11a95`;
- recovery LaunchAgent: 20 public tables, migration `a6c9d1e4f7b2`, users 2,
  tasks 122, delivery batches 34, RTO 0,54 s, status `ok`, exit 0;
- post-live SLO: reminders `ok`, lag 0 s, pending 0; backup `ok`, artifact
  `metadata-ok`.

Первый post-deploy live E2E run `DP-20260902T173418-e1ace5` завершился
`81/85`: во время длинного теста плановое memoir/chronometry-взаимодействие
корректно заняло единственный interaction slot, из-за чего voice-сценарии
получили fail-close отказ. Это была гонка тестового окружения по времени суток,
а не потеря production-данных; DB acceptance oracle и cleanup прошли.

Повторный run после очистки daily marker/state:
`DP-20260902T181030-4a57c7`, `85/85 PASS` за 795 s, voice Q1–Q4 PASS, state
oracle `12/12`, cleanup residual `{}`. Report:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260902_212350.md`.

## Repository governance

`main` защищена: PR required, admins included, strict up-to-date branch, пять
required checks, linear history, conversation resolution; force-push и deletion
запрещены.

В репозитории только один collaborator (`kravdim`). Поэтому approval и
CODEOWNERS review пока нельзя сделать обязательными: автор не может одобрить
собственный PR, и repository станет немержабельным. После приглашения второго
reviewer нужно включить `required_approving_review_count=1`,
`require_code_owner_reviews=true` и last-push approval, затем проверить это
тестовым PR ([issue #6](https://github.com/kravdim/telegram-notebook-bot/issues/6)).

## Оставшийся scope

- пригласить второго GitHub collaborator/reviewer и включить обязательное
  независимое approval;
- архитектурная декомпозиция продолжается в milestones
  [v0.4.0](https://github.com/kravdim/telegram-notebook-bot/issues/4) и
  [v0.5.0](https://github.com/kravdim/telegram-notebook-bot/issues/5); это
  контролируемый backlog, а не блокер production release `v0.3.1`.

Legal issue
[#7](https://github.com/kravdim/telegram-notebook-bot/issues/7) закрыт как
неприменимый: владелец подтвердил, что проект останется личным и внешних
клиентов не будет.
