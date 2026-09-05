# DailyPlanner threat model

Scope: personal owner-operated deployment with a closed Telegram allowlist.
This describes the remediation branch; production v0.5.0 has not received the
new fingerprint/lease/action-journal controls yet. Synthetic data only in public
examples and release evidence.

## Assets and trust boundaries

Protected assets are user text/audio, derived embeddings, database/backups,
Telegram sessions and bot tokens, AI credentials, and release/deployment authority.
An authenticated Telegram sender crosses the allowlist boundary before handlers.
Model output crosses a separate, untrusted command boundary: text is not proof of
authorization or a completed write. PostgreSQL owns domain and recovery state;
process caches and Telegram response delivery are not sources of truth.
AI providers and Telegram receive only the data required by an enabled workflow,
but data already transmitted to them cannot be recalled by a database rollback.

| Threat | Implemented controls | Remaining limits / verification |
| --- | --- | --- |
| Forged callback or another user's identifier | Sender-scoped lookups, workflow tokens, owner-scoped retry plans | Keep negative ownership tests for each new channel; UUID secrecy is not authorization |
| Model instruction injection or false success | Typed commands, field validation, mutation-required guard, persisted CommandResult | Recognition can still be wrong; destructive operations need explicit confirmation |
| Provider changes reuse old consent | Recipient fingerprint, notice-bound enable buttons, text/voice gates, per-record cloud reindex check | In-flight network requests cannot be undone; existing users must confirm once after migration |
| Duplicate or partially completed request | Plan/result journal and domain effect in one transaction; explicit `/retry` | Telegram may repeat a response; arbitrary new text is a new request, not a retry |
| Concurrent delivery, reschedule or failure | Durable reminder claims, token revocation on reschedule, failed/backoff states | Telegram has no exactly-once send key; send-versus-cancel has an in-flight window |
| User content leaks through logs, demo or export | Content-free operational errors, synthetic fixtures, bounded private temporary exports | Export files downloaded by the owner and old backups remain sensitive |
| Compromised dependency or release | Frozen dependencies, cloud/STT audit jobs, reusable release CI, checksums/provenance, pinned live runner | Real CI/assets and STT-specific SBOM still need release acceptance; local green tests are insufficient |
| Broken migration or operator action | Disposable DB gates, least-privilege recovery workflow, guarded downgrade | Backward compatibility of new reminder semantics with old runtime is not yet proven; deploy remains gated |

## Credential rotation and incident response

1. Identify the exact credential and issuer without copying its value into a
   ticket, shell command, chat, screenshot or log. Stop the affected application
   using the deployment-specific procedure in [OPERATIONS](OPERATIONS.md).
2. Revoke/rotate a compromised bot token or API key at its issuer. For a Telegram
   userbot session, revoke the affected active session and authenticate again;
   rotating the bot token does not revoke a separate userbot session.
3. Update the secret through the deployment's protected secret file/environment,
   not tracked source. Inspect access permissions and remove stale secret copies
   using the operator's recovery procedure. Never print the whole environment.
4. Verify the old credential is revoked, restart the intended runtime, then run
   the documented credential/readiness checks. Use synthetic requests for checks
   that send data; health alone does not prove the replacement credential works.
5. Review metadata for the affected time window and record impact, actions and
   verified runtime SHA without payloads/secrets. If repository/history or release
   credentials were involved, treat publication authority separately from app
   credentials and verify the release artifacts again.

Changing only an API secret does not alter recipient consent. Changing the
provider or endpoint does: do not backfill fingerprints to avoid asking the owner.
Database restoration does not undo data previously sent to a provider. Data
deletion and backup retention are described in [PRIVACY](PRIVACY.md).

## Evidence locations

- `tests/test_provider_consent.py`: changed recipients, stale enable buttons,
  onboarding notice binding and text/voice rejection before processing.
- `tests/integration/test_provider_consent_egress.py`: revocation during a real
  PostgreSQL-backed reindex batch stops remaining records.
- `tests/integration/test_action_journal.py`: concurrent retry, rollback,
  lost commit acknowledgement and cross-task transaction guard.
- `tests/integration/test_task_lifecycle_boundaries.py`: deadline clear/reschedule,
  revoked sender acknowledgement and protected recurring reopen.

Outstanding release/UX/architecture work stays in the
[remediation plan](REMEDIATION_PLAN_2026-09-04.md), not hidden behind this document.
