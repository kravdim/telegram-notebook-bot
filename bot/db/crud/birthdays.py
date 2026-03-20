"""CRUD-операции для дней рождения."""

from datetime import date
from typing import List, Optional

from sqlalchemy import select, extract, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Birthday


async def add_birthday(
    session: AsyncSession,
    user_id: int,
    name: str,
    birth_date: date,
    note: Optional[str] = None,
) -> Birthday:
    """Добавить день рождения."""
    birthday = Birthday(
        user_id=user_id,
        name=name,
        birth_date=birth_date,
        note=note,
    )
    session.add(birthday)
    await session.commit()
    await session.refresh(birthday)
    return birthday


async def get_birthdays_on_date(
    session: AsyncSession,
    user_id: int,
    today: date,
) -> List[Birthday]:
    """Получить дни рождения на конкретную дату (по месяцу и дню)."""
    result = await session.execute(
        select(Birthday)
        .where(
            Birthday.user_id == user_id,
            extract("month", Birthday.birth_date) == today.month,
            extract("day", Birthday.birth_date) == today.day,
        )
    )
    return list(result.scalars().all())


async def get_upcoming_birthdays(
    session: AsyncSession,
    user_id: int,
    today: date,
    days_ahead: int = 7,
) -> List[Birthday]:
    """Получить дни рождения на ближайшие N дней."""
    results = []
    for delta in range(days_ahead):
        check_date = today + __import__("datetime").timedelta(days=delta)
        bdays = await get_birthdays_on_date(session, user_id, check_date)
        for b in bdays:
            b._upcoming_date = check_date  # type: ignore
            results.append(b)
    return results


async def get_all_birthdays(
    session: AsyncSession,
    user_id: int,
) -> List[Birthday]:
    """Получить все дни рождения пользователя."""
    result = await session.execute(
        select(Birthday)
        .where(Birthday.user_id == user_id)
        .order_by(
            extract("month", Birthday.birth_date),
            extract("day", Birthday.birth_date),
        )
    )
    return list(result.scalars().all())


async def delete_birthday(
    session: AsyncSession,
    birthday_id,
    user_id: int,
) -> bool:
    """Удалить день рождения."""
    result = await session.execute(
        select(Birthday).where(
            Birthday.id == birthday_id,
            Birthday.user_id == user_id,
        )
    )
    birthday = result.scalar_one_or_none()
    if not birthday:
        return False
    await session.delete(birthday)
    await session.commit()
    return True
