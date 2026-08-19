"""Двойной контур: sweep пропущенных напоминаний каждые 5 минут."""

import logging
from html import escape

import pendulum
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.db.crud.reminders import get_pending_reminders, mark_sent, record_delivery_failure
from bot.db.engine import async_session
from bot.handlers.callbacks import build_snooze_keyboard

logger = logging.getLogger(__name__)


async def sweep_missed_reminders(bot: Bot) -> None:
    """Подобрать и отправить пропущенные напоминания (remind_at < now AND is_sent = false)."""
    now = pendulum.now("UTC")

    async with async_session() as session:
        reminders = await get_pending_reminders(session, before=now)

        if not reminders:
            return

        logger.info("Sweep: найдено %d пропущенных напоминаний", len(reminders))

        for reminder in reminders:
            try:
                kb = build_snooze_keyboard(str(reminder.id))
                await bot.send_message(
                    chat_id=reminder.user_id,
                    text=f"🔔 <b>Напоминание</b> (пропущенное):\n{escape(reminder.message)}",
                    parse_mode="HTML",
                    reply_markup=kb.as_markup(),
                )
                await mark_sent(session, reminder.id)
                logger.info("Sweep: напоминание %s отправлено", reminder.id)
            except Exception as e:
                logger.error("Sweep: ошибка отправки %s: %s", reminder.id, e, exc_info=True)
                await session.rollback()
                await record_delivery_failure(
                    session,
                    reminder.id,
                    str(e),
                    terminal=isinstance(e, (TelegramBadRequest, TelegramForbiddenError)),
                )
