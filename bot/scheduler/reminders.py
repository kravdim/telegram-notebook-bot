"""Основной контур отправки напоминаний из собственного async scheduler loop."""

import logging
from html import escape

import pendulum
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from bot.db.crud.reminders import (
    ReminderClaim,
    claim_due_reminders,
    claim_is_active,
    mark_sent,
    record_delivery_failure,
)
from bot.db.engine import async_session
from bot.handlers.callbacks import build_snooze_keyboard
from bot.logging_safety import error_type
from bot.observability import metrics

logger = logging.getLogger(__name__)


async def send_pending_reminders(bot: Bot) -> None:
    """Отправить все напоминания, время которых наступило."""
    now = pendulum.now("UTC")

    async with async_session() as session:
        claims = await claim_due_reminders(session, now)
    for claim in claims:
        await _deliver_claim(bot, claim)


async def _deliver_claim(bot: Bot, claim: ReminderClaim) -> None:
    try:
        async with async_session() as session:
            if not await claim_is_active(session, claim):
                return
        kb = build_snooze_keyboard(str(claim.id))
        await bot.send_message(
            chat_id=claim.user_id,
            text=f"🔔 <b>Напоминание:</b>\n{escape(claim.message)}",
            parse_mode="HTML", reply_markup=kb.as_markup(),
            request_timeout=30,
        )
        async with async_session() as session:
            await mark_sent(session, claim.id, lease_token=claim.token)
        metrics.increment("reminders.delivered")
        metrics.observe("reminders.delivery_lag_seconds", max(
            0.0, (pendulum.now("UTC") - pendulum.instance(claim.remind_at)).total_seconds(),
        ))
    except Exception as exc:
        metrics.increment("reminders.delivery_error")
        logger.error("Reminder delivery failed: error_type=%s", error_type(exc))
        try:
            async with async_session() as session:
                await record_delivery_failure(
                    session, claim.id, error_type(exc),
                    terminal=isinstance(exc, (TelegramBadRequest, TelegramForbiddenError)),
                    lease_token=claim.token,
                    retry_after=exc.retry_after if isinstance(exc, TelegramRetryAfter) else None,
                )
        except Exception as recording_error:
            logger.error("Reminder failure recording failed: error_type=%s", error_type(recording_error))
