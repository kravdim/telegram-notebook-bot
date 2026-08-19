"""CRUD-операции для мемуарника."""

import uuid
from datetime import date
from typing import List, Optional

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import MemoirEntry


async def create_memoir_entry(
    session: AsyncSession,
    user_id: int,
    event_date: date,
    content: str,
    value_tag: Optional[str] = None,
    period_type: str = "day",
    commit: bool = True,
) -> MemoirEntry:
    """Создать запись мемуарника (upsert по user_id + event_date + period_type)."""
    result = await session.execute(
        select(MemoirEntry).where(
            MemoirEntry.user_id == user_id,
            MemoirEntry.event_date == event_date,
            MemoirEntry.period_type == period_type,
        )
    )
    entry = result.scalar_one_or_none()
    if entry:
        entry.content = content
        entry.value_tag = value_tag
    else:
        entry = MemoirEntry(
            user_id=user_id,
            event_date=event_date,
            content=content,
            value_tag=value_tag,
            period_type=period_type,
        )
        session.add(entry)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(entry)
    return entry


async def get_memoir_entries(
    session: AsyncSession,
    user_id: int,
    period_type: str = "day",
    limit: int = 7,
) -> List[MemoirEntry]:
    """Получить последние записи мемуарника."""
    result = await session.execute(
        select(MemoirEntry)
        .where(
            MemoirEntry.user_id == user_id,
            MemoirEntry.period_type == period_type,
        )
        .order_by(MemoirEntry.event_date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_memoir_for_date(
    session: AsyncSession,
    user_id: int,
    event_date: date,
    period_type: str = "day",
) -> Optional[MemoirEntry]:
    """Получить запись мемуарника за конкретную дату."""
    result = await session.execute(
        select(MemoirEntry).where(
            MemoirEntry.user_id == user_id,
            MemoirEntry.event_date == event_date,
            MemoirEntry.period_type == period_type,
        )
    )
    return result.scalar_one_or_none()


async def get_value_stats(
    session: AsyncSession,
    user_id: int,
    days: int = 90,
) -> List[dict]:
    """Статистика ценностей за N дней."""
    import pendulum
    since = pendulum.now().subtract(days=days).date()
    result = await session.execute(
        select(
            MemoirEntry.value_tag,
            func.count().label("cnt"),
        )
        .where(
            MemoirEntry.user_id == user_id,
            MemoirEntry.period_type == "day",
            MemoirEntry.event_date >= since,
            MemoirEntry.value_tag.isnot(None),
        )
        .group_by(MemoirEntry.value_tag)
        .order_by(func.count().desc())
    )
    return [{"value": row.value_tag, "count": row.cnt} for row in result.all()]


async def hybrid_search_memoir(
    session: AsyncSession,
    user_id: int,
    query: str,
    query_embedding: Optional[str] = None,
    limit: int = 5,
) -> list:
    """Гибридный поиск по мемуарнику (векторный + текстовый)."""
    if query_embedding:
        res = await session.execute(
            text("""
                SELECT id, content,
                       COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0) * 0.6 +
                       COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                FROM memoir_entries
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
            select(MemoirEntry)
            .where(MemoirEntry.user_id == user_id, MemoirEntry.content.ilike(f"%{query}%"))
            .limit(limit)
        )
        return list(res.scalars().all())
    return res.fetchall()
