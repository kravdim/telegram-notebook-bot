from types import SimpleNamespace
from uuid import uuid4

import pytest

import bot.handlers.callbacks as callbacks
from tests.fakes import FakeCallback, FakeSessionContext


@pytest.mark.asyncio
async def test_snooze_done_marks_reminder_and_task(monkeypatch):
    reminder_id = uuid4()
    task_id = uuid4()
    resolved = []
    completed = []

    async def fake_get_reminder(session, rid, user_id):
        assert rid == reminder_id
        assert user_id == 42
        return SimpleNamespace(id=reminder_id, task_id=task_id)

    async def fake_resolve(session, rid, user_id):
        resolved.append((rid, user_id))

    async def fake_complete_task(session, tid, user_id):
        completed.append((tid, user_id))
        return SimpleNamespace(title="Подключить кассу")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "get_reminder_by_id", fake_get_reminder)
    monkeypatch.setattr(callbacks, "resolve_reminder", fake_resolve)
    monkeypatch.setattr(callbacks, "complete_task_by_id", fake_complete_task)

    callback = FakeCallback(user_id=42)
    callback.data = f"snooze_done:{reminder_id}"
    await callbacks.cb_snooze_done(callback)

    assert resolved == [(reminder_id, 42)]
    assert completed == [(task_id, 42)]
    assert callback.message.edits[0][0] == "✅ Задача «Подключить кассу» выполнена!"


@pytest.mark.asyncio
async def test_snooze_limit_message(monkeypatch):
    reminder_id = uuid4()

    async def fake_snooze(session, rid, new_time, user_id):
        assert rid == reminder_id
        assert user_id == 42
        return SimpleNamespace(snooze_count=5, message="Позвонить")

    async def fake_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "snooze_reminder", fake_snooze)
    monkeypatch.setattr(callbacks, "get_user", fake_user)

    callback = FakeCallback(user_id=42)
    await callbacks._do_snooze(callback, str(reminder_id), minutes=30)

    text = callback.message.edits[0][0]
    assert "уже откладывалось 5 раз" in text
    assert "Позвонить" in text
@pytest.mark.asyncio
async def test_snooze_confirmation_shows_delay_and_user_timezone(monkeypatch):
    reminder_id = uuid4()

    async def fake_snooze(session, rid, new_time, user_id):
        return SimpleNamespace(snooze_count=1, message="Попить воды")

    async def fake_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "snooze_reminder", fake_snooze)
    monkeypatch.setattr(callbacks, "get_user", fake_user)

    callback = FakeCallback(user_id=42)
    await callbacks._do_snooze(callback, str(reminder_id), minutes=30)

    text = callback.message.edits[0][0]
    assert "Отложено на 30 минут" in text
    assert "Попить воды" in text


@pytest.mark.asyncio
async def test_delete_confirm_yes(monkeypatch):
    task_id = uuid4()
    deleted = []

    async def fake_delete_task(session, tid, user_id):
        deleted.append((tid, user_id))
        return True

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "delete_task", fake_delete_task)

    callback = FakeCallback(user_id=42)
    callback.data = f"task_delete_yes:{task_id}"
    await callbacks.cb_delete_yes(callback)

    assert deleted == [(task_id, 42)]
    assert callback.message.edits[0][0] == "🗑 Задача удалена."


@pytest.mark.asyncio
async def test_project_completion_is_scoped_to_callback_user(monkeypatch):
    project_id = uuid4()
    calls = []

    async def fake_complete(session, pid, user_id):
        calls.append((pid, user_id))
        return SimpleNamespace(title="Запуск продукта")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "complete_project_and_cancel_open_tasks", fake_complete)

    callback = FakeCallback(user_id=42)
    callback.data = f"project_complete_yes:{project_id}"
    await callbacks.cb_project_complete_yes(callback)

    assert calls == [(project_id, 42)]
    assert "оставшиеся задачи отменены" in callback.message.edits[0][0]
