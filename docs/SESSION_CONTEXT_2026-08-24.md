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

## Проверка последнего этапа

- `pytest`: 142 passed, 9 skipped;
- strict operational Ruff и mypy: успешно;
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

## Следующий архитектурный этап

Следующий незакрытый пункт review checklist — полная выплата Ruff/mypy debt с
расширением CI gate на весь поддерживаемый код.

## Фиксация и deployment

- Recovery implementation зафиксирован commit `530015f` (`Add least-privilege
  recovery drill`) и отправлен в `origin/main`.
- GitHub Actions CI run `32764429844` завершился успешно.
- Установленный plist совпадает с шаблоном из commit; production LaunchAgent
  указывает на текущий checkout и зарегистрирован в GUI domain 501.
- Post-deploy запуск через launchd стал вторым успешным drill: exit code `0`,
  RTO 0,22 секунды, stderr пуст. Временных drill-БД не осталось; recovery role
  сохранила только `CREATEDB`, основной application preflight прошёл.
