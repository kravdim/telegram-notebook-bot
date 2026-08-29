# Контекст доводки DailyPlanner — 30.08.2026

## Текущее состояние

Независимое ревью от 29.08.2026 разобрано и его кодовая часть доведена локально.
Production не перезапускался и остаётся на ранее развёрнутом commit `13dca6b`;
текущие изменения ещё не имеют release SHA, CI run, live E2E или тега.

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

## Следующий release цикл

1. Зафиксировать изменения одним review-remediation commit и отправить в PR.
2. Дождаться required CI и включить/проверить protected-main ruleset.
3. На точном принятом SHA выполнить backup, deploy, live Telegram E2E,
   post-deploy SLO и recovery drill.
4. После acceptance согласовать новую версию, обновить handoff, создать
   annotated tag и проверить SBOM/checksums/provenance GitHub Release.
5. Legal review и native STT drill остаются отдельными внешними gates.
