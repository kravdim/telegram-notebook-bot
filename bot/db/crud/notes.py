"""CRUD-операции для заметок."""

from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Note
from bot.embeddings.identity import embedding_identity


async def create_note(
    session: AsyncSession,
    user_id: int,
    content: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    commit: bool = True,
) -> Note:
    """Создать заметку."""
    note = Note(
        user_id=user_id,
        content=content,
        title=title,
        tags=tags or [],
    )
    session.add(note)
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(note)
    return note


async def hybrid_search_notes(
    session: AsyncSession,
    user_id: int,
    query: str,
    query_embedding: Optional[str] = None,
    limit: int = 5,
) -> list:
    """Гибридный поиск по заметкам (векторный + текстовый)."""
    if query_embedding:
        res = await session.execute(
            text("""
                SELECT id, title, content,
                       CASE WHEN embedding_model = :embedding_model
                            THEN COALESCE(1 - (embedding <=> CAST(:emb AS vector)), 0)
                            ELSE 0 END * 0.5 +
                       GREATEST(
                           COALESCE(similarity(content, CAST(:query AS text)), 0),
                           COALESCE(similarity(title, CAST(:query AS text)), 0)
                       ) * 0.5 AS score
                FROM notes
                WHERE user_id = :uid
                  AND (content % CAST(:query AS text) OR content ILIKE :pattern
                       OR title % CAST(:query AS text) OR title ILIKE :pattern
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
            select(Note)
            .where(
                Note.user_id == user_id,
                Note.content.ilike(f"%{query}%") | Note.title.ilike(f"%{query}%"),
            )
            .limit(limit)
        )
        return list(res.scalars().all())
    return list(res.fetchall())
