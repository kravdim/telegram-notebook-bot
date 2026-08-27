# Remediation независимого ревью от 26 августа 2026

**Дата сверки:** 27 августа 2026 года
**Исходное ревью:** [`REVIEW_2026-08-26_INDEPENDENT.md`](REVIEW_2026-08-26_INDEPENDENT.md)
**Статус:** P1 закрыты реализацией; для окончательной повторной приёмки остаются
внешняя юридическая проверка privacy-текста и новый live/release evidence.

Документ описывает текущее рабочее дерево поверх ревизии, указанной в исходном
ревью. Он не заменяет commit, release tag или результаты GitHub Actions.

## Матрица замечаний

| ID | Статус | Что сделано / что ещё требуется |
|---|---|---|
| REV-001 | Закрыто | Глобальный private-chat middleware стоит перед allowlist; group, supergroup, channel, anonymous и callback без private source отклоняются. Добавлены негативные тесты. |
| REV-002 | Закрыто | Введены content-free logging helpers. В application logs и LLM metadata остаются только тип/код ошибки, размер и имена полей. Canary-тесты покрывают malformed JSON, validation/provider errors и выключенное хранение payload. Durable delivery errors также сохраняются без текста exception. |
| REV-003 | Закрыто | `/export` использует тот же inventory, что verified deletion. Все наборы выгружаются потоково через закрытый disk staging в versioned JSONL-архив с manifest и сверкой counts. Неизвестный год рождения представлен как `--MM-DD`; integration-тест доказывает полноту и tenant isolation. |
| REV-004 | Код закрыт, external gate | До первого cloud-вызова требуется versioned consent; есть `/privacy`, enable/disable, `/export` и `/delete_data`. Notice строит список активных провайдеров из config, отказ блокирует свободный текст, voice и cloud reindex. Перед выходом на новый рынок нужна отдельная юридическая проверка текста и сроков. |
| REV-005 | Закрыто | Ограничение истории перенесено внутрь context API и применяется при add/get, включая early returns и provider failures. Есть метрики и стресс-тест на 300 пар. |
| REV-006 | Реализовано, live gate ожидается | Добавлен read-only DB oracle с точными AND side effects, negative effects и доказательством нулевого cleanup. Runner различает response/state/cleanup. Нужен новый авторизованный live-прогон полного набора после deploy. |
| REV-007 | Закрыто решением scope | Неполный systemd target удалён. Единственный заявленный Linux deployment — hardened Docker Compose; README и operations приведены в соответствие. |
| REV-008 | Частично | В CI включён C90/PLR ratchet, coverage floor повышен с 45 до 46%; фактически получено 47,45%. Цель исходного ревью 70% overall / 85% critical ещё не достигнута и остаётся отдельным большим quality-этапом, а не маскируется исключениями. |
| REV-009 | Реализовано, release evidence ожидается | Actions и images закреплены SHA/digest, добавлены Dependabot, CycloneDX SBOM, Trivy и tag-triggered provenance attestation/checksums. Реальный tag и immutable artifact появятся только при авторизованном выпуске релиза. |
| REV-010 | Закрыто | Контейнер запускается UID/GID 10001, без capabilities, с read-only root, no-new-privileges, PID/CPU/RAM limits, tmpfs и log rotation. Hardened cold-start/healthcheck прошёл. |
| REV-011 | Закрыто | README синхронизирован с 15 tools и расписанием 11/13/15/17; добавлен regression-тест. Неиспользуемый APScheduler удалён из dependency/lock, docstring исправлен. |
| REV-012 | Закрыто | Неподдерживаемые attachments получают явный безопасный ответ; добавлен UX-тест. |
| REV-013 | Закрыто | Settings поддерживает произвольную валидную IANA timezone через FSM, как и onboarding. |
| REV-014 | Закрыто | Граница frog statistics строится в локальной timezone пользователя и переводится в UTC. Тесты покрывают Tokyo и DST New York. |
| REV-015 | Закрыто | Критичные числовые, date-order и work-day инварианты закреплены CHECK constraints в ORM и Alembic migration; реальная PostgreSQL integration-проверка отвергает нарушения. |
| REV-016 | Закрыто | Allowlist logging содержит count, но не Telegram IDs; сценарий включён в canary-тест. |
| REV-017 | Закрыто | Trip dates в companion runner генерируются относительно даты запуска в фиксированной timezone. |
| REV-018 | Закрыто | Voice/log artifacts создаются в run-specific каталогах 0700/0600 и удаляются в `finally`; failure bundle сохраняется только явным opt-in. |
| REV-019 | Закрыто | Добавлены readme, license, authors/maintainers, classifiers и project URLs. |
| REV-020 | Код закрыт, native drill ожидается | STT lifecycle имеет `close()`, выгружает model и закрывает generators; unit regression выполняет повторные циклы. Добавлен opt-in native test на 20 транскрипций и runbook, но тяжёлый прогон с реальной моделью ещё не выполнен. |

## Контрольные результаты 27 августа 2026

- Ruff: PASS.
- mypy: PASS, 103 production/ops файла.
- pytest без external services: 234 passed, 20 skipped.
- coverage: 47,45%, установленный floor 46% пройден.
- disposable PostgreSQL/pgvector: fresh Alembic upgrade PASS, schema drift отсутствует,
  19/19 integration tests PASS; контейнер после проверки удалён.
- Bandit medium/high: PASS.
- pip-audit: известных уязвимостей нет.
- tracked secret scan: PASS, 204 присутствующих файла.
- LLM contracts: parser 6/6, utterance 17/17, invalid tool rate 0.
- Python compile и shell syntax: PASS.
- hardened Docker cold-start: миграция, non-root UID 10001 и healthcheck PASS;
  тестовый compose с volumes удалён.

## Условия финальной повторной приёмки

1. Выполнить юридическую проверку privacy notice для целевого рынка.
2. После deploy прогнать полный live E2E и приложить отдельные response/state/cleanup
   результаты, включая нулевой остаток namespace.
3. Выполнить native STT resource drill на целевой машине.
4. При выпуске релиза создать tag; дождаться SBOM, Trivy, checksums и provenance
   attestation от release workflow.
5. Coverage 70%/85% считать отдельным обязательным quality milestone для заявления
   «образцовый», пока фактический результат остаётся 47,45%.
