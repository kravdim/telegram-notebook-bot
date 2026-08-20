"""PostgreSQL scenarios. Enabled explicitly in CI with RUN_DB_TESTS=1."""

import os
import uuid

import pendulum
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from bot.db.crud.interaction_states import get_state, set_state
from bot.db.crud.reminders import create_reminder, get_pending_reminders, mark_sent
from bot.db.engine import async_session, engine
from bot.db.models import ProcessedRequest, Reminder, Task, User
from bot.runtime.singleton import SingletonLease

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires disposable migrated PostgreSQL"
)


@pytest_asyncio.fixture(autouse=True)
async def isolate_engine_pool():
    """Do not carry asyncpg connections across pytest event loops."""
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_processes_cannot_hold_runtime_lease():
    first = SingletonLease(engine, "dailyplanner:test:singleton")
    second = SingletonLease(engine, "dailyplanner:test:singleton")
    assert await first.acquire() is True
    try:
        assert await second.acquire() is False
    finally:
        await first.release()
    assert await second.acquire() is True
    await second.release()


@pytest.mark.asyncio
async def test_completed_request_and_interaction_survive_new_sessions():
    user_id = 8_000_000_000 + int(uuid.uuid4().hex[:6], 16)
    key = f"restart:{uuid.uuid4()}"
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="restart-test"))
        await session.commit()
        session.add(ProcessedRequest(request_key=key, user_id=user_id, status="completed"))
        await session.commit()
        await set_state(session, user_id, "complete_project", {"source": "restart"})

    async with async_session() as restarted_session:
        request = (
            await restarted_session.execute(
                select(ProcessedRequest).where(ProcessedRequest.request_key == key)
            )
        ).scalar_one()
        state = await get_state(restarted_session, user_id)
        assert request.status == "completed"
        assert state and state.payload == {"source": "restart"}
        await restarted_session.execute(delete(User).where(User.telegram_id == user_id))
        await restarted_session.commit()


@pytest.mark.asyncio
async def test_skip_locked_and_repeated_delivery_do_not_duplicate_occurrence():
    user_id = 8_100_000_000 + int(uuid.uuid4().hex[:6], 16)
    now = pendulum.now("UTC").subtract(minutes=5)
    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="reminder-test"))
        await setup.commit()
        reminder = await create_reminder(
            setup, user_id, "restart drill", now, repeat_rule="daily"
        )
        reminder_id = reminder.id

    async with async_session() as first, async_session() as second:
        claimed = await get_pending_reminders(first, before=pendulum.now("UTC"))
        assert [row.id for row in claimed] == [reminder_id]
        assert await get_pending_reminders(second, before=pendulum.now("UTC")) == []
        await mark_sent(first, reminder_id)

    async with async_session() as restarted:
        await mark_sent(restarted, reminder_id)
        occurrences = (
            await restarted.execute(
                select(Reminder).where(Reminder.user_id == user_id)
            )
        ).scalars().all()
        assert len(occurrences) == 2
        await restarted.execute(delete(User).where(User.telegram_id == user_id))
        await restarted.commit()


@pytest.mark.asyncio
async def test_task_version_rejects_lost_update():
    user_id = 8_200_000_000 + int(uuid.uuid4().hex[:6], 16)
    task_id = uuid.uuid4()
    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="version-test"))
        await setup.commit()
        setup.add(Task(id=task_id, user_id=user_id, title="original"))
        await setup.commit()

    async with async_session() as first, async_session() as second:
        task_a = await first.get(Task, task_id)
        task_b = await second.get(Task, task_id)
        task_a.title = "first"
        await first.commit()
        task_b.title = "second"
        from sqlalchemy.orm.exc import StaleDataError
        with pytest.raises(StaleDataError):
            await second.commit()

    async with async_session() as cleanup:
        await cleanup.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup.commit()
