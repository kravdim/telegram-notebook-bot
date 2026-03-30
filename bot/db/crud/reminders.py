"""CRUD-операции для напоминаний."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import pendulum
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Reminder

logger = logging.getLogger(__name__)


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
    """Пометить напоминание как отправленное. Если есть repeat_rule — создаёт следующее."""
    result = await session.execute(
        select(Reminder).where(Reminder.id == reminder_id)
    )
    reminder = result.scalar_one_or_none()
    if not reminder:
        return

    reminder.is_sent = True

    # Обработка повторяющихся напоминаний
    if reminder.repeat_rule:
        next_at = _calc_next_occurrence(reminder.remind_at, reminder.repeat_rule)
        if next_at:
            new_reminder = Reminder(
                user_id=reminder.user_id,
                message=reminder.message,
                remind_at=next_at,
                repeat_rule=reminder.repeat_rule,
                task_id=reminder.task_id,
            )
            session.add(new_reminder)

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


def _calc_next_occurrence(current: datetime, rule: str) -> Optional[datetime]:
    """Вычислить следующее время по repeat_rule.

    Форматы:
      daily              — каждый день
      weekdays           — пн-пт
      weekly:1           — каждую неделю в день 1 (пн), 2 (вт), ...7 (вс)
      weekly:1,3         — каждую неделю в пн и ср
      monthly:15         — каждый месяц 15-го числа
      every:3d           — каждые 3 дня
      every:2w           — каждые 2 недели
    """
    try:
        dt = pendulum.instance(current) if not isinstance(current, pendulum.DateTime) else current

        if rule == "daily":
            return dt.add(days=1)

        if rule == "weekdays":
            nxt = dt.add(days=1)
            while nxt.isoweekday() >= 6:  # 6=сб, 7=вс
                nxt = nxt.add(days=1)
            return nxt

        if rule.startswith("weekly:"):
            days_str = rule.split(":", 1)[1]
            target_days = sorted(int(d) for d in days_str.split(","))
            # target_days: 1=пн ... 7=вс (ISO формат)
            # Используем isoweekday() для корректного сравнения
            for offset in range(1, 8):
                candidate = dt.add(days=offset)
                if candidate.isoweekday() in target_days:
                    return candidate
            return dt.add(weeks=1)

        if rule.startswith("monthly:"):
            day_num = int(rule.split(":", 1)[1])
            nxt = dt.add(months=1)
            try:
                return nxt.set(day=day_num)
            except ValueError:
                # Месяц короче — берём последний день
                return nxt.end_of("month").start_of("day").set(
                    hour=dt.hour, minute=dt.minute, second=dt.second
                )

        if rule.startswith("every:"):
            interval = rule.split(":", 1)[1]
            num = int(interval[:-1])
            unit = interval[-1]
            if unit == "d":
                return dt.add(days=num)
            elif unit == "w":
                return dt.add(weeks=num)
            elif unit == "m":
                return dt.add(months=num)

    except Exception as e:
        logger.warning("Не удалось вычислить следующее время для rule=%s: %s", rule, e)

    return None
