"""Проверки ревью на изолированной PostgreSQL, без Telegram/AI-запросов."""

import asyncio
import os
import uuid
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pendulum
import pytest
import pytest_asyncio
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import delete, select, text, update

from bot.db.crud.birthdays import get_birthdays_on_date
from bot.db.crud.memoir import create_memoir_entry, get_memoir_entries
from bot.db.crud.projects import complete_project_and_cancel_open_tasks
from bot.db.engine import async_session, engine
from bot.db.fsm_storage import DatabaseFSMStorage
from bot.db.models import (
    Birthday,
    DeliveryBatch,
    FsmState,
    Project,
    Reminder,
    Task,
    User,
)
from bot.llm.dispatcher import dispatch_result
from bot.runtime.singleton import SingletonLease
from bot.services.delivery import DeliveryPartSpec, deliver_batch, resume_pending_deliveries
from bot.services.tasks import complete_task_workflow, update_task_workflow

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="disposable PostgreSQL")


@pytest_asyncio.fixture
async def owner():
    user_id = 9_100_000_000 + int(uuid.uuid4().hex[:7], 16)
    async with async_session() as session:
        session.add(User(telegram_id=user_id, username="review", timezone="Europe/Moscow"))
        await session.commit()
    yield user_id
    async with async_session() as session:
        await session.execute(delete(FsmState).where(FsmState.storage_key.like(f"%:{user_id}:{user_id}:%")))
        await session.execute(delete(User).where(User.telegram_id == user_id))
        await session.commit()
    await engine.dispose()


async def test_lost_backend_fails_lease_verification(owner):
    lease = SingletonLease(engine, f"review:{owner}")
    assert await lease.acquire()
    assert await lease.verify()
    pid = await lease._connection.scalar(text("SELECT pg_backend_pid()"))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    try:
        with pytest.raises(Exception):
            await lease.watch(interval_seconds=0)
    finally:
        try:
            await lease.release()
        except Exception:
            pass  # expected: PostgreSQL killed precisely this test-owned session


async def test_concurrent_fsm_updates_preserve_state_and_all_fields(owner):
    storage = DatabaseFSMStorage()
    key = StorageKey(bot_id=1, chat_id=owner, user_id=owner)
    await asyncio.gather(
        storage.set_state(key, "onboarding"),
        *(storage.update_data(key, {str(i): i}) for i in range(20)),
    )
    assert await storage.get_state(key) == "onboarding"
    assert await storage.get_data(key) == {str(i): i for i in range(20)}


async def test_project_closure_cancels_alarm_and_clears_active_claim(owner):
    async with async_session() as session:
        project = Project(user_id=owner, title="project")
        session.add(project)
        await session.flush()
        task = Task(user_id=owner, project_id=project.id, title="task")
        session.add(task)
        await session.flush()
        reminder = Reminder(user_id=owner, task_id=task.id, message="alarm",
                            occurrence_at=pendulum.now("UTC"),
                            remind_at=pendulum.now("UTC"), lease_token=uuid.uuid4(),
                            lease_expires_at=pendulum.now("UTC").add(minutes=5))
        session.add(reminder)
        await session.commit()
        await complete_project_and_cancel_open_tasks(session, project.id, owner)
        await session.refresh(reminder)
        await session.refresh(task)
        assert task.status == "cancelled" and task.completed_at is not None
        assert reminder.status == "cancelled" and reminder.is_sent
        assert reminder.lease_token is None and reminder.lease_expires_at is None


async def test_stale_review_cannot_overwrite_completed_history(owner):
    async with async_session() as session:
        task = Task(user_id=owner, title="finished", status="done", resolution="completed",
                    completed_at=pendulum.now("UTC"))
        session.add(task)
        await session.commit()
        original = task.completed_at
        assert await update_task_workflow(session, task.id, owner, status="cancelled",
                                          expected_status="open") is None
        await session.refresh(task)
        assert task.status == "done" and task.completed_at == original


async def test_recurrence_keeps_deadline_offset_and_original_timezone(owner, monkeypatch):
    now = pendulum.datetime(2030, 1, 7, 12, tz="Europe/Moscow")
    monkeypatch.setattr("bot.services.tasks.pendulum.now", lambda _: now)
    async with async_session() as session:
        task = Task(user_id=owner, title="weekly", scheduled_date=date(2030, 1, 7),
                    due_date=date(2030, 1, 9), due_time=time(9), repeat_rule="weekly:1",
                    recurrence_timezone="Europe/Moscow")
        session.add(task)
        await session.commit()
        result = await complete_task_workflow(session, task.id, owner, "America/New_York")
        assert result.next_task.scheduled_date == date(2030, 1, 14)
        assert result.next_task.due_date == date(2030, 1, 16)
        assert result.next_task.recurrence_timezone == "Europe/Moscow"


async def test_offset_only_creation_creates_a_real_alarm(owner):
    await dispatch_result({"name": "create_task", "arguments": {
        "title": "offset test", "due_date": "2030-01-01", "due_time": "09:00",
        "remind_before_min": 15,
    }}, owner, "Europe/Moscow")
    async with async_session() as session:
        task = await session.scalar(select(Task).where(Task.user_id == owner))
        reminder = await session.scalar(select(Reminder).where(Reminder.task_id == task.id))
        assert reminder.remind_at == pendulum.datetime(2030, 1, 1, 8, 45, tz="Europe/Moscow")
        assert task.remind_before_min == 15


async def test_memoir_edit_invalidates_vector_and_period_excludes_old_entries(owner):
    async with async_session() as session:
        entry = await create_memoir_entry(session, owner, date(2030, 1, 8), "old")
        entry.embedding = [0.1] * 768
        entry.embedding_model = "old-model"
        await session.commit()
        entry = await create_memoir_entry(session, owner, date(2030, 1, 8), "new")
        assert entry.embedding is None and entry.embedding_model is None
        await create_memoir_entry(session, owner, date(2029, 12, 31), "previous month")
        entries = await get_memoir_entries(session, owner, start_date=date(2030, 1, 1),
                                           end_date=date(2030, 2, 1), limit=31)
        assert [item.content for item in entries] == ["new"]


async def test_leap_day_notification_uses_same_february_policy(owner):
    async with async_session() as session:
        session.add(Birthday(user_id=owner, name="Leap", birth_date=date(2000, 2, 29), year_known=False))
        await session.commit()
        assert len(await get_birthdays_on_date(session, owner, date(2030, 2, 28))) == 1
        assert not await get_birthdays_on_date(session, owner, date(2032, 2, 28))
        assert len(await get_birthdays_on_date(session, owner, date(2032, 2, 29))) == 1


async def test_outbox_resumes_saved_parts_without_original_scheduler(owner):
    calls = []

    async def send(**kwargs):
        if kwargs["chat_id"] != owner:
            return SimpleNamespace(message_id=999)
        calls.append(kwargs["text"])
        if len(calls) == 2:
            raise OSError("offline")
        return SimpleNamespace(message_id=len(calls))

    bot = SimpleNamespace(send_message=send)
    with pytest.raises(OSError):
        await deliver_batch(bot, delivery_key=f"review:{owner}", user_id=owner, kind="review",
                            parts=[DeliveryPartSpec(owner, "first"), DeliveryPartSpec(owner, "second")])
    async with async_session() as session:
        await session.execute(update(DeliveryBatch).where(DeliveryBatch.user_id == owner).values(
            next_attempt_at=pendulum.now("UTC").subtract(seconds=1)))
        await session.commit()
    await resume_pending_deliveries(bot)
    assert calls == ["first", "second", "second"]
    async with async_session() as session:
        assert await session.scalar(select(DeliveryBatch.status).where(DeliveryBatch.user_id == owner)) == "delivered"


async def test_expired_outbox_never_sends(owner):
    bot = SimpleNamespace(send_message=AsyncMock())
    result = await deliver_batch(bot, delivery_key=f"expired:{owner}", user_id=owner, kind="review",
                                 parts=[DeliveryPartSpec(owner, "old")],
                                 expires_at=pendulum.now("UTC").subtract(seconds=1))
    assert result.terminal and not result.completed
    bot.send_message.assert_not_awaited()


async def test_local_commands_are_atomic_idempotent_without_cloud_consent(owner):
    from sqlalchemy import func

    from bot.services.local_commands import execute_local_command

    results = await asyncio.gather(*(
        execute_local_command(owner, owner, 123, "add", "<local task>") for _ in range(8)
    ))
    assert all(result == results[0] for result in results)
    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Task).where(Task.user_id == owner)) == 1
        user = await session.get(User, owner)
        assert not user.cloud_processing_enabled
    invalid = await execute_local_command(owner, owner, 124, "remind", "2000-01-01 09:00 past")
    assert invalid.kind == "error"


async def test_onboarding_retries_commit_profile_and_first_task_once(owner):
    from sqlalchemy import func

    from bot.services.onboarding import complete_onboarding

    results = await asyncio.gather(*(
        complete_onboarding(owner, {"timezone": "Asia/Tokyo"}, "first") for _ in range(5)
    ))
    assert results.count("first") == 1
    async with async_session() as session:
        user = await session.get(User, owner)
        assert user.onboarding_completed and user.timezone == "Asia/Tokyo"
        assert await session.scalar(select(func.count()).select_from(Task).where(Task.user_id == owner)) == 1


async def test_late_embedding_cannot_overwrite_edited_note(owner, monkeypatch):
    from bot.db.models import Note
    from bot.scheduler import reindex

    async with async_session() as session:
        note = Note(user_id=owner, content="before")
        session.add(note)
        await session.commit()
        note_id = note.id

        async def embed(value):
            assert value == "before"
            async with async_session() as editor:
                await editor.execute(update(Note).where(Note.id == note_id).values(content="after"))
                await editor.commit()
            return [0.1] * 768

        monkeypatch.setattr(reindex, "_embed_client", SimpleNamespace(embed=embed))
        await reindex._reindex_records(session, [note], False, "note")
        await session.commit()
        await session.refresh(note)
        assert note.content == "after" and note.embedding is None


async def test_recurrence_timezone_is_fixed_when_repeat_is_enabled(owner):
    async with async_session() as session:
        task = Task(user_id=owner, title="new series")
        session.add(task)
        await session.commit()
        await update_task_workflow(session, task.id, owner, repeat_rule="daily")
        assert task.recurrence_timezone == "Europe/Moscow"
        user = await session.get(User, owner)
        user.timezone = "Asia/Tokyo"
        await session.commit()
        await update_task_workflow(session, task.id, owner, title="renamed")
        assert task.recurrence_timezone == "Europe/Moscow"
        await update_task_workflow(session, task.id, owner, repeat_rule=None)
        assert task.recurrence_timezone is None


async def test_export_keeps_one_snapshot_during_concurrent_insert(owner, tmp_path, monkeypatch):
    import json

    from bot.services import user_export

    original_counts = user_export.user_data_counts

    async def counts_then_insert(session, user_id):
        counts = await original_counts(session, user_id)
        async with async_session() as writer:
            writer.add(Task(user_id=owner, title="created during export"))
            await writer.commit()
        return counts

    monkeypatch.setattr(user_export, "user_data_counts", counts_then_insert)
    async with async_session() as session:
        sections = dict(await user_export.build_user_export_sections(
            session, owner, tmp_path / "export", max_bytes=2_000_000,
        ))
        manifest = json.loads("".join(sections["manifest.json"]))
        assert manifest["datasets"]["tasks"] == 0
        assert list(sections["data/tasks.jsonl"]) == []
    async with async_session() as session:
        assert await session.scalar(select(Task.id).where(Task.user_id == owner)) is not None


async def test_revoked_consent_blocks_provider_after_queue_wait(owner):
    from bot.llm.client import LLMClient
    from bot.llm.queue import LLMQueue
    from bot.privacy import PRIVACY_NOTICE_VERSION, provider_fingerprint

    async with async_session() as session:
        user = await session.get(User, owner)
        user.cloud_processing_enabled = True
        user.privacy_notice_version = PRIVACY_NOTICE_VERSION
        user.privacy_provider_fingerprint = provider_fingerprint()
        await session.commit()
    client = LLMClient()
    create = AsyncMock()
    # Replace the transport, so an accidental egress is observable, never external.
    client.main_client.chat.completions.create = create
    if client.fallback_client:
        client.fallback_client.chat.completions.create = create
    queue = LLMQueue()
    request = asyncio.create_task(queue.submit(1, client.chat(
        user_id=owner, messages=[{"role": "user", "content": "private"}],
    )))
    await asyncio.sleep(0)
    async with async_session() as session:
        await session.execute(update(User).where(User.telegram_id == owner).values(cloud_processing_enabled=False))
        await session.commit()
    queue.start()
    try:
        with pytest.raises(Exception):
            await request
        create.assert_not_awaited()
    finally:
        await queue.stop()
        await client.main_client.close()
        if client.fallback_client:
            await client.fallback_client.close()


async def test_generic_updates_cannot_change_ownership_or_privileges(owner):
    from bot.db.crud.projects import update_project
    from bot.db.crud.users import update_user_settings

    async with async_session() as session:
        with pytest.raises(ValueError, match="Unsupported user"):
            await update_user_settings(session, owner, role="admin")
        project = Project(user_id=owner, title="scoped")
        session.add(project)
        await session.commit()
        with pytest.raises(ValueError, match="Unsupported project"):
            await update_project(session, project.id, owner, user_id_override=1)
        with pytest.raises(ValueError, match="lifecycle"):
            await update_project(session, project.id, owner, status="done")
        await session.refresh(project)
        assert project.user_id == owner and project.status == "active"


async def test_retry_pagination_and_abandon_preserve_completed_effects(owner):
    from bot.db.models import ProcessedRequest
    from bot.handlers.request_retry import _show_retries, abandon_request, retry_data
    from tests.fakes import FakeCallback, FakeMessage

    keys = [uuid.uuid4().hex * 2 for _ in range(6)]
    async with async_session() as session:
        for index, key in enumerate(keys):
            session.add(ProcessedRequest(
                request_key=key, user_id=owner, status="failed",
                action_plan=[{"name": "create_task", "arguments": {"title": f"preview {index}"}}],
                action_results={"0": {"text": "saved", "schema_version": 1}},
                created_at=pendulum.now("UTC").subtract(minutes=10+index),
            ))
        await session.commit()
    message = FakeMessage(user_id=owner)
    await _show_retries(message, owner, 0)
    assert len(message.answers) == 6  # five requests and navigation
    assert "preview 0" in message.answers[0][0]
    message = FakeMessage(user_id=owner)
    await _show_retries(message, owner, 5)
    assert "preview 5" in message.answers[0][0]
    callback = FakeCallback(user_id=owner, data=retry_data(keys[-1]).replace("reqretry:", "reqclose:"))
    await abandon_request(callback)
    async with async_session() as session:
        row = await session.get(ProcessedRequest, keys[-1])
        assert row.status == "completed"
        assert row.action_results["0"]["text"] == "saved"
        assert "_abandoned" in row.action_results


async def test_prepared_project_survives_retry_without_holding_action_transaction(owner):
    from sqlalchemy import func

    from bot.application.command_bus import CommandResult
    from bot.db.models import ProcessedRequest
    from bot.services.command_execution import (
        active_request,
        execute_action,
        prepare_project_action,
    )

    key = uuid.uuid4().hex * 2
    async with async_session() as session:
        session.add(ProcessedRequest(request_key=key, user_id=owner,
                    action_plan=[{"name": "create_project", "arguments": {"title": "project"}}],
                    action_results={"0": {"kind": "project_created", "text": "created"}}))
        await session.commit()
    token = active_request.set(key)
    calls = []

    async def prepare():
        # A provider waiting outside the UoW leaves its reservation unlocked.
        async with async_session() as session:
            await session.scalar(select(ProcessedRequest).where(
                ProcessedRequest.request_key == key).with_for_update(nowait=True))
        calls.append("prepare")
        return CommandResult("prepared", payload={"task_titles": ["step"]})

    async def effect():
        async with async_session() as session:
            session.add(Task(user_id=owner, title="step"))
            await session.commit()  # compatibility repository participates in UoW
        calls.append("effect")
        return CommandResult("saved")

    try:
        first = await prepare_project_action(owner, 0, prepare)
        # New invocation after a stop between preparation and domain commit.
        assert await prepare_project_action(owner, 0, prepare) == first
        await execute_action(owner, 0, effect, phase="project_tasks")
        await execute_action(owner, 0, effect, phase="project_tasks")
        assert calls == ["prepare", "effect"]
        async with async_session() as session:
            assert await session.scalar(select(func.count()).select_from(Task).where(Task.user_id == owner)) == 1
    finally:
        active_request.reset(token)
