# Changelog

## Unreleased

## 0.3.6 — 2026-09-03

- Corrected the targeted memoir live gate to assert the task domain's actual
  completion pair: `status=done` and `resolution=completed`.

## 0.3.5 — 2026-09-03

- Added a date-specific visible marker to each memoir prompt and persisted it
  as a strict cross-API Reply ownership fallback.
- Kept plain text and replies to any other prompt excluded from memoir capture.

## 0.3.4 — 2026-09-03

- Made explicit memoir Reply ownership robust across Telegram Bot API and
  MTProto message-ID spaces by verifying the prompt's unique callback token.
- Corrected the targeted production memoir gate to locate and Reply to the
  prompt using the test user's own Telegram message-ID space.

## 0.3.3 — 2026-09-03

- Restricted memoir capture to an explicit Telegram Reply to the persisted
  prompt, preventing pending memoir state from stealing tasks, reminders or
  completion commands.
- Made deterministic task creation fail closed for natural-language dates and
  conflicting date/priority qualifiers so the full parser retains ownership.
- Bound live E2E evidence to the exact deployed Git SHA before and after each
  run.
- Made rollback health checks execute from the restored release and introduced
  an explicit allowlist for newer database heads compatible with that release.
- Replaced the suppression-count complexity check with per-function numerical
  ceilings for complexity, branches, returns and statements.

## 0.3.2 — 2026-09-02

- Gave an active memoir prompt ownership of the next text before task shortcuts
  or LLM intent routing, including task-like reflections sent as Telegram
  replies.
- Accepted the next plain text as a memoir answer without requiring Telegram's
  explicit Reply action, while preserving unrelated reply threads.

## 0.3.1 — 2026-09-02

- Removed recognized date, urgency and explicit priority qualifiers from fast-path
  task titles while failing closed for unsupported qualifier variants.
- Replaced static rollback assertions with an executable eleven-case failure
  matrix and made every staged-deploy failure publish an atomic phase report.
- Moved Telegram/STT checks ahead of expand/contract migrations and hardened
  previous-LaunchAgent load and readiness failures during rollback.
- Extracted owned background-job lifecycle from the composition root, removed
  its complexity suppression and added a CI-enforced exception allowlist.
- Raised overall and startup coverage ratchets after adding behavioral tests.

## 0.3.0 — 2026-09-02

- Made task-list recognition fail closed when a date, project, trip, category,
  priority or person qualifier cannot be represented by the supported scope.
- Added versioned staged macOS deployment with credential/config/database/model
  prechecks, release-identity readiness, an exclusive deploy lock, reports and
  automatic rollback to the previous release.
- Added minimal and full-local runtime profiles, application-layer recognizer
  boundaries and strict typing across `bot.application`.
- Unified the documented local PostgreSQL gate with CI and added job timeouts,
  PR concurrency, ShellCheck, coverage artifacts and version consistency.
- Expanded CODEOWNERS over real security/deployment surfaces and made tag builds
  publish durable GitHub Releases with image, SBOM, checksums and provenance.
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
