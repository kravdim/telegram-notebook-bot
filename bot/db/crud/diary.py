"""CRUD-операции для дневника."""

from datetime import date

import pendulum
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import DiaryEntry


async def create_diary_entry(
    session: AsyncSession,
    user_id: int,
    content: str,
    entry_date: date = None,
    tz: str = "Europe/Moscow",
) -> DiaryEntry:
    """Создать запись в дневнике."""
    if entry_date is None:
        entry_date = pendulum.now(tz).date()

    entry = DiaryEntry(
        user_id=user_id,
        content=content,
        entry_date=entry_date,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry
