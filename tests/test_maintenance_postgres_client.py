"""Fail-closed local PostgreSQL client invocation, no database access."""

import asyncio

import pytest
from sqlalchemy.engine import make_url

from bot.operations import maintenance_postgres as pg


@pytest.mark.parametrize("url", [
    "sqlite:///test.db", "postgresql:///test", "postgresql://host/db",
    "postgresql://user@host/db?service=unexpected",
])
def test_implicit_connection_targets_are_refused(url):
    with pytest.raises(ValueError):
        pg.database_url(url)


def test_identity_excludes_secrets_and_normalizes_driver_and_port():
    first = make_url("postgresql://app:first-secret@localhost/db")
    second = make_url("postgresql+asyncpg://app:second-secret@localhost:5432/db")
    assert pg.identity(first) == pg.identity(second)
    assert pg.identity(first) != pg.identity(first.set(database="other"))
    assert "secret" not in pg.identity(first)


def test_cross_server_restore_operator_rejected(tmp_path):
    with pytest.raises(ValueError, match="same server"):
        pg.MaintenancePostgres("postgresql://app@localhost/db",
                               "postgresql://op@other/db", tmp_path)


class Process:
    def __init__(self, *, code=0, version=b"pg_dump (PostgreSQL) 16.4", failure=None):
        self.returncode = None
        self.code = code
        self.version = version
        self.failure = failure
        self.killed = False

    async def communicate(self):
        await self.wait()
        return self.version, None

    async def wait(self):
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        self.returncode = self.code
        return self.code

    def kill(self):
        self.killed = True


def runner(monkeypatch, probe=None, process=None):
    calls = []
    processes = [probe or Process(), process or Process()]

    async def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return processes[len(calls) - 1]

    monkeypatch.setattr(pg, "client", lambda name: "/trusted/" + name)
    monkeypatch.setattr(pg.asyncio, "create_subprocess_exec", launch)
    return calls


async def test_password_only_in_environment_and_no_inherited_libpq_target(monkeypatch):
    calls = runner(monkeypatch)
    monkeypatch.setenv("PGSERVICE", "unexpected")
    monkeypatch.setenv("PGHOST", "other-host")
    await pg.run_client("pg_dump", make_url("postgresql://app:secret@localhost/db"),
                        ["-d", "db"], server_major=16)
    args, kwargs = calls[1]
    assert "secret" not in str(args)
    assert kwargs["env"]["PGPASSWORD"] == "secret"
    assert "PGSERVICE" not in kwargs["env"] and "PGHOST" not in kwargs["env"]
    assert "--no-password" in args


async def test_wrong_major_refuses_before_connecting(monkeypatch):
    calls = runner(monkeypatch, probe=Process(version=b"pg_dump (PostgreSQL) 17.2"))
    with pytest.raises(RuntimeError, match="must match"):
        await pg.run_client("pg_dump", make_url("postgresql://app@localhost/db"),
                            ["-d", "db"], server_major=16)
    assert len(calls) == 1


async def test_subprocess_failure_does_not_expose_credentials(monkeypatch):
    runner(monkeypatch, process=Process(code=1))
    with pytest.raises(RuntimeError, match="service must stay stopped") as error:
        await pg.run_client("pg_restore", make_url("postgresql://app:secret@localhost/db"),
                            ["-d", "db"], server_major=16)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("probe_failure", [False, True])
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_timeout_or_cancellation_kills_and_reaps_child(monkeypatch, probe_failure, failure):
    process = Process(failure=failure())
    runner(monkeypatch, **({"probe": process} if probe_failure else {"process": process}))
    with pytest.raises(failure):
        await pg.run_client("pg_dump", make_url("postgresql://app@localhost/db"),
                            ["-d", "db"], server_major=16)
    assert process.killed
    assert process.returncode is not None
