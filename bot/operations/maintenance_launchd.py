"""Fail-closed launchd maintenance control; only this user's one bot label."""

import asyncio
import copy
import json
import os
import plistlib
import re
import tempfile
import time
from pathlib import Path
from typing import Protocol

from bot.operations.maintenance import MaintenanceJournal
from bot.operations.maintenance_postgres import database_url as parse_database_url
from bot.operations.maintenance_postgres import identity
from bot.runtime.readiness import validate_readiness_file

LABEL = "com.notebook-bot"


class WriterLease(Protocol):
    async def acquire(self, timeout: float = 30) -> None: ...
    async def assert_exclusive(self) -> None: ...
    async def release(self) -> None: ...


async def launchctl(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "/bin/launchctl", *arguments,
        env={**os.environ, "LC_ALL": "C"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, output.decode("utf-8", errors="strict")


def atomic_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".maintenance-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class MaintenanceLaunchd:
    def __init__(self, plist: Path, journal: MaintenanceJournal, lease: WriterLease):
        self.plist = plist
        self.journal = journal
        self.lease = lease
        self.domain = f"gui/{os.getuid()}"
        self.target = f"{self.domain}/{LABEL}"

    async def _command(self, *arguments: str) -> str:
        status, output = await launchctl(*arguments)
        if status:
            # Never include print's environment or a plist's database credentials.
            raise RuntimeError(f"launchctl {arguments[0]} failed (exit {status})")
        return output

    def installed(self) -> dict:
        if self.plist.is_symlink():
            raise RuntimeError("Installed LaunchAgent must not be a symlink")
        stat = self.plist.stat()
        if stat.st_uid != os.getuid() or stat.st_mode & 0o022:
            raise RuntimeError("Installed LaunchAgent ownership/permissions unsafe")
        payload = plistlib.loads(self.plist.read_bytes())
        if payload.get("Label") != LABEL or not payload.get("WorkingDirectory"):
            raise RuntimeError("Installed LaunchAgent identity mismatch")
        return payload

    async def _disabled(self) -> None:
        output = await self._command("print-disabled", self.domain)
        # Diagnostic output is not a stable API. Only the observed exact entry
        # is accepted; an OS format change must fail closed, never mean 'safe'.
        entries = re.findall(r'^\s*"com\.notebook-bot"\s*=>\s*(\w+)\s*$', output, re.M)
        if entries != ["disabled"] and entries != ["true"]:
            raise RuntimeError("Cannot confirm persistent launchd disable state")

    async def _absent(self) -> None:
        # Prove domain access separately: permission/domain failures are not an
        # absent service. Service-not-found on this exact target is the sole miss.
        await self._command("print", self.domain)
        status, _ = await launchctl("print", self.target)
        if status != 113:
            raise RuntimeError("Cannot confirm launchd service removal")

    async def halt(self) -> None:
        self.installed()  # Revalidate the exact target before any external write.
        await self._command("disable", self.target)
        await self._disabled()
        status, _ = await launchctl("bootout", self.target)
        if status not in {0, 113}:
            raise RuntimeError(f"launchctl bootout failed (exit {status})")
        await self._absent()

    async def freeze(self) -> None:
        await self.halt()
        await self.lease.acquire()
        await self.assert_frozen()

    async def assert_frozen(self) -> None:
        await self._disabled()
        await self._absent()
        await self.lease.assert_exclusive()

    def _activation_payload(self, release: Path, sha: str, database_url: str) -> tuple[dict, Path]:
        record = self.journal.load()
        if record.get("phase") != "admission_started" or record.get("rollback_permitted") is not False:
            raise RuntimeError("Launch requires durable admission boundary")
        selected = record.get("activating_release")
        expected = record.get("identity", {})
        if selected not in {"candidate", "previous"} or expected.get(selected) != sha:
            raise RuntimeError("Launch release differs from maintenance identity")
        url = parse_database_url(database_url)
        source_database = expected.get("source_database")
        target_database = (source_database if selected == "candidate"
                           else record.get("activating_database"))
        if (not source_database or not target_database or url.database != target_database
                or identity(url.set(database=source_database)) != expected.get("database")):
            raise RuntimeError("Launch database differs from maintenance identity")
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or release.name != sha:
            raise RuntimeError("Launch requires an exact versioned release directory")
        if (not release.is_absolute() or release.resolve() != release
                or not (release / ".venv/bin/python").is_file()):
            raise RuntimeError("Prepared release Python missing")
        payload = copy.deepcopy(self.installed())
        # Do not invoke run.sh: it migrates/seeds before the bot's singleton lock.
        payload.pop("Program", None)
        payload["ProgramArguments"] = [str(release / ".venv/bin/python"), "-m", "bot.main"]
        payload["WorkingDirectory"] = str(release)
        payload["RunAtLoad"] = True
        payload["KeepAlive"] = True
        payload.pop("Disabled", None)
        environment = payload.setdefault("EnvironmentVariables", {})
        # Each attempt has a unique heartbeat path, never accept an old heartbeat.
        readiness = self.journal.path.parent / f"readiness-{os.urandom(12).hex()}.json"
        environment.update(DATABASE_URL=database_url, PYTHONPATH=str(release),
                           DAILYPLANNER_RELEASE_SHA=sha, READINESS_FILE=str(readiness),
                           PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=str(readiness) + ".pycache")
        return payload, readiness

    async def activate(self, release: Path, sha: str, database_url: str, timeout: float = 90) -> None:
        await self.assert_frozen()
        payload, readiness = self._activation_payload(release, sha, database_url)
        try:
            atomic_private(self.plist, plistlib.dumps(payload))
            # Admission was persisted before changing the plist or releasing the
            # source lock. Any uncertainty from here is halt/manual reconciliation.
            await self.lease.release()
            await self._command("enable", self.target)
            await self._command("bootstrap", self.domain, str(self.plist))
            await self._wait_ready(readiness, sha, timeout)
            atomic_private(self.journal.path.parent / "current-release", (sha + "\n").encode())
        except BaseException:
            await self.halt()
            raise

    async def _wait_ready(self, readiness: Path, sha: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            await self._command("print", self.target)
            try:
                validate_readiness_file(readiness, 15, expected_release_sha=sha)
                return
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Maintenance runtime readiness timed out") from None
                await asyncio.sleep(0.2)

    async def close(self) -> None:
        await self.lease.release()
