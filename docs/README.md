# DailyPlanner documentation

Актуальная документация продукта:

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — компоненты и контракты;
- [`OPERATIONS.md`](OPERATIONS.md) — эксплуатация, release gate и recovery;
- [`PRIVACY.md`](PRIVACY.md) — хранение, экспорт и удаление данных;
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — границы доверия, угрозы, действующие защиты
  и порядок ротации credentials;
- [`MIGRATION_ROLLBACK.md`](MIGRATION_ROLLBACK.md) — исполняемая проверка старого
  runtime и восстановления снимка; условия maintenance-перехода новой схемы;
- [`SESSION_CONTEXT_2026-09-05.md`](SESSION_CONTEXT_2026-09-05.md) — текущий
  remediation checkpoint: реализованные изменения, локальные проверки и оставшаяся работа;
- [`ADR_2026-09-04_RETRY_AND_DELIVERY.md`](ADR_2026-09-04_RETRY_AND_DELIVERY.md)
  — реализуемые контракты доставки и повторов, ограничения и границы rollback;
- [`REMEDIATION_PLAN_2026-09-04.md`](REMEDIATION_PLAN_2026-09-04.md)
  — рабочий план исполнения комплексного ревью: этапы, зависимости, проверки,
  выпуск и таблица закрытия R01–R16;
- [`REVIEW_2026-09-04_COMPREHENSIVE_INDEPENDENT.md`](REVIEW_2026-09-04_COMPREHENSIVE_INDEPENDENT.md)
  — актуальное комплексное ревью `v0.5.0`: архитектура, корректность, UX,
  безопасность, тестирование, выпуск и документация; рекомендации и критерии приёмки;
- [`REVIEW_2026-09-02_V032_INDEPENDENT.md`](REVIEW_2026-09-02_V032_INDEPENDENT.md)
  — историческая независимая перепроверка `v0.3.2` и условия приёмки того релиза;
- [`REVIEW_2026-09-02_INDEPENDENT.md`](REVIEW_2026-09-02_INDEPENDENT.md) —
  предыдущее повторное приёмочное ревью и backlog до образцового релиза;
- [`REVIEW_2026-09-02_INDEPENDENT_REMEDIATION.md`](REVIEW_2026-09-02_INDEPENDENT_REMEDIATION.md)
  — выполненные исправления, acceptance evidence и явно внешние gates;
- [`REVIEW_2026-09-02_FINAL_VERIFICATION.md`](REVIEW_2026-09-02_FINAL_VERIFICATION.md)
  — независимая перепроверка remediation и оставшиеся условия финальной
  приёмки;
- [`REVIEW_2026-09-02_FINAL_REMEDIATION.md`](REVIEW_2026-09-02_FINAL_REMEDIATION.md)
  — исправления финальной перепроверки, исполняемый rollback drill и внешние
  gates;
- [`REVIEW_2026-08-29_INDEPENDENT.md`](REVIEW_2026-08-29_INDEPENDENT.md) —
  исходное независимое ревью предыдущего цикла;
- [`REVIEW_2026-08-29_INDEPENDENT_REMEDIATION.md`](REVIEW_2026-08-29_INDEPENDENT_REMEDIATION.md)
  — состояние закрытия замечаний и оставшиеся внешние gates;
- [`SESSION_CONTEXT_2026-09-03.md`](SESSION_CONTEXT_2026-09-03.md) — текущий
  предыдущий handoff релиза `v0.3.6`;
- [`SESSION_CONTEXT_2026-09-04.md`](SESSION_CONTEXT_2026-09-04.md) — текущий
  handoff релиза `v0.5.0`; milestones #4/#5, zero complexity debt, immutable
  release, backup/restore, deployed revision и полный live evidence;
- [`SESSION_CONTEXT_2026-09-02.md`](SESSION_CONTEXT_2026-09-02.md) — предыдущий
  handoff релизов `v0.3.1`–`v0.3.2`;
- [`SESSION_CONTEXT_2026-08-30.md`](SESSION_CONTEXT_2026-08-30.md) — предыдущий
  handoff цикла от 29 августа.

`archive/` содержит исходные постановки этапов, beta-отчёты, завершённые review,
remediation-планы и старые session handoff. Они сохранены для аудита и не
являются списком незавершённой работы.
