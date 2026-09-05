"""Durable maintenance state machine: snapshot rollback ends before traffic starts."""

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


class MaintenanceError(RuntimeError):
    """Operator-safe diagnostic: never include credentials, SQL or user content."""


@dataclass(frozen=True)
class Snapshot:
    path: str
    sha256: str
    database_identity: str
    schema_head: str
    traffic_guard: dict = field(default_factory=dict)

    def verify(self) -> None:
        digest = hashlib.sha256()
        with Path(self.path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != self.sha256:
            raise RuntimeError("Maintenance snapshot checksum mismatch")


class MaintenancePort(Protocol):
    async def validate(self) -> dict[str, str]: ...
    async def freeze(self) -> None: ...
    async def assert_frozen(self) -> None: ...
    async def assert_unchanged(self, snapshot: Snapshot) -> None: ...
    async def snapshot(self) -> Snapshot: ...
    async def verify_restore(self, snapshot: Snapshot) -> None: ...
    async def migrate(self) -> None: ...
    async def validate_candidate(self) -> None: ...
    async def restore_separate(self, snapshot: Snapshot) -> str: ...
    async def validate_previous(self, database: str) -> None: ...
    async def activate(self, release: str, database: str | None) -> None: ...
    async def halt(self) -> None: ...
    async def close(self) -> None: ...


class MaintenanceJournal:
    """Replace+fsync before each unsafe boundary; no credentials or raw exceptions."""

    def __init__(self, path: Path):
        self.path = path
        self.record: dict = {}

    def load(self) -> dict:
        self.record = json.loads(self.path.read_text(encoding="utf-8"))
        return self.record

    def save(self, phase: str, **fields: object) -> None:
        record = {**self.record, **fields, "phase": phase}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".maintenance-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.record = record
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def exclusive(self):
        """Share install.sh's directory lock; crash leftovers require operator review.

        The journal must live in the installer's state directory. Never remove a
        pre-existing lock: it may belong to a running installer or crashed deploy.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.parent / "deploy.lock"
        lock.mkdir(mode=0o700)
        try:
            yield
        finally:
            lock.rmdir()


async def _activate(
    port: MaintenancePort, journal: MaintenanceJournal, release: str, database: str | None,
) -> str:
    # Persist BEFORE enabling/loading the service: even an uncertain load result
    # must never cause a later invocation to restore over accepted user writes.
    journal.save("admission_started", rollback_permitted=False,
                 activating_release=release, activating_database=database)
    await port.activate(release, database)
    final = "deployed" if release == "candidate" else "restored_previous"
    journal.save(final)
    return final


async def _restore(port: MaintenancePort, journal: MaintenanceJournal) -> str:
    if not journal.record.get("rollback_permitted") or not journal.record.get("snapshot_verified"):
        raise RuntimeError("Automatic restore is not permitted for this journal")
    await port.assert_frozen()
    snapshot = Snapshot(**journal.record["snapshot"])
    snapshot.verify()
    await port.assert_unchanged(snapshot)
    journal.save("restoring")
    database = await port.restore_separate(snapshot)
    journal.save("restored_database", restored_database=database)
    await port.validate_previous(database)
    await port.assert_frozen()
    return await _activate(port, journal, "previous", database)


async def _deploy(port: MaintenancePort, journal: MaintenanceJournal) -> str:
    if journal.path.exists():
        raise RuntimeError("Existing maintenance journal requires explicit recovery")
    identity = await port.validate()
    journal.save("prepared", identity=identity, rollback_permitted=False, snapshot_verified=False)
    try:
        journal.save("freezing")
        await port.freeze()
        await port.assert_frozen()
        journal.save("frozen")
        snapshot = await port.snapshot()
        snapshot.verify()
        journal.save("snapshot_created", snapshot=asdict(snapshot))
        await port.verify_restore(snapshot)
        await port.assert_frozen()
        journal.save("snapshot_verified", snapshot_verified=True, rollback_permitted=True)
        journal.save("migrating")
        await port.migrate()
        journal.save("validating_candidate")
        await port.validate_candidate()
        await port.assert_frozen()
        await port.assert_unchanged(snapshot)
        return await _activate(port, journal, "candidate", None)
    except Exception as exc:
        await port.halt()
        # Reload durable state: a journal write may have reached disk and then
        # raised. In-memory rollback_permitted must not override that evidence.
        journal.load()
        if journal.record.get("rollback_permitted"):
            try:
                return await _restore(port, journal)
            except Exception as recovery_error:
                await port.halt()
                journal.save("recovery_required", error_type=type(recovery_error).__name__)
                raise
        await port.halt()
        journal.save("recovery_required", error_type=type(exc).__name__)
        raise


async def _recover(port: MaintenancePort, journal: MaintenanceJournal) -> str:
    record = journal.load()
    if not record.get("rollback_permitted") or not record.get("snapshot_verified"):
        raise RuntimeError("Recovery requires operator reconciliation; snapshot restore forbidden")
    if await port.validate() != record["identity"]:
        raise RuntimeError("Release/config/database identity changed since snapshot")
    try:
        await port.freeze()
        return await _restore(port, journal)
    except Exception:
        await port.halt()
        journal.save("recovery_required")
        raise


async def deploy(port: MaintenancePort, journal: MaintenanceJournal) -> str:
    with journal.exclusive():
        try:
            return await _deploy(port, journal)
        finally:
            await port.close()


async def recover(port: MaintenancePort, journal: MaintenanceJournal) -> str:
    with journal.exclusive():
        try:
            return await _recover(port, journal)
        finally:
            await port.close()
