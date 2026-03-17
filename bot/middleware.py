"""Middleware для проверки whitelist."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.config import settings

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """Проверяет, что пользователь в allowed_telegram_ids."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not settings.allowed_telegram_ids:
            # Если whitelist пуст — пропускаем всех
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
