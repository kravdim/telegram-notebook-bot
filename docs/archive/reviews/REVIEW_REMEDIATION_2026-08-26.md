# Remediation — review 2026-08-26

Дата: 26 августа 2026 года

## P1

1. Provider-based history compression удалена из request pipeline. История
   ограничивается детерминированным recent-window; SDK retries отключены,
   main/retry/fallback ограничены одним total deadline. Добавлены метрики
   end-to-end latency, ожидания user lock, trimming и provider attempts.
2. Voice и memoir callback payload содержат session token. PostgreSQL payload
   хранит тот же token и Telegram message ID; stale confirm/edit/cancel/skip
   fail closed и не очищает новую сессию. Process-local структуры используются
   только как cache после проверки БД.
3. Project completion больше не consume-ит state до dispatch и использует
   `pending → processing → failed/completed`. Voice confirm использует CAS
   `voice_confirm → voice_processing`, восстанавливая retry на исключении.
   Memoir и chronometry выполняют domain writes и state deletion в одной
   транзакции.

## P2

4. Typed `CommandResult` доходит до Telegram adapter; handler больше не
   декодирует sentinel strings. Исполнители старых use cases пока остаются в
   `llm/dispatcher.py` как честно документированный compatibility layer.
5. Coverage floor поднят с 42% до 45%; добавлены targeted stale-callback,
   total-deadline, deterministic-context и PostgreSQL fencing/CAS tests.
6. Privacy deletion использует durable operation journal в
   `operational_state` и идемпотентно возобновляет фазы после process crash.
7. Частый backup health check сверяет marker, размер, recorded digest и
   sidecar как metadata; полная SHA-256 проверка явно закреплена за recovery
   drill.
8. Exception update `DeliveryPart` fenced текущим batch lease token и pending
   status; stale worker не может затереть прогресс нового владельца.
9. Time entry, `chronometry_last_asked` и interaction completion получили одну
   transaction boundary; fallback больше не дублирует уже committed entry.
10. Версия поднята до 0.2.0, CHANGELOG и фактические architecture/operations/
    privacy boundaries обновлены. Release tag создаётся только после зелёного
    CI, production deploy и единого live gate.

## Release evidence до тега

- локальные unit/scenario tests и coverage gate;
- PostgreSQL suite в disposable CI database;
- Ruff, mypy, Bandit, secret scan, LLM contracts и Alembic check;
- единый live Telegram 82/82 с forced provider timeout;
- production backup/recovery drill и post-deploy SLO check.
