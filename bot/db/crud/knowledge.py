"""CRUD-операции для базы знаний (knowledge_base)."""

import logging
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import KnowledgeChunk

logger = logging.getLogger(__name__)


def _topic_hint(query: str) -> str:
    normalized = (query or "").casefold().replace("ё", "е")
    hints = {
        "слоны": ("слон", "бифштекс", "декомпоз"),
        "хронометраж": ("хронометраж", "хронофаг", "фотографи"),
        "мемуарник": ("мемуар", "главное событие дня", "гсд"),
        "прокрастинация": ("прокраст", "не хочется начинать", "откладыва"),
    }
    for topic, markers in hints.items():
        if any(marker in normalized for marker in markers):
            return topic
    return ""


async def add_chunk(
    session: AsyncSession,
    source: str,
    topic: str,
    content: str,
    embedding: Optional[List[float]] = None,
) -> KnowledgeChunk:
    """Добавить чанк в базу знаний."""
    chunk = KnowledgeChunk(
        source=source,
        topic=topic,
        content=content,
        embedding=embedding,
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(chunk)
    return chunk


async def hybrid_search(
    session: AsyncSession,
    query: str,
    query_embedding: Optional[List[float]] = None,
    limit: int = 5,
    semantic_weight: float = 0.6,
    text_weight: float = 0.4,
) -> List[KnowledgeChunk]:
    """Гибридный поиск: vector similarity + text trigram.

    Если embedding недоступен — только текстовый поиск.
    """
    topic_hint = _topic_hint(query)
    if query_embedding:
        # Гибридный поиск: RRF (Reciprocal Rank Fusion)
        sql = text("""
            WITH vector_results AS (
                SELECT id, content, source, topic,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:emb AS vector)) AS vrank
                FROM knowledge_base
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:emb AS vector)
                LIMIT 20
            ),
            text_results AS (
                SELECT id, content, source, topic,
                       ROW_NUMBER() OVER (ORDER BY similarity(content, CAST(:query AS text)) DESC) AS trank
                FROM knowledge_base
                WHERE content % CAST(:query AS text)
                ORDER BY similarity(content, CAST(:query AS text)) DESC
                LIMIT 20
            ),
            combined AS (
                SELECT COALESCE(v.id, t.id) AS id,
                       COALESCE(v.content, t.content) AS content,
                       COALESCE(v.source, t.source) AS source,
                       COALESCE(v.topic, t.topic) AS topic,
                       :sw / (60.0 + COALESCE(v.vrank, 100)) +
                       :tw / (60.0 + COALESCE(t.trank, 100)) +
                       CASE WHEN lower(COALESCE(v.topic, t.topic)) = :topic
                            THEN 0.05 ELSE 0 END AS rrf_score
                FROM vector_results v
                FULL OUTER JOIN text_results t ON v.id = t.id
            )
            SELECT id, content, source, topic
            FROM combined
            ORDER BY rrf_score DESC
            LIMIT :lim
        """)
        result = await session.execute(
            sql,
            {"emb": str(query_embedding), "query": query, "topic": topic_hint,
             "sw": semantic_weight, "tw": text_weight, "lim": limit},
        )
    else:
        # Только текстовый поиск (trigram similarity)
        sql = text("""
            SELECT id, content, source, topic
            FROM knowledge_base
            WHERE content % CAST(:query AS text) OR content ILIKE :pattern
               OR lower(topic) = :topic
            ORDER BY CASE WHEN lower(topic) = :topic THEN 0 ELSE 1 END,
                     similarity(content, CAST(:query AS text)) DESC
            LIMIT :lim
        """)
        result = await session.execute(
            sql, {"query": query, "pattern": f"%{query}%", "topic": topic_hint,
                  "lim": limit}
        )

    rows = result.fetchall()
    chunks = []
    for row in rows:
        c = KnowledgeChunk()
        c.id = row.id
        c.content = row.content
        c.source = row.source
        c.topic = row.topic
        chunks.append(c)
    return chunks


async def get_all_chunks(session: AsyncSession) -> List[KnowledgeChunk]:
    """Получить все чанки (для переиндексации)."""
    result = await session.execute(select(KnowledgeChunk))
    return list(result.scalars().all())
