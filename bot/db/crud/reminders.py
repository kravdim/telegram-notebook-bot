"""CRUD-операции для напоминаний."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pendulum
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Reminder, Task, User
from bot.logging_safety import error_type

logger = logging.getLogger(__name__)


async def create_reminder(
    session: AsyncSession,
    user_id: int,
    message: str,
    remind_at: datetime,
    repeat_rule: Optional[str] = None,
    task_id=None,
    commit: bool = True,
    timezone: str | None = None,
) -> Reminder:
    """Создать напоминание."""
    if timezone is None:
        timezone = await session.scalar(select(User.timezone).where(User.telegram_id == user_id))
    reminder = Reminder(
        user_id=user_id,
        message=message,
        remind_at=remind_at,
        repeat_rule=repeat_rule,
        task_id=task_id,
        occurrence_at=remind_at,
        series_timezone=timezone or "Europe/Moscow",
    )
    session.add(reminder)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(reminder)
    return reminder


async def get_pending_reminders(
    session: AsyncSession,
    before: datetime,
) -> List[Reminder]:
    """Получить неотправленные напоминания, время которых наступило.

    Использует FOR UPDATE SKIP LOCKED для предотвращения race condition
    между основным циклом и sweep.
    """
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.is_sent.is_(False),
            Reminder.remind_at <= before,
        )
        .order_by(Reminder.remind_at.asc())
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def mark_sent(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    lease_token: uuid.UUID | None = None,
) -> None:
    """Пометить напоминание как отправленное. Если есть repeat_rule — создаёт следующее."""
    query = select(Reminder).where(Reminder.id == reminder_id).with_for_update()
    if lease_token is not None:
        query = query.where(Reminder.lease_token == lease_token)
    result = await session.execute(query.execution_options(populate_existing=True))
    reminder = result.scalar_one_or_none()
    if not reminder:
        return

    # Callback "Сделано" может повторно прийти для уже доставленного
    # напоминания. Повторная обработка не должна создавать ещё одно
    # occurrence повторяющейся серии.
    if reminder.is_sent:
        return

    reminder.is_sent = True
    reminder.status = "delivered"
    reminder.lease_token = None
    reminder.lease_expires_at = None
    reminder.next_attempt_at = None

    # Обработка повторяющихся напоминаний
    if reminder.repeat_rule:
        next_at = next_future_occurrence(
            pendulum.instance(reminder.remind_at).in_timezone(reminder.series_timezone),
            reminder.repeat_rule,
            pendulum.now("UTC"),
        )
        if next_at:
            new_reminder = Reminder(
                user_id=reminder.user_id,
                message=reminder.message,
                remind_at=next_at,
                repeat_rule=reminder.repeat_rule,
                task_id=reminder.task_id,
                series_id=reminder.series_id,
                occurrence_at=next_at,
                series_timezone=reminder.series_timezone,
            )
            session.add(new_reminder)

    await session.commit()


async def snooze_reminder(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    new_remind_at: datetime,
    user_id: Optional[int] = None,
) -> Optional[Reminder]:
    """Отложить напоминание на новое время."""
    query = select(Reminder).where(Reminder.id == reminder_id)
    if user_id is not None:
        query = query.where(Reminder.user_id == user_id)
    result = await session.execute(query)
    reminder = result.scalar_one_or_none()
    if not reminder:
        return None

    if reminder.snooze_count >= 5:
        return reminder  # Вызывающий код проверит snooze_count

    # Для recurring reminder следующее occurrence уже создаётся после первой
    # доставки. Snooze текущего occurrence не должен породить ещё одну ветку
    # серии при повторной доставке.
    if reminder.is_sent and reminder.repeat_rule:
        reminder.repeat_rule = None
    reminder.remind_at = new_remind_at
    reminder.occurrence_at = new_remind_at
    reminder.is_sent = False
    reminder.status = "snoozed"
    reminder.lease_token = None
    reminder.lease_expires_at = None
    reminder.next_attempt_at = None
    reminder.delivery_attempts = 0
    reminder.snooze_count += 1
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def get_reminder_by_id(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    user_id: Optional[int] = None,
) -> Optional[Reminder]:
    """Получить напоминание по ID."""
    query = select(Reminder).where(Reminder.id == reminder_id)
    if user_id is not None:
        query = query.where(Reminder.user_id == user_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def resolve_reminder(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    user_id: int,
) -> Optional[Reminder]:
    """Идемпотентно отметить доставленное напоминание завершённым."""
    reminder = await get_reminder_by_id(session, reminder_id, user_id)
    if not reminder:
        return None
    if reminder.status != "resolved":
        reminder.status = "resolved"
        reminder.is_sent = True
        await session.commit()
        await session.refresh(reminder)
    return reminder


async def record_delivery_failure(
    session: AsyncSession,
    reminder_id: uuid.UUID,
    error: str,
    terminal: bool = False,
    lease_token: uuid.UUID | None = None,
    retry_after: float | None = None,
) -> None:
    """Записать ошибку доставки и прекратить бесконечные terminal retry."""
    query = select(Reminder).where(Reminder.id == reminder_id).with_for_update()
    if lease_token is not None:
        query = query.where(Reminder.lease_token == lease_token, Reminder.is_sent.is_(False))
    result = await session.execute(query.execution_options(populate_existing=True))
    reminder = result.scalar_one_or_none()
    if not reminder:
        return
    reminder.delivery_attempts += 1
    reminder.last_error = error[:1000]
    if terminal or reminder.delivery_attempts >= 5:
        reminder.status = "failed"
        reminder.is_sent = False
    delay = max(retry_after or 0, min(3600, 30 * 2 ** min(reminder.delivery_attempts, 7)))
    reminder.next_attempt_at = pendulum.now("UTC").add(seconds=delay)
    reminder.lease_token = None
    reminder.lease_expires_at = None
    await session.commit()


@dataclass(frozen=True)
class ReminderClaim:
    id: uuid.UUID
    user_id: int
    message: str
    remind_at: datetime
    token: uuid.UUID


async def claim_due_reminders(
    session: AsyncSession, now: datetime, limit: int = 50,
) -> list[ReminderClaim]:
    """Commit bounded durable ownership before doing any network I/O."""
    rows = await session.scalars(
        select(Reminder).where(
            Reminder.is_sent.is_(False),
            Reminder.status.in_(("pending", "snoozed")),
            Reminder.remind_at <= now,
            or_(Reminder.next_attempt_at.is_(None), Reminder.next_attempt_at <= now),
            or_(Reminder.lease_token.is_(None), Reminder.lease_expires_at <= now),
            or_(Reminder.task_id.is_(None), Reminder.task_id.in_(
                select(Task.id).where(Task.status == "open")
            )),
        ).order_by(Reminder.remind_at, Reminder.id).limit(limit)
        .with_for_update(skip_locked=True)
    )
    claims = []
    for reminder in rows:
        token = uuid.uuid4()
        reminder.lease_token = token
        reminder.lease_expires_at = pendulum.instance(now).add(minutes=5)
        claims.append(ReminderClaim(
            reminder.id, reminder.user_id, reminder.message, reminder.remind_at, token,
        ))
    await session.commit()
    return claims


async def claim_is_active(session: AsyncSession, claim: ReminderClaim) -> bool:
    return await session.scalar(select(Reminder.id).where(
        Reminder.id == claim.id, Reminder.lease_token == claim.token,
        Reminder.is_sent.is_(False), Reminder.status.in_(("pending", "snoozed")),
        Reminder.lease_expires_at > pendulum.now("UTC"),
    )) is not None


async def retry_failed_reminder(session: AsyncSession, reminder_id: uuid.UUID, user_id: int) -> bool:
    """Requeue only an owned failed alarm whose task is still actionable."""
    reminder = await session.scalar(select(Reminder).where(
        Reminder.id == reminder_id, Reminder.user_id == user_id, Reminder.status == "failed",
    ).with_for_update())
    if reminder is None:
        return False
    if reminder.task_id:
        task = await session.get(Task, reminder.task_id)
        if task is None or task.status != "open":
            return False
    reminder.status = "pending"
    reminder.is_sent = False
    reminder.delivery_attempts = 0
    reminder.next_attempt_at = pendulum.now("UTC")
    reminder.lease_token = None
    reminder.lease_expires_at = None
    await session.commit()
    return True


def next_future_occurrence(current: datetime, rule: str, now: datetime) -> datetime | None:
    candidate = current
    for _ in range(10000):
        following = _calc_next_occurrence(candidate, rule)
        if following is None or following > now:
            return following
        candidate = following
    raise ValueError("recurrence did not advance into the future")


async def upsert_task_reminder(
    session: AsyncSession,
    user_id: int,
    task_id: uuid.UUID,
    message: str,
    remind_at: datetime,
    repeat_rule: Optional[str] = None,
    commit: bool = True,
) -> Reminder:
    """Создать или перенести ещё не завершённое напоминание задачи."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.user_id == user_id,
            Reminder.task_id == task_id,
            Reminder.status.in_(("pending", "snoozed", "failed")),
        )
        .order_by(Reminder.created_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    reminder = result.scalar_one_or_none()
    if reminder:
        reminder.message = message
        reminder.remind_at = remind_at
        reminder.occurrence_at = remind_at
        reminder.repeat_rule = repeat_rule
        reminder.is_sent = False
        reminder.status = "pending"
        reminder.lease_token = None
        reminder.lease_expires_at = None
        reminder.next_attempt_at = None
        reminder.delivery_attempts = 0
        reminder.last_error = None
    else:
        reminder = Reminder(
            user_id=user_id,
            task_id=task_id,
            message=message,
            remind_at=remind_at,
            occurrence_at=remind_at,
            repeat_rule=repeat_rule,
        )
        session.add(reminder)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(reminder)
    return reminder


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
        logger.warning(
            "Не удалось вычислить следующее время: error_type=%s",
            error_type(e),
        )

    return None


def is_valid_repeat_rule(rule: Optional[str]) -> bool:
    """Проверить поддерживаемый внутренний формат повторения."""
    if rule is None:
        return True
    if rule in {"daily", "weekdays"}:
        return True
    try:
        if rule.startswith("weekly:"):
            days = [int(day) for day in rule.split(":", 1)[1].split(",")]
            return bool(days) and all(1 <= day <= 7 for day in days)
        if rule.startswith("monthly:"):
            day = int(rule.split(":", 1)[1])
            return 1 <= day <= 31
        if rule.startswith("every:"):
            interval = rule.split(":", 1)[1]
            return len(interval) >= 2 and int(interval[:-1]) > 0 and interval[-1] in "dwm"
    except (TypeError, ValueError):
        return False
    return False
