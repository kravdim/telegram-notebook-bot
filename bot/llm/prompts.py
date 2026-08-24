"""Загрузка промптов из БД с кэшированием."""

import logging
import time
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import PromptVersion

logger = logging.getLogger(__name__)

# Кэш: prompt_key → (content, timestamp)
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 минут


async def get_prompt(session: AsyncSession, prompt_key: str) -> Optional[str]:
    """Получить активный промпт по ключу. Кэширование 5 минут."""
    now = time.monotonic()

    if prompt_key in _cache:
        content, cached_at = _cache[prompt_key]
        if now - cached_at < _CACHE_TTL:
            return content

    result = await session.execute(
        select(PromptVersion.content)
        .where(PromptVersion.prompt_key == prompt_key, PromptVersion.is_active.is_(True))
    )
    row = result.scalar_one_or_none()

    if row:
        _cache[prompt_key] = (row, now)
        return row

    logger.warning("Промпт '%s' не найден в БД", prompt_key)
    return None


async def get_all_prompts(session: AsyncSession) -> list:
    """Получить список всех активных промптов (для /prompts)."""
    result = await session.execute(
        select(PromptVersion)
        .where(PromptVersion.is_active.is_(True))
        .order_by(PromptVersion.prompt_key)
    )
    return list(result.scalars().all())


def invalidate_cache(prompt_key: Optional[str] = None) -> None:
    """Сбросить кэш промптов."""
    if prompt_key:
        _cache.pop(prompt_key, None)
    else:
        _cache.clear()
