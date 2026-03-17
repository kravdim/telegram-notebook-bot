"""CRUD-операции для напоминаний."""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Reminder


async def create_reminder(
    session: AsyncSession,
    user_id: int,
    message: str,
    remind_at: datetime,
    repeat_rule: Optional[str] = None,
    task_id=None,
) -> Reminder:
    """Создать напоминание."""
    reminder = Reminder(
        user_id=user_id,
        message=message,
        remind_at=remind_at,
        repeat_rule=repeat_rule,
        task_id=task_id,
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def get_pending_reminders(
    session: AsyncSession,
    before: datetime,
) -> List[Reminder]:
    """Получить неотправленные напоминания, время которых наступило."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.is_sent == False,
            Reminder.remind_at <= before,
        )
        .order_by(Reminder.remind_at.asc())
    )
    return list(result.scalars().all())


async def mark_sent(
    session: AsyncSession,
    reminder_id: uuid.UUID,
) -> None:
    """Пометить напоминание как отправленное."""
    result = await session.execute(
        select(Reminder).where(Reminder.id == reminder_id)
    )
    reminder = result.scalar_one_or_none()
    if reminder:
        reminder.is_sent = True
        await session.commit()


async def snooze_reminder(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    new_remind_at: datetime,
) -> Optional[Reminder]:
    """Отложить напоминание на новое время."""
    result = await session.execute(
        select(Reminder).where(Reminder.id == reminder_id)
    )
    reminder = result.scalar_one_or_none()
    if not reminder:
        return None

    if reminder.snooze_count >= 5:
        return reminder  # Вызывающий код проверит snooze_count

    reminder.remind_at = new_remind_at
    reminder.is_sent = False
    reminder.snooze_count += 1
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def get_reminder_by_id(
    session: AsyncSession,
    reminder_id: uuid.UUID,
) -> Optional[Reminder]:
    """Получить напоминание по ID."""
    result = await session.execute(
        select(Reminder).where(Reminder.id == reminder_id)
    )
    return result.scalar_one_or_none()
