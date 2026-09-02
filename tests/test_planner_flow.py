from datetime import time
from types import SimpleNamespace

from bot.handlers.messages import (
    _extract_cancel_request,
    _extract_done_query,
    _extract_reschedule_request,
    _extract_task_request,
)
from bot.llm.dispatcher import _format_open_today_state


def task(title, *, is_frog=False, priority="normal", due_time=None):
    return SimpleNamespace(
        title=title,
        is_frog=is_frog,
        priority=priority,
        due_time=due_time,
    )


def test_extract_done_query_dash_forms():
    assert _extract_done_query("Разобраться с Онлайн кассой - сделал") == "Разобраться с Онлайн кассой"
    assert _extract_done_query("Купить Телинстар — готово") == "Купить Телинстар"
    assert _extract_done_query("Подключить онлайн-кассу – закрыто") == "Подключить онлайн-кассу"
    assert _extract_done_query("Разобраться с Онлайн кассой - решено") == "Разобраться с Онлайн кассой"


def test_extract_done_query_prefix_forms():
    assert _extract_done_query("готово Купить Телинстар") == "Купить Телинстар"
    assert _extract_done_query("закрыл: Подключить онлайн-кассу") == "Подключить онлайн-кассу"
    assert _extract_done_query("решил Подключить онлайн-кассу") == "Подключить онлайн-кассу"


def test_extract_done_query_conversational_payment_forms():
    assert _extract_done_query("Денег Фокусу заплатили") == "Денег Фокусу"
    assert _extract_done_query("Зарплаты выплатили") == "Зарплаты"
    assert _extract_done_query("Мувикс маме настроил") == "Мувикс маме"
    assert _extract_done_query("Мувикс маме я настроил") == "Мувикс маме"


def test_extract_done_query_ignores_chronometry_text():
    assert _extract_done_query("Обедаю") is None
    assert _extract_done_query("воюю с почтой, что-то не работает") is None


def test_extract_task_request_common_forms():
    assert _extract_task_request("Надо купить смеситель", "Europe/Moscow") == {
        "title": "Купить смеситель",
        "category": "personal",
        "priority": "normal",
    }

    today_task = _extract_task_request(
        "Надо сегодня настроить почту altair-bot.ru", "Europe/Moscow"
    )
    assert today_task["title"] == "Настроить почту altair-bot.ru"
    assert today_task["category"] == "work"
    assert today_task["priority"] == "normal"
    assert today_task["scheduled_date"]


def test_extract_task_request_removes_recognized_qualifiers_from_title():
    tomorrow = _extract_task_request("Надо купить молоко завтра", "Europe/Moscow")
    assert tomorrow is not None
    assert tomorrow["title"] == "Купить молоко"
    assert tomorrow["scheduled_date"]

    high = _extract_task_request(
        "Надо отправить отчёт, приоритет высокий", "Europe/Moscow"
    )
    assert high == {
        "title": "Отправить отчёт",
        "category": "work",
        "priority": "high",
    }


def test_extract_task_request_handles_leading_and_trailing_qualifier_matrix():
    cases = (
        ("Надо завтра купить молоко", "Купить молоко", "normal", True),
        ("Завтра надо купить молоко", "Купить молоко", "normal", True),
        ("Надо срочно отправить отчёт", "Отправить отчёт", "high", False),
        ("Надо отправить отчёт срочно", "Отправить отчёт", "high", False),
        ("Надо приоритет средний отправить отчёт", "Отправить отчёт", "medium", False),
        ("Надо отправить отчёт, приоритет обычный", "Отправить отчёт", "normal", False),
        ("Надо срочно отправить отчёт завтра", "Отправить отчёт", "high", True),
    )
    for text, title, priority, has_date in cases:
        result = _extract_task_request(text, "Europe/Moscow")
        assert result is not None
        assert result["title"] == title
        assert result["priority"] == priority
        assert ("scheduled_date" in result) is has_date


def test_extract_task_request_fails_closed_for_unknown_qualifiers():
    assert _extract_task_request(
        "Надо отправить отчёт, приоритет максимальный", "Europe/Moscow"
    ) is None
    assert _extract_task_request("Надо купить молоко послезавтра", "Europe/Moscow") is None


def test_extract_task_request_fails_closed_for_natural_temporal_qualifiers():
    cases = (
        "Надо купить молоко через два дня",
        "Надо сделать отчёт через 2 дня",
        "Надо сделать отчёт на следующей неделе",
        "Надо сделать отчёт 10 сентября",
        "Надо сделать отчёт в следующем месяце",
    )
    for text in cases:
        assert _extract_task_request(text, "Europe/Moscow") is None


def test_extract_task_request_fails_closed_for_conflicting_qualifiers():
    assert _extract_task_request(
        "Завтра надо купить молоко сегодня", "Europe/Moscow"
    ) is None
    assert _extract_task_request(
        "Надо срочно купить молоко, приоритет средний", "Europe/Moscow"
    ) is None


def test_extract_task_request_ignores_activity():
    assert _extract_task_request("Настраиваю компьютер на Силикатном", "Europe/Moscow") is None


def test_extract_reschedule_request_common_forms():
    result = _extract_reschedule_request(
        "Купить смеситель - это на воскресенье же перенесли", "Europe/Moscow"
    )

    assert result["search_query"] == "Купить смеситель"
    assert result["updates"]["scheduled_date"]


def test_extract_cancel_request_common_forms():
    assert _extract_cancel_request("Купить смеситель тоже пока не надо, вроде этот получилось сделать") == {
        "search_query": "Купить смеситель",
        "updates": {"status": "cancelled"},
    }
    assert _extract_cancel_request("перфоратор из офиса брать пока что не надо") == {
        "search_query": "перфоратор из офиса",
        "updates": {"status": "cancelled"},
    }


def test_format_open_today_state_empty():
    assert _format_open_today_state([]) == "На сегодня открытых задач не осталось."


def test_format_open_today_state_uses_only_passed_tasks():
    text = _format_open_today_state(
        [
            task("Съесть лягушку", is_frog=True),
            task("Срочное", priority="high", due_time=time(15, 30)),
            task("Обычное"),
        ]
    )

    assert "Осталось на сегодня: 3" in text
    assert "🐸 Съесть лягушку" in text
    assert "🔴 Срочное 15:30" in text
    assert "📌 Обычное" in text


def test_format_open_today_state_truncates_long_lists():
    tasks = [task(f"Задача {i}") for i in range(7)]
    text = _format_open_today_state(tasks)

    assert "Осталось на сегодня: 7" in text
    assert "Задача 4" in text
    assert "Задача 5" not in text
    assert "... и ещё 2" in text
