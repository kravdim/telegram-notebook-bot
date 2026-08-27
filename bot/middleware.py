"""Middleware: whitelist + rate limiting."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings

logger = logging.getLogger(__name__)

# Rate limiting: max сообщений за окно (секунды)
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 20


class PrivateChatMiddleware(BaseMiddleware):
    """Fail closed unless an authenticated update belongs to a private chat."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.from_user is None or event.chat.type != ChatType.PRIVATE:
                logger.warning(
                    "Rejected non-private message: chat_type=%s has_user=%s",
                    event.chat.type,
                    event.from_user is not None,
                )
                return None
        elif isinstance(event, CallbackQuery):
            callback_message = event.message
            chat = getattr(callback_message, "chat", None)
            if (
                event.from_user is None
                or chat is None
                or chat.type != ChatType.PRIVATE
            ):
                logger.warning(
                    "Rejected callback outside private chat: chat_type=%s has_user=%s",
                    getattr(chat, "type", None),
                    event.from_user is not None,
                )
                await event.answer(
                    "Эта кнопка доступна только в личном чате с ботом.",
                    show_alert=True,
                )
                return None
        else:
            logger.warning("Rejected unsupported Telegram update type=%s", type(event).__name__)
            return None
        return await handler(event, data)


class WhitelistMiddleware(BaseMiddleware):
    """Проверяет, что пользователь в allowed_telegram_ids."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, "from_user") and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            logger.warning("Отклонено событие без authenticated from_user")
            return None

        if settings.allow_all_users:
            return await handler(event, data)

        allowed = set(settings.allowed_telegram_ids) | set(settings.admin_telegram_ids)
        if user_id not in allowed:
            logger.info("Отклонён authenticated user вне whitelist")
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
            logger.warning(
                "Rate limit exceeded: requests=%d window_seconds=%d",
                len(self._timestamps[user_id]),
                self._window,
            )
            if isinstance(event, Message):
                await event.answer(
                    "Слишком много сообщений. Подожди минутку ⏳"
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком частые нажатия. Подожди минутку ⏳")
            return None

        self._timestamps[user_id].append(now)
        return await handler(event, data)
