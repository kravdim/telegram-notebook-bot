"""Переиндексация записей с NULL embedding."""

import logging

from sqlalchemy import select

from bot.db.engine import async_session
from bot.db.models import DiaryEntry, MemoirEntry, Note

logger = logging.getLogger(__name__)

# Ссылка на embedding-клиент, устанавливается при инициализации
_embed_client = None


def init(embed_client) -> None:
    """Установить ссылку на embedding-клиент."""
    global _embed_client
    _embed_client = embed_client


async def reindex_missing_embeddings() -> None:
    """Найти записи без embedding и проиндексировать их."""
    if not _embed_client:
        return

    async with async_session() as session:
        # Notes
        result = await session.execute(
            select(Note).where(Note.embedding.is_(None)).limit(50)
        )
        notes = list(result.scalars().all())

        for i, note in enumerate(notes, 1):
            try:
                text = f"{note.title or ''} {note.content}"
                embedding = await _embed_client.embed(text.strip())
                note.embedding = embedding
            except Exception as e:
                logger.warning("Ошибка embedding для note %s: %s", note.id, e)
            if i % 10 == 0:
                await session.commit()

        # Diary
        result = await session.execute(
            select(DiaryEntry).where(DiaryEntry.embedding.is_(None)).limit(50)
        )
        diaries = list(result.scalars().all())

        for i, entry in enumerate(diaries, 1):
            try:
                embedding = await _embed_client.embed(entry.content)
                entry.embedding = embedding
            except Exception as e:
                logger.warning("Ошибка embedding для diary %s: %s", entry.id, e)
            if i % 10 == 0:
                await session.commit()

        # Memoir
        result = await session.execute(
            select(MemoirEntry).where(MemoirEntry.embedding.is_(None)).limit(50)
        )
        memoirs = list(result.scalars().all())

        for i, entry in enumerate(memoirs, 1):
            try:
                embedding = await _embed_client.embed(entry.content)
                entry.embedding = embedding
            except Exception as e:
                logger.warning("Ошибка embedding для memoir %s: %s", entry.id, e)
            if i % 10 == 0:
                await session.commit()

        await session.commit()

    total = len(notes) + len(diaries) + len(memoirs)
    if total:
        logger.info("Reindex: обработано %d записей", total)
