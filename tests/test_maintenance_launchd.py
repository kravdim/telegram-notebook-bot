"""launchd failure injection: no real services or processes are started/stopped."""

import asyncio
import json
import os
import plistlib
import time
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from bot.operations import maintenance_launchd as module
from bot.operations.maintenance import MaintenanceJournal
from bot.operations.maintenance_postgres import identity

SHA = "a" * 40
PREVIOUS = "b" * 40
SOURCE = "postgresql+asyncpg://app:synthetic-secret@localhost/source"


class Lease:
    def __init__(self):
        self.held = False
        self.calls = []

    async def acquire(self, timeout=30):
        self.held = True
        self.calls.append("acquire")

    async def assert_exclusive(self):
        self.calls.append("assert")
        if not self.held:
            raise RuntimeError("lease not held")

    async def release(self):
        self.held = False
        self.calls.append("release")


class Launchctl:
    def __init__(self, controller):
        self.controller = controller
        self.disabled = False
        self.loaded = True
        self.calls = []
        self.failure = None
        self.heartbeat = True
        self.disabled_output = None
        self.admissions = 0
        self.uncertain_bootstrap = False

    async def __call__(self, *args):
        self.calls.append(args)
        command = args[0]
        if command == self.failure:
            return 5, "synthetic-secret must never be printed"
        if command == "disable":
            self.disabled = True
        elif command == "print-disabled":
            value = "disabled" if self.disabled else "enabled"
            return 0, self.disabled_output or f'\tdisabled services = {{\n "com.notebook-bot" => {value}\n}}'
        elif command == "bootout":
            if not self.loaded:
                return 113, ""
            self.loaded = False
        elif command == "print":
            if args[1] == self.controller.target and not self.loaded:
                return 113, ""
        elif command == "enable":
            self.admissions += 1
            assert not self.controller.lease.held
            assert self.controller.journal.load()["rollback_permitted"] is False
            self.disabled = False
        elif command == "bootstrap":
            assert not self.disabled
            self.loaded = True
            if self.uncertain_bootstrap:
                return 5, "load succeeded, acknowledgement lost"
            if self.heartbeat:
                payload = plistlib.loads(self.controller.plist.read_bytes())
                env = payload["EnvironmentVariables"]
                Path(env["READINESS_FILE"]).write_text(json.dumps({
                    "pid": os.getpid(), "ready": True, "heartbeat_epoch": time.time(),
                    "release_sha": env["DAILYPLANNER_RELEASE_SHA"],
                }))
        return 0, ""


@pytest.fixture
def service(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    release = tmp_path / SHA
    (release / ".venv/bin").mkdir(parents=True)
    (release / ".venv/bin/python").touch()
    plist = tmp_path / "LaunchAgents/com.notebook-bot.plist"
    module.atomic_private(plist, plistlib.dumps({
        "Label": module.LABEL, "WorkingDirectory": str(tmp_path / PREVIOUS),
        "ProgramArguments": [str(tmp_path / PREVIOUS / "platform/macos/run.sh")],
        "EnvironmentVariables": {"HTTP_PROXY": "http://localhost:8080"},
    }))
    journal = MaintenanceJournal(tmp_path / "state/maintenance.json")
    journal.save("snapshot_verified", rollback_permitted=True, snapshot_verified=True,
                 identity={"candidate": SHA, "previous": PREVIOUS,
                           "database": identity(make_url(SOURCE)), "source_database": "source"})
    lease = Lease()
    controller = module.MaintenanceLaunchd(plist, journal, lease)
    fake = Launchctl(controller)
    monkeypatch.setattr(module, "launchctl", fake)
    return controller, fake, release


def admit(controller, release="candidate", database=None):
    controller.journal.save("admission_started", rollback_permitted=False,
                            activating_release=release, activating_database=database)


async def test_freeze_disables_before_removal_and_acquires_live_lease(service):
    controller, fake, _ = service
    await controller.freeze()
    assert fake.disabled and not fake.loaded
    assert [call[0] for call in fake.calls[:3]] == ["disable", "print-disabled", "bootout"]
    assert controller.lease.held
    await controller.freeze()  # Explicit recovery can encounter an absent job.


@pytest.mark.parametrize("failure", ["disable", "print-disabled", "bootout", "print"])
async def test_failed_stop_never_acquires_writer_lease(service, failure):
    controller, fake, _ = service
    fake.failure = failure
    with pytest.raises(RuntimeError) as error:
        await controller.freeze()
    assert "synthetic-secret" not in str(error.value)
    assert not controller.lease.held
    assert fake.admissions == 0


@pytest.mark.parametrize("output", [
    '"com.other" => disabled', '"com.notebook-bot" => enabled',
    '"com.notebook-bot" => disabled\n"com.notebook-bot" => enabled',
])
async def test_unrecognized_disabled_state_fails_closed(service, output):
    controller, fake, _ = service
    fake.disabled_output = output
    with pytest.raises(RuntimeError, match="disable state"):
        await controller.freeze()
    assert not controller.lease.held


async def test_candidate_activation_uses_private_plist_without_run_sh(service):
    controller, fake, release = service
    await controller.freeze()
    admit(controller)
    await controller.activate(release, SHA, SOURCE)
    payload = plistlib.loads(controller.plist.read_bytes())
    assert payload["ProgramArguments"] == [str(release / ".venv/bin/python"), "-m", "bot.main"]
    assert payload["EnvironmentVariables"]["DATABASE_URL"] == SOURCE
    assert payload["EnvironmentVariables"]["HTTP_PROXY"] == "http://localhost:8080"
    assert controller.plist.stat().st_mode & 0o777 == 0o600
    assert fake.loaded and not fake.disabled
    assert (controller.journal.path.parent / "current-release").read_text().strip() == SHA


async def test_previous_runtime_targets_restored_database_without_editing_env(service):
    controller, fake, release = service
    previous = release.parent / PREVIOUS
    (previous / ".venv/bin").mkdir(parents=True)
    (previous / ".venv/bin/python").touch()
    env_file = previous / ".env"
    env_file.write_text("unchanged shared config")
    await controller.freeze()
    admit(controller, "previous", "restored")
    await controller.activate(previous, PREVIOUS, SOURCE.replace("/source", "/restored"))
    assert env_file.read_text() == "unchanged shared config"
    assert fake.loaded


async def test_activation_without_durable_admission_is_forbidden(service):
    controller, fake, release = service
    await controller.freeze()
    with pytest.raises(RuntimeError, match="durable admission"):
        await controller.activate(release, SHA, SOURCE)
    assert fake.admissions == 0 and fake.disabled


@pytest.mark.parametrize("url,sha", [
    (SOURCE.replace("/source", "/wrong"), SHA),
    (SOURCE.replace("localhost", "other"), SHA),
    (SOURCE, PREVIOUS),
])
async def test_activation_is_bound_to_journal_release_and_database(service, url, sha):
    controller, fake, release = service
    await controller.freeze()
    admit(controller)
    with pytest.raises(RuntimeError, match="identity"):
        await controller.activate(release, sha, url)
    assert fake.admissions == 0


@pytest.mark.parametrize("failure", ["enable", "bootstrap", "readiness"])
async def test_uncertain_activation_stops_without_snapshot_rollback(service, failure):
    controller, fake, release = service
    await controller.freeze()
    admit(controller)
    fake.failure = failure
    fake.heartbeat = False
    with pytest.raises(RuntimeError):
        await controller.activate(release, SHA, SOURCE, timeout=0)
    assert fake.disabled and not fake.loaded
    assert controller.journal.load()["rollback_permitted"] is False
    assert not (controller.journal.path.parent / "current-release").exists()


async def test_reenabled_service_or_lost_lease_invalidates_freeze(service):
    controller, fake, _ = service
    await controller.freeze()
    fake.disabled = False
    with pytest.raises(RuntimeError):
        await controller.assert_frozen()
    fake.disabled = True
    controller.lease.held = False
    with pytest.raises(RuntimeError, match="lease"):
        await controller.assert_frozen()


async def test_foreign_plist_is_not_touched(service):
    controller, fake, _ = service
    module.atomic_private(controller.plist, plistlib.dumps({"Label": "com.other"}))
    with pytest.raises(RuntimeError, match="identity"):
        await controller.freeze()
    assert fake.calls == []


async def test_bootstrap_started_but_acknowledgement_failed_is_halted(service):
    controller, fake, release = service
    await controller.freeze()
    admit(controller)
    fake.uncertain_bootstrap = True
    with pytest.raises(RuntimeError, match="bootstrap"):
        await controller.activate(release, SHA, SOURCE)
    assert fake.disabled and not fake.loaded
    assert controller.journal.load()["rollback_permitted"] is False


async def test_plist_replace_failure_after_disk_write_stays_disabled(service, monkeypatch):
    controller, fake, release = service
    await controller.freeze()
    admit(controller)
    original = module.atomic_private

    def uncertain_write(path, content):
        original(path, content)
        raise OSError("fsync acknowledgement lost")

    monkeypatch.setattr(module, "atomic_private", uncertain_write)
    with pytest.raises(OSError):
        await controller.activate(release, SHA, SOURCE)
    assert fake.disabled and not fake.loaded
    assert fake.admissions == 0


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_native_launchctl_timeout_and_cancellation_reap_process(monkeypatch, failure):
    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            raise failure()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        assert args == ("/bin/launchctl", "print-disabled", f"gui/{os.getuid()}")
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(failure):
        await module.launchctl("print-disabled", f"gui/{os.getuid()}")
    assert process.killed and process.returncode == -9
