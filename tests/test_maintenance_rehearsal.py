"""Boundary checks for the exact-release rehearsal driver (no Docker here)."""

import os
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

from scripts import run_maintenance_rehearsal as driver


async def test_environment_overrides_cannot_redirect_the_drill(monkeypatch):
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/production/venv")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production")
    monkeypatch.setenv("GIT_DIR", "/production/repository")

    async def inner(previous):
        assert previous == driver.PREVIOUS_RELEASE
        assert not {"UV_PROJECT_ENVIRONMENT", "DATABASE_URL", "GIT_DIR"} & os.environ.keys()
        return {"ok": True}

    monkeypatch.setattr(driver, "_rehearse", inner)
    assert await driver.rehearse(driver.PREVIOUS_RELEASE) == {"ok": True}
    assert os.environ["UV_PROJECT_ENVIRONMENT"] == "/production/venv"


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
async def test_uncommitted_runtime_fails_before_preparation(monkeypatch, dirty):
    calls = []

    def run(command):
        calls.append(command)
        if command[1] == "diff" and dirty == "tracked":
            raise RuntimeError("dirty runtime")
        return b"bot/untracked.py" if command[1] == "ls-files" else b""

    monkeypatch.setattr(driver, "run", run)
    with pytest.raises(RuntimeError):
        await driver.rehearse(driver.PREVIOUS_RELEASE)
    assert all(command[0] == "git" for command in calls)


@pytest.mark.parametrize("host,major,container", [
    ("production.invalid", 16, "dailyplanner-maintenance-rehearsal-test"),
    ("127.0.0.1", 17, "dailyplanner-maintenance-rehearsal-test"),
    ("127.0.0.1", 16, "unrelated-container"),
])
async def test_database_client_refuses_outside_disposable_boundary(host, major, container):
    simulator = driver.Simulator(container, None, "success")
    with pytest.raises(RuntimeError, match="boundary"):
        await simulator.client("pg_dump", make_url(f"postgresql://app@{host}/test"), [],
                               server_major=major)


async def test_simulated_admission_requires_durable_prohibition():
    port = SimpleNamespace(journal=SimpleNamespace(load=lambda: {"rollback_permitted": True}))
    simulator = driver.Simulator("unused", port, "success")
    with pytest.raises(RuntimeError, match="Admission"):
        await simulator.launchctl("enable")
    assert simulator.admissions == 0


def test_rehearsal_is_required_by_reusable_ci_and_preserves_evidence():
    ci = yaml.safe_load((driver.ROOT / ".github/workflows/ci.yml").read_text())
    steps = ci["jobs"]["migration-rollback"]["steps"]
    assert any("scripts.run_maintenance_rehearsal" in step.get("run", "") for step in steps)
    assert any("maintenance-rehearsal.json" in step.get("with", {}).get("path", "") for step in steps)
