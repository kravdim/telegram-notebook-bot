"""Переиндексация записей с NULL embedding."""

import logging

from sqlalchemy import select

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import DiaryEntry, MemoirEntry, Note, User
from bot.logging_safety import error_type

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

    cloud_embedding = (
        settings.yaml_config.get("embedding", {}).get("provider") == "cloud"
    )

    async with async_session() as session:
        # Notes
        note_query = select(Note).where(Note.embedding.is_(None))
        if cloud_embedding:
            note_query = note_query.join(User, User.telegram_id == Note.user_id).where(
                User.cloud_processing_enabled.is_(True)
            )
        result = await session.execute(note_query.limit(50))
        notes = list(result.scalars().all())

        for i, note in enumerate(notes, 1):
            try:
                text = f"{note.title or ''} {note.content}"
                embedding = await _embed_client.embed(text.strip())
                note.embedding = embedding
            except Exception as e:
                logger.warning(
                    "Embedding failed: entity=note error_type=%s", error_type(e)
                )
            if i % 10 == 0:
                await session.commit()

        # Diary
        diary_query = select(DiaryEntry).where(DiaryEntry.embedding.is_(None))
        if cloud_embedding:
            diary_query = diary_query.join(
                User, User.telegram_id == DiaryEntry.user_id
            ).where(User.cloud_processing_enabled.is_(True))
        result = await session.execute(diary_query.limit(50))
        diaries = list(result.scalars().all())

        for i, entry in enumerate(diaries, 1):
            try:
                embedding = await _embed_client.embed(entry.content)
                entry.embedding = embedding
            except Exception as e:
                logger.warning(
                    "Embedding failed: entity=diary error_type=%s", error_type(e)
                )
            if i % 10 == 0:
                await session.commit()

        # Memoir
        memoir_query = select(MemoirEntry).where(MemoirEntry.embedding.is_(None))
        if cloud_embedding:
            memoir_query = memoir_query.join(
                User, User.telegram_id == MemoirEntry.user_id
            ).where(User.cloud_processing_enabled.is_(True))
        result = await session.execute(memoir_query.limit(50))
        memoirs = list(result.scalars().all())

        for i, entry in enumerate(memoirs, 1):
            try:
                embedding = await _embed_client.embed(entry.content)
                entry.embedding = embedding
            except Exception as e:
                logger.warning(
                    "Embedding failed: entity=memoir error_type=%s", error_type(e)
                )
            if i % 10 == 0:
                await session.commit()

        await session.commit()

    total = len(notes) + len(diaries) + len(memoirs)
    if total:
        logger.info("Reindex: обработано %d записей", total)
