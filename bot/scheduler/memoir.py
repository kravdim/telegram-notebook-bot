"""Планировщик мемуарника: вопрос ГСД + недельный/месячный ревью."""

import logging

import pendulum
from aiogram import Bot

from bot.db.crud.memoir import get_memoir_entries
from bot.db.crud.users import get_all_users, update_user_settings
from bot.db.engine import async_session
from bot.formatters.memoir import format_memoir_question, format_weekly_review

logger = logging.getLogger(__name__)


async def send_memoir_prompts(bot: Bot) -> None:
    """Проверить и отправить вопросы мемуарника."""
    async with async_session() as session:
        users = await get_all_users(session)

    for user in users:
        try:
            tz = user.timezone or "Europe/Moscow"
            now = pendulum.now(tz)
            today = now.date()

            prompt_time = user.memoir_prompt_time
            target = now.set(hour=prompt_time.hour, minute=prompt_time.minute, second=0)

            # Идемпотентность
            if user.memoir_asked_date == today:
                continue

            if now >= target and now <= target.add(minutes=5):
                # Воскресенье — недельный ревью
                if now.day_of_week == pendulum.SUNDAY:
                    await _send_weekly_review(bot, user, tz)
                else:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=format_memoir_question(),
                        parse_mode="HTML",
                    )

                # Последний день месяца — месячный ревью
                if now.day == now.days_in_month:
                    await _send_monthly_review(bot, user, tz)

                async with async_session() as session:
                    await update_user_settings(
                        session, user.telegram_id, memoir_asked_date=today
                    )
                logger.info("Мемуарник отправлен: %s", user.telegram_id)

        except Exception as e:
            logger.error(
                "Ошибка мемуарника для %s: %s",
                user.telegram_id, e, exc_info=True,
            )


async def _send_weekly_review(bot: Bot, user, tz: str) -> None:
    """Отправить недельный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=7)

    text = format_weekly_review(entries)
    await bot.send_message(
        chat_id=user.telegram_id, text=text, parse_mode="HTML"
    )

    # Также задаём вопрос дня
    await bot.send_message(
        chat_id=user.telegram_id,
        text=format_memoir_question(),
        parse_mode="HTML",
    )


async def _send_monthly_review(bot: Bot, user, tz: str) -> None:
    """Отправить месячный ревью мемуарника."""
    async with async_session() as session:
        entries = await get_memoir_entries(session, user.telegram_id, limit=31)

    if not entries:
        return

    values = {}
    for e in entries:
        tag = e.value_tag or "другое"
        values[tag] = values.get(tag, 0) + 1

    total = sum(values.values())
    parts = ["📔 <b>Мемуарник: итоги месяца</b>\n"]
    parts.append(f"Записей: {len(entries)}\n")
    parts.append("<b>Ценности:</b>")
    for v, cnt in sorted(values.items(), key=lambda x: -x[1]):
        pct = int(cnt / total * 100)
        parts.append(f"  {v}: {pct}% ({cnt})")

    await bot.send_message(
        chat_id=user.telegram_id,
        text="\n".join(parts),
        parse_mode="HTML",
    )
