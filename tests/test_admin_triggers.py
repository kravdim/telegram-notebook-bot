from types import SimpleNamespace

import pytest

import bot.handlers.admin as admin
import bot.scheduler.chronometry as chronometry
import bot.scheduler.digest as digest
import bot.scheduler.weekly_review as weekly_review
from tests.fakes import FakeMessage, FakeSessionContext


@pytest.mark.asyncio
async def test_digest_command_is_admin_only(monkeypatch):
    monkeypatch.setattr(admin, "_is_admin", lambda user_id: False)
    msg = FakeMessage("/digest morning", user_id=7)

    await admin.cmd_digest_now(msg, SimpleNamespace(args="morning"))

    assert "только администратору" in msg.answers[-1][0]


@pytest.mark.asyncio
async def test_digest_command_targets_user_and_reports_duplicate(monkeypatch):
    user = SimpleNamespace(telegram_id=99)

    async def target(message, raw_id):
        assert raw_id == "99"
        return user

    calls = []

    async def send_now(bot, selected, period):
        calls.append((selected.telegram_id, period))
        return False

    monkeypatch.setattr(admin, "_is_admin", lambda user_id: True)
    monkeypatch.setattr(admin, "_get_trigger_target", target)
    monkeypatch.setattr(digest, "send_digest_now", send_now)
    msg = FakeMessage("/digest evening 99", user_id=42)

    await admin.cmd_digest_now(msg, SimpleNamespace(args="evening 99"))

    assert calls == [(99, "evening")]
    assert "дубль пропущен" in msg.answers[-1][0]


@pytest.mark.asyncio
async def test_review_now_alias_and_chrono_pending(monkeypatch):
    user = SimpleNamespace(telegram_id=42)

    async def target(message, raw_id):
        assert raw_id is None
        return user

    async def send_review(bot, selected):
        return True

    async def send_chrono(bot, selected):
        return "pending"

    monkeypatch.setattr(admin, "_is_admin", lambda user_id: True)
    monkeypatch.setattr(admin, "_get_trigger_target", target)
    monkeypatch.setattr(weekly_review, "send_weekly_review_now", send_review)
    monkeypatch.setattr(chronometry, "send_chronometry_prompt_now", send_chrono)

    review_msg = FakeMessage("/review now", user_id=42)
    await admin.cmd_review_now(review_msg, SimpleNamespace(args="now"))
    assert "Sunday Review отправлен" in review_msg.answers[-1][0]

    chrono_msg = FakeMessage("/chrono_ping", user_id=42)
    await admin.cmd_chrono_ping(chrono_msg, SimpleNamespace(args=None))
    assert "незакрытый вопрос" in chrono_msg.answers[-1][0]


@pytest.mark.asyncio
async def test_manual_digest_does_not_mark_date_when_delivery_fails(monkeypatch):
    user = SimpleNamespace(telegram_id=42, timezone="Europe/Moscow")
    claimed = []

    async def claim(session, user_id, marker, today):
        claimed.append((user_id, marker, today))
        return True

    async def fail_send(*args):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(digest, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(digest, "claim_date_marker", claim)
    monkeypatch.setattr(digest, "_send_morning", fail_send)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        await digest.send_digest_now(object(), user, "morning")

    assert claimed == []


@pytest.mark.asyncio
async def test_manual_review_skips_already_claimed_slot(monkeypatch):
    user = SimpleNamespace(telegram_id=42, timezone="Europe/Moscow")

    async def no_claim(*args):
        return False

    async def forbidden_send(*args):
        raise AssertionError("duplicate review must not be sent")

    monkeypatch.setattr(weekly_review, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(weekly_review, "claim_date_marker", no_claim)
    monkeypatch.setattr(weekly_review, "_send_review", forbidden_send)

    assert await weekly_review.send_weekly_review_now(object(), user) is False


@pytest.mark.asyncio
async def test_manual_chrono_does_not_send_second_pending_question(monkeypatch):
    user = SimpleNamespace(
        telegram_id=42,
        timezone="Europe/Moscow",
        chronometry_interval_min=30,
    )
    sent_messages = []

    class Bot:
        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))
            return SimpleNamespace(message_id=123)

    async def no_state(user_id, expected_type=None):
        return None

    async def claim_state(*args, **kwargs):
        return SimpleNamespace()

    async def transition_state(*args, **kwargs):
        return SimpleNamespace()

    async def update_settings(*args, **kwargs):
        return None

    monkeypatch.setattr(chronometry, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(
        chronometry.interaction_service, "get", no_state
    )
    monkeypatch.setattr(
        chronometry.interaction_service, "claim", claim_state
    )
    monkeypatch.setattr(
        chronometry.interaction_service, "transition", transition_state
    )
    monkeypatch.setattr(chronometry, "update_user_settings", update_settings)
    chronometry.clear_awaiting(42)
    chronometry._prompt_locks.pop(42, None)

    assert await chronometry.send_chronometry_prompt_now(Bot(), user) == "sent"
    assert await chronometry.send_chronometry_prompt_now(Bot(), user) == "pending"
    assert len(sent_messages) == 1

    chronometry.clear_awaiting(42)
    chronometry._prompt_locks.pop(42, None)
