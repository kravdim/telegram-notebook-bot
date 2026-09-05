"""Lifecycle edits must invalidate stale senders without losing independent alarms."""

import os
import uuid

import pendulum
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from bot.db.crud.reminders import claim_due_reminders, create_reminder, mark_sent
from bot.db.engine import async_session, engine
from bot.db.models import Reminder, Task, User
from bot.services.tasks import complete_task_workflow, update_task_workflow

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="requires PostgreSQL")


@pytest_asyncio.fixture
async def bound_task():
    user_id = 8_150_000_000 + int(uuid.uuid4().hex[:6], 16)
    due = pendulum.now("Europe/Moscow").add(hours=2).replace(second=0, microsecond=0)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="lifecycle-boundaries"))
        await session.commit()
        task = Task(user_id=user_id, title="Bound deadline", due_date=due.date(),
                    due_time=due.time(), remind_before_min=15, remind_at=due.subtract(minutes=15))
        session.add(task)
        await session.commit()
        alarm = await create_reminder(session, user_id, task.title, task.remind_at, task_id=task.id)
        identifiers = user_id, task.id, alarm.id, due
    try:
        yield identifiers
    finally:
        async with async_session() as session:
            await session.execute(delete(User).where(User.telegram_id == user_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_reschedule_revokes_claim_and_stale_ack(bound_task):
    user_id, task_id, alarm_id, due = bound_task
    async with async_session() as session:
        claim = (await claim_due_reminders(session, due))[0]
        assert claim.id == alarm_id
    later = due.add(days=2)
    async with async_session() as session:
        await update_task_workflow(session, task_id, user_id, due_date=later.date())
    async with async_session() as session:
        await mark_sent(session, alarm_id, lease_token=claim.token)
    async with async_session() as session:
        alarm = await session.get(Reminder, alarm_id)
        assert alarm.status == "pending" and not alarm.is_sent
        assert alarm.lease_token is None and alarm.lease_expires_at is None
        assert alarm.remind_at == later.subtract(minutes=15)
        assert alarm.next_attempt_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["due_date", "due_time"])
async def test_explicit_deadline_clear_cancels_bound_alarm(bound_task, field):
    user_id, task_id, alarm_id, _ = bound_task
    async with async_session() as session:
        task = await update_task_workflow(session, task_id, user_id, **{field: None})
        assert getattr(task, field) is None and task.remind_at is None
        alarm = await session.get(Reminder, alarm_id)
        assert alarm.status == "cancelled" and alarm.is_sent


@pytest.mark.asyncio
async def test_planning_date_does_not_move_independent_alarm(bound_task):
    user_id, task_id, alarm_id, due = bound_task
    async with async_session() as session:
        await update_task_workflow(session, task_id, user_id, scheduled_date=due.add(days=3).date())
        alarm = await session.get(Reminder, alarm_id)
        assert alarm.remind_at == due.subtract(minutes=15)
        assert alarm.status == "pending"


@pytest.mark.asyncio
async def test_reopen_recurring_occurrence_cannot_fork_series(bound_task):
    user_id, task_id, _, _ = bound_task
    async with async_session() as session:
        await update_task_workflow(session, task_id, user_id, repeat_rule="daily")
        result = await complete_task_workflow(session, task_id, user_id)
        assert result.next_task is not None
    async with async_session() as session:
        with pytest.raises(ValueError, match="series reconciliation"):
            await update_task_workflow(session, task_id, user_id, status="open")
        await session.rollback()
    async with async_session() as session:
        assert len(list(await session.scalars(select(Task).where(Task.user_id == user_id)))) == 2
        assert (await session.get(Task, task_id)).status == "done"
