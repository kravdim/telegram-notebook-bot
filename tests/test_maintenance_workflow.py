"""Failure injection for the durable maintenance admission boundary (no services)."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from bot.operations.maintenance import MaintenanceJournal, Snapshot, deploy, recover


class FakePort:
    def __init__(self, tmp_path, journal, failure=None):
        self.journal = journal
        self.failure = failure
        self.calls = []
        self.identity = {"candidate": "new", "previous": "old", "database": "db-id"}
        path = tmp_path / "snapshot.dump"
        path.write_bytes(b"synthetic snapshot")
        self.backup = Snapshot(str(path), hashlib.sha256(path.read_bytes()).hexdigest(),
                               "db-id", "old-head")

    def call(self, name):
        self.calls.append(name)
        if self.failure == name:
            raise RuntimeError(name)

    async def validate(self):
        self.call("validate")
        return self.identity

    async def freeze(self):
        self.call("freeze")

    async def assert_frozen(self):
        self.call("assert_frozen")

    async def assert_unchanged(self, snapshot):
        assert snapshot == self.backup
        self.call("assert_unchanged")

    async def snapshot(self):
        self.call("snapshot")
        return self.backup

    async def verify_restore(self, snapshot):
        snapshot.verify()
        self.call("verify_restore")

    async def migrate(self):
        self.call("migrate")

    async def validate_candidate(self):
        self.call("validate_candidate")

    async def restore_separate(self, snapshot):
        snapshot.verify()
        self.call("restore_separate")
        return "restored-db"

    async def validate_previous(self, database):
        assert database == "restored-db"
        self.call("validate_previous")

    async def activate(self, release, database):
        durable = json.loads(self.journal.path.read_text())
        assert durable["rollback_permitted"] is False
        assert durable["phase"] == "admission_started"
        assert database == ("restored-db" if release == "previous" else None)
        self.call("activate_" + release)

    async def halt(self):
        self.call("halt")

    async def close(self):
        self.call("close")


def setup(tmp_path, failure=None, journal_class=MaintenanceJournal):
    journal = journal_class(tmp_path / "state" / "maintenance.json")
    return FakePort(tmp_path, journal, failure), journal


@pytest.mark.asyncio
async def test_success_persists_admission_before_activation(tmp_path):
    port, journal = setup(tmp_path)
    assert await deploy(port, journal) == "deployed"
    assert journal.load()["rollback_permitted"] is False
    assert "restore_separate" not in port.calls
    assert port.calls.index("verify_restore") < port.calls.index("migrate")
    assert port.calls[-1] == "close"
    assert journal.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["migrate", "validate_candidate"])
async def test_pre_admission_failure_restores_separate_database(tmp_path, failure):
    port, journal = setup(tmp_path, failure)
    assert await deploy(port, journal) == "restored_previous"
    assert "activate_candidate" not in port.calls
    assert port.calls.index("assert_unchanged") < port.calls.index("restore_separate")
    assert journal.load()["restored_database"] == "restored-db"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["freeze", "snapshot", "verify_restore",
                                    "assert_frozen", "assert_unchanged", "activate_candidate"])
async def test_failure_cannot_restore_unverified_or_changed_data(tmp_path, failure):
    port, journal = setup(tmp_path, failure)
    with pytest.raises(RuntimeError):
        await deploy(port, journal)
    assert "restore_separate" not in port.calls
    assert journal.load()["phase"] == "recovery_required"
    assert "halt" in port.calls
    assert port.calls[-1] == "close"


class UncertainWriteJournal(MaintenanceJournal):
    def save(self, phase, **fields):
        super().save(phase, **fields)
        if phase == "admission_started":
            # Simulate durable replacement followed by failed acknowledgement.
            self.record["rollback_permitted"] = True
            raise OSError("uncertain acknowledgement")


@pytest.mark.asyncio
async def test_uncertain_journal_write_reloads_disk_before_recovery(tmp_path):
    port, journal = setup(tmp_path, journal_class=UncertainWriteJournal)
    with pytest.raises(OSError):
        await deploy(port, journal)
    assert "restore_separate" not in port.calls
    assert "activate_candidate" not in port.calls
    assert journal.load()["rollback_permitted"] is False


def interrupted(port, journal):
    journal.save("migrating", identity=port.identity, rollback_permitted=True,
                 snapshot_verified=True, snapshot=asdict(port.backup))


@pytest.mark.asyncio
async def test_recovery_after_interruption_rechecks_freeze_and_data(tmp_path):
    port, journal = setup(tmp_path)
    interrupted(port, journal)
    assert await recover(port, journal) == "restored_previous"
    assert port.calls[:4] == ["validate", "freeze", "assert_frozen", "assert_unchanged"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["assert_unchanged", "restore_separate",
                                    "validate_previous", "activate_previous"])
async def test_failed_recovery_stays_halted(tmp_path, failure):
    port, journal = setup(tmp_path, failure)
    interrupted(port, journal)
    with pytest.raises(RuntimeError):
        await recover(port, journal)
    assert port.calls[-2:] == ["halt", "close"]
    assert journal.load()["phase"] == "recovery_required"


@pytest.mark.asyncio
async def test_changed_identity_refuses_recovery(tmp_path):
    port, journal = setup(tmp_path)
    interrupted(port, journal)
    port.identity = {**port.identity, "candidate": "different"}
    with pytest.raises(RuntimeError, match="identity changed"):
        await recover(port, journal)
    assert "restore_separate" not in port.calls


@pytest.mark.asyncio
async def test_corrupt_snapshot_refuses_recovery(tmp_path):
    port, journal = setup(tmp_path)
    interrupted(port, journal)
    Path(port.backup.path).write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="checksum"):
        await recover(port, journal)
    assert "restore_separate" not in port.calls


@pytest.mark.asyncio
async def test_existing_journal_requires_explicit_recovery(tmp_path):
    port, journal = setup(tmp_path)
    interrupted(port, journal)
    with pytest.raises(RuntimeError, match="explicit recovery"):
        await deploy(port, journal)
    assert port.calls == ["close"]


@pytest.mark.asyncio
async def test_recovery_forbidden_after_admission(tmp_path):
    port, journal = setup(tmp_path)
    await deploy(port, journal)
    port.calls.clear()
    with pytest.raises(RuntimeError, match="restore forbidden"):
        await recover(port, journal)
    assert port.calls == ["close"]


@pytest.mark.asyncio
async def test_existing_installer_lock_is_never_removed(tmp_path):
    port, journal = setup(tmp_path)
    lock = journal.path.parent / "deploy.lock"
    lock.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        await deploy(port, journal)
    assert lock.is_dir()
    assert not port.calls


@pytest.mark.asyncio
async def test_validation_failure_closes_resources_and_releases_lock(tmp_path):
    port, journal = setup(tmp_path, "validate")
    with pytest.raises(RuntimeError):
        await deploy(port, journal)
    assert port.calls == ["validate", "close"]
    assert not (journal.path.parent / "deploy.lock").exists()
    assert not journal.path.exists()
