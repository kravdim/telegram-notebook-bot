import asyncio
from types import SimpleNamespace

import pytest
import yaml

import bot.handlers.callbacks as callbacks
import bot.llm.context as context
import scripts.delete_user_data as deletion_script
from bot.llm.client import LLMClient
from bot.services.access_config import read_allowed_telegram_ids
from tests.fakes import FakeCallback, FakeMessage, FakeSession, FakeSessionContext


def test_context_trimming_is_local_and_keeps_recent_pairs(monkeypatch):
    user_id = 8_260_001
    context.clear_history(user_id)
    monkeypatch.setattr(context, "_MAX_TOKENS", 1)
    monkeypatch.setattr(context, "_KEEP_RECENT_PAIRS", 1)
    for index in range(3):
        context.add_message(user_id, "user", f"request-{index}")
        context.add_message(user_id, "assistant", f"response-{index}")

    assert context.needs_trimming(user_id) is True
    context.trim_history(user_id)

    assert context.get_history(user_id) == [
        {"role": "user", "content": "request-2"},
        {"role": "assistant", "content": "response-2"},
    ]
    context.clear_history(user_id)


@pytest.mark.asyncio
async def test_llm_total_deadline_bounds_whole_provider_chain():
    client = LLMClient.__new__(LLMClient)
    client.total_timeout = 0.01

    async def hanging_chain(*args):
        await asyncio.sleep(60)

    client._chat_with_fallback = hanging_chain
    with pytest.raises(TimeoutError):
        await client.chat(messages=[{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_old_memoir_skip_cannot_clear_new_question(monkeypatch):
    state = SimpleNamespace(
        state_type="memoir",
        payload={"session_token": "session-b", "message_id": 1001},
    )
    cleared = []

    async def get_state(user_id, expected_type):
        return state

    async def clear_state(*args):
        cleared.append(args)
        return True

    monkeypatch.setattr(callbacks.interaction_service, "get", get_state)
    monkeypatch.setattr(callbacks.interaction_service, "clear", clear_state)
    old_message = FakeMessage(user_id=42)
    old_message.message_id = 999
    callback = FakeCallback(
        user_id=42, message=old_message, data="memoir_skip:session-a"
    )

    await callbacks.cb_memoir_skip(callback)

    assert cleared == []
    assert callback.answered[0][0] == "Эта сессия уже устарела"


@pytest.mark.asyncio
async def test_privacy_deletion_resumes_prepared_journal_after_yaml_change(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"bot": {"allowed_telegram_ids": [77]}}),
        encoding="utf-8",
    )
    journal = {
        "privacy.deletion.42": {
            "phase": "prepared",
            "telegram_id": 42,
            "original_whitelist": [42, 77],
            "whitelist_changed": True,
        }
    }

    class Lease:
        def __init__(self, engine):
            pass

        async def acquire(self):
            return True

        async def release(self):
            return None

    async def get_operation(session, key):
        return SimpleNamespace(value=journal[key]) if key in journal else None

    async def set_operation(session, key, value, *, commit=True):
        journal[key] = value
        return SimpleNamespace(value=value)

    async def delete_data(session, user_id):
        return {"users": 1, "tasks": 2}

    async def zero_counts(session, user_id):
        return {"users": 0, "tasks": 0}

    monkeypatch.setattr(
        deletion_script, "async_session", lambda: FakeSessionContext(FakeSession())
    )
    monkeypatch.setattr(deletion_script, "SingletonLease", Lease)
    monkeypatch.setattr(deletion_script, "get_operational_state", get_operation)
    monkeypatch.setattr(deletion_script, "set_operational_state", set_operation)
    monkeypatch.setattr(deletion_script, "delete_user_data", delete_data)
    monkeypatch.setattr(deletion_script, "user_data_counts", zero_counts)
    monkeypatch.setattr(deletion_script.settings, "admin_telegram_ids", [])
    monkeypatch.setattr(deletion_script.settings, "allow_all_users", False)
    monkeypatch.setattr(deletion_script.settings, "allowed_telegram_ids", [77])
    args = SimpleNamespace(
        telegram_id=42,
        execute=True,
        confirm="DELETE-42",
        config=config_path,
    )

    result = await deletion_script.run(args)

    assert result["mode"] == "executed"
    assert read_allowed_telegram_ids(config_path) == [77]
    assert journal["privacy.deletion.42"]["phase"] == "completed"
    assert "original_whitelist" not in journal["privacy.deletion.42"]
