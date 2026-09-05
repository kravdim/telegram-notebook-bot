"""Exact-source maintenance rehearsal in disposable PostgreSQL; launchd is simulated."""

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import tempfile
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

from sqlalchemy import text

from bot.operations.maintenance import MaintenanceJournal, deploy
from bot.operations.maintenance_deploy import MacMaintenance
from bot.operations.maintenance_launchd import LABEL, atomic_private
from bot.operations.maintenance_postgres import MaintenancePostgres
from bot.operations.maintenance_release import Release
from scripts.run_migration_rollback_drill import (
    IMAGE,
    PREVIOUS_RELEASE,
    ROOT,
    export_previous,
    run,
    wait_database,
)

SCENARIOS = ("success", "migration", "validation", "new_data", "activation")
SEED = """
import asyncio
from bot.db.engine import engine, async_session
from bot.db.models import User, Task
async def main():
    async with async_session() as session:
        session.add(User(telegram_id=8290000001, username='maintenance-rehearsal'))
        await session.flush()
        session.add(Task(user_id=8290000001, title='baseline task'))
        await session.commit()
    await engine.dispose()
asyncio.run(main())
"""


def provision(container: str) -> None:
    run(["docker", "exec", "-i", container, "psql", "-U", "drill", "-d", "postgres",
         "-v", "ON_ERROR_STOP=1"], data=b"""
CREATE ROLE rehearsal_app LOGIN PASSWORD 'synthetic-drill';
CREATE ROLE rehearsal_operator LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION PASSWORD 'synthetic-drill';
CREATE DATABASE dailyplanner_recovery_template OWNER rehearsal_operator;
""")
    run(["docker", "exec", "-i", container, "psql", "-U", "drill",
         "-d", "dailyplanner_recovery_template", "-v", "ON_ERROR_STOP=1"], data=b"""
BEGIN;
ALTER ROLE rehearsal_operator SUPERUSER;
SET ROLE rehearsal_operator;
CREATE EXTENSION vector;
CREATE EXTENSION pg_trgm;
CREATE EXTENSION pgcrypto;
RESET ROLE;
ALTER ROLE rehearsal_operator NOSUPERUSER;
COMMIT;
""")
    run(["docker", "exec", container, "psql", "-U", "drill", "-d", "postgres",
         "-v", "ON_ERROR_STOP=1", "-c",
         "ALTER DATABASE dailyplanner_recovery_template IS_TEMPLATE true ALLOW_CONNECTIONS false"])


class Simulator:
    def __init__(self, container: str, port: MacMaintenance, scenario: str):
        self.container, self.port, self.scenario = container, port, scenario
        self.loaded = True
        self.disabled = False
        self.admissions = 0
        self.injected = False

    async def launchctl(self, *args: str) -> tuple[int, str]:
        command = args[0]
        if command == "disable":
            self.disabled = True
        elif command == "print-disabled":
            state = "disabled" if self.disabled else "enabled"
            return 0, f'"{LABEL}" => {state}'
        elif command == "bootout":
            self.loaded = False
        elif command == "print" and args[1] == self.port.launchd.target:
            return (0 if self.loaded else 113), ""
        elif command == "enable":
            if self.port.journal.load()["rollback_permitted"] is not False:
                raise RuntimeError("Admission without durable rollback prohibition")
            self.admissions += 1
            self.disabled = False
        elif command == "bootstrap":
            self.loaded = True
            if self.scenario == "activation":
                self.injected = True
                return 5, ""
            env = plistlib.loads(self.port.launchd.plist.read_bytes())["EnvironmentVariables"]
            atomic_private(Path(env["READINESS_FILE"]), json.dumps({
                "pid": os.getpid(), "ready": True, "heartbeat_epoch": time.time(),
                "release_sha": env["DAILYPLANNER_RELEASE_SHA"],
            }).encode())
        return 0, ""

    async def client(self, name, url, arguments, timeout=300, *, server_major):
        if (server_major != 16 or url.host != "127.0.0.1"
                or not self.container.startswith("dailyplanner-maintenance-rehearsal-")):
            raise RuntimeError("Rehearsal client escaped its disposable boundary")
        arguments = list(arguments)
        with ExitStack() as stack:
            stdin: int | BinaryIO = asyncio.subprocess.DEVNULL
            stdout: int | BinaryIO = asyncio.subprocess.DEVNULL
            if name == "pg_dump":
                index = arguments.index("--file")
                stdout = stack.enter_context(open(arguments[index + 1], "wb"))
                del arguments[index:index + 2]
            elif name == "pg_restore":
                stdin = stack.enter_context(open(arguments.pop(), "rb"))
            process = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", "-e", "PGPASSWORD=synthetic-drill", self.container,
                name, "-h", "127.0.0.1", "-p", "5432", "-U", url.username, "--no-password",
                *arguments, stdin=stdin, stdout=stdout, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError(f"Rehearsal PostgreSQL client failed: {name}")


async def scenario(root: Path, previous: Release, candidate: Release, container: str,
                   database_port: str, mode: str) -> dict:
    database = "maintenance_case_" + mode
    run(["docker", "exec", container, "createdb", "-U", "drill", "--owner", "rehearsal_app",
         "--template", "dailyplanner_recovery_template", database])
    source = f"postgresql+asyncpg://rehearsal_app:synthetic-drill@127.0.0.1:{database_port}/{database}"
    operator = f"postgresql+asyncpg://rehearsal_operator:synthetic-drill@127.0.0.1:{database_port}/postgres"
    for release in (previous, candidate):
        atomic_private(release.directory / ".env", (
            f"DATABASE_URL={source}\nBOT_TOKEN=synthetic-drill\n"
            "MINIMAX_API_KEY=synthetic-drill\nALLOW_ALL_USERS=true\n"
        ).encode())
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    await previous.command(["-m", "alembic", "upgrade", "head"], environment)
    await previous.command(["-c", SEED], environment)
    journal = MaintenanceJournal(root / mode / "state/maintenance.json")
    plist = root / mode / "bot.plist"
    atomic_private(plist, plistlib.dumps({
        "Label": LABEL, "WorkingDirectory": str(previous.directory),
        "EnvironmentVariables": {"DAILYPLANNER_RELEASE_SHA": previous.sha,
                                 "READINESS_FILE": str(journal.path.parent / "old-ready.json")},
    }))
    postgres = MaintenancePostgres(source, operator, journal.path.parent / "backups")
    port = MacMaintenance(previous, candidate, postgres, plist, journal)
    simulator = Simulator(container, port, mode)
    native = Release.command
    invoked = []
    injected = []

    async def command(release, arguments, env):
        await native(release, arguments, env)
        if release.sha != candidate.sha:
            return
        phase = "migration" if arguments[:2] == ["-m", "alembic"] else "validation"
        invoked.append(phase)
        if phase == mode and (mode != "validation" or arguments == ["scripts/preflight.py"]):
            injected.append(mode)
            raise RuntimeError("Injected failure after real candidate command")
        if phase == "migration" and mode == "new_data":
            async with postgres.engine.begin() as connection:
                await connection.execute(text("UPDATE tasks SET title='post-snapshot canary'"))
            injected.append(mode)
            raise RuntimeError("Injected post-snapshot data")

    with patch("bot.operations.maintenance_launchd.launchctl", simulator.launchctl), \
            patch("bot.operations.maintenance_postgres.run_client", simulator.client), \
            patch.object(Release, "command", command):
        try:
            outcome = await deploy(port, journal)
        except RuntimeError:
            if mode not in {"new_data", "activation"} or not journal.path.exists():
                raise
            outcome = "recovery_required"
    expected = {"success": "deployed", "migration": "restored_previous",
                "validation": "restored_previous", "new_data": "recovery_required",
                "activation": "recovery_required"}[mode]
    record = journal.load()
    if outcome != expected or not invoked:
        raise RuntimeError("Exact-release rehearsal outcome differs from expected")
    if mode != "success" and not injected and not simulator.injected:
        raise RuntimeError("Expected injection point was not reached")
    if simulator.admissions != (0 if mode == "new_data" else 1):
        raise RuntimeError("Unexpected admission count")
    if len(postgres.created_databases) != (2 if mode in {"migration", "validation"} else 1):
        raise RuntimeError("Unexpected restore count")
    if mode == "activation" and record["rollback_permitted"] is not False:
        raise RuntimeError("Uncertain admission did not prohibit snapshot rollback")
    if mode in {"new_data", "activation"} and (simulator.loaded or not simulator.disabled):
        raise RuntimeError("Failed admission did not remain stopped")
    async with postgres.engine.connect() as connection:
        title = await connection.scalar(text("SELECT title FROM tasks"))
        head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if title != ("post-snapshot canary" if mode == "new_data" else "baseline task"):
            raise RuntimeError("Source data was lost during rehearsal")
    await postgres.close()
    return {"scenario": mode, "status": outcome, "source_head": head,
            "source_data_preserved": True, "admissions": simulator.admissions,
            "snapshot_sha256": record["snapshot"]["sha256"],
            "rollback_permitted": record["rollback_permitted"],
            "restores": len(postgres.created_databases), "injection_reached": bool(injected or simulator.injected),
            "identity": {key: value for key, value in record["identity"].items()
                         if not key.endswith("_path") and key != "state"}}


async def _rehearse(previous_sha: str) -> dict:
    run(["git", "diff", "--exit-code", "HEAD", "--", "bot", "pyproject.toml", "uv.lock"])
    if run(["git", "ls-files", "--others", "--exclude-standard", "--", "bot"]).strip():
        raise RuntimeError("Commit runtime changes before exact-SHA rehearsal")
    candidate_sha = run(["git", "rev-parse", "HEAD"]).decode().strip()
    previous_sha = run(["git", "rev-parse", "--verify", "--end-of-options",
                        f"{previous_sha}^{{commit}}"]).decode().strip()
    if previous_sha == candidate_sha:
        raise RuntimeError("Rehearsal requires distinct previous and candidate releases")
    started_at = datetime.now(timezone.utc).isoformat()
    container = "dailyplanner-maintenance-rehearsal-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="dailyplanner-maintenance-rehearsal-") as temporary:
        root = Path(temporary).resolve()
        previous, candidate = [Release(ROOT, root / sha, sha) for sha in (previous_sha, candidate_sha)]
        for release in (previous, candidate):
            release.directory.mkdir()
            export_previous(release.sha, release.directory)
        started = False
        try:
            run(["docker", "run", "--rm", "-d", "--name", container,
                 "-e", "POSTGRES_USER=drill", "-e", "POSTGRES_PASSWORD=synthetic-drill",
                 "-p", "127.0.0.1::5432", IMAGE])
            started = True
            wait_database(container)
            provision(container)
            port = run(["docker", "port", container, "5432/tcp"]).decode().strip().rsplit(":", 1)[1]
            results = []
            for mode in SCENARIOS:
                results.append(await scenario(root, previous, candidate, container, port, mode))
            return {"ok": True, "previous_sha": previous_sha, "candidate_sha": candidate_sha,
                    "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
                    "scenarios": results, "production_touched": False, "launchd_simulated": True,
                    "telegram_polling_started": False, "release_commands_simulated": False,
                    "temporary_databases_and_snapshots_removed": True,
                    "export_helper_sha256": hashlib.sha256(
                        (ROOT / "scripts/run_migration_rollback_drill.py").read_bytes()
                    ).hexdigest(),
                    "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        finally:
            if started:
                run(["docker", "rm", "-f", container])


async def rehearse(previous_sha: str) -> dict:
    # Never let inherited UV_PROJECT_ENVIRONMENT, DATABASE_URL or Git overrides
    # redirect preparation/operations outside the disposable boundary.
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG")
                   if key in os.environ}
    with patch.dict(os.environ, environment, clear=True):
        return await _rehearse(previous_sha)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", default=PREVIOUS_RELEASE)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(rehearse(args.previous)), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
