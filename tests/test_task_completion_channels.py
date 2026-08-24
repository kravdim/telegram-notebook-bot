from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

import bot.handlers.commands as commands
from bot.llm import dispatcher
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


def _completion(task, next_date=None):
    return SimpleNamespace(
        task=task,
        completed=True,
        next_task=SimpleNamespace() if next_date else None,
        next_date=next_date,
    )


@pytest.mark.asyncio
async def test_done_command_uses_completion_service(monkeypatch):
    task = SimpleNamespace(id=uuid4(), title="Ежедневная зарядка")
    calls = []

    async def search(session, user_id, query, status=None):
        assert status == "open"
        return [task]

    async def get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def complete(session, task_id, user_id, timezone):
        calls.append((task_id, user_id, timezone))
        return _completion(task, date(2026, 8, 25))

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "search_tasks", search)
    monkeypatch.setattr(commands, "get_user", get_user)
    monkeypatch.setattr(commands, "complete_task_workflow", complete)
    msg = FakeMessage("/done Ежедневная зарядка", user_id=42)

    await commands.cmd_done(msg, SimpleNamespace(args="Ежедневная зарядка"))

    assert calls == [(task.id, 42, "Europe/Moscow")]
    assert "Следующая: 25.08" in msg.answers[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "prefix"),
    [(commands.cb_task_done, "task_done"), (commands.cb_frog_done, "frog_done")],
)
async def test_task_and_frog_buttons_use_completion_service(
    monkeypatch, handler, prefix
):
    task = SimpleNamespace(id=uuid4(), title="Повторяющаяся задача")
    calls = []

    async def get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def complete(session, task_id, user_id, timezone):
        calls.append((task_id, user_id, timezone))
        return _completion(task, date(2026, 8, 25))

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user", get_user)
    monkeypatch.setattr(commands, "complete_task_workflow", complete)
    callback = FakeCallback(user_id=42)
    callback.data = f"{prefix}:{task.id}"

    await handler(callback)

    assert calls == [(task.id, 42, "Europe/Moscow")]
    assert "Следующая: 25.08" in callback.message.edits[-1][0]


@pytest.mark.asyncio
async def test_llm_completion_uses_completion_service(monkeypatch):
    task = SimpleNamespace(id=uuid4(), title="Ежедневная зарядка", status="open")
    calls = []

    async def search(session, user_id, query, status=None):
        return [task]

    async def complete(session, task_id, user_id, timezone):
        calls.append((task_id, user_id, timezone))
        return _completion(task, date(2026, 8, 25))

    async def similar(*args, **kwargs):
        return 1, None

    async def planner(*args, **kwargs):
        return "Осталось на сегодня: 1"

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", search)
    monkeypatch.setattr(dispatcher, "complete_task_workflow", complete)
    monkeypatch.setattr(dispatcher, "count_similar_completed", similar)
    monkeypatch.setattr(dispatcher, "_format_today_planner_state", planner)

    result = await dispatcher._handle_complete_task(
        42, {"search_query": "Ежедневная зарядка"}, "Europe/Moscow"
    )

    assert calls == [(task.id, 42, "Europe/Moscow")]
    assert "Следующая: 25.08" in result
