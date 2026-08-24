from datetime import date
from types import SimpleNamespace

import pendulum

from bot.formatters.digest import format_morning_digest
from bot.scheduler.chronometry import _awaiting_is_stale, _awaiting_since
from bot.scheduler.digest import _digest_sent_flags
from bot.scheduler.task_reminders import _format_task_reminder, _task_reminder_already_sent


def test_digest_sent_flags_are_independent():
    today = date(2026, 5, 4)
    user = SimpleNamespace(
        digest_sent_date=today,
        digest_evening_sent_date=None,
    )

    assert _digest_sent_flags(user, today) == (True, False)

    user.digest_evening_sent_date = today
    assert _digest_sent_flags(user, today) == (True, True)


def test_digest_evening_marker_does_not_skip_next_morning():
    yesterday = date(2026, 5, 3)
    today = date(2026, 5, 4)
    user = SimpleNamespace(
        digest_sent_date=yesterday,
        digest_evening_sent_date=yesterday,
    )

    assert _digest_sent_flags(user, today) == (False, False)


def test_task_reminder_idempotency_uses_date_and_hour():
    today = date(2026, 5, 4)
    yesterday = date(2026, 5, 3)

    user = SimpleNamespace(tasks_reminder_last_date=today, tasks_reminder_last_hour=11)
    assert _task_reminder_already_sent(user, today, 9) is True
    assert _task_reminder_already_sent(user, today, 11) is True
    assert _task_reminder_already_sent(user, today, 13) is False

    user.tasks_reminder_last_date = yesterday
    user.tasks_reminder_last_hour = 17
    assert _task_reminder_already_sent(user, today, 9) is False


def test_chronometry_awaiting_stale_logic():
    user_id = 123
    now = pendulum.datetime(2026, 5, 4, 12, 0, tz="Europe/Moscow")

    _awaiting_since[user_id] = now.subtract(minutes=30)
    assert _awaiting_is_stale(user_id, now, interval_min=30) is False

    _awaiting_since[user_id] = now.subtract(hours=2)
    assert _awaiting_is_stale(user_id, now, interval_min=30) is True

    _awaiting_since.pop(user_id, None)
    assert _awaiting_is_stale(user_id, now, interval_min=30) is True


def test_task_reminder_format_omits_noise_when_tasks_exist():
    today = date(2026, 5, 4)
    task = SimpleNamespace(
        title="Подключить онлайн-кассу",
        is_frog=False,
        priority="high",
        due_time=None,
        due_date=today,
    )
    completed = [SimpleNamespace(title="Сделать хостинг")]

    text = _format_task_reminder([task], completed, frog=None, today=today, hour=13)

    assert "Актуальные задачи" in text
    assert "✅ Уже сделано: 1" in text
    assert "🔴 Подключить онлайн-кассу" in text
    assert "Все задачи выполнены" not in text


def test_morning_digest_with_project_is_not_free_day():
    project = SimpleNamespace(id="p1", title="Настройка Телеграм бота DailyPlanner")
    text = format_morning_digest(
        today=date(2026, 5, 6),
        tasks=[],
        frog=None,
        projects=[project],
        project_progress={"p1": {"percent": 0}},
        is_weekend=False,
        active_trip=None,
        birthdays=[],
    )

    assert "Слоны" in text
    assert "Сегодня свободный день" not in text
    assert "Задач на сегодня нет" in text
