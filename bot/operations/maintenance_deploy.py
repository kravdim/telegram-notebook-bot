"""Concrete maintenance composition; all writer admission belongs to the journal."""

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from bot.operations.maintenance import MaintenanceJournal, Snapshot
from bot.operations.maintenance_launchd import MaintenanceLaunchd
from bot.operations.maintenance_lease import MaintenanceLease
from bot.operations.maintenance_postgres import MaintenancePostgres, identity
from bot.operations.maintenance_release import Release

_CHECK_SOURCE = """
import os
from sqlalchemy.engine import make_url
from bot.config import settings
def target(value):
    u = make_url(value)
    return (u.host, u.port or 5432, u.username, u.database)
if target(settings.database_url) != target(os.environ['MAINTENANCE_EXPECTED_DATABASE']):
    raise RuntimeError('Installed runtime database does not match maintenance source')
"""
_SCHEMA_SMOKE = """
import asyncio
from bot.db.engine import engine
from scripts.container_smoke import exercise_schema
async def main():
    try:
        await exercise_schema()
    finally:
        await engine.dispose()
asyncio.run(main())
"""


def confirmation(identity_record: dict[str, str]) -> str:
    return "MAINTENANCE-" + hashlib.sha256(
        json.dumps(identity_record, sort_keys=True).encode()
    ).hexdigest()


class MacMaintenance:
    def __init__(self, previous: Release, candidate: Release, postgres: MaintenancePostgres,
                 plist: Path, journal: MaintenanceJournal):
        self.previous, self.candidate = previous, candidate
        self.postgres, self.journal = postgres, journal
        self.source_lease = MaintenanceLease(postgres.engine)
        self.launchd = MaintenanceLaunchd(plist, journal, self.source_lease)
        self.expected: dict[str, str] | None = None
        self.environment: dict[str, str] = {}
        self.target_lease: MaintenanceLease | None = None
        self.target_engine: AsyncEngine | None = None
        self.target_database: str | None = None
        self.backup: Snapshot | None = None

    async def validate(self) -> dict[str, str]:
        payload = self.launchd.installed()
        if Path(payload["WorkingDirectory"]) != self.previous.directory:
            raise RuntimeError("Installed runtime differs from previous release")
        if payload.get("EnvironmentVariables", {}).get("DAILYPLANNER_RELEASE_SHA") != self.previous.sha:
            raise RuntimeError("Installed runtime SHA differs from previous release")
        readiness = payload.get("EnvironmentVariables", {}).get("READINESS_FILE")
        if not readiness or Path(readiness).parent.resolve() != self.journal.path.parent.resolve():
            raise RuntimeError("Maintenance journal must share the installer's state/lock directory")
        # Do not inherit arbitrary shell application settings, PYTHONHOME, UV_* or PG_*.
        base = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG") if key in os.environ}
        environment = {**base, **payload.get("EnvironmentVariables", {})}
        previous = await self.previous.verify(base)
        candidate = await self.candidate.verify(base)
        record = {"previous": self.previous.sha, "candidate": self.candidate.sha,
                  "previous_path": str(self.previous.directory),
                  "candidate_path": str(self.candidate.directory),
                  "database": identity(self.postgres.source),
                  "source_database": self.postgres.source.database or "",
                  "operator": identity(self.postgres.operator),
                  "plist": hashlib.sha256(self.launchd.plist.read_bytes()).hexdigest(),
                  "state": str(self.journal.path.parent.resolve())}
        for prefix, values in (("previous", previous), ("candidate", candidate)):
            record.update({f"{prefix}_{key}": value for key, value in values.items()})
        if self.expected is not None and record != self.expected:
            raise RuntimeError("Release/configuration identity changed during maintenance")
        await self.previous.command(["-c", _CHECK_SOURCE], {
            **environment, "MAINTENANCE_EXPECTED_DATABASE": self._url(),
        })
        if not self.journal.path.exists():
            await self.previous.command(["scripts/preflight.py"], {
                **environment, "DATABASE_URL": self._url(),
            })
        await self.postgres.validate_operator()
        self.environment = environment
        self.expected = record
        return record

    def _url(self, database: str | None = None) -> str:
        url = self.postgres.source
        if database is not None:
            url = url.set(database=database)
        return url.render_as_string(hide_password=False)

    def _env(self, database: str | None = None) -> dict[str, str]:
        return {**self.environment, "DATABASE_URL": self._url(database)}

    async def freeze(self) -> None:
        await self.validate()
        await self.launchd.freeze()

    async def assert_frozen(self) -> None:
        await self.launchd.assert_frozen()
        if self.target_lease is not None:
            await self.target_lease.assert_exclusive()

    async def assert_unchanged(self, snapshot: Snapshot) -> None:
        await self.postgres.assert_unchanged(snapshot)

    async def snapshot(self) -> Snapshot:
        await self.assert_frozen()
        self.backup = await self.postgres.snapshot()
        return self.backup

    async def verify_restore(self, snapshot: Snapshot) -> None:
        database = await self.postgres.restore_separate(snapshot)
        try:
            await self.validate_previous(database)
        finally:
            await self._release_target()

    async def migrate(self) -> None:
        await self.validate()
        await self.assert_frozen()
        # No compatibility override and no implicit knowledge seeding.
        await self.candidate.command(["-m", "alembic", "upgrade", "head"], self._env())

    async def validate_candidate(self) -> None:
        await self.validate()
        await self.candidate.command(["scripts/preflight.py"], self._env())
        await self.candidate.command(["-c", _SCHEMA_SMOKE], self._env())

    async def restore_separate(self, snapshot: Snapshot) -> str:
        self.backup = snapshot
        return await self.postgres.restore_separate(snapshot)

    async def validate_previous(self, database: str) -> None:
        await self.validate()
        if database not in self.postgres.created_databases:
            raise RuntimeError("Refusing an unowned recovery target")
        await self._release_target()
        self.target_engine = self.postgres._engine(self.postgres.source.set(database=database))
        self.target_lease = MaintenanceLease(self.target_engine)
        self.target_database = database
        await self.target_lease.acquire()
        await self.previous.command(["scripts/preflight.py"], self._env(database))
        await self.previous.command(["-c", _SCHEMA_SMOKE], self._env(database))
        await self.target_lease.assert_exclusive()
        if self.backup is None:
            raise RuntimeError("Recovery validation requires the exact snapshot")
        await self.postgres._validate_restored(self.postgres.source.set(database=database), self.backup)

    async def activate(self, release: str, database: str | None) -> None:
        await self.validate()
        await self.assert_frozen()
        if self.backup is None:
            raise RuntimeError("Activation requires the exact snapshot")
        await self.assert_unchanged(self.backup)
        if release == "previous":
            if database is None or database != self.target_database:
                raise RuntimeError("Restored target lease is missing")
            await self.postgres._validate_restored(self.postgres.source.set(database=database), self.backup)
        elif release != "candidate" or database is not None:
            raise RuntimeError("Invalid activation target")
        # The source lease remains held until launchd's admission operation. The
        # restored-target lease is released only after the same durable boundary.
        record = self.journal.load()
        if record.get("phase") != "admission_started" or record.get("rollback_permitted") is not False:
            raise RuntimeError("Target lease release requires durable admission")
        await self._release_target()
        selected = self.previous if release == "previous" else self.candidate
        await self.launchd.activate(selected.directory, selected.sha, self._url(database))

    async def halt(self) -> None:
        await self.launchd.halt()

    async def _release_target(self) -> None:
        try:
            if self.target_lease is not None:
                await self.target_lease.release()
        finally:
            self.target_lease = None
            self.target_database = None
            if self.target_engine is not None:
                await self.target_engine.dispose()
                self.target_engine = None

    async def close(self) -> None:
        try:
            await self._release_target()
        finally:
            try:
                await self.launchd.close()
            finally:
                await self.postgres.close()
