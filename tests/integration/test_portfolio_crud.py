"""PostgreSQL integration coverage for the less frequently used CRUD modules."""

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pendulum
import pytest
import pytest_asyncio
from sqlalchemy import delete

from bot.db.crud.birthdays import (
    add_birthday,
    delete_birthday,
    get_all_birthdays,
    get_birthdays_on_date,
    get_upcoming_birthdays,
)
from bot.db.crud.chronometry import create_time_entry, get_day_stats, get_week_stats
from bot.db.crud.diary import create_diary_entry, hybrid_search_diary
from bot.db.crud.knowledge import add_chunk, get_all_chunks, hybrid_search
from bot.db.crud.memoir import (
    create_memoir_entry,
    get_memoir_entries,
    get_value_stats,
    hybrid_search_memoir,
)
from bot.db.crud.notes import create_note, hybrid_search_notes
from bot.db.crud.projects import (
    batch_project_progress,
    complete_project_and_cancel_open_tasks,
    create_project,
    get_project_progress,
    get_user_projects,
    search_projects,
    update_project,
)
from bot.db.crud.reminders import (
    create_reminder,
    get_pending_reminders,
    get_reminder_by_id,
    mark_sent,
    record_delivery_failure,
    resolve_reminder,
    snooze_reminder,
    upsert_task_reminder,
)
from bot.db.crud.tasks import (
    count_similar_completed,
    create_task,
    delete_task,
    get_completed_in_range,
    get_completed_today,
    get_frog,
    get_frogs_in_range,
    get_today_tasks,
    get_user_tasks,
    search_tasks,
    set_frog,
    update_task,
)
from bot.db.crud.trips import (
    complete_trip,
    create_trip,
    get_active_trip,
    get_open_trip,
    get_user_trips,
)
from bot.db.crud.users import (
    claim_date_marker,
    claim_task_reminder_slot,
    get_or_create_user,
    get_user,
    release_date_marker,
    release_task_reminder_slot,
    update_user_settings,
)
from bot.db.engine import async_session, engine
from bot.db.models import KnowledgeChunk, User
from bot.services.tasks import complete_task_workflow

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires disposable migrated PostgreSQL"
)


@pytest_asyncio.fixture(autouse=True)
async def isolate_engine_pool():
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def portfolio_users():
    """Create two isolated tenants and remove their cascaded data afterwards."""
    base = 8_700_000_000 + int(uuid.uuid4().hex[:6], 16)
    user_ids = (base, base + 1)
    async with async_session() as session:
        for user_id, username in zip(user_ids, ("portfolio-owner", "portfolio-other")):
            await get_or_create_user(session, user_id, username)
    try:
        yield user_ids
    finally:
        async with async_session() as session:
            await session.execute(delete(User).where(User.telegram_id.in_(user_ids)))
            await session.commit()


@pytest.mark.asyncio
async def test_users_and_birthdays_support_upsert_filters_and_tenant_delete(portfolio_users):
    owner_id, other_id = portfolio_users
    async with async_session() as session:
        user, created = await get_or_create_user(session, owner_id, "ignored")
        assert created is False
        assert user.username == "portfolio-owner"
        assert await update_user_settings(session, owner_id, chronometry_enabled=True)
        assert (await get_user(session, owner_id)).chronometry_enabled is True
        assert await claim_date_marker(session, owner_id, "digest_sent_date", date(2026, 8, 28))
        assert not await claim_date_marker(session, owner_id, "digest_sent_date", date(2026, 8, 28))
        await release_date_marker(session, owner_id, "digest_sent_date", date(2026, 8, 28))
        assert await claim_task_reminder_slot(session, owner_id, date(2026, 8, 28), 9)
        assert not await claim_task_reminder_slot(session, owner_id, date(2026, 8, 28), 9)
        await release_task_reminder_slot(session, owner_id, date(2026, 8, 28), 9)

        birthday = await add_birthday(
            session, owner_id, "Alice", date(1990, 1, 1), note="old", year_known=True
        )
        updated = await add_birthday(
            session, owner_id, "ALICE", date(1990, 1, 1), note="new", year_known=False
        )
        await add_birthday(session, owner_id, "Bob", date(1980, 12, 31))
        await add_birthday(session, other_id, "Other", date(2000, 1, 1))

        assert updated.id == birthday.id
        assert updated.note == "new" and updated.year_known is False
        assert [row.name for row in await get_birthdays_on_date(session, owner_id, date(2026, 1, 1))] == [
            "Alice"
        ]
        upcoming = await get_upcoming_birthdays(session, owner_id, date(2026, 12, 31), 2)
        assert [(row.name, row._upcoming_date) for row in upcoming] == [
            ("Bob", date(2026, 12, 31)),
            ("Alice", date(2027, 1, 1)),
        ]
        assert [row.name for row in await get_all_birthdays(session, owner_id)] == ["Alice", "Bob"]
        assert not await delete_birthday(session, birthday.id, other_id)
        assert await delete_birthday(session, birthday.id, owner_id)
        assert [row.name for row in await get_all_birthdays(session, owner_id)] == ["Bob"]


@pytest.mark.asyncio
async def test_personal_records_are_searchable_and_scoped_to_tenant(portfolio_users):
    owner_id, other_id = portfolio_users
    now = pendulum.now("UTC")
    today = now.in_timezone("Europe/Moscow").date()
    async with async_session() as session:
        diary = await create_diary_entry(session, owner_id, "Portfolio diary marker", today)
        await create_diary_entry(session, other_id, "Portfolio diary marker", today)
        assert [row.id for row in await hybrid_search_diary(session, owner_id, "diary marker")] == [
            diary.id
        ]

        memoir = await create_memoir_entry(session, owner_id, today, "Initial memoir", "growth")
        revised = await create_memoir_entry(session, owner_id, today, "Revised memoir marker", "health")
        await create_memoir_entry(session, other_id, today, "Revised memoir marker", "health")
        assert revised.id == memoir.id
        assert [row.id for row in await get_memoir_entries(session, owner_id)] == [memoir.id]
        assert [row.id for row in await hybrid_search_memoir(session, owner_id, "memoir marker")] == [
            memoir.id
        ]
        assert {row["value"]: row["count"] for row in await get_value_stats(session, owner_id, 1)}[
            "health"
        ] == 1

        note = await create_note(session, owner_id, "Note body", title="Portfolio note marker", tags=["work"])
        await create_note(session, other_id, "Portfolio note marker")
        assert [row.id for row in await hybrid_search_notes(session, owner_id, "note marker")] == [note.id]

        entry = await create_time_entry(
            session,
            owner_id,
            "Portfolio focus",
            "focus",
            timestamp=now,
            duration_minutes=35,
            productivity_score=5,
        )
        await create_time_entry(
            session, other_id, "Portfolio focus", "focus", timestamp=now, duration_minutes=99
        )
        day_stats = await get_day_stats(session, owner_id)
        week_stats = await get_week_stats(session, owner_id)
        assert day_stats["entries_count"] == 1 and day_stats["categories"]["focus"] == 35
        assert day_stats["avg_productivity"] == 5.0
        assert week_stats["entries_count"] == 1
        assert entry.user_id == owner_id


@pytest.mark.asyncio
async def test_projects_tasks_and_trips_cover_mutations_filters_and_ownership(portfolio_users):
    owner_id, other_id = portfolio_users
    today = date.today()
    async with async_session() as session:
        project = await create_project(session, owner_id, "P42- Portfolio launch", description="draft")
        other_project = await create_project(session, other_id, "Portfolio launch")
        assert [row.id for row in await search_projects(session, owner_id, "P42- launch")] == [project.id]
        assert await update_project(session, project.id, other_id, title="stolen") is None
        updated = await update_project(session, project.id, owner_id, description="approved")
        assert updated and updated.description == "approved"
        assert [row.id for row in await get_user_projects(session, owner_id)] == [project.id]

        done_task = await create_task(
            session, owner_id, "Ship portfolio", project_id=project.id, scheduled_date=today
        )
        open_task = await create_task(session, owner_id, "Review portfolio", project_id=project.id)
        other_task = await create_task(session, other_id, "Ship portfolio", scheduled_date=today)
        assert await set_frog(session, done_task.id, owner_id)
        assert [row.id for row in await get_today_tasks(session, owner_id, today)] == [done_task.id]
        assert {row.id for row in await search_tasks(session, owner_id, "portfolio")} == {
            done_task.id,
            open_task.id,
        }
        assert (await complete_task_workflow(session, done_task.id, other_id)).task is None
        completed = (await complete_task_workflow(session, done_task.id, owner_id)).task
        assert completed and completed.resolution == "completed"
        assert await update_task(session, other_task.id, owner_id, title="stolen") is None
        progress = await get_project_progress(session, project.id)
        assert progress == {"total": 2, "done": 1, "percent": 50}
        assert await complete_project_and_cancel_open_tasks(session, project.id, owner_id)
        assert {row.status for row in await get_user_tasks(session, owner_id, status=None)} == {
            "done",
            "cancelled",
        }
        assert (await batch_project_progress(session, [project.id, other_project.id]))[project.id]["done"] == 1
        assert await delete_task(session, other_task.id, owner_id) is False
        assert await get_completed_in_range(
            session, owner_id, datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc) + timedelta(days=1)
        )

        past_trip = await create_trip(session, owner_id, "Past", today - timedelta(days=4), today - timedelta(days=2))
        active_trip = await create_trip(session, owner_id, "Active", today, today + timedelta(days=2))
        other_trip = await create_trip(session, other_id, "Other", today, today + timedelta(days=1))
        assert (await get_active_trip(session, owner_id, today)).id == active_trip.id
        assert (await get_open_trip(session, owner_id, today)).id == active_trip.id
        assert (await get_user_trips(session, owner_id))[0].id == active_trip.id
        assert await complete_trip(session, other_trip.id, owner_id) is None
        assert (await complete_trip(session, active_trip.id, owner_id)).status == "completed"
        assert (await get_open_trip(session, owner_id, today)) is None
        assert (await get_user_trips(session, owner_id))[-1].id == past_trip.id


@pytest.mark.asyncio
async def test_reminders_cover_recurrence_snooze_resolution_failure_and_task_upsert(portfolio_users):
    owner_id, other_id = portfolio_users
    now = datetime.now(timezone.utc).replace(microsecond=0)
    async with async_session() as session:
        recurring = await create_reminder(session, owner_id, "Daily marker", now, repeat_rule="daily")
        other = await create_reminder(session, other_id, "Other marker", now)
        assert {recurring.id, other.id}.issubset(
            {row.id for row in await get_pending_reminders(session, now)}
        )
        await mark_sent(session, recurring.id)
        await mark_sent(session, recurring.id)
        delivered = await get_reminder_by_id(session, recurring.id, owner_id)
        assert delivered and delivered.status == "delivered" and delivered.is_sent
        tomorrow = await get_pending_reminders(session, now + timedelta(days=2))
        next_occurrences = [row for row in tomorrow if row.series_id == recurring.series_id]
        assert len(next_occurrences) == 1 and next_occurrences[0].remind_at == now + timedelta(days=1)
        assert await snooze_reminder(session, other.id, now + timedelta(hours=1), owner_id) is None
        snoozed = await snooze_reminder(session, other.id, now + timedelta(hours=1), other_id)
        assert snoozed and snoozed.status == "snoozed" and snoozed.snooze_count == 1
        assert (await resolve_reminder(session, other.id, other_id)).status == "resolved"
        assert (await resolve_reminder(session, other.id, other_id)).status == "resolved"
        await record_delivery_failure(session, recurring.id, "temporary failure")
        retried = await get_reminder_by_id(session, recurring.id, owner_id)
        assert retried and retried.delivery_attempts == 1 and retried.status == "delivered"
        await record_delivery_failure(session, recurring.id, "x" * 1200, terminal=True)
        failed = await get_reminder_by_id(session, recurring.id, owner_id)
        assert failed and failed.status == "failed" and not failed.is_sent
        assert len(failed.last_error) == 1000

        task = await create_task(session, owner_id, "Reminder task")
        task_reminder = await upsert_task_reminder(session, owner_id, task.id, "first", now)
        moved = await upsert_task_reminder(session, owner_id, task.id, "moved", now + timedelta(hours=3))
        assert moved.id == task_reminder.id
        assert moved.message == "moved" and moved.remind_at == now + timedelta(hours=3)
        assert (await resolve_reminder(session, moved.id, owner_id)).status == "resolved"
        replacement = await upsert_task_reminder(session, owner_id, task.id, "replacement", now)
        assert replacement.id != moved.id and replacement.status == "pending"


@pytest.mark.asyncio
async def test_tasks_cover_calendar_associations_frogs_completion_and_deletion(portfolio_users):
    owner_id, other_id = portfolio_users
    now = datetime.now(timezone.utc)
    today = now.date()
    async with async_session() as session:
        project = await create_project(session, owner_id, "Task filter project", category="personal")
        trip = await create_trip(session, owner_id, "Task filter trip", today, today + timedelta(days=2))
        overdue = await create_task(
            session,
            owner_id,
            "Overdue personal marker",
            category="personal",
            priority="high",
            project_id=project.id,
            trip_id=trip.id,
            due_date=today - timedelta(days=1),
            repeat_rule="weekly:1",
        )
        scheduled = await create_task(
            session,
            owner_id,
            "Scheduled work marker",
            category="work",
            scheduled_date=today,
        )
        future = await create_task(
            session,
            owner_id,
            "Future marker",
            scheduled_date=today + timedelta(days=1),
        )
        other_tenant = await create_task(
            session, other_id, "Overdue personal marker", due_date=today - timedelta(days=1)
        )

        today_tasks = await get_today_tasks(session, owner_id, today)
        assert {task.id for task in today_tasks} == {overdue.id, scheduled.id}
        assert overdue.category == "personal"
        assert overdue.project_id == project.id and overdue.trip_id == trip.id
        assert overdue.repeat_rule == "weekly:1"
        assert other_tenant.id not in {task.id for task in today_tasks}

        assert (await set_frog(session, overdue.id, owner_id)).is_frog
        replacement_frog = await set_frog(session, future.id, owner_id)
        assert replacement_frog and replacement_frog.is_frog
        assert (await get_frog(session, owner_id)).id == future.id
        await session.refresh(overdue)
        assert overdue.is_frog is False
        assert future.id in {task.id for task in await get_today_tasks(session, owner_id, today)}

        completed = (await complete_task_workflow(session, scheduled.id, owner_id)).task
        assert completed and completed.id == scheduled.id and completed.completed_at is not None
        from bot.services.tasks import update_task_workflow

        cancelled = await update_task_workflow(session, overdue.id, owner_id, status="cancelled")
        assert cancelled and cancelled.resolution == "cancelled" and cancelled.completed_at is not None
        completed_today = await get_completed_today(session, owner_id, today, tz="UTC")
        assert [task.id for task in completed_today] == [scheduled.id]
        completed_range = await get_completed_in_range(
            session, owner_id, now - timedelta(days=1), now + timedelta(days=1)
        )
        assert [task.id for task in completed_range] == [scheduled.id]
        frogs = await get_frogs_in_range(
            session, owner_id, now - timedelta(days=1), now + timedelta(days=1)
        )
        assert {task.id for task in frogs} == {future.id}
        similar_count, last_completed_at = await count_similar_completed(
            session, owner_id, "Scheduled work marker"
        )
        assert similar_count == 1 and last_completed_at == scheduled.completed_at
        assert await delete_task(session, future.id, other_id) is False
        assert await delete_task(session, future.id, owner_id) is True
        assert future.id not in {task.id for task in await get_user_tasks(session, owner_id, status=None)}


@pytest.mark.asyncio
async def test_knowledge_chunks_can_be_added_found_and_cleaned_up():
    marker = uuid.uuid4().hex
    async with async_session() as session:
        try:
            matching = await add_chunk(
                session, f"test-{marker}", "слоны", f"Portfolio {marker} chunk"
            )
            unrelated = await add_chunk(session, f"test-{marker}", "other", "unrelated chunk")
            found = await hybrid_search(session, marker)
            assert [row.id for row in found] == [matching.id]
            all_chunks = await get_all_chunks(session)
            assert {row.id for row in all_chunks if row.source == f"test-{marker}"} == {
                matching.id,
                unrelated.id,
            }
        finally:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source == f"test-{marker}"))
            await session.commit()
