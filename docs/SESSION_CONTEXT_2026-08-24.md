# Контекст сессии — архитектурные этапы 24.08.2026

## Отправная точка

В работу взяты рекомендации [`../REVIEW_2026-08-24.md`](../REVIEW_2026-08-24.md)
после исправлений по двум живым beta-тестам. Актуальный checklist находится в
[`REVIEW_REMEDIATION_2026-08-24.md`](REVIEW_REMEDIATION_2026-08-24.md).

## Завершённые этапы

1. Review remediation: persisted memoir interaction, единый task-completion
   workflow, recurrence/row-lock guarantees, Telegram chunking, LLM timeouts,
   cloud adapter contracts, retention и onboarding validation.
2. Durable multipart delivery outbox: PostgreSQL batch/part ledger, DB lease,
   partial resume и документированная at-least-once семантика.
3. Изоляция userbot E2E: выделенный account `8514454144`, уникальные run IDs,
   allowlist-guarded pre-cleanup и mandatory teardown без удаления регистрации.
4. Container E2E/readiness: cloud-first образ без local Whisper, обязательный
   mounted config, чистый PostgreSQL 16 + migrations/extensions/schema/pgvector
   smoke, event-loop heartbeat и DB/Alembic readiness probe, отдельный CI job.
5. Recovery operator: application role остаётся без `CREATEDB`; отдельная
   Keychain-backed role клонирует закрытую pgvector template, восстанавливает
   backup в случайную БД, пишет SHA-256/row counts/RTO evidence и удаляет drill
   DB. Weekly LaunchAgent запускается по воскресеньям после ночного backup.
6. Engineering quality: полный Ruff и mypy debt закрыт без выборочного
   исключения модулей; CI проверяет весь проект, coverage floor, Bandit и
   frozen production dependency audit.
7. Operations/privacy: отдельный ежедневный LaunchAgent делает bounded
   copy-truncate ротацию четырёх точных log-файлов; deletion workflow работает
   dry-run-first, требует target-bound confirmation и проверяет нулевые остатки
   во всех user-owned таблицах до commit.
8. UX polish: нулевой frog progress больше не показывает заполненный блок,
   trend локализован, streak вычисляется по фактическим датам, а хронометраж
   честно подписан как интервальная оценка.

## Проверка последнего этапа

- `pytest`: 152 passed, 10 skipped, coverage 42,31% при floor 40%;
- полный Ruff и mypy по 92 source-файлам: успешно;
- Bandit medium/high gate, secret scan и dependency audit: успешно, известных
  уязвимостей нет;
- PostgreSQL integration suite: 10 passed, включая verified user deletion;
- Compose config и CI YAML: валидны;
- disposable Compose project собран с нуля и достиг `healthy`;
- migration `f4b8c2d6e1a0`, `vector`/`pg_trgm`/`pgcrypto`, ORM lifecycle и
  768-dimensional vector roundtrip проверены;
- smoke-user после проверки отсутствует в БД;
- остановленный Python event loop дал ожидаемый stale-heartbeat failure, после
  `SIGCONT` readiness восстановился;
- disposable containers, network и volumes удалены; остальные Colima workloads
  оставались запущены и healthy там, где у них определён healthcheck.

## Production и границы изменений

Primary production остаётся macOS LaunchAgent `com.notebook-bot`; recovery
добавлен отдельным LaunchAgent `com.notebook-bot-recovery-drill` с расписанием
Sunday 04:30 и не потребовал restart основного сервиса. Первый запуск через
launchd успешно восстановил backup `notebook_bot_2026-08-24_162501.sql.gz`:
20 public tables, migration `f4b8c2d6e1a0`, 2 users, 97 tasks, RTO 0,62 секунды.
JSONL evidence записан, stderr пуст, временных drill-БД не осталось, application
preflight прошёл. Docker/VPS теперь является проверяемым cloud-adapter target,
но hermetic CI smoke не заменяет release-проверку реального Telegram `/status`
и одной read-only команды с настоящими provider credentials.

Перед текущим deploy создан и проверен backup
`notebook_bot_2026-08-24_230526.sql.gz` с SHA-256 sidecar. LaunchAgent
`com.notebook-bot-log-maintenance` установлен на 02:30 и первый раз завершился
с exit 0: активный `stdout.log` copy-truncate ротирован, а 157-МиБ артефакт
сохранён как `stdout.log.1`. Основной LaunchAgent после финальной проверки
получил PID `21485`, singleton lease, migration `f4b8c2d6e1a0`, запустил
Telegram polling и прогрел local Whisper; Ollama health виден в startup logs.

## Текущий статус review

Все инженерные и эксплуатационные пункты `REVIEW_2026-08-24.md`, выбранные как
полезные и входящие в поддерживаемые продуктовые контракты, реализованы. Для
финального release evidence остаётся commit/push и зелёный GitHub Actions run.

## Фиксация и deployment

- Recovery implementation зафиксирован commit `530015f` (`Add least-privilege
  recovery drill`) и отправлен в `origin/main`.
- GitHub Actions CI run `32764429844` завершился успешно.
- Установленный plist совпадает с шаблоном из commit; production LaunchAgent
  указывает на текущий checkout и зарегистрирован в GUI domain 501.
- Post-deploy запуск через launchd стал вторым успешным drill: exit code `0`,
  RTO 0,22 секунды, stderr пуст. Временных drill-БД не осталось; recovery role
  сохранила только `CREATEDB`, основной application preflight прошёл.

## Финальный review-remediation release

- Основная реализация текущего этапа зафиксирована commit `93b2002`
  (`Complete production review remediation`): full-project quality gates,
  log-maintenance LaunchAgent, verified privacy deletion, UX polish и
  актуальная документация.
- Перед deploy сохранён проверенный backup
  `notebook_bot_2026-08-24_230526.sql.gz` с корректными gzip и SHA-256.
- Production уже исполняет содержимое commit `93b2002`: основной LaunchAgent
  работает как PID `21485`, singleton lease и Telegram polling активны,
  migration остаётся `f4b8c2d6e1a0`, local Whisper прогрет.
- Context commit `b7bbc94` и implementation commit `93b2002` отправлены в
  `origin/main`. GitHub Actions CI run `32773353999` завершился успешно:
  `container-e2e`, `quality` и `secrets` зелёные; quality включал полный
  Ruff/mypy, Bandit, dependency audit, coverage, disposable PostgreSQL,
  backup/restore и secret scan.
