"""Абстрактный интерфейс для embedding-провайдеров."""

from abc import ABC, abstractmethod
from typing import List


class EmbeddingClient(ABC):
    """Абстрактный embedding-клиент."""

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Получить embedding для одного текста."""

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Получить embeddings для списка текстов."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверить доступность провайдера."""
