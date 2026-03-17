"""Embedding через Ollama (macOS, локально)."""

import logging
from typing import List

import httpx

from bot.config import settings
from bot.embeddings.base import EmbeddingClient

logger = logging.getLogger(__name__)


class OllamaEmbeddingClient(EmbeddingClient):
    """Ollama + nomic-embed-text."""

    def __init__(self):
        yaml_cfg = settings.yaml_config
        embed_cfg = yaml_cfg.get("embedding", {})
        self.base_url = embed_cfg.get("base_url", "http://localhost:11434")
        self.model = embed_cfg.get("model", "nomic-embed-text")
        self.dimensions = embed_cfg.get("dimensions", 768)

    async def embed(self, text: str) -> List[float]:
        """Получить embedding для текста через Ollama API."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            response.raise_for_status()
            data = response.json()
            return data["embedding"]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Получить embeddings для списка текстов (последовательно)."""
        results = []
        for text in texts:
            emb = await self.embed(text)
            results.append(emb)
        return results

    async def health_check(self) -> bool:
        """Проверить доступность Ollama."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False
