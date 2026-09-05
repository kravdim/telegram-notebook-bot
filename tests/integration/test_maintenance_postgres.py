"""Actual pg_dump/pg_restore and logical guards, only in the disposable gate DB."""

import asyncio
import hashlib
import json
import os
import plistlib
import re
import time
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from bot.operations.maintenance_lease import MaintenanceLease
from bot.operations.maintenance_postgres import MaintenancePostgres
from bot.runtime.singleton import SingletonLease

pytestmark = pytest.mark.skipif(
    os.environ.get("DAILYPLANNER_DISPOSABLE_DB") != "1",
    reason="requires explicitly disposable PostgreSQL quality gate",
)


async def container_client(name, url, arguments, timeout=300, *, server_major):
    """Use the isolated server's matching client version, with host file streams."""
    container = os.environ["DAILYPLANNER_TEST_PG_CONTAINER"]
    assert (
        container.startswith("dailyplanner-quality-") and container.endswith("-postgres-1")
    ) or (os.environ.get("GITHUB_ACTIONS") == "true" and re.fullmatch(r"[0-9a-f]{64}", container))
    assert server_major == 16
    arguments = list(arguments)
    with ExitStack() as stack:
        stdin, stdout = asyncio.subprocess.DEVNULL, asyncio.subprocess.DEVNULL
        if name == "pg_dump":
            index = arguments.index("--file")
            stdout = stack.enter_context(open(arguments[index + 1], "wb"))
            del arguments[index:index + 2]
        elif name == "pg_restore":
            stdin = stack.enter_context(open(arguments.pop(), "rb"))
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", "-e", "PGPASSWORD=password", container, name,
            "-h", "127.0.0.1", "-p", "5432", "-U", url.username, "--no-password",
            *arguments, stdin=stdin, stdout=stdout, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, errors = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        # Only synthetic fixtures; diagnostic text never comes from production.
        assert process.returncode == 0, errors.decode()


@pytest_asyncio.fixture
async def postgres(tmp_path, monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    assert (url.host, url.username, url.password, url.database) in {
        ("127.0.0.1", "notebook", "password", "notebook_bot"),
        ("localhost", "planner", "planner", "planner_test"),
    }
    if os.environ.get("DAILYPLANNER_TEST_PG_CONTAINER"):
        monkeypatch.setattr("bot.operations.maintenance_postgres.run_client", container_client)
    suffix = uuid.uuid4().hex[:12]
    source, template = "maintenance_test_" + suffix, "maintenance_template_" + suffix
    operator, app = "maintenance_op_" + suffix, "maintenance_app_" + suffix
    monkeypatch.setattr("bot.operations.maintenance_postgres.RECOVERY_TEMPLATE", template)
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    db = MaintenancePostgres(
        url.set(database=source, username=app, password="password").render_as_string(hide_password=False),
        url.set(username=operator, password="password").render_as_string(hide_password=False), tmp_path,
    )
    created = []
    roles = []
    try:
        async with admin.connect() as connection:
            for role, capabilities in ((operator, "CREATEDB"), (app, "NOCREATEDB")):
                await connection.execute(text(
                    f"CREATE ROLE {role} LOGIN {capabilities} NOSUPERUSER NOCREATEROLE "
                    "NOREPLICATION PASSWORD 'password'"
                ))
                roles.append(role)
            for database, owner in ((source, app), (template, operator)):
                await connection.execute(text(f"CREATE DATABASE {database} OWNER {owner}"))
                created.append(database)
        # Match provisioned recovery template, including extension ownership.
        template_engine = create_async_engine(url.set(database=template), poolclass=NullPool)
        try:
            async with template_engine.begin() as connection:
                await connection.execute(text(f"ALTER ROLE {operator} SUPERUSER"))
                await connection.execute(text(f"SET LOCAL ROLE {operator}"))
                await connection.execute(text("CREATE EXTENSION vector"))
                await connection.execute(text("RESET ROLE"))
                await connection.execute(text(f"ALTER ROLE {operator} NOSUPERUSER"))
        finally:
            await template_engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(
                f"ALTER DATABASE {template} IS_TEMPLATE true ALLOW_CONNECTIONS false"
            ))
        async with db.engine.begin() as connection:
            for sql in (
                "CREATE TABLE alembic_version (version_num text PRIMARY KEY)",
                "INSERT INTO alembic_version VALUES ('old-head')",
                "CREATE TABLE users (telegram_id bigint PRIMARY KEY, timezone text, title text)",
                "INSERT INTO users VALUES (1, 'Europe/Moscow', 'private memo')",
                "CREATE TABLE reminders (id serial PRIMARY KEY, user_id bigint, title text)",
                "INSERT INTO reminders (user_id, title) VALUES (1, 'original task')",
                "CREATE TABLE processed_requests (id text PRIMARY KEY)",
                "INSERT INTO processed_requests VALUES ('request')",
            ):
                await connection.execute(text(sql))
        yield db
    finally:
        await db.close()
        async with admin.connect() as connection:
            # Names are generated here, not read from production configuration.
            for database in [*db.created_databases, *reversed(created)]:
                if database == template:
                    await connection.execute(text(f"ALTER DATABASE {template} IS_TEMPLATE false"))
                await connection.execute(text(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"))
            for role in reversed(roles):
                await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


async def execute(db, sql):
    async with db.engine.begin() as connection:
        await connection.execute(text(sql))


async def add_reviewed_columns(db):
    for sql in (
        "ALTER TABLE users ADD COLUMN privacy_provider_fingerprint text",
        "ALTER TABLE reminders ADD COLUMN series_timezone text NOT NULL DEFAULT 'Europe/Moscow'",
        "ALTER TABLE reminders ADD COLUMN lease_token uuid",
        "ALTER TABLE reminders ADD COLUMN lease_expires_at timestamptz",
        "ALTER TABLE reminders ADD COLUMN next_attempt_at timestamptz",
        "ALTER TABLE processed_requests ADD COLUMN action_plan jsonb",
        "ALTER TABLE processed_requests ADD COLUMN action_results jsonb NOT NULL DEFAULT '{}'",
        "UPDATE alembic_version SET version_num='new-head'",
    ):
        await execute(db, sql)


async def test_real_snapshot_restore_retains_candidate_and_grants_app_access(postgres):
    snapshot = await postgres.snapshot()
    snapshot.verify()
    assert "private memo" not in str(snapshot.traffic_guard)
    assert os.stat(snapshot.path).st_mode & 0o777 == 0o600
    await postgres.assert_unchanged(snapshot)
    await add_reviewed_columns(postgres)
    await postgres.assert_unchanged(snapshot)
    await execute(postgres, "UPDATE users SET title='candidate canary'")
    with pytest.raises(RuntimeError, match="data changed"):
        await postgres.assert_unchanged(snapshot)
    # Component restore does not decide admission: the workflow calls guard first.
    restored = await postgres.restore_separate(snapshot)
    engine = postgres._engine(postgres.source.set(database=restored))
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT title FROM users")) == "private memo"
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "old-head"
            await connection.execute(text("UPDATE users SET title='old runtime write'"))
            await connection.execute(text("INSERT INTO reminders (title) VALUES ('new task')"))
    finally:
        await engine.dispose()
    async with postgres.engine.connect() as connection:
        assert await connection.scalar(text("SELECT title FROM users")) == "candidate canary"


@pytest.mark.parametrize("sql", [
    "INSERT INTO users VALUES (2, 'UTC', 'new write')",
    "DELETE FROM users",
    "UPDATE users SET title='changed'",
])
async def test_baseline_insert_update_delete_blocks_snapshot_rollback(postgres, sql):
    snapshot = await postgres.snapshot()
    await execute(postgres, sql)
    with pytest.raises(RuntimeError, match="data changed"):
        await postgres.assert_unchanged(snapshot)


async def test_new_recovery_fields_are_not_silently_ignored(postgres):
    snapshot = await postgres.snapshot()
    await add_reviewed_columns(postgres)
    for change, reset in (
        ("UPDATE processed_requests SET action_plan='[]'", "UPDATE processed_requests SET action_plan=NULL"),
        ("UPDATE processed_requests SET action_results='{\"0\": true}'", "UPDATE processed_requests SET action_results='{}'"),
        ("UPDATE reminders SET series_timezone='UTC'", "UPDATE reminders SET series_timezone='Europe/Moscow'"),
        ("UPDATE users SET privacy_provider_fingerprint='new'", "UPDATE users SET privacy_provider_fingerprint=NULL"),
    ):
        await execute(postgres, change)
        with pytest.raises(RuntimeError, match="post-snapshot data"):
            await postgres.assert_unchanged(snapshot)
        await execute(postgres, reset)
        await postgres.assert_unchanged(snapshot)


async def test_unknown_schema_change_fails_closed(postgres):
    snapshot = await postgres.snapshot()
    await execute(postgres, "ALTER TABLE users ADD COLUMN unknown_field text")
    with pytest.raises(RuntimeError, match="unreviewed column"):
        await postgres.assert_unchanged(snapshot)


async def test_identifiers_are_quoted_and_missing_tables_fail_closed(postgres):
    await execute(postgres, 'CREATE TABLE "odd""table" ("odd""column" text)')
    snapshot = await postgres.snapshot()
    await postgres.assert_unchanged(snapshot)
    await execute(postgres, 'DROP TABLE "odd""table"')
    with pytest.raises(RuntimeError, match="table set changed"):
        await postgres.assert_unchanged(snapshot)


async def test_superuser_operator_refused_before_creating_restore_target(postgres):
    snapshot = await postgres.snapshot()
    await postgres.operator_engine.dispose()
    postgres.operator_engine = postgres._engine(make_url(os.environ["DATABASE_URL"]))
    with pytest.raises(RuntimeError, match="least privilege"):
        await postgres.restore_separate(snapshot)
    assert postgres.created_databases == []


async def test_dump_and_fingerprint_use_same_exported_snapshot(postgres, monkeypatch):
    from bot.operations import maintenance_postgres as module

    original = module.run_client

    async def write_during_dump(name, url, arguments, **kwargs):
        if name == "pg_dump":
            await execute(postgres, "UPDATE users SET title='concurrent write'")
        await original(name, url, arguments, **kwargs)

    monkeypatch.setattr(module, "run_client", write_during_dump)
    snapshot = await postgres.snapshot()
    with pytest.raises(RuntimeError, match="data changed"):
        await postgres.assert_unchanged(snapshot)
    restored = await postgres.restore_separate(snapshot)
    engine = postgres._engine(postgres.source.set(database=restored))
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT title FROM users")) == "private memo"
    finally:
        await engine.dispose()


async def test_failed_restore_retains_target_and_durable_manifest(postgres):
    snapshot = await postgres.snapshot()
    Path(snapshot.path).write_bytes(b"not a PostgreSQL archive")
    # Valid checksum is not evidence of a restorable archive.
    snapshot = replace(snapshot, sha256=hashlib.sha256(b"not a PostgreSQL archive").hexdigest())
    with pytest.raises((RuntimeError, AssertionError)):
        await postgres.restore_separate(snapshot)
    assert len(postgres.created_databases) == 1
    database = postgres.created_databases[0]
    path = postgres.directory / f"restore-{database}.json"
    manifest = json.loads(path.read_text())
    assert manifest["database"] == database and manifest["phase"] == "database_created"
    assert path.stat().st_mode & 0o777 == 0o600
    async with postgres.operator_engine.connect() as connection:
        assert await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_database "
                                            "WHERE datname=:name)"), {"name": database})


async def test_maintenance_lease_blocks_runtime_and_survives_snapshot_connections(postgres):
    lease = MaintenanceLease(postgres.engine)
    runtime = SingletonLease(postgres.engine)
    try:
        await lease.acquire(timeout=0)
        assert not await runtime.acquire()
        await postgres.snapshot()
        await lease.assert_exclusive()
    finally:
        await lease.release()
    assert await runtime.acquire()
    await runtime.release()


async def test_existing_runtime_blocks_maintenance_until_it_exits(postgres):
    runtime = SingletonLease(postgres.engine)
    lease = MaintenanceLease(postgres.engine)
    assert await runtime.acquire()
    try:
        with pytest.raises(RuntimeError, match="did not drain"):
            await lease.acquire(timeout=0)
    finally:
        await runtime.release()
    try:
        await lease.acquire(timeout=0)
    finally:
        await lease.release()


async def test_new_database_client_invalidates_previously_verified_freeze(postgres):
    lease = MaintenanceLease(postgres.engine)
    try:
        await lease.acquire(timeout=0)
        await lease.assert_exclusive()
        async with postgres.engine.connect():
            with pytest.raises(RuntimeError, match="no other database clients"):
                await lease.assert_exclusive()
        await lease.assert_exclusive()
        assert lease.connection is not None
        await lease.connection.execute(text(
            "SELECT pg_advisory_unlock(hashtext('dailyplanner:runtime'))"
        ))
        with pytest.raises(RuntimeError, match="live lease"):
            await lease.assert_exclusive()
    finally:
        await lease.release()


async def test_invalidated_connection_cannot_reuse_cached_lease_state(postgres):
    lease = MaintenanceLease(postgres.engine)
    try:
        await lease.acquire(timeout=0)
        assert lease.connection is not None
        await lease.connection.invalidate()
        with pytest.raises(RuntimeError, match="not held"):
            await lease.assert_exclusive()
    finally:
        await lease.release()


class _MaintenanceLaunchSimulator:
    def __init__(self, port, journal, plist, state, scenario):
        self.port, self.journal, self.plist = port, journal, plist
        self.state, self.scenario = state, scenario

    async def __call__(self, *args):
        state, port, journal = self.state, self.port, self.journal
        command = args[0]
        if command == "disable":
            state["disabled"] = True
        elif command == "print-disabled":
            value = "disabled" if state["disabled"] else "enabled"
            return 0, f'"com.notebook-bot" => {value}'
        elif command == "bootout":
            state["loaded"] = False
        elif command == "print" and args[1] == port.launchd.target:
            return (0 if state["loaded"] else 113), ""
        elif command == "enable":
            assert journal.load()["rollback_permitted"] is False
            assert port.source_lease.connection is None
            assert port.target_lease is None
            state["admissions"] += 1
            state["disabled"] = False
        elif command == "bootstrap":
            state["loaded"] = True
            if self.scenario == "activation":
                return 5, "uncertain activation"
            env = plistlib.loads(self.plist.read_bytes())["EnvironmentVariables"]
            Path(env["READINESS_FILE"]).write_text(json.dumps({
                "ready": True, "pid": os.getpid(), "heartbeat_epoch": time.time(),
                "release_sha": env["DAILYPLANNER_RELEASE_SHA"],
            }))
        return 0, ""


@pytest.mark.parametrize("scenario", ["success", "migration", "validation", "new_data", "activation"])
async def test_composed_maintenance_with_real_postgres(postgres, tmp_path, monkeypatch, scenario):
    from bot.operations import maintenance_launchd as launch
    from bot.operations.maintenance import MaintenanceJournal, deploy
    from bot.operations.maintenance_deploy import _CHECK_SOURCE, _SCHEMA_SMOKE, MacMaintenance
    from bot.operations.maintenance_release import Release

    root = tmp_path.resolve()
    previous_sha, candidate_sha = "b" * 40, "a" * 40
    journal = MaintenanceJournal(root / "state/maintenance.json")
    releases = [Release(root, root / sha, sha) for sha in (previous_sha, candidate_sha)]
    for release in releases:
        (release.directory / ".venv/bin").mkdir(parents=True)
        (release.directory / ".venv/bin/python").touch()
    plist = root / "bot.plist"
    launch.atomic_private(plist, plistlib.dumps({
        "Label": launch.LABEL, "WorkingDirectory": str(releases[0].directory),
        "EnvironmentVariables": {"DAILYPLANNER_RELEASE_SHA": previous_sha,
                                 "READINESS_FILE": str(journal.path.parent / "old-ready.json")},
    }))
    port = MacMaintenance(*releases, postgres, plist, journal)
    state = {"disabled": False, "loaded": True, "admissions": 0, "old_smoke": 0}

    async def verify(self, env):
        return {"source": self.sha, "config": "synthetic-reviewed-fixture"}

    async def command(self, arguments, env):
        # Source/artifact verification has separate real-Git tests. Here release
        # commands use the small fixture schema; no Telegram/polling is invoked.
        if arguments == ["-c", _CHECK_SOURCE]:
            return
        if arguments == ["-m", "alembic", "upgrade", "head"]:
            assert state["disabled"] and not state["loaded"]
            if scenario == "migration":
                raise RuntimeError("injected migration failure")
            await add_reviewed_columns(postgres)
            if scenario == "new_data":
                await execute(postgres, "UPDATE users SET title='must retain new write'")
                raise RuntimeError("injected post-snapshot write")
            return
        if self.sha == candidate_sha and scenario == "validation":
            raise RuntimeError("injected candidate validation failure")
        engine = postgres._engine(make_url(env["DATABASE_URL"]))
        try:
            async with engine.connect() as connection:
                head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                assert head == ("old-head" if self.sha == previous_sha else "new-head")
            if arguments == ["-c", _SCHEMA_SMOKE]:
                runtime = SingletonLease(engine)
                assert not await runtime.acquire(), "validation must hold the target's runtime lock"
                if self.sha == previous_sha:
                    state["old_smoke"] += 1
        finally:
            await engine.dispose()

    monkeypatch.setattr(launch, "launchctl", _MaintenanceLaunchSimulator(port, journal, plist, state, scenario))
    monkeypatch.setattr(Release, "verify", verify)
    monkeypatch.setattr(Release, "command", command)
    if scenario in {"new_data", "activation"}:
        with pytest.raises(RuntimeError):
            await deploy(port, journal)
        assert state["disabled"] and not state["loaded"]
        assert journal.load()["phase"] == "recovery_required"
        if scenario == "activation":
            assert journal.load()["rollback_permitted"] is False
        else:
            async with postgres.engine.connect() as connection:
                assert await connection.scalar(text("SELECT title FROM users")) == "must retain new write"
        assert len(postgres.created_databases) == 1  # Only the pre-migration verification restore.
    else:
        result = await deploy(port, journal)
        assert result == ("deployed" if scenario == "success" else "restored_previous")
        assert state["admissions"] == 1 and state["loaded"]
        assert state["old_smoke"] == (1 if scenario == "success" else 2)
    assert not (journal.path.parent / "deploy.lock").exists()
