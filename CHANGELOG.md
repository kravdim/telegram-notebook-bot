# Changelog

## Unreleased

- Made the macOS proxy profile explicit and validated, with direct networking
  as the portable default and secret-free LaunchAgent rendering tests.
- Added reproducible Docker-backed developer bootstrap and local PostgreSQL
  quality gates, and made CI smoke-test the documented clean-host path.
- Added deterministic task-list scope contracts for today, all, overdue and
  completed-today requests, including morphology and negative examples.
- Made weekend digest empty states depend only on visible sections and covered
  personal, work, overdue, frog, project, trip and birthday combinations.
- Split the central message pipeline into routing, LLM request, mutation guard,
  persistence and presentation phases; tightened complexity and typed-module
  ratchets.
- Archived historical review/session evidence, added an active-document link
  contract, CODEOWNERS and a release-evidence pull-request checklist.
- Enforced a global private-chat transport boundary and content-free exception
  logging, including whitelist-log canaries.
- Added a versioned, count-verified full JSONL export backed by the deletion
  inventory, plus user-facing privacy disclosure and explicit cloud-AI choice.
- Made conversation history bounded at its API boundary and upgraded the live
  Telegram gate with an independent PostgreSQL state and cleanup oracle.
- Removed the incomplete systemd target; hardened the supported Docker target
  as non-root/read-only with pinned images, limits, SBOM, vulnerability scan,
  Dependabot and tag-triggered provenance evidence.
- Added critical PostgreSQL domain constraints, arbitrary IANA timezone
  settings, timezone-correct frog statistics and explicit attachment feedback.
- Added explicit local-STT model teardown and run-scoped voice/log artifact
  cleanup; raised the coverage floor and introduced complexity ratchets.
- Made mutation responses fail closed unless a typed mutating command actually
  completed; added a dedicated typed clarification intent and deterministic
  routing for the failed live reminder phrase.
- Added typed message outcomes so handled provider failures preserve voice
  confirmation state and make the inbound request retryable.
- Reopened completed privacy-deletion journals after legal re-onboarding using
  operation generations and fresh verification counts.
- Kept memoir skip state and its retry button when persistent cleanup fails.
- Closed partial-startup resources when Telegram command registration fails and
  hardened the live DB oracle against provider title normalization.
- Added deterministic handling for noisy evening-task requests and made the
  voice acceptance fixture wait for a self-contained terminal mutation result.
- Refreshed pinned Python/uv container layers and explicitly upgraded OpenSSL
  security packages after new HIGH advisories reached the CI vulnerability DB.
- Raised the measured overall coverage gate to 70% and added independent 85%
  critical-path gates for access, privacy, export, delivery and reminders.

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
