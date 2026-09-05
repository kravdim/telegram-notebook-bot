import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import messages, request_retry
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


@pytest.mark.asyncio
async def test_failed_reservation_prevents_processing(monkeypatch):
    monkeypatch.setattr(messages, "_claim_request", AsyncMock(return_value=None))
    process = AsyncMock()
    monkeypatch.setattr(messages, "_process_text_message_unlocked", process)
    message = FakeMessage("надо купить хлеб")
    result = await messages.process_text_message(1, message.text, message)
    assert result == messages.MessageOutcome.RETRYABLE_ERROR
    process.assert_not_called()


@pytest.mark.asyncio
async def test_consent_rejection_keeps_resumed_plan_retryable(monkeypatch):
    monkeypatch.setattr(messages, "_claim_request", AsyncMock(return_value=True))
    monkeypatch.setattr(messages, "_get_persisted_interaction", AsyncMock(return_value=None))
    monkeypatch.setattr(messages, "_process_text_message_unlocked", AsyncMock(return_value=messages.MessageOutcome.REJECTED))
    finish = AsyncMock()
    monkeypatch.setattr(messages, "_finish_request", finish)
    key = uuid.uuid4().hex * 2
    outcome = await messages.process_text_message(1, "", FakeMessage(), resume_key=key)
    assert outcome == messages.MessageOutcome.REJECTED
    finish.assert_awaited_once_with(key, "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_retry_callback_scopes_owner_and_reuses_key(monkeypatch, exists):
    key = uuid.uuid4().hex * 2
    data = request_retry.retry_data(key)
    assert len(data.encode()) <= 64
    scalar = AsyncMock(return_value=[{"name": "create_note"}] if exists else None)
    monkeypatch.setattr(request_retry, "async_session", lambda: FakeSessionContext(SimpleNamespace(scalar=scalar)))
    process = AsyncMock()
    monkeypatch.setattr(messages, "process_text_message", process)
    callback = FakeCallback(user_id=42, data=data)
    await request_retry.retry_request(callback)
    parameters = scalar.call_args.args[0].compile().params
    assert 42 in parameters.values() and key in parameters.values()
    if exists:
        process.assert_awaited_once_with(42, "", callback.message, resume_key=key)
    else:
        process.assert_not_called()
        assert "недоступен" in callback.message.answers[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["reqretry:!", "reqretry:YQ", "reqretry:"])
async def test_malformed_retry_never_processes(monkeypatch, data):
    process = AsyncMock()
    monkeypatch.setattr(messages, "process_text_message", process)
    await request_retry.retry_request(FakeCallback(data=data))
    process.assert_not_called()
