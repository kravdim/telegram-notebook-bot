import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from bot.db.crud.reminders import is_valid_repeat_rule
from bot.formatters.digest import format_morning_digest
from bot.formatters import split_html_message
from bot.llm import dispatcher
from bot.llm.queue import LLMQueue, PRIORITY_INTENT
from tests.fakes import FakeSessionContext


@pytest.mark.parametrize(
    "rule",
    [None, "daily", "weekdays", "weekly:1", "weekly:1,3", "monthly:31", "every:2w"],
)
def test_supported_repeat_rules(rule):
    assert is_valid_repeat_rule(rule)


@pytest.mark.parametrize(
    "rule",
    ["RRULE:FREQ=DAILY", "weekly:0", "weekly:8", "monthly:32", "every:0d", "every:2x"],
)
def test_unsupported_repeat_rules_are_rejected(rule):
    assert not is_valid_repeat_rule(rule)


@pytest.mark.asyncio
async def test_llm_timeout_cancels_execution_and_queue_survives():
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    queue = LLMQueue()
    queue.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await queue.submit(PRIORITY_INTENT, slow(), timeout=0.01)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        assert await queue.submit(PRIORITY_INTENT, asyncio.sleep(0, result="ok")) == "ok"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_ambiguous_completion_does_not_change_database(monkeypatch):
    candidates = [
        SimpleNamespace(id="1", title="Подготовить отчёт для банка", status="open"),
        SimpleNamespace(id="2", title="Подготовить отчёт для клиента", status="open"),
    ]

    async def fake_search(session, user_id, query, status=None):
        return candidates

    async def forbidden_complete(*args, **kwargs):
        raise AssertionError("ambiguous task must not be completed")

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", fake_search)
    monkeypatch.setattr(dispatcher, "complete_task_by_id", forbidden_complete)

    result = await dispatcher._handle_complete_task(
        42, {"search_query": "отчёт"}, "Europe/Moscow"
    )
    assert "несколько задач" in result.lower()
    assert "для банка" in result
    assert "для клиента" in result


@pytest.mark.asyncio
async def test_unknown_llm_tool_is_rejected_before_dispatch():
    result = await dispatcher.dispatch(
        {"name": "pretend_database_was_changed", "arguments": {}},
        user_id=42,
    )
    assert "ошибка" in result.lower()


def test_digest_escapes_dynamic_html():
    task = SimpleNamespace(
        title="Позвонить <Ивану> & уточнить",
        priority="normal",
        due_time=None,
        scheduled_date=date(2026, 8, 19),
        due_date=None,
        status="open",
    )
    text = format_morning_digest(
        today=date(2026, 8, 19),
        tasks=[task],
        frog=None,
        projects=[],
        project_progress={},
        is_weekend=False,
    )
    assert "<Ивану>" not in text
    assert "&lt;Ивану&gt; &amp;" in text


def test_html_split_balances_tags_and_keeps_entities_intact():
    text = "<b>" + ("задача &amp; подробности " * 30) + "</b>"
    parts = split_html_message(text, max_len=120)
    assert len(parts) > 1
    assert all(len(part) <= 120 for part in parts)
    assert all(part.startswith("<b>") and part.endswith("</b>") for part in parts)
    assert all("&am" not in part.replace("&amp;", "") for part in parts)
