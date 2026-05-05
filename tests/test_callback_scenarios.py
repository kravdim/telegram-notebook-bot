from types import SimpleNamespace
from uuid import uuid4

import pytest

import bot.handlers.callbacks as callbacks
from tests.fakes import FakeCallback, FakeSessionContext


@pytest.mark.asyncio
async def test_snooze_done_marks_reminder_and_task(monkeypatch):
    reminder_id = uuid4()
    task_id = uuid4()
    marked = []
    completed = []

    async def fake_get_reminder(session, rid):
        assert rid == reminder_id
        return SimpleNamespace(id=reminder_id, task_id=task_id)

    async def fake_mark_sent(session, rid):
        marked.append(rid)

    async def fake_complete_task(session, tid, user_id):
        completed.append((tid, user_id))
        return SimpleNamespace(title="Подключить кассу")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "get_reminder_by_id", fake_get_reminder)
    monkeypatch.setattr(callbacks, "mark_sent", fake_mark_sent)
    monkeypatch.setattr(callbacks, "complete_task_by_id", fake_complete_task)

    callback = FakeCallback(user_id=42)
    callback.data = f"snooze_done:{reminder_id}"
    await callbacks.cb_snooze_done(callback)

    assert marked == [reminder_id]
    assert completed == [(task_id, 42)]
    assert callback.message.edits[0][0] == "✅ Задача «Подключить кассу» выполнена!"


@pytest.mark.asyncio
async def test_snooze_limit_message(monkeypatch):
    reminder_id = uuid4()

    async def fake_snooze(session, rid, new_time):
        assert rid == reminder_id
        return SimpleNamespace(snooze_count=5, message="Позвонить")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "snooze_reminder", fake_snooze)

    callback = FakeCallback(user_id=42)
    await callbacks._do_snooze(callback, str(reminder_id), minutes=30)

    text = callback.message.edits[0][0]
    assert "уже откладывалось 5 раз" in text
    assert "Позвонить" in text


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
