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
deploying this schema, enforce/review a maintenance workflow with these gates:

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

## Maintenance implementation checkpoint (not deploy-ready)

`bot/operations/maintenance.py` now contains the orchestration state machine and
an atomic, fsynced, mode-0600 journal. Its state directory must be the installer's
state directory: both workflows use `deploy.lock`. Existing locks are never
reclaimed automatically, including after a crash.

The journal revokes snapshot rollback **before** calling runtime activation.
An uncertain journal-write acknowledgement reloads disk state; it cannot grant
rollback based on stale in-memory state. Once admission starts, even a failed
startup requires operator reconciliation rather than automatic snapshot restore.
Pre-admission recovery requires a verified checksum, unchanged identities, writer
freeze and a data guard, and restores into a separate database through the port.

`tests/test_maintenance_workflow.py` exercises these decisions using failure
injection and a real filesystem journal. These are orchestration tests, **not**
proof of launchd shutdown, PostgreSQL writer exclusion or actual restoration.
The executable composition and its separate acceptance limits are described below.

Remaining production acceptance obligations:

- Rehearse the composition below with the exact prepared old/candidate runtimes
  and confirm native launchctl responses on the target macOS version.
- Pass release CI/live/profile gates and obtain an explicit maintenance window.
- Keep all administrative writers and artifact/configuration editors stopped
  for the entire window; the runtime lease is not a universal DBA write fence.

### PostgreSQL component (implemented, not a production deployment)

`bot/operations/maintenance_postgres.py` creates a private custom-format dump.
The dump and logical data fingerprint use the **same exported MVCC snapshot**;
a concurrent commit cannot produce a backup of different data than its guard.
The file and its directory are fsynced; its checksum is verified before restore.

`maintenance_data.py` compares all original public-table columns, excluding
Alembic's version row. New columns are not blindly ignored: only the reviewed
reminder timezone backfill and empty lease/action/consent fields are allowed.
Unexpected values, unknown additions, removed columns or changed table sets
block rollback. New migration effects require another review. This is a logical
data check, not a WAL audit: sequence gaps or changes subsequently fully reverted
are not evidence of lost surviving user data. A writer freeze is still mandatory.

Restore requires a CREATEDB, NOSUPERUSER, NOCREATEROLE, NOREPLICATION operator
and the provisioned recovery template. It creates a random **new** target,
verifies the old schema head and data fingerprint, and grants the application
role table/sequence access. It never modifies the shared `.env` or drops the
source database. Before `createdb`, a private, fsynced `restore-*.json` records
the target name; failed targets are retained for operator review and cleanup.
Restored objects are owned by the recovery operator, not the application role;
future schema migrations require an explicitly reviewed ownership/role plan.

Client execution requires the same PostgreSQL major version as the server,
has bounded waits, kills/reaps children on cancellation, and keeps passwords out
of argv and journals. Errors omit raw SQL/stderr. Configure matching clients on
PATH; implicit libpq settings and URL query options are deliberately unsupported.

Integration tests use disposable PostgreSQL 16 and its matching container clients
in the local quality gate and CI. They cover actual dump/restore, application-role
read/write, unchanged source canaries, concurrent writes during dump, data/schema
guard failures, least-privilege rejection and a retained failed restore target.
Fixtures use a small representative schema, not the complete old runtime. The
separate exact-old-release drill above supplies that evidence. Native client
invocation, secrets handling and cancellation have additional unit tests.

Neither component runs Telegram or launchd. Do not treat these checks as evidence
of an end-to-end maintenance deploy or authorization for production downtime.

### launchd and writer-lease components (implemented, integration still pending)

`maintenance_launchd.py` targets only `gui/<current uid>/com.notebook-bot` and
checks the installed plist label, ownership and permissions. It persistently
disables the service before bootout, then requires a recognized disabled entry,
accessible GUI domain and service-not-found status. launchctl diagnostic output
is not a stable API: unfamiliar output/status fails closed and requires a review
for the target macOS version. Native launchd state has not been changed or tested
in this checkpoint; fault injection simulates those responses.

`maintenance_lease.py` acquires the same PostgreSQL advisory lock as the runtime.
Every verification checks the live connection PID, exact lock ownership, absence
of other database connections and absence of prepared transactions. It clears
PostgreSQL's statistics snapshot before checking connections; a cached previous
absence is not proof. Recovery connections may run within an operation but must
be closed before the next freeze check. This is a cooperative freeze, not a DBA
write fence: external administrative writers must remain stopped throughout the
maintenance window. New clients detected at a checkpoint block progress.

Activation requires the durable `admission_started` phase with rollback revoked
and matching journal release/database identities. A private atomic plist sets
the selected DATABASE_URL without rewriting shared `.env`. It launches the
release's Python directly as `-m bot.main`, **not** `run.sh` (which runs Alembic
and seeding before the runtime singleton). Migration, seeding policy and preflight
  therefore belong to the composition rather than the launchd adapter.

Only after that boundary is the source lease released and the service enabled
and bootstrapped. Each activation uses a unique readiness path and requires a
fresh, exact-SHA heartbeat. Failed/uncertain activation disables and removes the
job again; it never restores the snapshot. A restored target needs its own lease
during old-runtime validation, then a controlled release at admission; this is
  handled by the composition layer, not supplied by the source lease.

Tests exercise launchd failure/uncertain-acknowledgement paths without invoking
the real service. Real PostgreSQL tests prove runtime/maintenance mutual exclusion,
snapshot connection cleanup, detection of newly connected clients, explicitly
lost locks and invalidated connections. No claim of full maintenance-deploy
acceptance is made without exact-release and native macOS rehearsal evidence.

## Executable composition and operator plan

`bot/operations/maintenance_deploy.py` now implements the complete MaintenancePort.
Before migration it performs a real separate restore, acquires that target's own
runtime lease, and runs previous-release preflight and schema/vector smoke without
polling. Candidate migration, exact-head preflight and smoke run while the source
lease is held. A recovery target is guarded again until the durable admission
boundary. There is deliberately **no implicit knowledge seeding**: a release that
needs seed changes needs a separately reviewed migration/data-guard policy.

`maintenance_release.py` compares prepared source files and executable modes with
exact Git commit objects (replacement refs disabled), rejects unexpected files and
source symlinks/submodules, checks private `.env`, and runs frozen offline `uv sync
--check --no-dev --extra stt`. The identity includes configuration, lockfile,
interpreter and source fingerprints and release paths. Configuration is rechecked
at phase boundaries. Runtime commands use a fresh bytecode-cache prefix with cache
writes disabled, so existing source-tree `.pyc` is not substituted for checked code.
This does not attest every installed dependency's bytes or replace signed release
evidence; prepared environments and the host remain trusted operator assets.

The journal must use the existing installer's state directory, checked against
the installed readiness path. It shares `deploy.lock`. The installed previous SHA
and effective source database must match the planned source before stopping it.
Plan and execution do not prepare missing releases, install packages, or fetch Git.

Use the module entry point from the repository virtual environment. Supply source
and recovery-operator credentials through protected environment variables
`DATABASE_URL` and `OPERATOR_DATABASE_URL`, never command-line arguments. Substitute
already-prepared paths and exact commits in this **plan-only** example:

```bash
.venv/bin/python -m scripts.maintenance_deploy \
  --repository "/path/to/repository" \
  --release-root "/path/to/prepared/releases" \
  --previous "<40-character previous commit SHA>" \
  --candidate "<40-character candidate commit SHA>" \
  --plist "/path/to/LaunchAgents/com.notebook-bot.plist" \
  --state-dir "/path/to/existing/installer/state"
```

Default mode validates and prints a `MAINTENANCE-...` confirmation identifier;
it does not stop launchd, migrate, back up, restore or create the maintenance journal.
After **all external acceptance gates and downtime approval**, the same invocation
requires `--execute --confirm "<exact identifier>"`. The identifier binds the
operation and current identities, not just a release SHA. An existing journal is
never overwritten by a new deploy. Explicit pre-admission recovery uses `--recover`
and its own freshly generated confirmation. Post-admission recovery is forbidden.
Crash-leftover locks are never automatically reclaimed; reconcile the service,
database and owning process before any operator cleanup. Retain recovery targets
and private manifests until that reconciliation is complete.

Exit codes: `0` for plan/deployed, `2` when candidate deployment failed and the
previous release was restored, `1` for failure/reconciliation required. Only
explicitly operator-safe errors are printed; raw database/OS exception details,
SQL and credentials are withheld.

Composition tests combine actual PostgreSQL dump/restore/locks with simulated
launchd and representative release commands: success, migration/validation failure,
new-data preservation and uncertain activation. Source verification separately
uses real disposable Git objects; CLI tests prove default-no-write and confirmation
binding. These tests do **not** replace the exact-old-runtime drill or a native
maintenance rehearsal. No production transition has been performed by this work.
