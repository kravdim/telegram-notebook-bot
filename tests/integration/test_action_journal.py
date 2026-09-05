"""Real PostgreSQL failure boundaries of the persisted command transaction."""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from bot.application.command_bus import CommandResult
from bot.db.engine import CommandSession, async_session, engine
from bot.db.models import Note, ProcessedRequest, User
from bot.services.command_execution import active_request, execute_action, persist_plan, saved_plan

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="requires PostgreSQL")


@pytest_asyncio.fixture
async def journal():
    user_id = 8_100_000_000 + int(uuid.uuid4().hex[:6], 16)
    key = uuid.uuid4().hex * 2
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="action-journal-test"))
        await session.commit()
        session.add(ProcessedRequest(request_key=key, user_id=user_id))
        await session.commit()
    token = active_request.set(key)
    try:
        yield user_id, key
    finally:
        active_request.reset(token)
        async with async_session() as session:
            await session.execute(delete(User).where(User.telegram_id == user_id))
            await session.commit()
        await engine.dispose()


async def write_note(user_id, content, *, fail=False, error=False):
    async with async_session() as session:
        session.add(Note(user_id=user_id, content=content))
        await session.commit()  # Legacy repository must flush, not commit independently.
    if fail:
        raise ConnectionError("injected failure after repository commit")
    return CommandResult(content, "error" if error else "message")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "error_result"])
async def test_partial_plan_replays_without_duplicate_effects(journal, failure):
    user_id, key = journal
    plan = [{"name": "create_note", "arguments": {"content": str(i)}} for i in range(3)]
    assert await persist_plan(user_id, plan) == plan
    assert await persist_plan(user_id, [{"name": "different"}]) == plan
    await execute_action(user_id, 0, lambda: write_note(user_id, "first"))
    if failure == "exception":
        with pytest.raises(ConnectionError):
            await execute_action(user_id, 1, lambda: write_note(user_id, "second", fail=True))
    else:
        result = await execute_action(user_id, 1, lambda: write_note(user_id, "second", error=True))
        assert result.kind == "error"
    await execute_action(user_id, 2, lambda: write_note(user_id, "third"))
    await engine.dispose()  # No session or process-local cache survives the replay.
    assert await saved_plan(user_id) == plan
    async with async_session() as session:
        notes = list(await session.scalars(select(Note.content).where(Note.user_id == user_id)))
        assert sorted(notes) == ["first", "third"]
        row = await session.get(ProcessedRequest, key)
        assert set(row.action_results) == {"0", "2"}
    for index, content in enumerate(("first", "second", "third")):
        result = await execute_action(user_id, index, lambda: write_note(user_id, content))
        assert result.text == content
    async with async_session() as session:
        notes = list(await session.scalars(select(Note.content).where(Note.user_id == user_id)))
        assert sorted(notes) == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_action_scope_and_position_are_checked_before_effect(journal):
    user_id, _ = journal
    await persist_plan(user_id, [{"name": "create_note"}])
    with pytest.raises(RuntimeError):
        await execute_action(user_id + 1, 0, lambda: write_note(user_id, "foreign"))
    with pytest.raises(ValueError):
        await execute_action(user_id, 1, lambda: write_note(user_id, "outside"))


@pytest.mark.asyncio
async def test_caught_repository_rollback_cannot_commit_journal(journal):
    user_id, key = journal
    await persist_plan(user_id, [{"name": "create_note"}])

    async def swallowed_error():
        async with async_session() as session:
            session.add(Note(user_id=user_id, content="rolled back"))
            await session.commit()
            await session.rollback()
        return CommandResult("pretend success")

    with pytest.raises(RuntimeError, match="rolled back"):
        await execute_action(user_id, 0, swallowed_error)
    async with async_session() as session:
        assert await session.scalar(select(Note.id).where(Note.user_id == user_id)) is None
        assert (await session.get(ProcessedRequest, key)).action_results == {}


@pytest.mark.asyncio
async def test_concurrent_retries_commit_effect_once(journal):
    user_id, _ = journal
    await persist_plan(user_id, [{"name": "create_note"}])
    calls = []

    async def effect():
        calls.append("executed")
        return await write_note(user_id, "one effect")

    results = await asyncio.gather(
        execute_action(user_id, 0, effect), execute_action(user_id, 0, effect),
    )
    assert results == [CommandResult("one effect")] * 2
    assert calls == ["executed"]
    async with async_session() as session:
        assert list(await session.scalars(select(Note.content).where(Note.user_id == user_id))) == ["one effect"]


@pytest.mark.asyncio
async def test_lost_commit_ack_replays_committed_result(journal, monkeypatch):
    user_id, _ = journal
    await persist_plan(user_id, [{"name": "create_note"}])
    original_commit = CommandSession.commit

    async def lose_ack(session):
        await original_commit(session)
        if session.info.pop("lose_ack", False) and not session.in_transaction():
            raise ConnectionError("server committed, client lost acknowledgement")

    async def effect():
        result = await write_note(user_id, "committed")
        async with async_session() as session:
            session.info["lose_ack"] = True
        return result

    monkeypatch.setattr(CommandSession, "commit", lose_ack)
    with pytest.raises(ConnectionError, match="client lost"):
        await execute_action(user_id, 0, effect)
    await engine.dispose()

    async def forbidden_replay():
        raise AssertionError("Committed action must not be repeated")

    assert await execute_action(user_id, 0, forbidden_replay) == CommandResult("committed")
    async with async_session() as session:
        assert list(await session.scalars(select(Note.content).where(Note.user_id == user_id))) == ["committed"]


@pytest.mark.asyncio
async def test_child_task_cannot_borrow_command_session(journal):
    user_id, _ = journal
    await persist_plan(user_id, [{"name": "create_note"}])

    async def effect():
        await asyncio.create_task(write_note(user_id, "unsafe concurrent session"))
        return CommandResult("not reached")

    with pytest.raises(RuntimeError, match="child task"):
        await execute_action(user_id, 0, effect)
    assert await execute_action(user_id, 0, lambda: write_note(user_id, "safe")) == CommandResult("safe")
