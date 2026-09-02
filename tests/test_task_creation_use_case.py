from datetime import datetime
from types import SimpleNamespace

import pendulum
import pytest

from bot.application.task_creation import CreateTaskDependencies, execute_create_task
from bot.db.crud.reminders import is_valid_repeat_rule
from bot.db.crud.tasks import normalize_task_identity
from bot.llm import dispatcher
from tests.fakes import FakeSessionContext


def _dependencies(**overrides):
    async def no_trip(*_args, **_kwargs):
        return None

    async def no_matches(*_args, **_kwargs):
        return []

    async def no_value(*_args, **_kwargs):
        return None

    async def no_count(*_args, **_kwargs):
        return 0, None

    values = {
        "session_factory": lambda: FakeSessionContext(),
        "get_active_trip": no_trip,
        "search_tasks": no_matches,
        "normalize_identity": normalize_task_identity,
        "valid_repeat_rule": is_valid_repeat_rule,
        "update_task": no_value,
        "set_frog": no_value,
        "upsert_task_reminder": no_value,
        "get_frog": no_value,
        "create_task": no_value,
        "create_reminder": no_value,
        "count_similar_completed": no_count,
        "sanitize_title": dispatcher._sanitize_title,
        "validate_title": dispatcher._validate_title,
        "parse_date": dispatcher._parse_date,
        "parse_time": dispatcher._parse_time,
        "parse_datetime": dispatcher._parse_datetime,
        "format_repeat_rule": dispatcher._format_repeat_rule,
        "recurring_comment": lambda *_args: "recurring",
    }
    values.update(overrides)
    return CreateTaskDependencies(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ({"title": " "}, "Заголовок не может быть пустым"),
        ({"title": "Отчёт", "scheduled_date": "bad"}, "дату планирования"),
        ({"title": "Отчёт", "due_date": "bad"}, "дедлайн"),
        ({"title": "Отчёт", "due_time": "bad"}, "формате ЧЧ:ММ"),
        ({"title": "Отчёт", "remind_at": "bad"}, "время напоминания"),
        ({"title": "Отчёт", "scheduled_date": "2000-01-01"}, "прошлом"),
        ({"title": "Отчёт", "repeat_rule": "sometimes"}, "правило повторения"),
    ),
)
async def test_invalid_task_never_opens_database(arguments, message):
    def forbidden_session():
        raise AssertionError("invalid command reached persistence")

    result = await execute_create_task(
        1, arguments, "UTC", _dependencies(session_factory=forbidden_session)
    )
    assert message in result


@pytest.mark.asyncio
async def test_create_task_coordinates_trip_frog_reminder_and_repeat_atomically():
    captured = {}
    old_frog = SimpleNamespace(is_frog=True)

    async def active_trip(*_args, **_kwargs):
        return SimpleNamespace(id="trip-7")

    async def get_frog(*_args, **_kwargs):
        return old_frog

    async def create_task(_session, **kwargs):
        captured["task"] = kwargs
        return SimpleNamespace(id="task-9", title=kwargs["title"])

    async def create_reminder(_session, user_id, **kwargs):
        captured["reminder"] = {"user_id": user_id, **kwargs}

    async def count(*_args, **_kwargs):
        return 3, pendulum.datetime(2026, 9, 1, tz="UTC")

    result = await execute_create_task(
        42,
        {
            "title": "<b>Отправить отчёт</b>",
            "priority": "high",
            "is_frog": True,
            "repeat_rule": "daily",
            "remind_at": "2030-01-01T10:00:00",
            "remind_before_min": 15,
        },
        "UTC",
        _dependencies(
            get_active_trip=active_trip,
            get_frog=get_frog,
            create_task=create_task,
            create_reminder=create_reminder,
            count_similar_completed=count,
        ),
    )

    assert captured["task"]["title"] == "Отправить отчёт"
    assert captured["task"]["trip_id"] == "trip-7"
    assert captured["task"]["scheduled_date"] == pendulum.now("UTC").date()
    assert captured["task"]["commit"] is False
    assert captured["reminder"]["task_id"] == "task-9"
    assert old_frog.is_frog is False
    assert "Лягушка" in result and "Каждый день" in result and "recurring" in result


@pytest.mark.asyncio
async def test_duplicate_task_updates_fields_and_reminder_in_one_session():
    existing = SimpleNamespace(
        id="task-1",
        title="Отчёт",
        scheduled_date=None,
        due_date=None,
        due_time=None,
        is_frog=False,
        priority="normal",
        repeat_rule=None,
    )
    captured = {}

    async def matches(*_args, **_kwargs):
        return [existing]

    async def update(_session, task_id, user_id, **kwargs):
        captured["update"] = (task_id, user_id, kwargs)

    async def set_frog(_session, task_id, user_id, **kwargs):
        captured["frog"] = (task_id, user_id, kwargs)

    async def upsert(_session, user_id, task_id, title, remind_at, repeat, **kwargs):
        captured["reminder"] = (user_id, task_id, title, remind_at, repeat, kwargs)

    result = await execute_create_task(
        5,
        {
            "title": "отчёт",
            "priority": "high",
            "is_frog": True,
            "repeat_rule": "weekdays",
            "remind_at": "2030-02-03T09:30:00",
        },
        "UTC",
        _dependencies(
            search_tasks=matches,
            update_task=update,
            set_frog=set_frog,
            upsert_task_reminder=upsert,
        ),
    )

    assert captured["frog"][0:2] == ("task-1", 5)
    assert captured["update"][2]["priority"] == "high"
    assert captured["update"][2]["repeat_rule"] == "weekdays"
    assert isinstance(captured["update"][2]["remind_at"], datetime)
    assert captured["reminder"][1:3] == ("task-1", "Отчёт")
    assert "приоритет high" in result and "weekdays" in result and "лягушка" in result


@pytest.mark.asyncio
async def test_duplicate_without_changes_is_idempotent():
    existing = SimpleNamespace(
        id="task-1",
        title="Отчёт",
        scheduled_date=None,
        due_date=None,
        due_time=None,
        is_frog=False,
        priority="normal",
        repeat_rule=None,
    )

    async def matches(*_args, **_kwargs):
        return [existing]

    result = await execute_create_task(
        5, {"title": "отчёт"}, "UTC", _dependencies(search_tasks=matches)
    )
    assert result == "Задача «Отчёт» уже существует ✅"
