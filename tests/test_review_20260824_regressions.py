import asyncio
from types import SimpleNamespace

import pytest

import bot.handlers.commands as commands
import bot.handlers.onboarding as onboarding
from bot.embeddings.cloud import CloudEmbeddingClient
from bot.llm.client import LLMClient
from bot.llm.queue import PRIORITY_INTENT, LLMQueue, LLMTask
from bot.services.tasks import _next_future_occurrence
from bot.stt.cloud_stt import CloudSTTClient
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


@pytest.mark.asyncio
async def test_long_html_command_output_is_split_under_telegram_limit():
    msg = FakeMessage(user_id=42)
    await commands._answer_html_parts(msg, "<b>Задачи</b>\n" + ("важное дело\n" * 700))

    assert len(msg.answers) > 1
    assert all(len(text) <= 4096 for text, _ in msg.answers)
    assert all(kwargs["parse_mode"] == "HTML" for _, kwargs in msg.answers)


@pytest.mark.asyncio
async def test_queue_timeout_includes_wait_for_free_slot():
    queue = LLMQueue(maxsize=1)
    loop = asyncio.get_running_loop()
    blocker_coro = asyncio.sleep(60)
    blocker = LLMTask(1, 0, blocker_coro, loop.create_future())
    await queue._queue.put(blocker)

    with pytest.raises(asyncio.TimeoutError):
        await queue.submit(PRIORITY_INTENT, asyncio.sleep(0), timeout=0.01)

    assert queue._queue.qsize() == 1
    queued = queue._queue.get_nowait()
    queued.coro.close()
    queue._queue.task_done()


@pytest.mark.asyncio
async def test_cloud_stt_sends_bytes_tuple_not_async_file(tmp_path):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="распознано")

    client = CloudSTTClient.__new__(CloudSTTClient)
    client.model = "whisper-test"
    client.language = "ru"
    client.client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=create)
        )
    )
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"ogg-data")

    assert await client.transcribe(audio) == "распознано"
    assert captured["file"] == ("voice.ogg", b"ogg-data", "application/octet-stream")


@pytest.mark.asyncio
async def test_cloud_embedding_requests_and_validates_dimensions():
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.0] * 3)])

    client = CloudEmbeddingClient.__new__(CloudEmbeddingClient)
    client.model = "embedding-test"
    client.dimensions = 3
    client.client = SimpleNamespace(
        embeddings=SimpleNamespace(create=create)
    )

    assert len(await client.embed("текст")) == 3
    assert calls == [
        {"model": "embedding-test", "input": "текст", "dimensions": 3}
    ]


@pytest.mark.asyncio
async def test_llm_health_check_always_probes_and_marks_failure():
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("provider down")

    client = LLMClient.__new__(LLMClient)
    client._main_healthy = True
    client.main_model = "test-model"
    client.main_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await client.health_check() is False
    assert client._main_healthy is False
    assert len(calls) == 1


def test_late_recurring_completion_skips_past_occurrences():
    import pendulum

    now = pendulum.datetime(2026, 8, 24, 12, tz="Europe/Moscow")
    old = pendulum.datetime(2026, 8, 20, 9, tz="Europe/Moscow")

    next_at = _next_future_occurrence(old, "daily", now)

    assert next_at == pendulum.datetime(2026, 8, 25, 9, tz="Europe/Moscow")


@pytest.mark.asyncio
async def test_onboarding_rejects_invalid_times_instead_of_using_defaults(monkeypatch):
    class State:
        async def update_data(self, **kwargs):
            raise AssertionError("invalid settings must not be stored")

    async def forbidden_next(*args, **kwargs):
        raise AssertionError("invalid settings must keep current onboarding step")

    monkeypatch.setattr(onboarding, "_send_work_schedule_step", forbidden_next)
    msg = FakeMessage("утро=99:70", user_id=42)

    await onboarding.onb_digest_text(msg, State())

    assert "Не понял время" in msg.answers[-1][0]


@pytest.mark.asyncio
async def test_onboarding_rejects_reversed_work_hours(monkeypatch):
    class State:
        async def update_data(self, **kwargs):
            raise AssertionError("reversed schedule must not be stored")

    async def forbidden_next(*args, **kwargs):
        raise AssertionError("invalid schedule must keep current onboarding step")

    monkeypatch.setattr(onboarding, "_send_concepts_step", forbidden_next)
    msg = FakeMessage(
        "дни=пн,вт начало=18:00 конец=09:00",
        user_id=42,
    )

    await onboarding.onb_work_text(msg, State())

    assert "позже начала" in msg.answers[-1][0]


@pytest.mark.asyncio
async def test_settings_keep_at_least_one_work_day(monkeypatch):
    async def get_user(session, user_id):
        return SimpleNamespace(work_days=[1])

    async def forbidden_update(*args, **kwargs):
        raise AssertionError("last work day must not be removed")

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user", get_user)
    monkeypatch.setattr(commands, "update_user_settings", forbidden_update)
    callback = FakeCallback(user_id=42)
    callback.data = "settings:toggle_day:1"

    await commands.cb_toggle_day(callback)

    assert callback.answered[-1][1]["show_alert"] is True
