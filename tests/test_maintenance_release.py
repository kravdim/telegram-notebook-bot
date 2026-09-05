"""Prepared source verification against real disposable Git objects."""

import os
import subprocess
from pathlib import Path

import pytest

from bot.operations import maintenance_release as module


@pytest.fixture
def release(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repository)

    git("init", "-q")
    (repository / "bot").mkdir()
    (repository / "bot/main.py").write_text("print('trusted source')\n")
    (repository / "uv.lock").write_text("synthetic test lock\n")
    git("add", ".")
    git("-c", "user.name=Maintenance test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "synthetic release")
    sha = git("rev-parse", "HEAD").decode().strip()
    directory = tmp_path.resolve() / sha
    directory.mkdir()
    (directory / "bot").mkdir()
    for name in ("bot/main.py", "uv.lock"):
        (directory / name).write_bytes((repository / name).read_bytes())
    (directory / ".env").write_text("synthetic private configuration")
    (directory / ".env").chmod(0o600)
    (directory / "config.yaml").write_text("synthetic config")
    (directory / ".venv/bin").mkdir(parents=True)
    (directory / ".venv/bin/python").write_bytes(b"synthetic interpreter")
    native = module.run
    calls = []

    async def run(command, cwd, env, timeout=120):
        calls.append(command)
        if command[0] == "uv":
            assert "--check" in command and "--frozen" in command and "--offline" in command
            return b""
        return await native(command, cwd, env, timeout)

    monkeypatch.setattr(module, "run", run)
    return module.Release(repository, directory, sha), calls


async def test_release_matches_real_git_objects_and_private_config(release):
    artifact, calls = release
    result = await artifact.verify(dict(os.environ))
    assert set(result) == {"source", "config", "dotenv", "lock", "python"}
    assert all(len(value) == 64 for value in result.values())
    assert "synthetic private" not in str(result)
    assert calls[-1][0] == "uv"


@pytest.mark.parametrize("change", ["source", "extra", "mode", "symlink", "dotenv"])
async def test_tampered_release_refused_before_dependency_check(release, change):
    artifact, calls = release
    source = artifact.directory / "bot/main.py"
    if change == "source":
        source.write_text("changed source")
    elif change == "extra":
        (artifact.directory / "bot/extra.py").write_text("unexpected module")
    elif change == "mode":
        source.chmod(0o755)
    elif change == "symlink":
        source.unlink()
        source.symlink_to(artifact.repository / "bot/main.py")
    else:
        (artifact.directory / ".env").chmod(0o644)
    with pytest.raises(RuntimeError):
        await artifact.verify(dict(os.environ))
    assert all(command[0] != "uv" for command in calls)


async def test_configuration_change_changes_identity(release):
    artifact, _ = release
    before = await artifact.verify(dict(os.environ))
    (artifact.directory / "config.yaml").write_text("new configuration")
    after = await artifact.verify(dict(os.environ))
    assert before["config"] != after["config"]
    assert before["source"] == after["source"]


async def test_runtime_command_uses_selected_python_and_fresh_cache_prefix(release, monkeypatch):
    artifact, _ = release
    calls = []

    async def run(command, cwd, env, timeout=120):
        calls.append((command, cwd, env))
        return b""

    monkeypatch.setattr(module, "run", run)
    await artifact.command(["scripts/preflight.py"], {"PYTHONPATH": "wrong"})
    await artifact.command(["scripts/preflight.py"], {})
    assert calls[0][0][0] == str(artifact.directory / ".venv/bin/python")
    assert calls[0][2]["PYTHONPATH"] == str(artifact.directory)
    assert calls[0][2]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert calls[0][2]["PYTHONPYCACHEPREFIX"] != calls[1][2]["PYTHONPYCACHEPREFIX"]
    assert not Path(calls[0][2]["PYTHONPYCACHEPREFIX"]).exists()
