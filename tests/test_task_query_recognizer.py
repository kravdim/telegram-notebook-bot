import pytest

from bot.application.task_query_recognizer import extract_task_list_scope


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("А какие еще задачи есть?", "today"),
        ("Какие ещё дела остались на сегодня?!", "today"),
        ("Что осталось сделать сегодня?", "today"),
        ("Покажи все открытые задачи", "all"),
        ("Какие у меня вообще дела?", "all"),
        ("Покажи просроченные задачи", "overdue"),
        ("Какие задачи выполнены сегодня?", "done_today"),
        ("Что выполнено сегодня?", "done_today"),
    ],
)
def test_task_list_scope_contract(text, scope):
    assert extract_task_list_scope(text) == scope


@pytest.mark.parametrize(
    "text",
    [
        "Создай задачу купить молоко",
        "Какие задачи создать на завтра?",
        "Задачу сделал",
        "Расскажи, что такое задача",
        "Привет!",
        "✅",
    ],
)
def test_task_list_scope_rejects_mutations_and_unrelated_text(text):
    assert extract_task_list_scope(text) is None
