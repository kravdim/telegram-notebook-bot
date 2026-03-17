"""Основной контур: отправка напоминаний через APScheduler."""

import logging

import pendulum
from aiogram import Bot

from bot.db.crud.reminders import get_pending_reminders, mark_sent
from bot.db.engine import async_session
from bot.handlers.callbacks import build_snooze_keyboard

logger = logging.getLogger(__name__)


async def send_pending_reminders(bot: Bot) -> None:
    """Отправить все напоминания, время которых наступило."""
    now = pendulum.now("UTC")

    async with async_session() as session:
        reminders = await get_pending_reminders(session, before=now)

    for reminder in reminders:
        try:
            kb = build_snooze_keyboard(str(reminder.id))
            await bot.send_message(
                chat_id=reminder.user_id,
                text=f"🔔 <b>Напоминание:</b>\n{reminder.message}",
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
            async with async_session() as session:
                await mark_sent(session, reminder.id)
            logger.info("Напоминание %s отправлено пользователю %s", reminder.id, reminder.user_id)
        except Exception as e:
            logger.error(
                "Не удалось отправить напоминание %s: %s", reminder.id, e, exc_info=True
            )
