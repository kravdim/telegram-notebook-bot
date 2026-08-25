"""Единый workflow завершения задачи для всех интерфейсов."""

import uuid
from dataclasses import dataclass
from datetime import date

import pendulum
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.crud.reminders import _calc_next_occurrence
from bot.db.models import Reminder, Task


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
) -> TaskCompletionResult:
    """Атомарно завершить задачу, закрыть reminders и продолжить recurrence.

    Row lock и проверка ``status == open`` делают повторный callback
    идемпотентным: только первый конкурент создаёт следующий экземпляр.
    """
    result = await session.execute(
        select(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .with_for_update()
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
            Reminder.status.in_(("pending", "snoozed", "delivered")),
        )
        .with_for_update()
    )
    reminders = list(reminder_result.scalars().all())
    for reminder in reminders:
        reminder.status = "resolved"
        reminder.is_sent = True

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

    await session.commit()
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
