import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.handlers.commands as commands
import bot.handlers.messages as messages
import bot.handlers.onboarding as onboarding
import bot.handlers.voice as voice
import bot.main as main_module
import bot.scheduler.chronometry as chronometry_scheduler
import bot.scheduler.memoir as memoir_scheduler
from bot.llm.client import LLMResponse
from bot.llm.dispatcher import _extract_value_tag
from bot.observability import MetricsRegistry, TelegramConflictAlertHandler
from tests.fakes import FakeMessage, FakeSessionContext


def test_fake_mutation_guard_covers_beta_entities():
    assert messages._looks_like_fake_mutation("День рождения мамы сохранён 🎂")
    assert messages._looks_like_fake_mutation("Напоминание установлено")
    assert messages._looks_like_fake_mutation("Заметку сохранил")
    assert messages._looks_like_fake_mutation("Слон создан")


def test_recurring_action_and_relative_reminder_are_mutation_requests():
    assert messages._looks_like_mutation_request(
        "Каждый будний день в 9 утра принимать витамины"
    )
    assert messages._looks_like_mutation_request("Напомни через 2 минуты попить воды")
    assert not messages._looks_like_mutation_request("Как сделать план на неделю?")
    assert not messages._looks_like_mutation_request("Какая встреча у меня сегодня?")


@pytest.mark.asyncio
async def test_mutation_without_tool_retries_with_required_tool(monkeypatch):
    calls = []
    dispatched = []

    class Client:
        async def chat(self, *args, **kwargs):
            calls.append(kwargs.get("tool_choice"))
            if kwargs.get("tool_choice") == "required":
                return LLMResponse(
                    function_calls=[
                        {
                            "name": "add_birthday",
                            "arguments": {
                                "name": "мама",
                                "date": "1900-03-15",
                                "year_known": False,
                            },
                        }
                    ],
                    model="test",
                )
            return LLMResponse(content="День рождения мамы сохранён", model="test")

    class Queue:
        async def submit(self, priority, coro):
            return await coro

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_dispatch(fc, user_id, tz):
        dispatched.append(fc["name"])
        return "День рождения сохранён ✅"

    async def fake_log(*args, **kwargs):
        return None

    async def no_state(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(messages, "llm_client", Client())
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(messages, "_get_persisted_interaction", no_state)
    monkeypatch.setattr(messages, "_consume_pending_interaction", no_state)
    monkeypatch.setattr(voice, "_load_voice_state", no_state)
    monkeypatch.setattr(voice, "_clear_voice_state", no_state)
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda _: False)
    monkeypatch.setattr(memoir_scheduler, "is_awaiting_memoir", lambda _: False)

    msg = FakeMessage("Запомни: у мамы день рождения 15 марта", user_id=42)
    await messages._process_text_message_unlocked(42, msg.text, msg)

    assert calls == [None, "required"]
    assert dispatched == ["add_birthday"]
    assert msg.answers[-1][0] == "День рождения сохранён ✅"


@pytest.mark.asyncio
async def test_project_decomposition_has_no_duplicate_confirmation(monkeypatch):
    import bot.db.crud.projects as project_crud
    import bot.llm.decompose as decompose

    class Client:
        async def chat(self, *args, **kwargs):
            return LLMResponse(
                function_calls=[
                    {
                        "name": "create_project",
                        "arguments": {"title": "Годовой отчёт"},
                    }
                ],
                model="test",
            )

    class Queue:
        async def submit(self, priority, coro):
            return await coro

    async def fake_get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def fake_get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def fake_dispatch(fc, user_id, tz):
        return "PROJECT_CREATED:00000000-0000-0000-0000-000000000001:Годовой отчёт"

    async def fake_project(session, project_id):
        return SimpleNamespace(description="", category="work")

    async def fake_decompose(*args, **kwargs):
        return ["Собрать данные", "Проверить цифры"]

    async def fake_create_tasks(*args, **kwargs):
        return 2

    async def fake_log(*args, **kwargs):
        return None

    async def no_state(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", fake_get_user)
    monkeypatch.setattr(messages, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(messages, "dispatch", fake_dispatch)
    monkeypatch.setattr(messages, "llm_client", Client())
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(messages, "_get_persisted_interaction", no_state)
    monkeypatch.setattr(messages, "_consume_pending_interaction", no_state)
    monkeypatch.setattr(voice, "_load_voice_state", no_state)
    monkeypatch.setattr(voice, "_clear_voice_state", no_state)
    monkeypatch.setattr(project_crud, "get_project_by_id", fake_project)
    monkeypatch.setattr(decompose, "decompose_project", fake_decompose)
    monkeypatch.setattr(decompose, "create_project_tasks", fake_create_tasks)
    monkeypatch.setattr("bot.db.crud.llm_logs.log_llm_request", fake_log)
    monkeypatch.setattr(chronometry_scheduler, "is_awaiting_response", lambda _: False)
    monkeypatch.setattr(memoir_scheduler, "is_awaiting_memoir", lambda _: False)

    msg = FakeMessage("Создай слона: годовой отчёт", user_id=42)
    await messages._process_text_message_unlocked(42, msg.text, msg)

    assert len(msg.answers) == 2
    assert "Сейчас нарезаю" in msg.answers[0][0]
    assert "Нарезано 2 бифштексов" in msg.answers[1][0]


@pytest.mark.asyncio
async def test_memoir_state_ttl_is_one_hour(monkeypatch):
    captured = {}

    async def fake_set_state(session, user_id, state_type, payload, ttl_minutes):
        captured.update(
            user_id=user_id,
            state_type=state_type,
            payload=payload,
            ttl_minutes=ttl_minutes,
        )

    monkeypatch.setattr(memoir_scheduler, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr("bot.db.crud.interaction_states.set_state", fake_set_state)

    await memoir_scheduler._persist_memoir_state(42, 100)
    assert captured["ttl_minutes"] == 60
    assert captured["payload"] == {"message_id": 100}


def test_memoir_command_detection_does_not_consume_task_text():
    assert messages._looks_like_mutation_request("Встреча в 15, заехать в банкомат")
    assert messages._looks_like_mutation_request(
        "Сделай заметку: идея подарка маме — книга по садоводству"
    )


def test_value_tag_does_not_treat_generic_meeting_as_friendship():
    assert _extract_value_tag("Встреча в 15, заехать в банкомат") != "дружба"


@pytest.mark.asyncio
async def test_voice_reports_progress_and_times_out(monkeypatch):
    class STT:
        async def transcribe(self, path):
            await asyncio.sleep(10)

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    class Bot:
        async def send_chat_action(self, chat_id, action):
            return None

        async def get_file(self, file_id):
            return SimpleNamespace(file_path="voice.ogg")

        async def download_file(self, file_path, destination):
            destination.write_bytes(b"voice")

    msg = FakeMessage(user_id=42)
    msg.bot = Bot()
    msg.voice = SimpleNamespace(file_id="abc", file_size=10)
    monkeypatch.setattr(voice, "_stt_client", STT())
    monkeypatch.setattr(voice.asyncio, "wait_for", fake_wait_for)

    await voice.handle_voice(msg)

    assert msg.answers[0][0] == "🎤 Распознаю голосовое…"
    assert "слишком много времени" in msg.answers[-1][0]


def test_onboarding_name_validation_rejects_beta_runner_phrase():
    assert onboarding._is_valid_name("Дмитрий")
    assert onboarding._is_valid_name("Анна-Мария")
    assert not onboarding._is_valid_name("Протестировать бота от и до")


@pytest.mark.asyncio
async def test_onboarding_resume_repeats_current_question():
    class State:
        async def get_data(self):
            return {}

    msg = FakeMessage(user_id=42)
    await onboarding._resend_current_step(
        msg, State(), onboarding.OnboardingStates.step_name.state
    )
    assert "Как тебя зовут" in msg.answers[-1][0]


def test_notes_include_title_and_content():
    note = SimpleNamespace(
        title="Идея подарка маме",
        content="Книга по садоводству",
        created_at=datetime(2026, 8, 21),
        tags=[],
    )
    line = commands._format_note_line(note)
    assert "Идея подарка маме — Книга по садоводству" in line


def test_tmux_runtime_is_rejected_unless_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux.sock")
    monkeypatch.delenv("DAILYPLANNER_ALLOW_TMUX", raising=False)
    assert main_module._tmux_runtime_disallowed()
    monkeypatch.setenv("DAILYPLANNER_ALLOW_TMUX", "1")
    assert not main_module._tmux_runtime_disallowed()


@pytest.mark.asyncio
async def test_polling_conflict_emits_metric_and_alert(monkeypatch):
    import bot.observability as observability

    event = asyncio.Event()

    async def fake_alert(bot):
        event.set()

    monkeypatch.setattr(observability, "metrics", MetricsRegistry())
    monkeypatch.setattr(observability, "_alert_telegram_conflict", fake_alert)
    handler = TelegramConflictAlertHandler(object(), asyncio.get_running_loop())
    record = logging.LogRecord(
        "aiogram.dispatcher",
        logging.ERROR,
        __file__,
        1,
        "Failed to fetch updates - TelegramConflictError: conflict",
        (),
        None,
    )
    handler.emit(record)
    await asyncio.wait_for(event.wait(), timeout=1)
    assert observability.metrics.snapshot()["counters"]["telegram.polling_conflict"] == 1
