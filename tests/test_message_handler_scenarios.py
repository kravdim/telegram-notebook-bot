from types import SimpleNamespace

import pytest

import bot.handlers.callbacks as callbacks
import bot.handlers.chronometry as chronometry_handler
import bot.handlers.messages as messages
import bot.handlers.voice as voice
import bot.scheduler.chronometry as chronometry_scheduler
from bot.llm.client import LLMUnavailableError
from bot.llm.context import clear_all, get_history
from tests.fakes import FakeMessage, FakeSessionContext


class FakeLLMClient:
    async def chat(self, *args, **kwargs):
        raise AssertionError("LLM should not be called in this scenario")


class FakeLLMQueue:
    async def submit(self, *args, **kwargs):
        raise AssertionError("LLM queue should not be called in this scenario")


class FakeResponse:
    def __init__(self, *, content=None, function_calls=None):
        self.content = content
        self.function_call = function_calls[0] if function_calls else None
        self.function_calls = function_calls or []
        self.model = "test-model"
        self.total_tokens = 1
        self.latency_ms = 1


@pytest.fixture(autouse=True)
def reset_message_globals(monkeypatch):
    old_client = messages.llm_client
    old_queue = messages.llm_queue
    messages.llm_client = FakeLLMClient()
    messages.llm_queue = FakeLLMQueue()
    messages._pending_project_completion.clear()
    clear_all()

    async def no_claim(*args, **kwargs):
        return None

    async def no_finish(*args, **kwargs):
        return None

    async def no_state(*args, **kwargs):
        return None

    async def no_clear(*args, **kwargs):
        return None

    async def consume_memory(user_id):
        return (
            "complete_project"
            if messages._pending_project_completion.pop(user_id, False)
            else None
        )

    async def set_memory(user_id, state_type):
        messages._pending_project_completion[user_id] = state_type == "complete_project"

    monkeypatch.setattr(messages, "_claim_request", no_claim)
    monkeypatch.setattr(messages, "_finish_request", no_finish)
    monkeypatch.setattr(messages, "_get_persisted_interaction", no_state)
    monkeypatch.setattr(messages, "_clear_persisted_interaction", no_clear)
    monkeypatch.setattr(messages, "_consume_pending_interaction", consume_memory)
    monkeypatch.setattr(messages, "_set_pending_interaction", set_memory)
    monkeypatch.setattr(voice, "_load_voice_state", no_state)
    monkeypatch.setattr(voice, "_clear_voice_state", no_clear)
    yield
    messages.llm_client = old_client
    messages.llm_queue = old_queue
    messages._pending_project_completion.clear()
    clear_all()


@pytest.mark.asyncio
async def test_done_message_routes_directly_to_dispatch(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append((function_call, user_id, user_tz))
        return "Задача закрыта\nОсталось на сегодня: 1"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)

    msg = FakeMessage("Подключить онлайн-кассу - сделал", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert calls == [
        (
            {
                "name": "complete_task",
                "arguments": {"search_query": "Подключить онлайн-кассу"},
            },
            42,
            "Europe/Moscow",
        )
    ]
    assert msg.answers[0][0] == "Задача закрыта\nОсталось на сегодня: 1"
    assert msg.bot.actions == [(42, "typing")]
    assert get_history(42)[-1]["content"] == "Задача закрыта\nОсталось на сегодня: 1"


@pytest.mark.asyncio
async def test_conversational_done_bypasses_pending_chronometry(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append(function_call)
        return "Задача закрыта"

    async def forbidden_chrono(*args, **kwargs):
        raise AssertionError("Done message must not be routed to chronometry")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", forbidden_chrono)

    msg = FakeMessage("Денег Фокусу заплатили", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert calls == [
        {
            "name": "complete_task",
            "arguments": {"search_query": "Денег Фокусу"},
        }
    ]
    assert msg.answers[-1][0] == "Задача закрыта"


@pytest.mark.asyncio
async def test_reschedule_bypasses_pending_chronometry(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append(function_call)
        return "Задача обновлена ✅"

    async def forbidden_chrono(*args, **kwargs):
        raise AssertionError("Reschedule message must not be routed to chronometry")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", forbidden_chrono)

    msg = FakeMessage("Купить смеситель - это на воскресенье же перенесли", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert calls[0]["name"] == "update_task"
    assert calls[0]["arguments"]["search_query"] == "Купить смеситель"
    assert calls[0]["arguments"]["updates"]["scheduled_date"]
    assert msg.answers[-1][0] == "Задача обновлена ✅"


@pytest.mark.asyncio
async def test_cancel_bypasses_pending_chronometry(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append(function_call)
        return "Задача отменена ✅"

    async def forbidden_chrono(*args, **kwargs):
        raise AssertionError("Cancel message must not be routed to chronometry")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", forbidden_chrono)

    msg = FakeMessage("Купить смеситель тоже пока не надо, вроде этот получилось сделать", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert calls == [
        {
            "name": "update_task",
            "arguments": {
                "search_query": "Купить смеситель",
                "updates": {"status": "cancelled"},
            },
        }
    ]
    assert msg.answers[-1][0] == "Задача отменена ✅"


@pytest.mark.asyncio
async def test_chronometry_reply_routes_to_chronometry_handler(monkeypatch):
    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_process_chrono(user_id, text, tz):
        assert (user_id, text, tz) == (42, "Обедаю", "Europe/Moscow")
        return "⏱ Записал: отдых."

    cleared = []

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_scheduler, "clear_awaiting", lambda user_id: cleared.append(user_id))
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", fake_process_chrono)

    reply = SimpleNamespace(message_id=100)
    msg = FakeMessage("Обедаю", user_id=42, reply_to_message=reply)
    await messages.process_text_message(42, msg.text, msg)

    assert cleared == [42]
    assert msg.answers[0][0] == "⏱ Записал: отдых."


@pytest.mark.asyncio
async def test_reply_to_other_message_does_not_go_to_chronometry(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(content="Обычный ответ")

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", None)

    reply = SimpleNamespace(message_id=999)
    msg = FakeMessage("Это обычное сообщение", user_id=42, reply_to_message=reply)
    await messages.process_text_message(42, msg.text, msg)

    assert msg.answers[-1][0] == "Обычный ответ"


@pytest.mark.asyncio
async def test_pending_memoir_requires_explicit_reply(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(content="Обычный ответ")

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def memoir_state(user_id, state_type):
        if state_type == "memoir":
            return SimpleNamespace(state_type="memoir", payload={"message_id": 100})
        return None

    async def forbidden_save(*args, **kwargs):
        raise AssertionError("message without reply must not become memoir")

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(messages, "_get_persisted_interaction", memoir_state)
    monkeypatch.setattr(messages, "_save_memoir_answer", forbidden_save)
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda _: False)

    msg = FakeMessage("Это обычное сообщение", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert msg.answers[-1][0] == "Обычный ответ"


@pytest.mark.asyncio
async def test_explicit_reply_to_persisted_memoir_is_saved(monkeypatch):
    saved = []
    cleared = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def memoir_state(user_id, state_type):
        if state_type == "memoir":
            return SimpleNamespace(state_type="memoir", payload={"message_id": 100})
        return None

    async def fake_clear(user_id, state_type):
        cleared.append((user_id, state_type))

    async def fake_save(user_id, text, timezone):
        saved.append((user_id, text, timezone))

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "_get_persisted_interaction", memoir_state)
    monkeypatch.setattr(messages, "_clear_persisted_interaction", fake_clear)
    monkeypatch.setattr(messages, "_save_memoir_answer", fake_save)

    reply = SimpleNamespace(message_id=100)
    msg = FakeMessage("Сегодня помог родителям", user_id=42, reply_to_message=reply)
    await messages.process_text_message(42, msg.text, msg)

    assert saved == [(42, "Сегодня помог родителям", "Europe/Moscow")]
    assert cleared == [(42, "memoir")]
    assert "Записано в мемуарник" in msg.answers[-1][0]


@pytest.mark.asyncio
async def test_incomplete_mutation_never_reuses_old_context(monkeypatch):
    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)

    msg = FakeMessage("создай задачу", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert "Уточни" in msg.answers[-1][0]
    assert [item["role"] for item in get_history(42)] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_greeting_does_not_get_captured_by_pending_chronometry(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(content="И тебе доброе утро.")

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    async def forbidden_chrono(*args, **kwargs):
        raise AssertionError("Greeting must not be routed to chronometry")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", forbidden_chrono)

    msg = FakeMessage("Доброе утро!", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert msg.answers[-1][0] == "И тебе доброе утро."


@pytest.mark.asyncio
async def test_task_request_bypasses_pending_chronometry(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append(function_call)
        return "Задача создана: Купить смеситель ✅"

    async def forbidden_chrono(*args, **kwargs):
        raise AssertionError("Task request must not be routed to chronometry")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: True)
    monkeypatch.setattr(chronometry_scheduler, "get_chrono_message_id", lambda user_id: 100)
    monkeypatch.setattr(chronometry_handler, "process_chronometry_response", forbidden_chrono)

    msg = FakeMessage("Надо купить смеситель", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert calls == [
        {
            "name": "create_task",
            "arguments": {
                "title": "Купить смеситель",
                "category": "personal",
                "priority": "normal",
            },
        }
    ]
    assert msg.answers[-1][0] == "Задача создана: Купить смеситель ✅"


@pytest.mark.asyncio
async def test_project_completion_waits_for_title_and_dispatches(monkeypatch):
    calls = []

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_dispatch(function_call, user_id, user_tz):
        calls.append(function_call)
        return "Слон «Настройка Телеграм бота DailyPlanner» закрыт ✅"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: False)

    first = FakeMessage("Слона тоже закрыли", user_id=42)
    await messages.process_text_message(42, first.text, first)
    assert first.answers[-1][0] == "Какой слон закрываем? Напиши название проекта."

    second = FakeMessage("Настройка Телеграм бота DailyPlanner", user_id=42)
    await messages.process_text_message(42, second.text, second)

    assert calls == [
        {
            "name": "complete_project",
            "arguments": {"search_query": "Настройка Телеграм бота DailyPlanner"},
        }
    ]
    assert second.answers[-1][0] == "Слон «Настройка Телеграм бота DailyPlanner» закрыт ✅"


@pytest.mark.asyncio
async def test_free_text_fake_task_creation_is_blocked(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(content="Создаю задачу: Настройка почты ✅\n\nВсё верно?")

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: False)

    msg = FakeMessage("какая-то сложная формулировка", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert "Не понял" in msg.answers[-1][0]
    assert get_history(42)[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_failed_mutation_is_closed_in_history_before_next_turn(monkeypatch):
    calls = []

    class Client:
        async def chat(self, *, messages, functions, tool_choice=None):
            calls.append((list(messages), tool_choice))
            if len(calls) == 1:
                return FakeResponse(content="Не уверен, что нужно сделать")
            if len(calls) == 2:
                return FakeResponse(
                    function_calls=[
                        {
                            "name": "respond_to_user",
                            "arguments": {"message": "Когда напомнить и о чём?"},
                        }
                    ]
                )
            return FakeResponse(
                function_calls=[
                    {
                        "name": "respond_to_user",
                        "arguments": {"message": "Я на связи."},
                    }
                ]
            )

    class Queue:
        async def submit(self, priority, coro):
            return await coro

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    async def fake_dispatch(function_call, user_id, user_tz):
        return function_call["arguments"]["message"]

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(messages, "llm_client", Client())
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda _: False)

    first = FakeMessage("запомни что-нибудь важное", user_id=42)
    await messages.process_text_message(42, first.text, first)
    assert first.answers[-1][0] == "Когда напомнить и о чём?"
    assert [item["role"] for item in get_history(42)] == ["user", "assistant"]

    second = FakeMessage("как дела?", user_id=42)
    await messages.process_text_message(42, second.text, second)

    third_call_messages = calls[2][0]
    assert third_call_messages[-3:] == [
        {"role": "user", "content": "запомни что-нибудь важное"},
        {"role": "assistant", "content": "Когда напомнить и о чём?"},
        {"role": "user", "content": "как дела?"},
    ]
    assert second.answers[-1][0] == "Я на связи."


@pytest.mark.asyncio
async def test_llm_multiple_function_calls_are_dispatched(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(
                function_calls=[
                    {"name": "create_task", "arguments": {"title": "А"}},
                    {"name": "create_reminder", "arguments": {"message": "Б"}},
                ]
            )

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    dispatched = []

    async def fake_dispatch(fc, user_id, tz):
        dispatched.append(fc["name"])
        return f"done:{fc['name']}"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: False)

    msg = FakeMessage("создай задачу и напоминание", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert dispatched == ["create_task", "create_reminder"]
    assert msg.answers[-1][0] == "done:create_task\n\ndone:create_reminder"


@pytest.mark.asyncio
async def test_confirm_delete_result_builds_keyboard(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            return FakeResponse(function_calls=[{"name": "delete_task", "arguments": {}}])

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_log(*args, **kwargs):
        return None

    async def fake_dispatch(fc, user_id, tz):
        return "CONFIRM_DELETE:abc123:Старая задача"

    class FakeKeyboard:
        def as_markup(self):
            return "keyboard"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(callbacks, "build_delete_confirm_keyboard", lambda task_id: FakeKeyboard())
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: False)

    msg = FakeMessage("удали старую задачу", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert msg.answers[-1][0] == "Нашёл задачу «Старая задача». Удалить?"
    assert msg.answers[-1][1]["reply_markup"] == "keyboard"


@pytest.mark.asyncio
async def test_llm_unavailable_message(monkeypatch):
    class Queue:
        async def submit(self, priority, coro):
            try:
                await coro
            except AssertionError:
                pass
            raise LLMUnavailableError("down")

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda user_id: False)

    msg = FakeMessage("что у меня на сегодня", user_id=42)
    await messages.process_text_message(42, msg.text, msg)

    assert "AI-сервис временно недоступен" in msg.answers[-1][0]
