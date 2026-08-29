# Remediation независимого ревью от 29.08.2026

**Состояние:** кодовая доводка завершена локально 30.08.2026; release evidence
ожидает отдельного commit/CI/deploy/tag цикла.

## Закрыто кодом и тестами

- **REV-001:** LaunchAgent больше не содержит обязательных localhost proxy.
  Direct network — профиль по умолчанию; `--http-proxy` и `--all-proxy`
  включаются явно, валидируются и проходят Telegram preflight до restart.
- **REV-002:** `scripts/bootstrap_dev.sh` поднимает pinned pgvector PostgreSQL,
  создаёт согласованные role/database, применяет миграции и preflight. CI
  воспроизводит этот путь через `--smoke`.
- **REV-004:** центральный message handler разделён на deterministic workflows,
  persisted replies, LLM request/mutation guard, metadata и presentation.
  Глобальный ratchet снижен с `60/70/25/300` до conventional
  `15/20/12/80`; 11 старых hotspots имеют точечный именованный
  `REVIEW-20260829 legacy ratchet`, а не широкое исключение по файлу. Strict
  mypy включён для нового application recognizer. Оставшийся dispatcher debt
  явно ограничен и требует следующего самостоятельного этапа.
- **REV-005:** task-list recognizer вынесен в application-модуль; contract
  matrix покрывает `today`, `all`, `overdue`, `done_today`, морфологию,
  пунктуацию и mutation-negative примеры.
- **REV-006:** weekend policy вычисляет empty-state из отображаемых секций;
  добавлена полная матрица задач, frog/projects, trip и birthdays.
- **REV-007:** `scripts/run_local_test_gate.sh` владеет disposable PostgreSQL,
  миграциями, DB tests, overall/critical coverage и cleanup.
- **REV-008:** исторические reviews/sessions перенесены в `docs/archive/`, а CI
  проверяет локальные ссылки активной документации.
- **REV-009 (репозиторная часть):** добавлены CODEOWNERS и PR evidence template.
  GitHub ruleset/branch protection настраивается отдельно владельцем репозитория.
- **REV-010:** behavioral matrices подняли локальный результат до 71,08% без
  механического ослабления общего или critical gate.

## Локальное evidence

- developer bootstrap smoke: PASS на чистом disposable pgvector volume;
- canonical local gate: `401 passed, 1 skipped`, overall coverage `71,08%`;
- critical coverage: `85–100%`;
- Ruff и mypy: PASS;
- documentation contract: PASS;
- LaunchAgent renderer: 6 profile/security tests PASS.

## Не закрыто этим изменением

- **REV-003:** version bump, annotated tag, GitHub Release и provenance нельзя
  считать закрытыми до зелёного CI и acceptance на одном зафиксированном SHA.
- GitHub branch ruleset требует внешнего изменения настроек репозитория.
- Legal review, native STT drill, live Telegram E2E, recovery drill и
  post-deploy SLO остаются внешними release gates.

Source HEAD и production revision нельзя объявлять одной версией до завершения
этого release цикла.
