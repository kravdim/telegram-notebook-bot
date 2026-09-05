"""Единый workflow завершения задачи для всех интерфейсов."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import pendulum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.crud.reminders import _calc_next_occurrence, upsert_task_reminder
from bot.db.models import Reminder, Task, User


@dataclass
class TaskCompletionResult:
    task: Task | None
    completed: bool = False
    next_task: Task | None = None
    next_date: date | None = None
    closed_reminders: int = 0


def closed_task_status(task: Task) -> str:
    """Return a truthful short status for an idempotent completion response."""
    if task.status == "done" or task.resolution == "completed":
        return "уже выполнена"
    if task.status == "cancelled" or task.resolution == "cancelled":
        return "уже отменена"
    return f"уже закрыта (статус: {task.status})"


def _next_future_occurrence(current, rule: str, now: pendulum.DateTime):
    """Ближайшее occurrence строго в будущем, без создания просроченного хвоста."""
    candidate = current
    for _ in range(10000):
        candidate = _calc_next_occurrence(candidate, rule)
        if candidate is None or candidate > now:
            return candidate
    raise ValueError(f"repeat rule did not reach future occurrence: {rule}")


def _next_reminder_for_occurrence(
    anchor: pendulum.DateTime,
    original_reminder: pendulum.DateTime,
    next_at: pendulum.DateTime,
    now: pendulum.DateTime,
) -> pendulum.DateTime:
    """Preserve the reminder-to-due offset for the selected occurrence.

    If that reminder time has already passed, the explicit product policy is to
    enqueue it immediately. A reminder is never moved beyond its task due time.
    """
    offset_seconds = max(0.0, (anchor - original_reminder).total_seconds())
    candidate = next_at.subtract(seconds=offset_seconds)
    return now if candidate <= now else candidate


async def complete_task_workflow(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: int,
    timezone: str = "Europe/Moscow",
    *,
    commit: bool = True,
) -> TaskCompletionResult:
    """Атомарно завершить задачу, закрыть reminders и продолжить recurrence.

    Row lock и проверка ``status == open`` делают повторный callback
    идемпотентным: только первый конкурент создаёт следующий экземпляр.
    """
    result = await session.execute(
        select(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    task = result.scalar_one_or_none()
    if not task:
        return TaskCompletionResult(task=None)
    if task.status != "open":
        return TaskCompletionResult(task=task)

    now_utc = pendulum.now("UTC")
    now_local = now_utc.in_tz(timezone)
    task.status = "done"
    task.resolution = "completed"
    task.completed_at = now_utc

    reminder_result = await session.execute(
        select(Reminder)
        .where(
            Reminder.task_id == task.id,
            Reminder.user_id == user_id,
            Reminder.status.in_(("pending", "snoozed", "delivered", "failed")),
        )
        .with_for_update()
    )
    reminders = list(reminder_result.scalars().all())
    for reminder in reminders:
        reminder.status = "resolved"
        reminder.is_sent = True
        reminder.lease_token = None
        reminder.lease_expires_at = None

    next_task = None
    next_date = None
    anchor_date = task.scheduled_date or task.due_date
    if task.repeat_rule and anchor_date:
        anchor = pendulum.datetime(
            anchor_date.year,
            anchor_date.month,
            anchor_date.day,
            hour=task.due_time.hour if task.due_time else 9,
            minute=task.due_time.minute if task.due_time else 0,
            tz=timezone,
        )
        next_at = _next_future_occurrence(anchor, task.repeat_rule, now_local)
        if next_at:
            next_date = next_at.date()
            next_task = Task(
                user_id=task.user_id,
                project_id=task.project_id,
                trip_id=task.trip_id,
                title=task.title,
                category=task.category,
                priority=task.priority,
                scheduled_date=next_date if task.scheduled_date else None,
                due_date=next_date if task.due_date else None,
                due_time=task.due_time,
                remind_before_min=task.remind_before_min,
                repeat_rule=task.repeat_rule,
                tags=list(task.tags or []),
            )
            session.add(next_task)
            await session.flush()

            if task.remind_at:
                reminder_at = pendulum.instance(task.remind_at).in_tz(timezone)
                next_reminder_at = _next_reminder_for_occurrence(
                    anchor,
                    reminder_at,
                    next_at,
                    now_local,
                )
                next_task.remind_at = next_reminder_at
                session.add(
                    Reminder(
                        user_id=user_id,
                        task_id=next_task.id,
                        message=task.title,
                        remind_at=next_reminder_at,
                        occurrence_at=next_reminder_at,
                    )
                )

    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(task)
    if next_task:
        await session.refresh(next_task)
    return TaskCompletionResult(
        task=task,
        completed=True,
        next_task=next_task,
        next_date=next_date,
        closed_reminders=len(reminders),
    )


async def _close_task_reminders(session: AsyncSession, task: Task, status: str) -> None:
    reminders = await session.scalars(
        select(Reminder).where(
            Reminder.task_id == task.id, Reminder.user_id == task.user_id,
            Reminder.status.in_(("pending", "snoozed", "delivered", "failed")),
        ).with_for_update()
    )
    for reminder in reminders:
        reminder.status = status
        reminder.is_sent = True
        reminder.lease_token = None
        reminder.lease_expires_at = None


async def _sync_bound_alarm(
    session: AsyncSession, task: Task, updates: dict[str, Any], timezone: str,
) -> None:
    """Only explicit remind_before_min establishes a deadline-relative alarm."""
    if task.remind_before_min is None or not {"due_date", "due_time"}.intersection(updates):
        return
    if not task.due_date or not task.due_time:
        task.remind_at = None
        await _close_task_reminders(session, task, "cancelled")
        return
    due = pendulum.datetime(
        task.due_date.year, task.due_date.month, task.due_date.day,
        task.due_time.hour, task.due_time.minute, tz=timezone,
    )
    task.remind_at = due.subtract(minutes=task.remind_before_min)
    await upsert_task_reminder(
        session, task.user_id, task.id, task.title, task.remind_at, commit=False,
    )


async def update_task_workflow(
    session: AsyncSession, task_id: uuid.UUID, user_id: int, *,
    commit: bool = True, **updates: Any,
) -> Task | None:
    """Apply edits and lifecycle effects atomically for every inbound channel."""
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if task is None:
        return None
    user = await session.get(User, user_id)
    timezone = user.timezone if user else "Europe/Moscow"
    target = updates.pop("status", None)
    if target == "open" and task.status != "open" and task.repeat_rule:
        raise ValueError("Reopening a recurring occurrence requires series reconciliation")
    allowed = {
        "title", "priority", "is_frog", "scheduled_date", "due_date", "due_time",
        "remind_at", "repeat_rule",
    }
    if updates.keys() - allowed:
        raise ValueError("Unsupported task fields")
    if any(value is None and key not in {"scheduled_date", "due_date", "due_time", "remind_at", "repeat_rule"}
           for key, value in updates.items()):
        raise ValueError("Required task fields cannot be cleared")
    for key, value in updates.items():
        setattr(task, key, value)
    if target == "done":
        await complete_task_workflow(session, task_id, user_id, timezone, commit=False)
    elif target == "cancelled":
        task.status = "cancelled"
        task.resolution = "cancelled"
        task.completed_at = pendulum.now("UTC")
        await _close_task_reminders(session, task, "cancelled")
    elif target == "open":
        task.status = "open"
        task.resolution = None
        task.completed_at = None
        task.is_frog = False
    elif target is not None:
        raise ValueError("Unsupported task status")
    if task.status == "open":
        await _sync_bound_alarm(session, task, updates, timezone)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(task)
    return task
