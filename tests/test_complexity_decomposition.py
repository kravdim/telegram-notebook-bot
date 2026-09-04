"""Behavioral contracts for the complexity-decomposition milestones."""

from datetime import date
from types import SimpleNamespace

import pendulum
import pytest

from bot.formatters import split_html_message
from bot.formatters.digest import format_morning_digest
from bot.handlers.messages import _extract_common_intent
from bot.scheduler.weekly_review import _format_review


@pytest.mark.parametrize(
    ("source", "limit", "plain"),
    [
        ("<b>hello world</b>", 10, "hello world"),
        ("<i>one\ntwo three</i>", 11, "one\ntwo three"),
        ("<b>A &amp; B</b>", 12, "A &amp; B"),
    ],
)
def test_html_splitter_preserves_content_and_chunk_limit(source, limit, plain):
    chunks = split_html_message(source, limit)

    assert all(len(chunk) <= limit for chunk in chunks)
    assert "".join(
        chunk.replace("<b>", "").replace("</b>", "")
        .replace("<i>", "").replace("</i>", "")
        for chunk in chunks
    ) == plain


@pytest.mark.parametrize(
    ("weekend", "projects", "expected", "absent"),
    [
        (False, [], "Просроченных: 1", "свободный день"),
        (False, [SimpleNamespace(id="p", title="Проект")], "Слоны:", "свободный день"),
        (True, [SimpleNamespace(id="p", title="Проект")], "свободный день", "Слоны:"),
    ],
)
def test_morning_digest_preserves_weekday_and_weekend_sections(
    weekend, projects, expected, absent
):
    task = SimpleNamespace(
        title="Задача",
        priority="normal",
        due_time=None,
        due_date=date(2026, 9, 1),
        scheduled_date=None,
        status="open",
    )
    tasks = [] if weekend else [task]

    text = format_morning_digest(
        date(2026, 9, 4),
        tasks,
        None,
        projects,
        {"p": {"percent": 25}},
        weekend,
    )

    assert expected in text
    assert absent not in text


@pytest.mark.parametrize(
    ("frogs_total", "frogs_eaten", "expected"),
    [
        (0, 0, "Лягушек на этой неделе не было"),
        (4, 4, "Все лягушки съедены"),
        (4, 3, "Хороший результат"),
        (4, 1, "На следующей неделе можно лучше"),
    ],
)
def test_weekly_review_preserves_frog_feedback(frogs_total, frogs_eaten, expected):
    text = _format_review(
        week_start=pendulum.date(2026, 8, 31),
        chrono_stats={},
        completed_tasks=[],
        frogs_total=frogs_total,
        frogs_eaten=frogs_eaten,
        value_stats=[],
        project_progress={},
    )

    assert expected in text


@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_field"),
    [
        ("Какие задачи на сегодня?", "list_tasks", ("scope", "today")),
        ("Сделай заметку Идея: проверить гипотезу", "create_note", ("title", "Идея")),
        ("Создай задачу: купить фильтр", "create_task", ("title", "купить фильтр")),
        ("Напомни через 15 минут размяться", "create_reminder", ("message", "размяться")),
        ("У мамы день рождения 10 сентября", "add_birthday", ("name", "мама")),
    ],
)
def test_common_intent_decomposition_preserves_extractor_order(
    phrase, expected_intent, expected_field
):
    result = _extract_common_intent(phrase, "Europe/Moscow")

    assert result is not None
    intent, arguments = result
    field, expected_value = expected_field
    assert intent == expected_intent
    assert arguments[field] == expected_value
