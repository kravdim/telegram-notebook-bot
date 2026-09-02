import asyncio
from types import SimpleNamespace

import pytest

from bot.runtime import background


@pytest.mark.asyncio
async def test_periodic_job_survives_action_failure(monkeypatch, caplog):
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        raise RuntimeError("private details")

    async def stop_after_one_interval(_seconds):
        if calls:
            raise asyncio.CancelledError

    monkeypatch.setattr(background.asyncio, "sleep", stop_after_one_interval)

    with pytest.raises(asyncio.CancelledError):
        await background._run_periodic("fixture", 10, action)

    assert calls == 1
    assert "fixture loop error: error_type=RuntimeError" in caplog.text
    assert "private details" not in caplog.text


@pytest.mark.asyncio
async def test_maintenance_job_runs_immediately(monkeypatch):
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    async def unexpected_sleep(_seconds):
        raise AssertionError("immediate job must run before sleeping")

    monkeypatch.setattr(background.asyncio, "sleep", unexpected_sleep)

    with pytest.raises(asyncio.CancelledError):
        await background._run_periodic("maintenance", 3600, action, run_immediately=True)
    assert calls == 1


@pytest.mark.asyncio
async def test_health_action_checks_provider_then_slos(monkeypatch):
    calls = []
    bot = object()
    client = object()

    async def health(actual):
        calls.append(("health", actual))

    async def evaluate():
        calls.append(("evaluate", None))
        return {"ok": False}

    async def alert(actual_bot, result):
        calls.append(("alert", actual_bot, result))

    monkeypatch.setattr(background, "check_llm_health", health)
    monkeypatch.setattr(background, "evaluate_slos", evaluate)
    monkeypatch.setattr(background, "alert_slo_violations", alert)

    await background._health_action(bot, client)

    assert calls == [
        ("health", client),
        ("evaluate", None),
        ("alert", bot, {"ok": False}),
    ]


@pytest.mark.asyncio
async def test_start_and_stop_owns_all_background_tasks(monkeypatch):
    async def wait_forever(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(background, "_run_periodic", wait_forever)
    monkeypatch.setattr(background, "_warmup_stt", wait_forever)

    tasks = background.start_background_tasks(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
    assert {task.get_name() for task in tasks} == {
        "background:reminders",
        "background:reminder_sweep",
        "background:health",
        "background:digest",
        "background:memoir",
        "background:chronometry",
        "background:task_reminders",
        "background:weekly_review",
        "background:maintenance",
        "background:stt-warmup",
    }

    await background.stop_background_tasks(tasks)
    assert all(task.cancelled() for task in tasks)
