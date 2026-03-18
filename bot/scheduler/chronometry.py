"""Планировщик хронометража: периодический опрос в рабочее время."""

import logging

import pendulum
from aiogram import Bot

from bot.db.crud.users import get_all_users
from bot.db.engine import async_session

logger = logging.getLogger(__name__)

# user_id → timestamp последнего вопроса
_last_asked: dict[int, float] = {}

# user_id → True если ожидаем ответ на хронометраж
_awaiting_response: dict[int, bool] = {}


def is_awaiting_response(user_id: int) -> bool:
    """Проверить, ожидается ли ответ на хронометраж от пользователя."""
    return _awaiting_response.get(user_id, False)


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

            # Проверяем интервал
            interval_sec = user.chronometry_interval_min * 60
            last = _last_asked.get(user.telegram_id, 0)
            if now.timestamp() - last < interval_sec:
                continue

            _last_asked[user.telegram_id] = now.timestamp()
            _awaiting_response[user.telegram_id] = True

            await bot.send_message(
                chat_id=user.telegram_id,
                text="⏱ Чем занимаешься сейчас?",
            )
            logger.info("Хронометраж: вопрос отправлен %s", user.telegram_id)

        except Exception as e:
            logger.error(
                "Ошибка хронометража для %s: %s",
                user.telegram_id, e, exc_info=True,
            )
