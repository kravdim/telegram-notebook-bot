"""Focused unit coverage for command dispatch and task/reminder coordination."""

from types import SimpleNamespace

import pendulum
import pytest

from bot.application.command_bus import CommandBus, CommandContext, CommandResult
from bot.application.intents import intent_from_parts
from bot.llm import dispatcher
from bot.services import tasks
from bot.services.interactions import InteractionService
from tests.fakes import FakeSessionContext


@pytest.mark.asyncio
async def test_dispatch_result_validates_before_bus_and_returns_safe_error(monkeypatch):
    called = False

    class Bus:
        async def execute(self, intent, context):
            nonlocal called
            called = True
            return CommandResult("should not run")

    monkeypatch.setattr(dispatcher, "_get_command_bus", lambda: Bus())

    result = await dispatcher.dispatch_result(
        {"name": "create_reminder", "arguments": '{"message": ""}'}, 11
    )

    assert result.kind == "error"
    assert "Ошибка распознавания" in result.text
    assert called is False


@pytest.mark.asyncio
async def test_dispatch_result_passes_typed_reminder_and_context_to_bus(monkeypatch):
    captured = {}

    class Bus:
        async def execute(self, intent, context):
            captured["intent"] = intent
            captured["context"] = context
            return CommandResult("Напоминание установлено")

    monkeypatch.setattr(dispatcher, "_get_command_bus", lambda: Bus())

    text = await dispatcher.dispatch(
        {
            "name": "create_reminder",
            "arguments": '{"message":"позвонить", "remind_at":"2030-01-01T10:00:00"}',
        },
        user_id=91,
        user_timezone="Asia/Tokyo",
    )

    assert text == "Напоминание установлено"
    assert captured["intent"].name == "create_reminder"
    assert captured["intent"].arguments()["message"] == "позвонить"
    assert captured["context"] == CommandContext(91, "Asia/Tokyo")


@pytest.mark.asyncio
async def test_registered_intent_preserves_typed_delete_result(monkeypatch):
    async def legacy_handler(user_id, args, timezone):
        assert (user_id, args, timezone) == (4, {"search_query": "счёт"}, "UTC")
        return CommandResult("Удалить?", "confirm_delete", {"task_id": "task-7", "title": "Счёт"})

    monkeypatch.setitem(dispatcher._COMMAND_EXECUTORS, "delete_task", legacy_handler)
    result = await dispatcher._execute_registered_intent(
        CommandContext(4, "UTC"),
        intent_from_parts("delete_task", {"search_query": "счёт"}),
    )

    assert result.kind == "confirm_delete"
    assert result.dict_payload() == {"task_id": "task-7", "title": "Счёт"}


@pytest.mark.asyncio
async def test_command_bus_rejects_duplicate_registration_and_unknown_route():
    bus = CommandBus()

    async def handler(context, intent):
        return CommandResult("ok")

    bus.register("complete_task", handler)
    with pytest.raises(ValueError, match="already registered"):
        bus.register("complete_task", handler)
    with pytest.raises(LookupError, match="No handler"):
        await bus.execute(
            intent_from_parts("delete_task", {"search_query": "отчёт"}),
            CommandContext(1),
        )


@pytest.mark.asyncio
async def test_create_reminder_rejects_past_time_without_opening_session(monkeypatch):
    opened = False

    def unexpected_session():
        nonlocal opened
        opened = True
        raise AssertionError("database must not be touched for invalid reminder")

    monkeypatch.setattr(dispatcher, "async_session", unexpected_session)
    result = await dispatcher._handle_create_reminder(
        3, {"message": "встреча", "remind_at": "2000-01-01T10:00:00"}, "UTC"
    )

    assert result == "Время напоминания в прошлом. Уточни."
    assert opened is False


@pytest.mark.asyncio
async def test_create_reminder_persists_validated_payload(monkeypatch):
    captured = {}

    async def create(session, user_id, **kwargs):
        captured.update(session=session, user_id=user_id, **kwargs)

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "create_reminder", create)
    result = await dispatcher._handle_create_reminder(
        12,
        {"message": "вода", "remind_at": "2030-04-05T09:30:00", "repeat_rule": "daily"},
        "Europe/Moscow",
    )

    assert result.startswith("Напоминание установлено: вода")
    assert captured["user_id"] == 12
    assert captured["message"] == "вода"
    assert captured["repeat_rule"] == "daily"
    assert captured["remind_at"].strftime("%Y-%m-%d %H:%M") == "2030-04-05 09:30"


@pytest.mark.asyncio
async def test_task_handlers_fail_fast_for_missing_or_invalid_input(monkeypatch):
    def unexpected_session():
        raise AssertionError("invalid task command must not reach database")

    monkeypatch.setattr(dispatcher, "async_session", unexpected_session)

    assert await dispatcher._handle_complete_task(1, {}, "UTC") == (
        "Уточни название задачи, которую нужно выполнить."
    )
    assert await dispatcher._handle_update_task(1, {"search_query": "x", "updates": {}}, "UTC") == (
        "Не указаны изменения."
    )
    assert await dispatcher._handle_delete_task(1, {}) == "Укажи какую задачу удалить."
    assert await dispatcher._handle_create_task(1, {"title": "<b> </b>"}, "UTC") == (
        "Заголовок не может быть пустым."
    )


@pytest.mark.asyncio
async def test_interaction_get_filters_wrong_workflow_after_read(monkeypatch):
    state = SimpleNamespace(state_type="memoir")
    calls = []

    async def get_state(session, user_id):
        calls.append((session, user_id))
        return state

    monkeypatch.setattr("bot.services.interactions.async_session", lambda: FakeSessionContext())
    monkeypatch.setattr("bot.services.interactions.get_state", get_state)

    assert await InteractionService().get(77, "voice_edit") is None
    assert len(calls) == 1 and calls[0][1] == 77


@pytest.mark.asyncio
async def test_interaction_transition_forwards_token_and_ttl(monkeypatch):
    captured = {}
    expected = SimpleNamespace(state_type="voice_edit")

    async def transition(*args):
        captured["args"] = args
        return expected

    monkeypatch.setattr("bot.services.interactions.async_session", lambda: FakeSessionContext())
    monkeypatch.setattr("bot.services.interactions.transition_state", transition)

    actual = await InteractionService().transition(
        5, "voice_processing", "voice_edit", {"draft": "x"}, 7, "token-1"
    )

    assert actual is expected
    assert captured["args"][1:] == (5, "voice_processing", "voice_edit", {"draft": "x"}, 7, "token-1")


def test_task_service_status_and_reminder_offset_policy():
    assert tasks.closed_task_status(SimpleNamespace(status="done", resolution=None)) == "уже выполнена"
    assert tasks.closed_task_status(SimpleNamespace(status="open", resolution="cancelled")) == "уже отменена"
    assert tasks.closed_task_status(SimpleNamespace(status="archived", resolution=None)) == "уже закрыта (статус: archived)"

    now = pendulum.datetime(2030, 1, 1, 10, tz="UTC")
    next_at = pendulum.datetime(2030, 1, 2, 9, tz="UTC")
    original = pendulum.datetime(2029, 12, 31, 8, tz="UTC")
    assert tasks._next_reminder_for_occurrence(next_at, original, next_at, now) == now


@pytest.mark.asyncio
async def test_completion_workflow_missing_task_has_no_mutation():
    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        committed = False

        async def execute(self, statement):
            return Result()

        async def commit(self):
            self.committed = True

    session = Session()
    result = await tasks.complete_task_workflow(session, "unused-task-id", 8)

    assert result.task is None
    assert result.completed is False
    assert session.committed is False


@pytest.mark.asyncio
async def test_note_and_diary_handlers_persist_sanitized_user_content(monkeypatch):
    captured = {}

    async def create_note(session, user_id, **kwargs):
        captured["note"] = (session, user_id, kwargs)
        return SimpleNamespace(title=kwargs["title"])

    async def create_diary_entry(session, user_id, **kwargs):
        captured["diary"] = (session, user_id, kwargs)

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "create_note", create_note)
    monkeypatch.setattr(dispatcher, "create_diary_entry", create_diary_entry)

    note_result = await dispatcher._handle_create_note(
        14, {"content": "мысли", "title": " <b>План</b> ", "tags": ["work"]}
    )
    diary_result = await dispatcher._handle_create_diary(14, {"content": "сделал отчёт"}, "UTC")

    assert note_result == "Заметка сохранена ✅ (План)"
    assert captured["note"][1:] == (14, {"content": "мысли", "title": "План", "tags": ["work"]})
    assert diary_result == "Записано в дневник ✅"
    assert captured["diary"][1:] == (14, {"content": "сделал отчёт", "tz": "UTC"})


@pytest.mark.asyncio
async def test_note_and_diary_empty_content_do_not_open_session(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "async_session",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected database access")),
    )

    assert await dispatcher._handle_create_note(1, {"content": "  "}) == "Заметка не может быть пустой."
    assert await dispatcher._handle_create_diary(1, {"content": ""}) == "Запись не может быть пустой."


@pytest.mark.asyncio
async def test_update_task_parses_allowed_fields_and_reports_cancelled(monkeypatch):
    captured = {}
    task = SimpleNamespace(id="task-1", title="Отчёт")

    async def search(session, user_id, query):
        return [task]

    async def update(session, task_id, user_id, **updates):
        captured.update(task_id=task_id, user_id=user_id, updates=updates)
        return SimpleNamespace(title="Новый отчёт", scheduled_date=None, due_date=None)

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", search)
    monkeypatch.setattr(dispatcher, "crud_update_task", update)

    result = await dispatcher._handle_update_task(
        6,
        {
            "search_query": "Отчёт",
            "updates": {"title": "<i>Новый отчёт</i>", "status": "cancelled", "ignored": "x"},
        },
        "UTC",
    )

    assert result == "Задача «Новый отчёт» отменена ✅"
    assert captured == {
        "task_id": "task-1",
        "user_id": 6,
        "updates": {"title": "Новый отчёт", "status": "cancelled"},
    }


@pytest.mark.asyncio
async def test_task_update_and_delete_return_not_found_from_search(monkeypatch):
    async def no_tasks(session, user_id, query):
        return []

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", no_tasks)

    assert await dispatcher._handle_update_task(2, {"search_query": "пропала", "updates": {"priority": "high"}}) == (
        "Не нашёл задачу «пропала»."
    )
    assert await dispatcher._handle_delete_task(2, {"search_query": "пропала"}) == "Не нашёл задачу «пропала»."


@pytest.mark.asyncio
async def test_list_today_tasks_formats_priority_time_and_overdue_marker(monkeypatch):
    async def today_tasks(session, user_id, today):
        return [
            SimpleNamespace(
                title="Срочно", is_frog=False, priority="high", due_time=pendulum.time(9, 5),
                scheduled_date=today.subtract(days=1), due_date=None,
            ),
            SimpleNamespace(
                title="Главное", is_frog=True, priority="normal", due_time=None,
                scheduled_date=today, due_date=None,
            ),
        ]

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "get_today_tasks", today_tasks)
    result = await dispatcher._handle_list_tasks(19, {"scope": "today"}, "UTC")

    assert "🔴 Срочно ⏰ 09:05 ⚠️ с" in result
    assert "🐸 Главное" in result


@pytest.mark.asyncio
async def test_project_creation_normalizes_category_and_returns_ui_protocol(monkeypatch):
    captured = {}

    async def search(session, user_id, title, status):
        return []

    async def create(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="project-3", title=kwargs["title"])

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_projects", search)
    monkeypatch.setattr(dispatcher, "crud_create_project", create)
    result = await dispatcher._handle_create_project(10, {"title": " <b>Ремонт</b> ", "category": "other"})

    assert result.kind == "project_created"
    assert result.payload == {"project_id": "project-3", "title": "Ремонт"}
    assert captured == {"user_id": 10, "title": "Ремонт", "description": "", "category": "work"}


@pytest.mark.asyncio
async def test_complete_project_not_found_and_already_closed_paths(monkeypatch):
    done_project = SimpleNamespace(title="Архив", status="done")
    calls = []

    async def search(session, user_id, query, status):
        calls.append(status)
        return [] if status == "active" else [done_project]

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_projects", search)
    assert await dispatcher._handle_complete_project(2, {"search_query": "Архив"}) == "Слон «Архив» уже был закрыт ✅"
    assert calls == ["active", None]


@pytest.mark.asyncio
async def test_birthday_handler_persists_parsed_date_and_optional_note(monkeypatch):
    captured = {}

    async def add_birthday(session, user_id, **kwargs):
        captured.update(user_id=user_id, **kwargs)

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr("bot.db.crud.birthdays.add_birthday", add_birthday)
    result = await dispatcher._handle_add_birthday(
        30, {"name": "Мама", "date": "2030-03-15", "note": "позвонить", "year_known": False}, "UTC"
    )

    assert result.startswith("🎂 Запомнил: Мама — 15.03")
    assert "📝 позвонить" in result
    assert captured == {
        "user_id": 30,
        "name": "Мама",
        "birth_date": pendulum.date(2030, 3, 15),
        "note": "позвонить",
        "year_known": False,
    }
