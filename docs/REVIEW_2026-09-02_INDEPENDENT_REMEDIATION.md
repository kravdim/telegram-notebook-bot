# Remediation независимого ревью от 02.09.2026

## Закрыто в коде

- **REV-20260902-001:** task-list recognizer больше не отбрасывает дату,
  проект, командировку, категорию, приоритет или человека. Непредставимый
  qualifier приводит к честному уточнению до LLM и до чтения неверного scope;
  adversarial matrix покрывает одиночные и комбинированные варианты.
- **REV-20260902-002:** macOS deployment использует versioned release directory,
  deploy mutex, pre-switch config/DB/migration/Telegram/STT/plist checks,
  bounded release-SHA readiness, deploy report и автоматический rollback.
- **REV-20260902-004:** task creation и task-list recognition физически вынесены
  в provider-independent application modules. Для всего `bot.application`
  включён strict mypy ratchet, AST-тест запрещает обратные transport/persistence
  зависимости recognizer-модулей. Остальные исторические hotspots перечислены
  ниже как ограниченный дальнейший scope.
- **REV-20260902-005:** появились проверяемые `minimal` и `local` профили.
  Minimal явно отключает embeddings/STT и не требует локальных моделей; local
  сохраняет Ollama и Whisper с отдельным prefetch/warmup acceptance.
- **REV-20260902-006/007/008/009:** CI вызывает canonical local gate, имеет
  timeout/concurrency/ShellCheck/coverage artifacts, critical и central-risk
  coverage ratchets, исправленные CODEOWNERS и version consistency check.
- **REV-20260902-003 (repository part):** tag workflow создаёт долговременный
  GitHub Release с image, SBOM, checksums, generated notes и provenance
  attestation. Фактический tag/release/ruleset фиксируется в session evidence
  после production acceptance release SHA.

## Acceptance до tag

Release SHA должен последовательно получить: полный локальный PostgreSQL gate,
зелёный GitHub CI, свежий backup и restore drill, staged production deploy,
live Telegram E2E и post-deploy SLO snapshot. Только после этого создаётся
annotated `v0.3.0`; tag обязан указывать на тот же deployed SHA.

## Явно внешний или дальнейший scope

- Legal review privacy notice/consent/retention требует решения юриста и
  product owner; кодовая проверка не может выдать юридическое заключение.
- Native STT drill на 20 транскрипций выполняется на production-class Mac с
  непубличным аудиофайлом и прикладывается к release evidence.
- Полная замена `llm/dispatcher.py`, дальнейшее дробление `commands.py`/`main.py`
  и удаление оставшихся legacy complexity exceptions продолжаются отдельными
  milestone-изменениями: это не маскируется повышением глобальных лимитов.
- Mutation framework не добавляется как декоративный CI job; текущий релиз
  использует mutation-negative/property-style matrices распознавателей и
  per-surface coverage ratchets. Полноценный mutation budget требует отдельной
  калибровки времени и survivor baseline.
