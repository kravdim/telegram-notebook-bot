from types import SimpleNamespace

import pytest

from bot.db.crud.diary import hybrid_search_diary
from bot.db.crud.knowledge import _topic_hint
from bot.db.crud.memoir import hybrid_search_memoir
from bot.db.crud.notes import hybrid_search_notes


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalarResult(self._rows)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *args, **kwargs):
        return FakeResult(self.rows)


@pytest.mark.asyncio
async def test_note_search_without_embedding_returns_model_objects():
    row = SimpleNamespace(title="Почта", content="Настройка почты")
    result = await hybrid_search_notes(FakeSession([row]), 1, "почта", query_embedding=None)
    assert result == [row]


@pytest.mark.asyncio
async def test_diary_search_without_embedding_returns_model_objects():
    row = SimpleNamespace(content="Обедал и работал")
    result = await hybrid_search_diary(FakeSession([row]), 1, "обед", query_embedding=None)
    assert result == [row]


@pytest.mark.asyncio
async def test_memoir_search_without_embedding_returns_model_objects():
    row = SimpleNamespace(content="Главное событие дня")
    result = await hybrid_search_memoir(FakeSession([row]), 1, "событие", query_embedding=None)
    assert result == [row]


def test_elephant_advice_query_has_explicit_topic_boost():
    assert _topic_hint("как съесть этого слона если не хочется начинать") == "слоны"
