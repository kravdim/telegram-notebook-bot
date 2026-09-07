"""Идентификатор совместимости embedding без ключей провайдера."""

import hashlib
import json

from bot.config import settings


def embedding_identity() -> str:
    config = settings.yaml_config.get("embedding", {})
    definition = {
        "provider": config.get("provider", "ollama"),
        "model": config.get("model", "text-embedding-3-small"
                            if config.get("provider") == "cloud" else "nomic-embed-text"),
        "dimensions": config.get("dimensions", 768),
        "base_url": settings.embedding_base_url or config.get("base_url", ""),
        "text_format": 1,
    }
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
