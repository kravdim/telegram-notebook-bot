"""Focused isolated coverage for less frequently exercised Telegram handlers."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

import bot.handlers.admin as admin
import bot.handlers.callbacks as callbacks
import bot.handlers.chronometry as chronometry
import bot.handlers.evening_review as evening_review
import bot.handlers.trip as trip
import bot.handlers.voice as voice
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


@pytest.mark.asyncio
async def test_admin_rejects_bad_target_and_bad_digest_shape(monkeypatch):
    monkeypatch.setattr(admin, "_is_admin", lambda user_id: True)
    message = FakeMessage(user_id=11)

    await admin._get_trigger_target(message, "not-an-id")
    await admin.cmd_digest_now(message, SimpleNamespace(args="night 1 2"))

    assert "TELEGRAM_ID должен быть числом" in message.answers[0][0]
    assert "Использование: /digest" in message.answers[1][0]


@pytest.mark.asyncio
async def test_admin_chrono_busy_and_unavailable_llm_status(monkeypatch):
    user = SimpleNamespace(telegram_id=42)

    async def target(message, raw_id):
        return user

    async def busy(bot, selected):
        return "busy"

    import bot.scheduler.chronometry as scheduler_chronometry

    monkeypatch.setattr(admin, "_is_admin", lambda user_id: True)
    monkeypatch.setattr(admin, "_get_trigger_target", target)
    monkeypatch.setattr(scheduler_chronometry, "send_chronometry_prompt_now", busy)
    message = FakeMessage(user_id=42)
    await admin.cmd_chrono_ping(message, SimpleNamespace(args=None))
    assert "другой вопрос" in message.answers[-1][0]

    import bot.handlers.messages as messages

    monkeypatch.setattr(messages, "llm_client", None)
    await admin.cmd_status(message)
    assert message.answers[-1][0] == "LLM-клиент не инициализирован."


@pytest.mark.asyncio
async def test_memoir_skip_rejects_stale_state_and_delete_choice_checks_owner(monkeypatch):
    callback = FakeCallback(user_id=5, data="memoir_skip:expired")

    async def missing_state(user_id, state_type):
        return None

    monkeypatch.setattr(callbacks.interaction_service, "get", missing_state)
    await callbacks.cb_memoir_skip(callback)
    assert callback.answered[-1][0] == "Эта сессия уже устарела"
    assert "уже не активен" in callback.message.edits[-1][0]

    task_id = uuid4()

    async def foreign_task(session, requested_id):
        return SimpleNamespace(id=requested_id, user_id=99, title="Чужая")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "get_task_by_id", foreign_task)
    choice = FakeCallback(user_id=5, data=f"task_delete_choose:{task_id}")
    await callbacks.cb_delete_choose(choice)
    assert choice.message.edits[-1][0] == "Задача не найдена."


@pytest.mark.asyncio
async def test_trip_validates_missing_dates_and_creates_escaped_trip(monkeypatch):
    message = FakeMessage(user_id=7)
    created = []

    async def user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def no_open_trip(session, user_id, today):
        return None

    async def create(session, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(trip, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(trip, "get_user", user)
    monkeypatch.setattr(trip, "get_open_trip", no_open_trip)
    monkeypatch.setattr(trip, "create_trip", create)
    await trip._trip_on(message, "Питер без диапазона")
    assert "Уточни даты" in message.answers[-1][0]
    await trip._trip_on(message, "Встречи в <Москва> 30.12-02.01")

    assert created[0]["end_date"].year == created[0]["start_date"].year + 1
    assert created[0]["destination"] is None
    assert "&lt;Москва&gt;" in message.answers[-1][0]


@pytest.mark.asyncio
async def test_trip_off_empty_and_existing_trip_denial(monkeypatch):
    async def user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def no_trip(session, user_id, date):
        return None

    monkeypatch.setattr(trip, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(trip, "get_user", user)
    monkeypatch.setattr(trip, "get_open_trip", no_trip)
    message = FakeMessage(user_id=7)
    await trip._trip_off(message)
    assert message.answers[-1][0] == "Нет активной командировки."


@pytest.mark.asyncio
async def test_voice_denies_without_privacy_and_edit_requires_matching_state(monkeypatch):
    async def no_user(session, user_id):
        return None

    monkeypatch.setattr(voice, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(voice, "get_user", no_user)
    message = FakeMessage(user_id=9)
    await voice.handle_voice(message)
    assert "обработк" in message.answers[-1][0].lower()

    stale = FakeCallback(user_id=9, data="voice_edit:old")

    async def absent_state(user_id, state_type):
        return None

    monkeypatch.setattr(voice, "_load_voice_state", absent_state)
    await voice.cb_voice_edit(stale)
    assert stale.answered[-1][0] == "Эта сессия уже устарела"


@pytest.mark.asyncio
async def test_voice_edit_moves_matching_confirmation_to_edit(monkeypatch):
    callback = FakeCallback(user_id=9, data="voice_edit:token")
    state = SimpleNamespace(
        payload={"session_token": "token", "message_id": 999, "transcript": "текст"}
    )
    persisted = []

    async def load(user_id, state_type):
        return state

    async def persist(*args, **kwargs):
        persisted.append((args, kwargs))
        return True

    monkeypatch.setattr(voice, "_load_voice_state", load)
    monkeypatch.setattr(voice, "_persist_voice_state", persist)
    voice._awaiting_edit.pop(9, None)
    await voice.cb_voice_edit(callback)

    assert persisted[0][0][1] == "voice_edit"
    assert voice._awaiting_edit[9] == "token"
    assert "исправленный текст" in callback.message.edits[-1][0]
    voice._awaiting_edit.pop(9, None)


@pytest.mark.asyncio
async def test_evening_review_updates_tomorrow_and_handles_missing_task(monkeypatch):
    task_id = uuid4()
    captured = []

    async def user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def update(session, requested_id, user_id, **kwargs):
        captured.append((requested_id, user_id, kwargs))
        return SimpleNamespace(title="<Счёт>")

    monkeypatch.setattr(evening_review, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr("bot.db.crud.users.get_user", user)
    monkeypatch.setattr(evening_review, "update_task", update)
    callback = FakeCallback(user_id=4, data=f"review_tomorrow:{task_id}")
    await evening_review.cb_review_tomorrow(callback)
    assert captured[0][2]["scheduled_date"]
    assert "&lt;Счёт&gt;" in callback.message.edits[-1][0]

    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(evening_review, "update_task", missing)
    callback = FakeCallback(user_id=4, data=f"review_tomorrow:{task_id}")
    await evening_review.cb_review_tomorrow(callback)
    assert callback.message.edits[-1][0] == "Задача не найдена."


@pytest.mark.asyncio
async def test_chronometry_records_sanitized_llm_result_and_fallback(monkeypatch):
    entry_calls = []
    setting_calls = []
    task_id = uuid4()

    class Queue:
        async def submit(self, priority, coro):
            coro.close()
            return SimpleNamespace(
                content='{"category":"work","is_planned":true,"productivity_score":5,"matched_task_title":"Отчёт","reaction_text":"Готово"}'
            )

    async def prompt(session, key):
        return "prompt"

    async def today_tasks(session, user_id, date):
        return [SimpleNamespace(title="Отчёт")]

    async def user(session, user_id):
        return SimpleNamespace(chronometry_interval_min=25)

    async def search(session, user_id, text, status):
        return [SimpleNamespace(id=task_id, title="Отчёт")]

    async def create_entry(session, **kwargs):
        entry_calls.append(kwargs)

    async def update_settings(session, user_id, **kwargs):
        setting_calls.append(kwargs)

    monkeypatch.setattr(chronometry, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(chronometry, "get_prompt", prompt)
    monkeypatch.setattr(chronometry, "get_today_tasks", today_tasks)
    monkeypatch.setattr(chronometry, "get_user", user)
    monkeypatch.setattr(chronometry, "search_tasks", search)
    monkeypatch.setattr(chronometry, "create_time_entry", create_entry)
    monkeypatch.setattr(chronometry, "update_user_settings", update_settings)

    async def chat(**kwargs):
        return None

    chronometry.init(SimpleNamespace(chat=chat), Queue())

    result = await chronometry.process_chronometry_response(3, "Пишу отчёт", "Europe/Moscow")
    assert result == "⏱ Записал."
    assert entry_calls[0]["matched_task_id"] == task_id
    assert entry_calls[0]["duration_minutes"] == 25
    assert setting_calls

    chronometry._llm_client = None
    assert await chronometry.process_chronometry_response(3, "текст", "Europe/Moscow") == (
        "LLM не доступен для обработки хронометража."
    )


@pytest.mark.asyncio
async def test_admin_digest_reports_delivery_success_and_error(monkeypatch):
    import bot.scheduler.digest as digest

    user = SimpleNamespace(telegram_id=77)

    async def target(message, raw_id):
        assert raw_id is None
        return user

    async def sent(bot, selected, period):
        assert (selected, period) == (user, "morning")
        return True

    monkeypatch.setattr(admin, "_is_admin", lambda user_id: True)
    monkeypatch.setattr(admin, "_get_trigger_target", target)
    monkeypatch.setattr(digest, "send_digest_now", sent)
    message = FakeMessage(user_id=1)
    await admin.cmd_digest_now(message, SimpleNamespace(args="morning"))
    assert message.answers[-1][0] == "✅ Утренний дайджест отправлен пользователю 77."

    async def unavailable(*args):
        raise RuntimeError("transport unavailable")

    monkeypatch.setattr(digest, "send_digest_now", unavailable)
    await admin.cmd_digest_now(message, SimpleNamespace(args="evening"))
    assert "слот освобождён для повтора" in message.answers[-1][0]


@pytest.mark.asyncio
async def test_callback_confirmation_renders_owned_task_and_cancel_paths(monkeypatch):
    task_id = uuid4()

    async def owned_task(session, requested_id):
        return SimpleNamespace(id=requested_id, user_id=5, title="Купить <чай>")

    monkeypatch.setattr(callbacks, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(callbacks, "get_task_by_id", owned_task)
    choose = FakeCallback(user_id=5, data=f"task_delete_choose:{task_id}")
    await callbacks.cb_delete_choose(choose)
    text, kwargs = choose.message.edits[-1]
    assert "Купить &lt;чай&gt;" in text
    assert kwargs["reply_markup"] is not None

    cancelled = FakeCallback(user_id=5, data="task_delete_no")
    await callbacks.cb_delete_no(cancelled)
    assert cancelled.answered == [(None, {})]
    assert cancelled.message.edits[-1][0] == "❌ Удаление отменено."

    project_cancel = FakeCallback(user_id=5, data=f"project_complete_no:{uuid4()}")
    await callbacks.cb_project_complete_no(project_cancel)
    assert "Закрытие слона отменено" in project_cancel.message.edits[-1][0]


@pytest.mark.asyncio
async def test_trip_on_refuses_existing_open_trip_before_create(monkeypatch):
    created = []

    async def user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def existing(session, user_id, date):
        return SimpleNamespace(title="Старая <поездка>")

    async def create(*args, **kwargs):
        created.append(True)

    monkeypatch.setattr(trip, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(trip, "get_user", user)
    monkeypatch.setattr(trip, "get_open_trip", existing)
    monkeypatch.setattr(trip, "create_trip", create)
    message = FakeMessage(user_id=7)
    await trip._trip_on(message, "Новая поездка 01.09-03.09")

    assert created == []
    assert "Старая &lt;поездка&gt;" in message.answers[-1][0]
    assert "/trip off" in message.answers[-1][0]


@pytest.mark.asyncio
async def test_voice_confirm_processing_error_restores_retryable_confirmation(monkeypatch):
    import bot.handlers.messages as messages

    callback = FakeCallback(user_id=9, data="voice_confirm:token")
    state = SimpleNamespace(
        payload={
            "session_token": "token",
            "message_id": 999,
            "transcript": "создай задачу",
        }
    )
    transitions = []

    async def load(user_id, state_type):
        return state

    async def persist(*args, **kwargs):
        transitions.append((args[1], kwargs["expected_type"], args[2]["phase"]))
        return True

    async def processing_error(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(voice, "_load_voice_state", load)
    monkeypatch.setattr(voice, "_persist_voice_state", persist)
    monkeypatch.setattr(messages, "process_text_message", processing_error)
    await voice.cb_voice_confirm(callback)

    assert transitions == [
        ("voice_processing", "voice_confirm", "processing"),
        ("voice_confirm", "voice_processing", "failed"),
    ]
    assert callback.answered == [(None, {})]
    assert "Можно повторить" in callback.message.edits[-1][0]
    assert callback.message.edits[-1][1]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_voice_cancel_clears_matching_persisted_and_local_state(monkeypatch):
    callback = FakeCallback(user_id=9, data="voice_cancel:token")
    state = SimpleNamespace(payload={"session_token": "token", "message_id": 999})
    cleared = []

    async def load(user_id, state_type):
        return state

    async def clear(user_id, expected_type, token):
        cleared.append((user_id, expected_type, token))

    monkeypatch.setattr(voice, "_load_voice_state", load)
    monkeypatch.setattr(voice, "_clear_voice_state", clear)
    voice._pending_transcripts[(9, "token")] = "старый текст"
    voice._awaiting_edit[9] = "token"
    await voice.cb_voice_cancel(callback)

    assert cleared == [(9, "voice_confirm", "token")]
    assert (9, "token") not in voice._pending_transcripts
    assert 9 not in voice._awaiting_edit
    assert callback.message.edits[-1][0] == "❌ Голосовое отменено."
