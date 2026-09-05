# Migration rollback: proof and maintenance gate

The remediation schema is **not code-rollback compatible with v0.5.0**.
Do not add `d9f2a4b6c803` to `rollback_compatible_heads.txt` or bypass preflight.
The macOS staged installer correctly refuses this transition before migrations.

## What is executable now

```bash
uv sync --frozen --group dev
.venv/bin/python scripts/run_migration_rollback_drill.py
```

Requirements: Docker, uv, the repository's pinned v0.5.0 commit in Git history.
No production database URL is accepted. The driver creates a private, randomly
named pgvector container with an ephemeral loopback port and synthetic credentials.
The old release is exported from commit
`27ce9e0620a18b00e199584cf013351bb9b8040b`, installs its own frozen dependencies
(including the STT extra), and uses its tracked example configuration. No model,
Telegram, launchctl, userbot session or production service is invoked.

The real PostgreSQL sequence is:

1. Apply the old release's migrations and run its own preflight.
2. Seed synthetic baseline records and create a custom-format `pg_dump` snapshot.
3. Apply candidate migrations and seed failed/leased reminders, a pending action
   journal and a post-snapshot canary record.
4. Require old preflight to reject the newer schema. Demonstrate that overriding
   the schema check is insufficient: the old sender query selects both a failed
   occurrence and one whose new lease is still active. No notification is sent.
5. Require downgrade with an unfinished plan to fail. Check that both schema head
   and consent state remain unchanged: the attempted downgrade is atomic.
6. Restore the snapshot into a **second database**, leaving candidate data intact.
   Run old preflight without compatibility overrides, old singleton/schema/pgvector
   smoke, baseline-data checks and a task completion/new-note write through old code.
7. Check that the failed candidate database still contains its post-snapshot canary.
   Remove the two disposable databases by removing their isolated container.

The driver emits JSON with release SHAs, migration heads, dependency-lock and
driver/probe hashes, snapshot checksum and timestamps. The reusable CI includes
this drill as `migration-rollback`; release evidence depends on that whole CI.
CI execution itself must still be confirmed for the eventual release SHA.

Local execution evidence (05.09.2026):
[machine-readable report](evidence/MIGRATION_ROLLBACK_2026-09-05.json).
It verifies previous runtime `27ce9e0` against candidate runtime `0676c18`,
with driver/probe hashes recorded separately. The temporary snapshot and databases
were deleted after successful checks; this report is not a retained production backup.

## What this does not prove

- It is not a zero-downtime/code-only rollback, nor a generic promise that an old
  runtime can read the newer schema.
- It does not test real Telegram polling, launchd switching, STT inference or an
  operator's production backup. It exercises the actual old source and dependencies
  against PostgreSQL, not a full production rehearsal.
- A snapshot predates later writes. The drill preserves candidate state separately;
  it does not silently reconcile or discard updates received after the snapshot.
- A rejected downgrade is not a recovery strategy. In particular, do not delete
  failed reminders or pending plans merely to make downgrade succeed.

## Production transition: still gated

The proof establishes a snapshot-restore recovery route. The current staged
installer deliberately does **not** automate this maintenance transition. Before
deploying this schema, implement/review a maintenance workflow with these gates:

1. Identify exact previous/candidate code, dependency and configuration revisions;
   pass release CI, validate artifacts, and arrange an explicit downtime window.
2. Stop all bot polling/scheduler writers and confirm they cannot auto-restart
   during backup/migration. Keep Telegram updates unconsumed during validation.
3. Create a verified pre-migration backup and test its restoration to a separate
   target. Persist a recovery manifest tying the snapshot to the old code/schema
   and to the traffic-freeze boundary; keep credentials out of that manifest.
4. Migrate and perform candidate readiness/domain checks **before** accepting
   user updates. A schema mismatch override is never a substitute for these checks.
5. If validation fails before admitting updates, restore the snapshot to a separate
   database, retain/quarantine the failed candidate database, point the old runtime
   at the restored target and verify it before allowing traffic.
6. If any post-snapshot user updates were accepted, stop automatic recovery and
   reconcile them explicitly. Returning to the snapshot alone would lose data.
7. Record snapshot checksums, exact runtime/config/schema identities, validation,
   final database target and cleanup policy before ending the maintenance window.

The next implementation step is this guarded maintenance workflow, not an
allowlist edit. Normal same-schema staged rollback remains covered separately by
`tests/test_macos_staged_deploy_contract.py`.
