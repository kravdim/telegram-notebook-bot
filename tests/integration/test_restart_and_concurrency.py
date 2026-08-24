"""PostgreSQL scenarios. Enabled explicitly in CI with RUN_DB_TESTS=1."""

import asyncio
import os
import uuid

import pendulum
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from bot.db.crud.interaction_states import get_state, set_state
from bot.db.crud.reminders import create_reminder, get_pending_reminders, mark_sent
from bot.db.engine import async_session, engine
from bot.db.models import (
    DeliveryBatch,
    DeliveryPart,
    FsmState,
    LlmLog,
    Note,
    ProcessedRequest,
    Project,
    Reminder,
    Task,
    User,
)
from bot.runtime.singleton import SingletonLease
from bot.services.delivery import DeliveryPartSpec, deliver_batch
from bot.services.tasks import complete_task_workflow
from bot.services.user_deletion import delete_user_data, user_data_counts
from scripts.cleanup_e2e_namespace import cleanup

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
async def test_verified_user_deletion_removes_cascades_logs_and_fsm():
    user_id = 8_050_000_000 + int(uuid.uuid4().hex[:6], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="privacy-test"))
        await session.flush()
        session.add(Task(user_id=user_id, title="private task"))
        session.add(
            LlmLog(
                user_id=user_id,
                model="test",
                input_messages={"content": "private input"},
                output_content="private output",
            )
        )
        session.add(
            FsmState(
                storage_key=f"123:{user_id}:{user_id}:0::default",
                state="private-state",
                data={"private": True},
            )
        )
        await session.commit()

    async with async_session() as session:
        before = await user_data_counts(session, user_id)
        assert before["users"] == 1
        assert before["tasks"] == 1
        assert before["llm_logs"] == 1
        assert before["fsm_states"] == 1
        deleted = await delete_user_data(session, user_id)
        await session.commit()
        assert deleted == before

    async with async_session() as verification:
        assert not any((await user_data_counts(verification, user_id)).values())


@pytest.mark.asyncio
async def test_skip_locked_and_repeated_delivery_do_not_duplicate_occurrence():
    user_id = 8_100_000_000 + int(uuid.uuid4().hex[:6], 16)
    now = pendulum.now("UTC").subtract(minutes=5)
    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="reminder-test"))
        await setup.commit()
        reminder = await create_reminder(setup, user_id, "restart drill", now, repeat_rule="daily")
        reminder_id = reminder.id

    async with async_session() as first, async_session() as second:
        claimed = await get_pending_reminders(first, before=pendulum.now("UTC"))
        assert [row.id for row in claimed] == [reminder_id]
        assert await get_pending_reminders(second, before=pendulum.now("UTC")) == []
        await mark_sent(first, reminder_id)

    async with async_session() as restarted:
        await mark_sent(restarted, reminder_id)
        occurrences = (
            (await restarted.execute(select(Reminder).where(Reminder.user_id == user_id)))
            .scalars()
            .all()
        )
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


@pytest.mark.asyncio
async def test_completion_workflow_is_idempotent_and_continues_recurrence():
    user_id = 8_300_000_000 + int(uuid.uuid4().hex[:6], 16)
    now = pendulum.now("Europe/Moscow")
    task_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="completion-test"))
        await setup.commit()
        setup.add(
            Task(
                id=task_id,
                user_id=user_id,
                title="Ежедневная зарядка",
                scheduled_date=now.subtract(days=3).date(),
                remind_at=now.subtract(days=3),
                repeat_rule="daily",
            )
        )
        await setup.flush()
        setup.add(
            Reminder(
                id=reminder_id,
                user_id=user_id,
                task_id=task_id,
                message="Ежедневная зарядка",
                remind_at=now.add(hours=2),
                occurrence_at=now.add(hours=2),
            )
        )
        await setup.commit()

    async with async_session() as first:
        completed = await complete_task_workflow(first, task_id, user_id, "Europe/Moscow")
        assert completed.completed is True
        assert completed.next_task is not None
        assert completed.next_date >= now.date()
        assert completed.closed_reminders == 1

    async with async_session() as second:
        repeated = await complete_task_workflow(second, task_id, user_id, "Europe/Moscow")
        assert repeated.completed is False
        tasks = (await second.execute(select(Task).where(Task.user_id == user_id))).scalars().all()
        old_reminder = await second.get(Reminder, reminder_id)
        assert len(tasks) == 2
        assert old_reminder.status == "resolved"
        assert old_reminder.is_sent is True

        await second.execute(delete(User).where(User.telegram_id == user_id))
        await second.commit()


@pytest.mark.asyncio
async def test_delivery_outbox_resumes_after_partial_failure_without_repeating_parts():
    user_id = 8_400_000_000 + int(uuid.uuid4().hex[:6], 16)
    delivery_key = f"integration:delivery:{uuid.uuid4()}"

    class Bot:
        def __init__(self):
            self.calls = []
            self.fail_second_once = True

        async def send_message(self, **kwargs):
            self.calls.append(kwargs["text"])
            if kwargs["text"] == "two" and self.fail_second_once:
                self.fail_second_once = False
                raise RuntimeError("temporary Telegram failure")
            return type("Sent", (), {"message_id": len(self.calls)})()

    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="delivery-test"))
        await setup.commit()

    bot = Bot()
    specs = [
        DeliveryPartSpec(user_id, "one"),
        DeliveryPartSpec(user_id, "two"),
        DeliveryPartSpec(user_id, "three"),
    ]
    with pytest.raises(RuntimeError, match="temporary Telegram failure"):
        await deliver_batch(
            bot,
            delivery_key=delivery_key,
            user_id=user_id,
            kind="integration",
            parts=specs,
        )

    resumed = await deliver_batch(
        bot,
        delivery_key=delivery_key,
        user_id=user_id,
        kind="integration",
        parts=specs,
    )
    repeated = await deliver_batch(
        bot,
        delivery_key=delivery_key,
        user_id=user_id,
        kind="integration",
        parts=specs,
    )

    assert resumed.completed is True
    assert repeated.already_completed is True
    assert bot.calls == ["one", "two", "two", "three"]

    async with async_session() as check:
        batch = await check.scalar(
            select(DeliveryBatch).where(DeliveryBatch.delivery_key == delivery_key)
        )
        parts = list(
            (
                await check.execute(
                    select(DeliveryPart)
                    .where(DeliveryPart.batch_id == batch.id)
                    .order_by(DeliveryPart.position)
                )
            )
            .scalars()
            .all()
        )
        assert batch.status == "delivered"
        assert [part.status for part in parts] == ["delivered"] * 3
        assert [part.attempts for part in parts] == [1, 2, 1]
        await check.execute(delete(User).where(User.telegram_id == user_id))
        await check.commit()


@pytest.mark.asyncio
async def test_delivery_outbox_lease_excludes_concurrent_sender():
    user_id = 8_500_000_000 + int(uuid.uuid4().hex[:6], 16)
    delivery_key = f"integration:delivery-lease:{uuid.uuid4()}"

    class BlockingBot:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def send_message(self, **kwargs):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return type("Sent", (), {"message_id": 101})()

    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="delivery-lease-test"))
        await setup.commit()

    bot = BlockingBot()
    kwargs = {
        "delivery_key": delivery_key,
        "user_id": user_id,
        "kind": "integration",
        "parts": [DeliveryPartSpec(user_id, "only once")],
    }
    first = asyncio.create_task(deliver_batch(bot, **kwargs))
    await bot.started.wait()
    concurrent = await deliver_batch(bot, **kwargs)
    assert concurrent.busy is True
    assert concurrent.completed is False

    bot.release.set()
    assert (await first).completed is True
    assert bot.calls == 1

    async with async_session() as cleanup:
        await cleanup.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_e2e_namespace_cleanup_deletes_only_current_run_artifacts():
    user_id = 8_600_000_000 + int(uuid.uuid4().hex[:6], 16)
    run_id = "DP-20260824T140501-a1b2c3"
    task_id = uuid.uuid4()
    kept_task_id = uuid.uuid4()

    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="cleanup-test"))
        await setup.flush()
        project = Project(user_id=user_id, title=f"Project {run_id}")
        setup.add(project)
        await setup.flush()
        project_id = project.id
        setup.add_all(
            [
                Task(
                    id=task_id,
                    user_id=user_id,
                    project_id=project_id,
                    title="generated child without marker",
                ),
                Task(id=kept_task_id, user_id=user_id, title="personal task"),
                Note(user_id=user_id, content=f"test note {run_id}"),
            ]
        )
        await setup.commit()

    preview = await cleanup(user_id, run_id, execute=False)
    assert preview["projects"] == 1
    assert preview["tasks"] == 1
    assert preview["notes"] == 1

    async with async_session() as after_preview:
        assert await after_preview.get(Task, task_id) is not None

    deleted = await cleanup(user_id, run_id, execute=True)
    assert deleted["projects"] == 1
    assert deleted["tasks"] == 1
    assert deleted["notes"] == 1

    async with async_session() as check:
        assert await check.get(Task, task_id) is None
        assert await check.get(Project, project_id) is None
        assert await check.get(Task, kept_task_id) is not None
        await check.execute(delete(User).where(User.telegram_id == user_id))
        await check.commit()


@pytest.mark.asyncio
async def test_dedicated_e2e_cleanup_wipes_account_data_but_keeps_user(monkeypatch):
    user_id = 8_514_454_144
    run_id = "DP-20260824T140502-b2c3d4"
    from bot.config import settings

    monkeypatch.setitem(
        settings.yaml_config,
        "testing",
        {"e2e_user_ids": [user_id]},
    )

    async with async_session() as setup:
        await setup.execute(delete(User).where(User.telegram_id == user_id))
        setup.add(
            User(
                telegram_id=user_id,
                username="dedicated-e2e",
                onboarding_completed=True,
            )
        )
        await setup.flush()
        setup.add_all(
            [
                Task(user_id=user_id, title="LLM stripped the marker"),
                Note(user_id=user_id, content="unmarked test note"),
                ProcessedRequest(
                    request_key=f"e2e:{uuid.uuid4()}",
                    user_id=user_id,
                    status="completed",
                ),
            ]
        )
        await setup.commit()

    deleted = await cleanup(
        user_id,
        run_id,
        execute=True,
        all_user_data=True,
    )
    assert deleted["tasks"] == 1
    assert deleted["notes"] == 1
    assert deleted["processed_requests"] == 1

    async with async_session() as check:
        user = await check.get(User, user_id)
        assert user is not None
        assert user.onboarding_completed is True
        assert (
            list((await check.execute(select(Task).where(Task.user_id == user_id))).scalars().all())
            == []
        )
        await check.delete(user)
        await check.commit()

    with pytest.raises(ValueError, match="not configured"):
        await cleanup(
            user_id + 1,
            run_id,
            execute=True,
            all_user_data=True,
        )
