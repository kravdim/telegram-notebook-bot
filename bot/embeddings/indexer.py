"""Фоновая индексация: создание embedding при записи."""

import logging
from typing import Optional

from bot.embeddings.base import EmbeddingClient

logger = logging.getLogger(__name__)

_client: Optional[EmbeddingClient] = None


def init(client: EmbeddingClient) -> None:
    """Установить embedding-клиент."""
    global _client
    _client = client


async def get_embedding(text: str) -> Optional[list]:
    """Получить embedding для текста. Возвращает None если клиент недоступен."""
    if not _client:
        return None
    try:
        return await _client.embed(text)
    except Exception as e:
        logger.warning("Ошибка получения embedding: %s", e)
        return None


def get_client() -> Optional[EmbeddingClient]:
    """Получить текущий embedding-клиент."""
    return _client
