from types import SimpleNamespace

import pytest

import bot.handlers.chronometry as chronometry
from tests.fakes import FakeSessionContext


class FakeLLMClient:
    def __init__(self, content):
        self.content = content
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.content)


class FakeQueue:
    async def submit(self, priority, coro):
        return await coro


@pytest.fixture(autouse=True)
def reset_chrono_globals(monkeypatch):
    old_client = chronometry._llm_client
    old_queue = chronometry._llm_queue
    async def fake_get_user(session, user_id):
        return SimpleNamespace(
            chronometry_interval_min=60,
            chronometry_last_asked=None,
        )
    async def fake_get_today_tasks(session, user_id, today):
        return []
    monkeypatch.setattr(chronometry, "get_user", fake_get_user)
    monkeypatch.setattr(chronometry, "get_today_tasks", fake_get_today_tasks)
    yield
    chronometry._llm_client = old_client
    chronometry._llm_queue = old_queue


@pytest.mark.asyncio
async def test_process_chronometry_response_records_entry_and_pause(monkeypatch):
    created = []
    updates = []

    async def fake_get_prompt(session, prompt_key):
        return "Верни JSON"

    async def fake_create_time_entry(session, **kwargs):
        created.append(kwargs)

    async def fake_update_user_settings(session, user_id, **kwargs):
        updates.append((user_id, kwargs))

    client = FakeLLMClient(
        '{"category":"rest","is_planned":false,"productivity_score":3,'
        '"reaction_text":"Записал: обед."}'
    )
    chronometry._llm_client = client
    chronometry._llm_queue = FakeQueue()
    monkeypatch.setattr(chronometry, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(chronometry, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chronometry, "create_time_entry", fake_create_time_entry)
    monkeypatch.setattr(chronometry, "update_user_settings", fake_update_user_settings)

    result = await chronometry.process_chronometry_response(42, "Обедаю", "Europe/Moscow")

    assert result == "⏱ Записал: обед."
    assert created[0]["user_id"] == 42
    assert created[0]["activity_text"] == "Обедаю"
    assert created[0]["category"] == "rest"
    assert updates[0][0] == 42
    assert "chronometry_last_asked" in updates[0][1]
    assert "Ответ пользователя" in client.messages[0]["content"]


@pytest.mark.asyncio
async def test_process_chronometry_response_updates_last_asked_without_pause(monkeypatch):
    updates = []

    async def fake_get_prompt(session, prompt_key):
        return "Верни JSON"

    async def fake_create_time_entry(session, **kwargs):
        return None

    async def fake_update_user_settings(session, user_id, **kwargs):
        updates.append((user_id, kwargs))

    chronometry._llm_client = FakeLLMClient(
        '{"category":"work","is_planned":true,"productivity_score":4,'
        '"reaction_text":"Записал."}'
    )
    chronometry._llm_queue = FakeQueue()
    monkeypatch.setattr(chronometry, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(chronometry, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chronometry, "create_time_entry", fake_create_time_entry)
    monkeypatch.setattr(chronometry, "update_user_settings", fake_update_user_settings)

    await chronometry.process_chronometry_response(42, "Доделываю бота ВБ", "Europe/Moscow")

    assert updates[0][0] == 42
    assert "chronometry_last_asked" in updates[0][1]


@pytest.mark.asyncio
async def test_process_chronometry_response_falls_back_on_bad_json(monkeypatch):
    created = []

    async def fake_get_prompt(session, prompt_key):
        return "Верни JSON"

    async def fake_create_time_entry(session, **kwargs):
        created.append(kwargs)

    chronometry._llm_client = FakeLLMClient("not json")
    chronometry._llm_queue = FakeQueue()
    monkeypatch.setattr(chronometry, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(chronometry, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chronometry, "create_time_entry", fake_create_time_entry)

    result = await chronometry.process_chronometry_response(42, "Пишу код", "Europe/Moscow")

    assert result == "Записал ✅"
    assert created[0]["category"] == "unknown"
