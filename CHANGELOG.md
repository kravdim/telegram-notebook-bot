# Changelog

All notable production changes are recorded here. Historical beta and review
reports remain dated evidence under the repository root and `docs/`.

## 2026-08-24

- Unified recurring-task completion and reminder closure across every user path.
- Added durable multipart delivery, isolated userbot E2E and container readiness.
- Added CREATEDB-only weekly recovery drill with measured RTO evidence.
- Closed full-project Ruff and mypy debt; added coverage, Bandit and dependency
  audit CI gates.
- Added daily copy-truncate launchd log rotation with bounded retention.
- Added dry-run-first, confirmed and post-verified user-data deletion workflow.
- Fixed frog progress/streak/localization and labelled chronometry durations as
  estimates.
