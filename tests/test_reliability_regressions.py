import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from bot.db.crud.reminders import is_valid_repeat_rule
from bot.db.crud.tasks import normalize_task_identity, task_title_similarity
from bot.formatters import split_html_message
from bot.formatters.digest import format_morning_digest
from bot.llm import dispatcher
from bot.llm.queue import PRIORITY_INTENT, LLMQueue
from tests.fakes import FakeSessionContext


@pytest.mark.parametrize(
    "rule",
    [None, "daily", "weekdays", "weekly:1", "weekly:1,3", "monthly:31", "every:2w"],
)
def test_supported_repeat_rules(rule):
    assert is_valid_repeat_rule(rule)


def test_task_identity_ignores_leading_action_but_not_shared_marker():
    assert normalize_task_identity("Купить Б22-молоко") == normalize_task_identity(
        "Б22-молоко"
    )
    assert task_title_similarity("Б22-кран", "Б22-молоко") < 0.6
    assert dispatcher._sanitize_title("<script>alert(1)</script> Б22-xss") == (
        "alert(1) Б22-xss"
    )


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
    monkeypatch.setattr(dispatcher, "complete_task_workflow", forbidden_complete)

    result = await dispatcher._handle_complete_task(
        42, {"search_query": "отчёт"}, "Europe/Moscow"
    )
    assert "несколько задач" in result.lower()
    assert "для банка" in result
    assert "для клиента" in result


@pytest.mark.asyncio
async def test_weak_single_match_is_not_updated(monkeypatch):
    candidate = SimpleNamespace(id="1", title="Б22-молоко", status="open")

    async def fake_search(session, user_id, query, status=None):
        return [candidate]

    async def forbidden_update(*args, **kwargs):
        raise AssertionError("weak match must not be updated")

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", fake_search)
    monkeypatch.setattr(dispatcher, "crud_update_task", forbidden_update)

    result = await dispatcher._handle_update_task(
        42,
        {"search_query": "Б22-кран", "updates": {"status": "cancelled"}},
    )
    assert "не нашёл" in result.lower()
    assert "Б22-кран" in result


@pytest.mark.asyncio
async def test_unknown_opaque_task_is_not_offered_for_deletion(monkeypatch):
    candidate = SimpleNamespace(id="1", title="Б22-молоко", status="open")

    async def fake_search(session, user_id, query, status=None):
        return [candidate]

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", fake_search)

    result = await dispatcher._handle_delete_task(
        42, {"search_query": "Б22-неттакойвообще"}
    )
    assert "не нашёл" in result.lower()
    assert not result.startswith("CHOOSE_DELETE:")


@pytest.mark.asyncio
async def test_create_task_deduplicates_leading_action_word(monkeypatch):
    existing = SimpleNamespace(
        id="1",
        title="Б22-молоко",
        scheduled_date=None,
        due_date=None,
        due_time=None,
        is_frog=False,
        priority="normal",
        repeat_rule=None,
    )

    async def fake_search(session, user_id, query, status=None):
        return [existing]

    async def no_trip(*args, **kwargs):
        return None

    async def forbidden_create(*args, **kwargs):
        raise AssertionError("normalized duplicate must not be created")

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", fake_search)
    monkeypatch.setattr(dispatcher, "get_active_trip", no_trip)
    monkeypatch.setattr(dispatcher, "create_task", forbidden_create)

    result = await dispatcher._handle_create_task(
        42, {"title": "Купить Б22-молоко"}, "Europe/Moscow"
    )
    assert "уже существует" in result


@pytest.mark.asyncio
async def test_ambiguous_delete_returns_button_choices(monkeypatch):
    candidates = [
        SimpleNamespace(id="00000000-0000-0000-0000-000000000001", title="Б22-xss"),
        SimpleNamespace(id="00000000-0000-0000-0000-000000000002", title="Б22-молоко"),
    ]

    async def fake_search(session, user_id, query, status=None):
        return candidates

    monkeypatch.setattr(dispatcher, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(dispatcher, "search_tasks", fake_search)

    result = await dispatcher._handle_delete_task(42, {"search_query": "Б22"})
    assert result.kind == "choose_delete"
    choices = result.list_payload()
    assert [item["title"] for item in choices] == ["Б22-xss", "Б22-молоко"]


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
