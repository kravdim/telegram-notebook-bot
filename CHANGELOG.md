# Changelog

## Unreleased

- Made mutation responses fail closed unless a typed mutating command actually
  completed; added a dedicated typed clarification intent and deterministic
  routing for the failed live reminder phrase.
- Added typed message outcomes so handled provider failures preserve voice
  confirmation state and make the inbound request retryable.
- Reopened completed privacy-deletion journals after legal re-onboarding using
  operation generations and fresh verification counts.
- Kept memoir skip state and its retry button when persistent cleanup fails.

## 0.2.0 — 2026-08-26

- Removed provider-based context compression from the per-user critical path;
  conversation history now uses deterministic bounded trimming.
- Added one total LLM provider-chain deadline, disabled nested SDK retries and
  exposed end-to-end, user-lock and provider-attempt metrics.
- Bound voice and memoir callbacks to durable session tokens and Telegram
  message IDs; stale buttons fail closed.
- Made memoir and chronometry interaction completion transactional and retained
  retryable state around project/voice side effects.
- Fenced delivery error bookkeeping, added crash-resumable privacy deletion
  journaling and clarified backup checksum metadata semantics.
- Preserved typed `CommandResult` through the Telegram adapter and raised the
  measured coverage floor from 42% to 45%.

## 2026-08-25

- Made `/export` disk-backed, size-bounded and cleanup-safe.
- Added shared-client STT health and observed transcription latency to `/status`.
- Expanded fail-fast runtime validation for every provider and operational limit.
- Added schema-aware natural-language LLM contract evaluation and a mandatory
  isolated live Telegram release gate.
- Raised the measured coverage floor to 42% and archived completed review plans.

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
