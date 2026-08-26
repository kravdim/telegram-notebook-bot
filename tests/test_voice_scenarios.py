import pytest

import bot.handlers.messages as messages
import bot.handlers.voice as voice
from tests.fakes import FakeCallback, FakeMessage


@pytest.fixture(autouse=True)
def clear_voice_state(monkeypatch):
    async def no_persist(*args, **kwargs):
        return True

    async def no_load(*args, **kwargs):
        return None

    monkeypatch.setattr(voice, "_persist_voice_state", no_persist)
    monkeypatch.setattr(voice, "_clear_voice_state", no_persist)
    monkeypatch.setattr(voice, "_load_voice_state", no_load)
    voice._pending_transcripts.clear()
    voice._awaiting_edit.clear()
    yield
    voice._pending_transcripts.clear()
    voice._awaiting_edit.clear()


@pytest.mark.asyncio
async def test_voice_confirm_processes_transcript(monkeypatch):
    processed = []

    async def fake_process_text_message(user_id, text, message):
        processed.append((user_id, text, message))

    monkeypatch.setattr(messages, "process_text_message", fake_process_text_message)
    async def load_state(user_id, state_type):
        return SimpleNamespace(
            state_type="voice_confirm",
            payload={
                "session_token": "session-b",
                "message_id": 999,
                "transcript": "Купить кофе завтра",
            },
        )

    from types import SimpleNamespace
    monkeypatch.setattr(voice, "_load_voice_state", load_state)

    callback = FakeCallback(user_id=42, data="voice_confirm:session-b")
    await voice.cb_voice_confirm(callback)

    assert callback.message.edits[0][0].startswith("🎤 Купить кофе завтра")
    assert "Выполняю" in callback.message.edits[0][0]
    assert processed == [(42, "Купить кофе завтра", callback.message)]
    assert 42 not in voice._pending_transcripts


@pytest.mark.asyncio
async def test_voice_confirm_expired_session():
    callback = FakeCallback(user_id=42, data="voice_confirm:expired")
    await voice.cb_voice_confirm(callback)

    assert callback.message.edits[0][0] == "Сессия истекла. Отправь голосовое ещё раз."


@pytest.mark.asyncio
async def test_voice_edit_marks_next_text_as_correction(monkeypatch):
    from types import SimpleNamespace

    async def load_state(user_id, state_type):
        return SimpleNamespace(
            state_type="voice_confirm",
            payload={
                "session_token": "session-b",
                "message_id": 999,
                "transcript": "старый текст",
            },
        )

    monkeypatch.setattr(voice, "_load_voice_state", load_state)
    callback = FakeCallback(
        user_id=42,
        message=FakeMessage(user_id=42),
        data="voice_edit:session-b",
    )

    await voice.cb_voice_edit(callback)

    assert 42 in voice._awaiting_edit
    assert "Введи исправленный текст" in callback.message.edits[0][0]
    assert voice.consume_voice_edit(42) is True


@pytest.mark.asyncio
async def test_old_voice_callback_cannot_confirm_new_session(monkeypatch):
    from types import SimpleNamespace

    processed = []

    async def load_new_state(user_id, state_type):
        return SimpleNamespace(
            state_type="voice_confirm",
            payload={
                "session_token": "session-b",
                "message_id": 1001,
                "transcript": "новая команда",
            },
        )

    async def forbidden_process(*args):
        processed.append(args)

    monkeypatch.setattr(voice, "_load_voice_state", load_new_state)
    monkeypatch.setattr(messages, "process_text_message", forbidden_process)
    old_message = FakeMessage(user_id=42)
    old_message.message_id = 999
    callback = FakeCallback(
        user_id=42, message=old_message, data="voice_confirm:session-a"
    )

    await voice.cb_voice_confirm(callback)

    assert processed == []
    assert callback.answered[0][0] == "Эта сессия уже устарела"
