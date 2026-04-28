"""CRUD-операции для дневника."""

from datetime import date
from typing import Optional

import pendulum
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import DiaryEntry


async def create_diary_entry(
    session: AsyncSession,
    user_id: int,
    content: str,
    entry_date: Optional[date] = None,
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


async def hybrid_search_diary(
    session: AsyncSession,
    user_id: int,
    query: str,
    query_embedding: Optional[str] = None,
    limit: int = 5,
) -> list:
    """Гибридный поиск по дневнику (векторный + текстовый)."""
    if query_embedding:
        res = await session.execute(
            text("""
                SELECT id, content,
                       COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0) * 0.6 +
                       COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                FROM diary_entries
                WHERE user_id = :uid
                  AND (content % CAST(:query AS text) OR content ILIKE :pattern
                       OR (embedding IS NOT NULL AND embedding <=> CAST(:emb AS vector) < 0.8))
                ORDER BY score DESC
                LIMIT :lim
            """),
            {"uid": user_id, "query": query, "pattern": f"%{query}%",
             "emb": query_embedding, "lim": limit},
        )
    else:
        res = await session.execute(
            select(DiaryEntry)
            .where(DiaryEntry.user_id == user_id, DiaryEntry.content.ilike(f"%{query}%"))
            .limit(limit)
        )
    return res.fetchall()
