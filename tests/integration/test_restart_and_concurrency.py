"""PostgreSQL scenarios. Enabled explicitly in CI with RUN_DB_TESTS=1."""

import asyncio
import json
import os
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pendulum
import pytest
import pytest_asyncio
import yaml
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

import bot.handlers.messages as message_handler
import scripts.delete_user_data as deletion_script
from bot.db.crud.interaction_states import (
    claim_state,
    clear_state_if_type,
    get_state,
    recover_interrupted_states,
    set_state,
    transition_state,
)
from bot.db.crud.reminders import create_reminder, get_pending_reminders, mark_sent
from bot.db.engine import async_session, engine
from bot.db.models import (
    Birthday,
    DeliveryBatch,
    DeliveryPart,
    DiaryEntry,
    FsmState,
    InteractionState,
    LlmLog,
    MemoirEntry,
    Note,
    OperationalState,
    ProcessedRequest,
    Project,
    Reminder,
    Task,
    TimeTrackingEntry,
    Trip,
    User,
)
from bot.llm.dispatcher import dispatch_result
from bot.runtime.singleton import SingletonLease
from bot.scheduler.reminders import send_pending_reminders
from bot.services.delivery import DeliveryPartSpec, deliver_batch
from bot.services.tasks import complete_task_workflow
from bot.services.user_deletion import delete_user_data, user_data_counts
from bot.services.user_export import build_user_export_sections
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
async def test_full_export_matches_deletion_inventory_and_excludes_other_users(tmp_path):
    user_id = 8_060_000_000 + int(uuid.uuid4().hex[:6], 16)
    other_id = user_id + 1
    now = datetime.now(timezone.utc)
    project_id = uuid.uuid4()
    trip_id = uuid.uuid4()
    task_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    async with async_session() as session:
        session.add_all(
            [
                User(
                    telegram_id=user_id,
                    username="export-owner",
                    privacy_notice_version=1,
                    cloud_processing_enabled=True,
                ),
                User(telegram_id=other_id, username="other-owner"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Project(id=project_id, user_id=user_id, title="project-marker"),
                Trip(
                    id=trip_id,
                    user_id=user_id,
                    title="trip-marker",
                    start_date=date(2026, 8, 27),
                    end_date=date(2026, 8, 28),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Task(
                    id=task_id,
                    user_id=user_id,
                    project_id=project_id,
                    trip_id=trip_id,
                    title="task-marker",
                ),
                Note(user_id=user_id, title="note", content="note-marker"),
                Note(user_id=other_id, title="other", content="OTHER-USER-CANARY"),
                DiaryEntry(
                    user_id=user_id,
                    content="diary-marker",
                    entry_date=date(2026, 8, 27),
                ),
                MemoirEntry(
                    user_id=user_id,
                    event_date=date(2026, 8, 27),
                    content="memoir-marker",
                ),
                TimeTrackingEntry(
                    user_id=user_id,
                    timestamp=now,
                    activity_text="tracking-marker",
                    category="work",
                    duration_minutes=15,
                ),
                Birthday(
                    user_id=user_id,
                    name="birthday-marker",
                    birth_date=date(1900, 5, 7),
                    year_known=False,
                ),
                ProcessedRequest(
                    request_key=f"export:{uuid.uuid4()}",
                    user_id=user_id,
                    status="completed",
                ),
                InteractionState(
                    user_id=user_id,
                    state_type="voice_confirm",
                    payload={"marker": "interaction-marker"},
                ),
                LlmLog(
                    user_id=user_id,
                    model="test",
                    input_messages={"metadata": True},
                ),
                DeliveryBatch(
                    id=batch_id,
                    delivery_key=f"export:{uuid.uuid4()}",
                    user_id=user_id,
                    kind="test",
                ),
                FsmState(
                    storage_key=f"bot:{user_id}:{user_id}:0::default",
                    state="export-state",
                    data={"marker": "fsm-marker"},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Reminder(
                    user_id=user_id,
                    task_id=task_id,
                    message="reminder-marker",
                    remind_at=now,
                    occurrence_at=now,
                ),
                DeliveryPart(
                    batch_id=batch_id,
                    position=0,
                    chat_id=user_id,
                    text="delivery-marker",
                ),
            ]
        )
        await session.commit()

    async with async_session() as session:
        expected = await user_data_counts(session, user_id)
        sections = await build_user_export_sections(
            session,
            user_id,
            tmp_path / "staging",
            max_bytes=10 * 1024 * 1024,
        )
        payloads = {name: "".join(lines) for name, lines in sections}

    manifest = json.loads(payloads["manifest.json"])
    assert manifest["schema"] == "dailyplanner-user-export"
    assert manifest["schema_version"] == 1
    assert manifest["datasets"] == expected
    assert set(payloads) == {"manifest.json"} | {
        f"data/{name}.jsonl" for name in expected
    }
    birthday = json.loads(payloads["data/birthdays.jsonl"])
    assert birthday["birth_date"] == "--05-07"
    assert birthday["year_known"] is False
    assert "OTHER-USER-CANARY" not in "".join(payloads.values())

    async with async_session() as cleanup_session:
        await cleanup_session.execute(
            delete(User).where(User.telegram_id.in_([user_id, other_id]))
        )
        await cleanup_session.commit()


@pytest.mark.asyncio
async def test_database_rejects_critical_domain_invariant_violations():
    user_id = 8_070_000_000 + int(uuid.uuid4().hex[:6], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="constraint-owner"))
        await session.commit()

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    Trip(
                        user_id=user_id,
                        title="invalid dates",
                        start_date=date(2026, 8, 28),
                        end_date=date(2026, 8, 27),
                    )
                )
                await session.flush()

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    TimeTrackingEntry(
                        user_id=user_id,
                        timestamp=datetime.now(timezone.utc),
                        activity_text="invalid duration",
                        category="work",
                        duration_minutes=0,
                    )
                )
                await session.flush()

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    Reminder(
                        user_id=user_id,
                        message="invalid attempts",
                        remind_at=datetime.now(timezone.utc),
                        occurrence_at=datetime.now(timezone.utc),
                        delivery_attempts=-1,
                    )
                )
                await session.flush()

        await session.execute(delete(User).where(User.telegram_id == user_id))
        await session.commit()


@pytest.mark.asyncio
async def test_privacy_deletion_runs_again_after_same_id_reonboards(
    tmp_path, monkeypatch
):
    user_id = 8_075_000_000 + int(uuid.uuid4().hex[:6], 16)
    config_path = tmp_path / "config.yaml"

    def write_access() -> None:
        config_path.write_text(
            yaml.safe_dump({"bot": {"allowed_telegram_ids": [user_id]}}),
            encoding="utf-8",
        )

    write_access()
    monkeypatch.setattr(deletion_script.settings, "admin_telegram_ids", [])
    monkeypatch.setattr(deletion_script.settings, "allow_all_users", False)
    monkeypatch.setattr(deletion_script.settings, "allowed_telegram_ids", [user_id])
    args = SimpleNamespace(
        telegram_id=user_id,
        execute=True,
        confirm=f"DELETE-{user_id}",
        config=config_path,
    )

    async with async_session() as first_generation:
        first_generation.add(User(telegram_id=user_id, username="privacy-generation-1"))
        await first_generation.commit()
        first_generation.add(Task(user_id=user_id, title="first private task"))
        await first_generation.commit()

    first = await deletion_script.run(args)
    assert first["mode"] == "executed"
    async with async_session() as first_journal_session:
        first_journal = await first_journal_session.get(
            OperationalState, f"privacy.deletion.{user_id}"
        )
        first_operation_id = first_journal.value["operation_id"]

    write_access()
    async with async_session() as second_generation:
        second_generation.add(User(telegram_id=user_id, username="privacy-generation-2"))
        await second_generation.commit()
        second_generation.add(Task(user_id=user_id, title="second private task"))
        await second_generation.commit()

    second = await deletion_script.run(args)
    assert second["mode"] == "executed"
    assert not any(second["verification_counts"].values())
    async with async_session() as verification:
        assert not any((await user_data_counts(verification, user_id)).values())
        journal = await verification.get(
            OperationalState, f"privacy.deletion.{user_id}"
        )
        assert journal.value["operation_id"] != first_operation_id
        await verification.delete(journal)
        await verification.commit()


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
async def test_live_reminder_phrase_creates_row_and_delivers_push():
    user_id = 8_125_000_000 + int(uuid.uuid4().hex[:6], 16)
    marker = f"DP-{uuid.uuid4().hex[:8]}-чай"
    text = f"слушай напомни через 2 минуты {marker} попить, а то забуду"
    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="live-reminder-regression"))
        await setup.commit()

    tool, arguments = message_handler._extract_common_mutation(
        text, "Europe/Moscow"
    )
    assert tool == "create_reminder"
    result = await dispatch_result(
        {"name": tool, "arguments": arguments}, user_id, "Europe/Moscow"
    )
    assert result.kind == "message"

    async with async_session() as make_due:
        reminder = (
            await make_due.execute(
                select(Reminder).where(
                    Reminder.user_id == user_id,
                    Reminder.message.contains(marker),
                )
            )
        ).scalar_one()
        reminder.remind_at = pendulum.now("UTC").subtract(seconds=1)
        await make_due.commit()

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(message_id=1)

    bot = Bot()
    await send_pending_reminders(bot)

    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == user_id
    assert marker in bot.sent[0]["text"]
    async with async_session() as verification:
        reminder = (
            await verification.execute(
                select(Reminder).where(Reminder.user_id == user_id)
            )
        ).scalar_one()
        assert reminder.is_sent is True
        await verification.execute(delete(User).where(User.telegram_id == user_id))
        await verification.commit()


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
                raise RuntimeError("temporary Telegram failure DELIVERY_SECRET_CANARY")
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

    async with async_session() as failed_check:
        failed_batch = await failed_check.scalar(
            select(DeliveryBatch).where(DeliveryBatch.delivery_key == delivery_key)
        )
        failed_part = await failed_check.scalar(
            select(DeliveryPart).where(
                DeliveryPart.batch_id == failed_batch.id,
                DeliveryPart.position == 1,
            )
        )
        assert failed_batch.last_error == "RuntimeError"
        assert failed_part.last_error == "RuntimeError"
        assert "DELIVERY_SECRET_CANARY" not in failed_batch.last_error
        assert "DELIVERY_SECRET_CANARY" not in failed_part.last_error

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
async def test_interaction_state_claim_does_not_replace_active_workflow():
    user_id = 8_550_000_000 + int(uuid.uuid4().hex[:6], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="interaction-cas-test"))
        await session.commit()

    workflow_types = ("voice_confirm", "memoir", "chronometry", "complete_project")
    for owner in workflow_types:
        async with async_session() as first:
            claimed = await claim_state(first, user_id, owner, {"owner": owner})
            assert claimed is not None

        for contender in workflow_types:
            if contender == owner:
                continue
            async with async_session() as second:
                blocked = await claim_state(
                    second, user_id, contender, {"contender": contender}
                )
                state = await get_state(second, user_id)
                assert blocked is None
                assert state.state_type == owner

        async with async_session() as release:
            assert await clear_state_if_type(release, user_id, owner) is True

    async with async_session() as cleanup:
        await cleanup.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_interaction_token_rejects_stale_clear_and_transition():
    user_id = 8_560_000_000 + int(uuid.uuid4().hex[:6], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="interaction-token-test"))
        await session.commit()
        claimed = await claim_state(
            session,
            user_id,
            "voice_confirm",
            {"session_token": "session-b"},
        )
        assert claimed is not None

    async with async_session() as stale:
        assert await clear_state_if_type(
            stale, user_id, "voice_confirm", "session-a"
        ) is False
        assert await transition_state(
            stale,
            user_id,
            "voice_confirm",
            "voice_edit",
            {"session_token": "session-a"},
            expected_token="session-a",
        ) is None

    async with async_session() as current:
        state = await get_state(current, user_id)
        assert state is not None
        assert state.state_type == "voice_confirm"
        assert state.payload["session_token"] == "session-b"
        assert await clear_state_if_type(
            current, user_id, "voice_confirm", "session-b"
        ) is True

    async with async_session() as cleanup:
        await cleanup.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_restart_recovers_voice_processing_as_retryable_confirmation():
    user_id = 8_565_000_000 + int(uuid.uuid4().hex[:6], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="voice-recovery-test"))
        await session.commit()
        await claim_state(
            session,
            user_id,
            "voice_processing",
            {
                "session_token": "recover-me",
                "message_id": 999,
                "transcript": "сохранённая команда",
                "phase": "processing",
            },
        )

    async with async_session() as recovery:
        assert await recover_interrupted_states(recovery) == 1

    async with async_session() as verification:
        state = await get_state(verification, user_id)
        assert state is not None
        assert state.state_type == "voice_confirm"
        assert state.payload["session_token"] == "recover-me"
        assert state.payload["phase"] == "recovered"
        await verification.execute(delete(User).where(User.telegram_id == user_id))
        await verification.commit()


@pytest.mark.asyncio
async def test_delivery_worker_cannot_commit_after_lease_expiry():
    user_id = 8_575_000_000 + int(uuid.uuid4().hex[:6], 16)
    delivery_key = f"integration:delivery-expired:{uuid.uuid4()}"

    class Bot:
        def __init__(self):
            self.calls = 0

        async def send_message(self, **kwargs):
            self.calls += 1
            return type("Sent", (), {"message_id": self.calls})()

    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="delivery-expiry-test"))
        await setup.commit()

    bot = Bot()
    kwargs = {
        "delivery_key": delivery_key,
        "user_id": user_id,
        "kind": "integration",
        "parts": [DeliveryPartSpec(user_id, "fenced")],
    }
    expired = await deliver_batch(bot, **kwargs, lease_seconds=0)
    resumed = await deliver_batch(bot, **kwargs)

    assert expired.completed is False
    assert expired.busy is True
    assert resumed.completed is True
    assert bot.calls == 2

    async with async_session() as cleanup_session:
        await cleanup_session.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup_session.commit()


@pytest.mark.asyncio
async def test_stale_delivery_failure_cannot_overwrite_new_owner_progress():
    user_id = 8_580_000_000 + int(uuid.uuid4().hex[:6], 16)
    delivery_key = f"integration:delivery-stale-error:{uuid.uuid4()}"
    started = asyncio.Event()
    release = asyncio.Event()

    class StaleBot:
        async def send_message(self, **kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("stale worker failed late")

    class CurrentBot:
        async def send_message(self, **kwargs):
            return type("Sent", (), {"message_id": 4242})()

    async with async_session() as setup:
        setup.add(User(telegram_id=user_id, username="delivery-fence-error-test"))
        await setup.commit()

    kwargs = {
        "delivery_key": delivery_key,
        "user_id": user_id,
        "kind": "integration",
        "parts": [DeliveryPartSpec(user_id, "fenced failure")],
    }
    stale = asyncio.create_task(deliver_batch(StaleBot(), **kwargs, lease_seconds=0))
    await started.wait()
    current = await deliver_batch(CurrentBot(), **kwargs)
    assert current.completed is True
    release.set()
    with pytest.raises(RuntimeError, match="stale worker failed late"):
        await stale

    async with async_session() as verification:
        batch = await verification.scalar(
            select(DeliveryBatch).where(DeliveryBatch.delivery_key == delivery_key)
        )
        part = await verification.scalar(
            select(DeliveryPart).where(DeliveryPart.batch_id == batch.id)
        )
        assert batch.status == "delivered"
        assert part.status == "delivered"
        assert part.telegram_message_id == 4242
        assert part.attempts == 1
        assert part.last_error is None

    async with async_session() as cleanup_session:
        await cleanup_session.execute(delete(User).where(User.telegram_id == user_id))
        await cleanup_session.commit()


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
