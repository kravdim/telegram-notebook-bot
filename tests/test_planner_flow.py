from datetime import time
from types import SimpleNamespace

from bot.handlers.messages import _extract_done_query
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


def test_extract_done_query_ignores_chronometry_text():
    assert _extract_done_query("Обедаю") is None
    assert _extract_done_query("воюю с почтой, что-то не работает") is None


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
