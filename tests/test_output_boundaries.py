from html import unescape
from types import SimpleNamespace

import pytest

from bot.application.intents import UpdateTaskIntent
from bot.formatters import split_html_message, split_message
from bot.llm import dispatcher
from tests.fakes import FakeSessionContext


@pytest.mark.parametrize("field", ["scheduled_date", "due_date", "due_time"])
def test_explicit_clear_survives_intent_boundary(field):
    command = UpdateTaskIntent(search_query="task", updates={field: None, "is_frog": False})
    assert command.arguments()["updates"] == {field: None, "is_frog": False}


@pytest.mark.parametrize("source", [
    '<a href="' + 'x' * 4096 + '">text</a>',
    '<b>' * 100 + 'text' + '</b>' * 100,
    '&' + 'x' * 500 + ';',
])
def test_oversized_markup_falls_back_without_loss(source):
    chunks = split_html_message(source, 100)
    assert all(0 < len(part) <= 100 for part in chunks)
    assert unescape(''.join(chunks)) == source


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limits_fail_immediately(limit):
    with pytest.raises(ValueError):
        split_message("test", limit)
    with pytest.raises(ValueError):
        split_html_message("test", limit)


@pytest.mark.asyncio
async def test_project_confirmation_preserves_colons(monkeypatch):
    title = "Проект: этап 1: выпуск"

    async def projects(*args, **kwargs):
        return [SimpleNamespace(id="p1", title=title)]

    async def tasks(*args):
        return [SimpleNamespace(status="open")]

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_projects", projects)
    monkeypatch.setattr(dispatcher, "get_project_tasks", tasks)
    result = await dispatcher.dispatch_result(
        {"name": "complete_project", "arguments": {"search_query": title}}, 42
    )
    assert result.kind == "confirm_project_complete"
    assert result.payload == {"project_id": "p1", "title": title, "open_count": 1}
