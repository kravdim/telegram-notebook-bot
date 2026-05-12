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
    assert _extract_done_query("Денег Фокусу заплатили") == "Заплатить деньги Фокусу"
    assert _extract_done_query("Зарплаты выплатили") == "Выплатить зарплаты"
    assert _extract_done_query("Мувикс маме настроил") == "Настроить маме Мувикс"
    assert _extract_done_query("Мувикс маме я настроил") == "Настроить маме Мувикс"


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
        "search_query": "Взять перфоратор из офиса",
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
