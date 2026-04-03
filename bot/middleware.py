"""Middleware: whitelist + rate limiting."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings

logger = logging.getLogger(__name__)

# Rate limiting: max сообщений за окно (секунды)
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 20


class WhitelistMiddleware(BaseMiddleware):
    """Проверяет, что пользователь в allowed_telegram_ids."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not settings.allowed_telegram_ids:
            return await handler(event, data)

        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, "from_user") and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        if user_id not in settings.allowed_telegram_ids:
            logger.info("Отклонён пользователь %s — не в whitelist", user_id)
            if isinstance(event, Message):
                await event.answer(
                    "Извините, у вас нет доступа к этому боту. "
                    "Обратитесь к администратору."
                )
            return None

        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Анти-флуд: ограничение частоты сообщений на пользователя."""

    def __init__(
        self,
        window: int = _RATE_LIMIT_WINDOW,
        max_requests: int = _RATE_LIMIT_MAX,
    ):
        self._window = window
        self._max_requests = max_requests
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        cutoff = now - self._window

        # Удаляем старые отметки
        ts = self._timestamps[user_id]
        self._timestamps[user_id] = [t for t in ts if t > cutoff]

        if len(self._timestamps[user_id]) >= self._max_requests:
            logger.warning("Rate limit: user %s — %d запросов за %ds",
                           user_id, len(self._timestamps[user_id]), self._window)
            if isinstance(event, Message):
                await event.answer(
                    "Слишком много сообщений. Подожди минутку ⏳"
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком частые нажатия. Подожди минутку ⏳")
            return None

        self._timestamps[user_id].append(now)
        return await handler(event, data)
