from datetime import date
from types import SimpleNamespace

import pytest

from bot.formatters.digest import format_morning_digest


def _task(title: str, category: str = "work", scheduled_date: date | None = None):
    return SimpleNamespace(
        title=title,
        priority="normal",
        category=category,
        due_time=None,
        due_date=None,
        scheduled_date=scheduled_date or date(2026, 8, 29),
        status="open",
    )


def _digest(*, tasks=None, frog=None, projects=None, trip=None, birthdays=None):
    return format_morning_digest(
        today=date(2026, 8, 29),
        tasks=tasks or [],
        frog=frog,
        projects=projects or [],
        project_progress={},
        is_weekend=True,
        active_trip=trip,
        birthdays=birthdays or [],
    )


@pytest.mark.parametrize(
    "tasks",
    [
        [_task("Личное", "personal")],
        [_task("Рабочее", "work")],
        [_task("Личное", "personal"), _task("Рабочее", "work")],
        [_task("Просроченное", scheduled_date=date(2026, 8, 28))],
    ],
)
def test_weekend_digest_keeps_every_scheduled_task(tasks):
    text = _digest(tasks=tasks)
    assert all(task.title in text for task in tasks)
    assert "Сегодня свободный день" not in text


@pytest.mark.parametrize(
    ("hidden_frog", "hidden_projects"),
    [(_task("Лягушка"), []), (None, [SimpleNamespace(id="p", title="Слон")])],
)
def test_weekend_hidden_coaching_sections_do_not_leave_header_only(
    hidden_frog, hidden_projects
):
    text = _digest(frog=hidden_frog, projects=hidden_projects)
    assert "Сегодня свободный день" in text
    assert "Лягушка дня" not in text
    assert "Слоны:" not in text


def test_weekend_trip_and_birthday_have_truthful_empty_task_state():
    birthday = SimpleNamespace(
        name="Аня",
        note=None,
        birth_date=date(2000, 8, 29),
        year_known=True,
    )
    text = _digest(trip="Москва", birthdays=[birthday])
    assert "Командировка: Москва" in text
    assert "Сегодня день рождения" in text
    assert "Задач на сегодня нет" in text
    assert "Сегодня свободный день" not in text
