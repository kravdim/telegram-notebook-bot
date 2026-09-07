"""Failure boundaries using synthetic I/O; no native model or Telegram requests."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import GetMe, GetUpdates

from bot.runtime.polling_health import PollingHealth
from bot.stt.local_whisper import LocalWhisperClient


@pytest.mark.asyncio
async def test_cancelled_native_work_keeps_admission_closed_until_thread_finishes():
    client = LocalWhisperClient()
    entered, release = threading.Event(), threading.Event()
    events = []

    def operation():
        entered.set()
        release.wait(timeout=5)
        events.append("finished")

    task = asyncio.create_task(client._run_native(operation))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RuntimeError, match="busy"):
            await client._run_native(lambda: events.append("unexpected"))
        client._close_sync = lambda: events.append("closed")
        close_task = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        assert not close_task.done()
        release.set()
        await close_task
        assert events == ["finished", "closed"]
    finally:
        release.set()
        if not client._closed:
            await client.close()


@pytest.mark.asyncio
async def test_polling_readiness_requires_recent_success_even_for_empty_updates(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("bot.runtime.polling_health.time.monotonic", lambda: clock[0])
    health = PollingHealth()
    assert not health.ready
    await health(AsyncMock(return_value=object()), None, GetMe())
    assert not health.ready
    await health(AsyncMock(return_value=[]), None, GetUpdates())
    assert health.ready
    clock[0] += 91
    with pytest.raises(OSError):
        await health(AsyncMock(side_effect=OSError()), None, GetUpdates())
    assert not health.ready
    await health(AsyncMock(return_value=[]), None, GetUpdates())
    assert health.ready


@pytest.mark.asyncio
async def test_lease_failure_cancels_polling_and_stops_readiness():
    from bot.main import _poll_with_lease

    cancelled = asyncio.Event()

    async def poll(*args, **kwargs):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    dispatcher = SimpleNamespace(start_polling=poll, resolve_used_update_types=lambda: [])
    lease = SimpleNamespace(watch=AsyncMock(side_effect=RuntimeError("lease lost")))
    readiness = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    with pytest.raises(RuntimeError, match="lease lost"):
        await _poll_with_lease(dispatcher, None, lease, readiness)
    assert cancelled.is_set()
    readiness.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", ["<b>literal</b>", "😀" * 6000])
async def test_voice_confirmation_preserves_text_and_telegram_utf16_limit(transcript):
    from bot.handlers.voice import _send_confirmation

    sent = []

    async def answer(value, **kwargs):
        assert len(value.encode("utf-16-le")) // 2 <= 4096
        assert kwargs["parse_mode"] is None
        sent.append((value, kwargs))
        return SimpleNamespace(message_id=len(sent))

    result = await _send_confirmation(SimpleNamespace(answer=answer), transcript, "token")
    assert result.message_id == len(sent)
    assert sent[-1][1]["reply_markup"]
    if len(sent) > 1:
        assert "".join(value for value, _ in sent[:-1]) == transcript
    else:
        assert transcript in sent[0][0]


@pytest.mark.asyncio
async def test_failed_voice_download_always_removes_temporary_audio(monkeypatch):
    from contextlib import asynccontextmanager

    from bot.handlers import voice

    paths = []

    @asynccontextmanager
    async def session():
        yield None

    async def download(remote, path):
        paths.append(path)
        path.write_bytes(b"partial audio")
        raise OSError("network failed")

    bot = SimpleNamespace(send_chat_action=AsyncMock(),
                          get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote")),
                          download_file=download)
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1),
                              voice=SimpleNamespace(file_id="x", file_size=100), answer=AsyncMock())
    monkeypatch.setattr(voice, "async_session", session)
    monkeypatch.setattr(voice, "get_user", AsyncMock(return_value=object()))
    monkeypatch.setattr(voice, "has_current_consent", lambda user: True)
    monkeypatch.setattr(voice, "message_bot", lambda message: bot)
    monkeypatch.setattr(voice, "_stt_client", SimpleNamespace(transcribe=AsyncMock()))
    await voice.handle_voice(message)
    assert paths and all(not path.exists() for path in paths)
    voice._stt_client.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_indexing_does_not_delay_backup_or_retention(monkeypatch):
    from bot.runtime import background

    entered = asyncio.Event()
    completed = set()

    async def index():
        entered.set()
        await asyncio.Future()

    async def done(name):
        completed.add(name)

    monkeypatch.setattr(background, "reindex_missing_embeddings", index)
    monkeypatch.setattr(background, "_maintenance_action", lambda: done("backup"))
    monkeypatch.setattr(background, "rotate_llm_logs", lambda: done("retention"))
    monkeypatch.setattr(background, "resume_pending_deliveries", lambda bot: done("outbox"))
    tasks = background.start_background_tasks(None, None, None)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        assert completed == {"backup", "retention", "outbox"}
    finally:
        await background.stop_background_tasks(tasks)
