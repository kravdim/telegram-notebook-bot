import pytest

import bot.handlers.messages as messages
import bot.handlers.voice as voice
from tests.fakes import FakeCallback, FakeMessage


@pytest.fixture(autouse=True)
def clear_voice_state(monkeypatch):
    async def no_persist(*args, **kwargs):
        return None

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
    voice._pending_transcripts[42] = "Купить кофе завтра"

    callback = FakeCallback(user_id=42)
    await voice.cb_voice_confirm(callback)

    assert callback.message.edits[0][0].startswith("🎤 Купить кофе завтра")
    assert "Выполняю" in callback.message.edits[0][0]
    assert processed == [(42, "Купить кофе завтра", callback.message)]
    assert 42 not in voice._pending_transcripts


@pytest.mark.asyncio
async def test_voice_confirm_expired_session():
    callback = FakeCallback(user_id=42)
    await voice.cb_voice_confirm(callback)

    assert callback.message.edits[0][0] == "Сессия истекла. Отправь голосовое ещё раз."


@pytest.mark.asyncio
async def test_voice_edit_marks_next_text_as_correction():
    callback = FakeCallback(user_id=42, message=FakeMessage(user_id=42))
    voice._pending_transcripts[42] = "старый текст"

    await voice.cb_voice_edit(callback)

    assert 42 in voice._awaiting_edit
    assert "Введи исправленный текст" in callback.message.edits[0][0]
    assert voice.consume_voice_edit(42) is True
    assert 42 not in voice._pending_transcripts
