"""PostgreSQL snapshot/restore component; does not stop or start the service."""

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from bot.operations.maintenance import MaintenanceJournal, Snapshot
from bot.operations.maintenance_data import capture

RECOVERY_TEMPLATE = "dailyplanner_recovery_template"


def database_url(value: str) -> URL:
    url = make_url(value)
    if (not url.drivername.startswith("postgresql") or not url.host or not url.username
            or not url.database or url.query):
        raise ValueError("Explicit PostgreSQL host/user/database required; URL options unsupported")
    return url


def identity(url: URL) -> str:
    safe = URL.create("postgresql", username=url.username, host=url.host,
                      port=url.port or 5432, database=url.database)
    return hashlib.sha256(safe.render_as_string().encode()).hexdigest()


def client(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("/opt/homebrew/opt").glob(f"postgresql*/bin/{name}"), reverse=True)
    if not candidates:
        raise RuntimeError(f"PostgreSQL client missing: {name}")
    return str(candidates[0])


async def run_client(
    name: str, url: URL, arguments: list[str], timeout: float = 300, *, server_major: int,
) -> None:
    # Discard inherited libpq/service settings: no implicit credentials or target.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGPASSWORD=url.password or "", PGCONNECT_TIMEOUT="10")
    executable = client(name)
    probe = await asyncio.create_subprocess_exec(
        executable, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        version, _ = await asyncio.wait_for(probe.communicate(), 10)
    except BaseException:
        if probe.returncode is None:
            probe.kill()
        await probe.wait()
        raise
    match = re.search(rb"\(PostgreSQL\) (\d+)\.", version)
    if probe.returncode or not match or int(match[1]) != server_major:
        raise RuntimeError(f"{name} major version must match PostgreSQL server {server_major}")
    process = await asyncio.create_subprocess_exec(
        executable, "-h", url.host or "", "-p", str(url.port or 5432),
        "-U", url.username or "", "--no-password", *arguments,
        env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"{name} failed (exit {process.returncode}); service must stay stopped")


class MaintenancePostgres:
    """Caller must persistently freeze writers and hold the runtime lease.

    Backup and data guard share one exported MVCC snapshot. Restore never drops,
    cleans or overwrites a database. All created targets are retained for review,
    including failed restores. Only synthetic integration tests clean their DBs.
    """

    def __init__(self, source_url: str, operator_url: str, directory: Path):
        self.source = database_url(source_url)
        self.operator = database_url(operator_url)
        if (self.source.host, self.source.port or 5432) != (
            self.operator.host, self.operator.port or 5432,
        ):
            raise ValueError("Source and recovery operator must target the same server")
        self.directory = directory
        self.engine = self._engine(self.source)
        self.operator_engine = self._engine(self.operator)
        self.created_databases: list[str] = []

    @staticmethod
    def _engine(url: URL):
        return create_async_engine(
            url.set(drivername="postgresql+asyncpg"), poolclass=NullPool,
            connect_args={"timeout": 10, "command_timeout": 60},
        )

    async def validate_operator(self) -> int:
        async with self.operator_engine.connect() as connection:
            role = (await connection.execute(text(
                "SELECT rolcreatedb, rolsuper, rolcreaterole, rolreplication "
                "FROM pg_roles WHERE rolname=current_user"
            ))).one()
            template = await connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=:name "
                "AND datistemplate AND NOT datallowconn)"
            ), {"name": RECOVERY_TEMPLATE})
            if tuple(role) != (True, False, False, False) or not template:
                raise RuntimeError("Recovery operator/template does not satisfy least privilege")
            return int(await connection.scalar(text("SHOW server_version_num"))) // 10000

    async def snapshot(self) -> Snapshot:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, filename = tempfile.mkstemp(prefix="maintenance-", suffix=".dump",
                                               dir=self.directory)
        os.close(descriptor)
        path = Path(filename)
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                exported = await connection.scalar(text("SELECT pg_export_snapshot()"))
                guard = await capture(connection)
                head = (await connection.execute(text("SELECT version_num FROM alembic_version")))
                revision = head.scalar_one()
                major = int(await connection.scalar(text("SHOW server_version_num"))) // 10000
                await run_client("pg_dump", self.source, [
                    "-d", self.source.database or "", "--format=custom", "--no-owner",
                    "--no-privileges", "--snapshot", str(exported), "--file", str(path),
                ], server_major=major)
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return Snapshot(str(path), digest, identity(self.source), revision, guard)

    async def assert_unchanged(self, snapshot: Snapshot) -> None:
        if snapshot.database_identity != identity(self.source) or not snapshot.traffic_guard:
            raise RuntimeError("Snapshot source identity or data guard invalid")
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                await capture(connection, snapshot.traffic_guard)

    async def restore_separate(self, snapshot: Snapshot) -> str:
        snapshot.verify()
        if snapshot.database_identity != identity(self.source) or not snapshot.traffic_guard:
            raise RuntimeError("Snapshot source identity or data guard invalid")
        major = await self.validate_operator()
        database = "dailyplanner_maintenance_" + secrets.token_hex(12)
        manifest = MaintenanceJournal(self.directory / f"restore-{database}.json")
        manifest.save("restore_planned", database=database, snapshot_sha256=snapshot.sha256,
                      source_identity=snapshot.database_identity)
        # Retain the name even if createdb succeeds but its acknowledgement fails.
        self.created_databases.append(database)
        await run_client("createdb", self.operator, [
            "--maintenance-db", self.operator.database or "postgres",
            "--template", RECOVERY_TEMPLATE, database,
        ], server_major=major)
        manifest.save("database_created")
        target = self.operator.set(database=database)
        await run_client("pg_restore", target, [
            "-d", database, "--no-owner", "--no-privileges", "--exit-on-error",
            "--single-transaction", snapshot.path,
        ], server_major=major)
        await self._validate_restored(target, snapshot)
        await self._grant_application(target)
        manifest.save("restore_verified")
        return database

    async def _validate_restored(self, target: URL, snapshot: Snapshot) -> None:
        engine = self._engine(target)
        try:
            async with engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    revision = (await connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    )).scalar_one()
                    if revision != snapshot.schema_head:
                        raise RuntimeError("Restored migration head mismatch")
                    await capture(connection, snapshot.traffic_guard)
        finally:
            await engine.dispose()

    async def _grant_application(self, target: URL) -> None:
        engine = self._engine(target)
        try:
            async with engine.begin() as connection:
                quote = connection.dialect.identifier_preparer.quote_identifier
                role = quote(self.source.username or "")
                await connection.execute(text(
                    f"GRANT CONNECT ON DATABASE {quote(target.database or '')} TO {role}"
                ))
                await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                await connection.execute(text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
                ))
                await connection.execute(text(
                    f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}"
                ))
        finally:
            await engine.dispose()

    async def close(self) -> None:
        await self.engine.dispose()
        await self.operator_engine.dispose()
