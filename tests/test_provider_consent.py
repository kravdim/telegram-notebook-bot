import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.privacy as privacy
from bot.handlers import messages, onboarding, voice
from bot.handlers import privacy as privacy_handler
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


@pytest.fixture
def recipients(monkeypatch):
    config = {
        "llm": {"main": {"provider": "openai", "base_url": "https://api.example.test/v1", "model": "model-a"}},
        "embedding": {"provider": "ollama", "base_url": "http://localhost:11434"},
        "stt": {"provider": "local"},
    }
    monkeypatch.setattr(privacy, "settings", SimpleNamespace(yaml_config=config, embedding_base_url=""))
    return config


@pytest.mark.parametrize("change", ["provider", "endpoint", "fallback", "stt", "embedding"])
def test_recipient_changes_invalidate_consent(recipients, change):
    user = SimpleNamespace(cloud_processing_enabled=True, privacy_notice_version=1,
                           privacy_provider_fingerprint=privacy.provider_fingerprint())
    assert privacy.has_current_consent(user)
    if change == "provider":
        recipients["llm"]["main"]["provider"] = "minimax"
    elif change == "endpoint":
        recipients["llm"]["main"]["base_url"] = "https://proxy.example.test/v1"
    elif change == "fallback":
        recipients["llm"]["fallback"] = {"provider": "minimax"}
    elif change == "stt":
        recipients["stt"]["provider"] = "groq"
    else:
        recipients["embedding"]["provider"] = "cloud"
    assert not privacy.has_current_consent(user)
    assert privacy.consent_display_state(user) is None


def test_model_and_secret_rotation_do_not_change_recipients(recipients):
    before = privacy.provider_fingerprint()
    recipients["llm"]["main"].update(model="model-b", api_key="synthetic-not-a-secret", timeout_sec=20)
    recipients["llm"]["main"]["base_url"] = "https://user:password@api.example.test/v1/?key=synthetic"
    assert privacy.provider_fingerprint() == before
    assert len(before) == 32


@pytest.mark.asyncio
async def test_stale_privacy_button_cannot_grant_new_recipients(recipients, monkeypatch):
    original = privacy.provider_fingerprint()
    recipients["stt"]["provider"] = "groq"
    update = AsyncMock()
    monkeypatch.setattr(privacy_handler, "update_user_settings", update)
    callback = FakeCallback(data=f"privacy:enable:{original}")
    await privacy_handler.cb_privacy_choice(callback)
    update.assert_not_called()
    assert "изменился" in callback.answered[0][0]
    keyboard = callback.message.answers[0][1]["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == f"privacy:enable:{privacy.provider_fingerprint()}"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["text", "voice"])
async def test_stale_consent_blocks_before_text_or_voice_egress(recipients, monkeypatch, channel):
    user = SimpleNamespace(timezone="Europe/Moscow", cloud_processing_enabled=True,
                           privacy_notice_version=1, privacy_provider_fingerprint=privacy.provider_fingerprint())
    recipients["llm"]["main"]["base_url"] = "https://changed.example.test/v1"
    handler = messages if channel == "text" else voice
    monkeypatch.setattr(handler, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(handler, "get_user", AsyncMock(return_value=user))
    message = FakeMessage("synthetic private content")
    if channel == "text":
        plan = AsyncMock(side_effect=AssertionError("must block before intent processing"))
        monkeypatch.setattr(messages, "saved_plan", plan)
        assert await messages._process_text_message_unlocked(1, message.text, message) == messages.MessageOutcome.REJECTED
        plan.assert_not_called()
    else:
        # FakeMessage has no voice payload: touching/download of it would fail.
        await voice.handle_voice(message)
    assert "Текущий выбор: не выбрана" in message.answers[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_notice_already_shown", [False, True])
async def test_onboarding_requires_fingerprint_of_shown_notice(recipients, monkeypatch, fresh_notice_already_shown):
    original = privacy.provider_fingerprint()
    recipients["stt"]["provider"] = "groq"
    offered = privacy.provider_fingerprint() if fresh_notice_already_shown else original
    state = SimpleNamespace(get_data=AsyncMock(return_value={"privacy_offered_fingerprint": offered}),
                            update_data=AsyncMock())
    resend = AsyncMock()
    monkeypatch.setattr(onboarding, "_send_privacy_step", resend)
    callback = FakeCallback(data=f"onb_privacy_accept:{original}")
    await onboarding.onb_privacy_choice(callback, state)
    resend.assert_awaited_once_with(callback.message, state)
    state.update_data.assert_not_called()


def test_embedding_environment_override_is_part_of_identity(recipients, monkeypatch):
    original = privacy.provider_fingerprint()
    monkeypatch.setattr(privacy, "settings", SimpleNamespace(
        yaml_config=copy.deepcopy(recipients), embedding_base_url="https://different.example.test/v1",
    ))
    assert privacy.provider_fingerprint() != original
