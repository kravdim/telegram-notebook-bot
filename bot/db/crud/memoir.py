"""CRUD-операции для мемуарника."""

from datetime import date
from typing import List, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import MemoirEntry
from bot.embeddings.identity import embedding_identity


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
        if entry.content != content:
            entry.embedding = None
            entry.embedding_model = None
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
    *, start_date: date | None = None, end_date: date | None = None,
) -> List[MemoirEntry]:
    """Получить последние записи мемуарника."""
    statement = select(MemoirEntry).where(
        MemoirEntry.user_id == user_id, MemoirEntry.period_type == period_type,
    ).order_by(MemoirEntry.event_date.desc()).limit(limit)
    if start_date is not None:
        statement = statement.where(MemoirEntry.event_date >= start_date)
    if end_date is not None:
        statement = statement.where(MemoirEntry.event_date < end_date)
    result = await session.execute(statement)
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
    *, tz: str = "Europe/Moscow", start_date: date | None = None,
) -> List[dict]:
    """Статистика ценностей за N дней."""
    import pendulum
    today = pendulum.now(tz).date()
    since = start_date or today.subtract(days=days - 1)
    result = await session.execute(
        select(
            MemoirEntry.value_tag,
            func.count().label("cnt"),
        )
        .where(
            MemoirEntry.user_id == user_id,
            MemoirEntry.period_type == "day",
            MemoirEntry.event_date >= since,
            MemoirEntry.event_date <= today,
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
                       CASE WHEN embedding_model = :embedding_model
                            THEN COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0)
                            ELSE 0 END * 0.6 +
                       COALESCE(similarity(content, CAST(:query AS text)), 0) * 0.4 AS score
                FROM memoir_entries
                WHERE user_id = :uid
                  AND (content % CAST(:query AS text) OR content ILIKE :pattern
                       OR (embedding_model = :embedding_model AND embedding IS NOT NULL
                           AND embedding <=> CAST(:emb AS vector) < 0.8))
                ORDER BY score DESC
                LIMIT :lim
            """),
            {"uid": user_id, "query": query, "pattern": f"%{query}%",
             "emb": query_embedding, "lim": limit, "embedding_model": embedding_identity()},
        )
    else:
        res = await session.execute(
            select(MemoirEntry)
            .where(MemoirEntry.user_id == user_id, MemoirEntry.content.ilike(f"%{query}%"))
            .limit(limit)
        )
        return list(res.scalars().all())
    return list(res.fetchall())
