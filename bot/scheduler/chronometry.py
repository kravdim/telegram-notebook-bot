"""Планировщик хронометража: периодический опрос в рабочее время."""

import logging
import random

import pendulum
from aiogram import Bot

from bot.db.crud.users import get_all_users, update_user_settings
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

# user_id → message_id хронометражного вопроса (None = не ожидаем)
_awaiting_response: dict[int, int] = {}

# user_id → индекс последнего вопроса (чтобы не повторять подряд)
_last_question_idx: dict[int, int] = {}

_CHRONOMETRY_QUESTIONS = [
    "⏱ Чем занимаешься сейчас?",
    "⏱ Что делаешь в данный момент?",
    "⏱ На чём сейчас сфокусирован?",
    "⏱ Чем сейчас занят?",
    "⏱ Как проводишь время прямо сейчас?",
    "⏱ Что сейчас в работе?",
    "⏱ Расскажи, чем занят?",
    "⏱ Над чем сейчас работаешь?",
    "⏱ Что происходит прямо сейчас?",
    "⏱ Чему посвящаешь время сейчас?",
    "⏱ Как дела? Чем занят?",
    "⏱ Что на повестке прямо сейчас?",
]


def is_awaiting_response(user_id: int) -> bool:
    """Проверить, ожидается ли ответ на хронометраж от пользователя."""
    return user_id in _awaiting_response


def get_chrono_message_id(user_id: int) -> int | None:
    """Вернуть message_id хронометражного вопроса (или None)."""
    return _awaiting_response.get(user_id)


def clear_awaiting(user_id: int) -> None:
    """Очистить флаг ожидания ответа."""
    _awaiting_response.pop(user_id, None)


async def send_chronometry_prompts(bot: Bot) -> None:
    """Проверить и отправить вопросы хронометража."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        if not user.chronometry_enabled:
            continue

        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)

            # Не беспокоим в нерабочее время (work_days: ISO 1=Пн...7=Вс)
            if now.isoweekday() not in user.work_days:
                continue

            current_time = now.time()
            if current_time < user.work_start_time or current_time > user.work_end_time:
                continue

            # Режим фокуса
            if user.focus_until:
                focus_until = pendulum.instance(user.focus_until)
                if now < focus_until:
                    continue

            # Проверяем интервал (из БД, переживает рестарты)
            interval_sec = user.chronometry_interval_min * 60
            if user.chronometry_last_asked:
                last = pendulum.instance(user.chronometry_last_asked)
                if (now - last).in_seconds() < interval_sec:
                    continue

            # Выбираем вопрос, отличный от предыдущего
            last_idx = _last_question_idx.get(user.telegram_id, -1)
            available = [i for i in range(len(_CHRONOMETRY_QUESTIONS)) if i != last_idx]
            idx = random.choice(available)
            _last_question_idx[user.telegram_id] = idx

            sent = await bot.send_message(
                chat_id=user.telegram_id,
                text=_CHRONOMETRY_QUESTIONS[idx],
            )
            _awaiting_response[user.telegram_id] = sent.message_id

            # Обновляем timestamp в БД только после успешной отправки
            async with async_session() as session:
                await update_user_settings(
                    session, user.telegram_id,
                    chronometry_last_asked=now,
                )
            logger.info("Хронометраж: вопрос отправлен %s", user.telegram_id)

        except Exception as e:
            logger.error(
                "Ошибка хронометража для %s: %s",
                user.telegram_id, e, exc_info=True,
            )
