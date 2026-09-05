"""Переиндексация записей с NULL embedding."""

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import DiaryEntry, MemoirEntry, Note, User
from bot.logging_safety import error_type
from bot.privacy import PRIVACY_NOTICE_VERSION, provider_fingerprint

logger = logging.getLogger(__name__)

# Ссылка на embedding-клиент, устанавливается при инициализации
_embed_client = None


async def _cloud_consent_current(session: AsyncSession, user_id: int) -> bool:
    """Recheck immediately before egress; a batch snapshot is not ongoing consent."""
    return await session.scalar(select(User.telegram_id).where(
        User.telegram_id == user_id,
        User.cloud_processing_enabled.is_(True),
        User.privacy_notice_version >= PRIVACY_NOTICE_VERSION,
        User.privacy_provider_fingerprint == provider_fingerprint(),
    )) is not None


def init(embed_client) -> None:
    """Установить ссылку на embedding-клиент."""
    global _embed_client
    _embed_client = embed_client


async def _reindex_records(
    session: AsyncSession, records: Sequence[Note | DiaryEntry | MemoirEntry],
    cloud: bool, entity: str,
) -> None:
    assert _embed_client is not None
    for index, record in enumerate(records, 1):
        try:
            if cloud and not await _cloud_consent_current(session, record.user_id):
                continue
            text = record.content
            if entity == "note":
                text = f"{getattr(record, 'title', '') or ''} {text}".strip()
            record.embedding = await _embed_client.embed(text)
        except Exception as exc:
            logger.warning("Embedding failed: entity=%s error_type=%s", entity, error_type(exc))
        if index % 10 == 0:
            await session.commit()


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
                User.cloud_processing_enabled.is_(True),
                User.privacy_notice_version >= PRIVACY_NOTICE_VERSION,
                User.privacy_provider_fingerprint == provider_fingerprint(),
            )
        result = await session.execute(note_query.limit(50))
        notes = list(result.scalars().all())

        await _reindex_records(session, notes, cloud_embedding, "note")

        # Diary
        diary_query = select(DiaryEntry).where(DiaryEntry.embedding.is_(None))
        if cloud_embedding:
            diary_query = diary_query.join(
                User, User.telegram_id == DiaryEntry.user_id
            ).where(User.cloud_processing_enabled.is_(True),
                    User.privacy_notice_version >= PRIVACY_NOTICE_VERSION,
                    User.privacy_provider_fingerprint == provider_fingerprint())
        result = await session.execute(diary_query.limit(50))
        diaries = list(result.scalars().all())

        await _reindex_records(session, diaries, cloud_embedding, "diary")

        # Memoir
        memoir_query = select(MemoirEntry).where(MemoirEntry.embedding.is_(None))
        if cloud_embedding:
            memoir_query = memoir_query.join(
                User, User.telegram_id == MemoirEntry.user_id
            ).where(User.cloud_processing_enabled.is_(True),
                    User.privacy_notice_version >= PRIVACY_NOTICE_VERSION,
                    User.privacy_provider_fingerprint == provider_fingerprint())
        result = await session.execute(memoir_query.limit(50))
        memoirs = list(result.scalars().all())

        await _reindex_records(session, memoirs, cloud_embedding, "memoir")

        await session.commit()

    total = len(notes) + len(diaries) + len(memoirs)
    if total:
        logger.info("Reindex: обработано %d записей", total)
