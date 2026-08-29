# Контекст доводки DailyPlanner — 30.08.2026

## Текущее состояние

Независимое ревью от 29.08.2026 разобрано, remediation закоммичена, отправлена
в `origin/main` и развёрнута в production. Application release SHA —
`e373b8b`; основной remediation commit — `5c92b2d`, clean-host bootstrap fix —
`1444b2a`, UTC integration-test fix — `e373b8b`.

GitHub Actions run
[`33276340956`](https://github.com/kravdim/telegram-notebook-bot/actions/runs/33276340956)
полностью зелёный на `e373b8b`: quality, secrets, developer-bootstrap и
container-e2e — PASS. Предыдущие два run выявили отсутствующий clean-host
`config.yaml` и полуночный UTC/Moscow test flake; оба дефекта исправлены до
deploy, а не обойдены rerun-ом.

## Что изменено

- direct-network macOS LaunchAgent с явным проверяемым proxy-профилем;
- Docker-backed clean developer bootstrap и единый local PostgreSQL gate;
- task-list scope recognizer и weekend digest policy с contract matrices;
- фазовый message pipeline вместо одной функции complexity 60;
- более строгие Ruff/mypy ratchets;
- архив исторической документации и active-link CI contract;
- CODEOWNERS, PR evidence checklist и синхронизированный CHANGELOG.

## Проверки

- `scripts/bootstrap_dev.sh --smoke`: PASS;
- `scripts/run_local_test_gate.sh`: `401 passed, 1 skipped`, coverage `71,08%`;
- critical coverage: `85–100%`;
- Ruff, mypy, shell syntax, plist validation и documentation contract: PASS.

## Backup, deploy и production acceptance

Перед deploy создан backup
`/Users/moltbot/backups/notebook-bot/notebook_bot_2026-08-30_003420.sql.gz`:
1 208 674 байта, gzip integrity PASS, SHA-256
`1b192ba97ed4c320bdaaf371066a55ec07472a8eaa07e4212cc3ca46c922b1cc`;
sidecar создан.

`platform/macos/install.sh` развернул `e373b8b` с явным proxy-профилем текущего
хоста; оба proxy endpoint прошли preflight до restart. LaunchAgent
`com.notebook-bot` после deploy: `running`, PID `53384`, `runs=1`,
`last exit=(never exited)`. Активен один процесс DailyPlanner `python -m
bot.main`; singleton lease получен, polling для `@daily76planner_bot` запущен,
Whisper `medium` прогрет. Preflight и Alembic подтверждают
`a6c9d1e4f7b2 (head)`.

Post-deploy SLO snapshot:

- reminders: `ok`, lag 0 секунд, pending 0;
- backup: `ok`, age 0,1 часа, artifact `metadata-ok`.

Recovery LaunchAgent завершил drill с exit 0: свежий backup восстановлен,
20 таблиц, migration `a6c9d1e4f7b2`, users 2, tasks 119, RTO 0,35 секунды.

Production live Telegram gate `DP-20260829T214247-6a37f2` прошёл `82/82 PASS`
за 780 секунд. PostgreSQL state oracle — `12/12`, teardown и cleanup oracle
подтвердили нулевой остаток при сохранённой регистрации E2E-пользователя.
Отчёт:
`/Users/moltbot/Projects/userbot/tests_dailyplanner/results/report_20260830_005553.md`.

## Оставшиеся release gates

- Включить и проверить protected-main GitHub ruleset; CODEOWNERS и PR template
  уже находятся в репозитории.
- Согласовать новую версию, создать annotated tag и проверить
  SBOM/checksums/provenance GitHub Release.
- Legal review и отдельный native STT resource drill на 20 транскрипций остаются
  внешними gates; voice acceptance в live Telegram gate прошёл `4/4`.
