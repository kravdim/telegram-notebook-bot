"""CRUD-операции для пользователей."""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Получить пользователя по telegram_id."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str,
    role: str = "user",
    timezone: str = "Europe/Moscow",
) -> tuple[User, bool]:
    """Получить или создать пользователя. Возвращает (user, created)."""
    user = await get_user(session, telegram_id)
    if user:
        return user, False

    user = User(
        telegram_id=telegram_id,
        username=username,
        role=role,
        timezone=timezone,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, True


async def update_user_settings(
    session: AsyncSession,
    telegram_id: int,
    commit: bool = True,
    **kwargs,
) -> User | None:
    """Обновить настройки пользователя."""
    user = await get_user(session, telegram_id)
    if not user:
        return None

    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)

    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(user)
    return user


async def get_all_users(session: AsyncSession) -> list[User]:
    """Получить всех пользователей (для admin)."""
    result = await session.execute(select(User))
    return list(result.scalars().all())


_DATE_MARKERS = {
    "digest_sent_date": User.digest_sent_date,
    "digest_evening_sent_date": User.digest_evening_sent_date,
    "memoir_asked_date": User.memoir_asked_date,
    "weekly_review_sent_date": User.weekly_review_sent_date,
}


async def claim_date_marker(
    session: AsyncSession, telegram_id: int, marker: str, value: date
) -> bool:
    """Атомарно занять дневной scheduler-slot между несколькими инстансами."""
    column = _DATE_MARKERS.get(marker)
    if column is None:
        raise ValueError(f"unsupported date marker: {marker}")
    result = await session.execute(
        update(User)
        .where(User.telegram_id == telegram_id, column.is_distinct_from(value))
        .values({marker: value})
        .returning(User.telegram_id)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


async def release_date_marker(
    session: AsyncSession, telegram_id: int, marker: str, value: date
) -> None:
    """Освободить только тот слот, который занял текущий неуспешный job."""
    column = _DATE_MARKERS.get(marker)
    if column is None:
        raise ValueError(f"unsupported date marker: {marker}")
    await session.execute(
        update(User)
        .where(User.telegram_id == telegram_id, column == value)
        .values({marker: None})
    )
    await session.commit()


async def claim_task_reminder_slot(
    session: AsyncSession, telegram_id: int, slot_date: date, slot_hour: int
) -> bool:
    result = await session.execute(
        update(User)
        .where(
            User.telegram_id == telegram_id,
            or_(
                User.tasks_reminder_last_date.is_distinct_from(slot_date),
                User.tasks_reminder_last_hour.is_(None),
                User.tasks_reminder_last_hour < slot_hour,
            ),
        )
        .values(
            tasks_reminder_last_date=slot_date,
            tasks_reminder_last_hour=slot_hour,
        )
        .returning(User.telegram_id)
    )
    claimed = result.scalar_one_or_none() is not None
    await session.commit()
    return claimed


async def release_task_reminder_slot(
    session: AsyncSession, telegram_id: int, slot_date: date, slot_hour: int
) -> None:
    await session.execute(
        update(User)
        .where(
            User.telegram_id == telegram_id,
            User.tasks_reminder_last_date == slot_date,
            User.tasks_reminder_last_hour == slot_hour,
        )
        .values(tasks_reminder_last_date=None, tasks_reminder_last_hour=None)
    )
    await session.commit()
