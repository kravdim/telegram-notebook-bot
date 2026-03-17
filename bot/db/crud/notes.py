"""CRUD-операции для заметок."""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Note


async def create_note(
    session: AsyncSession,
    user_id: int,
    content: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Note:
    """Создать заметку."""
    note = Note(
        user_id=user_id,
        content=content,
        title=title,
        tags=tags or [],
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note
