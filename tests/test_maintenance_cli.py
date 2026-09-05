"""CLI plans do not authorize writes and confirmations bind operation + inputs."""

from types import SimpleNamespace

import pytest

from scripts import maintenance_deploy as cli


@pytest.fixture
def planned(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:synthetic@localhost/source")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "postgresql://op:synthetic@localhost/postgres")
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    calls = []

    class Port:
        def __init__(self, *args):
            self.postgres = args[2]

        async def validate(self):
            calls.append("validate")
            return {"candidate": "a" * 40, "database": "synthetic identity"}

        async def close(self):
            calls.append("close")
            await self.postgres.close()

    async def deploy(*args):
        calls.append("deploy")
        return "deployed"

    async def recover(*args):
        calls.append("recover")
        return "restored_previous"

    monkeypatch.setattr(cli, "MacMaintenance", Port)
    monkeypatch.setattr(cli, "deploy", deploy)
    monkeypatch.setattr(cli, "recover", recover)
    args = cli.parser().parse_args([
        "--repository", str(tmp_path), "--release-root", str(tmp_path / "releases"),
        "--previous", "b" * 40, "--candidate", "a" * 40,
        "--plist", str(tmp_path / "bot.plist"), "--state-dir", str(tmp_path / "state"),
    ])
    return args, calls


async def test_default_plan_never_creates_state_or_invokes_deploy(planned):
    args, calls = planned
    plan = await cli.execute(args)
    assert plan["status"] == "plan_only" and plan["production_changed"] is False
    assert calls == ["validate", "close"]
    assert not args.state_dir.exists()
    assert "synthetic@" not in str(plan)


async def test_confirmation_is_required_and_operation_bound(planned):
    args, calls = planned
    plan = await cli.execute(args)
    args.execute = True
    with pytest.raises(RuntimeError, match="confirmation"):
        await cli.execute(args)
    args.confirm = plan["confirmation"]
    args.recover = True
    with pytest.raises(RuntimeError, match="does not match"):
        await cli.execute(args)
    assert "deploy" not in calls and "recover" not in calls
    args.recover = False
    assert (await cli.execute(args))["status"] == "deployed"
    assert calls.count("deploy") == 1


async def test_execute_refuses_non_macos(planned, monkeypatch):
    args, calls = planned
    args.execute, args.confirm = True, "MAINTENANCE-test"
    monkeypatch.setattr(cli.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        await cli.execute(args)
    assert calls == []


@pytest.mark.parametrize("status,exit_code", [("plan_only", 0), ("deployed", 0), ("restored_previous", 2)])
def test_cli_distinguishes_restored_previous_from_success(planned, monkeypatch, capsys, status, exit_code):
    args, _ = planned
    monkeypatch.setattr(cli, "parser", lambda: SimpleNamespace(parse_args=lambda: args))

    async def execute(args):
        return {"status": status}

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main() == exit_code
    assert status in capsys.readouterr().out


@pytest.mark.parametrize("safe", [False, True])
def test_cli_only_prints_explicitly_safe_diagnostics(planned, monkeypatch, capsys, safe):
    args, _ = planned
    monkeypatch.setattr(cli, "parser", lambda: SimpleNamespace(parse_args=lambda: args))

    async def execute(args):
        if safe:
            raise cli.MaintenanceError("Release dotenv must be private (0600)")
        raise RuntimeError("connection failed: password=synthetic-secret")

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    assert ("dotenv must be private" in output) == safe
