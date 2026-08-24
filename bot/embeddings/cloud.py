"""Embedding через облачный API (VPS)."""

import logging
from typing import List

from openai import AsyncOpenAI

from bot.config import settings
from bot.embeddings.base import EmbeddingClient

logger = logging.getLogger(__name__)


class CloudEmbeddingClient(EmbeddingClient):
    """Embedding через OpenAI-совместимый API."""

    def __init__(self):
        yaml_cfg = settings.yaml_config
        embed_cfg = yaml_cfg.get("embedding", {})
        self.model = embed_cfg.get("model", "text-embedding-3-small")
        self.dimensions = embed_cfg.get("dimensions", 768)

        self.client = AsyncOpenAI(
            base_url=settings.embedding_base_url or embed_cfg.get("base_url", ""),
            api_key=settings.embedding_api_key,
            timeout=30,
        )

    async def embed(self, text: str) -> List[float]:
        """Получить embedding через API."""
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
            dimensions=self.dimensions,
        )
        embedding = response.data[0].embedding
        self._validate_dimensions(embedding)
        return embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Получить embeddings для списка текстов."""
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )
        embeddings = [item.embedding for item in response.data]
        for embedding in embeddings:
            self._validate_dimensions(embedding)
        return embeddings

    def _validate_dimensions(self, embedding: List[float]) -> None:
        if len(embedding) != self.dimensions:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimensions}, "
                f"got {len(embedding)}"
            )

    async def health_check(self) -> bool:
        """Проверить доступность API."""
        try:
            await self.embed("test")
            return True
        except Exception:
            return False
